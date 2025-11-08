# SPDX-License-Identifier: GPL-3.0-or-later
"""Integration coverage for auto mode encountering corrupted JPEG inputs."""

from __future__ import annotations

import random
import shutil
from pathlib import Path

import pytest

from ._docker import mk_mounts, run_container

ASSETS_DIR = Path(__file__).parent / "assets"
SOURCE_IMAGE = ASSETS_DIR / "sample_with_exif.jpg"
TOTAL_IMAGES = 15
LIGHT_CORRUPTION_RATIO = 0.3


def _prepare_jpegs(root: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """Return (good_files, corrupted_files, lightly_corrupted_files) under root/input."""
    input_dir = root / "input"
    input_dir.mkdir()

    good: list[Path] = []
    corrupted: list[Path] = []
    lightly_corrupted: list[Path] = []

    for idx in range(TOTAL_IMAGES):
        target = input_dir / f"photo_{idx:02d}.jpg"
        shutil.copyfile(SOURCE_IMAGE, target)

        if idx % 2 == 0:
            good.append(target)
            continue

        _corrupt_file(target, seed=idx)
        corrupted.append(target)

    # Apply lighter random mutations to ~30% of files (preferably ones that should still scrub)
    mutation_count = max(1, int(TOTAL_IMAGES * LIGHT_CORRUPTION_RATIO))
    rng = random.Random(42)
    mutation_candidates = rng.sample(good, k=min(mutation_count, len(good)))
    for idx, target in enumerate(mutation_candidates):
        if _lightly_corrupt_file(target, seed=100 + idx):
            lightly_corrupted.append(target)

    return good, corrupted, lightly_corrupted


def _corrupt_file(path: Path, seed: int) -> None:
    """Smear random data across the file to simulate broken JPEGs."""
    data = bytearray(path.read_bytes())
    rng = random.Random(seed)
    if len(data) >= 4:
        # Clobber SOI marker to ensure the decoder complains
        data[0:2] = b"\x00\x00"
        data[-2:] = b"\x00\x00"
    # Overwrite several random segments to resemble partial corruption.
    mutations = max(6, len(data) // 128)
    for _ in range(mutations):
        if len(data) < 2:
            break
        start = rng.randrange(0, len(data) - 1)
        span = rng.randrange(1, min(128, len(data) - start))
        for offset in range(span):
            data[start + offset] = rng.randrange(0, 256)
    path.write_bytes(data)


def _lightly_corrupt_file(path: Path, seed: int) -> bool:
    """Inject random single-byte mutations without destroying JPEG structure."""
    original = path.read_bytes()
    data = bytearray(original)
    if len(data) < 4096:
        return False

    rng = random.Random(seed)
    mutations = max(10, len(data) // 4096)
    for _ in range(mutations):
        start = rng.randrange(512, len(data) - 512)
        data[start] = rng.randrange(0, 256)

    path.write_bytes(data)
    try:
        from PIL import Image

        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        path.write_bytes(original)
        return False

@pytest.mark.nightly
@pytest.mark.docker
def test_corrupted_inputs_moved_to_processed(tmp_path):
    if not SOURCE_IMAGE.exists():
        pytest.skip("sample_with_exif.jpg fixture missing")

    good_files, damaged_files, lightly_corrupted = _prepare_jpegs(tmp_path)

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

    assert cp.returncode == 0, f"Docker exited with {cp.returncode}:\n{cp.stderr}\n{cp.stdout}"

    processed_names = {p.name for p in processed_dir.glob("*.jpg")}
    expected_names = {f.name for f in good_files} | {f.name for f in damaged_files}
    assert processed_names == expected_names, "Expected all originals retained in processed/"

    output_names = {p.name for p in output_dir.glob("*.jpg")}
    assert {f.name for f in good_files} <= output_names, "Scrubbed output missing known good files"
    assert not ({f.name for f in damaged_files} & output_names), "Damaged files should not appear in output/"
    assert {f.name for f in lightly_corrupted} <= output_names, "Lightly corrupted files should still scrub successfully"

    assert not any((tmp_path / "input").iterdir()), "Input directory should be emptied after processing"

    for damaged in damaged_files:
        expected_fragment = f"Scrub failed for {damaged.name}; moved original"
        assert expected_fragment in (cp.stdout or ""), f"Missing failure notice for {damaged.name}"
