"""PolicyEngine source-file inventory for Chronicle ingestion."""

from __future__ import annotations

from pathlib import Path

from chronicle.env import env_value

from .schema import Jurisdiction
from .source_files import SourceArtifactSpec, make_slug, make_url_slug

PE_US_DATA_ROOT_ENV = "CHRONICLE_PE_US_DATA_ROOT"
PE_UK_DATA_ROOT_ENV = "CHRONICLE_PE_UK_DATA_ROOT"

SOURCE_SUFFIXES = {
    ".csv",
    ".gz",
    ".html",
    ".htm",
    ".json",
    ".ods",
    ".pdf",
    ".txt",
    ".xlsx",
    ".yaml",
    ".yml",
    ".zip",
}

US_CALIBRATION_TARGET_ROOT_FILES = {
    "aca_ptc_multipliers_2022_2024.csv",
    "aca_ptc_multipliers_2022_2025.csv",
}

UK_STORAGE_TARGET_FILES = {
    "constituencies_2010.csv",
    "constituencies_2024.csv",
    "council_tax_bands_2024.csv",
    "demographics.csv",
    "dfc-ni-uc-stats-supp-tables-may-2025.ods",
    "incomes.csv",
    "incomes_projection.csv",
    "la_count_households.xlsx",
    "la_private_rents_median.xlsx",
    "la_tenure.xlsx",
    "local_authorities_2021.csv",
    "local_authority_ons_income.xlsx",
    "tax_benefit.csv",
    "uc_la_households.xlsx",
    "uc_national_payment_dist.xlsx",
    "uc_pc_households.xlsx",
}

US_CALIBRATION_SUPPORT_FILES = {
    "block_cd_distributions.csv.gz",
    "block_crosswalk.csv.gz",
    "county_cd_distributions.csv",
    "district_mapping.csv",
    "national_and_district_rents_2023.csv",
}

US_LONG_TERM_TARGET_SOURCE_FILES = {
    "oact_2025_08_05_provisional.csv",
    "oasdi_oact_20250805_nominal_delta.csv",
    "sources.json",
    "trustees_2025_current_law.csv",
}

US_LONG_TERM_ROOT_SOURCE_FILES = {
    "social_security_aux.csv",
    "SSPopJul_TR2024.csv",
}

US_LONG_TERM_REFERENCE_URLS = [
    {
        "source_id": "ssa",
        "url": "https://www.ssa.gov/oact/tr/2025/lrIndex.html",
        "filename": "ssa_2025_trustees_report_index.html",
    },
    {
        "source_id": "ssa",
        "url": "https://www.ssa.gov/oact/solvency/provisions/tables/table_run133.html",
        "filename": "ssa_solvencey_provision_table_run133.html",
    },
    {
        "source_id": "ssa",
        "url": "https://www.ssa.gov/OACT/solvency/RWyden_20250805.pdf",
        "filename": "ssa_oact_wyden_2025_08_05.pdf",
    },
]

UK_TARGET_CONFIG_FILES = {
    "policyengine_uk_data/targets/sources.yaml",
}

