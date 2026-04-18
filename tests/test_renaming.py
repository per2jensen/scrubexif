"""Unit tests for scrubexif/renaming.py.

No filesystem I/O beyond Path objects, no exiftool, no Docker.
_read_datetime_original is mocked wherever EXIF data would be read.
"""

import re
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from scrubexif.renaming import resolve_rename, validate_rename_format

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_FAKE_PATH = Path("/photos/IMG_0042.jpg")


def _counter() -> dict[str, int]:
    return {"n": 0}


# ---------------------------------------------------------------------------
# validate_rename_format — accepted inputs
# ---------------------------------------------------------------------------


class TestValidateAccepted:
    @pytest.mark.parametrize("fmt", [
        "%r8",
        "%r",
        "%u",
        "%n4",
        "%n",
        "%Y%m_%r6",
        "850_%r6",
        "%r6_backup",
        "MY_CAM_%r8",
        "holiday 2026_%r6",
        "A-%r6",
        "%%_%r6",
        "%Y%m",
        # exactly at prefix limit: 16 literal chars before first token
        "a" * 16 + "%r",
        # exactly at postfix limit: 16 literal chars after last token
        "%r" + "a" * 16,
        "%r32",
        "%n12",
    ])
    def test_accepted(self, fmt: str) -> None:
        """validate_rename_format must not raise for valid format strings."""
        validate_rename_format(fmt)  # must not raise SystemExit


# ---------------------------------------------------------------------------
# validate_rename_format — rejected inputs
# ---------------------------------------------------------------------------


class TestValidateRejected:
    def _expect_exit(self, fmt: str) -> None:
        with pytest.raises(SystemExit) as exc_info:
            validate_rename_format(fmt)
        assert exc_info.value.code != 0

    # Privacy-forbidden tokens
    @pytest.mark.parametrize("fmt", ["%d", "%H", "%M", "%S", "%Y%d"])
    def test_forbidden_privacy_tokens(self, fmt: str) -> None:
        self._expect_exit(fmt)

    # Unknown tokens
    @pytest.mark.parametrize("fmt", ["%x", "%20", "%z"])
    def test_unknown_tokens(self, fmt: str) -> None:
        self._expect_exit(fmt)

    def test_trailing_percent(self) -> None:
        self._expect_exit("850_%")

    # Dot in format string
    @pytest.mark.parametrize("fmt", ["850_.%r6", "%r6.jpg"])
    def test_dot_not_allowed(self, fmt: str) -> None:
        self._expect_exit(fmt)

    # Disallowed characters
    @pytest.mark.parametrize("fmt", [
        "850/cam_%r6",
        "café_%r6",
        "hello\nworld_%r",
    ])
    def test_disallowed_characters(self, fmt: str) -> None:
        self._expect_exit(fmt)

    def test_prefix_too_long(self) -> None:
        # 17 chars before first token
        self._expect_exit("a" * 17 + "%r")

    def test_postfix_too_long(self) -> None:
        # 17 chars after last token
        self._expect_exit("%r" + "a" * 17)

    def test_r_length_exceeds_max(self) -> None:
        self._expect_exit("%r33")

    def test_n_digits_exceed_max(self) -> None:
        self._expect_exit("%n13")

    def test_total_length_exceeds_max(self) -> None:
        # %r32 (32) + %u (36) = 68 > 64
        self._expect_exit("%r32%u")

    def test_empty_string(self) -> None:
        self._expect_exit("")


# ---------------------------------------------------------------------------
# Hostile input rejection — must be caught before any file is touched
# ---------------------------------------------------------------------------


class TestHostileInputRejection:
    """
    Prove that dangerous format strings are rejected by validate_rename_format
    before the CLI reaches any file processing.

    The whitelist (^[A-Za-z0-9 \\-_]*$) is the primary defence; these tests
    document each attack vector explicitly so regressions are immediately visible.
    """

    def _expect_exit(self, fmt: str) -> None:
        with pytest.raises(SystemExit) as exc_info:
            validate_rename_format(fmt)
        assert exc_info.value.code != 0

    @pytest.mark.parametrize("fmt", [
        # Null byte injection — C library functions treat \x00 as end-of-string;
        # the validated prefix could differ from what the OS sees.
        "prefix\x00_%r8",
        "_%r8\x00",
    ])
    def test_null_byte_injection_rejected(self, fmt: str) -> None:
        self._expect_exit(fmt)

    @pytest.mark.parametrize("fmt, description", [
        # Unicode lookalikes for path separators — could enable traversal if
        # the whitelist only checked for ASCII slash/dot.
        ("foo\u2025_%r6", "TWO DOT LEADER (‥) — slash lookalike"),
        ("foo\u2215_%r6", "DIVISION SLASH (∕) — slash lookalike"),
        ("foo\u2044_%r6", "FRACTION SLASH (⁄) — slash lookalike"),
    ])
    def test_path_traversal_unicode_lookalikes_rejected(self, fmt: str, description: str) -> None:
        self._expect_exit(fmt)

    @pytest.mark.parametrize("fmt, description", [
        # Fullwidth Latin letters normalise to ASCII under NFKC — if validation
        # were applied post-normalisation an attacker could bypass length limits
        # or inject characters that look safe but expand unexpectedly.
        ("\uff21\uff22\uff23_%r6", "fullwidth ABC (ＡＢＣ)"),
        ("\uff10\uff11\uff12_%r6", "fullwidth digits (０１２)"),
    ])
    def test_fullwidth_unicode_normalisation_rejected(self, fmt: str, description: str) -> None:
        self._expect_exit(fmt)

    @pytest.mark.parametrize("fmt, description", [
        # Right-to-left override makes filenames display reversed in terminals,
        # e.g. "evil.jpg" can appear as "gpj.live" — a cosmetic deception attack.
        ("\u202ephoto_%r8", "RTL override at start"),
        ("photo\u202e_%r8", "RTL override in middle"),
    ])
    def test_rtl_override_rejected(self, fmt: str, description: str) -> None:
        self._expect_exit(fmt)

    @pytest.mark.parametrize("fmt, description", [
        # Zero-width characters make two filenames look identical in a terminal
        # while being different on disk — enables silent collision/confusion.
        ("photo\u200b_%r8", "zero-width space (U+200B)"),
        ("photo\u200c_%r8", "zero-width non-joiner (U+200C)"),
        ("photo\u200d_%r8", "zero-width joiner (U+200D)"),
        ("photo\u2060_%r8", "word joiner / zero-width no-break (U+2060)"),
    ])
    def test_zero_width_characters_rejected(self, fmt: str, description: str) -> None:
        self._expect_exit(fmt)


