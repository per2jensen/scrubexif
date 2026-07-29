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


def _write_fake_docker(tmp_path: Path, cli_version: str) -> Path:
    """Create a Docker command stub that reports a scrub CLI version.

    Args:
        tmp_path: Pytest temporary directory.
        cli_version: Version returned by the fake container command.

    Returns:
        Executable path suitable for the Makefile DOCKER variable.
    """
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"echo 'scrub {cli_version}'\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    return fake_docker


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

    def test_numeric_refresh_version_passes(self) -> None:
        """A positive numeric suffix is accepted for refreshed image tags."""
        r = _make("FINAL_VERSION=0.0.1-1", "log-build-json")
        assert "FINAL_VERSION must be" not in r.stdout + r.stderr

    def test_zero_refresh_version_fails(self) -> None:
        """A zero refresh number is rejected."""
        r = _make("FINAL_VERSION=0.0.1-0", "log-build-json")
        assert r.returncode != 0
        assert "FINAL_VERSION must be" in r.stdout + r.stderr

    def test_named_prerelease_version_fails(self) -> None:
        """A named prerelease is not confused with a numeric refresh."""
        r = _make("FINAL_VERSION=0.0.1-rc1", "log-build-json")
        assert r.returncode != 0
        assert "FINAL_VERSION must be" in r.stdout + r.stderr

    def test_refresh_cli_base_version_matches_expected_version(
        self,
        tmp_path: Path,
    ) -> None:
        """A refresh image accepts the stable application CLI version."""
        fake_docker = _write_fake_docker(tmp_path, "0.0.1")

        r = _make(
            f"DOCKER={fake_docker}",
            "FINAL_VERSION=0.0.1-1",
            "EXPECTED_CLI_VERSION=0.0.1",
            "verify-cli-version",
        )

        assert r.returncode == 0, r.stdout + r.stderr

    def test_refresh_cli_wrong_base_version_fails(
        self,
        tmp_path: Path,
    ) -> None:
        """A refresh image fails when its application CLI version is wrong."""
        fake_docker = _write_fake_docker(tmp_path, "0.0.2")

        r = _make(
            f"DOCKER={fake_docker}",
            "FINAL_VERSION=0.0.1-1",
            "EXPECTED_CLI_VERSION=0.0.1",
            "verify-cli-version",
        )

        assert r.returncode != 0
        assert "Version mismatch" in r.stdout + r.stderr
