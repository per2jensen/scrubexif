# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated terminal outcomes and accounting for one scrubexif invocation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


_REASON_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class OutcomeKind(str, Enum):
    """Classify one examined input with exactly one terminal outcome."""

    SCRUBBED = "scrubbed"
    DUPLICATE_HANDLED = "duplicate_handled"
    UNSUPPORTED = "unsupported"
    PLANNED = "planned"
    PREVIEWED = "previewed"
    SKIPPED = "skipped"
    FAILED = "failed"


class InputClassification(str, Enum):
    """Classify an input before assigning its terminal outcome."""

    ELIGIBLE_JPEG = "eligible_jpeg"
    UNSUPPORTED_REGULAR = "unsupported_regular"


class ProcessingPhase(str, Enum):
    """Identify the processing boundary at which a result became final."""

    PREFLIGHT = "preflight"
    DISCOVERY = "discovery"
    PLANNING = "planning"
    SCRUB = "scrub"
    VERIFICATION = "verification"
    PUBLICATION = "publication"
    SOURCE_FINALIZATION = "source_finalization"
    DUPLICATE_HANDLING = "duplicate_handling"


class SourceDisposition(str, Enum):
    """Describe the final filesystem disposition of an input source."""

    UNCHANGED = "unchanged"
    MOVED = "moved"
    DELETED = "deleted"
    MISSING = "missing"


class DuplicateDisposition(str, Enum):
    """Describe the final disposition of an input verified as a duplicate."""

    UNCHANGED = "unchanged"
    MOVED = "moved"
    DELETED = "deleted"
    MISSING = "missing"


class RunReportValidationError(RuntimeError):
    """Indicate that aggregated run counters cannot be reconciled."""


@dataclass(frozen=True, slots=True)
class RunFailure:
    """Describe one controlled failure not assigned to a classified input.

    Args:
        reason_code: Stable lowercase machine-readable explanation.
        phase: Processing boundary at which the run-level failure occurred.
        diagnostic: Human-readable context for the failure.
        input_path: Related path when classification could not be completed.
        interrupted: Whether a handled interruption caused the failure.

    Raises:
        ValueError: If a field is invalid.
    """

    reason_code: str
    phase: ProcessingPhase
    diagnostic: str
    input_path: Path | None = None
    interrupted: bool = False

    def __post_init__(self) -> None:
        """Validate the run-level failure.

        Raises:
            ValueError: If a field is invalid.
        """
        validate_reason_code(self.reason_code)
        if not isinstance(self.phase, ProcessingPhase):
            raise ValueError("phase must be a ProcessingPhase")
        if not isinstance(self.diagnostic, str) or not self.diagnostic.strip():
            raise ValueError("diagnostic must be a non-empty string")
        _validate_path("input_path", self.input_path, required=False)
        if not isinstance(self.interrupted, bool):
            raise ValueError("interrupted must be a boolean")


