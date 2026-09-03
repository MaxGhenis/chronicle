"""Source artifact acquisition and storage helpers for Chronicle."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import posixpath
import re
import shlex
import sqlite3
import subprocess
import tempfile
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
import yaml

from chronicle.database import (
    CHRONICLE_DB_FILENAME,
    CHRONICLE_DB_FILENAMES,
    LEGACY_CHRONICLE_DB_FILENAME,
)
from chronicle.env import env_value
from chronicle.registration import (
    ACCESS_PUBLIC,
    HASH_SOURCE_CHRONICLE_FETCH,
    MICRODATA_RELEASE_KIND,
    PUBLISHER_TABLE_KIND,
    REDISTRIBUTABLE_MICRODATA_LICENCES,
    manifest_kind as normalize_manifest_kind,
    ListSpecRejected,
    ManifestAccessError,
    canonical_artifact_filename,
    canonical_manifest_filename,
    effective_entry_access,
    is_hash_only,
    iter_file_specs,
    manifest_filename_errors,
    microdata_licence_evidence_errors,
    normalize_access,
    resolve_year_key,
    safe_manifest_kind,
    strict_manifest_kind,
    validate_file_entry,
)


R2_RAW_BUCKET_ENV = "CHRONICLE_R2_RAW_BUCKET"
R2_DERIVED_BUCKET_ENV = "CHRONICLE_R2_DERIVED_BUCKET"

# The bucket defaults stay at their ledger-era names. Archived witness records
# pin raw R2 URLs by hash, so ledger-raw and ledger-derived are preserved
# read-only forever and no recorded manifest URI is ever rewritten. The env
# vars exist so the cutover in docs/storage-architecture.md can be rehearsed,
# and so flipping to chronicle-raw/chronicle-derived is a default change rather
# than a code change (PolicyEngine/chronicle#143, mechanism 3).
DEFAULT_R2_RAW_BUCKET = "ledger-raw"
DEFAULT_R2_DERIVED_BUCKET = "ledger-derived"
DEFAULT_R2_PREFIX = "raw"
DEFAULT_R2_DERIVED_PREFIX = "derived"

# Most packages keep one manifest.yaml. Publisher directories that feed several
# source packages keep one manifest each -- db/data/irs_soi/ira_contributions
# holds manifest_traditional_source_package.yaml beside the Roth one -- so the
# name is an input, not a constant, wherever a caller addresses a package.
DEFAULT_MANIFEST_FILENAME = "manifest.yaml"


def _manifest_path(output: Path, manifest_filename: str) -> Path:
    """Return the named manifest inside ``output``.

    The name is a filename, not a path: it selects among the manifests a
    package directory keeps, and must not reach outside it.
    """
    try:
        name = canonical_manifest_filename(manifest_filename)
    except ManifestAccessError as exc:
        raise ValueError(
            "Manifest must name a file inside the package directory, not "
            f"{manifest_filename!r}."
        ) from exc
    return output / name


def _manifest_paths(root: Path, manifest_filename: str) -> list[Path]:
    """Return exact named manifests below ``root`` without glob expansion."""
    name = _manifest_path(Path(), manifest_filename).name
    return sorted(
        candidate
        for candidate in root.rglob("*")
        if candidate.is_file() and candidate.name == name
    )


def default_r2_raw_bucket() -> str:
    """Resolve the raw bucket: ``$CHRONICLE_R2_RAW_BUCKET`` or the default."""
    return env_value(R2_RAW_BUCKET_ENV, default=DEFAULT_R2_RAW_BUCKET)


def default_r2_derived_bucket() -> str:
    """Resolve the derived bucket: ``$CHRONICLE_R2_DERIVED_BUCKET`` or default."""
    return env_value(R2_DERIVED_BUCKET_ENV, default=DEFAULT_R2_DERIVED_BUCKET)


class SourceArtifactManifestError(RuntimeError):
    """A manifest refuses the write a fetch is about to make.

    The checks that raise these run before the publisher is read, so an
    ordinary refusal costs nothing and leaves the package exactly as it was.
    They are repeated immediately before the manifest is rewritten, so no
    caller can reach a false-provenance write by another route.
    """


class SourceArtifactRevisionError(SourceArtifactManifestError):
    """Fetched bytes are not the bytes the manifest entry identifies.

    A manifest entry identifies specific bytes: by its declared ``sha256``, and
    -- once published -- by a content-addressed R2 key that repeats them. When
    a publisher re-publishes under the same URL and vintage, rewriting that
    entry would attach its provenance, and any recorded URI, to bytes it never
    described. Chronicle refuses instead: same vintage plus new bytes is a new
    release revision (docs/adr-chronicle-fact-identity-v2.md), registered with
    ``fetch-artifact --record-revision``.
    """


class MalformedManifestError(SourceArtifactManifestError):
    """A manifest document, or a block inside one, is not a mapping.

    Reading such a file as an absent manifest would let a fetch replace it with
    a single entry, dropping whatever the unreadable document recorded.
    """


class RecordedR2LocatorError(SourceArtifactManifestError):
    """A recorded ``storage.r2`` block does not locate exactly one object.

    ``provider``, ``bucket``, ``key`` and ``uri`` all describe the same object,
    so any that are supplied have to agree, and the key has to carry the
    ``{sha256}/{filename}`` tail that says which bytes it holds. A block whose
    fields contradict each other has no single answer to "which bytes does this
    entry claim R2 holds", and preserving or publishing under it would ship
    whichever field the reader happened to consult.
    """


class ArtifactIntegrityError(SourceArtifactManifestError):
    """Fetched bytes disagree with an explicitly reviewed checksum or size."""


# New UK and New Zealand uploads are namespaced by country. US objects predate
# the country segment and deliberately keep their legacy ``raw/{source_id}``
# and ``derived/{source_id}`` shapes. Publisher directories are the stable
# routing input because they are shared by packages/ and db/data/.
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


# A raw key ends in {sha256}/{filename} (see build_r2_key), so the segment
# before the filename is what says which bytes the object holds.
_SHA256_KEY_SEGMENT = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class RecordedR2Object:
    """The R2 object a manifest entry's ``storage.r2`` block claims exists.

    Built only by :func:`_validated_recorded_r2`, so every instance names one
    object whose locator fields agree with each other.
    """

    provider: str
    bucket: str
    key: str
    sha256: str
    filename: str

    @property
    def uri(self) -> str:
        """Return the storage URI the recorded fields spell out."""
        return f"{self.provider}://{self.bucket}/{self.key}"


@dataclass(frozen=True)
class RecordedIdentity:
    """The bytes a manifest entry says its vintage currently holds.

    From the recorded object's content-addressed key once the entry has been
    published, and from the entry's own declared ``sha256``/``filename`` before
    that. ``r2`` is None in the second case: protection does not wait for an
    upload to have happened.
    """

    sha256: str
    filename: str
    size_bytes: int | None
    declared_sha256: str | None
    r2: RecordedR2Object | None

    def holds(self, *, sha256: str, filename: str) -> bool:
        """Whether this identity is exactly the given bytes under that name.

        The filename participates only when the entry records one: a published
        key always carries it, an entry that declares bytes and no name does
        not, and inventing a mismatch there would refuse a re-fetch of the very
        bytes the entry describes.
        """
        if self.sha256 != sha256:
            return False
        return not self.filename or self.filename == Path(filename).name


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
    access: str = ACCESS_PUBLIC
    licence: str | None = None
    hash_only: bool = False

    @property
    def valid(self) -> bool:
        """Whether this registration is complete and, if public, available."""
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
            "access": self.access,
            "licence": self.licence,
            "hash_only": self.hash_only,
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
    skipped: str | None = None

    @property
    def valid(self) -> bool:
        """Whether this raw artifact uploaded, or was correctly not uploaded."""
        if self.skipped is not None:
            return not self.errors
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
            "skipped": self.skipped,
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
        uploaded = [
            entry for entry in self.entries if entry.skipped is None and entry.valid
        ]
        return {
            "manifest_count": len(manifest_paths),
            "artifact_count": len(self.entries),
            "uploaded_count": len(uploaded),
            "failed_count": sum(1 for entry in self.entries if not entry.valid),
            "hash_only_refused_count": sum(
                1
                for entry in self.entries
                if entry.skipped is not None
                and entry.skipped.startswith("hash_only_access:")
            ),
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
    manifest_filename: str = DEFAULT_MANIFEST_FILENAME,
    access: str | None = None,
    licence: str | None = None,
    licence_evidence: Mapping[str, str] | None = None,
    kind: str | None = None,
    publisher: str | None = None,
    vintage: str | None = None,
    expected_sha256: str | None = None,
    expected_size_bytes: int | None = None,
    upload_r2: bool = False,
    staging_dir: str | Path | None = None,
    record_revision: bool = False,
    r2_bucket: str | None = None,
    r2_prefix: str | None = None,
    wrangler_command: str = "npx wrangler",
) -> ArtifactFetchReport:
    """Fetch/register a source artifact and optionally upload it to R2.

    ``manifest_filename`` names the manifest inside ``output_dir`` the entry
    belongs to. Packages that split one publisher directory across several
    source packages keep one manifest each, so a fetch that always wrote
    ``manifest.yaml`` would write a fresh manifest beside the real ones and
    never see the entry it is revising.

    ``record_revision`` opts into registering a publisher revision: the fetched
    bytes get their own content-addressed key under the configured bucket and
    the superseded object moves to ``storage.previous_r2``. Without it, bytes
    that disagree with the entry's recorded identity raise
    :class:`SourceArtifactRevisionError` before anything is overwritten.

    Only ``public`` artifacts travel this path. Publisher tables are written
    into the package tree; public microdata is staged transiently outside it
    and uploaded directly. Licensed and restricted artifacts are hash-only with
    :func:`chronicle.registration.register_hash_only_artifact`.
    """
    r2_bucket = r2_bucket or default_r2_raw_bucket()
    output = Path(output_dir)
    manifest_path = _manifest_path(output, manifest_filename)
    # Read and validate the entry being written before anything is fetched: a
    # manifest Chronicle cannot read, or a recorded block that names two
    # different objects, is a refusal that need not touch the publisher.
    existing_manifest = _read_manifest(manifest_path)
    manifest_kind_value = _manifest_kind_for_fetch(
        existing_manifest,
        manifest_path=manifest_path,
        requested_kind=kind,
    )
    _validate_existing_manifest_before_fetch(
        existing_manifest,
        manifest_path=manifest_path,
        kind=manifest_kind_value,
    )
    _validate_manifest_identity_before_fetch(
        existing_manifest,
        manifest_path=manifest_path,
        source_id=source_id,
        package_id=package_id,
    )

    inferred_before_fetch = _filename_from_url(source_url)
    requested_filename = filename if filename is not None else inferred_before_fetch
    if not requested_filename:
        raise ManifestAccessError(
            "Could not infer an artifact basename from --url; pass --filename."
        )
    artifact_filename = canonical_artifact_filename(requested_filename)
    _validate_fetch_slot(
        existing_manifest,
        manifest_path=manifest_path,
        kind=manifest_kind_value,
        year=year,
        filename=artifact_filename,
    )
    package_manifests = _package_manifests_before_fetch(
        manifest_path=manifest_path,
        selected_manifest=existing_manifest,
        selected_kind=manifest_kind_value,
        filename=artifact_filename,
    )
    if any(
        kind_value == MICRODATA_RELEASE_KIND for _, _, kind_value in package_manifests
    ):
        _assert_microdata_package_is_manifest_only(
            manifest_path,
            allowed_manifests={path for path, _, _ in package_manifests},
        )
        _assert_microdata_source_is_outside_db_data(source_url)

    if expected_sha256 is not None and not _SHA256_KEY_SEGMENT.fullmatch(
        expected_sha256
    ):
        raise ArtifactIntegrityError(
            "--expected-sha256 must be a lowercase 64-character SHA-256; got "
            f"{expected_sha256!r}."
        )
    if expected_size_bytes is not None and expected_size_bytes < 0:
        raise ArtifactIntegrityError(
            f"--expected-size-bytes must be non-negative; got {expected_size_bytes!r}."
        )
    if expected_sha256 is not None:
        _assert_package_filename_accepts_identity(
            package_manifests,
            selected_manifest_path=manifest_path,
            year=year,
            filename=artifact_filename,
            sha256=expected_sha256,
        )

    if manifest_kind_value == MICRODATA_RELEASE_KIND and access is None:
        raise ManifestAccessError(
            "A microdata fetch must declare --access explicitly before bytes "
            "are read. Missing access is never inferred as public."
        )
    access_class = normalize_access(access)
    licence_text = licence.strip() if isinstance(licence, str) else None
    if (
        manifest_kind_value == MICRODATA_RELEASE_KIND
        and access_class == ACCESS_PUBLIC
        and not licence_text
    ):
        raise ManifestAccessError(
            f"{manifest_path} registers a microdata release, so every entry "
            "must record its publisher licence; pass --licence."
        )
    evidence = dict(licence_evidence) if isinstance(licence_evidence, Mapping) else None
    evidence_sha = (
        evidence.get("covered_sha256") if isinstance(evidence, Mapping) else None
    )
    proposed_entry = {
        "filename": artifact_filename,
        "access": access_class,
        "licence": licence_text,
        "sha256": expected_sha256 or evidence_sha,
        "licence_evidence": evidence,
    }
    evidence_manifest = dict(existing_manifest)
    if publisher:
        evidence_manifest.setdefault("publisher", publisher)
    evidence_errors = microdata_licence_evidence_errors(
        proposed_entry,
        manifest=evidence_manifest,
    )
    effective_access = effective_entry_access(proposed_entry, kind=manifest_kind_value)
    if (
        manifest_kind_value == MICRODATA_RELEASE_KIND
        and access_class == ACCESS_PUBLIC
        and licence_text in REDISTRIBUTABLE_MICRODATA_LICENCES
        and evidence_errors
    ):
        raise ManifestAccessError(
            "fetch-artifact refuses invalid artifact-bound licence evidence "
            f"({', '.join(evidence_errors)}). Register the artifact hash-only "
            "with `chronicle register-artifact`."
        )
    if is_hash_only(effective_access):
        if access_class != ACCESS_PUBLIC:
            reason = f"access={access_class!r}"
        elif not licence_text or licence_text not in REDISTRIBUTABLE_MICRODATA_LICENCES:
            reason = f"non-allowlisted public microdata licence {licence_text!r}"
        else:
            reason = (
                "invalid artifact-bound licence evidence ("
                + ", ".join(evidence_errors)
                + ")"
            )
        raise ManifestAccessError(
            f"fetch-artifact stores bytes and refuses {reason}. Register the "
            "artifact hash-only with `chronicle register-artifact`."
        )
    if manifest_kind_value == MICRODATA_RELEASE_KIND:
        assert evidence is not None
        covered_sha256 = str(evidence["covered_sha256"])
        if expected_sha256 is None:
            expected_sha256 = covered_sha256
    if manifest_kind_value == MICRODATA_RELEASE_KIND:
        if not publisher or not publisher.strip():
            raise ManifestAccessError(
                "A microdata fetch must record --publisher before bytes are read."
            )
        recorded_publisher = existing_manifest.get("publisher")
        if recorded_publisher is not None and recorded_publisher != publisher:
            raise SourceArtifactManifestError(
                f"{manifest_path} declares publisher={recorded_publisher!r}; "
                f"refusing to write publisher={publisher!r}."
            )
        if not vintage or not vintage.strip():
            raise ManifestAccessError(
                "A microdata fetch must record --vintage before bytes are read."
            )
        if not upload_r2:
            raise ManifestAccessError(
                "Public microdata bytes may only be acquired for immediate R2 "
                "upload; pass --upload-r2 and --staging-dir."
            )
        if staging_dir is None:
            raise ManifestAccessError(
                "Public microdata bytes require an untracked transient "
                "--staging-dir outside db/data before bytes are read."
            )
        staging_root = _validated_microdata_staging_dir(
            staging_dir,
            output_dir=output,
        )
    else:
        staging_root = None

    resolved_r2_prefix = resolve_r2_prefix(
        prefix=r2_prefix,
        default_prefix=DEFAULT_R2_PREFIX,
        source_id=source_id,
        package_path=output,
    )
    recorded_identity = _recorded_identity(
        _manifest_file_spec(existing_manifest, year, artifact_filename),
        manifest_path=manifest_path,
        year=year,
    )

    fetched_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    content, _inferred_filename = _read_artifact(source_url)

    sha256 = hashlib.sha256(content).hexdigest()
    size_bytes = len(content)
    if expected_sha256 is not None and sha256 != expected_sha256:
        raise ArtifactIntegrityError(
            f"Fetched artifact checksum mismatch for {artifact_filename}: "
            f"expected {expected_sha256}, got {sha256}."
        )
    if expected_size_bytes is not None and size_bytes != expected_size_bytes:
        raise ArtifactIntegrityError(
            f"Fetched artifact size mismatch for {artifact_filename}: expected "
            f"{expected_size_bytes}, got {size_bytes}."
        )

    _assert_package_filename_accepts_identity(
        package_manifests,
        selected_manifest_path=manifest_path,
        year=year,
        filename=artifact_filename,
        sha256=sha256,
    )

    # Guard before the cached artifact is touched. A rejected fetch must leave
    # the recorded bytes and their manifest entry exactly as they were.
    _assert_recorded_identity_holds_these_bytes(
        recorded_identity,
        manifest_path=manifest_path,
        year=year,
        filename=artifact_filename,
        sha256=sha256,
        size_bytes=size_bytes,
        r2_bucket=r2_bucket,
        record_revision=record_revision,
    )

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
    if staging_root is not None:
        staging_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="chronicle-microdata-",
            dir=staging_root,
        ) as temporary:
            local_path = Path(temporary) / artifact_filename
            local_path.write_bytes(content)
            r2_upload = _upload_r2_object(
                r2_location,
                local_path,
                wrangler_command=wrangler_command,
            )
        if not r2_upload.ok:
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
                r2_location=None,
                r2_upload=r2_upload,
                errors=("r2_upload_failed",),
            )
    else:
        output.mkdir(parents=True, exist_ok=True)
        local_path = output / artifact_filename
        local_path.write_bytes(content)
        if upload_r2:
            r2_upload = _upload_r2_object(
                r2_location,
                local_path,
                wrangler_command=wrangler_command,
            )
            if not r2_upload.ok:
                errors.append("r2_upload_failed")

    output.mkdir(parents=True, exist_ok=True)
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
        access=access_class,
        licence=licence_text,
        licence_evidence=evidence,
        kind=manifest_kind_value,
        publisher=publisher,
        vintage=vintage,
        r2_location=(r2_location if upload_r2 and r2_upload and r2_upload.ok else None),
        record_revision=record_revision,
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


def _validated_microdata_staging_dir(
    staging_dir: str | Path,
    *,
    output_dir: Path,
) -> Path:
    """Return a staging root that cannot put microdata beside its manifest."""
    staging = Path(staging_dir).resolve()
    output = output_dir.resolve()
    if staging == output or staging in output.parents or output in staging.parents:
        raise ManifestAccessError(
            "--staging-dir must be separate from the microdata manifest directory."
        )
    parts = staging.parts
    if any(parts[index : index + 2] == ("db", "data") for index in range(len(parts))):
        raise ManifestAccessError(
            "--staging-dir for public microdata must be outside db/data."
        )
    return staging


def publish_derived_artifacts(
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
    """Upload a deterministic build output directory to the derived R2 bucket."""
    r2_bucket = r2_bucket or default_r2_derived_bucket()
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
    manifest_filename: str = DEFAULT_MANIFEST_FILENAME,
    source_id: str | None = None,
    package_id: str | None = None,
    r2_bucket: str | None = None,
    r2_prefix: str | None = None,
    wrangler_command: str = "npx wrangler",
    skip_hash_only: bool = False,
) -> RawArtifactPublishReport:
    """Upload manifest-declared raw source artifacts and record R2 locations.

    Only ``public`` artifacts are uploaded. A licensed or restricted entry is
    refused: no bytes are read or sent, and the entry carries a
    ``hash_only_access_refuses_bytes`` error unless ``skip_hash_only`` marks the
    scan as deliberately mixed.
    """
    r2_bucket = r2_bucket or default_r2_raw_bucket()
    root_path = Path(root)
    if not root_path.exists():
        return RawArtifactPublishReport(
            root=str(root_path),
            entries=(),
            errors=(f"Root does not exist: {root_path}",),
        )

    entries: list[RawArtifactPublishEntry] = []
    errors: list[str] = []
    for manifest_path in _manifest_paths(root_path, manifest_filename):
        try:
            manifest = _read_manifest(manifest_path)
        except (OSError, MalformedManifestError) as exc:
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
        filename_errors = manifest_filename_errors(manifest)
        if filename_errors:
            errors.append(
                f"Unsafe artifact filenames in {manifest_path}: "
                f"{', '.join(filename_errors)}"
            )
            continue
        try:
            for year in files:
                resolve_year_key(files, year)
        except ManifestAccessError as exc:
            errors.append(f"Invalid year keys in {manifest_path}: {exc}")
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

        try:
            kind = strict_manifest_kind(
                manifest,
                manifest_locator=manifest_path,
                manifest_bytes=manifest_path.read_bytes(),
            )
        except ManifestAccessError as exc:
            errors.append(f"Invalid manifest kind in {manifest_path}: {exc}")
            continue
        try:
            _assert_package_manifest_boundaries(
                manifest_path=manifest_path,
                selected_manifest=manifest,
                selected_kind=kind,
            )
        except SourceArtifactManifestError as exc:
            errors.append(f"Unsafe package boundary at {manifest_path}: {exc}")
            continue
        updated = False
        for year, spec in files.items():
            for file_spec in iter_file_specs(spec, kind=kind):
                entry, updated_spec = _publish_raw_manifest_entry(
                    manifest_path,
                    manifest_source_id,
                    manifest_package_id,
                    year,
                    file_spec,
                    manifest=manifest,
                    kind=kind,
                    r2_bucket=r2_bucket,
                    r2_prefix=resolved_r2_prefix,
                    wrangler_command=wrangler_command,
                    skip_hash_only=skip_hash_only,
                )
                entries.append(entry)
                if updated_spec is not None and isinstance(file_spec, dict):
                    file_spec.update(updated_spec)
                    updated = True
        if updated:
            manifest.setdefault("source_id", manifest_source_id)
            manifest.setdefault("package_id", manifest_package_id)
            manifest["kind"] = kind
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
    manifest_filename: str = DEFAULT_MANIFEST_FILENAME,
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
                "hash_only_count": 0,
                "r2_link_count": 0,
            },
            entries=(),
            errors=(f"Root does not exist: {root_path}",),
        )

    manifests = _manifest_paths(root_path, manifest_filename)
    for manifest_path in manifests:
        try:
            manifest = _read_manifest(manifest_path)
            files = manifest.get("files") or {}
        except (OSError, MalformedManifestError) as exc:
            errors.append(f"Could not read {manifest_path}: {exc}")
            continue
        if not isinstance(files, dict):
            errors.append(f"Manifest files must be a mapping: {manifest_path}")
            continue
        filename_errors = manifest_filename_errors(manifest)
        if filename_errors:
            errors.append(
                f"Unsafe artifact filenames in {manifest_path}: "
                f"{', '.join(filename_errors)}"
            )
            continue
        try:
            for year in files:
                resolve_year_key(files, year)
            kind = strict_manifest_kind(
                manifest,
                manifest_locator=manifest_path,
                manifest_bytes=manifest_path.read_bytes(),
            )
        except ManifestAccessError as exc:
            errors.append(f"Invalid manifest {manifest_path}: {exc}")
            continue
        try:
            _assert_package_manifest_boundaries(
                manifest_path=manifest_path,
                selected_manifest=manifest,
                selected_kind=kind,
            )
        except SourceArtifactManifestError as exc:
            errors.append(f"Unsafe package boundary at {manifest_path}: {exc}")
            continue
        for year, spec in files.items():
            for file_spec in iter_file_specs(spec, kind=kind):
                entries.append(
                    _inventory_entry(
                        manifest_path,
                        year,
                        file_spec,
                        manifest=manifest,
                        kind=kind,
                    )
                )

    counts = {
        "manifest_count": len(manifests),
        "artifact_count": len(entries),
        "missing_count": sum(1 for entry in entries if "missing_file" in entry.errors),
        "checksum_mismatch_count": sum(
            1 for entry in entries if "checksum_mismatch" in entry.errors
        ),
        "hash_only_count": sum(1 for entry in entries if entry.hash_only),
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
    raw_bucket: str | None = None,
    derived_bucket: str | None = None,
    wrangler_command: str = "npx wrangler",
) -> R2BootstrapReport:
    """Create the R2 buckets Chronicle expects, if Wrangler is authenticated."""
    buckets = (
        raw_bucket or default_r2_raw_bucket(),
        derived_bucket or default_r2_derived_bucket(),
    )
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
        canonical_artifact_filename(filename),
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

    db_path = input_path / CHRONICLE_DB_FILENAME
    if not db_path.exists():
        db_path = input_path / LEGACY_CHRONICLE_DB_FILENAME
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


def _read_manifest(manifest_path: Path) -> dict[str, Any]:
    """Return a manifest's parsed payload, refusing a document it cannot read.

    An absent or empty manifest reads as an empty mapping: ``fetch-artifact``
    writes the first entry into a package that has none. A document that parses
    as anything else -- a list, a scalar, a truncated or half-merged file -- is
    not an absent manifest, and treating it as one would let the fetch replace
    it with a single entry and drop everything it recorded.
    """
    if manifest_path.is_symlink():
        raise MalformedManifestError(
            f"{manifest_path} is a symlink; manifest reads and writes require "
            "a regular file at its lexical package path."
        )
    if not manifest_path.exists():
        return {}
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise MalformedManifestError(
            f"{manifest_path} is not valid YAML: {exc}"
        ) from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise MalformedManifestError(
            f"{manifest_path} must be a YAML mapping; it parses as a "
            f"{type(payload).__name__}. Chronicle will not overwrite a manifest "
            "it cannot read."
        )
    return payload


def _manifest_file_spec(
    payload: dict[str, Any],
    year: Any,
    filename: str | None = None,
) -> dict[str, Any]:
    """Return one manifest entry for a semantic year and optional basename."""
    files = payload.get("files")
    if not isinstance(files, dict):
        return {}
    year_key = resolve_year_key(files, year)
    spec = files.get(year_key)
    if isinstance(spec, dict):
        if filename is None or spec.get("filename") == filename:
            return spec
        return {}
    if isinstance(spec, list) and filename is not None:
        for entry in spec:
            if isinstance(entry, dict) and entry.get("filename") == filename:
                return entry
    return {}


def _manifest_kind_for_fetch(
    manifest: dict[str, Any],
    *,
    manifest_path: Path,
    requested_kind: str | None,
) -> str:
    """Resolve fetch kind and refuse a reclassification before artifact I/O."""
    try:
        if manifest:
            existing_kind = strict_manifest_kind(
                manifest,
                manifest_locator=manifest_path,
                manifest_bytes=manifest_path.read_bytes(),
            )
        else:
            existing_kind = None
        if requested_kind is None:
            if existing_kind is None:
                raise ManifestAccessError(
                    "A new or empty manifest requires --kind "
                    "('publisher_table' or 'microdata_release')."
                )
            requested = existing_kind
        else:
            requested = normalize_manifest_kind({"kind": requested_kind})
    except ManifestAccessError as exc:
        raise SourceArtifactManifestError(f"{manifest_path}: {exc}") from exc
    if existing_kind is not None and requested != existing_kind:
        raise SourceArtifactManifestError(
            f"{manifest_path} is kind={existing_kind!r}; --kind "
            f"{requested!r} would reclassify the existing manifest."
        )
    return requested


def _validate_existing_manifest_before_fetch(
    manifest: dict[str, Any],
    *,
    manifest_path: Path,
    kind: str,
) -> None:
    """Strictly validate an existing manifest before publisher bytes are read."""
    filename_errors = manifest_filename_errors(manifest)
    if filename_errors:
        raise SourceArtifactManifestError(
            f"{manifest_path} has unsafe artifact filenames: "
            f"{', '.join(filename_errors)}."
        )
    files = manifest.get("files")
    if files is None:
        return
    if not isinstance(files, dict):
        raise MalformedManifestError(
            f"{manifest_path} files must be a mapping; it is a {type(files).__name__}."
        )
    try:
        for year in files:
            resolve_year_key(files, year)
    except ManifestAccessError as exc:
        raise SourceArtifactManifestError(f"{manifest_path}: {exc}") from exc

    for year, year_spec in files.items():
        for spec in iter_file_specs(year_spec, kind=kind):
            if isinstance(spec, ListSpecRejected):
                raise SourceArtifactManifestError(
                    f"{manifest_path} entry {year!r} is a list but "
                    "kind='publisher_table' requires one mapping."
                )
            if not isinstance(spec, dict):
                raise MalformedManifestError(
                    f"{manifest_path} entry {year!r} must be a mapping; it is a "
                    f"{type(spec).__name__}."
                )
            if kind == PUBLISHER_TABLE_KIND:
                checksum = spec.get("sha256")
                if not isinstance(checksum, str) or not _SHA256_KEY_SEGMENT.fullmatch(
                    checksum
                ):
                    raise SourceArtifactManifestError(
                        f"{manifest_path} entry {year!r} must record a lowercase "
                        "64-character sha256 before fetch-artifact may replace "
                        "its path."
                    )
            filename = spec.get("filename")
            local_exists = (
                bool(filename)
                and (
                    manifest_path.parent / canonical_artifact_filename(filename)
                ).exists()
            )
            entry_errors = validate_file_entry(
                spec,
                kind=kind,
                manifest=manifest,
                local_file_exists=local_exists,
            )
            if entry_errors:
                raise SourceArtifactManifestError(
                    f"{manifest_path} entry {year!r} filename={filename!r} is invalid: "
                    f"{', '.join(entry_errors)}."
                )
            _validated_recorded_r2(
                spec,
                manifest_path=manifest_path,
                year=year,
            )


def _validate_manifest_identity_before_fetch(
    manifest: dict[str, Any],
    *,
    manifest_path: Path,
    source_id: str,
    package_id: str,
) -> None:
    """Refuse a source/package mismatch before download, write, or upload."""
    for field, value in (("source_id", source_id), ("package_id", package_id)):
        recorded = manifest.get(field)
        if recorded is not None and recorded != value:
            raise SourceArtifactManifestError(
                f"{manifest_path} declares {field}={recorded!r}; refusing to "
                f"write {field}={value!r}."
            )


def _validate_fetch_slot(
    manifest: dict[str, Any],
    *,
    manifest_path: Path,
    kind: str,
    year: Any,
    filename: str,
) -> None:
    """Refuse a destructive publisher-table replacement before artifact I/O."""
    files = manifest.get("files")
    if not isinstance(files, dict):
        return
    year_key = resolve_year_key(files, year)
    existing = files.get(year_key)
    if kind != PUBLISHER_TABLE_KIND or existing is None:
        return
    if not isinstance(existing, dict):
        raise SourceArtifactManifestError(
            f"{manifest_path} entry {year_key!r} must remain a mapping."
        )
    existing_filename = canonical_artifact_filename(existing.get("filename"))
    if existing_filename != filename:
        raise SourceArtifactRevisionError(
            f"{manifest_path} entry {year_key!r} already identifies "
            f"{existing_filename!r}; refusing to replace it with {filename!r}. "
            "Use a separate vintage or package."
        )


def _recorded_storage(spec: Any) -> dict[str, Any]:
    """Return a manifest file spec's recorded ``storage`` block, if any."""
    if not isinstance(spec, dict):
        return {}
    storage = spec.get("storage")
    return storage if isinstance(storage, dict) else {}


