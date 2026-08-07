"""Census ACS 5-year state-legislative-district source-package authoring.

State legislative districts (Census summary levels 610 upper / 620 lower) are
published only in the ACS 5-year dataset, and the API serves them per state
(``in=state:*`` is rejected for the SLD hierarchy). Each package therefore
witnesses one canonical per-state API response per table and chamber, and its
``source_package.yaml`` is generated deterministically from those witnessed
bytes by this module — a drifted regeneration is a test failure, not a silent
edit.

The record-set shape is the compact rectangular one (`packages/cms_aca/
oep_state_level` precedent): one record set per (table, chamber, state) whose
rows are the districts (row-level geography overrides, GEO_ID/NAME guard
cells) and whose measures are the table's estimate columns, carrying the
age/income bound constraints. Facts are rows x measures via
``compile_source_record_set_specs``.

Fetching requires a Census API key (``CENSUS_API_KEY``), appended only at
request time. The recorded ``source_url`` is the canonical keyless query; the
response body is key-independent, so the witnessed sha256 matches the
canonical URL's bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any

__all__ = [
    "AGE_BAND_MEASURES",
    "CHAMBERS",
    "INCOME_BRACKET_MEASURES",
    "MEDIAN_INCOME_MEASURES",
    "SLD_GEOGRAPHY_VINTAGE",
    "SLD_TABLES",
    "build_source_url",
    "fetch_witness_bytes",
    "generate_manifest",
    "generate_source_package",
    "parse_witness_rows",
]

#: Boundary vintage of the districts in the ACS 2020-2024 5-year tabulation.
#: District names in the witnessed responses carry the "(2024)" label; the
#: matching block-equivalency source is the Census 2024 SLD BEF release.
SLD_GEOGRAPHY_VINTAGE = "2024_state_legislative_districts"

#: States whose districts a package may witness. FIPS -> (USPS, name).
#: Puerto Rico and the island territories stay outside the US artifact spine.
SLD_STATES: dict[str, tuple[str, str]] = {
    "49": ("ut", "Utah"),
}


@dataclass(frozen=True)
class SldChamber:
    """One chamber of a state legislature as the ACS geography hierarchy."""

    key: str
    summary_level: str
    geography_level: str
    for_clause: str
    short: str


CHAMBERS: dict[str, SldChamber] = {
    "upper": SldChamber(
        key="upper",
        summary_level="610",
        geography_level="state_legislative_district_upper",
        for_clause="state%20legislative%20district%20(upper%20chamber):*",
        short="sldu",
    ),
    "lower": SldChamber(
        key="lower",
        summary_level="620",
        geography_level="state_legislative_district_lower",
        for_clause="state%20legislative%20district%20(lower%20chamber):*",
        short="sldl",
    ),
}


@dataclass(frozen=True)
class SldMeasure:
    """One estimate column of an ACS table declared as a package measure."""

    variable: str
    measure_id: str
    label: str
    source_concept: str
    concept: str
    unit: str
    aggregation: str
    constraint_variable: str | None = None
    constraint_unit: str | None = None
    lower: int | None = None
    upper: int | None = None

    def constraints(self) -> list[dict[str, Any]]:
        """Constraint payloads scoping this measure's facts."""
        payload: list[dict[str, Any]] = []
        if self.constraint_variable is None:
            return payload
        if self.lower is not None:
            payload.append(
                {
                    "variable": self.constraint_variable,
                    "operator": ">=",
                    "value": self.lower,
                    "unit": self.constraint_unit,
                    "label": f"{self.constraint_variable} lower bound",
                }
            )
        if self.upper is not None:
            payload.append(
                {
                    "variable": self.constraint_variable,
                    "operator": "<",
                    "value": self.upper,
                    "unit": self.constraint_unit,
                    "label": f"{self.constraint_variable} upper bound",
                }
            )
        return payload


def _age_band(variable: str, lower: int, upper: int | None) -> SldMeasure:
    if upper is None:
        label = f"Aged {lower} and over"
        measure_id = f"age_{lower}_and_over"
    else:
        label = f"Aged {lower}-{upper - 1}"
        measure_id = f"age_{lower}_to_{upper - 1}"
    return SldMeasure(
        variable=variable,
        measure_id=measure_id,
        label=label,
        source_concept=f"ACS S0101 total population {label}",
        concept="census_acs.person_count",
        unit="count",
        aggregation="sum",
        constraint_variable="age",
        constraint_unit="years",
        lower=lower,
        upper=upper,
    )


