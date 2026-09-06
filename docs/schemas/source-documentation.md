# Source documentation v1

Status: additive local artifact contract. Registration and independent judge
approval remain separate. No database, journal, aggregate-fact contract or
existing identity changes are part of this slice.

## Decision

Questionnaires, instructions and definitions need their own publisher-excerpt
type. A sentence is not an aggregate observation. The new identifiers are
`chronicle.documentation_claim.v1` and `chronicle.documentation_artifact.v1`;
they have no invented Ledger-era aliases. The wheel-packaged schemas under
`policyengine_chronicle/schemas/` are authoritative.

`DocumentationSource` reuses `SourceArtifactMetadata`. Its declared kind must
be `source_documentation`; `microdata_release` is rejected. The first contract
supports public PDFs held locally. Every claim explicitly says
`local_unregistered`; R2 locations must be null. Neither a matching hash nor an
official URL establishes publisher authentication, Chronicle registration,
journal acceptance or witnessed custody. Source classification and publisher
attribution are caller assertions that require source-fidelity review.

Claim kinds cover question text, routing, respondent instructions, definitions,
universes, code definitions and methodological statements. Only literal
publisher excerpts belong in these fields. Microcosm transformations, derived
numeric observations, interpretation and Axiom alignments have no slot in this
contract. Consumers can link the claim key from their own provenance later.

## Producer and consumer

```python
from chronicle.sources.documentation import (
    DocumentationSource,
    documentation_claim,
    extract_documentation_page,
    write_documentation_artifact,
)
from policyengine_chronicle.documentation import load_documentation_artifact

# metadata is SourceArtifactMetadata, with SHA-256/size from retained PDF bytes.
source = DocumentationSource(metadata, "Publisher", "questionnaire")
page = extract_documentation_page(source, pdf_bytes, page_index=0)
literal = "An exact, already inspected excerpt"
start = page.encode("utf-8").index(literal.encode("utf-8"))
claim = documentation_claim(
    source, pdf_bytes, page_index=0,
    byte_start=start, byte_end=start + len(literal.encode("utf-8")),
    literal_text=literal, claim_kind="question_text",
    annotations={"item_id": "18a", "instrument_mode": "unknown"},
)
write_documentation_artifact("local-docs", [claim],
                             pdfs={metadata.sha256: pdf_bytes})
verified = load_documentation_artifact("local-docs")
```

The producer checks every claim before creating output and refuses to overwrite
existing artifacts. The output contains `manifest.json`,
`documentation_claims.jsonl`, original PDFs under `sources/<sha256>.pdf`, and
exact page UTF-8 under `pages/<pdf-sha256>/<zero-based-index>.utf8`.

The consumer validates the pinned claim schema and transport hash, reopens the
original PDFs, re-extracts each referenced page, compares every page byte and
its hash, and checks the half-open UTF-8 byte span against `literal_text`.
Matching only a stored page-text or transport hash is insufficient. Duplicate
claim keys and contradictory metadata for the same PDF are rejected.

Extraction uses the existing lock's **pypdf 6.11.0** with strict PDF reading,
plain extraction, orientations `(0, 90, 180, 270)` and space width `200.0`.
Other installed versions fail explicitly. Upgrading this profile requires
review and an explicit supported contract; an old artifact must remain
verifiable under its recorded environment. There is no automatic fallback,
OCR or normalization. `automated_exact_text` describes the actual verification;
it does not claim that a human inspected the page. Scanned/image-only pages
without text cannot produce a nonempty claim through this API.

Wheel consumers must explicitly resolve `pypdf==6.11.0` when using this optional
API. The package's broader dependency range does not select this extraction
profile outside the repository lock. Existing aggregate consumers do not need
to adopt the documentation API or its extraction environment.

## Identity and annotations

The key is SHA-256 of canonical JSON containing the original PDF SHA-256,
claim kind, the full evidence locator, and literal text. The locator contains
the zero-based PDF page index, exact page-text SHA-256, extractor profile and
half-open byte span. Changing the PDF, page, span, extraction profile, text or
claim kind changes the key. This intentionally rekeys an excerpt when its
extraction environment changes, even if its display looks identical.
The fixed extractor profile uses its canonical producer representation in the
key: equivalent JSON numbers such as `200` and `200.0` do not rekey the excerpt.
Page indexes and byte offsets must be actual integers in the Python API.

Publisher/source descriptive metadata, the printed page label, item ID,
heading, language, survey vintage, interview mode and display text do not enter
the key. Corrections to them change artifact bytes and transport hash, not the
identity of the bound excerpt. These annotations are caller-authored metadata,
not independently authenticated publisher assertions. Printed page numbers
are never substituted for canonical PDF indexes. `display_text` may normalize
whitespace for reading; `literal_text` always preserves extraction whitespace.
Do not mistake extracted Unicode text for the PDF's original content-stream
bytes, which are separately retained.

Unknown interview mode stays `unknown`. For example, a subject-definition
document describing an instruction does not establish a paper, telephone or
all-modes instrument. A methodology paper's historical mode statement is its
own dated publisher excerpt. `SourceArtifactMetadata.vintage` describes the
document; `extracted_at` describes extraction. This contract supplies no
publication, retrieval, database acceptance or witness timestamp by inference.

## Boundaries and existing work

The existing aggregate artifact/row consumers reject these new schema IDs.
`SourceArtifactSpec._artifact_content` also rejects a manifest whose declared
kind is `source_documentation`, before opening the source bytes. This narrow
guard does not claim a complete manifest-kind registry or block arbitrary
misclassification of content by a source author.

PR227 at `89737a14866a9c661abdfa28ec26b08fb238e473` changes the same source-package
method for microdata registration and kind dispatch. Its integration must
preserve this documentation rejection. This slice neither duplicates that
registration nor changes the existing raw-microdata ADR in PR222 or the
principal-artifact lineage work in issue238. Missing provenance links to
registered roots remain an explicit future integration.

Before merge, the source-fidelity, contract and boundary judges must review
the exact revision. Tests use a genuine two-page invented PDF with non-ASCII
text; actual publisher examples must remain distinguished from that fixture.
