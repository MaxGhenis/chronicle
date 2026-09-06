# Concept-alignment report semantics

`chronicle.concepts.ConceptAlignmentReport` (built by
`validate_concept_alignments` in `chronicle/concepts.py`) describes
Chronicle's source-to-canonical concept alignments and, optionally, the
result of checking them against an external Axiom CLI. This note fixes
the semantics of two fields that are easy to misread, and states the
gap between what this module verifies and what it does not.

## Alignment identity

An alignment is uniquely identified by **every** field on
`ConceptAlignment`: `canonical_concept`, `source_concept`, `relation`,
`fact_key`, `source_record_id`, `authority`, `evidence_url`,
`evidence_notes`, and `legal_vintage`. Two facts collapse into one
alignment only when all of these match exactly. Deduplicating on a
partial key (e.g. `source_concept` + `canonical_concept` + `relation` +
`legal_vintage` alone, as `collect_concept_alignments` did before this
fix) silently discards distinct facts' identities and evidence — in
particular, it can hide that one record backing a claimed alignment has
no evidence at all, because a sibling record with evidence happened to
be collected first. `alignment_count` reflects the number of these
fully-distinct assertions, not the number of unique concept pairs.

## `checked_count` and Axiom-CLI memoization

`checked_count` is the number of **alignments** for which a check
result (cached or freshly run) was obtained — not the number of
subprocess invocations. When the same `canonical_concept` appears
across multiple distinct alignments (common: many source records
asserting the same source→canonical mapping), `validate_concept_alignments`
invokes the Axiom CLI once per distinct `canonical_concept` per call and
reuses that result for every alignment sharing it. This avoids spawning
one process per fact while still counting and reporting every alignment
individually.

For example, two facts with different record IDs or evidence retain two
alignments even when they name the same canonical concept. If the configured
protocol returns a check result, `alignment_count` and `checked_count` are both
`2`, with one subprocess invocation. In a metadata-only call with no command,
`alignment_count` remains `2` and `checked_count` is `0`. The `alignments` objects
in `to_dict()` preserve each assertion's fact key, source record, authority and
evidence fields; fields whose value is `None` are omitted.

## What counts as a successful Axiom check

A response from the configured Axiom command is accepted as valid only
when **all** of the following hold:

- the process exited `0`;
- stdout parses as a JSON object (not a scalar or list);
- its `concept_id` key, if present at all (including an explicit
  `null`), matches the concept that was requested — an *absent* key is
  the only case treated as "the CLI doesn't echo this field" and skips
  the check; a present `null` is compared like any other value and
  fails to match a non-empty requested concept id;
- its `valid` field is the literal boolean `true` (not a truthy
  non-boolean such as `"true"` or `1`);
- its `errors` field, if present and non-`null`, is a JSON list — a
  present non-list `errors` value (a string, object, or number) is
  rejected outright, regardless of `valid`; an absent or explicit
  `null` `errors` field is treated as "no errors reported", preserving
  compatibility with CLIs that omit it;
- it does not simultaneously report `valid: true` and a non-empty
  `errors` list.

Any other combination — nonzero exit with `valid: true`, a missing or
non-boolean `valid` field, a non-list `errors` field, a mismatched or
explicitly-null `concept_id`, non-object JSON, or a contradictory
`valid`/`errors` pair — is treated as an invalid concept check, never
as success. A process that fails to start or exceeds the bounded
timeout (`axiom_timeout`, default 30s) is reported as an
`axiom_cli_timeout` / `axiom_cli_unavailable` warning and checking
stops for that call; it is never reported as a passing check.

`axiom_timeout` is validated before any alignment is collected or any
subprocess is launched: it must be a finite, positive, non-boolean
`int` or `float`. `None`, `NaN`, `+inf`/`-inf`, zero, negative numbers,
non-numeric values, and `bool` (which is a subtype of `int` in Python,
so `True`/`False` are excluded explicitly) all raise `ValueError`
immediately — a bad timeout is a caller configuration error, not
something that can silently degrade into "never times out" or "times
out instantly".

## Remaining real-interface gap

This module implements and tests one specific CLI protocol:
`<axiom_command> concepts validate <concept_id> --json [--root ...]`,
returning a JSON object with `concept_id`/`valid`/`errors`. Chronicle's
tests exercise this protocol only against a fake executable written for
the test; **no compatible production implementation of this exact
protocol has been verified**. The actual Axiom Encode library exposes a
different, verified surface (`load_concept_registry`,
`ConceptRegistry`, and an `axiom-encode concepts-audit --roots ... --json`
corpus-drift CLI) — the existence of that surface is not evidence that
the `concepts validate --json` protocol assumed here exists anywhere.
Passing this module's tests demonstrates that `validate_concept_alignments`
correctly handles well- and ill-formed responses to its own assumed
protocol; it does not demonstrate semantic equivalence to any real
Axiom concept check, and no such claim should be made from it. Wiring
this module to a real Axiom interface — whatever protocol that
interface actually exposes — remains separate from this report API.