@dataclass(frozen=True, slots=True)
class FileOutcome:
    """Describe the validated terminal outcome for one classified input.

    Args:
        input_path: Path identifying the examined input.
        input_classification: Classification established during discovery.
        kind: Exactly one terminal outcome classification.
        reason_code: Stable lowercase machine-readable explanation.
        phase: Processing boundary at which the outcome became final.
        intended_output_path: Planned destination, when one was selected.
        actual_output_path: Published or verified existing output, when present.
        published: Whether this operation published a new audited output.
        duplicate_verified: Whether an existing output was verified as identical.
        source_disposition: Final disposition of the input source.
        duplicate_disposition: Duplicate source disposition, when applicable.
        diagnostic: Optional human-readable context.

    Raises:
        ValueError: If a field or combination of fields is invalid.
    """

    input_path: Path
    input_classification: InputClassification
    kind: OutcomeKind
    reason_code: str
    phase: ProcessingPhase
    intended_output_path: Path | None = None
    actual_output_path: Path | None = None
    published: bool = False
    duplicate_verified: bool = False
    source_disposition: SourceDisposition = SourceDisposition.UNCHANGED
    duplicate_disposition: DuplicateDisposition | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        """Validate the outcome and reject contradictory observed facts.

        Raises:
            ValueError: If a field or combination of fields is invalid.
        """
        _validate_path("input_path", self.input_path, required=True)
        _validate_path(
            "intended_output_path",
            self.intended_output_path,
            required=False,
        )
        _validate_path(
            "actual_output_path",
            self.actual_output_path,
            required=False,
        )
        self._validate_field_types()
        self._validate_input_classification()
        self._validate_publication_facts()
        self._validate_duplicate_facts()
        self._validate_terminal_kind()

    def _validate_field_types(self) -> None:
        """Validate scalar and enum fields before checking combinations.

        Raises:
            ValueError: If a scalar or enum field has an invalid value.
        """
        if not isinstance(self.input_classification, InputClassification):
            raise ValueError("input_classification must be an InputClassification")
        if not isinstance(self.kind, OutcomeKind):
            raise ValueError("kind must be an OutcomeKind")
        if not isinstance(self.phase, ProcessingPhase):
            raise ValueError("phase must be a ProcessingPhase")
        if not isinstance(self.source_disposition, SourceDisposition):
            raise ValueError("source_disposition must be a SourceDisposition")
        if (
            self.duplicate_disposition is not None
            and not isinstance(self.duplicate_disposition, DuplicateDisposition)
        ):
            raise ValueError(
                "duplicate_disposition must be a DuplicateDisposition or None"
            )
        if not isinstance(self.published, bool):
            raise ValueError("published must be a boolean")
        if not isinstance(self.duplicate_verified, bool):
            raise ValueError("duplicate_verified must be a boolean")
        validate_reason_code(self.reason_code)
        if self.diagnostic is not None and (
            not isinstance(self.diagnostic, str) or not self.diagnostic.strip()
        ):
            raise ValueError("diagnostic must be a non-empty string or None")

    def _validate_input_classification(self) -> None:
        """Require unsupported classification and outcome to agree.

        Raises:
            ValueError: If input classification contradicts the terminal kind.
        """
        if self.kind is OutcomeKind.UNSUPPORTED:
            if self.input_classification is not InputClassification.UNSUPPORTED_REGULAR:
                raise ValueError(
                    "an UNSUPPORTED outcome requires UNSUPPORTED_REGULAR classification"
                )
            return
        if self.input_classification is not InputClassification.ELIGIBLE_JPEG:
            raise ValueError(
                "non-UNSUPPORTED outcomes require ELIGIBLE_JPEG classification"
            )

    def _validate_publication_facts(self) -> None:
        """Require publication facts to identify permitted audited output.

        Raises:
            ValueError: If publication facts are incomplete or contradictory.
        """
        if self.published:
            if self.kind not in {OutcomeKind.SCRUBBED, OutcomeKind.FAILED}:
                raise ValueError(
                    "only SCRUBBED or FAILED outcomes may publish a new output"
                )
            if self.intended_output_path is None or self.actual_output_path is None:
                raise ValueError(
                    "a published outcome requires intended and actual output paths"
                )
            return
        if self.kind is OutcomeKind.SCRUBBED:
            raise ValueError("a SCRUBBED outcome must publish an audited output")

    def _validate_duplicate_facts(self) -> None:
        """Require duplicate verification and disposition facts to agree.

        Raises:
            ValueError: If duplicate facts are missing or contradictory.
        """
        if not self.duplicate_verified:
            if self.duplicate_disposition is not None:
                raise ValueError(
                    "duplicate_disposition requires duplicate_verified=true"
                )
            if self.kind is OutcomeKind.DUPLICATE_HANDLED:
                raise ValueError(
                    "a DUPLICATE_HANDLED outcome requires duplicate verification"
                )
            return

        if self.published:
            raise ValueError("a verified duplicate cannot publish a new output")
        if self.actual_output_path is None:
            raise ValueError("a verified duplicate requires the verified output path")
        if self.duplicate_disposition is None:
            raise ValueError("a verified duplicate requires a duplicate disposition")

        expected_source = SourceDisposition(self.duplicate_disposition.value)
        if self.source_disposition is not expected_source:
            raise ValueError("duplicate_disposition must match source_disposition")
        if (
            self.kind is OutcomeKind.DUPLICATE_HANDLED
            and self.duplicate_disposition is DuplicateDisposition.MISSING
        ):
            raise ValueError(
                "a DUPLICATE_HANDLED source cannot have a missing disposition"
            )

    def _validate_terminal_kind(self) -> None:
        """Validate facts that are specific to the selected terminal kind.

        Raises:
            ValueError: If terminal-kind facts are incomplete or contradictory.
        """
        if self.kind is OutcomeKind.SCRUBBED:
            if self.duplicate_verified:
                raise ValueError("a SCRUBBED outcome cannot be a verified duplicate")
            return

        if self.kind is OutcomeKind.DUPLICATE_HANDLED:
            return

        if self.kind is OutcomeKind.UNSUPPORTED:
            if self.intended_output_path is not None or self.actual_output_path is not None:
                raise ValueError("an UNSUPPORTED outcome cannot have output paths")
            if self.source_disposition is not SourceDisposition.UNCHANGED:
                raise ValueError("an UNSUPPORTED input must remain unchanged")
            return

        if self.kind is OutcomeKind.PLANNED:
            if self.phase is not ProcessingPhase.PLANNING:
                raise ValueError("a PLANNED outcome must finish in the PLANNING phase")
            self._validate_unchanged_without_actual_output("PLANNED")
            return

        if self.kind is OutcomeKind.PREVIEWED:
            if self.phase is not ProcessingPhase.VERIFICATION:
                raise ValueError(
                    "a PREVIEWED outcome must finish in the VERIFICATION phase"
                )
            self._validate_unchanged_without_actual_output("PREVIEWED")
            return

        if self.kind is OutcomeKind.SKIPPED:
            self._validate_unchanged_without_actual_output("SKIPPED")
            return

        if self.kind is OutcomeKind.FAILED:
            if self.diagnostic is None:
                raise ValueError("a FAILED outcome requires a diagnostic")
            if (
                self.actual_output_path is not None
                and not self.published
                and not self.duplicate_verified
            ):
                raise ValueError(
                    "a FAILED outcome may identify actual output only when published "
                    "or duplicate-verified"
                )
            return

        raise ValueError(f"unsupported outcome kind: {self.kind!r}")

    def _validate_unchanged_without_actual_output(self, kind_name: str) -> None:
        """Validate a non-mutating terminal outcome.

        Args:
            kind_name: Outcome name used in validation messages.

        Raises:
            ValueError: If the outcome reports mutation or retained output.
        """
        if self.actual_output_path is not None:
            raise ValueError(f"a {kind_name} outcome cannot have an actual output")
        if self.source_disposition is not SourceDisposition.UNCHANGED:
            raise ValueError(f"a {kind_name} input must remain unchanged")
        if self.duplicate_verified:
            raise ValueError(f"a {kind_name} outcome cannot be a verified duplicate")


