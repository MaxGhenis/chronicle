"""Tests for merged Chronicle consumer bundles."""

from __future__ import annotations

import json

from chronicle.bundle import build_bundle, build_bundle_coverage
from chronicle.harness import main as harness_main


def _load_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_build_bundle_writes_merged_consumer_contract(tmp_path):
    output_dir = tmp_path / "bundle"

    report = build_bundle(output_dir, year=2023)
    summary = json.loads((output_dir / "reports" / "build_bundle.json").read_text())
    rows = _load_jsonl(output_dir / "consumer_facts.jsonl")
    source_packages = json.loads((output_dir / "source_packages.json").read_text())
    coverage = json.loads((output_dir / "coverage.json").read_text())

    assert report.valid
    assert summary["valid"]
    assert summary["counts"] == {
        "aggregate_duplicate_key_count": 0,
        "entity_count": 9,
        "error_count": 0,
        "fact_count": 145222,
        "geography_count": 9390,
        "period_count": 144,
        "semantic_duplicate_key_count": 12,
        "skipped_source_count": 16,
        "source_count": 40,
        "source_package_count": 120,
        "warning_count": 1,
    }
    assert len(rows) == 145222
    assert {row["provenance_class"] for row in rows} <= {
        "administrative",
        "census",
        "model_output",
        "survey_aggregate",
    }
    assert all(
        (
            isinstance(row.get("survey_instrument"), str)
            and row["survey_instrument"].strip()
        )
        if row["provenance_class"] == "survey_aggregate"
        else "survey_instrument" not in row
        for row in rows
    )
    assert rows[0]["aggregate_fact_key"].startswith("ledger.aggregate_fact.v2:")
    assert rows[0]["semantic_fact_key"].startswith("ledger.semantic_fact.v2:")
    assert source_packages["source_package_count"] == 120
    assert source_packages["skipped_source_count"] == 16
    assert sorted(item["source"] for item in source_packages["skipped_sources"]) == [
        "census-acs-b19001-sld-lower-utah-household-income-2024",
        "census-acs-b19001-sld-upper-utah-household-income-2024",
        "census-acs-b19013-sld-lower-utah-median-household-income-2024",
        "census-acs-b19013-sld-upper-utah-median-household-income-2024",
        "census-acs-s0101-congressional-district-age-2024",
        "census-acs-s0101-national-age-2024",
        "census-acs-s0101-sld-lower-utah-age-2024",
        "census-acs-s0101-sld-upper-utah-age-2024",
        "census-acs-s0101-state-age-2024",
        "census-acs-s2201-congressional-district-snap-2024",
        "cms-aca-effectuated-enrollment-2022",
        "cms-aca-oep-state-level",
        "cms-aca-oep-state-level-2022",
        "cms-aca-oep-state-level-2025",
        "jct-obbba-revenue-estimates-2025",
        "jct-tax-expenditures-2024",
    ]
    assert coverage["fact_count"] == 145222
    assert coverage["counts"]["by_source"] == {
        "bea": 445,
        "bfp_economic_outlook": 5,
        "cbo": 7,
        "census_acs": 468,
        "census_pep": 988,
        "census_population_projections": 86,
        "census_stc": 46,
        "cms_medicaid": 515,
        "cms_medicare": 1,
        "cms_nhe": 3,
        "dft": 81,
        "dwp": 6327,
        "federal_reserve": 1,
        "hhs_acf_liheap": 2,
        "hhs_acf_tanf": 110,
        "hmrc": 20533,
        "ici": 12,
        "irs_soi": 33777,
        "isc": 2,
        "jrc_euromod_be": 18,
        "kff": 52,
        "mhclg": 2672,
        "nbb_national_accounts": 1,
        "nisra": 510,
        "nrs": 5589,
        "obr": 196,
        "onem_rva_unemployment": 1,
        "ons": 65630,
        "onss_contributions": 1,
        "opgroeien_groeipakket": 11,
        "scotgov": 2504,
        "sfpd_pensions": 4,
        "slc": 199,
        "spf_finances_pit": 1,
        "ssa": 426,
        "statbel_fiscal_income": 565,
        "statbel_population_structure": 18,
        "usda_snap": 216,
        "voa": 3001,
        "welshgov": 198,
    }
    table_counts = coverage["counts"]["by_source_table"]
    assert len(table_counts) == 115
    assert table_counts["irs_soi:Congressional District Data 2022"] == 26880
    assert (
        table_counts[
            "cms_nhe:Employer-Sponsored Private Health Insurance: "
            "Calendar Years 1987-2024"
        ]
        == 2
    )
    assert table_counts["ssa:SSI Monthly Statistics, December 2024, Table 1"] == 4
    assert table_counts["irs_soi:Publication 1304 Table 1.1"] == 80
    assert (
        table_counts[
            "irs_soi:Publication 1304 Table 2.5 EITC by AGI and qualifying children"
        ]
        == 464
    )
    assert table_counts["bea:BEA Regional annual state personal income CSV ZIP"] == 416
    assert (
        table_counts["cbo:CBO budget and economic data, individual income tax receipts"]
        == 1
    )
    assert (
        table_counts[
            "cbo:Revenue Projections, by Category, February 2026, "
            "sheet 3.Individual Income Tax Details"
        ]
        == 6
    )
    assert (
        table_counts[
            "census_acs:ACS 2023 1-year detailed table B01001 female age bands by state"
        ]
        == 468
    )
    assert (
        table_counts[
            "cms_medicaid:State Medicaid and CHIP Applications, Eligibility "
            "Determinations, and Enrollment Data"
        ]
        == 515
    )
    assert table_counts["ssa:SSA Annual Statistical Supplement 2025 Table 7.B1"] == 416
    assert (
        table_counts[
            "ons:UK Business, Activity, Size and Location 2025 enterprise "
            "counts by SIC division, turnover band, and employment size band"
        ]
        == 1232
    )
    assert (
        table_counts[
            "ons:UK Business, Activity, Size and Location 2025 enterprise "
            "turnover and employment size bands"
        ]
        == 14
    )
    assert (
        table_counts[
            "hmrc:Annual UK VAT Statistics 2024 to 2025 VAT trader "
            "population and net VAT liability by trade sector"
        ]
        == 176
    )
    assert (
        table_counts[
            "hmrc:Annual UK VAT Statistics 2024 to 2025 VAT trader "
            "population and net VAT liability by turnover band"
        ]
        == 17
    )
    assert (
        table_counts[
            "statbel_fiscal_income:Personal income tax statistics by municipality, "
            "income year 2023, 2025 NIS geography"
        ]
        == 565
    )
    assert (
        table_counts[
            "spf_finances_pit:Personal income tax statistics total taxes, income year "
            "2023"
        ]
        == 1
    )
    assert (
        table_counts[
            "statbel_population_structure:Population by place of residence, nationality, "
            "marital status, age and sex, 2026"
        ]
        == 18
    )
    assert (
        table_counts[
            "onss_contributions:Declared contributions 2024, Table 6 by sector, "
            "status and sex"
        ]
        == 1
    )
    assert (
        table_counts[
            "onem_rva_unemployment:Annual report complete unemployment benefit "
            "recipients, 2024"
        ]
        == 1
    )
    assert (
        table_counts[
            "nbb_national_accounts:Household income accounts, gross disposable income, "
            "Belgium"
        ]
        == 1
    )
    assert (
        table_counts[
            "jrc_euromod_be:EUROMOD Country Report Belgium 2025 validation tables"
        ]
        == 18
    )
    assert coverage["counts"]["by_period"] == {
        "academic_year:2013": 6,
        "academic_year:2014": 6,
        "academic_year:2015": 6,
        "academic_year:2016": 6,
        "academic_year:2017": 6,
        "academic_year:2018": 6,
        "academic_year:2019": 6,
        "academic_year:2020": 6,
        "academic_year:2021": 6,
        "academic_year:2022": 6,
        "academic_year:2023": 6,
        "academic_year:2024": 26,
        "academic_year:2025": 20,
        "academic_year:2026": 20,
        "academic_year:2027": 20,
        "academic_year:2028": 20,
        "academic_year:2029": 16,
        "calendar_year:1951": 3,
        "calendar_year:1961": 3,
        "calendar_year:1971": 3,
        "calendar_year:1981": 3,
        "calendar_year:1995": 3,
        "calendar_year:1996": 36,
        "calendar_year:1997": 36,
        "calendar_year:1998": 36,
        "calendar_year:1999": 36,
        "calendar_year:2000": 36,
        "calendar_year:2001": 36,
        "calendar_year:2002": 39,
        "calendar_year:2003": 39,
        "calendar_year:2004": 39,
        "calendar_year:2005": 39,
        "calendar_year:2006": 39,
        "calendar_year:2007": 39,
        "calendar_year:2008": 39,
        "calendar_year:2009": 39,
        "calendar_year:2010": 39,
        "calendar_year:2011": 39,
        "calendar_year:2012": 39,
        "calendar_year:2013": 72,
        "calendar_year:2014": 72,
        "calendar_year:2015": 74,
        "calendar_year:2016": 72,
        "calendar_year:2017": 72,
        "calendar_year:2018": 84,
        "calendar_year:2019": 83,
        "calendar_year:2020": 83,
        "calendar_year:2021": 3990,
        "calendar_year:2022": 1911,
        "calendar_year:2023": 6161,
        "calendar_year:2024": 30578,
        "calendar_year:2025": 4438,
        "calendar_year:2026": 235,
        "calendar_year:2027": 214,
        "calendar_year:2028": 214,
        "calendar_year:2029": 214,
        "calendar_year:2031": 2,
        "fiscal_year:1996": 33,
        "fiscal_year:1997": 33,
        "fiscal_year:1998": 33,
        "fiscal_year:1999": 33,
        "fiscal_year:2000": 33,
        "fiscal_year:2001": 33,
        "fiscal_year:2002": 33,
        "fiscal_year:2003": 33,
        "fiscal_year:2004": 33,
        "fiscal_year:2005": 33,
        "fiscal_year:2006": 33,
        "fiscal_year:2007": 33,
        "fiscal_year:2008": 33,
        "fiscal_year:2009": 33,
        "fiscal_year:2010": 33,
        "fiscal_year:2011": 33,
        "fiscal_year:2012": 33,
        "fiscal_year:2013": 33,
        "fiscal_year:2014": 33,
        "fiscal_year:2015": 33,
        "fiscal_year:2016": 33,
        "fiscal_year:2017": 33,
        "fiscal_year:2018": 33,
        "fiscal_year:2019": 33,
        "fiscal_year:2020": 33,
        "fiscal_year:2021": 33,
        "fiscal_year:2022": 33,
        "fiscal_year:2023": 385,
        "fiscal_year:2024": 610,
        "fiscal_year:2025": 1284,
        "fiscal_year:2026": 1439,
        "fiscal_year:2027": 28,
        "fiscal_year:2028": 28,
        "fiscal_year:2029": 28,
        "fiscal_year:2030": 28,
        "month:2023-01": 1,
        "month:2023-12": 6,
        "month:2024-01": 1,
        "month:2024-12": 270,
        "month:2025-01": 2,
        "month:2025-03": 120,
        "month:2025-04": 92,
        "month:2025-05": 6214,
        "month:2025-08": 4,
        "month:2025-09": 9,
        "month:2025-11": 15,
        "month:2025-12": 256,
        "month:2026-02": 4,
        "month:2026-06": 348,
        "tax_year:1987": 9,
        "tax_year:1988": 9,
        "tax_year:1989": 9,
        "tax_year:1990": 9,
        "tax_year:1991": 9,
        "tax_year:1992": 9,
        "tax_year:1993": 9,
        "tax_year:1994": 9,
        "tax_year:1995": 9,
        "tax_year:1996": 9,
        "tax_year:1997": 9,
        "tax_year:1998": 9,
        "tax_year:1999": 9,
        "tax_year:2000": 9,
        "tax_year:2001": 9,
        "tax_year:2002": 9,
        "tax_year:2003": 9,
        "tax_year:2004": 9,
        "tax_year:2005": 9,
        "tax_year:2006": 9,
        "tax_year:2007": 9,
        "tax_year:2008": 9,
        "tax_year:2009": 9,
        "tax_year:2010": 9,
        "tax_year:2011": 9,
        "tax_year:2012": 9,
        "tax_year:2013": 9,
        "tax_year:2014": 9,
        "tax_year:2015": 9,
        "tax_year:2016": 9,
        "tax_year:2017": 9,
        "tax_year:2018": 9,
        "tax_year:2019": 9,
        "tax_year:2020": 9,
        "tax_year:2021": 9,
        "tax_year:2022": 34951,
        "tax_year:2023": 48436,
        "tax_year:2024": 40,
    }
    assert coverage["counts"]["by_geography"]["country:BE"] == 31
    assert coverage["counts"]["by_geography"]["nuts1:BE1"] == 6
    assert coverage["counts"]["by_geography"]["nuts1:BE2"] == 17
    assert coverage["counts"]["by_geography"]["nuts1:BE3"] == 6
    assert coverage["counts"]["by_geography"]["commune:11002"] == 1
    assert coverage["counts"]["by_geography"]["country:0100000US"] == 2109
    assert coverage["counts"]["by_geography"]["state:0400000US06"] == 217
    assert (
        coverage["counts"]["by_geography"]["congressional_district:5001700US0601"] == 56
    )
    assert coverage["counts"]["by_geography"]["country:K02000001"] == 4189
    assert coverage["counts"]["by_geography"]["country:K03000001"] == 277
    assert len(coverage["counts"]["by_geography"]) == 9390
    assert coverage["counts"]["by_entity"] == {
        "dwelling": 12708,
        "family": 107,
        "firm": 1439,
        "government": 213,
        "household": 40114,
        "institutional_sector": 103,
        "pension_plan": 2,
        "person": 56753,
        "tax_unit": 33783,
    }
    assert not coverage["duplicates"]["aggregate_fact_keys"]
    assert len(coverage["duplicates"]["semantic_fact_keys"]) == 12
    assert summary["warnings"] == [
        {
            "code": "duplicate_semantic_fact_key",
            "message": (
                "One or more semantic facts appear in multiple rows; downstream "
                "consumers should reconcile or select sources."
            ),
        }
    ]
    assert (output_dir / "sources" / "soi-table-1-1" / "consumer_facts.jsonl").exists()
    assert (
        output_dir / "sources" / "soi-table-1-4" / "reports" / "build_summary.json"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "hhs-acf-liheap-fy2024-national-profile"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "soi-ira-roth-contributions-2022"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "census-stc-individual-income-tax"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "cms-medicare-trustees-report-2025-part-b-premium-income"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "cms-nhe-historical-service-source"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir / "sources" / "cms-nhe-table-24" / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "federal-reserve-z1-household-net-worth"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir / "sources" / "usda-snap-fy69-to-current" / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir / "sources" / "soi-historic-table-2" / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir / "sources" / "hhs-acf-tanf-caseload-2024" / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir / "sources" / "hhs-acf-tanf-financial-2024" / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "soi-congressional-district-2022"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "cbo-revenue-projections-income-by-source-2026-02"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "bea-regional-state-personal-income-components-2024"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir / "sources" / "ssa-ssi-table-7b1-2024" / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "ssa-ssi-monthly-statistics-2024-12"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "cms-medicaid-chip-monthly-enrollment-december-2024"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "soi-historic-table-2-state-broad-2022"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "kff-marketplace-effectuated-enrollment"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "ons-uk-business-firm-targets-2025"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "ons-uk-business-firm-sector-targets-2025"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "hmrc-vat-firm-targets-2024-25"
        / "consumer_facts.jsonl"
    ).exists()
    assert (
        output_dir
        / "sources"
        / "hmrc-vat-firm-sector-targets-2024-25"
        / "consumer_facts.jsonl"
    ).exists()


