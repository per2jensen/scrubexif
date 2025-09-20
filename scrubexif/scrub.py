#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Scrub EXIF metadata from JPEG files while retaining selected tags.

🐾 Designed for photographers who want to preserve camera details
    (exposure, lens, ISO, etc.) but remove private or irrelevant data.
"""

import argparse
import logging
import os
import subprocess
import shutil
import sys
from pathlib import Path
from pathlib import Path
from typing import Optional

sys.stdout.reconfigure(line_buffering=True)


__version__ = "0.5.12"

__license__ = '''Licensed under GNU GENERAL PUBLIC LICENSE v3, see the supplied file "LICENSE" for details.
THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY APPLICABLE LAW, not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See section 15 and section 16 in the supplied "LICENSE" file.'''


class ScrubResult:
    __slots__ = ("input_path", "output_path", "status", "error_message", "duplicate_path")

    def __init__(
        self,
        input_path: Path,
        output_path: Optional[Path] = None,
        status: str = "scrubbed",
        error_message: Optional[str] = None,
        duplicate_path: Optional[Path] = None,
    ):
        self.input_path = input_path
        self.output_path = output_path
        self.status = status  # "scrubbed", "skipped", "duplicate", "error"
        self.error_message = error_message
        self.duplicate_path = duplicate_path

    def __repr__(self):
        return (
            f"ScrubResult(status={self.status!r}, "
            f"input={self.input_path.name}, "
            f"output={self.output_path.name if self.output_path else 'n/a'}, "
            f"error={bool(self.error_message)})"
        )



class ScrubSummary:
    def __init__(self):
        self.total = 0
        self.scrubbed = 0
        self.skipped = 0
        self.duplicates_deleted = 0
        self.duplicates_moved = 0
        self.errors = 0

    def update(self, result: ScrubResult):
        self.total += 1
        match result.status:
            case "scrubbed":
                self.scrubbed += 1
            case "skipped":
                self.skipped += 1
            case "duplicate":
                if result.duplicate_path:
                    self.duplicates_moved += 1
                else:
                    self.duplicates_deleted += 1
            case "error":
                self.errors += 1

    def print(self):
        print("📊 Summary:")
        print(f"  Total JPEGs found     : {self.total}")
        print(f"  Successfully scrubbed : {self.scrubbed}")
        print(f"  Skipped (errors)      : {self.errors}")
        if self.duplicates_deleted:
            print(f"  Duplicates deleted    : {self.duplicates_deleted}")
        if self.duplicates_moved:
            print(f"  Duplicates moved      : {self.duplicates_moved}")




# === Fixed container paths ===
INPUT_DIR = Path("/photos/input")
OUTPUT_DIR = Path("/photos/output")
PROCESSED_DIR = Path("/photos/processed")
ERRORS_DIR = Path("/photos/errors")

# === Whitelisted tags ===
EXIF_TAGS_TO_KEEP = [
    "ExposureTime",
    "FNumber",
    "ImageSize",
    "Title",
    "FocalLength",
    "ISO",
    "Orientation",
]

# Exiftool "bundles" that affect a set of tags
EXIFTOOL_META_TAGS = ["ColorSpaceTags"]  # https://exiftool.org/forum/index.php?topic=13451.0

# Groups to check for tags
TAG_GROUPS = ["", "XMP", "XMP-dc", "EXIF", "IPTC", "Makernotes", "Comment", "PhotoShop"]


def setup_logger(level: str = "info"):
    """
    Configure the global logger with console output and uppercase level names.

    Args:
        level (str): One of 'debug', 'info', 'warn', 'error', 'crit'
    """
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warn": logging.WARNING,
        "error": logging.ERROR,
        "crit": logging.CRITICAL,
    }

    logger = logging.getLogger("scrubexif")
    logger.setLevel(level_map.get(level.lower(), logging.INFO))

    handler = logging.StreamHandler()
    formatter = logging.Formatter("🔎 [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)

    logger.handlers.clear()  # Avoid duplicate handlers
    logger.addHandler(handler)
    logger.propagate = False

    return logger

# Will be initialized in main()
log = logging.getLogger("scrubexif")




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


def build_preserve_args(paranoia: bool = False) -> list[str]:
    """
    Build a list of -tag arguments for ExifTool to preserve selected metadata.

    This function expands each tag from EXIF_TAGS_TO_KEEP across multiple
    metadata groups (like EXIF, XMP, IPTC) and returns a deduplicated list
    of arguments in the format expected by ExifTool.

    Returns:
        List[str]: A list of arguments like ['-ISO', '-EXIF:ISO', '-XMP:ISO', ...]

    Example:
        Given:
            EXIF_TAGS_TO_KEEP = ["ISO", "CreateDate"]
            TAG_GROUPS = ["", "EXIF", "XMP"]

        The output will be:
            [
                "-ISO",
                "-EXIF:ISO",
                "-XMP:ISO",
                "-CreateDate",
                "-EXIF:CreateDate",
                "-XMP:CreateDate"
            ]

        These arguments can be used like so:
            exiftool -tagsFromFile @ -ISO -EXIF:ISO -XMP:ISO -CreateDate ...

    This allows ExifTool to preserve whitelisted tags across different metadata
    namespaces when removing all other EXIF data.
    """
    args = []
    seen = set()
    tags = EXIF_TAGS_TO_KEEP.copy()
    if not paranoia:
        tags += EXIFTOOL_META_TAGS
    for tag in tags:
        for group in TAG_GROUPS:
            key = f"{group}:{tag}" if group else tag
            if key not in seen:
                args.append(f"-{key}")
                seen.add(key)
    if log.isEnabledFor(logging.DEBUG):
        log.debug("Preserving tags: %s", " ".join(args))
    return args


def build_preview_cmd(input_path: Path, output_path: Path, paranoia: bool) -> list[str]:
    """
    Build a safe ExifTool command to scrub input → output (temp file).
    Never overwrites input.
    """
    cmd = ["exiftool", "-P", "-m", "-all=", "-gps:all=", "-tagsFromFile", "@"]

    # Use whitelist logic
    cmd += build_preserve_args(paranoia=paranoia)

    # Apply paranoia: strip ICC if requested
    if paranoia:
        cmd += ["-ICC_Profile:all="]

    # Output to temp file
    cmd += ["-o", str(output_path), str(input_path)]
    return cmd


def build_exiftool_cmd(input_path: Path, output_path: Path | None = None,
                       overwrite: bool = False, paranoia: bool = False) -> list[str]:
    """
    Construct a full ExifTool command for metadata scrubbing.
    """
    cmd = ["exiftool"]
    if overwrite:
        cmd.append("-overwrite_original")
    cmd += [
        "-P",
        "-all=",
        "-gps:all=",
        "-tagsFromFile", "@"
    ]
    if paranoia:
        cmd += ["-ICC_Profile:all="]
    cmd += build_preserve_args(paranoia=paranoia)
    if output_path:
        cmd += ["-o", str(output_path)]
    cmd.append(str(input_path))
    return cmd


def print_tags(file: Path, label: str = ""):
    try:
        result = subprocess.run(
            ["exiftool", "-a", "-G1", "-s", str(file)],
            capture_output=True, text=True
        )
        print(f"\n📸 Tags {label} {file.name}:")
        print(result.stdout.strip())
    except Exception as e:
        print(f"❌ Failed to read tags: {e}")


def scrub_file(
    input_path: Path,
    output_path: Path | None = None,
    delete_original=False,
    dry_run=False,
    show_tags_mode: str | None = None,
    paranoia: bool = True,
    on_duplicate: str = "delete",
) -> ScrubResult:
    print(f"scrub_file: input={input_path}, output={output_path}")
    output_file = output_path / input_path.name if output_path else input_path
    print("Output file will be:", output_file)

    # === Handle duplicates ===
    if output_file.exists() and input_path.resolve() != output_file.resolve():
        print(f"⚠️ Duplicate logic triggered: input={input_path}, output={output_path}")

        if dry_run:
            print(f"🚫 [dry-run] Would detect duplicate: {output_path.name}")
            return ScrubResult(input_path, output_path, status="duplicate")

        if on_duplicate == "delete":
            print(f"🗑️  Duplicate detected — deleting {input_path.name}")
            input_path.unlink(missing_ok=True)
            return ScrubResult(input_path, output_path, status="duplicate")

        elif on_duplicate == "move":
            target = ERRORS_DIR / input_path.name
            count = 1
            while target.exists():
                target = ERRORS_DIR / f"{input_path.stem}_{count}{input_path.suffix}"
                count += 1
            shutil.move(input_path, target)
            print(f"📦 Moved duplicate to: {target}")
            return ScrubResult(input_path, output_path, status="duplicate", duplicate_path=target)

    # === Dry-run handling ===
    if dry_run:
        if show_tags_mode in {"before", "both"}:
            print_tags(input_path, label="before")
        if show_tags_mode in {"after", "both"}:
            print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
        print(f"🔍 Dry run: would scrub {input_path}")
        return ScrubResult(input_path, output_path, status="scrubbed")

    # === Build ExifTool command ===
    in_place = output_path is None or input_path.resolve() == output_path.resolve()
    cmd = build_exiftool_cmd(input_path, output_path=None if in_place else output_path,
                             overwrite=in_place, paranoia=paranoia)

    if log.isEnabledFor(logging.DEBUG):
        log.debug("Running ExifTool command: %s", " ".join(cmd))

    if show_tags_mode in {"before", "both"}:
        print_tags(input_path, label="before")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err_msg = result.stderr.strip().splitlines()[0] if result.stderr else "Unknown error"
        print(f"❌ Failed to scrub {input_path.name}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="error",
            error_message=err_msg
        )

    if show_tags_mode in {"after", "both"}:
        print_tags(output_path or input_path, label="after")

    def display_path(path: Path) -> str:
        try:
            return str(path.relative_to("/photos"))
        except ValueError:
            return str(path)

    print(f"✅ Saved scrubbed file to {display_path(output_path or input_path)}")

    if delete_original and not in_place and input_path.exists():
        input_path.unlink()
        print(f"❌ Deleted original: {input_path}")

    return ScrubResult(input_path, output_path, status="scrubbed")



def find_jpegs_in_dir(dir_path: Path, recursive: bool = False) -> list[Path]:
    if not dir_path.is_dir():
        return []
    search_func = dir_path.rglob if recursive else dir_path.glob
    return [
        f for f in search_func("*")
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg")
    ]



def auto_scrub(summary: ScrubSummary, dry_run=False, delete_original=False,
               show_tags_mode: str | None = None,
               paranoia: bool = True,
               max_files: int | None = None,
               on_duplicate: str = "delete")  -> ScrubSummary:
    print(f"🚀 Auto mode: Scrubbing JPEGs in {INPUT_DIR}")

    # Safety checks
    check_dir_safety(INPUT_DIR, "Input")
    check_dir_safety(OUTPUT_DIR, "Output")
    check_dir_safety(PROCESSED_DIR, "Processed")

    input_files = find_jpegs_in_dir(INPUT_DIR, recursive=False)

    if max_files is not None:
        input_files = input_files[:max_files]

    if not input_files:
        print("⚠️ No JPEGs found — nothing to do.")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    success = 0
    for file in input_files:
        if dry_run:
            if show_tags_mode in {"before", "both"}:
                print_tags(file, label="before")
            if show_tags_mode in {"after", "both"}:
                print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
            print(f"🔍 Would scrub: {file.name}")
            continue

        result = scrub_file(file, OUTPUT_DIR,
                        delete_original=delete_original,
                        show_tags_mode=show_tags_mode,
                        paranoia=paranoia,
                        on_duplicate=on_duplicate)
        
        summary.update(result)
        if result.status == "scrubbed":
            dst_processed = PROCESSED_DIR / file.name
            if file.resolve() != dst_processed.resolve():
                shutil.move(file, dst_processed)
            else:
                print(f"⚠️ Skipping move: source and destination are the same")
            print(f"📦 Moved original to {PROCESSED_DIR / file.name}")
            success += 1

    return summary

def manual_scrub(files: list[Path],
                summary: ScrubSummary, 
                recursive: bool, dry_run=False,
                 show_tags_mode: str | None = None,
                 paranoia: bool = True,
                 max_files: int | None = None,
                 preview: bool = False) -> ScrubSummary:
    if not files and not recursive:
        print("⚠️ No files provided and --recursive not set.")
        return

    targets = []

    for file in files:
        if file.is_file() and file.suffix.lower() in (".jpg", ".jpeg"):
            targets.append(file)
        elif file.is_dir():
            search_func = file.rglob if recursive else file.glob
            targets.extend(
                f for f in search_func("*")
                if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg")
            )

    if not targets:
        print("⚠️ No JPEGs matched.")
        return

    if max_files is not None:
        targets = targets[:max_files]

    success = 0

    if (preview or
        (dry_run and show_tags_mode in {"after", "both"} and len(targets) == 1)):

        f = targets[0]
        from tempfile import NamedTemporaryFile
        temp = NamedTemporaryFile(suffix=".jpg", delete=False)
        shutil.copy(f, temp.name)
        preview_input = Path(temp.name)
        preview_output = preview_input.with_suffix(".scrubbed.jpg")

        cmd = build_preview_cmd(preview_input, preview_output, paranoia=paranoia)

        if log.isEnabledFor(logging.DEBUG):
            log.debug("Preview mode: %s", " ".join(cmd))

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"❌ Preview scrub failed: {result.stderr.strip()}")
        else:
            if show_tags_mode in {"before", "both"}:
                print_tags(f, label="before")
            print_tags(preview_output, label="after")

        preview_input.unlink(missing_ok=True)
        preview_output.unlink(missing_ok=True)

        print("📊 Preview complete — original file was not modified.")
        return

    for f in targets:
        if dry_run:
            if show_tags_mode in {"before", "both"}:
                print_tags(f, label="before")
            if show_tags_mode in {"after", "both"}:
                print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
            print(f"🔍 Would scrub: {f}")
            continue

        # Preview mode uses a temp scrub target
        preview_output = None

        result = scrub_file(f,
                        output_path=None,
                        delete_original=False,
                        dry_run=False,  # must be False to allow scrub
                        show_tags_mode=show_tags_mode,
                        paranoia=paranoia,
                        on_duplicate=None)

        summary.update(result)

    return summary



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
    parser.add_argument("--show-tags", choices=["before", "after", "both"], help="Show metadata tags before, after, or both for each image")
    parser.add_argument("--paranoia", action="store_true",
       help="Maximum metadata scrubbing — removes ICC profile including it's fingerprinting vector")
    parser.add_argument("--preview", action="store_true",
       help="Preview scrub effect on one file without modifying it (shows before/after metadata)")
    parser.add_argument("--max-files", type=int, metavar="N",
       help="Limit number of files to scrub (for testing or safe inspection)")
    parser.add_argument("--dry-run", action="store_true", help="List actions without performing them")
    parser.add_argument("--on-duplicate", choices=["delete", "move"], default=os.getenv("SCRUBEXIF_ON_DUPLICATE", "delete"), help="What to do with duplicate files in auto mode."
        "'delete' (default) will remove them. "
        "'move' will move them to /photos/errors/")
    parser.add_argument("--delete-original", action="store_true", help="Delete original files after scrub (works in auto mode)")
    parser.add_argument("--log-level", choices=["debug", "info", "warn", "error", "crit"], default="info", help="Set log verbosity (default: info)")

    parser.add_argument("-v", "--version", action="store_true", help="Show version and license")
    args = parser.parse_args()

    global log
    log = setup_logger(args.log_level)

    if args.version:
        show_version()
        sys.exit(0)

    # results keepper and summarizer
    summary = ScrubSummary()

    if args.on_duplicate == "move":
        try:
            ERRORS_DIR.mkdir(parents=True, exist_ok=True)
            check_dir_safety(ERRORS_DIR, "Errors")

        except Exception as e:
            print(f"❌ Failed to create errors directory: {ERRORS_DIR}\n{e}", file=sys.stderr)
            sys.exit(1)


    if args.preview:
        args.dry_run = True
        args.show_tags = "both"
        args.max_files = 1

    if args.from_input:


        auto_scrub(summary=summary,
                dry_run=args.dry_run,
                delete_original=args.delete_original,
                show_tags_mode=args.show_tags,
                paranoia=args.paranoia,
                max_files=args.max_files,
                on_duplicate=args.on_duplicate)
    else:
        if args.files:
            resolved_files = [
                f if f.is_absolute() else Path("/photos") / f
                for f in args.files
            ]
        else:
            # Implicit default when using --recursive
            resolved_files = [Path("/photos")]

        manual_scrub(resolved_files,
                    summary=summary,
                    recursive=args.recursive,
                    dry_run=args.dry_run,
                    show_tags_mode=args.show_tags,
                    paranoia=args.paranoia,
                    max_files=args.max_files,
                    preview=args.preview)


    summary.print()

if __name__ == "__main__":
    main()

