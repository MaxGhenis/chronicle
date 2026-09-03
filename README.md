# PolicyEngine Chronicle

PolicyEngine Chronicle is the source-backed fact store for PolicyEngine,
Microcosm, and Thesis. New consumers should use the `policyengine_chronicle`
import path and the `chronicle` console command.

Chronicle is PolicyEngine's source-data foundation for social simulation. It
captures source publications, preserves provenance, and represents published
values as structured, queryable facts.

Chronicle may normalize structure: parse files, type values, declare units and
scales, assign geography and period identifiers, preserve lineage back to
source artifacts, and publish source-backed facts. Chronicle does not own
selection or measurement contracts, reconcile inconsistent sources, impute
missing data, store raw survey microdata, or execute simulator-specific calibration.

Microcosm consumes Chronicle facts, owns the contracts that select and bind
them, applies declared period alignment, and runs calibration. Thesis can
consume the same facts as official observations.

## Purpose

This repository provides:

- **Sources**: Source file references, retrieval metadata, manifests, checksums,
  and provenance.
- **Facts**: Source-backed claims represented with typed values, units,
  geography, period, source table, and lineage. Publisher projections (CBO
  baselines, BFP outlooks, SSA trustees tables, TPC/JCT scores) are facts
  typed `assertion: source_projection`; measured outcomes are the default
  `assertion: observation`. Every fact also carries a required, closed
  `provenance_class` measurement basis; survey aggregates additionally name
  their `survey_instrument`.
- **Normalization**: Low-assumption representation changes such as unit/scale
  conversion and source-published total/share arithmetic.
- **Consumer artifacts**: Versioned, reproducible bundles of consumer-contract
  fact rows plus manifest hashes (`chronicle build-consumer-artifact`).
- **Jurisdiction loaders**: Source-specific ETL that emits the shared Chronicle
  schema.

Chronicle facts are not PolicyEngine's assertion that a source claim is ultimately true.
They are source-backed claims with provenance.

## Boundary

The load-bearing rule:

> Chronicle may re-express a published value, but may not select it for a
> consumer or transform it in ways that change its meaning.

The store is facts-only, and the line is who asserted the value. Everything a
publisher asserted — including the publisher's own projections — is a fact.
Everything PolicyEngine computes (aged, uprated, forecast, or reconciled
levels) is a downstream build artifact and never enters the store; Microcosm
owns aging as a named, versioned model over Chronicle growth-factor facts. A
fact's `period` is the period its value refers to. Consumers must enforce any
contract that aligns it to another period (see
[`docs/adr-chronicle-facts-only.md`](docs/adr-chronicle-facts-only.md)).

| Layer | Owns | Examples |
|-------|------|----------|
| Chronicle Sources | Source artifacts and provenance | URLs, checksums, source files, parsed tables/cells |
| Chronicle Facts | Structured source claims | SOI cells, ACS estimates, CPI values, CBO-published projections |
| Chronicle Normalization | Representation changes | Unit scales, typed values, geography/date identifiers |
| Microcosm Target Contracts | Selection, measurement bindings, and active subset | Period alignment, support-aware activation, solver inputs, diagnostics |

The storage split is documented in
[`docs/storage-architecture.md`](docs/storage-architecture.md): a raw R2 archive
stores immutable source bytes, a derived R2 archive stores reproducible build
artifacts, and Supabase/Postgres hosts the queryable relational Chronicle registry
mirrored from accepted builds. The bucket names are configuration
(`$CHRONICLE_R2_RAW_BUCKET` and `$CHRONICLE_R2_DERIVED_BUCKET`), still defaulting
to the ledger-era `ledger-raw` and `ledger-derived`.

## Repository Model

Chronicle is global at the schema, validation, database, and build-harness layer.
Jurisdiction packages are modular source packages that emit the same Chronicle
objects.

```text
Planned GitHub repositories after the rename:
  PolicyEngine/chronicle # Core schema, validation, harness, DB schema
  PolicyEngine/ledger-us   # US source parsers/specs; emits Chronicle records
  PolicyEngine/chronicle-uk   # UK source parsers/specs; emits Chronicle records

Python distributions:
  policyengine-chronicle
  policyengine-ledger-us
  policyengine-chronicle-uk

Python imports:
  policyengine_chronicle # New public API
  policyengine_chronicle_us
  policyengine_chronicle_uk
```