def _recorded_r2(spec: Any) -> dict[str, Any]:
    """Return the recorded ``storage.r2`` block verbatim, if any.

    Raw access, for callers that carry the block forward as history. Callers
    that reason about which object it names go through
    :func:`_validated_recorded_r2` instead.
    """
    recorded = _recorded_storage(spec).get("r2")
    return recorded if isinstance(recorded, dict) else {}


def _split_r2_uri(uri: str) -> tuple[str, str, str] | None:
    """Split ``provider://bucket/key`` into its three parts, or None."""
    provider, separator, remainder = uri.partition("://")
    if not separator or not provider:
        return None
    bucket, separator, key = remainder.partition("/")
    if not separator or not bucket or not key:
        return None
    return (provider, bucket, key)


def _validated_recorded_storage(
    spec: Any,
    *,
    manifest_path: Path,
    year: Any,
) -> dict[str, Any]:
    """Return the entry's ``storage`` mapping, refusing a malformed one."""
    if not isinstance(spec, dict) or "storage" not in spec:
        return {}
    storage = spec["storage"]
    if not isinstance(storage, dict):
        raise MalformedManifestError(
            f"{manifest_path} entry {year!r} storage must be a mapping; it is a "
            f"{type(storage).__name__}."
        )
    return storage


def _validated_recorded_r2(
    spec: Any,
    *,
    manifest_path: Path,
    year: Any,
) -> RecordedR2Object | None:
    """Return the object a recorded ``storage.r2`` block names, or None.

    Every locator field the block supplies is cross-checked against every
    other: ``key`` against the URI's path, ``bucket`` against its authority,
    ``provider`` against its scheme, and the resulting key against the
    canonical content-addressed shape :func:`build_r2_key` writes. Reading one
    field and trusting the rest is what lets a block that says two different
    things survive a preserve or a publish.
    """
    storage = _validated_recorded_storage(spec, manifest_path=manifest_path, year=year)
    if "r2" not in storage:
        return None
    block = storage["r2"]
    where = f"{manifest_path} entry {year!r} storage.r2"
    if not isinstance(block, dict):
        raise MalformedManifestError(
            f"{where} must be a mapping; it is a {type(block).__name__}."
        )

    supplied: dict[str, str] = {}
    for field in ("provider", "bucket", "key", "uri"):
        value = block.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise RecordedR2LocatorError(
                f"{where}: {field} must be a non-empty string, not {value!r}."
            )
        supplied[field] = value

    provider = supplied.get("provider")
    bucket = supplied.get("bucket")
    key = supplied.get("key")
    uri = supplied.get("uri")
    if uri is not None:
        parts = _split_r2_uri(uri)
        if parts is None:
            raise RecordedR2LocatorError(
                f"{where}: uri {uri!r} is not provider://bucket/key."
            )
        for field, value, from_uri in zip(
            ("provider", "bucket", "key"), (provider, bucket, key), parts
        ):
            if value is not None and value != from_uri:
                raise RecordedR2LocatorError(
                    f"{where}: {field}={value!r} contradicts uri {uri!r}, which "
                    f"names {from_uri!r}. The block records two different "
                    "objects, so Chronicle cannot say which bytes it claims."
                )
        provider, bucket, key = (
            provider or parts[0],
            bucket or parts[1],
            key or parts[2],
        )

    missing = [
        field
        for field, value in (
            ("provider", provider),
            ("bucket", bucket),
            ("key", key),
        )
        if not value
    ]
    if missing:
        raise RecordedR2LocatorError(
            f"{where}: records no {', '.join(missing)}. A recorded block has to "
            "locate its object: provider, bucket and key, or a uri that "
            "supplies them."
        )
    if provider != "r2":
        raise RecordedR2LocatorError(
            f"{where}: provider must be 'r2', not {provider!r}; storage.r2 "
            "cannot name another storage service."
        )

    segments = key.split("/")
    if (
        len(segments) < 2
        or not all(segments)
        or not _SHA256_KEY_SEGMENT.fullmatch(segments[-2])
    ):
        raise RecordedR2LocatorError(
            f"{where}: key {key!r} is not content-addressed. A raw key ends in "
            "{sha256}/{filename}, which is what says the object holds the "
            "entry's bytes; Chronicle will not guess for a key that does not."
        )
    return RecordedR2Object(
        provider=provider,
        bucket=bucket,
        key=key,
        sha256=segments[-2],
        filename=segments[-1],
    )


