# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI level tests for debug logging."""

from __future__ import annotations

import sys
from scrubexif import scrub
from tests.conftest import SAMPLE_BYTES


def test_debug_flag_enables_verbose_logging(tmp_path, monkeypatch, capsys):
    root = tmp_path / "photos"
    input_dir = root / "input"
    output_dir = root / "output"
    processed_dir = root / "processed"
    errors_dir = root / "errors"
    for directory in (input_dir, output_dir, processed_dir, errors_dir):
        directory.mkdir(parents=True, exist_ok=True)

    sample = input_dir / "sample.jpg"
    sample.write_bytes(SAMPLE_BYTES)

    state_file = root / ".state.json"

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", root)
    monkeypatch.setattr(scrub, "INPUT_DIR", input_dir)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(scrub, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(scrub, "ERRORS_DIR", errors_dir)
    monkeypatch.setattr(scrub, "_resolve_state_path_from_env", lambda: state_file)
    monkeypatch.setattr(scrub, "STATE_FILE", state_file, raising=False)

    argv = ["scrub", "--from-input", "--dry-run", "--debug"]
    monkeypatch.setattr(sys, "argv", argv)

    scrub.main()

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "Debug logging enabled" in combined
    assert "CLI arguments" in combined
    assert "Input scan yielded" in combined
    sys.__stdout__.write(captured.out)
    sys.__stderr__.write(captured.err)


def test_debug_overrides_log_level(tmp_path, monkeypatch, capsys):
    """--debug must force debug logging even if --log-level specifies something else."""
    root = tmp_path / "photos"
    input_dir = root / "input"
    output_dir = root / "output"
    processed_dir = root / "processed"
    errors_dir = root / "errors"
    for directory in (input_dir, output_dir, processed_dir, errors_dir):
        directory.mkdir(parents=True, exist_ok=True)

    sample = input_dir / "sample.jpg"
    sample.write_bytes(SAMPLE_BYTES)

    state_file = root / ".state.json"

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", root)
    monkeypatch.setattr(scrub, "INPUT_DIR", input_dir)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(scrub, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(scrub, "ERRORS_DIR", errors_dir)
    monkeypatch.setattr(scrub, "_resolve_state_path_from_env", lambda: state_file)
    monkeypatch.setattr(scrub, "STATE_FILE", state_file, raising=False)

    argv = ["scrub", "--from-input", "--dry-run", "--debug", "--log-level", "warn"]
    monkeypatch.setattr(sys, "argv", argv)

    scrub.main()

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "Debug logging enabled" in combined
    assert "CLI arguments" in combined
    assert "log_level': 'debug'" in combined
    sys.__stdout__.write(captured.out)
    sys.__stderr__.write(captured.err)
