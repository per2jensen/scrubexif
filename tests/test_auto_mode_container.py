# SPDX-License-Identifier: GPL-3.0-or-later
"""
Integration test that runs the actual Docker container on sample images.

✔ Verifies GPS metadata is stripped
✔ Verifies ExposureTime (and other key tags) are retained
"""

import os
import shutil
import subprocess
import json
import pytest
import uuid
from pathlib import Path

from ._docker import mk_mounts, run_container  # centralize docker flags/envs

ASSETS_DIR = Path(__file__).parent / "assets"
SAMPLE_IMAGE = ASSETS_DIR / "sample_with_exif.jpg"
SCRUBBED_NAME = SAMPLE_IMAGE.name
IMAGE_TAG = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")


@pytest.fixture
def setup_test_env(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    input_dir.mkdir()
    output_dir.mkdir()
    processed_dir.mkdir()

    assert SAMPLE_IMAGE.exists(), f"Missing test image: {SAMPLE_IMAGE}"

    # Unique filename avoids triggering duplicate logic
    unique_name = f"sample_{uuid.uuid4().hex[:8]}.jpg"
    dst = input_dir / unique_name
    shutil.copyfile(SAMPLE_IMAGE, dst)

    return input_dir, output_dir, processed_dir, dst


def run_scrubexif_container(input_dir: Path, output_dir: Path, processed_dir: Path):
    """Run scrubexif in auto mode with stable_seconds=0 and writable /tmp."""
    mounts = mk_mounts(input_dir, output_dir, processed_dir)
    cp = run_container(
        image=IMAGE_TAG,
        mounts=mounts,
        args=["--from-input", "--log-level", "debug"],
        capture_output=True,
    )
    print(cp.stdout)
    print(cp.stderr)
    assert cp.returncode == 0, f"Docker failed:\n{cp.stderr}\n{cp.stdout}"


def inspect_exif_host(file: Path) -> list[str]:
    result = subprocess.run(["exiftool", str(file)], capture_output=True, text=True)
    return result.stdout.lower().splitlines()


def inspect_exif_container(file: Path) -> list[str]:
    mounts = ["-v", f"{file.parent}:/photos"]
    cp = run_container(
        image=IMAGE_TAG,
        mounts=mounts,
        entrypoint="exiftool",
        args=[f"/photos/{file.name}"],
        capture_output=True,
    )
    print(cp.stdout)
    print(cp.stderr)
    return (cp.stdout or "").lower().splitlines()


def assert_no_gps_tags(lines: list[str]):
    offending = [line for line in lines if "gps" in line and not line.startswith("directory")]
    assert not offending, f"❌ GPS tags still present:\n" + "\n".join(offending)


def test_sample_image_contains_gps_data():
    cp = run_container(
        image=IMAGE_TAG,
        mounts=["-v", f"{SAMPLE_IMAGE.parent}:/photos"],
        entrypoint="exiftool",
        args=[f"/photos/{SAMPLE_IMAGE.name}"],
        capture_output=True,
    )
    assert "gps" in (cp.stdout or "").lower(), "❌ Expected GPS metadata not found in test image"


def test_gps_removed_and_exposure_retained(setup_test_env):
    input_dir, output_dir, processed_dir, original = setup_test_env
    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / original.name
    lines = inspect_exif_host(scrubbed)
    assert_no_gps_tags(lines)
    assert any("exposure time" in line for line in lines), "❌ Missing ExposureTime in scrubbed file"


def test_output_file_has_no_gps_tag(setup_test_env):
    input_dir, output_dir, processed_dir, original = setup_test_env
    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / original.name
    output = subprocess.run(["exiftool", str(scrubbed)], capture_output=True, text=True)
    assert "gps position" not in output.stdout.lower(), "❌ 'GPS Position' still present in scrubbed output"


def test_scrubbed_output_exists_and_is_jpeg(setup_test_env):
    input_dir, output_dir, processed_dir, original = setup_test_env
    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / original.name
    assert scrubbed.exists()
    with open(scrubbed, "rb") as f:
        assert f.read(2) == b"\xff\xd8", "❌ Output is not a valid JPEG (missing SOI marker)"


def test_original_file_moved_to_processed(setup_test_env):
    input_dir, output_dir, processed_dir, original = setup_test_env
    run_scrubexif_container(input_dir, output_dir, processed_dir)

    input_file = input_dir / original.name
    assert not input_file.exists(), f"❌ Original file still in input/: {original}"
    assert (processed_dir / original.name).exists(), "❌ Processed original not found"


def test_no_gps_keys_remain(setup_test_env):
    input_dir, output_dir, processed_dir, original = setup_test_env
    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / original.name
    cp = run_container(
        image=IMAGE_TAG,
        entrypoint="exiftool",
        mounts=["-v", f"{output_dir}:/photos/output"],
        args=["-j", f"/photos/output/{original.name}"],
        capture_output=True,
    )
    tags = json.loads(cp.stdout or "[]")[0]
    assert all(not k.lower().startswith("gps") for k in tags), \
        f"❌ Found GPS tag(s): {[k for k in tags if 'gps' in k.lower()]}"


def test_paranoia_no_gps_anywhere(setup_test_env):
    input_dir, output_dir, processed_dir, original = setup_test_env
    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / original.name
    lines = inspect_exif_container(scrubbed)
    offending = [line for line in lines if "gps" in line and not line.startswith("directory")]
    assert not offending, "❌ Paranoia check failed: 'gps' still present in EXIF output"
