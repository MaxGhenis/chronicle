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

HASH_SOURCE_CHRONICLE_FETCH = "chronicle_fetch"
HASH_SOURCE_CONSUMER_ATTESTED = "consumer_attested"
HASH_SOURCE_CONSUMER_PIN = "consumer_pin"
#: The closed set of ways a microdata checksum may enter Chronicle.
HASH_SOURCES: tuple[str, ...] = (
    HASH_SOURCE_CHRONICLE_FETCH,
    HASH_SOURCE_CONSUMER_ATTESTED,
    HASH_SOURCE_CONSUMER_PIN,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

LICENCE_EVIDENCE_FIELDS: tuple[str, ...] = (
    "issuer",
    "licence_id",
    "licence_version",
    "scope_statement",
    "evidence_url",
    "covered_sha256",
)


@dataclass(frozen=True)
class RedistributableLicence:
    """One exact public-byte licence identifier and its authority."""

    name: str
    basis: str
    evidence_url: str


# Exact identifiers keep the custody decision reviewable. A descriptive string
# that merely mentions one of these terms is not allowlisted.
REDISTRIBUTABLE_MICRODATA_LICENCES: Mapping[str, RedistributableLicence] = {
    "US-GOVERNMENT-WORK-17-USC-105": RedistributableLicence(
        name="U.S. Government work",
        basis="17 U.S.C. §105",
        evidence_url="https://www.copyright.gov/title17/92chap1.html#105",
    ),
    "U.S. Government work / 17 U.S.C. §105": RedistributableLicence(
        name="U.S. Government work",
        basis="17 U.S.C. §105",
        evidence_url="https://www.copyright.gov/title17/92chap1.html#105",
    ),
    "OGL-3.0": RedistributableLicence(
        name="Open Government Licence v3.0",
        basis="UK Open Government Licence, version 3.0",
        evidence_url=(
            "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
        ),
    ),
    "UK Open Government Licence v3": RedistributableLicence(
        name="Open Government Licence v3.0",
        basis="UK Open Government Licence, version 3.0",
        evidence_url=(
            "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
        ),
    ),
    "CC0-1.0": RedistributableLicence(
        name="CC0 1.0 Universal",
        basis="Creative Commons CC0 1.0 legal code",
        evidence_url="https://creativecommons.org/publicdomain/zero/1.0/legalcode",
    ),
    "CC0 1.0": RedistributableLicence(
        name="CC0 1.0 Universal",
        basis="Creative Commons CC0 1.0 legal code",
        evidence_url="https://creativecommons.org/publicdomain/zero/1.0/legalcode",
    ),
    "CC-BY-4.0": RedistributableLicence(
        name="Creative Commons Attribution 4.0 International",
        basis="Creative Commons CC BY 4.0 legal code",
        evidence_url="https://creativecommons.org/licenses/by/4.0/legalcode",
    ),
    "CC BY 4.0": RedistributableLicence(
        name="Creative Commons Attribution 4.0 International",
        basis="Creative Commons CC BY 4.0 legal code",
        evidence_url="https://creativecommons.org/licenses/by/4.0/legalcode",
    ),
}

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
    "hash_source",
    "attested_by",
    "verified_at",
    "licence_evidence",
    "attestation_evidence",
    "pinned_from",
    "public_object_removal_evidence",
    "notes",
)


class ManifestAccessError(ValueError):
    """Raised when a manifest declares an unusable access class or kind."""


class HashOnlyRegistrationError(ValueError):
    """Raised when a hash-only registration is malformed or would store bytes."""


