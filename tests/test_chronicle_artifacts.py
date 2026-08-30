"""Tests for Chronicle source artifact acquisition and storage metadata."""

from __future__ import annotations

import hashlib
import json

import pytest
import yaml

from chronicle.cli import main as cli_main
from chronicle.artifacts import (
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
        ("uscis", "db/data/uscis/active_daca_recipients_fy2026_q2", None),
        ("dhs_ohss", "packages/dhs_ohss/lpr_population_2024", None),
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
    uscis_key = build_r2_key(
        source_id="uscis",
        package_id="uscis-active-daca-recipients-fy2026-q2",
        year=2026,
        sha256="abc123",
        filename="daca.xlsx",
    )
    dhs_ohss_key = build_derived_r2_key(
        source_id="dhs_ohss",
        package_id="ohss-lpr-population-2024",
        year=2024,
        build_id="ledger.build.v1:abc123",
        artifact_name="facts.jsonl",
    )

    assert nz_key.startswith("raw/nz/ird/")
    assert uk_key.startswith("derived/uk/ons/")
    assert uscis_key == (
        "raw/uscis/uscis-active-daca-recipients-fy2026-q2/2026/abc123/daca.xlsx"
    )
    assert dhs_ohss_key == (
        "derived/dhs_ohss/ohss-lpr-population-2024/2024/"
        "ledger.build.v1:abc123/facts.jsonl"
    )


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
