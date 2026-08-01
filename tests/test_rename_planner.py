"""Tests for bounded, collision-safe rename planning."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from scrubexif.rename_planner import (
    RenamePlanLimits,
    RenamePlanningError,
    build_rename_plan,
    resolve_unique_destination,
)


def _make_sources(directory: Path, count: int) -> list[Path]:
    """Create distinct source paths cheaply using hard links.

    Args:
        directory: Directory that will contain the sources.
        count: Number of source paths to create.

    Returns:
        Created source paths.
    """
    seed_paths: list[Path] = []
    sources: list[Path] = []
    for index in range(count):
        seed_group = index // 50_000
        if seed_group == len(seed_paths):
            seed_path = directory / f"seed-{seed_group}.bin"
            seed_path.write_bytes(b"jpeg-placeholder")
            seed_paths.append(seed_path)
        source = directory / f"input-{index:06d}.jpg"
        os.link(seed_paths[seed_group], source)
        sources.append(source)
    for seed_path in seed_paths:
        seed_path.unlink()
    return sources


def test_build_rename_plan_sequential_format_maps_every_source(tmp_path: Path) -> None:
    """A valid batch produces unique mappings in input order."""
    sources = _make_sources(tmp_path, 3)

    with build_rename_plan(
        sources,
        "%n4",
        tmp_path / "output",
        {"n": 0},
        RenamePlanLimits(),
    ) as plan:
        entries = list(plan)

    assert [entry.source_path for entry in entries] == sources
    assert [entry.destination_path.name for entry in entries] == [
        "0001.jpg",
        "0002.jpg",
        "0003.jpg",
    ]


def test_build_rename_plan_deterministic_collision_rejects_entire_batch(
    tmp_path: Path,
) -> None:
    """A deterministic collision fails before any source is changed."""
    sources = _make_sources(tmp_path, 2)
    original_bytes = {source: source.read_bytes() for source in sources}

    with pytest.raises(RenamePlanningError, match="cannot be re-rolled"):
        build_rename_plan(
            sources,
            "fixed",
            None,
            {"n": 0},
            RenamePlanLimits(),
        )

    assert {source: source.read_bytes() for source in sources} == original_bytes
    assert not (tmp_path / "fixed.jpg").exists()


def test_resolve_unique_destination_random_collision_rerolls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A random collision is re-rolled and the occupied file is preserved."""
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    occupied = tmp_path / "taken.jpg"
    occupied.write_bytes(b"occupied")
    generated = iter(("taken", "fresh"))

    # Random collisions cannot be triggered deterministically through real entropy.
    monkeypatch.setattr(
        "scrubexif.rename_planner.resolve_rename",
        lambda _format, _source, _counter: next(generated),
    )

    destination = resolve_unique_destination(
        "%r5",
        source,
        tmp_path,
        {"n": 0},
        lambda candidate: candidate.exists(),
    )

    assert destination == tmp_path / "fresh.jpg"
    assert occupied.read_bytes() == b"occupied"


def test_build_rename_plan_file_limit_stops_without_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The file-count breaker aborts before returning an executable plan."""
    sources = _make_sources(tmp_path, 3)
    planning_temp = tmp_path / "planning-temp"
    planning_temp.mkdir()
    monkeypatch.setattr("scrubexif.rename_planner.tempfile.tempdir", str(planning_temp))

    with pytest.raises(RenamePlanningError, match="2-file limit"):
        build_rename_plan(
            sources,
            "%n4",
            tmp_path / "output",
            {"n": 0},
            RenamePlanLimits(max_files=2),
        )

    assert all(source.exists() for source in sources)
    assert not (tmp_path / "output").exists()
    assert list(planning_temp.iterdir()) == []


def test_build_rename_plan_timeout_stops_without_changes(tmp_path: Path) -> None:
    """The wall-clock breaker aborts a slow inventory without mutation."""
    source = _make_sources(tmp_path, 1)[0]

    def delayed_source() -> Iterator[Path]:
        """Delay source delivery beyond the planning deadline.

        Yields:
            One existing source path.
        """
        time.sleep(0.01)
        yield source

    with pytest.raises(RenamePlanningError, match="exceeded"):
        build_rename_plan(
            delayed_source(),
            "%n4",
            tmp_path / "output",
            {"n": 0},
            RenamePlanLimits(max_seconds=0.001),
        )

    assert source.exists()


def test_build_rename_plan_disk_limit_stops_without_changes(tmp_path: Path) -> None:
    """The temporary-database breaker aborts before file mutation."""
    source = _make_sources(tmp_path, 1)[0]

    with pytest.raises(RenamePlanningError, match="database exceeded"):
        build_rename_plan(
            [source],
            "%n4",
            tmp_path / "output",
            {"n": 0},
            RenamePlanLimits(max_database_bytes=1),
        )

    assert source.exists()


def test_build_rename_plan_reports_long_running_progress(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Planning warns immediately and reports periodic resource usage."""
    sources = _make_sources(tmp_path, 2)

    def delayed_sources() -> Iterator[Path]:
        """Yield sources slowly enough to trigger progress output.

        Yields:
            Existing source paths.
        """
        for source in sources:
            time.sleep(0.003)
            yield source

    with build_rename_plan(
        delayed_sources(),
        "%n4",
        tmp_path / "output",
        {"n": 0},
        RenamePlanLimits(progress_seconds=0.001),
    ):
        pass

    output = capsys.readouterr().out
    assert "Large directory hierarchies may take some time" in output
    assert "Rename planning (inventory)" in output