class MicrodataReleaseNotParseableError(ValueError):
    """Raised when a source package points at a registered microdata release.

    Registration is manifest-level: no source package parses a microdata
    release, and no microdata records, rows, columns, row values, or cells
    enter Chronicle.
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


def normalize_hash_source(hash_source: str | None) -> str:
    """Return a hash-source value from the closed attestation vocabulary."""
    value = _text(hash_source)
    if value not in HASH_SOURCES:
        raise ManifestAccessError(
            f"Unknown hash_source {value!r}; expected one of {list(HASH_SOURCES)}."
        )
    return value


def canonical_artifact_filename(filename: Any) -> str:
    """Return a bare artifact basename, refusing every path spelling.

    Both slash styles are rejected explicitly. Using ``Path.name`` alone on
    POSIX would accept a Windows separator, while silently taking the basename
    would let ``./adult.tab`` evade an existing ``adult.tab`` registration.
    """
    value = _text(filename)
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ManifestAccessError(
            "Artifact filename must be a bare basename with no path separators "
            f"or dot segments; got {filename!r}."
        )
    return value


def resolve_year_key(files: Mapping[Any, Any], year: Any) -> Any:
    """Resolve an integer/string year to the one existing manifest key.

    YAML may load an unquoted year as ``2023`` and a quoted year as ``"2023"``.
    They identify one vintage. If a malformed manifest already carries both,
    refuse rather than choosing one and leaving parallel state behind.
    """
    numeric_year: int | None = None
    if isinstance(year, int) and not isinstance(year, bool):
        numeric_year = year
    elif isinstance(year, str) and re.fullmatch(r"0|[1-9][0-9]*", year):
        numeric_year = int(year)
    if numeric_year is None:
        return year

    candidates = (numeric_year, str(numeric_year))
    present = [candidate for candidate in candidates if candidate in files]
    if len(present) > 1:
        raise ManifestAccessError(
            f"Manifest files contains parallel year keys {numeric_year!r} and "
            f"{str(numeric_year)!r}; Chronicle cannot choose between them."
        )
    return present[0] if present else year


def manifest_filename_errors(manifest: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Return filename and alias-collision errors across a whole manifest."""
    if not isinstance(manifest, Mapping):
        return ()
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        return ()

    errors: list[str] = []
    kind, _kind_error = safe_manifest_kind(manifest)
    seen: dict[str, tuple[str, Any, Mapping[str, Any]]] = {}
    for year, year_spec in files.items():
        entries = year_spec if isinstance(year_spec, list) else (year_spec,)
        for spec in entries:
            if not isinstance(spec, Mapping) or "filename" not in spec:
                continue
            raw = spec.get("filename")
            try:
                canonical = canonical_artifact_filename(raw)
            except ManifestAccessError:
                errors.append(f"filename_not_bare_basename:{year}:{raw}")
                # Still derive the spelling solely for collision reporting; it
                # is never used to address a file.
                text = _text(raw)
                if not text:
                    continue
                canonical = text.replace("\\", "/").rsplit("/", 1)[-1]
            previous = seen.get(canonical)
            raw_text = str(raw)
            if previous is not None and previous[0] != raw_text:
                errors.append(
                    "filename_alias_collision:"
                    f"{canonical}:{previous[1]}:{previous[0]}:{year}:{raw_text}"
                )
            elif previous is not None and kind == MICRODATA_RELEASE_KIND:
                previous_spec = previous[2]
                previous_sha = _text(previous_spec.get("sha256"))
                current_sha = _text(spec.get("sha256"))
                public_path_collision = ACCESS_PUBLIC in {
                    safe_entry_access(previous_spec),
                    safe_entry_access(spec),
                }
                if previous_sha == current_sha or public_path_collision:
                    errors.append(
                        "filename_identity_collision:"
                        f"{canonical}:{previous[1]}:{previous_sha}:"
                        f"{year}:{current_sha}"
                    )
            else:
                seen.setdefault(canonical, (raw_text, year, spec))
    return tuple(_dedupe(errors))


def is_redistributable_microdata_licence(licence: Any) -> bool:
    """Whether ``licence`` is one exact allowlisted redistribution term."""
    return _text(licence) in REDISTRIBUTABLE_MICRODATA_LICENCES


def microdata_licence_evidence_errors(spec: Any) -> tuple[str, ...]:
    """Return errors in an artifact-bound redistribution evidence block."""
    if not isinstance(spec, Mapping):
        return ("missing_licence_evidence",)
    evidence = spec.get("licence_evidence")
    if not isinstance(evidence, Mapping):
        return ("missing_licence_evidence",)

    errors: list[str] = []
    for field in LICENCE_EVIDENCE_FIELDS:
        if not _text(evidence.get(field)):
            errors.append(f"licence_evidence_missing_{field}")

    licence_id = _text(evidence.get("licence_id"))
    if licence_id and licence_id != _text(spec.get("licence")):
        errors.append("licence_evidence_licence_mismatch")

    covered_sha256 = _text(evidence.get("covered_sha256"))
    if covered_sha256 and not _SHA256_RE.fullmatch(covered_sha256):
        errors.append("licence_evidence_malformed_covered_sha256")
    artifact_sha256 = _text(spec.get("sha256"))
    if covered_sha256 and artifact_sha256 and covered_sha256 != artifact_sha256:
        errors.append("licence_evidence_sha256_mismatch")

    evidence_url = _text(evidence.get("evidence_url"))
    if evidence_url and not evidence_url.startswith(("https://", "http://")):
        errors.append("licence_evidence_url_not_durable")
    return tuple(_dedupe(errors))


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


