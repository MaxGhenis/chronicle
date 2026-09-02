# Chronicle Storage Architecture

This note is the canonical storage plan for Chronicle while the source-package
harness stabilizes. The detailed agent workflow remains in
`docs/agent-source-package-harness.md`; this document only defines where each
class of Chronicle data belongs.

## Decision Summary

Chronicle uses three storage layers with different jobs.

The raw archive is the immutable source-byte store. It holds exact publisher
artifacts as fetched: workbooks, CSVs, PDFs, ZIPs, HTML snapshots, and similar
government-statistics release files. Raw objects are
content-addressed by checksum and should never be overwritten in place.

The derived archive is the reproducible artifact store. It holds build outputs
that Chronicle can regenerate from raw bytes, package specs, parser code, and build
configuration. Examples include parsed-cell or parsed-row Parquet/JSONL files,
source record outputs, `chronicle.db`, mirror JSONL exports, QA reports, Data
Package metadata, and RO-Crate metadata.

Both bucket names are configuration, not constants. The raw archive is
`$CHRONICLE_R2_RAW_BUCKET` and the derived archive is
`$CHRONICLE_R2_DERIVED_BUCKET`; the shipped defaults are still the ledger-era
`ledger-raw` and `ledger-derived`. [Bucket Cutover](#bucket-cutover) records how
those defaults move to `chronicle-raw` and `chronicle-derived` and why the
ledger-era buckets are preserved read-only rather than retired.

Supabase/Postgres is the queryable relational registry for accepted Chronicle
builds. It stores rows that applications, agents, and downstream systems need
to search and join: source artifacts, source rows/cells, source records,
aggregate facts, aggregate constraints, concept alignments, lineage edges, build
metadata, validation status, and object-location pointers. Supabase is not the
source of raw bytes and should not be hand-edited as the ingestion authority.

The deterministic local build remains the authority for source-backed facts.
Hosted tables mirror accepted build outputs and provide a shared query surface.

## Ownership Matrix

| Data class | Git/local package | Raw R2 | Derived R2 | SQLite `chronicle.db` | Supabase/Postgres |
|------------|-------------------|--------|------------|-----------------------|-------------------|
| Source package specs | Authoritative YAML and parser code | No | Optional packaged snapshot | No | Metadata only |
| Raw publisher files | Tiny fixtures only | Authoritative bytes | No | Metadata only | Metadata plus R2 pointer |
| Source manifests | Authoritative checked metadata | No | Optional snapshot | Metadata loaded into tables | Queryable artifact registry |
| Parsed source rows/cells | Generated local output | No | Snapshot artifact | Queryable table | Queryable mirror |
| Source records/facts | Generated local output | No | Snapshot artifact | Queryable table | Queryable mirror |
| Aggregate constraints | Generated local output | No | Snapshot artifact | Queryable table | Queryable mirror |
| Build reports and QA | Generated local output | No | Snapshot artifact | Build summary rows | Queryable validation status |
| Mirror JSONL exports | Generated local output | No | Snapshot artifact | Export source | Bulk-load input |
| Microcosm active targets | No | No | No | No | Future adapter output outside Chronicle core |

## Object Key Conventions

New UK and New Zealand source artifacts use country-organized,
content-addressed keys:

```text
raw/{country}/{source_id}/{package_id}/{year}/{sha256}/{filename}
```

For example, an IRD artifact uses:

```text
raw/nz/ird/ird-working-for-families-statistics-sept-2025/2024/{sha256}/working-for-families-statistics---sept-2025.xlsx
```

The implemented country segments are `nz` and `uk`. US objects deliberately
retain the legacy shape `raw/{source_id}/...`; migrating those keys requires a
separate consumer audit. The fetch and raw-publish commands infer the country
from the package publisher directory. Raw publication refuses to replace a
manifest-recorded key that disagrees with the inferred country path.

New UK and New Zealand derived build artifacts use the same country segment
and build-scoped keys so different builds can coexist and be audited:

```text
derived/{country}/{source_id}/{package_id}/{year}/{build_id}/{artifact_name}
```

Examples:

```text
derived/uk/ons/ons-mye-2024-uk/2024/{build_id}/source_cells.jsonl
derived/nz/ird/ird-working-for-families-statistics-sept-2025/2024/{build_id}/chronicle.db
```

Legacy US derived keys likewise remain `derived/{source_id}/...`.

Derived artifacts are reproducible and may be replaced by a new build, but a
specific `{build_id}` path should be immutable once published.

## Relational Registry Contract

The hosted `chronicle` schema should be the lookup surface for Chronicle, not the place
where agents invent source facts. Rows should be bulk-loaded from deterministic
build outputs.

The registry should expose:

- source artifact identity: source name, table/file, URL, vintage, extraction
  date, extraction method, checksum, size, and raw R2 bucket/key/URI;
- source rows/cells and source records, including exact source-row and
  source-cell lineage;
- source columns and source-row values, so row-oriented artifacts are queryable
  by raw or normalized column names without JSON scans;
- aggregate facts and aggregate constraints with stable keys, dimensions,
  filters, units, aggregation semantics, labels, and source provenance;
- concept alignments, including source concept, canonical concept, relation,
  authority, legal vintage, and evidence;
- build metadata, validation status, and derived artifact R2 bucket/key/URI.

The current Supabase migration mirrors the core relational tables and includes
R2 location fields for raw source artifacts and derived build artifacts, so the
registry can serve as the shared index over both R2 buckets.

## Build And Publish Flow

The intended flow is:

1. Register raw source artifacts with `uv run chronicle fetch-artifact`, which
   writes local bytes, records checksums in `manifest.yaml`, and can upload the
   exact bytes to the raw archive. Existing manifest-declared artifacts can be
   checksum-validated, uploaded, and linked with `uv run chronicle publish-raw`.
   Production package specs may omit raw bytes from Git as long as the manifest
   keeps `source_url` and SHA-256 metadata; builds can fill
   `CHRONICLE_SOURCE_ARTIFACT_CACHE_DIR` by setting
   `CHRONICLE_SOURCE_ARTIFACT_FETCH=1`. Ledger-era spellings of both still work;
   see [Environment Variable Rename Window](#environment-variable-rename-window).
2. Validate and build a source package with `uv run chronicle validate-package` and
   `uv run chronicle build-suite`.
3. Produce local deterministic outputs: parsed rows/cells, source records,
   aggregate facts, `chronicle.db`, QA reports, Data Package metadata, and
   RO-Crate metadata. Builds before this rename wrote `ledger.db`; every reader
   still accepts that name.
4. Export relational mirror files with `uv run chronicle export-db-tables`.
5. Publish derived build outputs to the derived archive:

   ```bash
   uv run chronicle publish-derived \
     --dir /tmp/chronicle-suite \
     --source-id irs_soi \
     --package-id soi-table-1-1 \
     --year 2023 \
     --build-artifacts-out /tmp/chronicle-build-artifacts.jsonl
   ```

6. Bulk-load or upsert accepted relational rows into Supabase/Postgres:

   ```bash
   uv run chronicle load-supabase-mirror \
     --dir /tmp/chronicle-mirror \
     --build-artifacts /tmp/chronicle-build-artifacts.jsonl
   ```

The Supabase project must have the checked migration applied and the `chronicle`
schema exposed in PostgREST/Data API settings before the REST loader can write
to it. Use `--dry-run` to verify local JSONL files without writing.

## Environment Variable Rename Window

Every Chronicle setting is read chronicle-first by one shared helper,
`chronicle/env.py`. For a setting `X`, the lookup order is:

1. `CHRONICLE_X`
2. `POLICYENGINE_LEDGER_X`
3. `LEDGER_X`

The first name that holds a non-empty value wins. When that name is a ledger-era
one, the process emits a single `ChronicleEnvDeprecationWarning` naming the
`CHRONICLE_`-prefixed variable to set instead. The warning fires once per legacy
name per process, and it subclasses `FutureWarning` rather than
`DeprecationWarning` so it actually reaches operators running the CLI.

Two consequences are worth stating outright, because both are the reverse of
what a naive fallback would do:

- The `CHRONICLE_` name wins even when its value reads false. An operator who
  has migrated can set `CHRONICLE_SOURCE_ARTIFACT_FETCH=0` and have the flag
  turn off, without first hunting down a stale `LEDGER_SOURCE_ARTIFACT_FETCH=1`
  somewhere in their profile.
- An empty value counts as unset, so exporting an empty `CHRONICLE_` name does
  not mask a set legacy name.

| Chronicle name | Ledger-era names still accepted | Meaning |
|----------------|--------------------------------|---------|
| `CHRONICLE_SOURCE_ARTIFACT_CACHE_DIR` | `LEDGER_SOURCE_ARTIFACT_CACHE_DIR` | Where fetched raw bytes are cached; defaults to `~/.cache/policyengine-chronicle/source-artifacts` |
| `CHRONICLE_SOURCE_ARTIFACT_FETCH` | `LEDGER_SOURCE_ARTIFACT_FETCH` | Fetch a missing manifest artifact from its `source_url` during a build |
| `CHRONICLE_PE_US_DATA_ROOT` | `LEDGER_PE_US_DATA_ROOT` | Local checkout root for PE US source inventory |
| `CHRONICLE_PE_UK_DATA_ROOT` | `LEDGER_PE_UK_DATA_ROOT` | Local checkout root for PE UK source inventory |
| `CHRONICLE_SCHEMA` | `POLICYENGINE_LEDGER_SCHEMA` | Postgres schema the Supabase client reads and writes |
| `CHRONICLE_R2_RAW_BUCKET` | `LEDGER_R2_RAW_BUCKET` | Raw R2 archive bucket; defaults to `ledger-raw` |
| `CHRONICLE_R2_DERIVED_BUCKET` | `LEDGER_R2_DERIVED_BUCKET` | Derived R2 archive bucket; defaults to `ledger-derived` |

The two R2 rows are new in this window rather than renamed: those buckets were
hardcoded before, so the ledger-era spellings are accepted for consistency, not
because anything ever set them.

Variables carrying none of the three prefixes are read literally. This helper
renames the ledger-era surface, not every PolicyEngine variable, so
`POLICYENGINE_SUPABASE_URL`, `POLICYENGINE_SUPABASE_SERVICE_KEY` and
`POLICYENGINE_TARGETS_SCHEMA` keep their names and gain no aliases.

The hosted schema *value* is a separate migration. `CHRONICLE_SCHEMA` renames
the variable that overrides the schema; the schema still defaults to `ledger`,
and the mirror table names are unchanged. Those move in a later slice
coordinated with the CI writers.

## Bucket Cutover

Chronicle's operational stores migrate by dual-run
(PolicyEngine/chronicle#143, mechanism 3): stand up the chronicle-named home,
backfill it, repoint writers, retire the old home. The R2 buckets take one
exception to the last step. Archived witness records pin raw R2 URLs by hash, so
`ledger-raw` and `ledger-derived` are preserved read-only forever rather than
deleted, and manifests keep the `storage.r2` URIs they already recorded as
historical truth. A backfill copies bytes into the new bucket; it never rewrites
where those bytes were first published. `publish-raw` and `fetch-artifact`
enforce that: both refuse to restate a recorded `storage.r2` block under a
different bucket.

The cutover therefore has one irreversible-looking step that is in fact additive
(creating and filling the new buckets), one cheap reversible step (flipping the
defaults, which is a one-line change in `chronicle/artifacts.py`), and no
deletion step at all.

### 1. Create the new buckets

Bucket creation needs a Cloudflare login carrying R2 permissions, so it is an
operator step rather than something CI can do. `wrangler.toml` already pins the
PolicyEngine account (`account_id = "20d90f557651969925eece96e58e24dc"`), so no
`CLOUDFLARE_ACCOUNT_ID` is needed even for a user who belongs to several
accounts:

```bash
bunx wrangler login
uv run chronicle bootstrap-r2 --raw-bucket chronicle-raw --derived-bucket chronicle-derived
```

`bootstrap-r2` verifies authentication with `wrangler whoami` before creating
anything, and creating a bucket that already exists is not an error.

### 2. Enumerate what has to be copied

Tracked manifests are the authoritative registry of raw objects. Every one of
them points at `ledger-raw` today:

```bash
git ls-files '*manifest*.yaml' '*manifest*.yml' \
  | xargs grep -ho 'r2://ledger-raw/[^"'"'"' ]*' | sort -u > /tmp/chronicle-raw-objects.txt
wc -l < /tmp/chronicle-raw-objects.txt
```

That is 186 distinct objects at `ff3efd3`, spread over 154 manifest files. Recount
rather than trusting the number: source packages land continuously, and each new
package adds objects.

The derived bucket needs no backfill. Derived artifacts are reproducible by
definition and are already keyed by `{build_id}`, so a rebuild republishes them
into whichever bucket is configured.

### 3. Backfill-copy the raw objects

Keys are content-addressed and identical across buckets, so the copy is a
straight get/put per object:

```bash
mkdir -p /tmp/chronicle-r2-backfill
while read -r uri; do
  key=${uri#r2://ledger-raw/}
  dest=/tmp/chronicle-r2-backfill/$key
  mkdir -p "$(dirname "$dest")"
  bunx wrangler r2 object get "ledger-raw/$key" --file "$dest" --remote
  bunx wrangler r2 object put "chronicle-raw/$key" --file "$dest" --remote
done < /tmp/chronicle-raw-objects.txt
```

### 4. Verify the copy against the keys themselves

Every raw key ends `.../{sha256}/{filename}`, so the key is its own checksum
witness and verification needs no manifest lookup:

```bash
while read -r uri; do
  key=${uri#r2://ledger-raw/}
  expected=$(printf '%s\n' "$key" | awk -F/ '{print $(NF-1)}')
  actual=$(shasum -a 256 "/tmp/chronicle-r2-backfill/$key" | cut -d' ' -f1)
  [ "$expected" = "$actual" ] || echo "MISMATCH $key"
done < /tmp/chronicle-raw-objects.txt
```

Silence means every downloaded object hashes to the checksum its key claims.
That covers the read from `ledger-raw`; to cover the write to `chronicle-raw`,
re-download each key from the new bucket into a second directory and rerun the
same loop against it.

### 5. Flip the defaults, in a follow-up PR

Once the new buckets are filled and verified, change `DEFAULT_R2_RAW_BUCKET` and
`DEFAULT_R2_DERIVED_BUCKET` in `chronicle/artifacts.py` to `chronicle-raw` and
`chronicle-derived`. Until then, operators can opt in per-shell:

```bash
export CHRONICLE_R2_RAW_BUCKET=chronicle-raw
export CHRONICLE_R2_DERIVED_BUCKET=chronicle-derived
```

New raw publications land in the new bucket from that point. Manifests written
before the flip keep pointing at `ledger-raw`, which is why the old bucket stays
readable.

### 6. Set the ledger-era buckets read-only

`ledger-raw` and `ledger-derived` keep serving archived witness records after the
flip. They should accept no further writes and should never be deleted.

## Non-Goals

Supabase should not store large raw binary artifacts. It should point to R2.

Chronicle should not store raw survey or administrative microdata. It reflects
government statistics releases and the provenance needed to audit those
published facts.

R2 should not be the schema authority. It stores bytes and reproducible build
files, while Chronicle code and checked specs define semantics.

Chronicle should not own Microcosm source selection, aging, reconciliation,
activation profiles, or simulator-specific target mappings. Those belong in
Microcosm or thin downstream adapters.
