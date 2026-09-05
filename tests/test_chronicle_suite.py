"""Tests for the Chronicle source-package build suite."""

from __future__ import annotations

from dataclasses import replace
import json
import sqlite3
import textwrap

from chronicle.concepts import ConceptAlignmentReport
from chronicle.core import (
    Aggregation,
    AggregateConstraint,
    AggregateFact,
    EntityDimension,
    GeographyDimension,
    Measure,
    PeriodDimension,
    SourceProvenance,
    SourceRecordLayout,
    validate_facts,
)
from chronicle.epoch import HASH_DOMAINS, Epoch
from chronicle.harness import main as harness_main
from chronicle.sources.cells import (
    SourceArtifactMetadata,
    SourceCell,
    build_source_cell_key,
    validate_source_cells,
)
from chronicle.sources.rows import SourceRow, build_source_row_key, validate_source_rows
from chronicle.suite import (
    SourceRecordSuiteReport,
    SourceRegionSuiteReport,
    _wide_table_filter_evidenced_by_source_column,
    build_agent_acceptance_report,
    build_source_cells,
    build_source_record_specs,
    build_source_regions,
    build_source_suite,
    validate_source_record_specs,
    validate_source_regions,
)


def test_wide_table_evidence_requires_explicit_column_dimension():
    assert not _wide_table_filter_evidenced_by_source_column(
        {},
        "provision",
        "all",
    )


def test_wide_table_evidence_uses_exact_typed_column_dimension_value():
    dimensions = {"provision": "all"}

    assert _wide_table_filter_evidenced_by_source_column(
        dimensions,
        "provision",
        "all",
    )
    assert not _wide_table_filter_evidenced_by_source_column(
        dimensions,
        "provision",
        "All",
    )
    assert not _wide_table_filter_evidenced_by_source_column(
        dimensions,
        "registration_basis",
        "registered_children",
    )


def test_build_source_suite_writes_artifacts_and_reports(tmp_path):
    output_dir = tmp_path / "suite"

    report = build_source_suite("soi-table-1-1", output_dir, year=2023)
    summary = json.loads((output_dir / "reports" / "build_summary.json").read_text())

    assert report.valid
    assert summary["valid"]
    assert summary["counts"] == {
        "artifact_count": 1,
        "agent_acceptance_error_count": 0,
        "concept_alignment_count": 20,
        "constraint_count": 144,
        "consumer_fact_count": 80,
        "fact_count": 80,
        "lineage_coverage": 1.0,
        "source_cell_count": 1932,
        "source_record_count": 80,
        "source_region_count": 1,
        "source_row_count": 0,
    }
    assert (output_dir / "source_cells.jsonl").exists()
    assert (output_dir / "source_regions.jsonl").exists()
    assert (output_dir / "facts.jsonl").exists()
    assert (output_dir / "consumer_facts.jsonl").exists()
    assert (output_dir / "ledger.db").exists()
    assert (output_dir / "datapackage.json").exists()
    assert (output_dir / "ro-crate-metadata.json").exists()
    assert (output_dir / "reports" / "source_regions.json").exists()
    assert (output_dir / "reports" / "selectors.json").exists()
    assert (output_dir / "reports" / "source_records.json").exists()
    assert (output_dir / "reports" / "consumer_facts.json").exists()
    assert (output_dir / "reports" / "agent_acceptance.json").exists()
    consumer_facts = [
        json.loads(line)
        for line in (output_dir / "consumer_facts.jsonl").read_text().splitlines()
    ]
    datapackage = json.loads((output_dir / "datapackage.json").read_text())
    acceptance = json.loads(
        (output_dir / "reports" / "agent_acceptance.json").read_text()
    )

    assert len(consumer_facts) == 80
    assert consumer_facts[0]["aggregate_fact_key"].startswith(
        "ledger.aggregate_fact.v2:"
    )
    assert consumer_facts[0]["semantic_fact_key"].startswith("ledger.semantic_fact.v2:")
    assert datapackage["profile"] == "data-package"
    assert {resource["path"] for resource in datapackage["resources"]} >= {
        "source_cells.jsonl",
        "source_regions.jsonl",
        "facts.jsonl",
        "consumer_facts.jsonl",
        "ledger.db",
        "reports/build_summary.json",
        "reports/source_regions.json",
        "reports/selectors.json",
        "reports/consumer_facts.json",
        "reports/agent_acceptance.json",
    }
    assert acceptance["valid"]
    assert acceptance["checks"] == {
        "concept_alignments_have_evidence": True,
        "concept_alignments_resolve": True,
        "expected_constraints_present": True,
        "facts_have_provenance": True,
        "facts_have_source_cell_lineage": True,
        "facts_have_source_row_lineage": True,
        "full_source_document_parsed": True,
        "raw_artifacts_have_r2": True,
        "required_concept_alignments_validated": True,
        "row_lineage_semantics_evidenced": True,
        "selected_row_only_parser_not_used": True,
        "stage_reports_valid": True,
    }
    assert [warning["code"] for warning in acceptance["warnings"]] == [
        "concept_alignment_validation_skipped"
    ]

    with sqlite3.connect(output_dir / "ledger.db") as connection:
        facts_count = connection.execute(
            "SELECT COUNT(*) FROM aggregate_facts"
        ).fetchone()[0]

    assert facts_count == 80


