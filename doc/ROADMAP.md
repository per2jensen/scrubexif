# scrubexif Roadmap

## Direction toward 1.0

The 0.8.x series focuses on fault resilience, accurate reporting, output
safety, and documentation verification.

The main themes are:

- Fail-closed publication under subprocess failures, disk-full conditions,
  permission errors, interruptions, concurrent changes, and cross-filesystem
  operations.
- Exactly one terminal outcome per examined input: scrubbed, duplicate handled,
  unsupported, planned, previewed, skipped, or failed.
- Summary totals derived from those outcomes, with reconciliation invariants
  that make contradictory statistics impossible.
- Cleanup and restart safety: no half-published JPEGs, lost originals, or stale
  temporary files mistaken for results.
- Fault-injection tests covering failures at every significant boundary.
- Documentation audit: verify every example, default, exit status, safety
  claim, and mode description against the implementation.
- Clear documentation of what scrubexif guarantees and what it cannot
  guarantee.

The intended progression is:

- `0.8.0`: unified outcome/accounting model and trustworthy summaries.
- `0.8.1`: fault injection, interruption recovery, and filesystem edge cases.
- `0.8.2`: complete documentation and CLI-behavior audit.
- `0.9.x`: stabilization only: interface review, compatibility decisions, and
  no major new features.
- `1.0`: documented behavioral contract backed by tests.

## 0.8.0 investigation

This section records the initial investigation of the 0.7.28 implementation.
It is a plan for review, not an implementation of the behavioral changes.

### Existing foundations

The current implementation already provides a strong base:

- `scrub_file()` performs work in a fresh private temporary directory.
- `_audit_scrubbed_output()` independently parses completed output against the
  active metadata policy.
- `_copy_audited_output_to_destination()` copies only audited bytes into an
  exclusively created destination-side `.part` file, calls `fsync()`, and
  verifies the copied digest.
- `_publish_no_clobber()` uses a same-filesystem hard link to publish without
  replacing an existing destination.
- In-place replacement uses a destination-side staged file before
  `os.replace()`.
- `_archive_no_clobber()` supports cross-filesystem archival without replacing
  an existing archive entry and removes the source only after publication.
- Focused tests cover silent unsafe pipeline output, corrupted JPEGs,
  destination races, unsafe existing outputs, duplicates, collisions, archive
  failures, and temporary-path ownership.
- The release workflow runs the normal suite and a separate fail-closed privacy
  gate against the final image.

### Findings that motivate 0.8.0

#### Free-form outcomes

`ScrubResult.status` is an unrestricted string. `ScrubSummary.update()` handles
the known values with pattern matching, but it does not reject an unknown
value. An unknown value therefore increases `total` without entering a
terminal outcome counter.

The current status vocabulary also mixes different concepts:

- Processing result: `scrubbed`, `error`, `skipped`.
- Existing-output result: `duplicate`, `collision`, `unsafe_output`.
- Post-publication state: `scrubbed_with_error`.
- Rename planning/publication state: `conflict`.

These should become a validated outcome type with separate reason and
disposition fields.

#### Counters have multiple writers

Some paths call `ScrubSummary.update()`, while other paths directly increment
`total`, `skipped`, `errors`, or `unsupported`. Direct mutations currently
occur in auto-mode filtering, dry-run handling, preview handling, unsupported
file counting, and post-scrub archival failure handling.

Because accounting rules are spread across modes, a new branch can process an
input without recording the same facts as the other modes.

#### Some counters intentionally overlap

`scrubbed_with_error` increments both `scrubbed` and `errors`. Auto mode also
records a successful scrub and then increments `errors` if archival of the
original fails. This contains useful information—an audited output may exist
even though the complete requested operation failed—but the current summary
does not distinguish a terminal outcome from the orthogonal fact that output
was published.

#### Discovery and accounting use separate traversals

JPEG processing candidates are collected before processing begins. When
renaming is active, scrubexif builds a complete disk-backed plan of eligible
JPEG sources and destinations and deduplicates repeated normalized source
paths. Unsupported regular files are currently counted in a separate traversal,
so the unsupported count and JPEG inventory can observe different filesystem
states. `--max-files` limits eligible JPEG processing but does not limit
unsupported-file accounting. Without a rename plan, overlapping explicit
inputs can also include the same JPEG more than once.

A unified discovery inventory should define the operation scope and record one
observation for each unique input.

#### Dry-run and preview have special accounting

Successful dry-run paths increment `total` directly without recording a
terminal classification. Successful previews can return without incrementing
any counter, while failed previews increment both `total` and `errors`
directly. These modes need explicit, consistent outcome semantics.

