# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent, fail-closed JPEG metadata auditing for scrubexif outputs."""

from __future__ import annotations

import struct
import xml.etree.ElementTree as element_tree
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType


MAX_IFD_DEPTH = 8
MAX_IFD_ENTRIES = 4096
MAX_TIFF_VALUES = 1_000_000

XMP_STANDARD_PREFIX = b"http://ns.adobe.com/xap/1.0/\x00"
XMP_EXTENDED_PREFIX = b"http://ns.adobe.com/xmp/extension/\x00"
XMP_NAMESPACE = "adobe:ns:meta/"
RDF_NAMESPACE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
DC_NAMESPACE = "http://purl.org/dc/elements/1.1/"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
XPACKET_ID = b"W5M0MpCehiHzreSzNTczkc9d"

TIFF_TYPE_SIZES: Mapping[int, int] = MappingProxyType({
    1: 1,   # BYTE
    2: 1,   # ASCII
    3: 2,   # SHORT
    4: 4,   # LONG
    5: 8,   # RATIONAL
    6: 1,   # SBYTE
    7: 1,   # UNDEFINED
    8: 2,   # SSHORT
    9: 4,   # SLONG
    10: 8,  # SRATIONAL
    11: 4,  # FLOAT
    12: 8,  # DOUBLE
    13: 4,  # IFD
})

APPROVED_TAG_LOCATIONS: Mapping[str, tuple[str, int]] = MappingProxyType({
    "Orientation": ("IFD0", 0x0112),
    "ExposureTime": ("ExifIFD", 0x829A),
    "FNumber": ("ExifIFD", 0x829D),
    "ISO": ("ExifIFD", 0x8827),
    "FocalLength": ("ExifIFD", 0x920A),
})

COPYRIGHT_TAG_LOCATION = ("IFD0", 0x8298)
USER_COMMENT_TAG_LOCATION = ("ExifIFD", 0x9286)

ALLOWED_TAGS_BY_IFD: Mapping[str, frozenset[int]] = MappingProxyType({
    "IFD0": frozenset({
        0x0112,  # Orientation
        0x011A,  # XResolution
        0x011B,  # YResolution
        0x0128,  # ResolutionUnit
        0x0213,  # YCbCrPositioning
        0x8769,  # ExifIFD pointer
    }),
    "ExifIFD": frozenset({
        0x829A,  # ExposureTime
        0x829D,  # FNumber
        0x8827,  # ISO
        0x9000,  # ExifVersion
        0x9101,  # ComponentsConfiguration
        0x920A,  # FocalLength
        0xA000,  # FlashpixVersion
        0xA001,  # ColorSpace
    }),
})


TiffScalar = int | float | str | bytes | Fraction


@dataclass(frozen=True)
class JpegSegment:
    """Represent one parsed JPEG marker segment.

    Attributes:
        marker: Marker byte without the leading ``0xff``.
        payload: Segment bytes excluding marker and length fields.
        offset: Byte offset of the marker prefix in the JPEG.
    """

    marker: int
    payload: bytes
    offset: int


@dataclass(frozen=True)
class TiffTag:
    """Represent one decoded TIFF IFD entry.

    Attributes:
        ifd_name: Logical IFD containing the entry.
        tag_id: Numeric TIFF tag identifier.
        field_type: Numeric TIFF field type.
        values: Decoded immutable values.
    """

    ifd_name: str
    tag_id: int
    field_type: int
    values: tuple[TiffScalar, ...]


