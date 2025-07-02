# SPDX-License-Identifier: MIT
"""
Integration test that runs the actual Docker container on sample images.

✔ Verifies GPS metadata is stripped
✔ Verifies ExposureTime (and other key tags) are retained

To test a specific image version (e.g., :0.5.2), set environment variable:
    export SCRUBEXIF_IMAGE=per2jensen/scrubexif:0.5.2
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
        "-v", f"{input_dir}:/photos/input",
        "-v", f"{output_dir}:/photos/output",
        "-v", f"{processed_dir}:/photos/processed",
        IMAGE_TAG, "--from-input"
    ], capture_output=True, text=True)

    assert result.returncode == 0, f"Docker failed: {result.stderr}"


def test_gps_removed_and_exposure_retained(setup_test_env):
    input_dir, output_dir, processed_dir, original = setup_test_env
    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / SCRUBBED_NAME
    assert scrubbed.exists()

    result = subprocess.run(["exiftool", str(scrubbed)], capture_output=True, text=True)
    output = result.stdout.lower()
    lines = output.splitlines()

    offending = [
        line for line in lines
        if "gps" in line and not line.startswith("directory")
    ]

    assert not offending, f"GPS tags still present:\n" + "\n".join(offending)
    assert any("exposure time" in line for line in lines), "Missing ExposureTime"


def test_output_file_has_no_gps_tag(setup_test_env):
    input_dir, output_dir, processed_dir, original = setup_test_env

    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / SCRUBBED_NAME
    output = subprocess.run(["exiftool", str(scrubbed)], capture_output=True, text=True)
    assert "GPS Position" not in output.stdout, "❌ 'GPS Position' still present in scrubbed output"


def test_scrubbed_output_exists_and_is_jpeg(setup_test_env):
    input_dir, output_dir, processed_dir, original = setup_test_env

    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / SCRUBBED_NAME
    assert scrubbed.exists(), f"❌ Scrubbed file does not exist: {scrubbed}"
    with open(scrubbed, "rb") as f:
        assert f.read(2) == b'\xff\xd8', "❌ Output file is not a valid JPEG (missing SOI marker)"



def test_original_file_moved_to_processed(setup_test_env):
    input_dir, output_dir, processed_dir, original = setup_test_env

    run_scrubexif_container(input_dir, output_dir, processed_dir)

    assert not original.exists(), f"❌ Original file still exists: {original}"
    assert (processed_dir / SCRUBBED_NAME).exists(), f"❌ Scrubbed original not found in: {processed_dir}"


def test_sample_image_contains_gps_data():
    assert SAMPLE_IMAGE.exists(), f"❌ Missing test image: {SAMPLE_IMAGE}"


    result = subprocess.run([
        "docker", "run", "--rm",
        "--entrypoint", "exiftool",
        "-v", f"{SAMPLE_IMAGE.parent}:/photos",
        IMAGE_TAG,
        f"/photos/{SAMPLE_IMAGE.name}"
    ], capture_output=True, text=True)


    output = result.stdout.lower()
    assert "gps" in output, "Expected GPS metadata not found in test image"



def read_exif_output(path: Path) -> list[str]:
    """Reads EXIF output from inside the container using exiftool."""
    assert path.exists(), f"❌ File does not exist: {path}"

    result = subprocess.run([
        "docker", "run", "--rm",
        "-v", f"{path.parent}:/photos",
        IMAGE_TAG,
        "exiftool", f"/photos/{path.name}"
    ], capture_output=True, text=True)

    return result.stdout.lower().splitlines()


def test_no_gps_keys_remain(setup_test_env):
    input_dir, output_dir, processed_dir, original = setup_test_env

    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / SCRUBBED_NAME
    assert scrubbed.exists(), "❌ Scrubbed output file not found"

    result = subprocess.run([
        "docker", "run", "--rm",
        "--entrypoint", "exiftool",
        "-v", f"{output_dir}:/photos/output",
        IMAGE_TAG,
        "-j", f"/photos/output/{SCRUBBED_NAME}"
    ], capture_output=True, text=True)

    tags = json.loads(result.stdout)[0]
    assert all(not key.lower().startswith("gps") for key in tags), \
        f"❌ GPS tag found: {[k for k in tags if 'gps' in k.lower()]}"


def test_paranoia_no_gps_anywhere(setup_test_env):
    input_dir, output_dir, processed_dir, original = setup_test_env

    run_scrubexif_container(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / SCRUBBED_NAME
    assert scrubbed.exists(), "❌ Scrubbed output file not found"

    result = subprocess.run([
        "docker", "run", "--rm",
        "--entrypoint", "exiftool",
        "-v", f"{output_dir}:/photos/output",
        IMAGE_TAG,
        f"/photos/output/{SCRUBBED_NAME}"
    ], capture_output=True, text=True)

    lines = result.stdout.splitlines()

    offending = [
        line for line in lines
        if "gps" in line.lower() and not line.lower().startswith("directory")
    ]
    assert not offending, (
        "Paranoia check failed: Found 'gps' in EXIF output:\n" +
        "\n".join(offending)
    )
