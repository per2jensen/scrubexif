# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests for _format_path_with_host and _format_relative_path_with_host.

Covers:
  - Path under PHOTOS_ROOT: resolved via parent mount
  - Path outside PHOTOS_ROOT: resolved via its own mount (e.g. -o /scrubbed bind-mount)
  - Path outside PHOTOS_ROOT with no mount entry: falls back to container path
  - SHOW_CONTAINER_PATHS=True variants
  - Integration: Output directory banner shows physical host path
"""

import sys
from pathlib import Path

import pytest

from scrubexif import scrub


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mount_resolver(photos_host: str, external_mounts: dict[str, str]):
    """
    Return a mock for _resolve_mount_source that maps:
      - PHOTOS_ROOT  → photos_host
      - any path in external_mounts → the corresponding host path
    """
    def _resolve(path: Path) -> str | None:
        if path == scrub.PHOTOS_ROOT:
            return photos_host
        return external_mounts.get(str(path))
    return _resolve


# ---------------------------------------------------------------------------
# Paths under PHOTOS_ROOT (existing behaviour, must not regress)
# ---------------------------------------------------------------------------

def test_format_paths_default_host_only(monkeypatch):
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", Path("/photos"))
    monkeypatch.setattr(scrub, "_resolve_mount_source", lambda _p: "/host/root")
    monkeypatch.setattr(scrub, "SHOW_CONTAINER_PATHS", False)

    target = Path("/photos/input/file.jpg")

    assert scrub._format_path_with_host(target) == "/host/root/input/file.jpg"
    assert scrub._format_relative_path_with_host(target) == "/host/root/input/file.jpg"


def test_format_paths_show_container_and_host(monkeypatch):
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", Path("/photos"))
    monkeypatch.setattr(scrub, "_resolve_mount_source", lambda _p: "/host/root")
    monkeypatch.setattr(scrub, "SHOW_CONTAINER_PATHS", True)

    target = Path("/photos/input/file.jpg")

    assert (
        scrub._format_path_with_host(target)
        == "/photos/input/file.jpg (host: /host/root/input/file.jpg)"
    )
    assert (
        scrub._format_relative_path_with_host(target)
        == "input/file.jpg (host: /host/root/input/file.jpg)"
    )


# ---------------------------------------------------------------------------
# Path outside PHOTOS_ROOT — own bind-mount resolved to host path
# Simulates: -v "$PWD:/photos" -v "/tmp/scrub-test:/scrubbed" -o /scrubbed
# ---------------------------------------------------------------------------

def test_format_path_outside_photos_root_resolves_own_mount(monkeypatch):
    """-o /scrubbed with a separate bind-mount must show the host path /tmp/scrub-test."""
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", Path("/photos"))
    monkeypatch.setattr(scrub, "SHOW_CONTAINER_PATHS", False)
    monkeypatch.setattr(
        scrub, "_resolve_mount_source",
        _mount_resolver("/pj/tmp/scrub", {"/scrubbed": "/tmp/scrub-test"}),
    )

    target = Path("/scrubbed")

    assert scrub._format_path_with_host(target) == "/tmp/scrub-test"
    assert scrub._format_relative_path_with_host(target) == "/tmp/scrub-test"


def test_format_path_outside_photos_root_show_container_and_host(monkeypatch):
    """With SHOW_CONTAINER_PATHS=True the container path and host path are both shown."""
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", Path("/photos"))
    monkeypatch.setattr(scrub, "SHOW_CONTAINER_PATHS", True)
    monkeypatch.setattr(
        scrub, "_resolve_mount_source",
        _mount_resolver("/pj/tmp/scrub", {"/scrubbed": "/tmp/scrub-test"}),
    )

    target = Path("/scrubbed")

    assert scrub._format_path_with_host(target) == "/scrubbed (host: /tmp/scrub-test)"
    assert scrub._format_relative_path_with_host(target) == "/scrubbed (host: /tmp/scrub-test)"


def test_format_path_outside_photos_root_no_mount_falls_back(monkeypatch):
    """If no mount entry exists for the path, fall back to the container path."""
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", Path("/photos"))
    monkeypatch.setattr(scrub, "SHOW_CONTAINER_PATHS", False)
    monkeypatch.setattr(
        scrub, "_resolve_mount_source",
        _mount_resolver("/pj/tmp/scrub", {}),
    )

    target = Path("/scrubbed")

    assert scrub._format_path_with_host(target) == "/scrubbed"
    assert scrub._format_relative_path_with_host(target) == "/scrubbed"


# ---------------------------------------------------------------------------
# Integration: Output directory banner shows physical host path
# Simulates the full simple_scrub run with -o /scrubbed -v /tmp/scrub-test:/scrubbed
# ---------------------------------------------------------------------------

def test_simple_scrub_output_banner_shows_host_path(tmp_path, monkeypatch, capsys):
    """
    '📁 Output directory:' must display the physical host path when the output
    directory is bound from outside PHOTOS_ROOT (e.g. -v /tmp/scrub-test:/scrubbed).
    """
    photos_root = tmp_path / "photos"
    photos_root.mkdir()
    output_dir = tmp_path / "scrub-test"
    output_dir.mkdir()

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", photos_root)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(scrub, "INPUT_DIR", photos_root / "input")
    monkeypatch.setattr(scrub, "PROCESSED_DIR", photos_root / "processed")
    monkeypatch.setattr(scrub, "ERRORS_DIR", photos_root / "errors")
    monkeypatch.setattr(scrub, "SHOW_CONTAINER_PATHS", False)

    # Simulate: PHOTOS_ROOT → /pj/tmp/scrub, OUTPUT_DIR → /tmp/scrub-test
    container_output = Path("/scrubbed")
    monkeypatch.setattr(
        scrub, "_resolve_mount_source",
        _mount_resolver(
            str(photos_root),
            {str(output_dir): str(tmp_path / "scrub-test")},
        ),
    )

    # No JPEGs — we only care about the banner line, not actual scrubbing
    monkeypatch.setattr(scrub, "find_jpegs_in_dir", lambda *_a, **_kw: [])

    summary = scrub.ScrubSummary()
    scrub.simple_scrub(summary=summary, output_explicit=True)

    out = capsys.readouterr().out
    assert f"Output directory: {tmp_path}/scrub-test" in out, (
        f"Expected host path in banner, got:\n{out}"
    )
