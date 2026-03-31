# SPDX-License-Identifier: GPL-3.0-or-later
"""Smoke tests for the Makefile check_version guard."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE  = REPO_ROOT / "Makefile"
MAKE      = shutil.which("make")

pytestmark = pytest.mark.makefile
needs_make = pytest.mark.skipif(not MAKE, reason="make not available")


def _make(*args: str) -> subprocess.CompletedProcess:
    # Strip FINAL_VERSION from the inherited environment — it may be set
    # by a parent ``make FINAL_VERSION=dev test`` invocation.
    env = {k: v for k, v in os.environ.items() if k != "FINAL_VERSION"}
    env["DRY_RUN"] = "1"
    return subprocess.run(
        [MAKE, f"--makefile={MAKEFILE}", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@needs_make
class TestCheckVersion:
    def test_missing_version_fails(self):
        # Pass FINAL_VERSION= (empty string) explicitly on the command line.
        # A command-line assignment always overrides environment variables and
        # any value inherited from a parent Make process, so the guard fires
        # reliably regardless of how pytest was invoked.
        r = _make("FINAL_VERSION=", "log-build-json")
        assert r.returncode != 0
        assert "FINAL_VERSION" in r.stdout + r.stderr

    def test_invalid_version_fails(self):
        r = _make("FINAL_VERSION=not-a-version", "log-build-json")
        assert r.returncode != 0

    def test_dev_passes(self):
        r = _make("FINAL_VERSION=dev", "log-build-json")
        assert "ERROR: You must set FINAL_VERSION" not in r.stdout + r.stderr

    def test_semver_passes(self):
        r = _make("FINAL_VERSION=0.0.1", "log-build-json")
        assert "ERROR: You must set FINAL_VERSION" not in r.stdout + r.stderr

