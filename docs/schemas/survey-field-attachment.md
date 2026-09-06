# Survey field documentation attachments

`chronicle.survey_field_attachment.v1` joins curator-authored metadata to exact
publisher documentation. It is a separate local artifact, not an aggregate
fact, a microdata parser, or another source registry. No journal append, source
registration, transformation, uncertainty estimate, concept equivalence, or fit
admission follows from loading it.

## Read-only consumer

```python
from policyengine_chronicle.field_documentation import load_field_documentation

field = load_field_documentation(
    "field.json",
    expected_sha256=reviewed_field_attachment_sha256,
    documentation_artifacts={reviewed_documentation_manifest_sha256: "documentation"},
)
```

The caller supplies both independently reviewed pins and local paths. The reader
does not fetch paths, update artifacts, or discover source registrations. It
validates the closed schema, joins claim keys to the pinned documentation
manifest, then uses the existing documentation reader to reopen original PDFs,
re-extract pages and verify the exact text spans. The schema ships in the wheel.

The optional `expected_manifest_sha256` keyword on `load_documentation_artifact`
checks the manifest bytes that reader actually uses. Calls without that pin
still verify the artifact but do not bind a caller-approved manifest identity.
A claim key excludes caller annotations, so a
claim key alone is insufficient for item/mode/vintage joins. An edited annotation
changes the documentation artifact pin even when the claim key stays the same.
The attachment's own mandatory digest binds its curator annotations and joins.

## Subject, scope and authority

The subject identifies a `dictionary_field` in a codebook or an `instrument_item`
in a questionnaire, using original PDF SHA-256, item ID and document vintage.
Its anchor must resolve to a claim with matching source identity, item annotation,
vintage and document role. A dictionary-field anchor must have claim kind
`definition`; an instrument-item anchor must have claim kind `question_text`.
A code legend or instruction carrying the same item annotation cannot establish
that anchor by itself. These checks validate the curator's declared claim kind;
they do not prove that the excerpt was classified correctly. If the required
definition or question excerpt is unavailable, the reader cannot accept that
subject anchor. Do not relabel another excerpt merely to satisfy this contract.
Document vintage is the literal source metadata value;
it is not automatically an interview, tax, income reference or model year.

Every supported facet cites one or more exact claims. A `subject` citation must
match the same field/item, PDF and vintage as the anchor. A `context` citation can
point to general codes, routing, subject definitions or historical methodology,
but must carry a curator rationale and its own expected item and document vintage.
An attachment may cite multiple independently pinned documentation manifests.
Each claim resolves within its cited manifest, and a context item's ID and
vintage must match that claim even when they differ from the subject's.
It is not an assertion that another item's question belongs to this field.
Question text, dictionary labels and allocation-flag facets require subject
citations. A future field-to-instrument mapping needs a separately reviewed
correspondence contract; this version does not infer one across PDFs or item IDs.

Facet names cover dictionary labels, question text, instructions, routing,
definitions, universes, allocation flags/codes, grain, period, roster, suppression
and methodology. Claim-kind compatibility is checked. Question text must come
from a questionnaire, and dictionary labels from a codebook. Distinct facets may
cite the same claim; duplicate facets or duplicate citations within a facet fail.

The attachment and its facets are curator assertions. `curator_locator` points
to the retained claim without creating a second copy of its wording.
`curator_normalization` adds an explicitly classified string value with evidence.
The returned publisher claim remains unchanged. Successful consistency checks do
not certify the semantic correctness of the curator's classification, item
annotation, context rationale, or normalized value.

`unknown`, `not_located` and `not_applicable` are separate facet statuses. Each
requires a reason and cannot carry a supported value or evidence list. They are
not zero values. An unresolved or missing instrument item can remain explicit
while its dictionary field has other supported facets.

## Release references

`release_binding.status` is either `unresolved` with a reason, or
`unverified_reference` with a reason and the existing registration identity
tuple `{source_id, package_id, year, sha256, filename}`. The latter is a reference
only: this reader does not claim the registration exists or that its bytes were
verified. There is no `registered` or `verified` status in this version. The first
ACS/SIPP local pilot uses unresolved bindings and invents no release hashes.

The raw-identity boundary remains intact: field names and labels are obtained
from publisher documentation; the reader never extracts a microdata column or
value. Release references are metadata and do not cause private file reads.

## Pending PR 227 integration

This branch starts from main `f31d902daa2fea137de3ef00bb0999fcda01cd71` and does
not merge open PR 227 (`89737a14866a9c661abdfa28ec26b08fb238e473`) or PR 222.
Its current source-package change is only a pre-read refusal for documentation
and field-attachment kinds. It does not claim to implement PR 227's release
registration or parser hardening. The existing aggregate/epoch paths are intact.

When PR 227 lands, reconcile these specific seams:

1. Preserve its strict manifest loader, complete-manifest validation, kindless
   manifest freeze, access/licence restrictions, filename/checksum alias checks,
   sibling-manifest protections and guards before read/fetch/cache operations.
   Do not replace its `SourceArtifactSpec` with this earlier source file.
2. Keep `source_documentation` and `survey_field_attachment` excluded from the
   aggregate parser. If registration adds documentation to its closed kind set,
   provide a deliberate documentation-only path; a field attachment remains a
   derived metadata artifact, not a publisher table or microdata release.
3. Add integration tests for both non-table kinds through every guarded reader,
   including sibling manifests and aliases; retain PR 227's licensed/restricted
   and public-release refusals. Run against the exact integrated head, not against
   this branch as evidence of PR compatibility.
4. If a later consumer resolves registration tuples, preserve `consumer_pin`
   versus attested/fetched provenance. Require matching tuples and actual receipt
   evidence before introducing any stronger status. Preserve existing v1 local
   documentation artifacts and attach registration receipts separately.

The current tests are invented PDF/metadata cases; external public pilots are
separate retained artifacts. Existing aggregate keys, source uncertainty/MOE,
epoch and accepted-state files are unchanged. Actual Axiom adapter and target
uncertainty work are outside this contract.
