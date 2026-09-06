"""Exact publisher-documentation excerpts, separate from aggregate facts.

The first contract accepts locally held public PDFs, not registered microdata
roots. Extracted UTF-8 text is a reproducible view of a PDF, not its original
glyph/content-stream bytes. Original PDF bytes are preserved independently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
from importlib.resources import files
from io import BytesIO
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
import pypdf

from chronicle.sources.cells import SourceArtifactMetadata

CLAIM_SCHEMA_VERSION = "chronicle.documentation_claim.v1"
ARTIFACT_SCHEMA_VERSION = "chronicle.documentation_artifact.v1"
EXTRACTOR = {
    "library": "pypdf",
    "version": "6.11.0",
    "method": "PageObject.extract_text",
    "settings": {
        "extraction_mode": "plain",
        "orientations": [0, 90, 180, 270],
        "space_width": 200.0,
        "reader_strict": True,
    },
}


@dataclass(frozen=True)
class DocumentationSource:
    """Caller-classified public documentation; no registration is asserted."""

    artifact: SourceArtifactMetadata
    publisher: str
    document_role: str
    kind: str = "source_documentation"


def schema_bytes(name: str) -> bytes:
    """Read the wheel-packaged v1 contract, without any epoch migration."""
    if name not in {"documentation_claim", "documentation_artifact"}:
        raise ValueError(f"Unknown documentation schema: {name}")
    return (
        files("policyengine_chronicle.schemas")
        .joinpath(f"{name}.v1.schema.json")
        .read_bytes()
    )


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _validate_schema(value: Any, name: str) -> None:
    validator = Draft202012Validator(json.loads(schema_bytes(name)))
    error = next(validator.iter_errors(value), None)
    if error is not None:
        raise ValueError(f"{name}: {error.json_path}: {error.message}")


def extract_documentation_page(
    source: DocumentationSource, pdf_bytes: bytes, *, page_index: int
) -> str:
    """Verify original bytes and extract one zero-based PDF page exactly.

    The extractor is deliberately pinned to the existing repository lock. A
    runtime upgrade requires an explicit contract/profile migration, not a
    silent change in evidence coordinates.
    """
    if source.kind != "source_documentation":
        raise ValueError("Only source_documentation roots may supply excerpts")
    artifact = source.artifact
    if len(pdf_bytes) != artifact.size_bytes or _sha(pdf_bytes) != artifact.sha256:
        raise ValueError("PDF bytes do not match SourceArtifactMetadata hash/size")
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("Source documentation must be a PDF")
    if pypdf.__version__ != EXTRACTOR["version"]:
        raise ValueError("Documentation extraction requires pypdf 6.11.0")
    if type(page_index) is not int or page_index < 0:
        raise ValueError("PDF page_index must be a nonnegative integer")
    reader = pypdf.PdfReader(BytesIO(pdf_bytes), strict=True)
    if reader.is_encrypted:
        raise ValueError("Encrypted documentation is unsupported")
    if page_index >= len(reader.pages):
        raise ValueError("PDF page_index exceeds original document")
    return (
        reader.pages[page_index].extract_text(
            extraction_mode="plain", orientations=(0, 90, 180, 270), space_width=200.0
        )
        or ""
    )


def _claim_key(claim: Mapping[str, Any]) -> str:
    evidence = dict(claim["evidence"])
    # The closed schema permits only this profile. Encode its canonical form
    # so JSON-equivalent settings (200 versus 200.0) retain existing keys.
    # Schema validation still rejects any different extraction profile.
    evidence["extractor"] = EXTRACTOR
    identity = {
        "pdf_sha256": claim["source"]["artifact"]["sha256"],
        "claim_kind": claim["claim_kind"],
        "evidence": evidence,
        "literal_text": claim["literal_text"],
    }
    return f"{CLAIM_SCHEMA_VERSION}:{_sha(_json_bytes(identity))}"


def documentation_claim(
    source: DocumentationSource,
    pdf_bytes: bytes,
    *,
    page_index: int,
    byte_start: int,
    byte_end: int,
    literal_text: str,
    claim_kind: str,
    annotations: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Create a publisher excerpt from a checked half-open UTF-8 byte span.

    Caller-supplied descriptive annotations are not publisher assertions.
    Whitespace is preserved in literal_text; display_text may differ explicitly.
    """
    page = extract_documentation_page(source, pdf_bytes, page_index=page_index).encode(
        "utf-8"
    )
    claim = {
        "schema_version": CLAIM_SCHEMA_VERSION,
        "kind": "source_documentation",
        "claim_key": "",
        "claim_kind": claim_kind,
        "assertion": "publisher_documentation",
        "source": {**asdict(source), "custody_status": "local_unregistered"},
        "evidence": {
            "pdf_page_index": page_index,
            "page_text_sha256": _sha(page),
            "byte_start": byte_start,
            "byte_end": byte_end,
            "extractor": deepcopy(EXTRACTOR),
        },
        "literal_text": literal_text,
        "verification": "automated_exact_text",
        "annotations": {"instrument_mode": "unknown", **dict(annotations or {})},
    }
    claim["claim_key"] = _claim_key(claim)
    validate_documentation_claim(claim, pdf_bytes, page_text_bytes=page)
    return claim


