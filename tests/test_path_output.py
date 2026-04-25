# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests for _resolve_mount_source, _format_path_with_host and _format_relative_path_with_host.

Covers:
  - _resolve_mount_source: parses /proc/self/mountinfo correctly
      - standard bind mount → host path returned
      - multiple entries → correct mount_point matched
      - escaped spaces (\\040) in path → correctly unescaped
      - no matching entry → None
      - OSError reading mountinfo → None (no crash)
      - lines missing ' - ' separator → skipped
      - lines with fewer than 5 pre-fields → skipped
      - root field not starting with '/' → post-field fallback
      - neither root nor post-field usable → None
  - Full pipeline: mountinfo → _resolve_mount_source → _format_path_with_host
      - bind mount present → physical host path shown
      - no bind mount → falls back to container path
  - Path under PHOTOS_ROOT: resolved via parent mount
  - Path outside PHOTOS_ROOT: resolved via its own mount (e.g. -o /scrubbed bind-mount)
  - Path outside PHOTOS_ROOT with no mount entry: falls back to container path
  - SHOW_CONTAINER_PATHS=True variants
  - Integration: Output directory banner shows physical host path
"""

import builtins
import io
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


# ---------------------------------------------------------------------------
# _resolve_mount_source — mountinfo parsing
# ---------------------------------------------------------------------------

# A realistic bind-mount entry: -v /srv/photos:/photos
_BIND_MOUNT_LINE = (
    "457 431 8:1 /srv/photos /photos rw,relatime master:1 - ext4 /dev/sda1 rw\n"
)

# Unrelated entries that must never match /photos
_UNRELATED_LINES = (
    "1 0 8:0 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n"
    "36 1 0:3 / /proc rw,nosuid shared:14 - proc proc rw\n"
    "45 1 0:4 / /dev rw,nosuid shared:9 - devtmpfs devtmpfs rw\n"
)


def _patch_mountinfo(monkeypatch, content: str) -> None:
    """
    Redirect reads of /proc/self/mountinfo to *content*; all other opens are real.
    """
    real_open = builtins.open

    def _fake_open(path, *args, **kwargs):
        if str(path) == "/proc/self/mountinfo":
            return io.StringIO(content)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _fake_open)


# --- positive tests ---------------------------------------------------------

def test_resolve_mount_source_finds_host_path(monkeypatch):
    """Standard bind-mount entry → host path returned."""
    _patch_mountinfo(monkeypatch, _BIND_MOUNT_LINE)
    assert scrub._resolve_mount_source(Path("/photos")) == "/srv/photos"


def test_resolve_mount_source_correct_entry_matched_among_many(monkeypatch):
    """Multiple unrelated entries present → only the matching mount_point used."""
    _patch_mountinfo(monkeypatch, _UNRELATED_LINES + _BIND_MOUNT_LINE)
    assert scrub._resolve_mount_source(Path("/photos")) == "/srv/photos"


def test_resolve_mount_source_unescapes_spaces_in_path(monkeypatch):
    r"""Paths containing \040 (space) are correctly unescaped."""
    entry = "123 1 8:1 /my\\040photos /photos rw - ext4 /dev/sda1 rw\n"
    _patch_mountinfo(monkeypatch, entry)
    assert scrub._resolve_mount_source(Path("/photos")) == "/my photos"


def test_resolve_mount_source_post_field_fallback(monkeypatch):
    """When root does not start with '/', fall back to post_fields[1] if it does."""
    entry = "123 1 8:1 relpath /photos rw - ext4 /dev/sda1 rw\n"
    _patch_mountinfo(monkeypatch, entry)
    assert scrub._resolve_mount_source(Path("/photos")) == "/dev/sda1"


def test_resolve_mount_source_skips_bad_lines_finds_good_one(monkeypatch):
    """Malformed lines are skipped; a valid entry later in the file is still found."""
    content = (
        "no dash separator here\n"           # missing ' - '
        "1 2 3 - ext4 /dev/sda1 rw\n"       # fewer than 5 pre-fields
        + _BIND_MOUNT_LINE
    )
    _patch_mountinfo(monkeypatch, content)
    assert scrub._resolve_mount_source(Path("/photos")) == "/srv/photos"


# --- negative tests ---------------------------------------------------------

def test_resolve_mount_source_no_matching_entry_returns_none(monkeypatch):
    """No entry for the requested path → None."""
    _patch_mountinfo(monkeypatch, _UNRELATED_LINES)
    assert scrub._resolve_mount_source(Path("/photos")) is None


def test_resolve_mount_source_oserror_returns_none(monkeypatch):
    """Unreadable /proc/self/mountinfo → None, no exception raised."""
    real_open = builtins.open

    def _raise(path, *args, **kwargs):
        if str(path) == "/proc/self/mountinfo":
            raise OSError("permission denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _raise)
    assert scrub._resolve_mount_source(Path("/photos")) is None


def test_resolve_mount_source_neither_root_nor_post_returns_none(monkeypatch):
    """root and post_fields[1] both lack a leading '/' → None."""
    entry = "123 1 8:1 relpath /photos rw - tmpfs none rw\n"
    _patch_mountinfo(monkeypatch, entry)
    assert scrub._resolve_mount_source(Path("/photos")) is None


# ---------------------------------------------------------------------------
# Full pipeline: mountinfo → _resolve_mount_source → _format_path_with_host
# ---------------------------------------------------------------------------

def test_full_pipeline_bind_mount_shows_host_path(monkeypatch):
    """
    Bind-mount present in mountinfo → _format_path_with_host returns the
    physical host path, not the container path.
    """
    _patch_mountinfo(monkeypatch, _BIND_MOUNT_LINE)
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", Path("/photos"))
    monkeypatch.setattr(scrub, "SHOW_CONTAINER_PATHS", False)

    result = scrub._format_path_with_host(Path("/photos/input/file.jpg"))
    assert result == "/srv/photos/input/file.jpg"


def test_full_pipeline_no_mount_falls_back_to_container_path(monkeypatch):
    """
    No bind-mount entry in mountinfo → _format_path_with_host falls back to
    the container path rather than crashing or returning garbage.
    """
    _patch_mountinfo(monkeypatch, _UNRELATED_LINES)
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", Path("/photos"))
    monkeypatch.setattr(scrub, "SHOW_CONTAINER_PATHS", False)

    result = scrub._format_path_with_host(Path("/photos/input/file.jpg"))
    assert result == "/photos/input/file.jpg"


# ---------------------------------------------------------------------------
# Subdirectory bind-mounts — PHOTOS_ROOT not mounted, each subdir is
# Simulates: -v /srv/input:/photos/input  -v /srv/output:/photos/output  etc.
# ---------------------------------------------------------------------------

_SUBDIR_MOUNT_LINES = (
    "457 431 8:1 /srv/photosync/input/job1 /photos/input rw,relatime master:1 - ext4 /dev/sda1 rw\n"
    "458 431 8:1 /mnt/vol/output /photos/output rw,relatime master:1 - ext4 /dev/sda1 rw\n"
    "459 431 8:1 /mnt/vol/processed /photos/processed rw,relatime master:1 - ext4 /dev/sda1 rw\n"
)


def test_format_path_subdir_bind_mount_shows_host_path(monkeypatch):
    """
    PHOTOS_ROOT has no mount entry but each subdir is bind-mounted individually.
    _format_path_with_host must return the physical host path for each subdir.
    """
    _patch_mountinfo(monkeypatch, _SUBDIR_MOUNT_LINES)
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", Path("/photos"))
    monkeypatch.setattr(scrub, "SHOW_CONTAINER_PATHS", False)

    assert scrub._format_path_with_host(Path("/photos/input")) == "/srv/photosync/input/job1"
    assert scrub._format_path_with_host(Path("/photos/output")) == "/mnt/vol/output"
    assert scrub._format_path_with_host(Path("/photos/processed")) == "/mnt/vol/processed"


def test_format_path_subdir_bind_mount_no_root_mount_falls_back(monkeypatch):
    """
    Neither PHOTOS_ROOT nor the specific subdir has a mount entry → container path returned.
    """
    _patch_mountinfo(monkeypatch, _UNRELATED_LINES)
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", Path("/photos"))
    monkeypatch.setattr(scrub, "SHOW_CONTAINER_PATHS", False)

    assert scrub._format_path_with_host(Path("/photos/input")) == "/photos/input"
    assert scrub._format_path_with_host(Path("/photos/output")) == "/photos/output"


def test_format_path_subdir_bind_mount_show_container_paths(monkeypatch):
    """With SHOW_CONTAINER_PATHS=True both container and host paths are shown."""
    _patch_mountinfo(monkeypatch, _SUBDIR_MOUNT_LINES)
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", Path("/photos"))
    monkeypatch.setattr(scrub, "SHOW_CONTAINER_PATHS", True)

    assert (
        scrub._format_path_with_host(Path("/photos/input"))
        == "/photos/input (host: /srv/photosync/input/job1)"
    )


# ---------------------------------------------------------------------------
# Physical tests — real /proc/self/mountinfo, no mocked filesystem
# ---------------------------------------------------------------------------

_VIRTUAL_FS = frozenset({
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup2",
    "securityfs", "pstore", "bpf", "autofs", "mqueue", "hugetlbfs",
    "tracefs", "debugfs", "configfs", "fusectl", "nfsd", "efivarfs",
    "squashfs",
})


def _first_physical_mount() -> Path | None:
    """
    Scan the real /proc/self/mountinfo for the first non-virtual, non-root
    mount point.  Returns None if the file is unreadable or nothing qualifies.
    """
    try:
        with open("/proc/self/mountinfo") as f:
            for line in f:
                if " - " not in line:
                    continue
                pre, post = line.rstrip("\n").split(" - ", 1)
                pre_fields = pre.split()
                post_fields = post.split()
                if len(pre_fields) < 5 or not post_fields:
                    continue
                if post_fields[0] in _VIRTUAL_FS:
                    continue
                root = pre_fields[3].replace("\\040", " ")
                mount_point = pre_fields[4].replace("\\040", " ")
                if root.startswith("/") and mount_point not in ("/", ""):
                    return Path(mount_point)
    except OSError:
        return None
    return None


@pytest.mark.skipif(
    not Path("/proc/self/mountinfo").exists(),
    reason="Not on Linux — no /proc/self/mountinfo",
)
def test_format_path_with_host_real_subdir_mount_positive(monkeypatch, tmp_path):
    """
    Physical test: no mocking of _resolve_mount_source or /proc/self/mountinfo.

    Mirrors the production Docker scenario where each container subdir is
    bind-mounted individually and PHOTOS_ROOT itself has no mount entry.

    PHOTOS_ROOT is set to an unmounted tmp directory so that
    _resolve_mount_source(PHOTOS_ROOT) returns None.  The target is a real
    mount point found in /proc/self/mountinfo.  The new fallback branch must
    fire and return the mount's source path rather than the raw container path.
    """
    real_mount = _first_physical_mount()
    if real_mount is None:
        pytest.skip("No suitable physical mount point found in /proc/self/mountinfo")

    fake_root = tmp_path / "photos"
    fake_root.mkdir()
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", fake_root)
    monkeypatch.setattr(scrub, "SHOW_CONTAINER_PATHS", False)

    result = scrub._format_path_with_host(real_mount)
    print(f"\n  mount point : {real_mount}")
    print(f"  resolved to : {result}")

    assert result != str(real_mount), (
        f"Expected host-source resolution for real mount point {real_mount!r} "
        f"but got the container path back — the new fallback branch did not fire."
    )


@pytest.mark.skipif(
    not Path("/proc/self/mountinfo").exists(),
    reason="Not on Linux — no /proc/self/mountinfo",
)
def test_format_path_with_host_real_unmounted_dirs_fall_back(monkeypatch, tmp_path):
    """
    Physical test: no mocking of _resolve_mount_source or /proc/self/mountinfo.

    Both PHOTOS_ROOT and the target are fresh tmp directories that are never
    mount points.  The function must fall back to returning the container path.
    """
    fake_root = tmp_path / "photos"
    fake_root.mkdir()
    target = fake_root / "input"
    target.mkdir()

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", fake_root)
    monkeypatch.setattr(scrub, "SHOW_CONTAINER_PATHS", False)

    result = scrub._format_path_with_host(target)
    print(f"\n  target (unmounted) : {target}")
    print(f"  resolved to        : {result}")

    assert result == str(target), (
        f"Expected container-path fallback for unmounted {target!r}, got {result!r}"
    )
