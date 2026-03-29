# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests proving that jpegtran -copy none correctly strips JPEG APP segments.

Three layers of verification are used:

1. Binary parser — reads the raw JPEG marker stream and asserts which APP
   segment numbers (0–15) are present.  This is independent of exiftool and
   is the only way to detect unknown/proprietary segments that exiftool does
   not recognise.

2. exiftool — confirms that named metadata groups (EXIF, GPS, XMP, IPTC, ICC)
   are absent or present as expected.

3. Pillow pixel comparison — proves the image data is losslessly preserved.
   Pillow is a test-only dependency (see pyproject.toml [test] extras).
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from scrubexif.scrub import (
    _do_scrub_pipeline,
    check_jpegtran,
    run_jpegtran,
)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
SAMPLE_IMAGE = ASSETS_DIR / "sample_with_exif.jpg"

EXIFTOOL = shutil.which("exiftool")
JPEGTRAN = shutil.which("jpegtran")

skipif_no_jpegtran = pytest.mark.skipif(not JPEGTRAN, reason="jpegtran not installed")
skipif_no_exiftool = pytest.mark.skipif(not EXIFTOOL, reason="exiftool not installed")


# ---------------------------------------------------------------------------
# JPEG binary helpers
# ---------------------------------------------------------------------------

def _get_jpeg_app_markers(path: Path) -> set[int]:
    """
    Parse the raw JPEG marker stream and return the set of APP marker numbers
    (integers 0–15) present in the file.

    APP0 = 0  (JFIF header)
    APP1 = 1  (EXIF / XMP)
    APP2 = 2  (ICC profile / MPF)
    APP3–15   (various proprietary uses)

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
        if marker in (0xD9, 0xDA):      # EOI / SOS — image data follows
            break
        if 0xE0 <= marker <= 0xEF:      # APP0–APP15
            markers.add(marker - 0xE0)
        # Standalone markers have no length field; all others do.
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
        else:
            segment_length = int.from_bytes(data[i + 2:i + 4], "big")
            i += 2 + segment_length
    return markers


def _inject_app_segment(src: Path, dst: Path, app_n: int, payload: bytes) -> None:
    """
    Copy src to dst and inject a synthetic APPn segment immediately after the
    JPEG SOI marker (FF D8).

    This simulates proprietary camera extensions that occupy APP3–APP15 and
    are invisible to exiftool but detectable via binary parsing.

    Args:
        src: Source JPEG file.
        dst: Destination path for the modified JPEG.
        app_n: Marker number 0–15 (the n in APPn).
        payload: Raw body bytes; the two-byte length field is computed
                 automatically.

    Raises:
        ValueError: If src does not start with the JPEG SOI marker.
    """
    data = src.read_bytes()
    if data[:2] != b"\xff\xd8":
        raise ValueError(f"Source is not a JPEG: {src}")
    marker_byte = 0xE0 + app_n
    # Length field includes itself (2 bytes) but not the FF/marker bytes.
    length = struct.pack(">H", 2 + len(payload))
    segment = bytes([0xFF, marker_byte]) + length + payload
    # Inject right after SOI so it sits before any existing APP segments.
    dst.write_bytes(data[:2] + segment + data[2:])


# ---------------------------------------------------------------------------
# Tests: check_jpegtran()
# ---------------------------------------------------------------------------

def test_check_jpegtran_passes_when_present(monkeypatch):
    """check_jpegtran() must not exit when jpegtran is on PATH."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/jpegtran" if name == "jpegtran" else None)
    # Should complete without raising SystemExit.
    check_jpegtran()


def test_check_jpegtran_exits_when_missing(monkeypatch):
    """check_jpegtran() must call sys.exit when jpegtran is absent."""
    monkeypatch.setattr("scrubexif.scrub.shutil.which", lambda _: None)
    with pytest.raises(SystemExit):
        check_jpegtran()


# ---------------------------------------------------------------------------
# Tests: _get_jpeg_app_markers helper (self-tests)
# ---------------------------------------------------------------------------

def test_binary_parser_detects_app_segments():
    """
    The binary parser must find at least APP0, APP1, and APP2 in the sample
    image (which has JFIF, EXIF/XMP, and an ICC profile).
    """
    markers = _get_jpeg_app_markers(SAMPLE_IMAGE)
    assert 0 in markers, "APP0 (JFIF) should be present"
    assert 1 in markers, "APP1 (EXIF/XMP) should be present"
    assert 2 in markers, "APP2 (ICC profile) should be present"


