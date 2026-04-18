"""
Filename renaming subsystem for scrubexif.

Provides format-string-based output filename generation with privacy-safe
token expansion. All logic is isolated here to keep scrub.py focused.
"""

import json
import logging
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_PREFIX_LEN = 16
_MAX_POSTFIX_LEN = 16
_MAX_RANDOM_LEN = 32
_MAX_COUNTER_DIGITS = 12
_MAX_TOTAL_LEN = 64
_DEFAULT_RANDOM_LEN = 8
_DEFAULT_COUNTER_DIGITS = 4

# Tokens that are explicitly forbidden for privacy reasons.
_FORBIDDEN_TOKENS = {"d", "H", "M", "S"}

# Tokens that are allowed.
_ALLOWED_TOKENS = {"r", "u", "n", "Y", "m", "%"}

# Allowed literal characters in format strings (non-token portions).
_ALLOWED_LITERAL_RE = re.compile(r"^[A-Za-z0-9 \-_]*$")

# Token pattern: %<letter> optionally followed by digits.
_TOKEN_RE = re.compile(r"%([A-Za-z%])(\d*)")

# Expected format from exiftool: 'YYYY:MM:DD HH:MM:SS'
_DATETIME_ORIGINAL_RE = re.compile(r"^(\d{4}):(\d{2}):\d{2} \d{2}:\d{2}:\d{2}$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_tokens(fmt: str) -> list[tuple[str, str, int, int]]:
    """
    Parse a format string into a list of (kind, raw, start, end) tuples.

    kind is one of: 'token', 'literal'
    raw is the matched text.

    Raises:
        SystemExit: On a malformed trailing % with no following character.
    """
    parts: list[tuple[str, str, int, int]] = []
    pos = 0
    while pos < len(fmt):
        if fmt[pos] == "%":
            if pos + 1 >= len(fmt):
                print(
                    "❌ --rename format string ends with a bare '%' — malformed token.",
                    flush=True,
                )
                raise SystemExit(1)
            # Consume %<letter><optional digits>
            m = _TOKEN_RE.match(fmt, pos)
            if m:
                parts.append(("token", m.group(0), m.start(), m.end()))
                pos = m.end()
            else:
                # % followed by something that doesn't match — caught later
                parts.append(("literal", fmt[pos], pos, pos + 1))
                pos += 1
        else:
            # Accumulate a run of non-% characters as one literal segment.
            start = pos
            while pos < len(fmt) and fmt[pos] != "%":
                pos += 1
            parts.append(("literal", fmt[start:pos], start, pos))
    return parts


def _token_letter(raw: str) -> str:
    """Return the single letter from a token like '%r8' -> 'r'."""
    return raw[1]


def _token_digits(raw: str) -> Optional[int]:
    """Return the numeric suffix from a token like '%r8' -> 8, or None."""
    suffix = raw[2:]
    return int(suffix) if suffix else None


def _worst_case_expansion(raw: str) -> int:
    """
    Return the maximum number of characters a token can produce.
    Used to validate total length at validation time.
    """
    letter = _token_letter(raw)
    if letter == "r":
        n = _token_digits(raw)
        return n if n is not None else _DEFAULT_RANDOM_LEN
    if letter == "u":
        return 36  # UUID v4 with hyphens
    if letter == "n":
        n = _token_digits(raw)
        return n if n is not None else _DEFAULT_COUNTER_DIGITS
    if letter == "Y":
        return 4
    if letter == "m":
        return 2
    if letter == "%":
        return 1
    return 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_rename_format(fmt: str) -> None:
    """
    Validate a --rename format string.

    Raises SystemExit with a clear message on the first violation found.
    Validation order follows the spec (Section 5 / Section 10.7) exactly.

    Args:
        fmt: The format string to validate.

    Raises:
        SystemExit: On any validation failure.
    """
    if not fmt:
        print("❌ --rename format string is empty — at least one token is required.", flush=True)
        raise SystemExit(1)

    parts = _parse_tokens(fmt)
    tokens = [p for p in parts if p[0] == "token"]
    literals = [p for p in parts if p[0] == "literal"]

    # Step 1: Forbidden tokens (%d %H %M %S).
    for _, raw, _, _ in tokens:
        letter = _token_letter(raw)
        if letter in _FORBIDDEN_TOKENS:
            reason = {
                "d": "day of month reveals capture date — too close to a full timestamp",
                "H": "hour directly defeats the purpose of timestamp scrubbing",
                "M": "minute directly defeats the purpose of timestamp scrubbing",
                "S": "second directly defeats the purpose of timestamp scrubbing",
            }[letter]
            print(
                f"❌ --rename token '%{letter}' is not allowed: {reason}.",
                flush=True,
            )
            raise SystemExit(1)

    # Step 2: Unknown tokens.
    for _, raw, _, _ in tokens:
        letter = _token_letter(raw)
        if letter not in _ALLOWED_TOKENS:
            print(
                f"❌ --rename token '{raw}' is not recognised. "
                "Allowed tokens: %r, %u, %n, %Y, %m, %%.",
                flush=True,
            )
            raise SystemExit(1)

    # Steps 3 & 4: Prefix and postfix literal character counts.
    # Prefix = literal chars before the first non-%% token.
    # Postfix = literal chars after the last non-%% token.
    meaningful_tokens = [p for p in tokens if _token_letter(p[1]) != "%"]

    if meaningful_tokens:
        first_token_start = meaningful_tokens[0][2]
        last_token_end = meaningful_tokens[-1][3]

        prefix_chars = sum(
            len(raw) for kind, raw, start, end in parts
            if kind == "literal" and end <= first_token_start
        )
        postfix_chars = sum(
            len(raw) for kind, raw, start, end in parts
            if kind == "literal" and start >= last_token_end
        )

        if prefix_chars > _MAX_PREFIX_LEN:
            print(
                f"❌ --rename prefix is {prefix_chars} characters; maximum is {_MAX_PREFIX_LEN}.",
                flush=True,
            )
            raise SystemExit(1)

        if postfix_chars > _MAX_POSTFIX_LEN:
            print(
                f"❌ --rename postfix is {postfix_chars} characters; maximum is {_MAX_POSTFIX_LEN}.",
                flush=True,
            )
            raise SystemExit(1)
    else:
        # Format string has no meaningful tokens — only literals and/or %%.
        # Count the full literal text as prefix for limit purposes.
        total_literal = sum(len(raw) for kind, raw, _, _ in literals)
        if total_literal > _MAX_PREFIX_LEN:
            print(
                f"❌ --rename format has no output tokens and {total_literal} literal "
                f"characters; maximum prefix is {_MAX_PREFIX_LEN}.",
                flush=True,
            )
            raise SystemExit(1)

    # Step 5: %r configured length.
    for _, raw, _, _ in tokens:
        if _token_letter(raw) == "r":
            n = _token_digits(raw)
            if n is not None and n > _MAX_RANDOM_LEN:
                print(
                    f"❌ --rename token '{raw}' requests {n} hex characters; "
                    f"maximum is {_MAX_RANDOM_LEN}.",
                    flush=True,
                )
                raise SystemExit(1)

    # Step 6: %n configured digits.
    for _, raw, _, _ in tokens:
        if _token_letter(raw) == "n":
            n = _token_digits(raw)
            if n is not None and n > _MAX_COUNTER_DIGITS:
                print(
                    f"❌ --rename token '{raw}' requests {n} digits; "
                    f"maximum is {_MAX_COUNTER_DIGITS}.",
                    flush=True,
                )
                raise SystemExit(1)

    # Step 7: No dot anywhere in literal text — checked before the general
    # whitelist so the user gets a specific "extension confusion" message
    # rather than the generic "disallowed character" message.
    for _, raw, _, _ in literals:
        if "." in raw:
            print(
                "❌ --rename format string must not contain '.' — "
                "it risks confusion with the file extension.",
                flush=True,
            )
            raise SystemExit(1)

    # Step 8: Whitelist check on literal characters.
    for _, raw, _, _ in literals:
        if not _ALLOWED_LITERAL_RE.match(raw):
            for ch in raw:
                if not re.match(r"[A-Za-z0-9 \-_]", ch):
                    print(
                        f"❌ --rename format contains disallowed character {ch!r}. "
                        "Only letters, digits, space, hyphen, and underscore are allowed in literal text.",
                        flush=True,
                    )
                    raise SystemExit(1)

    # Step 9: Total expanded length (worst-case).
    token_expansion = sum(_worst_case_expansion(raw) for _, raw, _, _ in tokens)
    literal_len = sum(len(raw) for _, raw, _, _ in literals)
    total = token_expansion + literal_len
    if total > _MAX_TOTAL_LEN:
        print(
            f"❌ --rename format expands to at most {total} characters; "
            f"maximum total filename length (before extension) is {_MAX_TOTAL_LEN}.",
            flush=True,
        )
        raise SystemExit(1)


def _read_datetime_original(input_path: Path) -> Optional[str]:
    """
    Read DateTimeOriginal from a file via exiftool.

    Args:
        input_path: Path to the image file.

    Returns:
        DateTimeOriginal as 'YYYY:MM:DD HH:MM:SS', or None if absent/unreadable.
    """
    try:
        result = subprocess.run(
            ["exiftool", "-j", "-DateTimeOriginal", str(input_path.absolute())],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        log.error("exiftool not found — cannot read DateTimeOriginal for rename.")
        return None
    except OSError as e:
        log.error("Failed to run exiftool on %s: %s", input_path, e)
        return None

    if result.returncode != 0:
        log.debug("exiftool returned non-zero for %s: %s", input_path, result.stderr)
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        log.warning("Could not parse exiftool JSON for %s: %s", input_path, e)
        return None

    if not data:
        return None

    return data[0].get("DateTimeOriginal")


def resolve_rename(
    fmt: str,
    input_path: Path,
    counter: dict[str, int],
) -> str:
    """
    Expand a validated format string into a filename stem (no extension).

    If the format contains %Y or %m and the file has no EXIF DateTimeOriginal,
    the stem falls back to a UUID v4 and a warning is emitted.

    The counter dict must have key 'n' with an integer value. It is mutated
    in place: each %n token increments counter['n'] by 1.

    Args:
        fmt: A validated format string (must have passed validate_rename_format).
        input_path: The source image file.
        counter: Mutable dict {'n': int} shared across a batch run.

    Returns:
        The expanded filename stem (no extension, no leading/trailing whitespace).
    """
    counter.setdefault("n", 0)

    parts = _parse_tokens(fmt)
    needs_exif = any(
        _token_letter(raw) in {"Y", "m"}
        for kind, raw, _, _ in parts
        if kind == "token"
    )

    datetime_original: Optional[str] = None
    datetime_match: Optional[re.Match[str]] = None
    if needs_exif:
        datetime_original = _read_datetime_original(input_path)
        if datetime_original is not None:
            datetime_match = _DATETIME_ORIGINAL_RE.match(datetime_original)
        if datetime_original is None or datetime_match is None:
            if datetime_original is not None:
                log.warning(
                    "Unrecognised DateTimeOriginal format %r in %s — falling back to UUID",
                    datetime_original, input_path.name,
                )
            fallback = str(uuid.uuid4())
            print(
                f"WARNING: {input_path.name} — EXIF DateTimeOriginal absent, "
                f"renamed to {fallback}{input_path.suffix}",
                flush=True,
            )
            return fallback

    result_parts: list[str] = []
    for kind, raw, _, _ in parts:
        if kind == "literal":
            result_parts.append(raw)
            continue

        letter = _token_letter(raw)

        if letter == "%":
            result_parts.append("%")

        elif letter == "r":
            n = _token_digits(raw) if _token_digits(raw) is not None else _DEFAULT_RANDOM_LEN
            result_parts.append(_random_hex(n))

        elif letter == "u":
            result_parts.append(str(uuid.uuid4()))

        elif letter == "n":
            digits = _token_digits(raw) if _token_digits(raw) is not None else _DEFAULT_COUNTER_DIGITS
            counter["n"] += 1
            result_parts.append(str(counter["n"]).zfill(digits))

        elif letter == "Y":
            result_parts.append(datetime_match.group(1))  # type: ignore[union-attr]

        elif letter == "m":
            result_parts.append(datetime_match.group(2))  # type: ignore[union-attr]

    return "".join(result_parts)


def _random_hex(length: int) -> str:
    """
    Return a random lowercase hex string of the given length.

    Args:
        length: Number of hex characters to generate.

    Returns:
        A hex string of exactly `length` characters.
    """
    # os.urandom gives cryptographically random bytes; each byte → 2 hex chars.
    byte_count = (length + 1) // 2
    return os.urandom(byte_count).hex()[:length]
