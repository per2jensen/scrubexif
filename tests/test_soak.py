# tests/test_soak.py
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Long-running soak test (real time). Intentionally slow.
- Writes JPEGs to /photos/input
- Calls container in auto mode repeatedly
- Sleeps between cycles to exercise SCRUBEXIF_STABLE_SECONDS gate
- Verifies steady progress over time

Control via env:
  SOAK_MINUTES (default: 10)
  SOAK_INTERVAL_SEC (default: 30)
  SOAK_STABLE_SECONDS (default: 120)
  SOAK_BATCH (default: 3)

Mark: pytest -m soak
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List

import pytest

from tests._docker import mk_mounts, run_container
from .conftest import create_fake_jpeg

pytestmark = pytest.mark.soak


def _envint(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _stats(out: Path, proc: Path) -> tuple[int, int]:
    return len(list(out.glob("*.jpg"))), len(list(proc.glob("*.jpg")))


def test_real_time_soak(tmp_path: Path):
    soak_minutes = _envint("SOAK_MINUTES", 10)
    interval = _envint("SOAK_INTERVAL_SEC", 30)
    stable = _envint("SOAK_STABLE_SECONDS", 120)
    batch = _envint("SOAK_BATCH", 3)

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    errors_dir = tmp_path / "errors"
    for d in (input_dir, output_dir, processed_dir, errors_dir):
        d.mkdir()

    mounts = mk_mounts(input_dir, output_dir, processed_dir) + ["-v", f"{errors_dir}:/photos/errors"]
    envs = {
        # NOTE: we use a non-zero stability window for a true soak
        "SCRUBEXIF_STABLE_SECONDS": str(stable),
        "SCRUBEXIF_STATE": "/tmp/.scrubexif_state.soak.json",
    }

    deadline = time.time() + (soak_minutes * 60)
    cycle = 0
    produced = 0
    last_out, last_proc = _stats(output_dir, processed_dir)

    while time.time() < deadline:
        cycle += 1

        # Simulate uploads for this cycle
        for i in range(batch):
            produced += 1
            create_fake_jpeg(input_dir / f"soak_{cycle:04d}_{i:02d}.jpg", "red")

        # Invoke the container once per cycle
        cp = run_container(
            mounts=mounts,
            args=["--from-input", "--log-level", "info"],
            capture_output=True,
            envs=envs,
        )
        # Optional: you can print cp.stdout for diagnostics
        assert cp.returncode == 0

        # Wait for next cycle
        time.sleep(interval)

        # Track steady progress (not necessarily every cycle—gate may block)
        cur_out, cur_proc = _stats(output_dir, processed_dir)
        assert cur_out >= last_out, "Output count regressed unexpectedly"
        assert cur_proc >= last_proc, "Processed count regressed unexpectedly"
        last_out, last_proc = cur_out, cur_proc

    # End-state sanity: some subset should have crossed the stability gate
    # We don’t demand 100% because the final batch might still be within the window.
    assert last_out > 0, "No files made it through the stability gate during soak"
    assert last_proc >= last_out, "Processed should be >= output after auto mode cycles"



