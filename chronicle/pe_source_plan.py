"""Agent batch planning for PolicyEngine source migration."""

from __future__ import annotations

import csv
import json
import re
import shlex
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


WORKBOOK_FORMATS = {".xls", ".xlsx", ".ods"}
RECTANGULAR_FORMATS = {".csv", ".csv.gz", ".json", ".txt"}
DOCUMENT_FORMATS = {".html", ".pdf"}
LOW_PRIORITY_PIPELINES = {"local-geography-source-documents"}
HIGH_PRIORITY_PIPELINES = {
    "national-soi-workbooks",
    "cbo-source-documents",
    "snap-source-documents",
    "tanf-source-documents",
    "medicaid-source-documents",
    "tax-expenditure-source-documents",
    "health-source-documents",
    "ssa-source-documents",
    "macro-source-documents",
    "medicare-source-documents",
}
FRED_SOURCE_ID = "fred"


@dataclass(frozen=True)
class PeSourcePlanItem:
    """One PE source artifact migration work item."""

    item_id: str
    batch_id: str
    priority: int
    recommended_stage: str
    source_id: str
    publisher_hint: str | None
    package_id: str
    package_path: str
    jurisdiction: str
    pipeline: str
    artifact_role: str
    artifact_kind: str
    artifact: str
    filename: str
    format: str
    exists_locally: str
    ledger_source_status: str
    source_cell_status: str
    target_construction_status: str
    command_hint: str
    blockers: tuple[str, ...]
    notes: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable work item."""
        data = asdict(self)
        data["blockers"] = list(self.blockers)
        return data


@dataclass(frozen=True)
class PeSourcePlanBatch:
    """One batch of PE source migration work items."""

    batch_id: str
    recommended_stage: str
    item_count: int
    priority_min: int
    priority_max: int
    items: tuple[PeSourcePlanItem, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable batch."""
        return {
            "batch_id": self.batch_id,
            "recommended_stage": self.recommended_stage,
            "item_count": self.item_count,
            "priority_min": self.priority_min,
            "priority_max": self.priority_max,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class PeSourcePlanReport:
    """Agent-ready PE source migration plan."""

    manifest_path: str
    row_count: int
    batch_size: int
    counts: dict[str, dict[str, int]]
    batches: tuple[PeSourcePlanBatch, ...]

    @property
    def item_count(self) -> int:
        """Return total work item count."""
        return sum(batch.item_count for batch in self.batches)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": True,
            "manifest_path": self.manifest_path,
            "row_count": self.row_count,
            "item_count": self.item_count,
            "batch_size": self.batch_size,
            "counts": self.counts,
            "batches": [batch.to_dict() for batch in self.batches],
        }


def build_pe_source_plan(
    manifest_path: str | Path,
    *,
    batch_size: int = 10,
    max_items: int | None = None,
    source_package_root: str | Path | None = "packages",
) -> PeSourcePlanReport:
    """Build an agent-ready batch plan from a PE source manifest CSV."""
    path = Path(manifest_path)
    rows = _read_manifest_rows(path)
    existing_packages = _existing_source_package_coverage(source_package_root)
    items = [
        _item_from_row(index + 1, row, existing_packages=existing_packages)
        for index, row in enumerate(rows)
    ]
    items.sort(key=_sort_key)
    if max_items is not None:
        items = items[:max_items]
    batches = _assign_batches(items, batch_size=batch_size)
    return PeSourcePlanReport(
        manifest_path=str(path),
        row_count=len(rows),
        batch_size=batch_size,
        counts=_counts(rows, items),
        batches=batches,
    )