def _recorded_identity(
    spec: Any,
    *,
    manifest_path: Path,
    year: Any,
) -> RecordedIdentity | None:
    """Return what a manifest entry says its vintage holds, if anything.

    A published entry is identified by its recorded object's content-addressed
    key. An entry that has not been published yet -- registered without an
    upload, or left behind by a failed one -- is identified by its own declared
    ``sha256`` and ``filename``. Both are recorded identities, and a fetch of
    different bytes over either one is a publisher revision.
    """
    recorded_r2 = _validated_recorded_r2(spec, manifest_path=manifest_path, year=year)
    declared_sha256 = spec.get("sha256") if isinstance(spec, dict) else None
    declared_sha256 = declared_sha256 if isinstance(declared_sha256, str) else None
    declared_filename = spec.get("filename") if isinstance(spec, dict) else None
    declared_filename = (
        declared_filename if isinstance(declared_filename, str) else None
    )
    size_bytes = spec.get("size_bytes") if isinstance(spec, dict) else None
    size_bytes = size_bytes if isinstance(size_bytes, int) else None
    if recorded_r2 is not None:
        return RecordedIdentity(
            sha256=recorded_r2.sha256,
            filename=recorded_r2.filename,
            # Only report a size the recorded key agrees with: an entry can
            # arrive here already describing the new bytes.
            size_bytes=size_bytes if declared_sha256 == recorded_r2.sha256 else None,
            declared_sha256=declared_sha256,
            r2=recorded_r2,
        )
    if not declared_sha256:
        return None
    return RecordedIdentity(
        sha256=declared_sha256,
        filename=Path(declared_filename).name if declared_filename else "",
        size_bytes=size_bytes,
        declared_sha256=declared_sha256,
        r2=None,
    )


