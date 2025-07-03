#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Scrub EXIF metadata from JPEG files while retaining selected tags.

🐾 Designed for photographers who want to preserve camera details
    (exposure, lens, ISO, etc.) but remove private or irrelevant data.
"""

import argparse
import os
import subprocess
import shutil
import sys
from pathlib import Path


__version__ = "0.5.7"

__license__ = '''Licensed under GNU GENERAL PUBLIC LICENSE v3, see the supplied file "LICENSE" for details.
THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY APPLICABLE LAW, not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See section 15 and section 16 in the supplied "LICENSE" file.'''



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



def show_version():
    script_name = os.path.basename(sys.argv[0])
    print(f"{script_name} {__version__}")
    print(f"{script_name} source code is here: https://github.com/per2jensen/scrubexif")
    print(__license__)



def check_dir_safety(path: Path, label: str):
    if not path.exists():
        print(f"❌ {label} directory does not exist: {path}")
        sys.exit(1)
    if not path.is_dir():
        print(f"❌ {label} path is not a directory: {path}")
        sys.exit(1)
    if path.is_symlink():
        print(f"❌ {label} is a symbolic link (not allowed): {path}")
        sys.exit(1)
    try:
        test_file = path / ".scrubexif_write_test"
        with open(test_file, "w") as f:
            f.write("test")
        test_file.unlink()
    except Exception:
        print(f"❌ {label} directory is not writable: {path}")
        sys.exit(1)



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



# EXIFTOOL_CMD_BASE_COMMON = (
#     ["exiftool", "-P", "-all:all=", "-gps:all=", "-tagsfromfile", "@"]
#     + [f"-exif:{tag}" for tag in EXIF_TAGS_TO_KEEP if tag not in {"By-line", "Event"}]
#     + ["-Iptc:By-line", "-Xmp-iptcExt:Event", "-ICC_Profile"]
# )

# EXIFTOOL_CMD_AUTO = EXIFTOOL_CMD_BASE_COMMON.copy()
# EXIFTOOL_CMD_MANUAL = ["-overwrite_original"] + EXIFTOOL_CMD_BASE_COMMON


EXIFTOOL_CMD_BASE_COMMON = (
    ["-P", "-all:all=", "-gps:all=", "-tagsfromfile", "@"]
    + [f"-exif:{tag}" for tag in EXIF_TAGS_TO_KEEP if tag not in {"By-line", "Event"}]
    + ["-Iptc:By-line", "-Xmp-iptcExt:Event", "-ICC_Profile"]
)

EXIFTOOL_CMD_AUTO = ["exiftool"] + EXIFTOOL_CMD_BASE_COMMON

#EXIFTOOL_CMD_MANUAL = ["exiftool", "-overwrite_original"] + EXIFTOOL_CMD_BASE_COMMON

EXIFTOOL_CMD_MANUAL = (
    ["exiftool", "-overwrite_original", "-P", "-all=", "-gps:all="] + build_preserve_args()
)



def scrub_file(input_path: Path, output_path: Path | None = None,
               delete_original=False, dry_run=False) -> bool:
    if dry_run:
        print(f"🔍 Dry run: would scrub {input_path}")
        return True

    in_place = output_path is None or input_path.resolve() == output_path.resolve()
    cmd = (
        EXIFTOOL_CMD_MANUAL + [str(input_path)]
        if in_place else
        ["exiftool", "-P", "-m", "-all=", "-tagsFromFile", "@"]
        + build_preserve_args()
        + ["-o", str(output_path), str(input_path)]
    )

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ Failed to scrub {input_path}: {result.stderr.strip()}")
        return False


    def display_path(path: Path) -> str:
        try:
            return str(path.relative_to("/photos"))
        except ValueError:
            return str(path)

    # And in scrub_file:
    print(f"✅ Saved scrubbed file to {display_path(output_path or input_path)}")



    #print(f"✅ Saved scrubbed file to {output_path or input_path}")
    if delete_original and not in_place and input_path.exists():
        input_path.unlink()
        print(f"❌ Deleted original: {input_path}")
    return True


def find_jpegs_in_dir(dir_path: Path, recursive: bool = False) -> list[Path]:
    if not dir_path.is_dir():
        return []
    search_func = dir_path.rglob if recursive else dir_path.glob
    return [
        f for f in search_func("*")
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg")
    ]



def auto_scrub(dry_run=False, delete_original=False):
    print(f"🚀 Auto mode: Scrubbing JPEGs in {INPUT_DIR}")

    # Safety checks
    check_dir_safety(INPUT_DIR, "Input")
    check_dir_safety(OUTPUT_DIR, "Output")
    check_dir_safety(PROCESSED_DIR, "Processed")

    input_files = find_jpegs_in_dir(INPUT_DIR, recursive=False)
 
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
        elif file.is_dir():
            targets.extend(find_jpegs_in_dir(file, recursive))

            search_func = file.rglob if recursive else file.glob
            targets.extend(
                f for f in search_func("*")
                if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg")
            )

    if not targets:
        print("⚠️ No JPEGs matched.")
        return

    success = 0
    for f in targets:
        if dry_run:
            print(f"🔍 Would scrub: {f}")
            continue

        ok = scrub_file(f, None, delete_original=delete_original)
        if ok:
            success += 1
    print(f"📊 Scrubbed {success} JPEG(s) out of {len(targets)}")


def require_force_for_root():
    if os.geteuid() == 0 and os.environ.get("ALLOW_ROOT") != "1":
        print("❌ Running as root is not allowed unless ALLOW_ROOT=1 is set.", file=sys.stderr)
        sys.exit(1)



def main():
    require_force_for_root()
    parser = argparse.ArgumentParser(description="Scrub EXIF metadata from JPEGs.")
    parser.add_argument("files", nargs="*", type=Path, help="Files or directories")
    parser.add_argument("--from-input", action="store_true", help="Use auto mode")
    parser.add_argument("-r", "--recursive", action="store_true", help="Recurse into directories")
    parser.add_argument("--dry-run", action="store_true", help="List actions without performing them")
    parser.add_argument("--delete-original", action="store_true", help="Delete original files after scrub (work in auto mode)")
    parser.add_argument("-v", "--version", action="store_true", help="Show version and license")
    args = parser.parse_args()


    if args.version:
        show_version()
        sys.exit(0) 

    if args.from_input:
        auto_scrub(dry_run=args.dry_run, delete_original=args.delete_original)
    else:
        if args.files:
            resolved_files = [
                f if f.is_absolute() else Path("/photos") / f
                for f in args.files
            ]
        else:
            # Implicit default when using --recursive
            resolved_files = [Path("/photos")]

        manual_scrub(resolved_files, recursive=args.recursive,
                     dry_run=args.dry_run, delete_original=False)


if __name__ == "__main__":
    main()

