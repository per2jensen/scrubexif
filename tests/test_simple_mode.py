# tests/test_simple_mode.py
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests for the default safe mode.

Covers:
  - Output directory is automatically created
  - .jpg, .jpeg, .JPG and .JPEG files are all processed
  - Existing files are not modified in any way
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from scrubexif import scrub


def _setup_simple_env(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """
    Create a fake /photos tree under tmp_path and point scrub.py globals at it.

    Layout:
      tmp_path/
        photos/
          (JPEGs will be placed directly here)
          output/   (created automatically by default mode)
    """
    root = tmp_path / "photos"
    root.mkdir()

    output = root / "output"

    # Point scrub globals at our temp tree
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", root)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", output)
    monkeypatch.setattr(scrub, "INPUT_DIR", root / "input")
    monkeypatch.setattr(scrub, "PROCESSED_DIR", root / "processed")
    monkeypatch.setattr(scrub, "ERRORS_DIR", root / "errors")

    return root, output


def test_simple_mode_creates_output_and_processes_all_jpeg_extensions(tmp_path, monkeypatch):
    """
    Ensure:
      - /photos/output is created automatically
      - .jpg, .jpeg, .JPG, .JPEG are all processed
    """
    photos_root, output_dir = _setup_simple_env(tmp_path, monkeypatch)

    # Start with no output directory
    assert not output_dir.exists()

    # Create four files with different JPEG extensions
    names = ["one.jpg", "two.jpeg", "three.JPG", "four.JPEG"]
    for name in names:
        (photos_root / name).write_bytes(f"data-{name}".encode("utf-8"))

    # Stub exiftool calls so we don't depend on the binary in unit tests
    commands: List[list[str]] = []

    def fake_run(cmd, capture_output=False, text=False, encoding=None, errors=None):
        commands.append(cmd)
        if "-outfile" in cmd:
            Path(cmd[cmd.index("-outfile") + 1]).write_bytes(b"scrubbed")
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    summary = scrub.ScrubSummary()
    scrub.simple_scrub(
        summary=summary,
        recursive=False,
        dry_run=False,
        show_tags_mode=None,
        paranoia=True,
        max_files=None,
    )

    # Output directory must have been created
    assert output_dir.exists()
    assert output_dir.is_dir()

    # All four JPEGs must have been processed exactly once
    assert summary.total == len(names)
    assert summary.scrubbed == len(names)
    assert summary.errors == 0

    processed = sorted(Path(cmd[-1]).name for cmd in commands)
    assert processed == sorted(names)


def test_simple_mode_does_not_modify_original_files(tmp_path, monkeypatch):
    """
    Ensure that default mode never modifies or deletes the original files:
    - All originals still exist after the run
    - Their byte content is unchanged
    """
    photos_root, output_dir = _setup_simple_env(tmp_path, monkeypatch)

    names = ["keep1.jpg", "keep2.JPEG"]
    original_bytes: dict[Path, bytes] = {}

    for name in names:
        p = photos_root / name
        data = f"original-{name}".encode("utf-8")
        p.write_bytes(data)
        original_bytes[p] = data

    def fake_run(cmd, capture_output=False, text=False, encoding=None, errors=None):
        if "-outfile" in cmd:
            Path(cmd[cmd.index("-outfile") + 1]).write_bytes(b"scrubbed")
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    summary = scrub.ScrubSummary()
    scrub.simple_scrub(
        summary=summary,
        recursive=False,
        dry_run=False,
        show_tags_mode=None,
        paranoia=True,
        max_files=None,
    )

    # Sanity: the run actually processed the files
    assert summary.scrubbed == len(names)
    assert summary.errors == 0

    # Originals must still exist and be byte-for-byte identical
    for path, expected_bytes in original_bytes.items():
        assert path.exists(), f"Original file missing after default mode: {path}"
        assert path.read_bytes() == expected_bytes, f"Original file modified: {path}"


def test_default_mode_warns_and_exits_when_output_exists(tmp_path, monkeypatch, capsys):
    photos_root, output_dir = _setup_simple_env(tmp_path, monkeypatch)
    output_dir.mkdir(parents=True)

    summary = scrub.ScrubSummary()
    with pytest.raises(SystemExit) as excinfo:
        scrub.simple_scrub(
            summary=summary,
            recursive=False,
            dry_run=False,
            show_tags_mode=None,
            paranoia=True,
            max_files=None,
        )

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Output directory already exists" in captured.out


def test_default_mode_refuses_preexisting_output_when_not_explicit(tmp_path, monkeypatch, capsys):
    """output_explicit=False (default): pre-existing output dir must be refused."""
    photos_root, output_dir = _setup_simple_env(tmp_path, monkeypatch)
    output_dir.mkdir(parents=True)

    summary = scrub.ScrubSummary()
    with pytest.raises(SystemExit) as excinfo:
        scrub.simple_scrub(summary=summary, output_explicit=False)

    assert excinfo.value.code == 1
    assert "Output directory already exists" in capsys.readouterr().out


