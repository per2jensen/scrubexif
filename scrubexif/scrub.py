#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Scrub EXIF metadata from JPEG files while retaining selected tags.

Designed for photographers who want to preserve camera details
(exposure, lens, ISO, etc.) but remove private or irrelevant data.
"""

import argparse
import contextlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from .__about__ import __version__

sys.stdout.reconfigure(line_buffering=True)

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
        # Track wall-clock duration of the whole run
        self.started_at = time.time()

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
        duration = time.time() - self.started_at
        print("📊 Summary:")
        print(f"  Total JPEGs found        : {self.total}")
        print(f"  Successfully scrubbed    : {self.scrubbed}")
        print(f"  Skipped (unstable/temp)  : {self.skipped}")
        print(f"  Errors                   : {self.errors}")
        if self.duplicates_deleted:
            print(f"  Duplicates deleted       : {self.duplicates_deleted}")
        if self.duplicates_moved:
            print(f"  Duplicates moved         : {self.duplicates_moved}")
        print(f"  Duration                 : {duration:.2f}s")
        # Machine-parsable one-liner for the bash script
        print(
            "SCRUBEXIF_SUMMARY "
            f"total={self.total} "
            f"scrubbed={self.scrubbed} "
            f"skipped={self.skipped} "
            f"errors={self.errors} "
            f"duplicates_deleted={self.duplicates_deleted} "
            f"duplicates_moved={self.duplicates_moved} "
            f"duration={duration:.3f}"
        )


# ----------------------------
# Fixed container paths
# ----------------------------

PHOTOS_ROOT = Path("/photos")
INPUT_DIR = PHOTOS_ROOT / "input"
OUTPUT_DIR = PHOTOS_ROOT / "output"
PROCESSED_DIR = PHOTOS_ROOT / "processed"
ERRORS_DIR = PHOTOS_ROOT / "errors"


def _unescape_mountinfo(value: str) -> str:
    return (
        value.replace(r"\040", " ")
        .replace(r"\011", "\t")
        .replace(r"\012", "\n")
        .replace(r"\134", "\\")
    )


def _resolve_mount_source(path: Path) -> Optional[str]:
    """
    Best-effort resolve of a bind-mount source path for a mount point.
    Falls back to None if /proc/self/mountinfo is unavailable or unhelpful.
    """
    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as f:
            for line in f:
                if " - " not in line:
                    continue
                pre, post = line.rstrip("\n").split(" - ", 1)
                pre_fields = pre.split()
                if len(pre_fields) < 5:
                    continue
                root = _unescape_mountinfo(pre_fields[3])
                mount_point = _unescape_mountinfo(pre_fields[4])
                if mount_point != str(path):
                    continue
                if root.startswith("/"):
                    return root
                post_fields = post.split()
                if len(post_fields) >= 2 and post_fields[1].startswith("/"):
                    return _unescape_mountinfo(post_fields[1])
    except OSError:
        return None
    return None


SHOW_CONTAINER_PATHS = False


def _format_path_with_host(path: Path) -> str:
    host_root = _resolve_mount_source(PHOTOS_ROOT)
    if not host_root:
        return str(path)
    try:
        rel = path.relative_to(PHOTOS_ROOT)
    except ValueError:
        # path is outside PHOTOS_ROOT (e.g. -o /scrubbed with a separate bind-mount).
        # Try resolving the mount for that path directly so the user sees the host path.
        own_host = _resolve_mount_source(path)
        if own_host:
            if SHOW_CONTAINER_PATHS:
                return f"{path} (host: {own_host})"
            return own_host
        return str(path)
    host_path = Path(host_root) / rel
    if SHOW_CONTAINER_PATHS:
        return f"{path} (host: {host_path})"
    return str(host_path)


def _format_relative_path_with_host(path: Path) -> str:
    host_root = _resolve_mount_source(PHOTOS_ROOT)
    if not host_root:
        return str(path)
    try:
        rel = path.relative_to(PHOTOS_ROOT)
    except ValueError:
        # path is outside PHOTOS_ROOT — resolve its own mount for a meaningful host path.
        own_host = _resolve_mount_source(path)
        if own_host:
            if SHOW_CONTAINER_PATHS:
                return f"{path} (host: {own_host})"
            return own_host
        return str(path)
    host_path = Path(host_root) / rel
    if SHOW_CONTAINER_PATHS:
        return f"{rel} (host: {host_path})"
    return str(host_path)


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

FORBIDDEN_OUTPUT_ROOTS = (
    Path("/usr"),
    Path("/var"),
    Path("/boot"),
    Path("/dev"),
    Path("/etc"),
    Path("/root"),
    Path("/lib"),
    Path("/lib32"),
    Path("/lib64"),
    Path("/libx32"),
    Path("/libexec"),
)


def _is_path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_forbidden_output_create_path(path: Path) -> bool:
    resolved = path.resolve(strict=False)
    for root in FORBIDDEN_OUTPUT_ROOTS:
        if _is_path_within(resolved, root):
            return True
    return False


def resolve_output_dir(raw: Path) -> Path:
    if raw.is_absolute():
        candidate = raw
    else:
        candidate = (PHOTOS_ROOT / raw).resolve(strict=False)
        try:
            candidate.relative_to(PHOTOS_ROOT)
        except ValueError:
            print(f"❌ Output path escapes allowed root {PHOTOS_ROOT}: {raw}", file=sys.stderr)
            sys.exit(1)

    if candidate.is_symlink():
        print(f"❌ Output directory is a symlink (not allowed): {candidate}", file=sys.stderr)
        sys.exit(1)
    if candidate.exists() and not candidate.is_dir():
        print(f"❌ Output path is not a directory: {candidate}", file=sys.stderr)
        sys.exit(1)
    if not candidate.exists() and _is_forbidden_output_create_path(candidate):
        print(
            f"❌ Refusing to create output directory under system path: {candidate}",
            file=sys.stderr
        )
        sys.exit(1)

    return candidate


def check_dir_safety(path: Path, label: str):
    display_path = _format_path_with_host(path)
    if not path.exists():
        print(f"❌ {label} directory does not exist: {display_path}")
        sys.exit(1)
    if not path.is_dir():
        print(f"❌ {label} path is not a directory: {display_path}")
        sys.exit(1)
    if path.is_symlink():
        print(f"❌ {label} is a symbolic link (not allowed): {display_path}")
        sys.exit(1)
    try:
        test_file = path / ".scrubexif_write_test"
        with open(test_file, "w") as f:
            f.write("test")
        test_file.unlink()
    except Exception:
        print(f"❌ {label} directory is not writable: {display_path}")
        sys.exit(1)


def _dirs_same(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except FileNotFoundError:
        return False
    except OSError:
        try:
            return a.resolve() == b.resolve()
        except Exception:
            return False


def guard_auto_mode_dirs(on_duplicate: str):
    pairs = [
        (INPUT_DIR, "input", OUTPUT_DIR, "output"),
        (INPUT_DIR, "input", PROCESSED_DIR, "processed"),
        (OUTPUT_DIR, "output", PROCESSED_DIR, "processed"),
    ]
    if on_duplicate == "move":
        pairs.extend([
            (ERRORS_DIR, "errors", INPUT_DIR, "input"),
            (ERRORS_DIR, "errors", OUTPUT_DIR, "output"),
            (ERRORS_DIR, "errors", PROCESSED_DIR, "processed"),
        ])

    for left, left_label, right, right_label in pairs:
        if _dirs_same(left, right):
            left_path = _format_path_with_host(left)
            right_path = _format_path_with_host(right)
            print(
                f"❌ Auto mode requires distinct directories; {left_label} and {right_label} "
                f"resolve to the same path ({left_path} == {right_path}).",
                file=sys.stderr,
            )
            sys.exit(1)


# ----------------------------
# Stability state management
# ----------------------------

def _resolve_state_path_from_env() -> Optional[Path]:
    """
    Priority (when no CLI override is provided):
      1) SCRUBEXIF_STATE env
      2) If no env: /photos/.scrubexif_state.json if writable
      3) If no env: /tmp/.scrubexif_state.json if writable
      4) None => state disabled, mtime-only
    """
    env = os.getenv("SCRUBEXIF_STATE")
    if env:
        env_path = Path(env)
        candidate = _validate_writable_path(env_path)
        if candidate:
            return candidate
        log.warning("SCRUBEXIF_STATE=%s is not writable; disabling state (mtime-only).", env_path)
        return None

    for p in (Path("/photos/.scrubexif_state.json"), Path("/tmp/.scrubexif_state.json")):
        candidate = _validate_writable_path(p)
        if candidate:
            log.info("State path auto-selected: %s", candidate)
            return candidate

    log.warning("No writable state path found; disabling state (mtime-only).")
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

# Camera tags to extract from the source JPEG and restore after stripping.
# ImageSize is a composite tag derived from the JPEG SOF segment, which
# jpegtran preserves intact — no need to round-trip it through EXIF.
TAGS_TO_EXTRACT: list[str] = [
    "ExposureTime",
    "FNumber",
    "FocalLength",
    "ISO",
    "Orientation",
]

# Conservative limits (UTF-8 bytes) to avoid bloated EXIF/XMP segments.
MAX_COPYRIGHT_BYTES = 1024
MAX_COMMENT_BYTES = 4096


def _truncate_utf8(label: str, value: str, max_bytes: int) -> str:
    """
    Truncate a string to at most max_bytes UTF-8 bytes.

    Args:
        label: Human-readable name used in the warning log message.
        value: String to truncate.
        max_bytes: Maximum byte length of the result.

    Returns:
        The original string if it fits, otherwise a valid UTF-8 truncation.
    """
    data = value.encode("utf-8")
    if len(data) <= max_bytes:
        return value
    log.warning(
        "%s too long (%d bytes); truncating to %d bytes.",
        label,
        len(data),
        max_bytes,
    )
    truncated = data[:max_bytes]
    # Trim to a valid UTF-8 boundary.
    while truncated and (truncated[-1] & 0xC0) == 0x80:
        truncated = truncated[:-1]
    return truncated.decode("utf-8", errors="ignore")


def build_stamp_args(copyright_text: str | None,
                     comment_text: str | None) -> list[str]:
    """
    Build exiftool arguments to stamp copyright and/or comment into a JPEG.

    Args:
        copyright_text: Copyright notice, or None to skip.
        comment_text: Comment string, or None to skip.

    Returns:
        List of exiftool tag-assignment arguments.
    """
    args: list[str] = []
    if copyright_text is not None:
        value = _truncate_utf8("Copyright notice", copyright_text, MAX_COPYRIGHT_BYTES)
        args.append(f"-EXIF:Copyright={value}")
        args.append(f"-XMP-dc:Rights={value}")
    if comment_text is not None:
        value = _truncate_utf8("Comment", comment_text, MAX_COMMENT_BYTES)
        args.append(f"-EXIF:UserComment={value}")
        args.append(f"-XMP-dc:Description={value}")
    return args


# ----------------------------
# jpegtran-based pipeline
# ----------------------------

def check_jpegtran() -> None:
    """
    Verify that jpegtran is available on PATH.

    Exits with a clear error message if not found.
    Install via: apt-get install libjpeg-turbo-progs
    """
    if not shutil.which("jpegtran"):
        print(
            "❌ jpegtran not found on PATH. "
            "Install libjpeg-turbo-progs (Debian/Ubuntu) or equivalent.",
            file=sys.stderr,
        )
        sys.exit(1)


def extract_wanted_tags(input_path: Path) -> dict[str, object]:
    """
    Extract the whitelist of EXIF tag values from a JPEG.

    Uses exiftool with -n to obtain raw numeric values suitable for
    round-tripping back into a clean JPEG via explicit tag assignments.
    Tags absent from the source are silently omitted from the result.

    Args:
        input_path: Path to the source JPEG.

    Returns:
        Dict mapping tag name to raw value (str, int, or float).

    Raises:
        RuntimeError: If exiftool exits non-zero.
    """
    tag_args = [f"-{tag}" for tag in TAGS_TO_EXTRACT]
    cmd = ["exiftool", "-j", "-n"] + tag_args + [str(input_path.absolute())]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"exiftool tag extraction failed: {result.stderr.strip()}"
        )
    data = json.loads(result.stdout)
    if not data:
        return {}
    return {k: v for k, v in data[0].items() if k != "SourceFile"}


def extract_icc_profile(input_path: Path, icc_path: Path) -> bool:
    """
    Extract the ICC colour profile from a JPEG to a binary file.

    Args:
        input_path: Source JPEG.
        icc_path: Destination path for the raw ICC profile bytes.

    Returns:
        True if an ICC profile was found and written; False if none present.

    Raises:
        RuntimeError: If exiftool fails or the output file cannot be written.
    """
    cmd = ["exiftool", "-b", "-ICC_Profile", str(input_path.absolute())]
    try:
        with open(icc_path, "wb") as f:
            result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)
    except OSError as e:
        raise RuntimeError(
            f"Failed to write ICC profile to {icc_path}: {e}"
        ) from e
    if result.returncode != 0:
        icc_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"exiftool ICC extraction failed: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    if not icc_path.exists() or icc_path.stat().st_size == 0:
        icc_path.unlink(missing_ok=True)
        return False
    return True


def run_jpegtran(input_path: Path, output_path: Path) -> None:
    """
    Strip all JPEG APP segments with jpegtran -copy none.

    Removes all metadata (EXIF, XMP, IPTC, ICC profile, and any unknown
    proprietary APP segments) while preserving the image data losslessly.
    The JPEG SOF segment — which carries image dimensions — is retained.

    Args:
        input_path: Source JPEG (not modified).
        output_path: Destination path for the stripped JPEG.

    Raises:
        RuntimeError: If jpegtran exits non-zero or produces no output.
    """
    cmd = [
        "jpegtran", "-copy", "none",
        "-outfile", str(output_path.absolute()),
        str(input_path.absolute()),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        raise RuntimeError(f"Failed to run jpegtran: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"jpegtran failed: {result.stderr.strip() or 'unknown error'}"
        )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("jpegtran produced no output file")


def build_tag_writeback_cmd(
    output_path: Path,
    tags: dict[str, object],
    icc_path: Optional[Path],
    copyright_text: Optional[str],
    comment_text: Optional[str],
) -> list[str]:
    """
    Build an exiftool command to write back preserved tags and ICC profile.

    The command modifies the file at output_path in-place using
    -overwrite_original.

    Args:
        output_path: JPEG to write into.
        tags: Dict of tag name to raw value as returned by extract_wanted_tags.
        icc_path: Path to a raw ICC profile binary, or None to skip.
        copyright_text: Optional copyright string to stamp.
        comment_text: Optional comment string to stamp.

    Returns:
        argv list ready for subprocess.run.
    """
    # -n: write raw numeric values; without it exiftool mis-applies inverse
    # print-conversion on integer tags (e.g. Orientation=1 stores as 3).
    cmd = ["exiftool", "-overwrite_original", "-P", "-m", "-n"]
    if icc_path is not None:
        cmd.append(f"-icc_profile<={icc_path.absolute()}")
    for tag, value in tags.items():
        if value is None:
            continue
        cmd.append(f"-EXIF:{tag}={value}")
    cmd += build_stamp_args(copyright_text, comment_text)
    cmd.append(str(output_path.absolute()))
    return cmd


def _do_scrub_pipeline(
    input_path: Path,
    output_path: Path,
    paranoia: bool,
    copyright_text: Optional[str],
    comment_text: Optional[str],
) -> None:
    """
    Core scrub pipeline — shared by scrub_file and preview mode.

    Paranoia mode:
        jpegtran -copy none only.  Zero metadata in the output.

    Normal mode (three steps):
        1. exiftool extracts the tag whitelist and ICC profile from the source.
        2. jpegtran -copy none strips all APP segments.
        3. exiftool writes the whitelist tags and ICC profile back.

    Args:
        input_path: Source JPEG (never modified).
        output_path: Destination for the scrubbed JPEG.
        paranoia: True for zero-metadata output.
        copyright_text: Copyright notice to stamp (normal mode only).
        comment_text: Comment to stamp (normal mode only).

    Raises:
        RuntimeError: On any subprocess failure.
    """
    if paranoia:
        run_jpegtran(input_path, output_path)
        return

    # Step 1 — extract tag values and ICC profile from the original.
    tags = extract_wanted_tags(input_path)
    log.debug("Extracted tags from %s: %s", input_path.name, tags)

    icc_fd, icc_tmp_str = tempfile.mkstemp(
        suffix=".icc", dir=output_path.parent, prefix=".scrubexif_icc_"
    )
    os.close(icc_fd)
    icc_tmp = Path(icc_tmp_str)

    try:
        has_icc = extract_icc_profile(input_path, icc_tmp)
        if not has_icc:
            icc_tmp.unlink(missing_ok=True)
            icc_tmp = None
            log.debug("No ICC profile found in %s", input_path.name)

        # Step 2 — strip everything with jpegtran.
        run_jpegtran(input_path, output_path)

        # Step 3 — write back the whitelist (skip if nothing to restore).
        if tags or icc_tmp or copyright_text or comment_text:
            writeback_cmd = build_tag_writeback_cmd(
                output_path, tags, icc_tmp, copyright_text, comment_text
            )
            if log.isEnabledFor(logging.DEBUG):
                log.debug("Tag write-back command: %s", " ".join(writeback_cmd))
            wb_result = subprocess.run(
                writeback_cmd,
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            if wb_result.returncode != 0:
                raise RuntimeError(
                    f"exiftool write-back failed: {wb_result.stderr.strip()}"
                )
    finally:
        if icc_tmp is not None:
            icc_tmp.unlink(missing_ok=True)


def print_tags(file: Path, label: str = ""):
    try:
        result = subprocess.run(
            ["exiftool", "-a", "-G1", "-s", str(file.absolute())],   # security advice on https://exiftool.org/
            capture_output=True, text=True
        )
        print(f"\n📸 Tags {label} {_format_path_with_host(file)}:")
        print(result.stdout.strip())
    except Exception as e:
        print(f"❌ Failed to read tags: {e}")


# ----------------------------
# Temp output handling
# ----------------------------

def _create_temp_output(dir_path: Path, suffix: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        name = f".scrubexif_tmp_{uuid.uuid4().hex}{suffix}"
        candidate = dir_path / name
        if not candidate.exists():
            return candidate
    raise RuntimeError("Failed to generate unique temp output path")


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
    copyright_text: str | None = None,
    comment_text: str | None = None,
) -> ScrubResult:
    print(f"scrub_file: input={_format_path_with_host(input_path)}, output={_format_path_with_host(output_path) if output_path else None}")
    output_file = output_path / input_path.name if output_path else input_path
    print("Output file will be:", _format_path_with_host(output_file))

    if output_path and output_file.is_symlink():
        msg = f"Destination is a symlink; refusing to scrub into {_format_path_with_host(output_file)}"
        print(f"❌ {msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="error",
            error_message=msg,
        )

    # duplicates
    if output_file.exists() and input_path.resolve() != output_file.resolve():
        print(
            "⚠️ Duplicate logic triggered: "
            f"input={_format_path_with_host(input_path)}, "
            f"output={_format_path_with_host(output_file)}"
        )

        if dry_run:
            print(f"🚫 [dry-run] Would detect duplicate: {_format_path_with_host(output_file)}")
            return ScrubResult(input_path, output_file, status="duplicate")

        if on_duplicate == "delete":
            print(f"🗑️  Duplicate detected — deleting {_format_path_with_host(input_path)}")
            input_path.unlink(missing_ok=True)
            return ScrubResult(input_path, output_file, status="duplicate")

        elif on_duplicate == "move":
            target = ERRORS_DIR / input_path.name
            count = 1
            while target.exists():
                target = ERRORS_DIR / f"{input_path.stem}_{count}{input_path.suffix}"
                count += 1
            shutil.move(input_path, target)
            print(f"📦 Moved duplicate to: {_format_path_with_host(target)}")
            return ScrubResult(input_path, output_file, status="duplicate", duplicate_path=target)

    # dry-run
    if dry_run:
        if show_tags_mode in {"before", "both"}:
            print_tags(input_path, label="before")
        if show_tags_mode in {"after", "both"}:
            print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
        print(f"🔍 Dry run: would scrub {_format_path_with_host(input_path)}")
        return ScrubResult(input_path, output_file, status="scrubbed")

    # exiftool command
    in_place = output_path is None or input_path.resolve() == output_path.resolve()
    try:
        temp_output = _create_temp_output(
            input_path.parent if in_place else output_file.parent,
            input_path.suffix,
        )
    except Exception as exc:
        err_msg = str(exc)
        print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="error",
            error_message=err_msg
        )
    if show_tags_mode in {"before", "both"}:
        print_tags(input_path, label="before")

    try:
        _do_scrub_pipeline(
            input_path,
            temp_output,
            paranoia=paranoia,
            copyright_text=copyright_text,
            comment_text=comment_text,
        )
    except RuntimeError as exc:
        temp_output.unlink(missing_ok=True)
        err_msg = str(exc)
        print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="error",
            error_message=err_msg,
        )

    if not temp_output.exists():
        err_msg = "Temp output missing after scrub"
        print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="error",
            error_message=err_msg
        )

    try:
        if in_place:
            os.replace(temp_output, input_path)
        else:
            if output_file.exists() and input_path.resolve() != output_file.resolve():
                temp_output.unlink(missing_ok=True)
                err_msg = "Output file appeared during scrub; refusing to overwrite"
                print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
                return ScrubResult(
                    input_path=input_path,
                    output_path=output_file,
                    status="error",
                    error_message=err_msg
                )
            os.replace(temp_output, output_file)
    except Exception as exc:
        temp_output.unlink(missing_ok=True)
        err_msg = str(exc)
        print(f"❌ Failed to scrub {_format_path_with_host(input_path)}: {err_msg}")
        return ScrubResult(
            input_path=input_path,
            output_path=output_file,
            status="error",
            error_message=err_msg
        )

    if show_tags_mode in {"after", "both"}:
        print_tags(output_file, label="after")

    def display_path(path: Path) -> str:
        return _format_relative_path_with_host(path)

    print(f"✅ Saved scrubbed file to {display_path(output_file)}")

    if delete_original and not in_place and input_path.exists():
        input_path.unlink()
        print(f"❌ Deleted original: {_format_path_with_host(input_path)}")

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
    if log.isEnabledFor(logging.DEBUG):
        log.debug(f"JPEGs in '{dir_path}', # of files: '{len(results)}', results: {results}")
    return results


def auto_scrub(summary: ScrubSummary, dry_run=False, delete_original=False,
               show_tags_mode: str | None = None,
               paranoia: bool = True,
               max_files: int | None = None,
               on_duplicate: str = "delete",
               stable_seconds: int = 120,
               copyright_text: str | None = None,
               comment_text: str | None = None) -> ScrubSummary:
    print(f"🚀 Auto mode: Scrubbing JPEGs in {_format_path_with_host(INPUT_DIR)}")
    print(f"📁 Output directory: {_format_path_with_host(OUTPUT_DIR)}")
    print(f"📁 Processed directory: {_format_path_with_host(PROCESSED_DIR)}")
    if on_duplicate == "move":
        print(f"📁 Errors directory: {_format_path_with_host(ERRORS_DIR)}")
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
            print(f"🔍 Would scrub: {_format_path_with_host(file)}")
            summary.total += 1
            continue

        result = scrub_file(
            file,
            OUTPUT_DIR,
            delete_original=delete_original,
            show_tags_mode=show_tags_mode,
            paranoia=paranoia,
            on_duplicate=on_duplicate,
            copyright_text=copyright_text,
            comment_text=comment_text,
        )

        summary.update(result)
        dst_processed = PROCESSED_DIR / file.name

        if result.status == "scrubbed" and not delete_original:
            moved = False
            if file.exists():
                if dst_processed.is_symlink():
                    print(f"⚠️ Skipping move: destination is a symlink ({_format_path_with_host(dst_processed)})")
                elif file.resolve() != dst_processed.resolve():
                    shutil.move(file, dst_processed)
                    moved = True
                else:
                    print("⚠️ Skipping move: source and destination are the same")
            else:
                print(f"⚠️ Skipping move: source file no longer exists ({_format_path_with_host(file)})")
            if moved:
                print(f"📦 Moved original to {_format_path_with_host(dst_processed)}")
        elif result.status == "error":
            if file.exists():
                if dst_processed.is_symlink():
                    print(f"⚠️ Scrub failed; destination is a symlink ({_format_path_with_host(dst_processed)}), leaving original in place")
                else:
                    try:
                        shutil.move(file, dst_processed)
                        print(
                            f"⚠️ Scrub failed for {_format_path_with_host(file)}; "
                            f"moved original to {_format_path_with_host(dst_processed)} for inspection"
                        )
                    except Exception as exc:
                        print(f"⚠️ Scrub failed for {_format_path_with_host(file)}; unable to move original: {exc}")
            else:
                print(f"⚠️ Scrub failed and source already missing: {_format_path_with_host(file)}")

        mark_seen(file, state)

    save_state(state)
    return summary


def simple_scrub(summary: ScrubSummary,
                 recursive: bool = False,
                 dry_run: bool = False,
                 show_tags_mode: str | None = None,
                 paranoia: bool = True,
                 max_files: int | None = None,
                 on_duplicate: str = "delete",
                 output_explicit: bool = False,
                 copyright_text: str | None = None,
                 comment_text: str | None = None) -> ScrubSummary:
    """
    Default safe mode:
      - Scan /photos for JPEGs (non-recursive by default, -r respected)
      - Write scrubbed copies to /photos/output
      - Leave originals in place.

    Intended for the "one-liner" use case:

        docker run --rm -v "$PWD:/photos" per2jensen/scrubexif:0.7.10

    Args:
        output_explicit: True when the caller supplied -o on the CLI. A
            pre-existing output directory is accepted in that case because the
            user stated intent (e.g. via a bind-mount). When False (default)
            a pre-existing directory is refused to prevent accidental clobbering.
    """
    host_root = _resolve_mount_source(PHOTOS_ROOT)
    print(f"🚀 Default safe mode: Scrubbing JPEGs in {_format_path_with_host(PHOTOS_ROOT)}")
    print(f"📁 Output directory: {_format_path_with_host(OUTPUT_DIR)}")

    # Safety: /photos must exist and be usable
    check_dir_safety(PHOTOS_ROOT, "Photos root")

    if OUTPUT_DIR.exists():
        if not output_explicit:
            print(f"⚠️ Output directory already exists: {_format_path_with_host(OUTPUT_DIR)}")
            print("⚠️ Refusing to run in default safe mode. Remove it or use --clean-inline/--from-input.")
            sys.exit(1)
        # output_explicit=True: user passed -o, pre-existing directory is intentional
    else:
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=False)
        except Exception as exc:
            print(f"❌ Failed to create output directory {_format_path_with_host(OUTPUT_DIR)}: {exc}")
            sys.exit(1)

    check_dir_safety(OUTPUT_DIR, "Output")

    files = find_jpegs_in_dir(PHOTOS_ROOT, recursive=recursive)

    # Avoid feeding our own pipeline directories back into the scrub loop
    filtered: list[Path] = []
    for f in files:
        # Skip symlinks aggressively
        if f.is_symlink():
            log.debug("Skipping symlink in default safe mode: %s", f)
            continue

        skip = False
        for special in (INPUT_DIR, OUTPUT_DIR, PROCESSED_DIR, ERRORS_DIR):
            try:
                f.relative_to(special)
                skip = True
                break
            except ValueError:
                continue
        if skip:
            continue

        filtered.append(f)

    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "Default safe mode: found %d JPEGs under %s (recursive=%s, after filtering=%d)",
            len(files),
            PHOTOS_ROOT,
            recursive,
            len(filtered),
        )

    if not filtered:
        print("⚠️ No eligible JPEGs found in default safe mode.")
        return summary

    if max_files is not None:
        filtered = filtered[:max_files]

    for f in filtered:
        dst = OUTPUT_DIR / f.name

        if dry_run:
            if show_tags_mode in {"before", "both"}:
                print_tags(f, label="before")
            if show_tags_mode in {"after", "both"}:
                print("⚠️  Cannot show tags *after* scrub in dry-run mode (no scrub performed).")
            print(f"🔍 [default] Would scrub: {_format_path_with_host(f)} -> {_format_path_with_host(dst)}")
            summary.total += 1
            continue

        result = scrub_file(
            f,
            output_path=OUTPUT_DIR,
            delete_original=False,
            dry_run=False,
            show_tags_mode=show_tags_mode,
            paranoia=paranoia,
            on_duplicate=on_duplicate,
            copyright_text=copyright_text,
            comment_text=comment_text,
        )
        summary.update(result)

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
                 preview: bool = False,
                 copyright_text: str | None = None,
                 comment_text: str | None = None) -> ScrubSummary:
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
        preview_fd, preview_tmp_str = tempfile.mkstemp(suffix=".jpg")
        os.close(preview_fd)
        preview_input = Path(preview_tmp_str)
        shutil.copy(f, preview_input)
        preview_output = preview_input.with_suffix(".scrubbed.jpg")

        try:
            _do_scrub_pipeline(
                preview_input,
                preview_output,
                paranoia=paranoia,
                copyright_text=copyright_text,
                comment_text=comment_text,
            )
            if show_tags_mode in {"before", "both"}:
                print_tags(f, label="before")
            print_tags(preview_output, label="after")
        except RuntimeError as exc:
            print(f"❌ Preview scrub failed: {exc}")
        finally:
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
            print(f"🔍 Would scrub: {_format_path_with_host(f)}")
            summary.total += 1
            continue

        result = scrub_file(f,
                            output_path=None,
                            delete_original=False,
                            dry_run=False,
                            show_tags_mode=show_tags_mode,
                            paranoia=paranoia,
                            on_duplicate=None,
                            copyright_text=copyright_text,
                            comment_text=comment_text)

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

def _run(args: argparse.Namespace) -> int:
    if args.quiet:
        args.log_level = "crit"
        args.debug = False
        with contextlib.redirect_stdout(io.StringIO()) as stdout_buffer:
            try:
                return _run_inner(args)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                if code != 0:
                    sys.stderr.write(stdout_buffer.getvalue())
                return code if isinstance(code, int) else 1
            except Exception:
                sys.stderr.write(stdout_buffer.getvalue())
                raise
    return _run_inner(args)


def _run_inner(args: argparse.Namespace) -> int:
    require_force_for_root()
    global log
    global OUTPUT_DIR
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
    global SHOW_CONTAINER_PATHS
    SHOW_CONTAINER_PATHS = args.show_container_paths
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

    check_jpegtran()

    # --paranoia removes all metadata; --copyright and --comment are incompatible.
    if args.paranoia and (args.copyright or args.comment):
        print(
            "❌ --paranoia removes all metadata. "
            "--copyright and --comment cannot be combined with --paranoia.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Mode sanity checks
    if args.clean_inline and args.from_input:
        print("❌ --clean-inline and --from-input cannot be used together.", file=sys.stderr)
        sys.exit(1)
    if args.files and not args.clean_inline:
        print("❌ Positional file or directory arguments require --clean-inline.", file=sys.stderr)
        sys.exit(1)
    if args.output and args.clean_inline:
        print("❌ --output cannot be used with --clean-inline.", file=sys.stderr)
        sys.exit(1)
    if args.output and args.from_input:
        print("❌ --output cannot be used with --from-input.", file=sys.stderr)
        sys.exit(1)

    if args.output:
        OUTPUT_DIR = resolve_output_dir(args.output)

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
            print(f"❌ Failed to create errors directory: {_format_path_with_host(ERRORS_DIR)}\n{e}", file=sys.stderr)
            sys.exit(1)

    if args.preview:
        args.dry_run = True
        args.show_tags = "both"
        args.max_files = 1

    if args.from_input:
        guard_auto_mode_dirs(args.on_duplicate)
        auto_scrub(
            summary=summary,
            dry_run=args.dry_run,
            delete_original=args.delete_original,
            show_tags_mode=args.show_tags,
            paranoia=args.paranoia,
            max_files=args.max_files,
            on_duplicate=args.on_duplicate,
            stable_seconds=args.stable_seconds,
            copyright_text=args.copyright,
            comment_text=args.comment,
        )
    elif args.clean_inline:
        if args.files:
            resolved_files = [resolve_cli_path(f) for f in args.files]
        else:
            resolved_files = [PHOTOS_ROOT]

        manual_scrub(
            resolved_files,
            summary=summary,
            recursive=args.recursive,
            dry_run=args.dry_run,
            show_tags_mode=args.show_tags,
            paranoia=args.paranoia,
            max_files=args.max_files,
            preview=args.preview,
            copyright_text=args.copyright,
            comment_text=args.comment,
        )
    else:
        simple_scrub(
            summary=summary,
            recursive=args.recursive,
            dry_run=args.dry_run,
            show_tags_mode=args.show_tags,
            paranoia=args.paranoia,
            max_files=args.max_files,
            on_duplicate=args.on_duplicate,
            output_explicit=bool(args.output),
            copyright_text=args.copyright,
            comment_text=args.comment,
        )

    summary.print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrub EXIF metadata from JPEGs.")
    parser.add_argument("files", nargs="*", type=Path, help="Files or directories")
    parser.add_argument("--from-input", action="store_true", help="Use auto mode")
    parser.add_argument("--clean-inline", action="store_true",
                        help="Scrub (destructive) in-place. This flag is required to modify originals.")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
    parser.add_argument("-r", "--recursive", action="store_true", help="Recurse into directories")
    parser.add_argument("--show-tags", choices=["before", "after", "both"], help="Show metadata before/after")
    parser.add_argument("--paranoia", action="store_true",
                        help="Maximum scrubbing: jpegtran -copy none only — zero metadata output. "
                             "Incompatible with --copyright and --comment.")
    parser.add_argument("--preview", action="store_true",
                        help="Preview scrub effect on one file without modifying it")
    parser.add_argument("--show-container-paths", action="store_true",
                        help="Include container paths alongside host paths in output")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress all output on success")
    parser.add_argument("--max-files", type=int, metavar="N",
                        help="Limit number of files to scrub")
    parser.add_argument("--dry-run", action="store_true", help="List actions without performing them")
    parser.add_argument("--on-duplicate", choices=["delete", "move"],
                        default=os.getenv("SCRUBEXIF_ON_DUPLICATE", "delete"),
                        help="Duplicate handling in auto/default modes. 'delete' or 'move' to /photos/errors/")
    parser.add_argument("--delete-original", action="store_true", help="Delete original after scrub (auto mode)")
    parser.add_argument("--copyright", metavar="TEXT",
                        help="Stamp a copyright notice into EXIF and XMP metadata")
    parser.add_argument("--comment", metavar="TEXT",
                        help="Stamp a comment into EXIF and XMP metadata")
    parser.add_argument("--log-level", choices=["debug", "info", "warn", "error", "crit"], default="info",
                        help="Set log verbosity")
    parser.add_argument("--stable-seconds", type=int,
                        default=int(os.getenv("SCRUBEXIF_STABLE_SECONDS", "120")),
                        help="Only process files whose mtime age ≥ this many seconds (default: 120)")
    parser.add_argument("--state-file", metavar="PATH|disabled", default=None,
                        help=("Override stability state file path. "
                              "Use 'disabled' (or '-', 'none') to force mtime-only. "
                              "If not provided, uses SCRUBEXIF_STATE or auto-detected writable path."))
    parser.add_argument("-o", "--output", type=Path,
                        help="Write scrubbed files to this directory (default safe mode)")
    parser.add_argument("-v", "--version", action="store_true", help="Show version and license")
    args = parser.parse_args(argv)

    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
