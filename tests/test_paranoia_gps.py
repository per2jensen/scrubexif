# SPDX-License-Identifier: GPL-3.0-or-later
import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("filename", [
    "sample_with_gps_exif.jpg",
    "sample_with_gps_xmp.jpg",
    "sample_with_gps_iptc.jpg",
])
def test_scrubber_removes_all_gps(filename, tmp_path, monkeypatch):
    """Ensure scrubber removes all GPS-related EXIF/XMP/IPTC metadata."""
    from scrubexif.scrub import auto_scrub

    src = Path("tests/assets") / filename

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    input_dir.mkdir()
    output_dir.mkdir()
    processed_dir.mkdir()
    shutil.copy(src, input_dir / "test.jpg")

    monkeypatch.setattr("scrubexif.scrub.INPUT_DIR", input_dir)
    monkeypatch.setattr("scrubexif.scrub.OUTPUT_DIR", output_dir)
    monkeypatch.setattr("scrubexif.scrub.PROCESSED_DIR", processed_dir)

    auto_scrub(dry_run=False, delete_original=False)

    result = subprocess.run(
        ["exiftool", "-a", "-j", str(output_dir / "test.jpg")],
        capture_output=True, text=True,
        check=True,
    )
    tags = json.loads(result.stdout)[0]
    offending = [k for k in tags if "gps" in k.lower()]
    assert not offending, f"❌ GPS tags not fully removed:\n" + "\n".join(offending)