def test_explicit_output_accepts_preexisting_directory(tmp_path, monkeypatch):
    """output_explicit=True: pre-existing output dir must be accepted (e.g. bind-mount use case)."""
    photos_root, output_dir = _setup_simple_env(tmp_path, monkeypatch)
    output_dir.mkdir(parents=True)
    (photos_root / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    def fake_run(cmd, capture_output=False, text=False, encoding=None, errors=None):
        if "-outfile" in cmd:
            Path(cmd[cmd.index("-outfile") + 1]).write_bytes(b"scrubbed")
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    summary = scrub.ScrubSummary()
    scrub.simple_scrub(summary=summary, output_explicit=True)

    assert summary.scrubbed == 1
    assert (output_dir / "photo.jpg").read_bytes() == b"scrubbed"


def test_simple_mode_allows_custom_output_dir(tmp_path, monkeypatch):
    photos_root, _ = _setup_simple_env(tmp_path, monkeypatch)

    custom_output = scrub.resolve_output_dir(Path("scrubbed"))
    monkeypatch.setattr(scrub, "OUTPUT_DIR", custom_output)

    # Create a JPEG in the photos root
    (photos_root / "one.jpg").write_bytes(b"jpeg")

    def fake_run(cmd, capture_output=False, text=False, encoding=None, errors=None):
        if "-outfile" in cmd:
            Path(cmd[cmd.index("-outfile") + 1]).write_bytes(b"scrubbed")
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    summary = scrub.ScrubSummary()
    scrub.simple_scrub(
        summary=summary,
        recursive=False,
        dry_run=False,
        show_tags_mode=None,
        paranoia=True,
        max_files=None,
    )

    assert custom_output.is_dir()
    assert summary.scrubbed == 1


def test_simple_scrub_second_run_skips_and_preserves_originals(tmp_path, monkeypatch):
    """On a second run into the same output directory, simple_scrub must skip files
    whose output already exists and leave originals byte-for-byte intact."""
    photos_root, output_dir = _setup_simple_env(tmp_path, monkeypatch)
    output_dir.mkdir(parents=True)

    original_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    photo = photos_root / "photo.jpg"
    photo.write_bytes(original_bytes)
    # Simulate a previous run: output already exists
    (output_dir / photo.name).write_bytes(b"previously-scrubbed")

    scrub_called = False

    def fake_run(*_a, **_kw):
        nonlocal scrub_called
        scrub_called = True

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    summary = scrub.ScrubSummary()
    scrub.simple_scrub(summary=summary, output_explicit=True)

    assert not scrub_called, "Subprocess (jpegtran/exiftool) must not run when output already exists"
    assert photo.read_bytes() == original_bytes, "Original must be byte-identical after skipping"
    assert (output_dir / photo.name).read_bytes() == b"previously-scrubbed", \
        "Existing output must not be overwritten"
    assert summary.scrubbed == 0
    assert summary.skipped == 1


def test_simple_scrub_on_duplicate_delete_ignored_originals_safe(tmp_path, monkeypatch, capsys):
    """Even if --on-duplicate delete is passed at CLI level, simple_scrub must
    never delete originals — the on_duplicate parameter is not forwarded to simple_scrub."""
    import sys
    dirs = {
        "root": tmp_path / "photos",
        "output": tmp_path / "photos" / "output",
    }
    dirs["root"].mkdir()

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", dirs["root"])
    monkeypatch.setattr(scrub, "OUTPUT_DIR", dirs["output"])
    monkeypatch.setattr(scrub, "INPUT_DIR", dirs["root"] / "input")
    monkeypatch.setattr(scrub, "PROCESSED_DIR", dirs["root"] / "processed")
    monkeypatch.setattr(scrub, "ERRORS_DIR", dirs["root"] / "errors")
    monkeypatch.setattr(scrub, "STATE_FILE", None, raising=False)
    monkeypatch.setattr(scrub, "_resolve_state_path_from_env", lambda: None)

    original_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    photo = dirs["root"] / "photo.jpg"
    photo.write_bytes(original_bytes)

    simple_calls: list[dict] = []

    def fake_simple_scrub(summary, **kwargs):
        # Verify on_duplicate is NOT in kwargs (was not forwarded)
        simple_calls.append(kwargs)
        return summary

    monkeypatch.setattr(scrub, "simple_scrub", fake_simple_scrub)
    monkeypatch.setattr(sys, "argv", ["scrub", "--on-duplicate", "delete"])

    scrub.main()

    assert len(simple_calls) == 1
    assert "on_duplicate" not in simple_calls[0], \
        "--on-duplicate must not be forwarded to simple_scrub"
    assert photo.read_bytes() == original_bytes, "Original must be untouched"
