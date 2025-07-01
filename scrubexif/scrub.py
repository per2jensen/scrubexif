#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Scrub EXIF metadata from JPEG files while retaining selected tags.

🐾 Designed for photographers who want to preserve camera details
    (exposure, lens, ISO, etc.) but remove private or irrelevant data.
"""

import argparse
import subprocess
import shutil
import sys
from pathlib import Path

# === Fixed container paths ===
INPUT_DIR = Path("/photos/input")
OUTPUT_DIR = Path("/photos/output")
PROCESSED_DIR = Path("/photos/processed")

# === Whitelisted tags ===
EXIF_TAGS_TO_KEEP = [
    "ExposureTime",
    "CreateDate",
    "FNumber",
    "ImageSize",
    "Rights",
    "Title",
    "Subject",
    "FocalLength",
    "ISO",
    "Orientation",
    "Artist",
    "Copyright",
]


# Groups to check for tags
TAG_GROUPS = ["", "XMP", "XMP-dc", "EXIF", "IPTC"]


EXIFTOOL_CMD_BASE_COMMON = (
    ["exiftool", "-P", "-all:all=", "-gps:all=", "-tagsfromfile", "@"]
    + [f"-exif:{tag}" for tag in EXIF_TAGS_TO_KEEP if tag not in {"By-line", "Event"}]
    + ["-Iptc:By-line", "-Xmp-iptcExt:Event", "-ICC_Profile"]
)

EXIFTOOL_CMD_AUTO = EXIFTOOL_CMD_BASE_COMMON.copy()
EXIFTOOL_CMD_MANUAL = ["-overwrite_original"] + EXIFTOOL_CMD_BASE_COMMON


def build_preserve_args():
    args = []
    seen = set()
    for tag in EXIF_TAGS_TO_KEEP:
        for group in TAG_GROUPS:
            key = f"{group}:{tag}" if group else tag
            if key not in seen:
                args.append(f"-{key}")
                seen.add(key)
    return args


def scrub_file(input_path: Path, output_path: Path, delete_original=False, dry_run=False) -> bool:
    if dry_run:
        print(f"🔍 Dry run: would scrub {input_path}")
        return True


    if input_path.resolve() == output_path.resolve():
        # Manual mode (overwrite)
        cmd = EXIFTOOL_CMD_MANUAL + [str(input_path)]
    else:
        # Auto mode (copy with preserved tags)
        cmd = (
            ["exiftool", "-P", "-m", "-all=", "-tagsFromFile", "@"]
            + build_preserve_args()
            + ["-o", str(output_path), str(input_path)]
        )

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"❌ Failed to scrub {input_path}: {result.stderr.strip()}")
        return False

    if output_path.exists() or input_path.resolve() == output_path.resolve():
        print(f"✅ Saved scrubbed file to {output_path}")
        if delete_original and input_path.exists():
            input_path.unlink()
            print(f"❌ Deleted original: {input_path}")
        return True
    else:
        print(f"⚠️ No output created for {input_path}")
        return False



def auto_scrub(dry_run=False, delete_original=False):
    print(f"🚀 Auto mode: Scrubbing JPEGs in {INPUT_DIR}")
    input_files = list(INPUT_DIR.glob("*.jpg")) + list(INPUT_DIR.glob("*.jpeg"))
    if not input_files:
        print("⚠️ No JPEGs found — nothing to do.")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    success = 0
    for file in input_files:
        if dry_run:
            print(f"🔍 Would scrub: {file.name}")
            continue

        ok = scrub_file(file, OUTPUT_DIR, delete_original=False)
        if ok:
            dst_processed = PROCESSED_DIR / file.name
            if file.resolve() != dst_processed.resolve():
                shutil.move(file, dst_processed)
            else:
                print(f"⚠️ Skipping move: source and destination are the same")
            print(f"📦 Moved original to {PROCESSED_DIR / file.name}")
            success += 1

    print("📊 Summary:")
    print(f"  Total JPEGs found     : {len(input_files)}")
    print(f"  Successfully scrubbed : {success}")
    print(f"  Skipped (errors)      : {len(input_files) - success}")




def manual_scrub(files: list[Path], recursive: bool, dry_run=False, delete_original=False):
    if not files and not recursive:
        print("⚠️ No files provided and --recursive not set.")
        return

    targets = []

    for file in files:
        if file.is_file() and file.suffix.lower() in (".jpg", ".jpeg"):
            targets.append(file)
        elif file.is_dir() and recursive:
            targets.extend(f for f in file.rglob("*.jpg"))
            targets.extend(f for f in file.rglob("*.jpeg"))

    if not targets:
        print("⚠️ No JPEGs matched.")
        return

    for f in targets:
        if dry_run:
            print(f"🔍 Would scrub: {f}")
            continue

        scrub_file(f, f.parent, delete_original=delete_original)

def main():
    parser = argparse.ArgumentParser(description="Scrub EXIF metadata from JPEGs.")
    parser.add_argument("files", nargs="*", type=Path, help="Files or directories")
    parser.add_argument("--from-input", action="store_true", help="Use auto mode")
    parser.add_argument("--recursive", action="store_true", help="Recurse into directories")
    parser.add_argument("--dry-run", action="store_true", help="List actions without performing them")
    parser.add_argument("--delete-original", action="store_true", help="Delete original files after scrub")
    args = parser.parse_args()

    if args.from_input:
        auto_scrub(dry_run=args.dry_run, delete_original=args.delete_original)
    else:
        manual_scrub(args.files, recursive=args.recursive, dry_run=args.dry_run, delete_original=args.delete_original)

if __name__ == "__main__":
    main()
