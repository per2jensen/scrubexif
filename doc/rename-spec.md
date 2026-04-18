# scrubexif — Filename Renaming Design

## 1. Overview

scrubexif removes privacy-sensitive metadata from image files before sharing.
This document specifies the filename sanitisation subsystem, which complements
EXIF/ICC stripping by also removing temporal and device-identifying information
that may be encoded in filenames.

A filename such as:

```
2026-04-07_11-13-45.jpeg
```

leaks the exact date and time of capture. Similarly, camera-derived prefixes
such as `D80_`, `D3S_`, `Z50_` reveal the device model. The renaming subsystem
addresses both vectors.

**Design principle:** no magic, no auto-detection of prefixes or timestamps from
filenames. The user is always in explicit control of the output filename shape.
If you want a prefix or postfix in the output filename, write it literally in
the format string.

---

## 2. The `--rename` Flag

A single new flag is added:

```
--rename FORMAT
```

Apply a format string to rename output files. The output filename is constructed
by expanding tokens in order, then appending the original file extension.

If `--rename` is not specified and `--paranoia` is given, scrubexif implies
`--rename "%r8"` as its filename default. An explicit `--rename` always takes
precedence over the `--paranoia` default.

If neither `--rename` nor `--paranoia` is given, the original filename is
preserved (existing behaviour).

`--dry-run` applies to renaming: proposed new filenames are printed alongside
other planned actions but no files are renamed or modified. Fallback warnings
(see Section 9) are also surfaced during `--dry-run` so problem files can be
identified before committing to a batch.

---

## 3. Format String Tokens

| Token | Description |
|-------|-------------|
| `%r`  | Random hex string. Default length 8. Configure with `%r6`, `%r32`, etc. Maximum length: 32. |
| `%u`  | RFC 4122 UUID v4 (e.g. `f47ac10b-58cc-4372-a567-0e02b2c3d479`). |
| `%n`  | Sequential counter, scoped per invocation. Zero-padded to 4 digits by default. Configure with `%n6` for 6 digits. Maximum digits: 12. |
| `%Y`  | 4-digit year sourced from EXIF DateTimeOriginal (e.g. `2026`). |
| `%m`  | 2-digit month sourced from EXIF DateTimeOriginal (e.g. `04`). |
| `%%`  | Literal percent sign. |

### Explicitly not supported

The following tokens are **not implemented** and will produce an immediate error
if used. Allowing time-of-day or day-of-month in output filenames would
undermine the privacy goal of the tool:

| Token | Reason |
|-------|--------|
| `%d`  | Day of month — too close to a full timestamp when combined with `%Y%m`. |
| `%H`  | Hour — directly defeats the purpose of timestamp scrubbing. |
| `%M`  | Minute — directly defeats the purpose of timestamp scrubbing. |
| `%S`  | Second — directly defeats the purpose of timestamp scrubbing. |

Any unrecognised `%x` sequence also produces an immediate error.

---

## 4. Allowed Characters

The format string may contain:

- **Token output** — characters produced by the tokens above (hex, digits,
  hyphens from UUID, letters from `%u`)
- **Uppercase and lowercase letters** — for literal prefixes and postfixes
  (e.g. `holiday_`, `MY_CAM_`)
- **Separators** — space (` `), hyphen (`-`), underscore (`_`)

The following are **not allowed** and produce an immediate error:

- `.` (dot) — risks confusion with the file extension
- URL-encoded sequences (e.g. `%20`) — caught by the unknown token check
- Shell metacharacters, slashes, unicode, or any character not in the whitelist

Input sanitisation is applied strictly before any files are touched. A format
string that fails validation produces a clear error message and exits
immediately.

---

## 5. Length Constraints

| Constraint | Limit |
|------------|-------|
| Prefix (literal chars before first token) | 16 chars |
| Postfix (literal chars after last token) | 16 chars |
| `%r` length | max 32 |
| `%n` digits | max 12 |
| Total expanded filename (before extension) | 64 chars |

