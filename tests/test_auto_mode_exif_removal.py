# tests/test_auto_mode_exif_removal.py
# SPDX-License-Identifier: MIT
"""
Integration test that runs scrubexif in auto mode and verifies:
- Required EXIF tags are preserved
- GPS and serial number data are fully removed
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from scrubexif.scrub import EXIF_TAGS_TO_KEEP as REQUIRED_TAGS

# Centralized docker helpers (tmpfs + envs + user flag)
from tests._docker import mk_mounts, run_container

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


def run_scrubexif_container(input_dir: Path, output_dir: Path, processed_dir: Path) -> subprocess.CompletedProcess:
    mounts = mk_mounts(input_dir, output_dir, processed_dir)
    return run_container(
        mounts=mounts,
        args=["--from-input", "--log-level", "debug"],
        capture_output=True,
    )


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

        assert SAMPLE_IMAGE.exists(), f"Missing test image: {SAMPLE_IMAGE}"
        dst = input_dir / SAMPLE_IMAGE.name
        shutil.copyfile(SAMPLE_IMAGE, dst)

        result = run_scrubexif_container(input_dir, output_dir, processed_dir)
        # Always print for helpful CI logs
        print(result.stdout)
        print(result.stderr)
        assert result.returncode == 0, f"Container failed:\n{result.stderr}\n{result.stdout}"

        scrubbed = output_dir / SAMPLE_IMAGE.name
        assert scrubbed.exists(), f"❌ Output file not found: {scrubbed}"

        tags = json.loads(
            subprocess.check_output([EXIFTOOL, "-j", str(scrubbed)], text=True)
        )[0]

        # ✅ Check that required tags are preserved
        for tag in REQUIRED_TAGS:
            assert find_tag(tags, tag), f"❌ Required tag missing: {tag}"

        # ❌ Ensure sensitive tags are fully removed
        keys_lower = [k.lower() for k in tags]
        assert not any("gps" in k for k in keys_lower), "❌ GPS tags should be removed"
        assert not any("serialnumber" in k for k in keys_lower), "❌ SerialNumber tag should be removed"


@pytest.mark.skipif(not EXIFTOOL, reason="exiftool not installed")
def test_bulk_auto_mode_scrubs_all_metadata(tmp_path):
    """Ensure bulk auto-mode scrubs EXIF, XMP, IPTC, and GPS from many files."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    for d in (input_dir, output_dir, processed_dir):
        d.mkdir(parents=True, exist_ok=True)

    total = 50
    for idx in range(total):
        target = input_dir / f"bulk_{idx:02d}.jpg"
        shutil.copyfile(SAMPLE_IMAGE, target)
        lat = 55.0 + idx * 0.01
        lon = 12.0 + idx * 0.01
        meta_cmd = [
            EXIFTOOL,
            "-overwrite_original",
            f"-EXIF:Artist=Photographer-{idx}",
            f"-GPSLatitude={lat}",
            "-GPSLatitudeRef=N",
            f"-GPSLongitude={lon}",
            "-GPSLongitudeRef=E",
            f"-XMP:Subject=Secret-{idx}",
            f"-IPTC:Keywords=Confidential-{idx}",
            str(target),
        ]
        subprocess.run(meta_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    result = run_scrubexif_container(input_dir, output_dir, processed_dir)
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, f"Container failed:\n{result.stderr}\n{result.stdout}"

    outputs = sorted(output_dir.glob("*.jpg"))
    assert len(outputs) == total, f"Expected {total} scrubbed files, found {len(outputs)}"
    assert len(list(processed_dir.glob('*.jpg'))) == total, "Originals should be moved to processed/"

    for file in outputs:
        tags = json.loads(subprocess.check_output([EXIFTOOL, "-j", str(file)], text=True))[0]
        keys_lower = {k.lower() for k in tags}
        assert not any("gps" in key for key in keys_lower), f"GPS tag leaked in {file.name}"
        assert "xmp:subject" not in keys_lower, f"XMP Subject present in {file.name}"
        assert "iptc:keywords" not in keys_lower, f"IPTC Keywords present in {file.name}"
