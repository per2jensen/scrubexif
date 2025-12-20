# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

from scrubexif import scrub


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
