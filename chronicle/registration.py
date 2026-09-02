"""Access classification and hash-only registration for Chronicle artifacts.

Chronicle registers every raw artifact its consumers build from and stores the
bytes of only those the publisher permits it to redistribute. Two manifest file
fields carry that split:

``licence``
    The publisher's terms, as an identifier or a URL.

``access``
    A closed class: ``public``, ``licensed``, or ``restricted``.

``public`` artifacts keep the existing fetch/publish path: bytes are archived in
the raw R2 bucket under the content-addressed key
``raw/{source_id}/{package_id}/{year}/{sha256}/{filename}``. ``licensed`` and
``restricted`` artifacts are registered *hash-only*: the manifest records the
checksum, vintage, licence, and access route, and no Chronicle store ever holds
the bytes. That key exists only for ``public`` artifacts.

A registration is identified by ``{source_id, package_id, year, sha256,
filename}``. Consumers reference a registration by exactly that tuple.

See ``docs/adr-chronicle-raw-microdata-identity.md``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml


ACCESS_PUBLIC = "public"
ACCESS_LICENSED = "licensed"
ACCESS_RESTRICTED = "restricted"
#: The closed set of access classes a manifest file entry may declare.
ACCESS_CLASSES: tuple[str, ...] = (ACCESS_PUBLIC, ACCESS_LICENSED, ACCESS_RESTRICTED)
#: Access class inferred for an entry that does not declare one.
DEFAULT_ACCESS = ACCESS_PUBLIC

PUBLISHER_TABLE_KIND = "publisher_table"
MICRODATA_RELEASE_KIND = "microdata_release"
#: The closed set of manifest kinds. Manifests without ``kind`` are tables.
MANIFEST_KINDS: tuple[str, ...] = (PUBLISHER_TABLE_KIND, MICRODATA_RELEASE_KIND)
DEFAULT_MANIFEST_KIND = PUBLISHER_TABLE_KIND

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Registration entry key order, so emitted manifests are byte-stable.
_REGISTRATION_FIELD_ORDER: tuple[str, ...] = (
    "filename",
    "access",
    "licence",
    "vintage",
    "sha256",
    "size_bytes",
    "source_url",
    "access_route",
    "doi",
    "study",
    "fetched_at",
    "verified_at",
    "hash_source",
    "notes",
)


class ManifestAccessError(ValueError):
    """Raised when a manifest declares an unusable access class or kind."""


class HashOnlyRegistrationError(ValueError):
    """Raised when a hash-only registration is malformed or would store bytes."""


class MicrodataReleaseNotParseableError(ValueError):
    """Raised when a source package points at a registered microdata release.

    Registration is manifest-level: no source package parses a microdata
    release, and no microdata row, cell, or fact enters Chronicle.
    """


@dataclass(frozen=True)
class ListSpecRejected:
    """Marker for a list ``files[year]`` value outside a microdata release."""

    spec: Any


def manifest_kind(manifest: Mapping[str, Any] | None) -> str:
    """Return a manifest's declared kind, defaulting to ``publisher_table``."""
    if not isinstance(manifest, Mapping):
        return DEFAULT_MANIFEST_KIND
    declared = manifest.get("kind")
    if declared is None:
        return DEFAULT_MANIFEST_KIND
    kind = str(declared)
    if kind not in MANIFEST_KINDS:
        raise ManifestAccessError(
            f"Unknown manifest kind {kind!r}; expected one of {list(MANIFEST_KINDS)}."
        )
    return kind


def safe_manifest_kind(manifest: Mapping[str, Any] | None) -> tuple[str, str | None]:
    """Return ``(kind, error_code)`` without raising on an unknown kind."""
    try:
        return manifest_kind(manifest), None
    except ManifestAccessError:
        declared = manifest.get("kind") if isinstance(manifest, Mapping) else None
        return DEFAULT_MANIFEST_KIND, f"unknown_manifest_kind:{declared}"


def is_microdata_release(manifest: Mapping[str, Any] | None) -> bool:
    """Whether a manifest registers a microdata release rather than a table."""
    return manifest_kind(manifest) == MICRODATA_RELEASE_KIND


def normalize_access(access: str | None) -> str:
    """Return a validated access class, defaulting to ``public``."""
    if access is None:
        return DEFAULT_ACCESS
    value = str(access)
    if value not in ACCESS_CLASSES:
        raise ManifestAccessError(
            f"Unknown access class {value!r}; expected one of {list(ACCESS_CLASSES)}."
        )
    return value


def entry_access(spec: Any) -> str:
    """Return the access class a manifest file entry declares or inherits."""
    if not isinstance(spec, Mapping):
        return DEFAULT_ACCESS
    return normalize_access(spec.get("access"))


