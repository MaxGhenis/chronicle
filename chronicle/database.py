"""Build a relational Chronicle database artifact.

The database is a Chronicle-owned query surface for source-backed aggregate facts.
It is deterministic output from source artifacts, selectors, and aggregate facts;
hosted systems such as Supabase can mirror this schema later.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from chronicle.core import AggregateFact, build_aggregate_constraints, build_fact_key
from chronicle.epoch import (
    EMIT_EPOCH,
    HASH_DOMAINS,
    Epoch,
    hash_domain,
    schema_id,
)
from chronicle.sources.cells import (
    SourceCell,
    build_source_cell_key,
    source_cell_to_mapping,
)
from chronicle.sources.rows import (
    SourceColumn,
    SourceRow,
    SourceRowValue,
    build_source_column_key,
    build_source_row_key,
    build_source_row_value_key,
    source_columns_from_source_rows,
    source_row_to_mapping,
)

LEDGER_DB_SCHEMA_VERSION = schema_id("relational", Epoch.LEDGER)


@dataclass(frozen=True)
class ChronicleDbBuildReport:
    """Counts from building a relational Chronicle DB artifact."""

    build_id: str
    facts_count: int
    constraints_count: int
    source_records_count: int
    source_rows_count: int
    source_columns_count: int
    source_row_values_count: int
    source_cells_count: int
    source_artifacts_count: int

    def to_dict(self) -> dict[str, int | str]:
        """Return a JSON-serializable report."""
        return asdict(self)


def build_chronicle_db(
    facts: list[AggregateFact],
    db_path: str | Path,
    *,
    source_cells: list[SourceCell] | None = None,
    source_rows: list[SourceRow] | None = None,
    build_id: str | None = None,
    replace: bool = False,
    emit_epoch: Epoch = EMIT_EPOCH,
) -> ChronicleDbBuildReport:
    """Build a deterministic SQLite Chronicle database artifact."""
    path = Path(db_path)
    if path.exists():
        if not replace:
            raise FileExistsError(f"Chronicle DB already exists: {path}")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    cells = source_cells or []
    rows = source_rows or []
    columns = source_columns_from_source_rows(rows)
    source_row_values_count = sum(len(row.values) for row in rows)
    resolved_build_id = (
        HASH_DOMAINS["build"].key_for_epoch(build_id, emit_epoch)
        if build_id is not None
        else _build_id(facts, cells, rows, epoch=emit_epoch)
    )
    fact_constraints = [(fact, build_aggregate_constraints(fact)) for fact in facts]
    source_record_ids = {
        fact.source_record_id for fact in facts if fact.source_record_id is not None
    }
    artifact_sha256s = {
        *(cell.artifact.sha256 for cell in cells),
        *(row.artifact.sha256 for row in rows),
    }

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _create_schema(connection)
        _insert_build(
            connection,
            build_id=resolved_build_id,
            facts_count=len(facts),
            constraints_count=sum(
                len(constraints) for _, constraints in fact_constraints
            ),
            source_records_count=len(source_record_ids),
            source_rows_count=len(rows),
            source_columns_count=len(columns),
            source_row_values_count=source_row_values_count,
            source_cells_count=len(cells),
            source_artifacts_count=len(artifact_sha256s),
            emit_epoch=emit_epoch,
        )
        _insert_source_rows(connection, rows, emit_epoch=emit_epoch)
        _insert_source_columns(connection, columns, emit_epoch=emit_epoch)
        _insert_source_row_values(
            connection,
            rows,
            columns,
            emit_epoch=emit_epoch,
        )
        _insert_source_cells(connection, cells, emit_epoch=emit_epoch)
        _insert_concept_alignments(connection, facts, resolved_build_id)
        _insert_facts(
            connection,
            fact_constraints,
            resolved_build_id,
            emit_epoch=emit_epoch,
        )
        _create_indexes(connection)
        connection.commit()

    return ChronicleDbBuildReport(
        build_id=resolved_build_id,
        facts_count=len(facts),
        constraints_count=sum(len(constraints) for _, constraints in fact_constraints),
        source_records_count=len(source_record_ids),
        source_rows_count=len(rows),
        source_columns_count=len(columns),
        source_row_values_count=source_row_values_count,
        source_cells_count=len(cells),
        source_artifacts_count=len(artifact_sha256s),
    )


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE ledger_builds (
            build_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            facts_count INTEGER NOT NULL,
            constraints_count INTEGER NOT NULL,
            source_records_count INTEGER NOT NULL,
            source_rows_count INTEGER NOT NULL,
            source_columns_count INTEGER NOT NULL,
            source_row_values_count INTEGER NOT NULL,
            source_cells_count INTEGER NOT NULL,
            source_artifacts_count INTEGER NOT NULL
        );

        CREATE TABLE build_artifacts (
            build_artifact_key TEXT PRIMARY KEY,
            build_id TEXT NOT NULL REFERENCES ledger_builds(build_id),
            artifact_kind TEXT NOT NULL,
            artifact_name TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            r2_bucket TEXT,
            r2_key TEXT,
            r2_uri TEXT
        );

        CREATE TABLE source_artifacts (
            artifact_sha256 TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_file TEXT NOT NULL,
            url TEXT,
            vintage TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            extracted_at TEXT NOT NULL,
            extraction_method TEXT NOT NULL,
            raw_r2_bucket TEXT,
            raw_r2_key TEXT,
            raw_r2_uri TEXT
        );

        CREATE TABLE source_rows (
            source_row_key TEXT PRIMARY KEY,
            artifact_sha256 TEXT NOT NULL REFERENCES source_artifacts(artifact_sha256),
            sheet_name TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            values_json TEXT NOT NULL
        );

        CREATE TABLE source_columns (
            source_column_key TEXT PRIMARY KEY,
            artifact_sha256 TEXT NOT NULL REFERENCES source_artifacts(artifact_sha256),
            sheet_name TEXT NOT NULL,
            column_number INTEGER NOT NULL,
            raw_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL
        );

        CREATE TABLE source_row_values (
            source_row_value_key TEXT PRIMARY KEY,
            source_row_key TEXT NOT NULL REFERENCES source_rows(source_row_key),
            source_column_key TEXT NOT NULL REFERENCES source_columns(source_column_key),
            artifact_sha256 TEXT NOT NULL REFERENCES source_artifacts(artifact_sha256),
            sheet_name TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            column_number INTEGER NOT NULL,
            raw_column_name TEXT NOT NULL,
            normalized_column_name TEXT NOT NULL,
            value_json TEXT NOT NULL,
            value_text TEXT,
            value_numeric REAL
        );

        CREATE TABLE source_cells (
            source_cell_key TEXT PRIMARY KEY,
            artifact_sha256 TEXT NOT NULL REFERENCES source_artifacts(artifact_sha256),
            source_row_key TEXT REFERENCES source_rows(source_row_key),
            sheet_name TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            column_number INTEGER NOT NULL,
            address TEXT NOT NULL,
            cell_type TEXT NOT NULL,
            raw_value_json TEXT NOT NULL,
            raw_value_text TEXT,
            raw_value_numeric REAL,
            display_value TEXT,
            formula TEXT,
            note TEXT
        );

        CREATE TABLE source_records (
            source_record_id TEXT PRIMARY KEY,
            build_id TEXT NOT NULL REFERENCES ledger_builds(build_id),
            source_name TEXT,
            source_table TEXT,
            source_file TEXT,
            source_vintage TEXT,
            source_row_count INTEGER NOT NULL,
            source_cell_count INTEGER NOT NULL
        );

        CREATE TABLE concept_alignments (
            source_concept TEXT NOT NULL,
            canonical_concept TEXT NOT NULL,
            build_id TEXT NOT NULL REFERENCES ledger_builds(build_id),
            relation TEXT NOT NULL,
            authority TEXT,
            evidence_url TEXT,
            evidence_notes TEXT,
            legal_vintage TEXT,
            period_type TEXT,
            period_value TEXT,
            PRIMARY KEY (
                source_concept,
                canonical_concept,
                relation,
                legal_vintage,
                period_type,
                period_value
            )
        );

        CREATE TABLE aggregate_facts (
            fact_key TEXT PRIMARY KEY,
            build_id TEXT NOT NULL REFERENCES ledger_builds(build_id),
            source_record_id TEXT REFERENCES source_records(source_record_id),
            layout_record_set_id TEXT,
            layout_record_set_spec_id TEXT,
            layout_record_set_spec_hash TEXT,
            layout_groupby_dimension TEXT,
            layout_groupby_value_id TEXT,
            layout_groupby_value_label TEXT,
            layout_groupby_ordinal INTEGER,
            layout_measure_id TEXT,
            layout_measure_label TEXT,
            layout_measure_ordinal INTEGER,
            layout_source_row_id TEXT,
            layout_source_column_id TEXT,
            layout_table_record_kind TEXT,
            layout_parent_record_set_id TEXT,
            layout_total_record_id TEXT,
            value_json TEXT NOT NULL,
            value_text TEXT,
            value_numeric REAL,
            period_type TEXT NOT NULL,
            period_value TEXT NOT NULL,
            geography_level TEXT NOT NULL,
            geography_id TEXT NOT NULL,
            geography_vintage TEXT,
            geography_name TEXT,
            entity_name TEXT NOT NULL,
            entity_role TEXT,
            measure_concept TEXT NOT NULL,
            measure_source_concept TEXT,
            measure_concept_relation TEXT,
            measure_concept_authority TEXT,
            measure_concept_evidence_url TEXT,
            measure_concept_evidence_notes TEXT,
            measure_legal_vintage TEXT,
            measure_unit TEXT NOT NULL,
            aggregation_method TEXT NOT NULL,
            aggregation_denominator TEXT,
            domain TEXT NOT NULL,
            provenance_class TEXT NOT NULL,
            survey_instrument TEXT,
            assertion TEXT NOT NULL DEFAULT 'observation',
            period_coverage_json TEXT,
            filters_json TEXT NOT NULL,
            label TEXT,
            source_name TEXT,
            source_table TEXT,
            source_file TEXT,
            source_url TEXT,
            source_vintage TEXT,
            source_extracted_at TEXT,
            source_extraction_method TEXT,
            source_method_notes TEXT
        );

        CREATE TABLE aggregate_constraints (
            fact_key TEXT NOT NULL REFERENCES aggregate_facts(fact_key),
            ordinal INTEGER NOT NULL,
            variable TEXT NOT NULL,
            operator TEXT NOT NULL,
            value_json TEXT NOT NULL,
            value_text TEXT,
            value_numeric REAL,
            unit TEXT,
            role TEXT NOT NULL,
            label TEXT,
            PRIMARY KEY (fact_key, ordinal)
        );

        CREATE TABLE fact_source_cells (
            fact_key TEXT NOT NULL REFERENCES aggregate_facts(fact_key),
            source_cell_key TEXT NOT NULL REFERENCES source_cells(source_cell_key),
            ordinal INTEGER NOT NULL,
            PRIMARY KEY (fact_key, source_cell_key)
        );

        CREATE TABLE fact_source_rows (
            fact_key TEXT NOT NULL REFERENCES aggregate_facts(fact_key),
            source_row_key TEXT NOT NULL REFERENCES source_rows(source_row_key),
            ordinal INTEGER NOT NULL,
            PRIMARY KEY (fact_key, source_row_key)
        );
        """
    )


