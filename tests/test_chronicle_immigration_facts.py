from chronicle.core import validate_facts
from chronicle.source_package import load_source_package, validate_source_package
from chronicle.sources.cells import validate_source_cells


def _build_facts(alias: str, year: int):
    package = load_source_package(alias)
    cells = package.build_source_cells(year)
    facts = package.build_facts(year, cells=cells)

    assert validate_source_cells(cells).valid
    assert validate_facts(facts).valid
    return facts


def _constraints(fact):
    return {
        (constraint.variable, constraint.operator, constraint.value)
        for constraint in fact.constraints
    }


def test_ohss_lpr_population_2024_package_builds_source_native_broad_class():
    report = validate_source_package("ohss-lpr-population-2024", year=2024)
    assert report.valid
    assert report.counts == {
        "record_set_count": 1,
        "row_count": 2,
        "measure_count": 1,
        "source_record_count": 2,
        "source_region_count": 1,
    }

    facts = _build_facts("ohss-lpr-population-2024", 2024)
    by_value_id = {fact.layout.groupby_value_id: fact for fact in facts}

    total = by_value_id["total"]
    refugee_or_asylee = by_value_id["refugee_or_asylee_broad_class"]

    assert total.value == 12_820_000
    assert _constraints(total) == {
        (
            "dhs_ohss_lpr_population_scope",
            "==",
            "resident_lawful_permanent_resident",
        )
    }
    assert refugee_or_asylee.value == 1_320_000
    assert _constraints(refugee_or_asylee) == {
        (
            "dhs_ohss_lpr_population_scope",
            "==",
            "resident_lawful_permanent_resident",
        ),
        (
            "dhs_ohss_lpr_broad_class_of_admission",
            "==",
            "refugees_and_asylees",
        ),
    }
    assert total.period.value == "2024-01"
    assert total.period_coverage.start_date == "2024-01-01"
    assert total.period_coverage.end_date == "2024-01-01"
    assert total.provenance_class == "model_output"
    assert (
        "class-of-admission proxy" in refugee_or_asylee.measure.concept_evidence_notes
    )


def test_uscis_active_daca_fy2026_q2_package_builds_true_snapshot_stock():
    report = validate_source_package(
        "uscis-active-daca-recipients-fy2026-q2", year=2026
    )
    assert report.valid
    assert report.counts["source_record_count"] == 1

    [fact] = _build_facts("uscis-active-daca-recipients-fy2026-q2", 2026)

    assert fact.source_record_id == (
        "uscis.daca.2026_03.active_recipients.total.active_daca_recipients"
    )
    assert fact.value == 455_170
    assert fact.period.value == "2026-03"
    assert fact.period_coverage.start_date == "2026-03-31"
    assert fact.period_coverage.end_date == "2026-03-31"
    assert _constraints(fact) == {
        ("uscis_daca_population_status", "==", "active_recipient"),
        (
            "uscis_daca_has_obtained_lawful_permanent_resident_status",
            "==",
            False,
        ),
        ("uscis_daca_has_obtained_us_citizenship", "==", False),
    }


def test_uscis_tps_cy2023_package_builds_country_stocks_and_prior_status_codes():
    report = validate_source_package("uscis-tps-cy2023-congressional-report", year=2023)
    assert report.valid
    assert report.counts == {
        "record_set_count": 2,
        "row_count": 20,
        "measure_count": 2,
        "source_record_count": 20,
        "source_region_count": 2,
    }

    facts = _build_facts("uscis-tps-cy2023-congressional-report", 2023)
    by_value_id = {fact.layout.groupby_value_id: fact for fact in facts}

    total = by_value_id["total"]
    country_facts = [
        fact
        for fact in facts
        if fact.layout.record_set_id.endswith("beneficiaries_by_designated_country")
        and fact.layout.groupby_value_id != "total"
    ]
    assert total.value == 870_332
    assert sum(fact.value for fact in country_facts) == total.value
    assert len(country_facts) == 16
    assert total.period_coverage.start_date == "2023-12-31"
    assert total.period_coverage.end_date == "2023-12-31"
    assert _constraints(total) == {
        (
            "uscis_tps_beneficiary_status",
            "==",
            "beneficiary_at_close_of_cy2023",
        )
    }

    assert by_value_id["venezuela"].value == 284_207
    assert _constraints(by_value_id["venezuela"]) == {
        (
            "uscis_tps_beneficiary_status",
            "==",
            "beneficiary_at_close_of_cy2023",
        ),
        ("uscis_tps_designated_country", "==", "Venezuela"),
    }

    asylee = by_value_id["asy_asylee"]
    refugee = by_value_id["re_refugee"]
    haitian_refugee = by_value_id["re5_haitian_refugee"]
    assert (asylee.value, refugee.value, haitian_refugee.value) == (1_808, 1_688, 619)
    assert _constraints(asylee) == {
        ("uscis_tps_prior_status_code", "==", "ASY"),
    }
    assert _constraints(refugee) == {
        ("uscis_tps_prior_status_code", "==", "RE"),
    }
    assert _constraints(haitian_refugee) == {
        ("uscis_tps_prior_status_code", "==", "RE5"),
    }
    assert "asylum-applicant categories" in (asylee.measure.concept_evidence_notes)
