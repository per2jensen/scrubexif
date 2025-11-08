#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Scrub EXIF metadata from JPEG files while retaining selected tags.

Designed for photographers who want to preserve camera details
(exposure, lens, ISO, etc.) but remove private or irrelevant data.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

sys.stdout.reconfigure(line_buffering=True)

__version__ = "0.7.7"

__license__ = '''Licensed under GNU GENERAL PUBLIC LICENSE v3, see the supplied file "LICENSE" for details.
THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY APPLICABLE LAW, not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See section 15 and section 16 in the supplied "LICENSE" file.'''


# ----------------------------
# Results and summary structs
# ----------------------------

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
        print(f"  Total JPEGs found        : {self.total}")
        print(f"  Successfully scrubbed    : {self.scrubbed}")
        print(f"  Skipped (unstable/temp)  : {self.skipped}")
        print(f"  Errors                   : {self.errors}")
        if self.duplicates_deleted:
            print(f"  Duplicates deleted       : {self.duplicates_deleted}")
        if self.duplicates_moved:
            print(f"  Duplicates moved         : {self.duplicates_moved}")


# ----------------------------
# Fixed container paths
# ----------------------------

PHOTOS_ROOT = Path("/photos")
INPUT_DIR = PHOTOS_ROOT / "input"
OUTPUT_DIR = PHOTOS_ROOT / "output"
PROCESSED_DIR = PHOTOS_ROOT / "processed"
ERRORS_DIR = PHOTOS_ROOT / "errors"


# ----------------------------
# Logger
# ----------------------------

def setup_logger(level: str = "info"):
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
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
    return logger


# Will be set/overridden in main()
log = logging.getLogger("scrubexif")

DEBUG_ENV_VARS = (
    "ALLOW_ROOT",
    "SCRUBEXIF_STATE",
    "SCRUBEXIF_ON_DUPLICATE",
    "SCRUBEXIF_STABLE_SECONDS",
    "SCRUBEXIF_IMAGE",
    "SCRUBEXIF_AUTOBUILD",
)


def show_version():
    script_name = os.path.basename(sys.argv[0])
    print(f"{script_name} {__version__}")
    print(f"{script_name} source code is here: https://github.com/per2jensen/scrubexif")
    print(__license__)


# ----------------------------
# Safety checks
# ----------------------------

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


# ----------------------------
# Stability state management
# ----------------------------

def _resolve_state_path_from_env() -> Optional[Path]:
    """
    Priority (when no CLI override is provided):
      1) SCRUBEXIF_STATE env
      2) /photos/.scrubexif_state.json if writable
      3) /tmp/.scrubexif_state.json if writable
      4) None => state disabled, mtime-only
    """
    env = os.getenv("SCRUBEXIF_STATE")
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates.append(Path("/photos/.scrubexif_state.json"))
    candidates.append(Path("/tmp/.scrubexif_state.json"))

    for p in candidates:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=p.parent, delete=True):
                return p
        except Exception:
            continue
    return None


def _validate_writable_path(p: Path) -> Optional[Path]:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=p.parent, delete=True):
            return p
    except Exception:
        return None


STATE_FILE: Optional[Path] = _resolve_state_path_from_env()
_warned_state_disabled = False


def load_state() -> dict:
    if STATE_FILE is None:
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("State load failed: %s", e)
    return {}


def save_state(state: dict):
    global _warned_state_disabled, STATE_FILE
    if STATE_FILE is None:
        if not _warned_state_disabled:
            log.info("State disabled: using mtime-only stability.")
            _warned_state_disabled = True
        return
    tmp = STATE_FILE.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        if not _warned_state_disabled:
            log.warning("State save failed at %s: %s. Falling back to mtime-only.", STATE_FILE, e)
            _warned_state_disabled = True
        try:
            os.unlink(tmp)
        except Exception:
            pass
        STATE_FILE = None  # stop future attempts


def prune_state(state: dict):
    remove = []
    for key in list(state.keys()):
        if not Path(key).exists():
            remove.append(key)
    for k in remove:
        state.pop(k, None)


def mark_seen(path: Path, state: dict):
    try:
        st = path.stat()
    except FileNotFoundError:
        return
    key = str(path.resolve())
    state[key] = {"size": st.st_size, "mtime": st.st_mtime, "seen": time.time()}