def effective_entry_access(spec: Any, *, kind: str) -> str:
    """Return the custody class after the microdata licence allowlist.

    A microdata entry described as public without an exact allowlisted licence
    is handled as licensed/hash-only. Publisher-table behavior is unchanged.
    """
    access = safe_entry_access(spec)
    if kind == MICRODATA_RELEASE_KIND and access == ACCESS_PUBLIC:
        licence = spec.get("licence") if isinstance(spec, Mapping) else None
        if not is_redistributable_microdata_licence(licence):
            return ACCESS_LICENSED
        if microdata_licence_evidence_errors(spec):
            return ACCESS_LICENSED
    return access


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
    return (
        f"{source_id}/{package_id}/{year}/{sha256}/"
        f"{canonical_artifact_filename(filename)}"
    )


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
    if "filename" in spec:
        try:
            canonical_artifact_filename(spec.get("filename"))
        except ManifestAccessError:
            errors.append("filename_not_bare_basename")
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

    if kind == MICRODATA_RELEASE_KIND:
        if not _text(spec.get("filename")):
            errors.append("missing_filename")
        sha256 = _text(spec.get("sha256"))
        if not sha256:
            errors.append("missing_sha256")
        elif not _SHA256_RE.fullmatch(sha256):
            errors.append("malformed_sha256")
        if spec.get("size_bytes") is None:
            errors.append("missing_size_bytes")
        elif not isinstance(spec.get("size_bytes"), int) or spec["size_bytes"] < 0:
            errors.append("invalid_size_bytes")
        if not _text(spec.get("vintage")):
            errors.append("missing_vintage")
        if not _text(spec.get("licence")):
            errors.append("missing_licence")
        if not isinstance(manifest, Mapping) or not _text(manifest.get("source_id")):
            errors.append("missing_source_id")
        if not isinstance(manifest, Mapping) or not _text(manifest.get("package_id")):
            errors.append("missing_package_id")
        if not isinstance(manifest, Mapping) or not _text(manifest.get("publisher")):
            errors.append("missing_publisher")
        if not _access_route(spec, manifest):
            errors.append("missing_access_route")
    if (
        kind == MICRODATA_RELEASE_KIND
        and access == ACCESS_PUBLIC
        and not is_redistributable_microdata_licence(spec.get("licence"))
    ):
        errors.append("public_microdata_licence_not_allowlisted")
    if kind == MICRODATA_RELEASE_KIND and access == ACCESS_PUBLIC:
        errors.extend(microdata_licence_evidence_errors(spec))
        if local_file_exists:
            errors.append("bytes_present_beside_microdata_manifest")
    if kind == MICRODATA_RELEASE_KIND:
        errors.extend(_microdata_attestation_errors(spec))

    effective_access = effective_entry_access(spec, kind=kind)
    if is_hash_only(effective_access):
        errors.extend(
            _hash_only_entry_errors(
                spec,
                manifest=manifest,
                local_file_exists=local_file_exists,
            )
        )
    return tuple(_dedupe(errors))


def _microdata_attestation_errors(spec: Mapping[str, Any]) -> list[str]:
    """Return the closed hash-attestation contract errors for one entry."""
    errors: list[str] = []
    hash_source = _text(spec.get("hash_source"))
    if hash_source is None:
        errors.append("missing_hash_source")
    elif hash_source not in HASH_SOURCES:
        errors.append(f"unknown_hash_source:{hash_source}")

    if not _text(spec.get("verified_at")):
        errors.append("missing_verified_at")

    attested_by = _text(spec.get("attested_by"))
    if not attested_by:
        errors.append("missing_attested_by")
    elif hash_source == HASH_SOURCE_CHRONICLE_FETCH and attested_by != "chronicle":
        errors.append("chronicle_fetch_attester_must_be_chronicle")
    elif (
        hash_source
        in {
            HASH_SOURCE_CONSUMER_ATTESTED,
            HASH_SOURCE_CONSUMER_PIN,
        }
        and attested_by == "chronicle"
    ):
        errors.append("consumer_hash_attester_must_not_be_chronicle")

    if hash_source == HASH_SOURCE_CONSUMER_ATTESTED and not _text(
        spec.get("attestation_evidence")
    ):
        errors.append("missing_attestation_evidence")
    if (
        hash_source != HASH_SOURCE_CONSUMER_ATTESTED
        and spec.get("attestation_evidence") is not None
    ):
        errors.append("unexpected_attestation_evidence")
    if hash_source == HASH_SOURCE_CONSUMER_PIN and not _valid_pinned_from(
        spec.get("pinned_from")
    ):
        errors.append("missing_or_invalid_pinned_from")
    if hash_source != HASH_SOURCE_CONSUMER_PIN and spec.get("pinned_from") is not None:
        errors.append("unexpected_pinned_from")
    return errors


