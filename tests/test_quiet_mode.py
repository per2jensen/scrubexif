# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

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