def _create_indexes(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE INDEX idx_aggregate_facts_source_record
            ON aggregate_facts(source_record_id);
        CREATE INDEX idx_build_artifacts_build
            ON build_artifacts(build_id);
        CREATE INDEX idx_source_artifacts_raw_r2_key
            ON source_artifacts(raw_r2_bucket, raw_r2_key);
        CREATE INDEX idx_source_rows_artifact_sheet_row
            ON source_rows(artifact_sha256, sheet_name, row_number);
        CREATE INDEX idx_source_columns_artifact_sheet_column
            ON source_columns(artifact_sha256, sheet_name, column_number);
        CREATE INDEX idx_source_columns_normalized_name
            ON source_columns(normalized_name);
        CREATE INDEX idx_source_row_values_row
            ON source_row_values(source_row_key);
        CREATE INDEX idx_source_row_values_column
            ON source_row_values(source_column_key);
        CREATE INDEX idx_source_row_values_text_lookup
            ON source_row_values(normalized_column_name, value_text);
        CREATE INDEX idx_source_row_values_numeric_lookup
            ON source_row_values(normalized_column_name, value_numeric);
        CREATE INDEX idx_source_cells_source_row
            ON source_cells(source_row_key);
        CREATE INDEX idx_aggregate_facts_record_set
            ON aggregate_facts(layout_record_set_id, layout_groupby_ordinal, layout_measure_ordinal);
        CREATE INDEX idx_aggregate_facts_measure
            ON aggregate_facts(measure_concept);
        CREATE INDEX idx_concept_alignments_canonical
            ON concept_alignments(canonical_concept);
        CREATE INDEX idx_aggregate_facts_period_geo
            ON aggregate_facts(period_type, period_value, geography_level, geography_id);
        CREATE INDEX idx_aggregate_constraints_variable
            ON aggregate_constraints(variable, operator);
        CREATE INDEX idx_fact_source_cells_cell
            ON fact_source_cells(source_cell_key);
        CREATE INDEX idx_fact_source_rows_row
            ON fact_source_rows(source_row_key);
        """
    )


def _insert_build(
    connection: sqlite3.Connection,
    *,
    build_id: str,
    facts_count: int,
    constraints_count: int,
    source_records_count: int,
    source_rows_count: int,
    source_columns_count: int,
    source_row_values_count: int,
    source_cells_count: int,
    source_artifacts_count: int,
    emit_epoch: Epoch,
) -> None:
    connection.execute(
        """
        INSERT INTO ledger_builds (
            build_id,
            schema_version,
            created_at,
            facts_count,
            constraints_count,
            source_records_count,
            source_rows_count,
            source_columns_count,
            source_row_values_count,
            source_cells_count,
            source_artifacts_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            build_id,
            schema_id("relational", emit_epoch),
            datetime.now(timezone.utc).isoformat(),
            facts_count,
            constraints_count,
            source_records_count,
            source_rows_count,
            source_columns_count,
            source_row_values_count,
            source_cells_count,
            source_artifacts_count,
        ),
    )


