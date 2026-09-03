# Chronicle PR #228 review-fix report

## Outcome

All three requested findings are fixed and committed locally on
`epoch-dual-domain`. Consumer rows now verify every reconstructible
content-addressed identity, relational builds collapse dual-epoch lineage
aliases before insertion, and the complete approved-agent governance check runs
against both accepted schema identifiers. `EMIT_EPOCH` remains
`Epoch.LEDGER`.

No changes were made under `releases/`; no fixture, golden, or frozen schema
bytes changed; and no environment, CLI, or bucket names were renamed. Nothing
was pushed because this lane has no network access. The dispatching session must
push the committed branch.

## Review fixes

### 1. Verify every consumer-row identity

- Replaced aggregate-only verification with independent recomputation of all
  nine row-carried identities: source release, source series, observed measure,
  dimension set, universe-constraint set, legacy fact, semantic fact, concept
  alignment, and aggregate fact.
- Each key is recomputed under the epoch declared by its own prefix. Composite
  payloads use verified Ledger spellings for nested identities, preserving the
  branch invariant and digest equivalence across epochs.
- Added a conditional legacy-key audit envelope only where normalized rows would
  otherwise lose legacy `fact-v1` preimage material (authored constraint labels
  or evidence-only/falsy measure metadata). The loader validates the envelope's
  exact canonical shape and visible projection before hashing it.
- Added the required 18-case key-tamper matrix: all nine identities, each with a
  valid Ledger or Chronicle prefix. Child-key cases also rehash the aggregate
  key, proving the old aggregate-only check would not have caught them.
- Added adversarial legacy-envelope coverage for authored order, labels,
  evidence-only metadata, missing keys, unknown keys, JSON numeric type changes,
  visible-field divergence, and forged alignment-trigger fields.

Focused result:

```text
114 passed, 12 warnings
```

An independent focused review returned **APPROVE** after the no-alignment
envelope regressions were added.

### 2. Deduplicate mixed-epoch relational lineage aliases

- Canonicalize source-cell and source-row keys to the requested emit epoch once
  per fact, then stably deduplicate them in first-seen order.
- Use the deduplicated keys for source-record counts and association inserts, so
  ordinals remain dense and the SQLite primary keys cannot collide.
- Added Ledger-emit and Chronicle-emit database builds where one fact carries
  both aliases of one real source cell and one real source row. Each build
  asserts one inserted edge per table, ordinal zero, counts of one, expected
  emitted key spelling, and an empty `PRAGMA foreign_key_check`.

Focused result:

```text
9 passed, 12 warnings
```

### 3. Exercise both governance schema identifiers

- Kept a separate assertion that the checked-in approved-agents manifest emits
  `schema_id("approved_agents")`, which is still the Ledger identifier.
- Parameterized the complete ownership, role, allowed-path,
  deterministic-check, and judge validation over both values in
  `SCHEMA_IDS["approved_agents"].accepted`.

Focused result:

```text
4 passed
```

An independent focused review returned **APPROVE**.

## Deterministic verification

The exact `uv run` commands used the already-provisioned sibling environment via
`UV_PROJECT_ENVIRONMENT=/Users/maxghenis/PolicyEngine/chronicle/.venv` and
`UV_NO_SYNC=1`, with a writable cache under `/tmp`. This avoided dependency
mutation after the worktree's normal cache proved inaccessible in the sandbox.

### Full test suite

```text
$ uv run pytest -q
822 passed, 1 skipped, 14 warnings in 1385.10s (0:23:05)
```

### Ruff lint

```text
$ uv run ruff check .
All checks passed!
```

### Ruff format

The exact requested directory-wide command exposes a pre-existing baseline
failure:

```text
$ uv run ruff format --check chronicle policyengine_chronicle tests
Would reformat: tests/test_chronicle_council_tax_nation_totals.py
Would reformat: tests/test_chronicle_facts_only.py
Would reformat: tests/test_chronicle_obr_council_tax_decomposition.py
Would reformat: tests/test_chronicle_pe_source_plan.py
Would reformat: tests/test_chronicle_source_rows.py
Would reformat: tests/test_etl_admin_packages.py
Would reformat: tests/test_etl_snap.py
Would reformat: tests/test_scale_value.py
8 files would be reformatted, 94 files already formatted
```

All eight reported files are byte-identical to `af418dc7` (`git diff
--exit-code` returned 0). The supplemental check over every Python file changed
by this review passes:

```text
6 files already formatted
```

Reformatting unrelated baseline files would widen PR #228, so they were left
unchanged.

