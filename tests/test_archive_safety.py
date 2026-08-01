# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for collision-safe original archival."""

import os
from pathlib import Path

import pytest

from scrubexif import scrub


def test_archive_no_clobber_uses_original_name_when_available(tmp_path: Path) -> None:
    """An available archive name is published and the source is removed."""
    source_directory = tmp_path / "input"
    archive_directory = tmp_path / "processed"
    source_directory.mkdir()
    archive_directory.mkdir()
    source = source_directory / "photo.jpg"
    source.write_bytes(b"original")

    archived = scrub._archive_no_clobber(source, archive_directory)

    assert archived == archive_directory / "photo.jpg"
    assert archived.read_bytes() == b"original"
    assert not source.exists()
    assert not any(path.name.startswith(".scrubexif_archive_") for path in archive_directory.iterdir())


def test_archive_no_clobber_collision_preserves_existing_and_rerolls(
    tmp_path: Path,
) -> None:
    """An occupied archive name is preserved and a fresh name receives the source."""
    source_directory = tmp_path / "input"
    archive_directory = tmp_path / "processed"
    source_directory.mkdir()
    archive_directory.mkdir()
    source = source_directory / "photo.jpg"
    source.write_bytes(b"new-original")
    occupied = archive_directory / source.name
    occupied.write_bytes(b"older-original")

    archived = scrub._archive_no_clobber(source, archive_directory)

    assert archived != occupied
    assert archived.parent == archive_directory
    assert archived.suffix == ".jpg"
    assert occupied.read_bytes() == b"older-original"
    assert archived.read_bytes() == b"new-original"
    assert not source.exists()


def test_archive_collision_truncates_stem_but_keeps_original_name_recognisable(
    tmp_path: Path,
) -> None:
    """A near-limit filename retains its original prefix before the random hash."""
    source_directory = tmp_path / "input"
    archive_directory = tmp_path / "processed"
    source_directory.mkdir()
    archive_directory.mkdir()
    original_stem = "recognisable-" + ("a" * 237)
    source = source_directory / f"{original_stem}.jpg"
    source.write_bytes(b"new-original")
    occupied = archive_directory / source.name
    occupied.write_bytes(b"older-original")

    archived = scrub._archive_no_clobber(source, archive_directory)

    name_max = os.pathconf(archive_directory, "PC_NAME_MAX")
    assert len(os.fsencode(archived.name)) <= name_max
    assert archived.name.startswith("recognisable-")
    assert archived.name.endswith(".jpg")
    assert occupied.read_bytes() == b"older-original"
    assert archived.read_bytes() == b"new-original"
    assert not source.exists()


def test_archive_no_clobber_exhausted_name_budget_preserves_source(
    tmp_path: Path,
) -> None:
    """Exhausting the configured reroll budget changes no user-owned files."""
    source_directory = tmp_path / "input"
    archive_directory = tmp_path / "processed"
    source_directory.mkdir()
    archive_directory.mkdir()
    source = source_directory / "photo.jpg"
    source.write_bytes(b"new-original")
    occupied = archive_directory / source.name
    occupied.write_bytes(b"older-original")

    with pytest.raises(scrub.ArchiveError, match="after 0 random re-rolls"):
        scrub._archive_no_clobber(source, archive_directory, max_rerolls=0)

    assert source.read_bytes() == b"new-original"
    assert occupied.read_bytes() == b"older-original"
    assert list(archive_directory.iterdir()) == [occupied]


def test_archive_no_clobber_publish_failure_preserves_source_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A destination-filesystem publication failure cannot consume the source."""
    source_directory = tmp_path / "input"
    archive_directory = tmp_path / "processed"
    source_directory.mkdir()
    archive_directory.mkdir()
    source = source_directory / "photo.jpg"
    source.write_bytes(b"original")

    # A reliable hard-link failure cannot be induced portably on every test filesystem.
    def fail_publication(temp_output: Path, destination: Path) -> None:
        """Simulate an OS-level failure while publishing the archive."""
        del temp_output, destination
        raise PermissionError("simulated archive publication failure")

    monkeypatch.setattr(scrub, "_publish_no_clobber", fail_publication)

    with pytest.raises(scrub.ArchiveError, match="simulated archive publication failure"):
        scrub._archive_no_clobber(source, archive_directory)

    assert source.read_bytes() == b"original"
    assert list(archive_directory.iterdir()) == []


def test_archive_no_clobber_source_removal_failure_preserves_both_copies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source-unlink failure leaves both the published archive and source intact."""
    source_directory = tmp_path / "input"
    archive_directory = tmp_path / "processed"
    source_directory.mkdir()
    archive_directory.mkdir()
    source = source_directory / "photo.jpg"
    source.write_bytes(b"original")
    original_unlink = Path.unlink

    # Source unlink failures are OS-level conditions that cannot be induced
    # portably without also changing directory access for the test process.
    def fail_source_unlink(path: Path, missing_ok: bool = False) -> None:
        """Fail only removal of the intake source."""
        if path == source:
            raise PermissionError("simulated source removal failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_source_unlink)

    with pytest.raises(scrub.ArchiveError, match="could not remove the source"):
        scrub._archive_no_clobber(source, archive_directory)

    assert source.read_bytes() == b"original"
    assert (archive_directory / source.name).read_bytes() == b"original"


def test_auto_finalization_keeps_repeated_original_names_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated intake names create two distinct processed originals."""
    input_directory = tmp_path / "input"
    processed_directory = tmp_path / "processed"
    input_directory.mkdir()
    processed_directory.mkdir()
    monkeypatch.setattr(scrub, "PROCESSED_DIR", processed_directory)
    source = input_directory / "photo.jpg"
    summary = scrub.ScrubSummary()
    state: dict[str, dict[str, float | int]] = {}

    source.write_bytes(b"first-original")
    first_result = scrub.ScrubResult(source, tmp_path / "output" / "photo.jpg")
    scrub._finalize_auto_result(source, first_result, summary, False, state)

    source.write_bytes(b"second-original")
    second_result = scrub.ScrubResult(source, tmp_path / "output" / "photo-2.jpg")
    scrub._finalize_auto_result(source, second_result, summary, False, state)

    archived_contents = {path.read_bytes() for path in processed_directory.iterdir()}
    assert archived_contents == {b"first-original", b"second-original"}
    assert len(list(processed_directory.iterdir())) == 2
    assert not source.exists()
    assert summary.scrubbed == 2
    assert summary.errors == 0


def test_duplicate_move_preserves_occupied_errors_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate archival never replaces an existing errors-directory entry."""
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "output"
    errors_directory = tmp_path / "errors"
    for directory in (input_directory, output_directory, errors_directory):
        directory.mkdir()
    source = input_directory / "photo.jpg"
    source.write_bytes(b"duplicate-original")
    (output_directory / source.name).write_bytes(b"scrubbed-output")
    occupied = errors_directory / source.name
    occupied.write_bytes(b"older-duplicate")
    monkeypatch.setattr(scrub, "ERRORS_DIR", errors_directory)

    result = scrub.scrub_file(
        source,
        output_path=output_directory,
        on_duplicate="move",
    )

    archived = [path for path in errors_directory.iterdir() if path != occupied]
    assert result.status == "duplicate"
    assert occupied.read_bytes() == b"older-duplicate"
    assert len(archived) == 1
    assert archived[0].read_bytes() == b"duplicate-original"
    assert not source.exists()
