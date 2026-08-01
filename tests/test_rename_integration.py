"""Integration tests for --rename using real JPEGs and real exiftool.

No Docker required. Tests call scrub_file() and the CLI directly with actual
JPEG fixtures to verify end-to-end rename behaviour.
"""

import re
import shutil
from functools import partial
from pathlib import Path

import pytest

import scrubexif.scrub as scrub
from scrubexif.scrub import main, scrub_file
from scrubexif.rename_planner import RenamePlanLimits, build_rename_plan

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ASSETS = Path(__file__).parent / "assets"

# Has DateTimeOriginal: 2000:01:01 04:00:00
FIXTURE_WITH_EXIF = ASSETS / "sample_with_exif.jpg"

# No DateTimeOriginal at all
FIXTURE_NO_DATETIME = ASSETS / "test_base.jpg"


@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    """Provide a clean temporary working directory per test."""
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scrub_to_dir(
    src: Path,
    out_dir: Path,
    rename_format: str | None = None,
    counter: dict[str, int] | None = None,
) -> Path:
    """Run scrub_file and return the output path."""
    result = scrub_file(
        src,
        output_path=out_dir,
        rename_format=rename_format,
        rename_counter=counter or {"n": 0},
    )
    assert result.status == "scrubbed", f"Expected scrubbed, got {result.status}: {result.error_message}"
    return result.output_path


# ---------------------------------------------------------------------------
# Basic token tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_rename_r8_produces_8_hex_chars(work_dir: Path) -> None:
    result = _scrub_to_dir(FIXTURE_WITH_EXIF, work_dir, "%r8")
    stem = result.stem
    assert re.match(r"^[0-9a-f]{8}$", stem), f"Unexpected stem: {stem!r}"
    assert result.suffix == FIXTURE_WITH_EXIF.suffix


@pytest.mark.integration
def test_rename_u_produces_uuid_filename(work_dir: Path) -> None:
    result = _scrub_to_dir(FIXTURE_WITH_EXIF, work_dir, "%u")
    assert _UUID_RE.match(result.stem), f"Not a UUID: {result.stem!r}"
    assert result.suffix == FIXTURE_WITH_EXIF.suffix


@pytest.mark.integration
def test_rename_prefix_r6(work_dir: Path) -> None:
    result = _scrub_to_dir(FIXTURE_WITH_EXIF, work_dir, "850_%r6")
    assert result.stem.startswith("850_")
    assert len(result.stem) == 10  # "850_" (4) + 6 hex


@pytest.mark.integration
def test_rename_n4_sequential_three_files(work_dir: Path) -> None:
    counter = {"n": 0}
    stems = []
    for _ in range(3):
        r = _scrub_to_dir(FIXTURE_WITH_EXIF, work_dir, "%n4", counter)
        stems.append(r.stem)
    assert stems == ["0001", "0002", "0003"]


@pytest.mark.integration
def test_rename_year_month_prefix_with_exif(work_dir: Path) -> None:
    result = _scrub_to_dir(FIXTURE_WITH_EXIF, work_dir, "%Y%m_%r6")
    # DateTimeOriginal is 2000:01:01 → year=2000, month=01
    assert result.stem.startswith("200001_"), f"Unexpected stem: {result.stem!r}"
    assert len(result.stem) == 13  # "200001_" (7) + 6 hex


# ---------------------------------------------------------------------------
# EXIF fallback tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_rename_year_month_fallback_to_uuid_when_no_datetime(
    work_dir: Path, capsys
) -> None:
    result = _scrub_to_dir(FIXTURE_NO_DATETIME, work_dir, "%Y%m_%r6")
    assert _UUID_RE.match(result.stem), f"Expected UUID fallback, got {result.stem!r}"
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "EXIF DateTimeOriginal absent" in captured.out


# ---------------------------------------------------------------------------
# Extension preservation
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_rename_preserves_jpeg_extension(work_dir: Path, tmp_path: Path) -> None:
    # Copy fixture with .jpeg extension to confirm suffix is preserved exactly.
    src = tmp_path / "photo.jpeg"
    shutil.copy(FIXTURE_WITH_EXIF, src)
    result = _scrub_to_dir(src, work_dir, "%r8")
    assert result.suffix == ".jpeg"


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_dry_run_rename_prints_proposed_name_and_no_file_written(
    work_dir: Path, capsys
) -> None:
    result = scrub_file(
        FIXTURE_WITH_EXIF,
        output_path=work_dir,
        dry_run=True,
        rename_format="%r8",
        rename_counter={"n": 0},
    )
    assert result.status == "scrubbed"
    captured = capsys.readouterr()
    assert "→" in captured.out
    # No file should have been written in dry-run.
    outputs = list(work_dir.iterdir())
    assert outputs == [], f"Unexpected files written: {outputs}"