### Offline wheel and clean-venv smoke

The runtime environment did not contain Hatchling. Supplying the already-cached
Hatchling build components and using `--offline --no-build-isolation` produced
the wheel without accessing the network:

```text
Building wheel...
Successfully built policyengine_chronicle-0.1.0-py3-none-any.whl
```

The wheel was installed with `pip --no-deps` into a new temporary Python 3.14
virtual environment. From outside the repository, the smoke loaded `chronicle`
from that environment's `site-packages`, confirmed distribution version
`0.1.0`, asserted `EMIT_EPOCH is Epoch.LEDGER`, and asserted the default fact
domain is `ledger.fact.v1`:

```text
wheel epoch import smoke: PASS
```

### Immutability and scope checks

- `git diff --check`: PASS.
- `EMIT_EPOCH = Epoch.LEDGER`: confirmed in `chronicle/epoch.py`.
- No changed paths under `releases/`, `chronicle/fixtures/`, `docs/schemas/`,
  `policyengine_chronicle/schemas/`, or `.github/chronicle-agents.yml`.
- Frozen fixture SHA-256 values remain:
  - `facts.jsonl`: `b0dd06765db7932c16a678b1ab321a7d908af26e2f2014d7da99c0eb5127e401`
  - `consumer_facts.jsonl`: `6123f1cca28ccc72c053b105b8d50b5c25a72a5f5b92e73e7219f32de152a96a`
  - `soi_table_1_1_2023_cells.jsonl`: `615639f21ee63e54595c677e24c3eddff484c00a795a2f91b45a8575f021c7e2`
- Both consumer-schema copies remain
  `76ac268e626c86146cee51193e0cbecbb197ddbf3bf410156fe7da7c0edae3ad`.

### Judge reviews and residual risk

- Independent `ledger-contract` judge: **PASS** (low risk). All nine row-carried
  identities, both database lineage domains, and both governance identifiers
  are covered under both epochs; emission and frozen bytes remain unchanged.
- Independent `ledger-boundary` judge: **PASS**. The changes only verify existing
  Chronicle identities/provenance and canonicalize alias spellings; no
  consumer-owned measurement or target-construction behavior was added.
- Previously generated lossy consumer rows that lack the new legacy-preimage
  envelope now fail closed because their exact legacy digest cannot be proven
  from their visible normalized fields. This is intentional integrity behavior;
  checked-in rows and ordinary reconstructible rows remain compatible.
- The conditional `legacy_fact_key_payload` layout extension introduces a small
  maintenance coupling to the private core legacy-payload builder. The frozen
  schema explicitly permits layout extensions, and dual-epoch plus adversarial
  regressions cover the coupling.

## Commits

Each commit carries the required Claude Fable co-author trailer:

- `d7a8b84` — Add review-fix progress journal
- `51938e8` — Verify every consumer row identity
- `74b6633` — Deduplicate database lineage aliases
- `3f33e7d` — Validate both governance schema epochs

## Role-path disclosure

Approved role: `ledger-contract-maintainer`.

The role directly permits `chronicle/consumer_contract.py` and
`policyengine_chronicle/**`. These required review-fix paths are outside its
narrow `allowed_paths` and are disclosed explicitly:

- `PROGRESS.md` and `out.md`: standing-order state and final report.
- `chronicle/database.py`: required relational alias-collision fix.
- `tests/test_chronicle_consumer.py`: required per-key, cross-epoch tamper
  regressions.
- `tests/test_chronicle_database.py`: required mixed-alias relational builds.
- `tests/test_chronicle_governance.py`: required dual-identifier governance
  coverage.

These changes enforce Chronicle identity, storage, and governance contracts.
They do not add source facts, reconcile sources, age or impute values, activate
targets, construct solver inputs, or add consumer model bindings.

## Lane limitations and handoff

- `pgrep -fl epoch-dual-domain` was attempted before repository mutation, but
  the sandbox could not access the macOS process-list service.
- `git fetch origin` and PR retrieval were attempted, but GitHub DNS/network
  access was unavailable. The local branch and local `origin/epoch-dual-domain`
  ref both matched the requested starting head
  `af418dc7e0e65b306a753089b21798c9f07b6b8e` before the hard reset.
- The supplied findings, local branch history/diff, and `chronicle/epoch.py`
  therefore served as the review specification in this lane.
- No push was attempted after implementation, per assignment. The dispatching
  session should push the locally committed branch and use the uncommitted
  `PR_BODY_ADDENDUM.md` handoff text.
