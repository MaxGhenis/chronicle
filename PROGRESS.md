# Concept-validation integrity correction — progress

## Scope
Bounded fix to `chronicle/concepts.py` for two demonstrated issues, per
`/Users/maxghenis/architecture-reviews/microcosm-20260904/chronicle-axiom-root-correction.md`:

1. `collect_concept_alignments` deduped on `(source_concept, canonical_concept,
   relation, legal_vintage)` via `dict.setdefault`, silently dropping distinct
   fact keys / source record IDs / authorities / evidence when two facts share
   those four fields but differ elsewhere.
2. `_run_axiom_validate` ignored the subprocess exit code and accepted a truthy
   non-boolean `valid` field, so a nonzero exit with `"valid": true` (or a
   non-boolean truthy value, or a mismatched `concept_id`) was reported as a
   successful concept check.

Base: `origin/main` @ `743742c27c4c86c95769e1a602aa7f7ddc7018d1` (verified —
branch was up to date with this commit, working tree was clean at start).
Python 3.14.6 / uv-managed `.venv`, `uv.lock` unmodified, no dependency change.

Allowed files: `chronicle/concepts.py`, `tests/test_chronicle_concepts.py`,
one short doc under `docs/schemas/`, plus this PROGRESS.md and (conditionally)
`.github/chronicle-agents.yml` allowed_paths for `ledger-contract-maintainer`.
No other files touched. No push/merge/deploy. No history/db/schema mutation.

## State: done, frozen for review

## Done
- [x] Verified worktree/branch/base match the assignment exactly.
- [x] Read AGENTS.md, .github/chronicle-agents.yml, chronicle/concepts.py,
      tests/test_chronicle_concepts.py, chronicle/core.py (build_fact_key /
      _canonical_key_payload), chronicle/harness.py + chronicle/suite.py call
      sites, and the root-correction report.
- [x] Confirmed baseline: `uv run pytest tests/test_chronicle_concepts.py -q`
      → 6 passed on the unmodified checkout.
- [x] Wrote 9 new failing tests covering: distinct fact/evidence survival,
      stable input order, missing evidence not hidden by a sibling's
      evidence, nonzero-exit-with-valid-true rejection, non-boolean valid
      rejection, missing valid field rejection, non-object/list JSON
      rejection, contradictory valid=true+errors rejection, mismatched
      concept_id rejection, timeout-is-not-success, and axiom-CLI call
      memoization for a repeated canonical concept. Confirmed red: 9 failed
      (3 of the "malformed response" cases already passed incidentally
      against the unmodified code — kept as explicit regression coverage).
- [x] Fixed `collect_concept_alignments`: dedup key is now the full
      `ConceptAlignment` tuple (all 9 fields), not a 4-field subset, so
      distinct fact_key/source_record_id/authority/evidence survive and
      only byte-identical assertions collapse. Preserves stable input order
      (dict insertion order) and the public dataclass/report shapes.
- [x] Fixed `_run_axiom_validate`: success now requires exit code 0 AND
      strict boolean `valid is True` AND no non-empty `errors` list AND a
      `concept_id` that matches what was requested. Malformed scalar/list
      JSON, a missing/non-boolean `valid` field, and any of the above
      mismatches are all normalized to an explicit invalid result carrying
      a specific error code. Added a bounded subprocess timeout (default
      30s, overridable via new `axiom_timeout` kwarg on
      `validate_concept_alignments`) that raises internally and is reported
      as an `axiom_cli_timeout` warning — never a success. Preserved the
      existing exit-1/valid=false error-message extraction path unchanged.
      Kept the no-command ("axiom_cli_not_configured") path fully unchecked,
      as before.
- [x] Added per-canonical-concept memoization inside one
      `validate_concept_alignments` call: a concept repeated across many
      alignments (now more of them survive, per the dedup fix) triggers one
      subprocess invocation, reused for every alignment sharing that
      concept; `checked_count` still counts alignments checked, not
      subprocesses run. Covered by a dedicated test using a call-counting
      fake CLI.
- [x] Confirmed green: `uv run pytest tests/test_chronicle_concepts.py -q`
      → **18 passed** (9 pre-existing + 9 new).
