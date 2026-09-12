# tests/test_duplicate_handling.py
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Duplicate-handling integration tests for scrubexif in auto mode.

Covers:
  - Unique files are scrubbed and originals moved to /processed
  - Re-uploaded duplicate moved to /errors with --on-duplicate move
  - Re-uploaded verified duplicate moved to /errors by default
  - Strict fail policy preserves a verified duplicate in /input
  - Same-name different content reported as a collision
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
    assert "Verified duplicate moved to" in second.stdout

    # Duplicate should be moved to errors/
    # Allow for collision suffixes (_1, _2, ...) created by the implementation
    moved_candidates = list(errors_dir.glob("photo*.jpg"))
    assert moved_candidates, "Expected duplicate moved into /errors"


def test_default_policy_moves_verified_duplicate(tmp_path: Path):
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

    # Second pass re-uploads same content — default duplicate policy is 'move'
    create_fake_jpeg(input_dir / "photo.jpg", "black")
    second = run_container(
        mounts=mounts_with_errors(input_dir, output_dir, processed_dir, errors_dir),
        args=["--from-input", "--log-level", "debug"],
        capture_output=True,
    )
    print(second.stdout)
    print(second.stderr)
    assert second.returncode == 0

    assert not (input_dir / "photo.jpg").exists()
    moved_candidates = list(errors_dir.glob("photo*.jpg"))
    assert moved_candidates, "Expected default policy to preserve duplicate in /errors"


def test_same_filename_different_content_is_nonzero_collision(
    tmp_path: Path,
) -> None:
    """A different photo with a reused name is preserved and reported."""
    input_dir, output_dir, processed_dir, errors_dir = prepare_common_dirs(tmp_path)
    mounts = mounts_with_errors(input_dir, output_dir, processed_dir, errors_dir)

    create_fake_jpeg(input_dir / "photo.jpg", "red")
    first = run_container(
        mounts=mounts,
        args=["--from-input", "--log-level", "debug"],
        capture_output=True,
    )
    assert first.returncode == 0

    create_fake_jpeg(input_dir / "photo.jpg", "blue")
    incoming_bytes = (input_dir / "photo.jpg").read_bytes()
    second = run_container(
        mounts=mounts,
        args=["--from-input", "--log-level", "debug"],
        capture_output=True,
    )

    assert second.returncode == 1
    assert "Collision, not a duplicate" in second.stdout
    moved_candidates = [
        path for path in errors_dir.glob("photo*.jpg")
        if path.read_bytes() == incoming_bytes
    ]
    assert moved_candidates, "Collision source was not preserved in /errors"


def test_fail_policy_preserves_verified_duplicate_and_returns_nonzero(
    tmp_path: Path,
) -> None:
    """The strict duplicate policy fails without moving or deleting the source."""
    input_dir, output_dir, processed_dir, errors_dir = prepare_common_dirs(tmp_path)
    mounts = mounts_with_errors(input_dir, output_dir, processed_dir, errors_dir)

    create_fake_jpeg(input_dir / "photo.jpg", "green")
    first = run_container(
        mounts=mounts,
        args=["--from-input"],
        capture_output=True,
    )
    assert first.returncode == 0

    create_fake_jpeg(input_dir / "photo.jpg", "green")
    incoming_bytes = (input_dir / "photo.jpg").read_bytes()
    second = run_container(
        mounts=mounts,
        args=["--from-input", "--on-duplicate", "fail"],
        capture_output=True,
    )

    assert second.returncode == 1
    assert "Verified duplicate rejected" in second.stdout
    assert (input_dir / "photo.jpg").read_bytes() == incoming_bytes
    assert not list(errors_dir.glob("photo*.jpg"))


def test_unsafe_existing_output_keeps_source_and_batch_continues(
    tmp_path: Path,
) -> None:
    """An unsafe destination fails closed without blocking unrelated files."""
    input_dir, output_dir, processed_dir, errors_dir = prepare_common_dirs(tmp_path)
    mounts = mounts_with_errors(input_dir, output_dir, processed_dir, errors_dir)

    create_fake_jpeg(input_dir / "unsafe.jpg", "red")
    create_fake_jpeg(input_dir / "safe.jpg", "blue")
    unsafe_source_bytes = (input_dir / "unsafe.jpg").read_bytes()
    unsafe_output_bytes = b"not-an-audited-jpeg"
    (output_dir / "unsafe.jpg").write_bytes(unsafe_output_bytes)

    result = run_container(
        mounts=mounts,
        args=["--from-input"],
        capture_output=True,
    )

    assert result.returncode == 1
    assert "Existing output failed privacy audit" in result.stdout
    assert (output_dir / "unsafe.jpg").read_bytes() == unsafe_output_bytes
    assert (input_dir / "unsafe.jpg").read_bytes() == unsafe_source_bytes
    assert (output_dir / "safe.jpg").is_file()
    assert (processed_dir / "safe.jpg").is_file()
