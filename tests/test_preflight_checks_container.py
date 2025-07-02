import os
import subprocess
import pytest
from pathlib import Path


IMAGE = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")


def run_scrub_with_volumes(mounts):
    cmd = ["docker", "run", "--rm"] + mounts + [IMAGE, "--from-input"]
    return subprocess.run(cmd, capture_output=True, text=True)

def test_input_is_file(tmp_path):
    bad_input = tmp_path / "input"
    bad_input.write_text("I am not a directory")
    (tmp_path / "output").mkdir()
    (tmp_path / "processed").mkdir()

    result = run_scrub_with_volumes([
        "-v", f"{bad_input}:/photos/input",
        "-v", f"{tmp_path / 'output'}:/photos/output",
        "-v", f"{tmp_path / 'processed'}:/photos/processed",
    ])

    assert result.returncode != 0
    assert "is not a directory" in result.stderr or "not a directory" in result.stdout

def test_processed_dir_not_writable(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    processed = tmp_path / "processed"
    processed.mkdir()
    processed.chmod(0o500)  # not writable

    try:
        result = run_scrub_with_volumes([
            "-v", f"{tmp_path / 'input'}:/photos/input",
            "-v", f"{tmp_path / 'output'}:/photos/output",
            "-v", f"{processed}:/photos/processed",
        ])

        assert result.returncode != 0
        assert "not writable" in result.stderr or "not writable" in result.stdout
    finally:
        processed.chmod(0o700)  # cleanup



def run_container_with_mounts(mounts):
    return subprocess.run(
        ["docker", "run", "--rm"] + mounts + [IMAGE, "--from-input"],
        capture_output=True, text=True
    )


def test_input_directory_does_not_exist(tmp_path):
    bogus_input = tmp_path / "input"
    output = tmp_path / "output"
    processed = tmp_path / "processed"
    output.mkdir()
    processed.mkdir()

    # Don't create bogus_input — test nonexistent dir
    result = run_container_with_mounts([
        "-v", f"{bogus_input}:/photos/input",
        "-v", f"{output}:/photos/output",
        "-v", f"{processed}:/photos/processed"
    ])

    assert result.returncode != 0
    assert "does not exist" in result.stderr.lower() or "does not exist" in result.stdout.lower()


def test_input_is_symlink(tmp_path):
    real_dir = tmp_path / "real"
    symlink_dir = tmp_path / "input"
    output = tmp_path / "output"
    processed = tmp_path / "processed"
    real_dir.mkdir()
    symlink_dir.symlink_to(real_dir)
    output.mkdir()
    processed.mkdir()

    result = run_container_with_mounts([
        "-v", f"{symlink_dir}:/photos/input",
        "-v", f"{output}:/photos/output",
        "-v", f"{processed}:/photos/processed"
    ])

    assert result.returncode != 0
    assert "symlink" in result.stderr.lower() or "symlink" in result.stdout.lower()


def test_unwritable_output_directory(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    input_dir.mkdir()
    output_dir.mkdir()
    processed_dir.mkdir()

    # Make output unwritable (only if running non-root)
    output_dir.chmod(0o555)

    try:
        if os.getuid() != 0:
            result = run_container_with_mounts([
                "-v", f"{input_dir}:/photos/input",
                "-v", f"{output_dir}:/photos/output",
                "-v", f"{processed_dir}:/photos/processed"
            ])
            assert result.returncode != 0
            assert "not writable" in result.stderr.lower() or "not writable" in result.stdout.lower()
    finally:
        output_dir.chmod(0o755)


def test_all_directories_valid(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    input_dir.mkdir()
    output_dir.mkdir()
    processed_dir.mkdir()

    result = run_container_with_mounts([
        "-v", f"{input_dir}:/photos/input",
        "-v", f"{output_dir}:/photos/output",
        "-v", f"{processed_dir}:/photos/processed"
    ])

    # Should exit with 0 because no JPEGs are found, but dirs are valid
    assert result.returncode == 0
    assert "no jpegs found" in result.stdout.lower()