def test_build_source_suite_supports_soi_table_1_4(tmp_path):
    output_dir = tmp_path / "suite"

    report = build_source_suite("soi-table-1-4", output_dir, year=2023)
    summary = json.loads((output_dir / "reports" / "build_summary.json").read_text())

    assert report.valid
    assert summary["counts"] == {
        "artifact_count": 1,
        "agent_acceptance_error_count": 0,
        "concept_alignment_count": 160,
        "constraint_count": 1044,
        "consumer_fact_count": 580,
        "fact_count": 580,
        "lineage_coverage": 1.0,
        "source_cell_count": 8109,
        "source_record_count": 580,
        "source_region_count": 1,
        "source_row_count": 0,
    }
    assert summary["reports"]["source_regions"]["covered_cell_count"] == 2460
    assert summary["reports"]["agent_acceptance"]["valid"]
    assert summary["reports"]["agent_acceptance"]["warnings"][0]["code"] == (
        "concept_alignment_validation_skipped"
    )
    assert (output_dir / "source_regions.jsonl").exists()
    assert (output_dir / "ledger.db").exists()


def test_agent_acceptance_accepts_aggregate_income_range_source_rows():
    artifact = SourceArtifactMetadata(
        source_name="irs_soi",
        source_table="Historic Table 2 state AGI facts",
        source_file="test.csv",
        url="https://example.test/test.csv",
        vintage="tax_year_2022",
        sha256="abc123",
        size_bytes=10,
        extracted_at="2026-05-06",
        extraction_method="test",
        raw_r2_bucket="ledger-raw",
        raw_r2_key="raw/irs_soi/test.csv",
        raw_r2_uri="r2://ledger-raw/raw/irs_soi/test.csv",
    )
    row_500k_to_1m = SourceRow(
        artifact=artifact,
        sheet_name="in55cmcsv",
        row_number=10,
        values={"AGI_STUB": 9},
    )
    row_1m_plus = SourceRow(
        artifact=artifact,
        sheet_name="in55cmcsv",
        row_number=11,
        values={"AGI_STUB": 10},
    )
    row_keys = (
        build_source_row_key(row_500k_to_1m),
        build_source_row_key(row_1m_plus),
    )
    cells = [
        SourceCell(
            artifact=artifact,
            sheet_name="in55cmcsv",
            row_number=10,
            column_number=1,
            address="A10",
            cell_type="number",
            raw_value=1,
            display_value="1",
            source_row_key=row_keys[0],
        ),
        SourceCell(
            artifact=artifact,
            sheet_name="in55cmcsv",
            row_number=11,
            column_number=1,
            address="A11",
            cell_type="number",
            raw_value=2,
            display_value="2",
            source_row_key=row_keys[1],
        ),
    ]
    fact = AggregateFact(
        value=3,
        period=PeriodDimension(type="tax_year", value=2022),
        geography=GeographyDimension(
            level="country",
            id="0100000US",
            vintage="current",
            name="United States",
        ),
        entity=EntityDimension(name="tax_unit"),
        measure=Measure(concept="irs_soi.taxable_interest", unit="usd"),
        aggregation=Aggregation(method="sum"),
        provenance_class="administrative",
        source=SourceProvenance(
            source_name="irs_soi",
            source_table="test",
            source_file="test.csv",
            url="https://example.test/test.csv",
            vintage="test",
            extracted_at="2026-05-06",
            extraction_method="test",
        ),
        filters={"income_range": "500k_plus"},
        source_record_id="irs_soi.test.500k_plus.taxable_interest",
        source_cell_keys=tuple(build_source_cell_key(cell) for cell in cells),
        source_row_keys=row_keys,
        constraints=(
            AggregateConstraint(
                variable="us:statutes/26/62#adjusted_gross_income",
                operator=">=",
                value=500_000,
                unit="usd",
            ),
        ),
        layout=SourceRecordLayout(
            groupby_dimension="income_range",
            groupby_value_id="500k_plus",
            table_record_kind="detail",
        ),
    )

    report = build_agent_acceptance_report(
        [fact],
        [row_500k_to_1m, row_1m_plus],
        cells,
        source_rows=validate_source_rows([row_500k_to_1m, row_1m_plus]),
        source_cells=validate_source_cells(cells),
        source_regions=SourceRegionSuiteReport(
            region_count=0,
            covered_cell_count=0,
            errors=(),
        ),
        source_records=SourceRecordSuiteReport(
            spec_count=1,
            resolved_count=1,
            lineaged_count=1,
            errors=(),
        ),
        fact_report=validate_facts([fact]),
        concept_alignments=ConceptAlignmentReport(
            alignment_count=0,
            checked_count=0,
            alignments=(),
            errors=(),
        ),
    )

    assert report.valid
    assert report.counts["row_semantic_error_count"] == 0


