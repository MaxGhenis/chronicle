# Concept-validation integrity correction — progress

## Scope
Bounded fix to `chronicle/concepts.py` for two demonstrated issues, per
`/Users/maxghenis/architecture-reviews/microcosm-20260904/chronicle-axiom-root-correction.md`:

1. `collect_concept_alignments` dedupes on `(source_concept, canonical_concept,
   relation, legal_vintage)` via `dict.setdefault`, silently dropping distinct
   fact keys / source record IDs / authorities / evidence when two facts share
   those four fields but differ elsewhere.
2. `_run_axiom_validate` ignores the subprocess exit code and accepts a truthy
   non-boolean `valid` field, so a nonzero exit with `"valid": true` (or a
   non-boolean truthy value) is reported as a successful concept check.

Base: `origin/main` @ `743742c27c4c86c95769e1a602aa7f7ddc7018d1` (verified —
branch is up to date with this commit, working tree was clean at start).

Allowed files: `chronicle/concepts.py`, `tests/test_chronicle_concepts.py`,
one short doc under `docs/schemas/`, plus this PROGRESS.md and (conditionally)
`.github/chronicle-agents.yml` allowed_paths for `ledger-contract-maintainer`.
No other files touched. No push/merge/deploy. No history/db/schema mutation.

## State: in progress

## Done
- [x] Verified worktree/branch/base match the assignment exactly.
- [x] Read AGENTS.md, .github/chronicle-agents.yml, chronicle/concepts.py,
      tests/test_chronicle_concepts.py, chronicle/core.py (build_fact_key /
      _canonical_key_payload), chronicle/harness.py CLI call sites, and the
      root-correction report.
- [x] Confirmed baseline: `uv run pytest tests/test_chronicle_concepts.py -q`
      → 6 passed on the unmodified checkout.

## Next
- [ ] Write failing tests for issue 1 (dedup/evidence-loss) and issue 2
      (exit-status / non-boolean valid / mismatched concept_id / timeout).
- [ ] Confirm red.
- [ ] Fix `collect_concept_alignments` to preserve all distinct complete
      `ConceptAlignment` assertions in stable order, collapsing only exact
      duplicates; keep the fixture whose only difference is fact value
      deduped (identical fact identity/evidence).
- [ ] Fix `_run_axiom_validate` to require exit 0 AND strict boolean
      `valid: true`, add a bounded timeout with clear unavailable reporting,
      and reject mismatched/malformed `concept_id`.
- [ ] Confirm green; run full relevant regression suite.
- [ ] Add short doc under `docs/schemas/` on report semantics + remaining
      real-Axiom-interface gap (no invented protocol claims).
- [ ] Governance: add `chronicle/concepts.py` to `ledger-contract-maintainer`
      allowed_paths in `.github/chronicle-agents.yml` if required.
- [ ] Lint/format, final diff review, freeze commit, write author report.