@dataclass(frozen=True)
class JpegAudit:
    """Contain independently parsed JPEG metadata facts.

    Attributes:
        app_markers: APP marker numbers encountered, including duplicates.
        comment_count: Number of JPEG COM segments.
        exif_segment_count: Number of EXIF APP1 segments.
        xmp_segment_count: Number of standard or extended XMP APP1 segments.
        xmp_payloads: Complete standard or extended XMP APP1 payloads.
        photoshop_segment_count: Number of Photoshop APP13 segments.
        iptc_segment_count: Number of Photoshop segments containing IPTC data.
        mpf_segment_count: Number of MPF APP2 segments.
        unknown_app_markers: APP markers with unapproved payload formats.
        jfif_thumbnail_present: Whether APP0 contains an inline JFIF thumbnail.
        jfif_extra_data_size: Vendor bytes following the declared JFIF thumbnail.
        photoshop_thumbnail_present: Whether APP13 contains a thumbnail resource.
        icc_profile: Reassembled ICC bytes, or None when absent.
        exif_tags: TIFF tags decoded from every EXIF segment.
        trailing_data_size: Bytes found after JPEG EOI.
    """

    app_markers: tuple[int, ...]
    comment_count: int
    exif_segment_count: int
    xmp_segment_count: int
    xmp_payloads: tuple[bytes, ...]
    photoshop_segment_count: int
    iptc_segment_count: int
    mpf_segment_count: int
    unknown_app_markers: tuple[int, ...]
    jfif_thumbnail_present: bool
    jfif_extra_data_size: int
    photoshop_thumbnail_present: bool
    icc_profile: bytes | None
    exif_tags: tuple[TiffTag, ...]
    trailing_data_size: int

    def approved_tag_values(self) -> dict[str, tuple[Fraction, ...]]:
        """Return normalized values for approved photographic EXIF tags.

        Returns:
            Approved tag names mapped to exact rational values.

        Raises:
            ValueError: If an approved tag is duplicated or non-numeric.
        """
        result: dict[str, tuple[Fraction, ...]] = {}
        for tag_name, (ifd_name, tag_id) in APPROVED_TAG_LOCATIONS.items():
            matches = [
                tag
                for tag in self.exif_tags
                if tag.ifd_name == ifd_name and tag.tag_id == tag_id
            ]
            if not matches:
                continue
            if len(matches) != 1:
                raise ValueError(
                    f"Approved EXIF tag {tag_name} appears {len(matches)} times"
                )
            if len(matches[0].values) != 1:
                raise ValueError(
                    f"Approved EXIF tag {tag_name} must contain exactly one value"
                )

            normalized: list[Fraction] = []
            for value in matches[0].values:
                if isinstance(value, bool) or not isinstance(value, (int, Fraction)):
                    raise ValueError(
                        f"Approved EXIF tag {tag_name} has non-numeric value {value!r}"
                    )
                normalized.append(Fraction(value))
            result[tag_name] = tuple(normalized)
        return result

    def unexpected_exif_tags(
        self,
        allowed_extra_locations: frozenset[tuple[str, int]] = frozenset(),
    ) -> tuple[TiffTag, ...]:
        """Return EXIF entries outside the normal-mode allowlist.

        Args:
            allowed_extra_locations: Additional ``(IFD name, tag ID)`` pairs
                allowed by an explicit output policy, such as requested stamps.

        Returns:
            Unexpected entries sorted in original parse order.

        Raises:
            ValueError: If allowed_extra_locations is not a frozenset.
        """
        if not isinstance(allowed_extra_locations, frozenset):
            raise ValueError("allowed_extra_locations must be a frozenset")
        unexpected: list[TiffTag] = []
        for tag in self.exif_tags:
            allowed_ids = ALLOWED_TAGS_BY_IFD.get(tag.ifd_name, frozenset())
            location = (tag.ifd_name, tag.tag_id)
            if tag.tag_id not in allowed_ids and location not in allowed_extra_locations:
                unexpected.append(tag)
        return tuple(unexpected)

    def gps_present(self) -> bool:
        """Report whether a GPS pointer or GPS IFD survived.

        Returns:
            True when GPS metadata is present.
        """
        return any(
            tag.ifd_name == "GPSIFD" or tag.tag_id == 0x8825
            for tag in self.exif_tags
        )

    def maker_note_present(self) -> bool:
        """Report whether a MakerNote tag survived.

        Returns:
            True when TIFF tag ``0x927c`` is present.
        """
        return any(tag.tag_id == 0x927C for tag in self.exif_tags)

    def embedded_image_present(self) -> bool:
        """Report whether an embedded thumbnail or auxiliary image survived.

        Returns:
            True for JFIF, Photoshop, EXIF IFD1, or MPF secondary images.
        """
        if self.jfif_thumbnail_present or self.photoshop_thumbnail_present:
            return True
        if self.mpf_segment_count > 0:
            return True
        return any(
            tag.ifd_name.startswith("IFD1") or tag.tag_id in {0x0201, 0x0202}
            for tag in self.exif_tags
        )


def audit_jpeg(path: Path) -> JpegAudit:
    """Audit JPEG metadata without invoking ExifTool.

    Args:
        path: Existing JPEG path.

    Returns:
        Independently parsed metadata facts.

    Raises:
        ValueError: If path or any JPEG, TIFF, Photoshop, or ICC structure is invalid.
        OSError: If the file cannot be read.
    """
    if not isinstance(path, Path):
        raise ValueError("path must be a pathlib.Path")
    if not path.is_file():
        raise ValueError(f"path must be an existing file: {path}")
    return audit_jpeg_bytes(path.read_bytes())


def audit_jpeg_bytes(data: bytes) -> JpegAudit:
    """Audit JPEG metadata from immutable bytes.

    Args:
        data: Complete JPEG byte stream.

    Returns:
        Independently parsed metadata facts.

    Raises:
        ValueError: If the byte stream or embedded metadata is malformed.
    """
    if not isinstance(data, bytes) or not data:
        raise ValueError("data must be non-empty bytes")

    segments, trailing_data_size = _parse_jpeg_segments(data)
    app_markers: list[int] = []
    unknown_app_markers: list[int] = []
    exif_tags: list[TiffTag] = []
    icc_chunks: list[tuple[int, int, bytes]] = []
    comment_count = 0
    exif_segment_count = 0
    xmp_segment_count = 0
    xmp_payloads: list[bytes] = []
    photoshop_segment_count = 0
    iptc_segment_count = 0
    mpf_segment_count = 0
    jfif_thumbnail_present = False
    jfif_extra_data_size = 0
    photoshop_thumbnail_present = False

    for segment in segments:
        if segment.marker == 0xFE:
            comment_count += 1
            continue
        if not 0xE0 <= segment.marker <= 0xEF:
            continue

        app_number = segment.marker - 0xE0
        app_markers.append(app_number)
        payload = segment.payload

        if app_number == 0:
            if payload.startswith(b"JFIF\x00"):
                thumbnail_present, extra_data_size = _inspect_jfif_payload(payload)
                jfif_thumbnail_present = jfif_thumbnail_present or thumbnail_present
                jfif_extra_data_size += extra_data_size
            else:
                unknown_app_markers.append(app_number)
            continue

        if app_number == 1:
            if payload.startswith(b"Exif\x00\x00"):
                exif_segment_count += 1
                exif_tags.extend(_parse_exif_payload(payload))
            elif payload.startswith((
                XMP_STANDARD_PREFIX,
                XMP_EXTENDED_PREFIX,
            )):
                xmp_segment_count += 1
                xmp_payloads.append(payload)
            else:
                unknown_app_markers.append(app_number)
            continue

        if app_number == 2:
            if payload.startswith(b"ICC_PROFILE\x00"):
                if len(payload) < 14:
                    raise ValueError("ICC APP2 segment is too short")
                icc_chunks.append((payload[12], payload[13], payload[14:]))
            elif payload.startswith(b"MPF\x00"):
                mpf_segment_count += 1
            else:
                unknown_app_markers.append(app_number)
            continue

        if app_number == 13 and payload.startswith(b"Photoshop 3.0\x00"):
            photoshop_segment_count += 1
            resource_ids = _parse_photoshop_resource_ids(payload)
            if 0x0404 in resource_ids:
                iptc_segment_count += 1
            if resource_ids & {0x040C, 0x040D}:
                photoshop_thumbnail_present = True
            continue

        unknown_app_markers.append(app_number)

    return JpegAudit(
        app_markers=tuple(app_markers),
        comment_count=comment_count,
        exif_segment_count=exif_segment_count,
        xmp_segment_count=xmp_segment_count,
        xmp_payloads=tuple(xmp_payloads),
        photoshop_segment_count=photoshop_segment_count,
        iptc_segment_count=iptc_segment_count,
        mpf_segment_count=mpf_segment_count,
        unknown_app_markers=tuple(unknown_app_markers),
        jfif_thumbnail_present=jfif_thumbnail_present,
        jfif_extra_data_size=jfif_extra_data_size,
        photoshop_thumbnail_present=photoshop_thumbnail_present,
        icc_profile=_assemble_icc_profile(icc_chunks),
        exif_tags=tuple(exif_tags),
        trailing_data_size=trailing_data_size,
    )


