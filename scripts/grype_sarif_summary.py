#!/usr/bin/env python3
"""
Summarize a Grype SARIF report into severity counts for build metadata.
"""

from __future__ import annotations

import collections
import json
import pathlib
import sys
from typing import Any, Dict, Optional


def summarize(path: str) -> Optional[Dict[str, Any]]:
    sarif_path = pathlib.Path(path)
    if not sarif_path.is_file():
        return None

    try:
        data = json.loads(sarif_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    counts: collections.Counter[str] = collections.Counter()

    for run in data.get("runs") or []:
        for result in run.get("results") or []:
            properties = result.get("properties") or {}
            severity = properties.get("severity") or result.get("level") or "unknown"
            if not isinstance(severity, str):
                severity = "unknown"
            counts[severity.lower()] += 1

    order = ["critical", "high", "medium", "low", "negligible", "warning", "note", "info", "unknown"]
    summary_counts = {key: counts.get(key, 0) for key in order}
    for key, value in counts.items():
        if key not in summary_counts:
            summary_counts[key] = value

    return {
        "file": sarif_path.name,
        "total": sum(counts.values()),
        "counts": summary_counts,
    }


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    summary = summarize(path)
    json.dump(summary, sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main()