def _revision_error_message(
    *,
    manifest_path: Path,
    year: Any,
    filename: str,
    identity: RecordedIdentity,
    sha256: str,
    size_bytes: int,
    r2_bucket: str,
) -> str:
    """Explain a refused fetch: recorded identity, fetched identity, next step."""
    records = (
        f"already records the R2 object {identity.r2.uri}, which holds"
        if identity.r2 is not None
        else "already records"
    )
    message = (
        f"{manifest_path} entry {year!r} {records} "
        f"sha256={identity.sha256} "
        f"filename={identity.filename or 'unknown'} "
        f"size_bytes="
        f"{identity.size_bytes if identity.size_bytes is not None else 'unknown'}. "
        f"The fetched bytes are sha256={sha256} filename={Path(filename).name} "
        f"size_bytes={size_bytes}. Chronicle will not rewrite a vintage that "
        "identifies specific bytes to describe bytes it never identified."
    )
    if identity.r2 is not None and identity.declared_sha256 not in (
        None,
        identity.sha256,
    ):
        message += (
            f" (The entry also declares sha256={identity.declared_sha256}, which "
            "its own R2 key contradicts: an earlier fetch rewrote the hash "
            "without moving the object.)"
        )
    return message + (
        " The same vintage with new bytes is a new release revision "
        "(docs/adr-chronicle-fact-identity-v2.md). Re-run with "
        "--record-revision to store the fetched bytes under their own "
        f"content-addressed key in {r2_bucket} and keep the superseded object "
        "in storage.previous_r2."
    )


