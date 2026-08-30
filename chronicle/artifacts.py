"""Source artifact acquisition and storage helpers for Chronicle."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import posixpath
import shlex
import sqlite3
import subprocess
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
import yaml


DEFAULT_R2_RAW_BUCKET = "ledger-raw"
DEFAULT_R2_DERIVED_BUCKET = "ledger-derived"
DEFAULT_R2_PREFIX = "raw"
DEFAULT_R2_DERIVED_PREFIX = "derived"

# UK and New Zealand uploads are namespaced by country. The US is deliberately
# a jurisdiction-wide legacy namespace: existing and newly onboarded US
# publishers keep the ``raw/{source_id}`` and ``derived/{source_id}`` shapes
# until a separate, consumer-audited US migration changes this contract.
# Publisher directories are the stable routing input because they are shared
# by packages/ and db/data/.
R2_COUNTRY_PUBLISHERS = {
    "nz": frozenset({"ird", "mbie", "msd", "stats_nz"}),
    "uk": frozenset(
        {
            "dft",
            "dwp",
            "hmrc",
            "isc",
            "mhclg",
            "nisra",
            "nrs",
            "obr",
            "ons",
            "scotgov",
            "slc",
            "voa",
            "welshgov",
        }
    ),
}


@dataclass(frozen=True)
class ArtifactStorageLocation:
    """Location metadata for a stored artifact."""

    provider: str
    bucket: str
    key: str

    @property
    def uri(self) -> str:
        """Return a storage URI."""
        return f"{self.provider}://{self.bucket}/{self.key}"

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable location."""
        return {
            "provider": self.provider,
            "bucket": self.bucket,
            "key": self.key,
            "uri": self.uri,
        }