def test_binary_parser_rejects_non_jpeg(tmp_path):
    """Binary parser must raise ValueError for a non-JPEG file."""
    bad = tmp_path / "not.jpg"
    bad.write_bytes(b"PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match="Not a JPEG"):
        _get_jpeg_app_markers(bad)


def test_inject_app_segment_adds_marker(tmp_path):
    """After injection, the target APP marker must appear in the binary stream."""
    dst = tmp_path / "injected.jpg"
    _inject_app_segment(SAMPLE_IMAGE, dst, app_n=9, payload=b"TEST_VENDOR\x00" + bytes(16))
    markers = _get_jpeg_app_markers(dst)
    assert 9 in markers, "APP9 should be present after injection"


# ---------------------------------------------------------------------------
# Tests: run_jpegtran()
# ---------------------------------------------------------------------------

@skipif_no_jpegtran
def test_jpegtran_strips_all_known_app_segments(tmp_path):
    """
    sample_with_exif.jpg contains APP1 (EXIF+XMP), APP2 (ICC), and APP13
    (Photoshop/IPTC).  After jpegtran -copy none only APP0 must remain.
    """
    out = tmp_path / "stripped.jpg"
    run_jpegtran(SAMPLE_IMAGE, out)
    markers = _get_jpeg_app_markers(out)
    assert markers == {0}, (
        f"Expected only APP0 after strip, got APP markers: {markers}"
    )


@skipif_no_jpegtran
def test_jpegtran_strips_all_app_segments_exhaustive(tmp_path):
    """
    Inject synthetic segments for every possible APP marker (APP0–APP15),
    run jpegtran -copy none, and verify that only the single APP0 written
    by jpegtran itself survives.

    This covers:
      - Multiple segments sharing the same marker number (APP0 appears both
        in the original JFIF header and as an injected segment).
      - All 16 possible APP marker slots simultaneously.
      - Payloads that differ per slot, ruling out any content-based filtering.
    """
    # Chain injections: each step adds one more APP marker to the file.
    current = SAMPLE_IMAGE
    for app_n in range(16):
        dst = tmp_path / f"step_{app_n:02d}.jpg"
        _inject_app_segment(
            current, dst,
            app_n=app_n,
            payload=f"VENDOR_APP{app_n}\x00".encode() + bytes(range(32)),
        )
        current = dst

    # Pre-flight: all 16 APP markers (0–15) must be present before stripping.
    markers_before = _get_jpeg_app_markers(current)
    assert markers_before == set(range(16)), (
        f"Expected APP0–APP15 before strip, got: {markers_before}"
    )

    out = tmp_path / "stripped.jpg"
    run_jpegtran(current, out)

    markers_after = _get_jpeg_app_markers(out)
    assert markers_after == {0}, (
        f"Expected only APP0 after strip, got APP markers: {markers_after}"
    )


@skipif_no_jpegtran
def test_jpegtran_strips_unknown_proprietary_app_segment(tmp_path):
    """
    Core proof: inject a synthetic APP3 (simulating a proprietary camera
    extension invisible to exiftool), run jpegtran -copy none, and verify
    via binary parser that APP3 is absent.
    """
    src = tmp_path / "with_app3.jpg"
    _inject_app_segment(
        SAMPLE_IMAGE, src,
        app_n=3,
        payload=b"FAKECAM\x00" + b"\xde\xad\xbe\xef" * 32,
    )
    assert 3 in _get_jpeg_app_markers(src), "APP3 must be present before strip"

    out = tmp_path / "stripped.jpg"
    run_jpegtran(src, out)

    after = _get_jpeg_app_markers(out)
    assert 3 not in after, "APP3 must be removed by jpegtran -copy none"
    assert after == {0}, f"Expected only APP0, got: {after}"


@skipif_no_jpegtran
@skipif_no_exiftool
def test_jpegtran_preserves_image_dimensions(tmp_path):
    """Image width and height must be identical before and after stripping."""
    def get_dims(path: Path) -> tuple[int, int]:
        data = json.loads(
            subprocess.check_output(["exiftool", "-j", str(path)], text=True)
        )
        return data[0]["ImageWidth"], data[0]["ImageHeight"]

    out = tmp_path / "stripped.jpg"
    run_jpegtran(SAMPLE_IMAGE, out)

    before = get_dims(SAMPLE_IMAGE)
    after = get_dims(out)
    assert after == before, f"Dimensions changed: {before} → {after}"


@skipif_no_jpegtran
def test_jpegtran_preserves_pixel_data(tmp_path):
    """
    Decoded pixel data must be byte-for-byte identical after stripping
    (lossless transform).  Requires Pillow (test extra).
    """
    Image = pytest.importorskip("PIL.Image")

    out = tmp_path / "stripped.jpg"
    run_jpegtran(SAMPLE_IMAGE, out)

    assert Image.open(SAMPLE_IMAGE).tobytes() == Image.open(out).tobytes(), (
        "Pixel data changed after jpegtran strip — transform is not lossless"
    )


@skipif_no_jpegtran
def test_jpegtran_fails_on_non_jpeg(tmp_path):
    """Passing a plain-text file must raise RuntimeError."""
    bad = tmp_path / "text.txt"
    bad.write_text("not a jpeg", encoding="utf-8")
    out = tmp_path / "out.jpg"
    with pytest.raises(RuntimeError, match="jpegtran"):
        run_jpegtran(bad, out)


@skipif_no_jpegtran
def test_jpegtran_fails_on_corrupt_jpeg(tmp_path):
    """A truncated JPEG (valid SOI, truncated header) must raise RuntimeError."""
    corrupt = tmp_path / "corrupt.jpg"
    corrupt.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF")   # truncated mid-header
    out = tmp_path / "out.jpg"
    with pytest.raises(RuntimeError, match="jpegtran"):
        run_jpegtran(corrupt, out)


# ---------------------------------------------------------------------------
# Tests: full _do_scrub_pipeline
# ---------------------------------------------------------------------------

@skipif_no_jpegtran
@skipif_no_exiftool
def test_pipeline_paranoia_zero_metadata(tmp_path):
    """
    Paranoia mode: binary parser confirms only APP0 survives;
    exiftool confirms no EXIF, GPS, XMP, IPTC, or ICC metadata.
    """
    out = tmp_path / "paranoia.jpg"
    _do_scrub_pipeline(SAMPLE_IMAGE, out, paranoia=True,
                       copyright_text=None, comment_text=None)

    # Binary: only JFIF marker allowed
    markers = _get_jpeg_app_markers(out)
    assert markers == {0}, f"Non-JFIF APP segments survived paranoia: {markers}"

    # exiftool: no identifying metadata groups
    tags = json.loads(
        subprocess.check_output(["exiftool", "-j", "-G1", str(out)], text=True)
    )[0]
    keys = list(tags.keys())
    assert not any(k.startswith(("IFD", "ExifIFD")) for k in keys), "EXIF survived paranoia"
    assert not any("GPS" in k for k in keys), "GPS survived paranoia"
    assert not any(k.startswith("XMP") for k in keys), "XMP survived paranoia"
    assert not any(k.startswith("IPTC") for k in keys), "IPTC survived paranoia"
    assert not any(k.startswith("ICC") for k in keys), "ICC profile survived paranoia"


@skipif_no_jpegtran
@skipif_no_exiftool
def test_pipeline_normal_preserves_camera_tags(tmp_path):
    """Normal mode: all whitelist camera tags survive the round-trip."""
    out = tmp_path / "normal.jpg"
    _do_scrub_pipeline(SAMPLE_IMAGE, out, paranoia=False,
                       copyright_text=None, comment_text=None)

    tags = json.loads(
        subprocess.check_output(["exiftool", "-j", str(out)], text=True)
    )[0]
    keys_lower = {k.lower() for k in tags}

    for tag in ("exposuretime", "fnumber", "focallength", "iso", "orientation"):
        assert tag in keys_lower, f"Whitelist tag missing from output: {tag}"


@skipif_no_jpegtran
@skipif_no_exiftool
def test_pipeline_normal_strips_gps_iptc_xmp_makernotes(tmp_path):
    """Normal mode: GPS, IPTC, XMP, and MakerNotes are fully removed."""
    out = tmp_path / "normal.jpg"
    _do_scrub_pipeline(SAMPLE_IMAGE, out, paranoia=False,
                       copyright_text=None, comment_text=None)

    tags = json.loads(
        subprocess.check_output(["exiftool", "-j", "-G1", str(out)], text=True)
    )[0]
    keys = list(tags.keys())

    assert not any("GPS" in k for k in keys), \
        f"GPS survived: {[k for k in keys if 'GPS' in k]}"
    assert not any(k.startswith("IPTC") for k in keys), "IPTC survived"
    assert not any(k.startswith("XMP") for k in keys), "XMP survived"
    assert not any(k.startswith("MakerNotes") for k in keys), "MakerNotes survived"


@skipif_no_jpegtran
@skipif_no_exiftool
def test_pipeline_normal_strips_unknown_proprietary_app_segment(tmp_path):
    """
    Normal mode must strip unknown proprietary APP segments via jpegtran.
    Inject a synthetic APP12 into the source, run the normal pipeline,
    and verify APP12 is absent at the binary level.
    """
    src = tmp_path / "with_app12.jpg"
    _inject_app_segment(
        SAMPLE_IMAGE, src,
        app_n=12,
        payload=b"VENDOR_EXT\x00" + bytes(range(64)),
    )
    assert 12 in _get_jpeg_app_markers(src), "APP12 must be present before pipeline"

    out = tmp_path / "result.jpg"
    _do_scrub_pipeline(src, out, paranoia=False,
                       copyright_text=None, comment_text=None)

    after = _get_jpeg_app_markers(out)
    assert 12 not in after, "APP12 must be removed by the normal pipeline"
    assert 1 in after, "APP1 (EXIF) must be present in normal mode output"


@skipif_no_jpegtran
@skipif_no_exiftool
def test_pipeline_normal_icc_round_trip(tmp_path):
    """ICC profile present in the source must survive the normal pipeline."""
    out = tmp_path / "icc.jpg"
    _do_scrub_pipeline(SAMPLE_IMAGE, out, paranoia=False,
                       copyright_text=None, comment_text=None)

    tags = json.loads(
        subprocess.check_output(["exiftool", "-j", "-G1", str(out)], text=True)
    )[0]
    assert any(k.startswith("ICC") for k in tags), \
        "ICC profile should be preserved in normal mode"


@skipif_no_jpegtran
@skipif_no_exiftool
def test_pipeline_no_icc_source_completes_cleanly(tmp_path):
    """
    A JPEG with no ICC profile must process without error in normal mode.
    The absence of ICC data must not crash the pipeline.
    """
    # Use -o to write to a new file; SAMPLE_IMAGE is never touched.
    # Do NOT combine -overwrite_original with -o — that modifies the source too.
    no_icc_src = tmp_path / "no_icc.jpg"
    subprocess.run(
        ["exiftool", "-ICC_Profile:all=", "-o", str(no_icc_src), str(SAMPLE_IMAGE)],
        check=True, capture_output=True,
    )

    out = tmp_path / "result.jpg"
    _do_scrub_pipeline(no_icc_src, out, paranoia=False,
                       copyright_text=None, comment_text=None)

    assert out.exists(), "Output file must exist"
    assert out.stat().st_size > 0, "Output file must not be empty"

    tags = json.loads(
        subprocess.check_output(["exiftool", "-j", str(out)], text=True)
    )[0]
    keys_lower = {k.lower() for k in tags}
    assert "exposuretime" in keys_lower, "Camera tags must still be preserved"


@skipif_no_jpegtran
@skipif_no_exiftool
def test_pipeline_copyright_and_comment_stamped_in_normal_mode(tmp_path):
    """Copyright and comment must be written into the output in normal mode."""
    out = tmp_path / "stamped.jpg"
    _do_scrub_pipeline(
        SAMPLE_IMAGE, out, paranoia=False,
        copyright_text="Test Corp 2026",
        comment_text="Batch processed",
    )

    tags = json.loads(
        subprocess.check_output(["exiftool", "-j", str(out)], text=True)
    )[0]
    keys_lower = {k.lower(): v for k, v in tags.items()}

    assert "copyright" in keys_lower, "Copyright tag missing"
    assert "Test Corp 2026" in str(keys_lower.get("copyright", "")), \
        "Copyright value mismatch"
    assert "usercomment" in keys_lower or "comment" in keys_lower, \
        "Comment tag missing"
