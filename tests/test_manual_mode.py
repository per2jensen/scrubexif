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
