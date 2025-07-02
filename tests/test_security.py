# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
import os
import pytest

IMAGE = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")

@pytest.mark.smoke
def test_root_user_blocked_without_allow_root():
    """Ensure container exits with error when run as root without ALLOW_ROOT=1."""
    result = subprocess.run([
        "docker", "run", "--rm", "--user", "0", IMAGE
    ], capture_output=True, text=True)

    assert result.returncode != 0, "❌ Container should fail when run as root without ALLOW_ROOT"
    assert "not allowed unless ALLOW_ROOT=1" in result.stderr + result.stdout


@pytest.mark.smoke
def test_root_user_allowed_with_env_override():
    """Ensure container runs successfully as root if ALLOW_ROOT=1 is set."""
    result = subprocess.run([
        "docker", "run", "--rm", "--user", "0",
        "-e", "ALLOW_ROOT=1",
        IMAGE, "--dry-run"
    ], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "Running as root" not in result.stdout + result.stderr  # It should silently allow



