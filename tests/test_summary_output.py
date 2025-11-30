# tests/test_summary_output.py
# SPDX-License-Identifier: GPL-3.0-or-later
"""Summary/counter regression test for scrubexif in auto mode.

Verifies that:
  * Human-readable summary lines are printed.
  * Machine-readable SCRUBEXIF_SUMMARY line is present.
  * total/scrubbed/skipped/errors/duplicates_* fields reflect reality.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests._docker import mk_mounts, run_container
from .conftest import create_fake_jpeg  # helper provided by the suite


IMAGE_TAG = os.getenv("SCRUBEXIF_IMAGE", "scrubexif:dev")


def _setup_summary_env(tmp_path: Path, count: int = 3):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"

    input_dir.mkdir()
    output_dir.mkdir()
    processed_dir.mkdir()

    # create a small, known number of unique JPEGs
    for idx in range(count):
        create_fake_jpeg(input_dir / f"photo_{idx+1}.jpg")

    return input_dir, output_dir, processed_dir


@pytest.mark.smoke
def test_auto_mode_summary_counters_and_output(tmp_path: Path):
    """End-to-end check that summary counters and output are consistent.

    This would have caught the previous bug where "Successfully scrubbed"
    was always reported as 0 regardless of how many files were processed.
    """
    input_dir, output_dir, processed_dir = _setup_summary_env(tmp_path, count=3)

    mounts = mk_mounts(input_dir, output_dir, processed_dir)
    envs = {
        # ensure all freshly-created files are immediately eligible
        "SCRUBEXIF_STABLE_SECONDS": "0",
    }

    cp = run_container(
        image=IMAGE_TAG,
        mounts=mounts,
        args=["--from-input", "--log-level", "info"],
        capture_output=True,
        envs=envs,
    )

    # Container must succeed
    assert cp.returncode == 0, f"Docker failed:\n{cp.stderr}\n{cp.stdout}"

    stdout = cp.stdout

    # Human-readable summary block
    assert "\n📊 Summary:" in stdout
    assert "Total JPEGs found" in stdout
    assert "Successfully scrubbed" in stdout
    assert "Duration" in stdout

    # Machine-readable one-liner
    m = re.search(r"^SCRUBEXIF_SUMMARY\s+(.*)$", stdout, re.MULTILINE)
    assert m, f"SCRUBEXIF_SUMMARY line missing in output:\n{stdout}"

    # parse key=value pairs from the summary line
    fields: dict[str, str] = {}
    for part in m.group(1).split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key] = value

    # We created exactly three unique JPEGs and no duplicates
    assert fields.get("total") == "3"
    assert fields.get("scrubbed") == "3"
    assert fields.get("skipped") == "0"
    assert fields.get("errors") == "0"
    assert fields.get("duplicates_deleted", "0") == "0"
    assert fields.get("duplicates_moved", "0") == "0"

    # Duration is a non-negative float
    assert "duration" in fields
    duration = float(fields["duration"])
    assert duration >= 0.0

    # Sanity-check that output and processed directories contain the right files
    scrubbed_files = sorted(p.name for p in output_dir.glob("*.jpg"))
    processed_files = sorted(p.name for p in processed_dir.glob("*.jpg"))

    assert scrubbed_files == ["photo_1.jpg", "photo_2.jpg", "photo_3.jpg"]
    assert processed_files == ["photo_1.jpg", "photo_2.jpg", "photo_3.jpg"]