class RunReport:
    """Aggregate validated outcomes and run failures for one invocation.

    Discovery owns filesystem-identity deduplication. This class retains
    normalized paths as a secondary guard against recording the same input path
    twice, but does not consult mutable filesystem state.
    """

    __slots__ = (
        "_duplicate_counts",
        "_input_keys",
        "_interrupted",
        "_legacy_counts",
        "_published",
        "_run_errors",
        "_terminal_counts",
    )

    def __init__(self) -> None:
        """Initialize an empty per-invocation report."""
        self._terminal_counts = {kind: 0 for kind in OutcomeKind}
        self._duplicate_counts = {
            disposition: 0 for disposition in DuplicateDisposition
        }
        self._input_keys: set[str] = set()
        self._published = 0
        self._run_errors = 0
        self._interrupted = False
        # Schema-1 output remains public until the schema-2 migration. Keep
        # this compatibility projection inside the sole accounting writer.
        self._legacy_counts = {
            "total": 0,
            "scrubbed": 0,
            "skipped": 0,
            "errors": 0,
            "duplicates_deleted": 0,
            "duplicates_moved": 0,
            "unsupported": 0,
        }

    def record(self, outcome: FileOutcome) -> None:
        """Record one terminal outcome atomically.

        Args:
            outcome: Validated immutable outcome to aggregate.

        Raises:
            ValueError: If outcome is invalid or its path was already recorded.
        """
        if not isinstance(outcome, FileOutcome):
            raise ValueError("outcome must be a FileOutcome")

        input_key = os.path.normcase(str(outcome.input_path.absolute()))
        if input_key in self._input_keys:
            raise ValueError(
                f"input already has a terminal outcome: {outcome.input_path}"
            )

        duplicate_disposition = (
            outcome.duplicate_disposition if outcome.duplicate_verified else None
        )
        if outcome.duplicate_verified and duplicate_disposition is None:
            raise RunReportValidationError(
                "validated duplicate outcome has no duplicate disposition"
            )
        legacy_deltas = self._legacy_deltas(outcome)

        # All rejection checks happen above this point so a failed record call
        # cannot leave partially incremented counters behind.
        self._input_keys.add(input_key)
        self._terminal_counts[outcome.kind] += 1
        if duplicate_disposition is not None:
            self._duplicate_counts[duplicate_disposition] += 1
        if outcome.published:
            self._published += 1
        for counter_name, increment in legacy_deltas.items():
            self._legacy_counts[counter_name] += increment

    @staticmethod
    def _legacy_deltas(outcome: FileOutcome) -> dict[str, int]:
        """Calculate the temporary schema-1 projection for one outcome.

        Args:
            outcome: Validated terminal outcome being recorded.

        Returns:
            Counter increments that preserve the pre-schema-2 CLI meanings.
        """
        deltas = {
            "total": 0,
            "scrubbed": 0,
            "skipped": 0,
            "errors": 0,
            "duplicates_deleted": 0,
            "duplicates_moved": 0,
            "unsupported": 0,
        }
        if outcome.kind is OutcomeKind.UNSUPPORTED:
            deltas["unsupported"] = 1
            return deltas
        if outcome.kind is OutcomeKind.PREVIEWED:
            return deltas

        deltas["total"] = 1
        if outcome.kind is OutcomeKind.SCRUBBED:
            deltas["scrubbed"] = 1
            return deltas
        if outcome.kind is OutcomeKind.FAILED:
            deltas["errors"] = 1
            deltas["scrubbed"] = int(outcome.published)
            return deltas
        if outcome.kind is OutcomeKind.SKIPPED:
            deltas["skipped"] = 1
            return deltas
        if outcome.kind is OutcomeKind.PLANNED:
            if outcome.reason_code == "scrub_planned":
                deltas["scrubbed"] = 1
            elif outcome.reason_code == "existing_output_check_planned":
                deltas["duplicates_deleted"] = 1
            return deltas
        if outcome.kind is OutcomeKind.DUPLICATE_HANDLED:
            if outcome.duplicate_disposition is DuplicateDisposition.MOVED:
                deltas["duplicates_moved"] = 1
            elif outcome.duplicate_disposition is DuplicateDisposition.DELETED:
                deltas["duplicates_deleted"] = 1
            elif outcome.duplicate_disposition is DuplicateDisposition.UNCHANGED:
                deltas["skipped"] = 1
            return deltas
        return deltas

    def record_run_failure(self, failure: RunFailure) -> None:
        """Record one controlled failure not assigned to a classified input.

        Args:
            failure: Validated immutable run-level failure.

        Returns:
            None.

        Raises:
            ValueError: If failure is not a RunFailure.
        """
        if not isinstance(failure, RunFailure):
            raise ValueError("failure must be a RunFailure")
        self._run_errors += 1
        # Schema 1 has no run_errors field. Project the failure into its
        # existing aggregate so controlled preflight failures cannot print a
        # misleading successful summary before the schema-2 migration.
        self._legacy_counts["errors"] += 1
        self._interrupted = self._interrupted or failure.interrupted

    @property
    def examined(self) -> int:
        """Return the number of classified inputs with terminal outcomes."""
        return sum(self._terminal_counts.values())

    @property
    def scrubbed(self) -> int:
        """Return the number of successfully scrubbed inputs."""
        return self._terminal_counts[OutcomeKind.SCRUBBED]

    @property
    def duplicates_handled(self) -> int:
        """Return the number of successfully handled duplicate inputs."""
        return self._terminal_counts[OutcomeKind.DUPLICATE_HANDLED]

    @property
    def verified_duplicates(self) -> int:
        """Return the number of inputs verified as byte-identical duplicates."""
        return sum(self._duplicate_counts.values())

    @property
    def unsupported(self) -> int:
        """Return the number of unsupported regular-file inputs."""
        return self._terminal_counts[OutcomeKind.UNSUPPORTED]

    @property
    def planned(self) -> int:
        """Return the number of successful dry-run plans."""
        return self._terminal_counts[OutcomeKind.PLANNED]

    @property
    def previewed(self) -> int:
        """Return the number of successful disposable previews."""
        return self._terminal_counts[OutcomeKind.PREVIEWED]

    @property
    def skipped(self) -> int:
        """Return the number of deliberately excluded eligible JPEG inputs."""
        return self._terminal_counts[OutcomeKind.SKIPPED]

    @property
    def failed(self) -> int:
        """Return the number of inputs with failed terminal outcomes."""
        return self._terminal_counts[OutcomeKind.FAILED]

    @property
    def jpeg_total(self) -> int:
        """Return the number of eligible JPEG inputs with terminal outcomes."""
        return self.examined - self.unsupported

    @property
    def total(self) -> int:
        """Return the schema-2 alias for the JPEG-only total."""
        return self.jpeg_total

    @property
    def duplicates_unchanged(self) -> int:
        """Return the number of verified duplicate sources left unchanged."""
        return self._duplicate_counts[DuplicateDisposition.UNCHANGED]

    @property
    def duplicates_moved(self) -> int:
        """Return the number of verified duplicate sources moved."""
        return self._duplicate_counts[DuplicateDisposition.MOVED]

    @property
    def duplicates_deleted(self) -> int:
        """Return the number of verified duplicate sources deleted."""
        return self._duplicate_counts[DuplicateDisposition.DELETED]

    @property
    def duplicates_missing(self) -> int:
        """Return the number of verified duplicate sources found missing."""
        return self._duplicate_counts[DuplicateDisposition.MISSING]

    @property
    def published(self) -> int:
        """Return the number of inputs that published a new audited output."""
        return self._published

    @property
    def run_errors(self) -> int:
        """Return the number of failures not assigned to classified inputs."""
        return self._run_errors

    @property
    def interrupted(self) -> bool:
        """Return whether a recorded run failure was an interruption."""
        return self._interrupted

    @property
    def errors(self) -> int:
        """Return the schema-2 aggregate error count."""
        return self.failed + self.run_errors

    def validate(self) -> None:
        """Verify all aggregation and schema-2 accounting identities.

        Raises:
            RunReportValidationError: If any counter is invalid or inconsistent.
        """
        all_counts = (
            *self._terminal_counts.values(),
            *self._duplicate_counts.values(),
            *self._legacy_counts.values(),
            self._published,
            self._run_errors,
        )
        if any(
            not isinstance(count, int) or isinstance(count, bool) or count < 0
            for count in all_counts
        ):
            raise RunReportValidationError(
                "run report counters must be non-negative integers"
            )
        if not isinstance(self._interrupted, bool):
            raise RunReportValidationError("interrupted state must be a boolean")

        terminal_total = (
            self.scrubbed
            + self.duplicates_handled
            + self.unsupported
            + self.planned
            + self.previewed
            + self.skipped
            + self.failed
        )
        if self.examined != terminal_total:
            raise RunReportValidationError(
                "examined does not equal the sum of terminal outcomes"
            )
        if len(self._input_keys) != self.examined:
            raise RunReportValidationError(
                "recorded input paths do not match terminal outcomes"
            )

        expected_jpeg_total = (
            self.scrubbed
            + self.duplicates_handled
            + self.planned
            + self.previewed
            + self.skipped
            + self.failed
        )
        if self.jpeg_total != expected_jpeg_total:
            raise RunReportValidationError(
                "jpeg_total does not equal eligible JPEG terminal outcomes"
            )

        duplicate_total = (
            self.duplicates_deleted
            + self.duplicates_moved
            + self.duplicates_unchanged
            + self.duplicates_missing
        )
        if self.verified_duplicates != duplicate_total:
            raise RunReportValidationError(
                "verified_duplicates does not equal duplicate dispositions"
            )
        if self.duplicates_handled > self.verified_duplicates:
            raise RunReportValidationError(
                "duplicates_handled exceeds verified_duplicates"
            )
        if self.errors != self.failed + self.run_errors:
            raise RunReportValidationError(
                "errors does not equal failed plus run_errors"
            )
        if self.published > self.scrubbed + self.failed:
            raise RunReportValidationError(
                "published exceeds outcomes permitted to publish output"
            )
        if self.interrupted and self.run_errors == 0:
            raise RunReportValidationError(
                "interrupted state requires a recorded run failure"
            )

    def counts(self) -> dict[str, int]:
        """Return a validated snapshot of all currently defined counters.

        Returns:
            Fresh dictionary whose values satisfy report reconciliation rules.

        Raises:
            RunReportValidationError: If internal report state is inconsistent.
        """
        self.validate()
        return {
            "total": self.total,
            "jpeg_total": self.jpeg_total,
            "scrubbed": self.scrubbed,
            "duplicates_handled": self.duplicates_handled,
            "unsupported": self.unsupported,
            "planned": self.planned,
            "previewed": self.previewed,
            "skipped": self.skipped,
            "failed": self.failed,
            "errors": self.errors,
            "verified_duplicates": self.verified_duplicates,
            "duplicates_deleted": self.duplicates_deleted,
            "duplicates_moved": self.duplicates_moved,
            "duplicates_unchanged": self.duplicates_unchanged,
            "duplicates_missing": self.duplicates_missing,
            "published": self.published,
            "run_errors": self.run_errors,
            "interrupted": int(self.interrupted),
            "examined": self.examined,
        }

    def legacy_counts(self) -> dict[str, int]:
        """Return the temporary schema-1 counter projection.

        Returns:
            Fresh dictionary preserving the existing CLI summary meanings.

        Raises:
            RunReportValidationError: If internal report state is inconsistent.
        """
        self.validate()
        counts = dict(self._legacy_counts)
        counts["examined"] = counts["total"] + counts["unsupported"]
        return counts