def normal_mode_violations(
    audit: JpegAudit,
    allowed_extra_locations: frozenset[tuple[str, int]] = frozenset(),
    allow_xmp: bool = False,
) -> tuple[str, ...]:
    """Return fail-closed privacy violations for normal output.

    Args:
        audit: Independently parsed JPEG audit.
        allowed_extra_locations: Explicitly requested EXIF stamp locations.
        allow_xmp: Whether an explicitly requested XMP stamp is permitted.

    Returns:
        Human-readable violations; empty means the metadata boundary is clean.

    Raises:
        ValueError: If audit is not a JpegAudit.
    """
    if not isinstance(audit, JpegAudit):
        raise ValueError("audit must be a JpegAudit")
    if not isinstance(allowed_extra_locations, frozenset):
        raise ValueError("allowed_extra_locations must be a frozenset")
    if not isinstance(allow_xmp, bool):
        raise ValueError("allow_xmp must be a boolean")

    violations: list[str] = []
    unexpected_markers = sorted(set(audit.app_markers) - {0, 1, 2})
    if unexpected_markers:
        violations.append(f"unexpected APP markers: {unexpected_markers}")
    if audit.unknown_app_markers:
        violations.append(
            f"unapproved APP payloads: {sorted(set(audit.unknown_app_markers))}"
        )
    if audit.comment_count:
        violations.append(f"JPEG comments: {audit.comment_count}")
    if audit.jfif_extra_data_size:
        violations.append(f"extra JFIF payload bytes: {audit.jfif_extra_data_size}")
    if audit.xmp_segment_count and not allow_xmp:
        violations.append(f"XMP segments: {audit.xmp_segment_count}")
    if allow_xmp and audit.xmp_segment_count > 1:
        violations.append(f"multiple XMP segments: {audit.xmp_segment_count}")
    if audit.photoshop_segment_count:
        violations.append(f"Photoshop APP13 segments: {audit.photoshop_segment_count}")
    if audit.iptc_segment_count:
        violations.append(f"IPTC segments: {audit.iptc_segment_count}")
    if audit.mpf_segment_count:
        violations.append(f"MPF segments: {audit.mpf_segment_count}")
    if audit.exif_segment_count > 1:
        violations.append(f"multiple EXIF segments: {audit.exif_segment_count}")
    if audit.gps_present():
        violations.append("GPS metadata")
    if audit.maker_note_present():
        violations.append("MakerNote metadata")
    if audit.embedded_image_present():
        violations.append("embedded secondary image")
    if audit.trailing_data_size:
        violations.append(f"trailing bytes after EOI: {audit.trailing_data_size}")

    # This validates uniqueness, cardinality, and numeric encoding for every
    # approved value in addition to checking the tag IDs below.
    audit.approved_tag_values()
    unexpected_tags = audit.unexpected_exif_tags(allowed_extra_locations)
    if unexpected_tags:
        rendered_tags = [
            f"{tag.ifd_name}:0x{tag.tag_id:04x}"
            for tag in unexpected_tags
        ]
        violations.append(f"unapproved EXIF tags: {rendered_tags}")
    return tuple(violations)


def paranoia_mode_violations(audit: JpegAudit) -> tuple[str, ...]:
    """Return fail-closed privacy violations for paranoia output.

    Args:
        audit: Independently parsed JPEG audit.

    Returns:
        Human-readable violations; empty means the JPEG contains only a plain
        JFIF APP0 segment and image data.

    Raises:
        ValueError: If audit is not a JpegAudit.
    """
    if not isinstance(audit, JpegAudit):
        raise ValueError("audit must be a JpegAudit")

    violations = list(normal_mode_violations(audit))
    if audit.exif_segment_count:
        violations.append(f"EXIF segments: {audit.exif_segment_count}")
    if audit.icc_profile is not None:
        violations.append("ICC profile")
    return tuple(violations)


