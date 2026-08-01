# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused unit tests for scrub_file behavior without requiring Docker."""

from pathlib import Path

import pytest

from scrubexif import scrub


def test_scrub_file_passes_full_output_path(tmp_path, monkeypatch):
    """Ensure jpegtran receives the resolved temp output path when writing to a directory."""
    input_file = tmp_path / "sample.jpg"
    input_file.write_bytes(b"jpeg-data")
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    commands: list[list[str]] = []

    def fake_run(cmd, *_, **__):
        commands.append(cmd)
        # Simulate jpegtran creating the output file so the pipeline proceeds.
        if "-outfile" in cmd:
            Path(cmd[cmd.index("-outfile") + 1]).write_bytes(b"scrubbed")

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    result = scrub.scrub_file(input_file, output_path=output_dir)

    assert result.output_path == output_dir / input_file.name
    assert commands, "Expected jpegtran command to be invoked"
    cmd = commands[0]
    assert "-outfile" in cmd, "Expected jpegtran to receive -outfile argument"
    target = Path(cmd[cmd.index("-outfile") + 1])
    assert target.parent == output_dir
    assert target != result.output_path


def test_duplicate_reporting_uses_output_file(tmp_path):
    """Dry-run duplicate detection should report the concrete output filename."""
    input_file = tmp_path / "photo.jpg"
    input_file.write_bytes(b"jpeg-data")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / input_file.name).write_bytes(b"existing")

    result = scrub.scrub_file(input_file, output_path=output_dir, dry_run=True)

    assert result.status == "duplicate"
    assert result.output_path == output_dir / input_file.name


def test_scrub_file_failure_does_not_create_output(tmp_path, monkeypatch):
    input_file = tmp_path / "bad.jpg"
    input_file.write_bytes(b"jpeg-data")
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    def fake_run(cmd, *_, **__):
        # Simulate jpegtran returning an error without creating output.
        class Proc:
            returncode = 1
            stdout = ""
            stderr = "jpegtran: not a JPEG file"

        return Proc()

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    result = scrub.scrub_file(input_file, output_path=output_dir)

    assert result.status == "error"
    assert not (output_dir / input_file.name).exists()
    assert not any(p.name.startswith(".scrubexif_tmp_") for p in output_dir.iterdir())


def test_scrub_file_exception_does_not_create_output(tmp_path, monkeypatch):
    """Simulate jpegtran binary missing; scrub_file must clean up and return error."""
    input_file = tmp_path / "error.jpg"
    input_file.write_bytes(b"jpeg-data")
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    def fake_run(cmd, *_, **__):
        # Simulate the OS being unable to find jpegtran.
        raise FileNotFoundError("jpegtran not found")

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    result = scrub.scrub_file(input_file, output_path=output_dir)

    assert result.status == "error"
    assert not (output_dir / input_file.name).exists()
    assert not any(p.name.startswith(".scrubexif_tmp_") for p in output_dir.iterdir())


def test_scrub_file_skip_leaves_original_untouched(tmp_path):
    """on_duplicate='skip': when the output already exists scrub_file must return
    status='skipped' and leave the original byte-for-byte intact."""
    original_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    input_file = tmp_path / "photo.jpg"
    input_file.write_bytes(original_bytes)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    existing_output = output_dir / input_file.name
    existing_output.write_bytes(b"previously-scrubbed")

    result = scrub.scrub_file(input_file, output_path=output_dir, on_duplicate="skip")

    assert result.status == "skipped", "Expected status='skipped' when output exists"
    assert input_file.read_bytes() == original_bytes, "Original must not be modified"
    assert existing_output.read_bytes() == b"previously-scrubbed", "Existing output must not be overwritten"


def test_in_place_failure_keeps_original(tmp_path, monkeypatch):
    input_file = tmp_path / "inplace.jpg"
    original = b"original"
    input_file.write_bytes(original)

    def fake_run(cmd, *_, **__):
        # Simulate jpegtran failure without creating any output.
        class Proc:
            returncode = 1
            stdout = ""
            stderr = "jpegtran: not a JPEG file"

        return Proc()

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    result = scrub.scrub_file(input_file, output_path=None)

    assert result.status == "error"
    assert input_file.read_bytes() == original
    assert not any(p.name.startswith(".scrubexif_tmp_") for p in tmp_path.iterdir())


