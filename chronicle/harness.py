"""Fixture-first Chronicle fact validation harness."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from chronicle.artifacts import (
    DEFAULT_R2_DERIVED_BUCKET,
    DEFAULT_R2_RAW_BUCKET,
    ArtifactFetchReport,
    ArtifactInventoryReport,
    DerivedArtifactPublishReport,
    R2BootstrapReport,
    RawArtifactPublishReport,
    SourceArtifactRevisionError,
    bootstrap_r2_buckets,
    fetch_source_artifact,
    inventory_source_artifacts,
    publish_derived_artifacts,
    publish_source_artifacts,
)
from chronicle.bundle import (
    UK_BUNDLE_SOURCES,
    BuildBundleReport,
    assert_uk_bundle_sources_match_aliases,
    build_bundle,
)
from chronicle.concepts import ConceptAlignmentReport, validate_concept_alignments
from chronicle.consumer_contract import (
    ConsumerFactExportReport,
    validate_consumer_fact_contract,
    write_consumer_facts_jsonl,
)
from chronicle.core import AggregateFact, ValidationReport, validate_facts
from chronicle.database import ChronicleDbBuildReport, build_chronicle_db
from chronicle.env import DEFAULT_CHRONICLE_SCHEMA
from chronicle.mirror import (
    ChronicleMirrorExportReport,
    SupabaseMirrorLoadReport,
    export_chronicle_db_tables,
    load_supabase_mirror,
)
from chronicle.pe_source_plan import (
    PeSourcePlanReport,
    build_pe_source_plan,
    write_pe_source_plan_json,
    write_pe_source_plan_markdown,
)
from chronicle.sources.cells import (
    SourceCell,
    SourceCellReport,
    load_source_cells_jsonl,
    save_source_cells_jsonl,
    validate_source_cells,
)
from chronicle.sources.rows import (
    SourceRow,
    SourceRowReport,
    load_source_rows_jsonl,
    save_source_rows_jsonl,
    validate_source_rows,
)
from chronicle.source_package import (
    SourcePackageScaffoldReport,
    SourcePackageValidationReport,
    scaffold_source_package,
    validate_source_package,
)
from chronicle.store import load_facts_jsonl, save_facts_jsonl
from chronicle.suite import BuildSuiteReport, build_source_suite

FIXTURE_FACTS_PATH = Path(__file__).with_name("fixtures") / "facts.jsonl"
FIXTURE_SOURCE_CELLS_PATH = (
    Path(__file__).with_name("fixtures")
    / "source_cells"
    / "soi_table_1_1_2023_cells.jsonl"
)
FIXTURE_BUILDERS = {
    "soi-table-1-1": "chronicle.jurisdictions.us.soi",
    "soi-table-1-4": "chronicle.jurisdictions.us.soi",
}


def validate_fact_file(path: str | Path) -> ValidationReport:
    """Validate a fact JSONL file."""
    return validate_facts(load_facts_jsonl(path))


def validate_fixture_facts() -> ValidationReport:
    """Validate the bundled fixture fact set."""
    return validate_fact_file(FIXTURE_FACTS_PATH)


def build_fixture_facts(source: str, *, year: int) -> list[AggregateFact]:
    """Build fixture aggregate facts from a supported source parser."""
    if source == "soi-table-1-1":
        from chronicle.jurisdictions.us.soi import build_soi_table_1_1_facts

        return build_soi_table_1_1_facts(year)
    if source == "soi-table-1-4":
        from chronicle.jurisdictions.us.soi import build_soi_table_1_4_facts

        return build_soi_table_1_4_facts(year)
    raise ValueError(f"Unsupported fixture fact source: {source}")


def build_fixture_fact_file(
    source: str,
    output: str | Path,
    *,
    year: int,
) -> ValidationReport:
    """Build, save, and validate a fixture fact file."""
    facts = build_fixture_facts(source, year=year)
    save_facts_jsonl(facts, output)
    return validate_facts(facts)


def validate_source_cell_file(path: str | Path) -> SourceCellReport:
    """Validate a source-cell JSONL file."""
    return validate_source_cells(load_source_cells_jsonl(path))


def validate_source_row_file(path: str | Path) -> SourceRowReport:
    """Validate a source-row JSONL file."""
    return validate_source_rows(load_source_rows_jsonl(path))


def validate_fixture_source_cells() -> SourceCellReport:
    """Validate the bundled source-cell fixture."""
    return validate_source_cell_file(FIXTURE_SOURCE_CELLS_PATH)


def build_fixture_source_cells(source: str, *, year: int) -> list[SourceCell]:
    """Build source-cell fixture records from a supported parser."""
    if source == "soi-table-1-1":
        from chronicle.jurisdictions.us.soi import build_soi_table_1_1_source_cells

        return build_soi_table_1_1_source_cells(year)
    if source == "soi-table-1-4":
        from chronicle.jurisdictions.us.soi import build_soi_table_1_4_source_cells

        return build_soi_table_1_4_source_cells(year)
    raise ValueError(f"Unsupported source-cell fixture source: {source}")


def build_fixture_source_rows(source: str, *, year: int) -> list[SourceRow]:
    """Build source-row fixture records from a supported parser."""
    package = None
    try:
        from chronicle.source_package import load_source_package

        package = load_source_package(source)
    except FileNotFoundError:
        package = None
    if package is not None:
        return package.build_source_rows(year)
    raise ValueError(f"Unsupported source-row fixture source: {source}")


def build_fixture_source_cell_file(
    source: str,
    output: str | Path,
    *,
    year: int,
) -> SourceCellReport:
    """Build, save, and validate source-cell fixture records."""
    cells = build_fixture_source_cells(source, year=year)
    save_source_cells_jsonl(cells, output)
    return validate_source_cells(cells)


def build_fixture_source_row_file(
    source: str,
    output: str | Path,
    *,
    year: int,
) -> SourceRowReport:
    """Build, save, and validate source-row fixture records."""
    rows = build_fixture_source_rows(source, year=year)
    save_source_rows_jsonl(rows, output)
    return validate_source_rows(rows)


def build_chronicle_db_file(
    db_path: str | Path,
    *,
    fact_path: str | Path = FIXTURE_FACTS_PATH,
    source_cells_path: str | Path | None = FIXTURE_SOURCE_CELLS_PATH,
    source_rows_path: str | Path | None = None,
    replace: bool = False,
) -> ChronicleDbBuildReport:
    """Build a relational Chronicle DB from fact and source-cell JSONL files."""
    facts = load_facts_jsonl(fact_path)
    source_cells = (
        load_source_cells_jsonl(source_cells_path)
        if source_cells_path is not None
        else None
    )
    source_rows = (
        load_source_rows_jsonl(source_rows_path)
        if source_rows_path is not None
        else None
    )
    return build_chronicle_db(
        facts,
        db_path,
        source_cells=source_cells,
        source_rows=source_rows,
        replace=replace,
    )


def export_consumer_fact_file(
    output: str | Path,
    *,
    fact_path: str | Path,
) -> ConsumerFactExportReport:
    """Export Chronicle facts to downstream consumer-contract JSONL."""
    facts = load_facts_jsonl(fact_path)
    validation_report = validate_facts(facts)
    if not validation_report.valid:
        raise ValueError("Cannot export invalid Chronicle facts to consumer contract.")
    contract_report = validate_consumer_fact_contract(facts)
    if not contract_report.valid:
        raise ValueError("Cannot export invalid Chronicle consumer-contract facts.")
    return write_consumer_facts_jsonl(facts, output)


def validate_concept_alignment_file(
    path: str | Path,
    *,
    axiom_command: list[str] | None = None,
    axiom_roots: list[str | Path] | None = None,
) -> ConceptAlignmentReport:
    """Validate concept alignments in a fact JSONL file."""
    return validate_concept_alignments(
        load_facts_jsonl(path),
        axiom_command=axiom_command,
        axiom_roots=axiom_roots or (),
    )


def build_source_suite_dir(
    source: str | Path,
    output_dir: str | Path,
    *,
    year: int,
    axiom_command: list[str] | None = None,
    axiom_roots: list[str | Path] | None = None,
    require_axiom_validation: bool = False,
    replace: bool = False,
) -> BuildSuiteReport:
    """Build a complete source-package suite directory."""
    return build_source_suite(
        source,
        output_dir,
        year=year,
        axiom_command=axiom_command,
        axiom_roots=axiom_roots or (),
        require_axiom_validation=require_axiom_validation,
        replace=replace,
    )


def build_bundle_dir(
    output_dir: str | Path,
    *,
    year: int,
    sources: list[str | Path] | None = None,
    suite: str | None = None,
    axiom_command: list[str] | None = None,
    axiom_roots: list[str | Path] | None = None,
    require_axiom_validation: bool = False,
    replace: bool = False,
) -> BuildBundleReport:
    """Build a merged Chronicle consumer bundle from source-package suites."""
    if suite == "uk":
        if sources:
            raise ValueError("--suite uk cannot be combined with --source.")
        assert_uk_bundle_sources_match_aliases()
        sources = list(UK_BUNDLE_SOURCES)
    return build_bundle(
        output_dir,
        year=year,
        sources=sources,
        axiom_command=axiom_command,
        axiom_roots=axiom_roots or (),
        require_axiom_validation=require_axiom_validation,
        replace=replace,
    )


def validate_source_package_dir(
    source: str | Path,
    *,
    year: int,
) -> SourcePackageValidationReport:
    """Validate a declarative source package before running the build suite."""
    return validate_source_package(source, year=year)


def scaffold_source_package_dir(
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
    """Write a starter source package directory."""
    return scaffold_source_package(
        output_dir,
        source_id=source_id,
        package_id=package_id,
        source_table=source_table,
        resource_package=resource_package,
        resource_directory=resource_directory,
        manifest=manifest,
        replace_existing=replace_existing,
    )


def fetch_artifact_file(
    source_url: str,
    *,
    source_id: str,
    package_id: str,
    year: int,
    output_dir: str | Path,
    dataset: str | None = None,
    source_page: str | None = None,
    table: str | None = None,
    filename: str | None = None,
    upload_r2: bool = False,
    record_revision: bool = False,
    r2_bucket: str | None = None,
    r2_prefix: str | None = None,
    wrangler_command: str = "npx wrangler",
) -> ArtifactFetchReport:
    """Fetch/register a raw source artifact and optionally upload it to R2.

    Raises :class:`SourceArtifactRevisionError` when the fetched bytes are not
    the bytes the manifest's recorded R2 object holds, unless
    ``record_revision`` opts into registering the publisher revision.
    """
    return fetch_source_artifact(
        source_url,
        source_id=source_id,
        package_id=package_id,
        year=year,
        output_dir=output_dir,
        dataset=dataset,
        source_page=source_page,
        table=table,
        filename=filename,
        upload_r2=upload_r2,
        record_revision=record_revision,
        r2_bucket=r2_bucket,
        r2_prefix=r2_prefix,
        wrangler_command=wrangler_command,
    )


def inventory_artifact_files(
    root: str | Path,
    *,
    manifest_filename: str = "manifest.yaml",
) -> ArtifactInventoryReport:
    """Inventory local manifest-declared source artifacts."""
    return inventory_source_artifacts(root, manifest_filename=manifest_filename)


def publish_raw_artifact_files(
    root: str | Path,
    *,
    manifest_filename: str = "manifest.yaml",
    source_id: str | None = None,
    package_id: str | None = None,
    r2_bucket: str | None = None,
    r2_prefix: str | None = None,
    wrangler_command: str = "npx wrangler",
) -> RawArtifactPublishReport:
    """Publish manifest-declared raw source artifacts to R2."""
    return publish_source_artifacts(
        root,
        manifest_filename=manifest_filename,
        source_id=source_id,
        package_id=package_id,
        r2_bucket=r2_bucket,
        r2_prefix=r2_prefix,
        wrangler_command=wrangler_command,
    )


def bootstrap_r2_storage(
    *,
    raw_bucket: str | None = None,
    derived_bucket: str | None = None,
    wrangler_command: str = "npx wrangler",
) -> R2BootstrapReport:
    """Create Chronicle R2 buckets when Wrangler is authenticated."""
    return bootstrap_r2_buckets(
        raw_bucket=raw_bucket,
        derived_bucket=derived_bucket,
        wrangler_command=wrangler_command,
    )


def publish_derived_artifact_files(
    input_dir: str | Path,
    *,
    source_id: str,
    package_id: str,
    year: int,
    build_id: str | None = None,
    r2_bucket: str | None = None,
    r2_prefix: str | None = None,
    wrangler_command: str = "npx wrangler",
    build_artifacts_output: str | Path | None = None,
) -> DerivedArtifactPublishReport:
    """Publish deterministic build outputs to the derived R2 bucket."""
    return publish_derived_artifacts(
        input_dir,
        source_id=source_id,
        package_id=package_id,
        year=year,
        build_id=build_id,
        r2_bucket=r2_bucket,
        r2_prefix=r2_prefix,
        wrangler_command=wrangler_command,
        build_artifacts_output=build_artifacts_output,
    )


def export_chronicle_db_table_files(
    db_path: str | Path,
    output_dir: str | Path,
    *,
    replace: bool = False,
) -> ChronicleMirrorExportReport:
    """Export local Chronicle DB tables to bulk-loadable JSONL files."""
    return export_chronicle_db_tables(db_path, output_dir, replace=replace)


def load_supabase_mirror_files(
    input_dir: str | Path,
    *,
    schema: str | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
    build_artifacts_path: str | Path | None = None,
) -> SupabaseMirrorLoadReport:
    """Load exported Chronicle JSONL mirror files into Supabase/Postgres.

    ``schema`` of None resolves to ``$CHRONICLE_SCHEMA``, else the default
    schema, so the hosted mirror writer answers to the renamed variable.
    """
    table_paths = (
        {"build_artifacts": Path(build_artifacts_path)}
        if build_artifacts_path is not None
        else None
    )
    return load_supabase_mirror(
        input_dir,
        schema=schema,
        batch_size=batch_size,
        dry_run=dry_run,
        table_paths=table_paths,
    )


def plan_pe_source_files(
    manifest_path: str | Path,
    *,
    output_path: str | Path | None = None,
    markdown_path: str | Path | None = None,
    batch_size: int = 10,
    max_items: int | None = None,
    source_package_root: str | Path | None = "packages",
) -> PeSourcePlanReport:
    """Build and optionally write an agent PE source migration plan."""
    report = build_pe_source_plan(
        manifest_path,
        batch_size=batch_size,
        max_items=max_items,
        source_package_root=source_package_root,
    )
    if output_path is not None:
        write_pe_source_plan_json(report, output_path)
    if markdown_path is not None:
        write_pe_source_plan_markdown(report, markdown_path)
    return report


def main(argv: list[str] | None = None) -> int:
    """Run the harness CLI."""
    parser = argparse.ArgumentParser(description="Chronicle fact validation harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate-facts",
        help="Validate a Chronicle fact JSONL file",
    )
    input_group = validate_parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--input",
        type=Path,
        help="Path to a Chronicle fact JSONL file",
    )
    input_group.add_argument(
        "--fixture",
        action="store_true",
        help="Validate the bundled fixture fact set",
    )

    source_validate_parser = subparsers.add_parser(
        "validate-source-cells",
        help="Validate a Chronicle source-cell JSONL file",
    )
    source_input_group = source_validate_parser.add_mutually_exclusive_group()
    source_input_group.add_argument(
        "--input",
        type=Path,
        help="Path to a Chronicle source-cell JSONL file",
    )
    source_input_group.add_argument(
        "--fixture",
        action="store_true",
        help="Validate the bundled source-cell fixture",
    )

    source_row_validate_parser = subparsers.add_parser(
        "validate-source-rows",
        help="Validate a Chronicle source-row JSONL file",
    )
    source_row_validate_parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to a Chronicle source-row JSONL file",
    )

    build_parser = subparsers.add_parser(
        "build-fixture-facts",
        help="Build a tiny source-backed Chronicle fact JSONL file",
    )
    build_parser.add_argument(
        "source",
        choices=sorted(FIXTURE_BUILDERS),
        help="Source parser to use",
    )
    build_parser.add_argument(
        "--year",
        type=int,
        default=2023,
        help="Source year to build",
    )
    build_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write fact JSONL",
    )

    source_build_parser = subparsers.add_parser(
        "build-source-cells",
        help="Build whole-artifact Chronicle source-cell JSONL records",
    )
    source_build_parser.add_argument(
        "source",
        choices=sorted(FIXTURE_BUILDERS),
        help="Source parser to use",
    )
    source_build_parser.add_argument(
        "--year",
        type=int,
        default=2023,
        help="Source year to build",
    )
    source_build_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write source-cell JSONL",
    )

    source_row_build_parser = subparsers.add_parser(
        "build-source-rows",
        help="Build full-document Chronicle source-row JSONL records",
    )
    source_row_build_parser.add_argument(
        "source",
        help="Source package alias, package directory, or source_package.yaml path",
    )
    source_row_build_parser.add_argument(
        "--year",
        type=int,
        default=2023,
        help="Source year to build",
    )
    source_row_build_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write source-row JSONL",
    )

    db_parser = subparsers.add_parser(
        "build-db",
        help="Build a relational Chronicle DB artifact",
    )
    db_input_group = db_parser.add_mutually_exclusive_group()
    db_input_group.add_argument(
        "--input",
        type=Path,
        help="Path to a Chronicle fact JSONL file",
    )
    db_input_group.add_argument(
        "--fixture",
        action="store_true",
        help="Build from bundled fixture facts and source cells",
    )
    db_parser.add_argument(
        "--source-cells",
        type=Path,
        help="Optional source-cell JSONL file to include",
    )
    db_parser.add_argument(
        "--source-rows",
        type=Path,
        help="Optional source-row JSONL file to include",
    )
    db_parser.add_argument(
        "--db",
        type=Path,
        required=True,
        help="SQLite Chronicle DB path to write",
    )
    db_parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing DB at --db",
    )

    consumer_parser = subparsers.add_parser(
        "export-consumer-facts",
        help="Export Chronicle facts to downstream consumer-contract JSONL",
    )
    consumer_input_group = consumer_parser.add_mutually_exclusive_group()
    consumer_input_group.add_argument(
        "--input",
        type=Path,
        help="Path to a Chronicle fact JSONL file",
    )
    consumer_input_group.add_argument(
        "--fixture",
        action="store_true",
        help="Export the bundled fixture fact set",
    )
    consumer_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write consumer-contract JSONL",
    )

    suite_parser = subparsers.add_parser(
        "build-suite",
        help="Build source cells, facts, DB, and JSON reports for a source",
    )
    suite_parser.add_argument(
        "source",
        help="Source package alias, package directory, or source_package.yaml path",
    )
    suite_parser.add_argument(
        "--year",
        type=int,
        default=2023,
        help="Source year to build",
    )
    suite_parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory for suite artifacts and reports",
    )
    suite_parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing nonempty output directory",
    )
    suite_parser.add_argument(
        "--axiom-cli",
        help=(
            "Optional Axiom command, e.g. `axiom` or `uv run --with-editable . axiom`."
        ),
    )
    suite_parser.add_argument(
        "--axiom-root",
        action="append",
        default=[],
        help="RuleSpec repo root to pass to the Axiom concepts validator.",
    )
    suite_parser.add_argument(
        "--require-axiom-validation",
        action="store_true",
        help="Fail agent acceptance unless every canonical concept is checked.",
    )

    bundle_parser = subparsers.add_parser(
        "build-bundle",
        help="Build source-package suites and merge consumer-contract facts",
    )
    bundle_parser.add_argument(
        "--year",
        type=int,
        default=2023,
        help="Source year to build",
    )
    bundle_parser.add_argument(
        "--source",
        action="append",
        default=None,
        help=(
            "Source package alias, package directory, or source_package.yaml "
            "path. May be repeated. Defaults to available packages for --year."
        ),
    )
    bundle_parser.add_argument(
        "--suite",
        choices=["uk"],
        help="Curated source-package suite to build.",
    )
    bundle_parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory for merged bundle artifacts and reports",
    )
    bundle_parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing nonempty output directory",
    )
    bundle_parser.add_argument(
        "--axiom-cli",
        help=(
            "Optional Axiom command, e.g. `axiom` or `uv run --with-editable . axiom`."
        ),
    )
    bundle_parser.add_argument(
        "--axiom-root",
        action="append",
        default=[],
        help="RuleSpec repo root to pass to the Axiom concepts validator.",
    )
    bundle_parser.add_argument(
        "--require-axiom-validation",
        action="store_true",
        help="Fail agent acceptance unless every canonical concept is checked.",
    )

    consumer_artifact_parser = subparsers.add_parser(
        "build-consumer-artifact",
        help="Build a versioned facts-only consumer artifact",
        description="Build a versioned facts-only consumer artifact.",
    )
    consumer_artifact_parser.add_argument(
        "--facts",
        type=Path,
        required=True,
        help="Path to a consumer_facts.jsonl file or a bundle directory",
    )
    consumer_artifact_parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory for the consumer artifact",
    )
    consumer_artifact_parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing output directory",
    )

    package_validate_parser = subparsers.add_parser(
        "validate-package",
        help="Validate a declarative Chronicle source package",
    )
    package_validate_parser.add_argument(
        "source",
        help="Source package alias, package directory, or source_package.yaml path",
    )
    package_validate_parser.add_argument(
        "--year",
        type=int,
        default=2023,
        help="Source year to validate",
    )

    scaffold_parser = subparsers.add_parser(
        "scaffold-package",
        help="Write a starter declarative source package",
    )
    scaffold_parser.add_argument(
        "--source-id",
        required=True,
        help="Stable source ID, such as irs_soi",
    )
    scaffold_parser.add_argument(
        "--package-id",
        required=True,
        help="Stable package ID, such as soi-table-1-2",
    )
    scaffold_parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output package directory",
    )
    scaffold_parser.add_argument(
        "--source-table",
        help="Human-readable source table title",
    )
    scaffold_parser.add_argument(
        "--resource-package",
        default="db",
        help="Python package that contains source artifacts",
    )
    scaffold_parser.add_argument(
        "--resource-directory",
        help="Resource directory containing manifest and artifacts",
    )
    scaffold_parser.add_argument(
        "--manifest",
        default="manifest.yaml",
        help="Artifact manifest filename",
    )
    scaffold_parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing source_package.yaml",
    )

    artifact_parser = subparsers.add_parser(
        "fetch-artifact",
        help="Fetch/register a raw source artifact and update manifest.yaml",
    )
    artifact_parser.add_argument(
        "--url",
        required=True,
        help="Publisher URL, file:// URL, or local path for the artifact",
    )
    artifact_parser.add_argument(
        "--source-id",
        required=True,
        help="Stable source ID, such as irs_soi",
    )
    artifact_parser.add_argument(
        "--package-id",
        required=True,
        help="Stable package ID, such as soi-table-1-2",
    )
    artifact_parser.add_argument(
        "--year",
        type=int,
        required=True,
        help="Artifact vintage year to record in manifest.yaml",
    )
    artifact_parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Directory where the raw artifact and manifest.yaml should live",
    )
    artifact_parser.add_argument(
        "--dataset",
        help="Manifest dataset ID. Defaults to <source-id>_<package-id>.",
    )
    artifact_parser.add_argument(
        "--source-page",
        help="Publisher landing page. Defaults to --url.",
    )
    artifact_parser.add_argument(
        "--table",
        help="Human-readable source table title. Defaults to --package-id.",
    )
    artifact_parser.add_argument(
        "--filename",
        help="Override artifact filename inferred from URL/path.",
    )
    artifact_parser.add_argument(
        "--upload-r2",
        action="store_true",
        help="Upload the artifact to R2 after local checksum capture.",
    )
    artifact_parser.add_argument(
        "--record-revision",
        action="store_true",
        help=(
            "Register a publisher revision: the fetched bytes get their own "
            "content-addressed key under the configured bucket and the "
            "superseded object moves to storage.previous_r2. Without this "
            "flag, bytes that disagree with the recorded R2 object are "
            "refused."
        ),
    )
    artifact_parser.add_argument(
        "--r2-bucket",
        default=None,
        help=(
            "R2 bucket for raw artifacts when --upload-r2 is set. Defaults to "
            f"$CHRONICLE_R2_RAW_BUCKET, else {DEFAULT_R2_RAW_BUCKET}."
        ),
    )
    artifact_parser.add_argument(
        "--r2-prefix",
        help=(
            "R2 key-prefix root for raw artifacts. Defaults to country-aware "
            "raw/nz or raw/uk for known publisher directories, and raw for "
            "legacy US packages."
        ),
    )
    artifact_parser.add_argument(
        "--wrangler-command",
        default="npx wrangler",
        help="Wrangler command prefix to use for R2 uploads.",
    )

    artifact_inventory_parser = subparsers.add_parser(
        "inventory-artifacts",
        help="Inventory local manifest-declared source artifacts",
    )
    artifact_inventory_parser.add_argument(
        "--root",
        type=Path,
        default=Path("db/data"),
        help="Root directory to scan for manifest.yaml files.",
    )
    artifact_inventory_parser.add_argument(
        "--manifest",
        default="manifest.yaml",
        help="Manifest filename to scan for.",
    )

    raw_publish_parser = subparsers.add_parser(
        "publish-raw",
        help="Upload manifest-declared raw source artifacts to the raw R2 bucket",
    )
    raw_publish_parser.add_argument(
        "--root",
        type=Path,
        default=Path("db/data"),
        help="Root directory to scan for manifest.yaml files.",
    )
    raw_publish_parser.add_argument(
        "--manifest",
        default="manifest.yaml",
        help="Manifest filename to scan for.",
    )
    raw_publish_parser.add_argument(
        "--source-id",
        help="Override manifest source_id for scanned artifacts.",
    )
    raw_publish_parser.add_argument(
        "--package-id",
        help="Override manifest package_id for scanned artifacts.",
    )
    raw_publish_parser.add_argument(
        "--r2-bucket",
        default=None,
        help=(
            "R2 bucket for immutable raw artifacts. Defaults to "
            f"$CHRONICLE_R2_RAW_BUCKET, else {DEFAULT_R2_RAW_BUCKET}."
        ),
    )
    raw_publish_parser.add_argument(
        "--r2-prefix",
        help=(
            "R2 key-prefix root for raw artifacts. Defaults to country-aware "
            "raw/nz or raw/uk for known publisher directories, and raw for "
            "legacy US packages."
        ),
    )
    raw_publish_parser.add_argument(
        "--wrangler-command",
        default="npx wrangler",
        help="Wrangler command prefix to use for R2 uploads.",
    )

    r2_parser = subparsers.add_parser(
        "bootstrap-r2",
        help="Create Chronicle R2 buckets when Wrangler is authenticated",
    )
    r2_parser.add_argument(
        "--raw-bucket",
        default=None,
        help=(
            "R2 bucket name for immutable raw source artifacts. Defaults to "
            f"$CHRONICLE_R2_RAW_BUCKET, else {DEFAULT_R2_RAW_BUCKET}."
        ),
    )
    r2_parser.add_argument(
        "--derived-bucket",
        default=None,
        help=(
            "R2 bucket name for derived Chronicle build artifacts. Defaults to "
            f"$CHRONICLE_R2_DERIVED_BUCKET, else {DEFAULT_R2_DERIVED_BUCKET}."
        ),
    )
    r2_parser.add_argument(
        "--wrangler-command",
        default="npx wrangler",
        help="Wrangler command prefix.",
    )

    derived_publish_parser = subparsers.add_parser(
        "publish-derived",
        help="Upload deterministic Chronicle build outputs to the derived R2 bucket",
    )
    derived_publish_parser.add_argument(
        "--dir",
        type=Path,
        required=True,
        help="Build output directory, such as a build-suite output directory.",
    )
    derived_publish_parser.add_argument(
        "--source-id",
        required=True,
        help="Stable source ID, such as irs_soi.",
    )
    derived_publish_parser.add_argument(
        "--package-id",
        required=True,
        help="Stable package ID, such as soi-table-1-1.",
    )
    derived_publish_parser.add_argument(
        "--year",
        type=int,
        required=True,
        help="Source/build year.",
    )
    derived_publish_parser.add_argument(
        "--build-id",
        help=(
            "Build ID. Defaults to the ID inferred from reports, chronicle.db, "
            "or a legacy ledger.db."
        ),
    )
    derived_publish_parser.add_argument(
        "--r2-bucket",
        default=None,
        help=(
            "R2 bucket for derived build artifacts. Defaults to "
            f"$CHRONICLE_R2_DERIVED_BUCKET, else {DEFAULT_R2_DERIVED_BUCKET}."
        ),
    )
    derived_publish_parser.add_argument(
        "--r2-prefix",
        help=(
            "R2 key-prefix root for derived artifacts. Defaults to "
            "country-aware derived/nz or derived/uk for known publishers, "
            "and derived for legacy US packages."
        ),
    )
    derived_publish_parser.add_argument(
        "--wrangler-command",
        default="npx wrangler",
        help="Wrangler command prefix to use for R2 uploads.",
    )
    derived_publish_parser.add_argument(
        "--build-artifacts-out",
        type=Path,
        help="Optional path to write build_artifacts JSONL rows.",
    )

    mirror_export_parser = subparsers.add_parser(
        "export-db-tables",
        help="Export a Chronicle SQLite DB artifact to per-table JSONL files",
    )
    mirror_export_parser.add_argument(
        "--db",
        type=Path,
        required=True,
        help="SQLite Chronicle DB path to export",
    )
    mirror_export_parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory for JSONL tables and manifest",
    )
    mirror_export_parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing nonempty output directory",
    )

    mirror_load_parser = subparsers.add_parser(
        "load-supabase-mirror",
        help="Load exported Chronicle JSONL mirror files into Supabase/Postgres",
    )
    mirror_load_parser.add_argument(
        "--dir",
        type=Path,
        required=True,
        help="Directory containing exported per-table JSONL files.",
    )
    mirror_load_parser.add_argument(
        "--schema",
        default=None,
        help=(
            "Supabase/Postgres schema to load into. Defaults to "
            f"$CHRONICLE_SCHEMA, else {DEFAULT_CHRONICLE_SCHEMA}."
        ),
    )
    mirror_load_parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows per Supabase upsert batch.",
    )
    mirror_load_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows and validate files without writing to Supabase.",
    )
    mirror_load_parser.add_argument(
        "--build-artifacts",
        type=Path,
        help="Optional build_artifacts JSONL path to use instead of the file in --dir.",
    )

    pe_plan_parser = subparsers.add_parser(
        "plan-pe-sources",
        help="Build an agent batch plan from a PE source manifest CSV",
    )
    pe_plan_parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("docs/pe-us-source-manifest.csv"),
        help="PE source manifest CSV path.",
    )
    pe_plan_parser.add_argument(
        "--out",
        type=Path,
        help="Optional JSON report path to write.",
    )
    pe_plan_parser.add_argument(
        "--markdown",
        type=Path,
        help="Optional Markdown report path to write.",
    )
    pe_plan_parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Maximum work items per generated batch.",
    )
    pe_plan_parser.add_argument(
        "--max-items",
        type=int,
        help="Optional cap for a preview plan.",
    )
    pe_plan_parser.add_argument(
        "--source-package-root",
        type=Path,
        default=Path("packages"),
        help="Existing source-package root used to mark covered artifacts.",
    )

    concept_parser = subparsers.add_parser(
        "validate-concept-alignments",
        help="Validate source-to-canonical concept alignments",
    )
    concept_input_group = concept_parser.add_mutually_exclusive_group()
    concept_input_group.add_argument(
        "--input",
        type=Path,
        help="Path to a Chronicle fact JSONL file",
    )
    concept_input_group.add_argument(
        "--fixture",
        action="store_true",
        help="Validate concept alignments in the bundled fixture fact set",
    )
    concept_parser.add_argument(
        "--axiom-cli",
        help=(
            "Optional Axiom command, e.g. `axiom` or `uv run --with-editable . axiom`."
        ),
    )
    concept_parser.add_argument(
        "--axiom-root",
        action="append",
        default=[],
        help="RuleSpec repo root to pass to the Axiom concepts validator.",
    )

    args = parser.parse_args(argv)

    if args.command == "validate-facts":
        path = FIXTURE_FACTS_PATH if args.fixture or not args.input else args.input
        report = validate_fact_file(path)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "validate-source-cells":
        path = (
            FIXTURE_SOURCE_CELLS_PATH if args.fixture or not args.input else args.input
        )
        report = validate_source_cell_file(path)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "validate-source-rows":
        report = validate_source_row_file(args.input)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "build-fixture-facts":
        report = build_fixture_fact_file(args.source, args.output, year=args.year)
        payload = {"output": str(args.output), **report.to_dict()}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "build-source-cells":
        report = build_fixture_source_cell_file(
            args.source,
            args.output,
            year=args.year,
        )
        payload = {"output": str(args.output), **report.to_dict()}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "build-source-rows":
        report = build_fixture_source_row_file(
            args.source,
            args.output,
            year=args.year,
        )
        payload = {"output": str(args.output), **report.to_dict()}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "build-db":
        fact_path = FIXTURE_FACTS_PATH if args.fixture or not args.input else args.input
        source_cells_path = args.source_cells
        if args.fixture and source_cells_path is None:
            source_cells_path = FIXTURE_SOURCE_CELLS_PATH
        report = build_chronicle_db_file(
            args.db,
            fact_path=fact_path,
            source_cells_path=source_cells_path,
            source_rows_path=args.source_rows,
            replace=args.replace,
        )
        payload = {"db": str(args.db), **report.to_dict()}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.command == "export-consumer-facts":
        fact_path = FIXTURE_FACTS_PATH if args.fixture or not args.input else args.input
        facts = load_facts_jsonl(fact_path)
        validation_report = validate_facts(facts)
        contract_report = validate_consumer_fact_contract(facts)
        if not validation_report.valid or not contract_report.valid:
            print(
                json.dumps(
                    {
                        "valid": False,
                        "output": str(args.output),
                        "source_validation": validation_report.to_dict(),
                        "contract_validation": contract_report.to_dict(),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 1
        report = write_consumer_facts_jsonl(facts, args.output)
        print(
            json.dumps(
                {
                    "valid": True,
                    **report.to_dict(),
                    "source_validation": validation_report.to_dict(),
                    "contract_validation": contract_report.to_dict(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "build-suite":
        axiom_command = shlex.split(args.axiom_cli) if args.axiom_cli else None
        report = build_source_suite_dir(
            args.source,
            args.out,
            year=args.year,
            axiom_command=axiom_command,
            axiom_roots=args.axiom_root,
            require_axiom_validation=args.require_axiom_validation,
            replace=args.replace,
        )
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "build-bundle":
        axiom_command = shlex.split(args.axiom_cli) if args.axiom_cli else None
        report = build_bundle_dir(
            args.out,
            year=args.year,
            sources=args.source,
            suite=args.suite,
            axiom_command=axiom_command,
            axiom_roots=args.axiom_root,
            require_axiom_validation=args.require_axiom_validation,
            replace=args.replace,
        )
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "build-consumer-artifact":
        from policyengine_chronicle.consumer import build_consumer_artifact

        artifact_report = build_consumer_artifact(
            args.out,
            facts_path=args.facts,
            replace=args.replace,
        )
        print(json.dumps(artifact_report.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "validate-package":
        report = validate_source_package_dir(args.source, year=args.year)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "scaffold-package":
        report = scaffold_source_package_dir(
            args.out,
            source_id=args.source_id,
            package_id=args.package_id,
            source_table=args.source_table,
            resource_package=args.resource_package,
            resource_directory=args.resource_directory,
            manifest=args.manifest,
            replace_existing=args.replace,
        )
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "fetch-artifact":
        try:
            report = fetch_artifact_file(
                args.url,
                source_id=args.source_id,
                package_id=args.package_id,
                year=args.year,
                output_dir=args.out_dir,
                dataset=args.dataset,
                source_page=args.source_page,
                table=args.table,
                filename=args.filename,
                upload_r2=args.upload_r2,
                record_revision=args.record_revision,
                r2_bucket=args.r2_bucket,
                r2_prefix=args.r2_prefix,
                wrangler_command=args.wrangler_command,
            )
        except SourceArtifactRevisionError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "inventory-artifacts":
        report = inventory_artifact_files(
            args.root,
            manifest_filename=args.manifest,
        )
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "publish-raw":
        report = publish_raw_artifact_files(
            args.root,
            manifest_filename=args.manifest,
            source_id=args.source_id,
            package_id=args.package_id,
            r2_bucket=args.r2_bucket,
            r2_prefix=args.r2_prefix,
            wrangler_command=args.wrangler_command,
        )
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "bootstrap-r2":
        report = bootstrap_r2_storage(
            raw_bucket=args.raw_bucket,
            derived_bucket=args.derived_bucket,
            wrangler_command=args.wrangler_command,
        )
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "publish-derived":
        report = publish_derived_artifact_files(
            args.dir,
            source_id=args.source_id,
            package_id=args.package_id,
            year=args.year,
            build_id=args.build_id,
            r2_bucket=args.r2_bucket,
            r2_prefix=args.r2_prefix,
            wrangler_command=args.wrangler_command,
            build_artifacts_output=args.build_artifacts_out,
        )
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "export-db-tables":
        report = export_chronicle_db_table_files(
            args.db,
            args.out,
            replace=args.replace,
        )
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "load-supabase-mirror":
        report = load_supabase_mirror_files(
            args.dir,
            schema=args.schema,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            build_artifacts_path=args.build_artifacts,
        )
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if args.command == "plan-pe-sources":
        report = plan_pe_source_files(
            args.manifest,
            output_path=args.out,
            markdown_path=args.markdown,
            batch_size=args.batch_size,
            max_items=args.max_items,
            source_package_root=args.source_package_root,
        )
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "validate-concept-alignments":
        path = FIXTURE_FACTS_PATH if args.fixture or not args.input else args.input
        axiom_command = shlex.split(args.axiom_cli) if args.axiom_cli else None
        report = validate_concept_alignment_file(
            path,
            axiom_command=axiom_command,
            axiom_roots=args.axiom_root,
        )
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
