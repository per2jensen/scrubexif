# tests/test_duplicate_handling.py
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Duplicate-handling integration tests for scrubexif in auto mode.

Covers:
  - Unique files are scrubbed and originals moved to /processed
  - Re-uploaded duplicate moved to /errors with --on-duplicate move
  - Re-uploaded duplicate deleted with default (delete)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests._docker import mk_mounts, run_container
from .conftest import create_fake_jpeg  # helper provided by the suite


def prepare_common_dirs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    errors_dir = tmp_path / "errors"
    input_dir.mkdir()
    output_dir.mkdir()
    processed_dir.mkdir()
    errors_dir.mkdir()
    return input_dir, output_dir, processed_dir, errors_dir


def mounts_with_errors(input_dir: Path, output_dir: Path, processed_dir: Path, errors_dir: Path) -> list[str]:
    mounts = mk_mounts(input_dir, output_dir, processed_dir)
    mounts += ["-v", f"{errors_dir}:/photos/errors"]
    return mounts


def test_scrub_unique_files(tmp_path: Path):
    input_dir, output_dir, processed_dir, errors_dir = prepare_common_dirs(tmp_path)
    create_fake_jpeg(input_dir / "photo1.jpg", "red")
    create_fake_jpeg(input_dir / "photo2.jpg", "yellow")

    cp = run_container(
        mounts=mounts_with_errors(input_dir, output_dir, processed_dir, errors_dir),
        args=["--from-input", "--log-level", "debug"],
        capture_output=True,
    )
    print(cp.stdout)
    print(cp.stderr)
    assert cp.returncode == 0
    assert "Successfully scrubbed" in cp.stdout

    # Originals should be moved to processed/
    assert (processed_dir / "photo1.jpg").exists()
    assert (processed_dir / "photo2.jpg").exists()
    # Scrubbed outputs should exist
    assert (output_dir / "photo1.jpg").exists()
    assert (output_dir / "photo2.jpg").exists()


def test_scrub_and_move_duplicate(tmp_path: Path):
    input_dir, output_dir, processed_dir, errors_dir = prepare_common_dirs(tmp_path)

    # First pass processes the file
    create_fake_jpeg(input_dir / "photo.jpg", "red")
    first = run_container(
        mounts=mounts_with_errors(input_dir, output_dir, processed_dir, errors_dir),
        args=["--from-input", "--log-level", "debug"],
        capture_output=True,
    )
    print(first.stdout)
    print(first.stderr)
    assert first.returncode == 0
    assert (output_dir / "photo.jpg").exists()
    assert (processed_dir / "photo.jpg").exists()

    # Second pass re-uploads the same name — should be treated as duplicate
    create_fake_jpeg(input_dir / "photo.jpg", "red")
    second = run_container(
        mounts=mounts_with_errors(input_dir, output_dir, processed_dir, errors_dir),
        args=["--from-input", "--on-duplicate", "move", "--log-level", "debug"],
        capture_output=True,
    )
    print(second.stdout)
    print(second.stderr)
    assert second.returncode == 0
    assert "Moved duplicate to" in second.stdout or "📦 Moved duplicate to" in second.stdout

    # Duplicate should be moved to errors/
    # Allow for collision suffixes (_1, _2, ...) created by the implementation
    moved_candidates = list(errors_dir.glob("photo*.jpg"))
    assert moved_candidates, "Expected duplicate moved into /errors"


def test_scrub_and_delete_duplicate(tmp_path: Path):
    input_dir, output_dir, processed_dir, errors_dir = prepare_common_dirs(tmp_path)

    # First pass processes the file
    create_fake_jpeg(input_dir / "photo.jpg", "black")
    first = run_container(
        mounts=mounts_with_errors(input_dir, output_dir, processed_dir, errors_dir),
        args=["--from-input", "--log-level", "debug"],
        capture_output=True,
    )
    print(first.stdout)
    print(first.stderr)
    assert first.returncode == 0
    assert (output_dir / "photo.jpg").exists()
    assert (processed_dir / "photo.jpg").exists()

    # Second pass re-uploads same name — default duplicate policy is 'delete'
    create_fake_jpeg(input_dir / "photo.jpg", "black")
    second = run_container(
        mounts=mounts_with_errors(input_dir, output_dir, processed_dir, errors_dir),
        args=["--from-input", "--log-level", "debug"],
        capture_output=True,
    )
    print(second.stdout)
    print(second.stderr)
    assert second.returncode == 0

    # Input duplicate should have been deleted by the tool
    assert not (input_dir / "photo.jpg").exists(), "Expected duplicate to be deleted from /input"
    # And nothing new should appear in errors/
    assert not list(errors_dir.glob("photo*.jpg")), "Did not expect a moved duplicate in /errors for delete policy"