UK_TARGET_URL_FILES = [
    {
        "source_id": "hmrc",
        "url": "https://assets.publishing.service.gov.uk/media/67cabb37ade26736dbf9ffe5/Collated_Tables_3_1_to_3_17_2223.ods",
        "filename": "hmrc_spi_collated_tables_3_1_to_3_17_2223.ods",
    },
    {
        "source_id": "hmrc",
        "url": "https://assets.publishing.service.gov.uk/media/67cabb7f8c1076c796a45bec/Collated_Tables_3_12_to_3_15a_2223.ods",
        "filename": "hmrc_spi_geography_tables_3_12_to_3_15a_2223.ods",
    },
    {
        "source_id": "hmrc",
        "url": "https://assets.publishing.service.gov.uk/media/687a294e312ee8a5f0806b6d/Tables_6_1_and_6_2.csv",
        "filename": "hmrc_salary_sacrifice_tables_6_1_and_6_2.csv",
    },
    {
        "source_id": "obr",
        "url": "https://obr.uk/download/november-2025-economic-and-fiscal-outlook-detailed-forecast-tables-receipts/",
        "filename": "obr_november_2025_efo_detailed_forecast_tables_receipts.xlsx",
    },
    {
        "source_id": "obr",
        "url": "https://obr.uk/download/november-2025-economic-and-fiscal-outlook-detailed-forecast-tables-expenditure/",
        "filename": "obr_november_2025_efo_detailed_forecast_tables_expenditure.xlsx",
    },
    {
        "source_id": "ons",
        "url": "https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/populationandmigration/populationprojections/datasets/z1zippedpopulationprojectionsdatafilesuk/2022based/uk.zip",
        "filename": "ons_2022_based_uk_population_projection.zip",
    },
    {
        "source_id": "ons",
        "url": "https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/birthsdeathsandmarriages/families/datasets/familiesandhouseholdsfamiliesandhouseholds/current/familiesandhouseholdsuk2024.xlsx",
        "filename": "ons_families_and_households_uk_2024.xlsx",
    },
    {
        "source_id": "ons",
        "url": "https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/housing/datasets/subnationaldwellingstockbytenureestimates/current/subnationaldwellingsbytenure2024.xlsx",
        "filename": "ons_subnational_dwellings_by_tenure_2024.xlsx",
    },
    {
        "source_id": "ons",
        "url": "https://www.ons.gov.uk/economy/grossdomesticproductgdp/timeseries/haxv/ukea/data",
        "filename": "ons_haxv_savings_interest_timeseries.json",
    },
    {
        "source_id": "slc",
        "url": "https://explore-education-statistics.service.gov.uk/data-tables/permalink/6ff75517-7124-487c-cb4e-08de6eccf22d",
        "filename": "slc_student_loan_forecasts_permalink.html",
    },
    {
        "source_id": "dwp",
        "url": "https://stat-xplore.dwp.gov.uk/",
        "filename": "dwp_stat_xplore.html",
    },
    {
        "source_id": "dwp",
        "url": "https://www.gov.uk/government/statistics/benefit-cap-number-of-households-capped-to-february-2025/benefit-cap-number-of-households-capped-to-february-2025",
        "filename": "dwp_benefit_cap_february_2025.html",
    },
    {
        "source_id": "dwp",
        "url": "https://www.gov.uk/government/statistics/universal-credit-and-child-tax-credit-claimants-statistics-related-to-the-policy-to-provide-support-for-a-maximum-of-2-children-april-2024",
        "filename": "dwp_two_child_limit_april_2024.html",
    },
    {
        "source_id": "dwp",
        "url": "https://www.disabilityrightsuk.org/news/90-pip-standard-daily-living-component-recipients-would-fail-new-green-paper-test",
        "filename": "disability_rights_uk_pip_green_paper_test.html",
    },
    {
        "source_id": "hmrc",
        "url": "https://www.gov.uk/government/statistics/income-tax-summarised-accounts-statistics",
        "filename": "hmrc_income_tax_summarised_accounts_statistics.html",
    },
    {
        "source_id": "hmrc",
        "url": "https://www.gov.uk/government/statistics/income-and-tax-by-county-and-region-and-by-parliamentary-constituency",
        "filename": "hmrc_income_and_tax_by_area.html",
    },
    {
        "source_id": "uk-government",
        "url": "https://assets.publishing.service.gov.uk/media/67ce0e7c08e764d17a5d3c21/2025_SPP_Review.pdf",
        "filename": "uk_government_2025_spp_review.pdf",
    },
    {
        "source_id": "uk-government",
        "url": "https://www.gov.uk/government/publications/salary-sacrifice-reform-for-pension-contributions-effective-from-6-april-2029",
        "filename": "uk_government_salary_sacrifice_reform_2029.html",
    },
    {
        "source_id": "isc",
        "url": "https://www.isc.co.uk/research/annual-census/",
        "filename": "isc_annual_census.html",
    },
    {
        "source_id": "nts",
        "url": "https://www.gov.uk/government/statistics/national-travel-survey-2024",
        "filename": "nts_national_travel_survey_2024.html",
    },
    {
        "source_id": "ons",
        "url": "https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/populationestimates",
        "filename": "ons_population_estimates.html",
    },
    {
        "source_id": "ons",
        "url": "https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/privaterentandhousepricesuk/january2025",
        "filename": "ons_private_rent_and_house_prices_january_2025.html",
    },
    {
        "source_id": "ons",
        "url": "https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/earningsandworkinghours/datasets/smallareaincomeestimatesformiddlelayersuperoutputareasenglandandwales",
        "filename": "ons_small_area_income_estimates.html",
    },
    {
        "source_id": "ons",
        "url": "https://www.ons.gov.uk/peoplepopulationandcommunity/housing/datasets/privaterentalmarketsummarystatisticsinengland",
        "filename": "ons_private_rental_market_summary_statistics.html",
    },
    {
        "source_id": "ons",
        "url": "https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/populationprojections/datasets/z1zippedpopulationprojectionsdatafilesuk",
        "filename": "ons_uk_population_projection_dataset_page.html",
    },
    {
        "source_id": "ons",
        "url": "https://www.ons.gov.uk/peoplepopulationandcommunity/birthsdeathsandmarriages/families/datasets/familiesandhouseholdsfamiliesandhouseholds",
        "filename": "ons_families_and_households_dataset_page.html",
    },
    {
        "source_id": "ons",
        "url": "https://www.ons.gov.uk/economy/grossdomesticproductgdp/timeseries/haxv/ukea",
        "filename": "ons_haxv_savings_interest_timeseries_page.html",
    },
    {
        "source_id": "ons",
        "url": "https://www.ons.gov.uk/peoplepopulationandcommunity/housing/datasets/subnationaldwellingstockbytenureestimates",
        "filename": "ons_subnational_dwelling_stock_by_tenure_page.html",
    },
    {
        "source_id": "nrs",
        "url": "https://www.nrscotland.gov.uk/statistics-and-data/statistics/statistics-by-theme/population/population-estimates/mid-year-population-estimates",
        "filename": "nrs_mid_year_population_estimates.html",
    },
    {
        "source_id": "nrs",
        "url": "https://www.nrscotland.gov.uk/publications/vital-events-reference-tables-2024/",
        "filename": "nrs_vital_events_reference_tables_2024.html",
    },
    {
        "source_id": "scotland-census",
        "url": "https://www.scotlandscensus.gov.uk/census-results/at-a-glance/household-composition/",
        "filename": "scotland_census_household_composition.html",
    },
    {
        "source_id": "scottish-government",
        "url": "https://www.gov.scot/publications/scottish-budget-2026-2027/pages/6/",
        "filename": "scottish_budget_2026_2027_page_6.html",
    },
    {
        "source_id": "scottish-government",
        "url": "https://www.gov.scot/publications/council-tax-datasets/",
        "filename": "scottish_council_tax_datasets.html",
    },
    {
        "source_id": "voa",
        "url": "https://www.gov.uk/government/statistics/council-tax-stock-of-properties-2024",
        "filename": "voa_council_tax_stock_of_properties_2024.html",
    },
    {
        "source_id": "ons-housing",
        "url": "https://www.gov.uk/government/statistics/english-housing-survey-2023",
        "filename": "english_housing_survey_2023.html",
    },
]


