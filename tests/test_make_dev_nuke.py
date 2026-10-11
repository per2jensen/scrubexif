# SPDX-License-Identifier: GPL-3.0-or-later
"""Integration tests for the destructive development-image rebuild target."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _create_fake_docker(tmp_path: Path) -> Path:
    """Create a Docker stand-in that records invocations and can fail on demand.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the executable Docker stand-in.

    Raises:
        OSError: If the stand-in cannot be written or made executable.
    """
    fake_docker = tmp_path / "fake-docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

printf '%s\\n' "$*" >> "${DOCKER_LOG:?DOCKER_LOG is required}"

if [ "${FAKE_DOCKER_FAIL:-}" = "${1:-} ${2:-}" ]; then
    exit 73
fi
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    return fake_docker


def _run_dev_nuke(
    fake_docker: Path,
    log_path: Path,
    fail_command: str = "",
) -> subprocess.CompletedProcess[str]:
    """Run the dev-nuke target with the Docker stand-in.

    Args:
        fake_docker: Executable Docker stand-in.
        log_path: Destination for recorded Docker arguments.
        fail_command: Optional first two Docker arguments that should fail.

    Returns:
        Completed make invocation.

    Raises:
        OSError: If make cannot be executed.
    """
    environment = os.environ.copy()
    environment["CI"] = "1"
    environment["DOCKER_LOG"] = str(log_path)
    environment["FAKE_DOCKER_FAIL"] = fail_command

    return subprocess.run(
        [
            "make",
            "--no-print-directory",
            f"DOCKER={fake_docker}",
            "dev-nuke",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_dev_nuke_success_prunes_and_builds_without_cache(tmp_path: Path) -> None:
    """A successful nuke prunes globally and performs an independent clean build.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        None.

    Raises:
        AssertionError: If command order or clean-build flags are incorrect.
    """
    log_path = tmp_path / "docker.log"
    fake_docker = _create_fake_docker(tmp_path)

    result = _run_dev_nuke(fake_docker, log_path)

    assert result.returncode == 0, result.stderr
    calls = log_path.read_text(encoding="utf-8").splitlines()
    assert calls[0] == "builder prune -a -f"
    assert calls[1] == "image prune -a -f"
    assert calls[2].startswith("build --no-cache --pull -f Dockerfile ")
    assert "--build-arg VERSION=dev" in calls[2]
    assert "-t scrubexif:dev ." in calls[2]
    assert calls[3].startswith(
        "run --read-only --security-opt no-new-privileges --rm "
    )
    assert "--entrypoint python3 scrubexif:dev -c " in calls[3]
    assert len(calls) == 4


@pytest.mark.parametrize(
    ("fail_command", "expected_calls", "expected_error"),
    [
        (
            "builder prune",
            ["builder prune -a -f"],
            "unable to prune Docker build cache",
        ),
        (
            "image prune",
            ["builder prune -a -f", "image prune -a -f"],
            "unable to prune unused Docker images",
        ),
    ],
)
def test_dev_nuke_prune_failure_stops_before_build(
    tmp_path: Path,
    fail_command: str,
    expected_calls: list[str],
    expected_error: str,
) -> None:
    """A prune failure aborts without claiming or attempting a clean build.

    Args:
        tmp_path: Pytest temporary directory.
        fail_command: Docker command prefix configured to fail.
        expected_calls: Complete list of Docker calls expected before failure.
        expected_error: Diagnostic fragment expected on standard error.

    Returns:
        None.

    Raises:
        AssertionError: If the target continues after a prune failure.
    """
    log_path = tmp_path / "docker.log"
    fake_docker = _create_fake_docker(tmp_path)

    result = _run_dev_nuke(fake_docker, log_path, fail_command)

    assert result.returncode != 0
    assert expected_error in result.stderr
    assert log_path.read_text(encoding="utf-8").splitlines() == expected_calls
