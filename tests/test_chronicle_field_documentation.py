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
    make_pdf,
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
        (0, "code_definition", "RENT"),
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


@pytest.fixture
def instrument_bundle(tmp_path, source):  # noqa: F811 - imported pytest fixture
    pdf_bytes = make_pdf(
        ((b"Invented questionnaire", b"18a. What is your monthly rent?"),)
    )
    source = replace(
        source,
        artifact=replace(
            source.artifact, sha256=sha(pdf_bytes), size_bytes=len(pdf_bytes)
        ),
    )
    text = extract_documentation_page(source, pdf_bytes, page_index=0)
    claims = [
        documentation_claim(
            source,
            pdf_bytes,
            page_index=0,
            byte_start=0,
            byte_end=len(text.encode()),
            literal_text=text,
            claim_kind=kind,
            annotations={"item_id": "18a"},
        )
        for kind in ("question_text", "respondent_instruction")
    ]
    artifact = tmp_path / "instrument"
    write_documentation_artifact(
        artifact, claims, pdfs={source.artifact.sha256: pdf_bytes}
    )
    manifest_sha = sha((artifact / "manifest.json").read_bytes())
    references = [
        {
            "artifact_manifest_sha256": manifest_sha,
            "claim_key": claim["claim_key"],
            "scope": "subject",
            "item_id": "18a",
            "document_vintage": "2024",
        }
        for claim in claims
    ]
    record = {
        "schema_version": "chronicle.survey_field_attachment.v1",
        "kind": "survey_field_attachment",
        "assertion": "curator_metadata",
        "asserted_by": "Invented test curator",
        "subject": {
            "kind": "instrument_item",
            "documentation_pdf_sha256": sha(pdf_bytes),
            "item_id": "18a",
            "document_vintage": "2024",
            "anchor": references[0],
        },
        "release_binding": {
            "status": "unresolved",
            "reason": "No registration tested.",
        },
        "facets": [
            {
                "name": "question_text",
                "status": "supported",
                "assertion": "curator_locator",
                "evidence": [references[0]],
            }
        ],
    }
    return record, {manifest_sha: artifact}, references, claims


def test_instrument_item_resolves_question_text(tmp_path, instrument_bundle):
    record, paths, _, claims = instrument_bundle
    loaded = read(tmp_path, record, paths)
    assert loaded.anchor == claims[0]
    assert loaded.facets[0].claims == (claims[0],)
    assert "What is your monthly rent?" in loaded.facets[0].claims[0]["literal_text"]
    assert loaded.record["assertion"] == "curator_metadata"


def test_instrument_item_refuses_dictionary_label(tmp_path, instrument_bundle):
    record, paths, _, _ = instrument_bundle
    record["facets"][0]["name"] = "dictionary_label"
    with pytest.raises(ValueError, match="claim kind"):
        read(tmp_path, record, paths)


@pytest.mark.parametrize("subject_kind", ["dictionary_field", "instrument_item"])
def test_anchor_requires_subject_identifying_claim_kind(
    tmp_path, bundle, instrument_bundle, subject_kind
):
    if subject_kind == "dictionary_field":
        record, paths, ref, _ = bundle
        record["subject"]["anchor"] = ref(3)
    else:
        record, paths, references, _ = instrument_bundle
        record["subject"]["anchor"] = references[1]
    # Even an otherwise valid claim with this exact item annotation and document
    # role cannot establish the required definition/question anchor by itself.
    record["facets"] = [
        {
            "name": "roster_reference",
            "status": "unknown",
            "reason": "Not established by this invented excerpt.",
        }
    ]
    with pytest.raises(ValueError, match="anchor claim kind"):
        read(tmp_path, record, paths)


@pytest.mark.parametrize(
    "key,value", [("item_id", "OTHER"), ("document_vintage", "2099")]
)
def test_context_reference_matches_its_own_item_and_vintage(
    tmp_path, bundle, key, value
):
    record, paths, _, _ = bundle
    record["facets"][1]["evidence"][0][key] = value
    with pytest.raises(ValueError, match="Documentation reference .* mismatch"):
        read(tmp_path, record, paths)


@pytest.fixture
def two_manifest_bundle(tmp_path, bundle, source):  # noqa: F811 - imported pytest fixture
    record, paths, _, claims = bundle
    pdf_bytes = make_pdf(
        ((b"Invented 2023 codebook", b"Code A means a reported amount."),)
    )
    source = replace(
        source,
        document_role="codebook",
        artifact=replace(
            source.artifact,
            sha256=sha(pdf_bytes),
            size_bytes=len(pdf_bytes),
            vintage="2023",
            source_file="invented-codes.pdf",
        ),
    )
    text = extract_documentation_page(source, pdf_bytes, page_index=0)
    claim = documentation_claim(
        source,
        pdf_bytes,
        page_index=0,
        byte_start=0,
        byte_end=len(text.encode()),
        literal_text=text,
        claim_kind="code_definition",
        annotations={},
    )
    artifact = tmp_path / "other-documentation"
    write_documentation_artifact(artifact, [claim], pdfs={sha(pdf_bytes): pdf_bytes})
    manifest_sha = sha((artifact / "manifest.json").read_bytes())
    paths[manifest_sha] = artifact
    record["facets"][1]["evidence"].append(
        {
            "artifact_manifest_sha256": manifest_sha,
            "claim_key": claim["claim_key"],
            "scope": "context",
            "item_id": None,
            "document_vintage": "2023",
            "rationale": "A separately dated general legend; no asserted field-to-code mapping.",
        }
    )
    return record, paths, claims, claim


def test_subject_and_context_evidence_resolve_across_two_pinned_manifests(
    tmp_path, two_manifest_bundle
):
    record, paths, first_claims, second_claim = two_manifest_bundle
    loaded = read(tmp_path, record, paths)
    assert loaded.anchor == first_claims[0]
    assert loaded.facets[1].claims == (first_claims[2], second_claim)
    assert loaded.facets[1].annotation["evidence"][1]["scope"] == "context"
    assert loaded.facets[1].claims[1]["source"]["artifact"]["vintage"] == "2023"
    assert loaded.record["subject"]["document_vintage"] == "2024"


@pytest.mark.parametrize(
    "mutation", ["missing", "substitution", "wrong_manifest_claim"]
)
def test_second_manifest_cannot_be_missing_substituted_or_cross_joined(
    tmp_path, two_manifest_bundle, mutation
):
    record, paths, first_claims, _ = two_manifest_bundle
    reference = record["facets"][1]["evidence"][1]
    digest = reference["artifact_manifest_sha256"]
    if mutation == "missing":
        del paths[digest]
        reason = "Missing pinned documentation artifact"
    elif mutation == "substitution":
        paths[digest] = next(path for key, path in paths.items() if key != digest)
        reason = "manifest hash"
    else:
        reference["claim_key"] = first_claims[0]["claim_key"]
        reason = "Claim not present in pinned documentation artifact"
    with pytest.raises(ValueError, match=reason):
        read(tmp_path, record, paths)