# ----------------------------
# Temp/partial detection
# ----------------------------

TEMP_SUFFIXES = {
    ".tmp", ".part", ".partial", ".crdownload", ".download", ".upload", ".cache",
    ".swp", ".swx", ".lck"
}
TEMP_PREFIXES = {".", "~", "._"}


def is_probably_temp(path: Path) -> bool:
    name = path.name
    if any(name.startswith(p) for p in TEMP_PREFIXES):
        return True
    low = name.lower()
    if path.suffix.lower() in TEMP_SUFFIXES:
        return True
    for suf in TEMP_SUFFIXES:
        if low.endswith(suf):
            return True
    return False


def is_file_stable(path: Path, state: dict, stable_seconds: int) -> bool:
    """
    Stable if:
      1) mtime age >= stable_seconds, and
      2) if previously seen, size+mtime unchanged since last run.
    """
    reason = "ok"
    try:
        st = path.stat()
    except FileNotFoundError:
        if log.isEnabledFor(logging.DEBUG):
            log.debug("Stability check: %s missing -> unstable", path)
        return False

    now = time.time()
    key = str(path.resolve())
    prev = state.get(key)
    age = now - st.st_mtime

    stable = True
    if stable_seconds > 0 and age < stable_seconds:
        stable = False
        reason = f"age<{stable_seconds}"
    elif prev and (prev.get("size") != st.st_size or prev.get("mtime") != st.st_mtime):
        stable = False
        reason = "changed"

    if log.isEnabledFor(logging.DEBUG):
        prev_seen = prev.get("seen") if prev else None
        prev_age = (now - prev_seen) if prev_seen else None
        log.debug(
            "Stability check: %s size=%d age=%.2fs threshold=%ds prev=%s -> %s (%s)",
            path,
            st.st_size,
            age,
            stable_seconds,
            {"size": prev.get("size") if prev else None,
             "mtime": prev.get("mtime") if prev else None,
             "seen_age": prev_age},
            stable,
            reason,
        )

    return stable


# ----------------------------
# EXIF config
# ----------------------------

EXIF_TAGS_TO_KEEP = [
    "ExposureTime",
    "FNumber",
    "ImageSize",
    "Title",
    "FocalLength",
    "ISO",
    "Orientation",
]

EXIFTOOL_META_TAGS = ["ColorSpaceTags"]  # bundle
TAG_GROUPS = ["", "XMP", "XMP-dc", "EXIF", "IPTC", "Makernotes", "Comment", "PhotoShop"]


def build_preserve_args(paranoia: bool = False) -> list[str]:
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
    cmd = ["exiftool", "-P", "-m", "-all=", "-gps:all=", "-tagsFromFile", "@"]
    cmd += build_preserve_args(paranoia=paranoia)
    if paranoia:
        cmd += ["-ICC_Profile:all="]
    cmd += ["-o", str(output_path), str(input_path)]
    return cmd


def build_exiftool_cmd(input_path: Path, output_path: Path | None = None,
                       overwrite: bool = False, paranoia: bool = False) -> list[str]:
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


