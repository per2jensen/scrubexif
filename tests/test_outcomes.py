# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for scrubexif's immutable outcome and accounting model."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from scrubexif.outcomes import (
    DuplicateDisposition,
    FileOutcome,
    InputClassification,
    OutcomeKind,
    ProcessingPhase,
    RunFailure,
    RunReport,
    RunReportValidationError,
    SourceDisposition,
)


def test_file_outcome_each_terminal_kind_valid_facts_succeeds() -> None:
    """Accept one internally consistent instance of every terminal kind."""
    input_path = Path("/photos/input/photo.jpg")
    intended_path = Path("/photos/output/photo.jpg")
    actual_path = Path("/photos/output/photo-r1.jpg")

    outcomes = (
        FileOutcome(
            input_path=input_path,
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.SCRUBBED,
            reason_code="audited_output_published",
            phase=ProcessingPhase.SOURCE_FINALIZATION,
            intended_output_path=intended_path,
            actual_output_path=actual_path,
            published=True,
            source_disposition=SourceDisposition.MOVED,
        ),
        FileOutcome(
            input_path=input_path,
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.DUPLICATE_HANDLED,
            reason_code="verified_duplicate_moved",
            phase=ProcessingPhase.DUPLICATE_HANDLING,
            intended_output_path=intended_path,
            actual_output_path=intended_path,
            duplicate_verified=True,
            source_disposition=SourceDisposition.MOVED,
            duplicate_disposition=DuplicateDisposition.MOVED,
        ),
        FileOutcome(
            input_path=Path("/photos/input/notes.txt"),
            input_classification=InputClassification.UNSUPPORTED_REGULAR,
            kind=OutcomeKind.UNSUPPORTED,
            reason_code="unsupported_extension",
            phase=ProcessingPhase.DISCOVERY,
        ),
        FileOutcome(
            input_path=input_path,
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.PLANNED,
            reason_code="dry_run_planned",
            phase=ProcessingPhase.PLANNING,
            intended_output_path=intended_path,
        ),
        FileOutcome(
            input_path=input_path,
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.PREVIEWED,
            reason_code="preview_audited",
            phase=ProcessingPhase.VERIFICATION,
            intended_output_path=intended_path,
        ),
        FileOutcome(
            input_path=input_path,
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.SKIPPED,
            reason_code="unstable_input",
            phase=ProcessingPhase.DISCOVERY,
        ),
        FileOutcome(
            input_path=input_path,
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.FAILED,
            reason_code="scrub_command_failed",
            phase=ProcessingPhase.SCRUB,
            diagnostic="jpegtran returned exit status 1",
        ),
    )

    assert {outcome.kind for outcome in outcomes} == set(OutcomeKind)


def test_file_outcome_failed_after_publication_preserves_published_fact() -> None:
    """Allow a failed operation to report that audited output was published."""
    outcome = FileOutcome(
        input_path=Path("/photos/input/photo.jpg"),
        input_classification=InputClassification.ELIGIBLE_JPEG,
        kind=OutcomeKind.FAILED,
        reason_code="source_archive_failed",
        phase=ProcessingPhase.SOURCE_FINALIZATION,
        intended_output_path=Path("/photos/output/photo.jpg"),
        actual_output_path=Path("/photos/output/photo.jpg"),
        published=True,
        diagnostic="output published but source archival failed",
    )

    assert outcome.kind is OutcomeKind.FAILED
    assert outcome.published is True


def test_file_outcome_failed_duplicate_preserves_verification_fact() -> None:
    """Keep duplicate verification when the requested policy fails."""
    outcome = FileOutcome(
        input_path=Path("/photos/input/photo.jpg"),
        input_classification=InputClassification.ELIGIBLE_JPEG,
        kind=OutcomeKind.FAILED,
        reason_code="duplicate_policy_failed",
        phase=ProcessingPhase.DUPLICATE_HANDLING,
        actual_output_path=Path("/photos/output/photo.jpg"),
        duplicate_verified=True,
        duplicate_disposition=DuplicateDisposition.UNCHANGED,
        diagnostic="verified duplicate rejected by policy",
    )

    assert outcome.duplicate_verified is True
    assert outcome.duplicate_disposition is DuplicateDisposition.UNCHANGED