def _assert_recorded_identity_holds_these_bytes(
    identity: RecordedIdentity | None,
    *,
    manifest_path: Path,
    year: Any,
    filename: str,
    sha256: str,
    size_bytes: int,
    r2_bucket: str,
    record_revision: bool,
) -> None:
    """Refuse a publisher revision that has not been opted into."""
    if record_revision or identity is None:
        return
    if identity.holds(sha256=sha256, filename=filename):
        return
    raise SourceArtifactRevisionError(
        _revision_error_message(
            manifest_path=manifest_path,
            year=year,
            filename=filename,
            identity=identity,
            sha256=sha256,
            size_bytes=size_bytes,
            r2_bucket=r2_bucket,
        )
    )


def _superseding_storage(
    recorded_spec: dict[str, Any],
    *,
    recorded_r2: RecordedR2Object | None,
    new_r2: dict[str, Any] | None,
    superseded_at: str,
) -> dict[str, Any]:
    """Return a storage block in which the recorded object becomes history.

    ``storage.r2`` only ever names the object that holds the entry's current
    bytes. The superseded block is appended, oldest first, to
    ``storage.previous_r2`` so the earlier bytes stay addressable by the URI
    archived witness records already pin. An entry that was never published has
    no object to supersede, and gets no ``previous_r2`` key.
    """
    storage = dict(_recorded_storage(recorded_spec))
    previous = storage.get("previous_r2")
    entries = list(previous) if isinstance(previous, list) else []
    if recorded_r2 is not None:
        entry = dict(_recorded_r2(recorded_spec))
        entry["sha256"] = recorded_r2.sha256
        if recorded_spec.get("sha256") == recorded_r2.sha256:
            # Only carry metadata the superseded key agrees with: a manifest
            # can arrive here already describing the new bytes.
            for field in ("size_bytes", "fetched_at", "source_url"):
                value = recorded_spec.get(field)
                if value is not None:
                    entry[field] = value
            for field in (
                "licence",
                "licence_evidence",
                "hash_source",
                "attested_by",
                "verified_at",
                "attestation_evidence",
                "pinned_from",
            ):
                value = recorded_spec.get(field)
                if value is not None:
                    entry[field] = value
        entry["superseded_at"] = superseded_at
        entries.append(entry)
    if entries:
        storage["previous_r2"] = entries
    if new_r2 is None:
        storage.pop("r2", None)
    else:
        storage["r2"] = new_r2
    return storage


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
    access: str,
    licence: str | None,
    licence_evidence: Mapping[str, str] | None,
    kind: str,
    publisher: str | None,
    vintage: str | None,
    r2_location: ArtifactStorageLocation | None,
    record_revision: bool = False,
) -> None:
    payload = _read_manifest(manifest_path)
    resolved_kind = _manifest_kind_for_fetch(
        payload,
        manifest_path=manifest_path,
        requested_kind=kind,
    )
    _validate_existing_manifest_before_fetch(
        payload,
        manifest_path=manifest_path,
        kind=resolved_kind,
    )
    for field, value in (("source_id", source_id), ("package_id", package_id)):
        recorded = payload.get(field)
        if recorded is not None and recorded != value:
            raise SourceArtifactManifestError(
                f"{manifest_path} declares {field}={recorded!r}; refusing to "
                f"write {field}={value!r}."
            )
    payload.setdefault("source_id", source_id)
    payload.setdefault("package_id", package_id)
    payload.setdefault("dataset", dataset)
    payload.setdefault("source_page", source_page)
    payload.setdefault("table", table)
    if publisher:
        payload.setdefault("publisher", publisher)
    payload["kind"] = resolved_kind
    files = payload.setdefault("files", {})
    if not isinstance(files, dict):
        raise MalformedManifestError(
            f"{manifest_path} files must be a mapping; it is a {type(files).__name__}."
        )
    try:
        year_key = resolve_year_key(files, year)
    except ManifestAccessError as exc:
        raise SourceArtifactManifestError(f"{manifest_path}: {exc}") from exc
    existing = files.get(year_key)
    entries = list(existing) if isinstance(existing, list) else _as_entry_list(existing)
    exact_index = next(
        (
            index
            for index, entry in enumerate(entries)
            if isinstance(entry, dict)
            and entry.get("filename") == filename
            and entry.get("sha256") == sha256
        ),
        None,
    )
    filename_index = next(
        (
            index
            for index, entry in enumerate(entries)
            if isinstance(entry, dict) and entry.get("filename") == filename
        ),
        None,
    )
    target_index = exact_index if exact_index is not None else filename_index
    recorded_spec = (
        entries[target_index]
        if target_index is not None and isinstance(entries[target_index], dict)
        else {}
    )
    if resolved_kind == PUBLISHER_TABLE_KIND and existing is not None:
        if isinstance(existing, list):
            raise SourceArtifactManifestError(
                f"{manifest_path} entry {year_key!r} is a list; a "
                "publisher_table year must remain a mapping."
            )
        existing_filename = canonical_artifact_filename(existing.get("filename"))
        if existing_filename != filename:
            raise SourceArtifactRevisionError(
                f"{manifest_path} entry {year_key!r} already identifies "
                f"{existing_filename!r}; refusing to replace it with "
                f"{filename!r}. Use a separate vintage or package."
            )
    # Access is written explicitly on every entry this command touches, so a
    # manifest never relies on the inferred ``public`` default once rewritten.
    file_entry: dict[str, Any] = dict(recorded_spec)
    if resolved_kind == MICRODATA_RELEASE_KIND:
        # A Chronicle fetch replaces a consumer attestation; carrying either
        # mutually exclusive evidence field forward would create an invalid
        # hybrid registration.
        file_entry.pop("attestation_evidence", None)
        file_entry.pop("pinned_from", None)
    file_entry.update(
        {
            "filename": filename,
            "source_url": source_url,
            "access": access,
        }
    )
    if licence:
        file_entry["licence"] = licence
    if licence_evidence:
        file_entry["licence_evidence"] = dict(licence_evidence)
    if vintage:
        file_entry["vintage"] = vintage
    file_entry.update(
        {
            "sha256": sha256,
            "size_bytes": size_bytes,
            "fetched_at": fetched_at,
        }
    )
    if resolved_kind == MICRODATA_RELEASE_KIND:
        file_entry.update(
            {
                "hash_source": HASH_SOURCE_CHRONICLE_FETCH,
                "attested_by": "chronicle",
                "verified_at": fetched_at,
            }
        )
    recorded_storage = _recorded_storage(recorded_spec)
    identity = _recorded_identity(recorded_spec, manifest_path=manifest_path, year=year)
    new_r2 = r2_location.to_dict() if r2_location is not None else None
    holds = identity is not None and identity.holds(sha256=sha256, filename=filename)
    if identity is not None and not holds and not record_revision:
        # Different bytes under the same vintage. The guard in
        # fetch_source_artifact refuses this without --record-revision; repeat
        # the check here so no caller can reach a false-provenance write.
        raise SourceArtifactRevisionError(
            _revision_error_message(
                manifest_path=manifest_path,
                year=year,
                filename=filename,
                identity=identity,
                sha256=sha256,
                size_bytes=size_bytes,
                r2_bucket=(new_r2 or {}).get("bucket") or default_r2_raw_bucket(),
            )
        )
    if holds and identity.r2 is not None:
        # A recorded storage.r2 block for these exact bytes is historical
        # truth: archived witness records pin raw R2 URLs by hash. Re-fetching
        # under a renamed bucket copies bytes; it does not restate where the
        # bytes were first published (PolicyEngine/chronicle#143, mechanism 3).
        storage = {**recorded_storage, "r2": _recorded_r2(recorded_spec)}
    elif identity is not None and not holds:
        storage = _superseding_storage(
            recorded_spec,
            recorded_r2=identity.r2,
            new_r2=new_r2,
            superseded_at=fetched_at,
        )
    elif new_r2 is not None:
        storage = {**recorded_storage, "r2": new_r2}
    else:
        storage = dict(recorded_storage)
    # An entry that has no storage to record carries no empty block: a
    # revision over a never-published entry supersedes nothing.
    if storage:
        file_entry["storage"] = storage
    if resolved_kind == PUBLISHER_TABLE_KIND:
        files[year_key] = file_entry
    elif isinstance(existing, list):
        if target_index is None:
            entries.append(file_entry)
        else:
            entries[target_index] = file_entry
        files[year_key] = entries
    elif existing is None:
        files[year_key] = [file_entry]
    elif target_index is not None:
        # Preserve a legacy one-entry microdata mapping in place.
        files[year_key] = file_entry
    else:
        files[year_key] = [existing, file_entry]
    manifest_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _as_entry_list(existing: Any) -> list[Any]:
    """Return an existing ``files[year]`` value as a list of entries."""
    if existing is None:
        return []
    return [existing]


