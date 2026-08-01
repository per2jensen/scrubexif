#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Scrub EXIF metadata from JPEG files while retaining selected tags.

Designed for photographers who want to preserve camera details
(exposure, lens, ISO, etc.) but remove private or irrelevant data.
"""

import argparse
import contextlib
import io
import itertools
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from functools import partial
from pathlib import Path
from typing import Optional

from .__about__ import __license__, __version__
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

class ScrubResult:
    __slots__ = ("input_path", "output_path", "status", "error_message", "duplicate_path")

    def __init__(
        self,
        input_path: Path,
        output_path: Optional[Path] = None,
        status: str = "scrubbed",
        error_message: Optional[str] = None,
        duplicate_path: Optional[Path] = None,
    ):
        self.input_path = input_path
        self.output_path = output_path
        self.status = status  # scrubbed, scrubbed_with_error, skipped, duplicate, conflict, error
        self.error_message = error_message
        self.duplicate_path = duplicate_path

    def __repr__(self):
        return (
            f"ScrubResult(status={self.status!r}, "
            f"input={self.input_path.name}, "
            f"output={self.output_path.name if self.output_path else 'n/a'}, "
            f"error={bool(self.error_message)})"
        )


class ScrubSummary:
    def __init__(self):
        self.total = 0
        self.scrubbed = 0
        self.skipped = 0
        self.duplicates_deleted = 0
        self.duplicates_moved = 0
        self.errors = 0
        # Track wall-clock duration of the whole run
        self.started_at = time.time()

    def update(self, result: ScrubResult):
        self.total += 1
        match result.status:
            case "scrubbed":
                self.scrubbed += 1
            case "scrubbed_with_error":
                self.scrubbed += 1
                self.errors += 1
            case "skipped":
                self.skipped += 1
            case "duplicate":
                if result.duplicate_path:
                    self.duplicates_moved += 1
                else:
                    self.duplicates_deleted += 1
            case "conflict" | "error":
                self.errors += 1

    def print(self):
        duration = time.time() - self.started_at
        print("📊 Summary:")
        print(f"  Total JPEGs found        : {self.total}")
        print(f"  Successfully scrubbed    : {self.scrubbed}")
        print(f"  Skipped (unstable/temp)  : {self.skipped}")
        print(f"  Errors                   : {self.errors}")
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
    data = json.loads(result.stdout)
    if not data:
        return {}
    return {k: v for k, v in data[0].items() if k != "SourceFile"}


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
    cmd = ["exiftool", "-overwrite_original", "-P", "-m", "-n"]
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
    on_duplicate: str = "delete",
    copyright_text: str | None = None,
    comment_text: str | None = None,
    rename_format: str | None = None,
    rename_counter: dict[str, int] | None = None,
    planned_rename_path: Path | None = None,
    rename_destination_allocator: Callable[[Path], Path] | None = None,
) -> ScrubResult:
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
                status="conflict",
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
                status="conflict",
                error_message=err_msg,
            )
        except Exception as exc:
            err_msg = f"Late rename reassignment failed: {exc}"
            print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
            return ScrubResult(
                input_path=input_path,
                output_path=output_file,
                status="error",
                error_message=err_msg,
            )

    if output_file.absolute() != input_path.absolute() and output_file.is_symlink():
        msg = f"Destination is a symlink; refusing to scrub into {_format_path_with_host(output_file)}"
        print(f"❌ {msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="conflict" if rename_requested else "error",
            error_message=msg,
        )

    # duplicates
    if os.path.lexists(output_file) and input_path.absolute() != output_file.absolute():
        print(
            "⚠️ Duplicate logic triggered: "
            f"input={_format_path_with_host(input_path)}, "
            f"output={_format_path_with_host(output_file)}"
        )

        if dry_run:
            print(f"🚫 [dry-run] Would detect duplicate: {_format_path_with_host(output_file)}")
            return ScrubResult(input_path, output_file, status="duplicate")

        if on_duplicate == "skip":
            print(f"⏭️  Output already exists — skipping (original untouched): {_format_path_with_host(input_path)}")
            return ScrubResult(input_path, output_file, status="skipped")

        elif on_duplicate == "delete":
            print(f"🗑️  Duplicate detected — deleting {_format_path_with_host(input_path)}")
            try:
                input_path.unlink(missing_ok=True)
            except OSError as exc:
                err_msg = f"Could not delete duplicate safely: {exc}"
                print(f"❌ {err_msg}")
                return ScrubResult(
                    input_path,
                    output_file,
                    status="error",
                    error_message=err_msg,
                )
            return ScrubResult(input_path, output_file, status="duplicate")

        elif on_duplicate == "move":
            try:
                target = _archive_no_clobber(input_path, ERRORS_DIR)
            except (ArchiveError, ValueError) as exc:
                err_msg = f"Could not archive duplicate safely: {exc}"
                print(f"❌ {err_msg}")
                return ScrubResult(
                    input_path,
                    output_file,
                    status="error",
                    error_message=err_msg,
                )
            print(f"📦 Moved duplicate to: {_format_path_with_host(target)}")
            return ScrubResult(
                input_path,
                output_file,
                status="duplicate",
                duplicate_path=target,
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
        return ScrubResult(input_path, output_file, status="scrubbed")

    # exiftool command
    in_place = output_path is None or input_path.resolve() == output_path.resolve()
    try:
        temp_output = _create_temp_output(
            input_path.parent if in_place else output_file.parent,
            input_path.suffix,
        )
    except Exception as exc:
        err_msg = str(exc)
        print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="error",
            error_message=err_msg
        )
    if show_tags_mode in {"before", "both"}:
        print_tags(input_path, label="before")

    try:
        _do_scrub_pipeline(
            input_path,
            temp_output,
            paranoia=paranoia,
            copyright_text=copyright_text,
            comment_text=comment_text,
        )
    except RuntimeError as exc:
        temp_output.unlink(missing_ok=True)
        err_msg = str(exc)
        print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="error",
            error_message=err_msg,
        )

    if not temp_output.exists():
        err_msg = "Temp output missing after scrub"
        print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="error",
            error_message=err_msg
        )

    try:
        if (in_place and output_file.absolute() != input_path.absolute()) or not in_place:
            while True:
                try:
                    _publish_no_clobber(temp_output, output_file)
                    break
                except FileExistsError:
                    if not rename_requested:
                        raise
                    output_file = reassign_late_destination(output_file)
                    rename_stem = output_file.stem
        elif in_place:
            os.replace(temp_output, input_path)
    except RenamePlanningError as exc:
        temp_output.unlink(missing_ok=True)
        err_msg = str(exc)
        print(f"❌ Rename conflict for {_format_path_with_host(input_path)}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="conflict",
            error_message=err_msg,
        )
    except FileExistsError:
        temp_output.unlink(missing_ok=True)
        err_msg = "Destination appeared during scrub; refusing to overwrite"
        print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="conflict" if rename_requested else "error",
            error_message=err_msg,
        )
    except Exception as exc:
        temp_output.unlink(missing_ok=True)
        err_msg = str(exc)
        print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="error",
            error_message=err_msg
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
                input_path,
                output_file,
                status="scrubbed_with_error",
                error_message=err_msg,
            )

    if delete_original and not in_place and input_path.exists():
        try:
            input_path.unlink()
        except OSError as exc:
            err_msg = f"Scrub succeeded but original deletion failed: {exc}"
            print(f"❌ {err_msg}")
            return ScrubResult(
                input_path,
                output_file,
                status="scrubbed_with_error",
                error_message=err_msg,
            )
        print(f"🗑️ Deleted original: {_format_path_with_host(input_path)}")

    return ScrubResult(input_path, output_file, status="scrubbed")


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
    if not isinstance(dir_path, Path):
        raise ValueError("dir_path must be a pathlib.Path")
    if not dir_path.is_dir():
        return
    search_func = dir_path.rglob if recursive else dir_path.glob
    for f in search_func("*"):
        if f.is_symlink():
            log.debug("Skipping symlinked file: %s", f)
            continue
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg"):
            yield f


def _limit_paths(paths: Iterable[Path], max_files: int | None) -> Iterator[Path]:
    """Apply the user processing limit without materializing source paths.

    Args:
        paths: Source path stream.
        max_files: Maximum paths to yield, or None for no user limit.

    Yields:
        At most max_files source paths.

    Raises:
        ValueError: If max_files is negative when provided.
    """
    if max_files is None:
        yield from paths
        return
    if not isinstance(max_files, int) or isinstance(max_files, bool) or max_files < 0:
        raise ValueError("max_files must be a non-negative integer")
    yield from itertools.islice(paths, max_files)


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
    summary.update(result)

    if result.status == "scrubbed" and not delete_original:
        try:
            archived_path = _archive_no_clobber(file, PROCESSED_DIR)
        except (ArchiveError, ValueError) as exc:
            summary.errors += 1
            print(
                f"❌ Scrub succeeded for {_format_path_with_host(file)}, but the "
                f"original could not be archived safely: {exc}"
            )
        else:
            print(f"📦 Moved original to {_format_path_with_host(archived_path)}")
    elif result.status == "scrubbed_with_error":
        print(
            f"⚠️ Scrub output was created for {_format_path_with_host(file)}, "
            "but source post-processing failed; leaving the source in place"
        )
    elif result.status == "conflict":
        print(
            f"⚠️ Rename destination conflict for {_format_path_with_host(file)}; "
            "leaving original in place"
        )
    elif result.status == "error":
        if file.exists():
            try:
                archived_path = _archive_no_clobber(file, PROCESSED_DIR)
            except (ArchiveError, ValueError) as exc:
                print(
                    f"⚠️ Scrub failed for {_format_path_with_host(file)} and the "
                    f"original could not be archived safely; leaving it in place: {exc}"
                )
            else:
                print(
                    f"⚠️ Scrub failed for {_format_path_with_host(file)}; "
                    f"moved original to {_format_path_with_host(archived_path)} "
                    "for inspection"
                )
        else:
            print(
                f"⚠️ Scrub failed and source already missing: "
                f"{_format_path_with_host(file)}"
            )

    mark_seen(file, state)


def auto_scrub(summary: ScrubSummary, dry_run=False, delete_original=False,
               show_tags_mode: str | None = None,
               paranoia: bool = True,
               max_files: int | None = None,
               on_duplicate: str = "delete",
               stable_seconds: int = 120,
               copyright_text: str | None = None,
               comment_text: str | None = None,
               rename_format: str | None = None,
               rename_counter: dict[str, int] | None = None,
               rename_plan_limits: RenamePlanLimits | None = None) -> ScrubSummary:
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

    if rename_format is not None:
        skipped = {"temp": 0, "unstable": 0}

        def eligible_sources() -> Iterator[Path]:
            """Stream stable auto-mode sources while updating skip counters.

            Yields:
                Stable, non-temporary intake paths.
            """
            for candidate in iter_jpegs_in_dir(INPUT_DIR, recursive=False):
                if is_probably_temp(candidate):
                    skipped["temp"] += 1
                    summary.skipped += 1
                    summary.total += 1
                    mark_seen(candidate, state)
                    continue
                if not is_file_stable(candidate, state, stable_seconds):
                    skipped["unstable"] += 1
                    summary.skipped += 1
                    summary.total += 1
                    mark_seen(candidate, state)
                    continue
                yield candidate

        counter = rename_counter if rename_counter is not None else {"n": 0}
        limits = rename_plan_limits or RenamePlanLimits()
        source_paths = _limit_paths(eligible_sources(), max_files)
        with _build_rename_plan_or_exit(
            source_paths,
            rename_format,
            OUTPUT_DIR,
            counter,
            limits,
        ) as rename_plan:
            if rename_plan.count == 0:
                if skipped["temp"] or skipped["unstable"]:
                    print(
                        "ℹ️ Nothing eligible yet. Skipped: "
                        f"temp={skipped['temp']}, unstable={skipped['unstable']}."
                    )
                else:
                    print("⚠️ No JPEGs found — nothing to do.")
                save_state(state)
                return summary

            if not dry_run:
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

            for entry in rename_plan:
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
                    summary.total += 1
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

    input_files = find_jpegs_in_dir(INPUT_DIR, recursive=False)
    if log.isEnabledFor(logging.DEBUG):
        log.debug("Input scan yielded %d files before filtering", len(input_files))

    # Filter
    filtered: list[Path] = []
    skipped_temp = 0
    skipped_unstable = 0

    for f in input_files:
        if is_probably_temp(f):
            skipped_temp += 1
            summary.skipped += 1
            summary.total += 1
            mark_seen(f, state)
            continue
        if not is_file_stable(f, state, stable_seconds):
            skipped_unstable += 1
            summary.skipped += 1
            summary.total += 1
            mark_seen(f, state)
            continue
        filtered.append(f)

    if max_files is not None:
        filtered = filtered[:max_files]

    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "Filtered candidates: %d (skipped temp=%d, unstable=%d)",
            len(filtered), skipped_temp, skipped_unstable
        )

    if not filtered:
        if skipped_temp or skipped_unstable:
            print(f"ℹ️ Nothing eligible yet. Skipped: temp={skipped_temp}, unstable={skipped_unstable}.")
        else:
            print("⚠️ No JPEGs found — nothing to do.")
        save_state(state)
        return summary

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    for file in filtered:
        if dry_run:
            if show_tags_mode in {"before", "both"}:
                print_tags(file, label="before")
            if show_tags_mode in {"after", "both"}:
                print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
            print(f"🔍 Would scrub: {_format_path_with_host(file)}")
            summary.total += 1
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


def _iter_simple_targets(
    explicit_files: Iterable[Path] | None,
    recursive: bool,
) -> Iterator[Path]:
    """Stream eligible default-mode source paths.

    Args:
        explicit_files: Explicit paths, or None to scan PHOTOS_ROOT.
        recursive: Whether directory scans recurse.

    Yields:
        Non-symlink JPEG paths outside pipeline directories.
    """
    if explicit_files is None:
        candidates: Iterable[Path] = iter_jpegs_in_dir(PHOTOS_ROOT, recursive=recursive)
    else:
        def explicit_candidates() -> Iterator[Path]:
            """Expand explicit files and directories without materializing them.

            Yields:
                JPEG source paths.
            """
            for path in explicit_files:
                if path.is_symlink():
                    log.debug("Skipping symlink in explicit-files mode: %s", path)
                    continue
                if path.is_file() and path.suffix.lower() in (".jpg", ".jpeg"):
                    yield path
                elif path.is_dir():
                    yield from iter_jpegs_in_dir(path, recursive=recursive)

        candidates = explicit_candidates()

    for candidate in candidates:
        if candidate.is_symlink():
            log.debug("Skipping symlink in default safe mode: %s", candidate)
            continue
        if _is_pipeline_path(candidate):
            continue
        yield candidate


def simple_scrub(summary: ScrubSummary,
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
                 explicit_files: list[Path] | None = None) -> ScrubSummary:
    """
    Default safe mode:
      - Scan /photos for JPEGs (non-recursive by default, -r respected)
      - Write scrubbed copies to /photos/output
      - Leave originals untouched in place — always.

    If the output file already exists (e.g. on a second run into the same
    output directory), the input is skipped and the original is never touched.
    on_duplicate is intentionally not exposed here; "skip" is hardcoded to
    preserve the safe-mode guarantee.

    Intended for the "one-liner" use case:

        docker run --rm -v "$PWD:/photos" per2jensen/scrubexif:0.7.10

    Args:
        output_explicit: True when the caller supplied -o on the CLI. A
            pre-existing output directory is accepted in that case because the
            user stated intent (e.g. via a bind-mount). When False (default)
            a pre-existing directory is refused to prevent accidental clobbering.
        explicit_files: When set, process only these resolved paths instead of
            scanning PHOTOS_ROOT. Directories in the list are expanded via
            find_jpegs_in_dir; the special-dirs safety filter still applies.
    """
    host_root = _resolve_mount_source(PHOTOS_ROOT)
    if explicit_files is not None:
        print(f"🚀 Default safe mode: Scrubbing {len(explicit_files)} explicit file(s)")
    else:
        print(f"🚀 Default safe mode: Scrubbing JPEGs in {_format_path_with_host(PHOTOS_ROOT)}")
    print(f"📁 Output directory: {_format_path_with_host(OUTPUT_DIR)}")

    # Safety: /photos must exist and be usable
    check_dir_safety(PHOTOS_ROOT, "Photos root")

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

    if rename_format is not None:
        counter = rename_counter if rename_counter is not None else {"n": 0}
        limits = rename_plan_limits or RenamePlanLimits()
        source_paths = _limit_paths(
            _iter_simple_targets(explicit_files, recursive=recursive),
            max_files,
        )
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
                    summary.total += 1
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

    if explicit_files is not None:
        candidates: list[Path] = []
        for p in explicit_files:
            if p.is_symlink():
                log.debug("Skipping symlink in explicit-files mode: %s", p)
                continue
            if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg"):
                candidates.append(p)
            elif p.is_dir():
                candidates.extend(find_jpegs_in_dir(p, recursive=recursive))
    else:
        candidates = find_jpegs_in_dir(PHOTOS_ROOT, recursive=recursive)

    # Avoid feeding our own pipeline directories back into the scrub loop
    filtered: list[Path] = []
    for f in candidates:
        # Skip symlinks aggressively
        if f.is_symlink():
            log.debug("Skipping symlink in default safe mode: %s", f)
            continue

        skip = False
        for special in (INPUT_DIR, OUTPUT_DIR, PROCESSED_DIR, ERRORS_DIR):
            try:
                f.relative_to(special)
                skip = True
                break
            except ValueError:
                continue
        if skip:
            continue

        filtered.append(f)

    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "Default safe mode: found %d JPEGs (explicit=%s, recursive=%s, after filtering=%d)",
            len(candidates),
            explicit_files is not None,
            recursive,
            len(filtered),
        )

    if not filtered:
        print("⚠️ No eligible JPEGs found in default safe mode.")
        return summary

    if max_files is not None:
        filtered = filtered[:max_files]

    for f in filtered:
        dst = OUTPUT_DIR / f.name

        if dry_run:
            if show_tags_mode in {"before", "both"}:
                print_tags(f, label="before")
            if show_tags_mode in {"after", "both"}:
                print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
            print(f"🔍 [default] Would scrub: {_format_path_with_host(f)} -> {_format_path_with_host(dst)}")
            summary.total += 1
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


def _iter_manual_targets(files: Iterable[Path], recursive: bool) -> Iterator[Path]:
    """Stream valid manual-mode JPEG targets.

    Args:
        files: Explicit files or directories.
        recursive: Whether directory scans recurse.

    Yields:
        Non-symlink JPEG paths.
    """
    for file in files:
        if file.is_symlink():
            log.warning("Skipping symlink input: %s", file)
            continue
        if file.is_file() and file.suffix.lower() in (".jpg", ".jpeg"):
            yield file
        elif file.is_dir():
            yield from iter_jpegs_in_dir(file, recursive=recursive)


def _preview_scrub(
    source_path: Path,
    show_tags_mode: str | None,
    paranoia: bool,
    copyright_text: str | None,
    comment_text: str | None,
) -> bool:
    """Scrub a disposable copy and display its resulting metadata.

    Args:
        source_path: Source JPEG that must remain untouched.
        show_tags_mode: Requested tag display mode.
        paranoia: Whether to use the maximum-scrubbing pipeline.
        copyright_text: Optional copyright value to stamp.
        comment_text: Optional comment value to stamp.

    Returns:
        True when the disposable scrub completed; otherwise False.
    """
    preview_fd, preview_tmp_str = tempfile.mkstemp(suffix=source_path.suffix)
    os.close(preview_fd)
    preview_input = Path(preview_tmp_str)
    preview_output: Path | None = None
    succeeded = False

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
        if show_tags_mode in {"before", "both"}:
            print_tags(source_path, label="before")
        print_tags(preview_output, label="after")
        succeeded = True
    except (OSError, RuntimeError) as exc:
        print(f"❌ Preview scrub failed: {exc}")
    finally:
        preview_input.unlink(missing_ok=True)
        if preview_output is not None:
            preview_output.unlink(missing_ok=True)

    if succeeded:
        print("📊 Preview complete — original file was not modified.")
    return succeeded


def manual_scrub(files: list[Path],
                 summary: ScrubSummary,
                 recursive: bool, dry_run=False,
                 show_tags_mode: str | None = None,
                 paranoia: bool = True,
                 max_files: int | None = None,
                 preview: bool = False,
                 copyright_text: str | None = None,
                 comment_text: str | None = None,
                 rename_format: str | None = None,
                 rename_counter: dict[str, int] | None = None,
                 rename_plan_limits: RenamePlanLimits | None = None) -> ScrubSummary:
    if not files and not recursive:
        print("⚠️ No files provided and --recursive not set.")
        return summary

    if rename_format is not None:
        counter = rename_counter if rename_counter is not None else {"n": 0}
        limits = rename_plan_limits or RenamePlanLimits()
        source_paths = _limit_paths(
            _iter_manual_targets(files, recursive=recursive),
            max_files,
        )
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

            should_preview = preview or (
                dry_run
                and show_tags_mode in {"after", "both"}
                and rename_plan.count == 1
            )
            if should_preview:
                entry = next(iter(rename_plan))
                print(
                    f"🔍 Would scrub: {_format_path_with_host(entry.source_path)} "
                    f"→ {entry.destination_path.name}"
                )
                preview_succeeded = _preview_scrub(
                    entry.source_path,
                    show_tags_mode,
                    paranoia,
                    copyright_text,
                    comment_text,
                )
                if not preview_succeeded:
                    summary.total += 1
                    summary.errors += 1
                return summary

            for entry in rename_plan:
                source_path = entry.source_path
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
                    summary.total += 1
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

    targets = list(_iter_manual_targets(files, recursive=recursive))

    if log.isEnabledFor(logging.DEBUG):
        log.debug("Manual mode targets gathered: %d files", len(targets))

    if not targets:
        print("⚠️ No JPEGs matched.")
        return summary

    if max_files is not None:
        targets = targets[:max_files]

    if preview or (dry_run and show_tags_mode in {"after", "both"} and len(targets) == 1):
        preview_succeeded = _preview_scrub(
            targets[0],
            show_tags_mode,
            paranoia,
            copyright_text,
            comment_text,
        )
        if not preview_succeeded:
            summary.total += 1
            summary.errors += 1
        return summary

    for f in targets:
        if f.is_symlink():
            log.warning("Skipping symlink target: %s", f)
            continue
        if dry_run:
            if show_tags_mode in {"before", "both"}:
                print_tags(f, label="before")
            if show_tags_mode in {"after", "both"}:
                print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
            print(f"🔍 Would scrub: {_format_path_with_host(f)}")
            summary.total += 1
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

    if args.output:
        OUTPUT_DIR = resolve_output_dir(args.output)

    # Emit the *exact* banner lines tests expect
    if STATE_FILE is None:
        print("🔎 [INFO] State path: disabled")
        print("🔎 [INFO] State disabled: using mtime-only stability.")
    else:
        print(f"🔎 [INFO] State path: {STATE_FILE}")

    summary = ScrubSummary()

    if args.on_duplicate == "move":
        try:
            ERRORS_DIR.mkdir(parents=True, exist_ok=True)
            check_dir_safety(ERRORS_DIR, "Errors")
        except Exception as e:
            print(f"❌ Failed to create errors directory: {_format_path_with_host(ERRORS_DIR)}\n{e}", file=sys.stderr)
            sys.exit(1)

    if args.preview:
        args.dry_run = True
        args.show_tags = "both"
        args.max_files = 1

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
                        help="Preview scrub effect on one file without modifying it")
    parser.add_argument("--show-container-paths", action="store_true",
                        help="Include container paths alongside host paths in output")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress all output on success")
    parser.add_argument("--max-files", type=int, metavar="N",
                        help="Limit number of files to scrub")
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
    parser.add_argument("--on-duplicate", choices=["delete", "move"],
                        default=os.getenv("SCRUBEXIF_ON_DUPLICATE", "delete"),
                        help="Duplicate handling in auto/default modes. 'delete' or 'move' to /photos/errors/")
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
