# SPDX-License-Identifier: MIT
import shutil
import pytest
import subprocess
import json
from pathlib import Path
from scrubexif.scrub import auto_scrub

ASSETS_DIR = Path(__file__).parent / "assets"
SAMPLE_FILES = [
    "sample_with_gps_exif.jpg",
#    "sample_with_gps_xmp.jpg",
#    "sample_with_gps_iptc.jpg",
]

@pytest.mark.parametrize("filename", SAMPLE_FILES)
def test_scrubber_removes_all_gps(filename, tmp_path, monkeypatch):
    """Ensure scrubber removes all GPS-related EXIF/XMP/IPTC metadata."""
    src = ASSETS_DIR / filename
    assert src.exists(), f"❌ Missing test asset: {src}"

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

    monkeypatch.setattr("scrubexif.scrub.INPUT_DIR", input_dir)
    monkeypatch.setattr("scrubexif.scrub.OUTPUT_DIR", output_dir)
    monkeypatch.setattr("scrubexif.scrub.PROCESSED_DIR", processed_dir)

    auto_scrub(dry_run=False, delete_original=False)

    scrubbed = output_dir / filename
    assert scrubbed.exists(), f"❌ Scrubbed output file not found: {scrubbed}"

    result = subprocess.run(["exiftool", "-j", str(scrubbed)], capture_output=True, text=True)
    tags = json.loads(result.stdout)[0]
    lower_keys = [k.lower() for k in tags]
    offending = [k for k in lower_keys if "gps" in k]

    assert not offending, f"❌ GPS tags still present: {offending}"


