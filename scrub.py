#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Scrub EXIF metadata from JPEG files while retaining selected tags.

🐾 Designed for photographers who want to preserve camera details
    (exposure, lens, ISO, etc.) but remove private or irrelevant data.

The script is case insensitive to file extensions and supports both.

Usage examples:
    docker run -v "$PWD:/photos" scrubexif:dev
    docker run -v "$PWD:/photos" scrubexif:dev image1.jpg image2.jpeg
    docker run -v "$PWD:/photos" scrubexif:dev --dry-run --recursive
"""

import sys
import subprocess
import argparse
from pathlib import Path

EXIFTOOL_CMD_BASE = [
    "exiftool",
    "-P",  # Preserve timestamps
    "-overwrite_original",
    "-all:all=",
    "-tagsfromfile", "@",
    "-ICC_Profile",
    "-exif:ExposureTime",
    "-exif:CreateDate",
    "-exif:SubSecTimeDigitized",
    "-exif:SubSecTime",
    "-exif:SubSecTimeOriginal",
    "-exif:FNumber",
    "-exif:ImageSize",
    "-LensModel",
    "-Nikon:Lens",
    "-Nikon:LensType",
    "-Nikon:LensIdNumber",
    "-exif:Rights",
    "-exif:Title",
    "-exif:FocalLength",
    "-exif:Subject",
    "-exif:ISO",
    "-exif:Orientation",
    "-Exif:Artist",
    "-Exif:CopyRight",
    "-Iptc:By-line",
    "-Xmp-dc:all",
    "-Xmp-iptcExt:Event",
    "-Model",
    "-Exif:Software",
]

VALID_EXTENSIONS = {".jpg", ".jpeg"}


def scrub_file(path: Path, dry_run: bool) -> bool:
    print(f"🧽 Scrubbing: {path}", flush=True)
    cmd = ["exiftool", "-listtags", str(path)] if dry_run else ["stdbuf", "-oL"] + EXIFTOOL_CMD_BASE + [str(path)]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        if "ICC_Profile deleted" not in line:
            print(line.strip(), flush=True)
    return process.wait() == 0


def find_images(paths, recursive):
    for base in paths:
        base_path = Path(base)
        if base_path.is_file():
            yield base_path
        elif base_path.is_dir():
            pattern = "**/*" if recursive else "*"
            for p in base_path.glob(pattern):
                if p.suffix.lower() in VALID_EXTENSIONS and p.is_file():
                    yield p


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", help="Files or directories to process")
    parser.add_argument("--dry-run", action="store_true", help="Only show which tags would be kept")
    parser.add_argument("--recursive", "-r", action="store_true", help="Recurse into subdirectories")
    args = parser.parse_args()

    if not args.paths:
        print("ℹ️ No files provided — scrubbing all JPEGs in /photos", flush=True)
        args.paths = ["/photos"]

    total = scrubbed = skipped = 0

    for path in find_images(args.paths, args.recursive):
        total += 1
        try:
            if scrub_file(path, dry_run=args.dry_run):
                scrubbed += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"❌ Error: {path}: {e}", flush=True)
            skipped += 1

    print("\n📊 Summary:")
    print(f"  Total JPEGs processed   : {total}")
    print(f"  Successfully scrubbed   : {scrubbed}")
    print(f"  Skipped (errors)        : {skipped}")
    if args.dry_run:
        print("📝 This was a dry run — no changes were made.")

    if total == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