def test_agent_acceptance_rejects_row_constraints_without_source_evidence():
    artifact = SourceArtifactMetadata(
        source_name="bea",
        source_table="test",
        source_file="test.csv",
        url="https://example.test/test.csv",
        vintage="test",
        sha256="abc123",
        size_bytes=10,
        extracted_at="2026-05-06",
        extraction_method="test",
        raw_r2_bucket="ledger-raw",
        raw_r2_key="raw/bea/test.csv",
        raw_r2_uri="r2://ledger-raw/raw/bea/test.csv",
    )
    row = SourceRow(
        artifact=artifact,
        sheet_name="NipaDataA",
        row_number=2,
        values={"Period": 2022, "SeriesCode": "W351RC", "Value": 1},
    )
    row_key = build_source_row_key(row)
    cell = SourceCell(
        artifact=artifact,
        sheet_name="NipaDataA",
        row_number=2,
        column_number=3,
        address="C2",
        cell_type="number",
        raw_value=1,
        display_value="1",
        source_row_key=row_key,
    )
    fact = AggregateFact(
        value=1,
        period=PeriodDimension(type="calendar_year", value=2022),
        geography=GeographyDimension(
            level="country",
            id="0100000US",
            vintage="current",
            name="United States",
        ),
        entity=EntityDimension(name="pension_plan"),
        measure=Measure(
            concept="bea_nipa.defined_contribution_employer_contributions",
            unit="usd",
        ),
        aggregation=Aggregation(method="sum"),
        provenance_class="model_output",
        source=SourceProvenance(
            source_name="bea",
            source_table="test",
            source_file="test.csv",
            url="https://example.test/test.csv",
            vintage="test",
            extracted_at="2026-05-06",
            extraction_method="test",
        ),
        filters={"bea_nipa.series_code": "W351RC"},
        source_record_id="bea.test.w351rc",
        source_cell_keys=(build_source_cell_key(cell),),
        source_row_keys=(row_key,),
        constraints=(
            AggregateConstraint(
                variable="bea_nipa.table_id",
                operator="==",
                value="T61100D",
            ),
        ),
        layout=SourceRecordLayout(
            groupby_dimension="bea_nipa.series_code",
            groupby_value_id="w351rc",
            table_record_kind="detail",
        ),
    )

    report = build_agent_acceptance_report(
        [fact],
        [row],
        [cell],
        source_rows=validate_source_rows([row]),
        source_cells=validate_source_cells([cell]),
        source_regions=SourceRegionSuiteReport(
            region_count=0,
            covered_cell_count=0,
            errors=(),
        ),
        source_records=SourceRecordSuiteReport(
            spec_count=1,
            resolved_count=1,
            lineaged_count=1,
            errors=(),
        ),
        fact_report=validate_facts([fact]),
        concept_alignments=ConceptAlignmentReport(
            alignment_count=0,
            checked_count=0,
            alignments=(),
            errors=(),
        ),
    )

    assert not report.valid
    assert "row_constraint_not_evidenced" in {error.code for error in report.errors}