**Prefix** is defined as the number of literal characters appearing before the
first token in the format string. **Postfix** is the number of literal
characters appearing after the last token. Literal separators between tokens
do not count toward either limit.

Examples:

```
D80_%r6          →  prefix "D80_" = 4 chars                      ✓
holiday_2026_%r6 →  prefix "holiday_2026_" = 13 chars            ✓
%Y%m_%r6         →  prefix "" = 0 chars, "_" is between tokens   ✓
%r6_backup       →  postfix "_backup" = 7 chars                   ✓
my_very_long_prefix_name_%r6  →  prefix = 25 chars               ✗ error
```

Validation order:

1. Prefix length <= 16
2. Postfix length <= 16
3. `%r` configured length <= 32
4. `%n` configured digits <= 12
5. Only whitelisted characters present
6. No `.` anywhere in the format string
7. No unrecognised `%x` tokens
8. Total expanded length <= 64

---

## 6. Sequential Counter Scoping (`%n`)

The `%n` counter is ephemeral — it exists only for the duration of one
invocation and is not persisted between runs.

Because there is no auto-detection of camera prefixes, `%n` maintains a single
counter per invocation. To maintain separate sequences per camera body, invoke
scrubexif once per body with its own literal prefix:

```bash
scrubexif --rename "D80_%n4" --clean-inline D80_*.jpg
scrubexif --rename "Z50_%n4" --clean-inline Z50_*.jpg
```

This gives independent sequences `D80_0001`, `D80_0002`... and `Z50_0001`,
`Z50_0002`... without any magic prefix detection.

---

## 7. Collision Handling

- **`%r` and `%u`**: collision probability is negligible for batch sizes up to
  ~100k files with `%r8`. If a collision is detected the random component is
  re-rolled up to 3 times before raising an error.
- **`%n`**: monotonically increasing within a run — collisions within a single
  invocation are impossible.
- If a collision cannot be resolved, scrubexif exits with a non-zero status and
  reports the conflicting filename. No partial rename is applied to the batch.

---

## 8. Usage Examples

### Maximum privacy — fully anonymous filename

```bash
scrubexif --paranoia --from-input
```

`--paranoia` implies `--rename "%r8"`, so no format string is needed. The
output filename is a random 8-character hex string with no prefix, no date,
and no hint of the camera used.

```
a3f9c2e1.jpg
```

---

### Maximum privacy — explicit format

```bash
scrubexif --rename "%r8" --clean-inline photo.jpg
```

Same result as `--paranoia` for filenames. Use this form if you want anonymous
renaming without the full `--paranoia` metadata scrubbing.

```
d4e7b1a9.jpg
```

---

### Keep your camera prefix, remove the timestamp

```bash
scrubexif --rename "D80_%r6" --clean-inline *.jpg
```

Write your prefix literally in the format string. The `%r6` token appends a
6-character random hex suffix. For multi-body workflows, run once per camera
body with its own prefix — no auto-detection needed or wanted.

```
D80_f3a91c.jpg
```

---

### Keep your prefix with a sequential counter

```bash
scrubexif --rename "D80_%n4" --clean-inline *.jpg
```

Use `%n` instead of `%r` when you prefer predictable sequential numbering
within a session. The counter resets each invocation, so process one camera
body at a time to keep sequences clean.

```
D80_0001.jpg  D80_0002.jpg  D80_0003.jpg ...
```

---

### Retain year and month, drop everything else

```bash
scrubexif --rename "%Y%m_%r6" --clean-inline *.jpg
```

Keeps enough temporal context for rough organisation (year and month, sourced
from EXIF DateTimeOriginal) while removing the exact date, time, and any camera
hint. A conscious trade-off: the user explicitly chooses to retain year and
month in exchange for file organisation convenience.

```
202604_f3a91c.jpg
```

---

### Preview what renames would happen — no changes made