# ----------------------------
# Scrub operations
# ----------------------------

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

    if output_path and output_file.is_symlink():
        msg = f"Destination is a symlink; refusing to scrub into {output_file}"
        print(f"❌ {msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="error",
            error_message=msg,
        )

    # duplicates
    if output_file.exists() and input_path.resolve() != output_file.resolve():
        print(f"⚠️ Duplicate logic triggered: input={input_path}, output={output_file}")

        if dry_run:
            print(f"🚫 [dry-run] Would detect duplicate: {output_file.name}")
            return ScrubResult(input_path, output_file, status="duplicate")

        if on_duplicate == "delete":
            print(f"🗑️  Duplicate detected — deleting {input_path.name}")
            input_path.unlink(missing_ok=True)
            return ScrubResult(input_path, output_file, status="duplicate")

        elif on_duplicate == "move":
            target = ERRORS_DIR / input_path.name
            count = 1
            while target.exists():
                target = ERRORS_DIR / f"{input_path.stem}_{count}{input_path.suffix}"
                count += 1
            shutil.move(input_path, target)
            print(f"📦 Moved duplicate to: {target}")
            return ScrubResult(input_path, output_file, status="duplicate", duplicate_path=target)

    # dry-run
    if dry_run:
        if show_tags_mode in {"before", "both"}:
            print_tags(input_path, label="before")
        if show_tags_mode in {"after", "both"}:
            print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
        print(f"🔍 Dry run: would scrub {input_path}")
        return ScrubResult(input_path, output_file, status="scrubbed")

    # exiftool command
    in_place = output_path is None or input_path.resolve() == output_path.resolve()
    cmd = build_exiftool_cmd(input_path, output_path=None if in_place else output_file,
                             overwrite=in_place, paranoia=paranoia)

    if log.isEnabledFor(logging.DEBUG):
        log.debug("Running ExifTool command: %s", " ".join(cmd))

    if show_tags_mode in {"before", "both"}:
        print_tags(input_path, label="before")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
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
        print_tags(output_file, label="after")

    def display_path(path: Path) -> str:
        try:
            return str(path.relative_to(PHOTOS_ROOT))
        except ValueError:
            return str(path)

    print(f"✅ Saved scrubbed file to {display_path(output_file)}")

    if delete_original and not in_place and input_path.exists():
        input_path.unlink()
        print(f"❌ Deleted original: {input_path}")

    return ScrubResult(input_path, output_file, status="scrubbed")


def find_jpegs_in_dir(dir_path: Path, recursive: bool = False) -> list[Path]:
    if not dir_path.is_dir():
        return []
    search_func = dir_path.rglob if recursive else dir_path.glob
    results: list[Path] = []
    for f in search_func("*"):
        if f.is_symlink():
            log.debug("Skipping symlinked file: %s", f)
            continue
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg"):
            results.append(f)
    return results


def auto_scrub(summary: ScrubSummary, dry_run=False, delete_original=False,
               show_tags_mode: str | None = None,
               paranoia: bool = True,
               max_files: int | None = None,
               on_duplicate: str = "delete",
               stable_seconds: int = 120) -> ScrubSummary:
    print(f"🚀 Auto mode: Scrubbing JPEGs in {INPUT_DIR}")
    print(f"⏳ Stability threshold: {stable_seconds}s")
    if STATE_FILE is None:
        print("ℹ️ Stability state: mtime-only (no writable state file)")

    # Safety
    check_dir_safety(INPUT_DIR, "Input")
    check_dir_safety(OUTPUT_DIR, "Output")
    check_dir_safety(PROCESSED_DIR, "Processed")

    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "Auto mode directories: input=%s output=%s processed=%s errors=%s",
            INPUT_DIR, OUTPUT_DIR, PROCESSED_DIR, ERRORS_DIR
        )

    state = load_state()
    prune_state(state)

    input_files = find_jpegs_in_dir(INPUT_DIR, recursive=False)
    if log.isEnabledFor(logging.DEBUG):
        log.debug("Input scan yielded %d files before filtering", len(input_files))

    # Filter
    filtered: list[Path] = []
    skipped_temp = 0
    skipped_unstable = 0

    for f in input_files:
        if is_probably_temp(f):
            skipped_temp += 1
            summary.skipped += 1
            summary.total += 1
            mark_seen(f, state)
            continue
        if not is_file_stable(f, state, stable_seconds):
            skipped_unstable += 1
            summary.skipped += 1
            summary.total += 1
            mark_seen(f, state)
            continue
        filtered.append(f)

    if max_files is not None:
        filtered = filtered[:max_files]

    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "Filtered candidates: %d (skipped temp=%d, unstable=%d)",
            len(filtered), skipped_temp, skipped_unstable
        )

    if not filtered:
        if skipped_temp or skipped_unstable:
            print(f"ℹ️ Nothing eligible yet. Skipped: temp={skipped_temp}, unstable={skipped_unstable}.")
        else:
            print("⚠️ No JPEGs found — nothing to do.")
        save_state(state)
        return summary

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    for file in filtered:
        if dry_run:
            if show_tags_mode in {"before", "both"}:
                print_tags(file, label="before")
            if show_tags_mode in {"after", "both"}:
                print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
            print(f"🔍 Would scrub: {file.name}")
            summary.total += 1
            continue

        result = scrub_file(
            file,
            OUTPUT_DIR,
            delete_original=delete_original,
            show_tags_mode=show_tags_mode,
            paranoia=paranoia,
            on_duplicate=on_duplicate,
        )

        summary.update(result)
        dst_processed = PROCESSED_DIR / file.name

        if result.status == "scrubbed" and not delete_original:
            moved = False
            if file.exists():
                if dst_processed.is_symlink():
                    print(f"⚠️ Skipping move: destination is a symlink ({dst_processed})")
                elif file.resolve() != dst_processed.resolve():
                    shutil.move(file, dst_processed)
                    moved = True
                else:
                    print("⚠️ Skipping move: source and destination are the same")
            else:
                print(f"⚠️ Skipping move: source file no longer exists ({file})")
            if moved:
                print(f"📦 Moved original to {dst_processed}")
        elif result.status == "error":
            if file.exists():
                if dst_processed.is_symlink():
                    print(f"⚠️ Scrub failed; destination is a symlink ({dst_processed}), leaving original in place")
                else:
                    try:
                        shutil.move(file, dst_processed)
                        print(f"⚠️ Scrub failed for {file.name}; moved original to {dst_processed} for inspection")
                    except Exception as exc:
                        print(f"⚠️ Scrub failed for {file.name}; unable to move original: {exc}")
            else:
                print(f"⚠️ Scrub failed and source already missing: {file}")

        mark_seen(file, state)

    save_state(state)
    return summary


