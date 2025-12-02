# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for state path resolution helper."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scrubexif import scrub


def _reset_scrub_logger():
    """Clear scrubexif logger handlers so caplog can capture without closed streams."""
    logger = logging.getLogger("scrubexif")
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.propagate = True
    logger.setLevel(logging.NOTSET)


def test_resolve_state_env_creates_parent(tmp_path, monkeypatch):
    """Writable env path should be returned and its parent created."""
    _reset_scrub_logger()
    target = tmp_path / "nested" / "state" / "file.json"
    monkeypatch.setenv("SCRUBEXIF_STATE", str(target))
    assert not target.parent.exists(), "Precondition: parent should not exist"

    resolved = scrub._resolve_state_path_from_env()

    assert resolved == target
    assert target.parent.exists()
    # Ensure the directory really is writable
    with scrub.tempfile.NamedTemporaryFile(dir=target.parent, delete=True):
        pass


def test_resolve_state_env_unwritable_disables_state(tmp_path, monkeypatch, caplog):
    """If the env path is not writable, resolver should warn and disable state."""
    _reset_scrub_logger()
    env_path = tmp_path / "blocked" / "state.json"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.parent.chmod(0o500)  # remove write permission
    monkeypatch.setenv("SCRUBEXIF_STATE", str(env_path))

    caplog.set_level(logging.WARNING, logger="scrubexif")
    resolved = scrub._resolve_state_path_from_env()

    # Restore permissions to let pytest clean up
    env_path.parent.chmod(0o700)

    assert resolved is None
    assert any("SCRUBEXIF_STATE" in rec.message and "not writable" in rec.message for rec in caplog.records)


def test_resolve_state_auto_falls_back_to_tmp(monkeypatch, caplog):
    """Without env, auto path should pick /tmp when /photos is unwritable."""
    _reset_scrub_logger()
    photos_path = Path("/photos/.scrubexif_state.json")
    tmp_path = Path("/tmp/.scrubexif_state.json")

    def fake_validate(path: Path):
        if path == photos_path:
            return None  # simulate unwritable /photos
        if path == tmp_path:
            return path
        return None

    monkeypatch.setenv("SCRUBEXIF_STATE", "")
    monkeypatch.setattr(scrub, "_validate_writable_path", fake_validate)
    caplog.set_level(logging.INFO, logger="scrubexif")

    resolved = scrub._resolve_state_path_from_env()

    assert resolved == tmp_path
    assert any("auto-selected" in rec.message and str(tmp_path) in rec.message for rec in caplog.records)


def test_resolve_state_auto_disabled_when_no_candidates(monkeypatch, caplog):
    """Without env and no writable defaults, resolver should disable state with warning."""
    _reset_scrub_logger()
    monkeypatch.setenv("SCRUBEXIF_STATE", "")
    monkeypatch.setattr(scrub, "_validate_writable_path", lambda _p: None)
    caplog.set_level(logging.WARNING, logger="scrubexif")

    resolved = scrub._resolve_state_path_from_env()

    assert resolved is None
    assert any("No writable state path found" in rec.message for rec in caplog.records)
