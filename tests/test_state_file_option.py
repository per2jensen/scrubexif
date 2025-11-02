# tests/test_state_file_option.py
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests for the new `--state-file` CLI option.

Covers:
  1) Explicit path overrides env + is used (and reported) by the tool
  2) "disabled" forces mtime-only mode regardless of env
  3) Unwritable path → warning + fallback to mtime-only

We keep `--stable-seconds 0` so the gate doesn't delay the tests.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests._docker import mk_mounts, run_container

ASSETS_DIR = Path(__file__).parent / "assets"
SAMPLE_IMAGE = ASSETS_DIR / "sample_with_exif.jpg"


@pytest.fixture
def io_dirs(tmp_path: Path):
    """Create isolated input/output/processed/errors dirs and return them."""
    inp = tmp_path / "input"
    out = tmp_path / "output"
    proc = tmp_path / "processed"
    err = tmp_path / "errors"
    for d in (inp, out, proc, err):
        d.mkdir(parents=True, exist_ok=True)
    # use a real JPEG so exiftool can operate
    shutil.copyfile(SAMPLE_IMAGE, inp / SAMPLE_IMAGE.name)
    return inp, out, proc, err


def _expect_ok_and_output(cp, output_dir: Path, name: str):
    assert cp.returncode == 0, f"Container failed\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
    scrubbed = output_dir / name
    assert scrubbed.exists(), f"Expected scrubbed file at {scrubbed}"


@pytest.mark.smoke
def test_state_file_explicit_path_overrides_env(io_dirs):
    """
    Given SCRUBEXIF_STATE in env,
    When --state-file=/tmp/custom_state.json is passed,
    Then the tool reports that path and processes files.
    """
    inp, out, proc, err = io_dirs
    mounts = mk_mounts(inp, out, proc) + ["-v", f"{err}:/photos/errors"]
    envs = {
        "SCRUBEXIF_STATE": "/tmp/ignored_env_state.json"  # should be overridden
    }
    custom_state = "/tmp/custom_state.json"

    cp = run_container(
        mounts=mounts,
        args=["--from-input", "--stable-seconds", "0", "--state-file", custom_state, "--log-level", "info"],
        capture_output=True,
        envs=envs,
    )

    # Check it processed and reported the explicit state path
    _expect_ok_and_output(cp, out, SAMPLE_IMAGE.name)
    assert f"State path: {custom_state}" in cp.stdout, f"Expected explicit state path in log.\n{cp.stdout}"


@pytest.mark.smoke
def test_state_file_disabled_forces_mtime_only(io_dirs):
    """
    Given SCRUBEXIF_STATE in env,
    When --state-file disabled is passed,
    Then state is disabled (mtime-only) and file still processes.
    """
    inp, out, proc, err = io_dirs
    mounts = mk_mounts(inp, out, proc) + ["-v", f"{err}:/photos/errors"]
    envs = {
        "SCRUBEXIF_STATE": "/tmp/would_be_used_without_cli.json"
    }

    cp = run_container(
        mounts=mounts,
        args=["--from-input", "--stable-seconds", "0", "--state-file", "disabled", "--log-level", "info"],
        capture_output=True,
        envs=envs,
    )

    _expect_ok_and_output(cp, out, SAMPLE_IMAGE.name)
    # Expect explicit "disabled" reporting
    assert "State path: disabled" in cp.stdout, f"Expected disabled state in log.\n{cp.stdout}"


@pytest.mark.smoke
def test_state_file_unwritable_fallback_to_mtime_only(io_dirs):
    """
    When --state-file points to an unwritable location for the container user,
    Then a warning is printed and the tool falls back to mtime-only.
    """
    inp, out, proc, err = io_dirs
    mounts = mk_mounts(inp, out, proc) + ["-v", f"{err}:/photos/errors"]

    # A path that should be unwritable for the non-root user inside the container
    unwritable = "/root/blocked_state.json"

    cp = run_container(
        mounts=mounts,
        args=["--from-input", "--stable-seconds", "0", "--state-file", unwritable, "--log-level", "info"],
        capture_output=True,
    )

    _expect_ok_and_output(cp, out, SAMPLE_IMAGE.name)

    # We expect a warning about not writable and a final "State path: disabled"
    # (logger messages are prefixed, so look for substrings)
    joined = (cp.stdout + "\n" + cp.stderr)
    assert "not writable" in joined.lower() or "State save failed" in joined, f"Expected a warning about unwritable state:\n{joined}"
    assert "State path: disabled" in joined, f"Expected fallback to disabled (mtime-only):\n{joined}"

