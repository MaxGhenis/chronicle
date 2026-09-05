"""Real PDF extraction and adversarial documentation-artifact boundaries."""

from copy import deepcopy
from dataclasses import replace
import hashlib
from io import BytesIO
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
import yaml

from chronicle.source_package import SourceArtifactSpec
from chronicle.sources.cells import SourceArtifactMetadata
from chronicle.sources.documentation import (
    DocumentationSource,
    documentation_claim,
    extract_documentation_page,
    write_documentation_artifact,
)
from policyengine_chronicle.consumer import load_consumer_artifact, load_consumer_rows
from policyengine_chronicle.documentation import load_documentation_artifact


@pytest.fixture
def pdf():
    """Invented, two-page PDF: actual pypdf extraction, not mocked text."""
    writer = PdfWriter()
    for lines in (
        (b"Caf\xe9 survey", b"Monthly rent excludes agency subsidies."),
        (b"Second page", b"Monthly rent excludes agency subsidies."),
    ):
        page = writer.add_blank_page(width=400, height=300)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(
            b"BT /F1 12 Tf 20 250 Td ("
            + lines[0]
            + b") Tj 0 -20 Td ("
            + lines[1]
            + b") Tj ET"
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def source(pdf):
    return DocumentationSource(
        artifact=SourceArtifactMetadata(
            source_name="invented_survey",
            source_table="Questionnaire",
            source_file="invented.pdf",
            url="https://example.test/invented.pdf",
            vintage="2024",
            sha256=hashlib.sha256(pdf).hexdigest(),
            size_bytes=len(pdf),
            extracted_at="2026-09-05",
            extraction_method="pypdf 6.11.0 plain",
        ),
        publisher="Invented test publisher",
        document_role="questionnaire",
    )


def claim_for(source, pdf, *, page_index=0, **kwargs):
    text = extract_documentation_page(source, pdf, page_index=page_index)
    literal = "Monthly rent excludes agency subsidies."
    start = text.encode().index(literal.encode())
    return documentation_claim(
        source,
        pdf,
        page_index=page_index,
        byte_start=start,
        byte_end=start + len(literal.encode()),
        literal_text=literal,
        claim_kind="respondent_instruction",
        **kwargs,
    )


def rewrite_claims(path, claims):
    content = b"".join((json.dumps(c, sort_keys=True) + "\n").encode() for c in claims)
    (path / "documentation_claims.jsonl").write_bytes(content)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["claims_sha256"] = hashlib.sha256(content).hexdigest()
    (path / "manifest.json").write_text(json.dumps(manifest))


def test_real_pdf_unicode_span_and_file_artifact_round_trip(tmp_path, source, pdf):
    text = extract_documentation_page(source, pdf, page_index=0)
    assert text == "Café survey\nMonthly rent excludes agency subsidies."
    claim = claim_for(source, pdf, annotations={"instrument_mode": "unknown"})
    # UTF-8 é takes two bytes: the span is a byte offset, not a Python str index.
    assert claim["evidence"]["byte_start"] == 13
    output = tmp_path / "artifact"
    write_documentation_artifact(output, [claim], pdfs={source.artifact.sha256: pdf})
    loaded = load_documentation_artifact(output)
    assert loaded.claims == (claim,)
    assert loaded.manifest["kind"] == "source_documentation"
    assert claim["verification"] == "automated_exact_text"
    assert claim["source"]["custody_status"] == "local_unregistered"


def test_exact_layout_is_preserved_and_annotation_changes_do_not_rekey(source, pdf):
    text = extract_documentation_page(source, pdf, page_index=0)
    claim = documentation_claim(
        source,
        pdf,
        page_index=0,
        byte_start=0,
        byte_end=len(text.encode()),
        literal_text=text,
        claim_kind="question_text",
    )
    annotated = documentation_claim(
        source,
        pdf,
        page_index=0,
        byte_start=0,
        byte_end=len(text.encode()),
        literal_text=text,
        claim_kind="question_text",
        annotations={"display_text": " ".join(text.split()), "printed_page_label": "i"},
    )
    assert annotated["claim_key"] == claim["claim_key"]
    assert annotated["literal_text"] == text
    assert "\n" in annotated["literal_text"]
    assert (
        claim_for(source, pdf, page_index=1)["claim_key"]
        != claim_for(source, pdf)["claim_key"]
    )


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("size_bytes", 1)])
def test_producer_rejects_wrong_original_bytes(source, pdf, field, value):
    bad = replace(source, artifact=replace(source.artifact, **{field: value}))
    with pytest.raises(ValueError, match="PDF bytes"):
        claim_for(bad, pdf)


