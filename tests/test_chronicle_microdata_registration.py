"""Tests for microdata-release registration: identity without content.

Chronicle registers every raw microdata release its consumers build from and
stores the bytes of only the ones a publisher permits it to redistribute
(``docs/adr-chronicle-raw-microdata-identity.md``). These tests pin the whole
refusal surface: which access classes may carry bytes, which commands refuse
them, and that a hash-only registration is a complete, valid artifact record
with no local file and no R2 key.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from chronicle.artifacts import (
    ArtifactCommandResult,
    SourceArtifactManifestError,
    SourceArtifactRevisionError,
    fetch_source_artifact,
    inventory_source_artifacts,
    publish_source_artifacts,
)
from chronicle.harness import main as harness_main
from chronicle.registration import (
    ACCESS_CLASSES,
    HASH_SOURCES,
    REDISTRIBUTABLE_MICRODATA_LICENCES,
    HashOnlyRegistrationError,
    ListSpecRejected,
    ManifestAccessError,
    MicrodataReleaseNotParseableError,
    canonical_artifact_filename,
    canonical_manifest_filename,
    effective_entry_access,
    entry_access,
    is_hash_only,
    is_microdata_release,
    iter_file_specs,
    manifest_filename_errors,
    manifest_kind,
    normalize_access,
    normalize_hash_source,
    register_hash_only_artifact,
    registration_id,
    resolve_year_key,
    safe_entry_access,
    stores_bytes,
    strict_manifest_kind,
    validate_file_entry,
)
from chronicle.source_package import SourceArtifactSpec, validate_source_package


REPO_ROOT = Path(__file__).resolve().parents[1]
FRS_PACKAGE = REPO_ROOT / "db" / "data" / "dwp" / "frs_2024_25"
SPI_PACKAGE = REPO_ROOT / "db" / "data" / "hmrc" / "spi_public_use_tape_2022_23"
RELEASE_PACKAGES = (
    FRS_PACKAGE,
    REPO_ROOT / "db" / "data" / "ons" / "was_round_8",
    REPO_ROOT / "db" / "data" / "ons" / "lcfs_2023_24",
    REPO_ROOT / "db" / "data" / "ons" / "etb_1977_2024",
    SPI_PACKAGE,
)

# A syntactically valid checksum that identifies no real publisher bytes.
FIXTURE_SHA = "a" * 64


def _successful_upload(*_args, **_kwargs) -> ArtifactCommandResult:
    """Return a successful R2 command without crossing the test boundary."""
    return ArtifactCommandResult(
        command=("wrangler", "r2", "object", "put"),
        returncode=0,
        stdout="",
        stderr="",
    )


def _public_licence_evidence(
    covered_sha256: str,
    *,
    licence_id: str = "US-GOVERNMENT-WORK-17-USC-105",
    issuer: str = "Fixture publisher",
    filename: str = "adult.tab",
) -> dict[str, str]:
    """Return complete artifact-bound redistribution evidence for a fixture."""
    return {
        "issuer": issuer,
        "licence_id": licence_id,
        "licence_version": (
            "3.0" if licence_id == "OGL-3.0" else "17 U.S.C. section 105"
        ),
        "scope_statement": f"The licence covers publisher artifact {filename}.",
        "evidence_url": "https://example.test/publisher/licence",
        "covered_sha256": covered_sha256,
    }


def _public_gated_duplicate_manifest() -> dict:
    """Return two incompatible identities sharing one exact basename."""
    return {
        "source_id": "dwp",
        "package_id": "dwp-frs-2023-24",
        "kind": "microdata_release",
        "publisher": "Fixture publisher",
        "source_page": "https://example.test/release",
        "files": {
            2022: [
                {
                    "filename": "adult.tab",
                    "access": "public",
                    "licence": "US-GOVERNMENT-WORK-17-USC-105",
                    "vintage": "2022_23",
                    "sha256": "b" * 64,
                    "source_url": "https://example.test/public/adult.tab",
                    "verified_at": "2026-09-03",
                    "hash_source": "chronicle_fetch",
                    "attested_by": "chronicle",
                    "licence_evidence": _public_licence_evidence("b" * 64),
                }
            ],
            2023: [
                {
                    "filename": "adult.tab",
                    "access": "licensed",
                    "licence": "UK Data Service End User Licence",
                    "vintage": "2023_24",
                    "sha256": FIXTURE_SHA,
                    "doi": "10.5255/UKDA-SN-9367-2",
                    "verified_at": "2026-09-03",
                    "hash_source": "consumer_attested",
                    "attested_by": "PolicyEngine/microcosm",
                    "attestation_evidence": "https://example.test/hash-attestation",
                }
            ],
        },
    }


def _register(output_dir: Path, **overrides: object) -> object:
    """Register a fixture licensed artifact, with per-test overrides."""
    kwargs: dict[str, object] = {
        "source_id": "dwp",
        "package_id": "dwp-frs-2023-24",
        "year": 2023,
        "output_dir": output_dir,
        "filename": "adult.tab",
        "sha256": FIXTURE_SHA,
        "licence": "UK Data Service End User Licence",
        "access": "licensed",
        "vintage": "2023_24",
        "size_bytes": 35323384,
        "publisher": "Department for Work and Pensions",
        "doi": "10.5255/UKDA-SN-9367-2",
        "verified_at": "2026-09-02",
        "hash_source": "consumer_pin",
        "attested_by": "PolicyEngine/microcosm",
        "pinned_from": {
            "repository": "PolicyEngine/microcosm",
            "path": "packages/microcosm-build/src/microcosm/build/uk/source_stages.json",
            "commit": "1234567890abcdef1234567890abcdef12345678",
        },
    }
    kwargs.update(overrides)
    return register_hash_only_artifact(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Access and kind vocabularies
# --------------------------------------------------------------------------


def test_access_classes_are_the_closed_contract_set():
    assert ACCESS_CLASSES == ("public", "licensed", "restricted")


def test_hash_sources_are_the_closed_contract_set():
    assert HASH_SOURCES == (
        "chronicle_fetch",
        "consumer_attested",
        "consumer_pin",
    )
    assert normalize_hash_source("consumer_pin") == "consumer_pin"
    with pytest.raises(ManifestAccessError, match="Unknown hash_source"):
        normalize_hash_source("copied_from_somewhere")


def test_redistributable_microdata_licences_have_authoritative_evidence():
    assert {
        "US-GOVERNMENT-WORK-17-USC-105",
        "OGL-3.0",
        "CC0-1.0",
        "CC-BY-4.0",
        "U.S. Government work / 17 U.S.C. §105",
        "UK Open Government Licence v3",
        "CC0 1.0",
        "CC BY 4.0",
    } <= set(REDISTRIBUTABLE_MICRODATA_LICENCES)
    for licence in REDISTRIBUTABLE_MICRODATA_LICENCES.values():
        assert licence.basis
        assert licence.evidence_url.startswith("https://")


@pytest.mark.parametrize("access", ACCESS_CLASSES)
def test_only_public_access_may_carry_bytes(access):
    assert stores_bytes(access) is (access == "public")
    assert is_hash_only(access) is (access != "public")


def test_absent_access_is_inferred_public():
    assert normalize_access(None) == "public"
    assert entry_access({"filename": "table.xlsx"}) == "public"


def test_unknown_access_class_is_refused():
    with pytest.raises(ManifestAccessError, match="Unknown access class"):
        normalize_access("internal")


def test_unknown_access_class_falls_back_to_restricted_not_public():
    # Never upload bytes because a class failed to parse.
    assert safe_entry_access({"access": "internal"}) == "restricted"


def test_manifest_kind_defaults_to_publisher_table_and_rejects_unknown():
    assert manifest_kind(None) == "publisher_table"
    assert manifest_kind({}) == "publisher_table"
    assert is_microdata_release({"kind": "microdata_release"}) is True
    with pytest.raises(ManifestAccessError, match="Unknown manifest kind"):
        manifest_kind({"kind": "microdata_rows"})


def test_registration_identity_is_the_contract_tuple():
    assert (
        registration_id(
            source_id="dwp",
            package_id="dwp-frs-2023-24",
            year=2023,
            sha256=FIXTURE_SHA,
            filename="adult.tab",
        )
        == f"dwp/dwp-frs-2023-24/2023/{FIXTURE_SHA}/adult.tab"
    )


# --------------------------------------------------------------------------
# Manifest entry validation
# --------------------------------------------------------------------------


def test_microdata_release_entry_requires_access_and_licence():
    errors = validate_file_entry(
        {"filename": "adult.tab", "sha256": FIXTURE_SHA},
        kind="microdata_release",
        manifest={},
        local_file_exists=False,
    )

    assert "missing_access" in errors
    assert "missing_licence" in errors


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ({"licence": None}, "missing_licence"),
        ({"sha256": None}, "missing_sha256"),
        ({"sha256": "not-a-checksum"}, "malformed_sha256"),
        ({"sha256": FIXTURE_SHA.upper()}, "malformed_sha256"),
        ({"vintage": None}, "missing_vintage"),
        ({"doi": None}, "missing_access_route"),
        ({"verified_at": None}, "missing_verified_at"),
        ({"hash_source": None}, "missing_hash_source"),
        ({"attested_by": None}, "missing_attested_by"),
    ],
)
def test_hash_only_entry_reports_each_missing_field(mutation, expected_code):
    entry = {
        "filename": "adult.tab",
        "access": "licensed",
        "licence": "UK Data Service End User Licence",
        "vintage": "2023_24",
        "sha256": FIXTURE_SHA,
        "doi": "10.5255/UKDA-SN-9367-2",
        "verified_at": "2026-09-02",
        "hash_source": "consumer_attested",
        "attested_by": "PolicyEngine/microcosm",
        "attestation_evidence": "https://example.test/hash-attestation",
    }
    entry.update(mutation)
    entry = {key: value for key, value in entry.items() if value is not None}

    errors = validate_file_entry(
        entry,
        kind="microdata_release",
        manifest={},
        local_file_exists=False,
    )

    assert expected_code in errors


def test_hash_only_entry_flags_bytes_and_r2_locations():
    entry = {
        "filename": "adult.tab",
        "access": "restricted",
        "licence": "Statbel/Eurostat scientific-use",
        "vintage": "2023",
        "sha256": FIXTURE_SHA,
        "doi": "10.5255/UKDA-SN-9422-1",
        "verified_at": "2026-09-02",
        "hash_source": "consumer_attested",
        "attested_by": "PolicyEngine/microcosm",
        "attestation_evidence": "https://example.test/hash-attestation",
        "storage": {"r2": {"bucket": "ledger-raw", "key": "raw/x/y/z"}},
    }

    errors = validate_file_entry(
        entry,
        kind="microdata_release",
        manifest={},
        local_file_exists=True,
    )

    assert "bytes_present_for_hash_only_entry" in errors
    assert "r2_location_for_hash_only_entry" in errors


def test_public_entry_needs_no_licence_or_access_route():
    assert (
        validate_file_entry(
            {"filename": "table.xlsx", "sha256": FIXTURE_SHA},
            kind="publisher_table",
            manifest={},
            local_file_exists=True,
        )
        == ()
    )


def test_non_allowlisted_public_microdata_is_treated_as_hash_only():
    entry = {
        "filename": "adult.tab",
        "access": "public",
        "licence": "downloadable public-use data",
        "vintage": "2024_25",
        "sha256": FIXTURE_SHA,
        "source_url": "https://example.test/adult.tab",
        "hash_source": "chronicle_fetch",
        "attested_by": "chronicle",
        "verified_at": "2026-09-03",
    }

    errors = validate_file_entry(
        entry,
        kind="microdata_release",
        manifest={},
        local_file_exists=False,
    )

    assert effective_entry_access(entry, kind="microdata_release") == "licensed"
    assert "public_microdata_licence_not_allowlisted" in errors


def test_public_microdata_entry_requires_artifact_bound_licence_evidence():
    entry = {
        "filename": "adult.tab",
        "access": "public",
        "licence": "US-GOVERNMENT-WORK-17-USC-105",
        "vintage": "2024",
        "sha256": FIXTURE_SHA,
        "source_url": "https://example.test/adult.tab",
        "hash_source": "chronicle_fetch",
        "attested_by": "chronicle",
        "verified_at": "2026-09-03",
    }

    assert "missing_licence_evidence" in validate_file_entry(
        entry,
        kind="microdata_release",
        manifest={},
        local_file_exists=False,
    )


def test_public_microdata_entry_rejects_evidence_for_other_bytes():
    entry = {
        "filename": "adult.tab",
        "access": "public",
        "licence": "US-GOVERNMENT-WORK-17-USC-105",
        "vintage": "2024",
        "sha256": FIXTURE_SHA,
        "source_url": "https://example.test/adult.tab",
        "hash_source": "chronicle_fetch",
        "attested_by": "chronicle",
        "verified_at": "2026-09-03",
        "licence_evidence": _public_licence_evidence("b" * 64),
    }

    assert "licence_evidence_sha256_mismatch" in validate_file_entry(
        entry,
        kind="microdata_release",
        manifest={},
        local_file_exists=False,
    )


@pytest.mark.parametrize(
    "filename",
    ["./adult.tab", "../adult.tab", "a/adult.tab", r"a\adult.tab", ".", ".."],
)
def test_artifact_filename_must_be_a_bare_basename(filename):
    with pytest.raises(ManifestAccessError, match="bare basename"):
        canonical_artifact_filename(filename)


def test_manifest_filename_alias_collisions_are_global():
    errors = manifest_filename_errors(
        {
            "files": {
                2023: {"filename": "adult.tab"},
                "2024": {"filename": "./adult.tab"},
            }
        }
    )

    assert any(error.startswith("filename_not_bare_basename") for error in errors)
    assert any(error.startswith("filename_alias_collision") for error in errors)


def test_conflicting_public_and_gated_exact_basenames_are_a_collision():
    errors = manifest_filename_errors(_public_gated_duplicate_manifest())

    assert any(
        error.startswith("filename_identity_collision:adult.tab") for error in errors
    )


def test_manifest_filename_collisions_compare_every_prior_reissue():
    manifest = {
        "kind": "microdata_release",
        "files": {
            2024: [
                {
                    "filename": "adult.tab",
                    "access": "licensed",
                    "sha256": "a" * 64,
                },
                {
                    "filename": "adult.tab",
                    "access": "licensed",
                    "sha256": "b" * 64,
                },
                {
                    "filename": "adult.tab",
                    "access": "licensed",
                    "sha256": "b" * 64,
                },
            ]
        },
    }

    assert any(
        error.startswith("filename_identity_collision:adult.tab")
        for error in manifest_filename_errors(manifest)
    )


def test_numeric_and_string_year_keys_resolve_to_the_existing_key():
    assert resolve_year_key({"2023": {}}, 2023) == "2023"
    assert resolve_year_key({2023: {}}, "2023") == 2023
    with pytest.raises(ManifestAccessError, match="parallel year keys"):
        resolve_year_key({2023: {}, "2023": {}}, 2023)


def test_list_file_spec_expands_only_for_a_microdata_release():
    specs = [{"filename": "adult.tab"}, {"filename": "child.tab"}]

    assert iter_file_specs(specs, kind="microdata_release") == tuple(specs)

    rejected = iter_file_specs(specs, kind="publisher_table")
    assert len(rejected) == 1
    assert isinstance(rejected[0], ListSpecRejected)
    assert validate_file_entry(
        rejected[0],
        kind="publisher_table",
        manifest={},
        local_file_exists=False,
    ) == ("list_file_spec_requires_microdata_release_kind",)


# --------------------------------------------------------------------------
# register-artifact
# --------------------------------------------------------------------------


def test_register_writes_identity_without_bytes_or_an_r2_key(tmp_path):
    output_dir = tmp_path / "dwp" / "frs_2023_24"

    report = _register(output_dir)

    manifest = yaml.safe_load((output_dir / "manifest.yaml").read_text())
    entry = manifest["files"][2023][0]

    assert report.valid
    assert report.registration == (f"dwp/dwp-frs-2023-24/2023/{FIXTURE_SHA}/adult.tab")
    assert report.to_dict()["r2_location"] is None
    assert manifest["kind"] == "microdata_release"
    assert entry["access"] == "licensed"
    assert entry["licence"] == "UK Data Service End User Licence"
    assert entry["sha256"] == FIXTURE_SHA
    assert entry["size_bytes"] == 35323384
    assert entry["vintage"] == "2023_24"
    assert entry["verified_at"] == "2026-09-02"
    assert entry["hash_source"] == "consumer_pin"
    assert entry["attested_by"] == "PolicyEngine/microcosm"
    assert entry["pinned_from"]["commit"] == "1234567890abcdef1234567890abcdef12345678"
    assert "storage" not in entry
    # The only file the registration creates is the manifest itself.
    assert sorted(p.name for p in output_dir.iterdir()) == ["manifest.yaml"]


def test_register_refuses_public_access(tmp_path):
    with pytest.raises(HashOnlyRegistrationError, match="refuses access='public'"):
        _register(tmp_path / "pkg", access="public")


@pytest.mark.parametrize("sha256", ["", "abc123", FIXTURE_SHA.upper(), "z" * 64])
def test_register_never_invents_a_hash(tmp_path, sha256):
    with pytest.raises(HashOnlyRegistrationError, match="Never invent a hash"):
        _register(tmp_path / "pkg", sha256=sha256)
    assert not (tmp_path / "pkg").exists()


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"licence": ""}, "must record the publisher licence"),
        ({"vintage": ""}, "must record the artifact vintage"),
        ({"verified_at": None}, "must record when the checksum was"),
        ({"hash_source": None}, "Unknown hash_source"),
        ({"attested_by": None}, "identify its attester"),
        ({"pinned_from": None}, "requires pinned_from"),
        ({"filename": "../adult.tab"}, "must be a bare filename"),
    ],
)
def test_register_refuses_an_incomplete_registration(tmp_path, overrides, expected):
    with pytest.raises(HashOnlyRegistrationError, match=expected):
        _register(tmp_path / "pkg", **overrides)


def test_register_refuses_without_an_access_route(tmp_path):
    with pytest.raises(HashOnlyRegistrationError, match="how the bytes are reached"):
        _register(tmp_path / "pkg", doi=None)


def test_register_refuses_to_claim_chronicle_fetch_attestation(tmp_path):
    output_dir = tmp_path / "pkg"

    with pytest.raises(HashOnlyRegistrationError, match="chronicle_fetch"):
        _register(
            output_dir,
            hash_source="chronicle_fetch",
            attested_by="chronicle",
            pinned_from=None,
        )

    assert not output_dir.exists()


def test_register_refuses_while_the_bytes_are_present(tmp_path):
    output_dir = tmp_path / "pkg"
    output_dir.mkdir()
    (output_dir / "adult.tab").write_bytes(b"licensed microdata must not live here")

    with pytest.raises(HashOnlyRegistrationError, match="while its bytes"):
        _register(output_dir)


def test_register_refuses_public_to_restricted_while_r2_is_recorded(tmp_path):
    output_dir = tmp_path / "pkg"
    output_dir.mkdir()
    manifest_path = output_dir / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "source_id": "dwp",
                "package_id": "dwp-frs-2023-24",
                "kind": "microdata_release",
                "source_page": "https://example.test/release",
                "files": {
                    2023: [
                        {
                            "filename": "adult.tab",
                            "access": "public",
                            "licence": "OGL-3.0",
                            "vintage": "2023_24",
                            "sha256": FIXTURE_SHA,
                            "hash_source": "chronicle_fetch",
                            "attested_by": "chronicle",
                            "verified_at": "2026-09-02",
                            "licence_evidence": _public_licence_evidence(
                                FIXTURE_SHA,
                                licence_id="OGL-3.0",
                            ),
                            "storage": {
                                "r2": {"key": f"raw/x/{FIXTURE_SHA}/adult.tab"}
                            },
                        }
                    ]
                },
            }
        )
    )
    before = manifest_path.read_bytes()

    with pytest.raises(HashOnlyRegistrationError, match="still records storage.r2"):
        _register(
            output_dir,
            supersede_public_object="https://example.test/removal/123",
        )

    assert manifest_path.read_bytes() == before


def test_register_requires_evidence_for_public_to_restricted_reclassification(
    tmp_path,
):
    output_dir = tmp_path / "pkg"
    output_dir.mkdir()
    manifest_path = output_dir / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "source_id": "dwp",
                "package_id": "dwp-frs-2023-24",
                "kind": "microdata_release",
                "source_page": "https://example.test/release",
                "files": {
                    2023: [
                        {
                            "filename": "adult.tab",
                            "access": "public",
                            "licence": "OGL-3.0",
                            "vintage": "2023_24",
                            "sha256": FIXTURE_SHA,
                            "hash_source": "chronicle_fetch",
                            "attested_by": "chronicle",
                            "verified_at": "2026-09-02",
                            "licence_evidence": _public_licence_evidence(
                                FIXTURE_SHA,
                                licence_id="OGL-3.0",
                            ),
                        }
                    ]
                },
            }
        )
    )
    before = manifest_path.read_bytes()

    with pytest.raises(HashOnlyRegistrationError, match="silently reclassify"):
        _register(output_dir)

    assert manifest_path.read_bytes() == before
    _register(
        output_dir,
        supersede_public_object="https://example.test/removal/123",
    )
    entry = yaml.safe_load(manifest_path.read_text())["files"][2023][0]
    assert entry["access"] == "licensed"
    assert entry["public_object_removal_evidence"] == (
        "https://example.test/removal/123"
    )


def test_register_reuses_a_quoted_year_key(tmp_path):
    output_dir = tmp_path / "pkg"
    output_dir.mkdir()
    (output_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "source_id": "dwp",
                "package_id": "dwp-frs-2023-24",
                "kind": "microdata_release",
                "files": {"2023": []},
            }
        )
    )

    _register(output_dir)

    files = yaml.safe_load((output_dir / "manifest.yaml").read_text())["files"]
    assert list(files) == ["2023"]


def test_register_refuses_a_publisher_table_manifest(tmp_path):
    output_dir = tmp_path / "pkg"
    output_dir.mkdir()
    (output_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "source_id": "dwp",
                "package_id": "dwp-frs-2023-24",
                "kind": "publisher_table",
                "files": {2023: {"filename": "table.ods", "sha256": FIXTURE_SHA}},
            }
        )
    )

    with pytest.raises(HashOnlyRegistrationError, match="is a publisher_table"):
        _register(output_dir)


def test_register_refuses_a_manifest_for_a_different_source(tmp_path):
    output_dir = tmp_path / "pkg"
    _register(output_dir)

    with pytest.raises(HashOnlyRegistrationError, match="declares source_id='dwp'"):
        _register(output_dir, source_id="hmrc")


def test_register_refuses_conflicting_exact_basename_identities(tmp_path):
    output_dir = tmp_path / "pkg"
    output_dir.mkdir()
    manifest_path = output_dir / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(_public_gated_duplicate_manifest()))
    before = manifest_path.read_bytes()

    with pytest.raises(
        HashOnlyRegistrationError,
        match="filename_identity_collision:adult.tab",
    ):
        _register(output_dir)

    assert manifest_path.read_bytes() == before


def test_register_is_idempotent_and_byte_stable(tmp_path):
    output_dir = tmp_path / "pkg"

    _register(output_dir)
    first = (output_dir / "manifest.yaml").read_bytes()
    report = _register(output_dir)

    assert report.replaced is True
    assert (output_dir / "manifest.yaml").read_bytes() == first


def test_register_refuses_a_reissue_unless_it_is_asked_for(tmp_path):
    output_dir = tmp_path / "pkg"
    _register(output_dir)

    with pytest.raises(HashOnlyRegistrationError, match="already registers"):
        _register(output_dir, sha256="b" * 64)

    _register(output_dir, sha256="b" * 64, allow_reissue=True)
    manifest = yaml.safe_load((output_dir / "manifest.yaml").read_text())
    entries = manifest["files"][2023]

    # A reissue is a new publisher release, so both registrations survive.
    assert [entry["sha256"] for entry in entries] == [FIXTURE_SHA, "b" * 64]


def test_register_keeps_distinct_files_under_one_vintage(tmp_path):
    output_dir = tmp_path / "pkg"

    _register(output_dir, filename="adult.tab", sha256=FIXTURE_SHA)
    _register(output_dir, filename="child.tab", sha256="c" * 64)

    manifest = yaml.safe_load((output_dir / "manifest.yaml").read_text())

    assert [entry["filename"] for entry in manifest["files"][2023]] == [
        "adult.tab",
        "child.tab",
    ]


# --------------------------------------------------------------------------
# fetch-artifact
# --------------------------------------------------------------------------


def test_fetch_refuses_a_hash_only_access_class(tmp_path, monkeypatch):
    source = tmp_path / "adult.tab"
    source.write_bytes(b"licensed microdata")

    def unexpected_read(_source_url):
        raise AssertionError("A refused access class must not read the artifact")

    monkeypatch.setattr("chronicle.artifacts._read_artifact", unexpected_read)

    with pytest.raises(ManifestAccessError, match="fetch-artifact stores bytes"):
        fetch_source_artifact(
            str(source),
            source_id="dwp",
            package_id="dwp-frs-2023-24",
            year=2023,
            output_dir=tmp_path / "pkg",
            kind="microdata_release",
            access="licensed",
        )


def test_fetch_microdata_requires_explicit_access_before_io(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(ManifestAccessError, match="--access"):
        fetch_source_artifact(
            "https://example.test/adult.tab",
            source_id="publisher",
            package_id="release",
            year=2024,
            output_dir=tmp_path / "pkg",
            kind="microdata_release",
            licence="US-GOVERNMENT-WORK-17-USC-105",
            publisher="Fixture publisher",
            vintage="2024",
            expected_sha256=FIXTURE_SHA,
            licence_evidence=_public_licence_evidence(FIXTURE_SHA),
        )

    assert not (tmp_path / "pkg").exists()


@pytest.mark.parametrize(
    ("field", "recorded", "requested"),
    [
        ("source_id", "recorded-source", "requested-source"),
        ("package_id", "recorded-package", "requested-package"),
    ],
)
def test_fetch_refuses_wrong_manifest_identity_before_any_io(
    tmp_path,
    monkeypatch,
    field,
    recorded,
    requested,
):
    output_dir = tmp_path / "pkg"
    output_dir.mkdir()
    manifest = {
        "source_id": "publisher",
        "package_id": "release",
        "kind": "publisher_table",
        "files": {
            2024: {
                "filename": "table.xlsx",
                "access": "public",
                "sha256": FIXTURE_SHA,
            }
        },
    }
    manifest[field] = recorded
    manifest_path = output_dir / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest))
    before = manifest_path.read_bytes()

    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )
    monkeypatch.setattr(
        "chronicle.artifacts._upload_r2_object",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("must not upload bytes")
        ),
    )
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("must not write bytes")
        ),
    )
    identity = {"source_id": "publisher", "package_id": "release"}
    identity[field] = requested

    with pytest.raises(SourceArtifactManifestError, match=field):
        fetch_source_artifact(
            "https://example.test/table.xlsx",
            source_id=identity["source_id"],
            package_id=identity["package_id"],
            year=2024,
            output_dir=output_dir,
            filename="table.xlsx",
            access="public",
            expected_sha256=FIXTURE_SHA,
            upload_r2=True,
        )

    assert manifest_path.read_bytes() == before
    assert not (output_dir / "table.xlsx").exists()


def test_fetch_refuses_to_pull_bytes_over_a_hash_only_registration(
    tmp_path, monkeypatch
):
    output_dir = tmp_path / "pkg"
    _register(output_dir)
    original_manifest = (output_dir / "manifest.yaml").read_bytes()
    source = tmp_path / "adult.tab"
    source.write_bytes(b"licensed microdata")

    def unexpected_read(_source_url):
        raise AssertionError("A refused fetch must not read the artifact")

    monkeypatch.setattr("chronicle.artifacts._read_artifact", unexpected_read)

    with pytest.raises(ManifestAccessError, match="Its bytes must not enter"):
        fetch_source_artifact(
            str(source),
            source_id="dwp",
            package_id="dwp-frs-2023-24",
            year=2023,
            output_dir=output_dir,
            filename="adult.tab",
            access="public",
        )

    assert not (output_dir / "adult.tab").exists()
    assert (output_dir / "manifest.yaml").read_bytes() == original_manifest


def test_fetch_refuses_filename_alias_before_reading_hash_only_bytes(
    tmp_path, monkeypatch
):
    output_dir = tmp_path / "pkg"
    _register(output_dir)

    def unexpected_read(_source_url):
        raise AssertionError("an unsafe filename must be refused before reading")

    monkeypatch.setattr("chronicle.artifacts._read_artifact", unexpected_read)

    with pytest.raises(ManifestAccessError, match="bare basename"):
        fetch_source_artifact(
            "https://example.test/adult.tab",
            source_id="dwp",
            package_id="dwp-frs-2023-24",
            year=2023,
            output_dir=output_dir,
            filename="./adult.tab",
        )


def test_fetch_infers_url_basename_before_hash_only_guard(tmp_path, monkeypatch):
    output_dir = tmp_path / "pkg"
    _register(output_dir)

    def unexpected_read(_source_url):
        raise AssertionError("the URL basename guard must run before download")

    monkeypatch.setattr("chronicle.artifacts._read_artifact", unexpected_read)

    with pytest.raises(ManifestAccessError, match="Its bytes must not enter"):
        fetch_source_artifact(
            "https://example.test/releases/adult.tab?download=1",
            source_id="dwp",
            package_id="dwp-frs-2023-24",
            year=2023,
            output_dir=output_dir,
        )


def test_fetch_writes_the_access_class_explicitly(tmp_path):
    source = tmp_path / "table.xlsx"
    source.write_bytes(b"publisher table")
    output_dir = tmp_path / "pkg"

    fetch_source_artifact(
        str(source),
        source_id="irs_soi",
        package_id="soi-table-1-2",
        year=2023,
        output_dir=output_dir,
        kind="publisher_table",
    )

    manifest = yaml.safe_load((output_dir / "manifest.yaml").read_text())

    assert manifest["files"][2023]["access"] == "public"


def test_fetch_into_a_microdata_release_manifest_requires_a_licence(
    tmp_path, monkeypatch
):
    output_dir = tmp_path / "pkg"
    _register(output_dir)
    source = tmp_path / "codebook.pdf"
    source.write_bytes(b"public codebook")

    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(
            AssertionError("A refused fetch must not read the artifact")
        ),
    )

    with pytest.raises(ManifestAccessError, match="must record its publisher licence"):
        fetch_source_artifact(
            str(source),
            source_id="dwp",
            package_id="dwp-frs-2023-24",
            year=2023,
            output_dir=output_dir,
            filename="codebook.pdf",
            access="public",
        )


def test_fetch_rejects_a_conflicting_stored_kind_before_io(tmp_path, monkeypatch):
    output_dir = tmp_path / "pkg"
    output_dir.mkdir()
    (output_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "publisher_table",
                "files": {2023: {"filename": "table.xlsx", "sha256": FIXTURE_SHA}},
            }
        )
    )
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(SourceArtifactManifestError, match="would reclassify"):
        fetch_source_artifact(
            "https://example.test/table.xlsx",
            source_id="publisher",
            package_id="release",
            year=2023,
            output_dir=output_dir,
            kind="microdata_release",
        )


def test_fetch_rejects_microdata_missing_access_before_io(tmp_path, monkeypatch):
    output_dir = _licensed_manifest(
        tmp_path / "pkg",
        {
            "kind": "microdata_release",
            "files": {
                2023: [
                    {
                        "filename": "adult.tab",
                        "licence": "OGL-3.0",
                        "vintage": "2023",
                        "sha256": FIXTURE_SHA,
                        "source_url": "https://example.test/adult.tab",
                        "verified_at": "2026-09-03",
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(SourceArtifactManifestError, match="missing_access"):
        fetch_source_artifact(
            "https://example.test/other.tab",
            source_id="publisher",
            package_id="release",
            year=2023,
            output_dir=output_dir,
            filename="other.tab",
        )


def test_fetch_refuses_non_allowlisted_public_microdata_before_io(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(ManifestAccessError, match="non-allowlisted"):
        fetch_source_artifact(
            "https://example.test/adult.tab",
            source_id="publisher",
            package_id="release",
            year=2024,
            output_dir=tmp_path / "pkg",
            kind="microdata_release",
            access="public",
            licence="downloadable on a public website",
            publisher="Example publisher",
            vintage="2024",
        )


def test_fetch_refuses_public_microdata_without_licence_evidence_before_io(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(ManifestAccessError, match="licence_evidence"):
        fetch_source_artifact(
            "https://example.test/adult.tab",
            source_id="publisher",
            package_id="release",
            year=2024,
            output_dir=tmp_path / "pkg",
            kind="microdata_release",
            access="public",
            licence="US-GOVERNMENT-WORK-17-USC-105",
            publisher="Fixture publisher",
            vintage="2024",
            expected_sha256=FIXTURE_SHA,
        )

    assert not (tmp_path / "pkg").exists()


def test_fetch_refuses_licence_evidence_for_a_different_sha_before_io(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(
        ManifestAccessError,
        match="licence_evidence_sha256_mismatch|covered_sha256|covered SHA-256",
    ):
        fetch_source_artifact(
            "https://example.test/adult.tab",
            source_id="publisher",
            package_id="release",
            year=2024,
            output_dir=tmp_path / "pkg",
            kind="microdata_release",
            access="public",
            licence="US-GOVERNMENT-WORK-17-USC-105",
            publisher="Fixture publisher",
            vintage="2024",
            expected_sha256=FIXTURE_SHA,
            licence_evidence=_public_licence_evidence("b" * 64),
        )

    assert not (tmp_path / "pkg").exists()


@pytest.mark.parametrize(
    ("upload_r2", "staging_dir", "message"),
    [
        (False, "staging", "--upload-r2"),
        (True, None, "--staging-dir"),
    ],
)
def test_public_microdata_requires_transient_r2_staging_before_io(
    tmp_path,
    monkeypatch,
    upload_r2,
    staging_dir,
    message,
):
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(ManifestAccessError, match=message):
        fetch_source_artifact(
            "https://example.test/adult.tab",
            source_id="publisher",
            package_id="release",
            year=2024,
            output_dir=tmp_path / "pkg",
            kind="microdata_release",
            access="public",
            licence="US-GOVERNMENT-WORK-17-USC-105",
            publisher="Fixture publisher",
            vintage="2024",
            expected_sha256=FIXTURE_SHA,
            licence_evidence=_public_licence_evidence(FIXTURE_SHA),
            upload_r2=upload_r2,
            staging_dir=(tmp_path / staging_dir if staging_dir else None),
        )


def test_failed_public_microdata_upload_leaves_no_payload_or_manifest(
    tmp_path, monkeypatch
):
    source = tmp_path / "publisher" / "adult.tab"
    source.parent.mkdir()
    source.write_bytes(b"public microdata")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    staged_paths: list[Path] = []

    def failed_upload(_location, local_path, **_kwargs):
        staged_paths.append(local_path)
        assert local_path.exists()
        return ArtifactCommandResult(
            command=("wrangler", "r2", "object", "put"),
            returncode=1,
            stdout="",
            stderr="not authenticated",
        )

    monkeypatch.setattr("chronicle.artifacts._upload_r2_object", failed_upload)
    output_dir = tmp_path / "db" / "data" / "publisher" / "release"

    report = fetch_source_artifact(
        str(source),
        source_id="publisher",
        package_id="release",
        year=2024,
        output_dir=output_dir,
        kind="microdata_release",
        access="public",
        licence="US-GOVERNMENT-WORK-17-USC-105",
        publisher="Fixture publisher",
        vintage="2024",
        expected_sha256=checksum,
        licence_evidence=_public_licence_evidence(checksum),
        upload_r2=True,
        staging_dir=tmp_path / "staging",
    )

    assert report.errors == ("r2_upload_failed",)
    assert staged_paths and all(not path.exists() for path in staged_paths)
    assert not output_dir.exists()


def test_public_microdata_staging_must_be_outside_db_data_before_io(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(ManifestAccessError, match="outside db/data"):
        fetch_source_artifact(
            "https://example.test/adult.tab",
            source_id="publisher",
            package_id="release",
            year=2024,
            output_dir=tmp_path / "db" / "data" / "publisher" / "release",
            kind="microdata_release",
            access="public",
            licence="US-GOVERNMENT-WORK-17-USC-105",
            publisher="Fixture publisher",
            vintage="2024",
            expected_sha256=FIXTURE_SHA,
            licence_evidence=_public_licence_evidence(FIXTURE_SHA),
            upload_r2=True,
            staging_dir=tmp_path / "db" / "data" / ".staging",
        )


def test_fetch_refuses_conflicting_exact_basename_identities_before_io(
    tmp_path, monkeypatch
):
    output_dir = tmp_path / "pkg"
    output_dir.mkdir()
    manifest_path = output_dir / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(_public_gated_duplicate_manifest()))
    before = manifest_path.read_bytes()
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(
        SourceArtifactManifestError,
        match="filename_identity_collision:adult.tab",
    ):
        fetch_source_artifact(
            "https://example.test/other.tab",
            source_id="dwp",
            package_id="dwp-frs-2023-24",
            year=2023,
            output_dir=output_dir,
            filename="other.tab",
            access="public",
        )

    assert manifest_path.read_bytes() == before
    assert not (output_dir / "other.tab").exists()


def test_expected_pin_mismatch_never_overwrites_existing_bytes(tmp_path):
    publisher_file = tmp_path / "publisher" / "table.xlsx"
    publisher_file.parent.mkdir()
    publisher_file.write_bytes(b"reviewed bytes")
    output_dir = tmp_path / "pkg"
    fetch_source_artifact(
        str(publisher_file),
        source_id="publisher",
        package_id="table",
        year=2023,
        output_dir=output_dir,
        kind="publisher_table",
    )
    manifest_before = (output_dir / "manifest.yaml").read_bytes()
    local_before = (output_dir / "table.xlsx").read_bytes()
    publisher_file.write_bytes(b"changed bytes")

    with pytest.raises(SourceArtifactManifestError, match="checksum mismatch"):
        fetch_source_artifact(
            str(publisher_file),
            source_id="publisher",
            package_id="table",
            year=2023,
            output_dir=output_dir,
            kind="publisher_table",
            expected_sha256=hashlib.sha256(b"reviewed bytes").hexdigest(),
            expected_size_bytes=len(b"reviewed bytes"),
        )

    assert (output_dir / "table.xlsx").read_bytes() == local_before
    assert (output_dir / "manifest.yaml").read_bytes() == manifest_before


# --------------------------------------------------------------------------
# publish-raw
# --------------------------------------------------------------------------


def _hash_only_tree(tmp_path: Path) -> Path:
    """Build a data root holding one hash-only registration."""
    root = tmp_path / "data"
    _register(root / "dwp" / "frs_2023_24")
    return root


def test_publish_raw_refuses_hash_only_entries_without_reading_bytes(
    tmp_path, monkeypatch
):
    root = _hash_only_tree(tmp_path)

    def unexpected_upload(*args, **kwargs):
        raise AssertionError("A hash-only registration must never be uploaded")

    monkeypatch.setattr("chronicle.artifacts._upload_r2_object", unexpected_upload)

    report = publish_source_artifacts(root)

    assert not report.valid
    assert report.counts["hash_only_refused_count"] == 1
    assert report.counts["uploaded_count"] == 0
    entry = report.entries[0]
    assert entry.skipped == "hash_only_access:licensed"
    assert entry.errors == ("hash_only_access_refuses_bytes:licensed",)
    assert entry.r2_location is None
    assert entry.upload is None


def test_publish_raw_skip_hash_only_reports_the_skip_without_failing(
    tmp_path, monkeypatch
):
    root = _hash_only_tree(tmp_path)
    monkeypatch.setattr(
        "chronicle.artifacts._upload_r2_object",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("A hash-only registration must never be uploaded")
        ),
    )

    report = publish_source_artifacts(root, skip_hash_only=True)

    assert report.valid
    assert report.counts["hash_only_refused_count"] == 1
    assert report.counts["uploaded_count"] == 0
    assert report.entries[0].errors == ()
    assert report.entries[0].r2_location is None


def test_publish_raw_leaves_the_manifest_untouched(tmp_path, monkeypatch):
    root = _hash_only_tree(tmp_path)
    manifest_path = root / "dwp" / "frs_2023_24" / "manifest.yaml"
    original = manifest_path.read_bytes()
    monkeypatch.setattr(
        "chronicle.artifacts._upload_r2_object",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no upload")),
    )

    publish_source_artifacts(root, skip_hash_only=True)

    assert manifest_path.read_bytes() == original


def test_publish_refuses_manifest_wide_filename_alias_collision(tmp_path, monkeypatch):
    package = tmp_path / "data" / "dwp" / "release"
    package.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "source_id": "dwp",
                "package_id": "dwp-release",
                "kind": "microdata_release",
                "files": {
                    2023: [
                        {"filename": "adult.tab"},
                        {"filename": "./adult.tab"},
                    ]
                },
            }
        )
    )
    monkeypatch.setattr(
        "chronicle.artifacts._upload_r2_object",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("an aliased filename must be refused before upload")
        ),
    )

    report = publish_source_artifacts(tmp_path / "data")

    assert not report.valid
    assert report.entries == ()
    assert "filename_alias_collision:adult.tab" in report.errors[0]


def test_publish_refuses_conflicting_exact_basename_identities(tmp_path, monkeypatch):
    package = tmp_path / "data" / "dwp" / "release"
    package.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        yaml.safe_dump(_public_gated_duplicate_manifest())
    )
    monkeypatch.setattr(
        "chronicle.artifacts._upload_r2_object",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("conflicting identities must be refused before upload")
        ),
    )

    report = publish_source_artifacts(tmp_path / "data")

    assert not report.valid
    assert report.entries == ()
    assert any(
        "filename_identity_collision:adult.tab" in error for error in report.errors
    )


def test_publish_refuses_non_allowlisted_public_microdata_without_reading(
    tmp_path, monkeypatch
):
    package = tmp_path / "data" / "dwp" / "release"
    package.mkdir(parents=True)
    artifact = package / "adult.tab"
    artifact.write_bytes(b"gated bytes")
    (package / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "source_id": "dwp",
                "package_id": "dwp-release",
                "kind": "microdata_release",
                "source_page": "https://example.test/release",
                "files": {
                    2023: [
                        {
                            "filename": "adult.tab",
                            "access": "public",
                            "licence": "Custom publisher terms",
                            "vintage": "2023_24",
                            "sha256": FIXTURE_SHA,
                            "verified_at": "2026-09-03",
                            "hash_source": "consumer_attested",
                            "attested_by": "publisher",
                            "attestation_evidence": "https://example.test/hash",
                        }
                    ]
                },
            }
        )
    )
    original_read_bytes = Path.read_bytes

    def refuse_artifact_read(path):
        if path == artifact:
            raise AssertionError("non-allowlisted microdata must remain unread")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", refuse_artifact_read)
    monkeypatch.setattr(
        "chronicle.artifacts._upload_r2_object",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("non-allowlisted microdata must not be uploaded")
        ),
    )

    report = publish_source_artifacts(tmp_path / "data")

    assert not report.valid
    assert report.entries == ()
    assert "adult.tab" in repr(report.errors)
    assert "manifest-only" in repr(report.errors)


# --------------------------------------------------------------------------
# inventory-artifacts
# --------------------------------------------------------------------------


def test_inventory_accepts_a_hash_only_entry_with_no_local_file(tmp_path):
    root = _hash_only_tree(tmp_path)

    report = inventory_source_artifacts(root)

    assert report.valid
    assert report.counts["hash_only_count"] == 1
    assert report.counts["missing_count"] == 0
    entry = report.entries[0]
    assert entry.valid
    assert entry.exists is False
    assert entry.hash_only is True
    assert entry.access == "licensed"
    assert entry.licence == "UK Data Service End User Licence"
    assert entry.sha256_expected == FIXTURE_SHA
    assert entry.sha256_actual is None
    # The size is the publisher's, recorded rather than measured.
    assert entry.size_bytes == 35323384


def test_inventory_flags_a_hash_only_entry_whose_bytes_are_present(tmp_path):
    root = _hash_only_tree(tmp_path)
    (root / "dwp" / "frs_2023_24" / "adult.tab").write_bytes(b"leaked bytes")

    report = inventory_source_artifacts(root)

    assert not report.valid
    assert report.entries == ()
    assert "adult.tab" in repr(report.errors)
    assert "manifest-only" in repr(report.errors)


def test_inventory_rejects_a_list_entry_outside_a_microdata_release(tmp_path):
    package = tmp_path / "data" / "dwp" / "tables"
    package.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "source_id": "dwp",
                "package_id": "dwp-tables",
                "kind": "publisher_table",
                "files": {2023: [{"filename": "a.ods", "sha256": FIXTURE_SHA}]},
            }
        )
    )

    report = inventory_source_artifacts(tmp_path / "data")

    assert not report.valid
    assert report.entries[0].errors == (
        "list_file_spec_requires_microdata_release_kind",
    )


# --------------------------------------------------------------------------
# Source packages never parse a microdata release
# --------------------------------------------------------------------------


def test_source_artifact_spec_refuses_to_parse_a_registered_release():
    spec = SourceArtifactSpec(
        source_name="dwp",
        source_table="Family Resources Survey 2024-25",
        resource_package="db",
        resource_directory="data/dwp/frs_2024_25",
        manifest="manifest.yaml",
        vintage="2024_25",
        extracted_at="2026-09-02",
        extraction_method="none",
        parser="delimited_text_full_rows",
        artifact_year=2024,
    )

    with pytest.raises(MicrodataReleaseNotParseableError, match="registers a"):
        spec.assert_parseable_manifest()
    with pytest.raises(MicrodataReleaseNotParseableError):
        spec._artifact_content(2024)


def test_year_mapping_refuses_a_multi_file_vintage():
    from chronicle.source_package import _year_mapping

    with pytest.raises(ValueError, match="list of 2 entries"):
        _year_mapping(
            {2023: [{"filename": "adult.tab"}, {"filename": "child.tab"}]}, 2023
        )


def test_validate_package_reports_a_microdata_release_carve_out(tmp_path):
    package_dir = tmp_path / "frs_2024_25"
    package_dir.mkdir()
    (package_dir / "source_package.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "ledger.source_package.v1",
                "package_id": "dwp-frs-2024-25-parse-attempt",
                "label": "Attempt to parse a registered microdata release",
                "artifact": {
                    "source_name": "dwp",
                    "source_table": "Family Resources Survey 2024-25",
                    "resource_package": "db",
                    "resource_directory": "data/dwp/frs_2024_25",
                    "manifest": "manifest.yaml",
                    "vintage": "2024_25",
                    "extracted_at": "2026-09-02",
                    "extraction_method": "none",
                    "parser": "delimited_text_full_rows",
                    "artifact_year": 2024,
                },
                "record_sets": [],
            },
            sort_keys=False,
        )
    )

    report = validate_source_package(package_dir, year=2024)

    assert not report.valid
    assert "microdata_release_not_parseable" in {issue.code for issue in report.errors}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_register_artifact_round_trips(tmp_path, capsys):
    output_dir = tmp_path / "pkg"

    exit_code = harness_main(
        [
            "register-artifact",
            "--source-id",
            "hmrc",
            "--package-id",
            "hmrc-spi-public-use-tape-2022-23",
            "--year",
            "2022",
            "--out-dir",
            str(output_dir),
            "--filename",
            "put2223uk.tab",
            "--sha256",
            FIXTURE_SHA,
            "--size-bytes",
            "141323762",
            "--vintage",
            "2022-23",
            "--licence",
            "UK Data Service End User Licence (study SN 9422)",
            "--access",
            "restricted",
            "--publisher",
            "HM Revenue and Customs",
            "--doi",
            "10.5255/UKDA-SN-9422-1",
            "--verified-at",
            "2026-09-02",
            "--hash-source",
            "consumer_pin",
            "--attested-by",
            "PolicyEngine/microcosm",
            "--pinned-from-repository",
            "PolicyEngine/microcosm",
            "--pinned-from-path",
            "packages/microcosm-build/src/microcosm/build/uk/source_stages.json",
            "--pinned-from-commit",
            "1234567890abcdef1234567890abcdef12345678",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["valid"] is True
    assert payload["access"] == "restricted"
    assert payload["r2_location"] is None
    assert payload["registration"] == (
        f"hmrc/hmrc-spi-public-use-tape-2022-23/2022/{FIXTURE_SHA}/put2223uk.tab"
    )
    assert not (output_dir / "put2223uk.tab").exists()


def test_cli_register_artifact_reports_a_clean_refusal(tmp_path, capsys):
    exit_code = harness_main(
        [
            "register-artifact",
            "--source-id",
            "dwp",
            "--package-id",
            "dwp-frs-2024-25",
            "--year",
            "2024",
            "--out-dir",
            str(tmp_path / "pkg"),
            "--filename",
            "adult.tab",
            "--sha256",
            "NOT-A-SHA256",
            "--size-bytes",
            "1",
            "--vintage",
            "2024_25",
            "--licence",
            "UK Data Service End User Licence",
            "--access",
            "licensed",
            "--publisher",
            "Department for Work and Pensions",
            "--doi",
            "10.5255/UKDA-SN-9563-1",
            "--verified-at",
            "2026-09-03",
            "--hash-source",
            "consumer_attested",
            "--attested-by",
            "fixture-consumer",
            "--attestation-evidence",
            "https://example.test/hash-attestation",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert "lowercase 64-character SHA-256" in captured.err
    assert not (tmp_path / "pkg").exists()


def test_cli_register_artifact_updates_the_named_manifest(tmp_path, capsys):
    output_dir = tmp_path / "pkg"
    output_dir.mkdir()
    named_manifest = output_dir / "manifest-release.yaml"
    named_manifest.write_text(
        yaml.safe_dump(
            {
                "source_id": "hmrc",
                "package_id": "hmrc-spi-public-use-tape-2022-23",
                "kind": "microdata_release",
                "files": {"2022": []},
            }
        )
    )

    exit_code = harness_main(
        [
            "register-artifact",
            "--source-id",
            "hmrc",
            "--package-id",
            "hmrc-spi-public-use-tape-2022-23",
            "--year",
            "2022",
            "--out-dir",
            str(output_dir),
            "--manifest",
            "manifest-release.yaml",
            "--filename",
            "put2223uk.tab",
            "--sha256",
            FIXTURE_SHA,
            "--size-bytes",
            "141323762",
            "--vintage",
            "2022-23",
            "--licence",
            "UK Data Service End User Licence (study SN 9422)",
            "--access",
            "restricted",
            "--publisher",
            "HM Revenue and Customs",
            "--doi",
            "10.5255/UKDA-SN-9422-1",
            "--verified-at",
            "2026-09-02",
            "--hash-source",
            "consumer_pin",
            "--attested-by",
            "PolicyEngine/microcosm",
            "--pinned-from-repository",
            "PolicyEngine/microcosm",
            "--pinned-from-path",
            "packages/microcosm-build/src/microcosm/build/uk/source_stages.json",
            "--pinned-from-commit",
            "1234567890abcdef1234567890abcdef12345678",
        ]
    )
    capsys.readouterr()

    assert exit_code == 0
    entries = yaml.safe_load(named_manifest.read_text())["files"]["2022"]
    assert [entry["filename"] for entry in entries] == ["put2223uk.tab"]
    assert not (output_dir / "manifest.yaml").exists()


def test_cli_register_artifact_rejects_public_access(tmp_path):
    with pytest.raises(SystemExit):
        harness_main(
            [
                "register-artifact",
                "--source-id",
                "census_cps",
                "--package-id",
                "census-cps-asec-2023",
                "--year",
                "2023",
                "--out-dir",
                str(tmp_path / "pkg"),
                "--filename",
                "asecpub23csv.zip",
                "--sha256",
                FIXTURE_SHA,
                "--vintage",
                "2023",
                "--licence",
                "Public domain",
                "--access",
                "public",
            ]
        )


# --------------------------------------------------------------------------
# Regressions: the byte boundary must not be escapable
# --------------------------------------------------------------------------


def _licensed_manifest(package: Path, payload: dict) -> Path:
    if payload.get("kind") == "microdata_release":
        payload.setdefault("publisher", "Fixture publisher")
        for year_spec in (payload.get("files") or {}).values():
            entries = year_spec if isinstance(year_spec, list) else [year_spec]
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                entry.setdefault("size_bytes", 1)
                entry.setdefault("hash_source", "consumer_attested")
                entry.setdefault("attested_by", "PolicyEngine/microcosm")
                entry.setdefault(
                    "attestation_evidence",
                    "https://example.test/hash-attestation",
                )
    package.mkdir(parents=True, exist_ok=True)
    (package / "manifest.yaml").write_text(yaml.safe_dump(payload))
    return package


def test_fetch_refuses_a_registration_recorded_under_another_vintage(tmp_path):
    # The write target is a path in the package directory, not a year, so a
    # registration under 2022 must still block a fetch requested for 2023.
    package = _licensed_manifest(
        tmp_path / "pkg",
        {
            "source_id": "dwp",
            "package_id": "dwp-frs-2023-24",
            "kind": "microdata_release",
            "files": {
                2022: [
                    {
                        "filename": "adult.tab",
                        "access": "licensed",
                        "licence": "UK Data Service End User Licence",
                        "vintage": "2022_23",
                        "sha256": FIXTURE_SHA,
                        "doi": "10.5255/UKDA-SN-9367-2",
                        "verified_at": "2026-09-02",
                    }
                ]
            },
        },
    )
    source = tmp_path / "adult.tab"
    source.write_bytes(b"licensed microdata")

    with pytest.raises(ManifestAccessError, match="for 2022 as access='licensed'"):
        fetch_source_artifact(
            str(source),
            source_id="dwp",
            package_id="dwp-frs-2023-24",
            year=2023,
            output_dir=package,
            filename="adult.tab",
            licence="UK Data Service End User Licence",
            access="public",
        )

    assert not (package / "adult.tab").exists()


def test_fetch_refuses_a_list_entry_in_a_publisher_table(tmp_path):
    package = _licensed_manifest(
        tmp_path / "pkg",
        {
            "source_id": "dwp",
            "package_id": "dwp-frs-2023-24",
            "kind": "publisher_table",
            "files": {
                2023: [
                    {
                        "filename": "adult.tab",
                        "access": "licensed",
                        "licence": "UK Data Service End User Licence",
                        "sha256": FIXTURE_SHA,
                    }
                ]
            },
        },
    )
    source = tmp_path / "adult.tab"
    source.write_bytes(b"licensed microdata")

    with pytest.raises(SourceArtifactManifestError, match="requires one mapping"):
        fetch_source_artifact(
            str(source),
            source_id="dwp",
            package_id="dwp-frs-2023-24",
            year=2023,
            output_dir=package,
            filename="adult.tab",
            access="public",
        )

    assert not (package / "adult.tab").exists()


def test_fetch_never_drops_an_existing_registration(tmp_path, monkeypatch):
    output_dir = tmp_path / "pkg"
    _register(output_dir)
    source = tmp_path / "other.csv"
    source.write_bytes(b"a public table in the same package")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "chronicle.artifacts._upload_r2_object",
        _successful_upload,
    )

    fetch_source_artifact(
        str(source),
        source_id="dwp",
        package_id="dwp-frs-2023-24",
        year=2023,
        output_dir=output_dir,
        filename="other.csv",
        licence="OGL-3.0",
        access="public",
        publisher="Department for Work and Pensions",
        vintage="2023_24",
        expected_sha256=source_sha256,
        upload_r2=True,
        staging_dir=tmp_path / "staging",
        licence_evidence=_public_licence_evidence(
            source_sha256,
            licence_id="OGL-3.0",
            issuer="Department for Work and Pensions",
            filename="other.csv",
        ),
    )

    manifest = yaml.safe_load((output_dir / "manifest.yaml").read_text())
    entries = manifest["files"][2023]

    assert [entry["filename"] for entry in entries] == ["adult.tab", "other.csv"]
    assert entries[0]["access"] == "licensed"
    assert entries[0]["sha256"] == FIXTURE_SHA


def test_publish_raw_reports_a_violation_even_when_skipping(tmp_path, monkeypatch):
    root = _hash_only_tree(tmp_path)
    (root / "dwp" / "frs_2023_24" / "adult.tab").write_bytes(b"leaked bytes")
    monkeypatch.setattr(
        "chronicle.artifacts._upload_r2_object",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no upload")),
    )

    report = publish_source_artifacts(root, skip_hash_only=True)

    # --skip-hash-only turns off the refusal, not the contract check.
    assert not report.valid
    assert report.entries == ()
    assert "adult.tab" in repr(report.errors)
    assert "manifest-only" in repr(report.errors)


def test_reregistering_the_current_pin_stays_idempotent_after_a_reissue(tmp_path):
    output_dir = tmp_path / "pkg"
    _register(output_dir, sha256=FIXTURE_SHA)
    _register(output_dir, sha256="b" * 64, allow_reissue=True)

    report = _register(output_dir, sha256="b" * 64)

    assert report.replaced is True
    entries = yaml.safe_load((output_dir / "manifest.yaml").read_text())["files"][2023]
    assert [entry["sha256"] for entry in entries] == [FIXTURE_SHA, "b" * 64]


# --------------------------------------------------------------------------
# Public microdata releases
# --------------------------------------------------------------------------


def test_fetch_can_declare_a_public_microdata_release(tmp_path, monkeypatch):
    output_dir = tmp_path / "pkg"
    household = tmp_path / "csv_hus.zip"
    household.write_bytes(b"public household pums")
    person = tmp_path / "csv_pus.zip"
    person.write_bytes(b"public person pums")
    monkeypatch.setattr(
        "chronicle.artifacts._upload_r2_object",
        _successful_upload,
    )

    for source in (household, person):
        source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        fetch_source_artifact(
            str(source),
            source_id="census_acs",
            package_id="census-acs-pums-2022-1yr",
            year=2022,
            output_dir=output_dir,
            licence="US-GOVERNMENT-WORK-17-USC-105",
            access="public",
            kind="microdata_release",
            publisher="U.S. Census Bureau",
            vintage="2022",
            expected_sha256=source_sha256,
            upload_r2=True,
            staging_dir=tmp_path / "staging",
            licence_evidence=_public_licence_evidence(
                source_sha256,
                issuer="U.S. Census Bureau",
                filename=source.name,
            ),
        )

    manifest = yaml.safe_load((output_dir / "manifest.yaml").read_text())
    entries = manifest["files"][2022]

    # A release may hold several files under one vintage, and both are kept.
    assert manifest["kind"] == "microdata_release"
    assert [entry["filename"] for entry in entries] == ["csv_hus.zip", "csv_pus.zip"]
    assert all(entry["access"] == "public" for entry in entries)
    assert all(entry["licence"] for entry in entries)
    assert all(entry["licence_evidence"] for entry in entries)
    assert not (output_dir / "csv_hus.zip").exists()
    assert not (output_dir / "csv_pus.zip").exists()

    report = inventory_source_artifacts(output_dir)
    assert report.valid
    assert report.counts["hash_only_count"] == 0


def test_fetch_rejects_an_unknown_manifest_kind(tmp_path):
    source = tmp_path / "table.xlsx"
    source.write_bytes(b"publisher table")

    with pytest.raises(SourceArtifactManifestError, match="Unknown manifest kind"):
        fetch_source_artifact(
            str(source),
            source_id="irs_soi",
            package_id="soi-table-1-2",
            year=2023,
            output_dir=tmp_path / "pkg",
            kind="microdata_rows",
        )


# --------------------------------------------------------------------------
# The committed registrations
# --------------------------------------------------------------------------


def _committed_entries(package: Path) -> list[dict]:
    manifest = yaml.safe_load((package / "manifest.yaml").read_text())
    assert manifest["kind"] == "microdata_release"
    return [entry for entries in manifest["files"].values() for entry in entries]


def test_committed_frs_registration_covers_every_pinned_tab():
    entries = _committed_entries(FRS_PACKAGE)

    assert [entry["filename"] for entry in entries] == [
        "accounts.tab",
        "adult.tab",
        "benefits.tab",
        "benunit.tab",
        "child.tab",
        "chldcare.tab",
        "extchild.tab",
        "househol.tab",
        "job.tab",
        "maint.tab",
        "mortgage.tab",
        "oddjob.tab",
        "penprov.tab",
        "pension.tab",
    ]


@pytest.mark.parametrize("package", RELEASE_PACKAGES)
def test_committed_registrations_are_identity_only(package):
    entries = _committed_entries(package)

    assert entries
    for entry in entries:
        assert entry["access"] in {"licensed", "restricted"}
        assert entry["licence"]
        assert entry["vintage"]
        assert entry["verified_at"]
        assert entry["hash_source"] == "consumer_pin"
        assert entry["attested_by"] == "PolicyEngine/microcosm"
        assert entry["pinned_from"]["repository"] == "PolicyEngine/microcosm"
        assert len(entry["pinned_from"]["commit"]) == 40
        assert len(entry["sha256"]) == 64
        assert int(entry["size_bytes"]) > 0
        assert "storage" not in entry
        # No bytes accompany a hash-only registration.
        assert not (package / entry["filename"]).exists()


@pytest.mark.parametrize("package", RELEASE_PACKAGES)
def test_committed_registrations_hold_no_microdata_bytes(package):
    assert sorted(path.name for path in package.iterdir()) == ["manifest.yaml"]


def test_committed_registrations_pass_inventory():
    report = inventory_source_artifacts(REPO_ROOT / "db" / "data")
    hash_only = [entry for entry in report.entries if entry.hash_only]

    assert report.valid
    assert len(hash_only) == 19
    assert all(entry.valid and not entry.exists for entry in hash_only)
    assert all(entry.r2 is None for entry in hash_only)


def test_no_committed_registration_computes_an_r2_key():
    for package in RELEASE_PACKAGES:
        text = (package / "manifest.yaml").read_text()
        assert "storage:" not in text
        assert "ledger-raw" not in text
        assert "r2://" not in text


def test_registration_hashes_are_not_hashes_of_anything_chronicle_holds():
    # A registered checksum identifies publisher bytes Chronicle never sees;
    # it must never coincide with the hash of the manifest that records it.
    for package in RELEASE_PACKAGES:
        manifest_hash = hashlib.sha256(
            (package / "manifest.yaml").read_bytes()
        ).hexdigest()
        assert manifest_hash not in (package / "manifest.yaml").read_text()


# --------------------------------------------------------------------------
# Round-one gate regressions: package-wide custody and frozen conventions
# --------------------------------------------------------------------------


def _write_public_microdata_manifest(
    package: Path,
    *,
    sha256: str,
    size_bytes: int,
    hash_source: str = "chronicle_fetch",
    storage: dict | None = None,
) -> Path:
    """Write one complete public microdata identity for boundary tests."""
    entry = {
        "filename": "adult.tab",
        "source_url": "https://example.test/releases/adult.tab",
        "access": "public",
        "licence": "US-GOVERNMENT-WORK-17-USC-105",
        "licence_evidence": _public_licence_evidence(
            sha256,
            issuer="U.S. Census Bureau",
        ),
        "vintage": "2024",
        "sha256": sha256,
        "size_bytes": size_bytes,
        "verified_at": "2026-09-03",
        "hash_source": hash_source,
        "attested_by": (
            "chronicle" if hash_source == "chronicle_fetch" else "fixture-consumer"
        ),
    }
    if hash_source == "consumer_pin":
        entry["pinned_from"] = {
            "repository": "example/consumer",
            "path": "pins.json",
            "commit": "1" * 40,
        }
    elif hash_source == "consumer_attested":
        entry["attestation_evidence"] = "https://example.test/hash-attestation"
    if storage is not None:
        entry["storage"] = storage

    package.mkdir(parents=True, exist_ok=True)
    manifest_path = package / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "source_id": "census_acs",
                "package_id": "census-acs-pums-2024",
                "kind": "microdata_release",
                "publisher": "U.S. Census Bureau",
                "source_page": "https://example.test/releases",
                "files": {2024: [entry]},
            },
            sort_keys=False,
        )
    )
    return manifest_path


def _recorded_r2_storage(sha256: str) -> dict:
    key = f"raw/census_acs/census-acs-pums-2024/2024/{sha256}/adult.tab"
    return {
        "r2": {
            "provider": "r2",
            "bucket": "ledger-raw",
            "key": key,
            "uri": f"r2://ledger-raw/{key}",
        }
    }


def test_new_fetch_requires_an_explicit_manifest_kind_before_io(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(SourceArtifactManifestError, match="--kind|explicit.*kind"):
        fetch_source_artifact(
            "https://example.test/table.xlsx",
            source_id="publisher",
            package_id="table",
            year=2024,
            output_dir=tmp_path / "pkg",
            access="public",
        )

    assert not (tmp_path / "pkg").exists()


def test_only_the_frozen_committed_kindless_manifest_is_grandfathered(tmp_path):
    committed_package = REPO_ROOT / "db/data/sfpd/legal_pension_caseload_2025"
    committed_manifest = committed_package / "manifest.yaml"
    committed_payload = yaml.safe_load(committed_manifest.read_text())
    artifact_name = committed_payload["files"][2025]["filename"]
    assert "kind" not in committed_payload
    assert inventory_source_artifacts(committed_package).valid

    copied_package = tmp_path / "db/data/sfpd/legal_pension_caseload_2025"
    copied_package.mkdir(parents=True)
    (copied_package / "manifest.yaml").write_bytes(committed_manifest.read_bytes())
    (copied_package / artifact_name).write_bytes(
        (committed_package / artifact_name).read_bytes()
    )

    exact_copy = inventory_source_artifacts(copied_package)
    assert not exact_copy.valid
    assert "kind" in repr(exact_copy.to_dict()).lower()

    with (copied_package / "manifest.yaml").open("a", encoding="utf-8") as stream:
        stream.write("\n# changed after the grandfathered digest was reviewed\n")
    modified_copy = inventory_source_artifacts(copied_package)
    assert not modified_copy.valid
    assert "kind" in repr(modified_copy.to_dict()).lower()


def test_frozen_kindless_manifest_cannot_be_reached_through_a_symlink(
    tmp_path, monkeypatch
):
    target = tmp_path / "target.yaml"
    manifest_bytes = b"files: {}\n"
    target.write_bytes(manifest_bytes)
    link = tmp_path / "manifest.yaml"
    link.symlink_to(target)
    monkeypatch.setitem(
        __import__(
            "chronicle.registration", fromlist=["FROZEN_PUBLISHER_MANIFEST_SHA256"]
        ).FROZEN_PUBLISHER_MANIFEST_SHA256,
        link.absolute().as_posix(),
        hashlib.sha256(manifest_bytes).hexdigest(),
    )

    with pytest.raises(ManifestAccessError, match="symlink"):
        strict_manifest_kind(
            yaml.safe_load(manifest_bytes),
            manifest_locator=link,
            manifest_bytes=manifest_bytes,
        )


def _microdata_package_with_unlisted_bytes(tmp_path: Path) -> tuple[Path, Path]:
    package = tmp_path / "data/dwp/frs_2023_24"
    _register(package)
    leak = package / "renamed-leak.tab"
    leak.write_bytes(b"licensed bytes under an unregistered basename")
    return package, leak


def test_inventory_refuses_unlisted_bytes_beside_a_microdata_manifest(tmp_path):
    package, leak = _microdata_package_with_unlisted_bytes(tmp_path)

    inventory = inventory_source_artifacts(package)
    assert not inventory.valid
    assert leak.name in repr(inventory.to_dict())


def test_publish_refuses_unlisted_bytes_beside_a_microdata_manifest(
    tmp_path, monkeypatch
):
    package, leak = _microdata_package_with_unlisted_bytes(tmp_path)
    monkeypatch.setattr(
        "chronicle.artifacts._upload_r2_object",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not upload bytes")
        ),
    )
    published = publish_source_artifacts(package, skip_hash_only=True)
    assert not published.valid
    assert leak.name in repr(published.to_dict())


def test_fetch_refuses_unlisted_bytes_beside_a_microdata_manifest_before_io(
    tmp_path, monkeypatch
):
    package, leak = _microdata_package_with_unlisted_bytes(tmp_path)
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )
    with pytest.raises(SourceArtifactManifestError, match=leak.name):
        fetch_source_artifact(
            "https://example.test/codebook.pdf",
            source_id="dwp",
            package_id="dwp-frs-2023-24",
            year=2023,
            output_dir=package,
            filename="codebook.pdf",
            kind="microdata_release",
            access="public",
            licence="OGL-3.0",
            licence_evidence=_public_licence_evidence(
                FIXTURE_SHA, licence_id="OGL-3.0"
            ),
            expected_sha256=FIXTURE_SHA,
            publisher="Department for Work and Pensions",
            vintage="2023_24",
            upload_r2=True,
            staging_dir=tmp_path / "staging",
        )


def test_sibling_named_manifest_reserves_a_gated_basename_before_fetch_io(
    tmp_path, monkeypatch
):
    package = tmp_path / "pkg"
    _register(package, manifest_filename="manifest-restricted.yaml")
    (package / "manifest-public.yaml").write_text(
        yaml.safe_dump(
            {
                "source_id": "dwp",
                "package_id": "dwp-frs-2023-24",
                "kind": "microdata_release",
                "publisher": "Department for Work and Pensions",
                "files": {},
            }
        )
    )
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(
        (ManifestAccessError, SourceArtifactManifestError),
        match="manifest-restricted.yaml|sibling.*adult.tab",
    ):
        fetch_source_artifact(
            "https://example.test/adult.tab",
            source_id="dwp",
            package_id="dwp-frs-2023-24",
            year=2023,
            output_dir=package,
            manifest_filename="manifest-public.yaml",
            filename="adult.tab",
            kind="microdata_release",
            access="public",
            licence="OGL-3.0",
            licence_evidence=_public_licence_evidence(
                FIXTURE_SHA, licence_id="OGL-3.0"
            ),
            expected_sha256=FIXTURE_SHA,
            publisher="Department for Work and Pensions",
            vintage="2023_24",
            upload_r2=True,
            staging_dir=tmp_path / "staging",
        )


def test_publish_respects_a_gated_basename_in_a_sibling_named_manifest(
    tmp_path, monkeypatch
):
    package = tmp_path / "data/dwp/release"
    _register(package, manifest_filename="manifest-restricted.yaml")
    public_bytes = b"a publisher table must not overwrite the gated basename"
    public_sha = hashlib.sha256(public_bytes).hexdigest()
    (package / "adult.tab").write_bytes(public_bytes)
    (package / "manifest-public.yaml").write_text(
        yaml.safe_dump(
            {
                "source_id": "dwp",
                "package_id": "dwp-frs-2023-24",
                "kind": "publisher_table",
                "publisher": "Department for Work and Pensions",
                "files": {
                    2024: {
                        "filename": "adult.tab",
                        "access": "public",
                        "sha256": public_sha,
                        "size_bytes": len(public_bytes),
                    }
                },
            }
        )
    )
    uploads: list[Path] = []

    def record_upload(_location, local_path, **_kwargs):
        uploads.append(local_path)
        return _successful_upload()

    monkeypatch.setattr("chronicle.artifacts._upload_r2_object", record_upload)

    report = publish_source_artifacts(
        package,
        manifest_filename="manifest-public.yaml",
    )

    assert not report.valid
    assert uploads == []
    assert "manifest-restricted.yaml" in repr(report.to_dict())
    assert "adult.tab" in repr(report.to_dict())


def test_publisher_table_rejects_a_cross_year_basename_reissue():
    manifest = {
        "kind": "publisher_table",
        "files": {
            2023: {
                "filename": "table.xlsx",
                "access": "public",
                "sha256": "a" * 64,
            },
            2024: {
                "filename": "table.xlsx",
                "access": "public",
                "sha256": "b" * 64,
            },
        },
    }

    assert any(
        error.startswith("filename_identity_collision:table.xlsx")
        for error in manifest_filename_errors(manifest)
    )

    manifest["files"][2024]["sha256"] = "a" * 64
    assert not any(
        error.startswith("filename_identity_collision:table.xlsx")
        for error in manifest_filename_errors(manifest)
    )


def test_register_refuses_cached_bytes_during_public_to_gated_reclassification(
    tmp_path, monkeypatch
):
    package = tmp_path / "pkg"
    manifest_path = _write_public_microdata_manifest(
        package,
        sha256=FIXTURE_SHA,
        size_bytes=1,
    )
    before = manifest_path.read_bytes()
    cache = tmp_path / "source-cache"
    cached_artifact = cache / FIXTURE_SHA / "adult.tab"
    cached_artifact.parent.mkdir(parents=True)
    cached_artifact.write_bytes(b"cached public bytes")
    monkeypatch.setenv("CHRONICLE_SOURCE_ARTIFACT_CACHE_DIR", str(cache))

    with pytest.raises(HashOnlyRegistrationError, match="cache|cached bytes"):
        _register(
            package,
            supersede_public_object="https://example.test/removal/123",
        )

    assert manifest_path.read_bytes() == before
    assert cached_artifact.exists()


def test_fetch_refuses_existing_public_microdata_without_r2_before_io(
    tmp_path, monkeypatch
):
    package = tmp_path / "pkg"
    manifest_path = _write_public_microdata_manifest(
        package,
        sha256=FIXTURE_SHA,
        size_bytes=1,
        hash_source="consumer_pin",
    )
    before = manifest_path.read_bytes()
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(SourceArtifactManifestError, match="records no storage.r2"):
        fetch_source_artifact(
            "https://example.test/releases/adult.tab",
            source_id="census_acs",
            package_id="census-acs-pums-2024",
            year=2024,
            output_dir=package,
            filename="adult.tab",
            kind="microdata_release",
            access="public",
            licence="US-GOVERNMENT-WORK-17-USC-105",
            licence_evidence=_public_licence_evidence(
                FIXTURE_SHA,
                issuer="U.S. Census Bureau",
            ),
            expected_sha256=FIXTURE_SHA,
            expected_size_bytes=1,
            publisher="U.S. Census Bureau",
            vintage="2024",
            upload_r2=True,
            staging_dir=tmp_path / "staging",
        )

    assert manifest_path.read_bytes() == before


@pytest.mark.parametrize(
    ("hash_source", "consumer_only_field"),
    [
        ("consumer_pin", "pinned_from"),
        ("consumer_attested", "attestation_evidence"),
    ],
)
def test_public_fetch_removes_stale_consumer_attestation_fields(
    tmp_path,
    monkeypatch,
    hash_source,
    consumer_only_field,
):
    content = b"public microdata with a consumer-supplied starting hash"
    checksum = hashlib.sha256(content).hexdigest()
    package = tmp_path / "pkg"
    manifest_path = _write_public_microdata_manifest(
        package,
        sha256=checksum,
        size_bytes=len(content),
        hash_source=hash_source,
        storage=_recorded_r2_storage(checksum),
    )
    source = tmp_path / "publisher/adult.tab"
    source.parent.mkdir()
    source.write_bytes(content)
    monkeypatch.setattr("chronicle.artifacts._upload_r2_object", _successful_upload)

    report = fetch_source_artifact(
        str(source),
        source_id="census_acs",
        package_id="census-acs-pums-2024",
        year=2024,
        output_dir=package,
        filename="adult.tab",
        kind="microdata_release",
        access="public",
        licence="US-GOVERNMENT-WORK-17-USC-105",
        licence_evidence=_public_licence_evidence(
            checksum,
            issuer="U.S. Census Bureau",
        ),
        expected_sha256=checksum,
        expected_size_bytes=len(content),
        publisher="U.S. Census Bureau",
        vintage="2024",
        upload_r2=True,
        staging_dir=tmp_path / "staging",
    )

    entry = yaml.safe_load(manifest_path.read_text())["files"][2024][0]
    assert report.valid
    assert entry["hash_source"] == "chronicle_fetch"
    assert entry["attested_by"] == "chronicle"
    assert consumer_only_field not in entry
    assert "pinned_from" not in entry
    assert "attestation_evidence" not in entry


def test_public_microdata_revision_preserves_old_licence_evidence(
    tmp_path, monkeypatch
):
    old_content = b"first public microdata release"
    old_sha = hashlib.sha256(old_content).hexdigest()
    package = tmp_path / "pkg"
    manifest_path = _write_public_microdata_manifest(
        package,
        sha256=old_sha,
        size_bytes=len(old_content),
        storage=_recorded_r2_storage(old_sha),
    )
    old_evidence = _public_licence_evidence(
        old_sha,
        issuer="U.S. Census Bureau",
    )
    new_content = b"revised public microdata release"
    new_sha = hashlib.sha256(new_content).hexdigest()
    source = tmp_path / "publisher/adult.tab"
    source.parent.mkdir()
    source.write_bytes(new_content)
    monkeypatch.setattr("chronicle.artifacts._upload_r2_object", _successful_upload)

    report = fetch_source_artifact(
        str(source),
        source_id="census_acs",
        package_id="census-acs-pums-2024",
        year=2024,
        output_dir=package,
        filename="adult.tab",
        kind="microdata_release",
        access="public",
        licence="US-GOVERNMENT-WORK-17-USC-105",
        licence_evidence=_public_licence_evidence(
            new_sha,
            issuer="U.S. Census Bureau",
        ),
        expected_sha256=new_sha,
        expected_size_bytes=len(new_content),
        publisher="U.S. Census Bureau",
        vintage="2024",
        upload_r2=True,
        staging_dir=tmp_path / "staging",
        record_revision=True,
    )

    revised = yaml.safe_load(manifest_path.read_text())["files"][2024][0]
    previous = revised["storage"]["previous_r2"][0]
    assert report.valid
    assert revised["licence_evidence"]["covered_sha256"] == new_sha
    assert previous["sha256"] == old_sha
    assert previous["licence_evidence"] == old_evidence


def test_publish_reports_an_already_uploaded_public_microdata_object_as_r2_linked(
    tmp_path, monkeypatch
):
    content = b"already-uploaded public microdata"
    checksum = hashlib.sha256(content).hexdigest()
    package = tmp_path / "data/census_acs/release"
    _write_public_microdata_manifest(
        package,
        sha256=checksum,
        size_bytes=len(content),
        storage=_recorded_r2_storage(checksum),
    )
    monkeypatch.setattr(
        "chronicle.artifacts._upload_r2_object",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not upload an already-recorded object")
        ),
    )

    report = publish_source_artifacts(package)

    assert report.valid
    assert report.counts == {
        "manifest_count": 1,
        "artifact_count": 1,
        "uploaded_count": 0,
        "failed_count": 0,
        "hash_only_refused_count": 0,
        "r2_link_count": 1,
    }
    assert report.entries[0].skipped == "public_microdata_uploaded_via_transient_fetch"
    assert report.entries[0].r2_location is not None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("provider", "s3", "provider"),
        ("bucket", "wrong-bucket", "bucket"),
        (
            "key",
            f"raw/census_acs/census-acs-pums-2024/2025/{'a' * 64}/adult.tab",
            "key",
        ),
    ],
)
def test_publish_refuses_noncanonical_recorded_public_microdata_locator(
    tmp_path, monkeypatch, field, value, message
):
    package = tmp_path / "data/census_acs/release"
    storage = _recorded_r2_storage(FIXTURE_SHA)
    storage["r2"][field] = value
    if field in {"provider", "bucket", "key"}:
        provider = storage["r2"]["provider"]
        bucket = storage["r2"]["bucket"]
        key = storage["r2"]["key"]
        storage["r2"]["uri"] = f"{provider}://{bucket}/{key}"
    _write_public_microdata_manifest(
        package,
        sha256=FIXTURE_SHA,
        size_bytes=1,
        storage=storage,
    )
    monkeypatch.setattr(
        "chronicle.artifacts._upload_r2_object",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an already-recorded public object is never re-uploaded")
        ),
    )

    report = publish_source_artifacts(package)

    assert not report.valid
    assert report.entries[0].r2_location is None
    assert message in repr(report.entries[0].errors)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"publisher": None}, "publisher"),
        ({"size_bytes": None}, "size"),
        ({"size_bytes": -1}, "size"),
        ({"size_bytes": "35323384"}, "size"),
    ],
)
def test_register_requires_a_publisher_and_sane_size(tmp_path, overrides, message):
    with pytest.raises(HashOnlyRegistrationError, match=message):
        _register(tmp_path / "pkg", **overrides)

    assert not (tmp_path / "pkg").exists()


def test_microdata_size_rejects_boolean_values():
    entry = {
        "filename": "adult.tab",
        "access": "licensed",
        "licence": "UK Data Service End User Licence",
        "vintage": "2024",
        "sha256": FIXTURE_SHA,
        "size_bytes": True,
        "doi": "10.5255/UKDA-SN-9563-1",
        "verified_at": "2026-09-03",
        "hash_source": "consumer_attested",
        "attested_by": "fixture-consumer",
        "attestation_evidence": "https://example.test/hash-attestation",
    }

    assert "invalid_size_bytes" in validate_file_entry(
        entry,
        kind="microdata_release",
        manifest={
            "source_id": "dwp",
            "package_id": "dwp-frs-2024-25",
            "publisher": "Department for Work and Pensions",
        },
        local_file_exists=False,
    )


def test_register_refuses_to_preserve_an_invalid_existing_registration(tmp_path):
    package = tmp_path / "pkg"
    _register(package)
    manifest_path = package / "manifest.yaml"
    payload = yaml.safe_load(manifest_path.read_text())
    payload["files"][2023][0].pop("hash_source")
    manifest_path.write_text(yaml.safe_dump(payload, sort_keys=False))
    before = manifest_path.read_bytes()

    with pytest.raises(HashOnlyRegistrationError, match="missing_hash_source"):
        _register(package, filename="child.tab", sha256="c" * 64)

    assert manifest_path.read_bytes() == before


@pytest.mark.parametrize("malformed_year_spec", [None, "adult.tab"])
def test_register_refuses_malformed_existing_year_shape_without_rewriting(
    tmp_path, malformed_year_spec
):
    package = tmp_path / "pkg"
    package.mkdir()
    manifest_path = package / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "source_id": "dwp",
                "package_id": "dwp-frs-2023-24",
                "kind": "microdata_release",
                "publisher": "Department for Work and Pensions",
                "files": {2023: malformed_year_spec},
            }
        )
    )
    before = manifest_path.read_bytes()

    with pytest.raises(HashOnlyRegistrationError, match="entry structure"):
        _register(package)

    assert manifest_path.read_bytes() == before


def test_manifest_only_guard_rejects_a_nonmanifest_symlink(tmp_path):
    package = tmp_path / "pkg"
    _register(package)
    alias = package / "metadata-alias.yaml"
    alias.symlink_to("manifest.yaml")
    before = (package / "manifest.yaml").read_bytes()

    inventory = inventory_source_artifacts(package)
    assert not inventory.valid
    assert alias.name in repr(inventory.to_dict())
    with pytest.raises(HashOnlyRegistrationError, match=alias.name):
        _register(package, filename="child.tab", sha256="c" * 64)

    assert (package / "manifest.yaml").read_bytes() == before


@pytest.mark.parametrize(
    ("access", "licence"),
    [
        ("licensed", "UK Data Service End User Licence"),
        ("public", "downloadable public-use data"),
    ],
)
def test_effective_hash_only_microdata_cannot_claim_chronicle_fetch(access, licence):
    entry = {
        "filename": "adult.tab",
        "source_url": "https://example.test/releases/adult.tab",
        "access": access,
        "licence": licence,
        "vintage": "2024",
        "sha256": FIXTURE_SHA,
        "size_bytes": 1,
        "verified_at": "2026-09-03",
        "hash_source": "chronicle_fetch",
        "attested_by": "chronicle",
    }

    errors = validate_file_entry(
        entry,
        kind="microdata_release",
        manifest={
            "source_id": "publisher",
            "package_id": "release",
            "publisher": "Fixture publisher",
        },
        local_file_exists=False,
    )

    assert "chronicle_fetch_requires_public_byte_custody" in errors


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ({"issuer": "A different publisher"}, "licence_evidence_issuer_mismatch"),
        ({"licence_version": "latest"}, "licence_evidence_version_mismatch"),
        (
            {"scope_statement": "This covers some unspecified release."},
            "licence_evidence_scope_mismatch",
        ),
        (
            {"evidence_url": "http://example.test/licence"},
            "licence_evidence_url_not_durable",
        ),
        (
            {"evidence_url": "https:///missing-host"},
            "licence_evidence_url_not_durable",
        ),
        (
            {"evidence_url": "https://user@example.test/licence"},
            "licence_evidence_url_not_durable",
        ),
    ],
)
def test_public_licence_evidence_has_publisher_bound_semantics(
    mutation, expected_error
):
    evidence = _public_licence_evidence(
        FIXTURE_SHA,
        issuer="U.S. Census Bureau",
    )
    evidence.update(mutation)
    entry = {
        "filename": "adult.tab",
        "source_url": "https://example.test/releases/adult.tab",
        "access": "public",
        "licence": "US-GOVERNMENT-WORK-17-USC-105",
        "licence_evidence": evidence,
        "vintage": "2024",
        "sha256": FIXTURE_SHA,
        "size_bytes": 1,
        "verified_at": "2026-09-03",
        "hash_source": "chronicle_fetch",
        "attested_by": "chronicle",
    }

    errors = validate_file_entry(
        entry,
        kind="microdata_release",
        manifest={
            "source_id": "census_acs",
            "package_id": "census-acs-pums-2024",
            "publisher": "U.S. Census Bureau",
        },
        local_file_exists=False,
    )

    assert expected_error in errors


def test_fetch_refuses_a_non_reserved_manifest_name_before_io(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises((ValueError, ManifestAccessError), match="[Mm]anifest"):
        fetch_source_artifact(
            "https://example.test/table.xlsx",
            source_id="publisher",
            package_id="table",
            year=2024,
            output_dir=tmp_path / "pkg",
            manifest_filename="secret.yaml",
            kind="publisher_table",
            access="public",
        )

    assert not (tmp_path / "pkg").exists()


def test_unlisted_secret_yaml_beside_microdata_is_never_read(tmp_path, monkeypatch):
    package = tmp_path / "data/dwp/frs_2023_24"
    _register(package)
    secret = package / "secret.yaml"
    secret.write_bytes(b"publisher-restricted secret material")
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text

    def guarded_read_bytes(path, *args, **kwargs):
        if path == secret:
            raise AssertionError("an unlisted payload must never be read")
        return original_read_bytes(path, *args, **kwargs)

    def guarded_read_text(path, *args, **kwargs):
        if path == secret:
            raise AssertionError("an unlisted payload must never be parsed")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not fetch bytes")),
    )

    with pytest.raises(SourceArtifactManifestError, match="secret.yaml"):
        fetch_source_artifact(
            "https://example.test/codebook.pdf",
            source_id="dwp",
            package_id="dwp-frs-2023-24",
            year=2023,
            output_dir=package,
            filename="codebook.pdf",
            kind="microdata_release",
            access="public",
            licence="OGL-3.0",
            licence_evidence=_public_licence_evidence(
                FIXTURE_SHA,
                licence_id="OGL-3.0",
                issuer="Department for Work and Pensions",
                filename="codebook.pdf",
            ),
            expected_sha256=FIXTURE_SHA,
            publisher="Department for Work and Pensions",
            vintage="2023_24",
            upload_r2=True,
            staging_dir=tmp_path / "staging",
        )


def test_register_refuses_an_exact_sibling_identity_with_recorded_r2(tmp_path):
    package = tmp_path / "pkg"
    _write_public_microdata_manifest(
        package,
        sha256=FIXTURE_SHA,
        size_bytes=1,
        storage=_recorded_r2_storage(FIXTURE_SHA),
    )

    with pytest.raises(HashOnlyRegistrationError, match="storage.r2"):
        _register(
            package,
            manifest_filename="manifest-restricted.yaml",
            supersede_public_object="https://example.test/removal/123",
        )

    assert not (package / "manifest-restricted.yaml").exists()


@pytest.mark.parametrize("stored_sha256", [None, "not-a-sha256"])
def test_fetch_refuses_missing_or_malformed_stored_publisher_sha_before_io(
    tmp_path, monkeypatch, stored_sha256
):
    package = tmp_path / "pkg"
    package.mkdir()
    entry = {
        "filename": "table.xlsx",
        "access": "public",
        "size_bytes": 1,
    }
    if stored_sha256 is not None:
        entry["sha256"] = stored_sha256
    manifest_path = package / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "source_id": "publisher",
                "package_id": "table",
                "kind": "publisher_table",
                "files": {2024: entry},
            }
        )
    )
    before = manifest_path.read_bytes()
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(SourceArtifactManifestError, match="sha256"):
        fetch_source_artifact(
            "https://example.test/table.xlsx",
            source_id="publisher",
            package_id="table",
            year=2024,
            output_dir=package,
            filename="table.xlsx",
            kind="publisher_table",
            access="public",
            expected_sha256=FIXTURE_SHA,
        )

    assert manifest_path.read_bytes() == before
    assert not (package / "table.xlsx").exists()


def test_fetch_refuses_a_prospective_cross_year_public_microdata_collision_before_io(
    tmp_path, monkeypatch
):
    package = tmp_path / "pkg"
    manifest_path = _write_public_microdata_manifest(
        package,
        sha256="b" * 64,
        size_bytes=1,
        storage=_recorded_r2_storage("b" * 64),
    )
    before = manifest_path.read_bytes()
    monkeypatch.setattr(
        "chronicle.artifacts._read_artifact",
        lambda _url: (_ for _ in ()).throw(AssertionError("must not read bytes")),
    )

    with pytest.raises(
        (SourceArtifactManifestError, SourceArtifactRevisionError),
        match="collision|reserves|second.*identity",
    ):
        fetch_source_artifact(
            "https://example.test/adult.tab",
            source_id="census_acs",
            package_id="census-acs-pums-2024",
            year=2025,
            output_dir=package,
            filename="adult.tab",
            kind="microdata_release",
            access="public",
            licence="US-GOVERNMENT-WORK-17-USC-105",
            licence_evidence=_public_licence_evidence(
                FIXTURE_SHA,
                issuer="U.S. Census Bureau",
            ),
            expected_sha256=FIXTURE_SHA,
            expected_size_bytes=1,
            publisher="U.S. Census Bureau",
            vintage="2025",
            upload_r2=True,
            staging_dir=tmp_path / "staging",
        )

    assert manifest_path.read_bytes() == before


def test_every_tracked_microdata_package_is_manifest_only():
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--", "db/data"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    tracked = tuple(
        REPO_ROOT / name
        for name in completed.stdout.decode("utf-8").split("\0")
        if name
    )
    microdata_packages: set[Path] = set()
    manifest_symlinks: list[str] = []
    for path in tracked:
        if not path.name.startswith("manifest") or path.suffix not in {
            ".yaml",
            ".yml",
        }:
            continue
        if path.is_symlink():
            manifest_symlinks.append(str(path.relative_to(REPO_ROOT)))
            continue
        if yaml.safe_load(path.read_text()).get("kind") == "microdata_release":
            microdata_packages.add(path.parent)

    unexpected = list(manifest_symlinks)
    for package in sorted(microdata_packages):
        for path in tracked:
            if not path.is_relative_to(package):
                continue
            if path.parent != package or path.is_symlink():
                unexpected.append(str(path.relative_to(REPO_ROOT)))
                continue
            try:
                canonical_manifest_filename(path.name)
            except ManifestAccessError:
                unexpected.append(str(path.relative_to(REPO_ROOT)))

    assert unexpected == []