#: S0101 five-year age bands (C01 total-population columns 002-019), the same
#: 18 bands the congressional-district package (`acs_s0101_district_2024`)
#: declares.
AGE_BAND_MEASURES: tuple[SldMeasure, ...] = (
    _age_band("S0101_C01_002E", 0, 5),
    _age_band("S0101_C01_003E", 5, 10),
    _age_band("S0101_C01_004E", 10, 15),
    _age_band("S0101_C01_005E", 15, 20),
    _age_band("S0101_C01_006E", 20, 25),
    _age_band("S0101_C01_007E", 25, 30),
    _age_band("S0101_C01_008E", 30, 35),
    _age_band("S0101_C01_009E", 35, 40),
    _age_band("S0101_C01_010E", 40, 45),
    _age_band("S0101_C01_011E", 45, 50),
    _age_band("S0101_C01_012E", 50, 55),
    _age_band("S0101_C01_013E", 55, 60),
    _age_band("S0101_C01_014E", 60, 65),
    _age_band("S0101_C01_015E", 65, 70),
    _age_band("S0101_C01_016E", 70, 75),
    _age_band("S0101_C01_017E", 75, 80),
    _age_band("S0101_C01_018E", 80, 85),
    _age_band("S0101_C01_019E", 85, None),
)


def _income_bracket(
    variable: str,
    lower: int | None,
    upper: int | None,
) -> SldMeasure:
    if lower is None and upper is None:
        raise ValueError("A bracket needs at least one bound.")
    if lower is None:
        label = f"Household income under ${upper:,}"
        measure_id = f"income_under_{upper}"
    elif upper is None:
        label = f"Household income ${lower:,} or more"
        measure_id = f"income_{lower}_and_over"
    else:
        label = f"Household income ${lower:,} to ${upper - 1:,}"
        measure_id = f"income_{lower}_to_{upper - 1}"
    return SldMeasure(
        variable=variable,
        measure_id=measure_id,
        label=label,
        source_concept=f"ACS B19001 households with {label.lower()}",
        concept="census_acs.household_count",
        unit="count",
        aggregation="sum",
        constraint_variable="household_income",
        constraint_unit="usd",
        lower=lower,
        upper=upper,
    )


#: B19001 household income brackets. The leading total-households column is
#: the bracket universe and doubles as the district household-count fact.
#: Dollars are the ACS 5-year convention: inflation-adjusted to the final
#: year of the window (2024 dollars for the 2020-2024 release).
INCOME_BRACKET_MEASURES: tuple[SldMeasure, ...] = (
    SldMeasure(
        variable="B19001_001E",
        measure_id="all_households",
        label="Total households",
        source_concept="ACS B19001 total households estimate",
        concept="census_acs.household_count",
        unit="count",
        aggregation="sum",
    ),
    _income_bracket("B19001_002E", None, 10_000),
    _income_bracket("B19001_003E", 10_000, 15_000),
    _income_bracket("B19001_004E", 15_000, 20_000),
    _income_bracket("B19001_005E", 20_000, 25_000),
    _income_bracket("B19001_006E", 25_000, 30_000),
    _income_bracket("B19001_007E", 30_000, 35_000),
    _income_bracket("B19001_008E", 35_000, 40_000),
    _income_bracket("B19001_009E", 40_000, 45_000),
    _income_bracket("B19001_010E", 45_000, 50_000),
    _income_bracket("B19001_011E", 50_000, 60_000),
    _income_bracket("B19001_012E", 60_000, 75_000),
    _income_bracket("B19001_013E", 75_000, 100_000),
    _income_bracket("B19001_014E", 100_000, 125_000),
    _income_bracket("B19001_015E", 125_000, 150_000),
    _income_bracket("B19001_016E", 150_000, 200_000),
    _income_bracket("B19001_017E", 200_000, None),
)


MEDIAN_INCOME_MEASURES: tuple[SldMeasure, ...] = (
    SldMeasure(
        variable="B19013_001E",
        measure_id="median_household_income",
        label="Median household income",
        source_concept=(
            "ACS B19013 median household income in the past 12 months "
            "(inflation-adjusted dollars)"
        ),
        concept="census_acs.median_household_income",
        unit="usd",
        aggregation="median",
    ),
)