The current in-repo US package is a prototype while the core schema is still
moving. Until the GitHub repository rename lands, clone this repository into a
local `chronicle` directory. Once the Chronicle contract stabilizes, US and UK source
packages should move to `ledger-us` and `chronicle-uk`. They must not fork
`AggregateConstraint`, source-row/source-cell lineage, stable keys, validation,
or the relational DB schema.

## Structure

```text
chronicle/
├── policyengine_chronicle/       # Public Chronicle namespace
│   ├── sources/             # Source lineage helpers
│   ├── facts/               # Source-backed facts
│   ├── normalization/       # Low-assumption representation helpers
│   ├── targets/             # Target input schema, client, loaders
│   ├── jurisdictions/       # Temporary in-repo jurisdiction source prototypes
├── db/                      # SQLModel persistence and source loaders
│   ├── schema.py            # SQLModel: Target, Stratum, StratumConstraint
│   ├── supabase_client.py   # Supabase client helpers
│   └── etl_*.py             # Source-specific ETL pipelines
├── data/                    # Cached data files
└── docs/                    # Architecture and source documentation
```

New code should prefer `policyengine_chronicle` for source-backed fact
consumers. Existing in-repo implementation code may continue using
legacy implementation modules while the namespace migration is completed.
Solver execution and calibrated dataset construction belong in Microcosm.

## Quick Start

### 1. Install

```bash
pip install policyengine-chronicle
# Or for development, clone this repository into a Chronicle-named directory:
git clone <current repository URL> chronicle
cd chronicle
pip install -e ".[dev]"
```

### 2. Initialize and Load Legacy Target Inputs

```bash
chronicle init
chronicle load soi --years 2021
chronicle stats
```

### 3. Validate Fixture Facts

The standalone Chronicle fact harness validates JSONL aggregate facts and emits a
JSON report with fact counts, QA counts, warnings, and validation errors:

```bash
uv run chronicle validate-facts --fixture
```

To build a tiny source-backed fixture from the packaged IRS SOI Table 1.1
workbook and validate it:

```bash
uv run chronicle build-fixture-facts soi-table-1-1 --year 2023 --output /tmp/chronicle-soi-facts.jsonl
uv run chronicle validate-facts --input /tmp/chronicle-soi-facts.jsonl
```

To preserve the whole used range of that workbook as source-cell records before
semantic fact construction:

```bash
uv run chronicle build-source-cells soi-table-1-1 --year 2023 --output /tmp/chronicle-soi-cells.jsonl
uv run chronicle validate-source-cells --input /tmp/chronicle-soi-cells.jsonl
```

Delimited source packages should preserve the whole file as row records before
selecting facts. For example, the BEA NIPA flat file pilot parses all source
rows, then emits two selected pension contribution facts:

```bash
uv run chronicle build-source-rows bea-nipa-pension-contributions --year 2022 --output /tmp/chronicle-bea-rows.jsonl
uv run chronicle validate-source-rows --input /tmp/chronicle-bea-rows.jsonl
```

ZIP archives with rectangular publisher files use the same row-first contract.
The CMS Marketplace OEP state-level package preserves the raw CMS ZIP, parses
its CSV member into source rows/cells, and emits state-level enrollment and
APTC facts:

```bash
uv run chronicle validate-package cms-aca-oep-state-level --year 2024
uv run chronicle build-suite cms-aca-oep-state-level --year 2024 --out /tmp/chronicle-cms-aca-oep-2024 --replace
```

To build a relational Chronicle DB artifact with aggregate facts, first-class
constraints, source-cell lineage, and source-row lineage when available:

```bash
uv run chronicle build-db --fixture --db /tmp/ledger-fixture.db --replace
```

This writes queryable Chronicle-owned tables such as `source_rows`,
`source_columns`, `source_row_values`, `source_cells`, `aggregate_facts`,
`aggregate_constraints`, `concept_alignments`, `fact_source_cells`, and
`fact_source_rows`. The DB is a deterministic build artifact from source
manifests, parsers, and checked-in specs; hosted Postgres/Supabase should mirror
this schema rather than become the unreproducible origin of source-backed facts.

