#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Scrub EXIF metadata from JPEG files while retaining selected tags.

Designed for photographers who want to preserve camera details
(exposure, lens, ISO, etc.) but remove private or irrelevant data.
"""

import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Optional

from .__about__ import __license__, __version__
from .jpeg_audit import audit_jpeg, scrubbed_output_violations
from .outcomes import (
    DuplicateDisposition,
    FileOutcome,
    InputClassification,
    OutcomeKind,
    ProcessingPhase,
    RunReport,
    SourceDisposition,
    validate_reason_code,
)
from .renaming import validate_rename_format
from .rename_planner import (
    DEFAULT_MAX_PLAN_BYTES,
    DEFAULT_MAX_PLAN_FILES,
    DEFAULT_MAX_PLAN_SECONDS,
    MAX_COLLISION_REROLLS,
    RenamePlan,
    RenamePlanLimits,
    RenamePlanningError,
    build_rename_plan,
    resolve_unique_destination,
)

sys.stdout.reconfigure(line_buffering=True)


# ----------------------------
# Results and summary structs
# ----------------------------

_SCRUB_RESULT_REASON_KINDS = MappingProxyType({
    "destination_appeared": OutcomeKind.FAILED,
    "destination_collision": OutcomeKind.FAILED,
    "destination_symlink": OutcomeKind.FAILED,
    "duplicate_deleted": OutcomeKind.DUPLICATE_HANDLED,
    "duplicate_delete_failed": OutcomeKind.FAILED,
    "duplicate_move_failed": OutcomeKind.FAILED,
    "duplicate_moved": OutcomeKind.DUPLICATE_HANDLED,
    "duplicate_policy_rejected": OutcomeKind.FAILED,
    "duplicate_unchanged": OutcomeKind.DUPLICATE_HANDLED,
    "existing_output_check_planned": OutcomeKind.PLANNED,
    "existing_output_unsafe": OutcomeKind.FAILED,
    "output_published": OutcomeKind.SCRUBBED,
    "rename_destination_appeared": OutcomeKind.FAILED,
    "rename_destination_conflict": OutcomeKind.FAILED,
    "rename_destination_symlink": OutcomeKind.FAILED,
    "rename_planning_failed": OutcomeKind.FAILED,
    "rename_publication_conflict": OutcomeKind.FAILED,
    "rename_reassignment_failed": OutcomeKind.FAILED,
    "renamed_source_removal_failed": OutcomeKind.FAILED,
    "scrub_failed": OutcomeKind.FAILED,
    "scrub_planned": OutcomeKind.PLANNED,
    "source_deletion_failed": OutcomeKind.FAILED,
})


@dataclass(frozen=True, slots=True)
class ScrubResult:
    """Describe the typed result returned by one scrub operation.

    This result is intentionally operation-scoped. A mode may still need to
    perform source finalization before constructing the terminal FileOutcome.

    Args:
        input_path: Source JPEG passed to the scrub operation.
        output_path: Intended, published, or verified output path when known.
        kind: Typed operation outcome.
        reason_code: Stable machine-readable explanation for the outcome.
        error_message: Human-readable diagnostic for a failed outcome.
        duplicate_path: Final archive path after moving a duplicate or collision.

    Raises:
        ValueError: If a field or field combination is invalid.
    """

    input_path: Path
    output_path: Path | None
    kind: OutcomeKind
    reason_code: str
    error_message: str | None = None
    duplicate_path: Path | None = None

    def __post_init__(self) -> None:
        """Validate the typed operation result.

        Raises:
            ValueError: If a field or field combination is invalid.
        """
        self._validate_path("input_path", self.input_path, required=True)
        self._validate_path("output_path", self.output_path, required=False)
        self._validate_path("duplicate_path", self.duplicate_path, required=False)
        if not isinstance(self.kind, OutcomeKind):
            raise ValueError("kind must be an OutcomeKind")
        validate_reason_code(self.reason_code)
        expected_kind = _SCRUB_RESULT_REASON_KINDS.get(self.reason_code)
        if expected_kind is None:
            raise ValueError(f"unsupported scrub result reason: {self.reason_code}")
        if self.kind is not expected_kind:
            raise ValueError(
                f"reason {self.reason_code} requires outcome {expected_kind.value}"
            )
        if self.kind in {
            OutcomeKind.SCRUBBED,
            OutcomeKind.DUPLICATE_HANDLED,
            OutcomeKind.PLANNED,
        } and self.output_path is None:
            raise ValueError(f"a {self.kind.value} scrub result requires output_path")
        if self.kind is OutcomeKind.FAILED:
            if not isinstance(self.error_message, str) or not self.error_message.strip():
                raise ValueError("a FAILED scrub result requires an error message")
        elif self.error_message is not None:
            raise ValueError("only a FAILED scrub result may have an error message")
        if self.duplicate_path is not None and self.reason_code not in {
            "destination_collision",
            "duplicate_moved",
        }:
            raise ValueError(
                "duplicate_path requires a moved duplicate or collision result"
            )
        if self.reason_code == "duplicate_moved" and self.duplicate_path is None:
            raise ValueError("duplicate_moved requires duplicate_path")

    @staticmethod
    def _validate_path(name: str, path: Path | None, required: bool) -> None:
        """Validate one result path without consulting filesystem state.

        Args:
            name: Field name used in validation messages.
            path: Candidate path.
            required: Whether None must be rejected.

        Returns:
            None.

        Raises:
            ValueError: If the path is missing or invalid.
        """
        if path is None:
            if required:
                raise ValueError(f"{name} must be a pathlib.Path")
            return
        if not isinstance(path, Path):
            raise ValueError(f"{name} must be a pathlib.Path or None")
        if not path.name or "\x00" in str(path):
            raise ValueError(f"{name} must identify a valid file path")

    def __repr__(self) -> str:
        """Return a concise diagnostic representation.

        Returns:
            String containing outcome, reason, paths, and error presence.
        """
        return (
            f"ScrubResult(kind={self.kind.value!r}, "
            f"reason={self.reason_code!r}, "
            f"input={self.input_path.name}, "
            f"output={self.output_path.name if self.output_path else 'n/a'}, "
            f"error={bool(self.error_message)})"
        )


@dataclass(frozen=True, slots=True)
class DiscoveredInput:
    """Describe one regular input observed by the unified discovery pass.

    Args:
        path: Normalized non-symlink pathname retained for processing.
        path_key: Normalized resolved pathname used only for path deduplication.
        classification: JPEG-candidate or unsupported-file classification.
        filesystem_identity: Captured ``(st_dev, st_ino)`` pair when reliable.
        skip_reason: Discovery reason when an eligible JPEG is not admitted.

    Raises:
        ValueError: If a field or field combination is invalid.
    """

    path: Path
    path_key: str
    classification: InputClassification
    filesystem_identity: tuple[int, int] | None
    skip_reason: str | None = None

    def __post_init__(self) -> None:
        """Validate the immutable discovery observation.

        Raises:
            ValueError: If a field or field combination is invalid.
        """
        if not isinstance(self.path, Path) or not self.path.name:
            raise ValueError("path must identify a file")
        if not isinstance(self.path_key, str) or not self.path_key:
            raise ValueError("path_key must be a non-empty string")
        if not isinstance(self.classification, InputClassification):
            raise ValueError("classification must be an InputClassification")
        if self.filesystem_identity is not None:
            if (
                not isinstance(self.filesystem_identity, tuple)
                or len(self.filesystem_identity) != 2
                or any(
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                    for value in self.filesystem_identity
                )
            ):
                raise ValueError(
                    "filesystem_identity must contain non-negative device and inode"
                )
        if self.skip_reason is not None:
            validate_reason_code(self.skip_reason)
            if self.classification is not InputClassification.ELIGIBLE_JPEG:
                raise ValueError("only an eligible JPEG may have a skip_reason")

    @property
    def admitted(self) -> bool:
        """Return whether this JPEG consumes one processing-limit slot.

        Returns:
            True for an eligible JPEG admitted for an operation attempt.
        """
        return (
            self.classification is InputClassification.ELIGIBLE_JPEG
            and self.skip_reason is None
        )


def _scrub_result_to_file_outcome(
    result: ScrubResult,
    source_disposition: SourceDisposition | None = None,
) -> FileOutcome:
    """Convert one operation result into a terminal file outcome.

    Args:
        result: Validated result returned by ``scrub_file``.
        source_disposition: Observed final source disposition when the caller
            completed additional source finalization.

    Returns:
        Validated terminal outcome ready for ``RunReport.record``.

    Raises:
        ValueError: If result or source_disposition has an invalid type.
    """
    if not isinstance(result, ScrubResult):
        raise ValueError("result must be a ScrubResult")
    if source_disposition is not None and not isinstance(
        source_disposition,
        SourceDisposition,
    ):
        raise ValueError("source_disposition must be a SourceDisposition or None")

    duplicate_reasons = {
        "duplicate_deleted",
        "duplicate_delete_failed",
        "duplicate_move_failed",
        "duplicate_moved",
        "duplicate_policy_rejected",
        "duplicate_unchanged",
    }
    post_publication_reasons = {
        "renamed_source_removal_failed",
        "source_deletion_failed",
    }
    phase = ProcessingPhase.SCRUB
    if result.kind is OutcomeKind.PLANNED or result.reason_code in {
        "rename_destination_conflict",
        "rename_destination_symlink",
        "rename_planning_failed",
        "rename_reassignment_failed",
    }:
        phase = ProcessingPhase.PLANNING
    elif result.reason_code in duplicate_reasons:
        phase = ProcessingPhase.DUPLICATE_HANDLING
    elif result.reason_code in {
        "destination_collision",
        "existing_output_unsafe",
    }:
        phase = ProcessingPhase.VERIFICATION
    elif result.kind is OutcomeKind.SCRUBBED or result.reason_code in {
        "destination_appeared",
        "destination_symlink",
        "rename_destination_appeared",
        "rename_publication_conflict",
    }:
        phase = ProcessingPhase.PUBLICATION
    if result.reason_code in post_publication_reasons:
        phase = ProcessingPhase.SOURCE_FINALIZATION

    observed_source = source_disposition
    if observed_source is None:
        observed_source = SourceDisposition.UNCHANGED
        if result.reason_code == "duplicate_deleted":
            observed_source = SourceDisposition.DELETED
        elif result.reason_code == "duplicate_moved":
            observed_source = SourceDisposition.MOVED
        elif (
            result.kind is OutcomeKind.SCRUBBED
            and result.output_path is not None
            and result.output_path.absolute() != result.input_path.absolute()
            and result.output_path.parent.absolute()
            == result.input_path.parent.absolute()
        ):
            observed_source = SourceDisposition.DELETED
        elif result.reason_code == "destination_collision" and result.duplicate_path:
            observed_source = SourceDisposition.MOVED

    duplicate_verified = result.reason_code in duplicate_reasons
    duplicate_disposition: DuplicateDisposition | None = None
    if duplicate_verified:
        duplicate_disposition = DuplicateDisposition(observed_source.value)

    published = (
        result.kind is OutcomeKind.SCRUBBED
        or result.reason_code in post_publication_reasons
    )
    actual_output_path = None
    if published or duplicate_verified:
        actual_output_path = result.output_path

    return FileOutcome(
        input_path=result.input_path,
        input_classification=InputClassification.ELIGIBLE_JPEG,
        kind=result.kind,
        reason_code=result.reason_code,
        phase=phase,
        intended_output_path=result.output_path,
        actual_output_path=actual_output_path,
        published=published,
        duplicate_verified=duplicate_verified,
        source_disposition=observed_source,
        duplicate_disposition=duplicate_disposition,
        diagnostic=result.error_message,
    )


class ScrubSummary:
    """Present the legacy summary while delegating accounting to RunReport."""

    __slots__ = ("_report", "started_at")

    def __init__(self, report: RunReport | None = None) -> None:
        """Initialize a compatibility summary for one invocation.

        Args:
            report: Optional fresh report owned by the current invocation.

        Raises:
            ValueError: If report is not a RunReport.
        """
        if report is not None and not isinstance(report, RunReport):
            raise ValueError("report must be a RunReport or None")
        self._report = report if report is not None else RunReport()
        self.started_at = time.time()

    @property
    def report(self) -> RunReport:
        """Return the underlying typed run report.

        Returns:
            Report that owns all accounting state for this invocation.
        """
        return self._report

    def _legacy_count(self, counter_name: str) -> int:
        """Read one validated compatibility counter.

        Args:
            counter_name: Counter key in the schema-1 projection.

        Returns:
            Current non-negative counter value.
        """
        return self._report.legacy_counts()[counter_name]

    @property
    def total(self) -> int:
        """Return the legacy JPEG total.

        Returns:
            Number of JPEG inputs visible in the schema-1 summary.
        """
        return self._legacy_count("total")

    @property
    def scrubbed(self) -> int:
        """Return the legacy successful-scrub count.

        Returns:
            Number reported as successfully scrubbed by schema 1.
        """
        return self._legacy_count("scrubbed")

    @property
    def skipped(self) -> int:
        """Return the legacy skipped count.

        Returns:
            Number reported as skipped by schema 1.
        """
        return self._legacy_count("skipped")

    @property
    def duplicates_deleted(self) -> int:
        """Return the legacy deleted-duplicate count.

        Returns:
            Number reported as deleted duplicates by schema 1.
        """
        return self._legacy_count("duplicates_deleted")

    @property
    def duplicates_moved(self) -> int:
        """Return the legacy moved-duplicate count.

        Returns:
            Number reported as moved duplicates by schema 1.
        """
        return self._legacy_count("duplicates_moved")

    @property
    def errors(self) -> int:
        """Return the legacy processing-error count.

        Returns:
            Number reported as errors by schema 1.
        """
        return self._legacy_count("errors")

    @property
    def unsupported(self) -> int:
        """Return the legacy unsupported-file count.

        Returns:
            Number reported as unsupported by schema 1.
        """
        return self._legacy_count("unsupported")

    @property
    def examined(self) -> int:
        """Return the number of supported and unsupported files examined.

        Returns:
            Sum of JPEG candidates and unsupported regular files.
        """
        return self._legacy_count("examined")

    def record(self, outcome: FileOutcome) -> None:
        """Record one terminal outcome through the sole accounting writer.

        Args:
            outcome: Validated terminal file outcome.

        Returns:
            None.

        Raises:
            ValueError: If the outcome is invalid or repeats an input path.
        """
        self._report.record(outcome)

    def update(self, result: ScrubResult) -> None:
        """Add one JPEG processing result to the summary.

        Args:
            result: Completed JPEG processing result.

        Returns:
            None.
        """
        self.record(_scrub_result_to_file_outcome(result))

    def print(self) -> None:
        """Print human-readable and machine-readable run summaries.

        Returns:
            None.
        """
        duration = time.time() - self.started_at
        print("📊 Summary:")
        print(f"  Total JPEGs found        : {self.total}")
        print(f"  Successfully scrubbed    : {self.scrubbed}")
        print(f"  Skipped (unstable/temp)  : {self.skipped}")
        print(f"  Errors                   : {self.errors}")
        print(f"  Unsupported files        : {self.unsupported}")
        print(f"  Files examined           : {self.examined}")
        if self.duplicates_deleted:
            print(f"  Duplicates deleted       : {self.duplicates_deleted}")
        if self.duplicates_moved:
            print(f"  Duplicates moved         : {self.duplicates_moved}")
        print(f"  Duration                 : {duration:.2f}s")
        # Machine-parsable one-liner for the bash script
        print(
            "SCRUBEXIF_SUMMARY "
            f"total={self.total} "
            f"scrubbed={self.scrubbed} "
            f"skipped={self.skipped} "
            f"errors={self.errors} "
            f"duplicates_deleted={self.duplicates_deleted} "
            f"duplicates_moved={self.duplicates_moved} "
            f"unsupported={self.unsupported} "
            f"examined={self.examined} "
            f"duration={duration:.3f}"
        )


# ----------------------------
# Fixed container paths
# ----------------------------

PHOTOS_ROOT = Path("/photos")
INPUT_DIR = PHOTOS_ROOT / "input"
OUTPUT_DIR = PHOTOS_ROOT / "output"
PROCESSED_DIR = PHOTOS_ROOT / "processed"
ERRORS_DIR = PHOTOS_ROOT / "errors"


def _unescape_mountinfo(value: str) -> str:
    return (
        value.replace(r"\040", " ")
        .replace(r"\011", "\t")
        .replace(r"\012", "\n")
        .replace(r"\134", "\\")
    )


def _resolve_mount_source(path: Path) -> Optional[str]:
    """
    Best-effort resolve of a bind-mount source path for a mount point.
    Falls back to None if /proc/self/mountinfo is unavailable or unhelpful.
    """
    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as f:
            for line in f:
                if " - " not in line:
                    continue
                pre, post = line.rstrip("\n").split(" - ", 1)
                pre_fields = pre.split()
                if len(pre_fields) < 5:
                    continue
                root = _unescape_mountinfo(pre_fields[3])
                mount_point = _unescape_mountinfo(pre_fields[4])
                if mount_point != str(path):
                    continue
                if root.startswith("/"):
                    return root
                post_fields = post.split()
                if len(post_fields) >= 2 and post_fields[1].startswith("/"):
                    return _unescape_mountinfo(post_fields[1])
    except OSError:
        return None
    return None


SHOW_CONTAINER_PATHS = False


def _resolve_own_host_path(path: Path) -> str:
    """
    Resolve *path* to its host source via its own mount entry.

    Falls back to str(path) if no mount entry exists for the path.
    """
    own_host = _resolve_mount_source(path)
    if own_host:
        if SHOW_CONTAINER_PATHS:
            return f"{path} (host: {own_host})"
        return own_host
    return str(path)


def _format_path_with_host(path: Path) -> str:
    """
    Return the host-side representation of *path* for display.

    If PHOTOS_ROOT has a mount entry, paths under it are rewritten to the
    host root.  Paths outside PHOTOS_ROOT (or when PHOTOS_ROOT itself has no
    mount) are resolved via their own mount entry.
    """
    host_root = _resolve_mount_source(PHOTOS_ROOT)
    if not host_root:
        return _resolve_own_host_path(path)
    try:
        rel = path.relative_to(PHOTOS_ROOT)
    except ValueError:
        return _resolve_own_host_path(path)
    host_path = Path(host_root) / rel
    if SHOW_CONTAINER_PATHS:
        return f"{path} (host: {host_path})"
    return str(host_path)


def _format_relative_path_with_host(path: Path) -> str:
    """
    Like _format_path_with_host but shows the relative container path when
    SHOW_CONTAINER_PATHS is True (e.g. 'input/file.jpg (host: /srv/…)').
    """
    host_root = _resolve_mount_source(PHOTOS_ROOT)
    if not host_root:
        return _resolve_own_host_path(path)
    try:
        rel = path.relative_to(PHOTOS_ROOT)
    except ValueError:
        return _resolve_own_host_path(path)
    host_path = Path(host_root) / rel
    if SHOW_CONTAINER_PATHS:
        return f"{rel} (host: {host_path})"
    return str(host_path)


# ----------------------------
# Logger
# ----------------------------

def setup_logger(level: str = "info"):
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warn": logging.WARNING,
        "error": logging.ERROR,
        "crit": logging.CRITICAL,
    }
    logger = logging.getLogger("scrubexif")
    logger.setLevel(level_map.get(level.lower(), logging.INFO))
    handler = logging.StreamHandler()
    formatter = logging.Formatter("🔎 [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
    return logger


# Will be set/overridden in main()
log = logging.getLogger("scrubexif")

DEBUG_ENV_VARS = (
    "ALLOW_ROOT",
    "SCRUBEXIF_STATE",
    "SCRUBEXIF_ON_DUPLICATE",
    "SCRUBEXIF_STABLE_SECONDS",
    "SCRUBEXIF_IMAGE",
    "SCRUBEXIF_AUTOBUILD",
)


def show_version() -> None:
    """Print version and license values sourced from ``__about__.py``.

    Returns:
        None.
    """
    print(f"scrubexif {__version__}")
    print(__license__)


# ----------------------------
# Safety checks
# ----------------------------

FORBIDDEN_OUTPUT_ROOTS = (
    Path("/usr"),
    Path("/var"),
    Path("/boot"),
    Path("/dev"),
    Path("/etc"),
    Path("/root"),
    Path("/lib"),
    Path("/lib32"),
    Path("/lib64"),
    Path("/libx32"),
    Path("/libexec"),
)


def _is_path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_forbidden_output_create_path(path: Path) -> bool:
    resolved = path.resolve(strict=False)
    for root in FORBIDDEN_OUTPUT_ROOTS:
        if _is_path_within(resolved, root):
            return True
    return False


def resolve_output_dir(raw: Path) -> Path:
    if raw.is_absolute():
        candidate = raw
    else:
        candidate = (PHOTOS_ROOT / raw).resolve(strict=False)
        try:
            candidate.relative_to(PHOTOS_ROOT)
        except ValueError:
            print(f"❌ Output path escapes allowed root {PHOTOS_ROOT}: {raw}", file=sys.stderr)
            sys.exit(1)

    if candidate.is_symlink():
        print(f"❌ Output directory is a symlink (not allowed): {candidate}", file=sys.stderr)
        sys.exit(1)
    if candidate.exists() and not candidate.is_dir():
        print(f"❌ Output path is not a directory: {candidate}", file=sys.stderr)
        sys.exit(1)
    if not candidate.exists() and _is_forbidden_output_create_path(candidate):
        print(
            f"❌ Refusing to create output directory under system path: {candidate}",
            file=sys.stderr
        )
        sys.exit(1)

    return candidate


def check_dir_safety(path: Path, label: str) -> None:
    """Validate that a required directory is safe and writable.

    Args:
        path: Directory to validate.
        label: Human-readable directory role for diagnostics.

    Returns:
        None.

    Raises:
        SystemExit: If the path is missing, unsafe, or not writable.
    """
    display_path = _format_path_with_host(path)
    if not path.exists():
        print(f"❌ {label} directory does not exist: {display_path}")
        sys.exit(1)
    if not path.is_dir():
        print(f"❌ {label} path is not a directory: {display_path}")
        sys.exit(1)
    if path.is_symlink():
        print(f"❌ {label} is a symbolic link (not allowed): {display_path}")
        sys.exit(1)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path,
            prefix=".scrubexif_write_test_",
            delete=True,
        ) as test_file:
            test_file.write("test")
            test_file.flush()
    except OSError as exc:
        log.error("Directory write probe failed for %s: %s", path, exc)
        print(f"❌ {label} directory is not writable: {display_path}")
        sys.exit(1)


def _dirs_same(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except FileNotFoundError:
        return False
    except OSError:
        try:
            return a.resolve() == b.resolve()
        except Exception:
            return False


def guard_auto_mode_dirs(on_duplicate: str):
    pairs = [
        (INPUT_DIR, "input", OUTPUT_DIR, "output"),
        (INPUT_DIR, "input", PROCESSED_DIR, "processed"),
        (OUTPUT_DIR, "output", PROCESSED_DIR, "processed"),
    ]
    if on_duplicate == "move":
        pairs.extend([
            (ERRORS_DIR, "errors", INPUT_DIR, "input"),
            (ERRORS_DIR, "errors", OUTPUT_DIR, "output"),
            (ERRORS_DIR, "errors", PROCESSED_DIR, "processed"),
        ])

    for left, left_label, right, right_label in pairs:
        if _dirs_same(left, right):
            left_path = _format_path_with_host(left)
            right_path = _format_path_with_host(right)
            print(
                f"❌ Auto mode requires distinct directories; {left_label} and {right_label} "
                f"resolve to the same path ({left_path} == {right_path}).",
                file=sys.stderr,
            )
            sys.exit(1)


# ----------------------------
# Stability state management
# ----------------------------

def _resolve_state_path_from_env() -> Optional[Path]:
    """
    Priority (when no CLI override is provided):
      1) SCRUBEXIF_STATE env
      2) If no env: /photos/.scrubexif_state.json if writable
      3) If no env: /tmp/.scrubexif_state.json if writable
      4) None => state disabled, mtime-only
    """
    env = os.getenv("SCRUBEXIF_STATE")
    if env:
        env_path = Path(env)
        candidate = _validate_writable_path(env_path)
        if candidate:
            return candidate
        log.warning("SCRUBEXIF_STATE=%s is not writable; disabling state (mtime-only).", env_path)
        return None

    for p in (Path("/photos/.scrubexif_state.json"), Path("/tmp/.scrubexif_state.json")):
        candidate = _validate_writable_path(p)
        if candidate:
            log.info("State path auto-selected: %s", candidate)
            return candidate

    log.warning("No writable state path found; disabling state (mtime-only).")
    return None


def _validate_writable_path(p: Path) -> Optional[Path]:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=p.parent, delete=True):
            return p
    except Exception:
        return None


STATE_FILE: Optional[Path] = None
_warned_state_disabled = False


def load_state() -> dict:
    if STATE_FILE is None:
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("State load failed: %s", e)
    return {}


def save_state(state: dict[str, object]) -> None:
    """Persist stability state through a freshly reserved temporary file.

    Args:
        state: JSON-serializable stability-state mapping.

    Returns:
        None.
    """
    global _warned_state_disabled, STATE_FILE
    if STATE_FILE is None:
        if not _warned_state_disabled:
            log.info("State disabled: using mtime-only stability.")
            _warned_state_disabled = True
        return
    state_file = STATE_FILE
    descriptor: int | None = None
    tmp: Path | None = None
    try:
        descriptor, raw_tmp = tempfile.mkstemp(
            dir=state_file.parent,
            prefix=f".{state_file.name}.",
            suffix=".tmp",
        )
        tmp = Path(raw_tmp)
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            descriptor = None
            json.dump(state, f, separators=(",", ":"), ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, state_file)
        tmp = None
    except (OSError, TypeError, ValueError) as e:
        if not _warned_state_disabled:
            log.warning("State save failed at %s: %s. Falling back to mtime-only.", state_file, e)
            _warned_state_disabled = True
        STATE_FILE = None  # stop future attempts
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                log.warning("Failed to close state temporary descriptor: %s", exc)
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("Failed to remove state temporary file %s: %s", tmp, exc)


def prune_state(state: dict):
    remove = []
    for key in list(state.keys()):
        if not Path(key).exists():
            remove.append(key)
    for k in remove:
        state.pop(k, None)


def mark_seen(path: Path, state: dict):
    try:
        st = path.stat()
    except FileNotFoundError:
        return
    key = str(path.resolve())
    state[key] = {"size": st.st_size, "mtime": st.st_mtime, "seen": time.time()}


# ----------------------------
# Temp/partial detection
# ----------------------------

TEMP_SUFFIXES = {
    ".tmp", ".part", ".partial", ".crdownload", ".download", ".upload", ".cache",
    ".swp", ".swx", ".lck"
}
TEMP_PREFIXES = {".", "~", "._"}


def is_probably_temp(path: Path) -> bool:
    name = path.name
    if any(name.startswith(p) for p in TEMP_PREFIXES):
        return True
    low = name.lower()
    if path.suffix.lower() in TEMP_SUFFIXES:
        return True
    for suf in TEMP_SUFFIXES:
        if low.endswith(suf):
            return True
    return False


def is_file_stable(path: Path, state: dict, stable_seconds: int) -> bool:
    """
    Stable if:
      1) mtime age >= stable_seconds, and
      2) if previously seen, size+mtime unchanged since last run.
    """
    reason = "ok"
    try:
        st = path.stat()
    except FileNotFoundError:
        if log.isEnabledFor(logging.DEBUG):
            log.debug("Stability check: %s missing -> unstable", path)
        return False

    now = time.time()
    key = str(path.resolve())
    prev = state.get(key)
    age = now - st.st_mtime

    stable = True
    if stable_seconds > 0 and age < stable_seconds:
        stable = False
        reason = f"age<{stable_seconds}"
    elif prev and (prev.get("size") != st.st_size or prev.get("mtime") != st.st_mtime):
        stable = False
        reason = "changed"

    if log.isEnabledFor(logging.DEBUG):
        prev_seen = prev.get("seen") if prev else None
        prev_age = (now - prev_seen) if prev_seen else None
        log.debug(
            "Stability check: %s size=%d age=%.2fs threshold=%ds prev=%s -> %s (%s)",
            path,
            st.st_size,
            age,
            stable_seconds,
            {"size": prev.get("size") if prev else None,
             "mtime": prev.get("mtime") if prev else None,
             "seen_age": prev_age},
            stable,
            reason,
        )

    return stable


# ----------------------------
# EXIF config
# ----------------------------

# Camera tags to extract from the source JPEG and restore after stripping.
# ImageSize is a composite tag derived from the JPEG SOF segment, which
# jpegtran preserves intact — no need to round-trip it through EXIF.
TAGS_TO_EXTRACT: list[str] = [
    "ExposureTime",
    "FNumber",
    "FocalLength",
    "ISO",
    "Orientation",
]

# Conservative limits (UTF-8 bytes) to avoid bloated EXIF/XMP segments.
MAX_COPYRIGHT_BYTES = 1024
MAX_COMMENT_BYTES = 4096


def _truncate_utf8(label: str, value: str, max_bytes: int) -> str:
    """
    Truncate a string to at most max_bytes UTF-8 bytes.

    Args:
        label: Human-readable name used in the warning log message.
        value: String to truncate.
        max_bytes: Maximum byte length of the result.

    Returns:
        The original string if it fits, otherwise a valid UTF-8 truncation.
    """
    data = value.encode("utf-8")
    if len(data) <= max_bytes:
        return value
    log.warning(
        "%s too long (%d bytes); truncating to %d bytes.",
        label,
        len(data),
        max_bytes,
    )
    truncated = data[:max_bytes]
    # Trim to a valid UTF-8 boundary.
    while truncated and (truncated[-1] & 0xC0) == 0x80:
        truncated = truncated[:-1]
    return truncated.decode("utf-8", errors="ignore")


def build_stamp_args(copyright_text: str | None,
                     comment_text: str | None) -> list[str]:
    """
    Build exiftool arguments to stamp copyright and/or comment into a JPEG.

    Args:
        copyright_text: Copyright notice, or None to skip.
        comment_text: Comment string, or None to skip.

    Returns:
        List of exiftool tag-assignment arguments.
    """
    args: list[str] = []
    if copyright_text is not None:
        value = _truncate_utf8("Copyright notice", copyright_text, MAX_COPYRIGHT_BYTES)
        args.append(f"-EXIF:Copyright={value}")
        args.append(f"-XMP-dc:Rights={value}")
    if comment_text is not None:
        value = _truncate_utf8("Comment", comment_text, MAX_COMMENT_BYTES)
        args.append(f"-EXIF:UserComment={value}")
        args.append(f"-XMP-dc:Description={value}")
    return args


# ----------------------------
# jpegtran-based pipeline
# ----------------------------

def check_jpegtran() -> None:
    """
    Verify that jpegtran is available on PATH.

    Exits with a clear error message if not found.
    Install via: apt-get install libjpeg-turbo-progs
    """
    if not shutil.which("jpegtran"):
        print(
            "❌ jpegtran not found on PATH. "
            "Install libjpeg-turbo-progs (Debian/Ubuntu) or equivalent.",
            file=sys.stderr,
        )
        sys.exit(1)


def extract_wanted_tags(input_path: Path) -> dict[str, object]:
    """
    Extract the whitelist of EXIF tag values from a JPEG.

    Uses exiftool with -n to obtain raw numeric values suitable for
    round-tripping back into a clean JPEG via explicit tag assignments.
    Tags absent from the source are silently omitted from the result.

    Args:
        input_path: Path to the source JPEG.

    Returns:
        Dict mapping tag name to raw value (str, int, or float).

    Raises:
        RuntimeError: If exiftool exits non-zero.
    """
    tag_args = [f"-{tag}" for tag in TAGS_TO_EXTRACT]
    cmd = ["exiftool", "-j", "-n"] + tag_args + [str(input_path.absolute())]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"exiftool tag extraction failed: {result.stderr.strip()}"
        )
    if result.stderr.strip():
        raise RuntimeError(
            f"exiftool tag extraction emitted a warning: {result.stderr.strip()}"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("exiftool tag extraction returned invalid JSON") from exc
    if not data:
        return {}
    if len(data) != 1 or not isinstance(data[0], dict):
        raise RuntimeError("exiftool tag extraction returned an unexpected JSON structure")

    requested_tags = set(TAGS_TO_EXTRACT)
    extracted = {k: v for k, v in data[0].items() if k != "SourceFile"}
    unexpected_tags = sorted(set(extracted) - requested_tags)
    if unexpected_tags:
        raise RuntimeError(
            "exiftool tag extraction returned unexpected keys: "
            + ", ".join(unexpected_tags)
        )
    for tag, value in extracted.items():
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise RuntimeError(
                f"exiftool tag extraction returned an invalid value for {tag}: "
                f"{type(value).__name__}"
            )
    return extracted


def extract_icc_profile(input_path: Path, icc_path: Path) -> bool:
    """
    Extract the ICC colour profile from a JPEG to a binary file.

    Args:
        input_path: Source JPEG.
        icc_path: Destination path for the raw ICC profile bytes.

    Returns:
        True if an ICC profile was found and written; False if none present.

    Raises:
        RuntimeError: If exiftool fails or the output file cannot be written.
    """
    cmd = ["exiftool", "-b", "-ICC_Profile", str(input_path.absolute())]
    try:
        with open(icc_path, "wb") as f:
            result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)
    except OSError as e:
        raise RuntimeError(
            f"Failed to write ICC profile to {icc_path}: {e}"
        ) from e
    if result.returncode != 0:
        icc_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"exiftool ICC extraction failed: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    if result.stderr.strip():
        icc_path.unlink(missing_ok=True)
        raise RuntimeError(
            "exiftool ICC extraction emitted a warning: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    if not icc_path.exists() or icc_path.stat().st_size == 0:
        icc_path.unlink(missing_ok=True)
        return False
    return True


def run_jpegtran(input_path: Path, output_path: Path) -> None:
    """
    Strip all JPEG APP segments with jpegtran -copy none.

    Removes all metadata (EXIF, XMP, IPTC, ICC profile, and any unknown
    proprietary APP segments) while preserving the image data losslessly.
    The JPEG SOF segment — which carries image dimensions — is retained.

    Args:
        input_path: Source JPEG (not modified).
        output_path: Destination path for the stripped JPEG.

    Raises:
        RuntimeError: If jpegtran exits non-zero or produces no output.
    """
    cmd = [
        "jpegtran", "-copy", "none",
        "-outfile", str(output_path.absolute()),
        str(input_path.absolute()),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        raise RuntimeError(f"Failed to run jpegtran: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"jpegtran failed: {result.stderr.strip() or 'unknown error'}"
        )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("jpegtran produced no output file")


def build_tag_writeback_cmd(
    output_path: Path,
    tags: dict[str, object],
    icc_path: Optional[Path],
    copyright_text: Optional[str],
    comment_text: Optional[str],
) -> list[str]:
    """
    Build an exiftool command to write back preserved tags and ICC profile.

    The command modifies the file at output_path in-place using
    -overwrite_original.

    Args:
        output_path: JPEG to write into.
        tags: Dict of tag name to raw value as returned by extract_wanted_tags.
        icc_path: Path to a raw ICC profile binary, or None to skip.
        copyright_text: Optional copyright string to stamp.
        comment_text: Optional comment string to stamp.

    Returns:
        argv list ready for subprocess.run.
    """
    # -n: write raw numeric values; without it exiftool mis-applies inverse
    # print-conversion on integer tags (e.g. Orientation=1 stores as 3).
    cmd = ["exiftool", "-overwrite_original", "-P", "-n"]
    if icc_path is not None:
        cmd.append(f"-icc_profile<={icc_path.absolute()}")
    for tag, value in tags.items():
        if value is None:
            continue
        cmd.append(f"-EXIF:{tag}={value}")
    cmd += build_stamp_args(copyright_text, comment_text)
    cmd.append(str(output_path.absolute()))
    return cmd


def _do_scrub_pipeline(
    input_path: Path,
    output_path: Path,
    paranoia: bool,
    copyright_text: Optional[str],
    comment_text: Optional[str],
) -> None:
    """
    Core scrub pipeline — shared by scrub_file and preview mode.

    Paranoia mode:
        jpegtran -copy none only.  Zero metadata in the output.

    Normal mode (three steps):
        1. exiftool extracts the tag whitelist and ICC profile from the source.
        2. jpegtran -copy none strips all APP segments.
        3. exiftool writes the whitelist tags and ICC profile back.

    Args:
        input_path: Source JPEG (never modified).
        output_path: Destination for the scrubbed JPEG.
        paranoia: True for zero-metadata output.
        copyright_text: Copyright notice to stamp (normal mode only).
        comment_text: Comment to stamp (normal mode only).

    Raises:
        RuntimeError: On any subprocess failure.
    """
    if paranoia:
        run_jpegtran(input_path, output_path)
        return

    # Step 1 — extract tag values and ICC profile from the original.
    tags = extract_wanted_tags(input_path)
    log.debug("Extracted tags from %s: %s", input_path.name, tags)

    icc_fd, icc_tmp_str = tempfile.mkstemp(
        suffix=".icc", dir=output_path.parent, prefix=".scrubexif_icc_"
    )
    os.close(icc_fd)
    icc_tmp = Path(icc_tmp_str)

    try:
        has_icc = extract_icc_profile(input_path, icc_tmp)
        if not has_icc:
            icc_tmp.unlink(missing_ok=True)
            icc_tmp = None
            log.debug("No ICC profile found in %s", input_path.name)

        # Step 2 — strip everything with jpegtran.
        run_jpegtran(input_path, output_path)

        # Step 3 — write back the whitelist (skip if nothing to restore).
        if tags or icc_tmp or copyright_text or comment_text:
            writeback_cmd = build_tag_writeback_cmd(
                output_path, tags, icc_tmp, copyright_text, comment_text
            )
            if log.isEnabledFor(logging.DEBUG):
                log.debug("Tag write-back command: %s", " ".join(writeback_cmd))
            wb_result = subprocess.run(
                writeback_cmd,
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            if wb_result.returncode != 0:
                raise RuntimeError(
                    f"exiftool write-back failed: {wb_result.stderr.strip()}"
                )
            if wb_result.stderr.strip():
                raise RuntimeError(
                    f"exiftool write-back emitted a warning: {wb_result.stderr.strip()}"
                )
    finally:
        if icc_tmp is not None:
            icc_tmp.unlink(missing_ok=True)


def print_tags(file: Path, label: str = ""):
    try:
        result = subprocess.run(
            ["exiftool", "-a", "-G1", "-s", str(file.absolute())],   # security advice on https://exiftool.org/
            capture_output=True, text=True
        )
        print(f"\n📸 Tags {label} {_format_path_with_host(file)}:")
        print(result.stdout.strip())
    except Exception as e:
        print(f"❌ Failed to read tags: {e}")


# ----------------------------
# Temp output handling
# ----------------------------

def _create_temp_output(dir_path: Path, suffix: str) -> Path:
    """Create a fresh temporary output file in the destination directory.

    Args:
        dir_path: Directory that will receive the final output.
        suffix: Source filename extension to preserve.

    Returns:
        Owned temporary path reserved with an exclusive create.

    Raises:
        OSError: If the directory or temporary file cannot be created.
        ValueError: If an argument has the wrong type.
    """
    if not isinstance(dir_path, Path):
        raise ValueError("dir_path must be a pathlib.Path")
    if not isinstance(suffix, str):
        raise ValueError("suffix must be a string")
    dir_path.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        dir=dir_path,
        prefix=".scrubexif_tmp_",
        suffix=suffix,
    )
    os.close(descriptor)
    return Path(raw_path)


def _audit_scrubbed_output(
    output_path: Path,
    paranoia: bool,
    copyright_text: str | None,
    comment_text: str | None,
) -> str:
    """Validate a completed JPEG and return its SHA-256 digest.

    Args:
        output_path: Completed private staging JPEG.
        paranoia: Whether the zero-metadata policy applies.
        copyright_text: Requested copyright stamp, if any.
        comment_text: Requested comment stamp, if any.

    Returns:
        Hexadecimal SHA-256 digest of the audited bytes.

    Raises:
        RuntimeError: If parsing fails or the JPEG violates its scrub policy.
        ValueError: If arguments are invalid.
    """
    if not isinstance(output_path, Path):
        raise ValueError("output_path must be a pathlib.Path")
    try:
        audit = audit_jpeg(output_path)
        violations = scrubbed_output_violations(
            audit,
            paranoia=paranoia,
            copyright_text=(
                _truncate_utf8("Copyright notice", copyright_text, MAX_COPYRIGHT_BYTES)
                if copyright_text is not None
                else None
            ),
            comment_text=(
                _truncate_utf8("Comment", comment_text, MAX_COMMENT_BYTES)
                if comment_text is not None
                else None
            ),
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Independent output audit could not parse JPEG: {exc}") from exc
    if violations:
        raise RuntimeError(
            "Independent output audit rejected JPEG: " + "; ".join(violations)
        )
    return _sha256_file(output_path)


def _sha256_file(path: Path) -> str:
    """Calculate a file's SHA-256 digest without loading it all into memory.

    Args:
        path: Existing regular file to hash.

    Returns:
        Lowercase hexadecimal SHA-256 digest.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If path is invalid.
    """
    if not isinstance(path, Path) or not path.is_file():
        raise ValueError("path must be an existing regular file")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_audited_output_to_destination(
    staged_output: Path,
    destination_directory: Path,
    expected_digest: str,
) -> Path:
    """Copy audited bytes into a durable destination-filesystem staging file.

    Only bytes from a previously audited private staging file enter the output
    directory. The returned ``.part`` file is ready for atomic publication.

    Args:
        staged_output: Privately staged and audited JPEG.
        destination_directory: Directory containing the final destination.
        expected_digest: SHA-256 digest calculated immediately after auditing.

    Returns:
        Owned temporary path on the destination filesystem.

    Raises:
        RuntimeError: If the copied bytes do not match the audited bytes.
        OSError: If staging, copying, or cleanup fails.
        ValueError: If arguments are invalid.
    """
    if not isinstance(staged_output, Path) or not staged_output.is_file():
        raise ValueError("staged_output must be an existing regular file")
    if not isinstance(destination_directory, Path) or not destination_directory.is_dir():
        raise ValueError("destination_directory must be an existing directory")
    if (
        not isinstance(expected_digest, str)
        or len(expected_digest) != 64
        or any(character not in "0123456789abcdef" for character in expected_digest)
    ):
        raise ValueError("expected_digest must be a SHA-256 hexadecimal digest")

    descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            dir=destination_directory,
            prefix=".scrubexif_publish_",
            suffix=".part",
        )
        temporary_path = Path(raw_path)
        with staged_output.open("rb") as source, os.fdopen(descriptor, "wb") as target:
            descriptor = None
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        actual_digest = _sha256_file(temporary_path)
        if actual_digest != expected_digest:
            raise RuntimeError(
                "Destination staging digest differs from the independently audited JPEG"
            )
        return temporary_path
    except (OSError, RuntimeError, ValueError):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                log.warning(
                    "Failed to close destination staging descriptor: %s",
                    exc,
                )
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning(
                    "Failed to remove destination staging file %s: %s",
                    temporary_path,
                    exc,
                )
        raise


def _publish_no_clobber(temp_output: Path, destination: Path) -> None:
    """Atomically publish a temporary file without replacing a destination.

    The temporary output is always created in the destination directory, so a
    hard link provides an atomic no-overwrite operation on the same filesystem.

    Args:
        temp_output: Completed temporary scrub output.
        destination: Final destination that must not already exist.

    Returns:
        None.

    Raises:
        FileExistsError: If the destination is already occupied.
        OSError: If publication or temporary-file cleanup fails.
        ValueError: If paths are invalid or reside in different directories.
    """
    if not isinstance(temp_output, Path) or not temp_output.is_file():
        raise ValueError("temp_output must be an existing file")
    if not isinstance(destination, Path) or not destination.name:
        raise ValueError("destination must be a pathlib.Path with a filename")
    if temp_output.parent.absolute() != destination.parent.absolute():
        raise ValueError("temp_output and destination must share a directory")
    os.link(temp_output, destination, follow_symlinks=False)
    temp_output.unlink()


class ArchiveError(RuntimeError):
    """Raised when an original cannot be archived without data loss.

    Args:
        message: Human-readable archival failure.
    """


def _archive_collision_candidate(source_path: Path, destination_directory: Path) -> Path:
    """Generate a recognisable, bounded archive name after a collision.

    Args:
        source_path: Original source whose extension is preserved.
        destination_directory: Directory that will contain the archive.

    Returns:
        Randomly suffixed candidate path that fits the filesystem name limit.

    Raises:
        ArchiveError: If the filesystem limit cannot retain any original stem.
    """
    token = secrets.token_hex(4)
    candidate_name = f"{source_path.stem}_{token}{source_path.suffix}"
    try:
        name_max = os.pathconf(destination_directory, "PC_NAME_MAX")
    except (OSError, ValueError):
        name_max = 255
    if len(os.fsencode(candidate_name)) <= name_max:
        return destination_directory / candidate_name

    suffix_with_token = f"_{token}{source_path.suffix}"
    stem_budget = name_max - len(os.fsencode(suffix_with_token))
    if stem_budget <= 0:
        raise ArchiveError(
            f"Filesystem filename limit cannot fit an archive hash and extension: "
            f"{destination_directory}"
        )

    truncated_stem: list[str] = []
    used_bytes = 0
    for character in source_path.stem:
        character_bytes = len(os.fsencode(character))
        if used_bytes + character_bytes > stem_budget:
            break
        truncated_stem.append(character)
        used_bytes += character_bytes
    if not truncated_stem:
        raise ArchiveError(
            f"Filesystem filename limit cannot retain the original archive name: "
            f"{source_path.name}"
        )

    candidate_name = f"{''.join(truncated_stem)}{suffix_with_token}"
    return destination_directory / candidate_name


def _archive_no_clobber(
    source_path: Path,
    destination_directory: Path,
    max_rerolls: int = MAX_COLLISION_REROLLS,
) -> Path:
    """Copy an original into an archive without replacing any destination.

    A temporary copy is created on the destination filesystem so publication
    remains atomic even when source and archive are separate bind mounts. The
    source is removed only after the archive entry is safely published.

    Args:
        source_path: Existing non-symlink file to archive.
        destination_directory: Existing archive directory.
        max_rerolls: Random filename attempts after the original name collides.

    Returns:
        Published archive path.

    Raises:
        ValueError: If an argument is invalid.
        ArchiveError: If copying, publication, or source removal fails.
    """
    if not isinstance(source_path, Path) or not source_path.name:
        raise ValueError("source_path must be a pathlib.Path with a filename")
    if source_path.is_symlink() or not source_path.is_file():
        raise ArchiveError(f"Archive source is missing or not a regular file: {source_path}")
    if not isinstance(destination_directory, Path):
        raise ValueError("destination_directory must be a pathlib.Path")
    if destination_directory.is_symlink() or not destination_directory.is_dir():
        raise ArchiveError(
            f"Archive destination is missing, not a directory, or a symlink: "
            f"{destination_directory}"
        )
    if not isinstance(max_rerolls, int) or isinstance(max_rerolls, bool) or max_rerolls < 0:
        raise ValueError("max_rerolls must be a non-negative integer")

    descriptor: int | None = None
    temporary_path: Path | None = None
    published_path: Path | None = None
    try:
        descriptor, raw_temporary_path = tempfile.mkstemp(
            dir=destination_directory,
            prefix=".scrubexif_archive_",
            suffix=source_path.suffix,
        )
        os.close(descriptor)
        descriptor = None
        temporary_path = Path(raw_temporary_path)
        shutil.copy2(source_path, temporary_path, follow_symlinks=False)
        with temporary_path.open("rb") as temporary_file:
            os.fsync(temporary_file.fileno())

        for attempt in range(max_rerolls + 1):
            candidate = (
                destination_directory / source_path.name
                if attempt == 0
                else _archive_collision_candidate(source_path, destination_directory)
            )
            try:
                _publish_no_clobber(temporary_path, candidate)
                temporary_path = None
                published_path = candidate
                break
            except FileExistsError:
                continue

        if published_path is None:
            raise ArchiveError(
                f"Could not reserve an archive name after {max_rerolls} random re-rolls "
                f"for {source_path}"
            )

        try:
            source_path.unlink()
        except OSError as exc:
            raise ArchiveError(
                f"Archived {source_path} to {published_path}, but could not remove "
                f"the source: {exc}"
            ) from exc
        return published_path
    except ArchiveError:
        raise
    except (OSError, shutil.Error) as exc:
        raise ArchiveError(f"Failed to archive {source_path}: {exc}") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                log.warning("Failed to close archive temporary descriptor: %s", exc)
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning(
                    "Failed to remove archive temporary file %s: %s",
                    temporary_path,
                    exc,
                )


# ----------------------------
# Scrub operations
# ----------------------------

def scrub_file(
    input_path: Path,
    output_path: Path | None = None,
    delete_original=False,
    dry_run=False,
    show_tags_mode: str | None = None,
    paranoia: bool = True,
    on_duplicate: str | None = "move",
    copyright_text: str | None = None,
    comment_text: str | None = None,
    rename_format: str | None = None,
    rename_counter: dict[str, int] | None = None,
    planned_rename_path: Path | None = None,
    rename_destination_allocator: Callable[[Path], Path] | None = None,
) -> ScrubResult:
    if on_duplicate is None and output_path is not None:
        raise ValueError(
            "on_duplicate may be None only for in-place/manual operation"
        )
    if (
        on_duplicate is not None
        and on_duplicate not in {"move", "fail", "delete", "skip"}
    ):
        raise ValueError(
            "on_duplicate must be one of: move, fail, delete, skip"
        )
    print(f"scrub_file: input={_format_path_with_host(input_path)}, output={_format_path_with_host(output_path) if output_path else None}")

    # Resolve rename stem before the scrub pipeline runs so that EXIF tags
    # (%Y, %m) are still present in the source file when exiftool reads them.
    rename_requested = rename_format is not None or planned_rename_path is not None
    if rename_destination_allocator is not None and not callable(rename_destination_allocator):
        raise ValueError("rename_destination_allocator must be callable")
    late_allocator = rename_destination_allocator
    late_reassignments = 0
    rename_stem: str | None = None
    if planned_rename_path is not None:
        expected_directory = output_path if output_path is not None else input_path.parent
        if planned_rename_path.parent.absolute() != expected_directory.absolute():
            raise ValueError("planned_rename_path must use the active destination directory")
        if planned_rename_path.suffix != input_path.suffix:
            raise ValueError("planned_rename_path must preserve the source extension")
        output_file = planned_rename_path
        rename_stem = planned_rename_path.stem
    elif rename_format:
        destination_directory = output_path if output_path is not None else input_path.parent
        counter = rename_counter if rename_counter is not None else {"n": 0}
        counter_start = counter.get("n", 0)

        def is_collision(candidate: Path) -> bool:
            """Check single-file rename availability.

            Args:
                candidate: Proposed destination path.

            Returns:
                True when another directory entry occupies the destination.
            """
            return candidate.absolute() != input_path.absolute() and os.path.lexists(candidate)

        try:
            output_file = resolve_unique_destination(
                rename_format,
                input_path,
                destination_directory,
                counter,
                is_collision,
            )
        except RenamePlanningError as exc:
            err_msg = str(exc)
            print(f"❌ Failed to plan rename for {_format_path_with_host(input_path)}: {err_msg}")
            return ScrubResult(
                input_path=input_path,
                output_path=input_path,
                kind=OutcomeKind.FAILED,
                reason_code="rename_planning_failed",
                error_message=err_msg,
            )
        rename_stem = output_file.stem

        if late_allocator is None:
            def allocate_direct_destination(occupied_destination: Path) -> Path:
                """Re-expand a direct-call rename after late occupation.

                Args:
                    occupied_destination: Destination that became occupied.

                Returns:
                    Newly available destination using the original counter value.
                """
                del occupied_destination
                return resolve_unique_destination(
                    rename_format,
                    input_path,
                    destination_directory,
                    {"n": counter_start},
                    is_collision,
                )

            late_allocator = allocate_direct_destination
    else:
        output_file = output_path / input_path.name if output_path else input_path

    def reassign_late_destination(occupied_destination: Path) -> Path:
        """Recover from harmless concurrent destination creation.

        Args:
            occupied_destination: Destination observed as occupied.

        Returns:
            Validated replacement destination.

        Raises:
            RenamePlanningError: If no safe replacement can be reserved.
            ValueError: If the allocator returns an invalid path.
        """
        nonlocal late_reassignments
        if late_allocator is None:
            raise RenamePlanningError(
                f"Rename destination became occupied and cannot be re-rolled: {occupied_destination}"
            )
        if late_reassignments >= MAX_COLLISION_REROLLS:
            raise RenamePlanningError(
                "Rename destination kept changing on the active filesystem; "
                f"stopped after {MAX_COLLISION_REROLLS} late reassignments"
            )
        replacement = late_allocator(occupied_destination)
        expected_directory = output_path if output_path is not None else input_path.parent
        if not isinstance(replacement, Path) or not replacement.name:
            raise ValueError("rename destination allocator must return a pathlib.Path")
        if replacement.parent.absolute() != expected_directory.absolute():
            raise ValueError("replacement rename destination uses the wrong directory")
        if replacement.suffix != input_path.suffix:
            raise ValueError("replacement rename destination must preserve the source extension")
        if replacement.absolute() == occupied_destination.absolute():
            raise RenamePlanningError(
                f"Rename destination allocator returned the occupied path again: {replacement}"
            )
        late_reassignments += 1
        print(
            "ℹ️ Rename destination became occupied on an active filesystem: "
            f"{_format_path_with_host(occupied_destination)}; continuing with "
            f"{_format_path_with_host(replacement)}"
        )
        return replacement

    print("Output file will be:", _format_path_with_host(output_file))

    while (
        rename_requested
        and output_file.absolute() != input_path.absolute()
        and os.path.lexists(output_file)
    ):
        try:
            output_file = reassign_late_destination(output_file)
            rename_stem = output_file.stem
        except RenamePlanningError as exc:
            err_msg = str(exc)
            print(f"❌ Rename conflict for {_format_path_with_host(input_path)}: {err_msg}")
            return ScrubResult(
                input_path=input_path,
                output_path=output_file,
                kind=OutcomeKind.FAILED,
                reason_code="rename_destination_conflict",
                error_message=err_msg,
            )
        except Exception as exc:
            err_msg = f"Late rename reassignment failed: {exc}"
            print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
            return ScrubResult(
                input_path=input_path,
                output_path=output_file,
                kind=OutcomeKind.FAILED,
                reason_code="rename_reassignment_failed",
                error_message=err_msg,
            )

    if output_file.absolute() != input_path.absolute() and output_file.is_symlink():
        msg = f"Destination is a symlink; refusing to scrub into {_format_path_with_host(output_file)}"
        print(f"❌ {msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            kind=OutcomeKind.FAILED,
            reason_code=(
                "rename_destination_symlink"
                if rename_requested
                else "destination_symlink"
            ),
            error_message=msg,
        )

    existing_destination = (
        os.path.lexists(output_file)
        and input_path.absolute() != output_file.absolute()
    )
    if existing_destination:
        print(
            "⚠️ Existing destination requires privacy audit and content comparison: "
            f"input={_format_path_with_host(input_path)}, "
            f"output={_format_path_with_host(output_file)}"
        )

        if dry_run:
            print(
                "🔍 [dry-run] Would audit and compare existing destination: "
                f"{_format_path_with_host(output_file)}"
            )
            return ScrubResult(
                input_path=input_path,
                output_path=output_file,
                kind=OutcomeKind.PLANNED,
                reason_code="existing_output_check_planned",
            )

    # dry-run
    if dry_run:
        if show_tags_mode in {"before", "both"}:
            print_tags(input_path, label="before")
        if show_tags_mode in {"after", "both"}:
            print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
        if rename_stem:
            proposed_name = rename_stem + input_path.suffix
            print(f"🔍 Dry run: would scrub {_format_path_with_host(input_path)} → {proposed_name}")
        else:
            print(f"🔍 Dry run: would scrub {_format_path_with_host(input_path)}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            kind=OutcomeKind.PLANNED,
            reason_code="scrub_planned",
        )

    in_place = output_path is None or input_path.resolve() == output_path.resolve()
    if show_tags_mode in {"before", "both"}:
        print_tags(input_path, label="before")

    destination_temp: Path | None = None
    try:
        existing_digest: str | None = None
        if existing_destination:
            try:
                existing_digest = _audit_scrubbed_output(
                    output_file,
                    paranoia,
                    copyright_text,
                    comment_text,
                )
            except (RuntimeError, ValueError) as exc:
                err_msg = f"Existing output failed privacy audit: {exc}"
                print(f"❌ {err_msg}: {_format_path_with_host(output_file)}")
                print(
                    "🛡️ Incoming source left untouched: "
                    f"{_format_path_with_host(input_path)}"
                )
                return ScrubResult(
                    input_path=input_path,
                    output_path=output_file,
                    kind=OutcomeKind.FAILED,
                    reason_code="existing_output_unsafe",
                    error_message=err_msg,
                )

        with tempfile.TemporaryDirectory(prefix="scrubexif_operation_") as operation_dir:
            staged_output = Path(operation_dir) / f"scrubbed{input_path.suffix}"
            _do_scrub_pipeline(
                input_path,
                staged_output,
                paranoia=paranoia,
                copyright_text=copyright_text,
                comment_text=comment_text,
            )
            staged_digest = _audit_scrubbed_output(
                staged_output,
                paranoia,
                copyright_text,
                comment_text,
            )

            if existing_digest is not None:
                if staged_digest == existing_digest:
                    if on_duplicate == "fail":
                        err_msg = (
                            "Verified duplicate rejected by --on-duplicate fail; "
                            "incoming original left untouched"
                        )
                        print(
                            f"❌ {err_msg}: "
                            f"{_format_path_with_host(input_path)}"
                        )
                        return ScrubResult(
                            input_path=input_path,
                            output_path=output_file,
                            kind=OutcomeKind.FAILED,
                            reason_code="duplicate_policy_rejected",
                            error_message=err_msg,
                        )
                    if on_duplicate == "skip":
                        print(
                            "⏭️ Verified duplicate — existing output passed audit; "
                            f"original untouched: {_format_path_with_host(input_path)}"
                        )
                        return ScrubResult(
                            input_path=input_path,
                            output_path=output_file,
                            kind=OutcomeKind.DUPLICATE_HANDLED,
                            reason_code="duplicate_unchanged",
                        )
                    if on_duplicate == "delete":
                        print(
                            "🗑️ Verified duplicate — deleting explicitly configured source: "
                            f"{_format_path_with_host(input_path)}"
                        )
                        try:
                            input_path.unlink()
                        except OSError as exc:
                            err_msg = (
                                "Verified duplicate could not be deleted safely: "
                                f"{exc}"
                            )
                            print(f"❌ {err_msg}")
                            return ScrubResult(
                                input_path=input_path,
                                output_path=output_file,
                                kind=OutcomeKind.FAILED,
                                reason_code="duplicate_delete_failed",
                                error_message=err_msg,
                            )
                        return ScrubResult(
                            input_path=input_path,
                            output_path=output_file,
                            kind=OutcomeKind.DUPLICATE_HANDLED,
                            reason_code="duplicate_deleted",
                        )
                    if on_duplicate == "move":
                        try:
                            target = _archive_no_clobber(input_path, ERRORS_DIR)
                        except (ArchiveError, ValueError) as exc:
                            err_msg = f"Verified duplicate could not be moved safely: {exc}"
                            print(f"❌ {err_msg}")
                            return ScrubResult(
                                input_path=input_path,
                                output_path=output_file,
                                kind=OutcomeKind.FAILED,
                                reason_code="duplicate_move_failed",
                                error_message=err_msg,
                            )
                        print(
                            "📦 Verified duplicate moved to: "
                            f"{_format_path_with_host(target)}"
                        )
                        return ScrubResult(
                            input_path=input_path,
                            output_path=output_file,
                            kind=OutcomeKind.DUPLICATE_HANDLED,
                            reason_code="duplicate_moved",
                            duplicate_path=target,
                        )
                    raise ValueError(f"Unsupported duplicate policy: {on_duplicate}")

                if on_duplicate == "move":
                    try:
                        target = _archive_no_clobber(input_path, ERRORS_DIR)
                    except (ArchiveError, ValueError) as exc:
                        err_msg = f"Collision source could not be moved safely: {exc}"
                        print(f"❌ {err_msg}")
                        return ScrubResult(
                            input_path=input_path,
                            output_path=output_file,
                            kind=OutcomeKind.FAILED,
                            reason_code="destination_collision",
                            error_message=err_msg,
                        )
                    err_msg = (
                        "Same filename has different content; incoming original moved "
                        f"to {_format_path_with_host(target)}"
                    )
                    print(
                        f"❌ Collision, not a duplicate: {_format_path_with_host(output_file)}"
                    )
                    print(f"📦 {err_msg}")
                    return ScrubResult(
                        input_path=input_path,
                        output_path=output_file,
                        kind=OutcomeKind.FAILED,
                        reason_code="destination_collision",
                        error_message=err_msg,
                        duplicate_path=target,
                    )

                err_msg = "Same filename has different content; original left untouched"
                print(
                    f"❌ Collision, not a duplicate: {_format_path_with_host(output_file)}"
                )
                return ScrubResult(
                    input_path=input_path,
                    output_path=output_file,
                    kind=OutcomeKind.FAILED,
                    reason_code="destination_collision",
                    error_message=err_msg,
                )

            destination_directory = input_path.parent if in_place else output_file.parent
            destination_temp = _copy_audited_output_to_destination(
                staged_output,
                destination_directory,
                staged_digest,
            )

        if (in_place and output_file.absolute() != input_path.absolute()) or not in_place:
            while True:
                try:
                    _publish_no_clobber(destination_temp, output_file)
                    destination_temp = None
                    break
                except FileExistsError:
                    if not rename_requested:
                        raise
                    output_file = reassign_late_destination(output_file)
                    rename_stem = output_file.stem
        elif in_place:
            os.replace(destination_temp, input_path)
            destination_temp = None
    except RenamePlanningError as exc:
        err_msg = str(exc)
        print(f"❌ Rename conflict for {_format_path_with_host(input_path)}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            kind=OutcomeKind.FAILED,
            reason_code="rename_publication_conflict",
            error_message=err_msg,
        )
    except FileExistsError:
        err_msg = "Destination appeared during scrub; refusing to overwrite"
        print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            kind=OutcomeKind.FAILED,
            reason_code=(
                "rename_destination_appeared"
                if rename_requested
                else "destination_appeared"
            ),
            error_message=err_msg,
        )
    except Exception as exc:
        err_msg = str(exc)
        print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            kind=OutcomeKind.FAILED,
            reason_code="scrub_failed",
            error_message=err_msg,
        )
    finally:
        if destination_temp is not None:
            try:
                destination_temp.unlink(missing_ok=True)
            except OSError as exc:
                log.error(
                    "Failed to remove destination staging file %s: %s",
                    destination_temp,
                    exc,
                )

    if show_tags_mode in {"after", "both"}:
        print_tags(output_file, label="after")

    def display_path(path: Path) -> str:
        return _format_relative_path_with_host(path)

    print(f"✅ Saved scrubbed file to {display_path(output_file)}")

    if in_place and output_file.absolute() != input_path.absolute():
        try:
            input_path.unlink()
        except OSError as exc:
            err_msg = f"Scrub succeeded but renamed source removal failed: {exc}"
            print(f"❌ {err_msg}")
            return ScrubResult(
                input_path=input_path,
                output_path=output_file,
                kind=OutcomeKind.FAILED,
                reason_code="renamed_source_removal_failed",
                error_message=err_msg,
            )

    if delete_original and not in_place and input_path.exists():
        try:
            input_path.unlink()
        except OSError as exc:
            err_msg = f"Scrub succeeded but original deletion failed: {exc}"
            print(f"❌ {err_msg}")
            return ScrubResult(
                input_path=input_path,
                output_path=output_file,
                kind=OutcomeKind.FAILED,
                reason_code="source_deletion_failed",
                error_message=err_msg,
            )
        print(f"🗑️ Deleted original: {_format_path_with_host(input_path)}")

    return ScrubResult(
        input_path=input_path,
        output_path=output_file,
        kind=OutcomeKind.SCRUBBED,
        reason_code="output_published",
    )


def _iter_regular_files_in_dir(
    dir_path: Path,
    recursive: bool = False,
) -> Iterator[Path]:
    """Stream non-symlink regular files from a directory.

    Args:
        dir_path: Directory to scan.
        recursive: Whether to descend into subdirectories.

    Yields:
        Regular, non-symlink paths in filesystem traversal order.

    Raises:
        ValueError: If dir_path is not a pathlib.Path.
    """
    if not isinstance(dir_path, Path):
        raise ValueError("dir_path must be a pathlib.Path")
    if not dir_path.is_dir():
        return
    search_func = dir_path.rglob if recursive else dir_path.glob
    for path in search_func("*"):
        if path.is_symlink():
            log.debug("Skipping symlinked file: %s", path)
            continue
        if path.is_file():
            yield path


def _unsupported_outcome(path: Path) -> FileOutcome:
    """Create an unsupported terminal outcome for one regular file.

    Args:
        path: Unsupported regular-file input.

    Returns:
        Validated unsupported outcome.
    """
    return FileOutcome(
        input_path=path,
        input_classification=InputClassification.UNSUPPORTED_REGULAR,
        kind=OutcomeKind.UNSUPPORTED,
        reason_code="unsupported_extension",
        phase=ProcessingPhase.DISCOVERY,
    )


def _skipped_outcome(path: Path, reason_code: str) -> FileOutcome:
    """Create a discovery-time skipped JPEG outcome.

    Args:
        path: Eligible JPEG deliberately excluded from processing.
        reason_code: Stable explanation for the exclusion.

    Returns:
        Validated skipped outcome.
    """
    return FileOutcome(
        input_path=path,
        input_classification=InputClassification.ELIGIBLE_JPEG,
        kind=OutcomeKind.SKIPPED,
        reason_code=reason_code,
        phase=ProcessingPhase.DISCOVERY,
    )


def _planned_outcome(path: Path, destination: Path | None) -> FileOutcome:
    """Create a mode-level dry-run outcome without claiming publication.

    Args:
        path: Eligible JPEG evaluated by dry-run mode.
        destination: Intended destination when the mode selected one.

    Returns:
        Validated planned outcome.
    """
    return FileOutcome(
        input_path=path,
        input_classification=InputClassification.ELIGIBLE_JPEG,
        kind=OutcomeKind.PLANNED,
        reason_code="dry_run_planned",
        phase=ProcessingPhase.PLANNING,
        intended_output_path=destination,
    )


def find_jpegs_in_dir(dir_path: Path, recursive: bool = False) -> list[Path]:
    """Collect non-symlink JPEG files from a directory.

    Args:
        dir_path: Directory to scan.
        recursive: Whether to descend into subdirectories.

    Returns:
        Eligible JPEG paths in filesystem traversal order.
    """
    return list(iter_jpegs_in_dir(dir_path, recursive=recursive))


def iter_jpegs_in_dir(dir_path: Path, recursive: bool = False) -> Iterator[Path]:
    """Stream non-symlink JPEG files from a directory.

    Args:
        dir_path: Directory to scan.
        recursive: Whether to descend into subdirectories.

    Yields:
        Eligible JPEG paths.

    Raises:
        ValueError: If dir_path is not a pathlib.Path.
    """
    for path in _iter_regular_files_in_dir(dir_path, recursive=recursive):
        if path.suffix.lower() in (".jpg", ".jpeg"):
            yield path


def _iter_scope_regular_paths(
    input_paths: Iterable[Path],
    recursive: bool,
) -> Iterator[Path]:
    """Expand explicit files and directories into regular non-symlink paths.

    Args:
        input_paths: Explicit file or directory paths in discovery order.
        recursive: Whether directory inputs are traversed recursively.

    Yields:
        Regular non-symlink paths before classification or deduplication.

    Raises:
        ValueError: If an input is not a pathlib.Path.
    """
    for input_path in input_paths:
        if not isinstance(input_path, Path):
            raise ValueError("input_paths must contain only pathlib.Path values")
        if input_path.is_symlink():
            log.debug("Skipping symlink during discovery: %s", input_path)
            continue
        if input_path.is_file():
            yield input_path
            continue
        if input_path.is_dir():
            yield from _iter_regular_files_in_dir(
                input_path,
                recursive=recursive,
            )


def _filesystem_identity(file_stat: os.stat_result) -> tuple[int, int] | None:
    """Return a stable filesystem identity when the inode is usable.

    Args:
        file_stat: Non-symlink stat result captured during discovery.

    Returns:
        Device and inode pair, or None when the filesystem supplies no inode.
    """
    if file_stat.st_ino <= 0:
        return None
    return file_stat.st_dev, file_stat.st_ino


def _normalized_discovery_path(path: Path) -> tuple[Path, str]:
    """Resolve one existing path and derive its pathname deduplication key.

    Args:
        path: Existing non-symlink regular-file path.

    Returns:
        Resolved path and normalized case-aware string key.

    Raises:
        OSError: If the path cannot be resolved.
        ValueError: If path is not a pathlib.Path.
    """
    if not isinstance(path, Path):
        raise ValueError("path must be a pathlib.Path")
    resolved = path.resolve(strict=True)
    return resolved, os.path.normcase(str(resolved))


def _discover_inputs(
    input_paths: Iterable[Path],
    *,
    recursive: bool,
    exclude_pipeline_paths: bool,
    max_files: int | None,
    skip_reason_for_jpeg: Callable[[Path], str | None] | None = None,
) -> list[DiscoveredInput]:
    """Build one bounded, classified inventory for an operational mode.

    Unsupported and skipped entries are retained while searching for admitted
    JPEG candidates, but neither category consumes a ``max_files`` slot.
    Distinct hard-link pathnames remain separate inventory entries.

    Args:
        input_paths: Explicit file or directory roots in discovery order.
        recursive: Whether directory roots are traversed recursively.
        exclude_pipeline_paths: Whether internal scrubexif directories are out
            of scope.
        max_files: Maximum JPEG candidates admitted for an operation attempt.
        skip_reason_for_jpeg: Optional classifier for eligible JPEGs that must
            be skipped before admission.

    Returns:
        Classified entries up to and including the final admitted candidate.

    Raises:
        ValueError: If an argument or skip reason is invalid.
    """
    if not isinstance(recursive, bool):
        raise ValueError("recursive must be a boolean")
    if not isinstance(exclude_pipeline_paths, bool):
        raise ValueError("exclude_pipeline_paths must be a boolean")
    if max_files is not None and (
        not isinstance(max_files, int)
        or isinstance(max_files, bool)
        or max_files < 0
    ):
        raise ValueError("max_files must be a non-negative integer or None")
    if skip_reason_for_jpeg is not None and not callable(skip_reason_for_jpeg):
        raise ValueError("skip_reason_for_jpeg must be callable or None")
    if max_files == 0:
        return []

    entries: list[DiscoveredInput] = []
    seen_paths: set[str] = set()
    first_path_by_identity: dict[tuple[int, int], Path] = {}
    admitted_count = 0

    for candidate in _iter_scope_regular_paths(input_paths, recursive):
        if exclude_pipeline_paths and _is_pipeline_path(candidate):
            continue
        try:
            file_stat = candidate.stat(follow_symlinks=False)
            resolved, path_key = _normalized_discovery_path(candidate)
        except OSError as exc:
            log.warning("Discovery skipped inaccessible path %s: %s", candidate, exc)
            continue
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            log.debug("Skipping non-regular discovery path: %s", candidate)
            continue
        if exclude_pipeline_paths and _is_pipeline_path(resolved):
            continue
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)

        identity = _filesystem_identity(file_stat)
        if identity is None:
            log.warning(
                "Filesystem identity unavailable for %s; using resolved-path "
                "fallback",
                resolved,
            )
        else:
            alias = first_path_by_identity.get(identity)
            if alias is None:
                first_path_by_identity[identity] = resolved
            elif alias != resolved:
                log.debug(
                    "Hard-link aliases remain separate inputs: %s and %s",
                    alias,
                    resolved,
                )

        classification = InputClassification.UNSUPPORTED_REGULAR
        skip_reason: str | None = None
        if resolved.suffix.lower() in (".jpg", ".jpeg"):
            classification = InputClassification.ELIGIBLE_JPEG
            if skip_reason_for_jpeg is not None:
                skip_reason = skip_reason_for_jpeg(resolved)
                if skip_reason is not None:
                    validate_reason_code(skip_reason)

        entry = DiscoveredInput(
            path=resolved,
            path_key=path_key,
            classification=classification,
            filesystem_identity=identity,
            skip_reason=skip_reason,
        )
        entries.append(entry)
        if entry.admitted:
            admitted_count += 1
            if max_files is not None and admitted_count >= max_files:
                break

    return entries


def _record_discovery_outcomes(
    summary: ScrubSummary,
    entries: Iterable[DiscoveredInput],
) -> list[DiscoveredInput]:
    """Record unsupported and skipped entries and return admitted JPEGs.

    Args:
        summary: Report-backed compatibility summary for this invocation.
        entries: Classified discovery entries.

    Returns:
        Fresh list of JPEG entries admitted for an operation attempt.

    Raises:
        ValueError: If an entry has an invalid type.
    """
    admitted: list[DiscoveredInput] = []
    for entry in entries:
        if not isinstance(entry, DiscoveredInput):
            raise ValueError("entries must contain only DiscoveredInput values")
        if entry.classification is InputClassification.UNSUPPORTED_REGULAR:
            summary.record(_unsupported_outcome(entry.path))
            continue
        if entry.skip_reason is not None:
            summary.record(_skipped_outcome(entry.path, entry.skip_reason))
            continue
        admitted.append(entry)
    return admitted


def _discovery_revalidation_failure(
    entry: DiscoveredInput,
) -> FileOutcome | None:
    """Revalidate one admitted input immediately before using its bytes.

    Args:
        entry: Discovery observation to compare with current filesystem state.

    Returns:
        Failed outcome when the path changed or became unsafe, otherwise None.

    Raises:
        ValueError: If entry is invalid or was not admitted.
    """
    if not isinstance(entry, DiscoveredInput):
        raise ValueError("entry must be a DiscoveredInput")
    if not entry.admitted:
        raise ValueError("only an admitted JPEG can be revalidated")

    diagnostic: str | None = None
    try:
        current_stat = entry.path.stat(follow_symlinks=False)
        if stat.S_ISLNK(current_stat.st_mode) or not stat.S_ISREG(current_stat.st_mode):
            diagnostic = "Input became a symlink or non-regular file after discovery"
        elif entry.filesystem_identity is not None:
            current_identity = _filesystem_identity(current_stat)
            if current_identity != entry.filesystem_identity:
                diagnostic = "Input filesystem identity changed after discovery"
        else:
            _, current_key = _normalized_discovery_path(entry.path)
            if current_key != entry.path_key:
                diagnostic = "Input resolved pathname changed after discovery"
    except OSError as exc:
        diagnostic = f"Input became inaccessible after discovery: {exc}"

    if diagnostic is None:
        return None
    return FileOutcome(
        input_path=entry.path,
        input_classification=InputClassification.ELIGIBLE_JPEG,
        kind=OutcomeKind.FAILED,
        reason_code="input_changed_after_discovery",
        phase=ProcessingPhase.DISCOVERY,
        diagnostic=diagnostic,
    )


def _revalidate_or_record(
    summary: ScrubSummary,
    entry: DiscoveredInput,
) -> bool:
    """Record a discovery-race failure or approve the entry for processing.

    Args:
        summary: Report-backed compatibility summary for this invocation.
        entry: Admitted JPEG observation to revalidate.

    Returns:
        True only when the original discovered file is still safe to process.
    """
    failure = _discovery_revalidation_failure(entry)
    if failure is None:
        return True
    print(f"❌ {failure.diagnostic}: {_format_path_with_host(entry.path)}")
    summary.record(failure)
    return False


def _iter_revalidated_paths(
    summary: ScrubSummary,
    entries: Iterable[DiscoveredInput],
) -> Iterator[Path]:
    """Yield admitted paths that still match their discovery observations.

    Args:
        summary: Report-backed compatibility summary for this invocation.
        entries: Admitted JPEG observations.

    Yields:
        Paths approved for planning or immediate processing.
    """
    for entry in entries:
        if _revalidate_or_record(summary, entry):
            yield entry.path


def _build_rename_plan_or_exit(
    source_paths: Iterable[Path],
    rename_format: str,
    output_directory: Path | None,
    rename_counter: dict[str, int],
    limits: RenamePlanLimits,
) -> RenamePlan:
    """Build a rename plan and convert safe planning failures to CLI errors.

    Args:
        source_paths: Stream of source paths.
        rename_format: Validated rename format.
        output_directory: Shared output directory, or None for clean-inline.
        rename_counter: Per-invocation sequential-token counter.
        limits: Planning resource limits.

    Returns:
        Completed disk-backed rename plan.

    Raises:
        SystemExit: If collision-safe planning cannot complete.
    """
    try:
        return build_rename_plan(
            source_paths,
            rename_format,
            output_directory,
            rename_counter,
            limits,
        )
    except (OSError, RenamePlanningError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _finalize_auto_result(
    file: Path,
    result: ScrubResult,
    summary: ScrubSummary,
    delete_original: bool,
    state: dict[str, dict[str, float | int]],
) -> None:
    """Update auto-mode state and move a processed or failed source.

    Args:
        file: Original intake path.
        result: Scrub operation result.
        summary: Mutable per-run summary.
        delete_original: Whether successful originals are deleted by scrub_file.
        state: Mutable stability-state mapping.

    Returns:
        None.
    """
    outcome = _scrub_result_to_file_outcome(result)

    if result.kind is OutcomeKind.SCRUBBED:
        if delete_original:
            outcome = _scrub_result_to_file_outcome(
                result,
                SourceDisposition.DELETED,
            )
        else:
            try:
                archived_path = _archive_no_clobber(file, PROCESSED_DIR)
            except (ArchiveError, ValueError) as exc:
                diagnostic = (
                    f"Scrub succeeded for {_format_path_with_host(file)}, but the "
                    f"original could not be archived safely: {exc}"
                )
                print(f"❌ {diagnostic}")
                outcome = FileOutcome(
                    input_path=file,
                    input_classification=InputClassification.ELIGIBLE_JPEG,
                    kind=OutcomeKind.FAILED,
                    reason_code="source_archive_failed",
                    phase=ProcessingPhase.SOURCE_FINALIZATION,
                    intended_output_path=result.output_path,
                    actual_output_path=result.output_path,
                    published=True,
                    source_disposition=SourceDisposition.UNCHANGED,
                    diagnostic=diagnostic,
                )
            else:
                outcome = _scrub_result_to_file_outcome(
                    result,
                    SourceDisposition.MOVED,
                )
                print(
                    f"📦 Moved original to {_format_path_with_host(archived_path)}"
                )
    elif result.reason_code in {
        "renamed_source_removal_failed",
        "source_deletion_failed",
    }:
        print(
            f"⚠️ Scrub output was created for {_format_path_with_host(file)}, "
            "but source post-processing failed; leaving the source in place"
        )
    elif result.reason_code == "existing_output_unsafe":
        print(
            f"🛡️ Existing output is unsafe for {_format_path_with_host(file)}; "
            "leaving the incoming source in place"
        )
    elif result.reason_code == "destination_collision":
        if result.duplicate_path is not None:
            print(
                f"⚠️ Filename collision for {_format_path_with_host(file)}; "
                f"incoming original preserved at "
                f"{_format_path_with_host(result.duplicate_path)}"
            )
        else:
            print(
                f"⚠️ Filename collision for {_format_path_with_host(file)}; "
                "leaving the incoming source in place"
            )
    elif result.reason_code == "duplicate_policy_rejected":
        print(
            f"🛡️ Verified duplicate rejected for {_format_path_with_host(file)}; "
            "leaving the incoming source in place"
        )
    elif result.reason_code in {
        "duplicate_delete_failed",
        "duplicate_move_failed",
    }:
        print(
            f"🛡️ Verified duplicate disposition failed for "
            f"{_format_path_with_host(file)}; leaving the incoming source in place"
        )
    elif result.reason_code in {
        "rename_destination_appeared",
        "rename_destination_conflict",
        "rename_destination_symlink",
        "rename_planning_failed",
        "rename_publication_conflict",
    }:
        print(
            f"⚠️ Rename destination conflict for {_format_path_with_host(file)}; "
            "leaving original in place"
        )
    elif result.kind is OutcomeKind.FAILED:
        if file.exists():
            try:
                archived_path = _archive_no_clobber(file, PROCESSED_DIR)
            except (ArchiveError, ValueError) as exc:
                print(
                    f"⚠️ Scrub failed for {_format_path_with_host(file)} and the "
                    f"original could not be archived safely; leaving it in place: {exc}"
                )
            else:
                outcome = _scrub_result_to_file_outcome(
                    result,
                    SourceDisposition.MOVED,
                )
                print(
                    f"⚠️ Scrub failed for {_format_path_with_host(file)}; "
                    f"moved original to {_format_path_with_host(archived_path)} "
                    "for inspection"
                )
        else:
            outcome = _scrub_result_to_file_outcome(
                result,
                SourceDisposition.MISSING,
            )
            print(
                f"⚠️ Scrub failed and source already missing: "
                f"{_format_path_with_host(file)}"
            )

    summary.record(outcome)
    mark_seen(file, state)


def auto_scrub(
    summary: ScrubSummary,
    dry_run: bool = False,
    delete_original: bool = False,
    show_tags_mode: str | None = None,
    paranoia: bool = True,
    max_files: int | None = None,
    on_duplicate: str = "move",
    stable_seconds: int = 120,
    copyright_text: str | None = None,
    comment_text: str | None = None,
    rename_format: str | None = None,
    rename_counter: dict[str, int] | None = None,
    rename_plan_limits: RenamePlanLimits | None = None,
) -> ScrubSummary:
    """Process stable JPEGs from the managed auto-mode intake directory.

    Args:
        summary: Report-backed summary owned by this invocation.
        dry_run: Whether admitted JPEGs are planned without publication.
        delete_original: Whether successful sources are deleted instead of archived.
        show_tags_mode: Optional before/after metadata display mode.
        paranoia: Whether the maximum-scrubbing policy is active.
        max_files: Maximum stable JPEG candidates admitted for operation attempts.
        on_duplicate: Policy for a verified existing identical output.
        stable_seconds: Minimum source modification age.
        copyright_text: Optional copyright metadata to retain.
        comment_text: Optional comment metadata to retain.
        rename_format: Optional validated output naming format.
        rename_counter: Per-invocation sequential rename state.
        rename_plan_limits: Resource limits for disk-backed rename planning.

    Returns:
        The supplied summary after all discovered outcomes are recorded.

    Raises:
        SystemExit: If safety validation or rename planning fails.
        ValueError: If a discovery or processing argument is invalid.
    """
    print(f"🚀 Auto mode: Scrubbing JPEGs in {_format_path_with_host(INPUT_DIR)}")
    print(f"📁 Output directory: {_format_path_with_host(OUTPUT_DIR)}")
    print(f"📁 Processed directory: {_format_path_with_host(PROCESSED_DIR)}")
    if on_duplicate == "move":
        print(f"📁 Errors directory: {_format_path_with_host(ERRORS_DIR)}")
    print(f"⏳ Stability threshold: {stable_seconds}s")
    if STATE_FILE is None:
        print("ℹ️ Stability state: mtime-only (no writable state file)")

    # Safety
    check_dir_safety(INPUT_DIR, "Input")
    check_dir_safety(OUTPUT_DIR, "Output")
    check_dir_safety(PROCESSED_DIR, "Processed")

    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "Auto mode directories: input=%s output=%s processed=%s errors=%s",
            INPUT_DIR, OUTPUT_DIR, PROCESSED_DIR, ERRORS_DIR
        )

    state = load_state()
    prune_state(state)

    def auto_skip_reason(path: Path) -> str | None:
        """Classify an eligible auto-mode JPEG before limit admission.

        Args:
            path: Eligible JPEG candidate observed during discovery.

        Returns:
            Stable skip reason for temporary or unstable input, otherwise None.
        """
        if is_probably_temp(path):
            return "temporary_input"
        if not is_file_stable(path, state, stable_seconds):
            return "unstable_input"
        return None

    discovered = _discover_inputs(
        [INPUT_DIR],
        recursive=False,
        exclude_pipeline_paths=False,
        max_files=max_files,
        skip_reason_for_jpeg=auto_skip_reason,
    )
    admitted = _record_discovery_outcomes(summary, discovered)
    skipped_temp = 0
    skipped_unstable = 0
    for entry in discovered:
        if entry.skip_reason == "temporary_input":
            skipped_temp += 1
            mark_seen(entry.path, state)
        elif entry.skip_reason == "unstable_input":
            skipped_unstable += 1
            mark_seen(entry.path, state)

    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "Auto discovery admitted %d JPEGs (skipped temp=%d, unstable=%d)",
            len(admitted),
            skipped_temp,
            skipped_unstable,
        )

    if rename_format is not None:
        counter = rename_counter if rename_counter is not None else {"n": 0}
        limits = rename_plan_limits or RenamePlanLimits()
        source_paths = _iter_revalidated_paths(summary, admitted)
        admitted_by_path = {entry.path: entry for entry in admitted}
        with _build_rename_plan_or_exit(
            source_paths,
            rename_format,
            OUTPUT_DIR,
            counter,
            limits,
        ) as rename_plan:
            if rename_plan.count == 0:
                if skipped_temp or skipped_unstable:
                    print(
                        "ℹ️ Nothing eligible yet. Skipped: "
                        f"temp={skipped_temp}, unstable={skipped_unstable}."
                    )
                else:
                    print("⚠️ No JPEGs found — nothing to do.")
                save_state(state)
                return summary

            if not dry_run:
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

            for entry in rename_plan:
                discovered_entry = admitted_by_path.get(entry.source_path)
                if discovered_entry is None:
                    raise RuntimeError(
                        f"Rename plan returned an undiscovered source: "
                        f"{entry.source_path}"
                    )
                if not _revalidate_or_record(summary, discovered_entry):
                    continue
                if dry_run:
                    if show_tags_mode in {"before", "both"}:
                        print_tags(entry.source_path, label="before")
                    if show_tags_mode in {"after", "both"}:
                        print(
                            "⚠️ Cannot show tags *after* scrub in dry-run mode "
                            "(no scrub performed)."
                        )
                    print(
                        f"🔍 Would scrub: {_format_path_with_host(entry.source_path)} "
                        f"→ {_format_path_with_host(entry.destination_path)}"
                    )
                    summary.record(
                        _planned_outcome(
                            entry.source_path,
                            entry.destination_path,
                        )
                    )
                    continue

                result = scrub_file(
                    entry.source_path,
                    OUTPUT_DIR,
                    delete_original=delete_original,
                    show_tags_mode=show_tags_mode,
                    paranoia=paranoia,
                    on_duplicate=on_duplicate,
                    copyright_text=copyright_text,
                    comment_text=comment_text,
                    planned_rename_path=entry.destination_path,
                    rename_destination_allocator=partial(
                        rename_plan.reassign_destination,
                        entry.entry_id,
                    ),
                )
                _finalize_auto_result(
                    entry.source_path,
                    result,
                    summary,
                    delete_original,
                    state,
                )

        save_state(state)
        return summary

    if not admitted:
        if skipped_temp or skipped_unstable:
            print(f"ℹ️ Nothing eligible yet. Skipped: temp={skipped_temp}, unstable={skipped_unstable}.")
        else:
            print("⚠️ No JPEGs found — nothing to do.")
        save_state(state)
        return summary

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    for discovered_entry in admitted:
        if not _revalidate_or_record(summary, discovered_entry):
            continue
        file = discovered_entry.path
        if dry_run:
            if show_tags_mode in {"before", "both"}:
                print_tags(file, label="before")
            if show_tags_mode in {"after", "both"}:
                print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
            print(f"🔍 Would scrub: {_format_path_with_host(file)}")
            summary.record(_planned_outcome(file, OUTPUT_DIR / file.name))
            continue

        result = scrub_file(
            file,
            OUTPUT_DIR,
            delete_original=delete_original,
            show_tags_mode=show_tags_mode,
            paranoia=paranoia,
            on_duplicate=on_duplicate,
            copyright_text=copyright_text,
            comment_text=comment_text,
            rename_format=rename_format,
            rename_counter=rename_counter,
        )

        _finalize_auto_result(file, result, summary, delete_original, state)

    save_state(state)
    return summary


def _is_pipeline_path(path: Path) -> bool:
    """Check whether a source belongs to an internal pipeline directory.

    Args:
        path: Candidate source path.

    Returns:
        True when the path is inside input, output, processed, or errors.
    """
    for special in (INPUT_DIR, OUTPUT_DIR, PROCESSED_DIR, ERRORS_DIR):
        try:
            path.relative_to(special)
            return True
        except ValueError:
            continue
    return False


def simple_scrub(
    summary: ScrubSummary,
    recursive: bool = False,
    dry_run: bool = False,
    show_tags_mode: str | None = None,
    paranoia: bool = True,
    max_files: int | None = None,
    output_explicit: bool = False,
    copyright_text: str | None = None,
    comment_text: str | None = None,
    rename_format: str | None = None,
    rename_counter: dict[str, int] | None = None,
    rename_plan_limits: RenamePlanLimits | None = None,
    explicit_files: list[Path] | None = None,
    preview: bool = False,
) -> ScrubSummary:
    """Write or preview scrubbed copies while leaving sources unchanged.

    Args:
        summary: Report-backed summary owned by this invocation.
        recursive: Whether directory inputs are traversed recursively.
        dry_run: Whether admitted JPEGs are planned without publication.
        show_tags_mode: Optional before/after metadata display mode.
        paranoia: Whether the maximum-scrubbing policy is active.
        max_files: Maximum JPEG candidates admitted for operation attempts.
        output_explicit: True when the caller supplied -o on the CLI. A
            pre-existing output directory is accepted in that case because the
            user stated intent (e.g. via a bind-mount). When False (default)
            a pre-existing directory is refused to prevent accidental clobbering.
        copyright_text: Optional copyright metadata to retain.
        comment_text: Optional comment metadata to retain.
        rename_format: Optional validated output naming format.
        rename_counter: Per-invocation sequential rename state.
        rename_plan_limits: Resource limits for disk-backed rename planning.
        explicit_files: When set, process only these resolved paths instead of
            scanning PHOTOS_ROOT. Internal pipeline directories remain excluded.
        preview: Whether admitted JPEGs are scrubbed only in temporary storage.

    Returns:
        The supplied summary after all discovered outcomes are recorded.

    Raises:
        SystemExit: If directory safety validation or rename planning fails.
        ValueError: If a discovery or processing argument is invalid.
    """
    if not isinstance(preview, bool):
        raise ValueError("preview must be a boolean")

    host_root = _resolve_mount_source(PHOTOS_ROOT)
    if explicit_files is not None:
        operation = "Previewing" if preview else "Scrubbing"
        print(
            f"🚀 Default safe mode: {operation} "
            f"{len(explicit_files)} explicit file(s)"
        )
    else:
        operation = "Previewing" if preview else "Scrubbing"
        print(
            f"🚀 Default safe mode: {operation} JPEGs in "
            f"{_format_path_with_host(PHOTOS_ROOT)}"
        )
    output_label = "Planned output directory" if preview else "Output directory"
    print(f"📁 {output_label}: {_format_path_with_host(OUTPUT_DIR)}")

    # Safety: /photos must exist and be usable
    check_dir_safety(PHOTOS_ROOT, "Photos root")

    if not preview:
        if OUTPUT_DIR.exists():
            if not output_explicit:
                print(f"⚠️ Output directory already exists: {_format_path_with_host(OUTPUT_DIR)}")
                print("⚠️ Refusing to run in default safe mode. Remove it or use --clean-inline/--from-input.")
                sys.exit(1)
            # output_explicit=True: user passed -o, pre-existing directory is intentional
        else:
            try:
                OUTPUT_DIR.mkdir(parents=True, exist_ok=False)
            except Exception as exc:
                print(f"❌ Failed to create output directory {_format_path_with_host(OUTPUT_DIR)}: {exc}")
                sys.exit(1)

        check_dir_safety(OUTPUT_DIR, "Output")

    discovery_roots = explicit_files if explicit_files is not None else [PHOTOS_ROOT]
    discovered = _discover_inputs(
        discovery_roots,
        recursive=recursive,
        exclude_pipeline_paths=True,
        max_files=max_files,
    )
    admitted = _record_discovery_outcomes(summary, discovered)
    admitted_by_path = {entry.path: entry for entry in admitted}

    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "Default safe-mode discovery examined %d entries and admitted %d "
            "JPEGs (explicit=%s, recursive=%s)",
            len(discovered),
            len(admitted),
            explicit_files is not None,
            recursive,
        )

    if rename_format is not None:
        counter = rename_counter if rename_counter is not None else {"n": 0}
        limits = rename_plan_limits or RenamePlanLimits()
        source_paths = _iter_revalidated_paths(summary, admitted)
        with _build_rename_plan_or_exit(
            source_paths,
            rename_format,
            OUTPUT_DIR,
            counter,
            limits,
        ) as rename_plan:
            if rename_plan.count == 0:
                print("⚠️ No eligible JPEGs found in default safe mode.")
                return summary

            for entry in rename_plan:
                discovered_entry = admitted_by_path.get(entry.source_path)
                if discovered_entry is None:
                    raise RuntimeError(
                        f"Rename plan returned an undiscovered source: "
                        f"{entry.source_path}"
                    )
                if not _revalidate_or_record(summary, discovered_entry):
                    continue
                if preview:
                    print(
                        f"🔍 [default] Would scrub: "
                        f"{_format_path_with_host(entry.source_path)} "
                        f"→ {_format_path_with_host(entry.destination_path)}"
                    )
                    summary.record(
                        _preview_scrub(
                            entry.source_path,
                            show_tags_mode,
                            paranoia,
                            copyright_text,
                            comment_text,
                            intended_output_path=entry.destination_path,
                        )
                    )
                    continue
                if dry_run:
                    if show_tags_mode in {"before", "both"}:
                        print_tags(entry.source_path, label="before")
                    if show_tags_mode in {"after", "both"}:
                        print(
                            "⚠️ Cannot show tags *after* scrub in dry-run mode "
                            "(no scrub performed)."
                        )
                    print(
                        f"🔍 [default] Would scrub: "
                        f"{_format_path_with_host(entry.source_path)} "
                        f"→ {_format_path_with_host(entry.destination_path)}"
                    )
                    summary.record(
                        _planned_outcome(
                            entry.source_path,
                            entry.destination_path,
                        )
                    )
                    continue

                result = scrub_file(
                    entry.source_path,
                    output_path=OUTPUT_DIR,
                    delete_original=False,
                    dry_run=False,
                    show_tags_mode=show_tags_mode,
                    paranoia=paranoia,
                    on_duplicate="skip",
                    copyright_text=copyright_text,
                    comment_text=comment_text,
                    planned_rename_path=entry.destination_path,
                    rename_destination_allocator=partial(
                        rename_plan.reassign_destination,
                        entry.entry_id,
                    ),
                )
                summary.update(result)
        return summary

    if not admitted:
        print("⚠️ No eligible JPEGs found in default safe mode.")
        return summary

    for discovered_entry in admitted:
        if not _revalidate_or_record(summary, discovered_entry):
            continue
        f = discovered_entry.path
        dst = OUTPUT_DIR / f.name

        if preview:
            print(
                f"🔍 [default] Would scrub: {_format_path_with_host(f)} "
                f"→ {_format_path_with_host(dst)}"
            )
            summary.record(
                _preview_scrub(
                    f,
                    show_tags_mode,
                    paranoia,
                    copyright_text,
                    comment_text,
                    intended_output_path=dst,
                )
            )
            continue

        if dry_run:
            if show_tags_mode in {"before", "both"}:
                print_tags(f, label="before")
            if show_tags_mode in {"after", "both"}:
                print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
            print(f"🔍 [default] Would scrub: {_format_path_with_host(f)} -> {_format_path_with_host(dst)}")
            summary.record(_planned_outcome(f, dst))
            continue

        result = scrub_file(
            f,
            output_path=OUTPUT_DIR,
            delete_original=False,
            dry_run=False,
            show_tags_mode=show_tags_mode,
            paranoia=paranoia,
            on_duplicate="skip",  # safe mode: never delete or move originals
            copyright_text=copyright_text,
            comment_text=comment_text,
            rename_format=rename_format,
            rename_counter=rename_counter,
        )
        summary.update(result)

    return summary


def resolve_cli_path(raw: Path) -> Path:
    """
    Convert user-supplied CLI paths into absolute paths under /photos.
    Reject anything that escapes the allowed root to avoid clobbering arbitrary files.
    """
    candidate = raw if raw.is_absolute() else PHOTOS_ROOT / raw
    if candidate.is_symlink():
        print(f"❌ Symlinks are not allowed: {candidate}", file=sys.stderr)
        sys.exit(1)
    try:
        resolved = candidate.resolve()
    except FileNotFoundError:
        print(f"❌ Path does not exist: {candidate}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"❌ Failed to resolve path {raw}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        resolved.relative_to(PHOTOS_ROOT)
    except ValueError:
        print(f"❌ Path escapes allowed root {PHOTOS_ROOT}: {raw}", file=sys.stderr)
        sys.exit(1)
    return resolved


def _preview_scrub(
    source_path: Path,
    show_tags_mode: str | None,
    paranoia: bool,
    copyright_text: str | None,
    comment_text: str | None,
    intended_output_path: Path | None = None,
) -> FileOutcome:
    """Scrub a disposable copy and display its resulting metadata.

    Args:
        source_path: Source JPEG that must remain untouched.
        show_tags_mode: Requested tag display mode.
        paranoia: Whether to use the maximum-scrubbing pipeline.
        copyright_text: Optional copyright value to stamp.
        comment_text: Optional comment value to stamp.
        intended_output_path: Destination the equivalent real run would use.

    Returns:
        Validated preview success or failure outcome.
    """
    preview_fd, preview_tmp_str = tempfile.mkstemp(suffix=source_path.suffix)
    os.close(preview_fd)
    preview_input = Path(preview_tmp_str)
    preview_output: Path | None = None
    succeeded = False
    diagnostic: str | None = None

    try:
        preview_output = _create_temp_output(preview_input.parent, source_path.suffix)
        shutil.copy(source_path, preview_input)
        _do_scrub_pipeline(
            preview_input,
            preview_output,
            paranoia=paranoia,
            copyright_text=copyright_text,
            comment_text=comment_text,
        )
        _audit_scrubbed_output(
            preview_output,
            paranoia,
            copyright_text,
            comment_text,
        )
        if show_tags_mode in {"before", "both"}:
            print_tags(source_path, label="before")
        if show_tags_mode in {"after", "both"}:
            print_tags(preview_output, label="after")
        succeeded = True
    except (OSError, RuntimeError, ValueError) as exc:
        diagnostic = f"Preview scrub failed: {exc}"
        print(f"❌ {diagnostic}")
    finally:
        preview_input.unlink(missing_ok=True)
        if preview_output is not None:
            preview_output.unlink(missing_ok=True)

    if succeeded:
        print(
            f"📊 Preview complete for {_format_path_with_host(source_path)} "
            "— original file was not modified."
        )
        return FileOutcome(
            input_path=source_path,
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.PREVIEWED,
            reason_code="preview_audited",
            phase=ProcessingPhase.VERIFICATION,
            intended_output_path=intended_output_path,
        )
    return FileOutcome(
        input_path=source_path,
        input_classification=InputClassification.ELIGIBLE_JPEG,
        kind=OutcomeKind.FAILED,
        reason_code="preview_failed",
        phase=ProcessingPhase.VERIFICATION,
        intended_output_path=intended_output_path,
        diagnostic=diagnostic or "Preview scrub failed without a diagnostic",
    )


def manual_scrub(
    files: list[Path],
    summary: ScrubSummary,
    recursive: bool,
    dry_run: bool = False,
    show_tags_mode: str | None = None,
    paranoia: bool = True,
    max_files: int | None = None,
    preview: bool = False,
    copyright_text: str | None = None,
    comment_text: str | None = None,
    rename_format: str | None = None,
    rename_counter: dict[str, int] | None = None,
    rename_plan_limits: RenamePlanLimits | None = None,
) -> ScrubSummary:
    """Scrub explicitly selected JPEG pathnames in place.

    Args:
        files: Explicit regular files or directory roots in discovery order.
        summary: Report-backed summary owned by this invocation.
        recursive: Whether directory inputs are traversed recursively.
        dry_run: Whether admitted JPEGs are planned without publication.
        show_tags_mode: Optional before/after metadata display mode.
        paranoia: Whether the maximum-scrubbing policy is active.
        max_files: Maximum JPEG candidates admitted for operation attempts.
        preview: Whether admitted JPEGs are scrubbed only in temporary storage.
        copyright_text: Optional copyright metadata to retain.
        comment_text: Optional comment metadata to retain.
        rename_format: Optional validated in-place naming format.
        rename_counter: Per-invocation sequential rename state.
        rename_plan_limits: Resource limits for disk-backed rename planning.

    Returns:
        The supplied summary after all discovered outcomes are recorded.

    Raises:
        SystemExit: If rename planning fails.
        ValueError: If a discovery or processing argument is invalid.
    """
    if not isinstance(preview, bool):
        raise ValueError("preview must be a boolean")

    if not files and not recursive:
        print("⚠️ No files provided and --recursive not set.")
        return summary

    discovered = _discover_inputs(
        files,
        recursive=recursive,
        exclude_pipeline_paths=False,
        max_files=max_files,
    )
    admitted = _record_discovery_outcomes(summary, discovered)
    admitted_by_path = {entry.path: entry for entry in admitted}

    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "Manual discovery examined %d entries and admitted %d JPEGs",
            len(discovered),
            len(admitted),
        )

    if rename_format is not None:
        counter = rename_counter if rename_counter is not None else {"n": 0}
        limits = rename_plan_limits or RenamePlanLimits()
        source_paths = _iter_revalidated_paths(summary, admitted)
        with _build_rename_plan_or_exit(
            source_paths,
            rename_format,
            None,
            counter,
            limits,
        ) as rename_plan:
            if rename_plan.count == 0:
                print("⚠️ No JPEGs matched.")
                return summary

            for entry in rename_plan:
                discovered_entry = admitted_by_path.get(entry.source_path)
                if discovered_entry is None:
                    raise RuntimeError(
                        f"Rename plan returned an undiscovered source: "
                        f"{entry.source_path}"
                    )
                source_path = entry.source_path
                if not _revalidate_or_record(summary, discovered_entry):
                    continue
                if preview:
                    print(
                        f"🔍 Would scrub: {_format_path_with_host(source_path)} "
                        f"→ {entry.destination_path.name}"
                    )
                    summary.record(
                        _preview_scrub(
                            source_path,
                            show_tags_mode,
                            paranoia,
                            copyright_text,
                            comment_text,
                            intended_output_path=entry.destination_path,
                        )
                    )
                    continue
                if dry_run:
                    if show_tags_mode in {"before", "both"}:
                        print_tags(source_path, label="before")
                    if show_tags_mode in {"after", "both"}:
                        print(
                            "⚠️ Cannot show tags *after* scrub in dry-run mode "
                            "(no scrub performed)."
                        )
                    print(
                        f"🔍 Would scrub: {_format_path_with_host(source_path)} "
                        f"→ {entry.destination_path.name}"
                    )
                    summary.record(
                        _planned_outcome(source_path, entry.destination_path)
                    )
                    continue

                result = scrub_file(
                    source_path,
                    output_path=None,
                    delete_original=False,
                    dry_run=False,
                    show_tags_mode=show_tags_mode,
                    paranoia=paranoia,
                    on_duplicate=None,
                    copyright_text=copyright_text,
                    comment_text=comment_text,
                    planned_rename_path=entry.destination_path,
                    rename_destination_allocator=partial(
                        rename_plan.reassign_destination,
                        entry.entry_id,
                    ),
                )
                summary.update(result)
        return summary

    if not admitted:
        print("⚠️ No JPEGs matched.")
        return summary

    for discovered_entry in admitted:
        if not _revalidate_or_record(summary, discovered_entry):
            continue
        f = discovered_entry.path
        if preview:
            print(f"🔍 Would scrub: {_format_path_with_host(f)}")
            summary.record(
                _preview_scrub(
                    f,
                    show_tags_mode,
                    paranoia,
                    copyright_text,
                    comment_text,
                    intended_output_path=f,
                )
            )
            continue
        if dry_run:
            if show_tags_mode in {"before", "both"}:
                print_tags(f, label="before")
            if show_tags_mode in {"after", "both"}:
                print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
            print(f"🔍 Would scrub: {_format_path_with_host(f)}")
            summary.record(_planned_outcome(f, f))
            continue

        result = scrub_file(f,
                            output_path=None,
                            delete_original=False,
                            dry_run=False,
                            show_tags_mode=show_tags_mode,
                            paranoia=paranoia,
                            on_duplicate=None,
                            copyright_text=copyright_text,
                            comment_text=comment_text,
                            rename_format=rename_format,
                            rename_counter=rename_counter)

        summary.update(result)

    return summary


# ----------------------------
# Root guard
# ----------------------------

def require_force_for_root():
    if os.geteuid() == 0 and os.environ.get("ALLOW_ROOT") != "1":
        print("❌ Running as root is not allowed unless ALLOW_ROOT=1 is set.", file=sys.stderr)
        sys.exit(1)


# ----------------------------
# CLI
# ----------------------------

def _run(args: argparse.Namespace) -> int:
    if args.version:
        return _run_inner(args)
    if args.quiet:
        args.log_level = "crit"
        args.debug = False
        stdout_buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout_buffer):
                exit_code = _run_inner(args)
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
        except Exception:
            sys.stderr.write(stdout_buffer.getvalue())
            raise
        if exit_code != 0:
            sys.stderr.write(stdout_buffer.getvalue())
        return exit_code
    return _run_inner(args)


def _run_inner(args: argparse.Namespace) -> int:
    if args.version:
        show_version()
        return 0

    require_force_for_root()
    global log
    global OUTPUT_DIR
    if args.debug:
        args.log_level = "debug"

    log = setup_logger(args.log_level)

    if log.isEnabledFor(logging.DEBUG):
        log.debug("Debug logging enabled")
        formatted_args = {}
        for key, value in vars(args).items():
            if isinstance(value, Path):
                formatted_args[key] = str(value)
            elif isinstance(value, list):
                formatted_args[key] = [
                    str(v) if isinstance(v, Path) else v for v in value
                ]
            else:
                formatted_args[key] = value
        log.debug("CLI arguments: %s", formatted_args)
        env_snapshot = {name: os.getenv(name) for name in DEBUG_ENV_VARS}
        log.debug("Environment snapshot: %s", env_snapshot)

    # Resolve/override state-file from CLI
    global STATE_FILE, _warned_state_disabled
    global SHOW_CONTAINER_PATHS
    SHOW_CONTAINER_PATHS = args.show_container_paths
    if args.state_file is not None:
        choice = str(args.state_file).strip().lower()
        if choice in {"disabled", "none", "-"}:
            STATE_FILE = None
        else:
            candidate = _validate_writable_path(Path(args.state_file))
            if candidate is None:
                log.warning("Requested --state-file %s is not writable; disabling state (mtime-only).", args.state_file)
                STATE_FILE = None
            else:
                STATE_FILE = candidate
    else:
        # Re-evaluate env/defaults in case calling context changed
        STATE_FILE = _resolve_state_path_from_env()

    check_jpegtran()

    # Resolve effective rename format: explicit --rename beats --paranoia default.
    rename_format: str | None = args.rename
    if rename_format is None and args.paranoia:
        rename_format = "%r8"
    if rename_format is not None:
        validate_rename_format(rename_format)  # raises SystemExit on any violation
    rename_counter: dict[str, int] = {"n": 0}
    rename_plan_limits = RenamePlanLimits(
        max_files=args.rename_plan_max_files,
        max_seconds=args.rename_plan_timeout_seconds,
        max_database_bytes=args.rename_plan_max_mib * 1024 * 1024,
    )

    # --paranoia removes all metadata; --copyright and --comment are incompatible.
    if args.paranoia and (args.copyright or args.comment):
        print(
            "❌ --paranoia removes all metadata. "
            "--copyright and --comment cannot be combined with --paranoia.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Mode sanity checks
    if args.clean_inline and args.from_input:
        print("❌ --clean-inline and --from-input cannot be used together.", file=sys.stderr)
        sys.exit(1)
    if args.output and args.clean_inline:
        print("❌ --output cannot be used with --clean-inline.", file=sys.stderr)
        sys.exit(1)
    if args.output and args.from_input:
        print("❌ --output cannot be used with --from-input.", file=sys.stderr)
        sys.exit(1)
    if args.preview and args.from_input:
        print("❌ --preview cannot be used with --from-input auto mode.", file=sys.stderr)
        sys.exit(1)

    if args.output:
        OUTPUT_DIR = resolve_output_dir(args.output)

    # Emit the *exact* banner lines tests expect
    if STATE_FILE is None:
        print("🔎 [INFO] State path: disabled")
        print("🔎 [INFO] State disabled: using mtime-only stability.")
    else:
        print(f"🔎 [INFO] State path: {STATE_FILE}")

    summary = ScrubSummary()

    if args.preview:
        if args.show_tags is None:
            args.show_tags = "both"

    if args.from_input:
        guard_auto_mode_dirs(args.on_duplicate)
        auto_scrub(
            summary=summary,
            dry_run=args.dry_run,
            delete_original=args.delete_original,
            show_tags_mode=args.show_tags,
            paranoia=args.paranoia,
            max_files=args.max_files,
            on_duplicate=args.on_duplicate,
            stable_seconds=args.stable_seconds,
            copyright_text=args.copyright,
            comment_text=args.comment,
            rename_format=rename_format,
            rename_counter=rename_counter,
            rename_plan_limits=rename_plan_limits,
        )
    elif args.clean_inline:
        if args.files:
            resolved_files = [resolve_cli_path(f) for f in args.files]
        else:
            resolved_files = [PHOTOS_ROOT]

        manual_scrub(
            resolved_files,
            summary=summary,
            recursive=args.recursive,
            dry_run=args.dry_run,
            show_tags_mode=args.show_tags,
            paranoia=args.paranoia,
            max_files=args.max_files,
            preview=args.preview,
            copyright_text=args.copyright,
            comment_text=args.comment,
            rename_format=rename_format,
            rename_counter=rename_counter,
            rename_plan_limits=rename_plan_limits,
        )
    else:
        resolved_explicit = [resolve_cli_path(f) for f in args.files] if args.files else None
        simple_scrub(
            summary=summary,
            recursive=args.recursive,
            dry_run=args.dry_run,
            preview=args.preview,
            show_tags_mode=args.show_tags,
            paranoia=args.paranoia,
            max_files=args.max_files,
            output_explicit=bool(args.output),
            copyright_text=args.copyright,
            comment_text=args.comment,
            rename_format=rename_format,
            rename_counter=rename_counter,
            rename_plan_limits=rename_plan_limits,
            explicit_files=resolved_explicit,
        )

    summary.print()
    return 1 if summary.errors else 0


def _positive_integer(raw_value: str) -> int:
    """Parse a strictly positive command-line integer.

    Args:
        raw_value: Raw argparse value.

    Returns:
        Positive integer.

    Raises:
        argparse.ArgumentTypeError: If the value is not a positive integer.
    """
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return value


def _non_negative_integer(raw_value: str) -> int:
    """Parse a non-negative command-line integer.

    Args:
        raw_value: Raw argparse value.

    Returns:
        Integer greater than or equal to zero.

    Raises:
        argparse.ArgumentTypeError: If the value is not a non-negative integer.
    """
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("value must be zero or greater")
    return value


def _positive_float(raw_value: str) -> float:
    """Parse a strictly positive command-line number.

    Args:
        raw_value: Raw argparse value.

    Returns:
        Positive floating-point number.

    Raises:
        argparse.ArgumentTypeError: If the value is not positive and finite.
    """
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a number") from exc
    if not value > 0 or not value < float("inf"):
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrub EXIF metadata from JPEGs.")
    parser.add_argument("files", nargs="*", type=Path, help="Files or directories")
    parser.add_argument("--from-input", action="store_true", help="Use auto mode")
    parser.add_argument("--clean-inline", action="store_true",
                        help="Scrub (destructive) in-place. This flag is required to modify originals.")
    parser.add_argument("--rename", metavar="FORMAT", default=None,
                        help="Format string for output filename (e.g. '%%r8', '850_%%r6', '%%Y%%m_%%r6'). "
                             "Implied '%%r8' when --paranoia is set. "
                             "With --clean-inline, the file is scrubbed then renamed in the same directory.")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
    parser.add_argument("-r", "--recursive", action="store_true", help="Recurse into directories")
    parser.add_argument("--show-tags", choices=["before", "after", "both"], help="Show metadata before/after")
    parser.add_argument("--paranoia", action="store_true",
                        help="Maximum scrubbing: jpegtran -copy none only — zero metadata output. "
                             "Incompatible with --copyright and --comment.")
    parser.add_argument("--preview", action="store_true",
                        help=("Scrub and audit all in-scope JPEGs in temporary "
                              "storage without publishing or modifying sources; "
                              "not available in --from-input auto mode"))
    parser.add_argument("--show-container-paths", action="store_true",
                        help="Include container paths alongside host paths in output")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress all output on success")
    parser.add_argument(
        "--max-files",
        type=_non_negative_integer,
        metavar="N",
        help=(
            "Limit JPEG operation attempts; unsupported and skipped files do "
            "not consume the limit"
        ),
    )
    parser.add_argument(
        "--rename-plan-max-files",
        type=_positive_integer,
        default=DEFAULT_MAX_PLAN_FILES,
        metavar="N",
        help=(
            "Stop rename planning before more than N files are indexed "
            f"(default: {DEFAULT_MAX_PLAN_FILES})"
        ),
    )
    parser.add_argument(
        "--rename-plan-timeout-seconds",
        type=_positive_float,
        default=DEFAULT_MAX_PLAN_SECONDS,
        metavar="SECONDS",
        help=(
            "Stop rename planning after this duration "
            f"(default: {DEFAULT_MAX_PLAN_SECONDS:g})"
        ),
    )
    parser.add_argument(
        "--rename-plan-max-mib",
        type=_positive_integer,
        default=DEFAULT_MAX_PLAN_BYTES // (1024 * 1024),
        metavar="MIB",
        help=(
            "Maximum temporary rename-plan database size "
            f"(default: {DEFAULT_MAX_PLAN_BYTES // (1024 * 1024)} MiB)"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="List actions without performing them")
    parser.add_argument("--on-duplicate", choices=["move", "fail", "delete"],
                        default=os.getenv("SCRUBEXIF_ON_DUPLICATE", "move"),
                        help=("Verified duplicate handling in auto mode. "
                              "Defaults to 'move' into /photos/errors; "
                              "'fail' leaves the source untouched and returns nonzero; "
                              "'delete' requires explicit selection."))
    parser.add_argument("--delete-original", action="store_true", help="Delete original after scrub (auto mode)")
    parser.add_argument("--copyright", metavar="TEXT",
                        help="Stamp a copyright notice into EXIF and XMP metadata")
    parser.add_argument("--comment", metavar="TEXT",
                        help="Stamp a comment into EXIF and XMP metadata")
    parser.add_argument("--log-level", choices=["debug", "info", "warn", "error", "crit"], default="info",
                        help="Set log verbosity")
    parser.add_argument("--stable-seconds", type=int,
                        default=int(os.getenv("SCRUBEXIF_STABLE_SECONDS", "120")),
                        help="Only process files whose mtime age ≥ this many seconds (default: 120)")
    parser.add_argument("--state-file", metavar="PATH|disabled", default=None,
                        help=("Override stability state file path. "
                              "Use 'disabled' (or '-', 'none') to force mtime-only. "
                              "If not provided, uses SCRUBEXIF_STATE or auto-detected writable path."))
    parser.add_argument("-o", "--output", type=Path,
                        help="Write scrubbed files to this directory (default safe mode)")
    parser.add_argument("-v", "--version", action="store_true", help="Show version and license")
    args = parser.parse_args(argv)

    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