@dataclass(frozen=True)
class SldTable:
    """One ACS table witnessed at SLD summary levels."""

    table: str
    dataset_path: str
    measures: tuple[SldMeasure, ...]
    entity: str
    entity_role: str
    domain: str
    topic: str
    label_topic: str
    with_moe: bool = True

    def variables(self) -> list[str]:
        """Estimate variables plus their margin-of-error twins, in order."""
        estimates = [measure.variable for measure in self.measures]
        if not self.with_moe:
            return estimates
        return estimates + [variable[:-1] + "M" for variable in estimates]


#: The three witnessed tables. S0101 keeps the C01_001E total-population
#: column in the witness (first estimate column) without declaring it as a
#: fact, mirroring the congressional-district package's band-only surface.
SLD_TABLES: dict[str, SldTable] = {
    "s0101": SldTable(
        table="S0101",
        dataset_path="acs/acs5/subject",
        measures=AGE_BAND_MEASURES,
        entity="person",
        entity_role="resident_population",
        domain="total_population",
        topic="age",
        label_topic="population by age band",
    ),
    "b19001": SldTable(
        table="B19001",
        dataset_path="acs/acs5",
        measures=INCOME_BRACKET_MEASURES,
        entity="household",
        entity_role="resident_household",
        domain="households",
        topic="household_income",
        label_topic="household income brackets",
    ),
    "b19013": SldTable(
        table="B19013",
        dataset_path="acs/acs5",
        measures=MEDIAN_INCOME_MEASURES,
        entity="household",
        entity_role="resident_household",
        domain="households",
        topic="median_household_income",
        label_topic="median household income",
    ),
}

_S0101_WITNESS_PREFIX = ("S0101_C01_001E",)


def _witness_variables(table: SldTable) -> list[str]:
    """The full ``get=`` variable list for the canonical witness query."""
    estimates = list(_S0101_WITNESS_PREFIX) if table.table == "S0101" else []
    estimates += [measure.variable for measure in table.measures]
    moes = [variable[:-1] + "M" for variable in estimates]
    return ["GEO_ID", "NAME", *estimates, *moes]


def build_source_url(table_key: str, chamber_key: str, state_fips: str) -> str:
    """Canonical keyless API query for one (table, chamber, state) witness."""
    table = SLD_TABLES[table_key]
    chamber = CHAMBERS[chamber_key]
    variables = ",".join(_witness_variables(table))
    return (
        f"https://api.census.gov/data/2024/{table.dataset_path}"
        f"?get={variables}&for={chamber.for_clause}&in=state:{state_fips}"
    )


def fetch_witness_bytes(source_url: str, *, api_key: str | None = None) -> bytes:
    """Fetch a canonical witness query, appending the API key at request time.

    The key never appears in recorded metadata; the response body does not
    depend on it.
    """
    key = api_key if api_key is not None else os.environ.get("CENSUS_API_KEY")
    if not key:
        raise RuntimeError(
            "CENSUS_API_KEY is required to fetch from api.census.gov; the "
            "canonical source_url is recorded keyless."
        )
    request = urllib.request.Request(f"{source_url}&key={key}")
    with urllib.request.urlopen(request) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeError(
                f"Census API returned HTTP {response.status} for {source_url}"
            )
        return response.read()


def parse_witness_rows(content: bytes) -> tuple[list[str], list[list[str]]]:
    """Parse witnessed bytes into (header, district rows), validated."""
    data = json.loads(content.decode("utf-8"))
    if not isinstance(data, list) or len(data) < 2:
        raise ValueError("Witness must be a JSON table with a header row.")
    header, rows = data[0], data[1:]
    if header[0] != "GEO_ID" or header[1] != "NAME":
        raise ValueError(f"Witness columns must start GEO_ID,NAME, got {header[:2]}.")
    codes = [row[-1] for row in rows]
    if codes != sorted(codes):
        raise ValueError("District rows must arrive sorted by district code.")
    if len(set(codes)) != len(codes):
        raise ValueError("District codes must be unique within a witness.")
    return header, rows


def _excel_column_name(column_number: int) -> str:
    name = ""
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        name = f"{chr(65 + remainder)}{name}"
    return name


