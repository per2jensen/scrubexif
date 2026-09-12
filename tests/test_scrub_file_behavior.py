# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused unit tests for scrub_file behavior without requiring Docker."""

import subprocess
from pathlib import Path

import pytest

from scrubexif import scrub
from tests.conftest import create_fake_jpeg


SAFE_JPEG_BYTES = (
    b"\xff\xd8"
    b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
    b"\x11\xff\xd9"
)


def test_scrub_file_rejects_unknown_duplicate_policy(tmp_path: Path) -> None:
    """An unsupported duplicate policy is rejected before filesystem work."""
    source = tmp_path / "source.jpg"
    source.write_bytes(SAFE_JPEG_BYTES)

    with pytest.raises(ValueError, match="on_duplicate must be one of"):
        scrub.scrub_file(source, on_duplicate="discard")


def test_scrub_file_stages_source_bytes_outside_output_directory(tmp_path, monkeypatch):
    """Ensure jpegtran writes privately before audited bytes enter output."""
    input_file = tmp_path / "sample.jpg"
    input_file.write_bytes(b"jpeg-data")
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    commands: list[list[str]] = []

    def fake_run(cmd, *_, **__):
        commands.append(cmd)
        # Simulate jpegtran creating the output file so the pipeline proceeds.
        if "-outfile" in cmd:
            Path(cmd[cmd.index("-outfile") + 1]).write_bytes(SAFE_JPEG_BYTES)

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
    assert target.parent != output_dir
    assert target != result.output_path
    assert result.status == "scrubbed"
    assert result.output_path.read_bytes() == SAFE_JPEG_BYTES
    assert list(output_dir.iterdir()) == [result.output_path]


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


