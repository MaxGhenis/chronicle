"""Tests for Chronicle source artifact acquisition and storage metadata."""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest
import yaml

from chronicle.cli import main as cli_main
from chronicle.artifacts import (
    SourceArtifactRevisionError,
    build_artifact_rows,
    build_derived_r2_key,
    bootstrap_r2_buckets,
    build_r2_key,
    fetch_source_artifact,
    infer_r2_country,
    infer_build_id,
    inventory_source_artifacts,
    publish_derived_artifacts,
    publish_source_artifacts,
)
from chronicle.harness import main as harness_main


def test_build_r2_key_is_content_addressed():
    key = build_r2_key(
        source_id="irs_soi",
        package_id="soi-table-1-2",
        year=2023,
        sha256="abc123",
        filename="23in12ms.xls",
    )

    assert key == "raw/irs_soi/soi-table-1-2/2023/abc123/23in12ms.xls"


def test_build_derived_r2_key_is_build_scoped():
    key = build_derived_r2_key(
        source_id="irs_soi",
        package_id="soi-table-1-1",
        year=2023,
        build_id="ledger.build.v1:abc123",
        artifact_name="reports/build_summary.json",
    )

    assert key == (
        "derived/irs_soi/soi-table-1-1/2023/"
        "ledger.build.v1:abc123/reports/build_summary.json"
    )


@pytest.mark.parametrize(
    ("source_id", "package_path", "expected_country"),
    [
        ("ird", "db/data/ird/wff", "nz"),
        ("ons", "db/data/ons/mye", "uk"),
        ("irs_soi", "db/data/irs_soi/table_1_1", None),
        ("irs_soi", "/work/ons/chronicle/db/data/irs_soi/table_1_1", None),
        ("ird", "/work/ons/chronicle/db/data/ird/wff", "nz"),
        ("ird", "/work/packages/ons/chronicle/db/data/ird/wff", "nz"),
        ("ird", "/work/ons/chronicle/packages/ird/wff", "nz"),
        ("ird", "/repo/db/data/ird/packages", "nz"),
        ("ird", "/repo/db/data/ird/packages/manifest.yaml", "nz"),
        ("ird", "/repo/packages/ird/packages/source_package.yaml", "nz"),
        ("irs_soi", "/repo/db/data/irs_soi/packages/manifest.yaml", None),
    ],
)
def test_infer_r2_country_uses_publisher_directory(
    source_id, package_path, expected_country
):
    assert (
        infer_r2_country(source_id=source_id, package_path=package_path)
        == expected_country
    )


def test_country_aware_r2_keys_preserve_legacy_us_layout():
    nz_key = build_r2_key(
        source_id="ird",
        package_id="ird-working-for-families-statistics-sept-2025",
        year=2024,
        sha256="abc123",
        filename="wff.xlsx",
    )
    uk_key = build_derived_r2_key(
        source_id="ons",
        package_id="ons-mye-2024-uk",
        year=2024,
        build_id="ledger.build.v1:abc123",
        artifact_name="facts.jsonl",
    )

    assert nz_key.startswith("raw/nz/ird/")
    assert uk_key.startswith("derived/uk/ons/")


def test_country_aware_r2_prefix_rejects_wrong_country():
    with pytest.raises(ValueError, match="disagrees with publisher country"):
        build_r2_key(
            source_id="ird",
            package_id="wff",
            year=2024,
            sha256="abc123",
            filename="wff.xlsx",
            prefix="raw/uk",
        )


def test_fetch_source_artifact_writes_manifest_and_inventory(tmp_path):
    source = tmp_path / "source.xls"
    content = b"chronicle artifact fixture"
    source.write_bytes(content)
    output_dir = tmp_path / "data" / "irs_soi" / "table_1_2"

    report = fetch_source_artifact(
        str(source),
        source_id="irs_soi",
        package_id="soi-table-1-2",
        year=2023,
        output_dir=output_dir,
        source_page="https://example.test/source-page",
        table="Publication 1304 Table 1.2",
    )

    expected_sha = hashlib.sha256(content).hexdigest()
    manifest = yaml.safe_load((output_dir / "manifest.yaml").read_text())
    inventory = inventory_source_artifacts(output_dir)

    assert report.valid
    assert report.sha256 == expected_sha
    assert (output_dir / "source.xls").read_bytes() == content
    assert manifest["source_id"] == "irs_soi"
    assert manifest["package_id"] == "soi-table-1-2"
    assert manifest["files"][2023]["sha256"] == expected_sha
    assert manifest["files"][2023]["source_url"] == str(source)
    assert inventory.valid
    assert inventory.counts == {
        "artifact_count": 1,
        "checksum_mismatch_count": 0,
        "manifest_count": 1,
        "missing_count": 0,
        "r2_link_count": 0,
    }


