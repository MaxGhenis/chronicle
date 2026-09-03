"""Tests for facts-only Chronicle consumer artifacts."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import pytest

from chronicle.consumer_contract import _hash_key, consumer_fact_rows
from chronicle.core import (
    DEFAULT_ASSERTION,
    AggregateConstraint,
    AggregateFact,
    Aggregation,
    EntityDimension,
    GeographyDimension,
    Measure,
    PeriodDimension,
    SourceProvenance,
    SourceRecordLayout,
)
from chronicle.harness import main
from chronicle.epoch import Epoch, canonicalize_key, hash_domain
from policyengine_chronicle.consumer import (
    build_consumer_artifact,
    load_consumer_artifact,
    load_consumer_rows,
)
from policyengine_chronicle.schema import CONSUMER_FACT_SCHEMA_SHA256

SHA = "ab" * 32


def _fact(*, value, period_value):
    return AggregateFact(
        value=value,
        period=PeriodDimension(type="tax_year", value=period_value),
        geography=GeographyDimension(
            level="country",
            id="0100000US",
            vintage="2020_census",
        ),
        entity=EntityDimension(name="tax_unit", role="filing_unit"),
        measure=Measure(concept="irs_soi.adjusted_gross_income", unit="usd"),
        aggregation=Aggregation(method="sum"),
        provenance_class="administrative",
        source=SourceProvenance(
            source_name="irs_soi",
            source_table="Table T",
            source_file="t.xls",
            url="https://example.gov/t.xls",
            vintage=f"tax_year_{period_value}",
            extracted_at="2026-05-01",
            extraction_method="test",
            source_sha256=SHA,
            source_size_bytes=10,
            raw_r2_bucket="ledger-raw",
            raw_r2_key=f"raw/irs_soi/t/{period_value}/{SHA}/t.xls",
            raw_r2_uri=f"r2://ledger-raw/raw/irs_soi/t/{period_value}/{SHA}/t.xls",
        ),
        domain="all_returns",
        source_record_id=f"irs_soi.{period_value}.t.all.agi",
        source_cell_keys=(f"ledger.source_cell.v1:{period_value}agi",),
        layout=SourceRecordLayout(
            record_set_id=f"irs_soi.{period_value}.t",
            record_set_spec_id="irs_soi.t.v1",
            measure_id="agi",
            groupby_dimension="us.agi",
        ),
    )


def _rows():
    return consumer_fact_rows(
        [_fact(value=100, period_value=2021), _fact(value=110, period_value=2022)]
    )


def _aligned_fact(*, value, period_value):
    fact = _fact(value=value, period_value=period_value)
    return replace(
        fact,
        measure=Measure(
            concept="us:statutes/26/62#adjusted_gross_income",
            unit="usd",
            source_concept="irs_soi.adjusted_gross_income",
            concept_relation="exact",
            concept_authority="ledger-us",
            concept_evidence_url="https://example.gov/statute",
            concept_evidence_notes="Publisher-to-statute alignment evidence.",
            legal_vintage=f"tax_year_{period_value}",
        ),
    )


def _constrained_fact():
    return replace(
        _aligned_fact(value=100, period_value=2021),
        constraints=(
            AggregateConstraint(
                variable="person.income",
                operator=">=",
                value=10,
                unit="usd",
                label="Arbitrary authored lower label",
            ),
            AggregateConstraint(
                variable="person.income",
                operator="<",
                value=20,
                unit="usd",
                label="Arbitrary authored upper label",
            ),
        ),
    )


def _write_facts(tmp_path):
    facts_path = tmp_path / "consumer_facts.jsonl"
    with facts_path.open("w") as file:
        for row in _rows():
            file.write(json.dumps(row, sort_keys=True) + "\n")
    return facts_path


def _write_rows(path, rows):
    with path.open("w") as file:
        for row in rows:
            file.write(json.dumps(row, sort_keys=True) + "\n")


def _recompute_aggregate_key_from_declared_children(row):
    epoch = Epoch(row["aggregate_fact_key"].split(".", maxsplit=1)[0])
    assertion = row["assertion"]
    return _hash_key(
        hash_domain("aggregate_fact", epoch),
        {
            "source_release_key": canonicalize_key(
                "source_release", row["source_release_key"]
            ),
            "source_series_key": canonicalize_key(
                "source_series", row["source_series_key"]
            ),
            "observed_measure_key": canonicalize_key(
                "observed_measure", row["observed_measure_key"]
            ),
            "aggregation": row["aggregation"],
            "period": row["period"],
            "geography": row["geography"],
            "entity": row["entity"],
            "dimension_set_key": canonicalize_key(
                "dimension_set", row["dimension_set_key"]
            ),
            "universe_constraint_set_key": canonicalize_key(
                "universe_constraint_set", row["universe_constraint_set_key"]
            ),
            "assertion": None if assertion == DEFAULT_ASSERTION else assertion,
        },
    )


def _recompute_legacy_key_from_payload(row):
    epoch = Epoch(row["legacy_fact_key"].split(".", maxsplit=1)[0])
    raw = json.dumps(
        row["layout"]["legacy_fact_key_payload"],
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{hash_domain('fact', epoch)}:{digest}"


def _rewrite_manifest_hash(out_dir):
    facts_file = out_dir / "consumer_facts.jsonl"
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["facts_sha256"] = hashlib.sha256(facts_file.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")


def test_artifact_build_load_round_trip_is_facts_only(tmp_path):
    facts_path = _write_facts(tmp_path)
    out_dir = tmp_path / "artifact"

    report = build_consumer_artifact(out_dir, facts_path=facts_path)
    artifact = load_consumer_artifact(out_dir)
    manifest = json.loads((out_dir / "manifest.json").read_text())

    assert report.to_dict() == {
        "schema_version": "policyengine_ledger.consumer_artifact.v2",
        "output_dir": str(out_dir),
        "fact_row_count": 2,
    }
    assert manifest == {
        "schema_version": "policyengine_ledger.consumer_artifact.v2",
        "consumer_fact_schema_versions": ["ledger.consumer_fact.v1"],
        "consumer_fact_schema_sha256": CONSUMER_FACT_SCHEMA_SHA256,
        "fact_row_count": 2,
        "facts_sha256": hashlib.sha256(
            (out_dir / "consumer_facts.jsonl").read_bytes()
        ).hexdigest(),
    }
    assert {path.name for path in out_dir.iterdir()} == {
        "consumer_facts.jsonl",
        "manifest.json",
    }
    assert artifact.path == out_dir
    assert len(artifact.rows) == 2


def test_artifact_is_reproducible(tmp_path):
    facts_path = _write_facts(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"

    build_consumer_artifact(first, facts_path=facts_path)
    build_consumer_artifact(second, facts_path=facts_path)

    for name in ("manifest.json", "consumer_facts.jsonl"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_chronicle_epoch_artifact_loads_end_to_end(tmp_path):
    facts_path = tmp_path / "chronicle-consumer-facts.jsonl"
    rows = consumer_fact_rows(
        [_fact(value=100, period_value=2021)], emit_epoch=Epoch.CHRONICLE
    )
    _write_rows(facts_path, rows)
    out_dir = tmp_path / "chronicle-artifact"

    report = build_consumer_artifact(
        out_dir,
        facts_path=facts_path,
        emit_epoch=Epoch.CHRONICLE,
    )
    artifact = load_consumer_artifact(out_dir)

    assert report.schema_version == "policyengine_chronicle.consumer_artifact.v3"
    assert artifact.manifest["schema_version"] == (
        "policyengine_chronicle.consumer_artifact.v3"
    )
    assert artifact.rows[0]["schema_version"] == "chronicle.consumer_fact.v2"
    assert artifact.rows[0]["aggregate_fact_key"].startswith(
        "chronicle.aggregate_fact.v3:"
    )


def test_mixed_epoch_rows_load_in_one_artifact(tmp_path):
    ledger_row = consumer_fact_rows([_fact(value=100, period_value=2021)])[0]
    chronicle_row = consumer_fact_rows(
        [_fact(value=110, period_value=2022)], emit_epoch=Epoch.CHRONICLE
    )[0]
    facts_path = tmp_path / "mixed-consumer-facts.jsonl"
    _write_rows(facts_path, [ledger_row, chronicle_row])
    out_dir = tmp_path / "mixed-artifact"

    build_consumer_artifact(out_dir, facts_path=facts_path)
    artifact = load_consumer_artifact(out_dir)

    assert artifact.manifest["consumer_fact_schema_versions"] == [
        "chronicle.consumer_fact.v2",
        "ledger.consumer_fact.v1",
    ]
    assert len(artifact.rows) == 2


def test_artifact_load_rejects_tampered_facts(tmp_path):
    facts_path = _write_facts(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(out_dir, facts_path=facts_path)

    facts_file = out_dir / "consumer_facts.jsonl"
    rows = facts_file.read_text().splitlines()
    tampered = json.loads(rows[0])
    tampered["value"] = 999
    rows[0] = json.dumps(tampered, sort_keys=True)
    facts_file.write_text("\n".join(rows) + "\n")

    with pytest.raises(ValueError, match="manifest hash"):
        load_consumer_artifact(out_dir)


def test_artifact_load_rejects_profile_metadata(tmp_path):
    facts_path = _write_facts(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(out_dir, facts_path=facts_path)
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["profiles"] = {"legacy": {"sha256": "00" * 32, "target_count": 1}}
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    with pytest.raises(ValueError, match="target profiles are consumer-owned"):
        load_consumer_artifact(out_dir)


def test_artifact_load_rejects_legacy_v1_schema(tmp_path):
    facts_path = _write_facts(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(out_dir, facts_path=facts_path)
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = "policyengine_ledger.consumer_artifact.v1"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    with pytest.raises(
        ValueError,
        match="Unsupported consumer artifact schema_version",
    ):
        load_consumer_artifact(out_dir)


def test_artifact_load_unknown_schema_names_both_accepted_forms(tmp_path):
    facts_path = _write_facts(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(out_dir, facts_path=facts_path)
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = "future.consumer_artifact.v9"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    with pytest.raises(ValueError) as error:
        load_consumer_artifact(out_dir)

    message = str(error.value)
    assert "policyengine_ledger.consumer_artifact.v2" in message
    assert "policyengine_chronicle.consumer_artifact.v3" in message


def test_artifact_load_unknown_row_schema_names_both_accepted_forms(tmp_path):
    facts_path = _write_facts(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(out_dir, facts_path=facts_path)
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["consumer_fact_schema_versions"] = ["future.consumer_fact.v9"]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    with pytest.raises(ValueError) as error:
        load_consumer_artifact(out_dir)

    message = str(error.value)
    assert "ledger.consumer_fact.v1" in message
    assert "chronicle.consumer_fact.v2" in message


def test_artifact_load_rejects_declared_row_schema_drift(tmp_path):
    facts_path = _write_facts(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(out_dir, facts_path=facts_path)
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["consumer_fact_schema_versions"] = ["chronicle.consumer_fact.v2"]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    with pytest.raises(ValueError, match="but its rows use"):
        load_consumer_artifact(out_dir)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing", "provenance_class"),
        ("unknown", "provenance_class"),
        ("wrong_type", "provenance_class"),
        ("survey_missing_instrument", "survey_instrument"),
        ("misplaced_instrument", "survey_instrument"),
    ],
)
def test_artifact_build_rejects_malformed_provenance(tmp_path, case, message):
    facts_path = _write_facts(tmp_path)
    rows = facts_path.read_text().splitlines()
    first = json.loads(rows[0])
    if case == "missing":
        first.pop("provenance_class")
    elif case == "unknown":
        first["provenance_class"] = "unknown"
    elif case == "wrong_type":
        first["provenance_class"] = 1
    elif case == "survey_missing_instrument":
        first["provenance_class"] = "survey_aggregate"
    elif case == "misplaced_instrument":
        first["survey_instrument"] = "ACS 1-year"
    rows[0] = json.dumps(first, sort_keys=True)
    facts_path.write_text("\n".join(rows) + "\n")

    with pytest.raises(ValueError, match=message):
        build_consumer_artifact(tmp_path / "artifact", facts_path=facts_path)


def test_artifact_load_rejects_row_missing_required_field(tmp_path):
    facts_path = _write_facts(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(out_dir, facts_path=facts_path)
    facts_file = out_dir / "consumer_facts.jsonl"
    rows = facts_file.read_text().splitlines()
    first = json.loads(rows[0])
    first.pop("entity")
    rows[0] = json.dumps(first, sort_keys=True)
    facts_file.write_text("\n".join(rows) + "\n")
    _rewrite_manifest_hash(out_dir)

    with pytest.raises(ValueError, match="schema validation"):
        load_consumer_artifact(out_dir)


def test_artifact_load_rejects_unknown_schema_sha256(tmp_path):
    facts_path = _write_facts(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(out_dir, facts_path=facts_path)
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["consumer_fact_schema_sha256"] = "00" * 32
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    with pytest.raises(ValueError, match="does not match the packaged"):
        load_consumer_artifact(out_dir)


def test_artifact_build_rejects_duplicate_aggregate_fact_key(tmp_path):
    facts_path = _write_facts(tmp_path)
    first = facts_path.read_text().splitlines()[0]
    facts_path.write_text(first + "\n" + first + "\n")

    with pytest.raises(ValueError, match="must be unique"):
        build_consumer_artifact(tmp_path / "artifact", facts_path=facts_path)


@pytest.mark.parametrize("emit_epoch", list(Epoch), ids=lambda epoch: epoch.value)
@pytest.mark.parametrize(
    ("key_path", "domain_name"),
    [
        (("source_release_key",), "source_release"),
        (("source_series_key",), "source_series"),
        (("observed_measure_key",), "observed_measure"),
        (("dimension_set_key",), "dimension_set"),
        (("universe_constraint_set_key",), "universe_constraint_set"),
        (("legacy_fact_key",), "fact"),
        (("semantic_fact_key",), "semantic_fact"),
        (
            ("concept_alignment", "concept_alignment_key"),
            "concept_alignment",
        ),
        (("aggregate_fact_key",), "aggregate_fact"),
    ],
    ids=(
        "source-release",
        "source-series",
        "observed-measure",
        "dimension-set",
        "universe-constraint-set",
        "legacy-fact",
        "semantic-fact",
        "concept-alignment",
        "aggregate-fact",
    ),
)
def test_load_rejects_each_forged_content_addressed_key(
    tmp_path,
    emit_epoch,
    key_path,
    domain_name,
):
    row = consumer_fact_rows([_aligned_fact(value=100, period_value=2021)])[0]
    key_container = row
    for part in key_path[:-1]:
        key_container = key_container[part]
    _prefix, _separator, genuine_digest = key_container[key_path[-1]].partition(":")
    key_container[key_path[-1]] = (
        f"{hash_domain(domain_name, emit_epoch)}:{genuine_digest}"
    )
    facts_path = tmp_path / "consumer_facts.jsonl"
    _write_rows(facts_path, [row])
    assert load_consumer_rows(facts_path) == (row,)

    key_container[key_path[-1]] = f"{hash_domain(domain_name, emit_epoch)}:{'0' * 24}"
    if key_path[-1] in {
        "source_release_key",
        "source_series_key",
        "observed_measure_key",
        "dimension_set_key",
        "universe_constraint_set_key",
    }:
        row["aggregate_fact_key"] = _recompute_aggregate_key_from_declared_children(row)
    _write_rows(facts_path, [row])

    with pytest.raises(
        ValueError,
        match=rf"{key_path[-1]}.*identity key does not match the row",
    ):
        load_consumer_rows(facts_path)


@pytest.mark.parametrize("emit_epoch", list(Epoch), ids=lambda epoch: epoch.value)
def test_load_accepts_exact_legacy_payload_for_labeled_constraints(
    tmp_path,
    emit_epoch,
):
    row = consumer_fact_rows([_constrained_fact()], emit_epoch=emit_epoch)[0]
    legacy_payload = row["layout"]["legacy_fact_key_payload"]

    assert [constraint["operator"] for constraint in legacy_payload["constraints"]] == [
        ">=",
        "<",
    ]
    assert [
        constraint["operator"]
        for constraint in row["universe_constraints"]["constraints"]
    ] == ["<", ">="]
    facts_path = tmp_path / "consumer_facts.jsonl"
    _write_rows(facts_path, [row])
    assert load_consumer_rows(facts_path) == (row,)


@pytest.mark.parametrize("emit_epoch", list(Epoch), ids=lambda epoch: epoch.value)
@pytest.mark.parametrize(
    ("evidence_url", "evidence_notes"),
    [
        ("https://example.gov/evidence", "Evidence without an alignment."),
        ("", None),
    ],
    ids=("nonempty", "empty-string"),
)
def test_load_accepts_legacy_payload_for_evidence_without_alignment(
    tmp_path,
    emit_epoch,
    evidence_url,
    evidence_notes,
):
    fact = _fact(value=100, period_value=2021)
    fact = replace(
        fact,
        measure=replace(
            fact.measure,
            concept_evidence_url=evidence_url,
            concept_evidence_notes=evidence_notes,
        ),
    )
    row = consumer_fact_rows([fact], emit_epoch=emit_epoch)[0]

    assert "concept_alignment" not in row
    assert (
        row["layout"]["legacy_fact_key_payload"]["measure"]["concept_evidence_url"]
        == evidence_url
    )
    facts_path = tmp_path / "consumer_facts.jsonl"
    _write_rows(facts_path, [row])
    assert load_consumer_rows(facts_path) == (row,)


@pytest.mark.parametrize(
    "measure_change",
    [
        {"source_concept": ""},
        {"concept_relation": ""},
        {"concept_authority": ""},
        {"legal_vintage": ""},
    ],
    ids=("source-concept", "relation", "authority", "legal-vintage"),
)
def test_load_accepts_falsy_legacy_measure_metadata(tmp_path, measure_change):
    fact = _fact(value=100, period_value=2021)
    fact = replace(fact, measure=replace(fact.measure, **measure_change))
    row = consumer_fact_rows([fact])[0]

    assert "concept_alignment" not in row
    assert "legacy_fact_key_payload" in row["layout"]
    facts_path = tmp_path / "consumer_facts.jsonl"
    _write_rows(facts_path, [row])
    assert load_consumer_rows(facts_path) == (row,)


def test_ordinary_row_does_not_gain_a_legacy_payload_envelope():
    row = consumer_fact_rows([_aligned_fact(value=100, period_value=2021)])[0]

    assert "legacy_fact_key_payload" not in row["layout"]


def test_load_rejects_missing_legacy_payload_for_constraints(tmp_path):
    row = consumer_fact_rows([_constrained_fact()])[0]
    row["layout"].pop("legacy_fact_key_payload")
    facts_path = tmp_path / "consumer_facts.jsonl"
    _write_rows(facts_path, [row])

    with pytest.raises(
        ValueError,
        match="legacy_fact_key.*identity key does not match the row",
    ):
        load_consumer_rows(facts_path)


def test_load_rejects_tampered_legacy_payload(tmp_path):
    row = consumer_fact_rows([_constrained_fact()])[0]
    row["layout"]["legacy_fact_key_payload"]["constraints"][0]["label"] = (
        "Tampered label"
    )
    facts_path = tmp_path / "consumer_facts.jsonl"
    _write_rows(facts_path, [row])

    with pytest.raises(
        ValueError,
        match="legacy_fact_key.*identity key does not match the row",
    ):
        load_consumer_rows(facts_path)


@pytest.mark.parametrize(
    "tamper",
    [
        "visible-field",
        "unknown-field",
        "numeric-type",
        "missing-measure-key",
        "unexpected-alignment-field",
    ],
)
def test_load_rejects_rehashed_inconsistent_legacy_payload(tmp_path, tamper):
    if tamper in {"missing-measure-key", "unexpected-alignment-field"}:
        fact = _fact(value=100, period_value=2021)
        fact = replace(
            fact,
            measure=replace(
                fact.measure,
                concept_evidence_notes="Evidence without an alignment.",
            ),
        )
    else:
        fact = _constrained_fact()
    row = consumer_fact_rows([fact])[0]
    legacy_payload = row["layout"]["legacy_fact_key_payload"]
    if tamper == "visible-field":
        legacy_payload["period"]["value"] = 2022
    elif tamper == "unknown-field":
        legacy_payload["constraints"][0]["unknown"] = "not canonical"
    elif tamper == "numeric-type":
        legacy_payload["constraints"][0]["value"] = 10.0
    elif tamper == "missing-measure-key":
        legacy_payload["measure"].pop("concept_evidence_notes")
    else:
        legacy_payload["measure"]["source_concept"] = "forged.source_concept"
    row["legacy_fact_key"] = _recompute_legacy_key_from_payload(row)
    facts_path = tmp_path / "consumer_facts.jsonl"
    _write_rows(facts_path, [row])

    with pytest.raises(ValueError, match="content that does not match"):
        load_consumer_rows(facts_path)


def test_load_rejects_a_false_manifest_row_count(tmp_path):
    facts_path = _write_facts(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(out_dir, facts_path=facts_path)
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["fact_row_count"] = 999
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    with pytest.raises(ValueError, match="declares fact_row_count"):
        load_consumer_artifact(out_dir)


def test_build_consumer_artifact_help_has_no_profile_options(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["build-consumer-artifact", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "--profile" not in help_text
    assert "facts-only" in help_text
