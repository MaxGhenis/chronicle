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

import pytest
import yaml

from chronicle.artifacts import (
    ArtifactCommandResult,
    SourceArtifactManifestError,
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
) -> dict[str, str]:
    """Return complete artifact-bound redistribution evidence for a fixture."""
    return {
        "issuer": "Fixture publisher",
        "licence_id": licence_id,
        "licence_version": (
            "3.0" if licence_id == "OGL-3.0" else "17 U.S.C. section 105"
        ),
        "scope_statement": "The licence covers this exact publisher artifact.",
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
    entry = report.entries[0]
    assert entry.skipped == "hash_only_access:licensed"
    assert "hash_only_access_refuses_bytes:licensed" in entry.errors
    assert "public_microdata_licence_not_allowlisted" in entry.errors
    assert "bytes_present_for_hash_only_entry" in entry.errors
    assert entry.upload is None


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
    assert "bytes_present_for_hash_only_entry" in report.entries[0].errors


def test_inventory_rejects_a_list_entry_outside_a_microdata_release(tmp_path):
    package = tmp_path / "data" / "dwp" / "tables"
    package.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "source_id": "dwp",
                "package_id": "dwp-tables",
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


def test_cli_register_artifact_updates_the_named_manifest(tmp_path, capsys):
    output_dir = tmp_path / "pkg"
    output_dir.mkdir()
    named_manifest = output_dir / "release.yaml"
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
            "release.yaml",
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


def test_fetch_refuses_a_list_entry_in_a_manifest_without_a_kind(tmp_path):
    # A missing or misspelled kind must not make the guard blind to the
    # entries the manifest actually holds.
    package = _licensed_manifest(
        tmp_path / "pkg",
        {
            "source_id": "dwp",
            "package_id": "dwp-frs-2023-24",
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
    assert "bytes_present_for_hash_only_entry" in report.entries[0].errors


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
            licence_evidence=_public_licence_evidence(source_sha256),
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
