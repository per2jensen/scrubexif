# SPDX-License-Identifier: GPL-3.0-or-later
"""Container integration coverage for the disk-backed rename planner."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._docker import mk_mounts, run_container
from tests.conftest import create_fake_jpeg


pytestmark = [pytest.mark.docker, pytest.mark.integration]


def _prepare_directories(tmp_path: Path, file_count: int) -> tuple[Path, Path, Path]:
    """Create mounted auto-mode directories populated with valid JPEGs.

    Args:
        tmp_path: Per-test temporary directory.
        file_count: Number of source JPEGs to create.

    Returns:
        Input, output, and processed directory paths.

    Raises:
        ValueError: If file_count is not positive.
    """
    if (
        not isinstance(file_count, int)
        or isinstance(file_count, bool)
        or file_count <= 0
    ):
        raise ValueError("file_count must be a positive integer")

    input_directory = tmp_path / "input"
    output_directory = tmp_path / "output"
    processed_directory = tmp_path / "processed"
    for directory in (input_directory, output_directory, processed_directory):
        directory.mkdir()
    for index in range(file_count):
        create_fake_jpeg(input_directory / f"source-{index + 1}.jpg")
    return input_directory, output_directory, processed_directory


def test_container_rename_planner_executes_sequential_batch(tmp_path: Path) -> None:
    """The packaged CLI plans and executes a complete sequential rename batch."""
    input_directory, output_directory, processed_directory = _prepare_directories(
        tmp_path,
        file_count=3,
    )
    original_names = {path.name for path in input_directory.iterdir()}

    result = run_container(
        mounts=mk_mounts(input_directory, output_directory, processed_directory),
        args=["--from-input", "--rename", "%n4"],
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "Rename plan ready" in result.stdout
    assert {path.name for path in output_directory.iterdir()} == {
        "0001.jpg",
        "0002.jpg",
        "0003.jpg",
    }
    assert {path.name for path in processed_directory.iterdir()} == original_names
    assert list(input_directory.iterdir()) == []


def test_container_rename_planner_collision_aborts_before_scrub(tmp_path: Path) -> None:
    """A deterministic batch collision leaves every mounted directory unchanged."""
    input_directory, output_directory, processed_directory = _prepare_directories(
        tmp_path,
        file_count=2,
    )
    original_bytes = {
        path.name: path.read_bytes()
        for path in input_directory.iterdir()
    }

    result = run_container(
        mounts=mk_mounts(input_directory, output_directory, processed_directory),
        args=["--from-input", "--rename", "fixed"],
        capture_output=True,
    )

    assert result.returncode == 1, result.stderr or result.stdout
    assert "cannot be re-rolled" in result.stderr + result.stdout
    assert {
        path.name: path.read_bytes()
        for path in input_directory.iterdir()
    } == original_bytes
    assert list(output_directory.iterdir()) == []
    assert list(processed_directory.iterdir()) == []


def test_container_rename_planner_file_limit_aborts_without_changes(
    tmp_path: Path,
) -> None:
    """The packaged planner's file-count breaker stops before any scrub occurs."""
    input_directory, output_directory, processed_directory = _prepare_directories(
        tmp_path,
        file_count=2,
    )
    original_bytes = {
        path.name: path.read_bytes()
        for path in input_directory.iterdir()
    }

    result = run_container(
        mounts=mk_mounts(input_directory, output_directory, processed_directory),
        args=[
            "--from-input",
            "--rename",
            "%n4",
            "--rename-plan-max-files",
            "1",
        ],
        capture_output=True,
    )

    assert result.returncode == 1, result.stderr or result.stdout
    assert "1-file limit" in result.stderr + result.stdout
    assert {
        path.name: path.read_bytes()
        for path in input_directory.iterdir()
    } == original_bytes
    assert list(output_directory.iterdir()) == []
    assert list(processed_directory.iterdir()) == []