def test_delete_original_failure_reports_scrubbed_output_and_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-scrub unlink failure preserves both files and reports an error."""
    sample = Path(__file__).parent / "assets" / "sample_with_exif.jpg"
    input_file = tmp_path / "input" / "sample.jpg"
    output_dir = tmp_path / "output"
    input_file.parent.mkdir()
    output_dir.mkdir()
    input_file.write_bytes(sample.read_bytes())
    original_unlink = Path.unlink

    # Source unlink failure is an OS-level condition that cannot be induced
    # portably without also making the test directory unusable.
    def fail_source_unlink(path: Path, missing_ok: bool = False) -> None:
        """Fail only deletion of the original after output publication."""
        if path == input_file:
            raise PermissionError("simulated original deletion failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_source_unlink)

    result = scrub.scrub_file(
        input_file,
        output_path=output_dir,
        delete_original=True,
    )

    assert result.status == "scrubbed_with_error"
    assert input_file.exists()
    assert (output_dir / input_file.name).exists()
    summary = scrub.ScrubSummary()
    summary.update(result)
    assert summary.scrubbed == 1
    assert summary.errors == 1


def test_inline_rename_source_removal_failure_preserves_both_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rename post-processing failure retains source and scrubbed destination."""
    sample = Path(__file__).parent / "assets" / "sample_with_exif.jpg"
    input_file = tmp_path / "sample.jpg"
    renamed_file = tmp_path / "renamed.jpg"
    input_file.write_bytes(sample.read_bytes())
    original_unlink = Path.unlink

    # Source unlink failure is an OS-level condition that cannot be induced
    # portably without also making the test directory unusable.
    def fail_source_unlink(path: Path, missing_ok: bool = False) -> None:
        """Fail only removal of the source after renamed output publication."""
        if path == input_file:
            raise PermissionError("simulated renamed source removal failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_source_unlink)

    result = scrub.scrub_file(
        input_file,
        output_path=None,
        planned_rename_path=renamed_file,
    )

    assert result.status == "scrubbed_with_error"
    assert input_file.exists()
    assert renamed_file.exists()


def test_duplicate_delete_failure_returns_error_without_data_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed duplicate deletion retains both original and existing output."""
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "output"
    input_directory.mkdir()
    output_directory.mkdir()
    input_file = input_directory / "photo.jpg"
    output_file = output_directory / input_file.name
    input_file.write_bytes(b"duplicate-original")
    output_file.write_bytes(b"existing-output")
    original_unlink = Path.unlink

    # Source unlink failure is an OS-level condition that cannot be induced
    # portably without also making the test directory unusable.
    def fail_source_unlink(path: Path, missing_ok: bool = False) -> None:
        """Fail only deletion of the duplicate source."""
        if path == input_file:
            raise PermissionError("simulated duplicate deletion failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_source_unlink)

    result = scrub.scrub_file(
        input_file,
        output_path=output_directory,
        on_duplicate="delete",
    )

    assert result.status == "error"
    assert input_file.read_bytes() == b"duplicate-original"
    assert output_file.read_bytes() == b"existing-output"


def test_publish_no_clobber_publishes_new_destination_atomically(tmp_path: Path) -> None:
    """The no-clobber primitive publishes output and removes its temp name."""
    temp_output = tmp_path / ".temporary.jpg"
    temp_output.write_bytes(b"scrubbed")
    destination = tmp_path / "renamed.jpg"

    scrub._publish_no_clobber(temp_output, destination)

    assert destination.read_bytes() == b"scrubbed"
    assert not temp_output.exists()


def test_publish_no_clobber_existing_destination_preserves_both_files(
    tmp_path: Path,
) -> None:
    """A publication race cannot replace an occupied destination."""
    temp_output = tmp_path / ".temporary.jpg"
    temp_output.write_bytes(b"new-output")
    destination = tmp_path / "renamed.jpg"
    destination.write_bytes(b"existing-output")

    with pytest.raises(FileExistsError):
        scrub._publish_no_clobber(temp_output, destination)

    assert destination.read_bytes() == b"existing-output"
    assert temp_output.read_bytes() == b"new-output"


def test_scrub_file_planned_destination_conflict_leaves_source_untouched(
    tmp_path: Path,
) -> None:
    """A destination appearing after planning returns a non-destructive conflict."""
    source = tmp_path / "source.jpg"
    source.write_bytes(b"original-source")
    destination = tmp_path / "planned.jpg"
    destination.write_bytes(b"racing-writer")

    result = scrub.scrub_file(
        source,
        output_path=None,
        planned_rename_path=destination,
    )

    assert result.status == "conflict"
    assert source.read_bytes() == b"original-source"
    assert destination.read_bytes() == b"racing-writer"


def test_auto_conflict_does_not_move_or_delete_intake_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-mode finalization reports a conflict but leaves intake untouched."""
    source = tmp_path / "input" / "source.jpg"
    source.parent.mkdir()
    source.write_bytes(b"original-source")
    processed = tmp_path / "processed"
    processed.mkdir()
    monkeypatch.setattr(scrub, "PROCESSED_DIR", processed)
    summary = scrub.ScrubSummary()
    state: dict[str, dict[str, float | int]] = {}
    result = scrub.ScrubResult(
        input_path=source,
        output_path=tmp_path / "output" / "planned.jpg",
        status="conflict",
        error_message="destination appeared",
    )

    scrub._finalize_auto_result(source, result, summary, False, state)

    assert summary.errors == 1
    assert source.read_bytes() == b"original-source"
    assert list(processed.iterdir()) == []
