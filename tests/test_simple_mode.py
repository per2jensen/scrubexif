# tests/test_simple_mode.py
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests for the new `--simple` mode.

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
          output/   (created automatically by simple mode)
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
        on_duplicate="delete",
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
    Ensure that `--simple` never modifies or deletes the original files:
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
        # Simulate exiftool success without touching any files
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
        on_duplicate="delete",
    )

    # Sanity: the run actually processed the files
    assert summary.scrubbed == len(names)
    assert summary.errors == 0

    # Originals must still exist and be byte-for-byte identical
    for path, expected_bytes in original_bytes.items():
        assert path.exists(), f"Original file missing after simple mode: {path}"
        assert path.read_bytes() == expected_bytes, f"Original file modified: {path}"