def test_fetch_rejects_wrong_country_before_reading_or_overwriting_cache(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"original official artifact")
    output_dir = tmp_path / "db" / "data" / "ird" / "wff"
    fetch_source_artifact(
        str(source),
        source_id="ird",
        package_id="ird-wff",
        year=2024,
        output_dir=output_dir,
    )
    artifact_path = output_dir / source.name
    manifest_path = output_dir / "manifest.yaml"
    original_artifact = artifact_path.read_bytes()
    original_manifest = manifest_path.read_bytes()

    def unexpected_read(_source_url):
        raise AssertionError("A rejected route must not read the source artifact")

    monkeypatch.setattr("chronicle.artifacts._read_artifact", unexpected_read)
    with pytest.raises(ValueError, match="disagrees with publisher country"):
        fetch_source_artifact(
            str(source),
            source_id="ird",
            package_id="ird-wff",
            year=2024,
            output_dir=output_dir,
            r2_prefix="raw/uk",
        )

    assert artifact_path.read_bytes() == original_artifact
    assert manifest_path.read_bytes() == original_manifest


def test_publish_source_artifacts_uploads_manifest_entries(tmp_path):
    output_dir = tmp_path / "data" / "irs_soi" / "table_1_2"
    source = tmp_path / "source.xls"
    source.write_bytes(b"raw artifact")
    fetch_source_artifact(
        str(source),
        source_id="irs_soi",
        package_id="soi-table-1-2",
        year=2023,
        output_dir=output_dir,
    )
    log = tmp_path / "wrangler.log"
    wrangler = tmp_path / "wrangler"
    wrangler.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\necho ok\n")
    wrangler.chmod(0o755)

    report = publish_source_artifacts(output_dir, wrangler_command=str(wrangler))
    manifest = yaml.safe_load((output_dir / "manifest.yaml").read_text())
    storage = manifest["files"][2023]["storage"]["r2"]

    assert report.valid
    assert report.counts == {
        "artifact_count": 1,
        "failed_count": 0,
        "manifest_count": 1,
        "r2_link_count": 1,
        "uploaded_count": 1,
    }
    assert storage["bucket"] == "ledger-raw"
    assert storage["key"].startswith("raw/irs_soi/soi-table-1-2/2023/")
    assert "ledger-raw/raw/irs_soi/soi-table-1-2/2023/" in log.read_text()


def test_publish_source_artifacts_handles_label_year_entries(tmp_path):
    output_dir = tmp_path / "data" / "ssa" / "ssi_monthly_statistics_2024_12"
    source = tmp_path / "table01.csv"
    source.write_bytes(b"csv artifact")
    fetch_source_artifact(
        str(source),
        source_id="ssa",
        package_id="ssa-ssi-monthly-statistics-2024-12",
        year=2024,
        output_dir=output_dir,
    )
    capture = output_dir / "table01.html"
    capture_bytes = b"<html>capture</html>"
    capture.write_bytes(capture_bytes)
    manifest_path = output_dir / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["files"]["source_capture"] = {
        "filename": capture.name,
        "sha256": hashlib.sha256(capture_bytes).hexdigest(),
        "size_bytes": len(capture_bytes),
    }
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    log = tmp_path / "wrangler.log"
    wrangler = tmp_path / "wrangler"
    wrangler.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\necho ok\n")
    wrangler.chmod(0o755)

    report = publish_source_artifacts(output_dir, wrangler_command=str(wrangler))
    manifest = yaml.safe_load(manifest_path.read_text())
    storage = manifest["files"]["source_capture"]["storage"]["r2"]

    assert report.valid
    assert report.counts["uploaded_count"] == 2
    assert report.counts["failed_count"] == 0
    assert storage["key"] == (
        "raw/ssa/ssa-ssi-monthly-statistics-2024-12/source_capture/"
        f"{hashlib.sha256(capture_bytes).hexdigest()}/table01.html"
    )


def test_publish_source_artifacts_uses_country_for_each_manifest(tmp_path):
    data_root = tmp_path / "ons" / "chronicle" / "db" / "data"
    log = tmp_path / "wrangler.log"
    wrangler = tmp_path / "wrangler"
    wrangler.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\necho ok\n")
    wrangler.chmod(0o755)
    for publisher, package_id, year in (
        ("ird", "ird-wff", 2024),
        ("ons", "ons-mye", 2024),
        ("irs_soi", "soi-table", 2023),
    ):
        output_dir = data_root / publisher / package_id
        source = tmp_path / f"{publisher}.csv"
        source.write_bytes(publisher.encode())
        fetch_source_artifact(
            str(source),
            source_id=publisher,
            package_id=package_id,
            year=year,
            output_dir=output_dir,
        )

    report = publish_source_artifacts(data_root, wrangler_command=str(wrangler))

    assert report.valid
    commands = log.read_text()
    assert "ledger-raw/raw/nz/ird/ird-wff/2024/" in commands
    assert "ledger-raw/raw/uk/ons/ons-mye/2024/" in commands
    assert "ledger-raw/raw/irs_soi/soi-table/2023/" in commands


