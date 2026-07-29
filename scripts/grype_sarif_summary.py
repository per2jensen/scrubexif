#!/usr/bin/env python3
"""Summarize Grype SARIF severity counts for build metadata."""

from __future__ import annotations

import collections
import gzip
import json
import logging
import pathlib
import re
import sys
from typing import Any

LOGGER = logging.getLogger(__name__)
KNOWN_SEVERITIES = (
    "critical",
    "high",
    "medium",
    "low",
    "negligible",
    "warning",
    "note",
    "info",
    "unknown",
)
VULNERABILITY_SEVERITY_PATTERN = re.compile(
    r"\b(critical|high|medium|low|negligible|unknown) vulnerability\b",
    re.IGNORECASE,
)
HELP_SEVERITY_PATTERN = re.compile(
    r"^Severity:\s*(critical|high|medium|low|negligible|unknown)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _read_sarif(sarif_path: pathlib.Path) -> dict[str, Any]:
    """Read an uncompressed or gzip-compressed SARIF document.

    Args:
        sarif_path: Existing SARIF or SARIF.gz path.

    Returns:
        Parsed SARIF root object.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If the file is not valid UTF-8 JSON or has no object root.
    """
    if not isinstance(sarif_path, pathlib.Path):
        raise ValueError("sarif_path must be a pathlib.Path")
    if not sarif_path.is_file():
        raise ValueError(f"SARIF file does not exist: {sarif_path}")

    try:
        if sarif_path.suffix == ".gz":
            with gzip.open(sarif_path, "rt", encoding="utf-8") as sarif_file:
                raw_data: Any = json.load(sarif_file)
        else:
            raw_data = json.loads(sarif_path.read_text(encoding="utf-8"))
    except (gzip.BadGzipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        LOGGER.error("Invalid SARIF document %s: %s", sarif_path, exc)
        raise ValueError(f"Invalid SARIF document: {sarif_path}") from exc
    except OSError as exc:
        LOGGER.error("Unable to read SARIF document %s: %s", sarif_path, exc)
        raise

    if not isinstance(raw_data, dict):
        raise ValueError(f"SARIF root must be an object: {sarif_path}")
    return raw_data


def _severity_from_text(value: Any) -> str | None:
    """Extract a Grype vulnerability severity from text.

    Args:
        value: Candidate textual value.

    Returns:
        Normalized severity, or None when no Grype severity is present.
    """
    if not isinstance(value, str) or not value:
        return None

    vulnerability_match = VULNERABILITY_SEVERITY_PATTERN.search(value)
    if vulnerability_match is not None:
        return vulnerability_match.group(1).lower()

    help_match = HELP_SEVERITY_PATTERN.search(value)
    if help_match is not None:
        return help_match.group(1).lower()
    return None


def _rule_severities(run: dict[str, Any]) -> dict[str, str]:
    """Build a rule-ID to vulnerability-severity mapping.

    Args:
        run: SARIF run object.

    Returns:
        Mapping for rules whose Grype severity can be determined.
    """
    if not isinstance(run, dict):
        raise ValueError("run must be a dictionary")

    tool = run.get("tool")
    driver = tool.get("driver") if isinstance(tool, dict) else None
    rules = driver.get("rules") if isinstance(driver, dict) else None
    if not isinstance(rules, list):
        return {}

    severities: dict[str, str] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            continue

        short_description = rule.get("shortDescription")
        short_text = (
            short_description.get("text")
            if isinstance(short_description, dict)
            else None
        )
        help_data = rule.get("help")
        help_text = help_data.get("text") if isinstance(help_data, dict) else None
        severity = _severity_from_text(short_text) or _severity_from_text(help_text)
        if severity is not None:
            severities[rule_id] = severity
    return severities


def _result_severity(
    result: dict[str, Any],
    rule_severities: dict[str, str],
) -> str:
    """Resolve the vulnerability severity for one SARIF result.

    Args:
        result: SARIF result object.
        rule_severities: Severity lookup indexed by SARIF rule ID.

    Returns:
        Normalized Grype severity or SARIF fallback level.
    """
    if not isinstance(result, dict):
        raise ValueError("result must be a dictionary")
    if not isinstance(rule_severities, dict):
        raise ValueError("rule_severities must be a dictionary")

    properties = result.get("properties")
    property_severity = (
        properties.get("severity")
        if isinstance(properties, dict)
        else None
    )
    if isinstance(property_severity, str) and property_severity:
        return property_severity.lower()

    rule_id = result.get("ruleId")
    if isinstance(rule_id, str) and rule_id in rule_severities:
        return rule_severities[rule_id]

    message = result.get("message")
    message_text = message.get("text") if isinstance(message, dict) else None
    message_severity = _severity_from_text(message_text)
    if message_severity is not None:
        return message_severity

    level = result.get("level")
    if isinstance(level, str) and level:
        return level.lower()
    return "unknown"


def summarize(path: str) -> dict[str, Any] | None:
    """Summarize vulnerability severities from SARIF or SARIF.gz.

    Args:
        path: Path to a Grype SARIF document. An empty or missing path is skipped.

    Returns:
        Filename, total result count, and severity counts; otherwise None when the
        requested path does not exist.

    Raises:
        OSError: If an existing file cannot be read.
        ValueError: If path has the wrong type or the SARIF content is invalid.
    """
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    if not path:
        return None

    sarif_path = pathlib.Path(path)
    if not sarif_path.is_file():
        return None

    data = _read_sarif(sarif_path)
    counts: collections.Counter[str] = collections.Counter()
    runs = data.get("runs")
    if not isinstance(runs, list):
        runs = []

    for run in runs:
        if not isinstance(run, dict):
            continue
        severities = _rule_severities(run)
        results = run.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                counts["unknown"] += 1
                continue
            counts[_result_severity(result, severities)] += 1

    summary_counts = {
        severity: counts.get(severity, 0)
        for severity in KNOWN_SEVERITIES
    }
    for severity, count in counts.items():
        if severity not in summary_counts:
            summary_counts[severity] = count

    return {
        "file": sarif_path.name,
        "total": sum(counts.values()),
        "counts": summary_counts,
    }


def main() -> int:
    """Write a compact JSON summary for a command-line SARIF path.

    Returns:
        Process exit status: zero on success, one for invalid input.
    """
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        summary = summarize(path)
    except (OSError, ValueError) as exc:
        LOGGER.error("Unable to summarize Grype SARIF: %s", exc)
        return 1

    json.dump(summary, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
