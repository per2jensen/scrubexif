#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Fetch total pull count for the scrubexif image from Docker Hub and
append/update a small history JSON under clonepulse/docker_pulls.json.

Uses the public Docker Hub v2 API, no auth needed for a public repo.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.request import urlopen


NAMESPACE = "per2jensen"
REPOSITORY = "scrubexif"
API_URL = f"https://hub.docker.com/v2/repositories/{NAMESPACE}/{REPOSITORY}/"


def fetch_pull_count() -> int:
    with urlopen(API_URL) as resp:
        data = json.load(resp)
    pulls = data.get("pull_count")
    if pulls is None:
        raise RuntimeError(f"No pull_count in Docker Hub response: {data}")
    return int(pulls)


def main() -> None:
    pulls = fetch_pull_count()
    today = date.today().isoformat()

    out_path = Path(__file__).with_name("docker_pulls.json")

    if out_path.exists():
        blob = json.loads(out_path.read_text(encoding="utf-8"))
    else:
        blob = {}

    history = blob.setdefault("history", [])
    # Update or append today’s entry
    for entry in history:
        if entry.get("date") == today:
            entry["pulls"] = pulls
            break
    else:
        history.append({"date": today, "pulls": pulls})

    blob["latest"] = {"date": today, "pulls": pulls}

    out_path.write_text(
        json.dumps(blob, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{today}: Docker Hub pulls = {pulls}")


if __name__ == "__main__":
    main()

