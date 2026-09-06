"""Invented PDF joins, with independent field/vintage and transport refusals."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from chronicle.sources.documentation import (
    documentation_claim,
    extract_documentation_page,
    write_documentation_artifact,
)
from policyengine_chronicle.documentation import load_documentation_artifact
from policyengine_chronicle.field_documentation import load_field_documentation
from policyengine_chronicle.consumer import load_consumer_rows
from tests.test_chronicle_source_documentation import (  # noqa: F401
    pdf,
    rewrite_claims,
    source,
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def bundle(tmp_path, source, pdf):  # noqa: F811 - imported pytest fixtures
    source = replace(source, document_role="codebook")
    claims = []
    for page_index, kind, item in [
        (0, "definition", "RENT"),
        (1, "definition", "OTHER"),
        (1, "code_definition", None),
    ]:
        text = extract_documentation_page(source, pdf, page_index=page_index)
        claims.append(
            documentation_claim(
                source,
                pdf,
                page_index=page_index,
                byte_start=0,
                byte_end=len(text.encode()),
                literal_text=text,
                claim_kind=kind,
                annotations={} if item is None else {"item_id": item},
            )
        )
    artifact = tmp_path / "documentation"
    write_documentation_artifact(artifact, claims, pdfs={source.artifact.sha256: pdf})
    manifest_sha = sha((artifact / "manifest.json").read_bytes())

    def reference(index, scope="subject"):
        ref = {
            "artifact_manifest_sha256": manifest_sha,
            "claim_key": claims[index]["claim_key"],
            "scope": scope,
            "item_id": claims[index]["annotations"].get("item_id"),
            "document_vintage": source.artifact.vintage,
        }
        if scope == "context":
            ref["rationale"] = (
                "General codes, not an assertion about this field's values."
            )
        return ref

    record = {
        "schema_version": "chronicle.survey_field_attachment.v1",
        "kind": "survey_field_attachment",
        "assertion": "curator_metadata",
        "asserted_by": "Invented test curator",
        "subject": {
            "kind": "dictionary_field",
            "documentation_pdf_sha256": source.artifact.sha256,
            "item_id": "RENT",
            "document_vintage": source.artifact.vintage,
            "anchor": reference(0),
        },
        "release_binding": {
            "status": "unresolved",
            "reason": "No registration tested.",
        },
        "facets": [
            {
                "name": "dictionary_label",
                "status": "supported",
                "assertion": "curator_locator",
                "evidence": [reference(0)],
            },
            {
                "name": "allocation_codes",
                "status": "supported",
                "assertion": "curator_locator",
                "evidence": [reference(2, "context")],
            },
            {
                "name": "roster_reference",
                "status": "unknown",
                "reason": "Not in excerpt.",
            },
        ],
    }
    return record, {manifest_sha: artifact}, reference, claims


def read(tmp_path, record, paths):
    path = tmp_path / "field.json"
    content = json.dumps(record, sort_keys=True).encode()
    path.write_bytes(content)
    return load_field_documentation(
        path, expected_sha256=sha(content), documentation_artifacts=paths
    )


def test_exact_claims_and_unknowns_remain_distinct(tmp_path, bundle):
    record, paths, _, claims = bundle
    loaded = read(tmp_path, record, paths)
    assert loaded.anchor == claims[0]
    assert loaded.facets[0].claims == (claims[0],)
    assert loaded.facets[1].claims == (claims[2],)
    assert loaded.facets[2].claims == ()
    assert loaded.record["release_binding"]["status"] == "unresolved"
    assert loaded.anchor["assertion"] == "publisher_documentation"
    assert loaded.record["assertion"] == "curator_metadata"


@pytest.mark.parametrize("status", ["unknown", "not_located", "not_applicable"])
def test_unresolved_facet_states_are_explicit(tmp_path, bundle, status):
    record, paths, _, _ = bundle
    record["facets"][2]["status"] = status
    assert read(tmp_path, record, paths).facets[2].annotation["status"] == status


def test_normalization_is_curator_assertion_not_modified_publisher_text(
    tmp_path, bundle
):
    record, paths, ref, claims = bundle
    record["facets"].append(
        {
            "name": "measurement_period",
            "status": "supported",
            "assertion": "curator_normalization",
            "value": "monthly",
            "evidence": [ref(0)],
        }
    )
    facet = read(tmp_path, record, paths).facets[-1]
    assert facet.annotation["value"] == "monthly"
    assert facet.claims == (claims[0],)


@pytest.mark.parametrize("which", ["anchor", "facet"])
def test_cross_field_subject_join_refused(tmp_path, bundle, which):
    record, paths, ref, _ = bundle
    if which == "anchor":
        record["subject"]["anchor"] = ref(1)
    else:
        record["facets"][0]["evidence"] = [ref(1)]
    with pytest.raises(ValueError, match="Cross-field"):
        read(tmp_path, record, paths)


@pytest.mark.parametrize("which", ["subject", "reference"])
def test_cross_vintage_join_refused(tmp_path, bundle, which):
    record, paths, _, _ = bundle
    target = record["subject"] if which == "subject" else record["subject"]["anchor"]
    target["document_vintage"] = "2099"
    with pytest.raises(ValueError, match="vintage"):
        read(tmp_path, record, paths)


def test_annotation_mutation_refused_even_when_claim_key_and_transport_rewritten(
    tmp_path, bundle
):
    record, paths, _, claims = bundle
    artifact = next(iter(paths.values()))
    changed = deepcopy(claims)
    changed[0]["annotations"]["instrument_mode"] = "web"
    rewrite_claims(artifact, changed)
    # The underlying documentation contract legitimately preserves claim identity.
    assert load_documentation_artifact(artifact).claims[0]["claim_key"] in {
        c["claim_key"] for c in claims
    }
    with pytest.raises(ValueError, match="manifest hash"):
        read(tmp_path, record, paths)


@pytest.mark.parametrize(
    "mutation",
    [
        "claim_missing",
        "artifact_missing",
        "item",
        "pdf",
        "role",
        "context_without_rationale",
        "context_label",
        "claim_kind",
        "duplicate_facet",
        "duplicate_reference",
        "context_anchor",
    ],
)
def test_invalid_joins_refused(tmp_path, bundle, mutation):
    record, paths, ref, _ = bundle
    first = record["facets"][0]
    if mutation == "claim_missing":
        first["evidence"][0]["claim_key"] = (
            "chronicle.documentation_claim.v1:" + "0" * 64
        )
    elif mutation == "artifact_missing":
        paths = {}
    elif mutation == "item":
        first["evidence"][0]["item_id"] = "OTHER"
    elif mutation == "pdf":
        record["subject"]["documentation_pdf_sha256"] = "0" * 64
    elif mutation == "role":
        record["subject"]["kind"] = "instrument_item"
    elif mutation == "context_without_rationale":
        del record["facets"][1]["evidence"][0]["rationale"]
    elif mutation == "context_label":
        first["evidence"] = [ref(1, "context")]
    elif mutation == "claim_kind":
        first["name"] = "question_text"
    elif mutation == "duplicate_facet":
        record["facets"].append(deepcopy(first))
    elif mutation == "duplicate_reference":
        first["evidence"].append(deepcopy(first["evidence"][0]))
    elif mutation == "context_anchor":
        record["subject"]["anchor"] = ref(0, "context")
    with pytest.raises(ValueError):
        read(tmp_path, record, paths)


@pytest.mark.parametrize(
    "mutation",
    [
        "uncertainty",
        "fit",
        "fact",
        "registration_verified",
        "evidence_on_unknown",
        "value_on_locator",
        "normalization_without_value",
        "publisher_assertion",
    ],
)
def test_closed_contract_excludes_other_authorities_and_model_decisions(
    tmp_path, bundle, mutation
):
    record, paths, ref, _ = bundle
    if mutation in {"uncertainty", "fit", "fact"}:
        record[
            {"uncertainty": "standard_error", "fit": "fit_admission", "fact": "value"}[
                mutation
            ]
        ] = 1
    elif mutation == "registration_verified":
        record["release_binding"]["status"] = "verified"
    elif mutation == "evidence_on_unknown":
        record["facets"][2]["evidence"] = [ref(0)]
    elif mutation == "value_on_locator":
        record["facets"][0]["value"] = "normalized"
    elif mutation == "normalization_without_value":
        record["facets"][0]["assertion"] = "curator_normalization"
    elif mutation == "publisher_assertion":
        record["assertion"] = "publisher_documentation"
    with pytest.raises(ValueError):
        read(tmp_path, record, paths)


def test_attachment_hash_required(tmp_path, bundle):
    record, paths, _, _ = bundle
    read(tmp_path, record, paths)
    with pytest.raises(ValueError, match="attachment hash"):
        load_field_documentation(
            tmp_path / "field.json",
            expected_sha256="0" * 64,
            documentation_artifacts=paths,
        )


def test_duplicate_json_keys_rejected_even_with_correct_hash(tmp_path):
    path = tmp_path / "field.json"
    content = b'{"kind":"survey_field_attachment","kind":"other"}'
    path.write_bytes(content)
    with pytest.raises(ValueError, match="Duplicate"):
        load_field_documentation(
            path, expected_sha256=sha(content), documentation_artifacts={}
        )


def test_reference_tuple_is_not_registration_verification(tmp_path, bundle):
    record, paths, _, _ = bundle
    record["release_binding"] = {
        "status": "unverified_reference",
        "reason": "Invented reference only; no registry or microdata read.",
        "registration": {
            "source_id": "invented_publisher",
            "package_id": "invented_release",
            "year": 2024,
            "sha256": "a" * 64,
            "filename": "invented.csv",
        },
    }
    assert (
        read(tmp_path, record, paths).record["release_binding"]
        == record["release_binding"]
    )


def test_attachment_never_enters_aggregate_fact_consumer(tmp_path, bundle):
    record, paths, _, _ = bundle
    read(tmp_path, record, paths)
    with pytest.raises(ValueError):
        load_consumer_rows(tmp_path / "field.json")


def test_non_json_numeric_constant_is_refused_before_schema(tmp_path):
    content = b'{"value":NaN}'
    path = tmp_path / "field.json"
    path.write_bytes(content)
    with pytest.raises(ValueError, match="Non-JSON"):
        load_field_documentation(
            path, expected_sha256=sha(content), documentation_artifacts={}
        )
