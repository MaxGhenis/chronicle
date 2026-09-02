"""Tests for Chronicle downstream consumer-contract exports."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import chronicle.consumer_contract as consumer_contract
from chronicle.consumer_contract import (
    CONSUMER_FACT_SCHEMA_VERSION,
    build_aggregate_fact_key,
    build_source_release_key,
    build_semantic_fact_key,
    consumer_fact_row,
    validate_consumer_fact_contract,
    write_consumer_facts_jsonl,
)
from chronicle.core import (
    AggregateConstraint,
    EntityDimension,
    GeographyDimension,
    Measure,
    PeriodDimension,
    build_aggregate_constraints,
    validate_facts,
)
from chronicle.harness import main
from chronicle.jurisdictions.us.soi import build_soi_table_1_1_facts
from chronicle.store import save_facts_jsonl
from policyengine_chronicle.consumer import (
    build_consumer_artifact,
    load_consumer_artifact,
)

CONSUMER_FACT_SCHEMA_PATH = (
    Path(__file__).parents[1] / "docs" / "schemas" / "consumer_fact.v1.schema.json"
)
CONSUMER_FACT_SAMPLE_PATH = (
    Path(__file__).parents[1] / "chronicle" / "fixtures" / "consumer_facts.jsonl"
)


def _soi_agi_fact():
    return next(
        fact
        for fact in build_soi_table_1_1_facts(2023)
        if fact.source_record_id == "irs_soi.ty2023.table_1_1.all.adjusted_gross_income"
    )


def _soi_agi_bracket_fact():
    return next(
        fact
        for fact in build_soi_table_1_1_facts(2023)
        if fact.source_record_id
        == "irs_soi.ty2023.table_1_1.1_to_5k.adjusted_gross_income"
    )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _assert_matches_schema(row: Any, schema: dict[str, Any], root: dict[str, Any]):
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            raise AssertionError(f"Unsupported test schema ref: {ref}")
        schema = root["$defs"][ref.removeprefix("#/$defs/")]

    if "not" in schema:
        try:
            _assert_matches_schema(row, schema["not"], root)
        except AssertionError:
            pass
        else:
            raise AssertionError(f"{row!r} matches forbidden schema {schema['not']!r}")

    for clause in schema.get("allOf", ()):
        condition = clause.get("if")
        if condition is None:
            _assert_matches_schema(row, clause, root)
            continue
        try:
            _assert_matches_schema(row, condition, root)
        except AssertionError:
            branch = clause.get("else")
        else:
            branch = clause.get("then")
        if branch is not None:
            _assert_matches_schema(row, branch, root)

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed_types = (
            expected_type if isinstance(expected_type, list) else [expected_type]
        )
        if not any(_matches_json_type(row, type_name) for type_name in allowed_types):
            raise AssertionError(f"{row!r} does not match type {expected_type!r}")

    if "const" in schema and row != schema["const"]:
        raise AssertionError(f"{row!r} does not match const {schema['const']!r}")
    if "enum" in schema and row not in schema["enum"]:
        raise AssertionError(f"{row!r} not in enum {schema['enum']!r}")
    if "pattern" in schema and not re.match(schema["pattern"], row):
        raise AssertionError(f"{row!r} does not match pattern {schema['pattern']!r}")

    if isinstance(row, dict):
        required = set(schema.get("required", ()))
        missing = required - set(row)
        if missing:
            raise AssertionError(f"Missing required keys: {sorted(missing)}")

        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(row) - set(properties)
            if extra:
                raise AssertionError(f"Unexpected keys: {sorted(extra)}")

        for key, value in row.items():
            if key in properties:
                _assert_matches_schema(value, properties[key], root)
            elif isinstance(schema.get("additionalProperties"), dict):
                _assert_matches_schema(value, schema["additionalProperties"], root)

    if isinstance(row, list) and "items" in schema:
        for item in row:
            _assert_matches_schema(item, schema["items"], root)


def _matches_json_type(value: Any, type_name: str) -> bool:
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    raise AssertionError(f"Unsupported JSON Schema type in test: {type_name}")


def test_consumer_fact_row_exposes_chronicle_and_lineage_keys():
    row = consumer_fact_row(_soi_agi_fact())

    assert row["schema_version"] == CONSUMER_FACT_SCHEMA_VERSION
    assert row["aggregate_fact_key"].startswith("ledger.aggregate_fact.v2:")
    assert row["semantic_fact_key"].startswith("ledger.semantic_fact.v2:")
    assert row["legacy_fact_key"].startswith("ledger.fact.v1:")
    assert row["source_release_key"].startswith("ledger.source_release.v2:")
    assert row["provenance_class"] == "administrative"
    assert "survey_instrument" not in row
    assert row["observed_measure_key"].startswith("ledger.observed_measure.v2:")
    assert row["concept_alignment"]["canonical_concept"] == (
        "us:statutes/26/62#adjusted_gross_income"
    )
    assert row["lineage"]["source_record_id"] == (
        "irs_soi.ty2023.table_1_1.all.adjusted_gross_income"
    )
    assert row["lineage"]["source_cell_keys"]


def test_aggregate_fact_key_ignores_lineage_labels_and_evidence_notes():
    fact = _soi_agi_fact()
    changed = replace(
        fact,
        label="Different human label",
        source_record_id="different.row.identity",
        source_cell_keys=("different-cell-key",),
        measure=replace(
            fact.measure,
            concept_evidence_url="https://example.test/evidence",
            concept_evidence_notes="Improved review notes.",
        ),
    )

    assert build_aggregate_fact_key(fact) == build_aggregate_fact_key(changed)


def test_semantic_fact_key_ignores_source_release_but_aggregate_key_does_not():
    fact = _soi_agi_fact()
    new_release = replace(
        fact,
        source=replace(fact.source, vintage="tax_year_2024"),
    )

    assert build_semantic_fact_key(fact) == build_semantic_fact_key(new_release)
    assert build_aggregate_fact_key(fact) != build_aggregate_fact_key(new_release)


def test_source_release_key_includes_artifact_identity():
    fact = _soi_agi_fact()
    corrected_artifact = replace(
        fact,
        source=replace(fact.source, source_sha256="0" * 64),
    )

    assert build_source_release_key(fact) != build_source_release_key(
        corrected_artifact
    )
    assert build_semantic_fact_key(fact) == build_semantic_fact_key(corrected_artifact)
    assert build_aggregate_fact_key(fact) != build_aggregate_fact_key(
        corrected_artifact
    )


def test_semantic_fact_key_ignores_source_layout_filters():
    fact = _soi_agi_bracket_fact()
    renamed_layout_filters = replace(
        fact,
        filters={
            "publisher_bucket": "$1 under $5,000",
            "publisher_row_id": "line_12",
        },
        constraints=build_aggregate_constraints(fact),
    )

    assert build_semantic_fact_key(fact) == build_semantic_fact_key(
        renamed_layout_filters
    )
    assert build_aggregate_fact_key(fact) != build_aggregate_fact_key(
        renamed_layout_filters
    )


def test_consumer_contract_rejects_implicit_filter_constraints(tmp_path):
    fact = replace(_soi_agi_bracket_fact(), constraints=())
    output = tmp_path / "consumer_facts.jsonl"

    report = validate_consumer_fact_contract([fact])

    assert not report.valid
    assert report.errors[0].code == "implicit_constraints_from_filters"
    with pytest.raises(ValueError, match="consumer-contract"):
        write_consumer_facts_jsonl([fact], output)
    assert not output.exists()


def test_consumer_contract_rejects_partial_filter_constraint_mismatch(tmp_path):
    bracket_fact = _soi_agi_bracket_fact()
    fact = replace(bracket_fact, constraints=bracket_fact.constraints[:1])
    output = tmp_path / "consumer_facts.jsonl"

    report = validate_consumer_fact_contract([fact])

    assert not report.valid
    assert report.errors[0].code == "constraint_filter_mismatch"
    with pytest.raises(ValueError, match="consumer-contract"):
        write_consumer_facts_jsonl([fact], output)
    assert not output.exists()


def test_consumer_contract_rejects_source_specific_explicit_constraints():
    bracket_fact = _soi_agi_bracket_fact()
    source_specific_constraints = build_aggregate_constraints(
        replace(bracket_fact, constraints=())
    )
    fact = replace(bracket_fact, constraints=source_specific_constraints)

    report = validate_consumer_fact_contract([fact])

    assert not report.valid
    assert report.errors[0].code == "constraint_filter_mismatch"


def test_consumer_contract_rejects_extra_source_specific_constraints():
    bracket_fact = _soi_agi_bracket_fact()
    source_specific_constraints = build_aggregate_constraints(
        replace(bracket_fact, constraints=())
    )
    fact = replace(
        bracket_fact,
        constraints=(*bracket_fact.constraints, *source_specific_constraints),
    )

    report = validate_consumer_fact_contract([fact])

    assert not report.valid
    assert "source_specific_constraint_variable" in {
        error.code for error in report.errors
    }


def test_consumer_contract_rejects_source_specific_equality_constraint():
    bracket_fact = _soi_agi_bracket_fact()
    source_specific_constraint = AggregateConstraint(
        variable="irs_soi.adjusted_gross_income",
        operator="==",
        value="1_to_5k",
    )
    fact = replace(
        bracket_fact,
        constraints=(*bracket_fact.constraints, source_specific_constraint),
    )

    report = validate_consumer_fact_contract([fact])

    assert not report.valid
    assert "source_specific_constraint_variable" in {
        error.code for error in report.errors
    }


def test_consumer_contract_rejects_unrelated_source_specific_constraint():
    bracket_fact = _soi_agi_bracket_fact()
    unrelated_constraint = AggregateConstraint(
        variable="irs_soi:some_other_variable",
        operator=">=",
        value=1,
    )
    fact = replace(
        bracket_fact,
        constraints=(*bracket_fact.constraints, unrelated_constraint),
    )

    report = validate_consumer_fact_contract([fact])

    assert not report.valid
    assert "source_specific_constraint_variable" in {
        error.code for error in report.errors
    }


def test_consumer_contract_does_not_overcanonicalize_source_filters(monkeypatch):
    bracket_fact = _soi_agi_bracket_fact()
    filing_status_constraint = AggregateConstraint(
        variable="irs_soi.filing_status",
        operator="==",
        value="single",
    )
    explicit_constraint = AggregateConstraint(
        variable=bracket_fact.layout.groupby_dimension,
        operator="==",
        value="single",
    )
    fact = replace(bracket_fact, constraints=(explicit_constraint,))
    monkeypatch.setattr(
        consumer_contract,
        "_filter_derived_constraints",
        lambda _: (filing_status_constraint,),
    )

    report = validate_consumer_fact_contract([fact])

    assert not report.valid
    assert report.errors[0].code == "constraint_filter_mismatch"


def test_consumer_contract_does_not_overcanonicalize_exact_measure_source_concept():
    bracket_fact = _soi_agi_bracket_fact()
    wrong_groupby = replace(
        bracket_fact.layout,
        groupby_dimension="us:tax#filing_status",
    )
    fact = replace(bracket_fact, layout=wrong_groupby)

    report = validate_consumer_fact_contract([fact])

    assert not report.valid
    assert report.errors[0].code == "constraint_filter_mismatch"


def test_consumer_contract_counts_duplicate_filter_constraints(monkeypatch):
    bracket_fact = _soi_agi_bracket_fact()
    duplicate_constraint = AggregateConstraint(
        variable="irs_soi.adjusted_gross_income",
        operator=">=",
        value=1,
        unit="usd",
    )
    explicit_constraint = AggregateConstraint(
        variable=bracket_fact.layout.groupby_dimension,
        operator=">=",
        value=1,
        unit="usd",
    )
    fact = replace(bracket_fact, constraints=(explicit_constraint,))
    monkeypatch.setattr(
        consumer_contract,
        "_filter_derived_constraints",
        lambda _: (duplicate_constraint, duplicate_constraint),
    )

    report = validate_consumer_fact_contract([fact])

    assert not report.valid
    assert report.errors[0].code == "constraint_filter_mismatch"


def test_consumer_fact_row_rejects_invalid_contract_fact():
    fact = replace(_soi_agi_bracket_fact(), constraints=())

    with pytest.raises(ValueError, match="consumer-contract"):
        consumer_fact_row(fact)


def test_semantic_fact_key_changes_with_canonical_concept():
    fact = _soi_agi_fact()
    changed = replace(
        fact,
        measure=Measure(
            concept="irs_soi.adjusted_gross_income_revised",
            unit=fact.measure.unit,
            source_concept=fact.measure.source_concept,
            concept_relation=fact.measure.concept_relation,
            concept_authority=fact.measure.concept_authority,
            concept_evidence_url=fact.measure.concept_evidence_url,
            concept_evidence_notes=fact.measure.concept_evidence_notes,
            legal_vintage=fact.measure.legal_vintage,
        ),
    )

    assert build_semantic_fact_key(fact) != build_semantic_fact_key(changed)


def test_write_consumer_facts_jsonl(tmp_path):
    output = tmp_path / "consumer_facts.jsonl"

    report = write_consumer_facts_jsonl([_soi_agi_fact()], output)
    rows = [json.loads(line) for line in output.read_text().splitlines()]

    assert report.to_dict() == {
        "schema_version": CONSUMER_FACT_SCHEMA_VERSION,
        "fact_count": 1,
        "output": str(output),
    }
    assert len(rows) == 1
    assert rows[0]["aggregate_fact_key"].startswith("ledger.aggregate_fact.v2:")


def _slc_student_support_fact(academic_year, value):
    """A fact shaped like the SLC/EES student-support rows this vocabulary
    exists for: recipients of maintenance loans in England, published per
    academic year (AY 2023/24 -> academic_year 2023, the opening year)."""
    base = _soi_agi_fact()
    return replace(
        base,
        value=value,
        period=PeriodDimension(type="academic_year", value=academic_year),
        geography=GeographyDimension(
            level="country", id="E92000001", vintage="current", name="England"
        ),
        entity=EntityDimension(name="person", role="student_support_recipient"),
        measure=replace(
            base.measure,
            concept="slc.maintenance_loan_recipients",
            unit="count",
            source_concept="slc.maintenance_loan_recipients",
            concept_relation="source_label",
            concept_authority="slc",
            concept_evidence_url=(
                "https://explore-education-statistics.service.gov.uk/"
                "data-tables/permalink/6ff75517-7124-487c-cb4e-08de6eccf22d"
            ),
            concept_evidence_notes=(
                "Synthetic test fixture shaped like the EES student-support "
                "rows: maintenance-loan recipients per academic year."
            ),
            legal_vintage=None,
        ),
        source=replace(
            base.source,
            source_name="slc",
            source_table="Student support for higher education in England",
        ),
        provenance_class="administrative",
        filters={},
        constraints=(),
        domain="student_support",
        label=f"Maintenance loan recipients, AY {academic_year}/"
        f"{(academic_year + 1) % 100:02d}",
    )


def test_academic_year_rows_round_trip_through_consumer_artifact(tmp_path):
    facts = [
        replace(
            _slc_student_support_fact(2023, 90.0),
            source_record_id="slc.ay2023.student_support.maintenance_loan_recipients",
            source_cell_keys=("ledger.source_cell.v1:ay2023slc",),
        ),
        replace(
            _slc_student_support_fact(2024, 100.0),
            source_record_id="slc.ay2024.student_support.maintenance_loan_recipients",
            source_cell_keys=("ledger.source_cell.v1:ay2024slc",),
        ),
    ]
    assert validate_facts(facts).valid  # the vocabulary gate itself
    facts_path = tmp_path / "consumer_facts.jsonl"
    write_consumer_facts_jsonl(facts, facts_path)
    rows = _load_jsonl(facts_path)
    assert [row["period"] for row in rows] == [
        {"type": "academic_year", "value": 2023},
        {"type": "academic_year", "value": 2024},
    ]

    artifact_dir = tmp_path / "artifact"
    build_consumer_artifact(artifact_dir, facts_path=facts_path)
    artifact = load_consumer_artifact(artifact_dir)

    assert [row["period"] for row in artifact.rows] == [
        {"type": "academic_year", "value": 2023},
        {"type": "academic_year", "value": 2024},
    ]
    assert [row["value"] for row in artifact.rows] == [90.0, 100.0]


def test_consumer_fact_row_marks_decimal_values_as_decimal_strings():
    row = consumer_fact_row(replace(_soi_agi_fact(), value=Decimal("1.25")))

    assert row["value"] == "1.25"
    assert row["value_type"] == "decimal"


def test_consumer_fact_row_preserves_required_empty_dimensions():
    row = consumer_fact_row(replace(_soi_agi_fact(), filters={}))

    assert row["dimensions"] == {}


def test_checked_in_consumer_fact_sample_matches_schema():
    schema = json.loads(CONSUMER_FACT_SCHEMA_PATH.read_text())
    rows = _load_jsonl(CONSUMER_FACT_SAMPLE_PATH)

    assert len(rows) == 3
    for row in rows:
        _assert_matches_schema(row, schema, schema)


def test_consumer_schema_requires_conditional_provenance_fields():
    schema = json.loads(CONSUMER_FACT_SCHEMA_PATH.read_text())
    row = consumer_fact_row(_soi_agi_fact())

    missing = dict(row)
    missing.pop("provenance_class")
    with pytest.raises(AssertionError, match="provenance_class"):
        _assert_matches_schema(missing, schema, schema)

    unknown = {**row, "provenance_class": "unknown"}
    with pytest.raises(AssertionError, match="not in enum"):
        _assert_matches_schema(unknown, schema, schema)

    survey_missing_instrument = {**row, "provenance_class": "survey_aggregate"}
    with pytest.raises(AssertionError, match="survey_instrument"):
        _assert_matches_schema(survey_missing_instrument, schema, schema)

    misplaced = {**row, "survey_instrument": "ACS 1-year"}
    with pytest.raises(AssertionError, match="forbidden schema"):
        _assert_matches_schema(misplaced, schema, schema)

    survey = {
        **row,
        "provenance_class": "survey_aggregate",
        "survey_instrument": "ACS 1-year",
    }
    _assert_matches_schema(survey, schema, schema)


@pytest.mark.parametrize(
    "fact",
    [
        replace(_soi_agi_fact(), provenance_class="unknown"),
        replace(_soi_agi_fact(), provenance_class="survey_aggregate"),
        replace(_soi_agi_fact(), survey_instrument="ACS 1-year"),
    ],
)
def test_consumer_export_rejects_malformed_provenance(fact):
    report = validate_consumer_fact_contract([fact])

    assert not report.valid
    assert report.errors[0].code in {
        "malformed_provenance_class",
        "missing_survey_instrument",
        "misplaced_survey_instrument",
    }
    with pytest.raises(ValueError, match="invalid Chronicle consumer-contract facts"):
        consumer_fact_row(fact)


def test_checked_in_consumer_fact_sample_matches_exporter():
    expected = [consumer_fact_row(fact) for fact in build_soi_table_1_1_facts(2023)[:3]]

    assert _load_jsonl(CONSUMER_FACT_SAMPLE_PATH) == expected


def test_generated_consumer_fact_export_matches_schema(tmp_path):
    schema = json.loads(CONSUMER_FACT_SCHEMA_PATH.read_text())
    output = tmp_path / "consumer_facts.jsonl"

    write_consumer_facts_jsonl(build_soi_table_1_1_facts(2023), output)

    for row in _load_jsonl(output):
        _assert_matches_schema(row, schema, schema)


def test_export_consumer_facts_cli_writes_fixture(tmp_path, capsys):
    output = tmp_path / "consumer_facts.jsonl"

    exit_code = main(["export-consumer-facts", "--fixture", "--output", str(output)])
    payload = json.loads(capsys.readouterr().out)
    first_row = json.loads(output.read_text().splitlines()[0])

    assert exit_code == 0
    assert payload["valid"]
    assert payload["fact_count"] == 80
    assert payload["schema_version"] == CONSUMER_FACT_SCHEMA_VERSION
    assert payload["source_validation"]["valid"]
    assert payload["contract_validation"]["valid"]
    assert first_row["aggregate_fact_key"].startswith("ledger.aggregate_fact.v2:")


def test_export_consumer_facts_cli_rejects_invalid_facts(tmp_path, capsys):
    input_path = tmp_path / "facts.jsonl"
    output_path = tmp_path / "consumer_facts.jsonl"
    invalid_fact = replace(
        _soi_agi_fact(),
        source=replace(_soi_agi_fact().source, source_name=None),
    )
    save_facts_jsonl([invalid_fact], input_path)

    exit_code = main(
        [
            "export-consumer-facts",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert not payload["valid"]
    assert not payload["source_validation"]["valid"]
    assert payload["contract_validation"]["valid"]
    assert payload["source_validation"]["errors"][0]["code"] == "missing_field"
    assert not output_path.exists()


def test_export_consumer_facts_cli_rejects_contract_invalid_facts(tmp_path, capsys):
    input_path = tmp_path / "facts.jsonl"
    output_path = tmp_path / "consumer_facts.jsonl"
    invalid_fact = replace(
        _soi_agi_fact(),
        source=replace(_soi_agi_fact().source, source_file=None),
    )
    save_facts_jsonl([invalid_fact], input_path)

    exit_code = main(
        [
            "export-consumer-facts",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert not payload["valid"]
    assert payload["source_validation"]["valid"]
    assert not payload["contract_validation"]["valid"]
    assert payload["contract_validation"]["errors"][0]["code"] == (
        "missing_contract_provenance"
    )
    assert not output_path.exists()


@pytest.mark.parametrize(
    ("overrides", "source_record_id"),
    [
        (
            {
                "source_name": "chronicle",
                "source_file": "publisher.xlsx",
                "raw_r2_bucket": "ledger-raw",
                "raw_r2_uri": "r2://ledger-raw/raw/source/publisher.xlsx",
            },
            "publisher.raw.fact",
        ),
        (
            {
                "source_name": "irs_soi",
                "source_file": "ledger-derived:taxable_interest.json",
                "raw_r2_bucket": "ledger-raw",
                "raw_r2_uri": "r2://ledger-raw/raw/source/publisher.xlsx",
            },
            "publisher.raw.fact",
        ),
        (
            {
                "source_name": "irs_soi",
                "source_file": "publisher.xlsx",
                "raw_r2_bucket": "ledger-derived",
                "raw_r2_key": "derived/source/fact.json",
                "raw_r2_uri": "r2://ledger-derived/derived/source/fact.json",
            },
            "publisher.raw.fact",
        ),
        (
            {
                "source_name": "irs_soi",
                "source_file": "publisher.xlsx",
                "raw_r2_bucket": "ledger-raw",
                "raw_r2_key": "derived/source/fact.json",
                "raw_r2_uri": "r2://ledger-raw/derived/source/fact.json",
            },
            "publisher.raw.fact",
        ),
        (
            {
                "source_name": "irs_soi",
                "source_file": "publisher.xlsx",
                "raw_r2_bucket": "ledger-raw",
                "raw_r2_uri": "r2://ledger-raw/raw/source/publisher.xlsx",
            },
            "irs_soi.ty2024.table.us.taxable_interest_amount.ledger_derived",
        ),
        # The guard used to match two hardcoded URI prefixes, so a URI naming
        # any derived bucket other than `ledger-derived` did not match. Once the
        # buckets are renamed (PolicyEngine/chronicle#143, mechanism 3) that is
        # every derived URI, so the guard has to match on shape.
        (
            {
                "source_name": "irs_soi",
                "source_file": "publisher.xlsx",
                "raw_r2_bucket": None,
                "raw_r2_key": None,
                "raw_r2_uri": "r2://chronicle-derived/derived/source/fact.json",
            },
            "publisher.raw.fact",
        ),
        (
            {
                "source_name": "irs_soi",
                "source_file": "chronicle-derived:taxable_interest.json",
                "raw_r2_bucket": "ledger-raw",
                "raw_r2_uri": "r2://ledger-raw/raw/source/publisher.xlsx",
            },
            "publisher.raw.fact",
        ),
    ],
)
def test_consumer_contract_rejects_downstream_derived_target_facts(
    overrides,
    source_record_id,
):
    fact = _soi_agi_fact()
    derived = replace(
        fact,
        source=replace(fact.source, **overrides),
        source_record_id=source_record_id,
    )

    report = validate_consumer_fact_contract([derived])

    assert not report.valid
    assert "derived_fact_provenance" in {error.code for error in report.errors}


def test_export_consumer_facts_cli_preserves_decimal_values(tmp_path, capsys):
    input_path = tmp_path / "facts.jsonl"
    output_path = tmp_path / "consumer_facts.jsonl"
    save_facts_jsonl([replace(_soi_agi_fact(), value=Decimal("1.25"))], input_path)

    exit_code = main(
        [
            "export-consumer-facts",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ]
    )
    row = json.loads(output_path.read_text().splitlines()[0])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["valid"]
    assert row["value"] == "1.25"
    assert row["value_type"] == "decimal"