def scrubbed_output_violations(
    audit: JpegAudit,
    paranoia: bool,
    copyright_text: str | None,
    comment_text: str | None,
) -> tuple[str, ...]:
    """Evaluate an audited JPEG against the active scrub policy.

    Args:
        audit: Independently parsed JPEG audit.
        paranoia: Whether the zero-metadata policy applies.
        copyright_text: Exact copyright stamp expected in EXIF and XMP.
        comment_text: Exact comment stamp expected in EXIF and XMP.

    Returns:
        Human-readable policy violations; empty means publication is allowed.

    Raises:
        ValueError: If arguments have invalid types or stamps are requested in
            paranoia mode.
    """
    if not isinstance(paranoia, bool):
        raise ValueError("paranoia must be a boolean")
    for label, value in (("copyright_text", copyright_text), ("comment_text", comment_text)):
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{label} must be a string or None")
    copyright_requested = copyright_text is not None
    comment_requested = comment_text is not None
    if paranoia and (copyright_requested or comment_requested):
        raise ValueError("paranoia output cannot permit metadata stamps")
    if paranoia:
        return paranoia_mode_violations(audit)

    allowed_extra_locations: set[tuple[str, int]] = set()
    if copyright_requested:
        allowed_extra_locations.add(COPYRIGHT_TAG_LOCATION)
    if comment_requested:
        allowed_extra_locations.add(USER_COMMENT_TAG_LOCATION)
    violations = list(normal_mode_violations(
        audit,
        allowed_extra_locations=frozenset(allowed_extra_locations),
        allow_xmp=copyright_requested or comment_requested,
    ))
    violations.extend(_stamp_tag_violations(audit, copyright_text, comment_text))
    violations.extend(_xmp_stamp_violations(audit, copyright_text, comment_text))
    return tuple(violations)


def _stamp_tag_violations(
    audit: JpegAudit,
    copyright_text: str | None,
    comment_text: str | None,
) -> tuple[str, ...]:
    """Validate exact EXIF stamp values against user-requested text.

    Args:
        audit: Independently parsed JPEG audit.
        copyright_text: Expected copyright value, or None.
        comment_text: Expected user-comment value, or None.

    Returns:
        Human-readable stamp policy violations.
    """
    violations: list[str] = []
    copyright_tags = [
        tag
        for tag in audit.exif_tags
        if (tag.ifd_name, tag.tag_id) == COPYRIGHT_TAG_LOCATION
    ]
    if copyright_text is not None:
        expected = copyright_text.encode("utf-8").decode("latin-1")
        if len(copyright_tags) != 1 or copyright_tags[0].values != (expected,):
            violations.append("EXIF Copyright stamp does not exactly match request")
    elif copyright_tags:
        violations.append("unexpected EXIF Copyright stamp")

    comment_tags = [
        tag
        for tag in audit.exif_tags
        if (tag.ifd_name, tag.tag_id) == USER_COMMENT_TAG_LOCATION
    ]
    if comment_text is not None:
        if len(comment_tags) != 1:
            violations.append("EXIF UserComment stamp is missing or duplicated")
        else:
            try:
                actual_comment = _decode_user_comment(comment_tags[0])
            except ValueError as exc:
                violations.append(f"EXIF UserComment stamp is invalid: {exc}")
            else:
                if actual_comment != comment_text:
                    violations.append("EXIF UserComment stamp does not exactly match request")
    elif comment_tags:
        violations.append("unexpected EXIF UserComment stamp")
    return tuple(violations)


def _decode_user_comment(tag: TiffTag) -> str:
    """Decode the narrowly supported ExifTool UserComment encodings.

    Args:
        tag: Parsed EXIF UserComment tag.

    Returns:
        Decoded comment text.

    Raises:
        ValueError: If the tag does not use a supported, unambiguous encoding.
    """
    if tag.field_type != 7 or len(tag.values) != 1 or not isinstance(tag.values[0], bytes):
        raise ValueError("must contain one UNDEFINED byte value")
    raw_value = tag.values[0]
    try:
        if raw_value.startswith(b"ASCII\x00\x00\x00"):
            return raw_value[8:].decode("ascii")
        if raw_value.startswith(b"UNICODE\x00"):
            return raw_value[8:].decode("utf-16-be")
    except UnicodeDecodeError as exc:
        raise ValueError("contains invalid encoded text") from exc
    raise ValueError("uses an unsupported character-code prefix")