def _validate_witness_values(
    header: list[str],
    rows: list[list[str]],
    table: SldTable,
) -> None:
    for measure in table.measures:
        if measure.variable not in header:
            raise ValueError(f"Witness is missing column {measure.variable}.")
        index = header.index(measure.variable)
        for row in rows:
            value = row[index]
            if value is None or str(value).startswith("-"):
                # Every value in these tables is a nonnegative count or a
                # published median; any negative is an ACS annotation
                # sentinel (suppression, jam values), never data.
                raise ValueError(
                    f"Suppressed/absent value for {measure.variable} at "
                    f"{row[0]}; scope the package rows to published cells."
                )
            if not str(value).isdigit():
                raise ValueError(
                    f"Non-numeric value {value!r} for {measure.variable} at {row[0]}."
                )


def _package_names(
    table_key: str,
    chamber_key: str,
    state_fips: str,
) -> dict[str, str]:
    table = SLD_TABLES[table_key]
    chamber = CHAMBERS[chamber_key]
    usps, state_name = SLD_STATES[state_fips]
    topic_slug = table.topic.replace("_", "-")
    directory = f"acs_{table_key}_{chamber.short}_{usps}_2024"
    package_id = (
        f"census-acs-{table_key}-sld-{chamber.key}-{state_name.lower()}"
        f"-{topic_slug}-2024"
    )
    return {
        "directory": directory,
        "package_id": package_id,
        "dataset": f"census_acs_{table_key}_{chamber.short}_{usps}_2024",
        "filename": f"acs_{table.table}_{chamber.short}_{usps}_2024.json",
        "state_name": state_name,
        "usps": usps,
    }


def generate_manifest(
    content: bytes,
    *,
    table_key: str,
    chamber_key: str,
    state_fips: str,
    fetched_at: str,
) -> dict[str, Any]:
    """The db-resource ``manifest.yaml`` payload for one witness."""
    table = SLD_TABLES[table_key]
    chamber = CHAMBERS[chamber_key]
    names = _package_names(table_key, chamber_key, state_fips)
    source_table = (
        f"ACS 2024 5-year table {table.table} {names['state_name']} state "
        f"legislative district ({chamber.key} chamber) {table.label_topic}"
    )
    return {
        "source_id": "census_acs",
        "package_id": names["package_id"],
        "dataset": names["dataset"],
        "source_page": f"https://api.census.gov/data/2024/{table.dataset_path}.html",
        "vintage": "2020-2024 ACS 5-year tables",
        "table": f"{table.table} at summary level {chamber.summary_level}",
        "files": {
            2024: {
                "filename": names["filename"],
                "source_url": build_source_url(table_key, chamber_key, state_fips),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "source_table": source_table,
                "year": 2024,
                "fetched_at": fetched_at,
            }
        },
    }


def _validated_district_rows(
    content: bytes,
    table: SldTable,
    chamber: SldChamber,
    state_fips: str,
) -> tuple[list[str], list[list[str]]]:
    """Parse + validate the witness, returning (header, district rows)."""
    header, rows = parse_witness_rows(content)
    _validate_witness_values(header, rows, table)
    expected_prefix = (
        f"{chamber.summary_level}U"
        if chamber.key == "upper"
        else f"{chamber.summary_level}L"
    )
    for row in rows:
        geo_id, district_code = str(row[0]), str(row[-1])
        if not geo_id.startswith(expected_prefix):
            raise ValueError(
                f"GEO_ID {geo_id} does not match summary level "
                f"{chamber.summary_level} ({chamber.key} chamber)."
            )
        if not geo_id.endswith(f"US{state_fips}{district_code}"):
            raise ValueError(
                f"GEO_ID {geo_id} does not end with state {state_fips} and "
                f"district {district_code}."
            )
    return header, rows


def _record_set_common(
    table: SldTable,
    chamber: SldChamber,
    table_key: str,
) -> dict[str, Any]:
    return {
        "provenance_class": "survey_aggregate",
        "survey_instrument": "ACS 5-year",
        "record_set_spec_id": (
            f"census_acs.{table_key}.sld_{chamber.key}_{table.topic}.v1"
        ),
        "sheet_name": "api_response",
        "period_type": "calendar_year",
        "period": "{year}",
        "period_coverage": {
            "start_date": "2020-01-01",
            "end_date": "2024-12-31",
            "basis": "survey_reference",
            "source_period_label": "2020-2024 ACS 5-year estimates",
        },
        "entity": table.entity,
        "entity_role": table.entity_role,
        "domain": table.domain,
    }


