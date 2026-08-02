# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit coverage for the independent JPEG metadata auditor."""

from __future__ import annotations

import struct
from fractions import Fraction

import pytest

from tests._jpeg_audit import audit_jpeg_bytes, normal_mode_violations


def _segment(marker: int, payload: bytes) -> bytes:
    """Build one length-bearing JPEG segment.

    Args:
        marker: Marker byte without the leading ``0xff``.
        payload: Segment payload.

    Returns:
        Encoded JPEG segment.

    Raises:
        ValueError: If marker or payload cannot form a JPEG segment.
    """
    if not 0 <= marker <= 0xFF or marker in {0x00, 0xD8, 0xD9}:
        raise ValueError(f"invalid length-bearing marker: {marker}")
    if not isinstance(payload, bytes) or len(payload) > 65533:
        raise ValueError("payload must be bytes no larger than 65533 bytes")
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def _jpeg(*segments: bytes, trailing: bytes = b"") -> bytes:
    """Build a structurally parseable JPEG around metadata segments.

    Args:
        *segments: Complete metadata segments placed before SOS.
        trailing: Optional bytes placed after EOI.

    Returns:
        JPEG bytes with stuffed entropy data and one restart marker.

    Raises:
        ValueError: If a supplied segment or trailing value is not bytes.
    """
    if any(not isinstance(segment, bytes) for segment in segments):
        raise ValueError("segments must be bytes")
    if not isinstance(trailing, bytes):
        raise ValueError("trailing must be bytes")
    sos = _segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")
    entropy_data = b"\x11\xff\x00\x22\xff\xd0\x33"
    return b"\xff\xd8" + b"".join(segments) + sos + entropy_data + b"\xff\xd9" + trailing


def _ifd_entry(tag_id: int, field_type: int, count: int, value_field: bytes) -> bytes:
    """Build one little-endian TIFF IFD entry.

    Args:
        tag_id: TIFF tag identifier.
        field_type: TIFF field type.
        count: Number of values.
        value_field: Exactly four inline value or offset bytes.

    Returns:
        Encoded 12-byte TIFF entry.

    Raises:
        ValueError: If values are outside their encoded ranges.
    """
    if not 0 <= tag_id <= 0xFFFF or not 0 <= field_type <= 0xFFFF:
        raise ValueError("tag_id and field_type must fit unsigned 16-bit values")
    if not 0 <= count <= 0xFFFFFFFF or len(value_field) != 4:
        raise ValueError("count must fit uint32 and value_field must be four bytes")
    return struct.pack("<HHI", tag_id, field_type, count) + value_field


def _ifd(entries: list[bytes], next_offset: int = 0) -> bytes:
    """Build one little-endian TIFF IFD.

    Args:
        entries: Complete 12-byte IFD entries.
        next_offset: Offset of the next linked IFD.

    Returns:
        Encoded IFD table.

    Raises:
        ValueError: If entries or next_offset are invalid.
    """
    if len(entries) > 0xFFFF or any(len(entry) != 12 for entry in entries):
        raise ValueError("entries must contain at most 65535 twelve-byte values")
    if not 0 <= next_offset <= 0xFFFFFFFF:
        raise ValueError("next_offset must fit an unsigned 32-bit value")
    return struct.pack("<H", len(entries)) + b"".join(entries) + struct.pack("<I", next_offset)


def _allowed_exif_payload() -> bytes:
    """Build little-endian EXIF containing one approved Orientation tag.

    Returns:
        Complete EXIF APP1 payload.
    """
    orientation = _ifd_entry(0x0112, 3, 1, struct.pack("<H", 6) + b"\x00\x00")
    tiff = b"II" + struct.pack("<HI", 42, 8) + _ifd([orientation])
    return b"Exif\x00\x00" + tiff


