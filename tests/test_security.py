# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil
import subprocess
from pathlib import Path
import pytest

from scrubexif import scrub
from scrubexif.scrub import ScrubResult

IMAGE = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
SAMPLE_IMAGE = ASSETS_DIR / "sample_with_exif.jpg"


def _run_security_container(args: list[str], mounts: list[str] | None = None) -> subprocess.CompletedProcess:
    """Utility to run the scrubexif container with standard hardening flags."""
    user_flag = ["--user", str(os.getuid())] if os.getuid() != 0 else []
    mounts = mounts or []
    cmd = [
        "docker", "run", "--rm", "--read-only", "--security-opt", "no-new-privileges"
    ] + user_flag + mounts + [IMAGE] + args
    return subprocess.run(cmd, capture_output=True, text=True)


def _skip_if_docker_unavailable(result: subprocess.CompletedProcess) -> None:
    if "permission denied while trying to connect to the Docker daemon socket" in result.stderr:
        pytest.skip("Docker daemon unavailable for test: permission denied")
    if "Cannot connect to the Docker daemon" in result.stderr:
        pytest.skip("Docker daemon unavailable for test")


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
        IMAGE, "--clean-inline", "--dry-run"
    ], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "Running as root" not in result.stdout + result.stderr  # It should silently allow


def test_manual_mode_rejects_relative_escape(tmp_path):
    """Passing ../path should be rejected before it can escape /photos."""
    (tmp_path / "dummy.jpg").write_text("placeholder", encoding="utf-8")

    result = _run_security_container(
        ["--clean-inline", "--dry-run", "../etc/passwd"],
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
        ["--clean-inline", "--dry-run", "/etc/passwd"],
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


def test_output_option_rejects_system_dir_creation(tmp_path, monkeypatch):
    root = tmp_path / "photos"
    root.mkdir()
    monkeypatch.setattr(scrub, "PHOTOS_ROOT", root)

    with pytest.raises(SystemExit):
        scrub.resolve_output_dir(Path("/usr/local/scrubbed"))


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
    assert file_path.read_bytes() == b"jpeg", "Original file content must be unchanged"
    assert summary.scrubbed == 1


def test_resolve_output_dir_rejects_symlink(tmp_path):
    """resolve_output_dir must reject an absolute path that is itself a symlink."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir)

    with pytest.raises(SystemExit):
        scrub.resolve_output_dir(link_dir)


def test_auto_scrub_error_branch_skips_symlink_destination(tmp_path, monkeypatch):
    """When scrub_file returns an error, auto_scrub must not move the original if
    the processed-dir destination is a symlink (line 1138 guard)."""
    root = tmp_path / "photos"
    input_dir = root / "input"
    output_dir = root / "output"
    processed_dir = root / "processed"
    errors_dir = root / "errors"
    for d in (input_dir, output_dir, processed_dir, errors_dir):
        d.mkdir(parents=True, exist_ok=True)

    file_path = input_dir / "one.jpg"
    file_path.write_bytes(b"jpeg")
    # processed-dir destination is a symlink — the error-branch guard must catch this
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

    def fake_scrub_file(path, output_path, **kwargs):
        return ScrubResult(path, output_path, status="error", error_message="simulated failure")

    monkeypatch.setattr(scrub.shutil, "move", fake_move)
    monkeypatch.setattr(scrub, "scrub_file", fake_scrub_file)

    summary = scrub.ScrubSummary()
    scrub.auto_scrub(summary=summary, delete_original=False, stable_seconds=0)

    assert moves == [], "shutil.move must not be called when processed-dir dest is a symlink"
    assert file_path.read_bytes() == b"jpeg", "Original file content must be unchanged"


def test_simple_scrub_skips_symlinks(tmp_path, monkeypatch):
    """simple_scrub must skip any symlinked JPEG even if find_jpegs_in_dir returns one."""
    root = tmp_path / "photos"
    root.mkdir()
    output_dir = root / "output"  # must not exist so simple_scrub can create it

    monkeypatch.setattr(scrub, "PHOTOS_ROOT", root)
    monkeypatch.setattr(scrub, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(scrub, "INPUT_DIR", root / "input")
    monkeypatch.setattr(scrub, "PROCESSED_DIR", root / "processed")
    monkeypatch.setattr(scrub, "ERRORS_DIR", root / "errors")

    real = root / "real.jpg"
    real.write_bytes(b"jpeg")
    link = root / "link.jpg"
    link.symlink_to(real)

    # Bypass find_jpegs_in_dir's own filter so the line-1203 guard is exercised
    monkeypatch.setattr(scrub, "find_jpegs_in_dir", lambda *_a, **_kw: [link])

    scrub_called_with: list[Path] = []

    def fake_scrub_file(path, **kwargs):
        scrub_called_with.append(path)
        return ScrubResult(path, output_dir / path.name, status="scrubbed")

    monkeypatch.setattr(scrub, "scrub_file", fake_scrub_file)

    summary = scrub.ScrubSummary()
    scrub.simple_scrub(summary=summary, recursive=False)

    assert scrub_called_with == [], "scrub_file must not be called for a symlinked JPEG"


def test_manual_scrub_skips_symlink_input(tmp_path, monkeypatch):
    """manual_scrub must skip a symlink passed directly as an input path."""
    real = tmp_path / "real.jpg"
    real.write_bytes(b"jpeg")
    link = tmp_path / "link.jpg"
    link.symlink_to(real)

    scrub_called_with: list[Path] = []

    def fake_scrub_file(path, **kwargs):
        scrub_called_with.append(path)
        return ScrubResult(path, None, status="scrubbed")

    monkeypatch.setattr(scrub, "scrub_file", fake_scrub_file)

    summary = scrub.ScrubSummary()
    scrub.manual_scrub([link], summary=summary, recursive=False)

    assert scrub_called_with == [], "scrub_file must not be called for a symlink input"
    assert summary.scrubbed == 0


@pytest.mark.smoke
def test_auto_mode_scrubs_with_hardening_flags(tmp_path):
    """Full auto pipeline works with read-only + no-new-privileges flags."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"

    for path in (input_dir, output_dir, processed_dir):
        path.mkdir(parents=True, exist_ok=True)

    sample = input_dir / "sample.jpg"
    shutil.copy(SAMPLE_IMAGE, sample)

    user_flag = ["--user", str(os.getuid())] if os.getuid() != 0 else []
    cmd = [
        "docker",
        "run",
        "--rm",
        "--read-only",
        "--security-opt",
        "no-new-privileges",
    ] + user_flag + [
        "-v",
        f"{input_dir}:/photos/input",
        "-v",
        f"{output_dir}:/photos/output",
        "-v",
        f"{processed_dir}:/photos/processed",
        IMAGE,
        "--from-input",
        "--stable-seconds",
        "0",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    _skip_if_docker_unavailable(result)

    assert result.returncode == 0, result.stderr or result.stdout
    assert (output_dir / sample.name).exists(), "Scrubbed file missing in output"
    assert (processed_dir / sample.name).exists(), "Original not moved to processed"
    assert not sample.exists(), "Input file should be moved out of intake"
