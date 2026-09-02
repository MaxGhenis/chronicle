"""Export deterministic Chronicle DB artifacts for hosted mirrors."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chronicle.env import default_chronicle_schema

LEDGER_MIRROR_TABLES = (
    "ledger_builds",
    "build_artifacts",
    "source_artifacts",
    "source_columns",
    "source_rows",
    "source_row_values",
    "source_cells",
    "source_records",
    "concept_alignments",
    "aggregate_facts",
    "aggregate_constraints",
    "fact_source_cells",
    "fact_source_rows",
)
LEDGER_MIRROR_PRIMARY_KEYS = {
    "ledger_builds": "build_id",
    "build_artifacts": "build_artifact_key",
    "source_artifacts": "artifact_sha256",
    "source_columns": "source_column_key",
    "source_rows": "source_row_key",
    "source_row_values": "source_row_value_key",
    "source_cells": "source_cell_key",
    "source_records": "source_record_id",
    "concept_alignments": (
        "source_concept,canonical_concept,relation,legal_vintage,"
        "period_type,period_value"
    ),
    "aggregate_facts": "fact_key",
    "aggregate_constraints": "fact_key,ordinal",
    "fact_source_cells": "fact_key,source_cell_key",
    "fact_source_rows": "fact_key,source_row_key",
}
JSON_COLUMNS = {
    "raw_value_json",
    "values_json",
    "value_json",
    "filters_json",
}


@dataclass(frozen=True)
class ChronicleTableExport:
    """One exported relational table."""

    table: str
    path: str
    row_count: int
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable export record."""
        return asdict(self)


@dataclass(frozen=True)
class ChronicleMirrorExportReport:
    """Report for a hosted-mirror export from a local Chronicle DB artifact."""

    db_path: str
    output_dir: str
    exported_at: str
    build_ids: tuple[str, ...]
    tables: tuple[ChronicleTableExport, ...]

    @property
    def table_count(self) -> int:
        """Number of exported tables."""
        return len(self.tables)

    @property
    def row_count(self) -> int:
        """Total exported row count."""
        return sum(table.row_count for table in self.tables)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable export report."""
        return {
            "db_path": self.db_path,
            "output_dir": self.output_dir,
            "exported_at": self.exported_at,
            "build_ids": list(self.build_ids),
            "table_count": self.table_count,
            "row_count": self.row_count,
            "tables": [table.to_dict() for table in self.tables],
        }


@dataclass(frozen=True)
class SupabaseTableLoad:
    """One Supabase mirror table load result."""

    table: str
    path: str
    row_count: int
    batch_count: int
    dry_run: bool
    manifest_row_count: int | None = None
    row_count_matches_manifest: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable load record."""
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class SupabaseMirrorLoadReport:
    """Report from loading Chronicle JSONL mirror files into Supabase/Postgres."""

    input_dir: str
    schema: str
    dry_run: bool
    tables: tuple[SupabaseTableLoad, ...]
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether the mirror load completed without errors."""
        return not self.errors

    @property
    def table_count(self) -> int:
        """Number of tables loaded or checked."""
        return len(self.tables)

    @property
    def row_count(self) -> int:
        """Total rows loaded or checked."""
        return sum(table.row_count for table in self.tables)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": self.valid,
            "input_dir": self.input_dir,
            "schema": self.schema,
            "dry_run": self.dry_run,
            "table_count": self.table_count,
            "row_count": self.row_count,
            "tables": [table.to_dict() for table in self.tables],
            "errors": list(self.errors),
        }


def export_chronicle_db_tables(
    db_path: str | Path,
    output_dir: str | Path,
    *,
    replace: bool = False,
) -> ChronicleMirrorExportReport:
    """Export Chronicle SQLite tables to JSONL files for bulk hosted loading."""
    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(f"Chronicle DB not found: {db}")

    output = Path(output_dir)
    _prepare_output_dir(output, replace=replace)

    exports: list[ChronicleTableExport] = []
    with sqlite3.connect(db) as connection:
        connection.row_factory = sqlite3.Row
        build_ids = tuple(
            row["build_id"]
            for row in connection.execute(
                "SELECT build_id FROM ledger_builds ORDER BY build_id"
            ).fetchall()
        )
        for table in LEDGER_MIRROR_TABLES:
            exports.append(_export_table(connection, output, table))

    report = ChronicleMirrorExportReport(
        db_path=str(db),
        output_dir=str(output),
        exported_at=datetime.now(timezone.utc).isoformat(),
        build_ids=build_ids,
        tables=tuple(exports),
    )
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def load_supabase_mirror(
    input_dir: str | Path,
    *,
    schema: str | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
    table_paths: dict[str, str | Path] | None = None,
    client: Any | None = None,
) -> SupabaseMirrorLoadReport:
    """Load exported Chronicle JSONL mirror files into Supabase/Postgres.

    ``schema`` defaults to :func:`chronicle.env.default_chronicle_schema`, so
    the writer that owns the hosted mirror answers to ``CHRONICLE_SCHEMA`` (and
    the ledger-era names behind it) exactly like every other reader of the
    setting. The resolved name is reported back in the load report.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1.")
    schema = schema or default_chronicle_schema()
    input_path = Path(input_dir)
    tables: list[SupabaseTableLoad] = []
    errors: list[str] = []
    if not input_path.exists():
        return SupabaseMirrorLoadReport(
            input_dir=str(input_path),
            schema=schema,
            dry_run=dry_run,
            tables=(),
            errors=(f"Input directory does not exist: {input_path}",),
        )
    manifest_counts = _load_mirror_manifest_counts(input_path)

    resolved_client = None
    if not dry_run:
        resolved_client = client or _get_supabase_client()

    for table in LEDGER_MIRROR_TABLES:
        path = (
            Path(table_paths.get(table, input_path / f"{table}.jsonl"))
            if table_paths
            else input_path / f"{table}.jsonl"
        )
        if not path.exists():
            errors.append(f"Missing mirror table JSONL: {path}")
            continue
        uses_default_path = path == input_path / f"{table}.jsonl"
        try:
            row_count, batch_count = _load_supabase_table(
                path,
                table=table,
                schema=schema,
                batch_size=batch_size,
                dry_run=dry_run,
                client=resolved_client,
            )
        except Exception as exc:  # pragma: no cover - exact client errors vary.
            errors.append(f"Could not load {table}: {exc}")
            continue
        manifest_row_count = manifest_counts.get(table) if uses_default_path else None
        row_count_matches_manifest = (
            row_count == manifest_row_count if manifest_row_count is not None else None
        )
        if row_count_matches_manifest is False:
            errors.append(
                "Mirror row count mismatch for "
                f"{table}: manifest={manifest_row_count}, actual={row_count}"
            )
        tables.append(
            SupabaseTableLoad(
                table=table,
                path=str(path),
                row_count=row_count,
                batch_count=batch_count,
                dry_run=dry_run,
                manifest_row_count=manifest_row_count,
                row_count_matches_manifest=row_count_matches_manifest,
            )
        )

    return SupabaseMirrorLoadReport(
        input_dir=str(input_path),
        schema=schema,
        dry_run=dry_run,
        tables=tuple(tables),
        errors=tuple(errors),
    )