def resolve_cli_path(raw: Path) -> Path:
    """
    Convert user-supplied CLI paths into absolute paths under /photos.
    Reject anything that escapes the allowed root to avoid clobbering arbitrary files.
    """
    candidate = raw if raw.is_absolute() else PHOTOS_ROOT / raw
    if candidate.is_symlink():
        print(f"❌ Symlinks are not allowed: {candidate}", file=sys.stderr)
        sys.exit(1)
    try:
        resolved = candidate.resolve()
    except FileNotFoundError:
        print(f"❌ Path does not exist: {candidate}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"❌ Failed to resolve path {raw}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        resolved.relative_to(PHOTOS_ROOT)
    except ValueError:
        print(f"❌ Path escapes allowed root {PHOTOS_ROOT}: {raw}", file=sys.stderr)
        sys.exit(1)
    return resolved


def manual_scrub(files: list[Path],
                 summary: ScrubSummary,
                 recursive: bool, dry_run=False,
                 show_tags_mode: str | None = None,
                 paranoia: bool = True,
                 max_files: int | None = None,
                 preview: bool = False) -> ScrubSummary:
    if not files and not recursive:
        print("⚠️ No files provided and --recursive not set.")
        return summary

    targets: list[Path] = []

    for file in files:
        if file.is_symlink():
            log.warning("Skipping symlink input: %s", file)
            continue
        if file.is_file() and file.suffix.lower() in (".jpg", ".jpeg"):
            targets.append(file)
        elif file.is_dir():
            targets.extend(find_jpegs_in_dir(file, recursive=recursive))

    if log.isEnabledFor(logging.DEBUG):
        log.debug("Manual mode targets gathered: %d files", len(targets))

    if not targets:
        print("⚠️ No JPEGs matched.")
        return summary

    if max_files is not None:
        targets = targets[:max_files]

    if preview or (dry_run and show_tags_mode in {"after", "both"} and len(targets) == 1):
        f = targets[0]
        from tempfile import NamedTemporaryFile
        temp = NamedTemporaryFile(suffix=".jpg", delete=False)
        temp_path = Path(temp.name)
        temp.close()
        shutil.copy(f, temp_path)
        preview_input = temp_path
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
        return summary

    for f in targets:
        if f.is_symlink():
            log.warning("Skipping symlink target: %s", f)
            continue
        if dry_run:
            if show_tags_mode in {"before", "both"}:
                print_tags(f, label="before")
            if show_tags_mode in {"after", "both"}:
                print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
            print(f"🔍 Would scrub: {f}")
            summary.total += 1
            continue

        result = scrub_file(f,
                            output_path=None,
                            delete_original=False,
                            dry_run=False,
                            show_tags_mode=show_tags_mode,
                            paranoia=paranoia,
                            on_duplicate=None)

        summary.update(result)

    return summary


# ----------------------------
# Root guard
# ----------------------------

def require_force_for_root():
    if os.geteuid() == 0 and os.environ.get("ALLOW_ROOT") != "1":
        print("❌ Running as root is not allowed unless ALLOW_ROOT=1 is set.", file=sys.stderr)
        sys.exit(1)


# ----------------------------
# CLI
# ----------------------------

def main():
    require_force_for_root()
    parser = argparse.ArgumentParser(description="Scrub EXIF metadata from JPEGs.")
    parser.add_argument("files", nargs="*", type=Path, help="Files or directories")
    parser.add_argument("--from-input", action="store_true", help="Use auto mode")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
    parser.add_argument("-r", "--recursive", action="store_true", help="Recurse into directories")
    parser.add_argument("--show-tags", choices=["before", "after", "both"], help="Show metadata before/after")
    parser.add_argument("--paranoia", action="store_true",
                        help="Maximum metadata scrubbing — removes ICC profile")
    parser.add_argument("--preview", action="store_true",
                        help="Preview scrub effect on one file without modifying it")
    parser.add_argument("--max-files", type=int, metavar="N",
                        help="Limit number of files to scrub")
    parser.add_argument("--dry-run", action="store_true", help="List actions without performing them")
    parser.add_argument("--on-duplicate", choices=["delete", "move"],
                        default=os.getenv("SCRUBEXIF_ON_DUPLICATE", "delete"),
                        help="Duplicate handling in auto mode. 'delete' or 'move' to /photos/errors/")
    parser.add_argument("--delete-original", action="store_true", help="Delete original after scrub (auto mode)")
    parser.add_argument("--log-level", choices=["debug", "info", "warn", "error", "crit"], default="info",
                        help="Set log verbosity")
    parser.add_argument("--stable-seconds", type=int,
                        default=int(os.getenv("SCRUBEXIF_STABLE_SECONDS", "120")),
                        help="Only process files whose mtime age ≥ this many seconds (default: 120)")
    parser.add_argument("--state-file", metavar="PATH|disabled", default=None,
                        help=("Override stability state file path. "
                              "Use 'disabled' (or '-', 'none') to force mtime-only. "
                              "If not provided, uses SCRUBEXIF_STATE or auto-detected writable path."))
    parser.add_argument("-v", "--version", action="store_true", help="Show version and license")
    args = parser.parse_args()

    global log
    if args.debug:
        args.log_level = "debug"

    log = setup_logger(args.log_level)

    if log.isEnabledFor(logging.DEBUG):
        log.debug("Debug logging enabled")
        formatted_args = {}
        for key, value in vars(args).items():
            if isinstance(value, Path):
                formatted_args[key] = str(value)
            elif isinstance(value, list):
                formatted_args[key] = [
                    str(v) if isinstance(v, Path) else v for v in value
                ]
            else:
                formatted_args[key] = value
        log.debug("CLI arguments: %s", formatted_args)
        env_snapshot = {name: os.getenv(name) for name in DEBUG_ENV_VARS}
        log.debug("Environment snapshot: %s", env_snapshot)

    # Resolve/override state-file from CLI
    global STATE_FILE, _warned_state_disabled
    if args.state_file is not None:
        choice = str(args.state_file).strip().lower()
        if choice in {"disabled", "none", "-"}:
            STATE_FILE = None
        else:
            candidate = _validate_writable_path(Path(args.state_file))
            if candidate is None:
                log.warning("Requested --state-file %s is not writable; disabling state (mtime-only).", args.state_file)
                STATE_FILE = None
            else:
                STATE_FILE = candidate
    else:
        # Re-evaluate env/defaults in case calling context changed
        STATE_FILE = _resolve_state_path_from_env()

    if args.version:
        show_version()
        sys.exit(0)

    # Emit the *exact* banner lines tests expect
    if STATE_FILE is None:
        print("🔎 [INFO] State path: disabled")
        print("🔎 [INFO] State disabled: using mtime-only stability.")
    else:
        print(f"🔎 [INFO] State path: {STATE_FILE}")

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
                   on_duplicate=args.on_duplicate,
                   stable_seconds=args.stable_seconds)
    else:
        if args.files:
            resolved_files = [resolve_cli_path(f) for f in args.files]
        else:
            resolved_files = [PHOTOS_ROOT]

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
