# SPDX-License-Identifier: MIT
import json
import shutil
import subprocess
import pytest
from pathlib import Path
from scrubexif.scrub import auto_scrub

ASSETS_DIR = Path(__file__).parent / "assets"
SAMPLE_IMAGE = ASSETS_DIR / "sample_with_exif.jpg"
SCRUBBED_NAME = SAMPLE_IMAGE.name


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


def read_exif_output(path):
    result = subprocess.run(["exiftool", str(path)], capture_output=True, text=True)
    return result.stdout.lower().splitlines()


def test_gps_removed_and_exposure_retained(setup_test_env, monkeypatch):
    input_dir, output_dir, processed_dir, original = setup_test_env

    monkeypatch.setattr("scrubexif.scrub.INPUT_DIR", input_dir)
    monkeypatch.setattr("scrubexif.scrub.OUTPUT_DIR", output_dir)
    monkeypatch.setattr("scrubexif.scrub.PROCESSED_DIR", processed_dir)

    auto_scrub(dry_run=False, delete_original=False)

    scrubbed = output_dir / SCRUBBED_NAME
    exif_lines = read_exif_output(scrubbed)

    gps_tags = ["gps latitude", "gps longitude", "gps position", "gps alt", "gps date", "gps time"]
    assert not any(any(tag in line for tag in gps_tags) for line in exif_lines), "GPS metadata should be removed"
    assert any("exposure time" in line for line in exif_lines), "ExposureTime tag should be retained"


def test_output_file_has_no_gps_tag(setup_test_env, monkeypatch):
    input_dir, output_dir, processed_dir, original = setup_test_env

    monkeypatch.setattr("scrubexif.scrub.INPUT_DIR", input_dir)
    monkeypatch.setattr("scrubexif.scrub.OUTPUT_DIR", output_dir)
    monkeypatch.setattr("scrubexif.scrub.PROCESSED_DIR", processed_dir)

    auto_scrub(dry_run=False, delete_original=False)

    scrubbed = output_dir / SCRUBBED_NAME
    output = subprocess.run(["exiftool", str(scrubbed)], capture_output=True, text=True)
    assert "GPS Position" not in output.stdout


def test_scrubbed_output_exists_and_is_jpeg(setup_test_env, monkeypatch):
    input_dir, output_dir, processed_dir, original = setup_test_env

    monkeypatch.setattr("scrubexif.scrub.INPUT_DIR", input_dir)
    monkeypatch.setattr("scrubexif.scrub.OUTPUT_DIR", output_dir)
    monkeypatch.setattr("scrubexif.scrub.PROCESSED_DIR", processed_dir)

    auto_scrub(dry_run=False, delete_original=False)

    scrubbed = output_dir / SCRUBBED_NAME
    assert scrubbed.exists()
    with open(scrubbed, "rb") as f:
        assert f.read(2) == b'\xff\xd8'  # JPEG SOI marker


def test_original_file_moved_to_processed(setup_test_env, monkeypatch):
    input_dir, output_dir, processed_dir, original = setup_test_env

    monkeypatch.setattr("scrubexif.scrub.INPUT_DIR", input_dir)
    monkeypatch.setattr("scrubexif.scrub.OUTPUT_DIR", output_dir)
    monkeypatch.setattr("scrubexif.scrub.PROCESSED_DIR", processed_dir)

    auto_scrub(dry_run=False, delete_original=False)

    assert not original.exists()
    assert (processed_dir / SCRUBBED_NAME).exists()


def test_sample_image_contains_gps_data():
    result = subprocess.run(["exiftool", str(SAMPLE_IMAGE)], capture_output=True, text=True)
    output = result.stdout.lower()
    assert "gps" in output, "Expected GPS metadata not found in test image"


def test_no_gps_keys_remain(setup_test_env, monkeypatch):
    input_dir, output_dir, processed_dir, original = setup_test_env

    monkeypatch.setattr("scrubexif.scrub.INPUT_DIR", input_dir)
    monkeypatch.setattr("scrubexif.scrub.OUTPUT_DIR", output_dir)
    monkeypatch.setattr("scrubexif.scrub.PROCESSED_DIR", processed_dir)

    auto_scrub(dry_run=False, delete_original=False)

    scrubbed = output_dir / SCRUBBED_NAME
    result = subprocess.run(["exiftool", "-j", str(scrubbed)], capture_output=True, text=True)
    tags = json.loads(result.stdout)[0]
    assert all(not key.lower().startswith("gps") for key in tags), f"GPS tag found: {[k for k in tags if 'gps' in k.lower()]}"


def test_paranoia_no_gps_anywhere(setup_test_env, monkeypatch):
    input_dir, output_dir, processed_dir, original = setup_test_env

    monkeypatch.setattr("scrubexif.scrub.INPUT_DIR", input_dir)
    monkeypatch.setattr("scrubexif.scrub.OUTPUT_DIR", output_dir)
    monkeypatch.setattr("scrubexif.scrub.PROCESSED_DIR", processed_dir)

    auto_scrub(dry_run=False, delete_original=False)

    scrubbed = output_dir / SCRUBBED_NAME
    result = subprocess.run(["exiftool", str(scrubbed)], capture_output=True, text=True)
    lines = result.stdout.splitlines()

    offending = [
        line for line in lines
        if "gps" in line.lower() and not line.lower().startswith("directory")
    ]
    assert not offending, f"Paranoia check failed: Found 'gps' in EXIF output:\n" + "\n".join(offending)
