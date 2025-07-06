# SPDX-License-Identifier: GPL-3.0-or-later
"""
Integration tests for scrubexif's handling of duplicate JPEGs in /input,
testing both --move-to-error-dir and default deletion.
"""

import os
import subprocess
from pathlib import Path
import shutil
import tempfile
import pytest
from conftest import create_fake_jpeg


IMAGE = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")


def run_container(mounts: list[str], args: list[str] = None) -> subprocess.CompletedProcess:
    user_flag = ["--user", str(os.getuid())] if os.getuid() != 0 else []
    cmd = ["docker", "run", "--rm"] + user_flag + mounts + [IMAGE]
    if args:
        cmd += args
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    print(result.stderr)
    return result


def prepare_common_dirs(base: Path):
    for d in ("input", "output", "processed", "errors"):
        (base / d).mkdir()
    return base / "input", base / "output", base / "processed", base / "errors"


def test_scrub_unique_files(tmp_path):
    input_dir, output_dir, processed_dir, errors_dir = prepare_common_dirs(tmp_path)
    create_fake_jpeg(input_dir / "photo1.jpg", "red")
    create_fake_jpeg(input_dir / "photo2.jpg", "yellow")

    result = run_container([
        "-v", f"{input_dir}:/photos/input",
        "-v", f"{output_dir}:/photos/output",
        "-v", f"{processed_dir}:/photos/processed",
        "-v", f"{errors_dir}:/photos/errors",
    ], args=["--from-input"])

    assert result.returncode == 0
    assert "Successfully scrubbed" in result.stdout
    assert (processed_dir / "photo1.jpg").exists()
    assert (processed_dir / "photo2.jpg").exists()
    assert not (errors_dir / "photo1.jpg").exists()


def test_scrub_and_move_duplicate(tmp_path):
    input_dir, output_dir, processed_dir, errors_dir = prepare_common_dirs(tmp_path)
    # First pass: upload one image
    create_fake_jpeg(input_dir / "photo.jpg", "red")
    run_container([
        "-v", f"{input_dir}:/photos/input",
        "-v", f"{output_dir}:/photos/output",
        "-v", f"{processed_dir}:/photos/processed",
        "-v", f"{errors_dir}:/photos/errors",
    ], args=["--from-input"])

    # Second pass: re-upload same file
    create_fake_jpeg(input_dir / "photo.jpg", "red")

    result = run_container([
        "-v", f"{input_dir}:/photos/input",
        "-v", f"{output_dir}:/photos/output",
        "-v", f"{processed_dir}:/photos/processed",
        "-v", f"{errors_dir}:/photos/errors",
    ], args=["--from-input", "--on-duplicate", "move"])

    assert result.returncode == 0
    assert "📦 Moved duplicate to" in result.stdout
    matching = list(errors_dir.glob("photo*.jpg"))
    assert len(matching) >= 1  # Could be photo.jpg or photo-1.jpg


def test_scrub_and_delete_duplicate(tmp_path):
    input_dir, output_dir, processed_dir, errors_dir = prepare_common_dirs(tmp_path)
    create_fake_jpeg(input_dir / "photo.jpg", "black")
    run_container([
        "-v", f"{input_dir}:/photos/input",
        "-v", f"{output_dir}:/photos/output",
        "-v", f"{processed_dir}:/photos/processed",
        "-v", f"{errors_dir}:/photos/errors",
    ], args=["--from-input"])

    # Re-upload same file, expect deletion
    create_fake_jpeg(input_dir / "photo.jpg", "black")
    result = run_container([
        "-v", f"{input_dir}:/photos/input",
        "-v", f"{output_dir}:/photos/output",
        "-v", f"{processed_dir}:/photos/processed",
        "-v", f"{errors_dir}:/photos/errors",
    ], args=["--from-input"])

    assert result.returncode == 0
    assert not (input_dir / "photo.jpg").exists()
    assert not (errors_dir / "photo.jpg").exists()



