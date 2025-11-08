#!/usr/bin/env python3
"""
Append a build entry to doc/build-history.json, optionally embedding Grype results.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Dict, List

from grype_sarif_summary import summarize as summarize_grype


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update build history metadata.")
    parser.add_argument("--log", required=True, help="Path to build-history.json")
    parser.add_argument("--build-number", type=int, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--git-rev", required=True)
    parser.add_argument("--created", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--grype-sarif", default="", help="Path to Grype SARIF report (optional)")
    return parser.parse_args()


def load_history(path: pathlib.Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, list):
        raise SystemExit(f"Expected list in {path}, found {type(data).__name__}")
    return data


def main() -> None:
    args = parse_args()
    log_path = pathlib.Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    history = load_history(log_path)

    entry: Dict[str, Any] = {
        "build_number": args.build_number,
        "tag": args.version,
        "base_image": args.base,
        "git_revision": args.git_rev,
        "created": args.created,
        "dockerhub_tag_url": args.url,
        "digest": args.digest,
        "image_id": args.image_id,
    }

    if args.grype_sarif:
        summary = summarize_grype(args.grype_sarif)
        if summary:
            entry["grype_scan"] = summary

    history.append(entry)
    log_path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