def _insert_source_cells(
    connection: sqlite3.Connection,
    cells: list[SourceCell],
    *,
    emit_epoch: Epoch,
) -> None:
    artifacts = {cell.artifact.sha256: cell.artifact for cell in cells}
    for artifact in artifacts.values():
        _insert_source_artifact(connection, artifact)

    for cell in cells:
        numeric_value = _numeric_value(cell.raw_value)
        connection.execute(
            """
            INSERT INTO source_cells (
                source_cell_key,
                artifact_sha256,
                source_row_key,
                sheet_name,
                row_number,
                column_number,
                address,
                cell_type,
                raw_value_json,
                raw_value_text,
                raw_value_numeric,
                display_value,
                formula,
                note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                build_source_cell_key(cell, epoch=emit_epoch),
                cell.artifact.sha256,
                (
                    HASH_DOMAINS["source_row"].key_for_epoch(
                        cell.source_row_key,
                        emit_epoch,
                    )
                    if cell.source_row_key is not None
                    else None
                ),
                cell.sheet_name,
                cell.row_number,
                cell.column_number,
                cell.address,
                cell.cell_type,
                _json_dumps(cell.raw_value),
                None if cell.raw_value is None else str(cell.raw_value),
                numeric_value,
                cell.display_value,
                cell.formula,
                cell.note,
            ),
        )


def _insert_source_rows(
    connection: sqlite3.Connection,
    rows: list[SourceRow],
    *,
    emit_epoch: Epoch,
) -> None:
    artifacts = {row.artifact.sha256: row.artifact for row in rows}
    for artifact in artifacts.values():
        _insert_source_artifact(connection, artifact)

    for row in rows:
        connection.execute(
            """
            INSERT INTO source_rows (
                source_row_key,
                artifact_sha256,
                sheet_name,
                row_number,
                values_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                build_source_row_key(row, epoch=emit_epoch),
                row.artifact.sha256,
                row.sheet_name,
                row.row_number,
                _json_dumps(row.values),
            ),
        )


