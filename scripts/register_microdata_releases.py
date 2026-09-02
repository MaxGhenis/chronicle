"""Register raw microdata releases from a Microcosm source-stages manifest.

Chronicle registers every raw microdata release its consumers build from and
stores the bytes of only those a publisher permits it to redistribute
(``docs/adr-chronicle-raw-microdata-identity.md``). This script drives both
halves of that from the pins Microcosm already reviewed:

``emit``
    Write hash-only ``kind: microdata_release`` manifests for ``licensed`` and
    ``restricted`` releases. Every checksum, size, filename, and vintage is read
    verbatim from the Microcosm source-stages JSON; nothing is recomputed and
    nothing is invented. A release Microcosm pins without a checksum is reported
    as a blocker and never registered.

``plan``
    Print the exact ``chronicle fetch-artifact ... --upload-r2`` commands to run
    from a networked machine for ``public`` releases, whose bytes Chronicle does
    archive. Publisher URLs are copied verbatim from the Microcosm manifest; a
    release whose manifest carries no URL prints a ``TODO`` instead of a guess.

The catalogue below is the only authored content: it maps a Microcosm artifact
onto Chronicle's ``{source_id, package_id, year, sha256, filename}`` identity
and records the publisher's terms. Run it read-only against a Microcosm
checkout; this script never writes to that repository.

Usage::

    python scripts/register_microdata_releases.py emit \\
        --microcosm-root ~/PolicyEngine/microcosm \\
        --root db/data --verified-at 2026-09-02

    python scripts/register_microdata_releases.py plan \\
        --microcosm-root ~/PolicyEngine/microcosm --root db/data
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
import shlex
import sys
from typing import Any

# Allow `python scripts/register_microdata_releases.py` from a checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chronicle.registration import (  # noqa: E402
    ACCESS_PUBLIC,
    MICRODATA_RELEASE_KIND,
    HashOnlyRegistrationError,
    register_hash_only_artifact,
)

#: Microcosm's per-artifact ``kind`` mapped onto Chronicle's access class.
#:
#: ``private_microdata`` maps to ``restricted`` rather than ``licensed``: the
#: bytes are held only in a private mirror, and over-classifying is the safe
#: direction because both classes are registered hash-only and neither ever
#: places bytes in a Chronicle store.
ACCESS_BY_MICROCOSM_KIND: dict[str, str] = {
    "public_microdata": "public",
    "licensed_microdata": "licensed",
    "private_microdata": "restricted",
    "restricted_microdata": "restricted",
}

#: Provenance sentence written onto every registration this script emits.
HASH_PROVENANCE = (
    "SHA-256 transcribed verbatim from the consumer's reviewed pin; Chronicle "
    "holds no bytes for this release and did not recompute the checksum."
)


@dataclass(frozen=True)
class ArtifactSelector:
    """Locate one artifact inside a Microcosm source-stages JSON file.

    ``stage`` names the build stage; ``match`` is a set of artifact fields that
    must equal the given values. A selector that matches nothing, or matches
    inconsistent bytes across stages, is a hard error rather than a guess.
    """

    stage: str | None = None
    match: Mapping[str, Any] = field(default_factory=dict)
    kind: str | None = None


@dataclass(frozen=True)
class Release:
    """One Chronicle registration drawn from a Microcosm pin."""

    release_id: str
    manifest: str
    selector: ArtifactSelector
    source_id: str
    package_id: str
    package_dir: str
    year: int
    table: str
    publisher: str
    licence: str
    access: str
    #: Artifact field holding the publisher filename, when not ``filename``.
    filename_field: str = "filename"
    #: Artifact field holding the publisher URL, for ``public`` releases.
    url_field: str = "locator"
    study: str | None = None
    doi: str | None = None
    source_page: str | None = None
    access_route: str | None = None
    vintage: str | None = None
    notes: str | None = None
    #: Whether Microcosm's pinned ``sha256`` is of the publisher artifact
    #: itself. It is not when the pin covers a derived file or an extracted
    #: archive member, and such a hash must never be presented as the checksum
    #: a fetch should reproduce.
    pinned_sha_is_publisher_bytes: bool = True
    #: Set when Microcosm pins the release without a checksum.
    blocker: str | None = None


UK_STAGES = "packages/microcosm-build/src/microcosm/build/uk/source_stages.json"
UK_HMRC_STAGES = (
    "packages/microcosm-build/src/microcosm/build/uk/hmrc_income_source_stages.json"
)
BE_STAGES = "packages/microcosm-build/src/microcosm/build/be/source_stages.json"
US_STAGES = "packages/microcosm-build/src/microcosm/build/us/source_stages.json"
US_ACS_2024 = (
    "packages/microcosm-build/src/microcosm/build/us_runtime/acs_2024_1yr_sources.json"
)

#: FRS 2023-24 tabs, in the order Microcosm's ``frs_spine`` stage lists them.
FRS_TABS: tuple[str, ...] = (
    "accounts",
    "adult",
    "benefits",
    "benunit",
    "child",
    "chldcare",
    "extchild",
    "househol",
    "job",
    "maint",
    "mortgage",
    "oddjob",
    "penprov",
    "pension",
)

FRS_LICENCE = "UK Data Service End User Licence"
FRS_STUDY = "UK Data Service SN 9367"
FRS_DOI = "10.5255/UKDA-SN-9367-2"
FRS_ACCESS_ROUTE = (
    "UK Data Service study SN 9367 under its End User Licence. Bytes stay in "
    "the licensed environment the consumer already operates; no Chronicle "
    "credential grants access to them."
)
FRS_NOTES = (
    "Microcosm's frs_spine stage cites 'UK Data Service SN 9367, DOI "
    "10.5255/UKDA-SN-9367-2'; its frs_employment, frs_council_tax, "
    "frs_education, and frs_legacy_proxies stages cite 'SN 9252' for the same "
    "2023_24 tabs. The tabs are the same bytes across all five stages (identical "
    "SHA-256), so the registration carries the study reference that also carries "
    "a DOI. " + HASH_PROVENANCE
)

CATALOGUE: tuple[Release, ...] = (
    *(
        Release(
            release_id=f"dwp-frs-2023-24:{tab}",
            manifest=UK_STAGES,
            selector=ArtifactSelector(
                stage="frs_spine",
                kind="licensed_microdata",
                match={"table": tab},
            ),
            source_id="dwp",
            package_id="dwp-frs-2023-24",
            package_dir="dwp/frs_2023_24",
            year=2023,
            table="Family Resources Survey 2023-24",
            publisher="Department for Work and Pensions",
            licence=FRS_LICENCE,
            access="licensed",
            filename_field="locator",
            study=FRS_STUDY,
            doi=FRS_DOI,
            access_route=FRS_ACCESS_ROUTE,
            notes=FRS_NOTES,
        )
        for tab in FRS_TABS
    ),
    Release(
        release_id="hmrc-spi-public-use-tape-2022-23:put2223uk",
        manifest=UK_HMRC_STAGES,
        selector=ArtifactSelector(
            stage="hmrc_spi_income",
            kind="private_microdata",
            match={"filename": "put2223uk.tab"},
        ),
        source_id="hmrc",
        package_id="hmrc-spi-public-use-tape-2022-23",
        package_dir="hmrc/spi_public_use_tape_2022_23",
        year=2022,
        table="Survey of Personal Incomes Public Use Tape 2022-23",
        publisher="HM Revenue and Customs",
        licence="UK Data Service End User Licence (study SN 9422)",
        access="restricted",
        study="UK Data Service SN 9422",
        doi="10.5255/UKDA-SN-9422-1",
        access_route=(
            "UK Data Service study SN 9422. Microcosm reaches the bytes through "
            "PolicyEngine's licensed copy in the private "
            "policyengine/policyengine-uk-data-private Hugging Face repository "
            "(spi_2022_23.zip); no Chronicle credential grants access to them."
        ),
        notes=(
            "Microcosm classes this artifact kind: private_microdata with "
            "access: private_local_input. Chronicle registers it restricted "
            "because the bytes are held only in a private mirror; if the UKDS "
            "terms for SN 9422 are confirmed as End User Licence it can be "
            "reclassified licensed, which changes nothing about storage — both "
            "classes are hash-only. " + HASH_PROVENANCE
        ),
    ),
    Release(
        release_id="statbel-be-silc-2023",
        manifest=BE_STAGES,
        selector=ArtifactSelector(
            stage="silc_load",
            kind="restricted_microdata",
        ),
        source_id="statbel",
        package_id="statbel-be-silc-2023",
        package_dir="statbel/be_silc_2023",
        year=2023,
        table="BE-SILC 2023 scientific-use files (D, R, H, P)",
        publisher="Statbel",
        licence="Statbel/Eurostat scientific-use",
        access="restricted",
        source_page="https://statbel.fgov.be/en/themes/households/poverty-and-living-conditions",
        access_route=(
            "Statbel BE-SILC scientific-use files: D (household register), "
            "R (personal register), H (household data), P (personal data)."
        ),
        blocker=(
            "Microcosm's be/source_stages.json pins the BE-SILC scientific-use "
            "files with no sha256, no size_bytes, and no per-file filename — it "
            "names only the four file roles. A registration is identified by "
            "{source_id, package_id, year, sha256, filename}, so this release "
            "cannot be registered until the consumer publishes a reviewed "
            "checksum per file. No hash is invented here."
        ),
    ),
    # ---- public releases: bytes are archived, so these are fetch-and-upload ----
    Release(
        release_id="census-cps-asec-2023",
        manifest=US_STAGES,
        selector=ArtifactSelector(
            stage="weeks_unemployed_input",
            kind="public_microdata",
            match={"member": "pppub23.csv"},
        ),
        source_id="census_cps",
        package_id="census-cps-asec-2023",
        package_dir="census/cps_asec_2023",
        year=2023,
        table="CPS Annual Social and Economic Supplement 2023 public-use files",
        publisher="U.S. Census Bureau",
        licence="U.S. Census Bureau public-use file; U.S. Government work, no copyright",
        access=ACCESS_PUBLIC,
        source_page="https://www.census.gov/programs-surveys/cps/data/datasets.html",
    ),
    Release(
        release_id="census-cps-basic-monthly-2024",
        manifest=US_STAGES,
        selector=ArtifactSelector(
            stage="org_wages",
            kind="public_microdata",
        ),
        source_id="census_cps",
        package_id="census-cps-basic-monthly-2024",
        package_dir="census/cps_basic_monthly_2024",
        year=2024,
        table="CPS basic monthly public-use files, January-December 2024",
        publisher="U.S. Census Bureau",
        licence="U.S. Census Bureau public-use file; U.S. Government work, no copyright",
        access=ACCESS_PUBLIC,
        source_page="https://www2.census.gov/programs-surveys/cps/datasets/2024/basic/",
        notes=(
            "Microcosm pins the twelve monthly files as the locator string "
            "'jan24pub through dec24pub' with no per-file URL, filename "
            "extension, or checksum. The publisher directory is the stage "
            "source; the twelve filenames must be read off that directory "
            "before the fetch commands can be completed."
        ),
    ),
    Release(
        release_id="census-acs-pums-2022-household",
        manifest=US_STAGES,
        selector=ArtifactSelector(
            stage="acs_rent",
            kind="versioned_derived_microdata",
        ),
        source_id="census_acs",
        package_id="census-acs-pums-2022-1yr",
        package_dir="census/acs_pums_2022_1yr",
        year=2022,
        table="ACS 2022 1-Year PUMS household file",
        publisher="U.S. Census Bureau",
        licence="U.S. Census Bureau public-use file; U.S. Government work, no copyright",
        access=ACCESS_PUBLIC,
        url_field="official_household_source",
        source_page="https://www.census.gov/programs-surveys/acs",
        pinned_sha_is_publisher_bytes=False,
        notes=(
            "The Microcosm artifact's own sha256 belongs to the derived "
            "acs_2022.h5, not to the publisher zip; only the publisher URL is "
            "reused here. The fetch computes the release checksum."
        ),
    ),
    Release(
        release_id="census-acs-pums-2022-person",
        manifest=US_STAGES,
        selector=ArtifactSelector(
            stage="acs_rent",
            kind="versioned_derived_microdata",
        ),
        source_id="census_acs",
        package_id="census-acs-pums-2022-1yr",
        package_dir="census/acs_pums_2022_1yr",
        year=2022,
        table="ACS 2022 1-Year PUMS person file",
        publisher="U.S. Census Bureau",
        licence="U.S. Census Bureau public-use file; U.S. Government work, no copyright",
        access=ACCESS_PUBLIC,
        url_field="official_person_source",
        source_page="https://www.census.gov/programs-surveys/acs",
        pinned_sha_is_publisher_bytes=False,
        notes=(
            "The Microcosm artifact's own sha256 belongs to the derived "
            "acs_2022.h5, not to the publisher zip; only the publisher URL is "
            "reused here. The fetch computes the release checksum."
        ),
    ),
    Release(
        release_id="census-acs-pums-2024-household",
        manifest=US_ACS_2024,
        selector=ArtifactSelector(match={"role": "household"}),
        source_id="census_acs",
        package_id="census-acs-pums-2024-1yr",
        package_dir="census/acs_pums_2024_1yr",
        year=2024,
        table="ACS 2024 1-Year PUMS household file",
        publisher="U.S. Census Bureau",
        licence="U.S. Census Bureau public-use file; U.S. Government work, no copyright",
        access=ACCESS_PUBLIC,
        url_field="url",
        source_page="https://www2.census.gov/programs-surveys/acs/data/pums/2024/1-Year/",
    ),
    Release(
        release_id="census-acs-pums-2024-person",
        manifest=US_ACS_2024,
        selector=ArtifactSelector(match={"role": "person"}),
        source_id="census_acs",
        package_id="census-acs-pums-2024-1yr",
        package_dir="census/acs_pums_2024_1yr",
        year=2024,
        table="ACS 2024 1-Year PUMS person file",
        publisher="U.S. Census Bureau",
        licence="U.S. Census Bureau public-use file; U.S. Government work, no copyright",
        access=ACCESS_PUBLIC,
        url_field="url",
        source_page="https://www2.census.gov/programs-surveys/acs/data/pums/2024/1-Year/",
    ),
    Release(
        release_id="federal-reserve-scf-2022-summary",
        manifest=US_STAGES,
        selector=ArtifactSelector(
            stage="scf_wealth",
            kind="public_microdata",
            match={"member": "rscfp2022.dta"},
        ),
        source_id="federal_reserve",
        package_id="federal-reserve-scf-2022",
        package_dir="federal_reserve/scf_2022",
        year=2022,
        table="Survey of Consumer Finances 2022 summary extract",
        publisher="Board of Governors of the Federal Reserve System",
        licence="Federal Reserve Board public-use file; U.S. Government work, no copyright",
        access=ACCESS_PUBLIC,
        source_page="https://www.federalreserve.gov/econres/scfindex.htm",
    ),
    Release(
        release_id="federal-reserve-scf-2022-full",
        manifest=US_STAGES,
        selector=ArtifactSelector(
            stage="scf_wealth",
            kind="public_microdata",
            match={"member": "p22i6.dta"},
        ),
        source_id="federal_reserve",
        package_id="federal-reserve-scf-2022",
        package_dir="federal_reserve/scf_2022",
        year=2022,
        table="Survey of Consumer Finances 2022 full public data set",
        publisher="Board of Governors of the Federal Reserve System",
        licence="Federal Reserve Board public-use file; U.S. Government work, no copyright",
        access=ACCESS_PUBLIC,
        source_page="https://www.federalreserve.gov/econres/scfindex.htm",
        notes=(
            "Microcosm records no checksum for this zip: 'Full-file SHA-256 "
            "pending one network-enabled provisioning fetch'. The fetch below "
            "computes and registers it."
        ),
    ),
    Release(
        release_id="census-sipp-2023",
        manifest=US_STAGES,
        selector=ArtifactSelector(
            stage="scf_wealth",
            kind="public_microdata",
            match={"member": "pu2023.csv"},
        ),
        source_id="census_sipp",
        package_id="census-sipp-2023",
        package_dir="census/sipp_2023",
        year=2023,
        table="Survey of Income and Program Participation 2023 public-use file",
        publisher="U.S. Census Bureau",
        licence="U.S. Census Bureau public-use file; U.S. Government work, no copyright",
        access=ACCESS_PUBLIC,
        source_page="https://www.census.gov/programs-surveys/sipp.html",
        pinned_sha_is_publisher_bytes=False,
        notes=(
            "Microcosm reaches this file through an immutable Hugging Face "
            "mirror (revision 21280dca5995e978d706740a8a4b9b7860cfd7b6) and "
            "records no Census URL, so the publisher URL must be read off the "
            "SIPP dataset page before the fetch. Microcosm's pinned sha256 "
            "5c30439e365fc26483318ef61d1d8f4bb2f0e9d6bb47c22c06756a7698733ee2 "
            "and size 3726010471 are for the mirrored pu2023.csv member, not "
            "for whatever archive the Census page serves, so they are not the "
            "checksum this fetch should be expected to reproduce."
        ),
    ),
)


class CatalogueError(RuntimeError):
    """Raised when the catalogue cannot be resolved against Microcosm."""


def load_manifest(microcosm_root: Path, relative: str) -> dict[str, Any]:
    """Load one Microcosm JSON manifest, read-only."""
    path = microcosm_root / relative
    if not path.exists():
        raise CatalogueError(f"Microcosm manifest not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CatalogueError(f"Microcosm manifest must be an object: {path}")
    return payload


def iter_manifest_artifacts(
    payload: Mapping[str, Any],
) -> Iterator[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Yield ``(stage, artifact)`` pairs from either Microcosm manifest shape.

    Source-stages manifests nest artifacts under ``stages``; the ACS runtime
    manifest carries a flat top-level ``artifacts`` list with no stages.
    """
    stages = payload.get("stages")
    if isinstance(stages, list):
        for stage in stages:
            if not isinstance(stage, Mapping):
                continue
            for artifact in stage.get("artifacts") or ():
                if isinstance(artifact, Mapping):
                    yield stage, artifact
        return
    for artifact in payload.get("artifacts") or ():
        if isinstance(artifact, Mapping):
            yield payload, artifact