```bash
scrubexif --rename "D80_%r6" --dry-run --clean-inline *.jpg
```

`--dry-run` prints the planned rename for each file alongside other planned
actions, then exits without modifying anything. Fallback warnings for files
missing EXIF DateTimeOriginal are also shown. Use this to verify your format
string before committing to a batch.

```
(printed to stdout, no files written)
```

---

### UUID — maximum anonymity, globally unique

```bash
scrubexif --rename "%u" --clean-inline photo.jpg
```

Produces a full RFC 4122 UUID v4. Longer than `%r8` but carries a formal
uniqueness guarantee. Useful when images will be stored in a shared system
where collision avoidance matters independently of scrubexif.

```
f47ac10b-58cc-4372-a567-0e02b2c3d479.jpg
```

---

## 9. EXIF DateTimeOriginal Fallback

When a format string contains `%Y` or `%m`, scrubexif reads EXIF
DateTimeOriginal to resolve the values. If a file has no EXIF DateTimeOriginal
the following behaviour applies:

- The file is **fully scrubbed** as normal — metadata removal is not affected
- The filename falls back to a **UUID v4** regardless of the requested format
- A **clear warning** is emitted naming the file and stating the reason
- The exit code is **not affected** — this is a handled fallback, not an error

Example warning:

```
WARNING: IMG_0042.jpeg — EXIF DateTimeOriginal absent,
         renamed to f47ac10b-58cc-4372-a567-0e02b2c3d479.jpg
```

The UUID fallback is visually unambiguous — it is immediately recognisable as
different from any user-specified format, making affected files easy to identify
after a batch run. The original potentially-leaking filename is never preserved.

UUID generation cannot fail, so the fallback chain bottoms out cleanly with no
further edge cases.

`--dry-run` surfaces fallback warnings without modifying any files, allowing
the user to identify problem files before committing to a batch.

---

## 10. Integration Notes for Implementors

This section describes exactly where and how `--rename` hooks into the existing
codebase. Read this alongside `scrub.py`.

### 10.1  Where to add the CLI flag

In `main()` (line 1589), add alongside the existing arguments:

```python
parser.add_argument(
    "--rename",
    metavar="FORMAT",
    default=None,
    help="Format string for output filename. See documentation for tokens.",
)
```

In `_run_inner()` (line 1442), resolve the effective format string early,
before the three mode branches (`from_input`, `clean_inline`, `simple_scrub`):

```python
rename_format = args.rename
if rename_format is None and args.paranoia:
    rename_format = "%r8"
# validate_rename_format() raises SystemExit on any violation
if rename_format is not None:
    validate_rename_format(rename_format)
```

Then pass `rename_format` through to each of the three mode functions
(`auto_scrub`, `manual_scrub`, `simple_scrub`) as a new keyword argument,
and from there into `scrub_file`.

### 10.2  New module: `renaming.py`

All rename logic should live in a new file `scrubexif/renaming.py` to keep
`scrub.py` focused. It should expose:

```python
def validate_rename_format(fmt: str) -> None:
    """Validate format string. Raises SystemExit with clear message on error."""

def resolve_rename(fmt: str, input_path: Path, counter: dict) -> str:
    """
    Expand fmt into a filename stem (no extension).
    counter is a mutable dict {'n': int} shared across a batch run.
    Reads EXIF DateTimeOriginal via exiftool if %Y or %m are present.
    Falls back to UUID and emits a warning if EXIF is absent.
    """
```

### 10.3  Reading EXIF DateTimeOriginal

scrubexif already calls exiftool via `extract_wanted_tags()` (line 632) using
`-j -n` with a tag whitelist. For `%Y` and `%m`, add a separate lightweight
exiftool call in `renaming.py` rather than modifying the existing tag whitelist:

```python
def _read_datetime_original(input_path: Path) -> Optional[str]:
    """Returns DateTimeOriginal as string 'YYYY:MM:DD HH:MM:SS', or None."""
    result = subprocess.run(
        ["exiftool", "-j", "-DateTimeOriginal", str(input_path.absolute())],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout)
    if not data:
        return None
    return data[0].get("DateTimeOriginal")
```

This keeps the rename concern isolated from `extract_wanted_tags()` and avoids
coupling the tag whitelist to the rename feature.

### 10.4  Where output_path is constructed in scrub_file

The critical line is in `scrub_file()` (line 882):

```python
output_file = output_path / input_path.name if output_path else input_path
```

This is where `input_path.name` becomes the output filename. The rename logic
must replace `input_path.name` with the expanded format string result here.
The extension must be preserved from `input_path.suffix`.

Proposed change:

```python
stem = expand_rename(rename_format, input_path, counter) if rename_format else input_path.stem
output_file = (output_path / (stem + input_path.suffix)) if output_path else input_path
```

For `--clean-inline` mode (`output_path is None`), the file is scrubbed in
place and then renamed in the same directory using `os.rename()`:

```python
if rename_format and in_place:
    new_name = stem + input_path.suffix
    new_path = input_path.parent / new_name
    os.rename(input_path, new_path)
```

The user has already explicitly opted into destructive in-place modification
with `--clean-inline`, so also opting into rename with `--rename` is consistent
and intentional. The original path will disappear — this is expected.

**Important:** the current code terminates early if both `--clean-inline` and
`--rename` are passed together. That guard must be removed. Collision handling
applies as normal within the same directory.

### 10.5  Passing the counter through the call stack

`%n` requires a counter that is shared across all files in a single batch run
and scoped per invocation. The simplest implementation is a mutable dict
`{'n': 0}` created once in `_run_inner()` and passed through:

```
_run_inner → auto_scrub / manual_scrub / simple_scrub → scrub_file → resolve_rename
```

This avoids globals and is easy to test.

### 10.6  dry-run integration

The dry-run path in `scrub_file()` (line 927) currently prints:

```python
print(f"🔍 Dry run: would scrub {_format_path_with_host(input_path)}")
```

When `rename_format` is set, extend this to also resolve and print the proposed
output filename:

```python
proposed_name = resolve_rename(rename_format, input_path, counter) + input_path.suffix
print(f"🔍 Dry run: would scrub {_format_path_with_host(input_path)} → {proposed_name}")
```

The same applies to the dry-run print statements in `auto_scrub()` (line 1121),
`simple_scrub()` (line 1274), and `manual_scrub()` (line 1391).

### 10.7  Validation order in validate_rename_format()

Implement exactly in this order, exiting immediately on first failure:

1. Scan for `%d`, `%H`, `%M`, `%S` — error with privacy rationale
2. Scan for any `%x` not in the allowed token set — error naming the token
3. Count literal chars before first token — error if > 16
4. Count literal chars after last token — error if > 16
5. Check `%rN` configured length — error if N > 32
6. Check `%nN` configured digits — error if N > 12
7. Scan all non-token characters against the whitelist — error naming offending char
8. Check for `.` anywhere in non-token literal text — specific error message
9. Expand the format string with worst-case token lengths to check total <= 64

Step 9 uses maximum possible expansion: `%r` → 32 chars, `%u` → 36 chars,
`%n` → 12 chars, `%Y` → 4 chars, `%m` → 2 chars.

---

## 11. Testing

The existing `pytest.ini` defines the following markers relevant to rename
testing:

| Marker | Usage |
|--------|-------|
| `smoke` | Basic sanity — format validation and a single `%r8` rename |
| `regression` | Full token matrix and boundary conditions |
| `integration` | Real JPEG + exiftool, no Docker |
| `docker` | Container end-to-end tests |
| `private` | Tests against real camera files in `tests/private-assets/` — excluded by default, run with `-m private` |

Tests are added to `tests/test_renaming.py` (unit) and
`tests/test_rename_integration.py` (integration + container).

---

