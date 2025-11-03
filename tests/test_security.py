# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
import os
from pathlib import Path
import pytest

from scrubexif import scrub
from scrubexif.scrub import ScrubResult

IMAGE = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")


def _run_security_container(args: list[str], mounts: list[str] | None = None) -> subprocess.CompletedProcess:
    """Utility to run the scrubexif container with standard hardening flags."""
    user_flag = ["--user", str(os.getuid())] if os.getuid() != 0 else []
    mounts = mounts or []
    cmd = [
        "docker", "run", "--rm", "--read-only", "--security-opt", "no-new-privileges"
    ] + user_flag + mounts + [IMAGE] + args
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.mark.smoke
def test_root_user_blocked_without_allow_root():
    """Ensure container exits with error when run as root without ALLOW_ROOT=1."""
    result = subprocess.run([
        "docker", "run", "--rm","--read-only", "--security-opt", "no-new-privileges", "--user", "0", IMAGE
    ], capture_output=True, text=True)

    assert result.returncode != 0, "❌ Container should fail when run as root without ALLOW_ROOT"
    assert "not allowed unless ALLOW_ROOT=1" in result.stderr + result.stdout


@pytest.mark.smoke
def test_root_user_allowed_with_env_override():
    """Ensure container runs successfully as root if ALLOW_ROOT=1 is set."""
    result = subprocess.run([
        "docker", "run", "--rm","--read-only", "--security-opt", "no-new-privileges", "--user", "0",
        "-e", "ALLOW_ROOT=1",
        IMAGE, "--dry-run"
    ], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "Running as root" not in result.stdout + result.stderr  # It should silently allow


def test_manual_mode_rejects_relative_escape(tmp_path):
    """Passing ../path should be rejected before it can escape /photos."""
    (tmp_path / "dummy.jpg").write_text("placeholder", encoding="utf-8")

    result = _run_security_container(
        ["--dry-run", "../etc/passwd"],
        mounts=["-v", f"{tmp_path}:/photos"]
    )

    if "permission denied while trying to connect to the Docker daemon socket" in result.stderr:
        pytest.skip("Docker daemon unavailable for test: permission denied")
    if "Cannot connect to the Docker daemon" in result.stderr:
        pytest.skip("Docker daemon unavailable for test")

    assert result.returncode != 0, "Process should exit with failure for escaping relative path"
    assert "escapes allowed root" in result.stderr + result.stdout


def test_manual_mode_rejects_absolute_escape(tmp_path):
    """Passing an absolute path outside /photos should also be rejected."""
    (tmp_path / "dummy.jpg").write_text("placeholder", encoding="utf-8")

    result = _run_security_container(
        ["--dry-run", "/etc/passwd"],
        mounts=["-v", f"{tmp_path}:/photos"]
    )

    if "permission denied while trying to connect to the Docker daemon socket" in result.stderr:
        pytest.skip("Docker daemon unavailable for test: permission denied")
    if "Cannot connect to the Docker daemon" in result.stderr:
        pytest.skip("Docker daemon unavailable for test")

    assert result.returncode != 0, "Process should exit with failure for absolute path outside root"
    assert "escapes allowed root" in result.stderr + result.stdout


def test_find_jpegs_skips_symlinks(tmp_path):
    real = tmp_path / "real.jpg"
    real.write_bytes(b"jpeg")
    link = tmp_path / "link.jpg"
    link.symlink_to(real)

    files = scrub.find_jpegs_in_dir(tmp_path, recursive=False)

    assert real in files
    assert link not in files


def test_resolve_cli_path_rejects_symlink(tmp_path, monkeypatch):
    root = tmp_path / "photos"
    root.mkdir()
    real = root / "real.jpg"
    real.write_bytes(b"jpeg")
    link = root / "link.jpg"
    link.symlink_to(real)

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", root)

    with pytest.raises(SystemExit):
        scrub.resolve_cli_path(Path("link.jpg"))


def test_scrub_file_rejects_symlink_destination(tmp_path, monkeypatch):
    src = tmp_path / "input.jpg"
    dst_dir = tmp_path / "output"
    dst_dir.mkdir()
    src.write_bytes(b"jpeg")
    (dst_dir / src.name).symlink_to(src)

    run_called = False

    def fake_run(*args, **kwargs):
        nonlocal run_called
        run_called = True
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(scrub.subprocess, "run", fake_run)

    result = scrub.scrub_file(src, output_path=dst_dir)

    assert result.status == "error"
    assert "symlink" in (result.error_message or "")
    assert run_called is False, "ExifTool should not run when destination is a symlink"


def test_auto_scrub_delete_original_skips_move(tmp_path, monkeypatch):
    root = tmp_path / "photos"
    input_dir = root / "input"
    output_dir = root / "output"
    processed_dir = root / "processed"
    errors_dir = root / "errors"
    for d in (input_dir, output_dir, processed_dir, errors_dir):
        d.mkdir(parents=True, exist_ok=True)

    file_path = input_dir / "one.jpg"
    file_path.write_bytes(b"jpeg")

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", root)
    monkeypatch.setattr(scrub, "INPUT_DIR", input_dir)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(scrub, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(scrub, "ERRORS_DIR", errors_dir)
    monkeypatch.setattr(scrub, "STATE_FILE", None, raising=False)

    moves: list[tuple[Path, Path]] = []

    def fake_move(src, dst):
        moves.append((Path(src), Path(dst)))

    def fake_scrub_file(path, output_path, delete_original, **kwargs):
        if delete_original and path.exists():
            path.unlink()
        return ScrubResult(path, output_path, status="scrubbed")

    monkeypatch.setattr(scrub.shutil, "move", fake_move)
    monkeypatch.setattr(scrub, "scrub_file", fake_scrub_file)

    summary = scrub.ScrubSummary()
    scrub.auto_scrub(summary=summary, delete_original=True, stable_seconds=0)

    assert moves == []
    assert not file_path.exists()
    assert summary.scrubbed == 1


def test_auto_scrub_skips_symlink_destination(tmp_path, monkeypatch):
    root = tmp_path / "photos"
    input_dir = root / "input"
    output_dir = root / "output"
    processed_dir = root / "processed"
    errors_dir = root / "errors"
    for d in (input_dir, output_dir, processed_dir, errors_dir):
        d.mkdir(parents=True, exist_ok=True)

    file_path = input_dir / "one.jpg"
    file_path.write_bytes(b"jpeg")
    processed_target = processed_dir / file_path.name
    processed_target.symlink_to(file_path)

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", root)
    monkeypatch.setattr(scrub, "INPUT_DIR", input_dir)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(scrub, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(scrub, "ERRORS_DIR", errors_dir)
    monkeypatch.setattr(scrub, "STATE_FILE", None, raising=False)

    moves: list[tuple[Path, Path]] = []

    def fake_move(src, dst):
        moves.append((Path(src), Path(dst)))

    def fake_scrub_file(path, output_path, delete_original, **kwargs):
        return ScrubResult(path, output_path, status="scrubbed")

    monkeypatch.setattr(scrub.shutil, "move", fake_move)
    monkeypatch.setattr(scrub, "scrub_file", fake_scrub_file)

    summary = scrub.ScrubSummary()
    scrub.auto_scrub(summary=summary, delete_original=False, stable_seconds=0)

    assert moves == []
    assert file_path.exists()
    assert summary.scrubbed == 1
