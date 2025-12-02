# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for state path resolution helper."""

from __future__ import annotations

from pathlib import Path

import pytest

from scrubexif import scrub


def test_resolve_state_env_creates_parent(tmp_path, monkeypatch):
    """Writable env path should be returned and its parent created."""
    target = tmp_path / "nested" / "state" / "file.json"
    monkeypatch.setenv("SCRUBEXIF_STATE", str(target))
    assert not target.parent.exists(), "Precondition: parent should not exist"

    resolved = scrub._resolve_state_path_from_env()

    assert resolved == target
    assert target.parent.exists()
    # Ensure the directory really is writable
    with scrub.tempfile.NamedTemporaryFile(dir=target.parent, delete=True):
        pass


def test_resolve_state_env_unwritable_falls_back(tmp_path, monkeypatch):
    """
    If the env path is not writable, the resolver should skip it and return a fallback.
    """
    env_path = tmp_path / "blocked" / "state.json"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.parent.chmod(0o500)  # remove write permission
    monkeypatch.setenv("SCRUBEXIF_STATE", str(env_path))

    real_namedtemp = scrub.tempfile.NamedTemporaryFile
    call_count = {"n": 0}

    def flaky_namedtempfile(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise PermissionError("forced failure for env path")
        return real_namedtemp(*args, **kwargs)

    monkeypatch.setattr(scrub.tempfile, "NamedTemporaryFile", flaky_namedtempfile)

    try:
        resolved = scrub._resolve_state_path_from_env()
    finally:
        # Restore permissions to let pytest clean up
        env_path.parent.chmod(0o700)

    assert resolved is not None
    assert resolved != env_path
    assert resolved.parent.exists()
    with real_namedtemp(dir=resolved.parent, delete=True):
        pass
