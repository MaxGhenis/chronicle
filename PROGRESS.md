# Microdata registration review fixes (chronicle#227)

## State

- Branch: `microdata-registration`, with reviewed head `2961c69d` rebased onto
  `origin/ops-rename-slice1` at `ea67f0a0` before round-1 gate fixes.
- Scope: close all listed artifact-registration boundary escapes, adopt the raw
  microdata ADR contract, and align registrations with microcosm#853.
- Roles: `ledger-source-ingestor` (manifests) and
  `ledger-contract-maintainer` (manifest schema and validation code).
- No microdata bytes may enter Chronicle for licensed or restricted releases.
  No work belongs under `releases/`.

## Done

- Attempted the required competing-agent process check; this sandbox denies
  both `pgrep` and `ps` process-table access.
- Attempted `git fetch origin`; DNS is unavailable. Verified the cached remote
  refs exactly match the supplied heads: `2961c69d` and `ea67f0a0`.
- Hard-reset the branch to `origin/microdata-registration` and completed the
  requested rebase onto `origin/ops-rename-slice1`.
- Resolved rebase conflicts in `PROGRESS.md`, `chronicle/artifacts.py`,
  `chronicle/harness.py`, and `chronicle/source_package.py`, keeping #226's
  strict reader, named-manifest routing, revision protection, R2 locator
  validation, bucket configuration, and Chronicle namespace changes together
  with this branch's access, kind, registration, and hash-only guards.
- Ran the rebased artifact and registration tests: 136 passed and nine exposed
  one integration defect in `inventory_source_artifacts` (the strict manifest
  read did not bind the payload used by the kind checks). Corrected the binding.

## Next

- Read the PR gate comments, ADR, governance role paths, and current Microcosm
  pins from immutable refs.
- Implement and regression-test findings A1-A9, B10-B12, and C13 in coherent
  commits, updating this file after each step.
- Run the full requested verification, write `out.md`, update the PR body, and
  push with `--force-with-lease`. Do not merge.