def _xmp_stamp_violations(
    audit: JpegAudit,
    copyright_text: str | None,
    comment_text: str | None,
) -> tuple[str, ...]:
    """Validate that XMP contains only exact requested dc stamp values.

    Args:
        audit: Independently parsed JPEG audit.
        copyright_text: Expected dc:rights value, or None.
        comment_text: Expected dc:description value, or None.

    Returns:
        Human-readable XMP policy violations.
    """
    if copyright_text is None and comment_text is None:
        return ()
    if len(audit.xmp_payloads) != 1:
        return ("XMP stamp packet is missing or duplicated",)

    payload = audit.xmp_payloads[0]
    if not payload.startswith(XMP_STANDARD_PREFIX):
        return ("extended XMP is not allowed for stamps",)
    packet_bytes = payload[len(XMP_STANDARD_PREFIX):]
    lowered = packet_bytes.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered or b"<!--" in lowered:
        return ("XMP stamp packet contains forbidden XML declarations or comments",)
    try:
        xml_bytes = _extract_xmp_xml_document(packet_bytes)
        root = element_tree.fromstring(xml_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, element_tree.ParseError) as exc:
        return (f"XMP stamp packet is malformed: {exc}",)

    xmpmeta_tag = f"{{{XMP_NAMESPACE}}}xmpmeta"
    rdf_tag = f"{{{RDF_NAMESPACE}}}RDF"
    description_tag = f"{{{RDF_NAMESPACE}}}Description"
    alt_tag = f"{{{RDF_NAMESPACE}}}Alt"
    item_tag = f"{{{RDF_NAMESPACE}}}li"
    toolkit_attribute = f"{{{XMP_NAMESPACE}}}xmptk"
    about_attribute = f"{{{RDF_NAMESPACE}}}about"
    language_attribute = f"{{{XML_NAMESPACE}}}lang"

    if root.tag != xmpmeta_tag:
        return ("XMP stamp packet root is not x:xmpmeta",)
    toolkit_value = root.attrib.get(toolkit_attribute)
    if set(root.attrib) != {toolkit_attribute} or not _is_exiftool_toolkit(toolkit_value):
        return ("XMP stamp packet has an invalid ExifTool toolkit identifier",)
    root_children = list(root)
    if len(root_children) != 1 or root_children[0].tag != rdf_tag:
        return ("XMP stamp packet must contain exactly one rdf:RDF element",)
    rdf = root_children[0]
    if rdf.attrib:
        return ("XMP rdf:RDF element contains attributes",)
    rdf_children = list(rdf)
    if len(rdf_children) != 1 or rdf_children[0].tag != description_tag:
        return ("XMP stamp packet must contain exactly one rdf:Description",)
    description = rdf_children[0]
    if description.attrib != {about_attribute: ""}:
        return ("XMP rdf:Description attributes do not match policy",)

    for element in root.iter():
        if element.tag != item_tag and (element.text or "").strip():
            return (f"XMP stamp packet contains unapproved text in {element.tag}",)
        if (element.tail or "").strip():
            return (f"XMP stamp packet contains unapproved tail text after {element.tag}",)

    expected_fields = {
        "rights": copyright_text,
        "description": comment_text,
    }
    violations: list[str] = []
    expected_tags = {
        f"{{{DC_NAMESPACE}}}{field_name}"
        for field_name, expected_text in expected_fields.items()
        if expected_text is not None
    }
    actual_tags = [element.tag for element in description]
    if len(actual_tags) != len(expected_tags) or set(actual_tags) != expected_tags:
        violations.append("XMP dc stamp fields do not exactly match request")

    for field_name, expected_text in expected_fields.items():
        if expected_text is None:
            continue
        fields = [
            element
            for element in description
            if element.tag == f"{{{DC_NAMESPACE}}}{field_name}"
        ]
        if len(fields) != 1 or fields[0].attrib:
            violations.append(f"XMP dc:{field_name} stamp is missing or duplicated")
            continue
        alt_children = list(fields[0])
        if len(alt_children) != 1 or alt_children[0].tag != alt_tag or alt_children[0].attrib:
            violations.append(f"XMP dc:{field_name} stamp has invalid rdf:Alt structure")
            continue
        list_items = list(alt_children[0])
        if (
            len(list_items) != 1
            or list_items[0].tag != item_tag
            or list_items[0].attrib != {language_attribute: "x-default"}
            or (list_items[0].text or "") != expected_text
        ):
            violations.append(f"XMP dc:{field_name} stamp does not exactly match request")
    return tuple(violations)


def _extract_xmp_xml_document(packet_bytes: bytes) -> bytes:
    """Extract XML from the narrowly supported ExifTool xpacket wrapper.

    Args:
        packet_bytes: Complete bytes after the standard XMP APP1 identifier.

    Returns:
        XML document bytes without xpacket processing instructions or padding.

    Raises:
        ValueError: If the wrapper is absent, ambiguous, or contains another
            processing instruction.
    """
    if not isinstance(packet_bytes, bytes) or not packet_bytes:
        raise ValueError("XMP packet must be non-empty bytes")
    begin_wrappers = (
        b"<?xpacket begin='\xef\xbb\xbf' id='" + XPACKET_ID + b"'?>",
        b'<?xpacket begin="\xef\xbb\xbf" id="' + XPACKET_ID + b'"?>',
    )
    end_wrappers = (b"<?xpacket end='w'?>", b'<?xpacket end="w"?>')
    remainder: bytes | None = None
    for wrapper in begin_wrappers:
        if packet_bytes.startswith(wrapper):
            remainder = packet_bytes[len(wrapper):]
            break
    if remainder is None:
        raise ValueError("unsupported or missing xpacket begin wrapper")

    stripped_remainder = remainder.rstrip()
    xml_document: bytes | None = None
    for wrapper in end_wrappers:
        if stripped_remainder.endswith(wrapper):
            xml_document = stripped_remainder[:-len(wrapper)].rstrip()
            break
    if xml_document is None:
        raise ValueError("unsupported or missing xpacket end wrapper")
    if not xml_document or b"<?" in xml_document:
        raise ValueError("unexpected XML processing instruction")
    return xml_document


def _is_exiftool_toolkit(value: str | None) -> bool:
    """Validate the non-user-controlled ExifTool XMP toolkit identifier.

    Args:
        value: Parsed x:xmptk attribute value.

    Returns:
        True only for ``Image::ExifTool`` followed by a dotted numeric version.
    """
    prefix = "Image::ExifTool "
    if value is None or not value.startswith(prefix):
        return False
    version_parts = value[len(prefix):].split(".")
    return len(version_parts) >= 2 and all(
        part.isascii() and part.isdigit()
        for part in version_parts
    )