def _resolve_required_root(
    root: Path | None,
    *,
    flag: str,
    env_var: str,
) -> Path:
    value = root if root is not None else env_value(env_var)
    if value is None:
        raise ValueError(f"{flag} or {env_var} is required.")
    path = Path(value).expanduser()
    if not path.exists():
        raise ValueError(f"{flag} / {env_var} path does not exist: {path}")
    return path


def _is_source_file(path: Path) -> bool:
    if path.name.startswith("~$"):
        return False
    if path.name.endswith(".csv.gz"):
        return True
    return path.suffix.lower() in SOURCE_SUFFIXES


def _infer_us_source_id(path: Path) -> str:
    name = path.name.lower()
    if "irs_soi" in name or "soi" in name or "agi" in name or "eitc" in name:
        return "irs-soi"
    if name.startswith(("acs", "census", "age_", "population_")) or (
        "real_estate_taxes" in name
    ):
        return "census"
    if "snap" in name:
        return "usda-snap"
    if "medicaid" in name:
        return "cms-medicaid"
    if "aca" in name:
        return "cms-aca"
    if "tanf" in name:
        return "hhs-acf-tanf"
    if "cdc" in name or "birth" in name or "pregnancy" in name:
        return "cdc"
    if "np2023" in name:
        return "census-population-projections"
    if "puf_filer" in name:
        return "irs-puf"
    if "healthcare" in name:
        return "cms-health-expenditures"
    return "policyengine-us-target-source"


