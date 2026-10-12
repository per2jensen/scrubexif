# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the typed operation-level scrub result."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from scrubexif import scrub
from scrubexif.outcomes import (
    DuplicateDisposition,
    FileOutcome,
    InputClassification,
    OutcomeKind,
    ProcessingPhase,
    SourceDisposition,
)
from scrubexif.scrub import ScrubResult, ScrubSummary


@pytest.mark.parametrize(
    ("kind", "reason_code", "error_message", "duplicate_path"),
    [
        (OutcomeKind.SCRUBBED, "output_published", None, None),
        (OutcomeKind.PLANNED, "scrub_planned", None, None),
        (
            OutcomeKind.DUPLICATE_HANDLED,
            "duplicate_moved",
            None,
            Path("/photos/errors/photo.jpg"),
        ),
        (OutcomeKind.FAILED, "scrub_failed", "jpegtran failed", None),
    ],
)
def test_scrub_result_valid_typed_outcome_is_accepted(
    kind: OutcomeKind,
    reason_code: str,
    error_message: str | None,
    duplicate_path: Path | None,
) -> None:
    """Valid typed outcomes retain their machine-readable reason and facts.

    Args:
        kind: Typed outcome under test.
        reason_code: Stable reason paired with the outcome.
        error_message: Failure diagnostic, when required.
        duplicate_path: Archive path for the moved-duplicate case.

    Returns:
        None.

    Raises:
        AssertionError: If construction loses or changes a supplied field.
    """
    result = ScrubResult(
        input_path=Path("/photos/input/photo.jpg"),
        output_path=Path("/photos/output/photo.jpg"),
        kind=kind,
        reason_code=reason_code,
        error_message=error_message,
        duplicate_path=duplicate_path,
    )

    assert result.kind is kind
    assert result.reason_code == reason_code
    assert result.error_message == error_message
    assert result.duplicate_path == duplicate_path


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"kind": "scrubbed"}, "kind must be an OutcomeKind"),
        ({"reason_code": "Bad-Reason"}, "reason_code must use lowercase"),
        ({"reason_code": "unknown_reason"}, "unsupported scrub result reason"),
        (
            {"kind": OutcomeKind.FAILED, "error_message": "failure"},
            "reason output_published requires outcome scrubbed",
        ),
        (
            {
                "kind": OutcomeKind.FAILED,
                "reason_code": "scrub_failed",
                "error_message": None,
            },
            "requires an error message",
        ),
        ({"error_message": "unexpected"}, "only a FAILED"),
        ({"output_path": None}, "requires output_path"),
        (
            {
                "kind": OutcomeKind.DUPLICATE_HANDLED,
                "reason_code": "duplicate_moved",
            },
            "duplicate_moved requires duplicate_path",
        ),
        (
            {"duplicate_path": Path("/photos/errors/photo.jpg")},
            "duplicate_path requires a moved duplicate or collision",
        ),
        ({"input_path": "/photos/input/photo.jpg"}, "input_path must be"),
    ],
)
def test_scrub_result_invalid_field_combination_raises_value_error(
    overrides: dict[str, object],
    expected_message: str,
) -> None:
    """Invalid types, reasons, paths, and combinations fail at construction.

    Args:
        overrides: Field values replacing the valid baseline.
        expected_message: Diagnostic fragment expected from validation.

    Returns:
        None.

    Raises:
        AssertionError: If invalid input is accepted or misdiagnosed.
    """
    arguments: dict[str, object] = {
        "input_path": Path("/photos/input/photo.jpg"),
        "output_path": Path("/photos/output/photo.jpg"),
        "kind": OutcomeKind.SCRUBBED,
        "reason_code": "output_published",
        "error_message": None,
        "duplicate_path": None,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=expected_message):
        ScrubResult(**arguments)  # type: ignore[arg-type]


def test_scrub_result_mutation_raises_frozen_instance_error() -> None:
    """A completed operation result cannot be mutated after validation.

    Returns:
        None.

    Raises:
        AssertionError: If mutation is permitted.
    """
    result = ScrubResult(
        input_path=Path("/photos/input/photo.jpg"),
        output_path=Path("/photos/output/photo.jpg"),
        kind=OutcomeKind.SCRUBBED,
        reason_code="output_published",
    )

    with pytest.raises(FrozenInstanceError):
        result.reason_code = "scrub_planned"  # type: ignore[misc]


def test_scrub_summary_invalid_result_raises_without_incrementing_total() -> None:
    """The transitional summary rejects unknown result types atomically.

    Returns:
        None.

    Raises:
        AssertionError: If validation mutates the summary or accepts bad input.
    """
    summary = ScrubSummary()

    with pytest.raises(ValueError, match="result must be a ScrubResult"):
        summary.update("scrubbed")  # type: ignore[arg-type]

    assert summary.total == 0


def test_scrub_summary_records_scrub_result_in_typed_report() -> None:
    """A compatibility update records one reconciled terminal outcome.

    Returns:
        None.

    Raises:
        AssertionError: If the typed report and legacy view disagree.
    """
    summary = ScrubSummary()
    summary.update(
        ScrubResult(
            input_path=Path("/photos/input/photo.jpg"),
            output_path=Path("/photos/output/photo.jpg"),
            kind=OutcomeKind.SCRUBBED,
            reason_code="output_published",
        )
    )

    summary.report.validate()

    assert summary.report.scrubbed == 1
    assert summary.report.published == 1
    assert summary.report.examined == 1
    assert summary.total == 1
    assert summary.scrubbed == 1


