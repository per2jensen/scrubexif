# tests/test_nightly_stability.py
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import time
from pathlib import Path

import pytest
from tests._docker import mk_mounts, run_container
from .conftest import create_fake_jpeg


@pytest.mark.nightly
def test_unstable_files_are_skipped_without_processing(tmp_path: Path):
    """
    With a high stability window and fresh mtimes, auto mode must skip processing.
    We don't sleep; we just keep mtime == now.
    """
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    errors_dir = tmp_path / "errors"
    for d in (input_dir, output_dir, processed_dir, errors_dir):
        d.mkdir()

    # Create fresh files (mtimes ~ now)
    create_fake_jpeg(input_dir / "recent1.jpg", "red")
    create_fake_jpeg(input_dir / "recent2.jpg", "green")

    mounts = mk_mounts(input_dir, output_dir, processed_dir) + ["-v", f"{errors_dir}:/photos/errors"]

    # Set a high gate so fresh files are considered unstable
    envs = {
        "SCRUBEXIF_STABLE_SECONDS": "300",
        "SCRUBEXIF_STATE": "/tmp/.scrubexif_state.nightly.json",
    }
    cp = run_container(
        mounts=mounts,
        args=["--from-input", "--log-level", "info"],
        capture_output=True,
        envs=envs,
    )
    print(cp.stdout)
    print(cp.stderr)
    assert cp.returncode == 0
    assert "Skipped (unstable/temp)  : 2" in cp.stdout
    assert not (output_dir / "recent1.jpg").exists()
    assert not (output_dir / "recent2.jpg").exists()
    assert (input_dir / "recent1.jpg").exists()  # not moved
    assert (input_dir / "recent2.jpg").exists()


@pytest.mark.nightly
def test_old_files_are_processed_when_older_than_gate(tmp_path: Path):
    """
    With a medium stability window, we 'age' the file mtimes to older than the gate.
    No sleep; set mtime in the past via os.utime so processing happens immediately.
    """
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    errors_dir = tmp_path / "errors"
    for d in (input_dir, output_dir, processed_dir, errors_dir):
        d.mkdir()

    target = input_dir / "old.jpg"
    create_fake_jpeg(target, "blue")

    # Age the file beyond the gate (e.g., gate 60s, set mtime to now-120s)
    now = time.time()
    aged = now - 120
    os.utime(target, (aged, aged))

    mounts = mk_mounts(input_dir, output_dir, processed_dir) + ["-v", f"{errors_dir}:/photos/errors"]
    envs = {
        "SCRUBEXIF_STABLE_SECONDS": "60",
        "SCRUBEXIF_STATE": "/tmp/.scrubexif_state.nightly.json",
    }
    cp = run_container(
        mounts=mounts,
        args=["--from-input", "--log-level", "info"],
        capture_output=True,
        envs=envs,
    )
    print(cp.stdout)
    print(cp.stderr)
    assert cp.returncode == 0
    assert "Successfully scrubbed" in cp.stdout
    assert (output_dir / "old.jpg").exists()
    assert (processed_dir / "old.jpg").exists()
    assert not (input_dir / "old.jpg").exists()