### 11.1  Unit tests (`tests/test_renaming.py`)

Unit tests cover `renaming.py` in isolation — no filesystem, no exiftool, no
Docker. Use `pytest`. Mock `_read_datetime_original()` where needed.

#### 11.1.1  validate_rename_format — accepted inputs

| Input | Expected |
|-------|----------|
| `%r8` | OK |
| `%r` | OK (default length 8) |
| `%u` | OK |
| `%n4` | OK |
| `%n` | OK (default 4 digits) |
| `%Y%m_%r6` | OK |
| `D80_%r6` | OK (prefix 4 chars) |
| `%r6_backup` | OK (postfix 7 chars) |
| `MY_CAM_%r8` | OK (uppercase prefix) |
| `holiday 2026_%r6` | OK (space in prefix) |
| `A-%r6` | OK (hyphen in prefix) |
| `%%_%r6` | OK (literal percent) |
| `%Y%m` | OK (no random token required) |
| 16-char prefix + `%r` | OK (exactly at limit) |
| 16-char postfix + `%r` | OK (exactly at limit) |
| `%r32` | OK (exactly at limit) |
| `%n12` | OK (exactly at limit) |

#### 11.1.2  validate_rename_format — rejected inputs

| Input | Expected error |
|-------|---------------|
| `%d` | Privacy: day not allowed |
| `%H` | Privacy: hour not allowed |
| `%M` | Privacy: minute not allowed |
| `%S` | Privacy: second not allowed |
| `%Y%d` | Privacy: day not allowed |
| `%x` | Unknown token `%x` |
| `%20` | Unknown token (URL-encoded) |
| `%` (trailing) | Malformed token |
| `D80_.%r6` | Dot not allowed |
| `%r6.jpg` | Dot not allowed in postfix |
| `D80/cam_%r6` | Slash not allowed |
| `café_%r6` | Non-ASCII not allowed |
| `hello\nworld_%r` | Non-ASCII / control char not allowed |
| 17-char prefix + `%r` | Prefix too long |
| 17-char postfix + `%r` | Postfix too long |
| `%r33` | `%r` length exceeds 32 |
| `%n13` | `%n` digits exceed 12 |
| `%r32%u%Y%m` + 14-char prefix + 14-char postfix | Total > 64 chars |
| empty string `""` | No tokens and no output — error |

#### 11.1.3  resolve_rename — token expansion

Each test calls `resolve_rename(fmt, input_path, counter)` with a mocked or
real `input_path` and asserts properties of the result.

| Format | Assertion |
|--------|-----------|
| `%r8` | Result is 8 hex chars matching `[0-9a-f]{8}` |
| `%r6` | Result is 6 hex chars |
| `%r` | Result is 8 hex chars (default) |
| `%u` | Result matches UUID v4 regex |
| `%n4` with counter `{'n': 0}` | Result is `0001`, counter becomes `{'n': 1}` |
| `%n4` called 3 times | Results are `0001`, `0002`, `0003` |
| `%n` with counter `{'n': 99}` | Result is `0100` |
| `D80_%r6` | Result starts with `D80_`, total length 10 |
| `%Y%m_%r6` with mocked date `2026:04:07` | Result starts with `202604_` |
| `%m` with mocked date `2026:04:07` | Result is `04` |
| `%Y` with mocked date `2026:04:07` | Result is `2026` |
| `%Y%m` with no EXIF (mocked None) | Result matches UUID v4, warning emitted |
| `%%` | Result is `%` |
| `%%_%r4` | Result starts with `%_` |
| Two calls with `%r8` | Results are different (random) |

#### 11.1.4  Total length enforcement

| Format | Max expansion | Expected |
|--------|--------------|----------|
| `%r32` | 32 | OK |
| `%u` | 36 | OK |
| `%r32%u` | 68 | Rejected at validation (> 64) |
| 10-char prefix + `%r32` + 10-char postfix | 52 | OK |
| 16-char prefix + `%r32` + 16-char postfix | 64 | OK (exactly at limit) |
| 16-char prefix + `%r32` + 16-char postfix + `%Y` | 68 | Rejected (> 64) |