def write_pe_source_plan_json(
    report: PeSourcePlanReport,
    output_path: str | Path,
) -> None:
    """Write a PE source plan report as JSON."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_pe_source_plan_markdown(
    report: PeSourcePlanReport,
    output_path: str | Path,
) -> None:
    """Write a compact Markdown view of a PE source plan report."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# PE Source Agent Plan",
        "",
        "This is an agent work queue generated from the PE source manifest. "
        "It is inventory/scaffold oriented: agents should not claim semantic "
        "target readiness until `build-suite` passes for a package.",
        "",
        f"Manifest rows: {report.row_count}",
        f"Planned items: {report.item_count}",
        f"Batches: {len(report.batches)}",
        "",
        "## Counts",
        "",
    ]
    for count_name, counts in report.counts.items():
        lines.extend([f"### {count_name}", ""])
        lines.extend(f"- `{key}`: {value}" for key, value in sorted(counts.items()))
        lines.append("")

    lines.extend(["## Batches", ""])
    for batch in report.batches:
        lines.extend(
            [
                f"### {batch.batch_id}",
                "",
                f"Stage: `{batch.recommended_stage}`",
                f"Items: {batch.item_count}",
                "",
                "| Priority | Source | Publisher hint | Pipeline | File | Stage | Package |",
                "|---:|---|---|---|---|---|---|",
            ]
        )
        for item in batch.items:
            lines.append(
                "| "
                f"{item.priority} | "
                f"{item.source_id} | "
                f"{item.publisher_hint or ''} | "
                f"{item.pipeline} | "
                f"`{item.filename}` | "
                f"{item.recommended_stage} | "
                f"`{item.package_id}` |"
            )
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _read_manifest_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def _item_from_row(
    index: int,
    row: dict[str, str],
    *,
    existing_packages: dict[str, dict[str, str]],
) -> PeSourcePlanItem:
    package_id = _package_id_for_row(row)
    notes_package_id = None
    notes_package = None
    if row["ledger_source_status"] == "source_package":
        notes_package_id = _package_id_from_notes(row["notes"])
        if notes_package_id:
            notes_package = existing_packages.get(f"package_id:{notes_package_id}")
    existing_package = existing_packages.get(row["artifact"])
    if notes_package is not None and (
        existing_package is None or existing_package["package_id"] != notes_package_id
    ):
        existing_package = notes_package
    if existing_package is None and row["ledger_source_status"] == "source_package":
        existing_package = existing_packages.get(row["filename"])
        if notes_package is not None and (
            existing_package is None
            or existing_package["package_id"] != notes_package_id
        ):
            existing_package = notes_package
    stage = _recommended_stage(row, existing_package=existing_package)
    publisher_hint = _publisher_hint_for_row(row)
    if existing_package:
        package_id = existing_package["package_id"]
    priority = _priority(row, stage)
    package_path = (
        existing_package["package_path"]
        if existing_package
        else (
            f"packages/pe_{_path_slug(row['jurisdiction'])}/"
            f"{_path_slug(row['source_id'])}/{package_id}"
        )
    )
    return PeSourcePlanItem(
        item_id=f"pe-source-{index:04d}",
        batch_id="",
        priority=priority,
        recommended_stage=stage,
        source_id=row["source_id"],
        publisher_hint=publisher_hint,
        package_id=package_id,
        package_path=package_path,
        jurisdiction=row["jurisdiction"],
        pipeline=row["pipeline"],
        artifact_role=row["artifact_role"],
        artifact_kind=row["artifact_kind"],
        artifact=row["artifact"],
        filename=row["filename"],
        format=row["format"],
        exists_locally=row["exists_locally"],
        ledger_source_status=row["ledger_source_status"],
        source_cell_status=row["source_cell_status"],
        target_construction_status=row["target_construction_status"],
        command_hint=_command_hint(
            row,
            package_id,
            package_path,
            stage,
            publisher_hint=publisher_hint,
            existing_package=existing_package,
        ),
        blockers=_blockers(row, stage),
        notes=row["notes"],
    )


def _assign_batches(
    items: list[PeSourcePlanItem],
    *,
    batch_size: int,
) -> tuple[PeSourcePlanBatch, ...]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    batches = []
    grouped: dict[str, list[PeSourcePlanItem]] = {}
    for item in items:
        grouped.setdefault(item.recommended_stage, []).append(item)
    stage_order = {
        "existing_source_package": 0,
        "find_primary_source": 0,
        "fetch_artifact": 0,
        "register_local_artifact": 1,
        "source_cell_scaffold": 2,
        "repair_artifact_ingest": 3,
        "blocked_or_deferred": 4,
        "blocked_missing_local_artifact": 5,
    }
    for stage, stage_items in sorted(
        grouped.items(),
        key=lambda entry: (stage_order.get(entry[0], 99), entry[0]),
    ):
        for chunk_index, start in enumerate(range(0, len(stage_items), batch_size), 1):
            chunk = stage_items[start : start + batch_size]
            batch_id = f"pe-us-{_path_slug(stage)}-{chunk_index:03d}"
            chunk_with_batch = tuple(
                PeSourcePlanItem(
                    **{
                        **item.to_dict(),
                        "batch_id": batch_id,
                        "blockers": item.blockers,
                    }
                )
                for item in chunk
            )
            priorities = [item.priority for item in chunk_with_batch]
            batches.append(
                PeSourcePlanBatch(
                    batch_id=batch_id,
                    recommended_stage=stage,
                    item_count=len(chunk_with_batch),
                    priority_min=min(priorities),
                    priority_max=max(priorities),
                    items=chunk_with_batch,
                )
            )
    return tuple(batches)


