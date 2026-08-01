# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI regression tests for version and license reporting."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scrubexif import scrub
from scrubexif.__about__ import __license__, __version__


EXPECTED_OUTPUT = f"scrubexif {__version__}\n{__license__}\n"


def test_version_prints_exact_about_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The in-process CLI prints only the values sourced from __about__.py."""
    exit_code = scrub.main(["--version"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == EXPECTED_OUTPUT
    assert captured.err == ""


def test_version_is_not_suppressed_by_quiet_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The explicit metadata request takes precedence over quiet mode."""
    exit_code = scrub.main(["--quiet", "--version"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == EXPECTED_OUTPUT
    assert captured.err == ""


def test_version_ignores_unusable_runtime_state_path(tmp_path: Path) -> None:
    """Version reporting performs no runtime state initialization or validation."""
    environment = os.environ.copy()
    environment["SCRUBEXIF_STATE"] = str(tmp_path / "missing" / "state.json")

    result = subprocess.run(
        [sys.executable, "-m", "scrubexif.scrub", "--version"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == EXPECTED_OUTPUT
    assert result.stderr == ""