---

### 11.2  Integration tests

Integration tests verify end-to-end behaviour using real JPEG files and real
exiftool. They do not require Docker.

#### File fixtures

| Fixture | Description |
|---------|-------------|
| `fixture_with_exif.jpg` | Real JPEG with valid `DateTimeOriginal` in EXIF |
| `fixture_no_exif.jpg` | Real JPEG with all EXIF stripped |
| `fixture_no_datetime.jpg` | Real JPEG with EXIF present but `DateTimeOriginal` absent |

#### Test cases

| Scenario | Setup | Assertion |
|----------|-------|-----------|
| `--rename "%r8"` on single file | Run `scrub_file` with format | Output filename is 8 hex chars + original extension |
| `--rename "%u"` on single file | Run `scrub_file` with format | Output filename matches UUID v4 pattern + extension |
| `--rename "D80_%r6"` on single file | Run `scrub_file` with format | Output filename starts with `D80_`, total stem 10 chars |
| `--rename "%n4"` on 3 files | Run with counter | Output filenames are `0001`, `0002`, `0003` + extension |
| `--rename "%Y%m_%r6"` with EXIF | `fixture_with_exif.jpg` | Output starts with `202604_` (or correct year/month) |
| `--rename "%Y%m_%r6"` without DateTimeOriginal | `fixture_no_datetime.jpg` | Output is UUID, warning printed |
| `--rename "%Y%m_%r6"` no EXIF at all | `fixture_no_exif.jpg` | Output is UUID, warning printed |
| `--dry-run --rename "%r8"` | Single file | Proposed name printed, file not renamed |
| `--dry-run --rename "%Y%m_%r6"` no DateTimeOriginal | `fixture_no_datetime.jpg` | Fallback warning printed, no file written |
| `--paranoia` without `--rename` | Any file | Output filename is 8 hex chars (implied `%r8`) |
| `--paranoia --rename "%u"` | Any file | Explicit `%u` overrides paranoia default |
| `--clean-inline --rename "%r8"` | Single file | File is scrubbed in place, then renamed to 8-char hex name in same directory, original path gone |
| `--clean-inline --rename "D80_%r6"` on 3 files | 3 files in same dir | All 3 renamed to `D80_XXXXXX.jpg`, original names gone |
| Invalid format `%d` passed to CLI | Any file | Process exits non-zero, clear error before any files touched |
| Collision on `%r` | Mock `os.urandom` to return same value 3 times | Error raised after 3 re-rolls |

---

### 11.3  Container integration tests

Container tests run scrubexif in its Docker deployment exactly as a real user
would. They verify that the `--rename` flag works end-to-end through the
container entrypoint, volume mounts, and all three operating modes
(`--from-input`, `--clean-inline`, default safe mode).

#### Prerequisites

- Docker available and scrubexif image built (`make build` or equivalent)
- A directory of real test JPEGs mounted at `/photos`

#### Test cases

