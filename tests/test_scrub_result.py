# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the typed operation-level scrub result."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from scrubexif.outcomes import OutcomeKind
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
