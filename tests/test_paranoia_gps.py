# SPDX-License-Identifier: GPL-3.0-or-later
"""
Integration test for `scrubexif` Docker container in auto mode.

This test verifies that GPS-related metadata (EXIF, XMP, IPTC) is properly
removed from JPEG files when using the containerized tool.

✅ It:
- Runs `scrubexif` inside a Docker container using `--from-input` mode
- Mounts isolated temp dirs as /photos/input, /photos/output, /photos/processed
- Accepts image tag via SCRUBEXIF_IMAGE env var (defaults to `scrubexif:dev`)
- Uses `exiftool -j` to confirm GPS-related keys are removed

Example:

    SCRUBEXIF_IMAGE=per2jensen/scrubexif:0.5.2 pytest tests/test_scrubber_removes_all_gps.py
"""

import os
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS_DIR = Path(__file__).parent / "assets"
SAMPLE_FILES = [
    "sample_with_gps_exif.jpg",
    # Add more here later: "sample_with_gps_xmp.jpg", ...
]
IMAGE_TAG = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")


def run_scrubexif(input_dir: Path, output_dir: Path, processed_dir: Path):
    """Run the container with given mounted directories."""
    result = subprocess.run([
        "docker", "run", "--rm",
        "--user", str(os.getuid()),
        "-v", f"{input_dir}:/photos/input",
        "-v", f"{output_dir}:/photos/output",
        "-v", f"{processed_dir}:/photos/processed",
        IMAGE_TAG, "--from-input"
    ], capture_output=True, text=True)
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, f"❌ Container failed: {result.stderr}"


def load_exif_json(image: Path) -> dict:
    """Return EXIF tags from image as lowercase key dict."""
    raw = subprocess.check_output(["exiftool", "-j", str(image)], text=True)
    return {k.lower(): v for k, v in json.loads(raw)[0].items()}



@pytest.mark.smoke
@pytest.mark.parametrize("filename", SAMPLE_FILES)
def test_scrubber_removes_all_gps(filename, tmp_path):
    """Ensure GPS-related metadata is removed from image in containerized scrub."""

    src = ASSETS_DIR / filename
    assert src.exists(), f"❌ Test asset not found: {src}"

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    input_dir.mkdir()
    output_dir.mkdir()
    processed_dir.mkdir()

    dst = input_dir / filename
    shutil.copy(src, dst)

    print(f"📂 Using input file: {dst}")
    run_scrubexif(input_dir, output_dir, processed_dir)

    scrubbed = output_dir / filename
    assert scrubbed.exists(), f"❌ Scrubbed file not found in output/: {scrubbed}"

    tags = load_exif_json(scrubbed)
    gps_keys = [k for k in tags if "gps" in k]

    assert not gps_keys, f"❌ GPS tags still present: {gps_keys}"