def _big_endian_allowed_exif_payload() -> bytes:
    """Build big-endian EXIF containing one approved Orientation tag.

    Returns:
        Complete EXIF APP1 payload.
    """
    orientation = (
        struct.pack(">HHI", 0x0112, 3, 1)
        + struct.pack(">H", 8)
        + b"\x00\x00"
    )
    ifd = struct.pack(">H", 1) + orientation + struct.pack(">I", 0)
    tiff = b"MM" + struct.pack(">HI", 42, 8) + ifd
    return b"Exif\x00\x00" + tiff


def _forbidden_exif_payload() -> bytes:
    """Build EXIF containing GPS, MakerNote, and IFD1 thumbnail entries.

    Returns:
        Complete EXIF APP1 payload with valid child offsets.
    """
    ifd0_offset = 8
    ifd0_size = 2 + (2 * 12) + 4
    exif_ifd_offset = ifd0_offset + ifd0_size
    exif_ifd_size = 2 + 12 + 4
    gps_ifd_offset = exif_ifd_offset + exif_ifd_size
    gps_ifd_size = 2 + 12 + 4
    ifd1_offset = gps_ifd_offset + gps_ifd_size

    exif_pointer = _ifd_entry(0x8769, 4, 1, struct.pack("<I", exif_ifd_offset))
    gps_pointer = _ifd_entry(0x8825, 4, 1, struct.pack("<I", gps_ifd_offset))
    ifd0 = _ifd([exif_pointer, gps_pointer], next_offset=ifd1_offset)

    maker_note = _ifd_entry(0x927C, 7, 4, b"TEST")
    exif_ifd = _ifd([maker_note])
    gps_version = _ifd_entry(0x0000, 1, 4, b"\x02\x03\x00\x00")
    gps_ifd = _ifd([gps_version])
    thumbnail_offset = _ifd_entry(0x0201, 4, 1, struct.pack("<I", 1234))
    thumbnail_size = _ifd_entry(0x0202, 4, 1, struct.pack("<I", 100))
    ifd1 = _ifd([thumbnail_offset, thumbnail_size])

    tiff = b"II" + struct.pack("<HI", 42, ifd0_offset)
    return b"Exif\x00\x00" + tiff + ifd0 + exif_ifd + gps_ifd + ifd1


def _photoshop_resource(resource_id: int, data: bytes) -> bytes:
    """Build one Photoshop APP13 image-resource block.

    Args:
        resource_id: Photoshop image-resource identifier.
        data: Resource bytes.

    Returns:
        Complete Photoshop APP13 payload.

    Raises:
        ValueError: If resource_id or data is invalid.
    """
    if not 0 <= resource_id <= 0xFFFF or not isinstance(data, bytes):
        raise ValueError("resource_id must fit uint16 and data must be bytes")
    padded_data = data + (b"\x00" if len(data) % 2 else b"")
    return (
        b"Photoshop 3.0\x00"
        + b"8BIM"
        + struct.pack(">H", resource_id)
        + b"\x00\x00"
        + struct.pack(">I", len(data))
        + padded_data
    )


def test_audit_normal_metadata_boundary_valid_input_reports_no_violations() -> None:
    """Accept approved EXIF and a complete multi-segment ICC profile.

    Returns:
        None.
    """
    jfif = _segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    exif = _segment(0xE1, _allowed_exif_payload())
    icc_second = _segment(0xE2, b"ICC_PROFILE\x00\x02\x02cd")
    icc_first = _segment(0xE2, b"ICC_PROFILE\x00\x01\x02ab")

    audit = audit_jpeg_bytes(_jpeg(jfif, exif, icc_second, icc_first))

    assert normal_mode_violations(audit) == ()
    assert audit.icc_profile == b"abcd"
    assert audit.approved_tag_values() == {"Orientation": (Fraction(6),)}


def test_audit_big_endian_exif_valid_input_decodes_approved_value() -> None:
    """Decode an approved tag from Motorola-order TIFF data.

    Returns:
        None.
    """
    exif = _segment(0xE1, _big_endian_allowed_exif_payload())

    audit = audit_jpeg_bytes(_jpeg(exif))

    assert normal_mode_violations(audit) == ()
    assert audit.approved_tag_values() == {"Orientation": (Fraction(8),)}


