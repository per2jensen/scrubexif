# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for unified input discovery and processing-scope boundaries."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from scrubexif import scrub
from scrubexif.outcomes import InputClassification, OutcomeKind


def _discover(
    paths: list[Path],
    *,
    max_files: int | None = None,
    skip_reason_for_jpeg: Callable[[Path], str | None] | None = None,
) -> list[scrub.DiscoveredInput]:
    """Run unified discovery with mode-independent test defaults.

    Args:
        paths: Explicit files or directories in discovery order.
        max_files: Maximum JPEG candidates admitted for processing.
        skip_reason_for_jpeg: Optional early-skip classifier.

    Returns:
        Classified discovery inventory.
    """
    return scrub._discover_inputs(
        paths,
        recursive=True,
        exclude_pipeline_paths=False,
        max_files=max_files,
        skip_reason_for_jpeg=skip_reason_for_jpeg,
    )


def test_discovery_overlapping_path_references_produce_one_entry(
    tmp_path: Path,
) -> None:
    """The same pathname supplied directly and through a directory is deduplicated.

    Args:
        tmp_path: Isolated real filesystem tree.

    Returns:
        None.
    """
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg-placeholder")

    entries = _discover([photo, tmp_path])

    assert [entry.path for entry in entries] == [photo.resolve()]
    assert entries[0].admitted is True


def test_discovery_distinct_hard_links_produce_separate_entries(
    tmp_path: Path,
) -> None:
    """Distinct JPEG hard-link names remain separate processing obligations.

    Args:
        tmp_path: Isolated real filesystem tree.

    Returns:
        None.
    """
    first = tmp_path / "first.jpg"
    alias = tmp_path / "alias.jpg"
    first.write_bytes(b"shared-jpeg-placeholder")
    os.link(first, alias)

    entries = _discover([first, alias])

    assert [entry.path for entry in entries] == [first.resolve(), alias.resolve()]
    assert entries[0].filesystem_identity == entries[1].filesystem_identity
    assert all(entry.admitted for entry in entries)


def test_discovery_hard_links_with_different_extensions_classify_independently(
    tmp_path: Path,
) -> None:
    """A hard link's extension controls its own classification.

    Args:
        tmp_path: Isolated real filesystem tree.

    Returns:
        None.
    """
    jpeg_name = tmp_path / "photo.jpg"
    png_name = tmp_path / "photo.png"
    jpeg_name.write_bytes(b"shared-placeholder")
    os.link(jpeg_name, png_name)

    entries = _discover([png_name, jpeg_name])

    assert [entry.classification for entry in entries] == [
        InputClassification.UNSUPPORTED_REGULAR,
        InputClassification.ELIGIBLE_JPEG,
    ]
    assert entries[0].filesystem_identity == entries[1].filesystem_identity


def test_discovery_skipped_and_unsupported_inputs_do_not_consume_limit(
    tmp_path: Path,
) -> None:
    """Discovery continues past skipped and unsupported entries to one admission.

    Args:
        tmp_path: Isolated real filesystem tree.

    Returns:
        None.
    """
    skipped = tmp_path / "skipped.jpg"
    unsupported_before = tmp_path / "before.png"
    admitted = tmp_path / "admitted.jpg"
    unsupported_after = tmp_path / "after.txt"
    later_jpeg = tmp_path / "later.jpg"
    for path in (
        skipped,
        unsupported_before,
        admitted,
        unsupported_after,
        later_jpeg,
    ):
        path.write_bytes(path.name.encode("utf-8"))

    def classify_skip(path: Path) -> str | None:
        """Skip only the designated JPEG.

        Args:
            path: Eligible JPEG candidate.

        Returns:
            Stable skip reason for the designated path, otherwise None.
        """
        return "unstable_input" if path == skipped.resolve() else None

    entries = _discover(
        [
            skipped,
            unsupported_before,
            admitted,
            unsupported_after,
            later_jpeg,
        ],
        max_files=1,
        skip_reason_for_jpeg=classify_skip,
    )

    assert [entry.path for entry in entries] == [
        skipped.resolve(),
        unsupported_before.resolve(),
        admitted.resolve(),
    ]
    assert entries[0].skip_reason == "unstable_input"
    assert entries[1].classification is InputClassification.UNSUPPORTED_REGULAR
    assert entries[2].admitted is True