def test_file_outcome_failed_duplicate_accepts_missing_disposition() -> None:
    """Represent a verified duplicate source lost through concurrent change."""
    outcome = FileOutcome(
        input_path=Path("/photos/input/photo.jpg"),
        input_classification=InputClassification.ELIGIBLE_JPEG,
        kind=OutcomeKind.FAILED,
        reason_code="duplicate_source_missing",
        phase=ProcessingPhase.DUPLICATE_HANDLING,
        actual_output_path=Path("/photos/output/photo.jpg"),
        duplicate_verified=True,
        source_disposition=SourceDisposition.MISSING,
        duplicate_disposition=DuplicateDisposition.MISSING,
        diagnostic="source disappeared during duplicate handling",
    )

    assert outcome.source_disposition is SourceDisposition.MISSING


def test_file_outcome_frozen_instance_rejects_mutation() -> None:
    """Prevent a validated outcome from changing after construction."""
    outcome = FileOutcome(
        input_path=Path("/photos/input/notes.txt"),
        input_classification=InputClassification.UNSUPPORTED_REGULAR,
        kind=OutcomeKind.UNSUPPORTED,
        reason_code="unsupported_extension",
        phase=ProcessingPhase.DISCOVERY,
    )

    with pytest.raises(FrozenInstanceError):
        outcome.reason_code = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "reason_code",
    ["", "Uppercase", "contains-hyphen", "1_starts_with_digit"],
)
def test_file_outcome_invalid_reason_code_raises_value_error(
    reason_code: str,
) -> None:
    """Reject reason codes that are not stable lowercase identifiers.

    Args:
        reason_code: Invalid machine-readable reason code.
    """
    with pytest.raises(ValueError, match="reason_code"):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.SKIPPED,
            reason_code=reason_code,
            phase=ProcessingPhase.DISCOVERY,
        )


@pytest.mark.parametrize("input_path", [None, "photo.jpg", Path("/")])
def test_file_outcome_invalid_input_path_raises_value_error(
    input_path: object,
) -> None:
    """Reject missing, incorrectly typed, and non-file input paths.

    Args:
        input_path: Invalid path value.
    """
    with pytest.raises(ValueError, match="input_path"):
        FileOutcome(
            input_path=input_path,  # type: ignore[arg-type]
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.SKIPPED,
            reason_code="unstable_input",
            phase=ProcessingPhase.DISCOVERY,
        )


def test_file_outcome_scrubbed_without_publication_raises_value_error() -> None:
    """Reject a scrubbed classification when no output was published."""
    with pytest.raises(ValueError, match="SCRUBBED.*publish"):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.SCRUBBED,
            reason_code="audited_output_published",
            phase=ProcessingPhase.PUBLICATION,
            intended_output_path=Path("/photos/output/photo.jpg"),
            actual_output_path=Path("/photos/output/photo.jpg"),
        )


def test_file_outcome_published_without_output_paths_raises_value_error() -> None:
    """Reject publication facts that omit intended or actual output paths."""
    with pytest.raises(ValueError, match="requires intended and actual"):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.SCRUBBED,
            reason_code="audited_output_published",
            phase=ProcessingPhase.PUBLICATION,
            published=True,
        )


def test_file_outcome_duplicate_handled_without_verification_raises_value_error() -> None:
    """Reject successful duplicate handling without verified identity."""
    with pytest.raises(ValueError, match="requires duplicate verification"):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.DUPLICATE_HANDLED,
            reason_code="verified_duplicate_unchanged",
            phase=ProcessingPhase.DUPLICATE_HANDLING,
        )


def test_file_outcome_verified_duplicate_without_disposition_raises_value_error() -> None:
    """Reject duplicate verification whose final disposition is unknown."""
    with pytest.raises(ValueError, match="requires a duplicate disposition"):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.DUPLICATE_HANDLED,
            reason_code="verified_duplicate",
            phase=ProcessingPhase.DUPLICATE_HANDLING,
            actual_output_path=Path("/photos/output/photo.jpg"),
            duplicate_verified=True,
        )


def test_file_outcome_verified_duplicate_without_output_raises_value_error() -> None:
    """Reject duplicate verification that cannot identify verified output."""
    with pytest.raises(ValueError, match="requires the verified output path"):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.DUPLICATE_HANDLED,
            reason_code="verified_duplicate",
            phase=ProcessingPhase.DUPLICATE_HANDLING,
            duplicate_verified=True,
            duplicate_disposition=DuplicateDisposition.UNCHANGED,
        )


