"""Tests for consumer artifacts and period-contract resolution.

The load-bearing behavior: consuming a fact at a period other than its
reference period is a hard error unless the consumer declares a named,
versioned alignment. This is the schema-level guard against the
PolicyEngine/populace#212 failure, where SOI tax-year dollar levels were
calibrated un-aged at a later build year and nothing complained.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

import policyengine_ledger.target_profiles as target_profiles_pkg
from ledger.consumer_contract import consumer_fact_rows
from ledger.core import (
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
from policyengine_ledger.consumer import (
    PeriodAlignmentDeclaration,
    PeriodContractError,
    build_consumer_artifact,
    load_consumer_artifact,
    resolve_profile_targets,
)
from policyengine_ledger.schema import CONSUMER_FACT_SCHEMA_SHA256
from policyengine_ledger.target_profiles import target_profile_from_mapping

SHA = "ab" * 32


def _fact(
    *,
    value,
    period_value,
    period_type="tax_year",
    source_name="irs_soi",
    measure_id="agi",
    concept="irs_soi.adjusted_gross_income",
    assertion="observation",
):
    return AggregateFact(
        value=value,
        period=PeriodDimension(type=period_type, value=period_value),
        geography=GeographyDimension(
            level="country",
            id="0100000US",
            vintage="2020_census",
        ),
        entity=EntityDimension(name="tax_unit", role="filing_unit"),
        measure=Measure(concept=concept, unit="usd"),
        aggregation=Aggregation(method="sum"),
        source=SourceProvenance(
            source_name=source_name,
            source_table="Table T",
            source_file="t.xls",
            url="https://example.gov/t.xls",
            vintage=f"{period_type}_{period_value}",
            extracted_at="2026-05-01",
            extraction_method="test",
            source_sha256=SHA,
            source_size_bytes=10,
            raw_r2_bucket="ledger-raw",
            raw_r2_key=f"raw/{source_name}/t/{period_value}/{SHA}/t.xls",
            raw_r2_uri=f"r2://ledger-raw/raw/{source_name}/t/{period_value}/{SHA}/t.xls",
        ),
        domain="all_returns",
        source_record_id=f"{source_name}.{period_value}.t.all.{measure_id}",
        source_cell_keys=(f"ledger.source_cell.v1:{period_value}{measure_id}",),
        layout=SourceRecordLayout(
            record_set_id=f"{source_name}.{period_value}.t",
            record_set_spec_id=f"{source_name}.t.v1",
            measure_id=measure_id,
            groupby_dimension="us.agi",
        ),
        assertion=assertion,
    )


def _rows():
    return consumer_fact_rows(
        [
            _fact(value=100, period_value=2021),
            _fact(value=110, period_value=2022),
            _fact(
                value=250,
                period_value=2027,
                period_type="calendar_year",
                source_name="cbo",
                measure_id="receipts",
                concept="cbo.individual_income_tax_receipts",
                assertion="source_projection",
            ),
        ]
    )


def _profile(selector=None, target_id="soi.agi.total"):
    return target_profile_from_mapping(
        {
            "schema_version": "policyengine_ledger.target_profile.v1",
            "profile_id": "test_profile",
            "country": "us",
            "label": "Test profile",
            "defaults": {
                "base_period_policy": "latest_not_after_build_base_period",
                "operation": "sum",
            },
            "targets": [
                {
                    "target_id": target_id,
                    "family": "irs_soi",
                    "geography_levels": ["country"],
                    "ledger_selector": selector
                    or {"source_name": "irs_soi", "source_measure_id": "agi"},
                    "measurement": {
                        "entity": "tax_unit",
                        "concept": "us.agi",
                    },
                    "bindings": {
                        "populace": {"metric_name": "irs_soi/agi/total"},
                    },
                }
            ],
        }
    )


def test_resolving_at_the_fact_period_is_fact_basis():
    report = resolve_profile_targets(
        _profile(),
        _rows(),
        {"type": "tax_year", "value": 2022},
    )
    assert report.valid
    assert len(report.resolved) == 1
    row = report.resolved[0]
    assert row.basis == "fact"
    assert row.value == 110
    assert row.assertion == "observation"
    assert row.fact_period == {"type": "tax_year", "value": 2022}
    assert row.requested_period == {"type": "tax_year", "value": 2022}
    assert row.alignment is None


def test_un_aged_consumption_hard_fails():
    with pytest.raises(PeriodContractError) as excinfo:
        resolve_profile_targets(
            _profile(),
            _rows(),
            {"type": "tax_year", "value": 2025},
        )
    message = str(excinfo.value)
    assert "PeriodAlignmentDeclaration" in message
    (violation,) = excinfo.value.violations
    assert violation.fact_period == {"type": "tax_year", "value": 2022}
    assert violation.requested_period == {"type": "tax_year", "value": 2025}


def test_declared_alignment_resolves_and_is_recorded():
    declaration = PeriodAlignmentDeclaration(
        model_id="cbo_growth_factor_aging",
        model_version="2026.1",
        parameters={"factor_series": "cbo.baseline_2026_01.agi_growth"},
    )
    report = resolve_profile_targets(
        _profile(),
        _rows(),
        {"type": "tax_year", "value": 2025},
        alignments={"soi.agi.total": declaration},
    )
    assert report.valid
    (row,) = report.resolved
    assert row.basis == "declared_alignment"
    # Ledger returns the published level; the consumer applies the model.
    assert row.value == 110
    assert row.fact_period == {"type": "tax_year", "value": 2022}
    assert row.requested_period == {"type": "tax_year", "value": 2025}
    assert row.alignment["model_id"] == "cbo_growth_factor_aging"
    assert row.alignment["model_version"] == "2026.1"


def test_latest_not_after_selects_the_newest_covered_period():
    report = resolve_profile_targets(
        _profile(),
        _rows(),
        {"type": "tax_year", "value": 2021},
    )
    (row,) = report.resolved
    assert row.basis == "fact"
    assert row.value == 100


def test_wildcard_alignment_covers_all_targets():
    declaration = PeriodAlignmentDeclaration(
        model_id="cbo_growth_factor_aging",
        model_version="2026.1",
    )
    report = resolve_profile_targets(
        _profile(),
        _rows(),
        {"type": "tax_year", "value": 2025},
        alignments=declaration,
    )
    assert report.valid
    assert report.resolved[0].basis == "declared_alignment"


def test_source_projections_resolve_at_their_own_period_as_facts():
    profile = _profile(
        selector={
            "source_name": "cbo",
            "source_measure_id": "receipts",
            "assertion": "source_projection",
        },
        target_id="cbo.receipts.2027",
    )
    report = resolve_profile_targets(
        profile,
        _rows(),
        {"type": "calendar_year", "value": 2027},
    )
    (row,) = report.resolved
    assert row.basis == "fact"
    assert row.assertion == "source_projection"
    assert row.value == 250


def test_alignment_declarations_reject_values_and_runtime_hooks():
    with pytest.raises(ValueError, match="never"):
        PeriodAlignmentDeclaration(
            model_id="m",
            model_version="1",
            parameters={"target_value": 130e9},
        )
    with pytest.raises(ValueError, match="model_version"):
        PeriodAlignmentDeclaration(model_id="m", model_version=" ")
    with pytest.raises(ValueError, match="non-scalar"):
        PeriodAlignmentDeclaration(
            model_id="m",
            model_version="1",
            parameters={"factors": [1.02, 1.03]},
        )


def test_unknown_selector_keys_fail_loudly():
    profile = _profile(selector={"source_name": "irs_soi", "spreadsheet": "t.xls"})
    with pytest.raises(ValueError, match="unknown keys"):
        resolve_profile_targets(
            profile,
            _rows(),
            {"type": "tax_year", "value": 2022},
        )


def test_no_matching_facts_fails_strict_and_reports_lenient():
    profile = _profile(selector={"source_name": "nonexistent"})
    with pytest.raises(ValueError, match="matched no consumer fact rows"):
        resolve_profile_targets(
            profile,
            _rows(),
            {"type": "tax_year", "value": 2022},
        )
    report = resolve_profile_targets(
        profile,
        _rows(),
        {"type": "tax_year", "value": 2022},
        strict=False,
    )
    assert not report.valid
    assert report.issues[0].code == "no_matching_facts"


def _write_artifact_inputs(tmp_path):
    facts_path = tmp_path / "consumer_facts.jsonl"
    with facts_path.open("w") as file:
        for row in _rows():
            file.write(json.dumps(row, sort_keys=True) + "\n")
    profile_path = tmp_path / "test_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": "policyengine_ledger.target_profile.v1",
                "profile_id": "test_profile",
                "country": "us",
                "label": "Test profile",
                "defaults": {
                    "base_period_policy": "latest_not_after_build_base_period",
                    "operation": "sum",
                },
                "targets": [
                    {
                        "target_id": "soi.agi.total",
                        "family": "irs_soi",
                        "geography_levels": ["country"],
                        "ledger_selector": {
                            "source_name": "irs_soi",
                            "source_measure_id": "agi",
                        },
                        "measurement": {"entity": "tax_unit", "concept": "us.agi"},
                        "bindings": {
                            "populace": {"metric_name": "irs_soi/agi/total"},
                        },
                    }
                ],
            }
        )
    )
    return facts_path, profile_path


def test_artifact_build_load_resolve_round_trip(tmp_path):
    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    out_dir = tmp_path / "artifact"
    report = build_consumer_artifact(
        out_dir,
        facts_path=facts_path,
        profile_paths=[profile_path],
    )
    assert report.fact_row_count == 3
    assert report.profile_ids == ("test_profile",)

    artifact = load_consumer_artifact(out_dir)
    assert artifact.manifest["fact_row_count"] == 3
    resolution = artifact.resolve(
        "test_profile",
        {"type": "tax_year", "value": 2022},
    )
    assert resolution.resolved[0].value == 110

    with pytest.raises(PeriodContractError):
        artifact.resolve("test_profile", {"type": "tax_year", "value": 2025})


def test_artifact_coverage_reports_periods_and_assertions(tmp_path):
    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    out_dir = tmp_path / "artifact"
    report = build_consumer_artifact(
        out_dir,
        facts_path=facts_path,
        profile_paths=[profile_path],
    )
    target_coverage = report.coverage["test_profile"]["soi.agi.total"]["country"]
    assert target_coverage["matched_row_count"] == 2
    assert target_coverage["fact_periods"] == ["tax_year:2021", "tax_year:2022"]
    assert target_coverage["assertions"] == ["observation"]
    coverage_on_disk = json.loads((out_dir / "coverage.json").read_text())
    assert coverage_on_disk == report.coverage


def test_artifact_load_rejects_tampered_facts(tmp_path):
    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(
        out_dir,
        facts_path=facts_path,
        profile_paths=[profile_path],
    )
    facts_file = out_dir / "consumer_facts.jsonl"
    rows = facts_file.read_text().splitlines()
    tampered = json.loads(rows[0])
    tampered["value"] = 999
    rows[0] = json.dumps(tampered, sort_keys=True)
    facts_file.write_text("\n".join(rows) + "\n")
    with pytest.raises(ValueError, match="manifest hash"):
        load_consumer_artifact(out_dir)


def test_artifact_requires_profiles(tmp_path):
    facts_path, _ = _write_artifact_inputs(tmp_path)
    with pytest.raises(ValueError, match="at least one target profile"):
        build_consumer_artifact(
            tmp_path / "artifact",
            facts_path=facts_path,
        )


def test_artifact_is_reproducible(tmp_path):
    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    for out_dir in (first, second):
        build_consumer_artifact(
            out_dir,
            facts_path=facts_path,
            profile_paths=[profile_path],
        )
    for name in ("manifest.json", "coverage.json", "consumer_facts.jsonl"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def _ons_firm_crosstab_fact(*, record_set_id, dimensions, value, cell):
    constraints = tuple(
        AggregateConstraint(variable=key, operator="==", value=band)
        for key, band in sorted(dimensions.items())
    )
    return AggregateFact(
        value=value,
        period=PeriodDimension(type="calendar_year", value=2025),
        geography=GeographyDimension(level="country", id="K02000001", vintage="2025"),
        entity=EntityDimension(name="firm"),
        measure=Measure(concept="uk.firm.count", unit="count"),
        aggregation=Aggregation(method="sum"),
        source=SourceProvenance(
            source_name="ons",
            source_table="UK Business Counts 2025",
            source_file="ukbusinesscounts2025.xlsx",
            url="https://www.ons.gov.uk/ukbusinesscounts2025.xlsx",
            vintage="cy2025",
            extracted_at="2026-06-01",
            extraction_method="test",
            source_sha256=SHA,
            source_size_bytes=100,
            raw_r2_bucket="ledger-raw",
            raw_r2_key=f"raw/ons/ukbc/{cell}/{SHA}/x.xlsx",
            raw_r2_uri=f"r2://ledger-raw/raw/ons/ukbc/{cell}/{SHA}/x.xlsx",
        ),
        domain="uk_enterprises",
        source_record_id=f"ons.uk_business.cy2025.{cell}",
        source_cell_keys=(f"ledger.source_cell.v1:{cell}",),
        filters=dict(dimensions),
        constraints=constraints,
        layout=SourceRecordLayout(
            record_set_id=record_set_id,
            record_set_spec_id="ons.uk_business.v1",
            measure_id="enterprise_count",
        ),
    )


def _uk_firms_crosstab_rows():
    by_sic_turnover = "ons.uk_business.cy2025.enterprise_count.by_sic_turnover_band"
    by_sic_employment = "ons.uk_business.cy2025.enterprise_count.by_sic_employment_band"
    return consumer_fact_rows(
        [
            _ons_firm_crosstab_fact(
                record_set_id=by_sic_turnover,
                dimensions={
                    "uk.firm.sic_code": "A",
                    "uk.firm.turnover_band": "0_99k",
                },
                value=1200,
                cell="sic_turnover.A.0_99k",
            ),
            _ons_firm_crosstab_fact(
                record_set_id=by_sic_turnover,
                dimensions={
                    "uk.firm.sic_code": "C",
                    "uk.firm.turnover_band": "100_249k",
                },
                value=800,
                cell="sic_turnover.C.100_249k",
            ),
            _ons_firm_crosstab_fact(
                record_set_id=by_sic_employment,
                dimensions={
                    "uk.firm.sic_code": "A",
                    "uk.firm.employment_band": "0_9",
                },
                value=1500,
                cell="sic_employment.A.0_9",
            ),
            _ons_firm_crosstab_fact(
                record_set_id=by_sic_employment,
                dimensions={
                    "uk.firm.sic_code": "C",
                    "uk.firm.employment_band": "10_49",
                },
                value=430,
                cell="sic_employment.C.10_49",
            ),
        ]
    )


def _uk_firms_crosstab_profile_payload():
    payload = json.loads(
        (Path(target_profiles_pkg.__file__).parent / "uk_firms.json").read_text()
    )
    crosstab_ids = {
        "ons.uk_business.enterprise_count.sic_turnover_bands",
        "ons.uk_business.enterprise_count.sic_employment_bands",
    }
    payload["targets"] = [
        target for target in payload["targets"] if target["target_id"] in crosstab_ids
    ]
    return payload


def test_uk_firms_cross_tab_targets_resolve_through_dimensions_selector(tmp_path):
    facts_path = tmp_path / "consumer_facts.jsonl"
    with facts_path.open("w") as file:
        for row in _uk_firms_crosstab_rows():
            file.write(json.dumps(row, sort_keys=True) + "\n")
    profile_path = tmp_path / "uk_firms.json"
    profile_path.write_text(json.dumps(_uk_firms_crosstab_profile_payload()))

    out_dir = tmp_path / "artifact"
    build_consumer_artifact(
        out_dir,
        facts_path=facts_path,
        profile_paths=[profile_path],
    )
    artifact = load_consumer_artifact(out_dir)

    report = artifact.resolve("uk_firms", {"type": "calendar_year", "value": 2025})

    assert report.valid
    assert artifact.profile_hash_semantics == {"uk_firms": "exact"}
    by_target: dict[str, list] = {}
    for row in report.resolved:
        assert row.basis == "fact"
        by_target.setdefault(row.target_id, []).append(row)

    sic_turnover = by_target["ons.uk_business.enterprise_count.sic_turnover_bands"]
    sic_employment = by_target["ons.uk_business.enterprise_count.sic_employment_bands"]
    assert sorted(row.value for row in sic_turnover) == [800, 1200]
    assert sorted(row.value for row in sic_employment) == [430, 1500]
    assert all(
        sorted(row.dimensions) == ["uk.firm.sic_code", "uk.firm.turnover_band"]
        for row in sic_turnover
    )
    assert all(
        sorted(row.dimensions) == ["uk.firm.employment_band", "uk.firm.sic_code"]
        for row in sic_employment
    )


def _rewrite_facts_file(out_dir, rows):
    facts_file = out_dir / "consumer_facts.jsonl"
    facts_file.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    )
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["facts_sha256"] = hashlib.sha256(facts_file.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")


def test_artifact_load_rejects_row_missing_required_field(tmp_path):
    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(
        out_dir, facts_path=facts_path, profile_paths=[profile_path]
    )

    rows = _rows()
    del rows[0]["observed_measure"]["unit"]
    _rewrite_facts_file(out_dir, rows)

    with pytest.raises(ValueError, match="unit"):
        load_consumer_artifact(out_dir)


def test_artifact_load_rejects_unknown_extra_field(tmp_path):
    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(
        out_dir, facts_path=facts_path, profile_paths=[profile_path]
    )

    rows = _rows()
    rows[0]["unexpected_field"] = "surprise"
    _rewrite_facts_file(out_dir, rows)

    with pytest.raises(ValueError, match="unexpected_field"):
        load_consumer_artifact(out_dir)


def test_artifact_load_rejects_unknown_schema_sha256(tmp_path):
    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(
        out_dir, facts_path=facts_path, profile_paths=[profile_path]
    )

    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["consumer_fact_schema_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    with pytest.raises(ValueError, match="consumer_fact_schema_sha256"):
        load_consumer_artifact(out_dir)


def test_artifact_load_rejects_tampered_profile(tmp_path):
    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(
        out_dir, facts_path=facts_path, profile_paths=[profile_path]
    )

    profile_file = out_dir / "profiles" / "test_profile.json"
    tampered = profile_file.read_bytes().replace(b"Test profile", b"Xest profile", 1)
    assert tampered != profile_file.read_bytes()
    profile_file.write_bytes(tampered)

    with pytest.raises(ValueError, match="does not match the manifest"):
        load_consumer_artifact(out_dir)


def test_artifact_load_accepts_legacy_profile_hash_only_via_explicit_path(tmp_path):
    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(
        out_dir, facts_path=facts_path, profile_paths=[profile_path]
    )

    profile_file = out_dir / "profiles" / "test_profile.json"
    profile_bytes = profile_file.read_bytes()
    assert profile_bytes.endswith(b"\n")
    legacy_hash = hashlib.sha256(profile_bytes[:-1]).hexdigest()

    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    # A pre-fix manifest carries no schema sha and hashed the profile without
    # its trailing newline.
    del manifest["consumer_fact_schema_sha256"]
    manifest["profiles"]["test_profile"]["sha256"] = legacy_hash
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    artifact = load_consumer_artifact(out_dir)
    assert artifact.profile_hash_semantics == {"test_profile": "legacy_profile_hash"}

    # The same legacy hash is rejected once the manifest is post-fix.
    manifest["consumer_fact_schema_sha256"] = CONSUMER_FACT_SCHEMA_SHA256
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    with pytest.raises(ValueError, match="does not match the manifest"):
        load_consumer_artifact(out_dir)


def test_artifact_build_rejects_duplicate_aggregate_fact_key(tmp_path):
    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    with facts_path.open("a") as file:
        file.write(json.dumps(_rows()[0], sort_keys=True) + "\n")

    with pytest.raises(ValueError, match="aggregate_fact_key"):
        build_consumer_artifact(
            tmp_path / "artifact",
            facts_path=facts_path,
            profile_paths=[profile_path],
        )


# ---------------------------------------------------------------------------
# 2026-07-10 independent verification counterexamples (SOL review).
# ---------------------------------------------------------------------------


def test_sol_schema_invalid_build_and_duplicate_load_fail_closed(tmp_path):
    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    invalid_rows = _rows()
    del invalid_rows[0]["observed_measure"]["unit"]
    facts_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in invalid_rows)
    )

    with pytest.raises(ValueError, match="unit"):
        build_consumer_artifact(
            tmp_path / "invalid-artifact",
            facts_path=facts_path,
            profile_paths=[profile_path],
        )

    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    out_dir = tmp_path / "valid-artifact"
    build_consumer_artifact(
        out_dir, facts_path=facts_path, profile_paths=[profile_path]
    )
    duplicate_rows = _rows()
    duplicate_rows.append(json.loads(json.dumps(duplicate_rows[0])))
    _rewrite_facts_file(out_dir, duplicate_rows)

    with pytest.raises(ValueError, match="repeats aggregate_fact_key"):
        load_consumer_artifact(out_dir)


def test_sol_profile_hash_legacy_boundary_is_manifest_versioned(tmp_path):
    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(
        out_dir, facts_path=facts_path, profile_paths=[profile_path]
    )

    profile_file = out_dir / "profiles" / "test_profile.json"
    profile_bytes = profile_file.read_bytes()
    assert profile_bytes.endswith(b"\n")
    legacy_hash = hashlib.sha256(profile_bytes[:-1]).hexdigest()
    exact_hash = hashlib.sha256(profile_bytes).hexdigest()
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())

    manifest["profiles"]["test_profile"]["sha256"] = legacy_hash
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    with pytest.raises(ValueError, match="does not match the manifest"):
        load_consumer_artifact(out_dir)

    del manifest["consumer_fact_schema_sha256"]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    artifact = load_consumer_artifact(out_dir)
    assert artifact.profile_hash_semantics == {"test_profile": "legacy_profile_hash"}

    manifest["consumer_fact_schema_sha256"] = CONSUMER_FACT_SCHEMA_SHA256
    manifest["profiles"]["test_profile"]["sha256"] = exact_hash
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    artifact = load_consumer_artifact(out_dir)
    assert artifact.profile_hash_semantics == {"test_profile": "exact"}


def test_sol_dimensions_selector_is_order_insensitive_but_exact_set():
    payload = _uk_firms_crosstab_profile_payload()
    payload["targets"] = [payload["targets"][0]]
    selector = payload["targets"][0]["ledger_selector"]
    selector["dimensions"] = list(reversed(selector["dimensions"]))
    profile = target_profile_from_mapping(payload)

    report = resolve_profile_targets(
        profile,
        _uk_firms_crosstab_rows(),
        {"type": "calendar_year", "value": 2025},
    )
    assert sorted(row.value for row in report.resolved) == [800, 1200]

    for wrong_dimensions in (
        ["uk.firm.sic_code"],
        [
            "uk.firm.sic_code",
            "uk.firm.turnover_band",
            "uk.firm.employment_band",
        ],
    ):
        wrong_payload = json.loads(json.dumps(payload))
        wrong_payload["targets"][0]["ledger_selector"]["dimensions"] = wrong_dimensions
        with pytest.raises(ValueError, match="matched no consumer fact rows"):
            resolve_profile_targets(
                target_profile_from_mapping(wrong_payload),
                _uk_firms_crosstab_rows(),
                {"type": "calendar_year", "value": 2025},
            )


def test_sol_counterexample_stale_identity_keys_and_false_counts_are_accepted(tmp_path):
    """Characterize integrity metadata the loader currently does not derive."""

    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(
        out_dir, facts_path=facts_path, profile_paths=[profile_path]
    )

    rows = _rows()
    stale = rows[0]
    stale["aggregate_fact_key"] = "ledger.aggregate_fact.v2:" + "0" * 24
    stale["semantic_fact_key"] = "ledger.semantic_fact.v2:" + "0" * 24
    stale["legacy_fact_key"] = "ledger.fact.v1:" + "0" * 24
    stale["source_release_key"] = "ledger.source_release.v2:" + "0" * 24
    stale["source_series_key"] = "ledger.source_series.v2:" + "0" * 24
    stale["observed_measure_key"] = "ledger.observed_measure.v2:" + "0" * 24
    stale["dimension_set_key"] = "ledger.dimension_set.v2:" + "0" * 24
    stale["universe_constraint_set_key"] = (
        "ledger.universe_constraint_set.v2:" + "0" * 24
    )
    _rewrite_facts_file(out_dir, rows)

    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["fact_row_count"] = 999
    manifest["consumer_fact_schema_versions"] = ["ledger.consumer_fact.v999"]
    manifest["profiles"]["test_profile"]["target_count"] = 999
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    artifact = load_consumer_artifact(out_dir)
    assert artifact.rows[0]["aggregate_fact_key"].endswith("0" * 24)
    assert len(artifact.rows) == 3
    assert artifact.manifest["fact_row_count"] == 999
    assert len(artifact.profiles["test_profile"].targets) == 1
    assert artifact.manifest["profiles"]["test_profile"]["target_count"] == 999


def test_sol_counterexample_coordinated_profile_and_manifest_rewrite_is_accepted(
    tmp_path,
):
    """A self-hash detects file-only tamper; no external pin authenticates both."""

    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    out_dir = tmp_path / "artifact"
    build_consumer_artifact(
        out_dir, facts_path=facts_path, profile_paths=[profile_path]
    )

    profile_file = out_dir / "profiles" / "test_profile.json"
    payload = json.loads(profile_file.read_text())
    payload["label"] = "Coordinated rewrite"
    profile_file.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["profiles"]["test_profile"]["sha256"] = hashlib.sha256(
        profile_file.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    artifact = load_consumer_artifact(out_dir)
    assert artifact.profiles["test_profile"].label == "Coordinated rewrite"


@pytest.mark.parametrize(
    "nonfinite",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_sol_counterexample_nonstandard_json_numbers_pass_build_and_load(
    tmp_path,
    nonfinite,
):
    facts_path, profile_path = _write_artifact_inputs(tmp_path)
    rows = _rows()
    rows[0]["value"] = nonfinite
    rows[0]["value_type"] = "number"
    facts_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    )
    out_dir = tmp_path / "artifact"

    build_consumer_artifact(
        out_dir, facts_path=facts_path, profile_paths=[profile_path]
    )
    artifact = load_consumer_artifact(out_dir)

    assert not math.isfinite(artifact.rows[0]["value"])
