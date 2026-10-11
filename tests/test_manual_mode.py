# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests for scrubexif clean-inline mode.

✅ Covers:
- Supplying two files
- Supplying no files or dirs
- Using -r and --recursive
"""

import subprocess
import shutil
import os
from pathlib import Path
import pytest

from scrubexif import scrub

from .conftest import SAMPLE_BYTES  # Explicit import

IMAGE = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")
ASSETS_DIR = Path(__file__).parent / "assets"
SAMPLE_IMG = ASSETS_DIR / "sample_with_exif.jpg"


@pytest.fixture
def sample_files(tmp_path):
    """Create two JPEG copies in a temp directory."""
    dst1 = tmp_path / "one.jpg"
    dst2 = tmp_path / "two.jpeg"
    shutil.copy(SAMPLE_IMG, dst1)
    shutil.copy(SAMPLE_IMG, dst2)
    return dst1, dst2


def run_container_manual(args: list[str], mounts: list[str] = None):
    """Run scrubexif container in clean-inline mode."""
    user_flag = ["--user", str(os.getuid())] if os.getuid() != 0 else []
    mounts = mounts or []
    return subprocess.run(
        [
            "docker",
            "run",
            "--read-only",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,size=64m",
            "--rm",
        ]
        + user_flag
        + mounts
        + [IMAGE]
        + args,
        capture_output=True, text=True
    )


def test_manual_mode_two_files(sample_files):
    f1, f2 = sample_files
    result = run_container_manual(["--clean-inline", "--log-level", "debug",
        f"/photos/{f1.name}", f"/photos/{f2.name}"
    ], mounts=["-v", f"{f1.parent}:/photos"])

    assert result.returncode == 0, result.stderr
    assert "✅ Saved scrubbed file" in result.stdout


@pytest.mark.regression
def test_manual_mode_hard_link_aliases_are_both_scrubbed(tmp_path: Path) -> None:
    """Atomic clean-inline publication sanitizes every hard-link pathname.

    Args:
        tmp_path: Isolated directory mounted into the container.

    Returns:
        None.
    """
    first = tmp_path / "first.jpg"
    alias = tmp_path / "alias.jpg"
    shutil.copy(SAMPLE_IMG, first)
    os.link(first, alias)
    assert first.stat().st_ino == alias.stat().st_ino

    result = run_container_manual(
        ["--clean-inline", f"/photos/{first.name}", f"/photos/{alias.name}"],
        mounts=["-v", f"{tmp_path}:/photos"],
    )

    assert result.returncode == 0, result.stderr
    saved_paths = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("✅ Saved scrubbed file to ")
    ]
    assert len(saved_paths) == 2
    assert any(path.endswith("/first.jpg") for path in saved_paths)
    assert any(path.endswith("/alias.jpg") for path in saved_paths)
    assert first.stat().st_ino != alias.stat().st_ino


def test_manual_mode_no_files(tmp_path):
    result = run_container_manual(["--clean-inline", "--log-level", "debug"], mounts=["-v", f"{tmp_path}:/photos"])
    assert result.returncode == 0
    assert "⚠️ No files provided" in result.stdout or "⚠️ No JPEGs matched" in result.stdout


def test_manual_mode_recursive_short_flag(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    shutil.copy(SAMPLE_IMG, sub / "img.jpg")

    result = run_container_manual(["--clean-inline", "--log-level", "debug",  "-r", "/photos"], mounts=["-v", f"{tmp_path}:/photos"])
    assert result.returncode == 0
    assert "✅ Saved scrubbed file" in result.stdout


def test_manual_mode_recursive_long_flag(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    shutil.copy(SAMPLE_IMG, sub / "img.jpg")

    result = run_container_manual(["--clean-inline", "--log-level", "debug", "--recursive", "/photos"], mounts=["-v", f"{tmp_path}:/photos"])
    assert result.returncode == 0
    assert "✅ Saved scrubbed file" in result.stdout


def test_manual_mode_recursive_no_args(tmp_path: Path) -> None:
    """Scrub the nested JPEG when only the recursive option is supplied.

    Docker cannot reconstruct the host mount prefix when the source directory
    lives on a separate filesystem such as a tmpfs-mounted ``/tmp``. Verify the
    stable path suffix that identifies the intended file instead.

    Args:
        tmp_path: Pytest temporary directory mounted at ``/photos``.

    Returns:
        None.

    Raises:
        AssertionError: If recursive mode fails or reports the wrong file.
    """
    sub = tmp_path / "deep"
    sub.mkdir()
    target = sub / "img.jpg"
    shutil.copy(SAMPLE_IMG, target)

    result = run_container_manual(
        ["--clean-inline", "--log-level", "debug", "--recursive"],
        mounts=["-v", f"{tmp_path}:/photos"]
    )

    assert result.returncode == 0
    saved_prefix = "✅ Saved scrubbed file to "
    saved_paths = [
        line.removeprefix(saved_prefix)
        for line in result.stdout.splitlines()
        if line.startswith(saved_prefix)
    ]
    assert len(saved_paths) == 1
    assert saved_paths[0].endswith("/deep/img.jpg")


def test_manual_preview_cleans_tempfiles(tmp_path, monkeypatch):
    sample = tmp_path / "sample.jpg"
    shutil.copy(SAMPLE_IMG, sample)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)
    monkeypatch.setattr(scrub, "print_tags", lambda *a, **k: None)

    summary = scrub.ScrubSummary()
    scrub.manual_scrub([sample], summary, recursive=False, preview=True, show_tags_mode="both")

    leftover = list(tmp_path.glob("*.scrubbed.jpg"))
    assert leftover == []


def test_manual_dry_run_with_after_tags_remains_planned(tmp_path: Path) -> None:
    """A dry run never executes the scrub pipeline merely to show after-tags.

    Args:
        tmp_path: Isolated real filesystem directory.

    Returns:
        None.
    """
    source = tmp_path / "invalid.jpg"
    original_bytes = b"not-a-jpeg-and-must-not-be-read-by-the-pipeline"
    source.write_bytes(original_bytes)
    summary = scrub.ScrubSummary()

    scrub.manual_scrub(
        [source],
        summary=summary,
        recursive=False,
        dry_run=True,
        show_tags_mode="after",
    )

    summary.report.validate()
    assert summary.report.planned == 1
    assert summary.report.previewed == 0
    assert summary.report.failed == 0
    assert summary.report.published == 0
    assert source.read_bytes() == original_bytes


def test_manual_preview_success_records_previewed_without_publication(
    tmp_path: Path,
) -> None:
    """An audited disposable scrub records PREVIEWED and preserves its source.

    Args:
        tmp_path: Isolated real filesystem directory.

    Returns:
        None.
    """
    source = tmp_path / "photo.jpg"
    shutil.copy(SAMPLE_IMG, source)
    original_bytes = source.read_bytes()
    summary = scrub.ScrubSummary()

    scrub.manual_scrub(
        [source],
        summary=summary,
        recursive=False,
        preview=True,
    )

    summary.report.validate()
    assert summary.report.previewed == 1
    assert summary.report.planned == 0
    assert summary.report.failed == 0
    assert summary.report.published == 0
    assert source.read_bytes() == original_bytes
    assert list(tmp_path.iterdir()) == [source]


def test_manual_preview_failure_records_failed_without_publication(
    tmp_path: Path,
) -> None:
    """A disposable scrub failure records FAILED and preserves its source.

    Args:
        tmp_path: Isolated real filesystem directory.

    Returns:
        None.
    """
    source = tmp_path / "invalid.jpg"
    original_bytes = b"not-a-jpeg"
    source.write_bytes(original_bytes)
    summary = scrub.ScrubSummary()

    scrub.manual_scrub(
        [source],
        summary=summary,
        recursive=False,
        preview=True,
    )

    summary.report.validate()
    assert summary.report.previewed == 0
    assert summary.report.failed == 1
    assert summary.report.published == 0
    assert source.read_bytes() == original_bytes
    assert list(tmp_path.iterdir()) == [source]


def test_manual_preview_processes_all_jpegs_in_scope(tmp_path: Path) -> None:
    """Preview processes every eligible JPEG and records unsupported inputs.

    Args:
        tmp_path: Isolated real filesystem directory.

    Returns:
        None.
    """
    first = tmp_path / "first.jpg"
    unsupported = tmp_path / "diagram.png"
    second = tmp_path / "second.jpeg"
    shutil.copy(SAMPLE_IMG, first)
    unsupported.write_bytes(b"unsupported")
    shutil.copy(SAMPLE_IMG, second)
    original_bytes = {first: first.read_bytes(), second: second.read_bytes()}
    summary = scrub.ScrubSummary()

    scrub.manual_scrub(
        [first, unsupported, second],
        summary=summary,
        recursive=False,
        preview=True,
    )

    summary.report.validate()
    assert summary.report.examined == 3
    assert summary.report.previewed == 2
    assert summary.report.unsupported == 1
    assert summary.report.published == 0
    assert {first: first.read_bytes(), second: second.read_bytes()} == original_bytes


def test_manual_preview_max_files_limits_jpeg_attempts(tmp_path: Path) -> None:
    """Preview honors max-files without charging unsupported inputs.

    Args:
        tmp_path: Isolated real filesystem directory.

    Returns:
        None.
    """
    unsupported = tmp_path / "diagram.png"
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpeg"
    unsupported.write_bytes(b"unsupported")
    shutil.copy(SAMPLE_IMG, first)
    shutil.copy(SAMPLE_IMG, second)
    original_bytes = {first: first.read_bytes(), second: second.read_bytes()}
    summary = scrub.ScrubSummary()

    scrub.manual_scrub(
        [unsupported, first, second],
        summary=summary,
        recursive=False,
        preview=True,
        max_files=1,
    )

    summary.report.validate()
    assert summary.report.examined == 2
    assert summary.report.previewed == 1
    assert summary.report.unsupported == 1
    assert summary.report.published == 0
    assert {first: first.read_bytes(), second: second.read_bytes()} == original_bytes


def test_manual_preview_negative_limit_raises_value_error(tmp_path: Path) -> None:
    """Preview rejects an invalid limit before running the scrub pipeline.

    Args:
        tmp_path: Isolated real filesystem directory.

    Returns:
        None.
    """
    source = tmp_path / "photo.jpg"
    shutil.copy(SAMPLE_IMG, source)

    with pytest.raises(ValueError, match="max_files"):
        scrub.manual_scrub(
            [source],
            summary=scrub.ScrubSummary(),
            recursive=False,
            preview=True,
            max_files=-1,
        )


@pytest.mark.integration
def test_default_mode_preview_processes_scope_without_creating_output(
    tmp_path: Path,
) -> None:
    """Standalone preview uses default-mode scope but publishes no files.

    Args:
        tmp_path: Isolated directory mounted into the container.

    Returns:
        None.
    """
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpeg"
    unsupported = tmp_path / "diagram.png"
    planned_output = tmp_path / "planned"
    shutil.copy(SAMPLE_IMG, first)
    shutil.copy(SAMPLE_IMG, second)
    unsupported.write_bytes(b"unsupported")
    original_bytes = {first: first.read_bytes(), second: second.read_bytes()}

    result = run_container_manual(
        [
            "--preview",
            "--show-tags",
            "after",
            "--output",
            "/photos/planned",
            "/photos/first.jpg",
            "/photos/diagram.png",
            "/photos/second.jpeg",
        ],
        mounts=["-v", f"{tmp_path}:/photos"],
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("Preview complete for ") == 2
    assert "unsupported=1" in result.stdout
    assert {first: first.read_bytes(), second: second.read_bytes()} == original_bytes
    assert not planned_output.exists()


@pytest.mark.regression
def test_manual_mode_default_dir(tmp_path):
    # Create multiple JPEGs in root and subdir
    (tmp_path / "one.jpg").write_bytes(SAMPLE_BYTES)
    subdir = tmp_path / "nested"
    subdir.mkdir()
    (subdir / "two.jpg").write_bytes(SAMPLE_BYTES)

    # Test without -r (should only scrub top-level image)
    result = run_container_manual(["--clean-inline", "--log-level", "debug"], mounts=["-v", f"{tmp_path}:/photos"])
    assert "one.jpg" in result.stdout
    assert "two.jpg" not in result.stdout

    # Test with -r (should scrub both)
    result = run_container_manual(["--clean-inline", "--log-level", "debug", "-r"], mounts=["-v", f"{tmp_path}:/photos"])
    assert "one.jpg" in result.stdout
    assert "two.jpg" in result.stdout