def test_file_outcome_duplicate_disposition_without_verification_raises() -> None:
    """Reject a duplicate disposition without the verification fact."""
    with pytest.raises(ValueError, match="requires duplicate_verified=true"):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.FAILED,
            reason_code="duplicate_policy_failed",
            phase=ProcessingPhase.DUPLICATE_HANDLING,
            duplicate_disposition=DuplicateDisposition.UNCHANGED,
            diagnostic="duplicate policy failed",
        )


def test_file_outcome_duplicate_dispositions_that_disagree_raise_value_error() -> None:
    """Reject contradictory source and duplicate disposition facts."""
    with pytest.raises(ValueError, match="must match source_disposition"):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.DUPLICATE_HANDLED,
            reason_code="verified_duplicate_moved",
            phase=ProcessingPhase.DUPLICATE_HANDLING,
            actual_output_path=Path("/photos/output/photo.jpg"),
            duplicate_verified=True,
            source_disposition=SourceDisposition.UNCHANGED,
            duplicate_disposition=DuplicateDisposition.MOVED,
        )


def test_file_outcome_duplicate_handled_missing_source_raises_value_error() -> None:
    """Reject successful duplicate handling when its source is missing."""
    with pytest.raises(ValueError, match="cannot have a missing disposition"):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.DUPLICATE_HANDLED,
            reason_code="verified_duplicate_missing",
            phase=ProcessingPhase.DUPLICATE_HANDLING,
            actual_output_path=Path("/photos/output/photo.jpg"),
            duplicate_verified=True,
            source_disposition=SourceDisposition.MISSING,
            duplicate_disposition=DuplicateDisposition.MISSING,
        )


def test_file_outcome_unsupported_with_jpeg_classification_raises_value_error() -> None:
    """Reject an unsupported outcome classified as an eligible JPEG."""
    with pytest.raises(ValueError, match="requires UNSUPPORTED_REGULAR"):
        FileOutcome(
            input_path=Path("/photos/input/notes.txt"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.UNSUPPORTED,
            reason_code="unsupported_extension",
            phase=ProcessingPhase.DISCOVERY,
        )


def test_file_outcome_unsupported_classification_with_skip_raises_value_error() -> None:
    """Reject a non-unsupported outcome for an unsupported regular file."""
    with pytest.raises(ValueError, match="require ELIGIBLE_JPEG"):
        FileOutcome(
            input_path=Path("/photos/input/notes.txt"),
            input_classification=InputClassification.UNSUPPORTED_REGULAR,
            kind=OutcomeKind.SKIPPED,
            reason_code="excluded_input",
            phase=ProcessingPhase.DISCOVERY,
        )


def test_file_outcome_unsupported_with_output_path_raises_value_error() -> None:
    """Reject output facts for an unsupported input."""
    with pytest.raises(ValueError, match="cannot have output paths"):
        FileOutcome(
            input_path=Path("/photos/input/notes.txt"),
            input_classification=InputClassification.UNSUPPORTED_REGULAR,
            kind=OutcomeKind.UNSUPPORTED,
            reason_code="unsupported_extension",
            phase=ProcessingPhase.DISCOVERY,
            intended_output_path=Path("/photos/output/notes.txt"),
        )


def test_file_outcome_unsupported_with_changed_source_raises_value_error() -> None:
    """Reject an unsupported classification after changing its source."""
    with pytest.raises(ValueError, match="must remain unchanged"):
        FileOutcome(
            input_path=Path("/photos/input/notes.txt"),
            input_classification=InputClassification.UNSUPPORTED_REGULAR,
            kind=OutcomeKind.UNSUPPORTED,
            reason_code="unsupported_extension",
            phase=ProcessingPhase.DISCOVERY,
            source_disposition=SourceDisposition.DELETED,
        )


@pytest.mark.parametrize(
    ("kind", "phase", "expected_message"),
    [
        (OutcomeKind.PLANNED, ProcessingPhase.DISCOVERY, "PLANNED.*PLANNING"),
        (OutcomeKind.PREVIEWED, ProcessingPhase.SCRUB, "PREVIEWED.*VERIFICATION"),
    ],
)
def test_file_outcome_nonmutating_success_wrong_phase_raises_value_error(
    kind: OutcomeKind,
    phase: ProcessingPhase,
    expected_message: str,
) -> None:
    """Reject planned and previewed outcomes at the wrong processing phase.

    Args:
        kind: Non-mutating terminal kind under test.
        phase: Invalid processing phase for that kind.
        expected_message: Regular expression expected in the validation error.
    """
    with pytest.raises(ValueError, match=expected_message):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=kind,
            reason_code="nonmutating_success",
            phase=phase,
        )


