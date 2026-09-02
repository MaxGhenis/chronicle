"""Build-suite harness for source-backed Chronicle packages."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from chronicle.concepts import ConceptAlignmentReport, validate_concept_alignments
from chronicle.consumer_contract import (
    ConsumerFactExportReport,
    write_consumer_facts_jsonl,
)
from chronicle.core import (
    AggregateFact,
    ValidationReport,
    build_aggregate_constraints,
    build_fact_key,
    validate_facts,
)
from chronicle.database import (
    CHRONICLE_DB_FILENAME,
    ChronicleDbBuildReport,
    build_chronicle_db,
)
from chronicle.sources.cells import (
    SourceCell,
    SourceCellReport,
    build_source_cell_key,
    save_source_cells_jsonl,
    validate_source_cells,
)
from chronicle.sources.rows import (
    SourceRow,
    SourceRowReport,
    build_source_row_key,
    save_source_rows_jsonl,
    validate_source_rows,
)
from chronicle.sources.specs import (
    SourceRecordSpec,
    SourceRecordSetSpec,
    SourceRegionSpec,
    build_cells_by_sheet_address,
    resolve_source_record,
)
from chronicle.source_package import SOURCE_PACKAGE_ALIASES, try_load_source_package
from chronicle.store import save_facts_jsonl

SUPPORTED_SOURCE_PACKAGES = set(SOURCE_PACKAGE_ALIASES)
SOI_HISTORIC_TABLE_2_AGI_STUB_RANGES = {
    0: ("all", None, None),
    1: ("under_1", None, 1),
    2: ("1_to_10k", 1, 10_000),
    3: ("10k_to_25k", 10_000, 25_000),
    4: ("25k_to_50k", 25_000, 50_000),
    5: ("50k_to_75k", 50_000, 75_000),
    6: ("75k_to_100k", 75_000, 100_000),
    7: ("100k_to_200k", 100_000, 200_000),
    8: ("200k_to_500k", 200_000, 500_000),
    9: ("500k_to_1m", 500_000, 1_000_000),
    10: ("1m_plus", 1_000_000, None),
}
SOI_EITC_CHILD_COUNT_COLUMN_VALUES = {
    "N59661": 0,
    "A59661": 0,
    "N59662": 1,
    "A59662": 1,
    "N59663": 2,
    "A59663": 2,
    "N59664": 3,
    "A59664": 3,
}


@dataclass(frozen=True)
class SourceRecordSuiteIssue:
    """One source-record suite validation issue."""

    code: str
    message: str
    source_record_id: str | None = None
    selector_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable issue."""
        data = asdict(self)
        return {key: value for key, value in data.items() if value is not None}