def _export_table(
    connection: sqlite3.Connection,
    output_dir: Path,
    table: str,
) -> ChronicleTableExport:
    path = output_dir / f"{table}.jsonl"
    row_count = 0
    with path.open("w", encoding="utf-8") as file:
        rows = connection.execute(
            f"SELECT * FROM {table} ORDER BY {LEDGER_MIRROR_PRIMARY_KEYS[table]}"
        ).fetchall()
        for row in rows:
            payload = {key: _export_value(key, row[key]) for key in row.keys()}
            file.write(json.dumps(payload, sort_keys=True))
            file.write("\n")
            row_count += 1

    return ChronicleTableExport(
        table=table,
        path=str(path),
        row_count=row_count,
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
    )


def _export_value(column: str, value: Any) -> Any:
    if column in JSON_COLUMNS and isinstance(value, str):
        return json.loads(value)
    return value


def _prepare_output_dir(output_path: Path, *, replace: bool) -> None:
    if output_path.exists() and any(output_path.iterdir()):
        if not replace:
            raise FileExistsError(
                f"Mirror export output directory is not empty: {output_path}"
            )
        if output_path.resolve() in {Path("/").resolve(), Path.home().resolve()}:
            raise ValueError(
                f"Refusing to replace unsafe output directory: {output_path}"
            )
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)


def _load_mirror_manifest_counts(input_path: Path) -> dict[str, int]:
    manifest_path = input_path / "manifest.json"
    if not manifest_path.exists():
        return {}
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    tables = payload.get("tables")
    if not isinstance(tables, list):
        return {}
    counts: dict[str, int] = {}
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = table.get("table")
        row_count = table.get("row_count")
        if isinstance(table_name, str) and isinstance(row_count, int):
            counts[table_name] = row_count
    return counts


def _load_supabase_table(
    path: Path,
    *,
    table: str,
    schema: str,
    batch_size: int,
    dry_run: bool,
    client: Any | None,
) -> tuple[int, int]:
    row_count = 0
    batch_count = 0
    batch: list[dict[str, Any]] = []
    for row in _iter_jsonl(path):
        batch.append(row)
        row_count += 1
        if len(batch) >= batch_size:
            _upsert_supabase_batch(
                client,
                schema=schema,
                table=table,
                rows=batch,
                dry_run=dry_run,
            )
            batch_count += 1
            batch = []
    if batch:
        _upsert_supabase_batch(
            client,
            schema=schema,
            table=table,
            rows=batch,
            dry_run=dry_run,
        )
        batch_count += 1
    return row_count, batch_count


def _iter_jsonl(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL: {exc}") from exc


def _upsert_supabase_batch(
    client: Any | None,
    *,
    schema: str,
    table: str,
    rows: list[dict[str, Any]],
    dry_run: bool,
) -> None:
    if dry_run or not rows:
        return
    if client is None:
        raise ValueError("Supabase client is required for non-dry-run loads.")
    query = _supabase_table(client, schema, table)
    query.upsert(rows, on_conflict=LEDGER_MIRROR_PRIMARY_KEYS[table]).execute()


def _supabase_table(client: Any, schema: str, table: str) -> Any:
    schema_method = getattr(client, "schema", None)
    if callable(schema_method):
        return schema_method(schema).table(table)
    return client.table(table)


def _get_supabase_client() -> Any:
    from db.supabase_client import get_supabase_client

    return get_supabase_client()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