@pytest.mark.parametrize(
    "kind",
    [OutcomeKind.PLANNED, OutcomeKind.PREVIEWED, OutcomeKind.SKIPPED],
)
def test_file_outcome_nonmutating_kind_with_changed_source_raises_value_error(
    kind: OutcomeKind,
) -> None:
    """Reject non-mutating outcomes that claim a changed source.

    Args:
        kind: Non-mutating terminal kind under test.
    """
    phase = (
        ProcessingPhase.PLANNING
        if kind is OutcomeKind.PLANNED
        else ProcessingPhase.VERIFICATION
        if kind is OutcomeKind.PREVIEWED
        else ProcessingPhase.DISCOVERY
    )
    with pytest.raises(ValueError, match="must remain unchanged"):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=kind,
            reason_code="nonmutating_result",
            phase=phase,
            source_disposition=SourceDisposition.MOVED,
        )


@pytest.mark.parametrize(
    "kind",
    [OutcomeKind.PLANNED, OutcomeKind.PREVIEWED, OutcomeKind.SKIPPED],
)
def test_file_outcome_nonmutating_kind_with_actual_output_raises_value_error(
    kind: OutcomeKind,
) -> None:
    """Reject non-mutating outcomes that claim a retained actual output.

    Args:
        kind: Non-mutating terminal kind under test.
    """
    phase = (
        ProcessingPhase.PLANNING
        if kind is OutcomeKind.PLANNED
        else ProcessingPhase.VERIFICATION
        if kind is OutcomeKind.PREVIEWED
        else ProcessingPhase.DISCOVERY
    )
    with pytest.raises(ValueError, match="cannot have an actual output"):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=kind,
            reason_code="nonmutating_result",
            phase=phase,
            actual_output_path=Path("/photos/output/photo.jpg"),
        )


def test_file_outcome_failed_without_diagnostic_raises_value_error() -> None:
    """Require actionable human context for every failed input outcome."""
    with pytest.raises(ValueError, match="FAILED outcome requires a diagnostic"):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.FAILED,
            reason_code="scrub_command_failed",
            phase=ProcessingPhase.SCRUB,
        )


def test_file_outcome_failed_with_unobserved_actual_output_raises_value_error() -> None:
    """Reject an actual output not established by publication or verification."""
    with pytest.raises(ValueError, match="only when published or duplicate-verified"):
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.FAILED,
            reason_code="publication_failed",
            phase=ProcessingPhase.PUBLICATION,
            actual_output_path=Path("/photos/output/photo.jpg"),
            diagnostic="publication failed before commit",
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "expected_message"),
    [
        (
            "input_classification",
            "eligible_jpeg",
            "input_classification must be an InputClassification",
        ),
        ("kind", "skipped", "kind must be an OutcomeKind"),
        ("phase", "discovery", "phase must be a ProcessingPhase"),
        (
            "source_disposition",
            "unchanged",
            "source_disposition must be a SourceDisposition",
        ),
        (
            "duplicate_disposition",
            "unchanged",
            "duplicate_disposition must be a DuplicateDisposition or None",
        ),
        ("published", 1, "published must be a boolean"),
        ("duplicate_verified", 1, "duplicate_verified must be a boolean"),
        ("diagnostic", "   ", "diagnostic must be a non-empty string or None"),
        (
            "intended_output_path",
            "photo.jpg",
            "intended_output_path must be a pathlib.Path or None",
        ),
    ],
)
def test_file_outcome_invalid_field_type_raises_value_error(
    field_name: str,
    invalid_value: object,
    expected_message: str,
) -> None:
    """Reject wrong runtime types and blank diagnostics at the model boundary.

    Args:
        field_name: FileOutcome field to replace.
        invalid_value: Invalid runtime value for the field.
        expected_message: Expected validation error text.
    """
    arguments: dict[str, object] = {
        "input_path": Path("/photos/input/photo.jpg"),
        "input_classification": InputClassification.ELIGIBLE_JPEG,
        "kind": OutcomeKind.SKIPPED,
        "reason_code": "unstable_input",
        "phase": ProcessingPhase.DISCOVERY,
    }
    arguments[field_name] = invalid_value

    with pytest.raises(ValueError, match=expected_message):
        FileOutcome(**arguments)  # type: ignore[arg-type]