| Scenario | Command | Assertion |
|----------|---------|-----------|
| Basic rename in `--from-input` mode | `docker run ... scrubexif --from-input --rename "%r8"` | All output files in `/photos/output` have 8-char hex names |
| Rename with prefix in `--from-input` mode | `docker run ... scrubexif --from-input --rename "D80_%r6"` | All output files start with `D80_` |
| `--clean-inline --rename "%r8"` | `docker run ... scrubexif --clean-inline --rename "%r8" photo.jpg` | File is scrubbed in place and renamed to 8-char hex name, original path gone |
| `--paranoia` implies `%r8` | `docker run ... scrubexif --paranoia --from-input` | Output files have 8-char hex names, ICC profile absent |
| `--paranoia --rename "%u"` | `docker run ... scrubexif --paranoia --from-input --rename "%u"` | Output files have UUID names, ICC profile absent |
| `--dry-run --rename "%r8"` | `docker run ... scrubexif --from-input --dry-run --rename "%r8"` | Proposed names printed to stdout, no files written to `/photos/output` |
| Invalid format rejected before any scrub | `docker run ... scrubexif --from-input --rename "%H%M"` | Exit non-zero, no files in output dir, error message names the forbidden token |
| EXIF fallback in container | `--rename "%Y%m_%r6"` with a no-DateTimeOriginal JPEG | Output file is UUID-named, warning line present in stdout |
| `%n` counter across a batch | `--from-input --rename "cam_%n4"` with 5 files | Output files are `cam_0001` through `cam_0005` in order |
| Extension preserved | `--from-input --rename "%r8"` with `.jpeg` file | Output file has `.jpeg` extension, not `.jpg` |

#### Container test structure

Container tests should be shell scripts or pytest with `subprocess` calls,
checking stdout, stderr, exit codes, and the actual filenames present in the
output directory after each run. Each test should start from a clean output
directory to avoid state leaking between runs.

```bash
# Example shell-based container test skeleton
setup() {
    rm -rf /tmp/test_photos/output
    cp fixtures/*.jpg /tmp/test_photos/input/
}

test_rename_r8_from_input() {
    docker run --rm \
        -v /tmp/test_photos:/photos \
        scrubexif --from-input --rename "%r8"
    # Assert: all files in output match [0-9a-f]{8}\.jpe?g
    for f in /tmp/test_photos/output/*; do
        [[ "$(basename "$f")" =~ ^[0-9a-f]{8}\.(jpg|jpeg)$ ]] || fail "$f"
    done
}
```

---

### 11.4  Private tests (`@pytest.mark.private`)

Private tests run against real camera files in `tests/private-assets/` and are
excluded from CI by default. Run explicitly with:

```bash
pytest -m private
```

These tests are the highest-confidence validation of the rename feature because
they exercise real-world inputs that synthetic fixtures cannot fully replicate.

#### What private tests cover that synthetic fixtures cannot

- Real `DateTimeOriginal` values from Nikon D80, D3s, Z50 — verifying
  that `%Y` and `%m` parse correctly from actual camera output
- Files that genuinely have no `DateTimeOriginal` (old scans, iPhone imports,
  screenshots) — confirming the UUID fallback triggers correctly in practice
- Real filename patterns (`D80_XXXX.jpg`, `Z50_XXXX.jpg`, `D3S_XXXX.jpg`,
  iPhone space-separated formats) — confirming extension preservation and that
  the original filename is never leaked into the output
- Edge cases in EXIF that synthetic files may not contain — unusual encodings,
  maker notes, non-standard date formats from older camera firmware

#### Suggested private test cases

| Scenario | Marker | Assertion |
|----------|--------|-----------|
| `--rename "%r8"` on each camera body's files | `private` | All outputs are 8-char hex, original name gone |
| `--rename "D80_%r6"` on D80 files | `private` | Outputs start with `D80_` |
| `--rename "%Y%m_%r6"` on all camera files | `private` | Year and month match known capture dates |
| `--rename "%Y%m_%r6"` on files known to lack DateTimeOriginal | `private` | UUID fallback, warning emitted |
| `--rename "%n4"` across a full session of files from one body | `private` | Sequential numbering correct, no gaps |
| Full batch of mixed camera bodies with `--rename "%r8"` | `private` | All outputs unique, none retain original name |

#### Organisation

Place private asset files in `tests/private-assets/` with subdirectories per
camera body for clarity:

```
tests/private-assets/
    d80/
    iphone/
    no-datetime/    ← files confirmed to have no DateTimeOriginal
```

The `no-datetime/` subdirectory is particularly valuable — populate it with
files confirmed via `exiftool` to lack `DateTimeOriginal`, so the fallback path
is always tested against real inputs rather than synthetically stripped files.