def test_discovery_admitted_candidate_reserves_limit_slot_before_processing(
    tmp_path: Path,
) -> None:
    """The first admitted JPEG owns the slot even if later processing may fail.

    Args:
        tmp_path: Isolated real filesystem tree.

    Returns:
        None.
    """
    invalid_jpeg = tmp_path / "invalid.jpg"
    later_jpeg = tmp_path / "later.jpg"
    invalid_jpeg.write_bytes(b"not-really-a-jpeg")
    later_jpeg.write_bytes(b"later")

    entries = _discover([invalid_jpeg, later_jpeg], max_files=1)

    assert [entry.path for entry in entries] == [invalid_jpeg.resolve()]
    assert entries[0].admitted is True


def test_discovery_zero_limit_examines_nothing(tmp_path: Path) -> None:
    """A zero processing limit creates an empty operation scope.

    Args:
        tmp_path: Isolated real filesystem tree.

    Returns:
        None.
    """
    unsupported = tmp_path / "ignored.png"
    jpeg = tmp_path / "photo.jpg"
    unsupported.write_bytes(b"png")
    jpeg.write_bytes(b"jpeg")

    assert _discover([unsupported, jpeg], max_files=0) == []


def test_discovery_negative_limit_raises_value_error(tmp_path: Path) -> None:
    """A negative processing limit is rejected before filesystem traversal.

    Args:
        tmp_path: Isolated real filesystem tree.

    Returns:
        None.
    """
    with pytest.raises(ValueError, match="max_files"):
        _discover([tmp_path], max_files=-1)


def test_discovery_revalidation_accepts_unchanged_input(tmp_path: Path) -> None:
    """An unchanged admitted JPEG passes identity revalidation.

    Args:
        tmp_path: Isolated real filesystem tree.

    Returns:
        None.
    """
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"unchanged")
    entry = _discover([photo])[0]

    assert scrub._discovery_revalidation_failure(entry) is None


def test_discovery_revalidation_rejects_replaced_input(tmp_path: Path) -> None:
    """Replacing a pathname after discovery produces a typed failure.

    Args:
        tmp_path: Isolated real filesystem tree.

    Returns:
        None.
    """
    photo = tmp_path / "photo.jpg"
    replacement = tmp_path / "replacement.jpg"
    photo.write_bytes(b"original")
    replacement.write_bytes(b"replacement")
    entry = _discover([photo])[0]
    os.replace(replacement, photo)

    failure = scrub._discovery_revalidation_failure(entry)

    assert failure is not None
    assert failure.kind is OutcomeKind.FAILED
    assert failure.reason_code == "input_changed_after_discovery"


def test_manual_dry_run_records_each_hard_link_path(tmp_path: Path) -> None:
    """Clean-inline discovery retains both hard-link publication obligations.

    Args:
        tmp_path: Isolated real filesystem tree.

    Returns:
        None.
    """
    first = tmp_path / "first.jpg"
    alias = tmp_path / "alias.jpg"
    first.write_bytes(b"shared-placeholder")
    os.link(first, alias)
    summary = scrub.ScrubSummary()

    scrub.manual_scrub(
        [first, alias],
        summary=summary,
        recursive=False,
        dry_run=True,
    )

    assert summary.report.planned == 2
    assert summary.report.examined == 2


def test_manual_max_files_counts_only_entries_before_admission_boundary(
    tmp_path: Path,
) -> None:
    """Manual-mode counters exclude entries after the admitted JPEG limit.

    Args:
        tmp_path: Isolated real filesystem tree.

    Returns:
        None.
    """
    unsupported_before = tmp_path / "before.png"
    admitted = tmp_path / "admitted.jpg"
    unsupported_after = tmp_path / "after.txt"
    later_jpeg = tmp_path / "later.jpg"
    for path in (
        unsupported_before,
        admitted,
        unsupported_after,
        later_jpeg,
    ):
        path.write_bytes(path.name.encode("utf-8"))
    summary = scrub.ScrubSummary()

    scrub.manual_scrub(
        [unsupported_before, admitted, unsupported_after, later_jpeg],
        summary=summary,
        recursive=False,
        dry_run=True,
        max_files=1,
    )

    assert summary.report.planned == 1
    assert summary.report.unsupported == 1
    assert summary.report.examined == 2


def test_manual_max_files_zero_records_empty_scope(tmp_path: Path) -> None:
    """Manual mode records no outcomes when the user requests a zero limit.

    Args:
        tmp_path: Isolated real filesystem tree.

    Returns:
        None.
    """
    unsupported = tmp_path / "ignored.png"
    jpeg = tmp_path / "photo.jpg"
    unsupported.write_bytes(b"png")
    jpeg.write_bytes(b"jpeg")
    summary = scrub.ScrubSummary()

    scrub.manual_scrub(
        [unsupported, jpeg],
        summary=summary,
        recursive=False,
        dry_run=True,
        max_files=0,
    )

    assert summary.report.examined == 0
    assert summary.report.unsupported == 0
    assert summary.report.planned == 0