def safe_entry_access(spec: Any) -> str:
    """Return an entry's access class, falling back to ``public`` if unknown.

    An unparseable class is reported by :func:`validate_file_entry`; treating it
    as ``public`` here would be unsafe, so it is treated as ``restricted`` and
    therefore never uploaded.
    """
    if not isinstance(spec, Mapping):
        return DEFAULT_ACCESS
    try:
        return normalize_access(spec.get("access"))
    except ManifestAccessError:
        return ACCESS_RESTRICTED


def stores_bytes(access: str) -> bool:
    """Whether Chronicle may hold this access class's bytes."""
    return normalize_access(access) == ACCESS_PUBLIC


def is_hash_only(access: str) -> bool:
    """Whether this access class must be registered without bytes."""
    return not stores_bytes(access)


def registration_id(
    *,
    source_id: str,
    package_id: str,
    year: Any,
    sha256: str,
    filename: str,
) -> str:
    """Return the registration identity tuple as a stable string."""
    return f"{source_id}/{package_id}/{year}/{sha256}/{filename}"


def iter_file_specs(spec: Any, *, kind: str) -> tuple[Any, ...]:
    """Expand one ``files[year]`` value into individual file entries.

    A microdata release registers many files under one vintage — the 14 FRS
    2023-24 tabs share ``{source_id, package_id, year}`` and differ only by
    ``filename`` and ``sha256`` — so its ``files[year]`` value may be a list.
    Publisher-table manifests keep the single-mapping shape, and a list there is
    surfaced as a rejected entry rather than silently expanded.
    """
    if isinstance(spec, list):
        if kind != MICRODATA_RELEASE_KIND:
            return (ListSpecRejected(spec),)
        return tuple(spec)
    return (spec,)


def validate_file_entry(
    spec: Any,
    *,
    kind: str,
    manifest: Mapping[str, Any] | None,
    local_file_exists: bool,
) -> tuple[str, ...]:
    """Return stable error codes for one manifest file entry.

    The codes are the refusal vocabulary shared by ``inventory-artifacts``,
    ``publish-raw``, and ``register-artifact``.
    """
    if isinstance(spec, ListSpecRejected):
        return ("list_file_spec_requires_microdata_release_kind",)
    if not isinstance(spec, Mapping):
        return ()

    errors: list[str] = []
    declared_access = spec.get("access")
    if declared_access is None:
        if kind == MICRODATA_RELEASE_KIND:
            errors.append("missing_access")
        access = DEFAULT_ACCESS
    else:
        try:
            access = normalize_access(declared_access)
        except ManifestAccessError:
            return (*errors, f"unknown_access_class:{declared_access}")

    if kind == MICRODATA_RELEASE_KIND and not _text(spec.get("licence")):
        errors.append("missing_licence")

    if is_hash_only(access):
        errors.extend(
            _hash_only_entry_errors(
                spec,
                manifest=manifest,
                local_file_exists=local_file_exists,
            )
        )
    return tuple(_dedupe(errors))


def _hash_only_entry_errors(
    spec: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any] | None,
    local_file_exists: bool,
) -> list[str]:
    """Return refusal codes for a licensed or restricted registration."""
    errors: list[str] = []
    if not _text(spec.get("licence")):
        errors.append("missing_licence")
    sha256 = _text(spec.get("sha256"))
    if not sha256:
        errors.append("missing_sha256")
    elif not _SHA256_RE.match(sha256):
        errors.append("malformed_sha256")
    if not _text(spec.get("vintage")):
        errors.append("missing_vintage")
    if not _access_route(spec, manifest):
        errors.append("missing_access_route")
    if not (_text(spec.get("verified_at")) or _text(spec.get("fetched_at"))):
        errors.append("missing_verification_timestamp")
    if local_file_exists:
        errors.append("bytes_present_for_hash_only_entry")
    if recorded_r2(spec):
        errors.append("r2_location_for_hash_only_entry")
    return errors


def _access_route(
    spec: Mapping[str, Any],
    manifest: Mapping[str, Any] | None,
) -> str | None:
    """Return the recorded route to the bytes, from the entry or the manifest."""
    for key in ("access_route", "source_url", "source_page", "doi"):
        value = _text(spec.get(key))
        if value:
            return value
    if isinstance(manifest, Mapping):
        for key in ("source_page", "access_route"):
            value = _text(manifest.get(key))
            if value:
                return value
    return None


def recorded_r2(spec: Any) -> Mapping[str, Any] | None:
    """Return a recorded ``storage.r2`` mapping, if the entry carries one."""
    if not isinstance(spec, Mapping):
        return None
    storage = spec.get("storage")
    if not isinstance(storage, Mapping):
        return None
    r2 = storage.get("r2")
    return r2 if isinstance(r2, Mapping) else None


