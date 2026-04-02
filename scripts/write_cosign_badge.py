#!/usr/bin/env python3
"""
Write doc/cosign_badge.json for the shields.io endpoint badge in README.md.

Usage:
    python3 scripts/write_cosign_badge.py [--failed]

Without --failed: badge shows "cosign / ok" in bright pink.
With    --failed: badge shows "cosign / failed" in dull gray.

The badge JSON has no 'url' field — the click target is controlled by the
<a href="..."> wrapper in README.md, which points to the verification docs.
"""

from __future__ import annotations

import argparse
import json
import pathlib


BADGE_PATH = pathlib.Path("doc/cosign_badge.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write cosign shields.io badge JSON.")
    parser.add_argument(
        "--failed",
        action="store_true",
        help="Write a failure badge instead of a success badge.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.failed:
        badge = {
            "schemaVersion": 1,
            "label": "cosign",
            "message": "failed",
            "color": "9e9e9e",
        }
    else:
        badge = {
            "schemaVersion": 1,
            "label": "cosign",
            "message": "ok",
            "color": "ff69b4",
        }

    if BADGE_PATH.exists():
        try:
            existing = json.loads(BADGE_PATH.read_text(encoding="utf-8"))
            if existing == badge:
                print(f"ℹ️  Badge already correct ('{badge['message']}') — skipping write")
                return
        except json.JSONDecodeError:
            pass  # Corrupted file — overwrite it

    BADGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BADGE_PATH.write_text(json.dumps(badge, indent=2) + "\n", encoding="utf-8")
    print(f"{'⚠️' if args.failed else '✅'} Wrote {BADGE_PATH}: {badge['message']}")


if __name__ == "__main__":
    main()