#: Unpivoted-stream layouts per table: (parser name, dimension key column,
#: value column, groupby dimension, band descriptors). Band descriptors are
#: (value_id, publisher label emitted by the parser, constraints payload,
#: table_record_kind). The labels MUST match the parser's emitted dimension
#: values byte for byte — the regeneration test holds them together.
def _s0101_band_descriptors() -> list[tuple[str, str, list[dict[str, Any]], str]]:
    return [
        (measure.measure_id, measure.label, measure.constraints(), "detail")
        for measure in AGE_BAND_MEASURES
    ]


def _b19001_band_descriptors() -> list[tuple[str, str, list[dict[str, Any]], str]]:
    from chronicle.sources.rows import B19001_INCOME_BRACKET_COLUMNS

    by_variable = {measure.variable: measure for measure in INCOME_BRACKET_MEASURES}
    descriptors: list[tuple[str, str, list[dict[str, Any]], str]] = []
    for variable, _value_id, label, _lower, _upper in B19001_INCOME_BRACKET_COLUMNS:
        measure = by_variable[variable]
        descriptors.append(
            (
                measure.measure_id,
                label,
                measure.constraints(),
                "total" if variable == "B19001_001E" else "detail",
            )
        )
    return descriptors


_UNPIVOTED_LAYOUTS: dict[str, dict[str, Any]] = {
    "s0101": {
        "parser": "census_acs_s0101_age_json_rows",
        "header_column": "E",
        "value_column": "F",
        "groupby_dimension": "age",
        "descriptors": _s0101_band_descriptors,
        "measure_id": "population",
        "measure_label": "Population",
        "extraction_method": (
            "Census API JSON table parsed and unpivoted to district age-band rows"
        ),
    },
    "b19001": {
        "parser": "census_acs_b19001_income_json_rows",
        "header_column": "E",
        "value_column": "H",
        "groupby_dimension": "household_income",
        "descriptors": _b19001_band_descriptors,
        "measure_id": "household_count",
        "measure_label": "Households",
        "extraction_method": (
            "Census API JSON table parsed and unpivoted to district "
            "income-bracket rows with explicit bound columns"
        ),
    },
}