def validate_documentation_claim(
    claim: Mapping[str, Any], pdf_bytes: bytes, *, page_text_bytes: bytes
) -> None:
    """Reopen original PDF, re-extract its page, and verify the literal span.

    No supplied page text, transport hash, or claim key substitutes for reading
    the original PDF. This validates evidence binding, not publisher truth or
    the correctness of caller-authored annotations.
    """
    _validate_schema(claim, "documentation_claim")
    source_fields = dict(claim["source"])
    source_fields.pop("custody_status")
    source_fields["artifact"] = SourceArtifactMetadata(**source_fields["artifact"])
    source = DocumentationSource(**source_fields)
    evidence = claim["evidence"]
    actual_page = extract_documentation_page(
        source, pdf_bytes, page_index=evidence["pdf_page_index"]
    ).encode("utf-8")
    if (
        actual_page != page_text_bytes
        or _sha(actual_page) != evidence["page_text_sha256"]
    ):
        raise ValueError(
            "Extracted page bytes/hash differ from original PDF extraction"
        )
    start, end = evidence["byte_start"], evidence["byte_end"]
    if type(start) is not int or type(end) is not int:
        raise ValueError("Evidence byte offsets must be integers")
    if not 0 <= start < end <= len(actual_page):
        raise ValueError("Evidence byte span is outside the extracted page")
    if actual_page[start:end] != claim["literal_text"].encode("utf-8"):
        raise ValueError("Literal text differs from exact UTF-8 evidence byte span")
    if _claim_key(claim) != claim["claim_key"]:
        raise ValueError("Documentation claim key does not match evidence identity")


def write_documentation_artifact(
    output_dir: str | Path,
    claims: Sequence[Mapping[str, Any]],
    *,
    pdfs: Mapping[str, bytes],
) -> Path:
    """Write verified claims, original PDFs and exact extracted pages locally.

    Only the PDFs referenced by these claims are accepted. Validation completes
    before creating output, and existing output is never replaced. This is not
    Chronicle registration, journal acceptance or a witnessed release.
    """
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(output)
    if not claims:
        raise ValueError("A documentation artifact requires at least one claim")
    rows = deepcopy(list(claims))
    pages: dict[tuple[str, int], bytes] = {}
    keys: set[str] = set()
    sources: dict[str, Any] = {}
    for claim in rows:
        _validate_schema(claim, "documentation_claim")
        digest = claim["source"]["artifact"]["sha256"]
        if digest not in pdfs:
            raise ValueError("Missing original PDF bytes for documentation claim")
        if claim["claim_key"] in keys:
            raise ValueError("Duplicate documentation claim key")
        if digest in sources and sources[digest] != claim["source"]:
            raise ValueError("Conflicting metadata for the same original PDF")
        sources[digest] = claim["source"]
        keys.add(claim["claim_key"])
        source = DocumentationSource(
            artifact=SourceArtifactMetadata(**claim["source"]["artifact"]),
            publisher=claim["source"]["publisher"],
            document_role=claim["source"]["document_role"],
        )
        page_index = claim["evidence"]["pdf_page_index"]
        page = extract_documentation_page(
            source, pdfs[digest], page_index=page_index
        ).encode("utf-8")
        validate_documentation_claim(claim, pdfs[digest], page_text_bytes=page)
        pages[digest, page_index] = page
    if set(pdfs) != set(sources):
        raise ValueError("Unreferenced source bytes are not documentation roots")
    rows.sort(key=lambda row: row["claim_key"])
    content = b"".join(_json_bytes(row) + b"\n" for row in rows)
    manifest = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": "source_documentation",
        "custody_status": "local_unregistered",
        "claim_schema_sha256": _sha(schema_bytes("documentation_claim")),
        "claims_sha256": _sha(content),
        "claim_count": len(rows),
    }
    _validate_schema(manifest, "documentation_artifact")
    output.mkdir(parents=True)
    (output / "sources").mkdir()
    for digest, content_bytes in sorted(pdfs.items()):
        (output / "sources" / f"{digest}.pdf").write_bytes(content_bytes)
    for (digest, page_index), page in sorted(pages.items()):
        directory = output / "pages" / digest
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{page_index}.utf8").write_bytes(page)
    (output / "documentation_claims.jsonl").write_bytes(content)
    (output / "manifest.json").write_bytes(_json_bytes(manifest) + b"\n")
    return output
