# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import pytest

from scrubexif import scrub

from .conftest import SAMPLE_BYTES


def test_quiet_suppresses_success_output(tmp_path, monkeypatch, capsys):
    root = tmp_path / "photos"
    root.mkdir()
    (root / "one.jpg").write_bytes(SAMPLE_BYTES)

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", root)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", root / "output")
    monkeypatch.setattr(scrub, "INPUT_DIR", root / "input")
    monkeypatch.setattr(scrub, "PROCESSED_DIR", root / "processed")
    monkeypatch.setattr(scrub, "ERRORS_DIR", root / "errors")

    exit_code = scrub.main(["--dry-run", "-q"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert captured.err == ""


def test_quiet_second_run_emits_errors_to_stderr(tmp_path, monkeypatch, capsys):
    root = tmp_path / "photos"
    root.mkdir()
    (root / "one.jpg").write_bytes(SAMPLE_BYTES)

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", root)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", root / "output")
    monkeypatch.setattr(scrub, "INPUT_DIR", root / "input")
    monkeypatch.setattr(scrub, "PROCESSED_DIR", root / "processed")
    monkeypatch.setattr(scrub, "ERRORS_DIR", root / "errors")

    exit_code = scrub.main(["--dry-run", "-q"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert captured.err == ""

    exit_code = scrub.main(["--dry-run", "-q"])
    captured = capsys.readouterr()
    assert exit_code != 0
    assert captured.out == ""
    assert "Output directory already exists" in captured.err


def test_quiet_scrub_failure_returns_nonzero_and_replays_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Quiet mode hides success only; scrub failures remain visible on stderr."""
    root = tmp_path / "photos"
    root.mkdir()
    corrupt = root / "corrupt.jpg"
    corrupt.write_bytes(b"not-a-jpeg")
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", root)

    exit_code = scrub.main(["--clean-inline", "-q", str(corrupt)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Failed to scrub" in captured.err
    assert "Errors" in captured.err
    assert corrupt.read_bytes() == b"not-a-jpeg"


def test_preview_scrub_failure_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed disposable preview is a command failure, not a successful preview."""
    root = tmp_path / "photos"
    root.mkdir()
    corrupt = root / "corrupt.jpg"
    corrupt.write_bytes(b"not-a-jpeg")
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", root)

    exit_code = scrub.main(["--clean-inline", "--preview", str(corrupt)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Preview scrub failed" in captured.out
    assert "Preview complete" not in captured.out
    assert corrupt.read_bytes() == b"not-a-jpeg"
