# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests against real camera files in tests/private-assets/.

These tests are excluded from the default pytest run (see pytest.ini).
Run explicitly with:

    pytest -m private
    pytest -m private -v

Each file in tests/private-assets/ is tested automatically. Coverage includes
metadata removal, exact whitelist and ICC preservation, embedded-image removal,
visual equivalence, lossless processing, and byte-for-byte idempotency. A batch
test also exercises the real container with all private assets together. A
standard-library JPEG/TIFF auditor cross-checks ExifTool independently.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from scrubexif.scrub import TAGS_TO_EXTRACT, _do_scrub_pipeline, run_jpegtran
from tests._docker import mk_mounts, run_container
from tests._jpeg_audit import JpegAudit, audit_jpeg, normal_mode_violations

PRIVATE_ASSETS_DIR = Path(__file__).resolve().parent / "private-assets"
EXIFTOOL = shutil.which("exiftool")
JPEGTRAN = shutil.which("jpegtran")

skipif_no_jpegtran = pytest.mark.skipif(not JPEGTRAN, reason="jpegtran not installed")
skipif_no_exiftool = pytest.mark.skipif(not EXIFTOOL, reason="exiftool not installed")

ALLOWED_EXIF_OUTPUT_TAGS = frozenset(TAGS_TO_EXTRACT) | {
    "ColorSpace",
    "ComponentsConfiguration",
    "ExifVersion",
    "FlashpixVersion",
    "ResolutionUnit",
    "XResolution",
    "YCbCrPositioning",
    "YResolution",
}


def _private_jpegs() -> list[Path]:
    """Return all private JPEG files in deterministic order.

    Returns:
        JPEG paths found directly under tests/private-assets/.
    """
    if not PRIVATE_ASSETS_DIR.exists():
        return []
    return sorted(
        p for p in PRIVATE_ASSETS_DIR.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg"} and p.is_file()
    )


