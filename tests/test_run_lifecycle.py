# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the centralized operational runner and final-report boundary."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import pytest

from scrubexif import scrub
from scrubexif.outcomes import OutcomeKind


def test_emit_final_summary_valid_report_emits_one_normal_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A valid report emits exactly one normal machine-readable summary.

    Args:
        capsys: Pytest output capture fixture.

    Returns:
        None.
    """
    emitted = scrub._emit_final_summary(scrub.ScrubSummary())

    captured = capsys.readouterr()
    assert emitted is True
    assert captured.out.count("SCRUBEXIF_SUMMARY ") == 1
    assert "SCRUBEXIF_SUMMARY_ERROR" not in captured.out
    assert captured.err == ""


def test_emit_final_summary_invalid_report_emits_only_error_marker(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An inconsistent report cannot emit contradictory normal counters.

    Args:
        capsys: Pytest output capture fixture.

    Returns:
        None.
    """
    summary = scrub.ScrubSummary()
    # Deliberate private-state corruption exercises the defensive final gate.
    report = summary.report
    report._terminal_counts[OutcomeKind.FAILED] = -1  # type: ignore[attr-defined]

    emitted = scrub._emit_final_summary(summary)

    captured = capsys.readouterr()
    assert emitted is False
    assert captured.out == (
        "SCRUBEXIF_SUMMARY_ERROR reason=summary_validation_failed\n"
    )
    assert "SCRUBEXIF_SUMMARY " not in captured.out
    assert "Summary validation failed" in captured.err


def test_controlled_preflight_failure_returns_one_and_emits_one_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A controlled operational rejection is reported once and returns one.

    Args:
        capsys: Pytest output capture fixture.

    Returns:
        None.
    """
    exit_code = scrub.main(["--preview", "--from-input"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "--preview cannot be used with --from-input auto mode" in captured.err
    assert captured.out.count("SCRUBEXIF_SUMMARY ") == 1
    assert "errors=1" in captured.out
    assert "examined=0" in captured.out


@pytest.mark.parametrize(
    ("arguments", "expected_status"),
    [
        (["--help"], 0),
        (["--max-files", "-1"], 2),
    ],
)
def test_parser_exit_emits_no_summary(
    arguments: list[str],
    expected_status: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Help and parser rejection finish before report initialization.

    Args:
        arguments: Command-line arguments that cause argparse to exit.
        expected_status: Expected argparse exit status.
        capsys: Pytest output capture fixture.

    Returns:
        None.
    """
    with pytest.raises(SystemExit) as exc_info:
        scrub.main(arguments)

    captured = capsys.readouterr()
    assert exc_info.value.code == expected_status
    assert "SCRUBEXIF_SUMMARY" not in captured.out
    assert "SCRUBEXIF_SUMMARY" not in captured.err


def test_keyboard_interrupt_returns_130_and_emits_one_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A handled cooperative interruption produces status 130 and one report.

    Args:
        monkeypatch: Pytest fixture used to inject an otherwise timing-dependent
            operating-system interruption.
        capsys: Pytest output capture fixture.

    Returns:
        None.
    """
    # Delivering SIGINT at a deterministic runner boundary is not reliable in a
    # unit test, so inject the equivalent KeyboardInterrupt at that boundary.
    def interrupt_run(
        args: argparse.Namespace,
        summary: scrub.ScrubSummary,
    ) -> NoReturn:
        """Raise the cooperative interruption handled by the outer runner.

        Args:
            args: Parsed arguments supplied by the runner.
            summary: Fresh per-invocation summary supplied by the runner.

        Raises:
            KeyboardInterrupt: Always, to simulate SIGINT delivery.
        """
        del args, summary
        raise KeyboardInterrupt

    monkeypatch.setattr(scrub, "_run_inner", interrupt_run)
    args = argparse.Namespace(version=False, quiet=False)

    exit_code = scrub._run(args)

    captured = capsys.readouterr()
    assert exit_code == 130
    assert "Run interrupted by the user" in captured.err
    assert captured.out.count("SCRUBEXIF_SUMMARY ") == 1
    assert "errors=1" in captured.out


def test_module_entrypoint_exits_with_returned_operational_status() -> None:
    """The executable boundary converts the runner's returned status to exit 1.

    Returns:
        None.
    """
    environment = os.environ.copy()
    environment["ALLOW_ROOT"] = "1"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scrubexif.scrub",
            "--state-file",
            "disabled",
            "--preview",
            "--from-input",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "--preview cannot be used with --from-input auto mode" in result.stderr
    assert result.stdout.count("SCRUBEXIF_SUMMARY ") == 1

