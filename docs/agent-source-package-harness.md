# Agent Source Package Harness

Chronicle source-population agents should fill constrained source packages and let
the build suite decide whether the package is admissible. Agents should not
hand-edit Chronicle DB artifacts, generated JSONL outputs, or core schema modules.

The older Python ETL loaders that write directly into the legacy `targets`
tables are compatibility and migration inputs, not the preferred agent
population path. They are useful for proving source coverage against Microcosm
today, but a source family should become agent-ready only after it is expressed
as a source package with full-document parsing, source-row or source-cell
lineage, provenance, constraints, and a passing `build-suite` report.

The first gate for a new package is source-artifact acquisition. Agents should
register raw source files with `uv run chronicle fetch-artifact` before authoring
selectors. This writes the local artifact, captures checksum and retrieval
metadata in `manifest.yaml`, and can upload the exact bytes to the private raw
R2 bucket (`ledger-raw` today; overridable with `CHRONICLE_R2_RAW_BUCKET`) when
Wrangler is authenticated. A publisher directory that feeds several source
packages keeps one reserved `manifest*.yaml` file each, so pass
`--manifest <filename>` to address the right one. A new or empty manifest also
requires `--kind publisher_table` (or `--kind microdata_release` for the
identity-only contract below); only frozen path-and-content identities may
remain kindless. Agents can audit the local
artifact registry with `uv run chronicle inventory-artifacts --root db/data`.
For already-downloaded manifest artifacts, agents should run
`uv run chronicle publish-raw --root db/data` to upload checksum-verified bytes to
R2 and write `storage.r2` metadata back into each manifest entry.