def _load_manifest_payload(manifest_path: Path) -> dict[str, Any]:
    """Load a manifest mapping, or an empty mapping when absent or unreadable."""
    if not manifest_path.exists():
        return {}
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Manifest must be a mapping: {manifest_path}")
    return payload


def _iter_manifest_entries(manifest: dict[str, Any]) -> Iterator[tuple[Any, Any]]:
    """Yield every ``(year, entry)`` a manifest declares, whatever its shape.

    Deliberately not gated on the manifest ``kind``: a guard must see the
    entries a manifest actually holds, including a list under a manifest whose
    kind is absent or misspelled. Reporting that shape as malformed is the
    validator's job, not the guard's.
    """
    files = manifest.get("files")
    if not isinstance(files, dict):
        return
    for year, spec in files.items():
        for entry in spec if isinstance(spec, list) else (spec,):
            yield year, entry


def _sibling_manifest_paths(manifest_path: Path) -> list[Path]:
    """Return reserved manifest filenames sharing one package directory."""
    paths = (
        {
            candidate
            for candidate in manifest_path.parent.iterdir()
            if candidate.is_file()
            and candidate.name.startswith("manifest")
            and candidate.suffix in {".yaml", ".yml"}
        }
        if manifest_path.parent.exists()
        else set()
    )
    if manifest_path.exists():
        paths.add(manifest_path)
    return sorted(paths)


def _package_manifests_before_fetch(
    *,
    manifest_path: Path,
    selected_manifest: dict[str, Any],
    selected_kind: str,
    filename: str,
) -> list[tuple[Path, dict[str, Any], str]]:
    """Strictly inspect every sibling manifest that can reserve ``filename``."""
    manifests = _package_manifests(
        manifest_path=manifest_path,
        selected_manifest=selected_manifest,
        selected_kind=selected_kind,
    )
    for candidate_path, candidate, candidate_kind in manifests:
        _assert_no_hash_only_entry(
            candidate,
            candidate_path,
            filename,
            kind=candidate_kind,
        )
    return manifests


def _package_manifests(
    *,
    manifest_path: Path,
    selected_manifest: dict[str, Any],
    selected_kind: str,
) -> list[tuple[Path, dict[str, Any], str]]:
    """Load and strictly validate every manifest sharing a package path."""
    manifests = [(manifest_path, selected_manifest, selected_kind)]
    for sibling_path in _sibling_manifest_paths(manifest_path):
        if sibling_path == manifest_path:
            continue
        sibling = _read_manifest(sibling_path)
        try:
            sibling_kind = strict_manifest_kind(
                sibling,
                manifest_locator=sibling_path,
                manifest_bytes=sibling_path.read_bytes(),
            )
        except ManifestAccessError as exc:
            raise SourceArtifactManifestError(f"{sibling_path}: {exc}") from exc
        _validate_existing_manifest_before_fetch(
            sibling,
            manifest_path=sibling_path,
            kind=sibling_kind,
        )
        manifests.append((sibling_path, sibling, sibling_kind))
    return manifests


