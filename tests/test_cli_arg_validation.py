# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests for CLI argument-combination validation in scrub.main().

Each documented constraint has both a negative test (invalid combo → rejected
with SystemExit and a clear message) and a positive test (valid usage →
argument validation passes, mode function is reached).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scrubexif import scrub
from scrubexif.scrub import ScrubSummary
from tests.conftest import SAMPLE_BYTES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_main(argv: list[str], monkeypatch) -> tuple[int, str]:
    """Invoke scrub.main() with the given argv; return (returncode, stderr)."""
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc_info:
        scrub.main()
    return exc_info.value.code


def _setup_dirs(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    """Create a minimal photos directory tree and monkeypatch all module-level paths."""
    root = tmp_path / "photos"
    input_dir = root / "input"
    output_dir = root / "output"
    processed_dir = root / "processed"
    errors_dir = root / "errors"
    for d in (input_dir, output_dir, processed_dir, errors_dir):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", root)
    monkeypatch.setattr(scrub, "INPUT_DIR", input_dir)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(scrub, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(scrub, "ERRORS_DIR", errors_dir)
    monkeypatch.setattr(scrub, "STATE_FILE", None, raising=False)
    monkeypatch.setattr(scrub, "_resolve_state_path_from_env", lambda: None)

    return {"root": root, "input": input_dir, "output": output_dir,
            "processed": processed_dir, "errors": errors_dir}


# ---------------------------------------------------------------------------
# Constraint: positional files require --clean-inline
# ---------------------------------------------------------------------------

def test_files_without_clean_inline_rejected(tmp_path, monkeypatch, capsys):
    """Passing positional file arguments without --clean-inline must exit with an error."""
    dummy = tmp_path / "photo.jpg"
    dummy.write_bytes(SAMPLE_BYTES)

    monkeypatch.setattr(sys, "argv", ["scrub", str(dummy)])

    with pytest.raises(SystemExit) as exc_info:
        scrub.main()

    assert exc_info.value.code != 0
    assert "require --clean-inline" in capsys.readouterr().err


def test_files_with_clean_inline_accepted(tmp_path, monkeypatch):
    """Positional file arguments combined with --clean-inline must pass validation."""
    dirs = _setup_dirs(tmp_path, monkeypatch)
    photo = dirs["root"] / "photo.jpg"
    photo.write_bytes(SAMPLE_BYTES)

    scrub_calls: list[list[Path]] = []

    def fake_manual_scrub(files, summary, **kwargs):
        scrub_calls.append(list(files))
        return summary

    monkeypatch.setattr(scrub, "manual_scrub", fake_manual_scrub)
    monkeypatch.setattr(sys, "argv", ["scrub", "--clean-inline", "--dry-run", str(photo)])

    scrub.main()

    assert len(scrub_calls) == 1, "manual_scrub must be called once"
    assert photo.resolve() in scrub_calls[0], "Resolved photo path must be forwarded"


# ---------------------------------------------------------------------------
# Constraint: --clean-inline and --from-input are mutually exclusive
# ---------------------------------------------------------------------------

def test_clean_inline_and_from_input_rejected(monkeypatch, capsys):
    """--clean-inline combined with --from-input must exit with an error."""
    monkeypatch.setattr(sys, "argv", ["scrub", "--clean-inline", "--from-input"])

    with pytest.raises(SystemExit) as exc_info:
        scrub.main()

    assert exc_info.value.code != 0
    assert "--clean-inline and --from-input cannot be used together" in capsys.readouterr().err


def test_from_input_without_clean_inline_accepted(tmp_path, monkeypatch):
    """--from-input without --clean-inline must pass validation and reach auto_scrub."""
    _setup_dirs(tmp_path, monkeypatch)

    auto_calls: list[dict] = []

    def fake_auto_scrub(**kwargs):
        auto_calls.append(kwargs)
        return ScrubSummary()

    monkeypatch.setattr(scrub, "auto_scrub", fake_auto_scrub)
    monkeypatch.setattr(scrub, "guard_auto_mode_dirs", lambda *_a, **_kw: None)
    monkeypatch.setattr(sys, "argv", ["scrub", "--from-input", "--dry-run"])

    scrub.main()

    assert len(auto_calls) == 1, "auto_scrub must be called once"
    assert auto_calls[0]["dry_run"] is True


# ---------------------------------------------------------------------------
# Constraint: --output cannot be used with --clean-inline
# ---------------------------------------------------------------------------

def test_output_with_clean_inline_rejected(tmp_path, monkeypatch, capsys):
    """--output combined with --clean-inline must exit with an error."""
    monkeypatch.setattr(sys, "argv",
                        ["scrub", "--output", str(tmp_path / "out"), "--clean-inline"])

    with pytest.raises(SystemExit) as exc_info:
        scrub.main()

    assert exc_info.value.code != 0
    assert "--output cannot be used with --clean-inline" in capsys.readouterr().err


def test_output_without_clean_inline_accepted(tmp_path, monkeypatch):
    """--output without --clean-inline must pass validation and reach simple_scrub."""
    dirs = _setup_dirs(tmp_path, monkeypatch)
    custom_output = tmp_path / "custom_out"

    simple_calls: list[dict] = []

    def fake_simple_scrub(summary, **kwargs):
        simple_calls.append(kwargs)
        return summary

    monkeypatch.setattr(scrub, "simple_scrub", fake_simple_scrub)
    monkeypatch.setattr(sys, "argv", ["scrub", "--output", str(custom_output)])

    scrub.main()

    assert len(simple_calls) == 1, "simple_scrub must be called once"


# ---------------------------------------------------------------------------
# Constraint: --output cannot be used with --from-input
# ---------------------------------------------------------------------------

def test_output_with_from_input_rejected(tmp_path, monkeypatch, capsys):
    """--output combined with --from-input must exit with an error."""
    monkeypatch.setattr(sys, "argv",
                        ["scrub", "--output", str(tmp_path / "out"), "--from-input"])

    with pytest.raises(SystemExit) as exc_info:
        scrub.main()

    assert exc_info.value.code != 0
    assert "--output cannot be used with --from-input" in capsys.readouterr().err


def test_from_input_without_output_accepted(tmp_path, monkeypatch):
    """--from-input without --output must pass validation and reach auto_scrub."""
    _setup_dirs(tmp_path, monkeypatch)

    auto_calls: list[dict] = []

    def fake_auto_scrub(**kwargs):
        auto_calls.append(kwargs)
        return ScrubSummary()

    monkeypatch.setattr(scrub, "auto_scrub", fake_auto_scrub)
    monkeypatch.setattr(scrub, "guard_auto_mode_dirs", lambda *_a, **_kw: None)
    monkeypatch.setattr(sys, "argv", ["scrub", "--from-input", "--dry-run"])

    scrub.main()

    assert len(auto_calls) == 1, "auto_scrub must be called once"


# ---------------------------------------------------------------------------
# Constraint: --paranoia is incompatible with --copyright and --comment
#
# Promise: --paranoia strips ALL metadata; stamping a copyright or comment
# would silently contradict that guarantee, so the combination is refused.
# ---------------------------------------------------------------------------

def test_paranoia_with_copyright_rejected(monkeypatch, capsys):
    """--paranoia combined with --copyright must exit with an error."""
    monkeypatch.setattr(sys, "argv",
                        ["scrub", "--clean-inline", "--paranoia", "--copyright", "ACME"])

    with pytest.raises(SystemExit) as exc_info:
        scrub.main()

    assert exc_info.value.code != 0
    assert "cannot be combined with --paranoia" in capsys.readouterr().err


def test_paranoia_with_comment_rejected(monkeypatch, capsys):
    """--paranoia combined with --comment must exit with an error."""
    monkeypatch.setattr(sys, "argv",
                        ["scrub", "--clean-inline", "--paranoia", "--comment", "private"])

    with pytest.raises(SystemExit) as exc_info:
        scrub.main()

    assert exc_info.value.code != 0
    assert "cannot be combined with --paranoia" in capsys.readouterr().err


def test_paranoia_with_copyright_and_comment_rejected(monkeypatch, capsys):
    """--paranoia combined with both --copyright and --comment must exit with an error."""
    monkeypatch.setattr(sys, "argv",
                        ["scrub", "--clean-inline", "--paranoia",
                         "--copyright", "ACME", "--comment", "private"])

    with pytest.raises(SystemExit) as exc_info:
        scrub.main()

    assert exc_info.value.code != 0
    assert "cannot be combined with --paranoia" in capsys.readouterr().err


def test_paranoia_alone_accepted_and_forwarded(tmp_path, monkeypatch):
    """--paranoia without --copyright or --comment must pass validation and
    reach the mode function with paranoia=True."""
    dirs = _setup_dirs(tmp_path, monkeypatch)
    photo = dirs["root"] / "photo.jpg"
    photo.write_bytes(SAMPLE_BYTES)

    manual_calls: list[dict] = []

    def fake_manual_scrub(files, summary, **kwargs):
        manual_calls.append(kwargs)
        return summary

    monkeypatch.setattr(scrub, "manual_scrub", fake_manual_scrub)
    monkeypatch.setattr(sys, "argv",
                        ["scrub", "--clean-inline", "--paranoia", "--dry-run", str(photo)])

    scrub.main()

    assert len(manual_calls) == 1, "manual_scrub must be called once"
    assert manual_calls[0]["paranoia"] is True, "paranoia=True must be forwarded to manual_scrub"


def test_copyright_without_paranoia_accepted_and_forwarded(tmp_path, monkeypatch):
    """--copyright without --paranoia must pass validation and reach the mode
    function with the copyright text intact."""
    dirs = _setup_dirs(tmp_path, monkeypatch)
    photo = dirs["root"] / "photo.jpg"
    photo.write_bytes(SAMPLE_BYTES)

    manual_calls: list[dict] = []

    def fake_manual_scrub(files, summary, **kwargs):
        manual_calls.append(kwargs)
        return summary

    monkeypatch.setattr(scrub, "manual_scrub", fake_manual_scrub)
    monkeypatch.setattr(sys, "argv",
                        ["scrub", "--clean-inline", "--dry-run",
                         "--copyright", "ACME Corp", str(photo)])

    scrub.main()

    assert len(manual_calls) == 1, "manual_scrub must be called once"
    assert manual_calls[0]["copyright_text"] == "ACME Corp"


def test_comment_without_paranoia_accepted_and_forwarded(tmp_path, monkeypatch):
    """--comment without --paranoia must pass validation and reach the mode
    function with the comment text intact."""
    dirs = _setup_dirs(tmp_path, monkeypatch)
    photo = dirs["root"] / "photo.jpg"
    photo.write_bytes(SAMPLE_BYTES)

    manual_calls: list[dict] = []

    def fake_manual_scrub(files, summary, **kwargs):
        manual_calls.append(kwargs)
        return summary

    monkeypatch.setattr(scrub, "manual_scrub", fake_manual_scrub)
    monkeypatch.setattr(sys, "argv",
                        ["scrub", "--clean-inline", "--dry-run",
                         "--comment", "scrubbed by ACME", str(photo)])

    scrub.main()

    assert len(manual_calls) == 1, "manual_scrub must be called once"
    assert manual_calls[0]["comment_text"] == "scrubbed by ACME"