def test_agent_acceptance_accepts_source_row_bound_constraints():
    artifact = SourceArtifactMetadata(
        source_name="hmrc_spi",
        source_table="test",
        source_file="test.csv",
        url="https://example.test/test.csv",
        vintage="test",
        sha256="abc123",
        size_bytes=10,
        extracted_at="2026-05-22",
        extraction_method="test",
        raw_r2_bucket="ledger-raw",
        raw_r2_key="raw/hmrc_spi/test.csv",
        raw_r2_uri="r2://ledger-raw/raw/hmrc_spi/test.csv",
    )
    row = SourceRow(
        artifact=artifact,
        sheet_name="incomes_projection",
        row_number=58,
        values={
            "total_income_lower_bound": 12570,
            "total_income_upper_bound": 15000.0,
            "employment_income_amount": 1,
        },
    )
    row_key = build_source_row_key(row)
    cell = SourceCell(
        artifact=artifact,
        sheet_name="incomes_projection",
        row_number=2,
        column_number=4,
        address="D2",
        cell_type="number",
        raw_value=1,
        display_value="1",
        source_row_key=row_key,
    )
    fact = AggregateFact(
        value=1,
        period=PeriodDimension(type="calendar_year", value=2026),
        geography=GeographyDimension(
            level="country",
            id="GBR",
            vintage="current",
            name="United Kingdom",
        ),
        entity=EntityDimension(name="person"),
        measure=Measure(
            concept="uk_personal_income.employment_income",
            unit="gbp",
        ),
        aggregation=Aggregation(method="sum"),
        provenance_class="model_output",
        source=SourceProvenance(
            source_name="hmrc_spi",
            source_table="test",
            source_file="test.csv",
            url="https://example.test/test.csv",
            vintage="test",
            extracted_at="2026-05-22",
            extraction_method="test",
        ),
        source_record_id="hmrc_spi.test.12k_to_15k.employment_income_amount",
        source_cell_keys=(build_source_cell_key(cell),),
        source_row_keys=(row_key,),
        constraints=(
            AggregateConstraint(
                variable="total_income",
                operator=">=",
                value=12570,
            ),
            AggregateConstraint(
                variable="total_income",
                operator="<",
                value=15000,
            ),
        ),
        layout=SourceRecordLayout(
            groupby_dimension="total_income",
            groupby_value_id="12k_to_15k",
            table_record_kind="detail",
        ),
    )

    report = build_agent_acceptance_report(
        [fact],
        [row],
        [cell],
        source_rows=validate_source_rows([row]),
        source_cells=validate_source_cells([cell]),
        source_regions=SourceRegionSuiteReport(
            region_count=0,
            covered_cell_count=0,
            errors=(),
        ),
        source_records=SourceRecordSuiteReport(
            spec_count=1,
            resolved_count=1,
            lineaged_count=1,
            errors=(),
        ),
        fact_report=validate_facts([fact]),
        concept_alignments=ConceptAlignmentReport(
            alignment_count=0,
            checked_count=0,
            alignments=(),
            errors=(),
        ),
    )

    assert report.valid
    assert report.checks["row_lineage_semantics_evidenced"]

    chronicle_row_key = HASH_DOMAINS["source_row"].key_for_epoch(
        row_key,
        Epoch.CHRONICLE,
    )
    chronicle_cell_key = HASH_DOMAINS["source_cell"].key_for_epoch(
        build_source_cell_key(cell),
        Epoch.CHRONICLE,
    )
    chronicle_cell = replace(cell, source_row_key=chronicle_row_key)
    chronicle_fact = replace(
        fact,
        source_cell_keys=(chronicle_cell_key,),
        source_row_keys=(chronicle_row_key,),
    )
    chronicle_report = build_agent_acceptance_report(
        [chronicle_fact],
        [row],
        [chronicle_cell],
        source_rows=validate_source_rows([row]),
        source_cells=validate_source_cells([chronicle_cell]),
        source_regions=SourceRegionSuiteReport(
            region_count=0,
            covered_cell_count=0,
            errors=(),
        ),
        source_records=SourceRecordSuiteReport(
            spec_count=1,
            resolved_count=1,
            lineaged_count=1,
            errors=(),
        ),
        fact_report=validate_facts([chronicle_fact]),
        concept_alignments=ConceptAlignmentReport(
            alignment_count=0,
            checked_count=0,
            alignments=(),
            errors=(),
        ),
    )

    assert chronicle_report.valid
    assert chronicle_report.checks["row_lineage_semantics_evidenced"]

    invalid_fact = replace(fact, source_row_keys=(123,))
    invalid_report = build_agent_acceptance_report(
        [invalid_fact],
        [row],
        [cell],
        source_rows=validate_source_rows([row]),
        source_cells=validate_source_cells([cell]),
        source_regions=SourceRegionSuiteReport(0, 0, ()),
        source_records=SourceRecordSuiteReport(1, 1, 1, ()),
        fact_report=validate_facts([invalid_fact]),
        concept_alignments=ConceptAlignmentReport(0, 0, (), ()),
    )

    assert not invalid_report.valid
    assert "fact_source_row_lineage_unresolved" in {
        error.code for error in invalid_report.errors
    }