- [x] Ran broader regression coverage appropriate to this change:
      `tests/test_chronicle_harness.py`, `tests/test_chronicle_core.py`,
      `tests/test_chronicle_governance.py` — all pass, unaffected.
      `tests/test_chronicle_suite.py` (the other real caller of
      `validate_concept_alignments`, via `build_source_suite`) has **3
      pre-existing failures** that are a direct, correct consequence of the
      dedup fix — see "Known downstream consequence" below. Confirmed by
      grep that `test_chronicle_bundle.py`, `test_chronicle_database.py`,
      `test_chronicle_source_package.py`, `test_policyengine_chronicle_schema.py`,
      and `test_chronicle_consumer_contract.py` do not call any of
      `collect_concept_alignments` / `validate_concept_alignments` /
      `ConceptAlignment` / `_run_axiom_validate` — not relevant regression
      surface for this diff.
- [x] `uv run ruff format --check chronicle/concepts.py tests/test_chronicle_concepts.py`
      → both already formatted. `uv run ruff check` on the same two files →
      all checks passed.
- [x] Added `docs/schemas/concept-alignment-report.md`: explains alignment
      identity (full-tuple dedup), `checked_count` vs. subprocess-call count
      under memoization, exact Axiom-CLI success criteria, and states
      plainly that the `concepts validate --json` protocol this module
      implements has no verified production counterpart (per the
      root-correction report) — only a fake test executable exercises it.
      No claim of semantic equivalence to real Axiom validation is made.
- [x] Added exactly `chronicle/concepts.py` to `ledger-contract-maintainer`'s
      `allowed_paths` in `.github/chronicle-agents.yml` (it was previously
      unowned by any approved agent role); roles/checks/judges unchanged.
      `tests/test_chronicle_governance.py` still passes (2/2).
- [x] Final diff vs. `origin/main`: exactly the 5 allowed/expected files
      touched — `.github/chronicle-agents.yml` (+1), `PROGRESS.md`,
      `chronicle/concepts.py` (+125/-57), `docs/schemas/concept-alignment-report.md`
      (new, +75), `tests/test_chronicle_concepts.py` (+325). Nothing else in
      the working tree; `git status` clean apart from tracked commits.
      `source_package.py` was not touched (confirmed no reference to
      `concepts.py` symbols from it — the other branch's work is untouched).
- [x] Froze a clean local commit; verified its actual message with
      `git log -1 --format=%B`.

## Known downstream consequence (out of scope for this task, not fixed here)
`tests/test_chronicle_suite.py` has three hardcoded goldens that assumed the
old, bug-collapsing behavior:
- `test_build_source_suite_writes_artifacts_and_reports`:
  `concept_alignment_count` was `1`, is now `20` (80 SOI table-1-1 facts that
  each separately assert the same source→canonical mapping now correctly
  count as 20 distinct assertions instead of being collapsed into 1).
- `test_build_source_suite_supports_soi_table_1_4`: `concept_alignment_count`
  was `8`, is now `160` (580 facts across 8 canonical concepts, same
  20-facts-per-concept pattern).
- `test_build_source_suite_accepts_required_axiom_validation`: asserted
  `checked_count == 1`, is now `20` for the same reason.
This is the intended, correct effect of fixing issue 1 — the old counts
encoded the very evidence-hiding bug this task was scoped to fix. That test
file is not in this task's allowed-files list (`chronicle/concepts.py`,
`tests/test_chronicle_concepts.py`, one `docs/schemas/` doc, plus this
PROGRESS.md and the one governance-YAML path), so it is deliberately left
unmodified here. It needs a follow-up change (in-scope for whoever owns
`tests/test_chronicle_*.py` under `.github/chronicle-agents.yml`, currently
`ledger-source-ingestor`) to update those three goldens to the corrected
counts. Not a blocker for this task's own correctness or test suite.

## Next
- None for this task's scope. Awaiting Root/Fable review before integration.
  Follow-up (separate, out-of-scope task): update the three
  `tests/test_chronicle_suite.py` goldens named above.
