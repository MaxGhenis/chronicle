# Operational rename, slice 1 (chronicle#143, mechanism 3)

Lane C5's handoff notes previously lived here; its durable record is
`LANE_C5_REPORT.md`. This file now tracks the active lane on this branch.

## State

- Branch: `ops-rename-slice1`, cut from `origin/main` at `ff3efd3`.
- Scope: env names, R2 bucket configurability, `ledger.db` -> `chronicle.db`,
  and the docs for all three. Code and docs only; no infrastructure changes.
- Out of scope and deliberately untouched: the `ledger` console-script alias,
  the Supabase `"ledger"` schema and mirror table names, governance role ids and
  concept authorities, hash domains and schema ids, anything under `releases/`.
- This PR does not touch the source-data boundary. No package spec, parser,
  selector, manifest, or fact value changes.

## Done

- Read `AGENTS.md`, `docs/storage-architecture.md`,
  `docs/agent-source-package-harness.md`, and the mechanism-3 migration spec in
  the first comment of PolicyEngine/chronicle#143.
- Enumerated every ledger-named env read in tracked Python: the four real
  variables (`LEDGER_SOURCE_ARTIFACT_CACHE_DIR`, `LEDGER_SOURCE_ARTIFACT_FETCH`,
  `LEDGER_PE_US_DATA_ROOT`, `LEDGER_PE_UK_DATA_ROOT`) plus
  `POLICYENGINE_LEDGER_SCHEMA`. `LEDGER_MIRROR_TABLES`,
  `LEDGER_MIRROR_PRIMARY_KEYS`, and `LEDGER_DB_SCHEMA_VERSION` are module
  constants, not env reads, and name out-of-scope surfaces.
- Added `chronicle/env.py`: one shared `env_value`/`env_flag`/`env_names`
  helper reading `CHRONICLE_<X>` first, then `POLICYENGINE_LEDGER_<X>` and
  `LEDGER_<X>` with a once-per-process `ChronicleEnvDeprecationWarning` naming
  the preferred variable.
- Replaced all three ad-hoc helpers (`db/supabase_client._env`,
  `chronicle/source_package._env_value`/`_truthy_env`,
  `db/pe_source_inventory._env_value`) with the shared helper.
- Made the R2 bucket names configurable via `CHRONICLE_R2_RAW_BUCKET` and
  `CHRONICLE_R2_DERIVED_BUCKET`, plumbed through fetch-artifact, publish-raw,
  publish-derived and bootstrap-r2. Defaults unchanged at `ledger-raw` and
  `ledger-derived`. Both manifest write paths now preserve a recorded
  `storage.r2` block instead of restating it under a renamed bucket.
- Emitted `chronicle.db` for new suite outputs, with `ledger.db` still accepted
  on read and on derived-artifact kind inference.
- Added `tests/test_chronicle_env.py` plus artifact tests: 75 hermetic tests
  covering the lookup ladder, precedence, the once-per-process warning, and every
  real call site.
- Swept the docs. `docs/storage-architecture.md` gained an "Environment Variable
  Rename Window" section (the old text stated the fallback direction backwards)
  and a "Bucket Cutover" section; `docs/agent-source-package-harness.md` and
  `README.md` follow. Verified 186 distinct `ledger-raw` objects across 154
  tracked manifest files, every key content-addressed by sha256.

## Review fixes (gate round 1)

The Fable+Sol gate requested changes; both findings are applied on this branch.

- **[high] `fetch-artifact` could attach a recorded R2 URI to new bytes.** The
  preserve rule keyed on the bucket, so a repeated fetch that did not re-upload
  into the same bucket kept the recorded `storage.r2` block while rewriting the
  entry's `sha256`/`size_bytes`. Reproduced against this branch's parent by
  serving two different bodies from one URL: the entry ends up declaring the
  fetched bytes' `sha256` under a key addressed by the superseded bytes' one,
  both when the fetch only registers the bytes and when the bucket default has
  moved.
  The rule now keys on identity — the recorded key's `{sha256}/{filename}` tail
  against the fetched bytes. Identical preserves the block exactly; different
  raises `SourceArtifactRevisionError` before the cached artifact or its
  manifest entry is touched, naming recorded and fetched `sha256`/`size_bytes`
  and the ADR rule that same vintage plus new bytes is a new release revision.
  `--record-revision` opts in: the new bytes get their own content-addressed key
  under the configured bucket, never the old key, and the superseded block moves
  to `storage.previous_r2`. `publish-raw` applies the same check before treating
  a recorded block as history (`recorded_r2_identity_mismatch`, nothing
  uploaded).
- **[low] Env isolation was scoped to one module.** The autouse fixture moved to
  `tests/conftest.py` and now clears all three prefixes for every test.
  `db.supabase_client` resolves `LEDGER_SCHEMA` at import — during collection,
  before any fixture — so `tests/test_chronicle_namespace.py` re-imports it
  under the cleared environment instead of asserting the constant it bound at
  collection time.

