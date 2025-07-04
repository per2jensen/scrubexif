# SPDX-License-Identifier: MIT
"""
Integration test that runs scrubexif in auto mode and verifies:
- Required EXIF tags are preserved
- GPS and serial number data are fully removed
"""

import subprocess
import shutil
import tempfile
import json
import os
from pathlib import Path

import pytest
from scrubexif.scrub import EXIF_TAGS_TO_KEEP as REQUIRED_TAGS

IMAGE_NAME = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
SAMPLE_IMAGE = ASSETS_DIR / "sample_with_exif.jpg"
EXIFTOOL = shutil.which("exiftool")


def find_tag(tags: dict, tag: str) -> str | None:
    """Return value of tag from any known EXIF/XMP/IPTC group."""
    return (
        tags.get(tag)
        or tags.get(f"XMP:{tag}")
        or tags.get(f"XMP-dc:{tag}")
        or tags.get(f"EXIF:{tag}")
        or tags.get(f"IPTC:{tag}")
    )


def run_scrubexif_container(input_dir, output_dir, processed_dir):
    user_flag = ["--user", str(os.getuid())] if os.getuid() != 0 else []
    return subprocess.run([
        "docker", "run", "--rm",
        *user_flag,
        "-v", f"{input_dir}:/photos/input",
        "-v", f"{output_dir}:/photos/output",
        "-v", f"{processed_dir}:/photos/processed",
        IMAGE_NAME, "--from-input",  "--log-level", "debug"
    ], capture_output=True, text=True)


@pytest.mark.skipif(not EXIFTOOL, reason="exiftool not installed")
def test_exif_sanitization_auto_mode():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        input_dir = base / "input"
        output_dir = base / "output"
        processed_dir = base / "processed"

        input_dir.mkdir()
        output_dir.mkdir()
        processed_dir.mkdir()

        dst = input_dir / SAMPLE_IMAGE.name
        shutil.copyfile(SAMPLE_IMAGE, dst)

        result = run_scrubexif_container(input_dir, output_dir, processed_dir)
        print(result.stdout)
        assert result.returncode == 0, f"Container failed: {result.stderr}"

        scrubbed = output_dir / SAMPLE_IMAGE.name
        assert scrubbed.exists(), f"❌ Output file not found: {scrubbed}"

        tags = json.loads(subprocess.check_output(
            [EXIFTOOL, "-j", str(scrubbed)], text=True
        ))[0]

        # ✅ Check that required tags are preserved
        for tag in REQUIRED_TAGS:
            assert find_tag(tags, tag), f"❌ Required tag missing: {tag}"

        # ❌ Ensure sensitive tags are fully removed
        keys_lower = [k.lower() for k in tags]
        assert not any("gps" in k for k in keys_lower), "❌ GPS tags should be removed"
        assert not any("serialnumber" in k for k in keys_lower), "❌ SerialNumber tag should be removed"