#### A final summary is not emitted on every controlled failure

The run summary is created after some preflight work and printed only at the
normal end of `_run_inner()`. Argument validation, directory guards, rename
planning failures, unexpected exceptions, and interruption paths can exit
without a machine-readable summary.

Run-level failures must be represented separately from per-input failures, and
the runner should emit a validated final report for every controlled exit after
run initialization.

#### Summary output has existing consumers

`scripts/scrubexif-demo.sh`, `scripts/run_scrubexif_photoprism.sh`, the GNOME
Files wrapper, and external automation parse `SCRUBEXIF_SUMMARY`. The 0.8.0
format therefore needs an explicit schema version and a compatibility plan.
Although named-field parsers tolerate additional `key=value` pairs
syntactically, they cannot safely consume changed meanings without checking the
schema. Schema 2 therefore requires an explicit consumer migration rather than
assuming that additive syntax provides semantic compatibility.

### Agreed 0.8.0 outcome model

Introduce a closed `OutcomeKind` enum with exactly these terminal values:

- `SCRUBBED`: a new audited output was published and all required source
  finalization completed.
- `DUPLICATE_HANDLED`: an existing output was verified as byte-identical and
  the requested non-failing duplicate policy completed.
- `UNSUPPORTED`: a regular input was classified as outside the supported JPEG
  scope.
- `PLANNED`: dry-run planning completed without running the scrub pipeline or
  publishing output.
- `PREVIEWED`: the scrub pipeline and independent audit succeeded on a
  disposable copy without publishing output.
- `SKIPPED`: an eligible JPEG was deliberately excluded without being planned,
  previewed, scrubbed, or verified as a duplicate.
- `FAILED`: the requested per-input operation did not complete successfully.

Represent each examined input with one immutable `FileOutcome` value. In
addition to its terminal kind, it should carry validated orthogonal facts:

- input path;
- input classification: eligible JPEG or unsupported regular file;
- stable reason code;
- processing phase at which the outcome was decided;
- intended and actual output path, when applicable;
- whether a new audited output was published;
- whether an existing output was independently verified as a byte-identical
  duplicate;
- source disposition: unchanged, moved, deleted, or missing;
- duplicate disposition: unchanged, moved, deleted, or missing, when
  applicable;
- diagnostic text for human output.

This separates the terminal outcome from filesystem facts. For example, an
audited output can be published successfully and the complete operation can
still have terminal outcome `FAILED` because required source archival failed.
Likewise, an input can have terminal outcome `FAILED` under
`--on-duplicate fail` while retaining `duplicate_verified=true` and an
unchanged duplicate disposition.

Invalid combinations must raise `ValueError` at construction. Examples include
a duplicate-handled result without duplicate verification and a duplicate
disposition, a scrubbed result without a published output, a planned or
previewed result with a published output, or an unsupported input with output
facts.

An input must be classified before it receives a `FileOutcome`. Failures before
classification are run-level failures and do not increment `examined` or
`jpeg_total`. Once an input is classified as an eligible JPEG, any subsequent
per-input failure contributes to `FAILED` and `jpeg_total`. This keeps the
JPEG-only meaning of `total` explicit instead of assuming that every failed
entry was a JPEG.

### Agreed run accounting

Replace public counter mutation with a per-invocation `RunReport` that accepts
validated outcomes through one `record()` method. Mode functions may return or
stream outcomes, but they must not edit counters directly.

The report can aggregate outcomes as they arrive; it does not need to retain an
unbounded in-memory list. Before printing, `validate()` must enforce at least:

```text
examined = scrubbed + duplicates_handled + unsupported + planned + previewed + skipped + failed
jpeg_total = scrubbed + duplicates_handled + planned + previewed + skipped + failed
verified_duplicates = duplicates_deleted + duplicates_moved + duplicates_unchanged + duplicates_missing
errors = failed + run_errors
```

`published` is a separate fact count. It becomes true at the publication commit
point: after the final hard link or replacement succeeds and the intended path
refers to the audited bytes. It remains true if later staging cleanup, source
cleanup, or archival fails, so it may include an input whose terminal outcome is
`FAILED`.

`verified_duplicates` is also a fact count, not the count of one terminal
outcome. It includes duplicate verification followed by a failing policy or a
failed duplicate-disposition operation. `duplicates_handled` counts only the
successful `DUPLICATE_HANDLED` terminal outcomes. Every verified duplicate has
exactly one observed final duplicate disposition, including `unchanged` after
a policy failure and `missing` when concurrent change prevents a stronger
conclusion.