def select_artifact(
    payload: Mapping[str, Any],
    selector: ArtifactSelector,
    *,
    release_id: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Return the single ``(stage, artifact)`` a selector identifies.

    Several Microcosm stages reference the same bytes. Duplicates are accepted
    only when they agree on every field the registration reads; disagreement is
    an error, never a silent first-match.
    """
    matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for stage, artifact in iter_manifest_artifacts(payload):
        if selector.stage is not None and stage.get("stage") != selector.stage:
            continue
        if selector.kind is not None and artifact.get("kind") != selector.kind:
            continue
        if any(artifact.get(key) != value for key, value in selector.match.items()):
            continue
        matches.append((stage, artifact))
    if not matches:
        raise CatalogueError(
            f"{release_id}: no Microcosm artifact matches {selector}. The "
            "consumer manifest changed; re-derive the catalogue rather than "
            "hand-editing a registration."
        )
    first_stage, first = matches[0]
    for _stage, artifact in matches[1:]:
        if dict(artifact) != dict(first):
            raise CatalogueError(
                f"{release_id}: Microcosm pins conflicting values for this "
                "artifact across stages; refusing to choose between them."
            )
    return first_stage, first


@dataclass(frozen=True)
class ResolvedRelease:
    """A catalogue entry resolved against Microcosm's pinned artifact."""

    release: Release
    stage: Mapping[str, Any]
    artifact: Mapping[str, Any]

    @property
    def filename(self) -> str | None:
        """Publisher filename, from the release's declared artifact field."""
        value = self.artifact.get(self.release.filename_field)
        if not value:
            return None
        # A locator may be a URL or a bare filename; take the last path segment.
        return str(value).rstrip("/").rsplit("/", 1)[-1]

    @property
    def url(self) -> str | None:
        """Publisher URL, when the Microcosm artifact records one."""
        value = self.artifact.get(self.release.url_field)
        text = str(value).strip() if value else ""
        return text if text.startswith(("http://", "https://")) else None

    @property
    def sha256(self) -> str | None:
        """Checksum Microcosm pins for these bytes, if any."""
        value = self.artifact.get("sha256")
        return str(value) if value else None

    @property
    def size_bytes(self) -> int | None:
        """Size Microcosm pins for these bytes, if any."""
        value = self.artifact.get("size_bytes")
        return int(value) if isinstance(value, int) else None

    @property
    def vintage(self) -> str:
        """Publisher vintage label, from the release or the artifact."""
        return str(self.release.vintage or self.artifact.get("vintage") or "")


def resolve(
    microcosm_root: Path,
    releases: Sequence[Release],
) -> list[ResolvedRelease]:
    """Resolve every catalogue entry against the Microcosm checkout."""
    payloads: dict[str, dict[str, Any]] = {}
    resolved: list[ResolvedRelease] = []
    for release in releases:
        if release.manifest not in payloads:
            payloads[release.manifest] = load_manifest(microcosm_root, release.manifest)
        stage, artifact = select_artifact(
            payloads[release.manifest],
            release.selector,
            release_id=release.release_id,
        )
        resolved.append(
            ResolvedRelease(release=release, stage=stage, artifact=artifact)
        )
    return resolved


def hash_source(release: Release) -> str:
    """Return the provenance pointer recorded on a registration."""
    return f"PolicyEngine/microcosm {release.manifest} ({release.selector.stage})"


def emit(
    resolved: Sequence[ResolvedRelease],
    *,
    root: Path,
    verified_at: str,
    allow_reissue: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Write hash-only manifests for every registrable non-public release.

    Returns ``(registrations, blockers)``. A release Microcosm pins without a
    checksum is a blocker, not a registration: no hash is ever invented.
    """
    registrations: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    for item in resolved:
        release = item.release
        if release.access == ACCESS_PUBLIC:
            continue
        if release.blocker:
            blockers.append({"release": release.release_id, "reason": release.blocker})
            continue
        filename = item.filename
        checksum = item.sha256
        if not filename or not checksum:
            blockers.append(
                {
                    "release": release.release_id,
                    "reason": (
                        "Microcosm pins this release without a "
                        f"{'filename' if not filename else 'sha256'}; a "
                        "registration needs both. No value is invented."
                    ),
                }
            )
            continue
        report = register_hash_only_artifact(
            source_id=release.source_id,
            package_id=release.package_id,
            year=release.year,
            output_dir=root / release.package_dir,
            filename=filename,
            sha256=checksum,
            licence=release.licence,
            access=release.access,
            vintage=item.vintage,
            size_bytes=item.size_bytes,
            source_page=release.source_page,
            access_route=release.access_route,
            doi=release.doi,
            study=release.study,
            table=release.table,
            publisher=release.publisher,
            verified_at=verified_at,
            hash_source=hash_source(release),
            notes=release.notes or HASH_PROVENANCE,
            allow_reissue=allow_reissue,
        )
        registrations.append(report.to_dict())
    return registrations, blockers


def fetch_command(
    item: ResolvedRelease,
    *,
    root: Path,
    r2_bucket: str,
) -> tuple[str, list[str]]:
    """Return ``(command, todos)`` for one public release.

    The command is the exact ``chronicle fetch-artifact`` invocation to run from
    a networked machine. Anything Microcosm does not pin becomes a TODO rather
    than a fabricated value.
    """
    release = item.release
    todos: list[str] = []
    url = item.url
    if url is None:
        url = "TODO_PUBLISHER_URL"
        todos.append(
            f"{release.release_id}: Microcosm records no publisher URL "
            f"(field {release.url_field!r}); read it off {release.source_page}."
        )
    argv = [
        "uv",
        "run",
        "chronicle",
        "fetch-artifact",
        "--source-id",
        release.source_id,
        "--package-id",
        release.package_id,
        "--year",
        str(release.year),
        "--out-dir",
        str(root / release.package_dir),
        "--url",
        url,
    ]
    if release.source_page:
        argv += ["--source-page", release.source_page]
    argv += [
        "--table",
        release.table,
        "--access",
        ACCESS_PUBLIC,
        "--licence",
        release.licence,
        # A public microdata release is archived, but it is still a release:
        # several files share one vintage and no source package parses it.
        "--kind",
        MICRODATA_RELEASE_KIND,
        "--upload-r2",
        "--r2-bucket",
        r2_bucket,
    ]
    if item.sha256 and release.pinned_sha_is_publisher_bytes:
        todos.append(
            f"{release.release_id}: expect sha256 {item.sha256}"
            + (f" and size {item.size_bytes}" if item.size_bytes else "")
            + " — fail the registration if the fetched bytes differ."
        )
    elif item.sha256:
        todos.append(
            f"{release.release_id}: Microcosm's pinned sha256 {item.sha256} is "
            "NOT the publisher artifact's checksum — do not use it to verify "
            "this fetch. See the note below."
        )
    if release.notes:
        todos.append(f"{release.release_id}: {release.notes}")
    return shlex.join(argv), todos


def plan(
    resolved: Sequence[ResolvedRelease],
    *,
    root: Path,
    r2_bucket: str,
) -> tuple[list[str], list[str]]:
    """Return the fetch commands and TODOs for every public release."""
    commands: list[str] = []
    todos: list[str] = []
    for item in resolved:
        if item.release.access != ACCESS_PUBLIC:
            continue
        command, item_todos = fetch_command(item, root=root, r2_bucket=r2_bucket)
        commands.append(command)
        todos.extend(item_todos)
    return commands, todos


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--microcosm-root",
        type=Path,
        default=Path.home() / "PolicyEngine" / "microcosm",
        help="Read-only path to a PolicyEngine/microcosm checkout.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("db/data"),
        help="Chronicle data root that holds the package directories.",
    )
    parser.add_argument(
        "--release",
        action="append",
        default=None,
        help="Limit to these release IDs. Repeatable.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of prose.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    emit_parser = subparsers.add_parser(
        "emit",
        help="Write hash-only manifests for licensed and restricted releases",
    )
    emit_parser.add_argument(
        "--verified-at",
        required=True,
        help=(
            "Date the pins were verified against Microcosm, as YYYY-MM-DD. "
            "Required so repeated runs are byte-stable."
        ),
    )
    emit_parser.add_argument(
        "--allow-reissue",
        action="store_true",
        help="Register different bytes alongside an existing pin for a filename.",
    )

    plan_parser = subparsers.add_parser(
        "plan",
        help="Print the fetch commands to run for public releases",
    )
    plan_parser.add_argument(
        "--r2-bucket",
        default="ledger-raw",
        help="Raw bucket the fetch should upload to.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the registration script."""
    args = build_parser().parse_args(argv)
    releases = CATALOGUE
    if args.release:
        wanted = set(args.release)
        releases = tuple(r for r in CATALOGUE if r.release_id in wanted)
        missing = wanted - {r.release_id for r in releases}
        if missing:
            print(f"Unknown release IDs: {sorted(missing)}", file=sys.stderr)
            return 2

    microcosm_root = args.microcosm_root.expanduser()
    try:
        resolved = resolve(microcosm_root, releases)
    except CatalogueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.command == "emit":
        try:
            registrations, blockers = emit(
                resolved,
                root=args.root,
                verified_at=args.verified_at,
                allow_reissue=args.allow_reissue,
            )
        except HashOnlyRegistrationError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            print(
                json.dumps(
                    {"registrations": registrations, "blockers": blockers},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            for registration in registrations:
                print(f"registered {registration['registration']}")
            for blocker in blockers:
                print(f"BLOCKED {blocker['release']}: {blocker['reason']}")
            print(
                f"\n{len(registrations)} registration(s), {len(blockers)} blocker(s)."
            )
        return 0

    commands, todos = plan(resolved, root=args.root, r2_bucket=args.r2_bucket)
    if args.json:
        print(json.dumps({"commands": commands, "todos": todos}, indent=2))
        return 0
    print("# Run from a networked machine with R2 credentials.\n")
    for command in commands:
        print(command + "\n")
    if todos:
        print("# TODO")
        for todo in todos:
            print(f"#   {todo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
