# SPDX-License-Identifier: GPL-3.0-or-later
"""Physical subprocess tests for the PhotoPrism integration wrapper."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_scrubexif_photoprism.sh"


def _summary_line(**overrides: int | str) -> str:
    """Build a complete schema-2 summary for wrapper tests.

    Args:
        **overrides: Field values to replace in the empty successful summary.

    Returns:
        One machine-readable schema-2 summary line.

    Raises:
        ValueError: If an unknown field is supplied.
    """
    fields: dict[str, int | str] = {
        "schema": 2,
        "total": 0,
        "jpeg_total": 0,
        "scrubbed": 0,
        "duplicates_handled": 0,
        "unsupported": 0,
        "planned": 0,
        "previewed": 0,
        "skipped": 0,
        "failed": 0,
        "errors": 0,
        "verified_duplicates": 0,
        "duplicates_deleted": 0,
        "duplicates_moved": 0,
        "duplicates_unchanged": 0,
        "duplicates_missing": 0,
        "published": 0,
        "run_errors": 0,
        "interrupted": 0,
        "examined": 0,
        "duration": "0.100",
    }
    unknown = set(overrides) - set(fields)
    if unknown:
        raise ValueError(f"unknown summary fields: {sorted(unknown)}")
    fields.update(overrides)
    values = " ".join(f"{name}={value}" for name, value in fields.items())
    return f"SCRUBEXIF_SUMMARY {values}"


def _run_wrapper(
    tmp_path: Path,
    summary_output: str,
    scrub_exit: int,
    *,
    scrubexif_image: str | None = None,
    create_errors_dir: bool = True,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the real wrapper against an isolated fake Docker executable.

    Args:
        tmp_path: Isolated directory for configuration and captured calls.
        summary_output: Output returned by the fake scrubexif container.
        scrub_exit: Exit status returned for the fake Docker run.
        scrubexif_image: Optional explicit container image override.
        create_errors_dir: Whether to create the configured duplicate archive.

    Returns:
        Completed wrapper process and captured Docker invocations.

    Raises:
        OSError: If fixture files cannot be created or read.
        ValueError: If scrub_exit is outside the shell exit-code range.
    """
    if scrub_exit < 0 or scrub_exit > 255:
        raise ValueError("scrub_exit must be between zero and 255")
    if scrubexif_image is not None and not scrubexif_image:
        raise ValueError("scrubexif_image must not be empty")

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    processed_dir = tmp_path / "processed"
    errors_dir = tmp_path / "errors"
    binary_dir = tmp_path / "bin"
    for directory in (input_dir, output_dir, processed_dir, binary_dir):
        directory.mkdir()
    if create_errors_dir:
        errors_dir.mkdir()

    capture_path = tmp_path / "docker-calls.txt"
    docker_path = binary_dir / "docker"
    docker_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s\\n' \"$*\" >> \"${DOCKER_CAPTURE}\"\n"
        "if [[ \"${1:-}\" == \"run\" ]]; then\n"
        "    printf '%s\\n' \"${DOCKER_SUMMARY}\"\n"
        "    exit \"${DOCKER_EXIT}\"\n"
        "fi\n"
        "if [[ \"${1:-}\" == \"exec\" ]]; then exit 0; fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    docker_path.chmod(0o755)

    environment = os.environ.copy()
    environment.pop("SCRUBEXIF_IMAGE", None)
    environment.pop("SCRUBEXIF_VERSION", None)
    environment.update(
        {
            "PATH": f"{binary_dir}:{environment['PATH']}",
            "DOCKER_CAPTURE": str(capture_path),
            "DOCKER_SUMMARY": summary_output,
            "DOCKER_EXIT": str(scrub_exit),
            "INPUT_DIR": str(input_dir),
            "OUTPUT_DIR": str(output_dir),
            "PROCESSED_DIR": str(processed_dir),
            "ERRORS_DIR": str(errors_dir),
            "LOCKFILE": str(tmp_path / "scrubexif.lock"),
        }
    )
    if scrubexif_image is not None:
        environment["SCRUBEXIF_IMAGE"] = scrubexif_image
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
    )
    calls = (
        capture_path.read_text(encoding="utf-8").splitlines()
        if capture_path.exists()
        else []
    )
    return result, calls


@pytest.mark.parametrize(
    ("image_override", "expected_image"),
    [
        (None, "per2jensen/scrubexif:0.8.0"),
        ("registry.example/scrubexif:test", "registry.example/scrubexif:test"),
    ],
    ids=["default-0.8.0", "explicit-override"],
)
def test_photoprism_wrapper_uses_schema_two_compatible_image(
    tmp_path: Path,
    image_override: str | None,
    expected_image: str,
) -> None:
    """Use the 0.8.0 default while preserving explicit image selection.

    Args:
        tmp_path: Isolated test directory.
        image_override: Optional image supplied through the environment.
        expected_image: Image expected in the real wrapper's Docker arguments.

    Returns:
        None.
    """
    result, calls = _run_wrapper(
        tmp_path,
        _summary_line(),
        scrub_exit=0,
        scrubexif_image=image_override,
    )

    assert result.returncode == 0, result.stderr
    run_calls = [call for call in calls if call.startswith("run ")]
    assert len(run_calls) == 1
    assert f" {expected_image} --from-input" in run_calls[0]


