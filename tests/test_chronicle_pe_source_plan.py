"""Tests for PE source agent batch planning."""

from __future__ import annotations

import csv
import json
import shlex

from chronicle.harness import main as harness_main
from chronicle.pe_source_plan import build_pe_source_plan


FIELDNAMES = [
    "status",
    "origin_project",
    "jurisdiction",
    "pipeline",
    "source_id",
    "artifact_role",
    "artifact_kind",
    "artifact",
    "filename",
    "format",
    "exists_locally",
    "ledger_source_status",
    "source_cell_status",
    "target_construction_status",
    "value_capture_policy",
    "notes",
]


def test_build_pe_source_plan_classifies_agent_work(tmp_path):
    manifest = tmp_path / "manifest.csv"
    _write_manifest(
        manifest,
        [
            _row(
                pipeline="national-soi-workbooks",
                source_id="irs-soi",
                artifact_kind="url",
                artifact="https://www.irs.gov/pub/irs-soi/23in12ms.xls",
                filename="irs_soi_ty2023_table_1_2.xls",
                format=".xls",
                exists_locally="n/a",
                ledger_source_status="not_loaded",
            ),
            _row(
                pipeline="database",
                artifact_role="pe_intermediate",
                artifact_kind="local_file",
                artifact="policyengine_us_data/storage/calibration/raw_inputs/source.csv",
                filename="source.csv",
                format=".csv",
                exists_locally="yes",
                ledger_source_status="not_loaded",
            ),
            _row(
                pipeline="long-term-target-references",
                source_id="ssa",
                artifact_kind="url",
                artifact="https://www.ssa.gov/source.pdf",
                filename="source.pdf",
                format=".pdf",
                exists_locally="n/a",
                ledger_source_status="row_parsed",
            ),
            _row(
                pipeline="cbo-source-documents",
                source_id="cbo",
                artifact_kind="url",
                artifact="https://www.cbo.gov/source.xlsx",
                filename="blocked.xlsx",
                format=".xlsx",
                exists_locally="n/a",
                ledger_source_status="blocked",
                source_cell_status="blocked_by_access_datadome",
            ),
            _row(
                pipeline="local-geography-source-documents",
                source_id="census",
                artifact_kind="url",
                artifact="https://www2.census.gov/source.zip",
                filename="deferred.zip",
                format=".zip",
                exists_locally="n/a",
                ledger_source_status="deferred",
                source_cell_status="deferred_geography_support_no_fact_package",
            ),
            _row(
                pipeline="soi-source-pages",
                source_id="irs-soi",
                artifact_kind="url",
                artifact="https://www.irs.gov/statistics/source-page",
                filename="source_page.html",
                format=".html",
                exists_locally="n/a",
                ledger_source_status="source_package",
                target_construction_status="build_suite_valid",
                notes="Package soi-table-1-1 preserves the linked workbook.",
            ),
        ],
    )

    report = build_pe_source_plan(
        manifest,
        batch_size=2,
        source_package_root=None,
    )
    stages = {
        item.filename: item.recommended_stage
        for batch in report.batches
        for item in batch.items
    }
    command_hints = {
        item.filename: item.command_hint
        for batch in report.batches
        for item in batch.items
    }

    assert report.row_count == 6
    assert report.item_count == 6
    assert stages["irs_soi_ty2023_table_1_2.xls"] == "fetch_artifact"
    assert stages["source.csv"] == "register_local_artifact"
    assert stages["source.pdf"] == "source_cell_scaffold"
    assert stages["blocked.xlsx"] == "blocked_or_deferred"
    assert stages["deferred.zip"] == "blocked_or_deferred"
    assert stages["source_page.html"] == "existing_source_package"
    assert shlex.split(command_hints["irs_soi_ty2023_table_1_2.xls"])[-3:] == [
        "--kind",
        "publisher_table",
        "--upload-r2",
    ]
    assert "--kind publisher_table --upload-r2" in command_hints["source.csv"]
    assert (
        "Manifest marks this row as source_package" in command_hints["source_page.html"]
    )
    assert report.counts["by_recommended_stage"] == {
        "blocked_or_deferred": 2,
        "existing_source_package": 1,
        "fetch_artifact": 1,
        "register_local_artifact": 1,
        "source_cell_scaffold": 1,
    }
    assert report.counts["by_publisher_hint"] == {"missing": 6}