@dataclass(frozen=True)
class ArtifactCommandResult:
    """Result from a storage command."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Whether the command succeeded."""
        return self.returncode == 0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable result."""
        return {
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass(frozen=True)
class ArtifactFetchReport:
    """Report from fetching and storing one source artifact."""

    source_id: str
    package_id: str
    year: int
    source_url: str
    filename: str
    local_path: str
    manifest_path: str
    sha256: str
    size_bytes: int
    fetched_at: str
    r2_location: ArtifactStorageLocation | None
    r2_upload: ArtifactCommandResult | None
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether acquisition and optional upload succeeded."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": self.valid,
            "source_id": self.source_id,
            "package_id": self.package_id,
            "year": self.year,
            "source_url": self.source_url,
            "filename": self.filename,
            "local_path": self.local_path,
            "manifest_path": self.manifest_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "fetched_at": self.fetched_at,
            "r2_location": (self.r2_location.to_dict() if self.r2_location else None),
            "r2_upload": self.r2_upload.to_dict() if self.r2_upload else None,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class ArtifactInventoryEntry:
    """One manifest-declared source artifact status."""

    manifest_path: str
    year: str
    filename: str
    local_path: str
    exists: bool
    sha256_expected: str | None
    sha256_actual: str | None
    size_bytes: int | None
    source_url: str | None
    r2: dict[str, Any] | None
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        """Whether this artifact is locally available and checksum-valid."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable inventory entry."""
        return {
            "valid": self.valid,
            "manifest_path": self.manifest_path,
            "year": self.year,
            "filename": self.filename,
            "local_path": self.local_path,
            "exists": self.exists,
            "sha256_expected": self.sha256_expected,
            "sha256_actual": self.sha256_actual,
            "size_bytes": self.size_bytes,
            "source_url": self.source_url,
            "r2": self.r2,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class ArtifactInventoryReport:
    """Manifest inventory report for source artifacts."""

    root: str
    counts: dict[str, int]
    entries: tuple[ArtifactInventoryEntry, ...]
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether every manifest entry is locally available and valid."""
        return not self.errors and all(entry.valid for entry in self.entries)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": self.valid,
            "root": self.root,
            "counts": self.counts,
            "entries": [entry.to_dict() for entry in self.entries],
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class RawArtifactPublishEntry:
    """One manifest-declared raw artifact upload status."""

    manifest_path: str
    source_id: str
    package_id: str
    year: str
    filename: str
    local_path: str
    sha256: str | None
    size_bytes: int | None
    r2_location: ArtifactStorageLocation | None
    upload: ArtifactCommandResult | None
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether this raw artifact uploaded and was registered."""
        return not self.errors and self.upload is not None and self.upload.ok

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable entry."""
        return {
            "valid": self.valid,
            "manifest_path": self.manifest_path,
            "source_id": self.source_id,
            "package_id": self.package_id,
            "year": self.year,
            "filename": self.filename,
            "local_path": self.local_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "r2_location": (self.r2_location.to_dict() if self.r2_location else None),
            "upload": self.upload.to_dict() if self.upload else None,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class RawArtifactPublishReport:
    """Report from publishing local manifest-declared raw artifacts to R2."""

    root: str
    entries: tuple[RawArtifactPublishEntry, ...]
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether every raw artifact uploaded and manifest metadata was updated."""
        return not self.errors and all(entry.valid for entry in self.entries)

    @property
    def counts(self) -> dict[str, int]:
        """Return summary counts."""
        manifest_paths = {entry.manifest_path for entry in self.entries}
        return {
            "manifest_count": len(manifest_paths),
            "artifact_count": len(self.entries),
            "uploaded_count": sum(1 for entry in self.entries if entry.valid),
            "failed_count": sum(1 for entry in self.entries if not entry.valid),
            "r2_link_count": sum(
                1 for entry in self.entries if entry.r2_location is not None
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": self.valid,
            "root": self.root,
            "counts": self.counts,
            "entries": [entry.to_dict() for entry in self.entries],
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class R2BootstrapReport:
    """Report from bootstrapping Chronicle R2 buckets."""

    buckets: tuple[str, ...]
    commands: tuple[ArtifactCommandResult, ...]
    authenticated: bool
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        """Whether all requested buckets were created or already available."""
        return self.authenticated and not self.errors

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": self.valid,
            "authenticated": self.authenticated,
            "buckets": list(self.buckets),
            "commands": [command.to_dict() for command in self.commands],
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class DerivedArtifactUploadEntry:
    """One derived build artifact upload status."""

    artifact_name: str
    local_path: str
    sha256: str
    size_bytes: int
    r2_location: ArtifactStorageLocation
    upload: ArtifactCommandResult

    @property
    def valid(self) -> bool:
        """Whether this derived artifact uploaded successfully."""
        return self.upload.ok

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable entry."""
        return {
            "valid": self.valid,
            "artifact_name": self.artifact_name,
            "local_path": self.local_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "r2_location": self.r2_location.to_dict(),
            "upload": self.upload.to_dict(),
        }


@dataclass(frozen=True)
class DerivedArtifactPublishReport:
    """Report from publishing deterministic build outputs to R2."""

    input_dir: str
    source_id: str
    package_id: str
    year: int
    build_id: str
    entries: tuple[DerivedArtifactUploadEntry, ...]
    build_artifacts_path: str | None = None
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether every derived artifact uploaded successfully."""
        return not self.errors and all(entry.valid for entry in self.entries)

    @property
    def counts(self) -> dict[str, int]:
        """Return summary counts."""
        return {
            "artifact_count": len(self.entries),
            "uploaded_count": sum(1 for entry in self.entries if entry.valid),
            "failed_count": sum(1 for entry in self.entries if not entry.valid),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": self.valid,
            "input_dir": self.input_dir,
            "source_id": self.source_id,
            "package_id": self.package_id,
            "year": self.year,
            "build_id": self.build_id,
            "build_artifacts_path": self.build_artifacts_path,
            "counts": self.counts,
            "entries": [entry.to_dict() for entry in self.entries],
            "errors": list(self.errors),
        }


def fetch_source_artifact(
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
    r2_bucket: str = DEFAULT_R2_RAW_BUCKET,
    r2_prefix: str | None = None,
    wrangler_command: str = "npx wrangler",
) -> ArtifactFetchReport:
    """Fetch/register a source artifact and optionally upload it to R2."""
    output = Path(output_dir)
    resolved_r2_prefix = resolve_r2_prefix(
        prefix=r2_prefix,
        default_prefix=DEFAULT_R2_PREFIX,
        source_id=source_id,
        package_path=output,
    )
    fetched_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    content, inferred_filename = _read_artifact(source_url)
    artifact_filename = filename or inferred_filename
    if not artifact_filename:
        raise ValueError("Could not infer artifact filename; pass --filename.")

    output.mkdir(parents=True, exist_ok=True)
    local_path = output / artifact_filename
    local_path.write_bytes(content)

    sha256 = hashlib.sha256(content).hexdigest()
    size_bytes = len(content)
    manifest_path = output / "manifest.yaml"

    r2_location = ArtifactStorageLocation(
        provider="r2",
        bucket=r2_bucket,
        key=build_r2_key(
            source_id=source_id,
            package_id=package_id,
            year=year,
            sha256=sha256,
            filename=artifact_filename,
            prefix=resolved_r2_prefix,
            package_path=output,
        ),
    )
    r2_upload = None
    errors: list[str] = []
    if upload_r2:
        r2_upload = _upload_r2_object(
            r2_location,
            local_path,
            wrangler_command=wrangler_command,
        )
        if not r2_upload.ok:
            errors.append("r2_upload_failed")

    _upsert_manifest(
        manifest_path,
        source_id=source_id,
        package_id=package_id,
        dataset=dataset or f"{source_id}_{package_id}",
        source_page=source_page or source_url,
        table=table or package_id,
        year=year,
        filename=artifact_filename,
        source_url=source_url,
        sha256=sha256,
        size_bytes=size_bytes,
        fetched_at=fetched_at,
        r2_location=(r2_location if upload_r2 and r2_upload and r2_upload.ok else None),
    )

    return ArtifactFetchReport(
        source_id=source_id,
        package_id=package_id,
        year=year,
        source_url=source_url,
        filename=artifact_filename,
        local_path=str(local_path),
        manifest_path=str(manifest_path),
        sha256=sha256,
        size_bytes=size_bytes,
        fetched_at=fetched_at,
        r2_location=r2_location if upload_r2 and r2_upload and r2_upload.ok else None,
        r2_upload=r2_upload,
        errors=tuple(errors),
    )


def publish_derived_artifacts(
    input_dir: str | Path,
    *,
    source_id: str,
    package_id: str,
    year: int,
    build_id: str | None = None,
    r2_bucket: str = DEFAULT_R2_DERIVED_BUCKET,
    r2_prefix: str | None = None,
    wrangler_command: str = "npx wrangler",
    build_artifacts_output: str | Path | None = None,
) -> DerivedArtifactPublishReport:
    """Upload a deterministic build output directory to the derived R2 bucket."""
    input_path = Path(input_dir)
    if not input_path.exists():
        return DerivedArtifactPublishReport(
            input_dir=str(input_path),
            source_id=source_id,
            package_id=package_id,
            year=year,
            build_id=build_id or "",
            entries=(),
            build_artifacts_path=str(build_artifacts_output)
            if build_artifacts_output
            else None,
            errors=(f"input_dir_not_found:{input_path}",),
        )
    if not input_path.is_dir():
        return DerivedArtifactPublishReport(
            input_dir=str(input_path),
            source_id=source_id,
            package_id=package_id,
            year=year,
            build_id=build_id or "",
            entries=(),
            build_artifacts_path=str(build_artifacts_output)
            if build_artifacts_output
            else None,
            errors=(f"input_dir_is_not_directory:{input_path}",),
        )

    resolved_build_id = build_id or infer_build_id(input_path)
    if not resolved_build_id:
        return DerivedArtifactPublishReport(
            input_dir=str(input_path),
            source_id=source_id,
            package_id=package_id,
            year=year,
            build_id="",
            entries=(),
            build_artifacts_path=str(build_artifacts_output)
            if build_artifacts_output
            else None,
            errors=("missing_build_id",),
        )

    resolved_r2_prefix = resolve_r2_prefix(
        prefix=r2_prefix,
        default_prefix=DEFAULT_R2_DERIVED_PREFIX,
        source_id=source_id,
    )
    entries: list[DerivedArtifactUploadEntry] = []
    errors: list[str] = []
    artifact_paths = sorted(path for path in input_path.rglob("*") if path.is_file())
    for artifact_path in artifact_paths:
        relative_path = artifact_path.relative_to(input_path).as_posix()
        if relative_path == "build_artifacts.jsonl":
            continue
        content = artifact_path.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        location = ArtifactStorageLocation(
            provider="r2",
            bucket=r2_bucket,
            key=build_derived_r2_key(
                source_id=source_id,
                package_id=package_id,
                year=year,
                build_id=resolved_build_id,
                artifact_name=relative_path,
                prefix=resolved_r2_prefix,
            ),
        )
        upload = _upload_r2_object(
            location,
            artifact_path,
            wrangler_command=wrangler_command,
        )
        if not upload.ok:
            errors.append(f"derived_upload_failed:{relative_path}")
        entries.append(
            DerivedArtifactUploadEntry(
                artifact_name=relative_path,
                local_path=str(artifact_path),
                sha256=sha256,
                size_bytes=len(content),
                r2_location=location,
                upload=upload,
            )
        )

    report = DerivedArtifactPublishReport(
        input_dir=str(input_path),
        source_id=source_id,
        package_id=package_id,
        year=year,
        build_id=resolved_build_id,
        entries=tuple(entries),
        build_artifacts_path=str(build_artifacts_output)
        if build_artifacts_output
        else None,
        errors=tuple(errors),
    )
    if build_artifacts_output is not None:
        write_build_artifacts_jsonl(report, build_artifacts_output)
    return report


def publish_source_artifacts(
    root: str | Path,
    *,
    manifest_filename: str = "manifest.yaml",
    source_id: str | None = None,
    package_id: str | None = None,
    r2_bucket: str = DEFAULT_R2_RAW_BUCKET,
    r2_prefix: str | None = None,
    wrangler_command: str = "npx wrangler",
) -> RawArtifactPublishReport:
    """Upload manifest-declared raw source artifacts and record R2 locations."""
    root_path = Path(root)
    if not root_path.exists():
        return RawArtifactPublishReport(
            root=str(root_path),
            entries=(),
            errors=(f"Root does not exist: {root_path}",),
        )

    entries: list[RawArtifactPublishEntry] = []
    errors: list[str] = []
    for manifest_path in sorted(root_path.rglob(manifest_filename)):
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"Could not read {manifest_path}: {exc}")
            continue

        manifest_source_id = source_id or manifest.get("source_id")
        manifest_package_id = package_id or manifest.get("package_id")
        files = manifest.get("files") or {}
        if not manifest_source_id:
            errors.append(f"Manifest missing source_id: {manifest_path}")
            continue
        if not manifest_package_id:
            errors.append(f"Manifest missing package_id: {manifest_path}")
            continue
        if not isinstance(files, dict):
            errors.append(f"Manifest files must be a mapping: {manifest_path}")
            continue

        try:
            resolved_r2_prefix = resolve_r2_prefix(
                prefix=r2_prefix,
                default_prefix=DEFAULT_R2_PREFIX,
                source_id=str(manifest_source_id),
                package_path=manifest_path,
            )
        except ValueError as exc:
            errors.append(f"Could not resolve R2 prefix for {manifest_path}: {exc}")
            continue

        updated = False
        for year, spec in files.items():
            entry, updated_spec = _publish_raw_manifest_entry(
                manifest_path,
                manifest_source_id,
                manifest_package_id,
                year,
                spec,
                r2_bucket=r2_bucket,
                r2_prefix=resolved_r2_prefix,
                wrangler_command=wrangler_command,
            )
            entries.append(entry)
            if updated_spec is not None and isinstance(spec, dict):
                spec.update(updated_spec)
                updated = True
        if updated:
            manifest.setdefault("source_id", manifest_source_id)
            manifest.setdefault("package_id", manifest_package_id)
            manifest_path.write_text(
                yaml.safe_dump(manifest, sort_keys=False),
                encoding="utf-8",
            )

    return RawArtifactPublishReport(
        root=str(root_path),
        entries=tuple(entries),
        errors=tuple(errors),
    )


def build_artifact_rows(
    report: DerivedArtifactPublishReport,
) -> tuple[dict[str, Any], ...]:
    """Build relational build_artifacts rows from a derived publish report."""
    rows: list[dict[str, Any]] = []
    for entry in report.entries:
        if not entry.valid:
            continue
        rows.append(
            {
                "build_artifact_key": build_artifact_key(
                    build_id=report.build_id,
                    artifact_name=entry.artifact_name,
                    sha256=entry.sha256,
                ),
                "build_id": report.build_id,
                "artifact_kind": _derived_artifact_kind(entry.artifact_name),
                "artifact_name": entry.artifact_name,
                "sha256": entry.sha256,
                "size_bytes": entry.size_bytes,
                "r2_bucket": entry.r2_location.bucket,
                "r2_key": entry.r2_location.key,
                "r2_uri": entry.r2_location.uri,
            }
        )
    return tuple(rows)


def write_build_artifacts_jsonl(
    report: DerivedArtifactPublishReport,
    output_path: str | Path,
) -> None:
    """Write build_artifacts JSONL rows for a derived publish report."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in build_artifact_rows(report):
            file.write(json.dumps(row, sort_keys=True))
            file.write("\n")


def inventory_source_artifacts(
    root: str | Path,
    *,
    manifest_filename: str = "manifest.yaml",
) -> ArtifactInventoryReport:
    """Inventory manifest-declared source artifacts under a root directory."""
    root_path = Path(root)
    errors: list[str] = []
    entries: list[ArtifactInventoryEntry] = []
    if not root_path.exists():
        return ArtifactInventoryReport(
            root=str(root_path),
            counts={
                "manifest_count": 0,
                "artifact_count": 0,
                "missing_count": 0,
                "checksum_mismatch_count": 0,
                "r2_link_count": 0,
            },
            entries=(),
            errors=(f"Root does not exist: {root_path}",),
        )

    manifests = sorted(root_path.rglob(manifest_filename))
    for manifest_path in manifests:
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            files = manifest.get("files") or {}
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"Could not read {manifest_path}: {exc}")
            continue
        if not isinstance(files, dict):
            errors.append(f"Manifest files must be a mapping: {manifest_path}")
            continue
        for year, spec in files.items():
            entries.append(_inventory_entry(manifest_path, year, spec))

    counts = {
        "manifest_count": len(manifests),
        "artifact_count": len(entries),
        "missing_count": sum(1 for entry in entries if not entry.exists),
        "checksum_mismatch_count": sum(
            1 for entry in entries if "checksum_mismatch" in entry.errors
        ),
        "r2_link_count": sum(1 for entry in entries if entry.r2 is not None),
    }
    return ArtifactInventoryReport(
        root=str(root_path),
        counts=counts,
        entries=tuple(entries),
        errors=tuple(errors),
    )


def bootstrap_r2_buckets(
    *,
    raw_bucket: str = DEFAULT_R2_RAW_BUCKET,
    derived_bucket: str = DEFAULT_R2_DERIVED_BUCKET,
    wrangler_command: str = "npx wrangler",
) -> R2BootstrapReport:
    """Create the R2 buckets Chronicle expects, if Wrangler is authenticated."""
    buckets = (raw_bucket, derived_bucket)
    commands: list[ArtifactCommandResult] = []
    errors: list[str] = []

    auth = _run_command([*shlex.split(wrangler_command), "whoami"])
    commands.append(auth)
    authenticated = (
        auth.ok and "not authenticated" not in (auth.stdout + auth.stderr).lower()
    )
    if not authenticated:
        return R2BootstrapReport(
            buckets=buckets,
            commands=tuple(commands),
            authenticated=False,
            errors=(
                "wrangler_not_authenticated: run `npx wrangler login` in the "
                "PolicyEngine Cloudflare account, then rerun this command.",
            ),
        )

    for bucket in buckets:
        command = _run_command(
            [*shlex.split(wrangler_command), "r2", "bucket", "create", bucket]
        )
        commands.append(command)
        combined_output = (command.stdout + command.stderr).lower()
        if not command.ok and "already exists" not in combined_output:
            errors.append(f"r2_bucket_create_failed:{bucket}")

    return R2BootstrapReport(
        buckets=buckets,
        commands=tuple(commands),
        authenticated=authenticated,
        errors=tuple(errors),
    )


def infer_r2_country(
    *,
    source_id: str | None = None,
    package_path: str | Path | None = None,
) -> str | None:
    """Infer an R2 country segment from a package publisher directory.

    ``package_path`` is authoritative when supplied. ``source_id`` is a
    fallback for low-level callers that do not have a package path, including
    derived-artifact publication.
    """
    publisher = None
    if package_path is not None:
        path_parts = tuple(part.lower() for part in Path(package_path).parts)
        # Match the innermost canonical root with both publisher and package
        # directories. A package itself may be named "packages"; the following
        # manifest filename must not then be mistaken for a publisher.
        for index in range(len(path_parts) - 1, -1, -1):
            if path_parts[index : index + 2] == ("db", "data"):
                if index + 3 < len(path_parts):
                    publisher = path_parts[index + 2]
                    break
            elif path_parts[index] == "packages" and index + 2 < len(path_parts):
                publisher = path_parts[index + 1]
                break
    path_country = next(
        (
            country
            for country, publishers in R2_COUNTRY_PUBLISHERS.items()
            if publisher in publishers
        ),
        None,
    )

    source_countries: set[str] = set()
    normalized_source_id = (source_id or "").lower()
    if normalized_source_id:
        source_countries = {
            country
            for country, publishers in R2_COUNTRY_PUBLISHERS.items()
            if any(
                normalized_source_id == publisher
                or normalized_source_id.startswith(f"{publisher}_")
                or normalized_source_id.startswith(f"{publisher}-")
                for publisher in publishers
            )
        }
        if len(source_countries) > 1:
            raise ValueError(f"source_id maps to multiple R2 countries: {source_id}")

    source_country = next(iter(source_countries)) if source_countries else None
    if publisher is not None and source_country and source_country != path_country:
        raise ValueError(
            "package publisher directory and source_id map to different "
            f"countries: publisher={publisher!r}, source_id={source_id!r}"
        )
    return path_country if publisher is not None else source_country


def resolve_r2_prefix(
    *,
    prefix: str | None,
    default_prefix: str,
    source_id: str | None = None,
    package_path: str | Path | None = None,
) -> str:
    """Return the country-aware R2 prefix for one source package."""
    resolved = _clean_key_part(prefix or default_prefix)
    country = infer_r2_country(source_id=source_id, package_path=package_path)
    if country is None:
        return resolved

    suffix = resolved.rsplit("/", maxsplit=1)[-1]
    if suffix in R2_COUNTRY_PUBLISHERS:
        if suffix != country:
            raise ValueError(
                f"R2 prefix country {suffix!r} disagrees with publisher "
                f"country {country!r}"
            )
        return resolved
    return posixpath.join(resolved, country)


def build_r2_key(
    *,
    source_id: str,
    package_id: str,
    year: int | str,
    sha256: str,
    filename: str,
    prefix: str | None = None,
    package_path: str | Path | None = None,
) -> str:
    """Build the canonical immutable R2 key for a raw source artifact.

    ``year`` is usually a calendar year but may be a label such as
    ``source_capture`` for non-year manifest file entries.
    """
    resolved_prefix = resolve_r2_prefix(
        prefix=prefix,
        default_prefix=DEFAULT_R2_PREFIX,
        source_id=source_id,
        package_path=package_path,
    )
    return posixpath.join(
        resolved_prefix,
        _clean_key_part(source_id),
        _clean_key_part(package_id),
        str(year),
        sha256,
        Path(filename).name,
    )


def build_derived_r2_key(
    *,
    source_id: str,
    package_id: str,
    year: int,
    build_id: str,
    artifact_name: str,
    prefix: str | None = None,
) -> str:
    """Build the canonical R2 key for a derived build artifact."""
    resolved_prefix = resolve_r2_prefix(
        prefix=prefix,
        default_prefix=DEFAULT_R2_DERIVED_PREFIX,
        source_id=source_id,
    )
    return posixpath.join(
        resolved_prefix,
        _clean_key_part(source_id),
        _clean_key_part(package_id),
        str(year),
        _clean_key_part(build_id),
        *_clean_relative_key_parts(artifact_name),
    )


def build_artifact_key(
    *,
    build_id: str,
    artifact_name: str,
    sha256: str,
) -> str:
    """Build a stable key for a derived build artifact registry row."""
    payload = json.dumps(
        {
            "artifact_name": artifact_name,
            "build_id": build_id,
            "sha256": sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"ledger.build_artifact.v1:{hashlib.sha256(payload).hexdigest()[:32]}"


def infer_build_id(input_dir: str | Path) -> str | None:
    """Infer a build ID from standard Chronicle build-suite outputs."""
    input_path = Path(input_dir)
    summary_path = input_path / "reports" / "build_summary.json"
    if summary_path.exists():
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        build_id = payload.get("reports", {}).get("database", {}).get("build_id")
        if build_id:
            return str(build_id)

    database_report_path = input_path / "reports" / "database.json"
    if database_report_path.exists():
        payload = json.loads(database_report_path.read_text(encoding="utf-8"))
        build_id = payload.get("build_id")
        if build_id:
            return str(build_id)

    db_path = input_path / "ledger.db"
    if not db_path.exists():
        db_path = input_path / "ledger.db"
    if db_path.exists():
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                "SELECT build_id FROM ledger_builds ORDER BY build_id LIMIT 1"
            ).fetchone()
            if row:
                return str(row[0])
    return None


def _read_artifact(source_url: str) -> tuple[bytes, str]:
    parsed = urlparse(source_url)
    if parsed.scheme in ("http", "https"):
        response = httpx.get(source_url, follow_redirects=True, timeout=60)
        response.raise_for_status()
        return response.content, _filename_from_url(source_url)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
        return path.read_bytes(), path.name
    if not parsed.scheme:
        path = Path(source_url)
        return path.read_bytes(), path.name
    raise ValueError(f"Unsupported source URL scheme: {parsed.scheme}")


def _filename_from_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    return Path(unquote(parsed.path)).name


def _upsert_manifest(
    manifest_path: Path,
    *,
    source_id: str,
    package_id: str,
    dataset: str,
    source_page: str,
    table: str,
    year: int,
    filename: str,
    source_url: str,
    sha256: str,
    size_bytes: int,
    fetched_at: str,
    r2_location: ArtifactStorageLocation | None,
) -> None:
    if manifest_path.exists():
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    else:
        payload = {}
    payload.setdefault("source_id", source_id)
    payload.setdefault("package_id", package_id)
    payload.setdefault("dataset", dataset)
    payload.setdefault("source_page", source_page)
    payload.setdefault("table", table)
    payload.setdefault("files", {})
    file_entry: dict[str, Any] = {
        "filename": filename,
        "source_url": source_url,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "fetched_at": fetched_at,
    }
    if r2_location is not None:
        file_entry["storage"] = {"r2": r2_location.to_dict()}
    payload["files"][year] = file_entry
    manifest_path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


def _upload_r2_object(
    location: ArtifactStorageLocation,
    local_path: Path,
    *,
    wrangler_command: str,
) -> ArtifactCommandResult:
    content_type, _ = mimetypes.guess_type(local_path.name)
    command = [
        *shlex.split(wrangler_command),
        "r2",
        "object",
        "put",
        f"{location.bucket}/{location.key}",
        "--file",
        str(local_path),
        "--remote",
    ]
    if content_type:
        command.extend(["--content-type", content_type])
    return _run_command(command)


def _publish_raw_manifest_entry(
    manifest_path: Path,
    source_id: str,
    package_id: str,
    year: Any,
    spec: Any,
    *,
    r2_bucket: str,
    r2_prefix: str,
    wrangler_command: str,
) -> tuple[RawArtifactPublishEntry, dict[str, Any] | None]:
    errors: list[str] = []
    if not isinstance(spec, dict):
        spec = {}
        errors.append("malformed_file_spec")
    filename = str(spec.get("filename") or "")
    artifact_path = manifest_path.parent / filename
    sha256_expected = spec.get("sha256")
    sha256_actual = None
    size_bytes = None
    if not filename:
        errors.append("missing_filename")
    elif not artifact_path.exists():
        errors.append("missing_file")
    else:
        content = artifact_path.read_bytes()
        sha256_actual = hashlib.sha256(content).hexdigest()
        size_bytes = len(content)
        if sha256_expected and sha256_actual != sha256_expected:
            errors.append("checksum_mismatch")

    if errors:
        return (
            RawArtifactPublishEntry(
                manifest_path=str(manifest_path),
                source_id=source_id,
                package_id=package_id,
                year=str(year),
                filename=filename,
                local_path=str(artifact_path),
                sha256=sha256_actual,
                size_bytes=size_bytes,
                r2_location=None,
                upload=None,
                errors=tuple(errors),
            ),
            None,
        )

    location = ArtifactStorageLocation(
        provider="r2",
        bucket=r2_bucket,
        key=build_r2_key(
            source_id=source_id,
            package_id=package_id,
            year=year,
            sha256=sha256_actual or "",
            filename=filename,
            prefix=r2_prefix,
            package_path=manifest_path,
        ),
    )
    storage = spec.get("storage") if isinstance(spec.get("storage"), dict) else {}
    recorded_r2 = storage.get("r2") if isinstance(storage.get("r2"), dict) else {}
    recorded_key = recorded_r2.get("key")
    if recorded_key and recorded_key != location.key:
        errors.append(
            "recorded_r2_key_disagrees_with_country_prefix:"
            f"recorded={recorded_key}:expected={location.key}"
        )
        return (
            RawArtifactPublishEntry(
                manifest_path=str(manifest_path),
                source_id=source_id,
                package_id=package_id,
                year=str(year),
                filename=filename,
                local_path=str(artifact_path),
                sha256=sha256_actual,
                size_bytes=size_bytes,
                r2_location=None,
                upload=None,
                errors=tuple(errors),
            ),
            None,
        )
    upload = _upload_r2_object(
        location,
        artifact_path,
        wrangler_command=wrangler_command,
    )
    if not upload.ok:
        errors.append("r2_upload_failed")

    updated_spec: dict[str, Any] | None = None
    if upload.ok:
        updated_spec = {
            "sha256": sha256_actual,
            "size_bytes": size_bytes,
            "storage": {
                **(
                    spec.get("storage") if isinstance(spec.get("storage"), dict) else {}
                ),
                "r2": location.to_dict(),
            },
        }

    return (
        RawArtifactPublishEntry(
            manifest_path=str(manifest_path),
            source_id=source_id,
            package_id=package_id,
            year=str(year),
            filename=filename,
            local_path=str(artifact_path),
            sha256=sha256_actual,
            size_bytes=size_bytes,
            r2_location=location if upload.ok else None,
            upload=upload,
            errors=tuple(errors),
        ),
        updated_spec,
    )


def _inventory_entry(
    manifest_path: Path,
    year: Any,
    spec: Any,
) -> ArtifactInventoryEntry:
    errors: list[str] = []
    if not isinstance(spec, dict):
        spec = {}
        errors.append("malformed_file_spec")
    filename = str(spec.get("filename") or "")
    artifact_path = manifest_path.parent / filename
    exists = bool(filename) and artifact_path.exists()
    sha256_expected = spec.get("sha256")
    sha256_actual = None
    size_bytes = None
    if not filename:
        errors.append("missing_filename")
    elif not exists:
        errors.append("missing_file")
    else:
        content = artifact_path.read_bytes()
        sha256_actual = hashlib.sha256(content).hexdigest()
        size_bytes = len(content)
        if sha256_expected and sha256_actual != sha256_expected:
            errors.append("checksum_mismatch")
    storage = spec.get("storage") if isinstance(spec, dict) else None
    r2 = storage.get("r2") if isinstance(storage, dict) else None
    return ArtifactInventoryEntry(
        manifest_path=str(manifest_path),
        year=str(year),
        filename=filename,
        local_path=str(artifact_path),
        exists=exists,
        sha256_expected=sha256_expected,
        sha256_actual=sha256_actual,
        size_bytes=size_bytes,
        source_url=spec.get("source_url"),
        r2=r2,
        errors=tuple(errors),
    )


def _derived_artifact_kind(artifact_name: str) -> str:
    if artifact_name in {"ledger.db", "ledger.db"}:
        return "sqlite_database"
    if artifact_name.endswith(".jsonl"):
        return "jsonl"
    if artifact_name.startswith("reports/"):
        return "report"
    if artifact_name.endswith(".json"):
        return "json"
    return "artifact"


def _run_command(command: list[str]) -> ArtifactCommandResult:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    return ArtifactCommandResult(
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _clean_key_part(value: str) -> str:
    cleaned = value.strip().strip("/")
    if not cleaned:
        raise ValueError("R2 key parts cannot be empty.")
    return cleaned.replace(" ", "_")


def _clean_relative_key_parts(value: str) -> tuple[str, ...]:
    path = Path(value)
    if path.is_absolute():
        raise ValueError("R2 artifact paths must be relative.")
    parts = tuple(_clean_key_part(part) for part in path.parts if part != ".")
    if not parts or any(part == ".." for part in parts):
        raise ValueError("R2 artifact paths cannot be empty or contain '..'.")
    return parts
