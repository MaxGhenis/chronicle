"""Read-only, content-pinned joins from curator field metadata to documentation.

No source registration, aggregate facts, model transformations or admission.
The caller supplies local documentation locations; this API never fetches them.
"""

from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from policyengine_chronicle.documentation import (
    DocumentationArtifact,
    load_documentation_artifact,
)


@dataclass(frozen=True)
class ResolvedFieldFacet:
    """A curator assertion and the exact publisher claims it cites."""

    annotation: dict[str, Any]
    claims: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class FieldDocumentation:
    """Verified attachment transport and joins, not semantic certification."""

    path: Path
    sha256: str
    record: dict[str, Any]
    anchor: dict[str, Any]
    facets: tuple[ResolvedFieldFacet, ...]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate field attachment key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-JSON field attachment constant: {value}")


_FACET_KINDS = {
    "dictionary_label": {"definition"},
    "question_text": {"question_text"},
    "respondent_instruction": {"respondent_instruction"},
    "routing": {"routing"},
    "definition": {"definition"},
    "universe": {"definition", "universe"},
    "allocation_flag": {"definition"},
    "allocation_codes": {"code_definition"},
    "record_grain": {"definition", "methodology_statement"},
    "measurement_period": {"definition", "methodology_statement", "question_text"},
    "roster_reference": {"definition", "methodology_statement", "universe"},
    "suppression": {"definition", "code_definition", "methodology_statement"},
    "methodology": {"methodology_statement"},
}


def load_field_documentation(
    path: str | Path,
    *,
    expected_sha256: str,
    documentation_artifacts: Mapping[str, str | Path],
) -> FieldDocumentation:
    """Verify an attachment and resolve its exact documentation evidence.

    Both the attachment digest and every cited documentation manifest digest
    are mandatory. A matching claim key alone cannot bind caller annotations.
    Subject links must match the anchor's PDF, item and document vintage;
    context links are visibly distinct and require an explanation. These are
    consistency checks on pinned curator assertions, not proof that an item
    mapping or normalized value is semantically correct. Release tuples are
    unverified references only; this API does not read a registration store.
    """
    path = Path(path)
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != expected_sha256:
        raise ValueError("Field attachment hash mismatch")
    record = json.loads(
        content, object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )
    schema = json.loads(
        files("policyengine_chronicle.schemas")
        .joinpath("survey_field_attachment.v1.schema.json")
        .read_bytes()
    )
    error = next(Draft202012Validator(schema).iter_errors(record), None)
    if error is not None:
        raise ValueError(f"Field attachment: {error.json_path}: {error.message}")
    artifacts: dict[str, DocumentationArtifact] = {}
    subject = record["subject"]

    def resolve(reference: dict[str, Any]) -> dict[str, Any]:
        manifest_sha = reference["artifact_manifest_sha256"]
        if manifest_sha not in artifacts:
            if manifest_sha not in documentation_artifacts:
                raise ValueError("Missing pinned documentation artifact")
            artifacts[manifest_sha] = load_documentation_artifact(
                documentation_artifacts[manifest_sha],
                expected_manifest_sha256=manifest_sha,
            )
        matches = [
            claim
            for claim in artifacts[manifest_sha].claims
            if claim["claim_key"] == reference["claim_key"]
        ]
        if len(matches) != 1:
            raise ValueError("Claim not present in pinned documentation artifact")
        claim = matches[0]
        source = claim["source"]
        item = claim["annotations"].get("item_id")
        if source["artifact"]["vintage"] != reference["document_vintage"]:
            raise ValueError("Documentation reference vintage mismatch")
        if item != reference["item_id"]:
            raise ValueError("Documentation reference item mismatch")
        if reference["scope"] == "subject":
            if (
                source["artifact"]["sha256"] != subject["documentation_pdf_sha256"]
                or item != subject["item_id"]
                or source["artifact"]["vintage"] != subject["document_vintage"]
            ):
                raise ValueError("Cross-field or cross-vintage subject join")
            expected_role = (
                "codebook" if subject["kind"] == "dictionary_field" else "questionnaire"
            )
            if source["document_role"] != expected_role:
                raise ValueError("Subject document role mismatch")
        return claim

    if subject["anchor"]["scope"] != "subject":
        raise ValueError("Field anchor must identify its subject")
    anchor = resolve(subject["anchor"])
    expected_anchor_kind = (
        "definition" if subject["kind"] == "dictionary_field" else "question_text"
    )
    if anchor["claim_kind"] != expected_anchor_kind:
        raise ValueError(
            f"Subject anchor claim kind must be {expected_anchor_kind} "
            f"for {subject['kind']}"
        )
    facets: list[ResolvedFieldFacet] = []
    seen: set[str] = set()
    for facet in record["facets"]:
        name = facet["name"]
        if name in seen:
            raise ValueError("Duplicate field facet")
        seen.add(name)
        references = facet.get("evidence", [])
        reference_ids = [
            (ref["artifact_manifest_sha256"], ref["claim_key"]) for ref in references
        ]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("Duplicate facet evidence")
        claims = tuple(resolve(ref) for ref in references)
        if any(claim["claim_kind"] not in _FACET_KINDS[name] for claim in claims):
            raise ValueError("Documentation claim kind does not support this facet")
        if name == "dictionary_label" and any(
            claim["source"]["document_role"] != "codebook" for claim in claims
        ):
            raise ValueError("Dictionary label requires codebook evidence")
        if name == "question_text" and any(
            claim["source"]["document_role"] != "questionnaire" for claim in claims
        ):
            raise ValueError("Question text requires questionnaire evidence")
        if name in {"dictionary_label", "question_text", "allocation_flag"} and any(
            ref["scope"] != "subject" for ref in references
        ):
            raise ValueError("Item-specific facet requires subject evidence")
        facets.append(ResolvedFieldFacet(annotation=facet, claims=claims))
    return FieldDocumentation(path, digest, record, anchor, tuple(facets))