def _valid_pinned_from(value: Any) -> bool:
    """Whether a consumer-pin pointer names repository, path, and commit."""
    if not isinstance(value, Mapping):
        return False
    repository = _text(value.get("repository"))
    path = _text(value.get("path"))
    commit = _text(value.get("commit"))
    return bool(repository and path and commit and _COMMIT_RE.fullmatch(commit))


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
    if not _text(spec.get("verified_at")):
        errors.append("missing_verified_at")
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
    manifest_filename: str = "manifest.yaml",
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
    attested_by: str | None = None,
    attestation_evidence: str | None = None,
    pinned_from: Mapping[str, str] | None = None,
    supersede_public_object: str | None = None,
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
    try:
        artifact_name = canonical_artifact_filename(filename)
    except ManifestAccessError as exc:
        raise HashOnlyRegistrationError(
            f"Registration filename must be a bare filename; got {filename!r}."
        ) from exc
    if not _text(verified_at):
        raise HashOnlyRegistrationError(
            "A hash-only registration must record when the checksum was "
            "verified; pass --verified-at."
        )
    try:
        hash_source_value = normalize_hash_source(hash_source)
    except ManifestAccessError as exc:
        raise HashOnlyRegistrationError(str(exc)) from exc
    attester = _text(attested_by)
    if not attester:
        raise HashOnlyRegistrationError(
            "A microdata registration must identify its attester; pass --attested-by."
        )
    if hash_source_value == HASH_SOURCE_CHRONICLE_FETCH:
        raise HashOnlyRegistrationError(
            "register-artifact never reads bytes and cannot attest "
            "hash_source='chronicle_fetch'; use consumer_attested or consumer_pin."
        )
    if attester == "chronicle":
        raise HashOnlyRegistrationError(
            "A consumer hash attestation cannot use attested_by='chronicle'."
        )
    if hash_source_value == HASH_SOURCE_CONSUMER_ATTESTED and not _text(
        attestation_evidence
    ):
        raise HashOnlyRegistrationError(
            "hash_source='consumer_attested' requires --attestation-evidence."
        )
    if hash_source_value == HASH_SOURCE_CONSUMER_PIN and not _valid_pinned_from(
        pinned_from
    ):
        raise HashOnlyRegistrationError(
            "hash_source='consumer_pin' requires pinned_from repository, path, "
            "and commit evidence."
        )
    if hash_source_value != HASH_SOURCE_CONSUMER_ATTESTED and _text(
        attestation_evidence
    ):
        raise HashOnlyRegistrationError(
            "--attestation-evidence applies only to hash_source='consumer_attested'."
        )
    if hash_source_value != HASH_SOURCE_CONSUMER_PIN and pinned_from is not None:
        raise HashOnlyRegistrationError(
            "pinned_from applies only to hash_source='consumer_pin'."
        )

    output = Path(output_dir)
    try:
        manifest_name = canonical_artifact_filename(manifest_filename)
    except ManifestAccessError as exc:
        raise HashOnlyRegistrationError(
            "--manifest must name one file inside --out-dir; got "
            f"{manifest_filename!r}."
        ) from exc
    manifest_path = output / manifest_name
    payload = _load_manifest(manifest_path)
    existing_kind = manifest_kind(payload)
    if payload and existing_kind != MICRODATA_RELEASE_KIND:
        raise HashOnlyRegistrationError(
            f"{manifest_path} is a {existing_kind} manifest; hash-only "
            "registrations belong in a kind: microdata_release manifest."
        )
    filename_errors = manifest_filename_errors(payload)
    if filename_errors:
        raise HashOnlyRegistrationError(
            f"{manifest_path} has unsafe artifact filenames: "
            f"{', '.join(filename_errors)}."
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
        hash_source=hash_source_value,
        attested_by=attester,
        attestation_evidence=attestation_evidence,
        pinned_from=dict(pinned_from) if pinned_from is not None else None,
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
    files = payload.setdefault("files", {})
    if not isinstance(files, dict):
        raise HashOnlyRegistrationError(
            f"{manifest_path} files must be a mapping, not {type(files).__name__}."
        )
    try:
        year_key = resolve_year_key(files, year)
    except ManifestAccessError as exc:
        raise HashOnlyRegistrationError(str(exc)) from exc

    entries = _existing_entries(files, year_key)
    local_path = output / artifact_name
    if local_path.exists():
        raise HashOnlyRegistrationError(
            f"Refusing to register {artifact_name!r} hash-only while its bytes "
            f"are present at {local_path}. A {access_class} artifact's bytes "
            "must not live in a Chronicle store."
        )
    # Two passes, so re-registering an existing pin stays idempotent even after
    # a reissue has added a second entry for the same filename. A single pass
    # would raise on the first filename match with a different checksum before
    # it could reach the exact match further down the list.
    exact_matches = [
        (index, existing)
        for index, existing in enumerate(entries)
        if isinstance(existing, Mapping)
        and _text(existing.get("filename")) == artifact_name
        and _text(existing.get("sha256")) == checksum
    ]
    for _index, existing in exact_matches:
        if recorded_r2(existing):
            raise HashOnlyRegistrationError(
                f"Refusing to reclassify {artifact_name!r} as "
                f"{access_class}: its identity still records storage.r2. "
                "Remove the public object and its locator with verifiable "
                "evidence before registering it hash-only."
            )
    public_matches = [
        existing
        for _index, existing in exact_matches
        if entry_access(existing) == ACCESS_PUBLIC
    ]
    if public_matches:
        removal_evidence = _text(supersede_public_object)
        if not removal_evidence:
            raise HashOnlyRegistrationError(
                f"Refusing to silently reclassify public identity "
                f"{artifact_name!r} as {access_class}. After its public "
                "object is verifiably removed, pass --supersede-public-object "
                "with the removal evidence."
            )
        entry["public_object_removal_evidence"] = removal_evidence

    replaced = bool(exact_matches)
    if exact_matches:
        target_index, existing = exact_matches[0]
        entries[target_index] = {**dict(existing), **entry}
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

    files[year_key] = entries
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
    """Load a manifest mapping, refusing malformed or non-mapping YAML."""
    if not manifest_path.exists():
        return {}
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise HashOnlyRegistrationError(
            f"{manifest_path} is not valid YAML: {exc}"
        ) from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise HashOnlyRegistrationError(
            f"{manifest_path} must be a YAML mapping; it parses as a "
            f"{type(payload).__name__}. Chronicle will not overwrite it."
        )
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
    "HASH_SOURCES",
    "HASH_SOURCE_CHRONICLE_FETCH",
    "HASH_SOURCE_CONSUMER_ATTESTED",
    "HASH_SOURCE_CONSUMER_PIN",
    "LICENCE_EVIDENCE_FIELDS",
    "DEFAULT_ACCESS",
    "DEFAULT_MANIFEST_KIND",
    "HashOnlyRegistrationError",
    "ListSpecRejected",
    "MANIFEST_KINDS",
    "MICRODATA_RELEASE_KIND",
    "ManifestAccessError",
    "MicrodataReleaseNotParseableError",
    "PUBLISHER_TABLE_KIND",
    "REDISTRIBUTABLE_MICRODATA_LICENCES",
    "RedistributableLicence",
    "canonical_artifact_filename",
    "effective_entry_access",
    "entry_access",
    "is_hash_only",
    "is_microdata_release",
    "iter_file_specs",
    "manifest_kind",
    "microdata_licence_evidence_errors",
    "manifest_filename_errors",
    "normalize_access",
    "normalize_hash_source",
    "recorded_r2",
    "register_hash_only_artifact",
    "registration_id",
    "resolve_year_key",
    "safe_entry_access",
    "safe_manifest_kind",
    "stores_bytes",
    "validate_file_entry",
]