# ---------------------------------------------------------------------------
# resolve_rename — token expansion
# ---------------------------------------------------------------------------


class TestResolveRename:
    def test_r8_is_8_hex_chars(self) -> None:
        result = resolve_rename("%r8", _FAKE_PATH, _counter())
        assert re.match(r"^[0-9a-f]{8}$", result)

    def test_r6_is_6_hex_chars(self) -> None:
        result = resolve_rename("%r6", _FAKE_PATH, _counter())
        assert re.match(r"^[0-9a-f]{6}$", result)

    def test_r_default_is_8_hex_chars(self) -> None:
        result = resolve_rename("%r", _FAKE_PATH, _counter())
        assert re.match(r"^[0-9a-f]{8}$", result)

    def test_u_is_uuid_v4(self) -> None:
        result = resolve_rename("%u", _FAKE_PATH, _counter())
        assert _UUID_RE.match(result), f"Not a UUID v4: {result!r}"

    def test_n4_first_call_is_0001(self) -> None:
        c = _counter()
        result = resolve_rename("%n4", _FAKE_PATH, c)
        assert result == "0001"
        assert c["n"] == 1

    def test_n4_three_sequential_calls(self) -> None:
        c = _counter()
        results = [resolve_rename("%n4", _FAKE_PATH, c) for _ in range(3)]
        assert results == ["0001", "0002", "0003"]

    def test_n_default_digits_with_large_counter(self) -> None:
        c = {"n": 99}
        result = resolve_rename("%n", _FAKE_PATH, c)
        assert result == "0100"

    def test_prefix_with_r6(self) -> None:
        result = resolve_rename("850_%r6", _FAKE_PATH, _counter())
        assert result.startswith("850_")
        assert len(result) == 10  # "850_" (4) + 6 hex chars

    def test_percent_literal(self) -> None:
        result = resolve_rename("%%", _FAKE_PATH, _counter())
        assert result == "%"

    def test_percent_literal_with_r4(self) -> None:
        result = resolve_rename("%%_%r4", _FAKE_PATH, _counter())
        assert result.startswith("%_")
        assert len(result) == 6  # "%_" + 4 hex

    def test_two_r8_calls_differ(self) -> None:
        results = {resolve_rename("%r8", _FAKE_PATH, _counter()) for _ in range(10)}
        # With 8 hex chars the collision probability is negligible; all 10 should differ.
        assert len(results) > 1

    @patch("scrubexif.renaming._read_datetime_original", return_value="2026:04:07 11:13:45")
    def test_year_from_exif(self, _mock) -> None:
        result = resolve_rename("%Y", _FAKE_PATH, _counter())
        assert result == "2026"

    @patch("scrubexif.renaming._read_datetime_original", return_value="2026:04:07 11:13:45")
    def test_month_from_exif(self, _mock) -> None:
        result = resolve_rename("%m", _FAKE_PATH, _counter())
        assert result == "04"

    @patch("scrubexif.renaming._read_datetime_original", return_value="2026:04:07 11:13:45")
    def test_year_month_prefix_r6(self, _mock) -> None:
        result = resolve_rename("%Y%m_%r6", _FAKE_PATH, _counter())
        assert result.startswith("202604_")
        assert len(result) == 13  # 7 prefix + 6 hex

    @patch("scrubexif.renaming._read_datetime_original", return_value=None)
    def test_no_exif_falls_back_to_uuid(self, _mock, capsys) -> None:
        result = resolve_rename("%Y%m_%r6", _FAKE_PATH, _counter())
        assert _UUID_RE.match(result), f"Expected UUID fallback, got {result!r}"
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "EXIF DateTimeOriginal absent" in captured.out
        assert _FAKE_PATH.name in captured.out


# ---------------------------------------------------------------------------
# Total length enforcement at validation time
# ---------------------------------------------------------------------------


class TestTotalLengthValidation:
    def test_r32_ok(self) -> None:
        validate_rename_format("%r32")

    def test_u_ok(self) -> None:
        validate_rename_format("%u")

    def test_r32_u_rejected(self) -> None:
        with pytest.raises(SystemExit):
            validate_rename_format("%r32%u")  # 32 + 36 = 68 > 64

    def test_prefix10_r32_postfix10_ok(self) -> None:
        # 10 prefix + 32 random + 10 postfix = 52 — OK
        validate_rename_format("a" * 10 + "%r32" + "b" * 10)

    def test_prefix16_r32_postfix16_ok(self) -> None:
        # 16 + 32 + 16 = 64 — exactly at limit
        validate_rename_format("a" * 16 + "%r32" + "b" * 16)

    def test_prefix16_r32_postfix16_plus_year_rejected(self) -> None:
        # 16 + 32 + 16 + 4 (%Y) = 68 > 64
        with pytest.raises(SystemExit):
            validate_rename_format("a" * 16 + "%r32" + "b" * 16 + "%Y")