def _validate_path(name: str, path: Path | None, required: bool) -> None:
    """Validate one path value without consulting mutable filesystem state.

    Args:
        name: Field name used in validation messages.
        path: Path value to validate.
        required: Whether None must be rejected.

    Raises:
        ValueError: If the path is missing, has the wrong type, or is invalid.
    """
    if path is None:
        if required:
            raise ValueError(f"{name} must be a pathlib.Path")
        return
    if not isinstance(path, Path):
        raise ValueError(f"{name} must be a pathlib.Path or None")
    if not path.name or "\x00" in str(path):
        raise ValueError(f"{name} must identify a valid file path")


def validate_reason_code(reason_code: str) -> None:
    """Validate one stable machine-readable reason code.

    Args:
        reason_code: Candidate reason code.

    Raises:
        ValueError: If the reason code is not a lowercase identifier.
    """
    if (
        not isinstance(reason_code, str)
        or _REASON_CODE_PATTERN.fullmatch(reason_code) is None
    ):
        raise ValueError(
            "reason_code must use lowercase letters, digits, and underscores"
        )


__all__ = [
    "DuplicateDisposition",
    "FileOutcome",
    "InputClassification",
    "OutcomeKind",
    "ProcessingPhase",
    "RunFailure",
    "RunReport",
    "RunReportValidationError",
    "SourceDisposition",
    "validate_reason_code",
]