def test_publish_source_artifacts_refuses_stale_country_key(tmp_path):
    output_dir = tmp_path / "data" / "ird" / "wff"
    source = tmp_path / "wff.xlsx"
    source.write_bytes(b"official WFF workbook")
    fetch_source_artifact(
        str(source),
        source_id="ird",
        package_id="ird-wff",
        year=2024,
        output_dir=output_dir,
    )
    manifest_path = output_dir / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    artifact = manifest["files"][2024]
    artifact["storage"] = {
        "r2": {
            "provider": "r2",
            "bucket": "ledger-raw",
            "key": (
                f"raw/ird/ird-wff/2024/{artifact['sha256']}/{artifact['filename']}"
            ),
        }
    }
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    log = tmp_path / "wrangler.log"
    wrangler = tmp_path / "wrangler"
    wrangler.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\necho ok\n")
    wrangler.chmod(0o755)

    report = publish_source_artifacts(output_dir, wrangler_command=str(wrangler))

    assert not report.valid
    assert report.entries[0].upload is None
    assert (
        report.entries[0]
        .errors[0]
        .startswith("recorded_r2_key_disagrees_with_country_prefix:")
    )
    assert not log.exists()


def test_publish_source_artifacts_accepts_package_named_packages(tmp_path):
    output_dir = tmp_path / "db" / "data" / "ird" / "packages"
    source = tmp_path / "wff.xlsx"
    source.write_bytes(b"official WFF workbook")
    fetched = fetch_source_artifact(
        str(source),
        source_id="ird",
        package_id="packages",
        year=2024,
        output_dir=output_dir,
    )
    wrangler = tmp_path / "wrangler"
    wrangler.write_text("#!/bin/sh\necho ok\n")
    wrangler.chmod(0o755)

    published = publish_source_artifacts(output_dir, wrangler_command=str(wrangler))

    assert fetched.valid
    assert published.valid
    assert published.counts["uploaded_count"] == 1
    manifest = yaml.safe_load((output_dir / "manifest.yaml").read_text())
    assert manifest["files"][2024]["storage"]["r2"]["key"].startswith(
        "raw/nz/ird/packages/2024/"
    )


def test_inventory_source_artifacts_catches_checksum_mismatch(tmp_path):
    source = tmp_path / "source.xls"
    source.write_bytes(b"original")
    output_dir = tmp_path / "data"
    fetch_source_artifact(
        str(source),
        source_id="irs_soi",
        package_id="soi-table-1-2",
        year=2023,
        output_dir=output_dir,
    )
    (output_dir / "source.xls").write_bytes(b"changed")

    report = inventory_source_artifacts(output_dir)

    assert not report.valid
    assert report.counts["checksum_mismatch_count"] == 1
    assert report.entries[0].errors == ("checksum_mismatch",)


def test_artifact_cli_commands_emit_json(tmp_path, capsys):
    source = tmp_path / "source.xls"
    source.write_bytes(b"cli artifact")
    output_dir = tmp_path / "artifact-dir"

    exit_code = harness_main(
        [
            "fetch-artifact",
            "--url",
            str(source),
            "--source-id",
            "irs_soi",
            "--package-id",
            "soi-table-cli",
            "--year",
            "2023",
            "--out-dir",
            str(output_dir),
        ]
    )
    fetch_payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert fetch_payload["valid"]
    assert (output_dir / "manifest.yaml").exists()

    exit_code = harness_main(
        [
            "inventory-artifacts",
            "--root",
            str(output_dir),
        ]
    )
    inventory_payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert inventory_payload["valid"]
    assert inventory_payload["counts"]["artifact_count"] == 1

    wrangler = tmp_path / "wrangler"
    wrangler.write_text("#!/bin/sh\necho ok\n")
    wrangler.chmod(0o755)
    exit_code = harness_main(
        [
            "publish-raw",
            "--root",
            str(output_dir),
            "--wrangler-command",
            str(wrangler),
        ]
    )
    raw_payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert raw_payload["valid"]
    assert raw_payload["counts"]["uploaded_count"] == 1


def test_bootstrap_r2_reports_missing_authentication(tmp_path):
    wrangler = tmp_path / "wrangler"
    wrangler.write_text("#!/bin/sh\necho 'You are not authenticated.'\n")
    wrangler.chmod(0o755)

    report = bootstrap_r2_buckets(wrangler_command=str(wrangler))

    assert not report.valid
    assert not report.authenticated
    assert "wrangler_not_authenticated" in report.errors[0]


def test_publish_derived_artifacts_uploads_build_directory(tmp_path):
    suite = tmp_path / "suite"
    reports = suite / "reports"
    reports.mkdir(parents=True)
    build_id = "ledger.build.v1:test123"
    (reports / "database.json").write_text(json.dumps({"build_id": build_id}))
    (reports / "build_summary.json").write_text(
        json.dumps({"reports": {"database": {"build_id": build_id}}})
    )
    (suite / "facts.jsonl").write_text("{}\n")
    log = tmp_path / "wrangler.log"
    wrangler = tmp_path / "wrangler"
    wrangler.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\necho ok\n")
    wrangler.chmod(0o755)

    report = publish_derived_artifacts(
        suite,
        source_id="irs_soi",
        package_id="soi-table-1-1",
        year=2023,
        build_artifacts_output=tmp_path / "build_artifacts.jsonl",
        wrangler_command=str(wrangler),
    )

    uploaded_names = {entry.artifact_name for entry in report.entries}
    command_log = log.read_text()
    build_artifact_rows = [
        json.loads(line)
        for line in (tmp_path / "build_artifacts.jsonl").read_text().splitlines()
    ]

    assert report.valid
    assert infer_build_id(suite) == build_id
    assert report.build_id == build_id
    assert report.build_artifacts_path == str(tmp_path / "build_artifacts.jsonl")
    assert uploaded_names == {
        "reports/build_summary.json",
        "reports/database.json",
        "facts.jsonl",
    }
    assert len(build_artifact_rows) == 3
    assert build_artifact_rows[0]["build_id"] == build_id
    assert build_artifact_rows[0]["r2_bucket"] == "ledger-derived"
    assert "ledger-derived/derived/irs_soi/soi-table-1-1/2023/" in command_log
    assert "reports/build_summary.json" in command_log