def test_build_pe_source_plan_marks_existing_source_packages(tmp_path):
    manifest = tmp_path / "manifest.csv"
    packages = tmp_path / "packages"
    package_dir = packages / "irs_soi" / "table_1_1"
    resource_dir = tmp_path / "db" / "data" / "irs_soi" / "table_1_1"
    package_dir.mkdir(parents=True)
    resource_dir.mkdir(parents=True)
    (package_dir / "source_package.yaml").write_text(
        "\n".join(
            [
                "schema_version: ledger.source_package.v1",
                "package_id: soi-table-1-1",
                "artifact:",
                "  source_name: irs_soi",
                "  source_table: Table 1.1",
                f"  resource_package: {tmp_path / 'db'}",
                "  resource_directory: data/irs_soi/table_1_1",
                "  manifest: manifest.yaml",
                "record_sets: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (resource_dir / "manifest.yaml").write_text(
        "\n".join(
            [
                "files:",
                "  2023:",
                "    filename: 23in11si.xls",
                "    source_url: https://www.irs.gov/pub/irs-soi/23in11si.xls",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_manifest(
        manifest,
        [
            _row(
                pipeline="national-soi-workbooks",
                source_id="irs-soi",
                artifact_kind="url",
                artifact="https://www.irs.gov/pub/irs-soi/23in11si.xls",
                filename="irs_soi_ty2023_table_1_1.xls",
                format=".xls",
                exists_locally="n/a",
                ledger_source_status="not_loaded",
            )
        ],
    )

    report = build_pe_source_plan(
        manifest,
        source_package_root=packages,
    )
    item = report.batches[0].items[0]

    assert item.recommended_stage == "existing_source_package"
    assert item.package_id == "soi-table-1-1"
    assert "build-suite" in item.command_hint


def test_build_pe_source_plan_keeps_artifact_year_packages_to_pinned_year(
    tmp_path,
):
    manifest = tmp_path / "manifest.csv"
    packages = tmp_path / "packages"
    all_returns_dir = packages / "irs_soi" / "table_2_5"
    child_dir = packages / "irs_soi" / "table_2_5_eitc_children_2020"
    resource_dir = tmp_path / "db" / "data" / "irs_soi" / "table_2_5"
    all_returns_dir.mkdir(parents=True)
    child_dir.mkdir(parents=True)
    resource_dir.mkdir(parents=True)
    shared_artifact = [
        "  source_name: irs_soi",
        "  source_table: Table 2.5",
        f"  resource_package: {tmp_path / 'db'}",
        "  resource_directory: data/irs_soi/table_2_5",
        "  manifest: manifest.yaml",
    ]
    (all_returns_dir / "source_package.yaml").write_text(
        "\n".join(
            [
                "schema_version: ledger.source_package.v1",
                "package_id: soi-table-2-5",
                "artifact:",
                *shared_artifact,
                "record_sets: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (child_dir / "source_package.yaml").write_text(
        "\n".join(
            [
                "schema_version: ledger.source_package.v1",
                "package_id: soi-table-2-5-eitc-children-2020",
                "artifact:",
                *shared_artifact,
                "  artifact_year: 2020",
                "record_sets: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (resource_dir / "manifest.yaml").write_text(
        "\n".join(
            [
                "files:",
                "  2020:",
                "    filename: 20in25ic.xls",
                "    source_url: https://www.irs.gov/pub/irs-soi/20in25ic.xls",
                "  2021:",
                "    filename: 21in25ic.xls",
                "    source_url: https://www.irs.gov/pub/irs-soi/21in25ic.xls",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_manifest(
        manifest,
        [
            _row(
                pipeline="national-soi-workbooks",
                source_id="irs-soi",
                artifact_kind="url",
                artifact="https://www.irs.gov/pub/irs-soi/21in25ic.xls",
                filename="irs_soi_ty2021_table_2_5.xls",
                format=".xls",
                exists_locally="n/a",
                ledger_source_status="source_package",
                target_construction_status="build_suite_valid",
                notes="Package soi-table-2-5 preserves the official source.",
            ),
            _row(
                pipeline="legacy-loss-targets",
                source_id="irs-soi",
                artifact_role="pe_intermediate",
                artifact_kind="local_file",
                artifact="policyengine_us_data/storage/calibration_targets/eitc.csv",
                filename="eitc.csv",
                format=".csv",
                exists_locally="yes",
                ledger_source_status="source_package",
                target_construction_status="build_suite_valid",
                notes=(
                    "Package soi-table-2-5-eitc-children-2020 preserves "
                    "the official IRS SOI source."
                ),
            ),
        ],
    )

    report = build_pe_source_plan(manifest, source_package_root=packages)
    items_by_filename = {
        item.filename: item for batch in report.batches for item in batch.items
    }

    assert items_by_filename["irs_soi_ty2021_table_2_5.xls"].package_id == (
        "soi-table-2-5"
    )
    assert (
        "--year 2021" in items_by_filename["irs_soi_ty2021_table_2_5.xls"].command_hint
    )
    assert items_by_filename["eitc.csv"].package_id == (
        "soi-table-2-5-eitc-children-2020"
    )
    assert "--year 2020" in items_by_filename["eitc.csv"].command_hint


def test_build_pe_source_plan_prefers_package_named_in_notes_for_shared_url(
    tmp_path,
):
    manifest = tmp_path / "manifest.csv"
    packages = tmp_path / "packages"
    first_dir = packages / "bea" / "nipa_pension_contributions"
    second_dir = packages / "bea" / "nipa_total_wages_salaries"
    resource_dir = tmp_path / "db" / "data" / "bea" / "nipa"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)
    resource_dir.mkdir(parents=True)
    shared_artifact = [
        "  source_name: bea",
        "  source_table: NIPA annual flat file",
        f"  resource_package: {tmp_path / 'db'}",
        "  resource_directory: data/bea/nipa",
        "  manifest: manifest.yaml",
    ]
    for package_dir, package_id in (
        (first_dir, "bea-nipa-pension-contributions"),
        (second_dir, "bea-nipa-total-wages-salaries"),
    ):
        (package_dir / "source_package.yaml").write_text(
            "\n".join(
                [
                    "schema_version: ledger.source_package.v1",
                    f"package_id: {package_id}",
                    "artifact:",
                    *shared_artifact,
                    "record_sets: []",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    (resource_dir / "manifest.yaml").write_text(
        "\n".join(
            [
                "files:",
                "  2024:",
                "    filename: NipaDataA.txt",
                "    source_url: https://apps.bea.gov/national/Release/TXT/NipaDataA.txt",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_manifest(
        manifest,
        [
            _row(
                pipeline="macro-source-documents",
                source_id="bea",
                artifact_kind="url",
                artifact="https://apps.bea.gov/national/Release/TXT/NipaDataA.txt",
                filename="bea_nipa_annual_data_ba06rc.txt",
                format=".txt",
                exists_locally="n/a",
                ledger_source_status="source_package",
                target_construction_status="build_suite_valid",
                notes=(
                    "Package bea-nipa-total-wages-salaries preserves the "
                    "official BEA source."
                ),
            ),
        ],
    )

    report = build_pe_source_plan(manifest, source_package_root=packages)
    item = report.batches[0].items[0]

    assert item.package_id == "bea-nipa-total-wages-salaries"
    assert item.package_path.endswith("packages/bea/nipa_total_wages_salaries")


def test_build_pe_source_plan_matches_source_package_local_files_by_filename(tmp_path):
    manifest = tmp_path / "manifest.csv"
    packages = tmp_path / "packages"
    package_dir = packages / "ssa" / "population_projections_tr2024"
    resource_dir = tmp_path / "db" / "data" / "ssa" / "population_projections_tr2024"
    package_dir.mkdir(parents=True)
    resource_dir.mkdir(parents=True)
    (package_dir / "source_package.yaml").write_text(
        "\n".join(
            [
                "schema_version: ledger.source_package.v1",
                "package_id: ssa-population-projections-tr2024",
                "artifact:",
                "  source_name: ssa",
                "  source_table: Population projections",
                f"  resource_package: {tmp_path / 'db'}",
                "  resource_directory: data/ssa/population_projections_tr2024",
                "  manifest: manifest.yaml",
                "record_sets: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (resource_dir / "manifest.yaml").write_text(
        "\n".join(
            [
                "files:",
                "  2024:",
                "    filename: SSPopJul_TR2024.csv",
                "    source_url: https://www.ssa.gov/oact/HistEst/Population/2024/SSPopJul_TR2024.csv",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_manifest(
        manifest,
        [
            _row(
                pipeline="long-term-target-sources",
                source_id="ssa",
                artifact_role="pe_support",
                artifact_kind="local_file",
                artifact="policyengine_us_data/storage/SSPopJul_TR2024.csv",
                filename="SSPopJul_TR2024.csv",
                format=".csv",
                exists_locally="yes",
                ledger_source_status="source_package",
                target_construction_status="build_suite_valid",
            )
        ],
    )

    report = build_pe_source_plan(manifest, source_package_root=packages)
    item = report.batches[0].items[0]

    assert item.recommended_stage == "existing_source_package"
    assert item.package_id == "ssa-population-projections-tr2024"
    assert item.package_path.endswith("packages/ssa/population_projections_tr2024")
    assert "build-suite" in item.command_hint


def test_build_pe_source_plan_matches_source_package_named_in_notes(tmp_path):
    manifest = tmp_path / "manifest.csv"
    packages = tmp_path / "packages"
    package_dir = packages / "irs_soi" / "historic_table_2_state_agi_2022"
    resource_dir = tmp_path / "db" / "data" / "irs_soi" / "historic_table_2"
    package_dir.mkdir(parents=True)
    resource_dir.mkdir(parents=True)
    (package_dir / "source_package.yaml").write_text(
        "\n".join(
            [
                "schema_version: ledger.source_package.v1",
                "package_id: soi-historic-table-2-state-agi-2022",
                "artifact:",
                "  source_name: irs_soi",
                "  source_table: Historic Table 2 state AGI",
                f"  resource_package: {tmp_path / 'db'}",
                "  resource_directory: data/irs_soi/historic_table_2",
                "  manifest: manifest.yaml",
                "record_sets: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (resource_dir / "manifest.yaml").write_text(
        "\n".join(
            [
                "files:",
                "  2022:",
                "    filename: 22in55cmcsv.csv",
                "    source_url: https://www.irs.gov/pub/irs-soi/22in55cmcsv.csv",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_manifest(
        manifest,
        [
            _row(
                pipeline="legacy-loss-targets",
                source_id="irs-soi",
                artifact_role="pe_intermediate",
                artifact_kind="local_file",
                artifact="policyengine_us_data/storage/calibration_targets/agi_state.csv",
                filename="agi_state.csv",
                format=".csv",
                exists_locally="yes",
                ledger_source_status="source_package",
                target_construction_status="build_suite_valid",
                notes=(
                    "Package soi-historic-table-2-state-agi-2022 preserves "
                    "the official IRS SOI source."
                ),
            )
        ],
    )

    report = build_pe_source_plan(manifest, source_package_root=packages)
    item = report.batches[0].items[0]

    assert item.recommended_stage == "existing_source_package"
    assert item.package_id == "soi-historic-table-2-state-agi-2022"
    assert item.package_path.endswith(
        "packages/irs_soi/historic_table_2_state_agi_2022"
    )
    assert "build-suite" in item.command_hint


def test_plan_pe_sources_cli_writes_json_and_markdown(tmp_path, capsys):
    manifest = tmp_path / "manifest.csv"
    output = tmp_path / "plan.json"
    markdown = tmp_path / "plan.md"
    _write_manifest(
        manifest,
        [
            _row(
                pipeline="national-soi-workbooks",
                source_id="irs-soi",
                artifact_kind="url",
                artifact="https://www.irs.gov/pub/irs-soi/23in12ms.xls",
                filename="irs_soi_ty2023_table_1_2.xls",
                format=".xls",
                exists_locally="n/a",
                ledger_source_status="not_loaded",
            )
        ],
    )

    exit_code = harness_main(
        [
            "plan-pe-sources",
            "--manifest",
            str(manifest),
            "--out",
            str(output),
            "--markdown",
            str(markdown),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["valid"]
    assert output.exists()
    assert markdown.exists()
    assert json.loads(output.read_text())["item_count"] == 1
    assert "irs_soi_ty2023_table_1_2.xls" in markdown.read_text()


def test_build_pe_source_plan_does_not_treat_embedded_ids_as_years(tmp_path):
    manifest = tmp_path / "manifest.csv"
    _write_manifest(
        manifest,
        [
            _row(
                pipeline="macro-source-documents",
                source_id="bea",
                artifact="https://example.test/series/ABC192090005Q",
                filename="bea_abc192090005q.html",
                format=".html",
            )
        ],
    )

    report = build_pe_source_plan(manifest)
    item = report.batches[0].items[0]

    assert "--year TODO_YEAR" in item.command_hint
    assert "--year 2090" not in item.command_hint


def test_build_pe_source_plan_routes_fred_to_publisher_lookup(tmp_path):
    manifest = tmp_path / "manifest.csv"
    _write_manifest(
        manifest,
        [
            _row(
                pipeline="macro-source-documents",
                source_id="fred",
                artifact="https://fred.stlouisfed.org/series/Y351RC1A027NBEA",
                filename="fred_y351rc1a027nbea.html",
                format=".html",
                notes="FRED/BEA defined-contribution pension contribution series page.",
            )
        ],
    )

    report = build_pe_source_plan(manifest)
    item = report.batches[0].items[0]

    assert item.recommended_stage == "find_primary_source"
    assert item.publisher_hint == "bea"
    assert item.priority == 75
    assert item.blockers == ("publisher_source_required",)
    assert "Find and register the bea publisher artifact" in item.command_hint
    assert (
        "Do not use this FRED URL as a Chronicle source artifact" in item.command_hint
    )


def _row(**overrides):
    row = {
        "status": "todo",
        "origin_project": "policyengine-us-data",
        "jurisdiction": "us",
        "pipeline": "database",
        "source_id": "census",
        "artifact_role": "publisher_source",
        "artifact_kind": "url",
        "artifact": "https://example.test/source.csv",
        "filename": "source.csv",
        "format": ".csv",
        "exists_locally": "n/a",
        "ledger_source_status": "not_loaded",
        "source_cell_status": "blocked_by_artifact_status",
        "target_construction_status": "not_ready",
        "value_capture_policy": "full source artifact",
        "notes": "test row",
    }
    row.update(overrides)
    return row


def _write_manifest(path, rows):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