@pytest.mark.parametrize("kind", ["microdata_release", "aggregate_table", "unknown"])
def test_producer_rejects_non_documentation_roots(source, pdf, kind):
    with pytest.raises(ValueError, match="source_documentation"):
        claim_for(replace(source, kind=kind), pdf)


@pytest.mark.parametrize(
    "start,end,text",
    [
        (12, 51, "Monthly rent excludes agency subsidies."),
        (3, 4, "é"),
        (0, 1000, "wrong"),
        (True, 4, "Caf"),
        (0.0, 3, "Caf"),
        (0, 0, ""),
    ],
)
def test_producer_rejects_wrong_or_non_utf8_spans(source, pdf, start, end, text):
    with pytest.raises(ValueError):
        documentation_claim(
            source,
            pdf,
            page_index=0,
            byte_start=start,
            byte_end=end,
            literal_text=text,
            claim_kind="question_text",
        )


@pytest.mark.parametrize(
    "change",
    [
        "span",
        "page_hash",
        "text",
        "key",
        "mode",
        "verification",
        "kind",
        "extractor",
        "numeric_fact",
        "interpretation",
        "registered",
    ],
)
def test_consumer_rejects_forged_claim_even_with_updated_transport_hash(
    tmp_path, source, pdf, change
):
    claim = claim_for(source, pdf)
    output = tmp_path / "artifact"
    write_documentation_artifact(output, [claim], pdfs={source.artifact.sha256: pdf})
    forged = deepcopy(claim)
    if change == "span":
        forged["evidence"]["byte_start"] -= 1
    if change == "page_hash":
        forged["evidence"]["page_text_sha256"] = "0" * 64
    if change == "text":
        forged["literal_text"] = "Subsidies are included."
    if change == "key":
        forged["claim_key"] = "chronicle.documentation_claim.v1:" + "0" * 64
    if change == "mode":
        forged["annotations"]["instrument_mode"] = "invented_mode"
    if change == "verification":
        forged["verification"] = "human_visual"
    if change == "kind":
        forged["source"]["kind"] = "microdata_release"
    if change == "extractor":
        forged["evidence"]["extractor"]["version"] = "0.0.0"
    if change == "numeric_fact":
        forged["value"] = 10
    if change == "interpretation":
        forged["claim_kind"] = "microcosm_interpretation"
    if change == "registered":
        forged["source"]["custody_status"] = "witnessed"
    rewrite_claims(output, [forged])
    with pytest.raises(ValueError):
        load_documentation_artifact(output)


