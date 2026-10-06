"""Golden tests for Microcosm pin selection, manifest emit, and fetch plan."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
from types import SimpleNamespace

import yaml

from chronicle import harness
from scripts import register_microdata_releases as generator


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "microcosm"
GOLDEN_PATH = FIXTURE_ROOT / "reviewed_pins.json"


def _golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _catalogue(release_ids: list[str]):
    by_id = {release.release_id: release for release in generator.CATALOGUE}
    assert set(release_ids) <= set(by_id)
    return [by_id[release_id] for release_id in release_ids]


def _resolved_projection(item: generator.ResolvedRelease) -> dict:
    release = item.release
    return {
        "release_id": release.release_id,
        "manifest": release.manifest,
        "package_dir": release.package_dir,
        "source_id": release.source_id,
        "package_id": release.package_id,
        "year": release.year,
        "filename": item.filename,
        "sha256": item.sha256,
        "size_bytes": item.size_bytes,
        "access": item.access,
        "vintage": item.vintage,
    }


def test_selection_matches_all_19_reviewed_origin_main_pins():
    golden = _golden()
    expected = golden["registrations"]
    resolved = generator.resolve(
        FIXTURE_ROOT,
        _catalogue([entry["release_id"] for entry in expected]),
    )

    assert len(expected) == 19
    assert len({entry["sha256"] for entry in expected}) == 19
    assert [_resolved_projection(item) for item in resolved] == expected
    assert {item.artifact["kind"] for item in resolved} == {
        "licensed_microdata",
        "private_microdata",
    }


def test_emit_matches_golden_and_writes_consumer_pin_attestations(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "CHRONICLE_SOURCE_ARTIFACT_CACHE_DIR",
        str(tmp_path / "empty-source-cache"),
    )
    golden = _golden()
    expected = golden["registrations"]
    resolved = generator.resolve(
        FIXTURE_ROOT,
        _catalogue([entry["release_id"] for entry in expected]),
    )

    reports, blockers = generator.emit(
        resolved,
        root=tmp_path,
        verified_at="2026-09-03",
        microcosm_commit=golden["microcosm_commit"],
    )

    assert blockers == []
    assert len(reports) == 19
    emitted = []
    for expected_entry in expected:
        manifest = yaml.safe_load(
            (tmp_path / expected_entry["package_dir"] / "manifest.yaml").read_text()
        )
        assert manifest["source_id"] == expected_entry["source_id"]
        assert manifest["package_id"] == expected_entry["package_id"]
        assert manifest["kind"] == "microdata_release"
        entries = manifest["files"][expected_entry["year"]]
        entry = next(
            item for item in entries if item["filename"] == expected_entry["filename"]
        )
        emitted.append(
            {
                **{
                    key: expected_entry[key]
                    for key in (
                        "release_id",
                        "manifest",
                        "package_dir",
                        "source_id",
                        "package_id",
                        "year",
                    )
                },
                **{
                    key: entry[key]
                    for key in (
                        "filename",
                        "sha256",
                        "size_bytes",
                        "access",
                        "vintage",
                    )
                },
            }
        )
        assert entry["hash_source"] == "consumer_pin"
        assert entry["attested_by"] == "PolicyEngine/microcosm"
        assert entry["verified_at"] == "2026-09-03"
        assert entry["pinned_from"] == {
            "repository": "PolicyEngine/microcosm",
            "path": expected_entry["manifest"],
            "commit": golden["microcosm_commit"],
        }
        assert "storage" not in entry

    assert emitted == expected


def test_committed_manifests_match_the_reviewed_pin_golden():
    golden = _golden()

    for expected in golden["registrations"]:
        manifest = yaml.safe_load(
            (
                REPO_ROOT / "db" / "data" / expected["package_dir"] / "manifest.yaml"
            ).read_text()
        )
        entries = manifest["files"][expected["year"]]
        entry = next(
            item for item in entries if item["filename"] == expected["filename"]
        )
        assert {
            "filename": entry["filename"],
            "sha256": entry["sha256"],
            "size_bytes": entry["size_bytes"],
            "access": entry["access"],
            "vintage": entry["vintage"],
        } == {
            key: expected[key]
            for key in ("filename", "sha256", "size_bytes", "access", "vintage")
        }
        assert manifest["source_id"] == expected["source_id"]
        assert manifest["package_id"] == expected["package_id"]
        assert entry["pinned_from"]["commit"] == golden["microcosm_commit"]


def test_committed_microdata_manifest_and_entry_sets_are_exactly_golden():
    golden = _golden()
    expected_by_package: dict[str, list[tuple]] = {}
    for entry in golden["registrations"]:
        expected_by_package.setdefault(entry["package_dir"], []).append(
            (
                entry["year"],
                entry["filename"],
                entry["sha256"],
                entry["size_bytes"],
                entry["access"],
                entry["vintage"],
            )
        )

    data_root = REPO_ROOT / "db" / "data"
    actual_by_package: dict[str, list[tuple]] = {}
    for manifest_path in data_root.rglob("manifest.yaml"):
        manifest = yaml.safe_load(manifest_path.read_text()) or {}
        if manifest.get("kind") != "microdata_release":
            continue
        package_dir = manifest_path.parent.relative_to(data_root).as_posix()
        entries: list[tuple] = []
        for year, year_spec in manifest["files"].items():
            specs = year_spec if isinstance(year_spec, list) else [year_spec]
            entries.extend(
                (
                    int(year),
                    spec["filename"],
                    spec["sha256"],
                    spec["size_bytes"],
                    spec["access"],
                    spec["vintage"],
                )
                for spec in specs
            )
        actual_by_package[package_dir] = entries

    assert set(actual_by_package) == set(expected_by_package)
    assert {
        package: sorted(entries) for package, entries in actual_by_package.items()
    } == {package: sorted(entries) for package, entries in expected_by_package.items()}


def test_plan_matches_golden_and_every_command_parses(monkeypatch, capsys):
    golden = _golden()
    releases = _catalogue(
        ["census-acs-pums-2024-household", "census-acs-pums-2024-person"]
    )
    resolved = generator.resolve(FIXTURE_ROOT, releases)

    commands, todos = generator.plan(
        resolved,
        root=Path("db/data"),
        r2_bucket="ledger-raw",
    )

    assert commands == golden["plan_commands"]
    assert todos == []
    captured = []

    def fake_fetch(url, **kwargs):
        captured.append({"url": url, **kwargs})
        return SimpleNamespace(valid=True, to_dict=lambda: {"valid": True})

    monkeypatch.setattr(harness, "fetch_artifact_file", fake_fetch)
    for command in commands:
        assert harness.main(shlex.split(command)[3:]) == 0
        capsys.readouterr()

    assert [item["publisher"] for item in captured] == [
        "U.S. Census Bureau",
        "U.S. Census Bureau",
    ]
    assert [item["vintage"] for item in captured] == ["2024", "2024"]
    assert [item["expected_sha256"] for item in captured] == [
        "8281008e53de98f0ef81e7a2ee5a8725991dda1ecfd2713ead73246425e515d0",
        "afdc6d90c6e2f0bab365ed32d95ba4c4d8ac651162f46ac7861295b2dc469894",
    ]
    assert [item["expected_size_bytes"] for item in captured] == [
        251500587,
        602847146,
    ]


def test_plan_preserves_a_size_only_publisher_pin():
    release = _catalogue(["census-acs-pums-2024-household"])[0]
    resolved = generator.ResolvedRelease(
        release=release,
        stage={},
        artifact={
            "kind": "public_microdata",
            "role": "household",
            "filename": "size-only.zip",
            "url": "https://example.test/size-only.zip",
            "size_bytes": 123456,
        },
    )

    commands, todos = generator.plan(
        [resolved],
        root=Path("db/data"),
        r2_bucket="ledger-raw",
    )

    assert len(commands) == 1
    assert "--expected-size-bytes 123456" in commands[0]
    assert "--expected-sha256" not in commands[0]
    assert "--licence-evidence-sha256 TODO_REVIEWED_PUBLISHER_SHA256" in commands[0]
    assert len(todos) == 1
    assert "obtain a reviewed publisher-artifact SHA-256" in todos[0]