def test_photoprism_wrapper_published_output_triggers_index(tmp_path: Path) -> None:
    """Index when schema 2 reports a newly published audited output.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        None.
    """
    summary = _summary_line(
        total=1,
        jpeg_total=1,
        scrubbed=1,
        published=1,
        examined=1,
    )

    result, calls = _run_wrapper(tmp_path, summary, scrub_exit=0)

    assert result.returncode == 0, result.stderr
    assert sum(call.startswith("exec ") for call in calls) == 1
    assert "1 newly published files" in result.stdout


def test_photoprism_wrapper_mounts_duplicate_archive(tmp_path: Path) -> None:
    """Mount the configured duplicate archive at the required container path.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        None.
    """
    result, calls = _run_wrapper(tmp_path, _summary_line(), scrub_exit=0)

    assert result.returncode == 0, result.stderr
    run_calls = [call for call in calls if call.startswith("run ")]
    assert len(run_calls) == 1
    assert f"-v {tmp_path / 'errors'}:/photos/errors" in run_calls[0]


def test_photoprism_wrapper_missing_duplicate_archive_fails_before_docker(
    tmp_path: Path,
) -> None:
    """Reject a missing duplicate archive before starting the container.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        None.
    """
    result, calls = _run_wrapper(
        tmp_path,
        _summary_line(),
        scrub_exit=0,
        create_errors_dir=False,
    )

    assert result.returncode == 1
    assert calls == []
    assert f"Directory does not exist: {tmp_path / 'errors'}" in result.stderr


def test_photoprism_wrapper_no_publication_skips_index(tmp_path: Path) -> None:
    """Skip indexing when a valid schema-2 run published no output.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        None.
    """
    result, calls = _run_wrapper(tmp_path, _summary_line(), scrub_exit=0)

    assert result.returncode == 0, result.stderr
    assert not any(call.startswith("exec ") for call in calls)
    assert "published=0" in result.stdout


def test_photoprism_wrapper_partial_failure_indexes_then_preserves_status(
    tmp_path: Path,
) -> None:
    """Index published output from a mixed run and retain its failure status.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        None.
    """
    summary = _summary_line(
        total=1,
        jpeg_total=1,
        failed=1,
        errors=1,
        published=1,
        examined=1,
    )

    result, calls = _run_wrapper(tmp_path, summary, scrub_exit=1)

    assert result.returncode == 1
    assert sum(call.startswith("exec ") for call in calls) == 1
    assert "also encountered 1 errors" in result.stdout


@pytest.mark.parametrize(
    ("summary_output", "scrub_exit"),
    [
        (_summary_line(errors=1, run_errors=1), 0),
        (_summary_line(), 1),
    ],
    ids=["success-with-errors", "failure-without-errors"],
)
def test_photoprism_wrapper_inconsistent_exit_status_fails_closed(
    tmp_path: Path,
    summary_output: str,
    scrub_exit: int,
) -> None:
    """Reject contradictory process status and schema-2 error facts.

    Args:
        tmp_path: Isolated test directory.
        summary_output: Internally valid schema-2 summary under test.
        scrub_exit: Inconsistent fake Docker status.

    Returns:
        None.
    """
    result, calls = _run_wrapper(tmp_path, summary_output, scrub_exit)

    assert result.returncode == 1
    assert not any(call.startswith("exec ") for call in calls)
    assert "Inconsistent scrubexif exit status" in result.stderr


@pytest.mark.parametrize(
    "summary_output",
    [
        "SCRUBEXIF_SUMMARY total=0 scrubbed=0 errors=0 duration=0.100",
        _summary_line(schema=3),
        _summary_line().replace(" published=0", ""),
        f"{_summary_line()} errors=0",
        f"{_summary_line()} future_counter=0",
        _summary_line().replace("errors=0", "errors=invalid"),
        _summary_line(total=1),
        _summary_line(total=1, jpeg_total=1),
        _summary_line(unsupported=1),
        _summary_line(verified_duplicates=1),
        _summary_line(errors=1),
        _summary_line(
            total=1,
            jpeg_total=1,
            duplicates_handled=1,
            examined=1,
        ),
        _summary_line(published=1),
        _summary_line(interrupted=2),
        _summary_line(interrupted=1),
        _summary_line(duration="invalid"),
        f"{_summary_line()}\n{_summary_line()}",
    ],
    ids=[
        "legacy-schema",
        "unknown-schema",
        "missing-field",
        "duplicate-field",
        "unexpected-field",
        "non-numeric-field",
        "total-alias-mismatch",
        "jpeg-terminal-mismatch",
        "examined-mismatch",
        "duplicate-fact-mismatch",
        "error-mismatch",
        "handled-duplicate-bound",
        "published-bound",
        "invalid-interrupted-value",
        "interrupted-without-run-error",
        "invalid-duration",
        "multiple-lines",
    ],
)
def test_photoprism_wrapper_invalid_summary_fails_closed(
    tmp_path: Path,
    summary_output: str,
) -> None:
    """Reject unsupported, malformed, or ambiguous machine summaries.

    Args:
        tmp_path: Isolated test directory.
        summary_output: Invalid scrubexif output under test.

    Returns:
        None.
    """
    result, calls = _run_wrapper(tmp_path, summary_output, scrub_exit=0)

    assert result.returncode == 1
    assert not any(call.startswith("exec ") for call in calls)
