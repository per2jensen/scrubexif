import shutil
import subprocess
import pytest
from pathlib import Path

@pytest.fixture
def setup_test_env(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    input_dir.mkdir()
    output_dir.mkdir()
    processed_dir.mkdir()

    test_image_src = Path(__file__).parent / "assets" / "sample_with_exif.jpg"
    test_image_dst = input_dir / "test.jpg"
    shutil.copyfile(test_image_src, test_image_dst)

    return {
        "input": input_dir,
        "output": output_dir,
        "processed": processed_dir,
        "original": test_image_dst,
    }

def test_exif_stripped(setup_test_env, monkeypatch):
    from scrub import auto_scrub

    monkeypatch.setattr("scrub.INPUT_DIR", setup_test_env["input"])
    monkeypatch.setattr("scrub.OUTPUT_DIR", setup_test_env["output"])
    monkeypatch.setattr("scrub.PROCESSED_DIR", setup_test_env["processed"])

    auto_scrub(dry_run=False, delete_original=False)

    scrubbed = setup_test_env["output"] / "test.jpg"
    result = subprocess.run(["exiftool", str(scrubbed)], capture_output=True, text=True)
    lines = result.stdout.lower().splitlines()

    assert all("gps" not in line for line in lines)
    assert any("exposure time" in line for line in lines)

def test_gps_data_removed(setup_test_env, monkeypatch):
    from scrub import auto_scrub

    monkeypatch.setattr("scrub.INPUT_DIR", setup_test_env["input"])
    monkeypatch.setattr("scrub.OUTPUT_DIR", setup_test_env["output"])
    monkeypatch.setattr("scrub.PROCESSED_DIR", setup_test_env["processed"])

    auto_scrub(dry_run=False, delete_original=False)

    scrubbed = setup_test_env["output"] / "test.jpg"
    result = subprocess.run(["exiftool", str(scrubbed)], capture_output=True, text=True)
    assert "GPS Position" not in result.stdout

def test_scrubbed_output_exists_and_is_jpeg(setup_test_env, monkeypatch):
    from scrub import auto_scrub

    monkeypatch.setattr("scrub.INPUT_DIR", setup_test_env["input"])
    monkeypatch.setattr("scrub.OUTPUT_DIR", setup_test_env["output"])
    monkeypatch.setattr("scrub.PROCESSED_DIR", setup_test_env["processed"])

    auto_scrub(dry_run=False, delete_original=False)

    scrubbed = setup_test_env["output"] / "test.jpg"
    assert scrubbed.exists()
    with open(scrubbed, "rb") as f:
        assert f.read(2) == b'\xff\xd8'  # JPEG SOI marker

def test_file_moved_to_processed(setup_test_env, monkeypatch):
    from scrub import auto_scrub

    monkeypatch.setattr("scrub.INPUT_DIR", setup_test_env["input"])
    monkeypatch.setattr("scrub.OUTPUT_DIR", setup_test_env["output"])
    monkeypatch.setattr("scrub.PROCESSED_DIR", setup_test_env["processed"])

    auto_scrub(dry_run=False, delete_original=False)

    assert not setup_test_env["original"].exists()
    assert (setup_test_env["processed"] / "test.jpg").exists()