@dataclass(frozen=True)
class ArtifactRegistrationReport:
    """Report from registering one hash-only source artifact."""

    manifest_path: str
    source_id: str
    package_id: str
    year: int
    filename: str
    sha256: str
    size_bytes: int | None
    vintage: str
    licence: str
    access: str
    registration: str
    replaced: bool
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether the registration was written without refusals."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": self.valid,
            "manifest_path": self.manifest_path,
            "source_id": self.source_id,
            "package_id": self.package_id,
            "year": self.year,
            "filename": self.filename,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "vintage": self.vintage,
            "licence": self.licence,
            "access": self.access,
            "registration": self.registration,
            "replaced": self.replaced,
            "r2_location": None,
            "errors": list(self.errors),
        }


def register_hash_only_artifact(
    *,
    source_id: str,
    package_id: str,
    year: int,
    output_dir: str | Path,
    filename: str,
    sha256: str,
    licence: str,
    access: str,
    vintage: str,
    size_bytes: int | None = None,
    source_page: str | None = None,
    source_url: str | None = None,
    access_route: str | None = None,
    doi: str | None = None,
    study: str | None = None,
    dataset: str | None = None,
    table: str | None = None,
    publisher: str | None = None,
    fetched_at: str | None = None,
    verified_at: str | None = None,
    hash_source: str | None = None,
    notes: str | None = None,
    allow_reissue: bool = False,
) -> ArtifactRegistrationReport:
    """Register a licensed or restricted artifact by identity, without bytes.

    Writes (or updates) a ``kind: microdata_release`` manifest entry carrying the
    checksum, size, vintage, licence, access route, and verification timestamp.
    No bytes are read, written, or uploaded, and no R2 key is recorded.
    """
    access_class = normalize_access(access)
    if stores_bytes(access_class):
        raise HashOnlyRegistrationError(
            "register-artifact records identity without bytes and refuses "
            f"access={ACCESS_PUBLIC!r}. Register a public artifact with its "
            "bytes using fetch-artifact."
        )
    checksum = _text(sha256)
    if not checksum or not _SHA256_RE.match(checksum):
        raise HashOnlyRegistrationError(
            "A registration needs a lowercase 64-character SHA-256; refusing to "
            f"register {filename!r} with sha256={sha256!r}. Never invent a hash."
        )
    if not _text(licence):
        raise HashOnlyRegistrationError(
            f"A {access_class} registration must record the publisher licence."
        )
    if not _text(vintage):
        raise HashOnlyRegistrationError(
            f"A {access_class} registration must record the artifact vintage."
        )
    artifact_name = _text(filename)
    if not artifact_name or Path(artifact_name).name != artifact_name:
        raise HashOnlyRegistrationError(
            f"Registration filename must be a bare filename; got {filename!r}."
        )
    if not (_text(verified_at) or _text(fetched_at)):
        raise HashOnlyRegistrationError(
            "A hash-only registration must record when the checksum was "
            "verified; pass --verified-at."
        )

    output = Path(output_dir)
    local_path = output / artifact_name
    if local_path.exists():
        raise HashOnlyRegistrationError(
            f"Refusing to register {artifact_name!r} hash-only while its bytes "
            f"are present at {local_path}. A {access_class} artifact's bytes "
            "must not live in a Chronicle store."
        )

    manifest_path = output / "manifest.yaml"
    payload = _load_manifest(manifest_path)
    existing_kind = manifest_kind(payload)
    if payload and existing_kind != MICRODATA_RELEASE_KIND:
        raise HashOnlyRegistrationError(
            f"{manifest_path} is a {existing_kind} manifest; hash-only "
            "registrations belong in a kind: microdata_release manifest."
        )

    entry = _registration_entry(
        filename=artifact_name,
        access=access_class,
        licence=str(licence),
        vintage=str(vintage),
        sha256=checksum,
        size_bytes=size_bytes,
        source_url=source_url,
        access_route=access_route,
        doi=doi,
        study=study,
        fetched_at=fetched_at,
        verified_at=verified_at,
        hash_source=hash_source,
        notes=notes,
    )
    route_context = dict(payload)
    if source_page:
        route_context["source_page"] = source_page
    if not _access_route(entry, route_context):
        raise HashOnlyRegistrationError(
            "A hash-only registration must record how the bytes are reached; "
            "pass --access-route, --source-url, --doi, or --source-page."
        )

    _assert_manifest_identity(payload, manifest_path, "source_id", source_id)
    _assert_manifest_identity(payload, manifest_path, "package_id", package_id)
    payload.setdefault("source_id", source_id)
    payload.setdefault("package_id", package_id)
    payload["kind"] = MICRODATA_RELEASE_KIND
    payload.setdefault("dataset", dataset or f"{source_id}_{package_id}")
    if publisher:
        payload.setdefault("publisher", publisher)
    if source_page:
        payload.setdefault("source_page", source_page)
    if table:
        payload.setdefault("table", table)
    payload.setdefault("files", {})

    entries = _existing_entries(payload["files"], year)
    # Two passes, so re-registering an existing pin stays idempotent even after
    # a reissue has added a second entry for the same filename. A single pass
    # would raise on the first filename match with a different checksum before
    # it could reach the exact match further down the list.
    replaced = False
    for index, existing in enumerate(entries):
        if not isinstance(existing, Mapping):
            continue
        if _text(existing.get("filename")) != artifact_name:
            continue
        if _text(existing.get("sha256")) == checksum:
            entries[index] = entry
            replaced = True
            break
    if not replaced:
        superseded = [
            existing
            for existing in entries
            if isinstance(existing, Mapping)
            and _text(existing.get("filename")) == artifact_name
        ]
        if superseded and not allow_reissue:
            raise HashOnlyRegistrationError(
                f"{manifest_path} already registers {artifact_name!r} for "
                f"{year} with sha256={superseded[0].get('sha256')!r}. Different "
                "bytes are a new publisher release, not a pin replacement; "
                "pass --allow-reissue to register both."
            )
        # A reissue sits alongside the pin it supersedes.
        entries.append(entry)

    payload["files"][year] = sorted(entries, key=_entry_sort_key)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    return ArtifactRegistrationReport(
        manifest_path=str(manifest_path),
        source_id=str(payload["source_id"]),
        package_id=str(payload["package_id"]),
        year=year,
        filename=artifact_name,
        sha256=checksum,
        size_bytes=size_bytes,
        vintage=str(vintage),
        licence=str(licence),
        access=access_class,
        registration=registration_id(
            source_id=str(payload["source_id"]),
            package_id=str(payload["package_id"]),
            year=year,
            sha256=checksum,
            filename=artifact_name,
        ),
        replaced=replaced,
    )