def _get_jpeg_app_markers(path: Path) -> set[int]:
    """Parse APP marker numbers from a raw JPEG marker stream.

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


def _read_exiftool_json(
    path: Path,
    arguments: list[str] | None = None,
) -> dict[str, object]:
    """Read one image's ExifTool JSON record.

    Args:
        path: Existing image path to inspect.
        arguments: Optional ExifTool arguments placed before the image path.

    Returns:
        Parsed ExifTool record for the image.

    Raises:
        ValueError: If path is not a file or ExifTool returns no single record.
        RuntimeError: If ExifTool is unavailable.
        subprocess.CalledProcessError: If ExifTool exits unsuccessfully.
        json.JSONDecodeError: If ExifTool emits invalid JSON.
    """
    if not path.is_file():
        raise ValueError(f"path must be an existing file: {path}")
    if EXIFTOOL is None:
        raise RuntimeError("exiftool is required to inspect private assets")

    command = [EXIFTOOL, "-j", *(arguments or []), str(path)]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    records = json.loads(result.stdout)
    if len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError(f"Expected one ExifTool record for {path}, got {records!r}")
    return records[0]


def _extract_binary_metadata(path: Path, tag_name: str) -> bytes:
    """Extract one raw metadata block through ExifTool.

    Args:
        path: Existing image path to inspect.
        tag_name: Non-empty ExifTool tag or group name without a leading dash.

    Returns:
        Raw metadata bytes, or empty bytes when the block is absent.

    Raises:
        ValueError: If path or tag_name is invalid.
        RuntimeError: If ExifTool is unavailable.
        subprocess.CalledProcessError: If ExifTool exits unsuccessfully.
    """
    if not path.is_file():
        raise ValueError(f"path must be an existing file: {path}")
    if not isinstance(tag_name, str) or not tag_name or tag_name.startswith("-"):
        raise ValueError("tag_name must be a non-empty name without a leading dash")
    if EXIFTOOL is None:
        raise RuntimeError("exiftool is required to extract binary metadata")

    result = subprocess.run(
        [EXIFTOOL, "-b", f"-{tag_name}", str(path)],
        check=True,
        capture_output=True,
    )
    return result.stdout


def _extract_icc_profile(path: Path) -> bytes:
    """Extract an image's raw ICC profile.

    Args:
        path: Existing image path to inspect.

    Returns:
        Raw ICC profile bytes, or empty bytes when no profile exists.

    Raises:
        ValueError: If path is not a file.
        RuntimeError: If ExifTool is unavailable.
        subprocess.CalledProcessError: If ExifTool exits unsuccessfully.
    """
    return _extract_binary_metadata(path, "ICC_Profile")


def _selected_tag_values(path: Path) -> dict[str, object]:
    """Read numeric values for the scrub pipeline's approved EXIF tags.

    Args:
        path: Existing image path to inspect.

    Returns:
        Approved tags present in the image, mapped to numeric values.

    Raises:
        ValueError: If path is invalid or ExifTool returns invalid data.
        RuntimeError: If ExifTool is unavailable.
        subprocess.CalledProcessError: If ExifTool exits unsuccessfully.
        json.JSONDecodeError: If ExifTool emits invalid JSON.
    """
    tag_arguments = ["-n", *(f"-{tag}" for tag in TAGS_TO_EXTRACT)]
    data = _read_exiftool_json(path, tag_arguments)
    return {tag: data[tag] for tag in TAGS_TO_EXTRACT if tag in data}


def _embedded_metadata_keys(tags: dict[str, object]) -> set[str]:
    """Find metadata keys associated with secondary embedded images.

    Args:
        tags: ExifTool family-one grouped JSON record.

    Returns:
        Keys belonging to thumbnail, preview, or MPF image groups.
    """
    embedded_groups = ("IFD1", "PreviewIFD", "MPF", "MPImage")
    embedded_tag_names = (
        "ThumbnailImage",
        "PreviewImage",
        "JpgFromRaw",
        "OtherImage",
        "MPImage",
    )
    matches: set[str] = set()
    for key in tags:
        group, _, tag_name = key.partition(":")
        if (
            group.startswith(embedded_groups)
            or tag_name.startswith(embedded_tag_names)
        ):
            matches.add(key)
    return matches


def _privacy_forbidden_keys(
    family_zero_tags: dict[str, object],
    family_one_tags: dict[str, object],
) -> set[str]:
    """Find metadata keys forbidden from normal-mode output.

    Args:
        family_zero_tags: ExifTool record produced with ``-G0``.
        family_one_tags: ExifTool record produced with ``-G1``.

    Returns:
        GPS, IPTC, XMP, MakerNotes, MPF, and non-approved EXIF keys.
    """
    forbidden: set[str] = set()
    forbidden_family_zero_groups = {"IPTC", "XMP", "MakerNotes", "MPF"}

    for key in family_zero_tags:
        group, _, tag_name = key.partition(":")
        if group in forbidden_family_zero_groups:
            forbidden.add(key)
            continue
        if group == "EXIF" and tag_name not in ALLOWED_EXIF_OUTPUT_TAGS:
            forbidden.add(key)

    for key in family_one_tags:
        group, _, tag_name = key.partition(":")
        if group == "GPS" or tag_name.startswith("GPS"):
            forbidden.add(key)

    return forbidden


def _assert_audit_matches_exiftool_values(
    audit: JpegAudit,
    exiftool_values: dict[str, object],
    context: str,
) -> None:
    """Assert that independent TIFF values agree with ExifTool numerically.

    Args:
        audit: Independently parsed JPEG audit.
        exiftool_values: Numeric values reported by ExifTool ``-n``.
        context: Filename or phase included in assertion diagnostics.

    Returns:
        None.

    Raises:
        ValueError: If ExifTool reports a non-numeric or multi-value tag.
    """
    audit_values = audit.approved_tag_values()
    assert set(audit_values) == set(exiftool_values), (
        f"{context}: independent and ExifTool tag sets differ: "
        f"audit={sorted(audit_values)}, exiftool={sorted(exiftool_values)}"
    )
    for tag_name, exiftool_value in exiftool_values.items():
        if (
            isinstance(exiftool_value, bool)
            or not isinstance(exiftool_value, (int, float))
        ):
            raise ValueError(
                f"{context}: ExifTool tag {tag_name} is non-numeric: "
                f"{exiftool_value!r}"
            )
        independent_values = audit_values[tag_name]
        if len(independent_values) != 1:
            raise ValueError(
                f"{context}: tag {tag_name} has {len(independent_values)} values"
            )
        assert float(independent_values[0]) == pytest.approx(
            float(exiftool_value),
            rel=1e-9,
            abs=1e-12,
        ), f"{context}: independent and ExifTool values differ for {tag_name}"


def _assert_audit_tag_values_equivalent(
    expected: dict[str, tuple[Fraction, ...]],
    actual: dict[str, tuple[Fraction, ...]],
    context: str,
) -> None:
    """Assert strict numeric equivalence between two independent TIFF audits.

    Args:
        expected: Approved values from the source audit.
        actual: Approved values from the scrubbed-output audit.
        context: Filename included in assertion diagnostics.

    Returns:
        None.
    """
    assert set(actual) == set(expected), (
        f"{context}: approved TIFF tag sets changed: "
        f"before={sorted(expected)}, after={sorted(actual)}"
    )
    for tag_name, expected_values in expected.items():
        actual_values = actual[tag_name]
        assert len(actual_values) == len(expected_values), (
            f"{context}: value count changed for {tag_name}"
        )
        for expected_value, actual_value in zip(expected_values, actual_values):
            assert float(actual_value) == pytest.approx(
                float(expected_value),
                rel=1e-9,
                abs=1e-12,
            ), f"{context}: independent TIFF value changed for {tag_name}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.private
@skipif_no_jpegtran
@pytest.mark.parametrize("jpeg_path", _private_jpegs(), ids=lambda p: p.name)
def test_paranoia_only_app0_survives(jpeg_path: Path, tmp_path: Path) -> None:
    """Confirm that only APP0 remains after a paranoia scrub.

    Args:
        jpeg_path: Path to the real camera JPEG under test.
        tmp_path: pytest-provided temporary directory.

    Returns:
        None.
    """
    out = tmp_path / jpeg_path.name
    run_jpegtran(jpeg_path, out)
    markers = _get_jpeg_app_markers(out)
    audit = audit_jpeg(out)
    assert markers == {0}, (
        f"{jpeg_path.name}: expected only APP0 after paranoia strip, "
        f"got APP markers: {markers}"
    )
    assert normal_mode_violations(audit) == (), (
        f"{jpeg_path.name}: independent auditor found metadata in paranoia output"
    )


@pytest.mark.private
@skipif_no_exiftool
@pytest.mark.parametrize("jpeg_path", _private_jpegs(), ids=lambda p: p.name)
def test_exiftool_agrees_with_independent_source_audit(jpeg_path: Path) -> None:
    """Cross-check ExifTool against the independent binary parser.

    Args:
        jpeg_path: Path to the real camera JPEG under test.

    Returns:
        None.
    """
    audit = audit_jpeg(jpeg_path)
    family_zero = _read_exiftool_json(jpeg_path, ["-G0"])
    family_one = _read_exiftool_json(jpeg_path, ["-G1"])
    family_zero_groups = {
        key.partition(":")[0]
        for key in family_zero
        if ":" in key
    }
    gps_reported = any(
        key.partition(":")[0] == "GPS"
        or key.partition(":")[2].startswith("GPS")
        for key in family_one
    )
    exiftool_categories = {
        "GPS": gps_reported,
        "IPTC": "IPTC" in family_zero_groups,
        # ExifTool can extract a valid XMP packet containing no recognized
        # properties, so raw extraction is a stronger presence check than JSON.
        "XMP": bool(_extract_binary_metadata(jpeg_path, "XMP")),
        "MakerNotes": "MakerNotes" in family_zero_groups,
        "MPF": "MPF" in family_zero_groups,
    }
    independent_categories = {
        "GPS": audit.gps_present(),
        "IPTC": audit.iptc_segment_count > 0,
        "XMP": audit.xmp_segment_count > 0,
        "MakerNotes": audit.maker_note_present(),
        "MPF": audit.mpf_segment_count > 0,
    }
    assert exiftool_categories == independent_categories, (
        f"{jpeg_path.name}: ExifTool and independent category detection differ: "
        f"exiftool={exiftool_categories}, independent={independent_categories}"
    )

    exiftool_values = _selected_tag_values(jpeg_path)
    _assert_audit_matches_exiftool_values(
        audit,
        exiftool_values,
        jpeg_path.name,
    )
    assert (audit.icc_profile or b"") == _extract_icc_profile(jpeg_path), (
        f"{jpeg_path.name}: ExifTool and independent ICC extraction differ"
    )


@pytest.mark.private
@skipif_no_jpegtran
@skipif_no_exiftool
@pytest.mark.parametrize("jpeg_path", _private_jpegs(), ids=lambda p: p.name)
def test_normal_strips_gps_iptc_xmp_makernotes(jpeg_path: Path, tmp_path: Path) -> None:
    """Remove all non-approved private metadata in normal mode.

    Args:
        jpeg_path: Path to the real camera JPEG under test.
        tmp_path: pytest-provided temporary directory.

    Returns:
        None.
    """
    out = tmp_path / jpeg_path.name
    _do_scrub_pipeline(jpeg_path, out, paranoia=False,
                       copyright_text=None, comment_text=None)

    source_family_zero = _read_exiftool_json(jpeg_path, ["-G0"])
    source_family_one = _read_exiftool_json(jpeg_path, ["-G1"])
    source_forbidden = _privacy_forbidden_keys(
        source_family_zero,
        source_family_one,
    )
    if not source_forbidden:
        pytest.skip(f"{jpeg_path.name}: no private metadata found in source")

    output_family_zero = _read_exiftool_json(out, ["-G0"])
    output_family_one = _read_exiftool_json(out, ["-G1"])
    output_forbidden = _privacy_forbidden_keys(
        output_family_zero,
        output_family_one,
    )
    independent_violations = normal_mode_violations(audit_jpeg(out))
    assert not output_forbidden, (
        f"{jpeg_path.name}: forbidden metadata survived: "
        f"{sorted(output_forbidden)}"
    )
    assert not independent_violations, (
        f"{jpeg_path.name}: independent auditor found privacy violations: "
        f"{independent_violations}"
    )


@pytest.mark.private
@skipif_no_jpegtran
@skipif_no_exiftool
@pytest.mark.parametrize("jpeg_path", _private_jpegs(), ids=lambda p: p.name)
def test_normal_preserves_whitelist_tag_values(
    jpeg_path: Path,
    tmp_path: Path,
) -> None:
    """Preserve exact numeric whitelist values in normal mode.

    Args:
        jpeg_path: Path to the real camera JPEG under test.
        tmp_path: pytest-provided temporary directory.

    Returns:
        None.
    """
    tags_before = _selected_tag_values(jpeg_path)
    if not tags_before:
        pytest.skip(f"{jpeg_path.name}: no whitelist tags found in source")

    out = tmp_path / jpeg_path.name
    _do_scrub_pipeline(jpeg_path, out, paranoia=False,
                       copyright_text=None, comment_text=None)

    tags_after = _selected_tag_values(out)
    assert tags_after == tags_before, (
        f"{jpeg_path.name}: whitelist values changed after scrub: "
        f"before={tags_before}, after={tags_after}"
    )
    source_audit = audit_jpeg(jpeg_path)
    output_audit = audit_jpeg(out)
    _assert_audit_matches_exiftool_values(source_audit, tags_before, jpeg_path.name)
    _assert_audit_matches_exiftool_values(output_audit, tags_after, jpeg_path.name)
    _assert_audit_tag_values_equivalent(
        source_audit.approved_tag_values(),
        output_audit.approved_tag_values(),
        jpeg_path.name,
    )


@pytest.mark.private
@skipif_no_jpegtran
@skipif_no_exiftool
@pytest.mark.parametrize("jpeg_path", _private_jpegs(), ids=lambda p: p.name)
def test_normal_preserves_icc_profile_bytes(
    jpeg_path: Path,
    tmp_path: Path,
) -> None:
    """Preserve an ICC profile exactly and never introduce one.

    Args:
        jpeg_path: Path to the real camera JPEG under test.
        tmp_path: Pytest-provided temporary directory.

    Returns:
        None.
    """
    profile_before = _extract_icc_profile(jpeg_path)
    out = tmp_path / jpeg_path.name

    _do_scrub_pipeline(
        jpeg_path,
        out,
        paranoia=False,
        copyright_text=None,
        comment_text=None,
    )

    profile_after = _extract_icc_profile(out)
    assert profile_after == profile_before, (
        f"{jpeg_path.name}: ICC profile changed during normal scrub"
    )
    source_audit_profile = audit_jpeg(jpeg_path).icc_profile or b""
    output_audit_profile = audit_jpeg(out).icc_profile or b""
    assert source_audit_profile == profile_before, (
        f"{jpeg_path.name}: independent source ICC extraction disagrees with ExifTool"
    )
    assert output_audit_profile == profile_after, (
        f"{jpeg_path.name}: independent output ICC extraction disagrees with ExifTool"
    )


@pytest.mark.private
@skipif_no_jpegtran
@skipif_no_exiftool
@pytest.mark.parametrize("jpeg_path", _private_jpegs(), ids=lambda p: p.name)
def test_normal_removes_embedded_secondary_images(
    jpeg_path: Path,
    tmp_path: Path,
) -> None:
    """Remove thumbnails, previews, and MPF auxiliary images.

    Args:
        jpeg_path: Path to the real camera JPEG under test.
        tmp_path: Pytest-provided temporary directory.

    Returns:
        None.
    """
    source_tags = _read_exiftool_json(jpeg_path, ["-G1"])
    source_embedded = _embedded_metadata_keys(source_tags)
    if not source_embedded:
        pytest.skip(f"{jpeg_path.name}: no embedded secondary image found")

    out = tmp_path / jpeg_path.name
    _do_scrub_pipeline(
        jpeg_path,
        out,
        paranoia=False,
        copyright_text=None,
        comment_text=None,
    )

    output_tags = _read_exiftool_json(out, ["-G1"])
    output_embedded = _embedded_metadata_keys(output_tags)
    output_audit = audit_jpeg(out)
    assert not output_embedded, (
        f"{jpeg_path.name}: embedded image metadata survived: "
        f"{sorted(output_embedded)}"
    )
    assert not output_audit.embedded_image_present(), (
        f"{jpeg_path.name}: independent auditor found an embedded image"
    )


@pytest.mark.private
@skipif_no_jpegtran
@skipif_no_exiftool
@pytest.mark.parametrize("jpeg_path", _private_jpegs(), ids=lambda p: p.name)
def test_normal_preserves_raw_and_rendered_pixels(
    jpeg_path: Path,
    tmp_path: Path,
) -> None:
    """Preserve both stored pixels and orientation-aware rendered pixels.

    Args:
        jpeg_path: Path to the real camera JPEG under test.
        tmp_path: Pytest-provided temporary directory.

    Returns:
        None.
    """
    image_module = pytest.importorskip("PIL.Image")
    image_ops_module = pytest.importorskip("PIL.ImageOps")

    out = tmp_path / jpeg_path.name
    _do_scrub_pipeline(
        jpeg_path,
        out,
        paranoia=False,
        copyright_text=None,
        comment_text=None,
    )

    with image_module.open(jpeg_path) as source_image:
        source_image.load()
        raw_before = (
            source_image.size,
            source_image.mode,
            source_image.tobytes(),
        )
        with image_ops_module.exif_transpose(source_image) as rendered_source:
            rendered_source.load()
            rendered_before = (
                rendered_source.size,
                rendered_source.mode,
                rendered_source.tobytes(),
            )

    with image_module.open(out) as output_image:
        output_image.load()
        raw_after = (
            output_image.size,
            output_image.mode,
            output_image.tobytes(),
        )
        with image_ops_module.exif_transpose(output_image) as rendered_output:
            rendered_output.load()
            rendered_after = (
                rendered_output.size,
                rendered_output.mode,
                rendered_output.tobytes(),
            )

    assert raw_after == raw_before, (
        f"{jpeg_path.name}: stored pixels changed during normal scrub"
    )
    assert rendered_after == rendered_before, (
        f"{jpeg_path.name}: orientation-aware rendering changed during normal scrub"
    )


@pytest.mark.private
@pytest.mark.docker
@skipif_no_exiftool
def test_container_batch_processes_private_assets_safely(tmp_path: Path) -> None:
    """Process every private asset together through the real container.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        None.
    """
    source_paths = _private_jpegs()
    if not source_paths:
        pytest.skip("No private JPEG assets found")

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    errors_dir = tmp_path / "errors"
    for directory in (input_dir, output_dir, processed_dir, errors_dir):
        directory.mkdir()

    original_contents: dict[str, bytes] = {}
    for source_path in source_paths:
        destination = input_dir / source_path.name
        shutil.copyfile(source_path, destination)
        original_contents[source_path.name] = source_path.read_bytes()

    mounts = mk_mounts(input_dir, output_dir, processed_dir)
    mounts += ["-v", f"{errors_dir}:/photos/errors"]
    result = run_container(
        mounts=mounts,
        args=["--from-input"],
        capture_output=True,
    )

    assert result.returncode == 0, (
        f"Container failed with {result.returncode}:\n"
        f"{result.stderr}\n{result.stdout}"
    )

    expected_names = set(original_contents)
    output_names = {path.name for path in output_dir.iterdir()}
    processed_names = {path.name for path in processed_dir.iterdir()}
    assert output_names == expected_names, "Container output set did not match inputs"
    assert processed_names == expected_names, "Not every original was archived"
    assert not any(input_dir.iterdir()), "Container left processed files in input/"
    assert not any(errors_dir.iterdir()), "Container unexpectedly populated errors/"

    for name, original_content in original_contents.items():
        assert (processed_dir / name).read_bytes() == original_content, (
            f"Container modified archived original: {name}"
        )
        source_path = PRIVATE_ASSETS_DIR / name
        output_path = output_dir / name
        markers = _get_jpeg_app_markers(output_path)
        output_audit = audit_jpeg(output_path)
        audit_violations = normal_mode_violations(output_audit)
        assert markers <= {0, 1, 2}, (
            f"{name}: normal output retained unexpected APP markers {markers}"
        )
        assert audit_violations == (), (
            f"{name}: independent auditor rejected container output: "
            f"{audit_violations}"
        )

        output_family_zero = _read_exiftool_json(output_path, ["-G0"])
        output_family_one = _read_exiftool_json(output_path, ["-G1"])
        output_forbidden = _privacy_forbidden_keys(
            output_family_zero,
            output_family_one,
        )
        assert not output_forbidden, (
            f"{name}: container output retained forbidden metadata: "
            f"{sorted(output_forbidden)}"
        )
        source_values = _selected_tag_values(source_path)
        output_values = _selected_tag_values(output_path)
        assert output_values == source_values, (
            f"{name}: container changed approved EXIF values"
        )
        _assert_audit_matches_exiftool_values(output_audit, output_values, name)
        _assert_audit_tag_values_equivalent(
            audit_jpeg(source_path).approved_tag_values(),
            output_audit.approved_tag_values(),
            name,
        )

        source_profile = _extract_icc_profile(source_path)
        output_profile = _extract_icc_profile(output_path)
        assert output_profile == source_profile, (
            f"{name}: container changed the ICC profile"
        )
        assert (output_audit.icc_profile or b"") == output_profile, (
            f"{name}: independent container ICC extraction disagrees with ExifTool"
        )

    stdout = result.stdout or ""
    expected_summary = (
        f"SCRUBEXIF_SUMMARY total={len(source_paths)} "
        f"scrubbed={len(source_paths)} skipped=0 errors=0 "
        "duplicates_deleted=0 duplicates_moved=0"
    )
    assert expected_summary in stdout, f"Unexpected container summary:\n{stdout}"


@pytest.mark.private
@skipif_no_jpegtran
@skipif_no_exiftool
@pytest.mark.parametrize("jpeg_path", _private_jpegs(), ids=lambda p: p.name)
def test_normal_pipeline_is_byte_idempotent(
    jpeg_path: Path,
    tmp_path: Path,
) -> None:
    """Produce an identical file when normal output is scrubbed again.

    Args:
        jpeg_path: Path to the real camera JPEG under test.
        tmp_path: Pytest-provided temporary directory.

    Returns:
        None.
    """
    original_content = jpeg_path.read_bytes()
    first_output = tmp_path / "first" / jpeg_path.name
    second_output = tmp_path / "second" / jpeg_path.name
    first_output.parent.mkdir()
    second_output.parent.mkdir()

    _do_scrub_pipeline(
        jpeg_path,
        first_output,
        paranoia=False,
        copyright_text=None,
        comment_text=None,
    )
    _do_scrub_pipeline(
        first_output,
        second_output,
        paranoia=False,
        copyright_text=None,
        comment_text=None,
    )

    assert second_output.read_bytes() == first_output.read_bytes(), (
        f"{jpeg_path.name}: second normal scrub changed the first output"
    )
    assert jpeg_path.read_bytes() == original_content, (
        f"{jpeg_path.name}: private source asset was modified"
    )


@pytest.mark.private
@skipif_no_jpegtran
@pytest.mark.parametrize("jpeg_path", _private_jpegs(), ids=lambda p: p.name)
def test_lossless_pixel_preservation(jpeg_path: Path, tmp_path: Path) -> None:
    """Preserve decoded pixels byte-for-byte in paranoia mode.

    Args:
        jpeg_path: Path to the real camera JPEG under test.
        tmp_path: pytest-provided temporary directory.

    Returns:
        None.
    """
    image_module = pytest.importorskip("PIL.Image")

    out = tmp_path / jpeg_path.name
    run_jpegtran(jpeg_path, out)

    with image_module.open(jpeg_path) as source_image:
        source_image.load()
        pixels_before = source_image.tobytes()
    with image_module.open(out) as output_image:
        output_image.load()
        pixels_after = output_image.tobytes()

    assert pixels_after == pixels_before, (
        f"{jpeg_path.name}: pixel data changed after jpegtran strip — "
        "transform is not lossless"
    )