def test_build_bundle_cli_supports_explicit_sources(tmp_path, capsys):
    output_dir = tmp_path / "bundle"

    exit_code = harness_main(
        [
            "build-bundle",
            "--year",
            "2023",
            "--source",
            "soi-table-1-1",
            "--out",
            str(output_dir),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["valid"]
    assert payload["counts"]["source_package_count"] == 1
    assert payload["counts"]["fact_count"] == 80
    assert payload["outputs"]["consumer_facts"] == str(
        output_dir / "consumer_facts.jsonl"
    )
    assert payload["coverage"]["counts"]["by_source_table"] == {
        "irs_soi:Publication 1304 Table 1.1": 80
    }


def test_build_bundle_cli_supports_historic_table_2_source(tmp_path, capsys):
    output_dir = tmp_path / "bundle"

    exit_code = harness_main(
        [
            "build-bundle",
            "--year",
            "2023",
            "--source",
            "soi-historic-table-2",
            "--out",
            str(output_dir),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["valid"]
    assert payload["counts"]["source_package_count"] == 1
    assert payload["counts"]["fact_count"] == 605
    assert payload["coverage"]["counts"]["by_source_table"] == {
        "irs_soi:Historic Table 2": 605
    }
    assert (
        output_dir / "sources" / "soi-historic-table-2" / "source_rows.jsonl"
    ).exists()


def test_build_bundle_cli_supports_ssa_supplement_source(tmp_path, capsys):
    output_dir = tmp_path / "bundle"

    exit_code = harness_main(
        [
            "build-bundle",
            "--year",
            "2024",
            "--source",
            "ssa-annual-statistical-supplement-2025",
            "--out",
            str(output_dir),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    rows = _load_jsonl(output_dir / "consumer_facts.jsonl")

    assert exit_code == 0
    assert payload["valid"]
    assert payload["counts"]["source_package_count"] == 1
    assert payload["counts"]["fact_count"] == 6
    assert payload["coverage"]["counts"]["by_source"] == {"ssa": 6}
    assert payload["coverage"]["counts"]["by_entity"] == {"person": 6}
    assert {row["universe_constraints"]["constraints"][0]["value"] for row in rows} == {
        "social_security_benefits",
        "social_security_retirement_benefits",
        "social_security_survivors_benefits",
        "social_security_disability_benefits",
        "social_security_dependents_benefits",
        "ssi_payments",
    }


def test_build_bundle_cli_supports_jct_tax_expenditure_source(tmp_path, capsys):
    output_dir = tmp_path / "bundle"

    exit_code = harness_main(
        [
            "build-bundle",
            "--year",
            "2024",
            "--source",
            "jct-tax-expenditures-2024",
            "--out",
            str(output_dir),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    rows = _load_jsonl(output_dir / "consumer_facts.jsonl")

    assert exit_code == 0
    assert payload["valid"]
    assert payload["counts"]["source_package_count"] == 1
    assert payload["counts"]["fact_count"] == 11
    assert payload["coverage"]["counts"]["by_source"] == {"jct": 11}
    assert payload["coverage"]["counts"]["by_entity"] == {"tax_unit": 11}
    assert {row["lineage"]["source_record_id"] for row in rows} == {
        "jct.tax_expenditures.cy2024.salt_deduction.revenue_loss",
        "jct.tax_expenditures.cy2024.medical_expense_deduction.revenue_loss",
        "jct.tax_expenditures.cy2024.charitable_deduction.revenue_loss",
        "jct.tax_expenditures.cy2024.deductible_mortgage_interest.revenue_loss",
        "jct.tax_expenditures.cy2024.qualified_business_income_deduction.revenue_loss",
        # JCX-48-24 extension (microcosm#514 anchors):
        "jct.tax_expenditures.cy2024.self_employed_health_insurance_deduction.revenue_loss",
        "jct.tax_expenditures.cy2024.health_savings_account_deduction.revenue_loss",
        "jct.tax_expenditures.cy2024.student_loan_interest_deduction.revenue_loss",
        "jct.tax_expenditures.cy2024.self_employed_pension_contribution_deduction.revenue_loss",
        "jct.tax_expenditures.cy2024.traditional_ira_deduction.revenue_loss",
        "jct.tax_expenditures.cy2024.cdcc_and_employer_child_care_exclusion.revenue_loss",
    }


def test_build_bundle_cli_supports_jct_obbba_source(tmp_path, capsys):
    output_dir = tmp_path / "bundle"

    exit_code = harness_main(
        [
            "build-bundle",
            "--year",
            "2026",
            "--source",
            "jct-obbba-revenue-estimates-2025",
            "--out",
            str(output_dir),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    rows = _load_jsonl(output_dir / "consumer_facts.jsonl")

    assert exit_code == 0
    assert payload["valid"]
    assert payload["counts"]["source_package_count"] == 1
    assert payload["counts"]["fact_count"] == 2
    assert payload["coverage"]["counts"]["by_source"] == {"jct": 2}
    assert payload["coverage"]["counts"]["by_entity"] == {"tax_unit": 2}
    assert {row["lineage"]["source_record_id"] for row in rows} == {
        "jct.obbba_title_vii.fy2026.no_tax_on_tips.revenue_effect",
        "jct.obbba_title_vii.fy2026.no_tax_on_overtime.revenue_effect",
    }
    values = {
        row["lineage"]["source_record_id"]: row["value"] for row in rows
    }
    # JCX-35-25 FY2026: tips -$10,121M, overtime -$32,806M.
    assert values[
        "jct.obbba_title_vii.fy2026.no_tax_on_tips.revenue_effect"
    ] == -10_121_000_000
    assert values[
        "jct.obbba_title_vii.fy2026.no_tax_on_overtime.revenue_effect"
    ] == -32_806_000_000


def test_build_bundle_coverage_reports_duplicate_keys():
    rows = [
        {
            "aggregate_fact_key": "ledger.aggregate_fact.v2:a",
            "semantic_fact_key": "ledger.semantic_fact.v2:s",
            "legacy_fact_key": "ledger.fact.v1:one",
            "source": {
                "source_name": "irs_soi",
                "source_table": "Publication 1304 Table 1.1",
            },
            "period": {"type": "tax_year", "value": 2023},
            "geography": {"level": "country", "id": "0100000US"},
            "entity": {"name": "tax_unit"},
            "observed_measure": {
                "source_name": "irs_soi",
                "source_measure_id": "return_count",
                "source_concept": "irs_soi.individual_income_tax_returns",
            },
        },
        {
            "aggregate_fact_key": "ledger.aggregate_fact.v2:a",
            "semantic_fact_key": "ledger.semantic_fact.v2:s",
            "legacy_fact_key": "ledger.fact.v1:two",
            "source": {
                "source_name": "irs_soi",
                "source_table": "Publication 1304 Table 1.1",
            },
            "period": {"type": "tax_year", "value": 2023},
            "geography": {"level": "country", "id": "0100000US"},
            "entity": {"name": "tax_unit"},
            "observed_measure": {
                "source_name": "irs_soi",
                "source_measure_id": "return_count",
                "source_concept": "irs_soi.individual_income_tax_returns",
            },
        },
    ]

    coverage = build_bundle_coverage(
        rows,
        aggregate_duplicates=[
            {
                "key": "ledger.aggregate_fact.v2:a",
                "count": 2,
                "sources": ["irs_soi:Publication 1304 Table 1.1"],
                "legacy_fact_keys": ["ledger.fact.v1:one", "ledger.fact.v1:two"],
            }
        ],
        semantic_duplicates=[
            {
                "key": "ledger.semantic_fact.v2:s",
                "count": 2,
                "sources": ["irs_soi:Publication 1304 Table 1.1"],
                "legacy_fact_keys": ["ledger.fact.v1:one", "ledger.fact.v1:two"],
            }
        ],
    )

    assert coverage["counts"]["by_source"] == {"irs_soi": 2}
    assert coverage["duplicates"]["aggregate_fact_keys"][0]["count"] == 2
    assert coverage["duplicates"]["semantic_fact_keys"][0]["count"] == 2
