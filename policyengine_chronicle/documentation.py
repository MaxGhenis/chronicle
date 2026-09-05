"""Public, documentation-only consumer API; aggregate APIs remain unchanged."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from chronicle.sources.documentation import (
    _sha,
    _validate_schema,
    schema_bytes,
    validate_documentation_claim,
)


@dataclass(frozen=True)
class DocumentationArtifact:
    """Locally verified publisher excerpts; not registered/witnessed facts."""

    path: Path
    manifest: dict[str, Any]
    claims: tuple[dict[str, Any], ...]


def _read_member(root: Path, relative: str) -> bytes:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Documentation artifact member escapes artifact directory")
    return path.read_bytes()


def load_documentation_artifact(path: str | Path) -> DocumentationArtifact:
    """Verify schema, transport, original PDFs, extraction and exact spans.

    Verification reopens the original PDF for each excerpt with the declared
    pinned extractor. It does not establish publisher authentication, custody,
    chronology or the semantic accuracy of the annotations.
    """
    root = Path(path)
    manifest = json.loads(_read_member(root, "manifest.json"))
    _validate_schema(manifest, "documentation_artifact")
    if manifest["claim_schema_sha256"] != _sha(schema_bytes("documentation_claim")):
        raise ValueError("Documentation claim schema hash mismatch")
    content = _read_member(root, "documentation_claims.jsonl")
    if _sha(content) != manifest["claims_sha256"]:
        raise ValueError("Documentation claims transport hash mismatch")
    rows = [json.loads(line) for line in content.splitlines()]
    if len(rows) != manifest["claim_count"]:
        raise ValueError("Documentation claim count mismatch")
    keys: set[str] = set()
    sources: dict[str, Any] = {}
    for row in rows:
        _validate_schema(row, "documentation_claim")
        digest = row["source"]["artifact"]["sha256"]
        if row["claim_key"] in keys:
            raise ValueError("Duplicate documentation claim key")
        if digest in sources and sources[digest] != row["source"]:
            raise ValueError("Conflicting metadata for the same original PDF")
        sources[digest] = row["source"]
        keys.add(row["claim_key"])
        index = row["evidence"]["pdf_page_index"]
        pdf_bytes = _read_member(root, f"sources/{digest}.pdf")
        page_bytes = _read_member(root, f"pages/{digest}/{index}.utf8")
        validate_documentation_claim(row, pdf_bytes, page_text_bytes=page_bytes)
    return DocumentationArtifact(path=root, manifest=manifest, claims=tuple(rows))