def _insert_source_columns(
    connection: sqlite3.Connection,
    columns: list[SourceColumn],
    *,
    emit_epoch: Epoch,
) -> None:
    for column in columns:
        connection.execute(
            """
            INSERT INTO source_columns (
                source_column_key,
                artifact_sha256,
                sheet_name,
                column_number,
                raw_name,
                normalized_name
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                build_source_column_key(column, epoch=emit_epoch),
                column.artifact.sha256,
                column.sheet_name,
                column.column_number,
                column.raw_name,
                column.normalized_name,
            ),
        )


def _insert_source_row_values(
    connection: sqlite3.Connection,
    rows: list[SourceRow],
    columns: list[SourceColumn],
    *,
    emit_epoch: Epoch,
) -> None:
    column_keys = {
        (column.artifact.sha256, column.sheet_name, column.column_number): (
            build_source_column_key(column, epoch=emit_epoch),
            column.normalized_name,
        )
        for column in columns
    }
    insert_sql = """
        INSERT INTO source_row_values (
            source_row_value_key,
            source_row_key,
            source_column_key,
            artifact_sha256,
            sheet_name,
            row_number,
            column_number,
            raw_column_name,
            normalized_column_name,
            value_json,
            value_text,
            value_numeric
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    batch = []
    for row in rows:
        source_row_key = build_source_row_key(row, epoch=emit_epoch)
        for column_number, (raw_name, value) in enumerate(
            row.values.items(),
            start=1,
        ):
            source_column_key, normalized_name = column_keys[
                (row.artifact.sha256, row.sheet_name, column_number)
            ]
            row_value = SourceRowValue(
                source_row_key=source_row_key,
                source_column_key=source_column_key,
                row_number=row.row_number,
                column_number=column_number,
                raw_column_name=raw_name,
                normalized_column_name=normalized_name,
                value=value,
            )
            batch.append(
                (
                    build_source_row_value_key(row_value, epoch=emit_epoch),
                    source_row_key,
                    source_column_key,
                    row.artifact.sha256,
                    row.sheet_name,
                    row.row_number,
                    column_number,
                    raw_name,
                    normalized_name,
                    _json_dumps(value),
                    None if value is None else str(value),
                    _numeric_value(value),
                )
            )
            if len(batch) >= 10_000:
                connection.executemany(insert_sql, batch)
                batch = []
    if batch:
        connection.executemany(insert_sql, batch)


def _insert_source_artifact(
    connection: sqlite3.Connection,
    artifact: Any,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO source_artifacts (
            artifact_sha256,
            source_name,
            source_table,
            source_file,
            url,
            vintage,
            size_bytes,
            extracted_at,
            extraction_method,
            raw_r2_bucket,
            raw_r2_key,
            raw_r2_uri
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact.sha256,
            artifact.source_name,
            artifact.source_table,
            artifact.source_file,
            artifact.url,
            artifact.vintage,
            artifact.size_bytes,
            artifact.extracted_at,
            artifact.extraction_method,
            artifact.raw_r2_bucket,
            artifact.raw_r2_key,
            artifact.raw_r2_uri,
        ),
    )


def _insert_facts(
    connection: sqlite3.Connection,
    fact_constraints: list[tuple[AggregateFact, tuple[Any, ...]]],
    build_id: str,
    *,
    emit_epoch: Epoch,
) -> None:
    for fact, constraints in fact_constraints:
        fact_key = build_fact_key(fact, epoch=emit_epoch)
        source_cell_keys = _lineage_keys_for_epoch(
            "source_cell",
            fact.source_cell_keys,
            emit_epoch,
        )
        source_row_keys = _lineage_keys_for_epoch(
            "source_row",
            fact.source_row_keys,
            emit_epoch,
        )
        if fact.source_record_id is not None:
            connection.execute(
                """
                INSERT OR IGNORE INTO source_records (
                    source_record_id,
                    build_id,
                    source_name,
                    source_table,
                    source_file,
                    source_vintage,
                    source_row_count,
                    source_cell_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.source_record_id,
                    build_id,
                    fact.source.source_name,
                    fact.source.source_table,
                    fact.source.source_file,
                    fact.source.vintage,
                    len(source_row_keys),
                    len(source_cell_keys),
                ),
            )
        _insert_aggregate_fact(connection, fact, fact_key, build_id)
        for ordinal, constraint in enumerate(constraints):
            connection.execute(
                """
                INSERT INTO aggregate_constraints (
                    fact_key,
                    ordinal,
                    variable,
                    operator,
                    value_json,
                    value_text,
                    value_numeric,
                    unit,
                    role,
                    label
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact_key,
                    ordinal,
                    constraint.variable,
                    constraint.operator,
                    _json_dumps(constraint.value),
                    None if constraint.value is None else str(constraint.value),
                    _numeric_value(constraint.value),
                    constraint.unit,
                    constraint.role,
                    constraint.label,
                ),
            )
        for ordinal, source_cell_key in enumerate(source_cell_keys):
            connection.execute(
                """
                INSERT INTO fact_source_cells (
                    fact_key,
                    source_cell_key,
                    ordinal
                )
                VALUES (?, ?, ?)
                """,
                (
                    fact_key,
                    source_cell_key,
                    ordinal,
                ),
            )
        for ordinal, source_row_key in enumerate(source_row_keys):
            connection.execute(
                """
                INSERT INTO fact_source_rows (
                    fact_key,
                    source_row_key,
                    ordinal
                )
                VALUES (?, ?, ?)
                """,
                (
                    fact_key,
                    source_row_key,
                    ordinal,
                ),
            )


def _lineage_keys_for_epoch(
    domain_name: str,
    keys: tuple[str, ...],
    emit_epoch: Epoch,
) -> tuple[str, ...]:
    """Canonicalize lineage aliases and retain their first-seen order."""
    return tuple(
        dict.fromkeys(
            HASH_DOMAINS[domain_name].key_for_epoch(key, emit_epoch) for key in keys
        )
    )


def _insert_concept_alignments(
    connection: sqlite3.Connection,
    facts: list[AggregateFact],
    build_id: str,
) -> None:
    seen: set[tuple[str, str, str, str | None, str, str]] = set()
    for fact in facts:
        measure = fact.measure
        if not measure.source_concept or not measure.concept_relation:
            continue
        key = (
            measure.source_concept,
            measure.concept,
            measure.concept_relation,
            measure.legal_vintage,
            fact.period.type,
            str(fact.period.value),
        )
        if key in seen:
            continue
        seen.add(key)
        connection.execute(
            """
            INSERT INTO concept_alignments (
                source_concept,
                canonical_concept,
                build_id,
                relation,
                authority,
                evidence_url,
                evidence_notes,
                legal_vintage,
                period_type,
                period_value
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                measure.source_concept,
                measure.concept,
                build_id,
                measure.concept_relation,
                measure.concept_authority,
                measure.concept_evidence_url,
                measure.concept_evidence_notes,
                measure.legal_vintage,
                fact.period.type,
                str(fact.period.value),
            ),
        )


def _insert_aggregate_fact(
    connection: sqlite3.Connection,
    fact: AggregateFact,
    fact_key: str,
    build_id: str,
) -> None:
    layout = fact.layout
    connection.execute(
        """
        INSERT INTO aggregate_facts (
            fact_key,
            build_id,
            source_record_id,
            layout_record_set_id,
            layout_record_set_spec_id,
            layout_record_set_spec_hash,
            layout_groupby_dimension,
            layout_groupby_value_id,
            layout_groupby_value_label,
            layout_groupby_ordinal,
            layout_measure_id,
            layout_measure_label,
            layout_measure_ordinal,
            layout_source_row_id,
            layout_source_column_id,
            layout_table_record_kind,
            layout_parent_record_set_id,
            layout_total_record_id,
            value_json,
            value_text,
            value_numeric,
            period_type,
            period_value,
            geography_level,
            geography_id,
            geography_vintage,
            geography_name,
            entity_name,
            entity_role,
            measure_concept,
            measure_source_concept,
            measure_concept_relation,
            measure_concept_authority,
            measure_concept_evidence_url,
            measure_concept_evidence_notes,
            measure_legal_vintage,
            measure_unit,
            aggregation_method,
            aggregation_denominator,
            domain,
            provenance_class,
            survey_instrument,
            assertion,
            period_coverage_json,
            filters_json,
            label,
            source_name,
            source_table,
            source_file,
            source_url,
            source_vintage,
            source_extracted_at,
            source_extraction_method,
            source_method_notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fact_key,
            build_id,
            fact.source_record_id,
            layout.record_set_id if layout else None,
            layout.record_set_spec_id if layout else None,
            layout.record_set_spec_hash if layout else None,
            layout.groupby_dimension if layout else None,
            layout.groupby_value_id if layout else None,
            layout.groupby_value_label if layout else None,
            layout.groupby_ordinal if layout else None,
            layout.measure_id if layout else None,
            layout.measure_label if layout else None,
            layout.measure_ordinal if layout else None,
            layout.source_row_id if layout else None,
            layout.source_column_id if layout else None,
            layout.table_record_kind if layout else None,
            layout.parent_record_set_id if layout else None,
            layout.total_record_id if layout else None,
            _json_dumps(fact.value),
            str(fact.value),
            _numeric_value(fact.value),
            fact.period.type,
            str(fact.period.value),
            fact.geography.level,
            fact.geography.id,
            fact.geography.vintage,
            fact.geography.name,
            fact.entity.name,
            fact.entity.role,
            fact.measure.concept,
            fact.measure.source_concept,
            fact.measure.concept_relation,
            fact.measure.concept_authority,
            fact.measure.concept_evidence_url,
            fact.measure.concept_evidence_notes,
            fact.measure.legal_vintage,
            fact.measure.unit,
            fact.aggregation.method,
            fact.aggregation.denominator,
            fact.domain,
            fact.provenance_class,
            fact.survey_instrument,
            fact.assertion,
            (
                _json_dumps(
                    {
                        key: value
                        for key, value in asdict(fact.period_coverage).items()
                        if value is not None
                    }
                )
                if fact.period_coverage is not None
                else None
            ),
            _json_dumps(fact.filters),
            fact.label,
            fact.source.source_name,
            fact.source.source_table,
            fact.source.source_file,
            fact.source.url,
            fact.source.vintage,
            fact.source.extracted_at,
            fact.source.extraction_method,
            fact.source.method_notes,
        ),
    )


def _build_id(
    facts: list[AggregateFact],
    cells: list[SourceCell],
    rows: list[SourceRow],
    *,
    epoch: Epoch = EMIT_EPOCH,
) -> str:
    digest = hashlib.sha256()
    _update_build_hash(digest, "schema", {"version": LEDGER_DB_SCHEMA_VERSION})
    for fact in sorted(
        facts,
        key=lambda item: build_fact_key(item, epoch=Epoch.LEDGER),
    ):
        _update_build_hash(
            digest,
            "fact",
            {
                "fact_key": build_fact_key(fact, epoch=Epoch.LEDGER),
                "fact": _canonical_fact_mapping(fact),
                "constraints": [
                    asdict(constraint)
                    for constraint in build_aggregate_constraints(fact)
                ],
            },
        )
    for cell in sorted(
        cells,
        key=lambda item: build_source_cell_key(item, epoch=Epoch.LEDGER),
    ):
        _update_build_hash(
            digest,
            "source_cell",
            {
                "source_cell_key": build_source_cell_key(
                    cell,
                    epoch=Epoch.LEDGER,
                ),
                "source_cell": _canonical_source_cell_mapping(cell),
            },
        )
    for row in sorted(
        rows,
        key=lambda item: build_source_row_key(item, epoch=Epoch.LEDGER),
    ):
        _update_build_hash(
            digest,
            "source_row",
            {
                "source_row_key": build_source_row_key(
                    row,
                    epoch=Epoch.LEDGER,
                ),
                "source_row": source_row_to_mapping(row),
            },
        )
    return f"{hash_domain('build', epoch)}:{digest.hexdigest()[:24]}"


def _canonical_fact_mapping(fact: AggregateFact) -> dict[str, Any]:
    """Return a build-hash payload independent of accepted key epochs."""
    mapping = asdict(fact)
    mapping["source_cell_keys"] = tuple(
        HASH_DOMAINS["source_cell"].key_for_epoch(key, Epoch.LEDGER)
        for key in fact.source_cell_keys
    )
    mapping["source_row_keys"] = tuple(
        HASH_DOMAINS["source_row"].key_for_epoch(key, Epoch.LEDGER)
        for key in fact.source_row_keys
    )
    return mapping


def _canonical_source_cell_mapping(cell: SourceCell) -> dict[str, Any]:
    """Return a source-cell build payload independent of row-key epoch."""
    mapping = source_cell_to_mapping(cell)
    if cell.source_row_key is not None:
        mapping["source_row_key"] = HASH_DOMAINS["source_row"].key_for_epoch(
            cell.source_row_key,
            Epoch.LEDGER,
        )
    return mapping


def _update_build_hash(
    digest: Any,
    record_type: str,
    payload: dict[str, Any],
) -> None:
    digest.update(record_type.encode("utf-8"))
    digest.update(b"\0")
    digest.update(_json_dumps(payload).encode("utf-8"))
    digest.update(b"\n")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    return None
