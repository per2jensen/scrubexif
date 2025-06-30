# SPDX-License-Identifier: MIT
import subprocess
import shutil
import tempfile
import json
import sys
from pathlib import Path

import pytest

# Add the top-level project dir (where scrub.py lives) to PYTHONPATH
sys.path.append(str(Path(__file__).resolve().parents[1]))

from scrub import EXIF_TAGS_TO_KEEP as REQUIRED_TAGS

# === Configuration ===
IMAGE_NAME = "scrubexif:dev"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
SAMPLE_IMAGE = ASSETS_DIR / "sample_with_exif.jpg"
EXIFTOOL = shutil.which("exiftool")

@pytest.mark.skipif(not EXIFTOOL, reason="exiftool not installed")
def test_exif_sanitization_auto_mode():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        input_dir = tmpdir / "input"
        output_dir = tmpdir / "output"
        processed_dir = tmpdir / "processed"
        input_dir.mkdir()
        output_dir.mkdir()
        processed_dir.mkdir()

        # Copy the EXIF-heavy photo into input dir
        shutil.copyfile(SAMPLE_IMAGE, input_dir / SAMPLE_IMAGE.name)

        # Run the container
        result = subprocess.run([
            "docker", "run", "--rm",
            "-v", f"{input_dir}:/photos/input",
            "-v", f"{output_dir}:/photos/output",
            "-v", f"{processed_dir}:/photos/processed",
            IMAGE_NAME, "--from-input"
        ], capture_output=True, text=True)

        print(result.stdout)
        assert result.returncode == 0

        scrubbed = output_dir / SAMPLE_IMAGE.name
        assert scrubbed.exists(), "Scrubbed file not found in output"

        # Extract EXIF tags as structured JSON
        exif_json = subprocess.check_output(
            [EXIFTOOL, "-j", str(scrubbed)],
            text=True
        )
        tags = json.loads(exif_json)[0]

        # Assert all required tags are present
        for tag in REQUIRED_TAGS:
            assert tag in tags, f"Expected tag '{tag}' missing"

        # Assert sensitive tags are removed
        lower_keys = [k.lower() for k in tags]
        assert not any("gps" in k for k in lower_keys), "GPS tag should be removed"
        assert not any("serialnumber" in k for k in lower_keys), "SerialNumber tag should be removed"
