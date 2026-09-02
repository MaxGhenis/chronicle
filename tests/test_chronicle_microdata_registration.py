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
    fetch_source_artifact,
    inventory_source_artifacts,
    publish_source_artifacts,
)
from chronicle.harness import main as harness_main
from chronicle.registration import (
    ACCESS_CLASSES,
    HashOnlyRegistrationError,
    ListSpecRejected,
    ManifestAccessError,
    MicrodataReleaseNotParseableError,
    entry_access,
    is_hash_only,
    is_microdata_release,
    iter_file_specs,
    manifest_kind,
    normalize_access,
    register_hash_only_artifact,
    registration_id,
    safe_entry_access,
    stores_bytes,
    validate_file_entry,
)
from chronicle.source_package import SourceArtifactSpec, validate_source_package


REPO_ROOT = Path(__file__).resolve().parents[1]
FRS_PACKAGE = REPO_ROOT / "db" / "data" / "dwp" / "frs_2023_24"
SPI_PACKAGE = REPO_ROOT / "db" / "data" / "hmrc" / "spi_public_use_tape_2022_23"

# A syntactically valid checksum that identifies no real publisher bytes.
FIXTURE_SHA = "a" * 64


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
        "doi": "10.5255/UKDA-SN-9367-2",
        "verified_at": "2026-09-02",
    }
    kwargs.update(overrides)
    return register_hash_only_artifact(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Access and kind vocabularies
# --------------------------------------------------------------------------


def test_access_classes_are_the_closed_contract_set():
    assert ACCESS_CLASSES == ("public", "licensed", "restricted")


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
        ({"verified_at": None}, "missing_verification_timestamp"),
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
        ({"filename": "../adult.tab"}, "must be a bare filename"),
    ],
)
def test_register_refuses_an_incomplete_registration(tmp_path, overrides, expected):
    with pytest.raises(HashOnlyRegistrationError, match=expected):
        _register(tmp_path / "pkg", **overrides)


def test_register_refuses_without_an_access_route(tmp_path):
    with pytest.raises(HashOnlyRegistrationError, match="how the bytes are reached"):
        _register(tmp_path / "pkg", doi=None)


def test_register_refuses_while_the_bytes_are_present(tmp_path):
    output_dir = tmp_path / "pkg"
    output_dir.mkdir()
    (output_dir / "adult.tab").write_bytes(b"licensed microdata must not live here")

    with pytest.raises(HashOnlyRegistrationError, match="while its bytes"):
        _register(output_dir)


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
        )


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
        source_table="Family Resources Survey 2023-24",
        resource_package="db",
        resource_directory="data/dwp/frs_2023_24",
        manifest="manifest.yaml",
        vintage="2023_24",
        extracted_at="2026-09-02",
        extraction_method="none",
        parser="delimited_text_full_rows",
        artifact_year=2023,
    )

    with pytest.raises(MicrodataReleaseNotParseableError, match="registers a"):
        spec.assert_parseable_manifest()
    with pytest.raises(MicrodataReleaseNotParseableError):
        spec._artifact_content(2023)


def test_year_mapping_refuses_a_multi_file_vintage():
    from chronicle.source_package import _year_mapping

    with pytest.raises(ValueError, match="list of 2 entries"):
        _year_mapping(
            {2023: [{"filename": "adult.tab"}, {"filename": "child.tab"}]}, 2023
        )


def test_validate_package_reports_a_microdata_release_carve_out(tmp_path):
    package_dir = tmp_path / "frs_2023_24"
    package_dir.mkdir()
    (package_dir / "source_package.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "ledger.source_package.v1",
                "package_id": "dwp-frs-2023-24-parse-attempt",
                "label": "Attempt to parse a registered microdata release",
                "artifact": {
                    "source_name": "dwp",
                    "source_table": "Family Resources Survey 2023-24",
                    "resource_package": "db",
                    "resource_directory": "data/dwp/frs_2023_24",
                    "manifest": "manifest.yaml",
                    "vintage": "2023_24",
                    "extracted_at": "2026-09-02",
                    "extraction_method": "none",
                    "parser": "delimited_text_full_rows",
                    "artifact_year": 2023,
                },
                "record_sets": [],
            },
            sort_keys=False,
        )
    )

    report = validate_source_package(package_dir, year=2023)

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


@pytest.mark.parametrize("package", [FRS_PACKAGE, SPI_PACKAGE])
def test_committed_registrations_are_identity_only(package):
    entries = _committed_entries(package)

    assert entries
    for entry in entries:
        assert entry["access"] in {"licensed", "restricted"}
        assert entry["licence"]
        assert entry["vintage"]
        assert entry["verified_at"]
        assert len(entry["sha256"]) == 64
        assert int(entry["size_bytes"]) > 0
        assert "storage" not in entry
        # No bytes accompany a hash-only registration.
        assert not (package / entry["filename"]).exists()


@pytest.mark.parametrize("package", [FRS_PACKAGE, SPI_PACKAGE])
def test_committed_registrations_hold_no_microdata_bytes(package):
    assert sorted(path.name for path in package.iterdir()) == ["manifest.yaml"]


def test_committed_registrations_pass_inventory():
    report = inventory_source_artifacts(REPO_ROOT / "db" / "data")
    hash_only = [entry for entry in report.entries if entry.hash_only]

    assert report.valid
    assert len(hash_only) == 15
    assert all(entry.valid and not entry.exists for entry in hash_only)
    assert all(entry.r2 is None for entry in hash_only)


def test_no_committed_registration_computes_an_r2_key():
    for package in (FRS_PACKAGE, SPI_PACKAGE):
        text = (package / "manifest.yaml").read_text()
        assert "storage:" not in text
        assert "ledger-raw" not in text
        assert "r2://" not in text


def test_registration_hashes_are_not_hashes_of_anything_chronicle_holds():
    # A registered checksum identifies publisher bytes Chronicle never sees;
    # it must never coincide with the hash of the manifest that records it.
    for package in (FRS_PACKAGE, SPI_PACKAGE):
        manifest_hash = hashlib.sha256(
            (package / "manifest.yaml").read_bytes()
        ).hexdigest()
        assert manifest_hash not in (package / "manifest.yaml").read_text()
