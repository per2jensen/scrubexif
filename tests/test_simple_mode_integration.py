# SPDX-License-Identifier: GPL-3.0-or-later
"""
Integration test for default safe mode using the real exiftool binary.

Proves that:
  - the output directory is automatically created
  - .jpg, .jpeg, .JPG and .JPEG files are all processed (exiftool succeeds)
  - the existing files are not modified in any way (contents unchanged)
"""

from __future__ import annotations

from pathlib import Path
import base64

import pytest

from scrubexif import scrub


# 1x1 white JPEG, generated once and embedded as base64 so we don't rely on external tools
_SMALL_JPEG_BASE64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIA"
    "AhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEB"
    "AQAAAAAAAAAAAAAAAAAAAAb/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCfAAH/"
    "2Q=="
)


def _write_small_jpeg(path: Path) -> bytes:
    data = base64.b64decode(_SMALL_JPEG_BASE64)
    path.write_bytes(data)
    return data


@pytest.mark.integration
def test_simple_mode_scrubs_all_jpeg_variants_and_preserves_originals(tmp_path, monkeypatch):
    # Set up a fake /photos tree under a temporary directory
    photos_root = tmp_path / "photos"
    photos_root.mkdir()

    output_dir = photos_root / "output"
    input_dir = photos_root / "input"
    processed_dir = photos_root / "processed"
    errors_dir = photos_root / "errors"

    # Point scrubexif.scrub globals at our temp tree
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", photos_root)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(scrub, "INPUT_DIR", input_dir)
    monkeypatch.setattr(scrub, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(scrub, "ERRORS_DIR", errors_dir)

    # Create four *valid* JPEGs with different extensions directly under PHOTOS_ROOT
    exts = ["jpg", "jpeg", "JPG", "JPEG"]
    originals: dict[Path, bytes] = {}

    for ext in exts:
        p = photos_root / f"file.{ext}"
        originals[p] = _write_small_jpeg(p)

    # Run default safe mode with the real exiftool (no monkeypatch of subprocess.run)
    summary = scrub.ScrubSummary()
    scrub.simple_scrub(
        summary=summary,
        recursive=False,
        dry_run=False,
        show_tags_mode=None,
        paranoia=True,
        max_files=None,
        on_duplicate="delete",
    )

    # 1) Output directory is automatically created
    assert output_dir.exists() and output_dir.is_dir()

    # 2) All four files are processed (exiftool succeeded) without errors
    assert summary.scrubbed == len(exts)
    assert summary.total == len(exts)
    assert summary.errors == 0

    # 3) Originals are not modified in any way: still exist and bytes unchanged
    for path, original_bytes in originals.items():
        assert path.exists(), f"Original file missing after default mode: {path}"
        assert path.read_bytes() == original_bytes, f"Original file modified: {path}"

    # Sanity: each corresponding output file exists and is non-empty
    for ext in exts:
        out_file = output_dir / f"file.{ext}"
        assert out_file.exists(), f"Missing scrubbed output file: {out_file}"
        out_bytes = out_file.read_bytes()
        assert len(out_bytes) > 0, f"Scrubbed output is empty: {out_file}"