def test_agent_acceptance_accepts_age_constraints_from_source_cell_header():
    artifact = SourceArtifactMetadata(
        source_name="census_population_projections",
        source_table="test",
        source_file="test.csv",
        url="https://example.test/test.csv",
        vintage="test",
        sha256="abc123",
        size_bytes=10,
        extracted_at="2026-05-11",
        extraction_method="test",
        raw_r2_bucket="ledger-raw",
        raw_r2_key="raw/census/test.csv",
        raw_r2_uri="r2://ledger-raw/raw/census/test.csv",
    )
    row = SourceRow(
        artifact=artifact,
        sheet_name="test",
        row_number=2,
        values={"YEAR": 2025, "POP_0": 1},
    )
    row_key = build_source_row_key(row)
    header_cell = SourceCell(
        artifact=artifact,
        sheet_name="test",
        row_number=1,
        column_number=6,
        address="F1",
        cell_type="text",
        raw_value="POP_0",
        display_value="POP_0",
    )
    value_cell = SourceCell(
        artifact=artifact,
        sheet_name="test",
        row_number=2,
        column_number=6,
        address="F2",
        cell_type="number",
        raw_value=1,
        display_value="1",
        source_row_key=row_key,
    )
    fact = AggregateFact(
        value=1,
        period=PeriodDimension(type="calendar_year", value=2025),
        geography=GeographyDimension(
            level="country",
            id="0100000US",
            vintage="current",
            name="United States",
        ),
        entity=EntityDimension(name="person"),
        measure=Measure(
            concept="census.population_projection",
            unit="count",
        ),
        aggregation=Aggregation(method="sum"),
        provenance_class="model_output",
        source=SourceProvenance(
            source_name="census_population_projections",
            source_table="test",
            source_file="test.csv",
            url="https://example.test/test.csv",
            vintage="test",
            extracted_at="2026-05-11",
            extraction_method="test",
        ),
        source_record_id="census.test.age_0.population",
        source_cell_keys=(
            build_source_cell_key(value_cell),
            build_source_cell_key(header_cell),
        ),
        source_row_keys=(row_key,),
        constraints=(
            AggregateConstraint(
                variable="age",
                operator=">=",
                value=0,
            ),
            AggregateConstraint(
                variable="age",
                operator="<",
                value=1,
            ),
        ),
        layout=SourceRecordLayout(
            groupby_dimension="age",
            groupby_value_id="age_0",
            table_record_kind="detail",
        ),
    )
    cells = [header_cell, value_cell]

    report = build_agent_acceptance_report(
        [fact],
        [row],
        cells,
        source_rows=validate_source_rows([row]),
        source_cells=validate_source_cells(cells),
        source_regions=SourceRegionSuiteReport(
            region_count=0,
            covered_cell_count=0,
            errors=(),
        ),
        source_records=SourceRecordSuiteReport(
            spec_count=1,
            resolved_count=1,
            lineaged_count=1,
            errors=(),
        ),
        fact_report=validate_facts([fact]),
        concept_alignments=ConceptAlignmentReport(
            alignment_count=0,
            checked_count=0,
            alignments=(),
            errors=(),
        ),
    )

    assert report.valid
    assert report.checks["row_lineage_semantics_evidenced"]
    assert "row_constraint_not_evidenced" not in {error.code for error in report.errors}


