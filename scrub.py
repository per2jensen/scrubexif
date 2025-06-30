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
from pathlib import Path

# === Fixed container paths ===
INPUT_DIR = Path("/photos/input")
OUTPUT_DIR = Path("/photos/output")
PROCESSED_DIR = Path("/photos/processed")

VALID_EXTENSIONS = {".jpg", ".jpeg"}

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



def scrub_file(input_file: Path, output_file: Path, dry_run: bool) -> bool:
    print(f"\n🧽 Scrubbing: {input_file}")

    if dry_run:
        cmd = ["stdbuf", "-oL", "exiftool", "-listtags", str(input_file)]
    else:
        if input_file.resolve() == output_file.resolve():
            # Manual mode: in-place scrub with overwrite
            cmd = ["stdbuf", "-oL"] + EXIFTOOL_CMD_BASE + [str(input_file)]
        else:
            # Auto mode: output to a different file — no overwrite_original!
            if output_file.exists():
                output_file.unlink()

            cmd = [
                "stdbuf", "-oL",
                "exiftool",
                "-P",
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
                "-o", str(output_file),
                str(input_file)
            ]

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    for line in process.stdout:
        if "ICC_Profile deleted" not in line:
            print(line.strip())

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



import shutil

def auto_scrub(dry_run: bool, delete_original: bool):
    print("🚀 Auto mode: Scrubbing JPEGs in /photos/input")
    print(f"📤 Output saved to:         {OUTPUT_DIR}")
    print(f"📦 Originals moved to:      {PROCESSED_DIR}" if not delete_original else "🗑️  Originals deleted after scrubbing")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    total = scrubbed = skipped = 0

    # 🔒 Freeze input list before processing
    images = sorted(
        p for p in INPUT_DIR.glob("*")
        if p.suffix.lower() in VALID_EXTENSIONS and p.is_file()
    )

    for image in images:
    # Check again before trying to move/delete after scrub
        if not dry_run and not image.exists():
            skipped += 1
            print(f"⚠️  Input file vanished during scrub: {image}")
            continue

        total += 1
        output_file = OUTPUT_DIR / image.name

        try:
            if scrub_file(image, output_file, dry_run=dry_run):
                scrubbed += 1
                print(f"✅ Saved scrubbed file to {output_file}")
                if not dry_run:
                    if delete_original:
                        image.unlink()
                        print("🗑️ Deleted original from input")
                    else:
                        dest = PROCESSED_DIR / image.name
                        shutil.move(str(image), str(dest))
                        print(f"📦 Moved original to {dest}")
            else:
                skipped += 1
                print(f"❌ Failed to scrub: {image}")
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
        # Default to scanning /photos (restores original behavior)
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