def _infer_uk_source_id(path: Path) -> str:
    name = path.name.lower()
    if "uc_" in name or "dfc-ni" in name:
        return "dwp"
    if "income" in name or "incomes" in name or "salary" in name or "spi_" in name:
        return "hmrc"
    if "council_tax" in name:
        return "voa"
    if (
        "demographics" in name
        or "households" in name
        or "tenure" in name
        or name == "age.csv"
    ):
        return "ons"
    if "rent" in name or "lha" in name:
        return "ons-housing"
    if "constituenc" in name or "local_authorit" in name:
        return "ons-geography"
    if "nomis" in name:
        return "nomis"
    if "tax_benefit" in name:
        return "obr"
    if "capital_gains" in name:
        return "advani-summers"
    return "policyengine-uk-target-source"


def _spec(
    *,
    path: Path,
    root: Path,
    origin_project: str,
    pipeline: str,
    jurisdiction: Jurisdiction,
    source_id: str,
    notes: str,
) -> SourceArtifactSpec:
    return SourceArtifactSpec(
        slug=make_slug(origin_project, pipeline, root, path),
        path=path,
        origin_project=origin_project,
        pipeline=pipeline,
        jurisdiction=jurisdiction,
        source_id=source_id,
        source_name=source_id,
        notes=notes,
    )


def _url_spec(
    *,
    origin_project: str,
    pipeline: str,
    jurisdiction: Jurisdiction,
    source_id: str,
    url: str,
    filename: str,
    notes: str,
) -> SourceArtifactSpec:
    return SourceArtifactSpec(
        slug=make_url_slug(origin_project, pipeline, filename),
        origin_project=origin_project,
        pipeline=pipeline,
        jurisdiction=jurisdiction,
        source_id=source_id,
        source_name=source_id,
        source_url=url,
        filename=filename,
        notes=notes,
    )


def pe_us_source_specs(
    pe_us_root: Path | None = None,
) -> list[SourceArtifactSpec]:
    """Return public PE-US target source files available in a local checkout."""
    pe_us_root = _resolve_required_root(
        pe_us_root,
        flag="--pe-us-root",
        env_var=PE_US_DATA_ROOT_ENV,
    )
    root = pe_us_root / "policyengine_us_data" / "storage"
    specs: list[SourceArtifactSpec] = []

    raw_inputs = root / "calibration" / "raw_inputs"
    if raw_inputs.exists():
        for path in sorted(raw_inputs.iterdir()):
            if not path.is_file() or not _is_source_file(path):
                continue
            specs.append(
                _spec(
                    path=path,
                    root=pe_us_root,
                    origin_project="policyengine-us-data",
                    pipeline="database",
                    jurisdiction=Jurisdiction.US,
                    source_id=_infer_us_source_id(path),
                    notes="PE-US policy_data.db source artifact",
                )
            )

    calibration_targets = root / "calibration_targets"
    if calibration_targets.exists():
        for path in sorted(calibration_targets.iterdir()):
            if not path.is_file() or not _is_source_file(path):
                continue
            specs.append(
                _spec(
                    path=path,
                    root=pe_us_root,
                    origin_project="policyengine-us-data",
                    pipeline="legacy-loss-targets",
                    jurisdiction=Jurisdiction.US,
                    source_id=_infer_us_source_id(path),
                    notes="PE-US legacy loss/calibration target source artifact",
                )
            )

    for filename in sorted(US_CALIBRATION_SUPPORT_FILES):
        path = root / filename
        if path.exists() and _is_source_file(path):
            specs.append(
                _spec(
                    path=path,
                    root=pe_us_root,
                    origin_project="policyengine-us-data",
                    pipeline="calibration-support",
                    jurisdiction=Jurisdiction.US,
                    source_id=_infer_us_source_id(path),
                    notes="PE-US calibration support source artifact",
                )
            )

    long_term_root = root / "long_term_target_sources"
    has_long_term_sources = long_term_root.exists()
    for filename in sorted(US_LONG_TERM_TARGET_SOURCE_FILES):
        path = long_term_root / filename
        if path.exists() and _is_source_file(path):
            has_long_term_sources = True
            specs.append(
                _spec(
                    path=path,
                    root=pe_us_root,
                    origin_project="policyengine-us-data",
                    pipeline="long-term-target-sources",
                    jurisdiction=Jurisdiction.US,
                    source_id=_infer_us_source_id(path),
                    notes="PE-US long-term calibration target source artifact",
                )
            )

    for filename in sorted(US_LONG_TERM_ROOT_SOURCE_FILES):
        path = root / filename
        if path.exists() and _is_source_file(path):
            has_long_term_sources = True
            specs.append(
                _spec(
                    path=path,
                    root=pe_us_root,
                    origin_project="policyengine-us-data",
                    pipeline="long-term-target-sources",
                    jurisdiction=Jurisdiction.US,
                    source_id="ssa",
                    notes="PE-US long-term calibration source artifact",
                )
            )

    if has_long_term_sources:
        for item in US_LONG_TERM_REFERENCE_URLS:
            specs.append(
                _url_spec(
                    origin_project="policyengine-us-data",
                    pipeline="long-term-target-references",
                    jurisdiction=Jurisdiction.US,
                    source_id=item["source_id"],
                    url=item["url"],
                    filename=item["filename"],
                    notes="PE-US long-term calibration reference source artifact",
                )
            )

    for filename in sorted(US_CALIBRATION_TARGET_ROOT_FILES):
        path = root / filename
        if path.exists():
            specs.append(
                _spec(
                    path=path,
                    root=pe_us_root,
                    origin_project="policyengine-us-data",
                    pipeline="unified-calibration",
                    jurisdiction=Jurisdiction.US,
                    source_id=_infer_us_source_id(path),
                    notes="PE-US unified calibration source artifact",
                )
            )

    return specs


