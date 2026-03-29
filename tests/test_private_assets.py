# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests against real camera files in tests/private-assets/.

These tests are excluded from the default pytest run (see pytest.ini).
Run explicitly with:

    pytest -m private
    pytest -m private -v

Each file in tests/private-assets/ is tested automatically — dropping a new
JPEG into that directory adds it to all four test cases without any code change.

Four layers of verification per file:

1. Paranoia — binary parser confirms only APP0 survives jpegtran -copy none.
2. Normal strip — GPS, IPTC, XMP, and MakerNotes are absent after the pipeline.
3. Normal preserve — whitelist tags (ExposureTime, FNumber, FocalLength, ISO,
   Orientation) survive the round-trip where present in the original.
4. Lossless — decoded pixel data is byte-for-byte identical before and after
   (requires Pillow, installed via the [test] extra in pyproject.toml).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scrubexif.scrub import _do_scrub_pipeline, run_jpegtran

PRIVATE_ASSETS_DIR = Path(__file__).resolve().parent / "private-assets"
EXIFTOOL = shutil.which("exiftool")
JPEGTRAN = shutil.which("jpegtran")

skipif_no_jpegtran = pytest.mark.skipif(not JPEGTRAN, reason="jpegtran not installed")
skipif_no_exiftool = pytest.mark.skipif(not EXIFTOOL, reason="exiftool not installed")


def _private_jpegs() -> list[Path]:
    """Return all JPEG files found in tests/private-assets/."""
    if not PRIVATE_ASSETS_DIR.exists():
        return []
    return sorted(
        p for p in PRIVATE_ASSETS_DIR.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg"} and p.is_file()
    )


def _get_jpeg_app_markers(path: Path) -> set[int]:
    """
    Parse the raw JPEG marker stream and return the set of APP marker numbers
    (0–15) present in the file.

    Args:
        path: Path to the JPEG file.

    Returns:
        Set of APP marker numbers found.

    Raises:
        ValueError: If the file does not start with the JPEG SOI marker.
    """
    data = path.read_bytes()
    if data[:2] != b"\xff\xd8":
        raise ValueError(f"Not a JPEG file: {path}")
    markers: set[int] = set()
    i = 2
    while i + 3 < len(data):
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker in (0xD9, 0xDA):
            break
        if 0xE0 <= marker <= 0xEF:
            markers.add(marker - 0xE0)
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
        else:
            segment_length = int.from_bytes(data[i + 2:i + 4], "big")
            i += 2 + segment_length
    return markers


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.private
@skipif_no_jpegtran
@pytest.mark.parametrize("jpeg_path", _private_jpegs(), ids=lambda p: p.name)
def test_paranoia_only_app0_survives(jpeg_path: Path, tmp_path: Path) -> None:
    """
    Paranoia mode: binary parser must confirm only APP0 remains after
    jpegtran -copy none on a real camera file.

    Args:
        jpeg_path: Path to the real camera JPEG under test.
        tmp_path: pytest-provided temporary directory.
    """
    out = tmp_path / jpeg_path.name
    run_jpegtran(jpeg_path, out)
    markers = _get_jpeg_app_markers(out)
    assert markers == {0}, (
        f"{jpeg_path.name}: expected only APP0 after paranoia strip, "
        f"got APP markers: {markers}"
    )


@pytest.mark.private
@skipif_no_jpegtran
@skipif_no_exiftool
@pytest.mark.parametrize("jpeg_path", _private_jpegs(), ids=lambda p: p.name)
def test_normal_strips_gps_iptc_xmp_makernotes(jpeg_path: Path, tmp_path: Path) -> None:
    """
    Normal mode: GPS, IPTC, XMP, and MakerNotes must be absent from the output.

    Args:
        jpeg_path: Path to the real camera JPEG under test.
        tmp_path: pytest-provided temporary directory.
    """
    out = tmp_path / jpeg_path.name
    _do_scrub_pipeline(jpeg_path, out, paranoia=False,
                       copyright_text=None, comment_text=None)

    tags = json.loads(
        subprocess.check_output(["exiftool", "-j", "-G1", str(out)], text=True)
    )[0]
    keys = list(tags.keys())

    assert not any("GPS" in k for k in keys), \
        f"{jpeg_path.name}: GPS survived — {[k for k in keys if 'GPS' in k]}"
    assert not any(k.startswith("IPTC") for k in keys), \
        f"{jpeg_path.name}: IPTC survived"
    assert not any(k.startswith("XMP") for k in keys), \
        f"{jpeg_path.name}: XMP survived"
    assert not any(k.startswith("MakerNotes") for k in keys), \
        f"{jpeg_path.name}: MakerNotes survived"


@pytest.mark.private
@skipif_no_jpegtran
@skipif_no_exiftool
@pytest.mark.parametrize("jpeg_path", _private_jpegs(), ids=lambda p: p.name)
def test_normal_preserves_whitelist_tags(jpeg_path: Path, tmp_path: Path) -> None:
    """
    Normal mode: whitelist tags present in the original must survive the
    round-trip.  Tags absent from the source are skipped without failure.

    Args:
        jpeg_path: Path to the real camera JPEG under test.
        tmp_path: pytest-provided temporary directory.
    """
    WHITELIST = {"exposuretime", "fnumber", "focallength", "iso", "orientation"}

    def present_tags(path: Path) -> set[str]:
        data = json.loads(
            subprocess.check_output(["exiftool", "-j", str(path)], text=True)
        )[0]
        return {k.lower() for k in data} & WHITELIST

    tags_before = present_tags(jpeg_path)
    if not tags_before:
        pytest.skip(f"{jpeg_path.name}: no whitelist tags found in source")

    out = tmp_path / jpeg_path.name
    _do_scrub_pipeline(jpeg_path, out, paranoia=False,
                       copyright_text=None, comment_text=None)

    tags_after = present_tags(out)
    missing = tags_before - tags_after
    assert not missing, (
        f"{jpeg_path.name}: whitelist tags lost after scrub: {missing}"
    )


@pytest.mark.private
@skipif_no_jpegtran
@pytest.mark.parametrize("jpeg_path", _private_jpegs(), ids=lambda p: p.name)
def test_lossless_pixel_preservation(jpeg_path: Path, tmp_path: Path) -> None:
    """
    Decoded pixel data must be byte-for-byte identical before and after
    jpegtran -copy none (lossless transform).  Requires Pillow.

    Args:
        jpeg_path: Path to the real camera JPEG under test.
        tmp_path: pytest-provided temporary directory.
    """
    Image = pytest.importorskip("PIL.Image")

    out = tmp_path / jpeg_path.name
    run_jpegtran(jpeg_path, out)

    assert Image.open(jpeg_path).tobytes() == Image.open(out).tobytes(), (
        f"{jpeg_path.name}: pixel data changed after jpegtran strip — "
        "transform is not lossless"
    )
