"""Declarative source-package loading for Chronicle build suites."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from importlib.resources import files
from io import BytesIO
from pathlib import Path
import re
from typing import Any
from urllib.parse import unquote, urlparse
from zipfile import ZipFile

import httpx
import yaml

from chronicle.core import (
    ALLOWED_AGGREGATIONS,
    ALLOWED_ASSERTIONS,
    ALLOWED_PROVENANCE_CLASSES,
    DEFAULT_ASSERTION,
    Aggregation,
    AggregateConstraint,
    EntityDimension,
    GeographyDimension,
    Measure,
    PeriodCoverage,
    PeriodDimension,
    SourceProvenance,
    AggregateFact,
    build_label,
)
from chronicle.env import env_flag, env_value
from chronicle.registration import (
    MicrodataReleaseNotParseableError,
    is_microdata_release,
)
from chronicle.sources.cells import (
    SourceArtifactMetadata,
    SourceCell,
    source_cells_from_delimited_text,
    source_cells_from_html_tables_and_text,
    source_cells_from_ods,
    source_cells_from_pdf_text_numbers,
    source_cells_from_xls,
    source_cells_from_xlsx,
)
from chronicle.sources.rows import (
    SourceRow,
    source_cells_from_source_rows,
    source_rows_from_census_acs_s0101_age_json,
    source_rows_from_census_acs_s2201_snap_json,
    source_rows_from_census_b01001_female_age_json,
    source_rows_from_cdc_vsrr_live_births_json,
    source_rows_from_json_table,
    source_rows_from_statxplore_table,
    source_rows_from_ees_permalink_table_html,
    source_rows_from_json_stat_2,
    source_rows_from_kff_state_indicator_gdocs_html,
    source_rows_from_ons_timeseries_json,
    source_rows_from_delimited_text,
    source_rows_from_xlsx_table,
)
from chronicle.sources.specs import (
    SourceRecord,
    SourceRecordSetMeasure,
    SourceRecordSetRangeLabelGuard,
    SourceRecordSetRow,
    SourceRecordSetRowGuard,
    SourceRecordSetSpec,
    SourceRecordSpec,
    build_cells_by_sheet_address,
    compile_source_record_set_specs,
    resolve_source_record,
    source_regions_from_record_set_spec,
)

SOURCE_PACKAGE_RESOURCE_PACKAGE = "packages"
SOURCE_PACKAGE_ALIASES = {
    "bea-nipa-personal-income-components": Path("bea/nipa_personal_income_components"),
    "bea-nipa-personal-income-disposition": Path(
        "bea/nipa_personal_income_disposition"
    ),
    "bea-nipa-pension-contributions": Path("bea/nipa_pension_contributions"),
    "bea-nipa-total-wages-salaries": Path("bea/nipa_total_wages_salaries"),
    "bea-regional-state-personal-income-components-2024": Path(
        "bea/regional_personal_income_state"
    ),
    "hmrc-spi-income-bands-2023-24": Path("hmrc/spi_income_bands_2023_24"),
    "hmrc-cgt-size-of-gain-2025": Path("hmrc/cgt_size_of_gain_2025"),
    "hmrc-cgt-statistics-2025": Path("hmrc/cgt_statistics_2025"),
    "hmrc-spi-income-by-area-2023-24": Path("hmrc/spi_income_by_area_2023_24"),
    "hmrc-salary-sacrifice-relief-2024-25": Path(
        "hmrc/salary_sacrifice_relief_2024_25"
    ),
    "hmrc-salary-sacrifice-reform-2029-headcounts": Path(
        "hmrc/salary_sacrifice_reform_2029_headcounts"
    ),
    "hmrc-tax-free-childcare-march-2026": Path("hmrc/tax_free_childcare_march_2026"),
    "ici-fact-book-table-30": Path("ici/fact_book_table_30"),
    "isc-annual-census-2023": Path("isc/annual_census_2023"),
    "isc-annual-census-2024": Path("isc/annual_census_2024"),
    "mhclg-council-tax-levels-england-2026-27": Path(
        "mhclg/council_tax_levels_england_2026_27"
    ),
    "mhclg-council-tax-levels-england-summary-2025-26": Path(
        "mhclg/council_tax_levels_england_summary_2025_26"
    ),
    "mhclg-council-tax-collection-england-2025-26": Path(
        "mhclg/council_tax_collection_england_2025_26"
    ),
    "mhclg-ehs-weekly-housing-costs-2023-24": Path(
        "mhclg/ehs_weekly_housing_costs_2023_24"
    ),
    "statbel-population-structure-2025": Path("statbel/population_structure_2025"),
    "statbel-population-structure-2026": Path("statbel/population_structure_2026"),
    "statbel-fiscal-income-2023-nis-2025": Path("statbel/fiscal_income_2023_nis_2025"),
    "statbel-fiscal-income-distribution-2023": Path(
        "statbel/fiscal_income_distribution_2023"
    ),
    "spf-finances-pit-2023": Path("spf_finances/pit_2023"),
    "onss-contributions-2024": Path("onss/contributions_2024"),
    "onem-rva-unemployment-2024": Path("onem_rva/unemployment_2024"),
    "nbb-national-accounts-household-disposable-income-2024": Path(
        "nbb/national_accounts_household_disposable_income_2024"
    ),
    "jrc-euromod-be-baseline-statistics-2025": Path(
        "jrc/euromod_be_baseline_statistics_2025"
    ),
    "sfpd-legal-pension-caseload-2025": Path("sfpd/legal_pension_caseload_2025"),
    "opgroeien-groeipakket-caseload-2025": Path("opgroeien/groeipakket_caseload_2025"),
    "bfp-economic-outlook-2026-06": Path("bfp/economic_outlook_2026_06"),
    "fpb-economic-outlook-2026-2031-june-2026": Path(
        "fpb/economic_outlook_2026_2031_june_2026"
    ),
    "dfe-funded-early-education-childcare-2026": Path(
        "dfe/funded_early_education_childcare_2026"
    ),
    "dft-nts-vehicle-ownership-2024": Path("dft/nts_vehicle_ownership_2024"),
    "dwp-benefit-cap-november-2025": Path("dwp/benefit_cap_november_2025"),
    "dwp-benefit-statistics-february-2026": Path(
        "dwp/benefit_statistics_february_2026"
    ),
    "dwp-uc-childcare-element-march-2021-august-2025": Path(
        "dwp/uc_childcare_element_march_2021_august_2025"
    ),
    "dwp-uc-deductions-march-2025-february-2026": Path(
        "dwp/uc_deductions_march_2025_february_2026"
    ),
    "dwp-pip-daily-living-foi-2025": Path("dwp/pip_daily_living_foi_2025"),
    "dwp-uc-households-by-constituency-may-2025": Path(
        "dwp/uc_households_by_constituency_may_2025"
    ),
    "dwp-uc-households-by-constituency-children-may-2025": Path(
        "dwp/uc_households_by_constituency_children_may_2025"
    ),
    "dwp-uc-households-by-local-authority-may-2025": Path(
        "dwp/uc_households_by_local_authority_may_2025"
    ),
    "dwp-uc-households-carer-entitlement-april-december-2025": Path(
        "dwp/uc_households_carer_entitlement_april_december_2025"
    ),
    "dwp-uc-households-children-april-december-2025": Path(
        "dwp/uc_households_children_april_december_2025"
    ),
    "dwp-uc-households-family-type-april-december-2025": Path(
        "dwp/uc_households_family_type_april_december_2025"
    ),
    "dwp-uc-households-housing-entitlement-april-december-2025": Path(
        "dwp/uc_households_housing_entitlement_april_december_2025"
    ),
    "dwp-uc-households-lcwra-entitlement-april-december-2025": Path(
        "dwp/uc_households_lcwra_entitlement_april_december_2025"
    ),
    "dwp-uc-payment-distribution-may-2025": Path(
        "dwp/uc_payment_distribution_may_2025"
    ),
    "dwp-uc-scotland-youngest-child-may-2025": Path(
        "dwp/uc_scotland_youngest_child_may_2025"
    ),
    "dwp-uc-two-child-limit-2025": Path("dwp/uc_two_child_limit_2025"),
    "cbo-revenue-projections-income-by-source-2026-02": Path(
        "cbo/revenue_projections_income_by_source_2026_02"
    ),
    "cbo-individual-income-tax-receipts-2026-02": Path(
        "cbo/individual_income_tax_receipts_2026_02"
    ),
    "census-acs-s0101-congressional-district-age-2024": Path(
        "census/acs_s0101_district_2024"
    ),
    "census-acs-s0101-national-age-2024": Path("census/acs_s0101_national_2024"),
    "census-acs-s0101-state-age-2024": Path("census/acs_s0101_state_2024"),
    "census-acs-s2201-congressional-district-snap-2024": Path(
        "census/acs_s2201_district_2024"
    ),
    "census-b01001-female-age-2023": Path("census/b01001_female_15_44_2023"),
    "census-pep-county-population-2024": Path("census/pep_county_2024"),
    "census-pep-2024-national-age-sex": Path("census/pep_2024_national_age_sex"),
    "census-pep-2024-state-age-sex": Path("census/pep_2024_state_age_sex"),
    "census-population-projections-2023": Path("census/population_projections_2023"),
    "census-stc-individual-income-tax": Path("census/stc_individual_income_tax"),
    "cms-medicaid-chip-monthly-enrollment-december-2024": Path(
        "cms_medicaid/chip_monthly_enrollment_december_2024"
    ),
    "cms-medicaid-chip-monthly-enrollment-dataset": Path(
        "cms_medicaid/chip_monthly_enrollment_dataset"
    ),
    "cms-aca-oep-state-level": Path("cms_aca/oep_state_level"),
    "cms-aca-oep-state-level-2022": Path("cms_aca/oep_state_level_2022"),
    "cms-aca-oep-state-level-2025": Path("cms_aca/oep_state_level_2025"),
    "cms-aca-effectuated-enrollment-2022": Path("cms_aca/effectuated_enrollment_2022"),
    "cms-medicare-trustees-report-2025-part-b-premium-income": Path(
        "cms_medicare/medicare_trustees_report_2025"
    ),
    "cms-nhe-historical-service-source": Path("cms_nhe/historical_service_source"),
    "cms-nhe-table-24": Path("cms_nhe/table_24"),
    "federal-reserve-z1-household-net-worth": Path(
        "federal_reserve/z1_household_net_worth"
    ),
    "hhs-acf-liheap-fy2023-national-profile": Path(
        "hhs_acf_liheap/fy2023_national_profile"
    ),
    "hhs-acf-liheap-fy2024-national-profile": Path(
        "hhs_acf_liheap/fy2024_national_profile"
    ),
    "jct-obbba-revenue-estimates-2025": Path("jct/obbba_revenue_estimates_2025"),
    "jct-tax-expenditures-2024": Path("jct/tax_expenditures_2024"),
    "slc-student-loan-borrower-forecasts-england-2025": Path(
        "slc/student_loan_borrower_forecasts_england_2025"
    ),
    "slc-student-loan-repayments-england-2025": Path(
        "slc/student_loan_repayments_england_2025"
    ),
    "slc-student-loan-repayments-northern-ireland-2025": Path(
        "slc/student_loan_repayments_northern_ireland_2025"
    ),
    "slc-student-loan-repayments-scotland-2025": Path(
        "slc/student_loan_repayments_scotland_2025"
    ),
    "slc-student-loan-repayments-wales-2025": Path(
        "slc/student_loan_repayments_wales_2025"
    ),
    "slc-student-support-england-2025": Path("slc/student_support_england_2025"),
    "soi-table-1-1": Path("irs_soi/table_1_1"),
    "soi-table-1-2": Path("irs_soi/table_1_2"),
    "soi-table-1-4": Path("irs_soi/table_1_4"),
    "soi-table-2-1": Path("irs_soi/table_2_1"),
    "soi-table-2-5": Path("irs_soi/table_2_5"),
    "soi-table-2-5-eitc-agi-children-2022": Path(
        "irs_soi/table_2_5_eitc_agi_children_2022"
    ),
    "soi-table-2-5-eitc-agi-children-2023": Path(
        "irs_soi/table_2_5_eitc_agi_children_2023"
    ),
    "soi-filing-season-week47-2024-eitc-total": Path(
        "irs_soi/filing_season_week47_2024"
    ),
    "soi-table-4-3": Path("irs_soi/table_4_3"),
    "soi-state-2022": Path("irs_soi/state_2022"),
    "soi-county-2022": Path("irs_soi/county_2022"),
    "soi-congressional-district-2022": Path("irs_soi/congressional_district_2022"),
    "soi-historic-table-2": Path("irs_soi/historic_table_2"),
    "soi-historic-table-2-state-agi-2022": Path(
        "irs_soi/historic_table_2_state_agi_2022"
    ),
    "soi-historic-table-2-state-broad-2022": Path(
        "irs_soi/historic_table_2_state_broad_2022"
    ),
    "soi-historic-table-2-state-eitc-2022": Path(
        "irs_soi/historic_table_2_state_eitc_2022"
    ),
    "soi-w2-statistics-2020": Path("irs_soi/w2_statistics_2020"),
    "soi-ira-traditional-contributions-2022": Path(
        "irs_soi/ira_traditional_contributions_2022"
    ),
    "soi-ira-roth-contributions-2022": Path("irs_soi/ira_roth_contributions_2022"),
    "ssa-annual-statistical-supplement-2025": Path(
        "ssa/annual_statistical_supplement_2025"
    ),
    "ssa-ssi-monthly-statistics-2024-12": Path("ssa/ssi_monthly_statistics_2024_12"),
    "ssa-ssi-table-7b1-2024": Path("ssa/ssi_table_7b1_2024"),
    "hhs-acf-tanf-caseload-2024": Path("hhs_acf/tanf_caseload_2024"),
    "hhs-acf-tanf-financial-2024": Path("hhs_acf/tanf_financial_2024"),
    "hmrc-vat-firm-targets-2024-25": Path("hmrc/vat_firm_targets_2024_25"),
    "hmrc-vat-firm-sector-targets-2024-25": Path(
        "hmrc/vat_firm_sector_targets_2024_25"
    ),
    "eurostat-gov-10a-taxag": Path("eurostat/gov_10a_taxag"),
    "eurostat-gov-10a-taxag-2025": Path("eurostat/gov_10a_taxag_2025"),
    "eurostat-spr-exp-func": Path("eurostat/spr_exp_func"),
    "eurostat-spr-exp-func-2024": Path("eurostat/spr_exp_func_2024"),
    "eurostat-nasa-10-nf-tr": Path("eurostat/nasa_10_nf_tr"),
    "eurostat-ilc-li02": Path("eurostat/ilc_li02"),
    "eurostat-ilc-di01": Path("eurostat/ilc_di01"),
    "kff-marketplace-effectuated-enrollment": Path(
        "kff/marketplace_effectuated_enrollment"
    ),
    "ons-census2021-ts041-households-lad": Path("ons/census2021_ts041_households_lad"),
    "ons-census2021-ts041-households-pcon24": Path(
        "ons/census2021_ts041_households_pcon24"
    ),
    "ons-mye-2023-uk-countries": Path("ons/mye_2023_uk_countries"),
    "ons-mye-2023-england-regions": Path("ons/mye_2023_england_regions"),
    "ons-mye-2024-uk": Path("ons/mye_2024_uk"),
    "ons-lad-population-by-age-2024": Path("ons/lad_population_by_age_2024"),
    "ons-pcon24-population-by-age-2024": Path("ons/pcon24_population_by_age_2024"),
    "ons-small-area-income-msoa-fye2023": Path("ons/small_area_income_msoa_fye2023"),
    "ons-census2021-ts054-tenure-lad": Path("ons/census2021_ts054_tenure_lad"),
    "ons-subnational-dwellings-by-tenure-2024": Path(
        "ons/subnational_dwellings_by_tenure_2024"
    ),
    "ons-pipr-rents-by-area-june-2026": Path("ons/pipr_rents_by_area_june_2026"),
    "nrs-census2022-households-ukpc24": Path("nrs/census2022_households_ukpc24"),
    "nrs-pcon24-population-by-age-2024": Path("nrs/pcon24_population_by_age_2024"),
    "nrs-census2022-uv404-tenure-council-area": Path(
        "nrs/census2022_uv404_tenure_council_area"
    ),
    "nisra-census2021-households-lgd": Path("nisra/census2021_households_lgd"),
    "nisra-census2021-households-pcon24": Path("nisra/census2021_households_pcon24"),
    "nisra-pcon24-population-by-age-2024": Path("nisra/pcon24_population_by_age_2024"),
    "nisra-census2021-tenure-lgd": Path("nisra/census2021_tenure_lgd"),
    "ons-uk-population-projections-2024": Path("ons/npp_2024_uk"),
    "scotgov-band-d-council-tax-rates-2026-27": Path(
        "scotgov/band_d_council_tax_rates_2026_27"
    ),
    "scotgov-band-d-equivalents-2025": Path("scotgov/band_d_equivalents_2025"),
    "scotgov-council-tax-bands-2025": Path("scotgov/council_tax_bands_2025"),
    "scotgov-council-tax-collection-2024-25": Path(
        "scotgov/council_tax_collection_2024_25"
    ),
    "scotgov-council-tax-collection-2025-26": Path(
        "scotgov/council_tax_collection_2025_26"
    ),
    "scotgov-slgfs-council-tax-2024-25": Path("scotgov/slgfs_council_tax_2024_25"),
    "scotgov-scottish-budget-social-security-assistance-2026": Path(
        "scotgov/scottish_budget_social_security_assistance_2026"
    ),
    "welshgov-council-tax-levels-2026-27": Path("welshgov/council_tax_levels_2026_27"),
    "welshgov-council-tax-collection-2024-25": Path(
        "welshgov/council_tax_collection_2024_25"
    ),
    "welshgov-council-tax-collection-2025-26": Path(
        "welshgov/council_tax_collection_2025_26"
    ),
    "welshgov-ctrs-annual-report-2024-25": Path("welshgov/ctrs_annual_report_2024_25"),
    "welshgov-ctrs-annual-report-2025-26": Path("welshgov/ctrs_annual_report_2025_26"),
    "voa-council-tax-bands-2025": Path("voa/council_tax_bands_2025"),
    "voa-council-tax-stock-by-lad-2025": Path("voa/council_tax_stock_by_lad_2025"),
    "obr-efo-receipts-march-2026": Path("obr/efo_receipts_march_2026"),
    "obr-efo-expenditure-march-2026": Path("obr/efo_expenditure_march_2026"),
    "obr-efo-economy-march-2026": Path("obr/efo_economy_march_2026"),
    "obr-efo-aggregates-march-2026": Path("obr/efo_aggregates_march_2026"),
    "ons-national-balance-sheet-land-2025": Path(
        "ons/national_balance_sheet_land_2025"
    ),
    "ons-public-sector-employment-2026": Path("ons/public_sector_employment_2026"),
    "ons-savings-interest-income": Path("ons/savings_interest_income"),
    "ons-families-households-2025": Path("ons/families_households_2025"),
    "ons-households-by-type-country-2025": Path("ons/households_by_type_country_2025"),
    "ons-pipr-private-rent-march-2026": Path("ons/pipr_private_rent_march_2026"),
    "ons-uk-business-firm-targets-2025": Path("ons/uk_business_firm_targets_2025"),
    "ons-uk-business-firm-sector-targets-2025": Path(
        "ons/uk_business_firm_sector_targets_2025"
    ),
    "usda-snap-fy69-to-current": Path("usda_snap/fy69_to_current"),
    "usda-snap-fy2025-monthly-state-caseloads": Path(
        "usda_snap/fy2025_monthly_state_caseloads"
    ),
}
SOURCE_ARTIFACT_CACHE_ENV = "CHRONICLE_SOURCE_ARTIFACT_CACHE_DIR"
SOURCE_ARTIFACT_FETCH_ENV = "CHRONICLE_SOURCE_ARTIFACT_FETCH"
DEFAULT_SOURCE_ARTIFACT_CACHE_DIR = (
    Path.home() / ".cache" / "policyengine-chronicle" / "source-artifacts"
)
SOURCE_PACKAGE_FILENAME = "source_package.yaml"
# Repository ``packages/`` directory: the source-package authoring surface a
# complete bundle must cover (see PolicyEngine/chronicle#78).
SOURCE_PACKAGE_ROOT = (
    Path(__file__).resolve().parents[1] / SOURCE_PACKAGE_RESOURCE_PACKAGE
)
EXCEL_COLUMN_RE = re.compile(r"^[A-Z]+$")


@dataclass(frozen=True)
class SourceArtifactSpec:
    """Declarative source artifact lookup and provenance metadata."""

    source_name: str
    source_table: str
    resource_package: str
    resource_directory: str
    manifest: str
    vintage: str
    extracted_at: str
    extraction_method: str
    parser: str = "xls_used_range"
    sheet_name: str | None = None
    archive_member: str | None = None
    artifact_year: int | None = None
    delimiter: str = ","
    header_row: int = 1
    selected_rows: tuple[dict[str, Any], ...] = ()
    sheets: tuple[str, ...] = ()

    def build_source_rows(self, year: int) -> list[SourceRow]:
        """Parse a delimited artifact for a year into full source-row records."""
        content, filename, source_url, raw_r2 = self._artifact_content(year)
        artifact = self._source_artifact_metadata(
            content,
            filename,
            source_url,
            raw_r2,
            year=year,
        )
        if self.parser in {"delimited_text_full_rows", "zip_delimited_text_full_rows"}:
            delimited_content = self._delimited_content(content, filename)
            return source_rows_from_delimited_text(
                delimited_content,
                artifact,
                sheet_name=self._sheet_name(filename, year=year),
                delimiter=self.delimiter,
                header_row=self.header_row,
            )
        if self.parser == "xlsx_table_full_rows":
            return source_rows_from_xlsx_table(
                content,
                artifact,
                sheet_name=self._sheet_name(filename, year=year),
                header_row=self.header_row,
            )
        if self.parser == "json_table_full_rows":
            return source_rows_from_json_table(
                content,
                artifact,
                sheet_name=self._sheet_name(filename, year=year),
            )
        if self.parser == "statxplore_table_json_rows":
            return source_rows_from_statxplore_table(
                content,
                artifact,
                sheet_name=self._sheet_name(filename, year=year),
            )
        if self.parser == "json_stat_2_full_rows":
            return source_rows_from_json_stat_2(
                content,
                artifact,
                sheet_name=self._sheet_name(filename, year=year),
            )
        if self.parser == "census_acs_s0101_age_json_rows":
            return source_rows_from_census_acs_s0101_age_json(
                content,
                artifact,
                sheet_name=self._sheet_name(filename, year=year),
            )
        if self.parser == "census_acs_s2201_snap_json_rows":
            return source_rows_from_census_acs_s2201_snap_json(
                content,
                artifact,
                sheet_name=self._sheet_name(filename, year=year),
            )
        if self.parser == "census_b01001_female_age_json_rows":
            return source_rows_from_census_b01001_female_age_json(
                content,
                artifact,
                sheet_name=self._sheet_name(filename, year=year),
            )
        if self.parser == "cdc_vsrr_live_births_json_rows":
            return source_rows_from_cdc_vsrr_live_births_json(
                content,
                artifact,
                sheet_name=self._sheet_name(filename, year=year),
            )
        if self.parser == "ons_timeseries_json_years":
            return source_rows_from_ons_timeseries_json(
                content,
                artifact,
                sheet_name=self.sheet_name or "years",
            )
        if self.parser == "ees_permalink_table_html":
            return source_rows_from_ees_permalink_table_html(
                content,
                artifact,
                sheet_name=self.sheet_name or "table",
            )
        if self.parser == "kff_state_indicator_gdocs_html_rows":
            return source_rows_from_kff_state_indicator_gdocs_html(
                content,
                artifact,
                sheet_name=self.sheet_name or "indicator",
            )
        return []

    def build_source_cells(
        self,
        year: int,
        *,
        source_rows: list[SourceRow] | None = None,
    ) -> list[SourceCell]:
        """Parse the artifact for a year into source-cell records."""
        content, filename, source_url, raw_r2 = self._artifact_content(year)
        artifact = self._source_artifact_metadata(
            content,
            filename,
            source_url,
            raw_r2,
            year=year,
        )
        if self.parser == "xls_used_range":
            return source_cells_from_xls(content, artifact)
        if self.parser == "xlsx_used_range":
            return source_cells_from_xlsx(content, artifact, sheets=self.sheets)
        if self.parser == "zip_xlsx_used_range":
            member_content, member_name = self._archive_member_content(
                content,
                suffixes=(".xlsx",),
            )
            member_sha256 = hashlib.sha256(member_content).hexdigest()
            return source_cells_from_xlsx(
                member_content,
                replace(
                    artifact,
                    source_file=f"{filename}!{member_name}",
                    sha256=member_sha256,
                    size_bytes=len(member_content),
                    extraction_method=(
                        f"{artifact.extraction_method}; ZIP member {member_name} "
                        f"from outer SHA-256 {artifact.sha256}"
                    ),
                ),
            )
        if self.parser == "zip_xls_used_range":
            member_content, member_name = self._archive_member_content(
                content,
                suffixes=(".xls",),
            )
            member_sha256 = hashlib.sha256(member_content).hexdigest()
            return source_cells_from_xls(
                member_content,
                replace(
                    artifact,
                    source_file=f"{filename}!{member_name}",
                    sha256=member_sha256,
                    size_bytes=len(member_content),
                    extraction_method=(
                        f"{artifact.extraction_method}; ZIP member {member_name} "
                        f"from outer SHA-256 {artifact.sha256}"
                    ),
                ),
            )
        if self.parser == "ods_used_range":
            return source_cells_from_ods(content, artifact)
        if self.parser == "html_tables_and_text":
            return source_cells_from_html_tables_and_text(content, artifact)
        if self.parser == "pdf_text_numbers":
            return source_cells_from_pdf_text_numbers(content, artifact)
        if self.parser == "delimited_text_selected_rows":
            return source_cells_from_delimited_text(
                content,
                artifact,
                sheet_name=self._sheet_name(filename, year=year),
                selected_rows=tuple(
                    {
                        key: str(_render_value(value, year=year))
                        for key, value in row.items()
                    }
                    for row in self.selected_rows
                ),
                delimiter=self.delimiter,
            )
        if self.parser in {"delimited_text_full_rows", "zip_delimited_text_full_rows"}:
            delimited_content = self._delimited_content(content, filename)
            rows = (
                source_rows
                if source_rows is not None
                else source_rows_from_delimited_text(
                    delimited_content,
                    artifact,
                    sheet_name=self._sheet_name(filename, year=year),
                    delimiter=self.delimiter,
                    header_row=self.header_row,
                )
            )
            return source_cells_from_source_rows(
                rows,
                selected_rows=tuple(
                    {
                        key: str(_render_value(value, year=year))
                        for key, value in row.items()
                    }
                    for row in self.selected_rows
                ),
            )
        if self.parser == "xlsx_table_full_rows":
            rows = (
                source_rows
                if source_rows is not None
                else source_rows_from_xlsx_table(
                    content,
                    artifact,
                    sheet_name=self._sheet_name(filename, year=year),
                    header_row=self.header_row,
                )
            )
            return source_cells_from_source_rows(
                rows,
                selected_rows=tuple(
                    {
                        key: str(_render_value(value, year=year))
                        for key, value in row.items()
                    }
                    for row in self.selected_rows
                ),
            )
        if self.parser == "json_table_full_rows":
            rows = (
                source_rows
                if source_rows is not None
                else source_rows_from_json_table(
                    content,
                    artifact,
                    sheet_name=self._sheet_name(filename, year=year),
                )
            )
            return source_cells_from_source_rows(
                rows,
                selected_rows=tuple(
                    {
                        key: str(_render_value(value, year=year))
                        for key, value in row.items()
                    }
                    for row in self.selected_rows
                ),
            )
        if self.parser == "json_stat_2_full_rows":
            rows = (
                source_rows
                if source_rows is not None
                else source_rows_from_json_stat_2(
                    content,
                    artifact,
                    sheet_name=self._sheet_name(filename, year=year),
                )
            )
            return source_cells_from_source_rows(
                rows,
                selected_rows=tuple(
                    {
                        key: str(_render_value(value, year=year))
                        for key, value in row.items()
                    }
                    for row in self.selected_rows
                ),
            )
        if self.parser == "census_acs_s0101_age_json_rows":
            rows = (
                source_rows
                if source_rows is not None
                else source_rows_from_census_acs_s0101_age_json(
                    content,
                    artifact,
                    sheet_name=self._sheet_name(filename, year=year),
                )
            )
            return source_cells_from_source_rows(
                rows,
                selected_rows=tuple(
                    {
                        key: str(_render_value(value, year=year))
                        for key, value in row.items()
                    }
                    for row in self.selected_rows
                ),
            )
        if self.parser == "census_b01001_female_age_json_rows":
            rows = (
                source_rows
                if source_rows is not None
                else source_rows_from_census_b01001_female_age_json(
                    content,
                    artifact,
                    sheet_name=self._sheet_name(filename, year=year),
                )
            )
            return source_cells_from_source_rows(
                rows,
                selected_rows=tuple(
                    {
                        key: str(_render_value(value, year=year))
                        for key, value in row.items()
                    }
                    for row in self.selected_rows
                ),
            )
        if self.parser == "census_acs_s2201_snap_json_rows":
            rows = (
                source_rows
                if source_rows is not None
                else source_rows_from_census_acs_s2201_snap_json(
                    content,
                    artifact,
                    sheet_name=self._sheet_name(filename, year=year),
                )
            )
            return source_cells_from_source_rows(
                rows,
                selected_rows=tuple(
                    {
                        key: str(_render_value(value, year=year))
                        for key, value in row.items()
                    }
                    for row in self.selected_rows
                ),
            )
        if self.parser == "cdc_vsrr_live_births_json_rows":
            rows = (
                source_rows
                if source_rows is not None
                else source_rows_from_cdc_vsrr_live_births_json(
                    content,
                    artifact,
                    sheet_name=self._sheet_name(filename, year=year),
                )
            )
            return source_cells_from_source_rows(
                rows,
                selected_rows=tuple(
                    {
                        key: str(_render_value(value, year=year))
                        for key, value in row.items()
                    }
                    for row in self.selected_rows
                ),
            )
        if self.parser == "ons_timeseries_json_years":
            rows = (
                source_rows
                if source_rows is not None
                else source_rows_from_ons_timeseries_json(
                    content,
                    artifact,
                    sheet_name=self.sheet_name or "years",
                )
            )
            return source_cells_from_source_rows(
                rows,
                selected_rows=tuple(
                    {
                        key: str(_render_value(value, year=year))
                        for key, value in row.items()
                    }
                    for row in self.selected_rows
                ),
            )
        if self.parser == "ees_permalink_table_html":
            rows = (
                source_rows
                if source_rows is not None
                else source_rows_from_ees_permalink_table_html(
                    content,
                    artifact,
                    sheet_name=self.sheet_name or "table",
                )
            )
            return source_cells_from_source_rows(
                rows,
                selected_rows=tuple(
                    {
                        key: str(_render_value(value, year=year))
                        for key, value in row.items()
                    }
                    for row in self.selected_rows
                ),
            )
        if self.parser == "kff_state_indicator_gdocs_html_rows":
            rows = (
                source_rows
                if source_rows is not None
                else source_rows_from_kff_state_indicator_gdocs_html(
                    content,
                    artifact,
                    sheet_name=self.sheet_name or "indicator",
                )
            )
            return source_cells_from_source_rows(
                rows,
                selected_rows=tuple(
                    {
                        key: str(_render_value(value, year=year))
                        for key, value in row.items()
                    }
                    for row in self.selected_rows
                ),
            )
        if self.parser == "statxplore_table_json_rows":
            rows = (
                source_rows
                if source_rows is not None
                else source_rows_from_statxplore_table(
                    content,
                    artifact,
                    sheet_name=self.sheet_name or "api_response",
                )
            )
            return source_cells_from_source_rows(
                rows,
                selected_rows=tuple(
                    {
                        key: str(_render_value(value, year=year))
                        for key, value in row.items()
                    }
                    for row in self.selected_rows
                ),
            )
        raise ValueError(f"Unsupported source artifact parser: {self.parser}")

    def _source_artifact_metadata(
        self,
        content: bytes,
        filename: str,
        source_url: str,
        raw_r2: dict[str, str],
        *,
        year: int,
    ) -> SourceArtifactMetadata:
        return SourceArtifactMetadata(
            source_name=self.source_name,
            source_table=_render_string(self.source_table, year=year),
            source_file=filename,
            url=source_url,
            vintage=_render_string(self.vintage, year=year),
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            extracted_at=self.extracted_at,
            extraction_method=self.extraction_method,
            raw_r2_bucket=raw_r2.get("bucket"),
            raw_r2_key=raw_r2.get("key"),
            raw_r2_uri=raw_r2.get("uri"),
        )

    def manifest_payload(self) -> dict[str, Any]:
        """Load the artifact manifest this package spec points at."""
        manifest_path = files(self.resource_package).joinpath(
            self.resource_directory,
            self.manifest,
        )
        with manifest_path.open("r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}

    def assert_parseable_manifest(self) -> None:
        """Refuse to parse a manifest that registers a microdata release.

        Microdata registration is manifest-level identity: no source package
        parses a release, and no microdata row, cell, or fact enters Chronicle
        (``docs/adr-chronicle-raw-microdata-identity.md``).
        """
        manifest = self.manifest_payload()
        if is_microdata_release(manifest):
            raise MicrodataReleaseNotParseableError(
                f"{self.resource_directory}/{self.manifest} registers a "
                "microdata release. Registration is identity only: no source "
                "package parses a microdata release and no microdata rows, "
                "cells, or facts enter Chronicle."
            )

    def _artifact_content(
        self,
        year: int,
    ) -> tuple[bytes, str, str, dict[str, str]]:
        manifest = self.manifest_payload()
        if is_microdata_release(manifest):
            self.assert_parseable_manifest()
        spec = _year_mapping(manifest["files"], self.artifact_year or year)
        artifact_path = files(self.resource_package).joinpath(
            self.resource_directory,
            spec["filename"],
        )
        content = _read_source_artifact_content(artifact_path, spec)
        expected_sha = spec.get("sha256")
        if expected_sha:
            _validate_source_artifact_sha(
                content,
                expected_sha=str(expected_sha),
                filename=str(spec["filename"]),
            )
        storage = spec.get("storage") if isinstance(spec, dict) else None
        raw_r2 = storage.get("r2") if isinstance(storage, dict) else {}
        return content, spec["filename"], spec["source_url"], raw_r2 or {}

    def _sheet_name(self, filename: str, *, year: int) -> str:
        if self.sheet_name:
            return _render_string(self.sheet_name, year=year)
        if self.archive_member:
            return self.archive_member
        return Path(filename).stem

    def _delimited_content(self, content: bytes, filename: str) -> bytes:
        if self.parser != "zip_delimited_text_full_rows":
            return content
        member, _member_name = self._archive_member_content(
            content,
            suffixes=(".csv", ".txt", ".tsv"),
        )
        return member

    def _archive_member_content(
        self,
        content: bytes,
        *,
        suffixes: tuple[str, ...],
    ) -> tuple[bytes, str]:
        with ZipFile(BytesIO(content)) as archive:
            member = self.archive_member or _single_archive_member(
                archive,
                suffixes=suffixes,
            )
            return archive.read(member), member


@dataclass(frozen=True)
class SourcePackageIssue:
    """One source-package authoring issue."""

    code: str
    message: str
    record_set_id: str | None = None
    row_id: str | None = None
    measure_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable issue."""
        return {
            key: value
            for key, value in {
                "code": self.code,
                "message": self.message,
                "record_set_id": self.record_set_id,
                "row_id": self.row_id,
                "measure_id": self.measure_id,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class SourcePackageValidationReport:
    """Validation report for one declarative source package."""

    package_id: str | None
    package_path: str
    year: int
    counts: dict[str, int]
    errors: tuple[SourcePackageIssue, ...]
    warnings: tuple[SourcePackageIssue, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether the package has no validation errors."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": self.valid,
            "package_id": self.package_id,
            "package_path": self.package_path,
            "year": self.year,
            "counts": self.counts,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass(frozen=True)
class SourcePackageScaffoldReport:
    """Report for a scaffolded source package."""

    package_id: str
    source_id: str
    package_path: str
    source_package_path: str
    replaced: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "package_id": self.package_id,
            "source_id": self.source_id,
            "package_path": self.package_path,
            "source_package_path": self.source_package_path,
            "replaced": self.replaced,
        }


@dataclass(frozen=True)
class DeclarativeRecordSet:
    """One YAML record-set payload that compiles to source-record specs."""

    payload: dict[str, Any]

    def to_record_set_spec(self, year: int) -> SourceRecordSetSpec:
        """Compile this YAML payload into the core record-set spec."""
        provenance_class, survey_instrument = _provenance_fields_from_mapping(
            self.payload
        )
        rows = tuple(
            _row_from_mapping(row, year=year)
            for row in _required(self.payload, "rows", "record_set")
        )
        measures = tuple(
            _measure_from_mapping(measure, year=year)
            for measure in _required(self.payload, "measures", "record_set")
        )
        return SourceRecordSetSpec(
            record_set_id=_render_required_string(
                self.payload,
                "record_set_id",
                year=year,
            ),
            record_set_spec_id=_required(
                self.payload,
                "record_set_spec_id",
                "record_set",
            ),
            source_record_id_prefix=_render_required_string(
                self.payload,
                "source_record_id_prefix",
                year=year,
            ),
            sheet_name=_record_set_sheet_name_from_mapping(
                self.payload,
                year=year,
            ),
            period_type=_required(self.payload, "period_type", "record_set"),
            period=_record_set_period_from_mapping(self.payload, year=year),
            geography_id=_required(self.payload, "geography_id", "record_set"),
            geography_level=_required(
                self.payload,
                "geography_level",
                "record_set",
            ),
            geography_name=self.payload.get("geography_name"),
            geography_vintage=self.payload.get("geography_vintage"),
            entity=_required(self.payload, "entity", "record_set"),
            entity_role=self.payload.get("entity_role"),
            domain=_required(self.payload, "domain", "record_set"),
            groupby_dimension=_required(
                self.payload,
                "groupby_dimension",
                "record_set",
            ),
            rows=rows,
            measures=measures,
            provenance_class=provenance_class,
            survey_instrument=survey_instrument,
            shared_filters={
                key: _render_value(value, year=year)
                for key, value in self.payload.get("shared_filters", {}).items()
            },
            shared_constraints=tuple(
                _constraint_from_mapping(constraint, year=year)
                for constraint in self.payload.get("shared_constraints", ())
            ),
            assertion=_assertion_from_mapping(self.payload),
            period_coverage=_period_coverage_from_mapping(
                self.payload.get("period_coverage"),
                year=year,
            ),
        )


@dataclass(frozen=True)
class SourcePackage:
    """A declarative Chronicle source package."""

    package_id: str
    label: str | None
    artifact: SourceArtifactSpec
    record_sets: tuple[DeclarativeRecordSet, ...]
    package_path: Path

    def build_source_rows(self, year: int) -> list[SourceRow]:
        """Build full source rows for row-oriented artifacts."""
        return self.artifact.build_source_rows(year)

    def build_source_cells(
        self,
        year: int,
        *,
        source_rows: list[SourceRow] | None = None,
    ) -> list[SourceCell]:
        """Build whole-artifact source cells for this package."""
        return self.artifact.build_source_cells(year, source_rows=source_rows)

    def build_source_record_set_specs(
        self,
        year: int,
    ) -> list[SourceRecordSetSpec]:
        """Build compact record-set specs for this package."""
        return [record_set.to_record_set_spec(year) for record_set in self.record_sets]

    def build_source_regions(self, year: int):
        """Build source-region specs implied by the package record sets."""
        regions = []
        for spec in self.build_source_record_set_specs(year):
            regions.extend(source_regions_from_record_set_spec(spec))
        return regions

    def build_source_record_specs(self, year: int) -> list[SourceRecordSpec]:
        """Build atomic source-record specs for this package."""
        specs: list[SourceRecordSpec] = []
        for record_set in self.build_source_record_set_specs(year):
            specs.extend(compile_source_record_set_specs(record_set))
        return specs

    def build_source_records(
        self,
        year: int,
        *,
        cells: list[SourceCell] | None = None,
        source_rows: list[SourceRow] | None = None,
    ) -> list[SourceRecord]:
        """Resolve package source-record specs against source cells."""
        if cells is None:
            cells = self.build_source_cells(year, source_rows=source_rows)
        cells_by_sheet_address = build_cells_by_sheet_address(cells)
        return [
            resolve_source_record(
                cells,
                spec,
                cells_by_sheet_address=cells_by_sheet_address,
            )
            for spec in self.build_source_record_specs(year)
        ]

    def build_facts(
        self,
        year: int,
        *,
        cells: list[SourceCell] | None = None,
        source_rows: list[SourceRow] | None = None,
    ) -> list[AggregateFact]:
        """Build source-lineaged Chronicle aggregate facts."""
        if cells is None:
            cells = self.build_source_cells(year, source_rows=source_rows)
        source = _source_provenance_from_cells(cells)
        facts = []
        for record in self.build_source_records(
            year,
            cells=cells,
            source_rows=source_rows,
        ):
            fact = _fact_from_source_record(record, source)
            facts.append(replace(fact, label=build_label(fact)))
        return facts


def load_source_package(source: str | Path) -> SourcePackage:
    """Load a declarative source package from an alias, directory, or YAML file."""
    path = resolve_source_package_path(source)
    with path.open("r", encoding="utf-8") as file:
        payload = yaml.safe_load(file)
    schema_version = _required(payload, "schema_version", str(path))
    if schema_version != "ledger.source_package.v1":
        raise ValueError(f"Unsupported source package schema: {schema_version}")
    package_dir = path.parent
    return SourcePackage(
        package_id=_required(payload, "package_id", str(path)),
        label=payload.get("label"),
        artifact=_artifact_from_mapping(
            _required(payload, "artifact", str(path)),
        ),
        record_sets=_record_sets_from_mapping(payload, path),
        package_path=package_dir,
    )


def try_load_source_package(source: str | Path) -> SourcePackage | None:
    """Return a declarative package if the source reference resolves to one."""
    try:
        return load_source_package(source)
    except FileNotFoundError:
        return None


def validate_source_package(
    source: str | Path,
    *,
    year: int = 2023,
) -> SourcePackageValidationReport:
    """Validate declarative package structure before a full build-suite run."""
    package_path = _safe_package_path(source)
    errors: list[SourcePackageIssue] = []
    warnings: list[SourcePackageIssue] = []
    counts = {
        "record_set_count": 0,
        "row_count": 0,
        "measure_count": 0,
        "source_record_count": 0,
        "source_region_count": 0,
    }

    try:
        package = load_source_package(source)
    except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
        errors.append(
            SourcePackageIssue(
                code="source_package_load_failed",
                message=str(exc),
            )
        )
        return SourcePackageValidationReport(
            package_id=None,
            package_path=str(package_path),
            year=year,
            counts=counts,
            errors=tuple(errors),
        )

    try:
        package.artifact.assert_parseable_manifest()
    except MicrodataReleaseNotParseableError as exc:
        errors.append(
            SourcePackageIssue(
                code="microdata_release_not_parseable",
                message=str(exc),
            )
        )
        return SourcePackageValidationReport(
            package_id=package.package_id,
            package_path=str(package.package_path),
            year=year,
            counts=counts,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        errors.append(
            SourcePackageIssue(
                code="source_artifact_manifest_unreadable",
                message=str(exc),
            )
        )

    try:
        package.artifact._artifact_content(year)
    except (FileNotFoundError, KeyError, OSError, ValueError) as exc:
        errors.append(
            SourcePackageIssue(
                code="source_artifact_unavailable",
                message=str(exc),
            )
        )

    try:
        record_sets = package.build_source_record_set_specs(year)
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(
            SourcePackageIssue(
                code="record_set_compile_failed",
                message=str(exc),
            )
        )
        return SourcePackageValidationReport(
            package_id=package.package_id,
            package_path=str(package.package_path),
            year=year,
            counts=counts,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    counts["record_set_count"] = len(record_sets)
    record_set_ids: dict[str, list[int]] = {}
    source_record_ids: dict[str, list[int]] = {}
    for record_set_index, record_set in enumerate(record_sets):
        record_set_ids.setdefault(record_set.record_set_id, []).append(record_set_index)
        counts["row_count"] += len(record_set.rows)
        counts["measure_count"] += len(record_set.measures)
        errors.extend(_validate_record_set_authoring(record_set))
        try:
            counts["source_region_count"] += len(
                source_regions_from_record_set_spec(record_set)
            )
        except ValueError as exc:
            errors.append(
                SourcePackageIssue(
                    code="source_region_compile_failed",
                    message=str(exc),
                    record_set_id=record_set.record_set_id,
                )
            )
        try:
            specs = compile_source_record_set_specs(record_set)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(
                SourcePackageIssue(
                    code="source_record_compile_failed",
                    message=str(exc),
                    record_set_id=record_set.record_set_id,
                )
            )
            continue
        for spec_index, spec in enumerate(specs):
            source_record_ids.setdefault(spec.source_record_id, []).append(spec_index)
            counts["source_record_count"] += 1

    for record_set_id, indices in record_set_ids.items():
        if len(indices) > 1:
            errors.append(
                SourcePackageIssue(
                    code="duplicate_record_set_id",
                    message=f"Duplicate record-set ID appears at indices {indices}.",
                    record_set_id=record_set_id,
                )
            )
    for source_record_id, indices in source_record_ids.items():
        if len(indices) > 1:
            errors.append(
                SourcePackageIssue(
                    code="duplicate_source_record_id",
                    message=(
                        "Duplicate compiled source-record ID appears at "
                        f"indices {indices}."
                    ),
                    record_set_id=source_record_id.rsplit(".", 2)[0],
                )
            )

    return SourcePackageValidationReport(
        package_id=package.package_id,
        package_path=str(package.package_path),
        year=year,
        counts=counts,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def scaffold_source_package(
    output_dir: str | Path,
    *,
    source_id: str,
    package_id: str,
    source_table: str | None = None,
    resource_package: str = "db",
    resource_directory: str | None = None,
    manifest: str = "manifest.yaml",
    replace_existing: bool = False,
) -> SourcePackageScaffoldReport:
    """Write a starter declarative source package."""
    package_path = Path(output_dir)
    source_package_path = package_path / SOURCE_PACKAGE_FILENAME
    replaced = source_package_path.exists()
    if replaced and not replace_existing:
        raise FileExistsError(f"Source package already exists: {source_package_path}")
    package_path.mkdir(parents=True, exist_ok=True)
    source_package_path.write_text(
        _scaffold_template(
            source_id=source_id,
            package_id=package_id,
            source_table=source_table or "TODO source table title",
            resource_package=resource_package,
            resource_directory=(resource_directory or f"data/{source_id}/TODO_table"),
            manifest=manifest,
        ),
        encoding="utf-8",
    )
    return SourcePackageScaffoldReport(
        package_id=package_id,
        source_id=source_id,
        package_path=str(package_path),
        source_package_path=str(source_package_path),
        replaced=replaced,
    )


def resolve_source_package_path(source: str | Path) -> Path:
    """Resolve a package alias, directory, or YAML file to source_package.yaml."""
    source_path = Path(source)
    if source_path.exists():
        if source_path.is_dir():
            return source_path / SOURCE_PACKAGE_FILENAME
        return source_path
    alias = SOURCE_PACKAGE_ALIASES.get(str(source))
    if alias is None:
        raise FileNotFoundError(f"Source package not found: {source}")
    repo_path = _repo_root() / "packages" / alias / SOURCE_PACKAGE_FILENAME
    if repo_path.exists():
        return repo_path
    try:
        resource_path = files(SOURCE_PACKAGE_RESOURCE_PACKAGE).joinpath(
            alias,
            SOURCE_PACKAGE_FILENAME,
        )
        with resource_path.open("rb"):
            pass
        return Path(str(resource_path))
    except (FileNotFoundError, ModuleNotFoundError):
        raise FileNotFoundError(f"Source package not found: {source}") from None


class SourcePackageAliasDriftError(RuntimeError):
    """Raised when the alias map and ``packages/*`` layout have drifted.

    Drift means a bundle built from the alias map would be silently
    incomplete (an on-disk package with no alias entry is dropped) or would
    reference a directory that no longer exists (a stale alias). Both are
    reported here rather than silently skipped (see PolicyEngine/chronicle#78).
    """


def source_package_root() -> Path:
    """Return the repository ``packages/`` directory (the authoring surface)."""
    return SOURCE_PACKAGE_ROOT


def discover_source_package_dirs(root: Path | None = None) -> list[Path]:
    """Discover ``<source>/<package>`` directories under a packages root.

    Returns repo-relative package paths (e.g. ``usda_snap/fy69_to_current``),
    matching the shape of the alias-map values, sorted for determinism. This
    is the authoring surface a complete bundle must cover.
    """
    packages_root = root if root is not None else source_package_root()
    if not packages_root.exists():
        return []
    discovered = {
        manifest.parent.relative_to(packages_root)
        for manifest in packages_root.glob(f"*/*/{SOURCE_PACKAGE_FILENAME}")
    }
    return sorted(discovered, key=str)


def find_alias_map_drift(
    root: Path | None = None,
    aliases: dict[str, Path] | None = None,
) -> tuple[list[Path], list[Path]]:
    """Report alias/directory drift as ``(missing_dirs, unmapped_dirs)``.

    ``missing_dirs`` are alias targets with no directory on disk (stale
    aliases). ``unmapped_dirs`` are on-disk package directories with no alias
    entry (silently-dropped packages). Both are returned as repo-relative
    package paths, sorted.
    """
    packages_root = root if root is not None else source_package_root()
    alias_map = aliases if aliases is not None else SOURCE_PACKAGE_ALIASES

    discovered = set(discover_source_package_dirs(root=packages_root))
    alias_targets = {Path(target) for target in alias_map.values()}

    missing_dirs = sorted(
        (
            target
            for target in alias_targets
            if not (packages_root / target / SOURCE_PACKAGE_FILENAME).exists()
        ),
        key=str,
    )
    unmapped_dirs = sorted(discovered - alias_targets, key=str)
    return missing_dirs, unmapped_dirs


def assert_alias_map_covers_packages(
    root: Path | None = None,
    aliases: dict[str, Path] | None = None,
) -> None:
    """Fail loudly if the alias map and ``packages/*`` layout have drifted.

    Raises :class:`SourcePackageAliasDriftError` naming every stale alias
    target and every unmapped package directory, so a drifted map cannot
    silently produce a partial bundle.
    """
    missing_dirs, unmapped_dirs = find_alias_map_drift(root=root, aliases=aliases)
    if not missing_dirs and not unmapped_dirs:
        return

    problems: list[str] = []
    if unmapped_dirs:
        listing = ", ".join(str(path) for path in unmapped_dirs)
        problems.append(
            "packages/* directories with no SOURCE_PACKAGE_ALIASES entry "
            f"(would be silently dropped from bundles): {listing}"
        )
    if missing_dirs:
        listing = ", ".join(str(path) for path in missing_dirs)
        problems.append(
            "SOURCE_PACKAGE_ALIASES entries pointing at missing directories "
            f"(stale aliases): {listing}"
        )
    raise SourcePackageAliasDriftError(
        "Source-package alias map has drifted from the packages/* layout. "
        + " ".join(problems)
        + " Re-sync SOURCE_PACKAGE_ALIASES with the packages/* directories."
    )


def _validate_record_set_authoring(
    record_set: SourceRecordSetSpec,
) -> list[SourcePackageIssue]:
    errors: list[SourcePackageIssue] = []
    row_ids: dict[str, list[int]] = {}
    row_ordinals: dict[int, list[int]] = {}
    measure_ids: dict[str, list[int]] = {}
    measure_ordinals: dict[int, list[int]] = {}
    for index, row in enumerate(record_set.rows):
        row_ids.setdefault(row.value_id, []).append(index)
        row_ordinals.setdefault(row.ordinal, []).append(index)
        if row.column is not None and not EXCEL_COLUMN_RE.match(row.column):
            errors.append(
                SourcePackageIssue(
                    code="malformed_row_column",
                    message=("Row column must be an Excel column name like B or AA."),
                    record_set_id=record_set.record_set_id,
                    row_id=row.value_id,
                )
            )
        if _row_needs_constraints(row) and not row.constraints:
            errors.append(
                SourcePackageIssue(
                    code="missing_row_constraints",
                    message=(
                        "Detail row has semantic filters but no first-class "
                        "constraints."
                    ),
                    record_set_id=record_set.record_set_id,
                    row_id=row.value_id,
                )
            )
        errors.extend(_missing_filter_bound_constraints(record_set, row))

    for index, measure in enumerate(record_set.measures):
        measure_ids.setdefault(measure.measure_id, []).append(index)
        measure_ordinals.setdefault(measure.ordinal, []).append(index)
        if not EXCEL_COLUMN_RE.match(measure.column):
            errors.append(
                SourcePackageIssue(
                    code="malformed_measure_column",
                    message=(
                        "Measure column must be an Excel column name like B or AA."
                    ),
                    record_set_id=record_set.record_set_id,
                    measure_id=measure.measure_id,
                )
            )
        if measure.aggregation not in ALLOWED_AGGREGATIONS:
            errors.append(
                SourcePackageIssue(
                    code="malformed_measure_aggregation",
                    message=(
                        "Measure aggregation must be one of "
                        f"{sorted(ALLOWED_AGGREGATIONS)!r}."
                    ),
                    record_set_id=record_set.record_set_id,
                    measure_id=measure.measure_id,
                )
            )
        if measure.concept_relation == "exact" and not (
            measure.concept_evidence_url or measure.concept_evidence_notes
        ):
            errors.append(
                SourcePackageIssue(
                    code="missing_concept_evidence",
                    message="Exact concept alignments need evidence notes or a URL.",
                    record_set_id=record_set.record_set_id,
                    measure_id=measure.measure_id,
                )
            )
        if measure.source_concept and not measure.concept_relation:
            errors.append(
                SourcePackageIssue(
                    code="missing_concept_relation",
                    message="source_concept requires concept_relation.",
                    record_set_id=record_set.record_set_id,
                    measure_id=measure.measure_id,
                )
            )
        if measure.concept_relation and not measure.source_concept:
            errors.append(
                SourcePackageIssue(
                    code="missing_source_concept",
                    message="concept_relation requires source_concept.",
                    record_set_id=record_set.record_set_id,
                    measure_id=measure.measure_id,
                )
            )

    errors.extend(
        _duplicate_issues(
            row_ids,
            code="duplicate_row_id",
            message="Duplicate row ID appears within a record set.",
            record_set_id=record_set.record_set_id,
            row=True,
        )
    )
    errors.extend(
        _duplicate_ordinal_issues(
            row_ordinals,
            code="duplicate_row_ordinal",
            message="Duplicate row ordinal appears within a record set.",
            record_set_id=record_set.record_set_id,
            row=True,
        )
    )
    errors.extend(
        _duplicate_issues(
            measure_ids,
            code="duplicate_measure_id",
            message="Duplicate measure ID appears within a record set.",
            record_set_id=record_set.record_set_id,
            row=False,
        )
    )
    errors.extend(
        _duplicate_ordinal_issues(
            measure_ordinals,
            code="duplicate_measure_ordinal",
            message="Duplicate measure ordinal appears within a record set.",
            record_set_id=record_set.record_set_id,
            row=False,
        )
    )
    return errors


def _row_needs_constraints(row: SourceRecordSetRow) -> bool:
    if row.table_record_kind == "total":
        return False
    return any(
        key not in {"filing_status", "income_range"}
        for key, value in row.filters.items()
        if value not in (None, "all")
    )


def _missing_filter_bound_constraints(
    record_set: SourceRecordSetSpec,
    row: SourceRecordSetRow,
) -> list[SourcePackageIssue]:
    errors: list[SourcePackageIssue] = []
    for suffix, operator in (("_lower_usd", ">="), ("_upper_usd", "<")):
        for filter_key, value in row.filters.items():
            if not filter_key.endswith(suffix):
                continue
            if not any(
                constraint.operator == operator and constraint.value == value
                for constraint in row.constraints
            ):
                errors.append(
                    SourcePackageIssue(
                        code="missing_bound_constraint",
                        message=(
                            f"Filter {filter_key}={value!r} needs a matching "
                            f"{operator} constraint."
                        ),
                        record_set_id=record_set.record_set_id,
                        row_id=row.value_id,
                    )
                )
    return errors


def _duplicate_issues(
    indices_by_value: dict[str, list[int]],
    *,
    code: str,
    message: str,
    record_set_id: str,
    row: bool,
) -> list[SourcePackageIssue]:
    issues = []
    for value, indices in indices_by_value.items():
        if len(indices) <= 1:
            continue
        issues.append(
            SourcePackageIssue(
                code=code,
                message=f"{message} Indices: {indices}.",
                record_set_id=record_set_id,
                row_id=value if row else None,
                measure_id=None if row else value,
            )
        )
    return issues


def _duplicate_ordinal_issues(
    indices_by_value: dict[int, list[int]],
    *,
    code: str,
    message: str,
    record_set_id: str,
    row: bool,
) -> list[SourcePackageIssue]:
    issues = []
    for value, indices in indices_by_value.items():
        if len(indices) <= 1:
            continue
        issue = SourcePackageIssue(
            code=code,
            message=f"{message} Ordinal: {value}; indices: {indices}.",
            record_set_id=record_set_id,
            row_id=str(value) if row else None,
            measure_id=None if row else str(value),
        )
        issues.append(issue)
    return issues


def _artifact_from_mapping(payload: dict[str, Any]) -> SourceArtifactSpec:
    return SourceArtifactSpec(
        source_name=_required(payload, "source_name", "artifact"),
        source_table=_required(payload, "source_table", "artifact"),
        resource_package=_required(payload, "resource_package", "artifact"),
        resource_directory=_required(payload, "resource_directory", "artifact"),
        manifest=_required(payload, "manifest", "artifact"),
        vintage=_required(payload, "vintage", "artifact"),
        extracted_at=_required(payload, "extracted_at", "artifact"),
        extraction_method=_required(payload, "extraction_method", "artifact"),
        parser=payload.get("parser", "xls_used_range"),
        sheet_name=payload.get("sheet_name"),
        archive_member=payload.get("archive_member"),
        artifact_year=(
            int(payload["artifact_year"])
            if payload.get("artifact_year") is not None
            else None
        ),
        delimiter=payload.get("delimiter", ","),
        header_row=int(payload.get("header_row", 1)),
        selected_rows=tuple(payload.get("selected_rows", ())),
        sheets=tuple(payload.get("sheets", ())),
    )


def _row_from_mapping(payload: dict[str, Any], *, year: int) -> SourceRecordSetRow:
    row_column = _optional_excel_column_from_mapping(payload, "column", year=year)
    return SourceRecordSetRow(
        value_id=_required(payload, "value_id", "row"),
        label=_required(payload, "label", "row"),
        ordinal=int(_required(payload, "ordinal", "row")),
        row_number=int(_required(payload, "row_number", "row")),
        row_end_number=(
            int(payload["row_end_number"])
            if payload.get("row_end_number") is not None
            else None
        ),
        column=row_column,
        source_column_id=(
            str(_render_value(payload["source_column_id"], year=year))
            if payload.get("source_column_id") is not None
            else None
        ),
        expected_column_header_row=(
            int(payload["expected_column_header_row"])
            if payload.get("expected_column_header_row") is not None
            else None
        ),
        expected_column_header=_render_value(
            _year_mapping(payload["expected_column_header_by_year"], year)
            if "expected_column_header_by_year" in payload
            else payload["expected_column_header"],
            year=year,
        )
        if "expected_column_header" in payload
        or "expected_column_header_by_year" in payload
        else None,
        geography_id=payload.get("geography_id"),
        geography_level=payload.get("geography_level"),
        geography_name=payload.get("geography_name"),
        geography_vintage=payload.get("geography_vintage"),
        filters={
            key: _render_value(value, year=year)
            for key, value in payload.get("filters", {}).items()
        },
        constraints=tuple(
            _constraint_from_mapping(constraint, year=year)
            for constraint in payload.get("constraints", ())
        ),
        value_scale=_render_value(payload.get("value_scale", 1), year=year),
        source_row_id=payload.get("source_row_id"),
        table_record_kind=payload.get("table_record_kind", "detail"),
        expected_row_header=_render_value(
            payload["expected_row_header"],
            year=year,
        )
        if "expected_row_header" in payload
        else None,
        expected_row_header_column=payload.get("expected_row_header_column"),
        guard_cells=tuple(
            _row_guard_from_mapping(guard, year=year)
            for guard in payload.get("guard_cells", ())
        ),
        range_label_guards=tuple(
            _range_label_guard_from_mapping(guard, year=year)
            for guard in payload.get("range_label_guards", ())
        ),
    )


def _row_guard_from_mapping(
    payload: dict[str, Any],
    *,
    year: int,
) -> SourceRecordSetRowGuard:
    unknown_keys = set(payload) - {"column", "expected_value", "row", "label"}
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise ValueError(f"Unknown row_guard field(s): {unknown}")
    column = str(_required(payload, "column", "row_guard")).upper()
    if EXCEL_COLUMN_RE.fullmatch(column) is None:
        raise ValueError(f"Malformed row_guard column: {column!r}")
    return SourceRecordSetRowGuard(
        column=column,
        expected_value=_render_value(
            _required(payload, "expected_value", "row_guard"),
            year=year,
        ),
        row=_render_value(payload.get("row", "start"), year=year),
        label=payload.get("label", "row guard"),
    )


def _range_label_guard_from_mapping(
    payload: dict[str, Any],
    *,
    year: int,
) -> SourceRecordSetRangeLabelGuard:
    unknown_keys = set(payload) - {"column", "expected_values", "label"}
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise ValueError(f"Unknown range_label_guard field(s): {unknown}")
    column = str(_required(payload, "column", "range_label_guard")).upper()
    if EXCEL_COLUMN_RE.fullmatch(column) is None:
        raise ValueError(f"Malformed range_label_guard column: {column!r}")
    expected_values = _range_label_values_from_payload(
        _required(payload, "expected_values", "range_label_guard"),
        year=year,
    )
    _validate_range_label_values(expected_values)
    return SourceRecordSetRangeLabelGuard(
        column=column,
        expected_values=expected_values,
        label=payload.get("label", "range label sequence"),
    )


def _range_label_values_from_payload(
    payload: Any,
    *,
    year: int,
) -> tuple[Any, ...]:
    if isinstance(payload, list):
        return tuple(_render_value(value, year=year) for value in payload)
    if not isinstance(payload, dict):
        raise ValueError("range_label_guard expected_values must be a list or mapping")
    unknown_keys = set(payload) - {"integer_range", "parts"}
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise ValueError(
            f"Unknown range_label_guard expected_values field(s): {unknown}"
        )
    compact_forms = {"integer_range", "parts"} & set(payload)
    if len(compact_forms) > 1:
        forms = ", ".join(sorted(compact_forms))
        raise ValueError(
            "range_label_guard expected_values must use exactly one compact "
            f"form; got {forms}"
        )
    if "integer_range" in payload:
        return _integer_range_values(payload["integer_range"], year=year)
    if "parts" in payload:
        values = []
        for part in payload["parts"]:
            values.extend(_range_label_values_from_payload(part, year=year))
        return tuple(values)
    raise ValueError("range_label_guard expected_values mapping is empty")


def _validate_range_label_values(values: tuple[Any, ...]) -> None:
    if any(value is None for value in values):
        raise ValueError("range_label_guard expected_values must not contain null")


def _integer_range_values(payload: dict[str, Any], *, year: int) -> tuple[Any, ...]:
    unknown_keys = set(payload) - {"start", "end", "final_value", "extra_values"}
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise ValueError(f"Unknown integer_range field(s): {unknown}")
    start = int(_render_value(_required(payload, "start", "integer_range"), year=year))
    end = int(_render_value(_required(payload, "end", "integer_range"), year=year))
    if end < start:
        raise ValueError("integer_range end must be greater than or equal to start")
    values: list[Any] = list(range(start, end + 1))
    if "final_value" in payload:
        values[-1] = _render_value(payload["final_value"], year=year)
    if "extra_values" in payload:
        extra_values = payload["extra_values"]
        if not isinstance(extra_values, list):
            extra_values = [extra_values]
        values.extend(_render_value(value, year=year) for value in extra_values)
    return tuple(values)


def _measure_from_mapping(
    payload: dict[str, Any],
    *,
    year: int,
) -> SourceRecordSetMeasure:
    return SourceRecordSetMeasure(
        measure_id=_required(payload, "measure_id", "measure"),
        label=_required(payload, "label", "measure"),
        ordinal=int(_required(payload, "ordinal", "measure")),
        column=_measure_column_from_mapping(payload, year=year),
        concept=_required(payload, "concept", "measure"),
        unit=_required(payload, "unit", "measure"),
        aggregation=_required(payload, "aggregation", "measure"),
        value_scale=_render_value(payload.get("value_scale", 1), year=year),
        divisor_column=payload.get("divisor_column"),
        round_to=(
            _render_value(payload["round_to"], year=year)
            if "round_to" in payload
            else None
        ),
        source_column_id=(
            str(_render_value(payload["source_column_id"], year=year))
            if payload.get("source_column_id") is not None
            else None
        ),
        source_column_dimensions={
            str(key): _render_value(value, year=year)
            for key, value in payload.get("source_column_dimensions", {}).items()
        },
        expected_cell_type=payload.get("expected_cell_type", "number"),
        expected_column_header_row=(
            int(payload["expected_column_header_row"])
            if payload.get("expected_column_header_row") is not None
            else None
        ),
        expected_column_header=_render_value(
            _year_mapping(payload["expected_column_header_by_year"], year)
            if "expected_column_header_by_year" in payload
            else payload["expected_column_header"],
            year=year,
        )
        if "expected_column_header" in payload
        or "expected_column_header_by_year" in payload
        else None,
        source_concept=payload.get("source_concept"),
        concept_relation=payload.get("concept_relation"),
        concept_authority=payload.get("concept_authority"),
        concept_evidence_url=payload.get("concept_evidence_url"),
        concept_evidence_notes=_optional_rendered_string(
            payload.get("concept_evidence_notes"),
            year=year,
        ),
        legal_vintage=(
            _render_string(payload["legal_vintage"], year=year)
            if payload.get("legal_vintage")
            else None
        ),
        filters={
            key: _render_value(value, year=year)
            for key, value in payload.get("filters", {}).items()
        },
        constraints=tuple(
            _constraint_from_mapping(constraint, year=year)
            for constraint in payload.get("constraints", ())
        ),
    )


def _measure_column_from_mapping(payload: dict[str, Any], *, year: int) -> str:
    if "column_by_year" in payload:
        return str(_year_mapping(payload["column_by_year"], year))
    return str(_required(payload, "column", "measure"))


def _optional_excel_column_from_mapping(
    payload: dict[str, Any],
    field: str,
    *,
    year: int,
) -> str | None:
    by_year_field = f"{field}_by_year"
    if by_year_field in payload:
        value = str(_year_mapping(payload[by_year_field], year)).upper()
    elif payload.get(field) is not None:
        value = str(_render_value(payload[field], year=year)).upper()
    else:
        return None
    if EXCEL_COLUMN_RE.fullmatch(value) is None:
        raise ValueError(f"Malformed row {field}: {value!r}")
    return value


def _record_set_period_from_mapping(
    payload: dict[str, Any],
    *,
    year: int,
) -> int | str:
    if "period_by_year" in payload:
        return _period_value(_year_mapping(payload["period_by_year"], year))
    return _period_value(
        _render_value(
            _required(payload, "period", "record_set"),
            year=year,
        )
    )


def _record_set_sheet_name_from_mapping(
    payload: dict[str, Any],
    *,
    year: int,
) -> str:
    if "sheet_name_by_year" in payload:
        return str(_year_mapping(payload["sheet_name_by_year"], year))
    return _render_string(
        str(_required(payload, "sheet_name", "record_set")), year=year
    )


def _period_value(value: Any) -> int | str:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
        return stripped
    raise ValueError(f"Unsupported period value: {value!r}")


def _constraint_from_mapping(
    payload: dict[str, Any],
    *,
    year: int,
) -> AggregateConstraint:
    return AggregateConstraint(
        variable=_required(payload, "variable", "constraint"),
        operator=_required(payload, "operator", "constraint"),
        value=_render_value(_required(payload, "value", "constraint"), year=year),
        unit=payload.get("unit"),
        role=payload.get("role", "filter"),
        label=payload.get("label"),
    )


def _fact_from_source_record(
    record: SourceRecord,
    source: SourceProvenance,
) -> AggregateFact:
    spec = record.spec
    return AggregateFact(
        value=record.value,
        period=PeriodDimension(type=spec.period_type, value=spec.period),
        geography=GeographyDimension(
            level=spec.geography_level,
            id=spec.geography_id,
            vintage=spec.geography_vintage,
            name=spec.geography_name,
        ),
        entity=EntityDimension(name=spec.entity, role=spec.entity_role),
        measure=Measure(
            concept=spec.concept,
            unit=spec.unit,
            source_concept=spec.source_concept,
            concept_relation=spec.concept_relation,
            concept_authority=spec.concept_authority,
            concept_evidence_url=spec.concept_evidence_url,
            concept_evidence_notes=spec.concept_evidence_notes,
            legal_vintage=spec.legal_vintage,
        ),
        aggregation=Aggregation(method=spec.aggregation),
        source=source,
        provenance_class=spec.provenance_class,
        survey_instrument=spec.survey_instrument,
        filters=spec.filters,
        domain=spec.domain,
        source_record_id=record.source_record_id,
        source_cell_keys=record.source_cell_keys,
        source_row_keys=record.source_row_keys,
        constraints=spec.constraints,
        layout=spec.layout,
        assertion=spec.assertion,
        period_coverage=spec.period_coverage,
    )


def _source_provenance_from_cells(cells: list[SourceCell]) -> SourceProvenance:
    artifact = cells[0].artifact
    return SourceProvenance(
        source_name=artifact.source_name,
        source_table=artifact.source_table,
        source_file=artifact.source_file,
        url=artifact.url,
        vintage=artifact.vintage,
        extracted_at=artifact.extracted_at,
        extraction_method="CellSelectorSpec and SourceRecordSpec resolved from cells",
        method_notes=(
            "Each fact carries source_record_id, source_cell_keys, and "
            "source_row_keys when available."
        ),
        source_sha256=artifact.sha256,
        source_size_bytes=artifact.size_bytes,
        raw_r2_bucket=artifact.raw_r2_bucket,
        raw_r2_key=artifact.raw_r2_key,
        raw_r2_uri=artifact.raw_r2_uri,
    )


def _assertion_from_mapping(payload: dict[str, Any]) -> str:
    assertion = payload.get("assertion", DEFAULT_ASSERTION)
    if assertion not in ALLOWED_ASSERTIONS:
        raise ValueError(
            f"record_set assertion must be one of {sorted(ALLOWED_ASSERTIONS)}, "
            f"got {assertion!r}. PolicyEngine-computed values are not Chronicle "
            "facts; only publisher observations and publisher projections are."
        )
    return assertion


def _provenance_fields_from_mapping(
    payload: dict[str, Any],
) -> tuple[str, str | None]:
    if not isinstance(payload, dict):
        raise TypeError("record_set must be a mapping.")
    provenance_class = _required(payload, "provenance_class", "record_set")
    if type(provenance_class) is not str:
        raise TypeError(
            "record_set provenance_class must be a string, "
            f"got {type(provenance_class).__name__}."
        )
    if provenance_class not in ALLOWED_PROVENANCE_CLASSES:
        raise ValueError(
            "record_set provenance_class must be one of "
            f"{sorted(ALLOWED_PROVENANCE_CLASSES)}, got {provenance_class!r}."
        )

    has_survey_instrument = "survey_instrument" in payload
    survey_instrument = payload.get("survey_instrument")
    if provenance_class == "survey_aggregate":
        if not has_survey_instrument:
            raise KeyError("Missing required record_set field: survey_instrument")
        if type(survey_instrument) is not str or not survey_instrument.strip():
            raise TypeError(
                "record_set survey_instrument must be a non-empty string for "
                "survey_aggregate provenance."
            )
        return provenance_class, survey_instrument

    if has_survey_instrument:
        raise ValueError(
            "record_set survey_instrument is forbidden unless provenance_class "
            "is 'survey_aggregate'."
        )
    return provenance_class, None


def _record_sets_from_mapping(
    payload: dict[str, Any],
    path: Path,
) -> tuple[DeclarativeRecordSet, ...]:
    record_sets = _required(payload, "record_sets", str(path))
    if not isinstance(record_sets, list):
        raise TypeError("source package record_sets must be a list.")
    loaded = []
    for index, record_set in enumerate(record_sets):
        try:
            _provenance_fields_from_mapping(record_set)
        except (KeyError, TypeError, ValueError) as exc:
            raise type(exc)(f"record_sets[{index}]: {exc}") from exc
        loaded.append(DeclarativeRecordSet(record_set))
    return tuple(loaded)


def _period_coverage_from_mapping(
    payload: Any,
    *,
    year: int,
) -> PeriodCoverage | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("record_set period_coverage must be a mapping.")
    allowed_keys = {
        "start_date",
        "end_date",
        "basis",
        "source_period_label",
        "accounting_basis",
        "notes",
    }
    unknown = sorted(set(payload) - allowed_keys)
    if unknown:
        raise ValueError(f"record_set period_coverage has unknown keys: {unknown}.")
    return PeriodCoverage(
        **{
            key: _optional_rendered_string(payload.get(key), year=year)
            for key in allowed_keys
        }
    )


def _render_required_string(
    payload: dict[str, Any],
    key: str,
    *,
    year: int,
) -> str:
    return _render_string(_required(payload, key, "record_set"), year=year)


def _render_string(value: str, *, year: int) -> str:
    # Non-integer keys (release/vintage labels used as ``files:`` or
    # ``column_by_year`` keys) are labels, not years: there is no year to
    # interpolate and ``year + 1`` is undefined, so skip ``{year}`` templating
    # rather than crashing (see PolicyEngine/chronicle#79).
    if not isinstance(year, int):
        return value
    return value.format(year=year, filing_year=year + 1)


def _optional_rendered_string(value: Any, *, year: int) -> str | None:
    if value is None:
        return None
    return _render_string(str(value), year=year).strip()


def _render_value(value: Any, *, year: int) -> Any:
    if isinstance(value, str):
        rendered = _render_string(value, year=year)
        if rendered.isdigit() and not (len(rendered) > 1 and rendered.startswith("0")):
            return int(rendered)
        return rendered
    if isinstance(value, list):
        return [_render_value(item, year=year) for item in value]
    if isinstance(value, dict):
        return {key: _render_value(item, year=year) for key, item in value.items()}
    return value


def _required(payload: dict[str, Any], key: str, context: str) -> Any:
    if key not in payload:
        raise ValueError(f"Missing required {context} field: {key}")
    return payload[key]


def _year_mapping(files_by_year: dict[Any, Any], year: int) -> dict[str, str]:
    if year in files_by_year:
        return _single_year_spec(files_by_year[year], year)
    if str(year) in files_by_year:
        return _single_year_spec(files_by_year[str(year)], year)
    raise ValueError(f"No source artifact for year {year}")


def _single_year_spec(spec: Any, year: int) -> dict[str, str]:
    """Return one file spec, refusing the multi-file microdata-release shape.

    Only a ``kind: microdata_release`` manifest may list several files under one
    vintage, and no source package parses one of those, so a list here is a
    malformed publisher-table manifest rather than something to index into.
    """
    if isinstance(spec, list):
        raise ValueError(
            f"Source artifact for year {year} is a list of {len(spec)} entries. "
            "Only a kind: microdata_release manifest may list several files "
            "under one vintage, and no source package parses one."
        )
    return spec


def _read_source_artifact_content(
    artifact_path: Any,
    spec: dict[str, Any],
) -> bytes:
    """Read a source artifact from package data, cache, or explicit fetch."""
    try:
        return artifact_path.read_bytes()
    except FileNotFoundError:
        pass

    cache_path = _source_artifact_cache_path(spec)
    if cache_path.exists():
        return cache_path.read_bytes()

    if not env_flag(SOURCE_ARTIFACT_FETCH_ENV):
        raise FileNotFoundError(
            f"Source artifact {spec['filename']} is not packaged and was not "
            f"found in {cache_path}. Set {SOURCE_ARTIFACT_FETCH_ENV}=1 to fetch "
            "and cache missing source artifacts."
        )

    content = _fetch_source_artifact_content(str(spec["source_url"]))
    expected_sha = spec.get("sha256")
    if expected_sha:
        _validate_source_artifact_sha(
            content,
            expected_sha=str(expected_sha),
            filename=str(spec["filename"]),
        )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(content)
    return content


def _source_artifact_cache_path(spec: dict[str, Any]) -> Path:
    cache_root = Path(
        env_value(
            SOURCE_ARTIFACT_CACHE_ENV,
            default=DEFAULT_SOURCE_ARTIFACT_CACHE_DIR,
        )
    )
    source_identity = str(spec.get("source_url", spec["filename"])).encode()
    identifier = str(spec.get("sha256") or hashlib.sha256(source_identity).hexdigest())
    return cache_root / identifier / str(spec["filename"])


def _fetch_source_artifact_content(source_url: str) -> bytes:
    parsed = urlparse(source_url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).read_bytes()
    if parsed.scheme == "":
        return Path(source_url).read_bytes()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported source artifact URL scheme: {parsed.scheme}")
    response = httpx.get(source_url, follow_redirects=True, timeout=60)
    response.raise_for_status()
    return response.content


def _validate_source_artifact_sha(
    content: bytes,
    *,
    expected_sha: str,
    filename: str,
) -> None:
    actual_sha = hashlib.sha256(content).hexdigest()
    if actual_sha != expected_sha:
        raise ValueError(
            f"Source artifact checksum mismatch for {filename}: "
            f"expected {expected_sha}, got {actual_sha}"
        )


def _single_archive_member(archive: ZipFile, *, suffixes: tuple[str, ...]) -> str:
    members = [
        name
        for name in archive.namelist()
        if not name.endswith("/") and name.lower().endswith(suffixes)
    ]
    if len(members) != 1:
        raise ValueError(
            "Archive parser needs exactly one matching member or an explicit "
            f"archive_member; found {members}."
        )
    return members[0]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _safe_package_path(source: str | Path) -> Path:
    try:
        return resolve_source_package_path(source)
    except FileNotFoundError:
        return Path(source)


def _scaffold_template(
    *,
    source_id: str,
    package_id: str,
    source_table: str,
    resource_package: str,
    resource_directory: str,
    manifest: str,
) -> str:
    return f"""schema_version: ledger.source_package.v1
package_id: {package_id}
label: TODO package label
artifact:
  source_name: {source_id}
  source_table: {source_table}
  resource_package: {resource_package}
  resource_directory: {resource_directory}
  manifest: {manifest}
  vintage: tax_year_{{year}}
  extracted_at: "TODO YYYY-MM-DD"
  extraction_method: xlrd whole-workbook used-range cell parse
record_sets:
  - record_set_id: {source_id}.ty{{year}}.TODO_table
    provenance_class: TODO
    record_set_spec_id: {source_id}.TODO_table.v1
    source_record_id_prefix: {source_id}.ty{{year}}.TODO_table
    sheet_name: TODO
    period_type: tax_year
    period: "{{year}}"
    geography_id: TODO
    geography_level: country
    geography_name: TODO
    geography_vintage: TODO
    entity: tax_unit
    entity_role: filing_unit
    domain: TODO_domain
    groupby_dimension: TODO_concept
    rows:
      - value_id: all
        label: TODO row label
        ordinal: 0
        row_number: 1
        filters:
          TODO_filter: all
        table_record_kind: total
    measures:
      - measure_id: TODO_measure
        label: TODO measure label
        ordinal: 0
        column: B
        concept: TODO_concept
        unit: count
        aggregation: sum
"""
