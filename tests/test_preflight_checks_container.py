# SPDX-License-Identifier: GPL-3.0-or-later
"""
Integration tests for pre-flight validation logic in the scrubexif container.

Covers:
- Input path is a file
- Missing or unreadable directories
- Non-writable output/processed dirs
- Symlink resolution (xfail)
- Sanity check: valid setup should pass
"""

import os
import subprocess
from pathlib import Path
import pytest

IMAGE = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")


def run_container(mounts: list[str]) -> subprocess.CompletedProcess:
    """Run container with mounts and current UID (unless root)."""
    user_flag = ["--user", str(os.getuid())] if os.getuid() != 0 else []
    cmd = ["docker", "run", "--read-only", "--security-opt", "no-new-privileges", "--rm"] + user_flag + mounts + [IMAGE, "--from-input"]
    return subprocess.run(cmd, capture_output=True, text=True)


def assert_failed_with_keywords(result: subprocess.CompletedProcess, keywords: list[str]):
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode != 0, "Expected container to fail, but it exited with 0"
    assert any(k in combined for k in keywords), f"Expected failure reason missing.\nOutput:\n{combined}"


def test_input_is_file(tmp_path):
    bad_input = tmp_path / "input"
    bad_input.write_text("I am not a directory")
    (tmp_path / "output").mkdir()
    (tmp_path / "processed").mkdir()

    result = run_container([
        "-v", f"{bad_input}:/photos/input",
        "-v", f"{tmp_path / 'output'}:/photos/output",
        "-v", f"{tmp_path / 'processed'}:/photos/processed",
    ])

    assert_failed_with_keywords(result, ["not a directory"])


def test_processed_dir_not_writable(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    input_dir.mkdir()
    output_dir.mkdir()
    processed_dir.mkdir()
    processed_dir.chmod(0o500)  # Remove write permissions

    try:
        result = run_container([
            "-v", f"{input_dir}:/photos/input",
            "-v", f"{output_dir}:/photos/output",
            "-v", f"{processed_dir}:/photos/processed",
        ])
        assert_failed_with_keywords(result, ["not writable"])
    finally:
        processed_dir.chmod(0o700)  # Restore permissions


def test_input_directory_does_not_exist(tmp_path):
    bogus_input = tmp_path / "input"
    output = tmp_path / "output"
    processed = tmp_path / "processed"
    output.mkdir()
    processed.mkdir()

    result = run_container([
        "-v", f"{bogus_input}:/photos/input",
        "-v", f"{output}:/photos/output",
        "-v", f"{processed}:/photos/processed"
    ])

    assert_failed_with_keywords(result, ["not writable", "does not exist"])


@pytest.mark.xfail(reason="Docker resolves host symlinks; container sees real dir")
def test_input_is_symlink(tmp_path):
    real_dir = tmp_path / "real"
    symlink_dir = tmp_path / "input"
    output = tmp_path / "output"
    processed = tmp_path / "processed"
    real_dir.mkdir()
    symlink_dir.symlink_to(real_dir)
    output.mkdir()
    processed.mkdir()

    result = run_container([
        "-v", f"{symlink_dir}:/photos/input",
        "-v", f"{output}:/photos/output",
        "-v", f"{processed}:/photos/processed"
    ])

    assert_failed_with_keywords(result, ["symlink", "not writable"])


def test_unwritable_output_directory(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    input_dir.mkdir()
    output_dir.mkdir()
    processed_dir.mkdir()
    output_dir.chmod(0o555)  # read-only

    try:
        if os.getuid() != 0:  # Only fails as non-root
            result = run_container([
                "-v", f"{input_dir}:/photos/input",
                "-v", f"{output_dir}:/photos/output",
                "-v", f"{processed_dir}:/photos/processed"
            ])
            assert_failed_with_keywords(result, ["not writable"])
    finally:
        output_dir.chmod(0o755)


def test_all_directories_valid(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    input_dir.mkdir()
    output_dir.mkdir()
    processed_dir.mkdir()

    result = run_container([
        "-v", f"{input_dir}:/photos/input",
        "-v", f"{output_dir}:/photos/output",
        "-v", f"{processed_dir}:/photos/processed"
    ])

    assert result.returncode == 0
    assert "no jpegs found" in (result.stdout + result.stderr).lower()
