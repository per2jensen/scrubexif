# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused unit tests for scrub_file behavior without requiring Docker."""

from pathlib import Path

from scrubexif import scrub


def test_scrub_file_passes_full_output_path(tmp_path, monkeypatch):
    """Ensure exiftool gets the resolved output filename when writing to a directory."""
    input_file = tmp_path / "sample.jpg"
    input_file.write_bytes(b"jpeg-data")
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    commands: list[list[str]] = []

    def fake_run(cmd, *_, **__):
        commands.append(cmd)

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    result = scrub.scrub_file(input_file, output_path=output_dir)

    assert result.output_path == output_dir / input_file.name
    assert commands, "Expected exiftool command to be invoked"
    cmd = commands[0]
    assert "-o" in cmd, "Expected exiftool to receive an output argument"
    target = Path(cmd[cmd.index("-o") + 1])
    assert target.parent == output_dir
    assert target != result.output_path


def test_duplicate_reporting_uses_output_file(tmp_path):
    """Dry-run duplicate detection should report the concrete output filename."""
    input_file = tmp_path / "photo.jpg"
    input_file.write_bytes(b"jpeg-data")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / input_file.name).write_bytes(b"existing")

    result = scrub.scrub_file(input_file, output_path=output_dir, dry_run=True)

    assert result.status == "duplicate"
    assert result.output_path == output_dir / input_file.name


def test_scrub_file_failure_does_not_create_output(tmp_path, monkeypatch):
    input_file = tmp_path / "bad.jpg"
    input_file.write_bytes(b"jpeg-data")
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    def fake_run(cmd, *_, **__):
        if "-o" in cmd:
            target = Path(cmd[cmd.index("-o") + 1])
            target.write_bytes(b"partial")

        class Proc:
            returncode = 1
            stdout = ""
            stderr = "exiftool failed"

        return Proc()

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    result = scrub.scrub_file(input_file, output_path=output_dir)

    assert result.status == "error"
    assert not (output_dir / input_file.name).exists()
    assert not any(p.name.startswith(".scrubexif_tmp_") for p in output_dir.iterdir())


def test_scrub_file_exception_does_not_create_output(tmp_path, monkeypatch):
    input_file = tmp_path / "error.jpg"
    input_file.write_bytes(b"jpeg-data")
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    def fake_run(cmd, *_, **__):
        if "-o" in cmd:
            target = Path(cmd[cmd.index("-o") + 1])
            target.write_bytes(b"partial")
        raise FileNotFoundError("exiftool missing")

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    result = scrub.scrub_file(input_file, output_path=output_dir)

    assert result.status == "error"
    assert not (output_dir / input_file.name).exists()
    assert not any(p.name.startswith(".scrubexif_tmp_") for p in output_dir.iterdir())


def test_in_place_failure_keeps_original(tmp_path, monkeypatch):
    input_file = tmp_path / "inplace.jpg"
    original = b"original"
    input_file.write_bytes(original)

    def fake_run(cmd, *_, **__):
        if "-o" in cmd:
            target = Path(cmd[cmd.index("-o") + 1])
            target.write_bytes(b"partial")

        class Proc:
            returncode = 1
            stdout = ""
            stderr = "exiftool failed"

        return Proc()

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    result = scrub.scrub_file(input_file, output_path=None)

    assert result.status == "error"
    assert input_file.read_bytes() == original
    assert not any(p.name.startswith(".scrubexif_tmp_") for p in tmp_path.iterdir())