def generate_source_package(
    content: bytes,
    *,
    table_key: str,
    chamber_key: str,
    state_fips: str,
    extracted_at: str,
) -> dict[str, Any]:
    """The ``source_package.yaml`` payload generated from witnessed bytes.

    Constrained tables (S0101 age bands, B19001 income brackets) use the
    unpivoted per-district record-set shape: each fact's source row carries
    its band's semantic value (and, for income, explicit numeric bounds),
    so the agent-acceptance constraint-evidence gate validates every bound
    against the parsed rows. The unconstrained B19013 median keeps the
    compact rectangular shape.
    """
    table = SLD_TABLES[table_key]
    chamber = CHAMBERS[chamber_key]
    names = _package_names(table_key, chamber_key, state_fips)
    header, rows = _validated_district_rows(content, table, chamber, state_fips)

    if table_key in _UNPIVOTED_LAYOUTS:
        layout = _UNPIVOTED_LAYOUTS[table_key]
        descriptors = layout["descriptors"]()
        n_bands = len(descriptors)
        record_sets: list[dict[str, Any]] = []
        for district_index, row in enumerate(rows):
            geo_id, name = str(row[0]), str(row[1])
            district_code = str(row[-1])
            record_set_id = (
                "census_acs.acs5_{year}."
                f"{table_key}.sld_{chamber.key}_{names['usps']}.{table.topic}"
                f".{state_fips}{district_code}"
            )
            row_payloads = []
            for band_index, (value_id, band_label, constraints, kind) in enumerate(
                descriptors
            ):
                payload: dict[str, Any] = {
                    "value_id": value_id,
                    "label": band_label,
                    "ordinal": band_index,
                    # Cells materialize a header row at row 1; data rows
                    # of the unpivoted stream start at row 2.
                    "row_number": district_index * n_bands + band_index + 2,
                    "expected_row_header_column": layout["header_column"],
                    "expected_row_header": band_label,
                    "guard_cells": [
                        {
                            "column": "A",
                            "expected_value": geo_id,
                            "label": "GEO_ID",
                        },
                        {
                            "column": "B",
                            "expected_value": name,
                            "label": "District name",
                        },
                    ],
                }
                if kind != "detail":
                    payload["table_record_kind"] = kind
                if constraints:
                    payload["constraints"] = constraints
                row_payloads.append(payload)
            record_sets.append(
                {
                    "record_set_id": record_set_id,
                    **_record_set_common(table, chamber, table_key),
                    "source_record_id_prefix": record_set_id,
                    "geography_id": geo_id,
                    "geography_level": chamber.geography_level,
                    "geography_name": name,
                    "geography_vintage": SLD_GEOGRAPHY_VINTAGE,
                    "groupby_dimension": layout["groupby_dimension"],
                    "rows": row_payloads,
                    "measures": [
                        {
                            "measure_id": layout["measure_id"],
                            "label": layout["measure_label"],
                            "ordinal": 0,
                            "column": layout["value_column"],
                            "source_column_id": "value",
                            "expected_column_header_row": 1,
                            "expected_column_header": "value",
                            "concept": table.measures[0].concept,
                            "source_concept": (
                                f"ACS {table.table} district "
                                f"{layout['groupby_dimension']} estimate"
                            ),
                            "concept_relation": "source_label",
                            "unit": table.measures[0].unit,
                            "aggregation": "sum",
                            "expected_cell_type": "number",
                        }
                    ],
                }
            )
        parser = layout["parser"]
        extraction_method = layout["extraction_method"]
    else:
        row_payloads = []
        for ordinal, row in enumerate(rows):
            geo_id, name = str(row[0]), str(row[1])
            district_code = str(row[-1])
            row_payloads.append(
                {
                    "value_id": f"district_{district_code}",
                    "label": name,
                    "ordinal": ordinal,
                    # json_table_full_rows numbers data rows from 2 (the
                    # header row is row 1 and is not materialized as cells).
                    "row_number": ordinal + 2,
                    "geography_id": geo_id,
                    "geography_level": chamber.geography_level,
                    "geography_name": name,
                    "geography_vintage": SLD_GEOGRAPHY_VINTAGE,
                    "expected_row_header_column": "A",
                    "expected_row_header": geo_id,
                    "table_record_kind": "total",
                    "guard_cells": [
                        {
                            "column": "A",
                            "expected_value": geo_id,
                            "label": "GEO_ID",
                        },
                        {
                            "column": "B",
                            "expected_value": name,
                            "label": "District name",
                        },
                    ],
                }
            )
        measure_payloads = []
        for ordinal, measure in enumerate(table.measures):
            column_index = header.index(measure.variable) + 1
            payload = {
                "measure_id": measure.measure_id,
                "label": measure.label,
                "ordinal": ordinal,
                "column": _excel_column_name(column_index),
                "source_column_id": measure.variable,
                "expected_column_header_row": 1,
                "expected_column_header": measure.variable,
                "concept": measure.concept,
                "source_concept": measure.source_concept,
                "concept_relation": "source_label",
                "unit": measure.unit,
                "aggregation": measure.aggregation,
                "expected_cell_type": "number",
            }
            constraints = measure.constraints()
            if constraints:
                payload["constraints"] = constraints
            measure_payloads.append(payload)
        record_set_id = (
            "census_acs.acs5_{year}."
            f"{table_key}.sld_{chamber.key}_{names['usps']}.{table.topic}"
        )
        record_sets = [
            {
                "record_set_id": record_set_id,
                **_record_set_common(table, chamber, table_key),
                "source_record_id_prefix": record_set_id,
                "geography_id": f"0400000US{state_fips}",
                "geography_level": "state",
                "geography_name": names["state_name"],
                "geography_vintage": "current",
                "groupby_dimension": "census_acs.state_legislative_district",
                "rows": row_payloads,
                "measures": measure_payloads,
            }
        ]
        parser = "json_table_full_rows"
        extraction_method = (
            "Census API JSON table parsed to one source row per district"
        )

    label = (
        f"Census ACS 2020-2024 5-year {table.table} {names['state_name']} "
        f"state legislative district ({chamber.key} chamber) "
        f"{table.label_topic}"
    )
    return {
        "schema_version": "ledger.source_package.v1",
        "package_id": names["package_id"],
        "label": label,
        "artifact": {
            "source_name": "census_acs",
            "source_table": (
                f"ACS 2024 5-year table {table.table} {names['state_name']} "
                f"state legislative district ({chamber.key} chamber) "
                f"{table.label_topic}"
            ),
            "resource_package": "db",
            "resource_directory": f"data/census/{names['directory']}",
            "manifest": "manifest.yaml",
            "vintage": "acs_5_year_{year}",
            "extracted_at": extracted_at,
            "extraction_method": extraction_method,
            "parser": parser,
            "sheet_name": "api_response",
        },
        "record_sets": record_sets,
    }