The existing `total` field should remain an alias for `jpeg_total` during the
0.8.x series. Summary schema 2 retains the existing field names and adds new
fields, but it deliberately changes some field meanings to match the terminal
outcome contract. In particular, dry-run results no longer inflate `scrubbed`,
preview and dry-run results no longer disappear from accounting, and a
post-publication failure is `failed` rather than `scrubbed`.

This is additive syntax, not semantic backward compatibility. Emit `schema=2`
as the first key-value field. Repository consumers must require and understand
schema 2 in the same change that enables the new summary. External consumers
must migrate before adopting 0.8.0 or remain pinned to the 0.7.x behavior; they
must not infer compatibility merely because a legacy field name remains
present. Schema 2 fields include:

- `schema=2`
- `jpeg_total`
- `duplicates_handled`
- `verified_duplicates`
- `duplicates_unchanged`
- `duplicates_missing`
- `planned`
- `previewed`
- `failed`
- `published`
- `run_errors`
- `interrupted`

The existing `total`, `scrubbed`, `skipped`, `errors`, `duplicates_deleted`,
`duplicates_moved`, `unsupported`, `examined`, and `duration` fields remain
present with their schema 2 definitions documented precisely. Consumers must
reject missing or unsupported schema versions, duplicate fields, non-numeric
counts, and summaries that fail the schema 2 reconciliation rules.

If internal reconciliation fails, scrubexif must return nonzero and emit a
distinct summary-validation diagnostic instead of printing contradictory
normal counters.

### Agreed discovery rules

Use one discovery pipeline for all modes and classify each entry once.

The rules are:

- Define `examined` as entries that receive a terminal `FileOutcome`.
- Classify an input before creating its `FileOutcome`. An eligible extension
  plus successful initial regular-file validation establishes a JPEG candidate;
  failures before that point are run-level failures.
- Deduplicate only repeated references to the same normalized, resolved,
  non-symlink pathname. This covers the same path supplied twice or reached
  through overlapping explicit directories. Capture the non-symlink
  `(st_dev, st_ino)` pair during discovery to detect hard-link aliases and to
  revalidate identity before processing, but do not use that identity to
  collapse distinct pathnames. Each hard-link pathname is a separate input and
  publication obligation because atomically replacing one directory entry does
  not sanitize the other entries. When stable filesystem identity is
  unavailable, use the normalized resolved path for replacement detection and
  record that fallback in diagnostics. Replacement after discovery becomes an
  explicit skipped or failed outcome.
- Reject symlinks before they enter the operation scope.
- Apply `--max-files` consistently to JPEG candidates admitted for an actual
  scrub, dry-run plan, or preview attempt. A candidate consumes one slot when
  admitted even if that attempt later fails. Unsupported files and eligible
  JPEGs classified as `SKIPPED`, including temporary and unstable inputs, do
  not consume slots. Record unsupported and skipped entries encountered while
  searching for admitted candidates, then stop discovery immediately when the
  requested number has been admitted; later entries do not affect counters.
  `--max-files 0` admits and examines no entries.
- Record unsupported regular files through the same outcome path rather than a
  second counting traversal.
- Record temporary and unstable JPEGs as `SKIPPED` with distinct reason codes.
- Record successful duplicate policies as `DUPLICATE_HANDLED`, with
  `duplicate_verified=true` and the observed duplicate disposition.
- Record `--on-duplicate fail` and failed duplicate disposition operations as
  `FAILED`, while preserving `duplicate_verified=true`, the verified output
  path, and the observed final duplicate disposition.
- Record successful dry-run evaluation as `PLANNED`. It must not run the scrub
  pipeline, claim an actual output, or increment `published`.
- Record successful preview as `PREVIEWED` only after the disposable scrubbed
  output passes the independent audit. A preview failure is `FAILED`.
- Reserve `SKIPPED` for eligible JPEGs excluded for reasons such as temporary or
  unstable input. Use stable reason codes for every case.

### Agreed run lifecycle

Create the report after successful argument parsing and informational exits, but
before recoverable operational preflight begins, and centralize final emission
in the CLI runner. `--help`, `--version`, and argument-parser errors do not
initialize a report and do not emit a summary. Root guards, dependency checks,
mode validation, path validation, discovery, and planning occur after report
initialization and must produce a final summary on controlled failure.

The lifecycle should be:

1. Parse and validate CLI syntax; handle `--help` and `--version` without a run
   report.
2. Create a fresh `RunReport` local to the operational invocation.
3. Perform preflight and discovery through operations that return typed
   outcomes or typed run-level failures.