def _recommended_stage(
    row: dict[str, str],
    *,
    existing_package: dict[str, str] | None,
) -> str:
    if existing_package is not None:
        return "existing_source_package"
    status = row["ledger_source_status"]
    if status in {"blocked", "deferred"}:
        return "blocked_or_deferred"
    if status == "source_package":
        return "existing_source_package"
    if status == "row_parsed":
        return "source_cell_scaffold"
    if status in {"fetch_error", "identity_mismatch", "parsed_no_rows"}:
        return "repair_artifact_ingest"
    if row["source_id"] == FRED_SOURCE_ID:
        return "find_primary_source"
    if row["artifact_kind"] == "url":
        return "fetch_artifact"
    if row["exists_locally"] == "yes":
        return "register_local_artifact"
    return "blocked_missing_local_artifact"


def _priority(row: dict[str, str], stage: str) -> int:
    pipeline = row["pipeline"]
    artifact_format = row["format"]
    if stage == "existing_source_package":
        return 105
    if stage == "blocked_or_deferred":
        return 10
    if stage == "find_primary_source":
        return 75
    if row["source_id"] == FRED_SOURCE_ID:
        return 70
    if pipeline in LOW_PRIORITY_PIPELINES:
        return 20
    if pipeline == "national-soi-workbooks":
        return 100
    if stage == "source_cell_scaffold" and row["artifact_role"] == "publisher_source":
        return 95
    if pipeline in HIGH_PRIORITY_PIPELINES:
        return 90
    if artifact_format in WORKBOOK_FORMATS:
        return 85
    if artifact_format in RECTANGULAR_FORMATS:
        return 80
    if artifact_format in DOCUMENT_FORMATS:
        return 70
    if stage == "source_cell_scaffold":
        return 65
    return 50


def _sort_key(item: PeSourcePlanItem) -> tuple[int, str, str, str]:
    return (-item.priority, item.recommended_stage, item.pipeline, item.filename)


def _counts(
    rows: list[dict[str, str]],
    items: list[PeSourcePlanItem],
) -> dict[str, dict[str, int]]:
    return {
        "by_artifact_role": dict(
            Counter(row["artifact_role"] for row in rows).most_common()
        ),
        "by_ledger_source_status": dict(
            Counter(row["ledger_source_status"] for row in rows).most_common()
        ),
        "by_format": dict(Counter(row["format"] for row in rows).most_common()),
        "by_pipeline": dict(Counter(row["pipeline"] for row in rows).most_common()),
        "by_recommended_stage": dict(
            Counter(item.recommended_stage for item in items).most_common()
        ),
        "by_publisher_hint": dict(
            Counter(item.publisher_hint or "missing" for item in items).most_common()
        ),
    }


def _package_id_for_row(row: dict[str, str]) -> str:
    source = _slug(row["source_id"])
    filename = row["filename"] or Path(row["artifact"]).name or "source"
    stem = _filename_stem(filename)
    name = _slug(stem)
    if name.startswith(f"{source}-"):
        return name
    return f"{source}-{name}" if source else name


def _filename_stem(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".csv.gz"):
        return filename[:-7]
    return Path(filename).stem


def _year_from_row(row: dict[str, str]) -> str:
    text = f"{row['filename']} {row['artifact']}"
    for token in re.split(r"[^A-Za-z0-9]+", text):
        match = re.fullmatch(r"(?:fy|ty)?((?:19|20)[0-9]{2})", token.lower())
        if match and 1900 <= int(match.group(1)) <= 2035:
            return match.group(1)
    return "TODO_YEAR"


def _command_hint(
    row: dict[str, str],
    package_id: str,
    package_path: str,
    stage: str,
    *,
    publisher_hint: str | None,
    existing_package: dict[str, str] | None,
) -> str:
    year = _year_from_row(row)
    source_id = row["source_id"]
    data_dir = f"db/data/pe/{row['jurisdiction']}/{_path_slug(source_id)}/{package_id}"
    if stage == "existing_source_package":
        if existing_package is None:
            return (
                "Manifest marks this row as source_package/build_suite_valid, "
                "but no local source_package manifest was matched by source URL. "
                "Use the package named in the manifest notes, or update the "
                "package manifest/source-page coverage mapping."
            )
        package_year = existing_package["year"] if existing_package else year
        return " ".join(
            shlex.quote(part)
            for part in (
                "uv",
                "run",
                "chronicle",
                "build-suite",
                package_path,
                "--year",
                package_year,
                "--out",
                f"/tmp/chronicle-suite-{package_id}-{package_year}",
                "--replace",
            )
        )
    if stage == "find_primary_source":
        if publisher_hint:
            return (
                f"Find and register the {publisher_hint} publisher artifact behind "
                f"{row['artifact']}. Do not use this FRED URL as a Chronicle source "
                "artifact."
            )
        return (
            f"Identify the publisher source behind {row['artifact']}. Do not use "
            "this FRED URL as a Chronicle source artifact."
        )
    if stage == "fetch_artifact":
        return " ".join(
            shlex.quote(part)
            for part in (
                "uv",
                "run",
                "chronicle",
                "fetch-artifact",
                "--url",
                row["artifact"],
                "--source-id",
                source_id,
                "--package-id",
                package_id,
                "--year",
                year,
                "--out-dir",
                data_dir,
                "--kind",
                "publisher_table",
                "--upload-r2",
            )
        )
    if stage == "register_local_artifact":
        return (
            "Resolve the PE checkout path, then run "
            "`uv run chronicle fetch-artifact --url <local-path> "
            f"--source-id {source_id} --package-id {package_id} "
            f"--year {year} --out-dir {data_dir} "
            "--kind publisher_table --upload-r2`."
        )
    if stage == "source_cell_scaffold":
        return " ".join(
            shlex.quote(part)
            for part in (
                "uv",
                "run",
                "chronicle",
                "scaffold-package",
                "--source-id",
                source_id,
                "--package-id",
                package_id,
                "--out",
                package_path,
                "--source-table",
                row["filename"],
                "--resource-directory",
                data_dir.removeprefix("db/"),
            )
        )
    if stage == "repair_artifact_ingest":
        return "Re-fetch or re-register the artifact, then regenerate the PE manifest."
    if stage == "blocked_or_deferred":
        return (
            "No package action until the manifest blocker is resolved: "
            f"{row['ledger_source_status']} / {row['source_cell_status']}."
        )
    return "Resolve the missing local artifact before source-cell or target work."