def _parse_jpeg_segments(data: bytes) -> tuple[tuple[JpegSegment, ...], int]:
    """Parse a complete JPEG marker stream, including markers after scans.

    Args:
        data: Complete JPEG byte stream.

    Returns:
        Parsed segments and trailing byte count after EOI.

    Raises:
        ValueError: If marker framing, lengths, scans, or EOI are invalid.
    """
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise ValueError("JPEG must start with the SOI marker")

    segments: list[JpegSegment] = []
    position = 2
    in_entropy_data = False

    while position < len(data):
        if in_entropy_data:
            position = _find_next_entropy_marker(data, position)
            in_entropy_data = False

        marker_offset = position
        if data[position] != 0xFF:
            raise ValueError(f"Expected JPEG marker at offset {position}")
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            raise ValueError("Truncated JPEG marker at end of file")

        marker = data[position]
        position += 1
        if marker == 0x00:
            raise ValueError(f"Unexpected stuffed byte at offset {marker_offset}")
        if marker == 0xD9:
            return tuple(segments), len(data) - position
        if marker == 0xD8:
            raise ValueError(f"Unexpected nested SOI marker at offset {marker_offset}")
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            segments.append(JpegSegment(marker, b"", marker_offset))
            continue

        length_bytes = _checked_slice(
            data,
            position,
            2,
            f"JPEG segment length at offset {marker_offset}",
        )
        segment_length = int.from_bytes(length_bytes, "big")
        if segment_length < 2:
            raise ValueError(f"Invalid JPEG segment length at offset {marker_offset}")
        payload = _checked_slice(
            data,
            position + 2,
            segment_length - 2,
            f"JPEG segment payload at offset {marker_offset}",
        )
        segments.append(JpegSegment(marker, payload, marker_offset))
        position += segment_length
        if marker == 0xDA:
            in_entropy_data = True

    raise ValueError("JPEG is missing the EOI marker")


def _find_next_entropy_marker(data: bytes, position: int) -> int:
    """Find the next non-stuffed, non-restart marker in entropy data.

    Args:
        data: Complete JPEG byte stream.
        position: First entropy-coded byte after an SOS header.

    Returns:
        Offset of the next marker prefix.

    Raises:
        ValueError: If entropy data ends before another marker.
    """
    while position < len(data):
        marker_offset = data.find(b"\xff", position)
        if marker_offset < 0:
            break
        marker_position = marker_offset + 1
        while marker_position < len(data) and data[marker_position] == 0xFF:
            marker_position += 1
        if marker_position >= len(data):
            break
        marker = data[marker_position]
        if marker == 0x00 or 0xD0 <= marker <= 0xD7:
            position = marker_position + 1
            continue
        return marker_offset
    raise ValueError("JPEG entropy data ends before EOI")


def _parse_exif_payload(payload: bytes) -> tuple[TiffTag, ...]:
    """Parse TIFF IFDs from one EXIF APP1 payload.

    Args:
        payload: APP1 payload beginning with ``Exif\0\0``.

    Returns:
        Decoded TIFF tags.

    Raises:
        ValueError: If the TIFF header, entries, offsets, or pointers are invalid.
    """
    if not payload.startswith(b"Exif\x00\x00"):
        raise ValueError("EXIF payload is missing its identifier")
    tiff = payload[6:]
    if len(tiff) < 8:
        raise ValueError("EXIF TIFF header is truncated")
    byte_order = tiff[:2]
    if byte_order == b"II":
        endian = "<"
    elif byte_order == b"MM":
        endian = ">"
    else:
        raise ValueError("EXIF TIFF byte order must be II or MM")
    if _unpack_integer(tiff, 2, 2, endian, signed=False) != 42:
        raise ValueError("EXIF TIFF magic must be 42")

    first_ifd_offset = _unpack_integer(tiff, 4, 4, endian, signed=False)
    if first_ifd_offset == 0:
        raise ValueError("EXIF TIFF IFD0 offset must be non-zero")
    visited_offsets: set[int] = set()
    return _parse_tiff_ifd(
        tiff,
        endian,
        first_ifd_offset,
        "IFD0",
        visited_offsets,
        depth=0,
    )


