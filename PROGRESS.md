# Epoch dual-domain acceptance progress

## State

- Branch initialized at `af418dc7e0e65b306a753089b21798c9f07b6b8e` from the local `origin/epoch-dual-domain` ref.
- Consumer-row content-address verification is implemented, regression-tested,
  and independently reviewed.
- Relational lineage insertion now collapses cross-epoch aliases before counts
  and association inserts; both emit epochs are regression-tested.
- Governance emission and acceptance are tested separately; the complete review
  surface runs against both approved-agent schema identifiers.
- Implementation and deterministic verification are complete locally. The full
  suite passes with 822 tests passed and 1 skipped; repository lint, changed-file
  formatting, and the offline wheel/import smoke pass.
- Independent final `ledger-contract` and `ledger-boundary` reviews pass.
- The exact directory-wide format check still reports eight files that are
  byte-identical to the requested starting head and outside this review diff.
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
- Canonicalized and stably deduplicated source-cell and source-row aliases before
  relational insertion, keeping association ordinals and source-record counts
  consistent; 9 focused database tests pass.
- Kept the checked-in approved-agents manifest on the Ledger default while
  exercising every ownership, role, path, deterministic-check, and judge
  assertion with both Ledger and Chronicle identifiers; 4 focused tests pass.
- Passed `uv run pytest -q` (822 passed, 1 skipped, 14 warnings),
  `uv run ruff check .`, the format check over all six changed Python files,
  an offline wheel build, and a clean-venv epoch import smoke.
- Confirmed `EMIT_EPOCH = Epoch.LEDGER`, no changes under `releases/` or frozen
  fixture/schema paths, and unchanged pinned SHA-256 values.
- Passed independent final `ledger-contract` and `ledger-boundary` reviews.
- Wrote the complete review-fix and verification report to `out.md`.

## Next

- Write an uncommitted `PR_BODY_ADDENDUM.md` with the review-fix summary and
  allowed-path disclosure.
- Leave the committed branch for the network-enabled dispatching session to push.