def test_run_failure_valid_preclassification_failure_succeeds() -> None:
    """Accept a path-related failure that occurred before classification."""
    failure = RunFailure(
        reason_code="input_classification_failed",
        phase=ProcessingPhase.DISCOVERY,
        diagnostic="input disappeared during discovery",
        input_path=Path("/photos/input/photo.jpg"),
    )

    assert failure.input_path == Path("/photos/input/photo.jpg")
    assert failure.interrupted is False


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "expected_message"),
    [
        ("reason_code", "Bad-Code", "reason_code"),
        ("phase", "discovery", "phase must be a ProcessingPhase"),
        ("diagnostic", "", "diagnostic must be a non-empty string"),
        ("input_path", "photo.jpg", "input_path must be a pathlib.Path or None"),
        ("interrupted", 1, "interrupted must be a boolean"),
    ],
)
def test_run_failure_invalid_field_raises_value_error(
    field_name: str,
    invalid_value: object,
    expected_message: str,
) -> None:
    """Reject invalid run-level failure fields.

    Args:
        field_name: RunFailure field to replace.
        invalid_value: Invalid runtime value for the field.
        expected_message: Expected validation error text.
    """
    arguments: dict[str, object] = {
        "reason_code": "preflight_failed",
        "phase": ProcessingPhase.DISCOVERY,
        "diagnostic": "preflight could not complete",
    }
    arguments[field_name] = invalid_value

    with pytest.raises(ValueError, match=expected_message):
        RunFailure(**arguments)  # type: ignore[arg-type]


def test_run_report_empty_report_reconciles_to_zero() -> None:
    """Validate that a fresh report contains only zero counters."""
    report = RunReport()

    report.validate()

    assert set(report.counts().values()) == {0}