4. Record each input exactly once after its final disposition is known.
5. On a controlled exception or handled interruption, record the active input
   as failed when the error is attributable to that classified input. Add a
   run-level failure only for additional or unassignable run-level impact; do
   not count one causal failure twice.
6. Validate and emit one final human summary and one machine-readable summary.
7. Derive the process exit status from `failed`, `run_errors`, and interruption
   state.

The reporting scope cannot include termination that prevents process cleanup or
output, such as `SIGKILL`, host power loss, or storage failure affecting the
output stream itself. Restart behavior for those cases belongs in 0.8.1.

### 0.8.0 implementation sequence

1. Add positive and negative tests for a pure typed outcome model and its
   validation rules.
2. Add `RunReport.record()` and reconciliation tests without changing the CLI
   output.
3. Replace free-form `ScrubResult.status` values with typed outcomes and reason
   codes.
4. Remove all direct counter mutation outside `RunReport`.
5. Unify discovery and unsupported classification, including `--max-files` and
   overlapping explicit inputs. Update CLI help and user documentation to state
   explicitly that skipped JPEGs do not consume the limit.
6. Normalize dry-run, preview, duplicate, collision, and post-publication
   failure accounting.
7. Move final report emission and exit-status calculation into one runner
   boundary.
8. Add summary schema version 2, document the changed field meanings, and emit
   `schema=2` first.
9. Update repository wrappers to require and validate schema 2. Test legacy
   lines as unsupported migration inputs rather than implying semantic
   compatibility.
10. Run focused tests, the full suite, the fail-closed privacy gate, and final
    image tests before release.

Each step should remain a small reviewable change. Behavioral changes require
both positive and negative tests.

### 0.8.0 test matrix

#### Outcome validation

- Accept every valid terminal kind with its required fields, including distinct
  planned and previewed success cases.
- Reject unknown kinds, missing reasons, invalid dispositions, and impossible
  combinations.
- Reject outcomes whose terminal kind conflicts with input classification,
  publication, preview execution, or duplicate-verification facts.
- Verify that a publication-cleanup failure retains `published=true` once the
  final destination refers to the audited bytes.
- Verify that recording the same outcome object twice is rejected.

#### Reconciliation

- Exercise each terminal category independently.
- Exercise a mixed batch containing all terminal categories.
- Verify that a failed duplicate policy increments `failed`,
  `verified_duplicates`, and exactly one duplicate-disposition counter without
  incrementing `duplicates_handled`.
- Verify that pre-classification failures increment `run_errors` without
  changing `examined`, `jpeg_total`, or `total`.
- Verify the reconciliation identities in default, auto, and clean-inline
  modes.
- Force a deliberately inconsistent report and verify a nonzero result with no
  contradictory normal summary.

#### Mode behavior

- Default mode: success, unsupported file, verified duplicate, corrupted JPEG,
  and occupied unsafe output.
- Auto mode: success with archival, delete-original success/failure, duplicate
  policies, collision, unstable input, temporary input, and archival failure.
- Clean-inline mode: success, renamed success, publication failure, and source
  removal failure after renamed publication.
- Dry-run: planned success and planning failure, with no pipeline execution or
  publication.
- Preview: disposable scrub-and-audit success and failure, with no publication.

#### Discovery boundaries

- Empty input.
- Unsupported-only input.
- `--max-files` with supported, unsupported, temporary, and unstable entries on
  both sides of the limit; skipped JPEGs must not consume slots, while an
  admitted JPEG that later fails processing must consume one slot.
- `--max-files 0`, which must admit and examine no entries.
- The same file supplied twice and through overlapping explicit directories.
- Two hard-link paths for the same inode, which must produce separate outcomes
  and separate publication obligations.
- Hard-link aliases with different extensions, which must be classified by
  pathname independently rather than by whichever alias discovery sees first.
- Filesystems without stable identity support, exercising the normalized
  resolved-path fallback and its diagnostic.
- Files appearing, disappearing, or changing during discovery; deeper
  concurrent-mutation handling continues in 0.8.1.

#### Reporting compatibility

- Exactly one machine-readable final line on every controlled exit after report
  initialization.
- No summary for `--help`, `--version`, or argument-parser rejection.
- Quiet-mode success remains silent; quiet-mode failure replays diagnostics and
  the final report to standard error.
- Repository parsers require `schema=2`, consume the revised meanings, and
  reject legacy or unknown schemas with a clear migration diagnostic.
