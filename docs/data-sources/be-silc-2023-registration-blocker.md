# BE-SILC 2023: Registration Blocker

The Belgian SILC scientific-use files are a `restricted_microdata` root of
Microcosm's Belgian build, so they belong in Chronicle's microdata registry
under `docs/adr-chronicle-raw-microdata-identity.md`. They are **not
registered yet**, and this document records why.

## Blocker

A registration is identified by `{source_id, package_id, year, sha256,
filename}`. Microcosm's pin carries none of the last two. The whole artifact
entry in `packages/microcosm-build/src/microcosm/build/be/source_stages.json`
(stage `silc_load`) is:

```json
{
  "format": "csv_or_spss",
  "kind": "restricted_microdata",
  "licence": "Statbel/Eurostat scientific-use; restricted — private artifacts only",
  "locator": "Statbel BE-SILC scientific-use files: D (household register), R (personal register), H (household data), P (personal data)",
  "vintage": "2023"
}
```

There is no `sha256`, no `size_bytes`, and no per-file `filename` — the
locator names four file *roles*, not four files. Every other pinned microdata
artifact in Microcosm carries a reviewed checksum; this one does not.

`chronicle register-artifact` refuses the release rather than accepting a
placeholder, and `scripts/register_microdata_releases.py` reports it as a
blocker instead of emitting a manifest. No checksum is invented for a file
Chronicle has never seen and may never hold.

## What The Registration Will Record Once Unblocked

Everything except the identity is already known and is held in the script's
catalogue entry `statbel-be-silc-2023`:

| Field | Value |
|-------|-------|
| `source_id` | `statbel` |
| `package_id` | `statbel-be-silc-2023` |
| `year` | 2023 |
| `access` | `restricted` |
| `licence` | Statbel/Eurostat scientific-use |
| `vintage` | 2023 |
| `source_page` | <https://statbel.fgov.be/en/themes/households/poverty-and-living-conditions> |
| `access_route` | Statbel BE-SILC scientific-use files: D, R, H, P |

The access class is `restricted`, so the registration is hash-only whatever
the checksums turn out to be: no BE-SILC bytes enter any Chronicle store, and
no `ledger-raw` key exists for them.

## To Unblock

1. Microcosm publishes a reviewed SHA-256, size, and exact filename for each of
   the four scientific-use files, in `be/source_stages.json` (tracked on the
   consumer side in PolicyEngine/microcosm#848).
2. Re-run the emitter, which will pick the pins up with no catalogue change:

   ```bash
   uv run python scripts/register_microdata_releases.py \
     --microcosm-root ~/PolicyEngine/microcosm \
     --root db/data \
     --release statbel-be-silc-2023 \
     emit --verified-at <date>
   ```

3. Delete the `blocker` field from the `statbel-be-silc-2023` catalogue entry,
   and delete this document.

Until then `uv run chronicle inventory-artifacts --root db/data` reports 15
hash-only registrations — the 14 DWP Family Resources Survey 2023-24 tabs and
the HMRC Survey of Personal Incomes Public Use Tape 2022-23 — and BE-SILC is
absent by design.