def test_build_suite_cli_emits_json_summary(tmp_path, capsys):
    output_dir = tmp_path / "suite"

    exit_code = harness_main(
        [
            "build-suite",
            "soi-table-1-1",
            "--year",
            "2023",
            "--out",
            str(output_dir),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["valid"]
    assert payload["outputs"]["source_regions"] == str(
        output_dir / "source_regions.jsonl"
    )
    assert payload["outputs"]["database"] == str(output_dir / "ledger.db")
    assert payload["outputs"]["consumer_facts"] == str(
        output_dir / "consumer_facts.jsonl"
    )
    assert payload["outputs"]["datapackage"] == str(output_dir / "datapackage.json")
    assert payload["reports"]["consumer_facts"]["fact_count"] == 80
    assert payload["reports"]["source_regions"]["covered_cell_count"] == 340
    assert payload["reports"]["source_records"]["lineage_coverage"] == 1.0
    assert payload["reports"]["agent_acceptance"]["valid"]
    assert payload["reports"]["agent_acceptance"]["warnings"][0]["code"] == (
        "concept_alignment_validation_skipped"
    )


def test_build_source_suite_accepts_required_axiom_validation(tmp_path):
    output_dir = tmp_path / "suite"
    axiom_cli = _write_fake_axiom_cli(
        tmp_path,
        valid_concepts={"us:statutes/26/62#adjusted_gross_income"},
    )

    report = build_source_suite(
        "soi-table-1-1",
        output_dir,
        year=2023,
        axiom_command=[str(axiom_cli)],
        axiom_roots=[tmp_path / "rules-us"],
        require_axiom_validation=True,
    )
    summary = json.loads((output_dir / "reports" / "build_summary.json").read_text())

    assert report.valid
    assert summary["reports"]["concept_alignments"]["checked_count"] == 20
    assert summary["reports"]["agent_acceptance"]["warnings"] == []
    assert summary["reports"]["agent_acceptance"]["checks"][
        "required_concept_alignments_validated"
    ]


def test_build_source_suite_can_require_axiom_validation(tmp_path):
    output_dir = tmp_path / "suite"

    report = build_source_suite(
        "soi-table-1-4",
        output_dir,
        year=2023,
        require_axiom_validation=True,
    )
    summary = json.loads((output_dir / "reports" / "build_summary.json").read_text())

    assert not report.valid
    assert not summary["valid"]
    assert summary["reports"]["agent_acceptance"]["errors"][0]["code"] == (
        "concept_alignment_validation_skipped"
    )
    assert not summary["reports"]["agent_acceptance"]["checks"][
        "required_concept_alignments_validated"
    ]


def test_build_suite_refuses_nonempty_output_dir_without_replace(tmp_path):
    output_dir = tmp_path / "suite"
    output_dir.mkdir()
    (output_dir / "existing.txt").write_text("keep", encoding="utf-8")

    try:
        build_source_suite("soi-table-1-1", output_dir, year=2023)
    except FileExistsError as exc:
        assert "not empty" in str(exc)
    else:
        raise AssertionError("Expected nonempty output directory to be rejected")

    assert (output_dir / "existing.txt").read_text(encoding="utf-8") == "keep"


def test_source_record_suite_reports_selector_errors():
    specs = build_source_record_specs("soi-table-1-1", year=2023)

    report = validate_source_record_specs(specs[:1], cells=[])

    assert not report.valid
    assert report.spec_count == 1
    assert report.resolved_count == 0
    assert report.errors[0].code == "source_record_resolution_failed"


def test_source_region_suite_reports_covered_cells():
    cells = build_source_cells("soi-table-1-1", year=2023)
    regions = build_source_regions("soi-table-1-1", year=2023)

    report = validate_source_regions(regions, cells)

    assert report.valid
    assert report.region_count == 1
    assert report.covered_cell_count == 340


def _write_fake_axiom_cli(tmp_path, *, valid_concepts: set[str]):
    axiom_cli = tmp_path / "axiom"
    axiom_cli.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json
            import sys

            concept_id = sys.argv[sys.argv.index("validate") + 1]
            valid_concepts = {sorted(valid_concepts)!r}
            if concept_id in valid_concepts:
                print(json.dumps({{
                    "concept_id": concept_id,
                    "concept": {{"concept_id": concept_id}},
                    "errors": [],
                    "valid": True,
                }}))
                raise SystemExit(0)
            print(json.dumps({{
                "concept_id": concept_id,
                "errors": [
                    {{
                        "code": "concept_not_found",
                        "message": f"Concept {{concept_id}} is not available.",
                    }}
                ],
                "valid": False,
            }}))
            raise SystemExit(1)
            """
        )
    )
    axiom_cli.chmod(0o755)
    return axiom_cli