- Missing, duplicated, non-numeric, or internally inconsistent summary fields
  are rejected by repository wrappers.

Prefer real files, directories, subprocesses, and container integration tests.
Use monkeypatching only for OS-level failures that cannot be triggered
deterministically on real test filesystems, and document that reason beside the
test.

### Resolved decisions for 0.8.0

1. A published audited output followed by required source cleanup or archival
   failure has terminal outcome `FAILED`; `published=1` preserves the observed
   publication fact.
2. Successful dry-run and preview operations use distinct `PLANNED` and
   `PREVIEWED` outcomes. They are neither `SCRUBBED` nor `SKIPPED`.
3. Overlapping explicit inputs are deduplicated by normalized, resolved,
   non-symlink pathname. Distinct hard-link pathnames remain distinct inputs and
   publication obligations. Filesystem identity is retained for alias
   detection and pre-processing replacement checks, not as the deduplication
   key.
4. `total` retains its JPEG-only meaning throughout 0.8.x. Pre-classification
   failures are run-level failures and do not enter `total`.
5. Schema 2 retains legacy field names but is a documented semantic migration,
   not a claim of backward-compatible meanings. Repository consumers migrate
   atomically and reject unsupported schemas.
6. Reporting initializes after successful argument parsing and informational
   exits, before operational preflight. Every controlled exit from that point
   emits exactly one final machine-readable summary.
7. Duplicate verification is an orthogonal fact. Policy and disposition
   failures remain `FAILED` without discarding the verified-duplicate fact or
   observed source disposition.
8. `--max-files` counts JPEG candidates admitted for a scrub, dry-run plan, or
   preview attempt. Unsupported and `SKIPPED` inputs do not consume the limit;
   an admitted candidate still consumes its slot when processing later fails.
   Discovery stops as soon as the requested number has been admitted, so later
   entries are outside the run and its counters.

## 0.8.1 fault-resilience investigation targets

Build a fault matrix for every stage and verify both filesystem state and
reported outcome after failure:

- ExifTool and jpegtran missing, nonzero, malformed, warning, timeout, or
  terminated.
- Input read failure or source mutation during processing.
- Private temporary-directory creation, write, audit, and cleanup failure.
- Destination staging creation, copy, `fsync`, digest, link, replace, unlink,
  and directory-sync failure.
- Disk full and inode exhaustion in private temp, output, processed, errors, and
  state locations.
- Read-only directories and permission changes between preflight and use.
- Destination creation or replacement attempts by another process.
- Cross-filesystem archive success and failure.
- Cross-filesystem archive copy digest verification before source removal,
  including a forced mismatch that must retain the source.
- `SIGINT` and `SIGTERM` before audit, after audit, during publication, and
  during source finalization.
- Restart with operation-owned temporary remnants at every boundary.

The investigation should decide where directory `fsync()` is required for
durability, how subprocess timeouts are configured, how cooperative signal
handling interacts with the active file, and how stale operation-owned files
are recognized without touching unrelated files.

## 0.8.2 documentation and CLI audit

Create a testable inventory covering:

- Every README and `DETAILS.md` command example.
- Every CLI option, default, incompatibility, environment override, and exit
  status.
- Default, auto, clean-inline, dry-run, preview, paranoia, rename, duplicate,
  and quiet-mode behavior.
- Human and machine-readable summary definitions.
- Source and output disposition for every terminal outcome.
- Temporary-file, publication, archive, and restart behavior.
- Container mounts, permissions, UID/GID handling, and supported filesystem
  layouts.
- Wrapper assumptions and summary-schema compatibility.
- Safety boundaries covering visible image content, filenames, sidecars,
  unsupported formats, and independently verifying results for high-stakes use.

Where practical, convert documentation examples into executable tests so later
CLI changes cannot silently make the documentation stale.

## 0.9.x stabilization

After the 0.8.x work is complete:

- Freeze the intended 1.0 command-line surface and summary schema.
- Review naming, defaults, deprecations, and migration behavior.
- Resolve or explicitly defer known correctness and portability issues.
- Run extended soak, interruption, private-photo, and cross-filesystem tests.
- Accept fixes and clarification work without adding major new features.

## 1.0 readiness

The 1.0 release should have:

- A documented behavioral contract for every mode.
- Reconciled, versioned machine-readable outcomes.
- Release-gating tests for the publication and accounting invariants.
- A reviewed fault matrix with deterministic regression coverage.
- Executable documentation examples or equivalent integration coverage.
- A compatibility policy for the CLI, exit statuses, and summary schema.
- A concise list of supported inputs, operating assumptions, and known
  limitations.
