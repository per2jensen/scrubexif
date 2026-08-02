# SPDX-License-Identifier: GPL-3.0-or-later
"""Integration coverage for auto mode encountering corrupted JPEG inputs."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ._docker import mk_mounts, run_container

ASSETS_DIR = Path(__file__).parent / "assets"
SOURCE_IMAGE = ASSETS_DIR / "sample_with_exif.jpg"
TOTAL_IMAGES = 15


def _prepare_jpegs(root: Path) -> tuple[list[Path], list[Path]]:
    """Create deterministic valid and invalid JPEG test inputs.

    Args:
        root: Existing temporary test directory.

    Returns:
        Valid and invalid input paths, respectively.

    Raises:
        ValueError: If root is not an existing directory.
    """
    if not root.is_dir():
        raise ValueError(f"root must be an existing directory: {root}")

    input_dir = root / "input"
    input_dir.mkdir()

    good: list[Path] = []
    corrupted: list[Path] = []

    for idx in range(TOTAL_IMAGES):
        target = input_dir / f"photo_{idx:02d}.jpg"
        shutil.copyfile(SOURCE_IMAGE, target)

        if idx % 2 == 0:
            good.append(target)
            continue

        _corrupt_file(target, variant=idx)
        corrupted.append(target)

    return good, corrupted


def _corrupt_file(path: Path, variant: int) -> None:
    """Replace a JPEG with one of two unambiguously invalid payloads.

    Args:
        path: Existing JPEG fixture to corrupt.
        variant: Non-negative value selecting the invalid payload.

    Returns:
        None.

    Raises:
        ValueError: If path is not a file or variant is negative.
    """
    if not path.is_file():
        raise ValueError(f"path must be an existing file: {path}")
    if variant < 0:
        raise ValueError("variant must be non-negative")

    if variant % 4 == 1:
        path.write_bytes(f"not-a-jpeg-{variant}".encode("ascii"))
        return

    # Alternate invalid inputs retain the JPEG body but cannot have a valid SOI.
    data = bytearray(path.read_bytes())
    if len(data) < 2:
        raise ValueError(f"JPEG fixture is too short to corrupt: {path}")
    data[0:2] = b"\x00\x00"
    path.write_bytes(data)


@pytest.mark.nightly
@pytest.mark.docker
def test_corrupted_inputs_moved_to_processed(tmp_path: Path) -> None:
    """Process valid files while safely retaining every original input.

    Args:
        tmp_path: Isolated pytest temporary directory.

    Returns:
        None.
    """
    assert SOURCE_IMAGE.is_file(), (
        f"Required JPEG fixture is missing: {SOURCE_IMAGE}"
    )

    good_files, damaged_files = _prepare_jpegs(tmp_path)
    all_files = good_files + damaged_files
    original_contents = {path.name: path.read_bytes() for path in all_files}
    good_names = {path.name for path in good_files}
    damaged_names = {path.name for path in damaged_files}
    expected_names = good_names | damaged_names

    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    errors_dir = tmp_path / "errors"
    for directory in (output_dir, processed_dir, errors_dir):
        directory.mkdir()

    mounts = mk_mounts(tmp_path / "input", output_dir, processed_dir)
    mounts += ["-v", f"{errors_dir}:/photos/errors"]

    cp = run_container(
        mounts=mounts,
        args=["--from-input"],
        capture_output=True,
    )
    print(cp.stdout)
    print(cp.stderr)

    assert cp.returncode == 1, f"Docker exited with {cp.returncode}:\n{cp.stderr}\n{cp.stdout}"

    processed_names = {path.name for path in processed_dir.iterdir()}
    assert processed_names == expected_names, "Expected all originals retained in processed/"
    for name, original_content in original_contents.items():
        assert (processed_dir / name).read_bytes() == original_content, (
            f"Archived original was modified: {name}"
        )

    output_names = {path.name for path in output_dir.iterdir()}
    assert output_names == good_names, "Output must contain exactly the scrubbed valid files"

    assert not any((tmp_path / "input").iterdir()), "Input directory should be emptied after processing"
    assert not any(errors_dir.iterdir()), "No files should be moved to errors/"

    stdout = cp.stdout or ""
    summary_line = next(
        (line for line in stdout.splitlines() if line.startswith("SCRUBEXIF_SUMMARY ")),
        None,
    )
    assert summary_line is not None, f"Machine-readable summary missing:\n{stdout}"
    summary_fields = dict(
        field.split("=", 1)
        for field in summary_line.removeprefix("SCRUBEXIF_SUMMARY ").split()
        if "=" in field
    )
    duration = summary_fields.pop("duration", None)
    assert duration is not None, f"Summary duration missing: {summary_line}"
    assert summary_fields == {
        "total": str(TOTAL_IMAGES),
        "scrubbed": str(len(good_files)),
        "skipped": "0",
        "errors": str(len(damaged_files)),
        "duplicates_deleted": "0",
        "duplicates_moved": "0",
    }
    assert float(duration) >= 0.0

    for damaged in damaged_files:
        expected_fragment = (
            f"Scrub failed for /photos/input/{damaged.name}; "
            f"moved original to /photos/processed/{damaged.name} for inspection"
        )
        assert expected_fragment in stdout, f"Missing failure notice for {damaged.name}"


@pytest.mark.smoke
@pytest.mark.docker
def test_corrupted_input_never_written_to_output(tmp_path: Path) -> None:
    """Reject an invalid JPEG without modifying its archived payload.

    Args:
        tmp_path: Isolated pytest temporary directory.

    Returns:
        None.
    """
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"

    for directory in (input_dir, output_dir, processed_dir):
        directory.mkdir()

    bad = input_dir / "bad.jpg"
    bad.write_bytes(b"not-a-jpeg")

    cp = run_container(
        mounts=mk_mounts(input_dir, output_dir, processed_dir),
        args=["--from-input", "--log-level", "debug"],
        capture_output=True,
    )
    print(cp.stdout)
    print(cp.stderr)

    assert cp.returncode == 1, f"Expected scrub failure exit:\n{cp.stderr}\n{cp.stdout}"

    assert not any(output_dir.iterdir()), "Corrupted input or temporary file leaked to output/"
    assert (processed_dir / bad.name).exists(), "Corrupted input should be moved to processed/"
    assert (processed_dir / bad.name).read_bytes() == b"not-a-jpeg"
    assert not bad.exists(), "Corrupted input should not remain in input/"
    assert "Scrub failed" in (cp.stdout or ""), "Expected failure message in output"
    assert bad.name in (cp.stdout or ""), "Expected filename in failure output"
