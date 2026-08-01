# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests that temporary resources never reuse predictable user paths."""

import json
from pathlib import Path

import pytest

from scrubexif import scrub


def test_directory_write_probe_preserves_old_predictable_probe_name(tmp_path: Path) -> None:
    """A pre-existing legacy probe filename is never opened or removed."""
    sentinel = tmp_path / ".scrubexif_write_test"
    sentinel.write_bytes(b"user-owned")

    scrub.check_dir_safety(tmp_path, "Test")

    assert sentinel.read_bytes() == b"user-owned"
    assert not any(path.name.startswith(".scrubexif_write_test_") for path in tmp_path.iterdir())


def test_save_state_preserves_old_predictable_temp_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """State saving does not overwrite the former fixed .json.tmp path."""
    state_file = tmp_path / "state.json"
    legacy_temp = state_file.with_suffix(".json.tmp")
    legacy_temp.write_bytes(b"user-owned")
    monkeypatch.setattr(scrub, "STATE_FILE", state_file)
    monkeypatch.setattr(scrub, "_warned_state_disabled", False)
    state: dict[str, object] = {"photo.jpg": {"size": 12, "mtime": 34.0}}

    scrub.save_state(state)

    assert json.loads(state_file.read_text(encoding="utf-8")) == state
    assert legacy_temp.read_bytes() == b"user-owned"
    assert not any(
        path.name.startswith(f".{state_file.name}.") for path in tmp_path.iterdir()
    )


def test_save_state_replace_failure_cleans_only_owned_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An atomic-replace failure preserves user files and removes the owned temp."""
    state_file = tmp_path / "state.json"
    legacy_temp = state_file.with_suffix(".json.tmp")
    legacy_temp.write_bytes(b"user-owned")
    monkeypatch.setattr(scrub, "STATE_FILE", state_file)
    monkeypatch.setattr(scrub, "_warned_state_disabled", False)

    # Atomic replacement failures depend on OS/filesystem conditions and are not
    # reliably reproducible with permissions when tests run as root.
    def fail_replace(source: Path, destination: Path) -> None:
        """Simulate an OS-level failure during final state publication."""
        del source, destination
        raise PermissionError("simulated state replace failure")

    monkeypatch.setattr(scrub.os, "replace", fail_replace)

    scrub.save_state({"photo.jpg": {"size": 12}})

    assert scrub.STATE_FILE is None
    assert legacy_temp.read_bytes() == b"user-owned"
    assert not any(
        path.name.startswith(f".{state_file.name}.") for path in tmp_path.iterdir()
    )
