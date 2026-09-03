# Epoch dual-domain acceptance progress

## State

- Branch initialized at `af418dc7e0e65b306a753089b21798c9f07b6b8e` from the local `origin/epoch-dual-domain` ref.
- Consumer-row content-address verification is implemented, regression-tested,
  and independently reviewed.
- This lane has no network access; `git fetch origin` could not resolve GitHub.
- The sandbox does not expose the process list, so `pgrep -fl epoch-dual-domain` could not complete.
- Approved role: `ledger-contract-maintainer`.

## Done

- Confirmed the checked-out branch and local remote-tracking ref both matched the requested head.
- Read the repository instructions and the GitNexus debugging workflow; no GitNexus integration is available in this environment.
- The original dual-domain acceptance implementation established the frozen
  Ledger/Chronicle epoch registry while retaining Ledger as the single emission
  default.
- Fact, source lineage, and all eight consumer-contract hash builders accept both
  epochs with invariant digest suffixes.
- Dual-epoch lineage validation, consumer-row normalization, artifact loading,
  bundle ingestion, relational emission/load, source-package readers, offline
  fetch, administrative generators, and CLI coverage were completed before this
  review-fix assignment.
- Existing fixtures and consumer schema bytes were pinned, Chronicle and mixed-
  epoch database paths were covered, and adversarial governance and lineage
  findings from the original implementation were addressed.
- The prior lane passed 783 tests with 1 skipped, repository-wide Ruff lint,
  focused formatting, an offline wheel build/import smoke, and independent
  `ledger-contract` and `ledger-boundary` judge reviews.
- The prior lane confirmed no changes under `releases/`, frozen fixture/schema
  directories, or `.github/chronicle-agents.yml`.
- Replaced aggregate-only consumer verification with per-key recomputation for
  all nine row-carried identities, honoring each declared prefix epoch and using
  Ledger spellings for nested identity payloads.
- Added an 18-case tamper matrix covering every row-carried key under both
  Ledger and Chronicle prefixes.
- Preserved exact legacy fact-v1 verification for lossy normalized rows through
  a conditional audit envelope; labeled/authored-order constraints and evidence-
  only measure metadata now round-trip without changing existing key digests,
  frozen schema bytes, or checked-in fixture bytes.
- Passed 114 focused consumer, consumer-contract, and schema tests plus an
  80-row constrained consumer-artifact build/load smoke.
- An independent consumer-verification review approved the final implementation
  after adversarial coverage for rehashed legacy payload envelopes.

## Next

- Deduplicate cross-epoch source-cell and source-row aliases during database insertion.
- Exercise governance validation against both accepted approved-agent schema identifiers.
- Run the requested full verification matrix and record it in `out.md`.
- Write an uncommitted `PR_BODY_ADDENDUM.md` with the review-fix summary and allowed-path disclosure.