def _assert_package_manifest_boundaries(
    *,
    manifest_path: Path,
    selected_manifest: dict[str, Any],
    selected_kind: str,
) -> None:
    """Refuse sibling collisions and payloads before inventory or publish I/O."""
    manifests = _package_manifests(
        manifest_path=manifest_path,
        selected_manifest=selected_manifest,
        selected_kind=selected_kind,
    )
    if any(kind == MICRODATA_RELEASE_KIND for _, _, kind in manifests):
        _assert_microdata_package_is_manifest_only(
            manifest_path,
            allowed_manifests={path for path, _, _ in manifests},
        )

    seen: dict[str, tuple[Path, Any, dict[str, Any], str]] = {}
    for candidate_path, manifest, kind in manifests:
        for entry_year, spec in _iter_manifest_entries(manifest):
            if not isinstance(spec, dict):
                continue
            try:
                filename = canonical_artifact_filename(spec.get("filename"))
            except ManifestAccessError:
                continue
            previous = seen.get(filename)
            if previous is None:
                seen[filename] = (candidate_path, entry_year, spec, kind)
                continue
            previous_path, previous_year, previous_spec, previous_kind = previous
            if previous_path == candidate_path:
                continue
            previous_access = effective_entry_access(
                previous_spec,
                kind=previous_kind,
            )
            access = effective_entry_access(spec, kind=kind)
            shared_public_table = (
                previous_kind == PUBLISHER_TABLE_KIND
                and kind == PUBLISHER_TABLE_KIND
                and previous_access == ACCESS_PUBLIC
                and access == ACCESS_PUBLIC
                and isinstance(previous_spec.get("sha256"), str)
                and previous_spec.get("sha256") == spec.get("sha256")
            )
            if shared_public_table:
                continue
            raise SourceArtifactManifestError(
                f"Sibling manifests {previous_path} entry {previous_year!r} and "
                f"{candidate_path} entry {entry_year!r} both reserve basename "
                f"{filename!r} with incompatible identities."
            )


def _assert_microdata_package_is_manifest_only(
    manifest_path: Path,
    *,
    allowed_manifests: set[Path],
) -> None:
    """Refuse every payload file beside a package containing microdata."""
    if not manifest_path.parent.exists():
        return
    allowed = {candidate.resolve() for candidate in allowed_manifests}
    payloads = sorted(
        candidate
        for candidate in manifest_path.parent.rglob("*")
        if candidate.is_file() and candidate.resolve() not in allowed
    )
    if payloads:
        relative = ", ".join(
            str(candidate.relative_to(manifest_path.parent)) for candidate in payloads
        )
        raise SourceArtifactManifestError(
            f"{manifest_path.parent} contains microdata payload files outside "
            f"its manifests: {relative}. Git custody is manifest-only."
        )


def _assert_microdata_source_is_outside_db_data(source_url: str) -> None:
    """Refuse a local microdata source already held anywhere under db/data."""
    parsed = urlparse(source_url)
    if parsed.scheme not in {"", "file"}:
        return
    source_path = Path(unquote(parsed.path) if parsed.scheme == "file" else source_url)
    parts = source_path.resolve().parts
    if any(parts[index : index + 2] == ("db", "data") for index in range(len(parts))):
        raise SourceArtifactManifestError(
            f"Public microdata source {source_path} is inside db/data. Chronicle "
            "may read it only from transient staging outside the data tree."
        )


def _assert_package_filename_accepts_identity(
    manifests: list[tuple[Path, dict[str, Any], str]],
    *,
    selected_manifest_path: Path,
    year: Any,
    filename: str,
    sha256: str,
) -> None:
    """Refuse a new basename identity that would collide anywhere on disk."""
    for candidate_path, manifest, kind in manifests:
        files = manifest.get("files")
        selected_year_key = (
            resolve_year_key(files, year) if isinstance(files, dict) else year
        )
        for entry_year, spec in _iter_manifest_entries(manifest):
            if not isinstance(spec, dict):
                continue
            try:
                registered_filename = canonical_artifact_filename(spec.get("filename"))
            except ManifestAccessError:
                continue
            if registered_filename != filename:
                continue
            if (
                candidate_path == selected_manifest_path
                and entry_year == selected_year_key
            ):
                continue
            registered_sha256 = spec.get("sha256")
            access = effective_entry_access(spec, kind=kind)
            if (
                kind == PUBLISHER_TABLE_KIND
                and access == ACCESS_PUBLIC
                and registered_sha256 == sha256
            ):
                # A small number of frozen table manifests intentionally share
                # one exact public file across years or named manifests.
                continue
            raise SourceArtifactRevisionError(
                f"{candidate_path} entry {entry_year!r} already reserves "
                f"basename {filename!r} with sha256={registered_sha256!r} and "
                f"access={access!r}; refusing a second on-disk identity with "
                f"sha256={sha256!r}. Use a separate package path."
            )