Both commands treat a manifest entry as a claim about specific bytes: by its
declared `sha256` from the moment it is registered, and by the content-addressed
key of its recorded `storage.r2` block once it is published. Re-fetching or
publishing bytes the entry does not identify is refused; when a publisher has re-published
under the same URL and vintage, register the revision with
`uv run chronicle fetch-artifact ... --record-revision`, which stores the new
bytes under their own key and keeps the superseded object in
`storage.previous_r2`. See
[Publisher Revisions](storage-architecture.md#publisher-revisions).

Builds do not require production raw bytes to be committed to Git. Source
packages first read packaged fixture bytes, then
`CHRONICLE_SOURCE_ARTIFACT_CACHE_DIR` (defaulting to
`~/.cache/policyengine-chronicle/source-artifacts`). If a manifest artifact is
missing locally, set `CHRONICLE_SOURCE_ARTIFACT_FETCH=1` to fetch it from the
manifest `source_url`, verify the declared SHA-256, and write it to that cache.
The ledger-era spellings `LEDGER_SOURCE_ARTIFACT_CACHE_DIR` and
`LEDGER_SOURCE_ARTIFACT_FETCH` are still honored during the rename window and
emit a one-time deprecation warning naming the `CHRONICLE_` variable to set
instead; see "Environment Variable Rename Window" in
[`docs/storage-architecture.md`](storage-architecture.md#environment-variable-rename-window).

## Hash-Only Registrations

Not every raw artifact a build starts from may be redistributed. Every
manifest file entry carries an `access` class from a closed set — `public`,
`licensed`, or `restricted` — and a `licence` naming the publisher's terms as
an identifier or URL. `public` is inferred when an entry omits `access`, and
`fetch-artifact` now writes the class explicitly onto every entry it touches.
Both fields are required on a `kind: microdata_release` manifest.

Only `public` bytes enter a Chronicle store. A `licensed` or `restricted`
artifact is registered *hash-only*: the manifest records the checksum, size,
vintage, licence, access route, and verification timestamp, and no bytes are
fetched, written, or uploaded. Agents should register one with:

```bash
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
  --pinned-from-commit <immutable-origin-main-commit>
```

Agents should never invent a checksum to satisfy the command: `--sha256` must
be a lowercase 64-character digest taken from a reviewed pin, and a release
whose checksum nobody has published is a blocker to record, not a value to
guess. The command refuses `--access public`, refuses bytes sitting beside the
manifest, and refuses to write into a `publisher_table` manifest.

The other commands enforce the same boundary from their side. `fetch-artifact`
refuses a `licensed` or `restricted` access class before reading anything, and
refuses to pull bytes over an entry already registered hash-only.
`publish-raw` refuses such an entry without reading or uploading its bytes;
pass `--skip-hash-only` to publish a tree that deliberately mixes both kinds.
`inventory-artifacts` treats a hash-only entry with no local file as valid —
the absent bytes are the correct state — and reports an error if the bytes
appear.

A `public` microdata release is different only when its exact licence is on the
redistribution allowlist and `licence_evidence` binds that term to the exact
artifact SHA-256. Its bytes are acquired into an ignored transient staging
directory outside `db/data`, uploaded directly to R2, and removed; Git custody
remains manifest-only. `--access` is mandatory for microdata and is never
defaulted to public:

```bash
uv run chronicle fetch-artifact \
  --url https://www2.census.gov/programs-surveys/acs/data/pums/2024/1-Year/csv_hus.zip \
  --source-id census_acs \
  --package-id census-acs-pums-2024-1yr \
  --year 2024 \
  --out-dir db/data/census/acs_pums_2024_1yr \
  --filename csv_hus.zip \
  --publisher "U.S. Census Bureau" \
  --vintage 2024 \
  --access public \
  --licence US-GOVERNMENT-WORK-17-USC-105 \
  --kind microdata_release \
  --expected-sha256 8281008e53de98f0ef81e7a2ee5a8725991dda1ecfd2713ead73246425e515d0 \
  --expected-size-bytes 251500587 \
  --licence-evidence-issuer "U.S. Census Bureau" \
  --licence-evidence-id US-GOVERNMENT-WORK-17-USC-105 \
  --licence-evidence-version "17 U.S.C. §105" \
  --licence-evidence-scope "U.S. Census Bureau identifies csv_hus.zip as part of ACS 2024 1-Year PUMS." \
  --licence-evidence-url https://www.census.gov/about/policies/copyright.html \
  --licence-evidence-sha256 8281008e53de98f0ef81e7a2ee5a8725991dda1ecfd2713ead73246425e515d0 \
  --staging-dir .chronicle-staging \
  --upload-r2
```

`fetch-artifact` refuses a public microdata acquisition without successful R2
upload and deletes the transient copy whether upload succeeds or fails.

Because several files can share one vintage, a `kind: microdata_release`
manifest may give `files[year]` as a list of entries rather than a single
mapping — the ACS household and person files above land side by side. A list
under any other manifest kind is an error. A fetch replaces only the entry for
its own filename, so acquiring a second file never drops the first.

No source package parses a microdata release. `validate-package` fails with
`microdata_release_not_parseable` if a package spec points at one. No microdata
records, rows, columns, row values, or cells ever enter Chronicle. Registration is
manifest-level identity; see `docs/adr-chronicle-raw-microdata-identity.md`.

`scripts/register_microdata_releases.py` drives both halves from synthetic test
fixtures or files materialized with `git show origin/main:<path>` into a temporary
directory, never from a clone's working tree. `emit` writes hash-only manifests
from Microcosm's reviewed pins, and `plan` prints `fetch-artifact` commands for
public releases.

For broad PE source migration, generate the agent queue from the manifest before
assigning work:

```bash
uv run chronicle plan-pe-sources \
  --manifest docs/pe-us-source-manifest.csv \
  --out docs/pe-us-source-agent-plan.json \
  --markdown docs/pe-us-source-agent-plan.md
```

The generated plan separates existing source packages, primary-source lookup
tasks, fetch/register tasks, source-cell scaffolds, and repair items. It is not
semantic acceptance; agents still need `validate-package` and `build-suite`
before a package can move past `semantic_candidate`. Aggregators such as FRED
are migration clues, not canonical Chronicle source artifacts; agents should find
and register the publisher-owned artifact before source cells or target facts
become canonical.

## Source Package Contract

A source package should eventually contain the source artifact manifest, parser
or retrieval code, cell selector specs, source-record specs, and focused tests
for one source family or table. The current in-repo pilot is
`soi-table-1-1`, with `soi-table-1-4` as a second SOI wage pilot, backed by
`chronicle.jurisdictions.us.soi` while the package contract stabilizes.

Agents should prefer declarative package directories over Python edits. A
minimal package has a `source_package.yaml` file that identifies the source
artifact manifest and declares compact record sets. The SOI pilots live at
`packages/irs_soi/table_1_1/source_package.yaml`,
`packages/irs_soi/table_1_4/source_package.yaml`, and
`packages/irs_soi/historic_table_2/source_package.yaml`.
For rectangular state tables, row-level geography overrides let one record set
represent repeated state rows without duplicating the measures. The first
ZIP-backed PE migration example is
`packages/cms_aca/oep_state_level/source_package.yaml`, which parses the CMS
OEP ZIP's CSV member into full source rows and emits state-level facts.

## Whole-Table Packages

Some publisher tables carry hundreds of geographies. Enumerating one record set
per geography is what makes a package unreadable — the two largest packages in
the repo are ~200k lines for under 8k facts. The cheaper shape, confirmed on the
650-constituency UK packages, is:

- **One record set per publisher category** (age band, tenure class, benefit
  band), not per geography.
- **`rows:` carries geometry only** — `row_number`, an `expected_row_header`
  guard on the code column, the per-row `geography_id` / `geography_level` /
  `geography_name` / `geography_vintage` overrides, `table_record_kind`, and a
  guard cell proving the row belongs to the category the record set claims.
- **The category's semantics go in `shared_filters` and `shared_constraints`**,
  declared once per record set rather than repeated on every geography row.
  Both merge into every fact, so each fact still carries its filters and
  constraints first-class.

That is roughly 11 to 16 YAML lines per fact against roughly 26 for the
per-geography orientation. `packages/ons/pcon24_population_by_age_2024` is the
worked example.

Long-format artifacts (one row per cell — the default for the Nomis, NISRA
PxStat and Stat-Xplore APIs) fit this shape directly. For a publisher-fixed
wide layout, where categories are columns, the orientation flips to one record
set per column with rows as geographies —
`packages/nrs/pcon24_population_by_age_2024` is that case, and it also shows
the trap that digit column headers (`0`, `1`, …) cannot be expressed as
`expected_column_header`, because the guard contract int-coerces digit
expectations while comparing raw. Guard the non-digit endpoints instead.

Two rules bite at this scale, both enforced by agent acceptance rather than
`validate-package`:

- **Row-backed filters and constraints must be evidenced by the parsed row's own
  columns.** The filter key has to normalize to a real column name and the value
  has to equal that column's value, so a prettier slug will not verify. Numeric
  bounds are the exception: `_source_row_age_range` accepts a source-coded age
  band as evidence for interpreted bounds, so a band row can carry both its
  publisher label and `age >= 0` / `age < 5`.
- **Geography-only rows are `table_record_kind: total`.** Detail rows in a
  grouped set need first-class constraints.

Multi-geography publisher workbooks often carry sheets a package never selects
from, and every cell of them costs a source-cell record on each build. Declare
`sheets:` on the artifact to restrict an `xlsx_used_range` parse to the
worksheets the package actually reads; the NRS workbook above is 963k cells of
which the constituency sheet is 5%.

## Selector Guards

Selectors should not rely on coordinates alone once a package is ready for
semantic review. Agents should add guards that prove the selected coordinates
still mean what the source package claims they mean.

Use `guard_cells` for exact row-relative checks such as a start row label, end
row label, neighboring header, or absolute sentinel. A row guard uses an Excel
column, an expected value, and one of `row: start`, `row: end`, or a positive
integer row number. Columns must be Excel letters, and expected values must not
depend on presentation-only formatting.

```yaml
rows:
  - value_id: female_0_14
    label: Female age 0 to 14
    row_number: 2
    row_end_number: 16
    expected_row_header_column: A
    expected_row_header: Females
    guard_cells:
      - column: B
        row: start
        expected_value: 0
        label: start age
      - column: A
        row: end
        expected_value: Females
        label: end sex
      - column: B
        row: end
        expected_value: 14
        label: end age
```

Use `range_label_guards` when a fact sums a dense row range and interior labels
are part of the fact definition. Endpoint guards catch off-by-one boundaries,
but they do not catch an inserted, duplicated, or shifted interior label. Range
label guards require `row_end_number` and validate every expected label in the
guard column from `row_number` through `row_end_number`.

```yaml
range_label_guards:
  - column: B
    expected_values:
      integer_range:
        start: 0
        end: 14
    label: age sequence
```

For ranges with tail labels, use `final_value` to replace the last integer and
`extra_values` to append labels after the integer range:

```yaml
range_label_guards:
  - column: B
    expected_values:
      integer_range:
        start: 0
        end: 105
        final_value: 105 - 109
        extra_values:
          - 110 and over
    label: age sequence
```

For concatenated sequences, use `parts`. A compact mapping must use exactly one
form: either `integer_range` or `parts`, not both. `null` entries are rejected
because they would otherwise behave like unguarded labels.

```yaml
range_label_guards:
  - column: B
    expected_values:
      parts:
        - integer_range:
            start: 0
            end: 105
            final_value: 105 - 109
            extra_values:
              - 110 and over
        - integer_range:
            start: 0
            end: 105
            final_value: 105 - 109
            extra_values:
              - 110 and over
    label: age sequence
```

Default rule: every selected record should have at least endpoint guards before
review. Add full range label guards for dense dimensions such as age, year,
geography, benefit band, income band, or education stage when a selected range
is interpreted as a sum over that dimension. Sparse sentinels are acceptable for
early drafts, but packages should not leave `semantic_candidate` with
coordinate-only selectors for dense summed ranges.

Guard cells and range label cells become source-cell lineage. This is useful for
auditability, but it can add hundreds of lineage cells for very dense ranges.
Use full label sequences where the interior labels are material to the aggregate
meaning; otherwise prefer endpoint guards plus a small number of sentinels.

The build suite is the review surface:

```bash
uv run chronicle validate-package packages/irs_soi/table_1_1 --year 2023
uv run chronicle build-suite soi-table-1-1 --year 2023 --out /tmp/chronicle-suite --replace
uv run chronicle build-suite packages/irs_soi/table_1_1 --year 2023 --out /tmp/chronicle-suite --replace
```

For the row-oriented IRS SOI Historic Table 2 package, the 2022 national first
slice can be checked with:

```bash
uv run chronicle validate-package soi-historic-table-2 --year 2022
uv run chronicle build-suite soi-historic-table-2 \
  --year 2022 \
  --out /tmp/chronicle-soi-historic-table-2-2022 \
  --replace
```

For the CMS Marketplace OEP state-level ZIP package, the 2024 first slice can
be checked with:

```bash
uv run chronicle validate-package cms-aca-oep-state-level --year 2024
uv run chronicle build-suite cms-aca-oep-state-level \
  --year 2024 \
  --out /tmp/chronicle-cms-aca-oep-2024 \
  --replace
```

For the next US publisher-source packages, the 2024 slices can be checked with:

```bash
uv run chronicle validate-package cms-nhe-historical-service-source --year 2024
uv run chronicle build-suite cms-nhe-historical-service-source \
  --year 2024 \
  --out /tmp/chronicle-cms-nhe-historical-service-source-2024 \
  --replace

uv run chronicle validate-package census-stc-individual-income-tax --year 2024
uv run chronicle build-suite census-stc-individual-income-tax \
  --year 2024 \
  --out /tmp/chronicle-census-stc-individual-income-tax-2024 \
  --replace

uv run chronicle validate-package census-pep-2024-national-age-sex --year 2024
uv run chronicle build-suite census-pep-2024-national-age-sex \
  --year 2024 \
  --out /tmp/chronicle-census-pep-2024-national-age-sex-2024 \
  --replace

uv run chronicle validate-package hhs-acf-tanf-financial-2024 --year 2024
uv run chronicle build-suite hhs-acf-tanf-financial-2024 \
  --year 2024 \
  --out /tmp/chronicle-hhs-acf-tanf-financial-2024 \
  --replace

uv run chronicle validate-package soi-ira-traditional-contributions-2022 --year 2022
uv run chronicle build-suite soi-ira-traditional-contributions-2022 \
  --year 2022 \
  --out /tmp/chronicle-soi-ira-traditional-contributions-2022 \
  --replace
uv run chronicle validate-package soi-ira-roth-contributions-2022 --year 2022
uv run chronicle build-suite soi-ira-roth-contributions-2022 \
  --year 2022 \
  --out /tmp/chronicle-soi-ira-roth-contributions-2022 \
  --replace
uv run chronicle validate-package soi-w2-statistics-2020 --year 2020
uv run chronicle build-suite soi-w2-statistics-2020 \
  --year 2020 \
  --out /tmp/chronicle-soi-w2-statistics-2020 \
  --replace
```

For the first UK packages, OBR March 2026 EFO receipts and expenditure can be
checked with:

```bash
uv run chronicle validate-package obr-efo-receipts --year 2025
uv run chronicle build-suite obr-efo-receipts \
  --year 2025 \
  --out /tmp/chronicle-obr-efo-receipts-2025 \
  --replace
uv run chronicle validate-package obr-efo-expenditure --year 2025
uv run chronicle build-suite obr-efo-expenditure \
  --year 2025 \
  --out /tmp/chronicle-obr-efo-expenditure-2025 \
  --replace
uv run chronicle validate-package slc-student-support-england-2025 --year 2025
uv run chronicle build-suite slc-student-support-england-2025 \
  --year 2025 \
  --out /tmp/chronicle-slc-student-support-england-2025 \
  --replace
uv run chronicle validate-package dwp-uc-two-child-limit-2025 --year 2026
uv run chronicle build-suite dwp-uc-two-child-limit-2025 \
  --year 2026 \
  --out /tmp/chronicle-dwp-uc-two-child-limit-2026 \
  --replace
uv run chronicle validate-package dwp-benefit-cap-november-2025 --year 2025
uv run chronicle build-suite dwp-benefit-cap-november-2025 \
  --year 2025 \
  --out /tmp/chronicle-dwp-benefit-cap-2025 \
  --replace
uv run chronicle validate-package dwp-benefit-statistics-february-2026 --year 2025
uv run chronicle build-suite dwp-benefit-statistics-february-2026 \
  --year 2025 \
  --out /tmp/chronicle-dwp-benefit-statistics-2025 \
  --replace
uv run chronicle validate-package dwp-pip-daily-living-foi-2025 --year 2025
uv run chronicle build-suite dwp-pip-daily-living-foi-2025 \
  --year 2025 \
  --out /tmp/chronicle-dwp-pip-daily-living-foi-2025 \
  --replace

uv run chronicle validate-package dwp-uc-payment-distribution-may-2025 --year 2025
uv run chronicle build-suite dwp-uc-payment-distribution-may-2025 \
  --year 2025 \
  --out /tmp/chronicle-dwp-uc-payment-distribution-may-2025 \
  --replace

uv run chronicle validate-package dwp-uc-childcare-element-march-2021-august-2025 --year 2025
uv run chronicle build-suite dwp-uc-childcare-element-march-2021-august-2025 \
  --year 2025 \
  --out /tmp/chronicle-dwp-uc-childcare-element-2025 \
  --replace

uv run chronicle validate-package dwp-uc-households-carer-entitlement-april-december-2025 --year 2025
uv run chronicle build-suite dwp-uc-households-carer-entitlement-april-december-2025 \
  --year 2025 \
  --out /tmp/chronicle-dwp-uc-carer-entitlement-2025 \
  --replace

uv run chronicle validate-package dwp-uc-households-children-april-december-2025 --year 2025
uv run chronicle build-suite dwp-uc-households-children-april-december-2025 \
  --year 2025 \
  --out /tmp/chronicle-dwp-uc-households-children-april-december-2025 \
  --replace

uv run chronicle validate-package dwp-uc-households-family-type-april-december-2025 --year 2025
uv run chronicle build-suite dwp-uc-households-family-type-april-december-2025 \
  --year 2025 \
  --out /tmp/chronicle-dwp-uc-households-family-type-april-december-2025 \
  --replace

uv run chronicle validate-package dwp-uc-households-housing-entitlement-april-december-2025 --year 2025
uv run chronicle build-suite dwp-uc-households-housing-entitlement-april-december-2025 \
  --year 2025 \
  --out /tmp/chronicle-dwp-uc-housing-entitlement-2025 \
  --replace

uv run chronicle validate-package dwp-uc-households-lcwra-entitlement-april-december-2025 --year 2025
uv run chronicle build-suite dwp-uc-households-lcwra-entitlement-april-december-2025 \
  --year 2025 \
  --out /tmp/chronicle-dwp-uc-lcwra-entitlement-2025 \
  --replace

uv run chronicle validate-package dwp-uc-scotland-youngest-child-may-2025 --year 2025
uv run chronicle build-suite dwp-uc-scotland-youngest-child-may-2025 \
  --year 2025 \
  --out /tmp/chronicle-dwp-uc-scotland-youngest-child-may-2025 \
  --replace

uv run chronicle validate-package hmrc-salary-sacrifice-relief-2024-25 --year 2024
uv run chronicle build-suite hmrc-salary-sacrifice-relief-2024-25 \
  --year 2024 \
  --out /tmp/chronicle-hmrc-salary-sacrifice-relief-2024-25 \
  --replace

uv run chronicle validate-package hmrc-spi-income-bands-2023-24 --year 2023
uv run chronicle build-suite hmrc-spi-income-bands-2023-24 \
  --year 2023 \
  --out /tmp/chronicle-hmrc-spi-income-bands-2023-24 \
  --replace

uv run chronicle validate-package ons-savings-interest-income --year 2023
uv run chronicle build-suite ons-savings-interest-income \
  --year 2023 \
  --out /tmp/chronicle-ons-savings-interest-income-2023 \
  --replace

uv run chronicle validate-package ons-uk-population-projections-2022 --year 2022
uv run chronicle build-suite ons-uk-population-projections-2022 \
  --year 2022 \
  --out /tmp/chronicle-ons-uk-population-projections-2022 \
  --replace

uv run chronicle validate-package nrs-mid-year-population-estimates-2024 --year 2024
uv run chronicle build-suite nrs-mid-year-population-estimates-2024 \
  --year 2024 \
  --out /tmp/chronicle-nrs-mid-year-population-estimates-2024 \
  --replace

uv run chronicle validate-package nrs-vital-events-reference-tables-2024 --year 2024
uv run chronicle build-suite nrs-vital-events-reference-tables-2024 \
  --year 2024 \
  --out /tmp/chronicle-nrs-vital-events-reference-tables-2024 \
  --replace

uv run chronicle validate-package ons-subnational-dwellings-by-tenure-2024 --year 2024
uv run chronicle build-suite ons-subnational-dwellings-by-tenure-2024 \
  --year 2024 \
  --out /tmp/chronicle-ons-subnational-dwellings-by-tenure-2024 \
  --replace

uv run chronicle validate-package hmrc-salary-sacrifice-reform-2029-headcounts --year 2025
uv run chronicle build-suite hmrc-salary-sacrifice-reform-2029-headcounts \
  --year 2025 \
  --out /tmp/chronicle-hmrc-ss-headcounts \
  --replace

uv run chronicle validate-package isc-annual-census-2023 --year 2023
uv run chronicle build-suite isc-annual-census-2023 \
  --year 2023 \
  --out /tmp/chronicle-isc-2023 \
  --replace
uv run chronicle validate-package isc-annual-census-2024 --year 2024
uv run chronicle build-suite isc-annual-census-2024 \
  --year 2024 \
  --out /tmp/chronicle-isc-2024 \
  --replace

uv run chronicle validate-package ons-national-balance-sheet-land-2025 --year 2024
uv run chronicle build-suite ons-national-balance-sheet-land-2025 \
  --year 2024 \
  --out /tmp/chronicle-ons-national-balance-sheet-land-2025 \
  --replace

uv run chronicle validate-package voa-council-tax-bands-2025 --year 2025
uv run chronicle build-suite voa-council-tax-bands-2025 \
  --year 2025 \
  --out /tmp/chronicle-voa-council-tax-bands-2025 \
  --replace

uv run chronicle validate-package scotgov-council-tax-bands-2025 --year 2025
uv run chronicle build-suite scotgov-council-tax-bands-2025 \
  --year 2025 \
  --out /tmp/chronicle-scotgov-council-tax-bands-2025 \
  --replace

uv run chronicle validate-package scotgov-scottish-budget-social-security-assistance-2026 --year 2026
uv run chronicle build-suite scotgov-scottish-budget-social-security-assistance-2026 \
  --year 2026 \
  --out /tmp/chronicle-scotgov-scottish-budget-social-security-assistance-2026 \
  --replace

uv run chronicle validate-package scotgov-council-tax-collection-2025-26 --year 2026
uv run chronicle build-suite scotgov-council-tax-collection-2025-26 \
  --year 2026 \
  --out /tmp/chronicle-scotgov-council-tax-collection-2025-26 \
  --replace
uv run chronicle validate-package scotgov-council-tax-collection-2024-25 --year 2025
uv run chronicle build-suite scotgov-council-tax-collection-2024-25 \
  --year 2025 \
  --out /tmp/chronicle-scotgov-council-tax-collection-2024-25 \
  --replace
uv run chronicle validate-package scotgov-slgfs-council-tax-2024-25 --year 2026
uv run chronicle build-suite scotgov-slgfs-council-tax-2024-25 \
  --year 2026 \
  --out /tmp/chronicle-scotgov-slgfs-council-tax-2024-25 \
  --replace
uv run chronicle validate-package welshgov-council-tax-collection-2025-26 --year 2026
uv run chronicle build-suite welshgov-council-tax-collection-2025-26 \
  --year 2026 \
  --out /tmp/chronicle-welshgov-council-tax-collection-2025-26 \
  --replace
uv run chronicle validate-package welshgov-council-tax-collection-2024-25 --year 2025
uv run chronicle build-suite welshgov-council-tax-collection-2024-25 \
  --year 2025 \
  --out /tmp/chronicle-welshgov-council-tax-collection-2024-25 \
  --replace
uv run chronicle validate-package welshgov-ctrs-annual-report-2025-26 --year 2026
uv run chronicle build-suite welshgov-ctrs-annual-report-2025-26 \
  --year 2026 \
  --out /tmp/chronicle-welshgov-ctrs-annual-report-2025-26 \
  --replace
uv run chronicle validate-package welshgov-ctrs-annual-report-2024-25 --year 2025
uv run chronicle build-suite welshgov-ctrs-annual-report-2024-25 \
  --year 2025 \
  --out /tmp/chronicle-welshgov-ctrs-annual-report-2024-25 \
  --replace
uv run chronicle validate-package mhclg-council-tax-levels-england-summary-2025-26 --year 2026
uv run chronicle build-suite mhclg-council-tax-levels-england-summary-2025-26 \
  --year 2026 \
  --out /tmp/chronicle-mhclg-council-tax-levels-england-summary-2025-26 \
  --replace
uv run chronicle validate-package mhclg-council-tax-collection-england-2025-26 --year 2026
uv run chronicle build-suite mhclg-council-tax-collection-england-2025-26 \
  --year 2026 \
  --out /tmp/chronicle-mhclg-council-tax-collection-england-2025-26 \
  --replace

uv run chronicle validate-package dft-nts-vehicle-ownership-2024 --year 2024
uv run chronicle build-suite dft-nts-vehicle-ownership-2024 \
  --year 2024 \
  --out /tmp/chronicle-dft-nts-2024 \
  --replace

uv run chronicle validate-package ons-public-sector-employment-2026 --year 2026
uv run chronicle build-suite ons-public-sector-employment-2026 \
  --year 2026 \
  --out /tmp/chronicle-ons-pse-2026 \
  --replace

uv run chronicle validate-package slc-student-loan-borrower-forecasts-england-2025 --year 2025
uv run chronicle build-suite slc-student-loan-borrower-forecasts-england-2025 \
  --year 2025 \
  --out /tmp/chronicle-slc-student-loan-borrower-forecasts-england-2025 \
  --replace

uv run chronicle validate-package slc-student-loan-repayments-england-2025 --year 2025
uv run chronicle build-suite slc-student-loan-repayments-england-2025 \
  --year 2025 \
  --out /tmp/chronicle-slc-student-loan-repayments-england-2025 \
  --replace
uv run chronicle validate-package slc-student-loan-repayments-scotland-2025 --year 2025
uv run chronicle build-suite slc-student-loan-repayments-scotland-2025 \
  --year 2025 \
  --out /tmp/chronicle-slc-student-loan-repayments-scotland-2025 \
  --replace
uv run chronicle validate-package slc-student-loan-repayments-wales-2025 --year 2025
uv run chronicle build-suite slc-student-loan-repayments-wales-2025 \
  --year 2025 \
  --out /tmp/chronicle-slc-student-loan-repayments-wales-2025 \
  --replace
uv run chronicle validate-package slc-student-loan-repayments-northern-ireland-2025 --year 2025
uv run chronicle build-suite slc-student-loan-repayments-northern-ireland-2025 \
  --year 2025 \
  --out /tmp/chronicle-slc-student-loan-repayments-northern-ireland-2025 \
  --replace
```

Use [`pe-uk-source-checklist.md`](pe-uk-source-checklist.md) as the ordered
queue for UK source-package migration against PolicyEngine UK's current target
sources.

It produces source rows/cells, source-region specs, selector reports,
aggregate facts, a relational SQLite DB artifact, and per-stage JSON reports under
`/tmp/chronicle-suite/reports`. It also writes `datapackage.json` and
`ro-crate-metadata.json` sidecars so the generated artifacts can be described
with common data-package conventions while Chronicle keeps its native schema strict.
For downstream integration, agents should use the merged year bundle after
individual source packages pass:

```bash
uv run chronicle build-bundle --year 2023 --out /tmp/ledger-us-2023 --replace
```

The bundle emits a root `consumer_facts.jsonl`, `source_packages.json`,
`coverage.json`, and `reports/build_bundle.json`, while preserving each
source-package suite under `sources/<source-package>/`.

For the UK source-package feed, use the curated UK suite and build a facts-only
consumer artifact:

```bash
uv run chronicle build-bundle --suite uk --out /tmp/chronicle-uk --replace
uv run chronicle build-consumer-artifact --facts /tmp/chronicle-uk --out /tmp/chronicle-uk-artifact --replace
```

`--year` is inert for `--suite uk` because the UK packages are year-pinned.
The US off-year bundle behavior is unchanged and out of scope here.

The first agent-facing gate is now
`/tmp/chronicle-suite/reports/agent_acceptance.json`; it summarizes whether raw
artifacts have R2 pointers, the full source document was parsed, facts have
provenance and source-cell/source-row lineage, expected constraints are
first-class, row-backed facts are consistent with their parsed source rows,
concept alignments have evidence, and all stage reports are valid. It also
reports whether canonical concepts were checked
against Axiom metadata. If Axiom checking is omitted, otherwise valid packages
warn with `concept_alignment_validation_skipped`; stricter agent runs can make
that warning fatal:

```bash
uv run chronicle build-suite packages/irs_soi/table_1_1 \
  --year 2023 \
  --out /tmp/chronicle-suite \
  --replace \
  --axiom-cli axiom \
  --axiom-root ../rules-us \
  --require-axiom-validation
```

The SQLite `chronicle.db` is the source of hosted mirrors. To prepare tables for
Supabase/Postgres bulk loading, export the DB artifact rather than inserting
cells through the Supabase client:

```bash
uv run chronicle export-db-tables --db /tmp/chronicle-suite/chronicle.db --out /tmp/chronicle-mirror --replace
```

Builds produced before the rename wrote `ledger.db`. That name is still read and
published unchanged, so point `--db` at whichever file the build emitted.

Accepted build-suite outputs can be published to the private derived R2 bucket
after validation:

```bash
uv run chronicle publish-derived \
  --dir /tmp/chronicle-suite \
  --source-id irs_soi \
  --package-id soi-table-1-1 \
  --year 2023 \
  --build-artifacts-out /tmp/chronicle-build-artifacts.jsonl
```

The SQL schema is checked in at
`supabase/migrations/20260504_chronicle_bronze.sql`. Spreadsheet publications are
stored as immutable artifact metadata and one parsed-cell row per workbook cell.
Agents should not try to normalize irregular government worksheets into tidy
sheet tables before selector specs interpret them.

After the DB export and derived publish, agents can validate and load the
hosted mirror:

```bash
uv run chronicle load-supabase-mirror \
  --dir /tmp/chronicle-mirror \
  --build-artifacts /tmp/chronicle-build-artifacts.jsonl \
  --dry-run
uv run chronicle load-supabase-mirror \
  --dir /tmp/chronicle-mirror \
  --build-artifacts /tmp/chronicle-build-artifacts.jsonl
```

The live load requires `POLICYENGINE_SUPABASE_URL` and
`POLICYENGINE_SUPABASE_SERVICE_KEY`, the Chronicle mirror migration applied, and the
`chronicle` schema exposed by the Supabase Data API.

## Declarative Authoring Contract

Each `source_package.yaml` should declare one source artifact and one or more
record sets. The artifact block points at a checked manifest with publisher
filenames, source URLs, and checksums by year. PE migration URLs from
aggregators can remain in the agent queue as clues, but they should not back
canonical source cells or target facts. Each record set declares sheet name, period,
geography, entity, domain, groupby dimension, row definitions, measure columns,
units, aggregation methods, filters, and first-class constraints. The harness
compiles those rows and measures into atomic source records, validates selectors
against parsed cells, then emits target facts and the relational Chronicle DB.

Every record set must declare a `provenance_class`; there is no default. The
closed vocabulary describes the publisher's measurement basis:

- `administrative` for program, tax, collection, caseload, or payment records;
- `census` for full-enumeration or census-controlled counts;
- `survey_aggregate` for published sample-survey tabulations; and
- `model_output` for model-based estimates, outlooks, baselines, and other
  evaluation/oracle outputs.

A `survey_aggregate` record set must also name its source survey in a non-empty
`survey_instrument` string. `survey_instrument` is forbidden for every other
class. Missing, unknown, wrongly typed, and misplaced values fail package load
and build validation.

Orthogonal to `provenance_class`, every fact carries an `assertion`:
`observation` (a realized value, the default) or `source_projection` (the
publisher's forward statement — OBR forecast years, NPP projection years,
budget-allocation years). `provenance_class` says how the publisher measured;
`assertion` says whether the period had happened. The two cross freely: a
national-balance-sheet estimate is `model_output` + `observation`, an NPP
projection year is `model_output` + `source_projection`.

Microcosm's consumer-owned contracts resolve this axis explicitly rather than
by convention. A contract can declare an `assertion_policy` such as
`observed_only` (the default — projections are invisible and a
projection-only family fails loudly with `only_projection_facts`),
`prefer_observed` (per series — one geography/entity/dimension tuple: a
series with any observed fact resolves only from observations, and a series
with none may fall back to projections, so no single series ever mixes
bases across periods and a projection-only series is never starved by a
neighbouring series' observation), or
`allow_source_projection` (both compete under the period policy; an
observation and a projection colliding within one series at the chosen
period resolve to the observation with an `ambiguous_assertion_at_period`
warning naming the series rather than double-counting it). Those selector and
resolution rules are validated in Microcosm. Chronicle's responsibility is to
emit the typed `assertion` and reject unknown values so consumers never infer
the estimate/projection boundary from convention.

```yaml
record_sets:
  - record_set_id: census_acs.acs1_{year}.s0101.national_age
    provenance_class: survey_aggregate
    survey_instrument: ACS 1-year
    record_set_spec_id: census_acs.s0101.national_age.v1
```

Agents may add new package directories and YAML specs. They should not modify
`chronicle.core`, `chronicle.database`, or `chronicle.suite` unless the package cannot be
expressed in the current contract and the failure is documented in the build
report or PR notes.

Agents can scaffold a new package before filling the table-specific fields:

```bash
uv run chronicle scaffold-package --source-id irs_soi --package-id soi-table-1-2 \
  --out packages/irs_soi/table_1_2 \
  --source-table "Publication 1304 Table 1.2" \
  --resource-directory data/irs_soi/table_1_2
```

`validate-package` is the first gate. It checks required YAML fields, artifact
manifest and year availability, duplicate row and measure identifiers, malformed
Excel columns, malformed guard specs, missing row constraints, and missing
evidence for exact concept alignments. `build-suite` remains the full gate
because it parses cells, resolves selectors, builds facts, and emits the SQLite
DB artifact.
For delimited full-row sources, selected-row criteria must match exactly one
parsed source row, and row-backed filters and constraints must be evidenced by
columns in that parsed row.

## Status Levels

Agents should move source packages through explicit statuses rather than claim
production readiness immediately:

| Status | Meaning |
|--------|---------|
| `inventory` | Source artifact is identified with publisher, URL/path, vintage, checksum, local path, optional R2 key, and notes. |
| `parsed` | The artifact is preserved as parsed source rows or source cells with provenance. |
| `selected` | `validate-package` passes, source regions cover parsed cells, and cell selectors resolve with endpoint guards. |
| `semantic_candidate` | Source-record specs interpret selected cells as aggregate facts. |
| `validated` | Facts, constraints, lineage, provenance, dense-range guards, and concept checks pass. |
| `production` | A human reviewed the source family and accepted the semantics. |

## Required Gates

Before a source package can leave `semantic_candidate`, the build summary should
show zero validation errors for source cells, source records, targets, DB build,
and concept alignments. It should also report complete lineage coverage unless
the package has an explicit documented exception.

Exact source-to-canonical concept alignments require evidence notes or an
evidence URL. When the canonical concept is an Axiom ID, the package should run
the suite with `--axiom-cli` and `--axiom-root` once the corresponding Axiom
concept exists. For agent-populated packages that are ready for review, run the
same command with `--require-axiom-validation` so unresolved or unchecked
canonical concepts fail `agent_acceptance.json`.

## Review Checklist

Reviewers should inspect `reports/build_summary.json` first, then only drill
into the stage report that failed. A valid source package should make it easy to
answer these questions:

- Which source artifact was parsed, and what exact vintage/checksum backed it?
- How many rows/cells were preserved, and were any source-row or source-cell
  keys duplicated?
- Which rectangular source regions were selected, and how many cells did they cover?
- Did every source-record selector resolve to the expected cell, endpoint guard,
  and dense range label guard where applicable?
- Did every aggregate fact have provenance, dimensions, unit, aggregation, and
  source-cell or source-row lineage?
- Are constraints first-class, queryable, and simulator-neutral?
- Are exact concept alignments evidence-bearing and externally validated where
  possible?