def test_run_report_mixed_terminal_outcomes_reconcile() -> None:
    """Aggregate every terminal kind while preserving orthogonal facts."""
    report = RunReport()
    output_path = Path("/photos/output/photo.jpg")
    outcomes = (
        FileOutcome(
            input_path=Path("/photos/input/scrubbed.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.SCRUBBED,
            reason_code="audited_output_published",
            phase=ProcessingPhase.PUBLICATION,
            intended_output_path=output_path,
            actual_output_path=output_path,
            published=True,
        ),
        FileOutcome(
            input_path=Path("/photos/input/duplicate.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.DUPLICATE_HANDLED,
            reason_code="verified_duplicate_deleted",
            phase=ProcessingPhase.DUPLICATE_HANDLING,
            actual_output_path=output_path,
            duplicate_verified=True,
            source_disposition=SourceDisposition.DELETED,
            duplicate_disposition=DuplicateDisposition.DELETED,
        ),
        FileOutcome(
            input_path=Path("/photos/input/notes.txt"),
            input_classification=InputClassification.UNSUPPORTED_REGULAR,
            kind=OutcomeKind.UNSUPPORTED,
            reason_code="unsupported_extension",
            phase=ProcessingPhase.DISCOVERY,
        ),
        FileOutcome(
            input_path=Path("/photos/input/planned.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.PLANNED,
            reason_code="dry_run_planned",
            phase=ProcessingPhase.PLANNING,
            intended_output_path=Path("/photos/output/planned.jpg"),
        ),
        FileOutcome(
            input_path=Path("/photos/input/previewed.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.PREVIEWED,
            reason_code="preview_audited",
            phase=ProcessingPhase.VERIFICATION,
        ),
        FileOutcome(
            input_path=Path("/photos/input/unstable.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.SKIPPED,
            reason_code="unstable_input",
            phase=ProcessingPhase.DISCOVERY,
        ),
        FileOutcome(
            input_path=Path("/photos/input/published-failure.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.FAILED,
            reason_code="source_archive_failed",
            phase=ProcessingPhase.SOURCE_FINALIZATION,
            intended_output_path=Path("/photos/output/published-failure.jpg"),
            actual_output_path=Path("/photos/output/published-failure.jpg"),
            published=True,
            diagnostic="output published but source archival failed",
        ),
        FileOutcome(
            input_path=Path("/photos/input/failed-duplicate.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.FAILED,
            reason_code="duplicate_policy_failed",
            phase=ProcessingPhase.DUPLICATE_HANDLING,
            actual_output_path=output_path,
            duplicate_verified=True,
            duplicate_disposition=DuplicateDisposition.UNCHANGED,
            diagnostic="verified duplicate rejected by policy",
        ),
    )

    for outcome in outcomes:
        report.record(outcome)

    assert report.counts() == {
        "total": 7,
        "jpeg_total": 7,
        "scrubbed": 1,
        "duplicates_handled": 1,
        "unsupported": 1,
        "planned": 1,
        "previewed": 1,
        "skipped": 1,
        "failed": 2,
        "errors": 2,
        "verified_duplicates": 2,
        "duplicates_deleted": 1,
        "duplicates_moved": 0,
        "duplicates_unchanged": 1,
        "duplicates_missing": 0,
        "published": 2,
        "run_errors": 0,
        "interrupted": 0,
        "examined": 8,
    }


@pytest.mark.parametrize(
    ("disposition", "expected_counter"),
    [
        (DuplicateDisposition.UNCHANGED, "duplicates_unchanged"),
        (DuplicateDisposition.MOVED, "duplicates_moved"),
        (DuplicateDisposition.DELETED, "duplicates_deleted"),
        (DuplicateDisposition.MISSING, "duplicates_missing"),
    ],
)
def test_run_report_duplicate_disposition_increments_matching_counter(
    disposition: DuplicateDisposition,
    expected_counter: str,
) -> None:
    """Aggregate every observed verified-duplicate disposition.

    Args:
        disposition: Duplicate source disposition under test.
        expected_counter: Counter that must be incremented.
    """
    kind = (
        OutcomeKind.FAILED
        if disposition is DuplicateDisposition.MISSING
        else OutcomeKind.DUPLICATE_HANDLED
    )
    report = RunReport()
    report.record(
        FileOutcome(
            input_path=Path(f"/photos/input/{disposition.value}.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=kind,
            reason_code=f"verified_duplicate_{disposition.value}",
            phase=ProcessingPhase.DUPLICATE_HANDLING,
            actual_output_path=Path("/photos/output/photo.jpg"),
            duplicate_verified=True,
            source_disposition=SourceDisposition(disposition.value),
            duplicate_disposition=disposition,
            diagnostic=(
                "duplicate source disappeared"
                if kind is OutcomeKind.FAILED
                else None
            ),
        )
    )

    counts = report.counts()

    assert counts[expected_counter] == 1
    assert report.verified_duplicates == 1
    assert report.duplicates_handled == int(kind is OutcomeKind.DUPLICATE_HANDLED)


def test_run_report_failed_duplicate_counts_fact_not_handled_outcome() -> None:
    """Count failed duplicate policy as verified but not successfully handled."""
    report = RunReport()
    report.record(
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.FAILED,
            reason_code="duplicate_policy_failed",
            phase=ProcessingPhase.DUPLICATE_HANDLING,
            actual_output_path=Path("/photos/output/photo.jpg"),
            duplicate_verified=True,
            duplicate_disposition=DuplicateDisposition.UNCHANGED,
            diagnostic="verified duplicate rejected by policy",
        )
    )

    assert report.failed == 1
    assert report.verified_duplicates == 1
    assert report.duplicates_unchanged == 1
    assert report.duplicates_handled == 0


def test_run_report_run_failure_does_not_change_file_totals() -> None:
    """Keep pre-classification failure outside examined and JPEG totals."""
    report = RunReport()
    report.record_run_failure(
        RunFailure(
            reason_code="input_classification_failed",
            phase=ProcessingPhase.DISCOVERY,
            diagnostic="input disappeared during discovery",
            input_path=Path("/photos/input/photo.jpg"),
        )
    )

    assert report.examined == 0
    assert report.jpeg_total == 0
    assert report.total == 0
    assert report.failed == 0
    assert report.run_errors == 1
    assert report.errors == 1
    assert report.legacy_counts()["errors"] == 1


def test_run_report_interrupted_failure_sets_interrupted_fact() -> None:
    """Record interruption separately from its run-level error count."""
    report = RunReport()
    report.record_run_failure(
        RunFailure(
            reason_code="run_interrupted",
            phase=ProcessingPhase.SCRUB,
            diagnostic="received SIGTERM",
            interrupted=True,
        )
    )

    assert report.run_errors == 1
    assert report.interrupted is True
    assert report.counts()["interrupted"] == 1


def test_run_report_same_outcome_twice_raises_without_changing_counts() -> None:
    """Reject a repeated terminal outcome without partial counter mutation."""
    report = RunReport()
    outcome = FileOutcome(
        input_path=Path("/photos/input/photo.jpg"),
        input_classification=InputClassification.ELIGIBLE_JPEG,
        kind=OutcomeKind.SKIPPED,
        reason_code="unstable_input",
        phase=ProcessingPhase.DISCOVERY,
    )
    report.record(outcome)
    original_counts = report.counts()

    with pytest.raises(ValueError, match="already has a terminal outcome"):
        report.record(outcome)

    assert report.counts() == original_counts


def test_run_report_second_outcome_for_same_path_raises_value_error() -> None:
    """Enforce one terminal classification per normalized input path."""
    report = RunReport()
    input_path = Path("/photos/input/photo.jpg")
    report.record(
        FileOutcome(
            input_path=input_path,
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.SKIPPED,
            reason_code="unstable_input",
            phase=ProcessingPhase.DISCOVERY,
        )
    )

    with pytest.raises(ValueError, match="already has a terminal outcome"):
        report.record(
            FileOutcome(
                input_path=input_path,
                input_classification=InputClassification.ELIGIBLE_JPEG,
                kind=OutcomeKind.FAILED,
                reason_code="input_disappeared",
                phase=ProcessingPhase.SCRUB,
                diagnostic="input disappeared before processing",
            )
        )


def test_run_report_wrong_outcome_type_raises_value_error() -> None:
    """Reject unvalidated values at the outcome recording boundary."""
    report = RunReport()

    with pytest.raises(ValueError, match="outcome must be a FileOutcome"):
        report.record("scrubbed")  # type: ignore[arg-type]


def test_run_report_wrong_run_failure_type_raises_without_mutation() -> None:
    """Reject unvalidated run failures without changing error counters."""
    report = RunReport()

    with pytest.raises(ValueError, match="failure must be a RunFailure"):
        report.record_run_failure("preflight failed")  # type: ignore[arg-type]

    assert report.run_errors == 0


def test_run_report_corrupt_counter_fails_reconciliation() -> None:
    """Refuse to expose a normal snapshot after counter corruption."""
    report = RunReport()

    # Deliberate private-state corruption verifies the final defensive gate.
    report._terminal_counts[OutcomeKind.FAILED] = -1  # type: ignore[attr-defined]

    with pytest.raises(RunReportValidationError, match="non-negative integers"):
        report.counts()


def test_run_report_handled_duplicate_without_fact_fails_reconciliation() -> None:
    """Reject aggregate state that loses a handled duplicate's verification."""
    report = RunReport()
    report.record(
        FileOutcome(
            input_path=Path("/photos/input/photo.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.DUPLICATE_HANDLED,
            reason_code="verified_duplicate_unchanged",
            phase=ProcessingPhase.DUPLICATE_HANDLING,
            actual_output_path=Path("/photos/output/photo.jpg"),
            duplicate_verified=True,
            duplicate_disposition=DuplicateDisposition.UNCHANGED,
        )
    )
    report._duplicate_counts[DuplicateDisposition.UNCHANGED] = 0  # type: ignore[attr-defined]

    with pytest.raises(
        RunReportValidationError,
        match="duplicates_handled exceeds verified_duplicates",
    ):
        report.counts()


def test_run_report_interrupted_without_run_error_fails_reconciliation() -> None:
    """Reject interruption state that has no corresponding run failure."""
    report = RunReport()
    report._interrupted = True  # type: ignore[attr-defined]

    with pytest.raises(
        RunReportValidationError,
        match="interrupted state requires a recorded run failure",
    ):
        report.counts()