def _assert_manifest_identity(
    payload: Mapping[str, Any],
    manifest_path: Path,
    key: str,
    value: str,
) -> None:
    """Refuse to register into a manifest that identifies a different source."""
    existing = _text(payload.get(key))
    if existing is not None and existing != value:
        raise HashOnlyRegistrationError(
            f"{manifest_path} declares {key}={existing!r}; refusing to register "
            f"{key}={value!r} into it."
        )


def _entry_sort_key(entry: Any) -> tuple[str, str]:
    """Return a deterministic sort key for a registration entry."""
    if not isinstance(entry, Mapping):
        return ("", "")
    return (_text(entry.get("filename")) or "", _text(entry.get("sha256")) or "")


def _registration_entry(**values: Any) -> dict[str, Any]:
    """Build a deterministic, field-ordered registration entry."""
    entry: dict[str, Any] = {}
    for key in _REGISTRATION_FIELD_ORDER:
        value = values.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        entry[key] = value
    return entry


def _existing_entries(files: Any, year: Any) -> list[Any]:
    """Return the existing file entries for a year as a mutable list."""
    if not isinstance(files, dict):
        return []
    spec = files.get(year)
    if spec is None:
        return []
    if isinstance(spec, list):
        return list(spec)
    return [spec]


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    """Load a manifest mapping, or an empty mapping when absent."""
    if not manifest_path.exists():
        return {}
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise HashOnlyRegistrationError(f"Manifest must be a mapping: {manifest_path}")
    return payload


def _text(value: Any) -> str | None:
    """Return a non-empty stripped string, or None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dedupe(values: Iterable[str]) -> list[str]:
    """Return values with duplicates removed, preserving order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


__all__ = [
    "ACCESS_CLASSES",
    "ACCESS_LICENSED",
    "ACCESS_PUBLIC",
    "ACCESS_RESTRICTED",
    "ArtifactRegistrationReport",
    "DEFAULT_ACCESS",
    "DEFAULT_MANIFEST_KIND",
    "HashOnlyRegistrationError",
    "ListSpecRejected",
    "MANIFEST_KINDS",
    "MICRODATA_RELEASE_KIND",
    "ManifestAccessError",
    "MicrodataReleaseNotParseableError",
    "PUBLISHER_TABLE_KIND",
    "entry_access",
    "is_hash_only",
    "is_microdata_release",
    "iter_file_specs",
    "manifest_kind",
    "normalize_access",
    "recorded_r2",
    "register_hash_only_artifact",
    "registration_id",
    "safe_entry_access",
    "safe_manifest_kind",
    "stores_bytes",
    "validate_file_entry",
]