def _parse_tiff_ifd(
    tiff: bytes,
    endian: str,
    offset: int,
    ifd_name: str,
    visited_offsets: set[int],
    depth: int,
) -> tuple[TiffTag, ...]:
    """Parse one TIFF IFD and its supported child pointers.

    Args:
        tiff: Complete TIFF byte stream.
        endian: Struct byte-order prefix.
        offset: IFD offset relative to the TIFF header.
        ifd_name: Logical name assigned to this IFD.
        visited_offsets: Per-audit set used to reject pointer cycles.
        depth: Current IFD recursion depth.

    Returns:
        Tags from this IFD and recursively referenced IFDs.

    Raises:
        ValueError: If limits, entries, offsets, types, or pointers are invalid.
    """
    if depth > MAX_IFD_DEPTH:
        raise ValueError("TIFF IFD nesting exceeds the safety limit")
    if offset in visited_offsets:
        raise ValueError(f"TIFF IFD pointer cycle at offset {offset}")
    visited_offsets.add(offset)

    entry_count = _unpack_integer(tiff, offset, 2, endian, signed=False)
    if entry_count > MAX_IFD_ENTRIES:
        raise ValueError(f"TIFF IFD has too many entries: {entry_count}")
    table_size = 2 + (entry_count * 12) + 4
    _checked_slice(tiff, offset, table_size, f"TIFF {ifd_name} table")

    current_tags: list[TiffTag] = []
    for entry_index in range(entry_count):
        entry_offset = offset + 2 + (entry_index * 12)
        entry = _checked_slice(tiff, entry_offset, 12, f"TIFF {ifd_name} entry")
        tag_id = int.from_bytes(entry[0:2], "little" if endian == "<" else "big")
        field_type = int.from_bytes(entry[2:4], "little" if endian == "<" else "big")
        value_count = int.from_bytes(entry[4:8], "little" if endian == "<" else "big")
        if field_type not in TIFF_TYPE_SIZES:
            raise ValueError(f"Unsupported TIFF field type {field_type} for tag 0x{tag_id:04x}")
        if value_count > MAX_TIFF_VALUES:
            raise ValueError(f"TIFF tag 0x{tag_id:04x} has too many values")

        value_size = TIFF_TYPE_SIZES[field_type] * value_count
        if value_size <= 4:
            raw_value = entry[8:8 + value_size]
        else:
            value_offset = int.from_bytes(
                entry[8:12],
                "little" if endian == "<" else "big",
            )
            raw_value = _checked_slice(
                tiff,
                value_offset,
                value_size,
                f"TIFF tag 0x{tag_id:04x} value",
            )
        values = _decode_tiff_values(raw_value, field_type, value_count, endian)
        current_tags.append(TiffTag(ifd_name, tag_id, field_type, values))

    all_tags = list(current_tags)
    child_pointer_names: dict[int, str] = {}
    if ifd_name == "IFD0":
        child_pointer_names = {0x8769: "ExifIFD", 0x8825: "GPSIFD"}
    elif ifd_name == "ExifIFD":
        child_pointer_names = {0xA005: "InteropIFD"}

    for pointer_tag_id, child_name in child_pointer_names.items():
        pointer_tags = [tag for tag in current_tags if tag.tag_id == pointer_tag_id]
        if len(pointer_tags) > 1:
            raise ValueError(f"Duplicate TIFF pointer tag 0x{pointer_tag_id:04x}")
        if not pointer_tags:
            continue
        if pointer_tags[0].field_type not in {4, 13}:
            raise ValueError(
                f"TIFF pointer tag 0x{pointer_tag_id:04x} must use LONG or IFD type"
            )
        child_offset = _single_integer_value(pointer_tags[0], "TIFF child pointer")
        if child_offset == 0:
            raise ValueError(f"TIFF pointer tag 0x{pointer_tag_id:04x} is zero")
        all_tags.extend(_parse_tiff_ifd(
            tiff,
            endian,
            child_offset,
            child_name,
            visited_offsets,
            depth + 1,
        ))

    next_offset_position = offset + 2 + (entry_count * 12)
    next_ifd_offset = _unpack_integer(
        tiff,
        next_offset_position,
        4,
        endian,
        signed=False,
    )
    if next_ifd_offset:
        next_name = "IFD1" if ifd_name == "IFD0" else f"{ifd_name}.next"
        all_tags.extend(_parse_tiff_ifd(
            tiff,
            endian,
            next_ifd_offset,
            next_name,
            visited_offsets,
            depth + 1,
        ))
    return tuple(all_tags)


def _decode_tiff_values(
    raw_value: bytes,
    field_type: int,
    value_count: int,
    endian: str,
) -> tuple[TiffScalar, ...]:
    """Decode immutable TIFF values from a validated value slice.

    Args:
        raw_value: Exact bytes occupied by the TIFF value.
        field_type: Numeric TIFF field type.
        value_count: Number of encoded values.
        endian: Struct byte-order prefix.

    Returns:
        Decoded values.

    Raises:
        ValueError: If a rational denominator is zero or the type is unsupported.
    """
    if field_type == 2:
        return (raw_value.rstrip(b"\x00").decode("latin-1"),)
    if field_type == 7:
        return (raw_value,)

    formats: dict[int, str] = {
        1: "B",
        3: "H",
        4: "I",
        6: "b",
        8: "h",
        9: "i",
        11: "f",
        12: "d",
        13: "I",
    }
    if field_type in formats:
        if value_count == 0:
            return ()
        return tuple(struct.unpack(f"{endian}{value_count}{formats[field_type]}", raw_value))

    if field_type not in {5, 10}:
        raise ValueError(f"Unsupported TIFF field type {field_type}")
    component_format = "I" if field_type == 5 else "i"
    values: list[Fraction] = []
    for index in range(value_count):
        numerator, denominator = struct.unpack_from(
            f"{endian}{component_format}{component_format}",
            raw_value,
            index * 8,
        )
        if denominator == 0:
            raise ValueError("TIFF rational denominator must be non-zero")
        values.append(Fraction(numerator, denominator))
    return tuple(values)


def _single_integer_value(tag: TiffTag, context: str) -> int:
    """Extract one non-negative integer from a TIFF pointer tag.

    Args:
        tag: Parsed TIFF tag.
        context: Human-readable error context.

    Returns:
        Pointer offset.

    Raises:
        ValueError: If the tag is not exactly one non-negative integer.
    """
    if len(tag.values) != 1:
        raise ValueError(f"{context} must contain exactly one value")
    value = tag.values[0]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{context} must be a non-negative integer")
    return value