def test_audit_forbidden_metadata_present_reports_every_category() -> None:
    """Detect forbidden APP payloads, EXIF tags, comments, and trailing data.

    Returns:
        None.
    """
    exif = _segment(0xE1, _forbidden_exif_payload())
    xmp = _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<xmp/>")
    mpf = _segment(0xE2, b"MPF\x00payload")
    iptc = _segment(0xED, _photoshop_resource(0x0404, b"iptc"))
    unknown = _segment(0xE3, b"private")
    comment = _segment(0xFE, b"camera comment")

    audit = audit_jpeg_bytes(
        _jpeg(exif, xmp, mpf, iptc, unknown, comment, trailing=b"trailer")
    )
    violations = normal_mode_violations(audit)

    assert audit.gps_present()
    assert audit.maker_note_present()
    assert audit.embedded_image_present()
    assert audit.xmp_segment_count == 1
    assert audit.iptc_segment_count == 1
    assert audit.mpf_segment_count == 1
    assert audit.comment_count == 1
    assert audit.trailing_data_size == len(b"trailer")
    assert any("unexpected APP markers" in violation for violation in violations)
    assert any("unapproved EXIF tags" in violation for violation in violations)


def test_audit_empty_padded_xmp_packet_is_still_forbidden() -> None:
    """Reject an XMP packet even when its RDF contains no properties.

    Returns:
        None.
    """
    empty_xmp_packet = (
        b"http://ns.adobe.com/xap/1.0/\x00"
        b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b"</rdf:RDF></x:xmpmeta>"
        + (b" " * 1820)
        + b'<?xpacket end="w"?>'
    )
    audit = audit_jpeg_bytes(_jpeg(_segment(0xE1, empty_xmp_packet)))
    violations = normal_mode_violations(audit)

    assert audit.xmp_segment_count == 1
    assert any("XMP segments: 1" in violation for violation in violations)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not-a-jpeg", "SOI"),
        (b"\xff\xd8\xff\xe1\x00\x01\xff\xd9", "segment length"),
        (b"\xff\xd8" + _segment(0xE1, b"x"), "EOI"),
    ],
)
def test_audit_malformed_jpeg_input_raises_value_error(
    data: bytes,
    message: str,
) -> None:
    """Reject malformed JPEG marker streams.

    Args:
        data: Malformed JPEG bytes.
        message: Expected diagnostic fragment.

    Returns:
        None.
    """
    with pytest.raises(ValueError, match=message):
        audit_jpeg_bytes(data)


def test_audit_cyclic_tiff_pointer_raises_value_error() -> None:
    """Reject an EXIF child pointer that cycles back to IFD0.

    Returns:
        None.
    """
    cyclic_pointer = _ifd_entry(0x8769, 4, 1, struct.pack("<I", 8))
    tiff = b"II" + struct.pack("<HI", 42, 8) + _ifd([cyclic_pointer])
    exif = _segment(0xE1, b"Exif\x00\x00" + tiff)

    with pytest.raises(ValueError, match="cycle"):
        audit_jpeg_bytes(_jpeg(exif))


def test_audit_out_of_bounds_tiff_value_raises_value_error() -> None:
    """Reject a TIFF value offset beyond its containing APP1 segment.

    Returns:
        None.
    """
    invalid_value = _ifd_entry(0x010E, 2, 20, struct.pack("<I", 9999))
    tiff = b"II" + struct.pack("<HI", 42, 8) + _ifd([invalid_value])
    exif = _segment(0xE1, b"Exif\x00\x00" + tiff)

    with pytest.raises(ValueError, match="exceeds bounds"):
        audit_jpeg_bytes(_jpeg(exif))


def test_audit_incomplete_icc_chunks_raise_value_error() -> None:
    """Reject an ICC sequence that advertises a missing second chunk.

    Returns:
        None.
    """
    incomplete_icc = _segment(0xE2, b"ICC_PROFILE\x00\x01\x02profile")

    with pytest.raises(ValueError, match="Incomplete ICC chunks"):
        audit_jpeg_bytes(_jpeg(incomplete_icc))
