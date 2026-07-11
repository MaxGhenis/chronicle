# Witnessed ledger releases

In the gated append flow, each proposed state of
`ledger/official_observations.jsonl` is committed by a canonical release manifest
and witnessed by two RFC 3161 timestamp authorities. The producer also signs the
manifest's exact bytes with the pinned Ed25519 release key. The manifests form an
append-only hash chain. The receipts let a verifier show that the exact manifest
bytes existed no later than each receipt's `genTime`; the producer signature
shows that the pinned release key signed those bytes.

## Files

For a stem `<index>-<hash16>`, where `index` is the zero-padded four-digit
release index and `hash16` is the first 16 lowercase hexadecimal characters of
the manifest file's SHA-256 digest, a release consists of exactly these files:

- `releases/manifests/<stem>.json`
- `releases/manifests/<stem>.freetsa.tsr`
- `releases/manifests/<stem>.digicert.tsr`
- `releases/manifests/<stem>.producer.sig`

For example, the receipt for manifest `0007-0123456789abcdef.json` is named
`0007-0123456789abcdef.freetsa.tsr`, not
`0007-0123456789abcdef.json.freetsa.tsr`. Receipts are DER-encoded RFC 3161
responses whose SHA-256 message imprint covers the manifest file's exact bytes.
Those bytes are canonical JSON produced by `scripts/canonical_json.py`, followed
by one newline. The producer sibling is a raw, DER-free 64-byte Ed25519 signature
over those same exact bytes.

The pinned TSA verification chains are committed as PEM files under
`releases/anchors/`. The producer public key is committed there as
`producer-ed25519.pub`; the verifier pins the SHA-256 digest of its DER
SubjectPublicKeyInfo to
`4a90eff40455ce0d853d4bab1608efbdae1efaf8c06054ead6e396c5b0c4846e`.
Verification does not contact either TSA or any other network service.

## Manifest schema

Every manifest uses the closed-world `thesis_ledger_release_v1` schema. Unknown
keys are invalid at every level, and counts are JSON integers (booleans are not
accepted as integers). The required root members are:

- `schemaVersion`: the literal `"thesis_ledger_release_v1"`.
- `releaseIndex`: a contiguous integer beginning at zero.
- `previousManifestSha256`: `null` for genesis; otherwise the full lowercase
  SHA-256 digest of the previous manifest file's exact bytes.
- `state`: an object containing only:
  - `path`: the literal `"ledger/official_observations.jsonl"`.
  - `jsonlSha256`: the lowercase SHA-256 digest of the ledger bytes represented
    by this release.
  - `lineCount`: the number of JSONL rows represented by this release.
  - `immutablePrefixSha256`: the lowercase SHA-256 digest of the exact bytes of
    `ledger/immutable_prefix.json`.
- `append`: `null` for genesis; otherwise an object containing only:
  - `previousLineCount`: the preceding manifest's `state.lineCount`.
  - `appendedRowCount`: the number of newly appended JSONL rows.
  - `appendedBytesSha256`: the lowercase SHA-256 digest of the exact byte suffix
    added after the preceding state.
- `createdAtUtc`: a strict UTC timestamp ending in `Z`.
- `producer`: an object containing only the free-form provenance strings `repo`
  and `branch`. These strings are recorded claims, not trusted authorization.

Genesis has index zero, a null previous hash, and a null append block. Every
later release increments the index by one, hashes the preceding manifest file,
increases the line count, and binds both the row-count delta and the exact byte
suffix in its append block. Both receipts must verify against their separately
pinned anchor chain and cover that release's exact manifest bytes. Every release,
including genesis, must also carry a valid signature from the pinned producer
key.

## Offline verification

Clone the repository at the state you want to inspect and run:

```console
python3 scripts/verify_release_chain.py --full
```

The verifier needs Python, OpenSSL, the committed manifests, signatures and
receipts, the committed anchors, and the ledger files. The project environment's
`cryptography` dependency provides portable Ed25519 verification; when that
package is unavailable, the verifier falls back to OpenSSL 3.0 or newer because
Ed25519 is a one-shot `pkeyutl -rawin` operation. It checks canonical bytes,
filenames, contiguous indices, previous-manifest links, state and append
commitments, the producer signature, both timestamp receipts, and timestamp
ordering. Any mismatch exits nonzero with an error identifying the failed
invariant.

Retain a trusted checkpoint outside this repository, at minimum the full SHA-256
digest of a previously accepted head manifest, and compare it with later clones.
Internal verification proves that a clone is self-consistent; by itself it cannot
distinguish the original history from a complete, freshly witnessed replacement
fork. RFC 3161 timestamp authorities attest timestamps for submitted digests but
do not publish a uniqueness-enforcing append-only ledger for this repository.

## Security properties and limits

Under the configured proposal gate, every ledger append arrives with the next
manifest, both receipts, and its producer signature. This binds the proposed
ledger state, immutable prefix, previous release, row-count change, and exact
appended byte suffix into a witnessed and producer-authenticated chain. Rewriting
a checkpointed manifest or producing an unwitnessed, unsigned, or internally
inconsistent state is therefore detectable by any verifier holding that
checkpoint.

An RFC 3161 `genTime` establishes that the manifest existed no later than that
time. It does **not** timestamp GitHub's merge or prove when the organization
accepted the proposal. In the intended pull-request flow the receipts are created
before the gate can pass and therefore before merge; their times do not upper-bound
the later acceptance time.

The Ed25519 signature proves that the pinned producer key signed the manifest. It
does not prove that the manifest's claims are correct, that the ledger append was
properly reviewed, or that GitHub accepted it at a particular time. The overall
mechanism provides tamper evidence and producer identity, not multi-party
authorization. Governance remains within one GitHub organization:

- It does not require approval by an independent institution or a second party.
- An unwitnessed or inconsistent admin direct push turns verification and CI red,
  but repository controls cannot prevent every admin bypass.
- An admin direct push that includes a valid next manifest, receipts, and producer
  signature is neither prevented nor distinguishable by offline cryptographic
  verification from an append merged through the intended pull-request path.
- Pull requests are split into data and gate classes. A data pull request cannot
  change the verifier, cutter, canonicalizer, append workflow, or anchors. The
  ordinary pull-request job runs the base commit's copies of those files against
  the proposed merge tree, but its workflow definition is candidate-controlled
  and is therefore test feedback rather than a complete trust root. The
  `Trusted base append gate` uses `pull_request_target` without executing
  candidate code. GitHub loads that event's workflow from the repository default
  branch, so this workflow must also be installed there, or bound as an
  organization ruleset's required workflow, before that check is active. Do not
  rely only on a name-based required status: candidate workflow YAML can emit a
  job with the same display name. A gate-only pull request can change the judge
  only for later data pull requests. Review controls and external checkpoints
  therefore remain necessary: a later gate change or an admin rewrite can still
  weaken future enforcement.
- The manifest chain hashes manifests, not receipt files. A receipt can be
  replaced by a newer valid receipt over the same manifest without changing later
  manifest hashes unless its exact bytes or digest were retained externally.
- The production anchors and producer key are fixed. There is no in-schema key- or
  anchor-rotation protocol, so a producer-key or TSA-chain change requires an
  explicit verifier/schema migration that continues to preserve verification of
  the old chain.
- Four-digit indices cap this filename format at release 9999; the current schema
  does not define a rollover.

These limitations are why a retained external checkpoint is essential and why
the current design should not be described as cross-institution governance.