def test_scrub_result_failed_duplicate_preserves_verification_facts() -> None:
    """A rejected verified duplicate remains failed and duplicate-verified.

    Returns:
        None.
    """
    output = Path("/photos/output/photo.jpg")
    outcome = scrub._scrub_result_to_file_outcome(
        ScrubResult(
            input_path=Path("/photos/input/photo.jpg"),
            output_path=output,
            kind=OutcomeKind.FAILED,
            reason_code="duplicate_policy_rejected",
            error_message="strict duplicate policy rejected the source",
        )
    )

    assert outcome.kind is OutcomeKind.FAILED
    assert outcome.phase is ProcessingPhase.DUPLICATE_HANDLING
    assert outcome.duplicate_verified is True
    assert outcome.duplicate_disposition is DuplicateDisposition.UNCHANGED
    assert outcome.actual_output_path == output
    assert outcome.published is False


def test_scrub_result_collision_is_failed_without_duplicate_fact() -> None:
    """Different content at an existing destination is not a duplicate.

    Returns:
        None.
    """
    outcome = scrub._scrub_result_to_file_outcome(
        ScrubResult(
            input_path=Path("/photos/input/photo.jpg"),
            output_path=Path("/photos/output/photo.jpg"),
            kind=OutcomeKind.FAILED,
            reason_code="destination_collision",
            error_message="same filename contains different bytes",
        )
    )

    assert outcome.kind is OutcomeKind.FAILED
    assert outcome.phase is ProcessingPhase.VERIFICATION
    assert outcome.duplicate_verified is False
    assert outcome.actual_output_path is None
    assert outcome.source_disposition is SourceDisposition.UNCHANGED
    assert outcome.published is False


def test_scrub_result_post_publication_failure_preserves_publication_fact() -> None:
    """Required source-cleanup failure remains FAILED after publication.

    Returns:
        None.
    """
    output = Path("/photos/output/photo.jpg")
    outcome = scrub._scrub_result_to_file_outcome(
        ScrubResult(
            input_path=Path("/photos/input/photo.jpg"),
            output_path=output,
            kind=OutcomeKind.FAILED,
            reason_code="source_deletion_failed",
            error_message="source unlink failed",
        )
    )

    assert outcome.kind is OutcomeKind.FAILED
    assert outcome.phase is ProcessingPhase.SOURCE_FINALIZATION
    assert outcome.published is True
    assert outcome.intended_output_path == output
    assert outcome.actual_output_path == output
    assert outcome.source_disposition is SourceDisposition.UNCHANGED
    assert outcome.duplicate_verified is False


def test_scrub_result_late_rename_collision_records_publication_phase() -> None:
    """A collision after scrub and audit remains a publication failure.

    Returns:
        None.
    """
    outcome = scrub._scrub_result_to_file_outcome(
        ScrubResult(
            input_path=Path("/photos/input/photo.jpg"),
            output_path=Path("/photos/output/generated.jpg"),
            kind=OutcomeKind.FAILED,
            reason_code="rename_publication_conflict",
            error_message="late destination allocation was exhausted",
        )
    )

    assert outcome.kind is OutcomeKind.FAILED
    assert outcome.phase is ProcessingPhase.PUBLICATION
    assert outcome.published is False
    assert outcome.actual_output_path is None


@pytest.mark.parametrize(
    "counter_name",
    [
        "total",
        "scrubbed",
        "skipped",
        "duplicates_deleted",
        "duplicates_moved",
        "errors",
        "unsupported",
    ],
)
def test_scrub_summary_counter_assignment_raises_attribute_error(
    counter_name: str,
) -> None:
    """Compatibility counters cannot bypass RunReport.record().

    Args:
        counter_name: Read-only compatibility property to test.

    Returns:
        None.

    Raises:
        AssertionError: If direct counter assignment is accepted.
    """
    summary = ScrubSummary()

    with pytest.raises(AttributeError):
        setattr(summary, counter_name, 1)

    assert summary.report.examined == 0


def test_scrub_summary_properties_use_schema_two_meanings() -> None:
    """Expose planned, previewed, and published-failure schema-2 meanings.

    Returns:
        None.

    Raises:
        AssertionError: If the summary properties retain schema-1 projections.
    """
    summary = ScrubSummary()
    summary.record(
        FileOutcome(
            input_path=Path("/photos/input/planned.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.PLANNED,
            reason_code="dry_run_planned",
            phase=ProcessingPhase.PLANNING,
            intended_output_path=Path("/photos/output/planned.jpg"),
        )
    )
    summary.record(
        FileOutcome(
            input_path=Path("/photos/input/previewed.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.PREVIEWED,
            reason_code="preview_audited",
            phase=ProcessingPhase.VERIFICATION,
        )
    )
    summary.record(
        FileOutcome(
            input_path=Path("/photos/input/archive-failed.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.FAILED,
            reason_code="source_archive_failed",
            phase=ProcessingPhase.SOURCE_FINALIZATION,
            intended_output_path=Path("/photos/output/archive-failed.jpg"),
            actual_output_path=Path("/photos/output/archive-failed.jpg"),
            published=True,
            source_disposition=SourceDisposition.UNCHANGED,
            diagnostic="source archival failed",
        )
    )

    summary.report.validate()

    assert summary.report.planned == 1
    assert summary.report.previewed == 1
    assert summary.report.failed == 1
    assert summary.report.published == 1
    assert summary.total == 3
    assert summary.scrubbed == 0
    assert summary.errors == 1