def test_build_artifact_rows_skips_failed_uploads(tmp_path):
    suite = tmp_path / "suite"
    reports = suite / "reports"
    reports.mkdir(parents=True)
    (reports / "database.json").write_text(
        json.dumps({"build_id": "ledger.build.v1:failed-row"})
    )
    (suite / "facts.jsonl").write_text("{}\n")
    wrangler = tmp_path / "wrangler"
    wrangler.write_text("#!/bin/sh\nexit 1\n")
    wrangler.chmod(0o755)

    report = publish_derived_artifacts(
        suite,
        source_id="irs_soi",
        package_id="soi-table-1-1",
        year=2023,
        wrangler_command=str(wrangler),
    )

    assert not report.valid
    assert build_artifact_rows(report) == ()


def test_publish_derived_cli_emits_json(tmp_path, capsys):
    suite = tmp_path / "suite"
    reports = suite / "reports"
    reports.mkdir(parents=True)
    (reports / "database.json").write_text(
        json.dumps({"build_id": "ledger.build.v1:cli"})
    )
    (suite / "ledger.db").write_bytes(b"db")
    log = tmp_path / "wrangler.log"
    wrangler = tmp_path / "wrangler"
    wrangler.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\necho ok\n")
    wrangler.chmod(0o755)

    exit_code = harness_main(
        [
            "publish-derived",
            "--dir",
            str(suite),
            "--source-id",
            "irs_soi",
            "--package-id",
            "soi-table-1-1",
            "--year",
            "2023",
            "--wrangler-command",
            str(wrangler),
            "--build-artifacts-out",
            str(tmp_path / "build_artifacts.jsonl"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["valid"]
    assert payload["build_id"] == "ledger.build.v1:cli"
    assert payload["counts"]["artifact_count"] == 2
    assert (tmp_path / "build_artifacts.jsonl").exists()


def test_top_level_cli_dispatches_publish_derived(tmp_path, capsys, monkeypatch):
    suite = tmp_path / "suite"
    reports = suite / "reports"
    reports.mkdir(parents=True)
    (reports / "database.json").write_text(
        json.dumps({"build_id": "ledger.build.v1:top-cli"})
    )
    (suite / "facts.jsonl").write_text("{}\n")
    wrangler = tmp_path / "wrangler"
    wrangler.write_text("#!/bin/sh\necho ok\n")
    wrangler.chmod(0o755)
    monkeypatch.setattr(
        "sys.argv",
        [
            "chronicle",
            "publish-derived",
            "--dir",
            str(suite),
            "--source-id",
            "irs_soi",
            "--package-id",
            "soi-table-1-1",
            "--year",
            "2023",
            "--wrangler-command",
            str(wrangler),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli_main()
    payload = json.loads(capsys.readouterr().out)

    assert exc.value.code == 0
    assert payload["valid"]


def _sqlite_build(path, build_id):
    """Write a minimal build database carrying one ledger_builds row."""
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE ledger_builds (build_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO ledger_builds VALUES (?)", (build_id,))


@pytest.mark.parametrize("db_name", ["chronicle.db", "ledger.db"])
def test_infer_build_id_reads_new_and_legacy_database_names(tmp_path, db_name):
    suite = tmp_path / "suite"
    suite.mkdir()
    _sqlite_build(suite / db_name, "ledger.build.v1:from-db")

    assert infer_build_id(suite) == "ledger.build.v1:from-db"


def test_infer_build_id_prefers_the_chronicle_database(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    _sqlite_build(suite / "chronicle.db", "ledger.build.v1:chronicle")
    _sqlite_build(suite / "ledger.db", "ledger.build.v1:legacy")

    assert infer_build_id(suite) == "ledger.build.v1:chronicle"


@pytest.mark.parametrize("db_name", ["chronicle.db", "ledger.db"])
def test_publish_derived_classifies_both_database_names(tmp_path, db_name):
    suite = tmp_path / "suite"
    reports = suite / "reports"
    reports.mkdir(parents=True)
    build_id = "ledger.build.v1:kind"
    (reports / "database.json").write_text(json.dumps({"build_id": build_id}))
    (suite / db_name).write_bytes(b"db")
    wrangler = tmp_path / "wrangler"
    wrangler.write_text("#!/bin/sh\necho ok\n")
    wrangler.chmod(0o755)

    report = publish_derived_artifacts(
        suite,
        source_id="irs_soi",
        package_id="soi-table-1-1",
        year=2023,
        wrangler_command=str(wrangler),
    )
    rows = {row["artifact_name"]: row for row in build_artifact_rows(report)}

    assert rows[db_name]["artifact_kind"] == "sqlite_database"


def test_publish_derived_uses_the_configured_bucket(tmp_path, monkeypatch):
    monkeypatch.setenv("CHRONICLE_R2_DERIVED_BUCKET", "chronicle-derived")
    suite = tmp_path / "suite"
    reports = suite / "reports"
    reports.mkdir(parents=True)
    (reports / "database.json").write_text(
        json.dumps({"build_id": "ledger.build.v1:bucket"})
    )
    (suite / "facts.jsonl").write_text("{}\n")
    log = tmp_path / "wrangler.log"
    wrangler = tmp_path / "wrangler"
    wrangler.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\necho ok\n")
    wrangler.chmod(0o755)

    report = publish_derived_artifacts(
        suite,
        source_id="irs_soi",
        package_id="soi-table-1-1",
        year=2023,
        wrangler_command=str(wrangler),
    )

    assert report.valid
    assert report.entries[0].r2_location.bucket == "chronicle-derived"
    assert "chronicle-derived/derived/irs_soi/" in log.read_text()


def test_publish_raw_refuses_to_restate_a_recorded_bucket(tmp_path, monkeypatch):
    """A recorded storage.r2 bucket is preserved history, not a publish target.

    Archived witness records pin raw R2 URLs by hash, so backfilling the same
    bytes into a renamed bucket must not rewrite the manifest.
    """
    monkeypatch.setenv("CHRONICLE_R2_RAW_BUCKET", "chronicle-raw")
    output_dir = tmp_path / "db" / "data" / "irs_soi" / "soi-table-1-1"
    source = tmp_path / "soi.xlsx"
    source.write_bytes(b"official SOI workbook")
    fetch_source_artifact(
        str(source),
        source_id="irs_soi",
        package_id="soi-table-1-1",
        year=2023,
        output_dir=output_dir,
    )
    manifest_path = output_dir / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    artifact = manifest["files"][2023]
    recorded_key = (
        f"raw/irs_soi/soi-table-1-1/2023/{artifact['sha256']}/{artifact['filename']}"
    )
    artifact["storage"] = {
        "r2": {
            "provider": "r2",
            "bucket": "ledger-raw",
            "key": recorded_key,
            "uri": f"r2://ledger-raw/{recorded_key}",
        }
    }
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    log = tmp_path / "wrangler.log"
    wrangler = tmp_path / "wrangler"
    wrangler.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\necho ok\n")
    wrangler.chmod(0o755)

    report = publish_source_artifacts(output_dir, wrangler_command=str(wrangler))
    unchanged = yaml.safe_load(manifest_path.read_text())

    assert not report.valid
    assert (
        report.entries[0]
        .errors[0]
        .startswith("recorded_r2_bucket_is_preserved_history:")
    )
    assert not log.exists()
    assert unchanged["files"][2023]["storage"]["r2"]["bucket"] == "ledger-raw"


def test_fetch_artifact_keeps_an_already_recorded_bucket(tmp_path, monkeypatch):
    output_dir = tmp_path / "db" / "data" / "irs_soi" / "soi-table-1-1"
    source = tmp_path / "soi.xlsx"
    source.write_bytes(b"official SOI workbook")
    log = tmp_path / "wrangler.log"
    wrangler = tmp_path / "wrangler"
    wrangler.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\necho ok\n")
    wrangler.chmod(0o755)
    fetch_source_artifact(
        str(source),
        source_id="irs_soi",
        package_id="soi-table-1-1",
        year=2023,
        output_dir=output_dir,
        upload_r2=True,
        wrangler_command=str(wrangler),
    )
    manifest_path = output_dir / "manifest.yaml"
    first = yaml.safe_load(manifest_path.read_text())
    assert first["files"][2023]["storage"]["r2"]["bucket"] == "ledger-raw"

    monkeypatch.setenv("CHRONICLE_R2_RAW_BUCKET", "chronicle-raw")
    report = fetch_source_artifact(
        str(source),
        source_id="irs_soi",
        package_id="soi-table-1-1",
        year=2023,
        output_dir=output_dir,
        upload_r2=True,
        wrangler_command=str(wrangler),
    )
    second = yaml.safe_load(manifest_path.read_text())

    # The backfill copy really is uploaded to the new bucket, but the manifest
    # keeps recording where the bytes were first published.
    assert report.r2_location.bucket == "chronicle-raw"
    assert "chronicle-raw" in log.read_text()
    assert (
        second["files"][2023]["storage"]["r2"] == first["files"][2023]["storage"]["r2"]
    )


# ---------------------------------------------------------------------------
# Publisher revisions
#
# A raw R2 key is content-addressed, so a recorded storage.r2 block is a claim
# about specific bytes. On 2026-09-02 the IRS re-published 22in05ira.xlsx and
# 22in06ira.xlsx under their existing URLs (PolicyEngine/chronicle#225): a
# repeated fetch must never pair those new bytes with the old object's URI.
# ---------------------------------------------------------------------------

REPUBLISHED_URL = "https://www.irs.gov/pub/irs-soi/22in05ira.xlsx"
REPUBLISHED_FILENAME = "22in05ira.xlsx"
FIRST_PUBLICATION = b"IRA table 5, first publication"
SECOND_PUBLICATION = b"IRA table 5, silently re-published with revised rows"


def _wrangler_stub(tmp_path, log):
    wrangler = tmp_path / "wrangler"
    wrangler.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\necho ok\n")
    wrangler.chmod(0o755)
    return wrangler


def _serve(monkeypatch, content):
    """Serve ``content`` from the publisher URL, without touching the network."""

    def _fake_read_artifact(source_url):
        assert source_url == REPUBLISHED_URL
        return content, REPUBLISHED_FILENAME

    monkeypatch.setattr("chronicle.artifacts._read_artifact", _fake_read_artifact)


def _fetch_republished(output_dir, wrangler, *, upload_r2=True, **kwargs):
    return fetch_source_artifact(
        REPUBLISHED_URL,
        source_id="irs_soi",
        package_id="soi-table-5",
        year=2022,
        output_dir=output_dir,
        upload_r2=upload_r2,
        wrangler_command=str(wrangler),
        **kwargs,
    )


def test_repeated_fetch_of_identical_bytes_preserves_the_recorded_block(
    tmp_path, monkeypatch
):
    """Same bytes: the recorded block survives whatever bucket is configured."""
    output_dir = tmp_path / "db" / "data" / "irs_soi" / "soi-table-5"
    log = tmp_path / "wrangler.log"
    wrangler = _wrangler_stub(tmp_path, log)
    manifest_path = output_dir / "manifest.yaml"
    _serve(monkeypatch, FIRST_PUBLICATION)
    _fetch_republished(output_dir, wrangler)
    first = yaml.safe_load(manifest_path.read_text())["files"][2022]

    monkeypatch.setenv("CHRONICLE_R2_RAW_BUCKET", "chronicle-raw")
    report = _fetch_republished(output_dir, wrangler)
    second = yaml.safe_load(manifest_path.read_text())["files"][2022]

    assert report.valid
    # The backfill copy really goes to the renamed bucket, but the manifest
    # keeps recording where these bytes were first published.
    assert report.r2_location.bucket == "chronicle-raw"
    assert "chronicle-raw" in log.read_text()
    assert second["storage"] == first["storage"]
    assert second["storage"]["r2"]["bucket"] == "ledger-raw"
    assert "previous_r2" not in second["storage"]
    assert second["sha256"] == first["sha256"]


@pytest.mark.parametrize(
    ("upload_r2", "configured_bucket"),
    [
        pytest.param(True, None, id="reuploaded"),
        # The two routes that reached a manifest in the wild: a fetch that only
        # registers the bytes, and a fetch once the bucket default has moved.
        # Both preserved the recorded block while rewriting sha256/size_bytes.
        pytest.param(False, None, id="registered-without-upload"),
        pytest.param(True, "chronicle-raw", id="after-the-bucket-rename"),
    ],
)
def test_repeated_fetch_of_different_bytes_is_refused(
    tmp_path, monkeypatch, upload_r2, configured_bucket
):
    """A publisher revision must not inherit the recorded object's provenance."""
    output_dir = tmp_path / "db" / "data" / "irs_soi" / "soi-table-5"
    log = tmp_path / "wrangler.log"
    wrangler = _wrangler_stub(tmp_path, log)
    manifest_path = output_dir / "manifest.yaml"
    artifact_path = output_dir / REPUBLISHED_FILENAME
    _serve(monkeypatch, FIRST_PUBLICATION)
    first_report = _fetch_republished(output_dir, wrangler)
    manifest_before = manifest_path.read_bytes()
    uploads_before = log.read_text()

    if configured_bucket:
        monkeypatch.setenv("CHRONICLE_R2_RAW_BUCKET", configured_bucket)
    _serve(monkeypatch, SECOND_PUBLICATION)
    with pytest.raises(SourceArtifactRevisionError) as raised:
        _fetch_republished(output_dir, wrangler, upload_r2=upload_r2)

    message = str(raised.value)
    assert first_report.sha256 in message
    assert hashlib.sha256(SECOND_PUBLICATION).hexdigest() in message
    assert f"size_bytes={len(FIRST_PUBLICATION)}" in message
    assert f"size_bytes={len(SECOND_PUBLICATION)}" in message
    assert "release revision" in message
    assert "--record-revision" in message
    # Nothing was overwritten, copied or uploaded on the way to the refusal.
    assert manifest_path.read_bytes() == manifest_before
    assert artifact_path.read_bytes() == FIRST_PUBLICATION
    assert log.read_text() == uploads_before


def test_record_revision_writes_a_new_key_and_keeps_the_previous_object(
    tmp_path, monkeypatch
):
    """The opt-in records the new bytes' own key under the configured bucket."""
    output_dir = tmp_path / "db" / "data" / "irs_soi" / "soi-table-5"
    log = tmp_path / "wrangler.log"
    wrangler = _wrangler_stub(tmp_path, log)
    manifest_path = output_dir / "manifest.yaml"
    _serve(monkeypatch, FIRST_PUBLICATION)
    first_report = _fetch_republished(output_dir, wrangler)
    superseded = yaml.safe_load(manifest_path.read_text())["files"][2022]

    monkeypatch.setenv("CHRONICLE_R2_RAW_BUCKET", "chronicle-raw")
    _serve(monkeypatch, SECOND_PUBLICATION)
    report = _fetch_republished(output_dir, wrangler, record_revision=True)
    revised = yaml.safe_load(manifest_path.read_text())["files"][2022]
    revised_sha256 = hashlib.sha256(SECOND_PUBLICATION).hexdigest()

    assert report.valid
    # storage.r2 names the object that holds the entry's current bytes...
    assert revised["sha256"] == revised_sha256
    assert revised["size_bytes"] == len(SECOND_PUBLICATION)
    assert revised["storage"]["r2"]["bucket"] == "chronicle-raw"
    assert revised["storage"]["r2"]["key"] == (
        f"raw/irs_soi/soi-table-5/2022/{revised_sha256}/{REPUBLISHED_FILENAME}"
    )
    assert revised["storage"]["r2"]["uri"] == (
        f"r2://chronicle-raw/{revised['storage']['r2']['key']}"
    )
    # ...and never the superseded key, which stays addressable as history.
    previous = revised["storage"]["previous_r2"]
    assert [entry["uri"] for entry in previous] == [superseded["storage"]["r2"]["uri"]]
    assert previous[0]["bucket"] == "ledger-raw"
    assert previous[0]["sha256"] == first_report.sha256
    assert previous[0]["size_bytes"] == len(FIRST_PUBLICATION)
    assert previous[0]["fetched_at"] == superseded["fetched_at"]
    assert previous[0]["superseded_at"] == revised["fetched_at"]
    assert (output_dir / REPUBLISHED_FILENAME).read_bytes() == SECOND_PUBLICATION
    assert f"chronicle-raw/{revised['storage']['r2']['key']}" in log.read_text()


def test_a_revised_manifest_still_reads_as_one_r2_linked_artifact(
    tmp_path, monkeypatch
):
    """storage.previous_r2 is a sibling key, so every storage.r2 reader is intact."""
    output_dir = tmp_path / "db" / "data" / "irs_soi" / "soi-table-5"
    wrangler = _wrangler_stub(tmp_path, tmp_path / "wrangler.log")
    _serve(monkeypatch, FIRST_PUBLICATION)
    _fetch_republished(output_dir, wrangler)
    _serve(monkeypatch, SECOND_PUBLICATION)
    _fetch_republished(output_dir, wrangler, record_revision=True)

    inventory = inventory_source_artifacts(output_dir)

    assert inventory.valid
    assert inventory.counts["r2_link_count"] == 1
    assert inventory.counts["checksum_mismatch_count"] == 0
    assert inventory.entries[0].r2["bucket"] == "ledger-raw"
    assert inventory.entries[0].sha256_actual == (
        hashlib.sha256(SECOND_PUBLICATION).hexdigest()
    )


def test_publish_raw_refuses_a_file_the_recorded_object_does_not_hold(
    tmp_path, monkeypatch
):
    """Recorded sha256 != local sha256 is a revision, not a backfill."""
    output_dir = tmp_path / "db" / "data" / "irs_soi" / "soi-table-5"
    log = tmp_path / "wrangler.log"
    wrangler = _wrangler_stub(tmp_path, log)
    manifest_path = output_dir / "manifest.yaml"
    _serve(monkeypatch, FIRST_PUBLICATION)
    first_report = _fetch_republished(output_dir, wrangler)

    # Reproduce the state a pre-fix fetch left behind: new bytes on disk, the
    # entry's own hash rewritten, the recorded key still addressing the old
    # bytes.
    revised_sha256 = hashlib.sha256(SECOND_PUBLICATION).hexdigest()
    (output_dir / REPUBLISHED_FILENAME).write_bytes(SECOND_PUBLICATION)
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["files"][2022]["sha256"] = revised_sha256
    manifest["files"][2022]["size_bytes"] = len(SECOND_PUBLICATION)
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    manifest_before = manifest_path.read_bytes()
    uploads_before = log.read_text()

    report = publish_source_artifacts(output_dir, wrangler_command=str(wrangler))

    assert not report.valid
    assert report.entries[0].upload is None
    assert report.entries[0].r2_location is None
    assert report.entries[0].errors == (
        "recorded_r2_identity_mismatch:"
        f"recorded_sha256={first_report.sha256}:"
        f"recorded_filename={REPUBLISHED_FILENAME}:"
        f"local_sha256={revised_sha256}:"
        f"local_filename={REPUBLISHED_FILENAME}",
    )
    assert log.read_text() == uploads_before
    assert manifest_path.read_bytes() == manifest_before


def test_publish_raw_uploads_a_registered_revision_and_keeps_its_history(
    tmp_path, monkeypatch
):
    """Once the revision is registered, publishing it is ordinary work."""
    output_dir = tmp_path / "db" / "data" / "irs_soi" / "soi-table-5"
    log = tmp_path / "wrangler.log"
    wrangler = _wrangler_stub(tmp_path, log)
    manifest_path = output_dir / "manifest.yaml"
    _serve(monkeypatch, FIRST_PUBLICATION)
    first_report = _fetch_republished(output_dir, wrangler)
    _serve(monkeypatch, SECOND_PUBLICATION)
    _fetch_republished(output_dir, wrangler, record_revision=True)
    revised_sha256 = hashlib.sha256(SECOND_PUBLICATION).hexdigest()

    report = publish_source_artifacts(output_dir, wrangler_command=str(wrangler))
    published = yaml.safe_load(manifest_path.read_text())["files"][2022]

    assert report.valid
    assert published["storage"]["r2"]["key"].endswith(
        f"/{revised_sha256}/{REPUBLISHED_FILENAME}"
    )
    assert [entry["sha256"] for entry in published["storage"]["previous_r2"]] == [
        first_report.sha256
    ]


def test_fetch_artifact_cli_refuses_a_revision_then_records_it_on_request(
    tmp_path, monkeypatch, capsys
):
    output_dir = tmp_path / "db" / "data" / "irs_soi" / "soi-table-5"
    wrangler = _wrangler_stub(tmp_path, tmp_path / "wrangler.log")
    argv = [
        "fetch-artifact",
        "--url",
        REPUBLISHED_URL,
        "--source-id",
        "irs_soi",
        "--package-id",
        "soi-table-5",
        "--year",
        "2022",
        "--out-dir",
        str(output_dir),
        "--upload-r2",
        "--wrangler-command",
        str(wrangler),
    ]
    _serve(monkeypatch, FIRST_PUBLICATION)
    assert harness_main(argv) == 0
    capsys.readouterr()

    _serve(monkeypatch, SECOND_PUBLICATION)
    refused = harness_main(argv)
    refusal = capsys.readouterr()

    assert refused == 1
    assert "--record-revision" in refusal.err
    assert refusal.out == ""

    assert harness_main([*argv, "--record-revision"]) == 0
    recorded = json.loads(capsys.readouterr().out)

    assert recorded["sha256"] == hashlib.sha256(SECOND_PUBLICATION).hexdigest()
    assert recorded["r2_location"]["key"].endswith(
        f"/{recorded['sha256']}/{REPUBLISHED_FILENAME}"
    )


def test_record_revision_without_an_upload_records_no_current_object(
    tmp_path, monkeypatch
):
    """An offline revision keeps history without claiming the new bytes exist.

    Registering a revision without ``--upload-r2`` leaves the entry with no
    ``storage.r2`` at all rather than a pointer to bytes R2 does not hold. The
    superseded object stays addressable, and a later publish-raw completes the
    registration.
    """
    output_dir = tmp_path / "db" / "data" / "irs_soi" / "soi-table-5"
    log = tmp_path / "wrangler.log"
    wrangler = _wrangler_stub(tmp_path, log)
    manifest_path = output_dir / "manifest.yaml"
    _serve(monkeypatch, FIRST_PUBLICATION)
    first_report = _fetch_republished(output_dir, wrangler)

    _serve(monkeypatch, SECOND_PUBLICATION)
    _fetch_republished(output_dir, wrangler, upload_r2=False, record_revision=True)
    registered = yaml.safe_load(manifest_path.read_text())["files"][2022]

    assert "r2" not in registered["storage"]
    assert [entry["sha256"] for entry in registered["storage"]["previous_r2"]] == [
        first_report.sha256
    ]

    report = publish_source_artifacts(output_dir, wrangler_command=str(wrangler))
    published = yaml.safe_load(manifest_path.read_text())["files"][2022]
    revised_sha256 = hashlib.sha256(SECOND_PUBLICATION).hexdigest()

    assert report.valid
    assert published["storage"]["r2"]["key"].endswith(
        f"/{revised_sha256}/{REPUBLISHED_FILENAME}"
    )
    assert [entry["sha256"] for entry in published["storage"]["previous_r2"]] == [
        first_report.sha256
    ]


def test_a_recorded_block_that_only_carries_a_uri_is_still_recognized(
    tmp_path, monkeypatch
):
    """Identity reads the URI when a hand-written block records no key."""
    output_dir = tmp_path / "db" / "data" / "irs_soi" / "soi-table-5"
    wrangler = _wrangler_stub(tmp_path, tmp_path / "wrangler.log")
    manifest_path = output_dir / "manifest.yaml"
    _serve(monkeypatch, FIRST_PUBLICATION)
    _fetch_republished(output_dir, wrangler)
    manifest = yaml.safe_load(manifest_path.read_text())
    recorded = manifest["files"][2022]["storage"]["r2"]
    manifest["files"][2022]["storage"]["r2"] = {
        "provider": recorded["provider"],
        "bucket": recorded["bucket"],
        "uri": recorded["uri"],
    }
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    uri_only = manifest["files"][2022]["storage"]["r2"]

    report = _fetch_republished(output_dir, wrangler)
    preserved = yaml.safe_load(manifest_path.read_text())["files"][2022]

    assert report.valid
    assert preserved["storage"]["r2"] == uri_only

    _serve(monkeypatch, SECOND_PUBLICATION)
    with pytest.raises(SourceArtifactRevisionError):
        _fetch_republished(output_dir, wrangler)
