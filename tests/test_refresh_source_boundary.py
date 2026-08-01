# SPDX-License-Identifier: GPL-3.0-or-later
"""Integration tests for refresh source revision boundaries."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"
MAKE = shutil.which("make")
GIT = shutil.which("git")
DOCKER = shutil.which("docker")
JQ = shutil.which("jq")

needs_tools = pytest.mark.skipif(
    not MAKE or not GIT,
    reason="make and git are required",
)
needs_build_tools = pytest.mark.skipif(
    not MAKE or not GIT or not DOCKER or not JQ,
    reason="make, git, docker, and jq commands are required",
)


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a local command without network access.

    Args:
        command: Executable and arguments to run.
        cwd: Working directory for the subprocess.

    Returns:
        Completed subprocess result with captured text output.
    """
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
    )


def _create_revision_pair(tmp_path: Path) -> tuple[Path, Path, str]:
    """Create controller and stable-source worktrees at different commits.

    Args:
        tmp_path: Fresh pytest temporary directory.

    Returns:
        Controller path, stable source path, and stable commit hash.

    Raises:
        RuntimeError: If Git cannot create the test repository or worktree.
    """
    controller = tmp_path / "controller"
    stable_source = tmp_path / "stable-source"
    controller.mkdir()

    commands = (
        [GIT, "init", "--initial-branch=main"],
        [GIT, "config", "user.name", "Refresh Boundary Test"],
        [GIT, "config", "user.email", "refresh-boundary@example.invalid"],
    )
    for command in commands:
        result = _run(command, controller)
        if result.returncode != 0:
            raise RuntimeError(result.stderr)

    tracked_file = controller / "application.txt"
    tracked_file.write_text("stable source\n", encoding="utf-8")
    (controller / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    for command in (
        [GIT, "add", "application.txt", "Dockerfile"],
        [GIT, "commit", "-m", "stable"],
    ):
        result = _run(command, controller)
        if result.returncode != 0:
            raise RuntimeError(result.stderr)

    stable_commit_result = _run([GIT, "rev-parse", "HEAD"], controller)
    if stable_commit_result.returncode != 0:
        raise RuntimeError(stable_commit_result.stderr)
    stable_commit = stable_commit_result.stdout.strip()

    tracked_file.write_text("controller source\n", encoding="utf-8")
    for command in (
        [GIT, "add", "application.txt"],
        [GIT, "commit", "-m", "controller"],
    ):
        result = _run(command, controller)
        if result.returncode != 0:
            raise RuntimeError(result.stderr)

    worktree_result = _run(
        [GIT, "worktree", "add", "--detach", str(stable_source), stable_commit],
        controller,
    )
    if worktree_result.returncode != 0:
        raise RuntimeError(worktree_result.stderr)
    return controller, stable_source, stable_commit


def _validate_source(
    controller: Path,
    stable_source: Path,
    stable_commit: str,
) -> subprocess.CompletedProcess[str]:
    """Run the Makefile refresh-source validator.

    Args:
        controller: Controller checkout used as the command directory.
        stable_source: Stable application source worktree.
        stable_commit: Expected stable application commit.

    Returns:
        Completed Make invocation.
    """
    return _run(
        [
            MAKE,
            f"--makefile={MAKEFILE}",
            f"SOURCE_DIR={stable_source}",
            f"EXPECTED_SOURCE_COMMIT={stable_commit}",
            "validate-refresh-source",
        ],
        controller,
    )


@needs_tools
def test_validate_refresh_source_clean_separate_worktree_passes(tmp_path: Path) -> None:
    """Accept an untouched stable worktree at the expected commit.

    Args:
        tmp_path: Fresh pytest temporary directory.

    Returns:
        None.
    """
    controller, stable_source, stable_commit = _create_revision_pair(tmp_path)

    result = _validate_source(controller, stable_source, stable_commit)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "isolated, clean checkout" in result.stdout


@needs_tools
def test_validate_refresh_source_overlaid_file_fails(tmp_path: Path) -> None:
    """Reject a file overlaid into the stable worktree before testing.

    Args:
        tmp_path: Fresh pytest temporary directory.

    Returns:
        None.
    """
    controller, stable_source, stable_commit = _create_revision_pair(tmp_path)
    (stable_source / "application.txt").write_text(
        "controller source overlaid into stable source\n",
        encoding="utf-8",
    )

    result = _validate_source(controller, stable_source, stable_commit)

    assert result.returncode != 0
    assert "contains modifications or untracked files" in result.stdout


@needs_build_tools
def test_refresh_final_uses_stable_source_context_and_revision(tmp_path: Path) -> None:
    """Use the stable Dockerfile, context, and Git revision for refresh builds.

    Args:
        tmp_path: Fresh pytest temporary directory.

    Returns:
        None.
    """
    controller, stable_source, stable_commit = _create_revision_pair(tmp_path)
    docker_arguments = tmp_path / "docker-arguments.txt"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\n' \"$@\" > \"{docker_arguments}\"\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    result = _run(
        [
            MAKE,
            f"--makefile={MAKEFILE}",
            f"DOCKER={fake_docker}",
            "FINAL_VERSION=1.2.3-1",
            f"SOURCE_DIR={stable_source}",
            f"EXPECTED_SOURCE_COMMIT={stable_commit}",
            "refresh-final",
        ],
        controller,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    arguments = docker_arguments.read_text(encoding="utf-8").splitlines()
    assert str(stable_source / "Dockerfile") in arguments
    assert arguments[-1] == str(stable_source)
    assert f"org.opencontainers.image.revision={stable_commit[:7]}" in arguments


@needs_tools
def test_install_refresh_dependencies_builds_in_temporary_export(
    tmp_path: Path,
) -> None:
    """Keep dependency-build artifacts outside the stable source worktree.

    Args:
        tmp_path: Fresh pytest temporary directory.

    Returns:
        None.
    """
    controller, stable_source, stable_commit = _create_revision_pair(tmp_path)
    python_arguments = tmp_path / "python-arguments.txt"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\n' \"$@\" > \"{python_arguments}\"\n"
        "install_source=\"${4%\\[test\\]}\"\n"
        "mkdir -p \"${install_source}/build\" "
        "\"${install_source}/scrubexif.egg-info\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    result = _run(
        [
            MAKE,
            f"--makefile={MAKEFILE}",
            f"PYTHON={fake_python}",
            f"SOURCE_DIR={stable_source}",
            f"EXPECTED_SOURCE_COMMIT={stable_commit}",
            "install-refresh-test-dependencies",
        ],
        controller,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    arguments = python_arguments.read_text(encoding="utf-8").splitlines()
    assert arguments[:3] == ["-m", "pip", "install"]
    install_source = Path(arguments[3].removesuffix("[test]"))
    assert install_source != stable_source
    assert not install_source.exists()
    assert _validate_source(controller, stable_source, stable_commit).returncode == 0