def test_build_rename_plan_context_removes_temporary_database(tmp_path: Path) -> None:
    """Closing a plan unconditionally removes its owned SQLite files."""
    source = _make_sources(tmp_path, 1)[0]
    plan = build_rename_plan(
        [source],
        "%n4",
        tmp_path / "output",
        {"n": 0},
        RenamePlanLimits(),
    )
    database_path = plan._database_path

    assert database_path.exists()
    plan.close()
    assert not database_path.exists()


def test_rename_plan_reassigns_late_random_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed plan can safely replace a newly occupied random reservation."""
    source = _make_sources(tmp_path, 1)[0]
    generated = iter(("initial", "replacement"))
    # Random collisions cannot be triggered deterministically through real entropy.
    monkeypatch.setattr(
        "scrubexif.rename_planner.resolve_rename",
        lambda _format, _source, _counter: next(generated),
    )

    with build_rename_plan(
        [source],
        "%r11",
        None,
        {"n": 0},
        RenamePlanLimits(),
    ) as plan:
        entry = next(iter(plan))
        entry.destination_path.write_bytes(b"active-filesystem")

        replacement = plan.reassign_destination(
            entry.entry_id,
            entry.destination_path,
        )

    assert replacement == tmp_path / "replacement.jpg"
    assert entry.destination_path.read_bytes() == b"active-filesystem"


def test_rename_plan_reassignment_avoids_future_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime re-rolls cannot steal another entry's reserved destination."""
    sources = _make_sources(tmp_path, 2)
    generated = iter(("first", "future", "future", "replacement"))
    # Random collisions cannot be triggered deterministically through real entropy.
    monkeypatch.setattr(
        "scrubexif.rename_planner.resolve_rename",
        lambda _format, _source, _counter: next(generated),
    )

    with build_rename_plan(
        sources,
        "%r11",
        None,
        {"n": 0},
        RenamePlanLimits(),
    ) as plan:
        entries = list(plan)
        entries[0].destination_path.write_bytes(b"active-filesystem")

        replacement = plan.reassign_destination(
            entries[0].entry_id,
            entries[0].destination_path,
        )

    assert replacement.name == "replacement.jpg"
    assert entries[1].destination_path.name == "future.jpg"


def test_rename_plan_late_deterministic_collision_remains_unrecoverable(
    tmp_path: Path,
) -> None:
    """A late deterministic collision is an error because no alternative exists."""
    source = _make_sources(tmp_path, 1)[0]

    with build_rename_plan(
        [source],
        "fixed",
        None,
        {"n": 0},
        RenamePlanLimits(),
    ) as plan:
        entry = next(iter(plan))
        entry.destination_path.write_bytes(b"active-filesystem")

        with pytest.raises(RenamePlanningError, match="cannot be re-rolled"):
            plan.reassign_destination(entry.entry_id, entry.destination_path)

    assert entry.destination_path.read_bytes() == b"active-filesystem"


@pytest.mark.soak
def test_build_rename_plan_scales_to_one_hundred_thousand_files(tmp_path: Path) -> None:
    """A 100,000-file batch remains disk-backed and produces all mappings."""
    sources = _make_sources(tmp_path, 100_000)

    with build_rename_plan(
        iter(sources),
        "%n6",
        tmp_path / "output",
        {"n": 0},
        RenamePlanLimits(max_files=100_001, max_seconds=300),
    ) as plan:
        first = next(iter(plan))
        count = sum(1 for _entry in plan)

    assert plan.count == 100_000
    assert first.destination_path.name == "000001.jpg"
    assert count == 100_000
