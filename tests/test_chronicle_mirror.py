"""Tests for hosted-mirror exports from Chronicle DB artifacts."""

from __future__ import annotations

import json

import pytest

from chronicle.env import ChronicleEnvDeprecationWarning
from chronicle.harness import main as harness_main
from chronicle.mirror import (
    LEDGER_MIRROR_TABLES,
    export_chronicle_db_tables,
    load_supabase_mirror,
)
from chronicle.database import build_chronicle_db
from chronicle.jurisdictions.us.soi import (
    build_soi_table_1_1_source_cells,
    build_soi_table_1_1_facts,
)


def test_export_chronicle_db_tables_writes_jsonl_and_manifest(tmp_path):
    db_path = tmp_path / "chronicle.db"
    output_dir = tmp_path / "mirror"
    build_chronicle_db(
        build_soi_table_1_1_facts(2023),
        db_path,
        source_cells=build_soi_table_1_1_source_cells(2023),
    )

    report = export_chronicle_db_tables(db_path, output_dir)
    manifest = json.loads((output_dir / "manifest.json").read_text())

    assert report.table_count == len(LEDGER_MIRROR_TABLES)
    assert manifest["table_count"] == len(LEDGER_MIRROR_TABLES)
    assert {table["table"] for table in manifest["tables"]} == set(LEDGER_MIRROR_TABLES)
    assert (output_dir / "source_cells.jsonl").exists()
    assert (output_dir / "aggregate_facts.jsonl").exists()
    assert (output_dir / "build_artifacts.jsonl").exists()

    first_cell = json.loads(
        (output_dir / "source_cells.jsonl").read_text().splitlines()[0]
    )
    first_artifact = json.loads(
        (output_dir / "source_artifacts.jsonl").read_text().splitlines()[0]
    )
    first_fact = json.loads(
        (output_dir / "aggregate_facts.jsonl").read_text().splitlines()[0]
    )

    assert first_cell["artifact_sha256"]
    assert "raw_r2_key" in first_artifact
    assert "raw_value_json" in first_cell
    assert isinstance(first_fact["filters_json"], dict)


def test_export_chronicle_db_tables_orders_rows_deterministically(tmp_path):
    db_path = tmp_path / "chronicle.db"
    first_output_dir = tmp_path / "mirror-first"
    second_output_dir = tmp_path / "mirror-second"
    build_chronicle_db(
        build_soi_table_1_1_facts(2023),
        db_path,
        source_cells=build_soi_table_1_1_source_cells(2023),
    )

    first_report = export_chronicle_db_tables(db_path, first_output_dir)
    second_report = export_chronicle_db_tables(db_path, second_output_dir)

    first_hashes = {table.table: table.sha256 for table in first_report.tables}
    second_hashes = {table.table: table.sha256 for table in second_report.tables}

    assert first_hashes == second_hashes