To run the source-package build suite agents should target, build the source
rows/cells, source-region spec, selector report, aggregate facts, DB artifact,
and JSON reports into one output directory:

```bash
uv run chronicle build-suite soi-table-1-1 --year 2023 --out /tmp/chronicle-suite --replace
```

The same command accepts a declarative package directory. This is the preferred
agent authoring surface:

```bash
uv run chronicle build-suite packages/irs_soi/table_1_1 --year 2023 --out /tmp/chronicle-suite --replace
```

The first UK source packages use the OBR March 2026 EFO receipts and
expenditure workbooks and emit 2025-26 fiscal-year aggregate facts:

```bash
uv run chronicle validate-package obr-efo-receipts --year 2025
uv run chronicle build-suite obr-efo-receipts --year 2025 --out /tmp/chronicle-obr-efo-receipts-2025 --replace
uv run chronicle validate-package obr-efo-expenditure --year 2025
uv run chronicle build-suite obr-efo-expenditure --year 2025 --out /tmp/chronicle-obr-efo-expenditure-2025 --replace
uv run chronicle validate-package slc-student-support-england-2025 --year 2025
uv run chronicle build-suite slc-student-support-england-2025 --year 2025 --out /tmp/chronicle-slc-student-support-england-2025 --replace
```

The first ZIP-backed PE migration package is the CMS Marketplace OEP
state-level public-use release:

```bash
uv run chronicle validate-package cms-aca-oep-state-level --year 2024
uv run chronicle build-suite cms-aca-oep-state-level --year 2024 --out /tmp/chronicle-cms-aca-oep-2024 --replace
```

This writes:

```text
<output-dir>/
  datapackage.json
  ro-crate-metadata.json
  source_rows.jsonl
  source_cells.jsonl
  source_regions.jsonl
  facts.jsonl
  consumer_facts.jsonl
  chronicle.db
  reports/
    source_rows.json
    source_cells.json
    source_regions.json
    selectors.json
    source_records.json
    facts.json
    consumer_facts.json
    concept_alignments.json
    database.json
    agent_acceptance.json
    build_summary.json
```

Agent-authored source packages should be judged by these reports. They should
add or update source manifests, parsers, selector specs, and source-record
specs; they should not hand-edit DB artifacts or core schemas.
The quick gate is `reports/agent_acceptance.json`, which checks raw R2 links,
full-document parsing, fact provenance, source-cell/source-row lineage,
expected first-class constraints, row-backed filter/constraint evidence,
concept alignment evidence, Axiom concept validation status, and stage-report
validity.

To build the downstream integration artifact Microcosm can inspect, merge
available source-package suites for a year into one bundle:

```bash
uv run chronicle build-bundle --year 2023 --out /tmp/ledger-us-2023 --replace
```

This writes a root `consumer_facts.jsonl`, `source_packages.json`,
`coverage.json`, and `reports/build_bundle.json`. Source-specific suite outputs
remain nested under `sources/<source-package>/`. The bundle coverage report
includes counts by source, geography, entity, period, observed measure, and
concept plus duplicate `aggregate_fact_key` and `semantic_fact_key` diagnostics.
The row-level downstream contract is `consumer_facts.jsonl`; the other bundle
files are diagnostic reports for gating and review. Consumer-contract rows must
carry canonical constraints explicitly in `universe_constraints`; source-layout
`dimensions` are metadata and are not target constraints.

For the UK source-package feed, build the curated UK suite and then a facts-only
consumer artifact:

```bash
uv run chronicle build-bundle --suite uk --out /tmp/chronicle-uk --replace
uv run chronicle build-consumer-artifact --facts /tmp/chronicle-uk --out /tmp/chronicle-uk-artifact --replace
```

The command writes a `policyengine_ledger.consumer_artifact.v2` artifact containing
only `consumer_facts.jsonl` and `manifest.json`. Version 2 is incompatible with the
retired v1 profile-bearing contract: loaders reject v1 manifests so downstreams must
adopt the facts-only surface explicitly.

