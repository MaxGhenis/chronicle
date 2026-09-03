# Microdata registration review fixes (chronicle#227)

## State

- Branch: `microdata-registration`, with reviewed head `2961c69d` rebased onto
  `origin/ops-rename-slice1` at `ea67f0a0` before round-1 gate fixes.
- Scope: close all listed artifact-registration boundary escapes, adopt the raw
  microdata ADR contract, and align registrations with microcosm#853.
- Roles: `ledger-source-ingestor` (manifests) and
  `ledger-contract-maintainer` (manifest schema and validation code).
- No microdata bytes may enter Chronicle for licensed or restricted releases.
  No work belongs under `releases/`.

## Done

- Attempted the required competing-agent process check; this sandbox denies
  both `pgrep` and `ps` process-table access.
- Attempted `git fetch origin`; DNS is unavailable. Verified the cached remote
  refs exactly match the supplied heads: `2961c69d` and `ea67f0a0`.
- Hard-reset the branch to `origin/microdata-registration` and completed the
  requested rebase onto `origin/ops-rename-slice1`.
- Resolved rebase conflicts in `PROGRESS.md`, `chronicle/artifacts.py`,
  `chronicle/harness.py`, and `chronicle/source_package.py`, keeping #226's
  strict reader, named-manifest routing, revision protection, R2 locator
  validation, bucket configuration, and Chronicle namespace changes together
  with this branch's access, kind, registration, and hash-only guards.
- Ran the rebased artifact and registration tests: 136 passed and nine exposed
  one integration defect in `inventory_source_artifacts` (the strict manifest
  read did not bind the payload used by the kind checks). Corrected the binding.
- Read the accepted raw-microdata ADR from cached immutable ref `92a44bf`, the
  approved-role configuration, and both cached Microcosm refs. GitHub comment
  retrieval was attempted but DNS is unavailable; the assignment's quoted
  round-1 findings and convention ruling are the review transcript in use.
- Hardened filenames, semantic year keys, strict pre-I/O manifest validation,
  public-to-gated reclassification, expected fetch pins, shape-preserving
  upserts, public-microdata licence custody, and checksum attestations.
- Moved the licensed/restricted refusal to the source package's lowest byte
  reader, before packaged bytes, cache bytes, cache fetches, or build parsing.
- Added focused regressions for the boundary escapes. The registration suite is
  94 passed with only the deliberately stale committed-manifest inventory test
  outstanding; the five new source-reader path checks pass.
- Replaced stale FRS 2023-24 registrations with FRS 2024-25 (SN 9563) and added
  WAS round 8, LCFS 2023-24, and ETB 1977-2024 alongside SPI PUT 2022-23: five
  publisher-release packages, 19 immutable hashes, and no microdata bytes.
- Made generator inputs explicit temporary files from Microcosm `origin/main`,
  emitted structured `consumer_pin` attestations, derived access from artifact
  kind, and made public fetch plans carry publisher, vintage, filename, SHA-256,
  and size pins.
- Checked in synthetic source-stage fixtures and a golden covering selection,
  emit, committed manifests, and plan. The focused suite is 104 passed and the
  repository inventory is valid with exactly 19 hash-only registrations.
- Added the explicit public-object removal evidence path; a public identity is
  never silently reclassified and a still-recorded R2 object always refuses.
- Closed the post-commit audit gaps: microdata access is explicit; manifest
  source/package identity, duplicate basenames, and complete registration
  shape are refused before I/O; hash-only registration cannot claim
  `chronicle_fetch` and honors a named `--manifest`; reviewed size-only pins
  survive planning.
- Added artifact-bound `licence_evidence` (issuer, licence identifier/version,
  scope statement, durable URL, and covered SHA-256). Public microdata uses an
  ignored transient staging directory outside `db/data`, requires successful
  R2 upload, leaves no local payload, and is refused by `publish-raw` if bytes
  appear beside its manifest.
- Expanded the synthetic golden to assert the exact five committed packages
  and 19 entries. The focused registration/generator suite is 123 passed.

## Next

- Run the full requested verification, write `out.md`, update the PR body, and
  push with `--force-with-lease`. Do not merge.