def test_export_db_tables_cli_emits_manifest_summary(tmp_path, capsys):
    db_path = tmp_path / "chronicle.db"
    output_dir = tmp_path / "mirror"
    build_chronicle_db(
        build_soi_table_1_1_facts(2023),
        db_path,
        source_cells=build_soi_table_1_1_source_cells(2023),
    )

    exit_code = harness_main(
        [
            "export-db-tables",
            "--db",
            str(db_path),
            "--out",
            str(output_dir),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["output_dir"] == str(output_dir)
    assert payload["table_count"] == len(LEDGER_MIRROR_TABLES)
    assert payload["row_count"] > 0
    assert (output_dir / "manifest.json").exists()


def test_load_supabase_mirror_dry_run_counts_exported_rows(tmp_path):
    db_path = tmp_path / "chronicle.db"
    output_dir = tmp_path / "mirror"
    build_chronicle_db(
        build_soi_table_1_1_facts(2023),
        db_path,
        source_cells=build_soi_table_1_1_source_cells(2023),
    )
    export_report = export_chronicle_db_tables(db_path, output_dir)

    load_report = load_supabase_mirror(output_dir, dry_run=True, batch_size=25)

    assert load_report.valid
    assert load_report.dry_run
    assert load_report.table_count == len(LEDGER_MIRROR_TABLES)
    assert load_report.row_count == export_report.row_count
    assert all(table.row_count_matches_manifest for table in load_report.tables)


def test_load_supabase_mirror_uses_schema_upserts_and_build_artifact_override(
    tmp_path,
):
    mirror_dir = tmp_path / "mirror"
    mirror_dir.mkdir()
    for table in LEDGER_MIRROR_TABLES:
        (mirror_dir / f"{table}.jsonl").write_text("")
    build_artifacts_path = tmp_path / "build_artifacts.jsonl"
    build_artifacts_path.write_text(
        json.dumps(
            {
                "build_artifact_key": "ledger.build_artifact.v1:test",
                "build_id": "ledger.build.v1:test",
                "artifact_kind": "json",
                "artifact_name": "reports/build_summary.json",
                "sha256": "abc",
                "size_bytes": 3,
                "r2_bucket": "ledger-derived",
                "r2_key": "derived/test",
                "r2_uri": "r2://ledger-derived/derived/test",
            },
            sort_keys=True,
        )
        + "\n"
    )
    client = _FakeSupabaseClient()

    report = load_supabase_mirror(
        mirror_dir,
        table_paths={"build_artifacts": build_artifacts_path},
        client=client,
    )

    assert report.valid
    assert report.row_count == 1
    build_artifact_load = next(
        table for table in report.tables if table.table == "build_artifacts"
    )
    assert build_artifact_load.manifest_row_count is None
    assert build_artifact_load.row_count_matches_manifest is None
    assert client.upserts == [
        (
            "ledger",
            "build_artifacts",
            "build_artifact_key",
            ["ledger.build_artifact.v1:test"],
        )
    ]


def test_load_supabase_mirror_cli_dry_run(tmp_path, capsys):
    mirror_dir = tmp_path / "mirror"
    mirror_dir.mkdir()
    for table in LEDGER_MIRROR_TABLES:
        (mirror_dir / f"{table}.jsonl").write_text("")

    exit_code = harness_main(
        [
            "load-supabase-mirror",
            "--dir",
            str(mirror_dir),
            "--dry-run",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["valid"]
    assert payload["dry_run"]
    assert payload["table_count"] == len(LEDGER_MIRROR_TABLES)


# ---------------------------------------------------------------------------
# Schema configuration
#
# The mirror loader is the primary writer into the hosted schema, so it is the
# call site CHRONICLE_SCHEMA has to reach (PolicyEngine/chronicle#143,
# mechanism 3). It defaulted to the literal "ledger" while only the read-side
# client honored the renamed variable, which would have sent a rehearsal load
# into production the moment an operator set it.
# ---------------------------------------------------------------------------


def _empty_mirror(tmp_path):
    mirror_dir = tmp_path / "mirror"
    mirror_dir.mkdir()
    for table in LEDGER_MIRROR_TABLES:
        (mirror_dir / f"{table}.jsonl").write_text("")
    return mirror_dir


def _one_build_artifact(tmp_path):
    path = tmp_path / "build_artifacts.jsonl"
    path.write_text(
        json.dumps(
            {
                "build_artifact_key": "ledger.build_artifact.v1:test",
                "build_id": "ledger.build.v1:test",
                "artifact_kind": "json",
                "artifact_name": "reports/build_summary.json",
                "sha256": "abc",
                "size_bytes": 3,
                "r2_bucket": "ledger-derived",
                "r2_key": "derived/test",
                "r2_uri": "r2://ledger-derived/derived/test",
            },
            sort_keys=True,
        )
        + "\n"
    )
    return path


def _load_into_fake_client(tmp_path, **kwargs):
    client = _FakeSupabaseClient()
    report = load_supabase_mirror(
        _empty_mirror(tmp_path),
        table_paths={"build_artifacts": _one_build_artifact(tmp_path)},
        client=client,
        **kwargs,
    )
    return report, client


def test_load_supabase_mirror_defaults_to_the_ledger_schema(tmp_path):
    report, client = _load_into_fake_client(tmp_path)

    assert report.schema == "ledger"
    assert [upsert[0] for upsert in client.upserts] == ["ledger"]


def test_load_supabase_mirror_writes_to_the_chronicle_schema(tmp_path, monkeypatch):
    """The renamed variable configures the writer, not just the reader."""
    monkeypatch.setenv("CHRONICLE_SCHEMA", "chronicle_probe")

    report, client = _load_into_fake_client(tmp_path)

    assert report.schema == "chronicle_probe"
    assert [upsert[0] for upsert in client.upserts] == ["chronicle_probe"]


@pytest.mark.parametrize("name", ["POLICYENGINE_LEDGER_SCHEMA", "LEDGER_SCHEMA"])
def test_load_supabase_mirror_honors_a_ledger_era_schema_name(
    tmp_path, monkeypatch, name
):
    monkeypatch.setenv(name, "legacy_probe")

    with pytest.warns(ChronicleEnvDeprecationWarning):
        report, client = _load_into_fake_client(tmp_path)

    assert report.schema == "legacy_probe"
    assert [upsert[0] for upsert in client.upserts] == ["legacy_probe"]


def test_an_explicit_schema_still_wins_over_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CHRONICLE_SCHEMA", "chronicle_probe")

    report, client = _load_into_fake_client(tmp_path, schema="explicit_probe")

    assert report.schema == "explicit_probe"
    assert [upsert[0] for upsert in client.upserts] == ["explicit_probe"]


def test_load_supabase_mirror_cli_writes_to_the_configured_schema(
    tmp_path, monkeypatch, capsys
):
    """The CLI resolves the same way when no --schema is supplied."""
    client = _FakeSupabaseClient()
    monkeypatch.setattr("chronicle.mirror._get_supabase_client", lambda: client)
    monkeypatch.setenv("CHRONICLE_SCHEMA", "chronicle_probe")
    argv = [
        "load-supabase-mirror",
        "--dir",
        str(_empty_mirror(tmp_path)),
        "--build-artifacts",
        str(_one_build_artifact(tmp_path)),
    ]

    exit_code = harness_main(argv)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == "chronicle_probe"
    assert [upsert[0] for upsert in client.upserts] == ["chronicle_probe"]

    assert harness_main([*argv, "--schema", "explicit_probe"]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "explicit_probe"
    assert [upsert[0] for upsert in client.upserts] == [
        "chronicle_probe",
        "explicit_probe",
    ]


class _FakeSupabaseClient:
    def __init__(self):
        self.upserts = []

    def schema(self, schema):
        return _FakeSupabaseSchema(self, schema)


class _FakeSupabaseSchema:
    def __init__(self, client, schema):
        self.client = client
        self.schema = schema

    def table(self, table):
        return _FakeSupabaseTable(self.client, self.schema, table)


class _FakeSupabaseTable:
    def __init__(self, client, schema, table):
        self.client = client
        self.schema = schema
        self.table = table

    def upsert(self, rows, *, on_conflict):
        self.client.upserts.append(
            (
                self.schema,
                self.table,
                on_conflict,
                [row.get("build_artifact_key") for row in rows],
            )
        )
        return self

    def execute(self):
        return None