@pytest.mark.parametrize("target", ["pdf", "page", "claims", "schema"])
def test_consumer_reopens_and_verifies_every_layer(tmp_path, source, pdf, target):
    claim = claim_for(source, pdf)
    output = tmp_path / "artifact"
    write_documentation_artifact(output, [claim], pdfs={source.artifact.sha256: pdf})
    if target == "pdf":
        (output / "sources" / f"{source.artifact.sha256}.pdf").write_bytes(
            pdf + b"changed"
        )
    if target == "page":
        (output / "pages" / source.artifact.sha256 / "0.utf8").write_text("changed")
    if target == "claims":
        (output / "documentation_claims.jsonl").write_text("{}\n")
    if target == "schema":
        manifest = json.loads((output / "manifest.json").read_text())
        manifest["claim_schema_sha256"] = "0" * 64
        (output / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_documentation_artifact(output)


def test_no_output_on_invalid_or_duplicate_claim_and_no_overwrite(
    tmp_path, source, pdf
):
    claim = claim_for(source, pdf)
    output = tmp_path / "artifact"
    for claims in ([claim, claim], [dict(claim, value=123)]):
        with pytest.raises(ValueError):
            write_documentation_artifact(
                output, claims, pdfs={source.artifact.sha256: pdf}
            )
        assert not output.exists()
    write_documentation_artifact(output, [claim], pdfs={source.artifact.sha256: pdf})
    before = (output / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        write_documentation_artifact(
            output, [claim], pdfs={source.artifact.sha256: pdf}
        )
    assert (output / "manifest.json").read_bytes() == before


def test_documentation_cannot_load_as_aggregate_facts(tmp_path, source, pdf):
    output = tmp_path / "artifact"
    write_documentation_artifact(
        output, [claim_for(source, pdf)], pdfs={source.artifact.sha256: pdf}
    )
    with pytest.raises(ValueError):
        load_consumer_artifact(output)
    with pytest.raises(ValueError):
        load_consumer_rows(output / "documentation_claims.jsonl")


@pytest.mark.parametrize("method", ["build_source_rows", "build_source_cells"])
def test_fact_source_parser_rejects_documentation_before_accessing_files(
    tmp_path, monkeypatch, method
):
    import chronicle.source_package as packages

    (tmp_path / "manifest.yaml").write_text("kind: source_documentation\n")
    monkeypatch.setattr(packages, "files", lambda _: tmp_path)
    spec = SourceArtifactSpec(
        source_name="test",
        source_table="test",
        resource_package="test",
        resource_directory="",
        manifest="manifest.yaml",
        vintage="2024",
        extracted_at="2026-09-05",
        extraction_method="test",
    )
    with pytest.raises(ValueError, match="source_documentation"):
        getattr(spec, method)(2024)


def test_governance_owns_narrow_fact_parser_guard():
    config = yaml.safe_load(
        (Path(__file__).parents[1] / ".github" / "chronicle-agents.yml").read_text()
    )
    maintainer = next(
        r for r in config["approved_agents"] if r["id"] == "ledger-contract-maintainer"
    )
    assert "chronicle/source_package.py" in maintainer["allowed_paths"]
    assert {"ledger-contract", "ledger-boundary"} <= set(maintainer["required_judges"])


def test_artifact_is_reproducible_and_display_change_only_changes_content(
    tmp_path, source, pdf
):
    claim = claim_for(source, pdf)
    for name in ("first", "second"):
        write_documentation_artifact(
            tmp_path / name, [claim], pdfs={source.artifact.sha256: pdf}
        )
    for path in (tmp_path / "first").rglob("*"):
        if path.is_file():
            assert (
                path.read_bytes()
                == (
                    tmp_path / "second" / path.relative_to(tmp_path / "first")
                ).read_bytes()
            )
    annotated = claim_for(
        source, pdf, annotations={"printed_page_label": "corrected label"}
    )
    assert annotated["claim_key"] == claim["claim_key"]
    write_documentation_artifact(
        tmp_path / "annotated", [annotated], pdfs={source.artifact.sha256: pdf}
    )
    assert (tmp_path / "first" / "manifest.json").read_bytes() != (
        tmp_path / "annotated" / "manifest.json"
    ).read_bytes()


def test_other_extractor_version_is_not_silently_used(source, pdf, monkeypatch):
    import pypdf

    monkeypatch.setattr(pypdf, "__version__", "6.12.0")
    with pytest.raises(ValueError, match="pypdf 6.11.0"):
        claim_for(source, pdf)


@pytest.mark.parametrize("index", [-1, 2, True, 0.5])
def test_invalid_pdf_page_indexes_are_rejected(source, pdf, index):
    with pytest.raises(ValueError, match="page_index"):
        extract_documentation_page(source, pdf, page_index=index)


def test_non_pdf_and_claimed_remote_custody_are_rejected(source, pdf):
    content = b"household_id,income\n1,123\n"
    fake = replace(
        source,
        artifact=replace(
            source.artifact,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        ),
    )
    with pytest.raises(ValueError, match="must be a PDF"):
        extract_documentation_page(fake, content, page_index=0)
    remote = replace(
        source, artifact=replace(source.artifact, raw_r2_key="invented/key")
    )
    with pytest.raises(ValueError):
        claim_for(remote, pdf)


def test_consumer_rejects_artifact_members_outside_its_directory(tmp_path, source, pdf):
    output = tmp_path / "artifact"
    write_documentation_artifact(
        output, [claim_for(source, pdf)], pdfs={source.artifact.sha256: pdf}
    )
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(pdf)
    member = output / "sources" / f"{source.artifact.sha256}.pdf"
    member.unlink()
    member.symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        load_documentation_artifact(output)