@dataclass(frozen=True)
class SourceRecordSuiteReport:
    """Validation report for source-record specs resolved against source cells."""

    spec_count: int
    resolved_count: int
    lineaged_count: int
    errors: tuple[SourceRecordSuiteIssue, ...]
    warnings: tuple[SourceRecordSuiteIssue, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether all source-record specs resolved without errors."""
        return not self.errors

    @property
    def lineage_coverage(self) -> float:
        """Share of resolved records carrying source-cell lineage."""
        if not self.resolved_count:
            return 0
        return self.lineaged_count / self.resolved_count

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": self.valid,
            "spec_count": self.spec_count,
            "resolved_count": self.resolved_count,
            "lineaged_count": self.lineaged_count,
            "lineage_coverage": self.lineage_coverage,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass(frozen=True)
class SourceRegionSuiteIssue:
    """One source-region suite validation issue."""

    code: str
    message: str
    region_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable issue."""
        data = asdict(self)
        return {key: value for key, value in data.items() if value is not None}


@dataclass(frozen=True)
class SourceRegionSuiteReport:
    """Validation report for source regions resolved against source cells."""

    region_count: int
    covered_cell_count: int
    errors: tuple[SourceRegionSuiteIssue, ...]
    warnings: tuple[SourceRegionSuiteIssue, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether source regions validated without errors."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": self.valid,
            "region_count": self.region_count,
            "covered_cell_count": self.covered_cell_count,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass(frozen=True)
class AgentAcceptanceIssue:
    """One agent-facing build acceptance issue."""

    code: str
    message: str
    fact_key: str | None = None
    source_record_id: str | None = None
    artifact_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable issue."""
        data = asdict(self)
        return {key: value for key, value in data.items() if value is not None}


@dataclass(frozen=True)
class AgentAcceptanceReport:
    """Agent-facing acceptance report for source-package population."""

    checks: dict[str, bool]
    counts: dict[str, int | float]
    errors: tuple[AgentAcceptanceIssue, ...]
    warnings: tuple[AgentAcceptanceIssue, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether the package passes agent acceptance gates."""
        return not self.errors and all(self.checks.values())

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": self.valid,
            "checks": self.checks,
            "counts": self.counts,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass(frozen=True)
class BuildSuiteReport:
    """End-to-end build-suite report for one source package."""

    source: str
    year: int
    output_dir: str
    outputs: dict[str, str]
    source_rows: SourceRowReport
    source_cells: SourceCellReport
    source_regions: SourceRegionSuiteReport
    source_records: SourceRecordSuiteReport
    facts: ValidationReport
    consumer_facts: ConsumerFactExportReport
    concept_alignments: ConceptAlignmentReport
    database: ChronicleDbBuildReport
    agent_acceptance: AgentAcceptanceReport

    @property
    def valid(self) -> bool:
        """Whether every suite report is valid."""
        return (
            self.source_rows.valid
            and self.source_cells.valid
            and self.source_regions.valid
            and self.source_records.valid
            and self.facts.valid
            and self.concept_alignments.valid
            and self.agent_acceptance.valid
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": self.valid,
            "source": self.source,
            "year": self.year,
            "output_dir": self.output_dir,
            "outputs": self.outputs,
            "counts": {
                "artifact_count": self.database.source_artifacts_count,
                "source_row_count": self.source_rows.row_count,
                "source_cell_count": self.source_cells.cell_count,
                "source_region_count": self.source_regions.region_count,
                "source_record_count": self.source_records.resolved_count,
                "fact_count": self.facts.fact_count,
                "consumer_fact_count": self.consumer_facts.fact_count,
                "constraint_count": self.database.constraints_count,
                "concept_alignment_count": (self.concept_alignments.alignment_count),
                "lineage_coverage": self.source_records.lineage_coverage,
                "agent_acceptance_error_count": len(self.agent_acceptance.errors),
            },
            "reports": {
                "source_rows": self.source_rows.to_dict(),
                "source_cells": self.source_cells.to_dict(),
                "source_regions": self.source_regions.to_dict(),
                "selectors": self.source_records.to_dict(),
                "source_records": self.source_records.to_dict(),
                "facts": self.facts.to_dict(),
                "consumer_facts": self.consumer_facts.to_dict(),
                "concept_alignments": self.concept_alignments.to_dict(),
                "database": self.database.to_dict(),
                "agent_acceptance": self.agent_acceptance.to_dict(),
            },
        }


def build_source_suite(
    source: str | Path,
    output_dir: str | Path,
    *,
    year: int = 2023,
    axiom_command: Sequence[str] | None = None,
    axiom_roots: Sequence[str | Path] = (),
    require_axiom_validation: bool = False,
    replace: bool = False,
) -> BuildSuiteReport:
    """Build all reproducible Chronicle artifacts and reports for a source package."""
    source_package = try_load_source_package(source)
    source_id = source_package.package_id if source_package else source
    output_path = Path(output_dir)
    _prepare_output_dir(output_path, replace=replace)
    reports_path = output_path / "reports"
    reports_path.mkdir(parents=True, exist_ok=True)

    source_rows = (
        source_package.build_source_rows(year)
        if source_package
        else build_source_rows(source, year=year)
    )
    source_row_report = validate_source_rows(source_rows)
    source_rows_path = output_path / "source_rows.jsonl"
    save_source_rows_jsonl(source_rows, source_rows_path)
    _write_report(reports_path / "source_rows.json", source_row_report.to_dict())

    cells = (
        source_package.build_source_cells(year, source_rows=source_rows)
        if source_package
        else build_source_cells(source, year=year)
    )
    source_cell_report = validate_source_cells(cells)
    source_cells_path = output_path / "source_cells.jsonl"
    save_source_cells_jsonl(cells, source_cells_path)
    _write_report(reports_path / "source_cells.json", source_cell_report.to_dict())

    regions = (
        source_package.build_source_regions(year)
        if source_package
        else build_source_regions(source, year=year)
    )
    source_regions_path = output_path / "source_regions.jsonl"
    _write_jsonl(source_regions_path, [asdict(region) for region in regions])
    source_region_report = validate_source_regions(regions, cells)
    _write_report(
        reports_path / "source_regions.json",
        source_region_report.to_dict(),
    )

    source_record_set_specs = (
        source_package.build_source_record_set_specs(year) if source_package else []
    )
    source_record_specs = (
        source_package.build_source_record_specs(year)
        if source_package
        else build_source_record_specs(source, year=year)
    )
    source_record_report = validate_source_record_specs(
        source_record_specs,
        cells,
    )
    _write_report(
        reports_path / "selectors.json",
        source_record_report.to_dict(),
    )
    _write_report(
        reports_path / "source_records.json",
        source_record_report.to_dict(),
    )

    facts = (
        source_package.build_facts(year, cells=cells, source_rows=source_rows)
        if source_package
        else build_facts(source, year=year)
    )
    fact_report = validate_facts(facts)
    facts_path = output_path / "facts.jsonl"
    save_facts_jsonl(facts, facts_path)
    _write_report(reports_path / "facts.json", fact_report.to_dict())
    consumer_facts_path = output_path / "consumer_facts.jsonl"
    consumer_fact_report = write_consumer_facts_jsonl(
        facts,
        consumer_facts_path,
    )
    _write_report(
        reports_path / "consumer_facts.json",
        consumer_fact_report.to_dict(),
    )

    concept_report = validate_concept_alignments(
        facts,
        axiom_command=axiom_command,
        axiom_roots=axiom_roots,
    )
    _write_report(
        reports_path / "concept_alignments.json",
        concept_report.to_dict(),
    )

    db_path = output_path / CHRONICLE_DB_FILENAME
    db_report = build_chronicle_db(
        facts,
        db_path,
        source_cells=cells,
        source_rows=source_rows,
        replace=True,
    )
    _write_report(reports_path / "database.json", db_report.to_dict())
    agent_acceptance_report = build_agent_acceptance_report(
        facts,
        source_rows,
        cells,
        source_rows=source_row_report,
        source_cells=source_cell_report,
        source_regions=source_region_report,
        source_records=source_record_report,
        fact_report=fact_report,
        concept_alignments=concept_report,
        require_axiom_validation=require_axiom_validation,
        source_column_dimensions_by_record_id=(
            _source_column_dimensions_by_record_id(source_record_set_specs)
        ),
        selected_only_source_parse=(
            bool(source_package)
            and source_package.artifact.parser == "delimited_text_selected_rows"
        ),
    )
    _write_report(
        reports_path / "agent_acceptance.json",
        agent_acceptance_report.to_dict(),
    )

    report = BuildSuiteReport(
        source=source_id,
        year=year,
        output_dir=str(output_path),
        outputs={
            "source_rows": str(source_rows_path),
            "source_cells": str(source_cells_path),
            "source_regions": str(source_regions_path),
            "facts": str(facts_path),
            "consumer_facts": str(consumer_facts_path),
            "database": str(db_path),
            "reports": str(reports_path),
            "datapackage": str(output_path / "datapackage.json"),
            "ro_crate": str(output_path / "ro-crate-metadata.json"),
            "agent_acceptance": str(reports_path / "agent_acceptance.json"),
        },
        source_rows=source_row_report,
        source_cells=source_cell_report,
        source_regions=source_region_report,
        source_records=source_record_report,
        facts=fact_report,
        consumer_facts=consumer_fact_report,
        concept_alignments=concept_report,
        database=db_report,
        agent_acceptance=agent_acceptance_report,
    )
    _write_report(reports_path / "build_summary.json", report.to_dict())
    _write_package_sidecars(output_path, source=source_id, year=year)
    return report


def validate_source_record_specs(
    specs: list[SourceRecordSpec],
    cells: list[SourceCell],
) -> SourceRecordSuiteReport:
    """Resolve source-record specs and report selector/lineage failures."""
    errors: list[SourceRecordSuiteIssue] = []
    resolved_count = 0
    lineaged_count = 0
    cells_by_sheet_address = build_cells_by_sheet_address(cells)

    for spec in specs:
        try:
            record = resolve_source_record(
                cells,
                spec,
                cells_by_sheet_address=cells_by_sheet_address,
            )
        except ValueError as exc:
            errors.append(
                SourceRecordSuiteIssue(
                    code="source_record_resolution_failed",
                    message=str(exc),
                    source_record_id=spec.source_record_id,
                    selector_id=spec.selector.selector_id,
                )
            )
            continue
        resolved_count += 1
        if record.source_cell_keys:
            lineaged_count += 1
        else:
            errors.append(
                SourceRecordSuiteIssue(
                    code="missing_source_cell_lineage",
                    message="Resolved source record has no source-cell lineage.",
                    source_record_id=spec.source_record_id,
                    selector_id=spec.selector.selector_id,
                )
            )

    return SourceRecordSuiteReport(
        spec_count=len(specs),
        resolved_count=resolved_count,
        lineaged_count=lineaged_count,
        errors=tuple(errors),
    )


def validate_source_regions(
    regions: list[SourceRegionSpec],
    cells: list[SourceCell],
) -> SourceRegionSuiteReport:
    """Validate source-region specs against parsed source cells."""
    errors: list[SourceRegionSuiteIssue] = []
    region_ids: dict[str, list[int]] = {}
    covered_cells: set[tuple[str, int, int]] = set()

    for index, region in enumerate(regions):
        region_ids.setdefault(region.region_id, []).append(index)
        if not region.region_id.strip():
            errors.append(
                SourceRegionSuiteIssue(
                    code="missing_region_id",
                    message="Source region is missing region_id.",
                )
            )
        if (
            region.top_row < 1
            or region.left_column < 1
            or region.bottom_row < region.top_row
            or region.right_column < region.left_column
        ):
            errors.append(
                SourceRegionSuiteIssue(
                    code="malformed_region_bounds",
                    message="Source region bounds must be one-based and ordered.",
                    region_id=region.region_id,
                )
            )
            continue

        matching_cells = [
            cell
            for cell in cells
            if cell.sheet_name == region.sheet_name
            and region.top_row <= cell.row_number <= region.bottom_row
            and region.left_column <= cell.column_number <= region.right_column
        ]
        if not matching_cells:
            errors.append(
                SourceRegionSuiteIssue(
                    code="region_matches_no_source_cells",
                    message="Source region did not cover any parsed source cells.",
                    region_id=region.region_id,
                )
            )
            continue
        covered_cells.update(
            (cell.sheet_name, cell.row_number, cell.column_number)
            for cell in matching_cells
        )

    for region_id, indices in region_ids.items():
        if region_id and len(indices) > 1:
            errors.append(
                SourceRegionSuiteIssue(
                    code="duplicate_region_id",
                    message=f"Duplicate source-region ID appears at indices {indices}.",
                    region_id=region_id,
                )
            )

    return SourceRegionSuiteReport(
        region_count=len(regions),
        covered_cell_count=len(covered_cells),
        errors=tuple(errors),
    )


def build_agent_acceptance_report(
    facts: list[AggregateFact],
    rows: list[SourceRow],
    cells: list[SourceCell],
    *,
    source_rows: SourceRowReport,
    source_cells: SourceCellReport,
    source_regions: SourceRegionSuiteReport,
    source_records: SourceRecordSuiteReport,
    fact_report: ValidationReport,
    concept_alignments: ConceptAlignmentReport,
    require_axiom_validation: bool = False,
    source_column_dimensions_by_record_id: dict[str, dict[str, Any]] | None = None,
    selected_only_source_parse: bool = False,
) -> AgentAcceptanceReport:
    """Build the stricter report agents should satisfy before review."""
    errors: list[AgentAcceptanceIssue] = []
    warnings: list[AgentAcceptanceIssue] = []
    artifacts = {
        **{row.artifact.sha256: row.artifact for row in rows},
        **{cell.artifact.sha256: cell.artifact for cell in cells},
    }
    source_rows_by_key = {build_source_row_key(row): row for row in rows}
    source_row_keys = set(source_rows_by_key)
    source_cells_by_key = {build_source_cell_key(cell): cell for cell in cells}
    source_column_dimensions_by_record_id = source_column_dimensions_by_record_id or {}
    raw_r2_link_count = 0

    if not cells and not rows:
        errors.append(
            AgentAcceptanceIssue(
                code="missing_source_document_parse",
                message="Accepted packages must preserve a parsed source document.",
            )
        )
    if selected_only_source_parse:
        errors.append(
            AgentAcceptanceIssue(
                code="selected_row_only_source_parse",
                message=(
                    "Accepted packages must parse the full source document "
                    "before selecting facts."
                ),
            )
        )
    for artifact_sha256, artifact in artifacts.items():
        if artifact.raw_r2_bucket and artifact.raw_r2_key and artifact.raw_r2_uri:
            raw_r2_link_count += 1
        else:
            errors.append(
                AgentAcceptanceIssue(
                    code="missing_raw_r2_link",
                    message="Source artifact is missing raw R2 storage metadata.",
                    artifact_sha256=artifact_sha256,
                )
            )

    missing_provenance_count = _fact_count(fact_report, "missing_provenance")
    if missing_provenance_count:
        errors.append(
            AgentAcceptanceIssue(
                code="missing_fact_provenance",
                message="One or more aggregate facts have incomplete provenance.",
            )
        )

    missing_lineage_count = _fact_count(fact_report, "missing_lineage")
    if missing_lineage_count:
        errors.append(
            AgentAcceptanceIssue(
                code="missing_fact_lineage",
                message="One or more aggregate facts have no source-cell lineage.",
            )
        )
    if source_records.lineaged_count != source_records.resolved_count:
        errors.append(
            AgentAcceptanceIssue(
                code="missing_source_record_lineage",
                message="One or more selected source records lack source-cell lineage.",
            )
        )
    missing_row_lineage_count = 0
    missing_row_resolution_count = 0
    row_semantic_error_count = 0
    if rows:
        for fact in facts:
            if not fact.source_row_keys:
                missing_row_lineage_count += 1
                errors.append(
                    AgentAcceptanceIssue(
                        code="missing_fact_source_row_lineage",
                        message=(
                            "Fact was built from a row-oriented source but has "
                            "no source-row lineage."
                        ),
                        fact_key=build_fact_key(fact),
                        source_record_id=fact.source_record_id,
                    )
                )
                continue
            unresolved_keys = [
                key for key in fact.source_row_keys if key not in source_row_keys
            ]
            if unresolved_keys:
                missing_row_resolution_count += 1
                errors.append(
                    AgentAcceptanceIssue(
                        code="fact_source_row_lineage_unresolved",
                        message=(
                            "Fact source-row lineage does not resolve into "
                            "the parsed source rows."
                        ),
                        fact_key=build_fact_key(fact),
                        source_record_id=fact.source_record_id,
                    )
                )
                continue
            for issue in _row_semantic_evidence_issues(
                fact,
                [source_rows_by_key[key] for key in fact.source_row_keys],
                [
                    source_cells_by_key[key]
                    for key in fact.source_cell_keys
                    if key in source_cells_by_key
                ],
                source_column_dimensions=(
                    source_column_dimensions_by_record_id.get(
                        fact.source_record_id or "",
                        {},
                    )
                ),
            ):
                row_semantic_error_count += 1
                errors.append(issue)

    expected_constraint_facts = [
        fact for fact in facts if _expects_first_class_constraints(fact)
    ]
    constrained_expected_facts = [
        fact for fact in expected_constraint_facts if build_aggregate_constraints(fact)
    ]
    for fact in expected_constraint_facts:
        if build_aggregate_constraints(fact):
            continue
        errors.append(
            AgentAcceptanceIssue(
                code="missing_expected_constraints",
                message=(
                    "Non-total grouped aggregate fact has no first-class constraints."
                ),
                fact_key=build_fact_key(fact),
                source_record_id=fact.source_record_id,
            )
        )

    missing_evidence_count = sum(
        1
        for issue in concept_alignments.errors
        if issue.code == "missing_concept_evidence"
    )
    invalid_concept_count = sum(
        1
        for issue in concept_alignments.errors
        if issue.code == "axiom_concept_invalid"
    )
    unchecked_alignment_count = max(
        0,
        concept_alignments.alignment_count - concept_alignments.checked_count,
    )
    if missing_evidence_count:
        errors.append(
            AgentAcceptanceIssue(
                code="missing_concept_alignment_evidence",
                message="Exact concept alignments are missing evidence.",
            )
        )
    if invalid_concept_count:
        errors.append(
            AgentAcceptanceIssue(
                code="invalid_canonical_concept",
                message="One or more canonical concepts failed Axiom validation.",
            )
        )
    if unchecked_alignment_count:
        issue = AgentAcceptanceIssue(
            code="concept_alignment_validation_skipped",
            message=(
                "One or more canonical concepts were not checked against "
                "Axiom metadata."
            ),
        )
        if require_axiom_validation:
            errors.append(issue)
        else:
            warnings.append(issue)
    if concept_alignments.alignment_count == 0:
        warnings.append(
            AgentAcceptanceIssue(
                code="no_concept_alignments",
                message="Package emitted no source-to-canonical concept alignments.",
            )
        )

    stage_reports_valid = (
        source_rows.valid
        and source_cells.valid
        and source_regions.valid
        and source_records.valid
        and fact_report.valid
        and concept_alignments.valid
    )
    if not stage_reports_valid:
        errors.append(
            AgentAcceptanceIssue(
                code="stage_report_failed",
                message="One or more build-suite stage reports are invalid.",
            )
        )

    checks = {
        "stage_reports_valid": stage_reports_valid,
        "raw_artifacts_have_r2": raw_r2_link_count == len(artifacts)
        and bool(artifacts),
        "full_source_document_parsed": bool(rows) or bool(cells),
        "selected_row_only_parser_not_used": not selected_only_source_parse,
        "facts_have_provenance": missing_provenance_count == 0,
        "facts_have_source_cell_lineage": (
            missing_lineage_count == 0
            and source_records.lineaged_count == source_records.resolved_count
        ),
        "facts_have_source_row_lineage": (
            not rows
            or (missing_row_lineage_count == 0 and missing_row_resolution_count == 0)
        ),
        "row_lineage_semantics_evidenced": row_semantic_error_count == 0,
        "expected_constraints_present": (
            len(constrained_expected_facts) == len(expected_constraint_facts)
        ),
        "concept_alignments_have_evidence": missing_evidence_count == 0,
        "concept_alignments_resolve": invalid_concept_count == 0,
        "required_concept_alignments_validated": (
            not require_axiom_validation or unchecked_alignment_count == 0
        ),
    }
    return AgentAcceptanceReport(
        checks=checks,
        counts={
            "raw_artifact_count": len(artifacts),
            "raw_r2_link_count": raw_r2_link_count,
            "source_row_count": source_rows.row_count,
            "fact_count": fact_report.fact_count,
            "missing_provenance_count": missing_provenance_count,
            "missing_lineage_count": missing_lineage_count,
            "missing_source_row_lineage_count": missing_row_lineage_count,
            "unresolved_source_row_lineage_count": missing_row_resolution_count,
            "row_semantic_error_count": row_semantic_error_count,
            "source_record_count": source_records.resolved_count,
            "source_record_lineaged_count": source_records.lineaged_count,
            "expected_constraint_fact_count": len(expected_constraint_facts),
            "constrained_expected_fact_count": len(constrained_expected_facts),
            "concept_alignment_count": concept_alignments.alignment_count,
            "concept_alignment_checked_count": concept_alignments.checked_count,
            "concept_alignment_unchecked_count": unchecked_alignment_count,
            "concept_alignment_missing_evidence_count": missing_evidence_count,
            "concept_alignment_invalid_count": invalid_concept_count,
        },
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def build_source_cells(source: str | Path, *, year: int) -> list[SourceCell]:
    """Build parsed source cells for a supported source package."""
    source_package = try_load_source_package(source)
    if source_package:
        return source_package.build_source_cells(year)
    _require_supported_source(source)
    if source == "soi-table-1-1":
        from chronicle.jurisdictions.us.soi import build_soi_table_1_1_source_cells

        return build_soi_table_1_1_source_cells(year)
    if source == "soi-table-1-4":
        from chronicle.jurisdictions.us.soi import build_soi_table_1_4_source_cells

        return build_soi_table_1_4_source_cells(year)
    raise AssertionError(f"Unhandled supported source: {source}")


def build_source_rows(source: str | Path, *, year: int) -> list[SourceRow]:
    """Build parsed source rows for a supported source package."""
    source_package = try_load_source_package(source)
    if source_package:
        return source_package.build_source_rows(year)
    _require_supported_source(source)
    return []


def build_source_regions(
    source: str | Path,
    *,
    year: int,
) -> list[SourceRegionSpec]:
    """Build source-region specs for a supported source package."""
    source_package = try_load_source_package(source)
    if source_package:
        return source_package.build_source_regions(year)
    _require_supported_source(source)
    if source == "soi-table-1-1":
        from chronicle.jurisdictions.us.soi import (
            build_soi_table_1_1_source_region_specs,
        )

        return build_soi_table_1_1_source_region_specs(year)
    if source == "soi-table-1-4":
        from chronicle.jurisdictions.us.soi import (
            build_soi_table_1_4_source_region_specs,
        )

        return build_soi_table_1_4_source_region_specs(year)
    raise AssertionError(f"Unhandled supported source: {source}")


def build_source_record_specs(
    source: str | Path,
    *,
    year: int,
) -> list[SourceRecordSpec]:
    """Build source-record specs for a supported source package."""
    source_package = try_load_source_package(source)
    if source_package:
        return source_package.build_source_record_specs(year)
    _require_supported_source(source)
    if source == "soi-table-1-1":
        from chronicle.jurisdictions.us.soi import (
            build_soi_table_1_1_source_record_specs,
        )

        return build_soi_table_1_1_source_record_specs(year)
    if source == "soi-table-1-4":
        from chronicle.jurisdictions.us.soi import (
            build_soi_table_1_4_source_record_specs,
        )

        return build_soi_table_1_4_source_record_specs(year)
    raise AssertionError(f"Unhandled supported source: {source}")


def build_facts(source: str | Path, *, year: int) -> list[AggregateFact]:
    """Build aggregate facts for a supported source package."""
    source_package = try_load_source_package(source)
    if source_package:
        return source_package.build_facts(year)
    _require_supported_source(source)
    if source == "soi-table-1-1":
        from chronicle.jurisdictions.us.soi import build_soi_table_1_1_facts

        return build_soi_table_1_1_facts(year)
    if source == "soi-table-1-4":
        from chronicle.jurisdictions.us.soi import build_soi_table_1_4_facts

        return build_soi_table_1_4_facts(year)
    raise AssertionError(f"Unhandled supported source: {source}")


def _require_supported_source(source: str | Path) -> None:
    if source not in SUPPORTED_SOURCE_PACKAGES:
        choices = ", ".join(sorted(SUPPORTED_SOURCE_PACKAGES))
        raise ValueError(
            f"Unsupported source package {source!r}; choose one of {choices}"
        )


def _expects_first_class_constraints(fact: AggregateFact) -> bool:
    layout = fact.layout
    return bool(
        layout
        and layout.groupby_dimension
        and layout.groupby_dimension != "geography"
        and layout.table_record_kind != "total"
    )


def _row_semantic_evidence_issues(
    fact: AggregateFact,
    rows: list[SourceRow],
    cells: list[SourceCell],
    *,
    source_column_dimensions: dict[str, Any] | None = None,
) -> list[AgentAcceptanceIssue]:
    issues: list[AgentAcceptanceIssue] = []
    source_column_dimensions = source_column_dimensions or {}
    fact_key = build_fact_key(fact)
    period_values = _source_row_values(rows, "period")
    for value in period_values:
        if not _values_equal(value, fact.period.value):
            issues.append(
                AgentAcceptanceIssue(
                    code="fact_period_not_evidenced_by_source_row",
                    message=(
                        "Fact period does not match the period value carried "
                        "by its source row."
                    ),
                    fact_key=fact_key,
                    source_record_id=fact.source_record_id,
                )
            )

    for variable, value in fact.filters.items():
        if value is None:
            continue
        if value == "all" and not source_column_dimensions:
            continue
        matched_values = _source_row_values(rows, variable)
        if not matched_values:
            if _filter_evidenced_by_source_cells(cells, variable, value):
                continue
            if _wide_table_filter_evidenced_by_source_column(
                source_column_dimensions,
                variable,
                value,
            ):
                continue
            issues.append(
                AgentAcceptanceIssue(
                    code="row_filter_not_evidenced",
                    message="Fact filter is not present in its source rows.",
                    fact_key=fact_key,
                    source_record_id=fact.source_record_id,
                )
            )
            continue
        for matched_value in matched_values:
            if not _filter_value_matches_source_value(
                variable,
                value,
                matched_value,
            ):
                issues.append(
                    AgentAcceptanceIssue(
                        code="row_filter_value_mismatch",
                        message=(
                            "Fact filter value does not match the value "
                            "carried by its source row."
                        ),
                        fact_key=fact_key,
                        source_record_id=fact.source_record_id,
                    )
                )

    for constraint in build_aggregate_constraints(fact):
        if _constraint_evidenced_by_source_rows(rows, constraint):
            continue
        if _constraint_evidenced_by_source_cells(cells, constraint):
            continue
        if _wide_table_constraint_evidenced_by_source_column(
            source_column_dimensions,
            constraint,
        ):
            continue
        matched_values = _source_row_values(rows, constraint.variable)
        if not matched_values:
            issues.append(
                AgentAcceptanceIssue(
                    code="row_constraint_not_evidenced",
                    message=("Fact constraint is not present in its source rows."),
                    fact_key=fact_key,
                    source_record_id=fact.source_record_id,
                )
            )
            continue
        for matched_value in matched_values:
            if not _constraint_matches_source_value(
                matched_value,
                constraint.operator,
                constraint.value,
            ):
                issues.append(
                    AgentAcceptanceIssue(
                        code="row_constraint_value_mismatch",
                        message=(
                            "Fact constraint does not match the value carried "
                            "by its source row."
                        ),
                        fact_key=fact_key,
                        source_record_id=fact.source_record_id,
                    )
                )
    return issues


def _wide_table_filter_evidenced_by_source_column(
    source_column_dimensions: dict[str, Any],
    variable: str,
    expected: Any,
) -> bool:
    """Accept an explicitly declared dimension of a guarded source column."""
    if variable not in source_column_dimensions:
        return False
    declared = source_column_dimensions[variable]
    return type(declared) is type(expected) and declared == expected


def _wide_table_constraint_evidenced_by_source_column(
    source_column_dimensions: dict[str, Any],
    constraint: Any,
) -> bool:
    if constraint.operator != "==":
        return False
    return _wide_table_filter_evidenced_by_source_column(
        source_column_dimensions,
        str(constraint.variable),
        constraint.value,
    )


def _source_column_dimensions_by_record_id(
    record_sets: list[SourceRecordSetSpec],
) -> dict[str, dict[str, Any]]:
    """Index explicit wide-column dimensions without changing fact payloads."""
    return {
        f"{record_set.source_record_id_prefix}.{row.value_id}.{measure.measure_id}": (
            dict(measure.source_column_dimensions)
        )
        for record_set in record_sets
        for row in record_set.rows
        for measure in record_set.measures
        if measure.source_column_dimensions
    }


def _constraint_evidenced_by_source_cells(
    cells: list[SourceCell],
    constraint: Any,
) -> bool:
    if _is_eitc_qualifying_children_variable(str(constraint.variable)):
        return _eitc_child_count_constraint_matches_cells(
            cells,
            constraint.operator,
            constraint.value,
        )
    if not _is_age_variable(str(constraint.variable)):
        return False
    ranges = [
        age_range
        for cell in cells
        if (age_range := _source_cell_age_range(cell)) is not None
    ]
    if not ranges:
        return False
    return _age_band_constraint_range_matches(
        ranges,
        constraint.operator,
        constraint.value,
    )


def _source_row_values(rows: list[SourceRow], variable: str) -> list[Any]:
    values = []
    for row in rows:
        matched, value = _source_row_value(row, variable)
        if matched:
            values.append(value)
    return values


def _source_row_value(row: SourceRow, variable: str) -> tuple[bool, Any]:
    values_by_column = {
        _normalize_semantic_name(column): value for column, value in row.values.items()
    }
    if _normalize_semantic_name(variable) == "incomerange":
        agi_stub = _source_row_agi_stub(row)
        if agi_stub in SOI_HISTORIC_TABLE_2_AGI_STUB_RANGES:
            bracket, _lower, _upper = SOI_HISTORIC_TABLE_2_AGI_STUB_RANGES[agi_stub]
            return True, bracket
    for candidate in _semantic_name_candidates(variable):
        if candidate in values_by_column:
            return True, values_by_column[candidate]
    return False, None


def _filter_value_matches_source_value(
    variable: str,
    expected: Any,
    source_value: Any,
) -> bool:
    if _values_equal(source_value, expected):
        return True
    if _normalize_semantic_name(variable) != "incomerange":
        return False
    return _income_range_contains(expected, source_value)


def _income_range_contains(expected: Any, source_value: Any) -> bool:
    expected_bounds = _income_range_bounds(expected)
    source_bounds = _income_range_bounds(source_value)
    if expected_bounds is None or source_bounds is None:
        return False
    expected_lower, expected_upper = expected_bounds
    source_lower, source_upper = source_bounds
    if expected_lower is not None:
        if source_lower is None or source_lower < expected_lower:
            return False
    if expected_upper is not None:
        if source_upper is None or source_upper > expected_upper:
            return False
    return True


def _income_range_bounds(value: Any) -> tuple[float | None, float | None] | None:
    text = str(value).strip().lower()
    if text == "all":
        return (None, None)
    if text.startswith("under_"):
        upper = _income_range_number(text.removeprefix("under_"))
        return (None, upper) if upper is not None else None
    if text.endswith("_plus"):
        lower = _income_range_number(text.removesuffix("_plus"))
        return (lower, None) if lower is not None else None
    if "_to_" in text:
        lower_text, upper_text = text.split("_to_", 1)
        lower = _income_range_number(lower_text)
        upper = _income_range_number(upper_text)
        if lower is None or upper is None:
            return None
        return (lower, upper)
    return None


def _income_range_number(text: str) -> float | None:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([km])?", text)
    if match is None:
        return None
    value = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k":
        return value * 1_000
    if suffix == "m":
        return value * 1_000_000
    return value


def _constraint_evidenced_by_source_rows(
    rows: list[SourceRow],
    constraint: Any,
) -> bool:
    """Accept source-coded bands as evidence for interpreted bounds."""
    if _is_age_variable(str(constraint.variable)):
        return _age_band_constraint_matches(
            rows,
            constraint.operator,
            constraint.value,
        )
    if _source_row_bound_constraint_matches(
        rows,
        str(constraint.variable),
        constraint.operator,
        constraint.value,
    ):
        return True
    if not _is_adjusted_gross_income_variable(str(constraint.variable)):
        return False
    return _agi_stub_constraint_matches_rows(
        rows,
        constraint.operator,
        constraint.value,
    )


def _agi_stub_constraint_matches_rows(
    rows: list[SourceRow],
    operator: str,
    expected: Any,
) -> bool:
    ranges = [
        SOI_HISTORIC_TABLE_2_AGI_STUB_RANGES[agi_stub]
        for row in rows
        if (agi_stub := _source_row_agi_stub(row))
        in SOI_HISTORIC_TABLE_2_AGI_STUB_RANGES
    ]
    if len(ranges) != len(rows) or not ranges:
        return False

    expected_number = _number_or_none(expected)
    if expected_number is None:
        return False
    lower_bounds = [lower for _bracket, lower, _upper in ranges if lower is not None]
    upper_bounds = [upper for _bracket, _lower, upper in ranges if upper is not None]
    if operator == ">=":
        return len(lower_bounds) == len(ranges) and min(lower_bounds) == expected_number
    if operator == "<":
        return len(upper_bounds) == len(ranges) and max(upper_bounds) == expected_number
    return False


def _source_row_bound_constraint_matches(
    rows: list[SourceRow],
    variable: str,
    operator: str,
    expected: Any,
) -> bool:
    expected_number = _number_or_none(expected)
    if expected_number is None:
        return False
    if operator in {">", ">="}:
        values = _source_row_bound_values(rows, variable, "lower")
    elif operator in {"<", "<="}:
        values = _source_row_bound_values(rows, variable, "upper")
    else:
        return False
    return bool(values) and all(
        (source_number := _number_or_none(value)) is not None
        and source_number == expected_number
        for value in values
    )


def _source_row_bound_values(
    rows: list[SourceRow],
    variable: str,
    bound: str,
) -> list[Any]:
    values = []
    suffixes = (
        (f"{bound}_bound", f"{bound}bound", bound)
        if bound in {"lower", "upper"}
        else (bound,)
    )
    for row in rows:
        values_by_column = {
            _normalize_semantic_name(column): value
            for column, value in row.values.items()
        }
        matched = False
        for candidate in _semantic_name_candidates(variable):
            for suffix in suffixes:
                key = f"{candidate}{_normalize_semantic_name(suffix)}"
                if key in values_by_column:
                    values.append(values_by_column[key])
                    matched = True
                    break
            if matched:
                break
        if not matched:
            return []
    return values


def _agi_stub_constraint_matches(
    row: SourceRow,
    operator: str,
    expected: Any,
) -> bool:
    agi_stub = _source_row_agi_stub(row)
    if agi_stub not in SOI_HISTORIC_TABLE_2_AGI_STUB_RANGES:
        return False
    _bracket, lower, upper = SOI_HISTORIC_TABLE_2_AGI_STUB_RANGES[agi_stub]
    expected_number = _number_or_none(expected)
    if expected_number is None:
        return False
    if operator == ">=":
        return lower == expected_number
    if operator == "<":
        return upper == expected_number
    return False


def _age_band_constraint_matches(
    rows: list[SourceRow],
    operator: str,
    expected: Any,
) -> bool:
    ranges = [_source_row_age_range(row) for row in rows]
    if not ranges or any(age_range is None for age_range in ranges):
        return False
    return _age_band_constraint_range_matches(
        [age_range for age_range in ranges if age_range is not None],
        operator,
        expected,
    )


def _age_band_constraint_range_matches(
    ranges: list[tuple[int, int | None]],
    operator: str,
    expected: Any,
) -> bool:
    ranges = list(dict.fromkeys(ranges))
    sorted_ranges = sorted(
        ranges,
        key=lambda age_range: age_range[0],
    )
    for previous, current in zip(sorted_ranges, sorted_ranges[1:]):
        if previous[1] is None or previous[1] != current[0]:
            return False

    expected_number = _number_or_none(expected)
    if expected_number is None:
        return False
    lower = float(sorted_ranges[0][0])
    upper = sorted_ranges[-1][1]
    if operator == ">=":
        return lower == expected_number
    if operator == ">":
        return lower > expected_number
    if operator == "<":
        return upper is not None and float(upper) == expected_number
    if operator == "<=":
        return upper is not None and float(upper - 1) <= expected_number
    return False


def _filter_evidenced_by_source_cells(
    cells: list[SourceCell],
    variable: str,
    expected: Any,
) -> bool:
    if _normalize_semantic_name(variable) != "eitcchildcount":
        return False
    expected_count = _eitc_child_count_filter_value(expected)
    if expected_count is None:
        return False
    return any(
        source_count == expected_count
        for source_count in _source_cell_eitc_child_count_values(cells)
    )


def _eitc_child_count_constraint_matches_cells(
    cells: list[SourceCell],
    operator: str,
    expected: Any,
) -> bool:
    values = _source_cell_eitc_child_count_values(cells)
    return bool(values) and any(
        _constraint_matches_source_value(value, operator, expected) for value in values
    )


def _source_cell_eitc_child_count_values(cells: list[SourceCell]) -> list[int]:
    values = []
    for cell in cells:
        for value in (cell.raw_value, cell.display_value):
            if value is None:
                continue
            code = str(value).strip().upper()
            if code in SOI_EITC_CHILD_COUNT_COLUMN_VALUES:
                values.append(SOI_EITC_CHILD_COUNT_COLUMN_VALUES[code])
    return values


def _eitc_child_count_filter_value(value: Any) -> int | None:
    if isinstance(value, str) and value.strip().lower() in {"3plus", "3+"}:
        return 3
    number = _number_or_none(value)
    if number is None:
        return None
    return int(number)


def _source_cell_age_range(cell: SourceCell) -> tuple[int, int | None] | None:
    for value in (cell.raw_value, cell.display_value):
        if value is None:
            continue
        label = str(value).strip()
        match = re.fullmatch(r"POP_(\d+)", label, re.I)
        if not match:
            continue
        lower = int(match.group(1))
        if lower == 85:
            return lower, None
        return lower, lower + 1
    return None


def _source_row_age_range(row: SourceRow) -> tuple[int, int | None] | None:
    for variable in (
        "C_AGE_NAME",
        "AGE_NAME",
        "age_name",
        "Five year age bands",
        "age",
    ):
        matched, value = _source_row_value_without_interpretation(row, variable)
        if not matched or value is None:
            continue
        if isinstance(value, int | float) and not isinstance(value, bool):
            lower = int(value)
            if float(value) == lower:
                return lower, lower + 1
        label = str(value).strip()
        if re.fullmatch(r"\d+", label):
            lower = int(label)
            return lower, lower + 1
        match = re.search(
            r"\b(?:Aged?|Female)\s+(\d+)\s*(?:[-–]|to)\s*(\d+)\b",
            label,
            re.I,
        )
        if match:
            lower = int(match.group(1))
            upper = int(match.group(2)) + 1
            return lower, upper
        match = re.search(
            r"\b(?:Aged?|Female)\s+(\d+)\s+and\s+(\d+)\s+years?\b",
            label,
            re.I,
        )
        if match:
            lower = int(match.group(1))
            upper = int(match.group(2)) + 1
            return lower, upper
        match = re.search(
            r"\b(?:Aged?|Female)\s+(\d+)\s+years?\b",
            label,
            re.I,
        )
        if match:
            lower = int(match.group(1))
            return lower, lower + 1
        match = re.search(r"\bAged?\s+(\d+)\s+and\s+over\b", label, re.I)
        if match:
            return int(match.group(1)), None
        # Nomis writes its open top band as "Aged 85+", the same statement as
        # the "and over" wording above.
        match = re.search(r"\bAged?\s+(\d+)\s*\+", label, re.I)
        if match:
            return int(match.group(1)), None
        # ... and its open bottom band as "Aged under 1 year".
        match = re.search(r"\bunder\s+(\d+)\s+years?\b", label, re.I)
        if match:
            return 0, int(match.group(1))
    return None


def _source_row_agi_stub(row: SourceRow) -> int | None:
    matched, value = _source_row_value_without_interpretation(row, "AGI_STUB")
    if not matched:
        return None
    numeric = _number_or_none(value)
    if numeric is None:
        return None
    return int(numeric)


def _source_row_value_without_interpretation(
    row: SourceRow,
    variable: str,
) -> tuple[bool, Any]:
    values_by_column = {
        _normalize_semantic_name(column): value for column, value in row.values.items()
    }
    for candidate in _semantic_name_candidates(variable):
        if candidate in values_by_column:
            return True, values_by_column[candidate]
    return False, None


def _is_adjusted_gross_income_variable(variable: str) -> bool:
    return any(
        candidate
        in {
            "adjustedgrossincome",
            "agi",
        }
        for candidate in _semantic_name_candidates(variable)
    )


def _is_age_variable(variable: str) -> bool:
    return any(
        candidate
        in {
            "age",
            "personage",
        }
        for candidate in _semantic_name_candidates(variable)
    )


def _is_eitc_qualifying_children_variable(variable: str) -> bool:
    return any(
        candidate
        in {
            "earnedincomecreditqualifyingchildren",
            "eitcqualifyingchildren",
        }
        for candidate in _semantic_name_candidates(variable)
    )


def _semantic_name_candidates(name: str) -> tuple[str, ...]:
    parts = [name, *re.split(r"[.#/:]", name)]
    return tuple(
        dict.fromkeys(
            normalized
            for part in parts
            if (normalized := _normalize_semantic_name(part))
        )
    )


def _normalize_semantic_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _constraint_matches_source_value(
    source_value: Any,
    operator: str,
    expected: Any,
) -> bool:
    if operator == "==":
        return _values_equal(source_value, expected)
    if operator == "!=":
        return not _values_equal(source_value, expected)
    if operator == "in" and isinstance(expected, list | tuple | set):
        return any(_values_equal(source_value, item) for item in expected)

    source_number = _number_or_none(source_value)
    expected_number = _number_or_none(expected)
    if source_number is None or expected_number is None:
        return False
    if operator == ">":
        return source_number > expected_number
    if operator == ">=":
        return source_number >= expected_number
    if operator == "<":
        return source_number < expected_number
    if operator == "<=":
        return source_number <= expected_number
    return False


def _values_equal(left: Any, right: Any) -> bool:
    left_number = _number_or_none(left)
    right_number = _number_or_none(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return str(left) == str(right)


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return None
    return None


def _fact_count(report: ValidationReport, count_name: str) -> int:
    value = report.counts.get(count_name, {}).get("count", 0)
    return int(value)


def _prepare_output_dir(output_path: Path, *, replace: bool) -> None:
    if output_path.exists() and any(output_path.iterdir()):
        if not replace:
            raise FileExistsError(
                f"Build suite output directory is not empty: {output_path}"
            )
        if output_path.resolve() in {Path("/").resolve(), Path.home().resolve()}:
            raise ValueError(
                f"Refusing to replace unsafe output directory: {output_path}"
            )
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as file:
        for row in rows:
            file.write(json.dumps(row, sort_keys=True))
            file.write("\n")


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_package_sidecars(output_path: Path, *, source: str, year: int) -> None:
    files = [
        output_path / "source_rows.jsonl",
        output_path / "source_cells.jsonl",
        output_path / "source_regions.jsonl",
        output_path / "facts.jsonl",
        output_path / "consumer_facts.jsonl",
        output_path / CHRONICLE_DB_FILENAME,
        output_path / "reports" / "source_rows.json",
        output_path / "reports" / "source_cells.json",
        output_path / "reports" / "source_regions.json",
        output_path / "reports" / "selectors.json",
        output_path / "reports" / "source_records.json",
        output_path / "reports" / "facts.json",
        output_path / "reports" / "consumer_facts.json",
        output_path / "reports" / "concept_alignments.json",
        output_path / "reports" / "database.json",
        output_path / "reports" / "agent_acceptance.json",
        output_path / "reports" / "build_summary.json",
    ]
    resources = [_resource_descriptor(output_path, path) for path in files]
    datapackage_path = output_path / "datapackage.json"
    ro_crate_path = output_path / "ro-crate-metadata.json"

    _write_report(
        datapackage_path,
        {
            "profile": "data-package",
            "name": f"chronicle-{source}-{year}",
            "title": f"Chronicle build suite for {source} {year}",
            "resources": resources,
        },
    )
    _write_report(
        ro_crate_path,
        {
            "@context": "https://w3id.org/ro/crate/1.2/context",
            "@graph": [
                {
                    "@id": "./",
                    "@type": "Dataset",
                    "name": f"Chronicle build suite for {source} {year}",
                    "hasPart": [{"@id": resource["path"]} for resource in resources],
                },
                {
                    "@id": "ro-crate-metadata.json",
                    "@type": "CreativeWork",
                    "about": {"@id": "./"},
                    "conformsTo": {"@id": "https://w3id.org/ro/crate/1.2"},
                },
                *[
                    {
                        "@id": resource["path"],
                        "@type": "File",
                        "name": resource["name"],
                        "contentSize": resource["bytes"],
                        "sha256": resource["hash"],
                    }
                    for resource in resources
                ],
            ],
        },
    )


def _resource_descriptor(output_path: Path, path: Path) -> dict[str, Any]:
    rel_path = path.relative_to(output_path).as_posix()
    return {
        "name": rel_path.replace("/", "_").replace(".", "_"),
        "path": rel_path,
        "format": path.suffix.removeprefix("."),
        "mediatype": _mediatype(path),
        "bytes": path.stat().st_size,
        "hash": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mediatype(path: Path) -> str:
    if path.suffix == ".json":
        return "application/json"
    if path.suffix == ".jsonl":
        return "application/x-ndjson"
    if path.suffix == ".db":
        return "application/vnd.sqlite3"
    return "application/octet-stream"
