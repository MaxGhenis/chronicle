# Microdata registration review fixes (chronicle#227)

## State

- Branch: `microdata-registration`, rebasing the reviewed head `2961c69d` onto
  `origin/ops-rename-slice1` at `ea67f0a0` before applying round-1 gate fixes.
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
- Hard-reset the branch to `origin/microdata-registration` and started the
  requested rebase onto `origin/ops-rename-slice1`.
- Began recording rebase conflicts for the final report; `PROGRESS.md` is the
  first conflict and is resolved as this assignment's active state log.

## Next

- Complete the rebase, preserving #226's strict manifest reader, explicit
  `--manifest`, revision identity checks, and R2 locator cross-checks.
- Read the PR gate comments, ADR, governance role paths, and current Microcosm
  pins from immutable refs.
- Implement and regression-test findings A1-A9, B10-B12, and C13 in coherent
  commits, updating this file after each step.
- Run the full requested verification, write `out.md`, update the PR body, and
  push with `--force-with-lease`. Do not merge.