`--year` is inert for `--suite uk` because the UK packages are year-pinned.
The US off-year bundle behavior is unchanged and out of scope here.

Builds without an Axiom CLI still pass when the source package is otherwise
valid, but `agent_acceptance.json` warns with
`concept_alignment_validation_skipped`. For strict agent review, require every
canonical concept to resolve through Axiom:

```bash
uv run chronicle build-suite packages/irs_soi/table_1_1 \
  --year 2023 \
  --out /tmp/chronicle-suite \
  --replace \
  --axiom-cli axiom \
  --axiom-root ../rules-us \
  --require-axiom-validation
```

For the faster authoring loop before running the full build suite, validate a
package directory directly:

```bash
uv run chronicle validate-package packages/irs_soi/table_1_1 --year 2023
```

To start a new package from the constrained YAML template:

```bash
uv run chronicle scaffold-package --source-id irs_soi --package-id soi-table-1-2 \
  --out packages/irs_soi/table_1_2 \
  --source-table "Publication 1304 Table 1.2" \
  --resource-directory data/irs_soi/table_1_2
```

Raw source artifacts should be content-addressed and checksum-locked before a
package spec depends on them. Tiny fixtures can stay in Git, but production raw
files should live in private Cloudflare R2 buckets, with `manifest.yaml` and the
hosted database carrying the queryable provenance.

The buckets live on the PolicyEngine Cloudflare account, which `wrangler.toml`
pins via `account_id`, so a plain `wrangler login` as any account member is the
only authentication step — no `CLOUDFLARE_ACCOUNT_ID` environment variable
needed, even when your Cloudflare user belongs to several accounts:

```bash
# One-time per machine (opens a browser consent page):
bunx wrangler login

# One-time per account (already done for the PolicyEngine account). The bucket
# flags default to $CHRONICLE_R2_RAW_BUCKET / $CHRONICLE_R2_DERIVED_BUCKET:
uv run chronicle bootstrap-r2

# Fetch/register a source artifact, write db/data/.../manifest.yaml, and upload
# the exact bytes to R2 when Wrangler is authenticated. Pass --manifest when the
# package directory keeps more than one manifest (ira_contributions keeps a
# traditional and a Roth one):
uv run chronicle fetch-artifact \
  --url https://www.irs.gov/pub/irs-soi/23in12ms.xls \
  --source-id irs_soi \
  --package-id soi-table-1-2 \
  --year 2023 \
  --out-dir db/data/irs_soi/table_1_2 \
  --source-page https://www.irs.gov/statistics/soi-tax-stats-individual-income-tax-returns-complete-report-publication-1304-basic-tables-part-1 \
  --table "Publication 1304 Table 1.2" \
  --upload-r2

# Re-fetching is safe: identical bytes keep the recorded storage.r2 block, and
# bytes that disagree with what the entry identifies -- its declared sha256, or
# its recorded content-addressed key once published -- are refused. When a
# publisher has re-published
# under the same URL and vintage, register the revision explicitly — the new
# bytes get their own content-addressed key and the superseded object is kept
# in storage.previous_r2:
uv run chronicle fetch-artifact ... --record-revision

# Audit local manifests and checksums:
uv run chronicle inventory-artifacts --root db/data

# Upload all existing manifest-declared local artifacts to the raw archive and
# write storage.r2 metadata back into the manifests:
uv run chronicle publish-raw --root db/data

# Register a licensed or restricted release by identity alone. No bytes are
# fetched, stored, or uploaded, and no R2 key is recorded:
uv run chronicle register-artifact \
  --source-id dwp \
  --package-id dwp-frs-2024-25 \
  --year 2024 \
  --out-dir db/data/dwp/frs_2024_25 \
  --filename adult.tab \
  --sha256 4eaea0809a7ccca0fddeb98e358771e4a6e5ebb81b21c4fac4070fc9d227658d \
  --size-bytes 34885825 \
  --vintage 2024_25 \
  --licence "UK Data Service End User Licence" \
  --access licensed \
  --doi 10.5255/UKDA-SN-9563-1 \
  --verified-at 2026-09-03 \
  --hash-source consumer_pin \
  --attested-by PolicyEngine/microcosm \
  --pinned-from-repository PolicyEngine/microcosm \
  --pinned-from-path packages/microcosm-build/src/microcosm/build/uk/source_stages.json \
  --pinned-from-commit 09abf2ad78e9af3c5314a4b303d42a75e30d49c4
```

