# Output Safety Plan

## Security promise

No unaudited or policy-noncompliant JPEG may be published as a scrubexif output.
This protects metadata privacy; it does not inspect sensitive information that
is visible in image pixels, filenames, or sidecar files.

## Agreed behavior

The following decisions incorporate the review discussion:

1. Scrubbing, ICC extraction, and metadata rebuilding happen in a fresh private
   system temporary directory, outside the output folder.
2. A production, standard-library JPEG/TIFF/XMP auditor parses the completed
   result independently of ExifTool. Normal mode permits only the documented
   technical allowlist, ICC, and explicitly requested stamps. Paranoia mode
   rejects EXIF, XMP, ICC, comments, unknown APP metadata, thumbnails, and
   trailing bytes.
3. Only bytes that pass the audit may be copied into an exclusively created
   hidden `.part` file in the destination directory. Its SHA-256 digest must
   still match the privately audited file before atomic publication.
4. Every operation cleans up only temporary paths that it created. Failures are
   reported with the affected input and reason, and cause a nonzero final status.
5. ExifTool warnings, malformed JSON, unexpected extracted keys, unsupported
   values, write-back warnings, and any auditor uncertainty fail closed.

## Existing destination decision table

An existing filename is not assumed to be a duplicate. Scrubexif audits the
existing output, independently scrubs and audits the incoming file, and compares
the two audited byte streams.

| Condition | Source handling | Existing output | Final status |
|---|---|---|---|
| Identical audited bytes, default `move` | Move incoming original to `errors/` | Unchanged | Success |
| Identical audited bytes, strict `fail` | Leave incoming original in `input/` | Unchanged | Nonzero duplicate failure |
| Identical audited bytes, explicit `delete` | Delete incoming original | Unchanged | Success |
| Same filename, different content | Move incoming original to `errors/` under default `move`; otherwise leave it in `input/` | Unchanged | Nonzero collision |
| Existing output fails audit | Leave incoming original in `input/` | Untrusted file is neither modified nor deleted | Nonzero safety failure |
| Duplicate/collision archive fails | Leave incoming original in `input/` | Unchanged | Nonzero archival failure |

The default is `--on-duplicate move`, because accidental re-uploads are common
and retaining the original is safer. Users who need stricter handling can select
`--on-duplicate fail`, which leaves the source in place and makes the run fail.
Users who intentionally want deletion may select `--on-duplicate delete`;
deletion occurs only for a verified duplicate.

“Existing output fails audit” means scrubexif cannot prove that the file already
in the output directory satisfies the current scrub policy. It therefore does
not use that file as evidence of a duplicate, does not replace or repair it, and
does not remove the new source. Other files in a batch may continue, but the run
cannot report success.

## Verification and release gate

The required privacy tests cover both permitted and rejected metadata policies,
pipeline success that silently returns an unsafe JPEG, destination auditing,
verified duplicates, same-name content collisions, corrupted inputs, staging
location, and cleanup. Real-camera private assets remain useful as an additional
local test corpus, but the release gate relies on committed deterministic tests
so it cannot silently disappear when those private files are unavailable.

The implementation is complete only when focused tests, container integration
tests, the explicit release privacy gate, and the normal test suite all pass.
