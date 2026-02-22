# SPDX-License-Identifier: GPL-3.0-or-later
"""
Integration tests for metadata stamping and section scrubbing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scrubexif.scrub import MAX_COMMENT_BYTES, MAX_COPYRIGHT_BYTES
from tests._docker import mk_mounts, run_container

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
SAMPLE_IMAGE = ASSETS_DIR / "sample_with_exif.jpg"
EXIFTOOL = shutil.which("exiftool")


def load_exif_json(path: Path) -> dict:
    raw = subprocess.check_output([EXIFTOOL, "-j", "-G1", str(path)], text=True)
    return json.loads(raw)[0]


def get_tag(tags: dict, *keys: str) -> str | None:
    keys_lower = {k.lower() for k in keys}
    for key, value in tags.items():
        if key.lower() in keys_lower:
            return value
    return None


def truncate_utf8(value: str, max_bytes: int) -> str:
    data = value.encode("utf-8")
    if len(data) <= max_bytes:
        return value
    truncated = data[:max_bytes]
    while truncated and (truncated[-1] & 0xC0) == 0x80:
        truncated = truncated[:-1]
    return truncated.decode("utf-8", errors="ignore")


@pytest.mark.skipif(not EXIFTOOL, reason="exiftool not installed")
def test_comment_and_copyright_stamped_and_truncated(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    for d in (input_dir, output_dir, processed_dir):
        d.mkdir(parents=True, exist_ok=True)

    assert SAMPLE_IMAGE.exists(), f"Missing test image: {SAMPLE_IMAGE}"
    src = input_dir / SAMPLE_IMAGE.name
    shutil.copyfile(SAMPLE_IMAGE, src)

    # Seed existing tags that should be removed/replaced.
    seed_cmd = [
        EXIFTOOL,
        "-overwrite_original",
        "-EXIF:Copyright=Old Copyright",
        "-XMP-dc:Rights=Old Copyright",
        "-EXIF:UserComment=Old Comment",
        "-XMP-dc:Description=Old Comment",
        "-XMP:History=Old History",
        "-EXIF:LensSerialNumber=Lens123",
        "-EXIF:OwnerName=Owner",
        "-EXIF:ImageUniqueID=ABCDEF",
        "-Comment=Old JPEG Comment",
        str(src),
    ]
    subprocess.run(seed_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    long_copyright = "C" * (MAX_COPYRIGHT_BYTES + 20)
    long_comment = "M" * (MAX_COMMENT_BYTES + 20)
    expected_copyright = truncate_utf8(long_copyright, MAX_COPYRIGHT_BYTES)
    expected_comment = truncate_utf8(long_comment, MAX_COMMENT_BYTES)

    mounts = mk_mounts(input_dir, output_dir, processed_dir)
    result = run_container(
        mounts=mounts,
        args=[
            "--from-input",
            "--copyright", long_copyright,
            "--comment", long_comment,
        ],
        capture_output=True,
    )
    assert result.returncode == 0, f"Container failed:\n{result.stderr}\n{result.stdout}"

    combined = (result.stdout or "") + (result.stderr or "")
    assert "truncating" in combined.lower(), "Expected truncation warning in logs"

    scrubbed = output_dir / SAMPLE_IMAGE.name
    assert scrubbed.exists(), f"Scrubbed file not found: {scrubbed}"

    tags = load_exif_json(scrubbed)
    assert get_tag(tags, "IFD0:Copyright", "EXIF:Copyright") == expected_copyright
    assert get_tag(tags, "XMP-dc:Rights", "XMP:Rights") == expected_copyright
    assert get_tag(tags, "ExifIFD:UserComment", "EXIF:UserComment") == expected_comment
    assert get_tag(tags, "XMP-dc:Description", "XMP:Description") == expected_comment

    # Ensure disallowed sections/tags are scrubbed.
    assert "File:Comment" not in tags, "JPEG Comment should be removed"
    assert not any(k.startswith("Photoshop:") for k in tags), "Photoshop section should be removed"
    assert not any(k.startswith("Comment:") for k in tags), "Comment section should be removed"
    assert not any(k.startswith("MakerNotes:") for k in tags), "MakerNotes section should be removed"
    assert not any(k.lower().startswith("xmp:history") for k in tags), "XMP:History should be removed"
    assert get_tag(tags, "ExifIFD:LensSerialNumber", "EXIF:LensSerialNumber") is None
    assert get_tag(tags, "ExifIFD:OwnerName", "EXIF:OwnerName") is None
    assert get_tag(tags, "ExifIFD:ImageUniqueID", "EXIF:ImageUniqueID") is None

    # ICC profile data should be preserved for accurate color.
    assert any(k.startswith("ICC_Profile:") or k.startswith("ICC-header:") for k in tags), \
        "Expected ICC profile metadata to be preserved"