def _assert_no_hash_only_entry(
    manifest: dict[str, Any],
    manifest_path: Path,
    filename: str | None,
    *,
    kind: str,
) -> None:
    """Refuse to fetch bytes over an existing hash-only registration.

    The write target is a path in the package directory, so the search spans
    every vintage rather than the requested one: a licensed release registered
    under one year must not be fetched into the tree under another.
    """
    if not filename:
        return
    requested = canonical_artifact_filename(filename)
    for entry_year, spec in _iter_manifest_entries(manifest):
        if not isinstance(spec, dict):
            continue
        try:
            registered = canonical_artifact_filename(spec.get("filename"))
        except ManifestAccessError:
            # Strict manifest preflight reports the malformed spelling.
            continue
        if registered != requested:
            continue
        access = effective_entry_access(spec, kind=kind)
        if is_hash_only(access):
            raise ManifestAccessError(
                f"{manifest_path} registers {requested!r} for {entry_year} as "
                f"access={access!r}. Its bytes must not enter a Chronicle "
                "store; keep the hash-only registration."
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
    manifest: dict[str, Any] | None = None,
    kind: str | None = None,
    r2_bucket: str,
    r2_prefix: str,
    wrangler_command: str,
    skip_hash_only: bool = False,
) -> tuple[RawArtifactPublishEntry, dict[str, Any] | None]:
    errors: list[str] = []
    if isinstance(spec, ListSpecRejected):
        return (
            RawArtifactPublishEntry(
                manifest_path=str(manifest_path),
                source_id=source_id,
                package_id=package_id,
                year=str(year),
                filename="",
                local_path=str(manifest_path.parent),
                sha256=None,
                size_bytes=None,
                r2_location=None,
                upload=None,
                errors=("list_file_spec_requires_microdata_release_kind",),
            ),
            None,
        )
    if not isinstance(spec, dict):
        spec = {}
        errors.append("malformed_file_spec")
    raw_filename = spec.get("filename")
    filename = ""
    if raw_filename:
        try:
            filename = canonical_artifact_filename(raw_filename)
        except ManifestAccessError:
            return (
                RawArtifactPublishEntry(
                    manifest_path=str(manifest_path),
                    source_id=source_id,
                    package_id=package_id,
                    year=str(year),
                    filename=str(raw_filename),
                    local_path=str(manifest_path.parent),
                    sha256=spec.get("sha256"),
                    size_bytes=spec.get("size_bytes"),
                    r2_location=None,
                    upload=None,
                    errors=("filename_not_bare_basename",),
                ),
                None,
            )
    kind_value = kind or safe_manifest_kind(manifest)[0]
    access = effective_entry_access(spec, kind=kind_value)
    if kind_value == MICRODATA_RELEASE_KIND and access == ACCESS_PUBLIC:
        local_exists = (manifest_path.parent / filename).exists() if filename else False
        errors.extend(
            validate_file_entry(
                spec,
                kind=kind_value,
                manifest=manifest,
                local_file_exists=local_exists,
            )
        )
        try:
            recorded_r2_object = _validated_recorded_r2(
                spec,
                manifest_path=manifest_path,
                year=year,
            )
        except SourceArtifactManifestError as error:
            recorded_r2_object = None
            errors.append(f"recorded_r2_locator_invalid:{error}")
        if recorded_r2_object is None and not any(
            error.startswith("recorded_r2_locator_invalid:") for error in errors
        ):
            errors.append("public_microdata_missing_r2_location")
        elif recorded_r2_object is not None and (
            recorded_r2_object.sha256 != spec.get("sha256")
            or recorded_r2_object.filename != filename
        ):
            errors.append(
                "recorded_r2_identity_mismatch:"
                f"recorded_sha256={recorded_r2_object.sha256}:"
                f"recorded_filename={recorded_r2_object.filename}:"
                f"declared_sha256={spec.get('sha256')}:"
                f"declared_filename={filename}"
            )
        if recorded_r2_object is not None:
            expected_key = build_r2_key(
                source_id=source_id,
                package_id=package_id,
                year=year,
                sha256=str(spec.get("sha256") or ""),
                filename=filename,
                prefix=r2_prefix,
                package_path=manifest_path,
            )
            if recorded_r2_object.key != expected_key:
                errors.append(
                    "recorded_r2_key_disagrees_with_country_prefix:"
                    f"recorded={recorded_r2_object.key}:expected={expected_key}"
                )
            if recorded_r2_object.bucket != r2_bucket:
                errors.append(
                    "recorded_r2_bucket_is_preserved_history:"
                    f"recorded={recorded_r2_object.bucket}:requested={r2_bucket}"
                )
        return (
            RawArtifactPublishEntry(
                manifest_path=str(manifest_path),
                source_id=source_id,
                package_id=package_id,
                year=str(year),
                filename=filename,
                local_path=str(manifest_path.parent / filename),
                sha256=spec.get("sha256"),
                size_bytes=spec.get("size_bytes"),
                r2_location=(
                    ArtifactStorageLocation(
                        provider=recorded_r2_object.provider,
                        bucket=recorded_r2_object.bucket,
                        key=recorded_r2_object.key,
                    )
                    if recorded_r2_object is not None and not errors
                    else None
                ),
                upload=None,
                errors=tuple(dict.fromkeys(errors)),
                skipped="public_microdata_uploaded_via_transient_fetch",
            ),
            None,
        )
    if is_hash_only(access):
        # Refuse before touching bytes: no Chronicle store holds a licensed or
        # restricted artifact, so there is nothing here to upload. The entry is
        # still validated, because bytes on disk or a recorded R2 key are
        # contract violations that --skip-hash-only must not hide.
        hash_only_errors = list(
            validate_file_entry(
                spec,
                kind=kind_value,
                manifest=manifest,
                local_file_exists=(manifest_path.parent / filename).exists()
                if filename
                else False,
            )
        )
        if not skip_hash_only:
            hash_only_errors.insert(0, f"hash_only_access_refuses_bytes:{access}")
        return (
            RawArtifactPublishEntry(
                manifest_path=str(manifest_path),
                source_id=source_id,
                package_id=package_id,
                year=str(year),
                filename=filename,
                local_path=str(manifest_path.parent / filename),
                sha256=spec.get("sha256"),
                size_bytes=spec.get("size_bytes"),
                r2_location=None,
                upload=None,
                errors=tuple(dict.fromkeys(hash_only_errors)),
                skipped=f"hash_only_access:{access}",
            ),
            None,
        )
    errors.extend(
        validate_file_entry(
            spec,
            kind=kind_value,
            manifest=manifest,
            local_file_exists=(manifest_path.parent / filename).exists()
            if filename
            else False,
        )
    )
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

    def refuse(reason: str | None = None) -> tuple[RawArtifactPublishEntry, None]:
        """Report the entry unpublished, with nothing uploaded or rewritten."""
        if reason is not None:
            errors.append(reason)
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

    if errors:
        return refuse()

    try:
        recorded_r2_object = _validated_recorded_r2(
            spec, manifest_path=manifest_path, year=year
        )
    except SourceArtifactManifestError as error:
        # A block that does not name one object cannot be treated as history,
        # and publishing under it would ship whichever field was read.
        return refuse(f"recorded_r2_locator_invalid:{error}")
    if recorded_r2_object is not None and (
        recorded_r2_object.sha256,
        recorded_r2_object.filename,
    ) != (
        sha256_actual or "",
        filename,
    ):
        # The recorded object is addressed by different bytes, so it is not
        # this file's history. Uploading anyway would either publish under a
        # key that misdescribes its content or restate a URI that belongs to
        # the superseded bytes. Registering a publisher revision is
        # `fetch-artifact --record-revision`, not a publish-time rewrite.
        return refuse(
            "recorded_r2_identity_mismatch:"
            f"recorded_sha256={recorded_r2_object.sha256}:"
            f"recorded_filename={recorded_r2_object.filename}:"
            f"local_sha256={sha256_actual}:"
            f"local_filename={filename}"
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
    recorded_bucket = (
        recorded_r2_object.bucket if recorded_r2_object is not None else None
    )
    if recorded_bucket and recorded_bucket != location.bucket:
        # The recorded bucket is preserved history. Publishing the same bytes
        # into a renamed bucket is a backfill copy, not a restatement, so the
        # manifest must not be rewritten to point at the new bucket.
        return refuse(
            "recorded_r2_bucket_is_preserved_history:"
            f"recorded={recorded_bucket}:requested={location.bucket}"
        )
    recorded_key = recorded_r2_object.key if recorded_r2_object is not None else None
    if recorded_key and recorded_key != location.key:
        return refuse(
            "recorded_r2_key_disagrees_with_country_prefix:"
            f"recorded={recorded_key}:expected={location.key}"
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
    *,
    manifest: dict[str, Any] | None = None,
    kind: str | None = None,
) -> ArtifactInventoryEntry:
    errors: list[str] = []
    original_spec = spec
    if isinstance(spec, ListSpecRejected):
        return ArtifactInventoryEntry(
            manifest_path=str(manifest_path),
            year=str(year),
            filename="",
            local_path=str(manifest_path.parent),
            exists=False,
            sha256_expected=None,
            sha256_actual=None,
            size_bytes=None,
            source_url=None,
            r2=None,
            errors=("list_file_spec_requires_microdata_release_kind",),
        )
    if not isinstance(spec, dict):
        spec = {}
        errors.append("malformed_file_spec")
    raw_filename = spec.get("filename")
    filename = ""
    if raw_filename:
        try:
            filename = canonical_artifact_filename(raw_filename)
        except ManifestAccessError:
            return ArtifactInventoryEntry(
                manifest_path=str(manifest_path),
                year=str(year),
                filename=str(raw_filename),
                local_path=str(manifest_path.parent),
                exists=False,
                sha256_expected=spec.get("sha256"),
                sha256_actual=None,
                size_bytes=spec.get("size_bytes"),
                source_url=spec.get("source_url"),
                r2=None,
                errors=("filename_not_bare_basename",),
                access=effective_entry_access(
                    spec,
                    kind=kind or safe_manifest_kind(manifest)[0],
                ),
                licence=spec.get("licence"),
                hash_only=True,
            )
    artifact_path = manifest_path.parent / filename
    exists = bool(filename) and artifact_path.exists()
    kind_value = kind or safe_manifest_kind(manifest)[0]
    access = effective_entry_access(spec, kind=kind_value)
    hash_only = is_hash_only(access)
    errors.extend(
        validate_file_entry(
            original_spec,
            kind=kind_value,
            manifest=manifest,
            local_file_exists=exists,
        )
    )
    sha256_expected = spec.get("sha256")
    sha256_actual = None
    size_bytes = spec.get("size_bytes") if hash_only else None
    if not filename:
        errors.append("missing_filename")
    elif hash_only:
        # A licensed or restricted registration is identity only: Chronicle
        # never holds the bytes, so a missing local file is the correct state.
        pass
    elif not exists:
        if kind_value == MICRODATA_RELEASE_KIND:
            size_bytes = spec.get("size_bytes")
        else:
            errors.append("missing_file")
    else:
        content = artifact_path.read_bytes()
        sha256_actual = hashlib.sha256(content).hexdigest()
        size_bytes = len(content)
        if sha256_expected and sha256_actual != sha256_expected:
            errors.append("checksum_mismatch")
    try:
        recorded_r2_object = _validated_recorded_r2(
            spec,
            manifest_path=manifest_path,
            year=year,
        )
    except SourceArtifactManifestError as error:
        recorded_r2_object = None
        errors.append(f"recorded_r2_locator_invalid:{error}")
    r2 = _recorded_r2(spec) if recorded_r2_object is not None else None
    if recorded_r2_object is not None and (
        recorded_r2_object.sha256 != sha256_expected
        or recorded_r2_object.filename != filename
    ):
        errors.append(
            "recorded_r2_identity_mismatch:"
            f"recorded_sha256={recorded_r2_object.sha256}:"
            f"recorded_filename={recorded_r2_object.filename}:"
            f"declared_sha256={sha256_expected}:"
            f"declared_filename={filename}"
        )
    if (
        kind_value == MICRODATA_RELEASE_KIND
        and not hash_only
        and not exists
        and recorded_r2_object is None
    ):
        errors.append("public_microdata_missing_r2_location")
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
        r2=dict(r2) if r2 is not None else None,
        errors=tuple(dict.fromkeys(errors)),
        access=access,
        licence=spec.get("licence"),
        hash_only=hash_only,
    )


def _derived_artifact_kind(artifact_name: str) -> str:
    if artifact_name in CHRONICLE_DB_FILENAMES:
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