def test_scrub_file_skip_leaves_original_untouched(tmp_path, monkeypatch):
    """on_duplicate='skip': when the output already exists scrub_file must return
    status='skipped' and leave the original byte-for-byte intact."""
    original_bytes = SAFE_JPEG_BYTES
    input_file = tmp_path / "photo.jpg"
    input_file.write_bytes(original_bytes)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    existing_output = output_dir / input_file.name
    existing_output.write_bytes(SAFE_JPEG_BYTES)

    def fake_pipeline(
        source_path: Path,
        staged_path: Path,
        **kwargs: object,
    ) -> None:
        """Produce byte-identical audited output for duplicate comparison."""
        del source_path, kwargs
        staged_path.write_bytes(SAFE_JPEG_BYTES)

    monkeypatch.setattr(scrub, "_do_scrub_pipeline", fake_pipeline)

    result = scrub.scrub_file(input_file, output_path=output_dir, on_duplicate="skip")

    assert result.status == "skipped", "Expected status='skipped' when output exists"
    assert input_file.read_bytes() == original_bytes, "Original must not be modified"
    assert existing_output.read_bytes() == SAFE_JPEG_BYTES, "Existing output must not be overwritten"


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
    input_file.write_bytes(SAFE_JPEG_BYTES)
    output_file.write_bytes(SAFE_JPEG_BYTES)
    original_unlink = Path.unlink

    # Source unlink failure is an OS-level condition that cannot be induced
    # portably without also making the test directory unusable.
    def fail_source_unlink(path: Path, missing_ok: bool = False) -> None:
        """Fail only deletion of the duplicate source."""
        if path == input_file:
            raise PermissionError("simulated duplicate deletion failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_source_unlink)

    def fake_pipeline(
        source_path: Path,
        staged_path: Path,
        **kwargs: object,
    ) -> None:
        """Produce byte-identical audited output for duplicate comparison."""
        del source_path, kwargs
        staged_path.write_bytes(SAFE_JPEG_BYTES)

    monkeypatch.setattr(scrub, "_do_scrub_pipeline", fake_pipeline)

    result = scrub.scrub_file(
        input_file,
        output_path=output_directory,
        on_duplicate="delete",
    )

    assert result.status == "error"
    assert input_file.read_bytes() == SAFE_JPEG_BYTES
    assert output_file.read_bytes() == SAFE_JPEG_BYTES


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


def test_silent_pipeline_privacy_failure_is_rejected_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero-error pipeline that returns the original JPEG cannot publish it."""
    source = tmp_path / "source.jpg"
    source.write_bytes((Path(__file__).parent / "assets" / "sample_with_exif.jpg").read_bytes())
    output_directory = tmp_path / "output"
    output_directory.mkdir()

    def copy_unscrubbed_source(
        input_path: Path,
        output_path: Path,
        **kwargs: object,
    ) -> None:
        """Simulate a dependency silently returning unscrubbed bytes."""
        del kwargs
        output_path.write_bytes(input_path.read_bytes())

    monkeypatch.setattr(scrub, "_do_scrub_pipeline", copy_unscrubbed_source)

    result = scrub.scrub_file(
        source,
        output_path=output_directory,
        paranoia=True,
    )

    assert result.status == "error"
    assert source.is_file()
    assert list(output_directory.iterdir()) == []
    assert "audit rejected" in (result.error_message or "")


def test_existing_unsafe_output_leaves_incoming_source_in_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unauditable destination fails closed without moving either file."""
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "output"
    processed_directory = tmp_path / "processed"
    for directory in (input_directory, output_directory, processed_directory):
        directory.mkdir()
    source = input_directory / "photo.jpg"
    create_fake_jpeg(source, "red")
    existing_output = output_directory / source.name
    existing_output.write_bytes(b"unscrubbed-or-corrupt")
    original_source = source.read_bytes()
    monkeypatch.setattr(scrub, "PROCESSED_DIR", processed_directory)

    result = scrub.scrub_file(source, output_path=output_directory, on_duplicate="move")
    summary = scrub.ScrubSummary()
    scrub._finalize_auto_result(source, result, summary, False, {})

    assert result.status == "unsafe_output"
    assert source.read_bytes() == original_source
    assert existing_output.read_bytes() == b"unscrubbed-or-corrupt"
    assert list(processed_directory.iterdir()) == []
    assert summary.errors == 1


def test_same_filename_different_content_moves_source_and_reports_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A safe but different destination is a visible collision, not a duplicate."""
    seed_directory = tmp_path / "seed"
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "output"
    errors_directory = tmp_path / "errors"
    for directory in (seed_directory, input_directory, output_directory, errors_directory):
        directory.mkdir()
    seed = seed_directory / "photo.jpg"
    create_fake_jpeg(seed, "red")
    first = scrub.scrub_file(seed, output_path=output_directory, on_duplicate="skip")
    assert first.status == "scrubbed"

    incoming = input_directory / "photo.jpg"
    create_fake_jpeg(incoming, "blue")
    incoming_bytes = incoming.read_bytes()
    existing_bytes = (output_directory / incoming.name).read_bytes()
    monkeypatch.setattr(scrub, "ERRORS_DIR", errors_directory)

    result = scrub.scrub_file(
        incoming,
        output_path=output_directory,
        on_duplicate="move",
    )
    summary = scrub.ScrubSummary()
    summary.update(result)

    assert result.status == "collision"
    assert result.duplicate_path is not None
    assert result.duplicate_path.read_bytes() == incoming_bytes
    assert (output_directory / incoming.name).read_bytes() == existing_bytes
    assert not incoming.exists()
    assert summary.errors == 1


def test_verified_duplicate_moves_to_errors_without_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A byte-identical audited result follows the default move policy."""
    seed_directory = tmp_path / "seed"
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "output"
    errors_directory = tmp_path / "errors"
    for directory in (seed_directory, input_directory, output_directory, errors_directory):
        directory.mkdir()
    seed = seed_directory / "photo.jpg"
    create_fake_jpeg(seed, "green")
    first = scrub.scrub_file(seed, output_path=output_directory, on_duplicate="skip")
    assert first.status == "scrubbed"

    incoming = input_directory / "photo.jpg"
    incoming.write_bytes(seed.read_bytes())
    incoming_bytes = incoming.read_bytes()
    monkeypatch.setattr(scrub, "ERRORS_DIR", errors_directory)

    result = scrub.scrub_file(
        incoming,
        output_path=output_directory,
        on_duplicate="move",
    )
    summary = scrub.ScrubSummary()
    summary.update(result)

    assert result.status == "duplicate"
    assert result.duplicate_path is not None
    assert result.duplicate_path.read_bytes() == incoming_bytes
    assert not incoming.exists()
    assert summary.errors == 0
    assert summary.duplicates_moved == 1


def test_extract_wanted_tags_accepts_requested_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The extraction boundary accepts the explicitly requested allowlist."""
    source = tmp_path / "source.jpg"
    source.write_bytes(SAFE_JPEG_BYTES)

    def return_requested_tags(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        """Return one valid ExifTool JSON record."""
        del kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='[{"SourceFile":"source.jpg","Orientation":1}]',
            stderr="",
        )

    monkeypatch.setattr(scrub.subprocess, "run", return_requested_tags)

    assert scrub.extract_wanted_tags(source) == {"Orientation": 1}


def test_extract_wanted_tags_rejects_unrequested_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected ExifTool key cannot expand the metadata allowlist."""
    source = tmp_path / "source.jpg"
    source.write_bytes(SAFE_JPEG_BYTES)

    def return_unrequested_tag(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        """Simulate a zero-exit dependency returning a forbidden key."""
        del kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='[{"SourceFile":"source.jpg","GPSLatitude":55.0}]',
            stderr="",
        )

    monkeypatch.setattr(scrub.subprocess, "run", return_unrequested_tag)

    with pytest.raises(RuntimeError, match="unexpected keys: GPSLatitude"):
        scrub.extract_wanted_tags(source)
