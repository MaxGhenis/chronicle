"""Adversarial verification of the Thesis observation append gate.

These tests exercise counterexamples from the 2026-07-10 ledger review. The
cases that pinned open findings 3, 6, and 13 are now real assertions that the
hardened gate rejects the attack. The remaining strict-xfail tests document
boundaries that are deliberately out of scope for this pass (provenance-format
validation, the no-base-ref full-file mode, and per-commit history walking);
each fails loudly if it ever starts passing.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from arch.core import (
    AggregateFact,
    Aggregation,
    EntityDimension,
    GeographyDimension,
    Measure,
    PeriodDimension,
    SourceProvenance,
    validate_facts,
)

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "ledger" / "official_observations.jsonl"
PREFIX_PATH = ROOT / "ledger" / "immutable_prefix.json"

sys.path.insert(0, str(ROOT / "scripts"))

from check_thesis_facts_append import (  # noqa: E402
    AppendError,
    check_append_only,
    check_rows,
    effective_current_rows,
    expected_assertion_version_id,
)


def _read_lines() -> list[str]:
    return [
        line
        for line in LEDGER_PATH.read_text(encoding="utf-8").split("\n")
        if line.strip()
    ]


def _json_line(row: dict) -> str:
    return json.dumps(row, separators=(",", ":"))


def _to_aggregate_fact(row: dict) -> AggregateFact:
    return AggregateFact(
        value=row["value"],
        period=PeriodDimension(**row["period"]),
        geography=GeographyDimension(**row["geography"]),
        entity=EntityDimension(**row["entity"]),
        measure=Measure(**row["measure"]),
        aggregation=Aggregation(**row["aggregation"]),
        source=SourceProvenance(**row["source"]),
        filters=row.get("filters", {}),
        domain=row.get("domain", "all"),
        label=row.get("label"),
        source_record_id=row.get("source_record_id"),
        source_cell_keys=tuple(row.get("source_cell_keys", ())),
        source_row_keys=tuple(row.get("source_row_keys", ())),
    )


def _appended_row(
    original: dict,
    *,
    value_delta: float = 0,
    source_record_id: str | None = None,
) -> dict:
    row = copy.deepcopy(original)
    row["value"] += value_delta
    if source_record_id is not None:
        row["source_record_id"] = source_record_id
    row.update(
        {
            "retrievedAt": "2026-07-11T00:00:00Z",
            "sourceVintage": "2026-07-11",
            "ledgerRepoSha": "a" * 40,
            "responseArchive": {
                "sha256": "b" * 64,
                "contentEncoding": "gzip",
            },
        }
    )
    row.pop("targetContentHash", None)
    row.pop("sourceBindingProjection", None)
    row["assertionVersion"] = {
        "id": expected_assertion_version_id(row),
        "supersedes": None,
    }
    return row


def _write_checker_fixture(path: Path, ledger_text: str, manifest: dict) -> None:
    ledger_dir = path / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    (ledger_dir / "official_observations.jsonl").write_text(
        ledger_text, encoding="utf-8"
    )
    (ledger_dir / "immutable_prefix.json").write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8"
    )
    scripts_dir = path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    for name in (
        "check_thesis_facts_append.py",
        "canonical_json.py",
        "verify_release_chain.py",
    ):
        (scripts_dir / name).write_text(
            (ROOT / "scripts" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def _run_checker(path: Path, base_ref: str | None = None) -> subprocess.CompletedProcess:
    command = [sys.executable, str(path / "scripts/check_thesis_facts_append.py")]
    if base_ref is not None:
        command.extend(["--base-ref", base_ref])
    return subprocess.run(command, cwd=path, capture_output=True, text=True)


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _init_fixture_repo(path: Path) -> str:
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "Append Gate Test")
    _git(path, "config", "user.email", "append-gate@example.invalid")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "base")
    return _git(path, "rev-parse", "HEAD")


def _rehash_manifest(lines: list[str]) -> dict:
    manifest = json.loads(PREFIX_PATH.read_text(encoding="utf-8"))
    count = int(manifest["prefixLineCount"])
    manifest["lineSha256s"] = [
        hashlib.sha256(line.encode("utf-8")).hexdigest()
        for line in lines[:count]
    ]
    manifest["prefixSha256"] = hashlib.sha256(
        ("\n".join(lines[:count]) + "\n").encode("utf-8")
    ).hexdigest()
    return manifest


def _extend_manifest_to_all_lines(lines: list[str]) -> dict:
    manifest = json.loads(PREFIX_PATH.read_text(encoding="utf-8"))
    manifest["prefixLineCount"] = len(lines)
    manifest["lineSha256s"] = [
        hashlib.sha256(line.encode("utf-8")).hexdigest() for line in lines
    ]
    manifest["prefixSha256"] = hashlib.sha256(
        ("\n".join(lines) + "\n").encode("utf-8")
    ).hexdigest()
    return manifest


def test_base_check_rejects_an_existing_line_rewrite():
    lines = _read_lines()
    rewritten = json.loads(lines[-1])
    rewritten["value"] += 1
    candidate = [*lines[:-1], _json_line(rewritten)]

    with pytest.raises(
        AppendError,
        match=rf"rewrites existing line {len(lines)}",
    ):
        check_append_only("HEAD", candidate)


def test_base_check_rejects_truncation():
    lines = _read_lines()

    with pytest.raises(
        AppendError,
        match=rf"truncates the ledger: {len(lines)} -> {len(lines) - 1}",
    ):
        check_append_only("HEAD", lines[:-1])


def test_base_check_accepts_a_true_append():
    lines = _read_lines()

    assert check_append_only("HEAD", [*lines, "{}"]) == 1


def test_duplicate_identity_without_supersedes_is_rejected():
    lines = _read_lines()
    duplicate = _appended_row(json.loads(lines[-1]), value_delta=1)

    with pytest.raises(AppendError, match="without superseding"):
        check_rows([*lines, _json_line(duplicate)], len(lines))


def test_mismatched_av2_id_is_rejected():
    lines = _read_lines()
    row = _appended_row(
        json.loads(lines[-1]),
        source_record_id="verification.unique.mismatched_av2",
    )
    row["assertionVersion"]["id"] = "av2:" + "0" * 64

    with pytest.raises(AppendError, match="does not match its content"):
        check_rows([*lines, _json_line(row)], len(lines))


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("measure.source_concept", "DIFFERENT_PUBLISHER_SERIES"),
        ("measure.concept_relation", "approximate"),
        ("measure.concept_authority", "different_authority"),
        ("measure.legal_vintage", "different_legal_vintage"),
        ("source.source_file", "different_release.csv"),
        ("source.source_sha256", "d" * 64),
        ("source_cell_keys", ["different:publisher:cell"]),
        ("source_row_keys", ["different:publisher:row"]),
    ],
)
def test_av2_binds_material_concept_and_source_lineage(
    path: str, replacement: object
):
    # Review finding 6: av1 projected only measure concept/unit and four source
    # fields, so these material changes collided. The av2 projection binds the
    # complete concept mapping, exact source file/digest, and row/cell lineage.
    original = json.loads(_read_lines()[-1])
    changed = copy.deepcopy(original)
    if "." in path:
        parent, key = path.split(".", maxsplit=1)
        changed[parent][key] = replacement
    else:
        changed[path] = replacement

    assert expected_assertion_version_id(changed) != (
        expected_assertion_version_id(original)
    )


def test_av2_binds_the_archived_response_digest():
    # av1 could reuse an id across different archived response bytes; av2 binds
    # responseArchive.sha256 so re-fetched bytes are a distinct assertion.
    original = json.loads(_read_lines()[-1])
    changed = copy.deepcopy(original)
    changed["responseArchive"] = {**changed["responseArchive"], "sha256": "e" * 64}

    assert expected_assertion_version_id(changed) != (
        expected_assertion_version_id(original)
    )


def test_inconsistent_supersedes_chain_is_rejected():
    lines = _read_lines()
    original = json.loads(lines[-1])
    original_id = expected_assertion_version_id(original)
    first_correction = _appended_row(original, value_delta=1)
    first_correction["assertionVersion"]["supersedes"] = original_id
    second_correction = _appended_row(original, value_delta=2)
    second_correction["assertionVersion"]["supersedes"] = original_id

    with pytest.raises(AppendError, match="but the active version"):
        check_rows(
            [*lines, _json_line(first_correction), _json_line(second_correction)],
            len(lines),
        )


def test_pre_versioning_row_is_addressable_by_recomputed_av2_id():
    lines = _read_lines()
    original = json.loads(lines[-1])
    correction = _appended_row(original, value_delta=1)
    correction["assertionVersion"]["supersedes"] = (
        expected_assertion_version_id(original)
    )

    check_rows([*lines, _json_line(correction)], len(lines))


def test_full_ci_fact_validation_accepts_an_explicit_correction():
    # Review finding 6: the append gate accepts an explicit correction, but the
    # required aggregate-fact validation used to reject it as a duplicate key.
    # Validating the supersede-aware effective current view resolves the
    # documented correction path.
    rows = [json.loads(line) for line in _read_lines()]
    original = rows[-1]
    correction = _appended_row(original, value_delta=1)
    correction["assertionVersion"]["supersedes"] = (
        expected_assertion_version_id(original)
    )

    # The append gate itself accepts exactly the advertised correction shape.
    lines = [_json_line(row) for row in rows]
    check_rows([*lines, _json_line(correction)], len(lines))

    # The subsequently required aggregate-fact validation now runs on the
    # supersede-aware current view, which drops the superseded original.
    current = effective_current_rows([*rows, correction])
    report = validate_facts([_to_aggregate_fact(row) for row in current])
    assert report.valid, report.to_dict()
    assert len(current) == len(rows)


def test_correction_chain_cannot_restore_a_superseded_legacy_value():
    # Review finding 6: an A->B->A chain must not resurrect a superseded value.
    # Restoring A means re-asserting A's exact content, which recomputes A's
    # effective id — reserved when A was first seen — so the restore is rejected.
    lines = _read_lines()
    original = json.loads(lines[-1])
    original_id = expected_assertion_version_id(original)
    first_correction = _appended_row(original, value_delta=1)
    first_correction["assertionVersion"]["supersedes"] = original_id
    restore = copy.deepcopy(original)
    restore["assertionVersion"] = {
        "id": expected_assertion_version_id(restore),
        "supersedes": first_correction["assertionVersion"]["id"],
    }
    assert restore["assertionVersion"]["id"] == original_id

    with pytest.raises(AppendError, match="restates assertion version"):
        check_rows(
            [*lines, _json_line(first_correction), _json_line(restore)],
            len(lines),
        )


def test_provenance_correction_gets_a_distinct_av2_id_and_supersedes():
    # Review finding 6: under av1 a provenance-only correction (changed lineage,
    # re-archived bytes) collided with the original id and could be reissued.
    # av2 binds lineage, so the correction is a DISTINCT assertion that merges
    # cleanly as an explicit supersede rather than reusing the legacy id.
    lines = _read_lines()
    original = json.loads(lines[-1])
    original_id = expected_assertion_version_id(original)
    correction = copy.deepcopy(original)
    correction["source_cell_keys"] = ["different:publisher:cell"]
    correction["assertionVersion"] = {
        "id": expected_assertion_version_id(correction),
        "supersedes": original_id,
    }

    assert correction["assertionVersion"]["id"] != original_id
    check_rows([*lines, _json_line(correction)], len(lines))


@pytest.mark.xfail(
    strict=True,
    reason=(
        "out of scope for this pass: provenance fields are checked for presence "
        "and binding, not for retrievedAt/sha/git-sha string formats"
    ),
)
@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("retrievedAt", "not-a-timestamp"),
        ("ledgerRepoSha", "not-a-git-sha"),
        ("responseArchive.sha256", "not-a-sha256"),
    ],
)
def test_appended_provenance_requires_valid_integrity_metadata(
    field: str, invalid_value: str
):
    lines = _read_lines()
    row = _appended_row(
        json.loads(lines[-1]),
        source_record_id=f"verification.unique.invalid.{field}",
    )
    if field == "responseArchive.sha256":
        row["responseArchive"]["sha256"] = invalid_value
    else:
        row[field] = invalid_value
    row["assertionVersion"]["id"] = expected_assertion_version_id(row)

    with pytest.raises(AppendError):
        check_rows([*lines, _json_line(row)], len(lines))


def test_joint_manifest_and_file_rewrite_is_rejected_against_git_base(tmp_path):
    # A coordinated rewrite of a frozen row plus a rehashed manifest is rejected
    # end-to-end: the base-anchored manifest check catches the co-edited
    # manifest before the append-only line diff even runs.
    lines = _read_lines()
    original_text = "\n".join(lines) + "\n"
    original_manifest = json.loads(PREFIX_PATH.read_text(encoding="utf-8"))
    _write_checker_fixture(tmp_path, original_text, original_manifest)
    base = _init_fixture_repo(tmp_path)

    rewritten = json.loads(lines[0])
    rewritten["value"] += 1
    lines[0] = _json_line(rewritten)
    manifest = _rehash_manifest(lines)
    _write_checker_fixture(tmp_path, "\n".join(lines) + "\n", manifest)

    completed = _run_checker(tmp_path, base)

    assert completed.returncode == 1
    assert "immutable prefix manifest" in completed.stderr


def test_manifest_extension_cannot_grandfather_an_unbound_append(tmp_path):
    # Review finding 3: a PR extends the co-editable prefix manifest over its own
    # append so the unbound row counts as "prefix" and every post-cutover binding
    # is skipped. Base-anchoring the manifest and using the BASE prefix count for
    # the binding boundary both reject it.
    lines = _read_lines()
    original_text = "\n".join(lines) + "\n"
    original_manifest = json.loads(PREFIX_PATH.read_text(encoding="utf-8"))
    _write_checker_fixture(tmp_path, original_text, original_manifest)
    base = _init_fixture_repo(tmp_path)

    row = copy.deepcopy(json.loads(lines[-1]))
    row["source_record_id"] = "verification.unbound.grandfathered_append"
    row["observed_at"] = "2099-02-01"
    row["period"] = {"type": "month", "value": "2099-01"}
    for field in (
        "retrievedAt",
        "sourceVintage",
        "ledgerRepoSha",
        "responseArchive",
        "targetContentHash",
        "sourceBindingProjection",
        "assertionVersion",
    ):
        row.pop(field, None)
    lines.append(_json_line(row))
    manifest = _extend_manifest_to_all_lines(lines)
    _write_checker_fixture(tmp_path, "\n".join(lines) + "\n", manifest)

    # This counterexample also survives the separately required aggregate-fact
    # validation: the new period gives it a unique semantic fact key.
    facts = [_to_aggregate_fact(json.loads(line)) for line in lines]
    assert validate_facts(facts).valid

    completed = _run_checker(tmp_path, base)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "immutable prefix manifest" in completed.stderr


@pytest.mark.xfail(
    strict=True,
    reason=(
        "by design: the full-file (no base-ref) mode trusts the co-editable "
        "manifest, so a joint manifest/file rewrite is only anchored on the PR "
        "path (--base-ref) or by an external witness"
    ),
)
def test_full_file_check_alone_rejects_joint_manifest_and_file_rewrite(tmp_path):
    lines = _read_lines()
    rewritten = json.loads(lines[0])
    rewritten["value"] += 1
    lines[0] = _json_line(rewritten)
    manifest = _rehash_manifest(lines)
    _write_checker_fixture(tmp_path, "\n".join(lines) + "\n", manifest)

    completed = _run_checker(tmp_path)

    assert completed.returncode == 1, completed.stdout + completed.stderr


def test_prefix_byte_check_rejects_an_inserted_blank_line(tmp_path):
    # Review finding 13: _lines dropped blank lines, so a blank line inserted
    # into the frozen JSONL normalized away and passed even with --base-ref. The
    # byte guard rejects any blank/whitespace-only line in the covered region.
    lines = _read_lines()
    manifest = json.loads(PREFIX_PATH.read_text(encoding="utf-8"))
    original_text = "\n".join(lines) + "\n"
    _write_checker_fixture(tmp_path, original_text, manifest)
    base = _init_fixture_repo(tmp_path)
    text_with_blank_line = lines[0] + "\n\n" + "\n".join(lines[1:]) + "\n"
    _write_checker_fixture(tmp_path, text_with_blank_line, manifest)

    completed = _run_checker(tmp_path, base)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "blank or whitespace-only" in completed.stderr


@pytest.mark.xfail(
    strict=True,
    reason=(
        "out of scope for this pass: the checker compares only base and final "
        "trees, not every intermediate commit, so a rewrite-then-restore whose "
        "final tree equals the base passes"
    ),
)
def test_base_check_rejects_rewrite_then_restore_across_commits(tmp_path):
    lines = _read_lines()
    original_text = "\n".join(lines) + "\n"
    manifest = json.loads(PREFIX_PATH.read_text(encoding="utf-8"))
    _write_checker_fixture(tmp_path, original_text, manifest)
    base = _init_fixture_repo(tmp_path)

    rewritten = json.loads(lines[0])
    rewritten["value"] += 1
    rewritten_lines = [_json_line(rewritten), *lines[1:]]
    (tmp_path / "ledger" / "official_observations.jsonl").write_text(
        "\n".join(rewritten_lines) + "\n", encoding="utf-8"
    )
    _git(tmp_path, "add", "ledger/official_observations.jsonl")
    _git(tmp_path, "commit", "-qm", "rewrite frozen row")

    (tmp_path / "ledger" / "official_observations.jsonl").write_text(
        original_text, encoding="utf-8"
    )
    _git(tmp_path, "add", "ledger/official_observations.jsonl")
    _git(tmp_path, "commit", "-qm", "restore frozen row")

    completed = _run_checker(tmp_path, base)

    assert completed.returncode == 1, completed.stdout + completed.stderr
