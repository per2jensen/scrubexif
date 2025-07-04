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
from pathlib import Path

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
    shutil.copyfile(SAMPLE_IMAGE, input_dir / SCRUBBED_NAME)

    return input_dir, output_dir, processed_dir, input_dir / SCRUBBED_NAME


def run_scrubexif_container(input_dir, output_dir, processed_dir):
    result = subprocess.run([
        "docker", "run", "--rm",
        "--user", str(os.getuid()),
        "-v", f"{input_dir}:/photos/input",
        "-v", f"{output_dir}:/photos/output",
        "-v", f"{processed_dir}:/photos/processed",
        IMAGE_TAG, "--from-input",  "--log-level", "debug",
    ], capture_output=True, text=True)
    assert result.returncode == 0, f"Docker failed:\n{result.stderr}\n{result.stdout}"


def inspect_exif_host(file: Path) -> list[str]:
    result = subprocess.run(["exiftool", str(file)], capture_output=True, text=True)
    return result.stdout.lower().splitlines()


def inspect_exif_container(file: Path) -> list[str]:
    result = subprocess.run([
        "docker", "run", "--rm",
        "--user", str(os.getuid()),
        "-v", f"{file.parent}:/photos",
        IMAGE_TAG, "exiftool", f"/photos/{file.name}"
    ], capture_output=True, text=True)
    return result.stdout.lower().splitlines()


def assert_no_gps_tags(lines: list[str]):
    offending = [line for line in lines if "gps" in line and not line.startswith("directory")]
    assert not offending, f"❌ GPS tags still present:\n" + "\n".join(offending)


def test_sample_image_contains_gps_data():
    result = subprocess.run([
        "docker", "run", "--rm",
        "--entrypoint", "exiftool",
        "-v", f"{SAMPLE_IMAGE.parent}:/photos",
        IMAGE_TAG,
        f"/photos/{SAMPLE_IMAGE.name}"
    ], capture_output=True, text=True)
    assert "gps" in result.stdout.lower(), "❌ Expected GPS metadata not found in test image"


def test_gps_removed_and_exposure_retained(setup_test_env):
    input_dir, output_dir, processed_dir, _ = setup_test_env
    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / SCRUBBED_NAME
    lines = inspect_exif_host(scrubbed)
    assert_no_gps_tags(lines)
    assert any("exposure time" in line for line in lines), "❌ Missing ExposureTime in scrubbed file"


def test_output_file_has_no_gps_tag(setup_test_env):
    input_dir, output_dir, processed_dir, _ = setup_test_env
    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / SCRUBBED_NAME
    output = subprocess.run(["exiftool", str(scrubbed)], capture_output=True, text=True)
    assert "GPS Position" not in output.stdout, "❌ 'GPS Position' still present in scrubbed output"


def test_scrubbed_output_exists_and_is_jpeg(setup_test_env):
    input_dir, output_dir, processed_dir, _ = setup_test_env
    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / SCRUBBED_NAME
    assert scrubbed.exists()
    with open(scrubbed, "rb") as f:
        assert f.read(2) == b'\xff\xd8', "❌ Output is not a valid JPEG (missing SOI marker)"


def test_original_file_moved_to_processed(setup_test_env):
    input_dir, output_dir, processed_dir, original = setup_test_env
    run_scrubexif_container(input_dir, output_dir, processed_dir)

    assert not original.exists(), f"❌ Original file still in input/: {original}"
    assert (processed_dir / SCRUBBED_NAME).exists(), "❌ Processed original not found"


def test_no_gps_keys_remain(setup_test_env):
    input_dir, output_dir, processed_dir, _ = setup_test_env
    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / SCRUBBED_NAME
    result = subprocess.run([
        "docker", "run", "--rm",
        "--entrypoint", "exiftool",
        "--user", str(os.getuid()),
        "-v", f"{output_dir}:/photos/output",
        IMAGE_TAG,
        "-j", f"/photos/output/{SCRUBBED_NAME}"
    ], capture_output=True, text=True)
    tags = json.loads(result.stdout)[0]
    assert all(not k.lower().startswith("gps") for k in tags), \
        f"❌ Found GPS tag(s): {[k for k in tags if 'gps' in k.lower()]}"


def test_paranoia_no_gps_anywhere(setup_test_env):
    input_dir, output_dir, processed_dir, _ = setup_test_env
    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / SCRUBBED_NAME
    lines = inspect_exif_container(scrubbed)
    offending = [line for line in lines if "gps" in line and not line.startswith("directory")]
    assert not offending, "❌ Paranoia check failed: 'gps' still present in EXIF output"