To coordinate broad PE source migration without jumping straight to semantic
target construction, generate an agent batch plan from the PE manifest:

```bash
uv run chronicle plan-pe-sources \
  --manifest docs/pe-us-source-manifest.csv \
  --out docs/pe-us-source-agent-plan.json \
  --markdown docs/pe-us-source-agent-plan.md
```

The plan marks existing source packages, primary-source lookup work,
fetch/register work, source-cell scaffolds, and repair items. Fetch hints
include `--upload-r2`; semantic target work still requires a package to pass
`build-suite`. Aggregators such as FRED stay in the migration plan only as
publisher-source lookup clues; they should not become canonical Chronicle source
artifacts or target provenance.

R2 owns the immutable bytes. Chronicle manifests and Supabase/Postgres mirrors own
metadata such as source URL, checksum, size, vintage, extraction date, and R2
key. Source-package parsers still read deterministic local/package resources,
so builds remain reproducible without making hosted storage the source of
schema truth.

The same build-suite path also supports the SOI Table 1.4 wage pilot:

```bash
uv run chronicle build-suite soi-table-1-4 --year 2023 --out /tmp/chronicle-suite-1-4 --replace
uv run chronicle build-suite packages/irs_soi/table_1_4 --year 2023 --out /tmp/chronicle-suite-1-4 --replace
```

To prepare the deterministic SQLite artifact for a hosted Supabase/Postgres
mirror, export each relational table to JSONL plus a manifest:

```bash
uv run chronicle export-db-tables --db /tmp/chronicle-suite/chronicle.db --out /tmp/chronicle-mirror --replace
```

To publish the deterministic build outputs to the derived R2 archive:

```bash
uv run chronicle publish-derived \
  --dir /tmp/chronicle-suite \
  --source-id irs_soi \
  --package-id soi-table-1-1 \
  --year 2023 \
  --build-artifacts-out /tmp/chronicle-build-artifacts.jsonl
```

The Supabase schema for this mirror lives at
`supabase/migrations/20260504_chronicle_bronze.sql`. Raw government spreadsheets are
mirrored as artifact metadata plus one row per parsed cell, not one tidy table
per sheet. Chronicle does not host raw survey microdata tables.

After the migration is applied and the `chronicle` schema is exposed through the
Supabase Data API, accepted mirror exports can be upserted with:

```bash
uv run chronicle load-supabase-mirror \
  --dir /tmp/chronicle-mirror \
  --build-artifacts /tmp/chronicle-build-artifacts.jsonl
```

Use `--dry-run` first to validate JSONL row counts and file coverage without
writing to Supabase.

