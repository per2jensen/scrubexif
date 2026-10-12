# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop-notification classification tests for the demo wrapper."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scrubexif-demo.sh"


def _summary_line(**overrides: int | str) -> str:
    """Build a complete valid schema-2 summary with selected overrides.

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


def _write_fake_notifier(binary_dir: Path, capture_path: Path) -> None:
    """Create a notify-send executable that records its arguments.

    Args:
        binary_dir: Directory that will be prepended to PATH.
        capture_path: File that receives one argument per line.

    Returns:
        None.

    Raises:
        OSError: If the executable cannot be created.
    """
    notifier_path = binary_dir / "notify-send"
    notifier_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s\\n' \"$@\" > \"${NOTIFY_CAPTURE}\"\n",
        encoding="utf-8",
    )
    notifier_path.chmod(0o755)


def _run_notification(
    tmp_path: Path,
    counts: tuple[int, int, int, int, int, int, int],
) -> list[str]:
    """Call the real Bash notification function with a fake notifier binary.

    Args:
        tmp_path: Isolated directory for the executable and captured arguments.
        counts: Total, scrubbed, skipped, errors, unsupported, examined, and
            duplicate-handled counts.

    Returns:
        One captured notify-send argument per list item.

    Raises:
        subprocess.CalledProcessError: If the Bash function fails.
        OSError: If test files cannot be created or read.
    """
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    capture_path = tmp_path / "notification.txt"
    _write_fake_notifier(binary_dir, capture_path)

    environment = os.environ.copy()
    environment["PATH"] = f"{binary_dir}:{environment['PATH']}"
    environment["NOTIFY_CAPTURE"] = str(capture_path)
    command = [
        "bash",
        "-c",
        'source "$1"; shift; send_summary_notification "$@"',
        "bash",
        str(SCRIPT),
        *(str(value) for value in counts),
        "/output",
    ]
    subprocess.run(command, check=True, env=environment, capture_output=True, text=True)
    return capture_path.read_text(encoding="utf-8").splitlines()


def _run_demo_wrapper(
    tmp_path: Path,
    summary_line: str,
    docker_exit: int,
    scrubexif_image: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    """Run the demo wrapper with real subprocesses and controlled adapters.

    Args:
        tmp_path: Isolated directory for inputs, logs, and fake executables.
        summary_line: Current invocation summary emitted by fake Docker.
        docker_exit: Exit status returned by fake Docker.
        scrubexif_image: Optional image override supplied to the wrapper.

    Returns:
        Completed wrapper process, notification arguments, and Docker arguments.

    Raises:
        OSError: If test files cannot be created or read.
        ValueError: If docker_exit is outside the shell exit-code range.
    """
    if docker_exit < 0 or docker_exit > 255:
        raise ValueError("docker_exit must be between zero and 255")

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    binary_dir = tmp_path / "bin"
    input_dir.mkdir()
    output_dir.mkdir()
    binary_dir.mkdir()

    capture_path = tmp_path / "notification.txt"
    docker_arguments_path = tmp_path / "docker-arguments.txt"
    _write_fake_notifier(binary_dir, capture_path)
    docker_path = binary_dir / "docker"
    docker_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [[ \"${1:-}\" == \"info\" ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == \"run\" ]]; then\n"
        "    printf '%s\\n' \"$@\" > \"${DOCKER_ARGUMENTS_CAPTURE}\"\n"
        "    printf '%s\\n' \"${DOCKER_SUMMARY}\"\n"
        "    exit \"${DOCKER_EXIT}\"\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    docker_path.chmod(0o755)

    log_path = tmp_path / "scrubexif.log"
    log_path.write_text(
        "SCRUBEXIF_SUMMARY total=9 scrubbed=9 skipped=0 errors=0 "
        "duplicates_deleted=0 duplicates_moved=0 unsupported=0 examined=9 "
        "duration=1.000\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.pop("SCRUBEXIF_IMAGE", None)
    environment.update(
        {
            "PATH": f"{binary_dir}:{environment['PATH']}",
            "NOTIFY_CAPTURE": str(capture_path),
            "DOCKER_SUMMARY": summary_line,
            "DOCKER_EXIT": str(docker_exit),
            "DOCKER_ARGUMENTS_CAPTURE": str(docker_arguments_path),
            "RUN_AS_UID": "1000",
            "RUN_AS_GID": "1000",
            "SCRUBEXIF_LOGFILE": str(log_path),
        }
    )
    if scrubexif_image is not None:
        environment["SCRUBEXIF_IMAGE"] = scrubexif_image
    result = subprocess.run(
        [str(SCRIPT), str(input_dir), str(output_dir)],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
    )
    arguments = capture_path.read_text(encoding="utf-8").splitlines()
    docker_arguments = docker_arguments_path.read_text(encoding="utf-8").splitlines()
    return result, arguments, docker_arguments


def test_notification_success_with_unsupported_file_stays_green(
    tmp_path: Path,
) -> None:
    """Ten scrubbed JPEGs plus one PNG produce an informative success.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        None.
    """
    arguments = _run_notification(tmp_path, (10, 10, 0, 0, 1, 11, 0))

    assert arguments[:4] == ["-u", "normal", "-i", "emblem-default"]
    assert "✅ Scrubbing complete" in arguments[4]
    assert "Examined 11 files" in arguments[5]
    assert "unsupported: 1" in arguments[5]


def test_notification_unsupported_only_is_neutral(tmp_path: Path) -> None:
    """An unsupported-only run produces a neutral information notification.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        None.
    """
    arguments = _run_notification(tmp_path, (0, 0, 0, 0, 1, 1, 0))

    assert arguments[:4] == ["-u", "low", "-i", "dialog-information"]
    assert arguments[4] == "scrubexif ℹ️"
    assert arguments[5] == "Nothing processed — 1 unsupported file examined"


def test_notification_processing_error_is_red(tmp_path: Path) -> None:
    """A JPEG processing error retains the critical red notification.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        None.
    """
    arguments = _run_notification(tmp_path, (1, 0, 0, 1, 0, 1, 0))

    assert arguments[:4] == ["-u", "critical", "-i", "dialog-error"]
    assert "❌ Completed with errors" in arguments[4]
    assert "errors: 1" in arguments[5]


def test_notification_duplicate_only_is_neutral(tmp_path: Path) -> None:
    """A handled duplicate without new output produces a neutral notification.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        None.
    """
    arguments = _run_notification(tmp_path, (1, 0, 0, 0, 0, 1, 1))

    assert arguments[:4] == ["-u", "low", "-i", "dialog-information"]
    assert arguments[5] == "No new output — 1 duplicate handled; examined: 1"


def test_demo_wrapper_uses_current_summary_not_stale_log(tmp_path: Path) -> None:
    """The wrapper ignores a successful summary left by an earlier run.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        None.
    """
    current_summary = _summary_line(unsupported=1, examined=1)
    result, arguments, _ = _run_demo_wrapper(
        tmp_path, current_summary, docker_exit=0
    )

    assert result.returncode == 0, result.stderr
    assert arguments[:4] == ["-u", "low", "-i", "dialog-information"]
    assert arguments[5] == "Nothing processed — 1 unsupported file examined"


@pytest.mark.parametrize(
    ("scrubexif_image", "expected_image"),
    [
        (None, "per2jensen/scrubexif:0.8.0"),
        ("registry.example/scrubexif:test", "registry.example/scrubexif:test"),
    ],
    ids=["schema-two-default", "explicit-override"],
)
def test_demo_wrapper_selects_configured_image(
    tmp_path: Path,
    scrubexif_image: str | None,
    expected_image: str,
) -> None:
    """Use the schema-2 default image unless the caller supplies an override.

    Args:
        tmp_path: Isolated directory for files and fake executables.
        scrubexif_image: Optional image override supplied to the wrapper.
        expected_image: Exact image expected in the Docker invocation.

    Returns:
        None.
    """
    result, _, docker_arguments = _run_demo_wrapper(
        tmp_path,
        _summary_line(),
        docker_exit=0,
        scrubexif_image=scrubexif_image,
    )

    assert result.returncode == 0, result.stderr
    assert docker_arguments[-3:] == [expected_image, "-o", "/scrubbed"]


def test_demo_wrapper_preserves_failed_docker_status(tmp_path: Path) -> None:
    """An informative error notification does not hide Docker's failure status.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        None.
    """
    current_summary = _summary_line(
        total=1,
        jpeg_total=1,
        failed=1,
        errors=1,
        examined=1,
    )
    result, arguments, _ = _run_demo_wrapper(
        tmp_path, current_summary, docker_exit=1
    )

    assert result.returncode == 1
    assert arguments[:4] == ["-u", "critical", "-i", "dialog-error"]
    assert "errors: 1" in arguments[5]


def test_demo_wrapper_preserves_interrupted_status(tmp_path: Path) -> None:
    """A reconciled interrupted report retains Docker status 130.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        None.
    """
    current_summary = _summary_line(
        errors=1,
        run_errors=1,
        interrupted=1,
    )

    result, arguments, _ = _run_demo_wrapper(
        tmp_path, current_summary, docker_exit=130
    )

    assert result.returncode == 130
    assert arguments[:4] == ["-u", "critical", "-i", "dialog-error"]


@pytest.mark.parametrize(
    ("summary_line", "docker_exit"),
    [
        (_summary_line(errors=1, run_errors=1), 0),
        (_summary_line(), 1),
        (_summary_line(errors=1, run_errors=1), 130),
    ],
    ids=["success-with-errors", "failure-without-errors", "missing-interruption"],
)
def test_demo_wrapper_inconsistent_exit_status_fails_closed(
    tmp_path: Path,
    summary_line: str,
    docker_exit: int,
) -> None:
    """Reject exit status and schema-2 fact combinations that disagree.

    Args:
        tmp_path: Isolated directory for files and fake executables.
        summary_line: Internally valid schema-2 summary under test.
        docker_exit: Inconsistent Docker status paired with the summary.

    Returns:
        None.
    """
    result, arguments, _ = _run_demo_wrapper(tmp_path, summary_line, docker_exit)

    assert result.returncode == 1
    assert arguments[:4] == ["-u", "critical", "-i", "dialog-error"]
    assert "Inconsistent scrubexif" in arguments[5]


@pytest.mark.parametrize(
    "summary_line",
    [
        (
            "SCRUBEXIF_SUMMARY total=0 scrubbed=0 skipped=0 errors=0 "
            "duplicates_deleted=0 duplicates_moved=0 unsupported=0 examined=0 "
            "duration=0.100"
        ),
        _summary_line(schema=3),
        _summary_line().replace(" published=0", ""),
        f"{_summary_line()} published=0",
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
def test_demo_wrapper_invalid_summary_fails_closed(
    tmp_path: Path,
    summary_line: str,
) -> None:
    """Reject malformed or unsupported summaries from a real wrapper process.

    Args:
        tmp_path: Isolated directory for files and fake executables.
        summary_line: Invalid machine summary under test.

    Returns:
        None.
    """
    result, arguments, _ = _run_demo_wrapper(
        tmp_path, summary_line, docker_exit=0
    )

    assert result.returncode == 1
    assert arguments[:4] == ["-u", "critical", "-i", "dialog-error"]