def pe_uk_source_specs(
    pe_uk_root: Path | None = None,
) -> list[SourceArtifactSpec]:
    """Return public PE-UK target source files available in a local checkout."""
    pe_uk_root = _resolve_required_root(
        pe_uk_root,
        flag="--pe-uk-root",
        env_var=PE_UK_DATA_ROOT_ENV,
    )
    storage = pe_uk_root / "policyengine_uk_data" / "storage"
    specs: list[SourceArtifactSpec] = []

    for filename in sorted(UK_TARGET_CONFIG_FILES):
        path = pe_uk_root / filename
        if path.exists() and _is_source_file(path):
            specs.append(
                _spec(
                    path=path,
                    root=pe_uk_root,
                    origin_project="policyengine-uk-data",
                    pipeline="target-registry-config",
                    jurisdiction=Jurisdiction.UK,
                    source_id="policyengine-uk",
                    notes="PE-UK target source registry configuration",
                )
            )

    for filename in sorted(UK_STORAGE_TARGET_FILES):
        path = storage / filename
        if path.exists() and _is_source_file(path):
            specs.append(
                _spec(
                    path=path,
                    root=pe_uk_root,
                    origin_project="policyengine-uk-data",
                    pipeline="target-registry",
                    jurisdiction=Jurisdiction.UK,
                    source_id=_infer_uk_source_id(path),
                    notes="PE-UK target registry source artifact",
                )
            )

    local_area_root = pe_uk_root / "policyengine_uk_data" / "datasets" / "local_areas"
    for path in sorted(local_area_root.glob("*/targets/*")):
        if not path.is_file() or not _is_source_file(path):
            continue
        specs.append(
            _spec(
                path=path,
                root=pe_uk_root,
                origin_project="policyengine-uk-data",
                pipeline="local-area-targets",
                jurisdiction=Jurisdiction.UK,
                source_id=_infer_uk_source_id(path),
                notes="PE-UK local-area target source artifact",
            )
        )

    for item in UK_TARGET_URL_FILES:
        specs.append(
            _url_spec(
                origin_project="policyengine-uk-data",
                pipeline="target-registry-live-sources",
                jurisdiction=Jurisdiction.UK,
                source_id=item["source_id"],
                url=item["url"],
                filename=item["filename"],
                notes="PE-UK target registry live source artifact",
            )
        )

    return specs


def pe_source_specs(
    pe_us_root: Path | None = None,
    pe_uk_root: Path | None = None,
    include_us: bool = True,
    include_uk: bool = True,
) -> list[SourceArtifactSpec]:
    """Return source files used by the PE-US and PE-UK calibration pipelines."""
    specs: list[SourceArtifactSpec] = []
    us_configured = pe_us_root is not None or env_value(PE_US_DATA_ROOT_ENV)
    uk_configured = pe_uk_root is not None or env_value(PE_UK_DATA_ROOT_ENV)
    if include_us and (us_configured or not include_uk or not uk_configured):
        specs.extend(pe_us_source_specs(pe_us_root))
    if include_uk and (uk_configured or not include_us or not us_configured):
        specs.extend(pe_uk_source_specs(pe_uk_root))
    return specs
