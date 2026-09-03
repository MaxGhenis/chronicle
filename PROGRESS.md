# Epoch dual-domain acceptance progress

## State

- Branch initialized at `af418dc7e0e65b306a753089b21798c9f07b6b8e` from the local `origin/epoch-dual-domain` ref.
- Review findings are being implemented and regression-tested one coherent change at a time.
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

## Next

- Fix consumer-row verification so every content-addressed key is recomputed under its declared epoch, with nested identities in ledger form.
- Deduplicate cross-epoch source-cell and source-row aliases during database insertion.
- Exercise governance validation against both accepted approved-agent schema identifiers.
- Run the requested full verification matrix and record it in `out.md`.
- Write an uncommitted `PR_BODY_ADDENDUM.md` with the review-fix summary and allowed-path disclosure.