def _existing_source_package_coverage(
    source_package_root: str | Path | None,
) -> dict[str, dict[str, str]]:
    if source_package_root is None:
        return {}
    root = Path(source_package_root)
    if not root.exists():
        return {}
    repo_root = Path.cwd()
    coverage = {}
    for source_package_path in sorted(root.glob("**/source_package.yaml")):
        try:
            package_payload = yaml.safe_load(
                source_package_path.read_text(encoding="utf-8")
            )
            artifact_payload = package_payload["artifact"]
            manifest_path = (
                repo_root
                / artifact_payload["resource_package"]
                / artifact_payload["resource_directory"]
                / artifact_payload["manifest"]
            )
            manifest_payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except (KeyError, OSError, TypeError, yaml.YAMLError):
            continue
        package_id = str(
            package_payload.get("package_id") or source_package_path.parent.name
        )
        artifact_year = artifact_payload.get("artifact_year")
        artifact_year_text = str(artifact_year) if artifact_year is not None else None
        package_by_id: dict[str, str] | None = None
        for year, spec in manifest_payload.get("files", {}).items():
            if not isinstance(spec, dict) or not spec.get("source_url"):
                continue
            package = {
                "package_id": package_id,
                "package_path": str(source_package_path.parent),
                "year": str(year),
                "filename": str(spec.get("filename", "")),
            }
            if artifact_year_text is None or str(year) == artifact_year_text:
                package_by_id = {
                    **package,
                    "year": str(artifact_year or year),
                }
            if artifact_year_text is not None and str(year) != artifact_year_text:
                continue
            coverage.setdefault(str(spec["source_url"]), package)
            filename = package["filename"]
            if filename:
                coverage.setdefault(filename, package)
        if package_by_id is not None:
            coverage.setdefault(f"package_id:{package_id}", package_by_id)
    return coverage


def _blockers(row: dict[str, str], stage: str) -> tuple[str, ...]:
    blockers = []
    if stage == "find_primary_source":
        blockers.append("publisher_source_required")
        if not _publisher_hint_for_row(row):
            blockers.append("publisher_not_identified")
    if row["pipeline"] in LOW_PRIORITY_PIPELINES:
        blockers.append("bulk_local_geography_source")
    if row["format"] in {".zip", ".pdf"} and stage == "source_cell_scaffold":
        blockers.append("needs_format_specific_source_cell_parser")
    if stage == "blocked_missing_local_artifact":
        blockers.append("local_artifact_missing")
    if stage == "repair_artifact_ingest":
        blockers.append(row["ledger_source_status"])
    if stage == "blocked_or_deferred":
        blockers.append(row["ledger_source_status"])
        if row["source_cell_status"]:
            blockers.append(row["source_cell_status"])
    return tuple(blockers)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-+", "-", slug) or "source"


def _path_slug(value: str) -> str:
    return _slug(value).replace("-", "_")


def _publisher_hint_for_row(row: dict[str, str]) -> str | None:
    text = f"{row['filename']} {row['artifact']} {row['notes']}".lower()
    if "bea" in text:
        return "bea"
    if "federal reserve" in text or "bogz" in text:
        return "federal_reserve"
    return None


def _package_id_from_notes(notes: str) -> str | None:
    match = re.search(r"\bPackage\s+([a-z0-9][a-z0-9-]*)\b", notes)
    return match.group(1) if match else None
