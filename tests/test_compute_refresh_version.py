# SPDX-License-Identifier: GPL-3.0-or-later
"""Integration tests for the image-refresh version computation command."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "compute_refresh_version.py"


def _run_git(repository: Path, *args: str) -> None:
    """Run a Git command in a test repository.

    Args:
        repository: Temporary Git repository.
        *args: Arguments passed to Git.

    Returns:
        None.

    Raises:
        subprocess.CalledProcessError: If Git exits unsuccessfully.
    """
    subprocess.run(["git", *args], cwd=repository, check=True, capture_output=True)


def _create_repository(tmp_path: Path, stable_version: str) -> Path:
    """Create a real Git repository with one stable release tag.

    Args:
        tmp_path: Pytest temporary directory.
        stable_version: Stable version used for the release tag.

    Returns:
        Path to the initialized Git repository.

    Raises:
        subprocess.CalledProcessError: If Git setup fails.
    """
    repository = tmp_path / "repository"
    repository.mkdir()
    _run_git(repository, "init", "--initial-branch=main")
    _run_git(repository, "config", "user.name", "Refresh Test")
    _run_git(repository, "config", "user.email", "refresh@example.invalid")
    marker = repository / "marker.txt"
    marker.write_text("release\n", encoding="utf-8")
    _run_git(repository, "add", "marker.txt")
    _run_git(repository, "commit", "-m", "release")
    _run_git(repository, "tag", f"v{stable_version}")
    return repository


def _write_history(path: Path, tags: list[str]) -> None:
    """Write a minimal build-history file.

    Args:
        path: Destination JSON path.
        tags: Ordered image tags to put in the history.

    Returns:
        None.
    """
    history = [
        {"build_number": build_number, "tag": tag}
        for build_number, tag in enumerate(tags)
    ]
    path.write_text(json.dumps(history), encoding="utf-8")


def _run_command(
    repository: Path,
    history: Path,
    output: Path,
) -> subprocess.CompletedProcess[str]:
    """Run the refresh-version command.

    Args:
        repository: Git repository passed to the command.
        history: Build-history path passed to the command.
        output: GitHub output path passed to the command.

    Returns:
        Completed command result.
    """
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--history",
            str(history),
            "--repository",
            str(repository),
            "--github-output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_compute_refresh_version_stable_release_returns_first_refresh(
    tmp_path: Path,
) -> None:
    """A stable release without refresh tags produces refresh number one."""
    repository = _create_repository(tmp_path, "1.2.3")
    history = tmp_path / "build-history.json"
    output = tmp_path / "github-output.txt"
    output.touch()
    _write_history(history, ["1.2.3"])

    result = _run_command(repository, history, output)

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == (
        "base_version=1.2.3\nrefresh_version=1.2.3-1\n"
    )


def test_compute_refresh_version_existing_refreshes_returns_next_number(
    tmp_path: Path,
) -> None:
    """Existing numeric refresh tags are incremented for the same release."""
    repository = _create_repository(tmp_path, "1.2.3")
    _run_git(repository, "tag", "v1.2.3-1")
    _run_git(repository, "tag", "v1.2.3-2")
    history = tmp_path / "build-history.json"
    output = tmp_path / "github-output.txt"
    output.touch()
    _write_history(history, ["1.2.3", "1.2.3-1", "1.2.3-2"])

    result = _run_command(repository, history, output)

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == (
        "base_version=1.2.3\nrefresh_version=1.2.3-3\n"
    )


def test_compute_refresh_version_malformed_history_fails_without_output(
    tmp_path: Path,
) -> None:
    """Malformed build history fails without producing version output."""
    repository = _create_repository(tmp_path, "1.2.3")
    history = tmp_path / "build-history.json"
    output = tmp_path / "github-output.txt"
    output.touch()
    history.write_text("{not-json", encoding="utf-8")

    result = _run_command(repository, history, output)

    assert result.returncode != 0
    assert "not valid JSON" in result.stderr
    assert output.read_text(encoding="utf-8") == ""


def test_compute_refresh_version_missing_stable_git_tag_fails(
    tmp_path: Path,
) -> None:
    """History whose stable release Git tag is absent fails explicitly."""
    repository = _create_repository(tmp_path, "1.2.2")
    history = tmp_path / "build-history.json"
    output = tmp_path / "github-output.txt"
    output.touch()
    _write_history(history, ["1.2.3"])

    result = _run_command(repository, history, output)

    assert result.returncode != 0
    assert "v1.2.3" in result.stderr
    assert output.read_text(encoding="utf-8") == ""


def test_compute_refresh_version_prerelease_history_tag_fails(
    tmp_path: Path,
) -> None:
    """A non-stable latest history tag is rejected."""
    repository = _create_repository(tmp_path, "1.2.3")
    history = tmp_path / "build-history.json"
    output = tmp_path / "github-output.txt"
    output.touch()
    _write_history(history, ["1.2.3-rc1"])

    result = _run_command(repository, history, output)

    assert result.returncode != 0
    assert "stable X.Y.Z" in result.stderr
    assert output.read_text(encoding="utf-8") == ""


def test_compute_refresh_version_zero_refresh_history_tag_fails(
    tmp_path: Path,
) -> None:
    """A zero-valued refresh suffix in build history is rejected."""
    repository = _create_repository(tmp_path, "1.2.3")
    history = tmp_path / "build-history.json"
    output = tmp_path / "github-output.txt"
    output.touch()
    _write_history(history, ["1.2.3-0"])

    result = _run_command(repository, history, output)

    assert result.returncode != 0
    assert "stable X.Y.Z" in result.stderr
    assert output.read_text(encoding="utf-8") == ""
