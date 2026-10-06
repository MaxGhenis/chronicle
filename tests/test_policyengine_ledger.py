"""Invariants for the checked-in PolicyEngine Ledger observation facts.

The old version of this file hard-coded a closed inventory of 49
source_record_ids, went stale at the first append, and was dropped from CI.
These tests assert the properties that must hold at ANY row count instead:
the frozen prefix is immutable, every row is a valid publisher fact, and
duplicate identities only exist as explicit supersede corrections.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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
    check_prefix,
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


def _read_ledger_facts() -> list[dict]:
    return [json.loads(line) for line in _read_lines()]


LEDGER_ROW_KEYS = {
    "label",
    "observed_at",
    "source_record_id",
    "source_cell_keys",
    "source_row_keys",
    # Resolution provenance attached by the Thesis resolver.
    "ledgerRepoSha",
    "sourceVintage",
    "retrievedAt",
    "responseArchive",
    "targetContentHash",
    "sourceBindingProjection",
    "assertionVersion",
}


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


def test_immutable_prefix_is_intact():
    lines = _read_lines()

    prefix = check_prefix(lines)

    assert prefix["prefixLineCount"] >= 128
    assert len(lines) >= prefix["prefixLineCount"]


def test_every_row_satisfies_the_append_invariants():
    lines = _read_lines()
    prefix = check_prefix(lines)

    # Raises on any malformed row, unexplained duplicate identity,
    # mis-addressed assertion version, or missing post-prefix binding.
    check_rows(lines, int(prefix["prefixLineCount"]))


def test_official_observation_ledger_contains_facts_not_predictions():
    for row in _read_ledger_facts():
        assert "prediction" not in json.dumps(row).lower()
        assert "forecast" not in json.dumps(row).lower()
        assert row["source_record_id"]
        assert row["source"]["url"].startswith("https://")
        assert row["source"]["vintage"]


def test_official_observations_validate_as_aggregate_facts():
    # ``validate_facts`` rejects two rows sharing a semantic aggregate key, so
    # the journal is validated as its supersede-aware effective current view
    # (latest non-superseded row per identity) rather than as raw duplicates.
    current = effective_current_rows(_read_ledger_facts())
    facts = [_to_aggregate_fact(row) for row in current]

    report = validate_facts(facts)

    assert report.valid, report.to_dict()
    assert report.counts["missing_lineage"]["count"] == 0


def test_rows_carry_no_unknown_top_level_fields():
    known = set(AggregateFact.__dataclass_fields__) | LEDGER_ROW_KEYS
    for number, row in enumerate(_read_ledger_facts(), start=1):
        unknown = set(row) - known
        assert not unknown, f"line {number} has unknown fields: {sorted(unknown)}"


def test_a_rewritten_prefix_line_is_detected(tmp_path):
    lines = _read_lines()
    row = json.loads(lines[0])
    row["value"] = 999999
    tampered = [json.dumps(row, separators=(",", ":")), *lines[1:]]
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    (ledger_dir / "official_observations.jsonl").write_text(
        "\n".join(tampered) + "\n"
    )
    (ledger_dir / "immutable_prefix.json").write_text(PREFIX_PATH.read_text())
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    for name in (
        "check_thesis_facts_append.py",
        "canonical_json.py",
        "verify_release_chain.py",
    ):
        (scripts_dir / name).write_text((ROOT / "scripts" / name).read_text())

    completed = subprocess.run(
        [sys.executable, str(scripts_dir / "check_thesis_facts_append.py")],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "was rewritten" in completed.stderr


def _appended_row(original: dict, *, value_delta: float = 0) -> dict:
    row = {
        **original,
        "value": original["value"] + value_delta,
        "retrievedAt": "2026-07-11T00:00:00Z",
        "sourceVintage": "2026-07-11",
        "ledgerRepoSha": "a" * 40,
        "responseArchive": {"sha256": "b" * 64, "contentEncoding": "gzip"},
    }
    row.pop("targetContentHash", None)
    row.pop("sourceBindingProjection", None)
    return row


def test_a_duplicate_identity_without_supersedes_is_detected():
    lines = _read_lines()
    duplicate = _appended_row(json.loads(lines[-1]))
    duplicate["assertionVersion"] = {
        "id": expected_assertion_version_id(duplicate),
        "supersedes": None,
    }
    try:
        check_rows(
            [*lines, json.dumps(duplicate, separators=(",", ":"))], len(lines)
        )
    except ValueError as error:
        assert "without superseding" in str(error)
    else:
        raise AssertionError("duplicate identity was accepted silently")


def test_an_explicit_supersede_correction_is_accepted():
    # A correction of a pre-versioning row supersedes that row's
    # recomputable content address.
    lines = _read_lines()
    original = json.loads(lines[-1])
    correction = _appended_row(original, value_delta=1)
    correction["assertionVersion"] = {
        "id": expected_assertion_version_id(correction),
        "supersedes": expected_assertion_version_id(original),
    }

    check_rows(
        [*lines, json.dumps(correction, separators=(",", ":"))], len(lines)
    )


def test_a_correction_naming_a_stale_version_is_rejected():
    lines = _read_lines()
    original = json.loads(lines[-1])
    correction = _appended_row(original, value_delta=1)
    correction["assertionVersion"] = {
        "id": expected_assertion_version_id(correction),
        "supersedes": "av2:" + "0" * 64,
    }
    try:
        check_rows(
            [*lines, json.dumps(correction, separators=(",", ":"))], len(lines)
        )
    except ValueError as error:
        assert "the active version" in str(error)
    else:
        raise AssertionError("stale supersedes target was accepted")