@pytest.mark.integration
def test_dry_run_no_datetime_shows_fallback_warning(work_dir: Path, capsys) -> None:
    scrub_file(
        FIXTURE_NO_DATETIME,
        output_path=work_dir,
        dry_run=True,
        rename_format="%Y%m_%r6",
        rename_counter={"n": 0},
    )
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "EXIF DateTimeOriginal absent" in captured.out
    assert list(work_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Helpers for CLI tests that need module-level globals patched
# ---------------------------------------------------------------------------

def _setup_auto_env(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    """Create input/output/processed dirs and patch scrub module globals."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    for d in (input_dir, output_dir, processed_dir):
        d.mkdir(parents=True)
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", tmp_path)
    monkeypatch.setattr(scrub, "INPUT_DIR", input_dir)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(scrub, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(scrub, "ERRORS_DIR", tmp_path / "errors")
    return input_dir, output_dir, processed_dir


# ---------------------------------------------------------------------------
# --paranoia implies %r8 via CLI
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_paranoia_implies_r8_via_cli(tmp_path: Path, monkeypatch) -> None:
    input_dir, output_dir, _ = _setup_auto_env(tmp_path, monkeypatch)
    shutil.copy(FIXTURE_WITH_EXIF, input_dir / "photo.jpg")

    rc = main(["--from-input", "--paranoia"])

    assert rc == 0
    outputs = list(output_dir.iterdir())
    assert len(outputs) == 1
    stem = outputs[0].stem
    assert re.match(r"^[0-9a-f]{8}$", stem), f"Expected 8 hex chars, got {stem!r}"


# ---------------------------------------------------------------------------
# --clean-inline --paranoia implies %r8 rename in-place
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_clean_inline_paranoia_implies_r8_rename(tmp_path: Path, monkeypatch) -> None:
    """--clean-inline --paranoia scrubs in-place and renames to 8-char hex (implied %r8)."""
    src = tmp_path / "photo.jpg"
    shutil.copy(FIXTURE_WITH_EXIF, src)

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", tmp_path)

    rc = main(["--clean-inline", "--paranoia"])

    assert rc == 0
    assert not src.exists(), "Original path should be gone after in-place rename"
    remaining = [f for f in tmp_path.iterdir() if f.is_file()]
    assert len(remaining) == 1, f"Expected 1 file, got {remaining}"
    stem = remaining[0].stem
    assert re.match(r"^[0-9a-f]{8}$", stem), f"Expected 8 hex chars, got {stem!r}"
    assert remaining[0].suffix == src.suffix


# ---------------------------------------------------------------------------
# Explicit --rename overrides --paranoia
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_explicit_rename_overrides_paranoia_via_cli(tmp_path: Path, monkeypatch) -> None:
    input_dir, output_dir, _ = _setup_auto_env(tmp_path, monkeypatch)
    shutil.copy(FIXTURE_WITH_EXIF, input_dir / "photo.jpg")

    rc = main(["--from-input", "--paranoia", "--rename", "%u"])

    assert rc == 0
    outputs = list(output_dir.iterdir())
    assert len(outputs) == 1
    stem = outputs[0].stem
    assert _UUID_RE.match(stem), f"Expected UUID, got {stem!r}"


# ---------------------------------------------------------------------------
# Invalid format rejected before any files are touched
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_invalid_format_exits_before_scrub(tmp_path: Path, monkeypatch) -> None:
    input_dir, output_dir, _ = _setup_auto_env(tmp_path, monkeypatch)
    shutil.copy(FIXTURE_WITH_EXIF, input_dir / "photo.jpg")

    with pytest.raises(SystemExit) as exc_info:
        main(["--from-input", "--rename", "%H%M"])

    assert exc_info.value.code != 0
    assert list(output_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# --clean-inline + --rename: scrub in-place then rename (scrub_file level)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_clean_inline_scrub_file_renames_in_place(tmp_path: Path) -> None:
    """scrub_file with output_path=None and rename_format scrubs then renames the file."""
    src = tmp_path / "photo.jpg"
    shutil.copy(FIXTURE_WITH_EXIF, src)

    result = scrub_file(
        src,
        output_path=None,
        rename_format="%r8",
        rename_counter={"n": 0},
    )

    assert result.status == "scrubbed"
    # Original path must be gone.
    assert not src.exists(), "Original path should be gone after in-place rename"
    # Renamed file must exist with 8-char hex stem.
    assert result.output_path.exists()
    assert re.match(r"^[0-9a-f]{8}$", result.output_path.stem)
    assert result.output_path.suffix == src.suffix
    assert result.output_path.parent == src.parent


# ---------------------------------------------------------------------------
# --clean-inline + --rename via CLI: file scrubbed and renamed in-place
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_clean_inline_cli_rename_renames_in_place(tmp_path: Path, monkeypatch) -> None:
    """--clean-inline --rename scrubs the file in-place and renames it in the same directory."""
    src = tmp_path / "photo.jpg"
    shutil.copy(FIXTURE_WITH_EXIF, src)

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", tmp_path)

    rc = main(["--clean-inline", "--rename", "%r8", str(src)])

    assert rc == 0
    # Original filename must be gone.
    assert not src.exists(), "Original path should not exist after in-place rename"
    # Exactly one file must remain — the renamed scrubbed version.
    remaining = [f for f in tmp_path.iterdir() if f.is_file()]
    assert len(remaining) == 1, f"Expected 1 file, got {remaining}"
    stem = remaining[0].stem
    assert re.match(r"^[0-9a-f]{8}$", stem), f"Expected 8 hex chars, got {stem!r}"
    assert remaining[0].suffix == src.suffix


@pytest.mark.integration
def test_clean_inline_cli_rename_multi_file(tmp_path: Path, monkeypatch) -> None:
    """--clean-inline --rename renames all files with the same prefix format."""
    files = []
    for i in range(3):
        f = tmp_path / f"photo_{i}.jpg"
        shutil.copy(FIXTURE_WITH_EXIF, f)
        files.append(f)

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", tmp_path)

    rc = main(["--clean-inline", "--rename", "850_%r6", str(tmp_path)])

    assert rc == 0
    # All originals must be gone.
    for f in files:
        assert not f.exists(), f"Original {f.name} should be gone"
    # Three renamed files must remain.
    remaining = [f for f in tmp_path.iterdir() if f.is_file()]
    assert len(remaining) == 3
    for f in remaining:
        assert re.match(r"^850_[0-9a-f]{6}$", f.stem), f"Unexpected stem: {f.stem!r}"
        assert f.suffix == ".jpg"


@pytest.mark.integration
def test_clean_inline_cli_random_collision_rerolls_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An occupied random name is preserved and a new name is generated."""
    src = tmp_path / "photo.jpg"
    shutil.copy(FIXTURE_WITH_EXIF, src)
    occupied = tmp_path / "taken.jpg"
    occupied.write_bytes(b"do-not-overwrite")
    generated = iter(("taken", "fresh"))

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", tmp_path)
    # Random collisions cannot be triggered deterministically through real entropy.
    monkeypatch.setattr(
        "scrubexif.rename_planner.resolve_rename",
        lambda _format, _source, _counter: next(generated),
    )

    rc = main(["--clean-inline", "--rename", "%r5", str(src)])

    assert rc == 0
    assert occupied.read_bytes() == b"do-not-overwrite"
    assert not src.exists()
    assert (tmp_path / "fresh.jpg").is_file()


@pytest.mark.integration
def test_clean_inline_cli_unresolved_collision_aborts_before_scrub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausted random retries leave both source and destination unchanged."""
    src = tmp_path / "photo.jpg"
    shutil.copy(FIXTURE_WITH_EXIF, src)
    source_bytes = src.read_bytes()
    occupied = tmp_path / "taken.jpg"
    occupied.write_bytes(b"do-not-overwrite")

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", tmp_path)
    # Random collisions cannot be triggered deterministically through real entropy.
    monkeypatch.setattr(
        "scrubexif.rename_planner.resolve_rename",
        lambda _format, _source, _counter: "taken",
    )

    with pytest.raises(SystemExit) as exc_info:
        main(["--clean-inline", "--rename", "%r5", str(src)])

    assert exc_info.value.code == 1
    assert src.read_bytes() == source_bytes
    assert occupied.read_bytes() == b"do-not-overwrite"


@pytest.mark.integration
def test_clean_inline_cli_batch_collision_aborts_before_any_scrub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deterministic batch collision cannot leave a partially renamed batch."""
    sources = [tmp_path / "one.jpg", tmp_path / "two.jpg"]
    for source in sources:
        shutil.copy(FIXTURE_WITH_EXIF, source)
    original_bytes = {source: source.read_bytes() for source in sources}

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        main(["--clean-inline", "--rename", "fixed", *map(str, sources)])

    assert exc_info.value.code == 1
    assert {source: source.read_bytes() for source in sources} == original_bytes
    assert not (tmp_path / "fixed.jpg").exists()


@pytest.mark.integration
def test_clean_inline_cli_rename_preview_preserves_source_and_shows_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rename preview uses the real planner and scrubs only a disposable copy."""
    src = tmp_path / "photo.jpg"
    shutil.copy(FIXTURE_WITH_EXIF, src)
    source_bytes = src.read_bytes()
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", tmp_path)

    rc = main(["--clean-inline", "--rename", "%r8", "--preview", str(src)])

    output = capsys.readouterr().out
    assert rc == 0
    assert src.read_bytes() == source_bytes
    assert [path for path in tmp_path.iterdir() if path.is_file()] == [src]
    assert "Rename plan ready" in output
    assert "Would scrub:" in output
    assert "Preview complete" in output


@pytest.mark.integration
def test_clean_inline_late_pre_scrub_collision_reassigns_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A destination occupied after planning is harmless when it can be re-rolled."""
    src = tmp_path / "photo.jpg"
    shutil.copy(FIXTURE_WITH_EXIF, src)
    generated = iter(("initial", "replacement"))
    # Random collisions cannot be triggered deterministically through real entropy.
    monkeypatch.setattr(
        "scrubexif.rename_planner.resolve_rename",
        lambda _format, _source, _counter: next(generated),
    )

    with build_rename_plan(
        [src],
        "%r11",
        None,
        {"n": 0},
        RenamePlanLimits(),
    ) as plan:
        entry = next(iter(plan))
        entry.destination_path.write_bytes(b"active-filesystem")
        result = scrub_file(
            src,
            output_path=None,
            planned_rename_path=entry.destination_path,
            rename_destination_allocator=partial(
                plan.reassign_destination,
                entry.entry_id,
            ),
        )

    output = capsys.readouterr().out
    assert result.status == "scrubbed"
    assert result.output_path == tmp_path / "replacement.jpg"
    assert entry.destination_path.read_bytes() == b"active-filesystem"
    assert not src.exists()
    assert "active filesystem" in output


@pytest.mark.integration
def test_clean_inline_publication_race_reuses_scrubbed_temp_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A publication-time collision reassigns without overwriting or re-scrubbing."""
    src = tmp_path / "photo.jpg"
    shutil.copy(FIXTURE_WITH_EXIF, src)
    generated = iter(("initial", "replacement"))
    published_temp_paths: list[Path] = []
    real_publish = scrub._publish_no_clobber

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", tmp_path)
    # Random collisions cannot be triggered deterministically through real entropy.
    monkeypatch.setattr(
        "scrubexif.rename_planner.resolve_rename",
        lambda _format, _source, _counter: next(generated),
    )

    def publish_with_one_race(temp_output: Path, destination: Path) -> None:
        """Inject one otherwise non-deterministic publication race."""
        published_temp_paths.append(temp_output)
        if len(published_temp_paths) == 1:
            destination.write_bytes(b"active-filesystem")
        real_publish(temp_output, destination)

    monkeypatch.setattr(scrub, "_publish_no_clobber", publish_with_one_race)

    rc = main(["--clean-inline", "--rename", "%r11", str(src)])

    output = capsys.readouterr().out
    assert rc == 0
    assert len(published_temp_paths) == 2
    assert published_temp_paths[0] == published_temp_paths[1]
    assert (tmp_path / "initial.jpg").read_bytes() == b"active-filesystem"
    assert (tmp_path / "replacement.jpg").is_file()
    assert not src.exists()
    assert "active filesystem" in output


@pytest.mark.integration
def test_clean_inline_continuous_publication_collisions_stop_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Continuously occupied replacements eventually stop without losing the source."""
    src = tmp_path / "photo.jpg"
    shutil.copy(FIXTURE_WITH_EXIF, src)
    source_bytes = src.read_bytes()
    destinations = iter(
        tmp_path / f"replacement-{index}.jpg"
        for index in range(1, 5)
    )
    real_publish = scrub._publish_no_clobber

    # Repeated publication races cannot be triggered reliably on a real filesystem.
    def publish_with_continuous_race(temp_output: Path, destination: Path) -> None:
        """Occupy every destination immediately before atomic publication."""
        destination.write_bytes(b"active-filesystem")
        real_publish(temp_output, destination)

    def allocate_replacement(_occupied: Path) -> Path:
        """Return the next replacement destination for the race simulation."""
        return next(destinations)

    monkeypatch.setattr(scrub, "_publish_no_clobber", publish_with_continuous_race)

    result = scrub_file(
        src,
        output_path=None,
        planned_rename_path=tmp_path / "initial.jpg",
        rename_destination_allocator=allocate_replacement,
    )

    assert result.status == "conflict"
    assert src.read_bytes() == source_bytes
    assert all(
        path.read_bytes() == b"active-filesystem"
        for path in [
            tmp_path / "initial.jpg",
            tmp_path / "replacement-1.jpg",
            tmp_path / "replacement-2.jpg",
            tmp_path / "replacement-3.jpg",
        ]
    )
    assert not any(
        path.name.startswith(".scrubexif_tmp_")
        for path in tmp_path.iterdir()
    )


# ---------------------------------------------------------------------------
# No --rename, no --paranoia: original filenames must be preserved
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_clean_inline_no_rename_preserves_filename(tmp_path: Path, monkeypatch) -> None:
    """--clean-inline without --rename must leave the filename unchanged."""
    src = tmp_path / "photo.jpg"
    shutil.copy(FIXTURE_WITH_EXIF, src)

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", tmp_path)

    rc = main(["--clean-inline", str(src)])

    assert rc == 0
    assert src.exists(), "File should still exist under its original name"
    remaining = [f for f in tmp_path.iterdir() if f.is_file()]
    assert remaining == [src], f"Unexpected files: {remaining}"


@pytest.mark.integration
def test_default_safe_mode_no_rename_preserves_filename(tmp_path: Path, monkeypatch) -> None:
    """Default safe mode without --rename must copy with the original filename."""
    photos_root = tmp_path / "photos"
    photos_root.mkdir()
    output_dir = photos_root / "output"

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", photos_root)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(scrub, "INPUT_DIR", photos_root / "input")
    monkeypatch.setattr(scrub, "PROCESSED_DIR", photos_root / "processed")
    monkeypatch.setattr(scrub, "ERRORS_DIR", photos_root / "errors")

    src = photos_root / "photo.jpg"
    shutil.copy(FIXTURE_WITH_EXIF, src)

    rc = main([])

    assert rc == 0
    outputs = [f for f in output_dir.iterdir() if f.is_file()]
    assert len(outputs) == 1, f"Expected 1 output file, got {outputs}"
    assert outputs[0].name == src.name, f"Expected {src.name!r}, got {outputs[0].name!r}"


# ---------------------------------------------------------------------------
# Default safe mode via CLI: rename is applied to output dir
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_default_safe_mode_cli_rename_r8(tmp_path: Path, monkeypatch) -> None:
    """Default safe mode writes renamed copies to OUTPUT_DIR, originals untouched."""
    photos_root = tmp_path / "photos"
    photos_root.mkdir()
    output_dir = photos_root / "output"

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", photos_root)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(scrub, "INPUT_DIR", photos_root / "input")
    monkeypatch.setattr(scrub, "PROCESSED_DIR", photos_root / "processed")
    monkeypatch.setattr(scrub, "ERRORS_DIR", photos_root / "errors")

    src = photos_root / "photo.jpg"
    shutil.copy(FIXTURE_WITH_EXIF, src)

    rc = main(["--rename", "%r8"])

    assert rc == 0
    outputs = [f for f in output_dir.iterdir() if f.is_file()]
    assert len(outputs) == 1, f"Expected 1 output file, got {outputs}"
    stem = outputs[0].stem
    assert re.match(r"^[0-9a-f]{8}$", stem), f"Expected 8 hex chars, got {stem!r}"
    assert outputs[0].suffix == src.suffix
    # Original must be untouched.
    assert src.exists()
    assert src.name == "photo.jpg"
