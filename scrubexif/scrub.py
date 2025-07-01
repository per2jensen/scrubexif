#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Scrub EXIF metadata from JPEG files while retaining selected tags.

🐾 Designed for photographers who want to preserve camera details
    (exposure, lens, ISO, etc.) but remove private or irrelevant data.

Usage examples:

  # ✅ Manual mode — scrub specific files or directories in $PWD
  docker run -v "$PWD:/photos" scrubexif:dev image1.jpg
  docker run -v "$PWD:/photos" scrubexif:dev --recursive

  # ✅ Auto mode — scrub all JPEGs in input dir ($PWD/input must be mounted)
  docker run -v "$PWD/input:/photos/input" \
             -v "$PWD/output:/photos/output" \
             -v "$PWD/processed:/photos/processed" \
             scrubexif:dev --from-input
"""

import sys
import subprocess
import argparse
import shutil
from pathlib import Path

# === Fixed container paths ===
INPUT_DIR = Path("/photos/input")
OUTPUT_DIR = Path("/photos/output")
PROCESSED_DIR = Path("/photos/processed")

VALID_EXTENSIONS = {".jpg", ".jpeg"}

# === EXIF preservation ===
# EXIF_TAGS_TO_KEEP = [
#     "ExposureTime",
#     "CreateDate",
#     "FNumber",
#     "ImageSize",
#     "Rights",
#     "Title",
#     "FocalLength",
#     "Subject",
#     "ISO",
#     "Orientation",
#     "Artist",
#     "Copyright",
#     "By-line",         # IPTC
#     "Event",           # XMP-iptcExt
# ]


# Group-prefixed versions
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

EXIFTOOL_CMD_BASE_COMMON = (
    ["exiftool", "-P", "-all:all=", "-gps:all=", "-tagsfromfile", "@"]
    + [f"-exif:{tag}" for tag in EXIF_TAGS_TO_KEEP if tag not in {"By-line", "Event"}]
    + ["-Iptc:By-line", "-Xmp-iptcExt:Event", "-ICC_Profile"]
)

EXIFTOOL_CMD_AUTO = EXIFTOOL_CMD_BASE_COMMON.copy()
EXIFTOOL_CMD_MANUAL = ["-overwrite_original"] + EXIFTOOL_CMD_BASE_COMMON

def scrub_file(input_path: Path, output_path: Path, dry_run=False) -> bool:
    if dry_run:
        print(f"🔍 Dry run: would scrub {input_path}")
        return True

    # Overwrite in-place if input == output (manual mode), else output separately (auto mode)
    if input_path.resolve() == output_path.resolve():
        cmd = EXIFTOOL_CMD_MANUAL + [str(input_path)]
    else:
        cmd = EXIFTOOL_CMD_AUTO + ["-o", str(output_path), str(input_path)]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"❌ Failed to scrub {input_path}: {result.stderr.strip()}")
        return False

    if output_path.exists() or input_path.resolve() == output_path.resolve():
        print(f"✅ Saved scrubbed file to {output_path}")
        return True
    else:
        print(f"⚠️  No output created for {input_path}")
        return False


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


def auto_scrub(dry_run: bool, delete_original: bool):
    print("🚀 Auto mode: Scrubbing JPEGs in /photos/input")
    print(f"📤 Output saved to:         {OUTPUT_DIR}")
    print(f"📦 Originals moved to:      {PROCESSED_DIR}" if not delete_original else "🗑️  Originals deleted after scrubbing")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    total = scrubbed = skipped = 0

    images = sorted(
        p for p in INPUT_DIR.glob("*")
        if p.suffix.lower() in VALID_EXTENSIONS and p.is_file()
    )

    for image in images:
        if not dry_run and not image.exists():
            skipped += 1
            print(f"⚠️  Input file vanished during scrub: {image}")
            continue

        total += 1
        output_file = OUTPUT_DIR / image.name

        try:
            was_scrubbed = scrub_file(image, output_file, dry_run=dry_run)
            if was_scrubbed:
                scrubbed += 1
                if not dry_run and image.exists():
                    if delete_original:
                        image.unlink()
                        print("🗑️ Deleted original from input")
                    else:
                        dest = PROCESSED_DIR / image.name
                        shutil.move(str(image), str(dest))
                        print(f"📦 Moved original to {dest}")
                else:
                    print(f"⚠️  Input file already removed: {image}")

        # try:
        #     if scrub_file(image, output_file, dry_run=dry_run):
        #         scrubbed += 1
        #         print(f"✅ Saved scrubbed file to {output_file}")
        #         if not dry_run and image.exists():
        #             if delete_original:
        #                 image.unlink()
        #                 print("🗑️ Deleted original from input")
        #             else:
        #                 dest = PROCESSED_DIR / image.name
        #                 shutil.move(str(image), str(dest))
        #                 print(f"📦 Moved original to {dest}")
        #         else:
        #             print(f"⚠️  Input file already removed: {image}")
        except Exception as e:
            skipped += 1
            print(f"❌ Error processing {image}: {e}")

    print("📊 Summary:")
    print(f"  Total JPEGs found     : {total}")
    print(f"  Successfully scrubbed : {scrubbed}")
    print(f"  Skipped (errors)      : {skipped}")



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", help="Files or directories to process manually")
    parser.add_argument("--dry-run", action="store_true", help="Show tags that would be kept, no output written")
    parser.add_argument("--recursive", "-r", action="store_true", help="Recurse into directories")
    parser.add_argument("--delete-original", action="store_true", help="Delete original files after processing (only applies with --from-input)")
    parser.add_argument("--from-input", action="store_true", help="Scrub all JPEGs in /photos/input (auto mode)")
    args = parser.parse_args()

    if args.from_input:
        auto_scrub(dry_run=args.dry_run, delete_original=args.delete_original)
        return

    if not args.paths:
        print("ℹ️ No files or folders provided — defaulting to scanning /photos")
        args.paths = ["/photos"]
    else:
        # Rewrite relative paths to /photos/<filename>
        resolved_paths = []
        for p in args.paths:
            path = Path(p)
            if not path.is_absolute():
                path = Path("/photos") / path
            resolved_paths.append(str(path))
        args.paths = resolved_paths

    print("🧼 Manual mode: Scrubbing user-specified files or directories")
    total = scrubbed = skipped = 0

    for path in find_images(args.paths, args.recursive):
        total += 1
        try:
            if scrub_file(path, path, dry_run=args.dry_run):
                scrubbed += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"❌ Error: {path}: {e}")
            skipped += 1

    print("\n📊 Summary:")
    print(f"  Total JPEGs processed : {total}")
    print(f"  Successfully scrubbed : {scrubbed}")
    print(f"  Skipped (errors)      : {skipped}")
    if args.dry_run:
        print("📝 This was a dry run — no changes were made.")
    if total == 0:
        sys.exit(1)



def main_old():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", help="Files or directories to process manually")
    parser.add_argument("--dry-run", action="store_true", help="Show tags that would be kept, no output written")
    parser.add_argument("--recursive", "-r", action="store_true", help="Recurse into directories")
    parser.add_argument("--delete-original", action="store_true", help="Delete original files after processing (only applies with --from-input)")
    parser.add_argument("--from-input", action="store_true", help="Scrub all JPEGs in /photos/input (auto mode)")
    args = parser.parse_args()

    if args.from_input:
        auto_scrub(dry_run=args.dry_run, delete_original=args.delete_original)
        return

    if not args.paths:
        print("ℹ️ No files or folders provided — defaulting to scanning /photos")
        args.paths = ["/photos"]

    print("🧼 Manual mode: Scrubbing user-specified files or directories")
    total = scrubbed = skipped = 0

    for path in find_images(args.paths, args.recursive):
        total += 1
        try:
            if scrub_file(path, path, dry_run=args.dry_run):
                scrubbed += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"❌ Error: {path}: {e}")
            skipped += 1

    print("\n📊 Summary:")
    print(f"  Total JPEGs processed : {total}")
    print(f"  Successfully scrubbed : {scrubbed}")
    print(f"  Skipped (errors)      : {skipped}")
    if args.dry_run:
        print("📝 This was a dry run — no changes were made.")
    if total == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