`storage.previous_r2` is a sibling key, chosen because every reader
(`inventory-artifacts`, `publish-raw`, `source_package._artifact_content`, the
suite's raw-R2-link acceptance check) reads `storage.r2` alone, and
`publish-raw` already spreads the rest of the `storage` block when it writes
back, so a revision survives publication untouched. All 180 tracked manifest
entries that carry a `storage.r2` block are content-addressed and agree with
their declared `sha256` and `filename`, so the identity check never fires on
tracked data.

## Review fixes (gate round 2)

The second Fable+Sol gate requested changes again. Seven findings, each fixed
with a regression test on this branch. Plan, in dependency order:

1. **[high] `CHRONICLE_SCHEMA` does not reach the Supabase mirror writer.**
   `chronicle/harness.py` and `chronicle/mirror.py` default the schema to the
   literal `"ledger"`; only `db.supabase_client` reads the renamed variable.
   Resolve through the shared helper whenever no explicit `--schema` is given.
2. **[high] The derived-fact boundary check is not rename-safe.**
   `chronicle/consumer_contract.py` matches the `.ledger_derived` suffix only.
3. **[high] `fetch-artifact` cannot address a package's non-default manifest.**
   Seven tracked packages keep a `manifest_*_source_package.yaml`; three
   directories keep two. A fetch into one of them writes a third manifest and
   never sees the recorded block.
4. **[high] Revision protection vanishes when the entry has no `storage.r2`.**
5. **[medium] Recorded-R2 locator fields must be cross-checked**, not read as
   key-or-URI, before a block is preserved or published.
6. **[medium] `_read_manifest` must reject a malformed document**, not treat a
   non-mapping YAML payload as an absent manifest.
7. **[low] Schema resolution must be lazy** so no legacy variable is read at
   collection, before the autouse isolation fixture runs.

## State (round 2)

- Read both gate rounds on PolicyEngine/chronicle#226 and the code each finding
  names.
- Scanned all 154 tracked manifest files (187 `files` entries, every one
  carrying `storage.r2`): every recorded block supplies provider, bucket, key
  and uri; every key is content-addressed; every declared `sha256`/`filename`
  agrees with its key tail; no `uri` contradicts its `key`. Strict locator
  validation therefore refuses nothing that is tracked today.
- All seven findings are applied, each with a regression test, and each
  reproduced against this branch's previous head (`34d1d0f`) first.

### What each fix does

1. `chronicle/env.py` gains `default_chronicle_schema()`: one home for the
   `CHRONICLE_SCHEMA` -> `POLICYENGINE_LEDGER_SCHEMA` -> `LEDGER_SCHEMA` ->
   `"ledger"` ladder. `load_supabase_mirror`, its harness wrapper and the
   `--schema` CLI default all resolve through it when no schema is supplied;
   an explicit `--schema` still wins. Defaults unchanged.
2. `chronicle/consumer_contract.py` matches the whole final dot-segment of a
   `source_record_id` against both `ledger_derived` and `chronicle_derived`.
3. `fetch-artifact --manifest <filename>` selects which of a package's
   manifests the entry belongs to (default `manifest.yaml`); the name must be
   a filename inside `--out-dir`.
4. Revision protection now compares against the entry's recorded identity --
   the recorded key's `{sha256}/{filename}` once published, the declared
   `sha256` before that -- so a registered-but-unpublished entry, or one whose
   upload failed, is protected exactly like a published one.
5. `_validated_recorded_r2` cross-checks every supplied locator field against
   every other and against the content-addressed key shape. A contradiction is
   `RecordedR2LocatorError` at fetch time and `recorded_r2_locator_invalid` at
   publish time, never a preserved block.
6. `_read_manifest` refuses a non-mapping or unparseable document
   (`MalformedManifestError`) before the publisher is read at all;
   `inventory-artifacts` and `publish-raw` report it instead of crashing.
7. `db.supabase_client` resolves both schemas per call rather than at import,
   and `tests/conftest.py` strips the rename window in `pytest_configure`, so
   no module can read or warn from an operator's shell during collection.

All four refusals share a `SourceArtifactManifestError` base, so the
`fetch-artifact` CLI reports every one as exit 1 with nothing written.

### Reproduced against `34d1d0f` (the round-1 head)

Running the same operations against a checkout of the previous head:

1. `load_supabase_mirror` default `schema='ledger'`; with
   `CHRONICLE_SCHEMA=chronicle_probe` the load still reports `schema='ledger'`.
2. `'.chronicle_derived'.endswith('.ledger_derived')` is False: the boundary
   never fired for the chronicle spelling.
3. `fetch_source_artifact()` rejects `manifest_filename` as an unexpected
   keyword; a fetch into `ira_contributions/` writes `manifest.yaml`.
4. A fetch of different bytes over a registered (unpublished) entry was
   accepted silently: the entry's `sha256` was rewritten with no refusal.
5. A block whose `key` and `uri` named different objects was preserved
   verbatim, key sha `c63744a4...` beside uri sha `1e9b3fdb...`.
6. A list-valued `manifest.yaml` was overwritten by the fetch.
7. Importing `db.supabase_client` under `LEDGER_SCHEMA=zzz` bound
   `LEDGER_SCHEMA='zzz'` and emitted a `FutureWarning` at collection.

## Verification

- `uv run pytest -q`: green.
- `uv run ruff check .`: clean.
- `uv run ruff format --check .`: clean for every file this branch touches. 13
  files are unformatted on `main` already and are byte-identical here; CI runs
  `ruff check` only, so they are pre-existing and out of scope.
- CI's db CLI gate (`chronicle init` / `load all` / `stats`): passes.
- `CHRONICLE_R2_RAW_BUCKET=zzz CHRONICLE_SCHEMA=zzz uv run pytest -q`: green.
  Before the shared fixture it failed five tests — four bucket-default
  assertions in `tests/test_chronicle_artifacts.py` and the collection-time
  schema constant in `tests/test_chronicle_namespace.py`.

## Next

- Push and open the PR against `main`. Do not merge.
- Follow-up PR, after Max creates and backfills the new buckets: flip
  `DEFAULT_R2_RAW_BUCKET` / `DEFAULT_R2_DERIVED_BUCKET` to `chronicle-raw` /
  `chronicle-derived`.
