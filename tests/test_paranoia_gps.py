# SPDX-License-Identifier: MIT
"""
Integration test for `scrubexif` Docker container in auto mode.

This test verifies that GPS-related metadata (EXIF, XMP, IPTC) is properly
removed from JPEG files when using the containerized tool.

✅ It:
- Runs `scrubexif` inside a Docker container using `--from-input` mode
- Mounts isolated temp dirs as /photos/input, /photos/output, /photos/processed
- Accepts a specific image tag via the SCRUBEXIF_IMAGE_TAG env var (defaults to `:dev`)
- Parses output with `exiftool -j` to confirm GPS-related keys are fully removed

Run with a specific image tag like this:

    SCRUBEXIF_IMAGE_TAG=per2jensen/scrubexif:0.5.2 pytest tests/test_scrubber_removes_all_gps.py

"""



import shutil
import subprocess
import json
import os
from pathlib import Path
import pytest

ASSETS_DIR = Path(__file__).parent / "assets"
SAMPLE_FILES = [
    "sample_with_gps_exif.jpg",
    # "sample_with_gps_xmp.jpg",
    # "sample_with_gps_iptc.jpg",
]

IMAGE_NAME = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")


@pytest.mark.parametrize("filename", SAMPLE_FILES)
def test_scrubber_removes_all_gps(filename, tmp_path):
    """Ensure scrubber removes all GPS-related EXIF/XMP/IPTC metadata via container."""

    src = ASSETS_DIR / filename
    assert src.exists(), f"❌ Missing test asset: {src}"

    # Set up temporary container mount points
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    input_dir.mkdir()
    output_dir.mkdir()
    processed_dir.mkdir()

    dst = input_dir / filename
    shutil.copy(src, dst)
    assert dst.exists(), f"❌ test file not copied to input/: {dst}"
    print(f"📂 Using input file: {dst}")

    # Run container using the scrubexif image
    result = subprocess.run([
        "docker", "run", "--rm",
        "-v", f"{input_dir}:/photos/input",
        "-v", f"{output_dir}:/photos/output",
        "-v", f"{processed_dir}:/photos/processed",
        IMAGE_NAME, "--from-input"
    ], capture_output=True, text=True)

    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, f"❌ Container exited with non-zero status: {result.stderr}"

    scrubbed = output_dir / filename
    assert scrubbed.exists(), f"❌ Scrubbed output file not found: {scrubbed}"

    # Analyze EXIF tags
    exif_json = subprocess.check_output(["exiftool", "-j", str(scrubbed)], text=True)
    tags = json.loads(exif_json)[0]
    lower_keys = [k.lower() for k in tags]
    offending = [k for k in lower_keys if "gps" in k]

    assert not offending, f"❌ GPS tags still present: {offending}"
