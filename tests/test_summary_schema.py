# SPDX-License-Identifier: GPL-3.0-or-later
"""Schema-2 machine summary contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from scrubexif.outcomes import (
    FileOutcome,
    InputClassification,
    OutcomeKind,
    ProcessingPhase,
    RunFailure,
    SourceDisposition,
)
from scrubexif.scrub import ScrubSummary, _format_machine_summary


EXPECTED_FIELD_ORDER = [
    "schema",
    "total",
    "jpeg_total",
    "scrubbed",
    "duplicates_handled",
    "unsupported",
    "planned",
    "previewed",
    "skipped",
    "failed",
    "errors",
    "verified_duplicates",
    "duplicates_deleted",
    "duplicates_moved",
    "duplicates_unchanged",
    "duplicates_missing",
    "published",
    "run_errors",
    "interrupted",
    "examined",
    "duration",
]


def _machine_fields(output: str) -> tuple[list[str], dict[str, str]]:
    """Parse the sole machine summary from captured output.

    Args:
        output: Captured command output containing one machine summary.

    Returns:
        Ordered field names and their string values.

    Raises:
        AssertionError: If output does not contain exactly one summary.
    """
    lines = [
        line for line in output.splitlines() if line.startswith("SCRUBEXIF_SUMMARY ")
    ]
    assert len(lines) == 1
    parts = lines[0].removeprefix("SCRUBEXIF_SUMMARY ").split()
    names = [part.split("=", 1)[0] for part in parts]
    return names, dict(part.split("=", 1) for part in parts)


def test_scrub_summary_schema_two_reports_revised_outcome_meanings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Emit ordered schema-2 counters for orthogonal terminal facts.

    Args:
        capsys: Pytest capture fixture for real summary output.

    Returns:
        None.
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
            input_path=Path("/photos/input/published-failure.jpg"),
            input_classification=InputClassification.ELIGIBLE_JPEG,
            kind=OutcomeKind.FAILED,
            reason_code="source_archive_failed",
            phase=ProcessingPhase.SOURCE_FINALIZATION,
            intended_output_path=Path("/photos/output/published-failure.jpg"),
            actual_output_path=Path("/photos/output/published-failure.jpg"),
            published=True,
            source_disposition=SourceDisposition.UNCHANGED,
            diagnostic="source archival failed",
        )
    )
    summary.report.record_run_failure(
        RunFailure(
            reason_code="run_interrupted",
            phase=ProcessingPhase.SCRUB,
            diagnostic="received SIGTERM",
            interrupted=True,
        )
    )

    summary.print()

    names, fields = _machine_fields(capsys.readouterr().out)
    assert names == EXPECTED_FIELD_ORDER
    assert fields == {
        "schema": "2",
        "total": "3",
        "jpeg_total": "3",
        "scrubbed": "0",
        "duplicates_handled": "0",
        "unsupported": "0",
        "planned": "1",
        "previewed": "1",
        "skipped": "0",
        "failed": "1",
        "errors": "2",
        "verified_duplicates": "0",
        "duplicates_deleted": "0",
        "duplicates_moved": "0",
        "duplicates_unchanged": "0",
        "duplicates_missing": "0",
        "published": "1",
        "run_errors": "1",
        "interrupted": "1",
        "examined": "3",
        "duration": fields["duration"],
    }
    assert float(fields["duration"]) >= 0.0


@pytest.mark.parametrize("duration", [-0.1, float("inf"), float("nan"), True])
def test_format_machine_summary_invalid_duration_raises(duration: float) -> None:
    """Reject duration values that cannot satisfy the wire contract.

    Args:
        duration: Invalid elapsed-time value under test.

    Returns:
        None.

    Raises:
        AssertionError: If an invalid duration is accepted.
    """
    summary = ScrubSummary()

    with pytest.raises(ValueError, match="summary duration"):
        _format_machine_summary(summary.report.counts(), duration)


def test_format_machine_summary_missing_counter_raises() -> None:
    """Reject an incomplete counter snapshot instead of emitting partial data.

    Returns:
        None.

    Raises:
        AssertionError: If an incomplete snapshot is accepted.
    """
    summary = ScrubSummary()
    counts = summary.report.counts()
    del counts["published"]

    with pytest.raises(ValueError, match=r"missing=\['published'\]"):
        _format_machine_summary(counts, 0.0)