def _parse_photoshop_resource_ids(payload: bytes) -> frozenset[int]:
    """Parse Photoshop APP13 image-resource identifiers.

    Args:
        payload: Complete APP13 payload beginning with ``Photoshop 3.0\0``.

    Returns:
        Resource identifiers present in the segment.

    Raises:
        ValueError: If resource framing, names, or data lengths are invalid.
    """
    prefix = b"Photoshop 3.0\x00"
    if not payload.startswith(prefix):
        raise ValueError("Photoshop APP13 identifier is missing")
    position = len(prefix)
    resource_ids: set[int] = set()

    while position < len(payload):
        signature = _checked_slice(payload, position, 4, "Photoshop resource signature")
        if signature != b"8BIM":
            raise ValueError(f"Unsupported Photoshop resource signature {signature!r}")
        resource_id = int.from_bytes(
            _checked_slice(payload, position + 4, 2, "Photoshop resource ID"),
            "big",
        )
        position += 6

        name_length = _checked_slice(payload, position, 1, "Photoshop resource name")[0]
        name_field_size = 1 + name_length
        if name_field_size % 2:
            name_field_size += 1
        _checked_slice(payload, position, name_field_size, "Photoshop resource name")
        position += name_field_size

        data_size = int.from_bytes(
            _checked_slice(payload, position, 4, "Photoshop resource data size"),
            "big",
        )
        position += 4
        padded_data_size = data_size + (data_size % 2)
        _checked_slice(payload, position, padded_data_size, "Photoshop resource data")
        position += padded_data_size
        resource_ids.add(resource_id)
    return frozenset(resource_ids)


def _inspect_jfif_payload(payload: bytes) -> tuple[bool, int]:
    """Validate JFIF APP0 framing and inspect its payload boundary.

    Args:
        payload: Complete JFIF APP0 payload.

    Returns:
        Thumbnail-presence flag and bytes following the declared thumbnail.

    Raises:
        ValueError: If the JFIF header or thumbnail data is truncated.
    """
    if len(payload) < 14:
        raise ValueError("JFIF APP0 segment is too short")
    width = payload[12]
    height = payload[13]
    required_size = 14 + (width * height * 3)
    if len(payload) < required_size:
        raise ValueError(
            f"JFIF APP0 is truncated: expected {required_size}, got {len(payload)}"
        )
    return width > 0 and height > 0, len(payload) - required_size


def _assemble_icc_profile(chunks: list[tuple[int, int, bytes]]) -> bytes | None:
    """Validate and reassemble ICC APP2 chunks.

    Args:
        chunks: Sequence number, total count, and data for every ICC APP2 chunk.

    Returns:
        Reassembled ICC bytes, or None when no chunks exist.

    Raises:
        ValueError: If numbering is inconsistent, duplicated, or incomplete.
    """
    if not chunks:
        return None
    totals = {total for _, total, _ in chunks}
    if len(totals) != 1:
        raise ValueError(f"ICC chunks disagree on total count: {sorted(totals)}")
    total = totals.pop()
    if total <= 0:
        raise ValueError("ICC chunk total must be positive")

    by_sequence: dict[int, bytes] = {}
    for sequence, chunk_total, chunk_data in chunks:
        if chunk_total != total or not 1 <= sequence <= total:
            raise ValueError(f"Invalid ICC chunk sequence {sequence}/{chunk_total}")
        if sequence in by_sequence:
            raise ValueError(f"Duplicate ICC chunk sequence {sequence}")
        by_sequence[sequence] = chunk_data
    expected_sequences = set(range(1, total + 1))
    if set(by_sequence) != expected_sequences:
        raise ValueError(
            f"Incomplete ICC chunks: expected {sorted(expected_sequences)}, "
            f"got {sorted(by_sequence)}"
        )
    return b"".join(by_sequence[index] for index in range(1, total + 1))


def _unpack_integer(
    data: bytes,
    offset: int,
    size: int,
    endian: str,
    signed: bool,
) -> int:
    """Read a bounded TIFF integer.

    Args:
        data: Complete TIFF byte stream.
        offset: Integer offset.
        size: Integer size in bytes; only 2 and 4 are accepted.
        endian: Struct byte-order prefix.
        signed: Whether the integer is signed.

    Returns:
        Decoded integer.

    Raises:
        ValueError: If size, endian, or bounds are invalid.
    """
    if size not in {2, 4}:
        raise ValueError(f"Unsupported integer size: {size}")
    if endian not in {"<", ">"}:
        raise ValueError(f"Unsupported TIFF endian prefix: {endian}")
    value_bytes = _checked_slice(data, offset, size, "TIFF integer")
    return int.from_bytes(
        value_bytes,
        "little" if endian == "<" else "big",
        signed=signed,
    )


def _checked_slice(data: bytes, offset: int, size: int, context: str) -> bytes:
    """Return a bounded byte slice or fail closed.

    Args:
        data: Source bytes.
        offset: Non-negative slice offset.
        size: Non-negative slice size.
        context: Human-readable error context.

    Returns:
        Exact requested bytes.

    Raises:
        ValueError: If inputs are invalid or the slice exceeds data bounds.
    """
    if not isinstance(data, bytes):
        raise ValueError("data must be bytes")
    if not isinstance(offset, int) or not isinstance(size, int):
        raise ValueError("offset and size must be integers")
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise ValueError(
            f"{context} exceeds bounds: offset={offset}, size={size}, "
            f"available={len(data)}"
        )
    return data[offset:offset + size]