Chronicle settings are read chronicle-first: `CHRONICLE_X` wins, and the
ledger-era `POLICYENGINE_LEDGER_X` and `LEDGER_X` spellings still work behind a
one-time deprecation warning naming the variable to move to.
[`docs/storage-architecture.md`](docs/storage-architecture.md#environment-variable-rename-window)
lists every variable in that window, and
[Bucket Cutover](docs/storage-architecture.md#bucket-cutover) covers the R2
bucket rename.

Chronicle facts keep source concepts and canonical concepts separately. For example,
the SOI Table 1.1 adjusted gross income column is preserved as
`irs_soi.adjusted_gross_income`, while the canonical concept is
`us:statutes/26/62#adjusted_gross_income` with an `exact` alignment assertion.
The SOI Table 1.4 wage amount column is preserved as `irs_soi.total_wages`,
while the canonical concept is `us:statutes/26/62#input.wages` with a
`broad_match` assertion because Axiom currently treats wages as an inferred
input under IRC section 62 rather than an exact statutory term.
This lets Chronicle share vocabulary with Axiom legal terms without importing Axiom
runtime code.

To validate source-to-canonical concept alignments against an installed Axiom
concept CLI outside the full suite:

```bash
uv run chronicle validate-concept-alignments --input /tmp/chronicle-soi-facts.jsonl \
  --axiom-cli axiom \
  --axiom-root ../rules-us
```

The command emits JSON with the alignments checked, validation errors, and
warnings. If the Axiom CLI is omitted, Chronicle still reports alignment metadata and
warns that external concept validation was skipped. `build-suite` accepts the
same `--axiom-cli` and `--axiom-root` flags, plus
`--require-axiom-validation` when skipped concept checks should fail agent
acceptance.

### 4. Run Chronicle Explorer

Chronicle Explorer is a Next/Tailwind app that reads the fixture fact JSONL and
source-cell JSONL, then shows aggregate facts, source-cell lineage, and
consumer-contract fields:

```bash
cd explorer
npm install
npm run dev -- --port 3090
```

Then open `http://localhost:3090`.

By default, the workbench reads the current local suite outputs at
`/tmp/ledger-us-2023-parity/sources/*` and
`/tmp/chronicle-soi-historic-table-2-2022`. To point it at another build, set:

```bash
LEDGER_EXPLORER_DATA_DIRS=/tmp/chronicle-build-a,/tmp/chronicle-build-b npm run dev -- --port 3090
```

### 5. Query Target Inputs in Python

```python
from policyengine_chronicle.targets import DataSource, Target, TargetType, query_targets

target_rows = query_targets(jurisdiction="us", year=2024)
```

## Target Input Schema

Target inputs use a three-table schema:

- **strata**: Population subgroups, such as California filers with AGI between
  $50k and $75k.
- **stratum_constraints**: Rules defining each stratum.
- **targets**: Source-published aggregate values linked to strata.

These are source-backed inputs. Microcosm owns the contracts that select them,
the active support-aware subset, and calibrated solver execution.

## Chronicle Facts And Microcosm Targets

Source facts should be structurally normalized before Microcosm considers them
as calibration target candidates.
Normalization is about representation, not modeling: units, scales, typed
values, geography IDs, period IDs, and same-source arithmetic where the source
publishes the total/share relationship.

Inflation, cross-source reconciliation, and support-aware activation belong in
Microcosm unless the source itself publishes the adjusted or projected series.
Microcosm contracts declare which source-backed rows and measurement bindings a
build may activate.

```python
from policyengine_chronicle.facts import SourceFact
from policyengine_chronicle.normalization import convert_units

fact = SourceFact(
    name="snap_households",
    value=22_323,
    period=2023,
    unit="thousands",
    source="usda_snap",
    jurisdiction="us",
)

normalized_fact = convert_units(fact, 1000, "count")
```

## Current Coverage

### Aggregate Facts And Target Inputs

| Source | Coverage | Description |
|--------|----------|-------------|
| IRS SOI | National, state, AGI brackets | Tax return aggregates |
| Census | Demographics, poverty, districts | Population statistics |
| BLS | Labor market and price data | Employment and index series |
| CBO | Federal projections | Budget and economic projections |
| SSA/SSI | National and state programs | Social Security data |
| SNAP | State-level | Food assistance |
| CMS | Medicaid and ACA enrollment | Health coverage |
| HMRC/ONS/OBR | UK tax, population, projections | UK official statistics |

## Boundaries

- **Chronicle** owns government-statistics release artifacts, provenance, source
  facts, and aggregate facts.
- **Microcosm** owns selection and measurement contracts, support-aware target
  activation, period alignment, raw microdata access, simulation interfaces,
  entity modeling, weights, diagnostics, and calibration execution.
- **Jurisdiction source packages** such as `ledger-us` and `chronicle-uk` own
  source-specific parsers and specs that emit shared Chronicle records.
- **Jurisdiction simulation packages** own simulation-specific variable
  mappings and target recipes.
- **PolicyEngine** owns policy-facing tools and analysis workflows.

## Related Repositories

- [microcosm](https://github.com/PolicyEngine/microcosm) - Simulation data builds,
  target selection, and calibration execution.
- [thesis](https://github.com/PolicyEngine/thesis) - Public-facing official
  observations and analysis surfaces backed by Chronicle facts.
