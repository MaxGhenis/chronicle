"""Tests for Chronicle concept-alignment validation."""

from __future__ import annotations

import json
import textwrap

import pytest

from chronicle.concepts import (
    collect_concept_alignments,
    validate_concept_alignments,
)
from chronicle.core import (
    Aggregation,
    EntityDimension,
    GeographyDimension,
    Measure,
    PeriodDimension,
    SourceProvenance,
    AggregateFact,
    build_fact_key,
)
from chronicle.harness import main as harness_main
from chronicle.store import save_facts_jsonl

AXIOM_CREDIT_CONCEPT = "us:statutes/26/45A/a#indian_employment_credit"
MISSING_CTC_CONCEPT = "us:statutes/26/24#child_tax_credit"
SOURCE_CREDIT_CONCEPT = "irs_soi.indian_employment_credit"


def _credit_fact(**overrides):
    fact = AggregateFact(
        value=123_000_000,
        period=PeriodDimension(type="tax_year", value=2023),
        geography=GeographyDimension(
            level="country",
            id="0100000US",
            vintage="2020_census",
            name="United States",
        ),
        entity=EntityDimension(name="return", role="filing_unit"),
        measure=Measure(
            concept=AXIOM_CREDIT_CONCEPT,
            unit="usd",
            source_concept=SOURCE_CREDIT_CONCEPT,
            concept_relation="exact",
            concept_authority="ledger-us",
            concept_evidence_notes=(
                "Synthetic fixture asserting a source credit column exactly "
                "adopts an encoded Axiom tax-credit concept."
            ),
            legal_vintage="tax_year_2023",
        ),
        aggregation=Aggregation(method="sum"),
        provenance_class="administrative",
        filters={"filing_status": "all"},
        source=SourceProvenance(
            source_name="irs_soi",
            source_table="Synthetic credit table",
            source_file="synthetic-credit.jsonl",
            url="https://example.org/synthetic-credit",
            vintage="tax_year_2023",
            extracted_at="2026-05-04",
            extraction_method="test fixture",
            method_notes="Synthetic concept-alignment fixture.",
        ),
        source_record_id="irs_soi.ty2023.synthetic.indian_employment_credit",
        label="United States 2023 tax year sum Indian employment credit",
    )
    return AggregateFact(**{**fact.__dict__, **overrides})


def test_collect_concept_alignments_deduplicates_assertions():
    fact = _credit_fact()
    duplicate_assertion = _credit_fact(value=456_000_000)

    alignments = collect_concept_alignments([fact, duplicate_assertion])

    assert len(alignments) == 1
    assert alignments[0].canonical_concept == AXIOM_CREDIT_CONCEPT
    assert alignments[0].source_concept == SOURCE_CREDIT_CONCEPT


def test_collect_concept_alignments_preserves_distinct_fact_evidence():
    first = _credit_fact(
        source_record_id="irs_soi.ty2023.synthetic.indian_employment_credit.first",
    )
    second = _credit_fact(
        source_record_id="irs_soi.ty2023.synthetic.indian_employment_credit.second",
        measure=Measure(
            concept=AXIOM_CREDIT_CONCEPT,
            unit="usd",
            source_concept=SOURCE_CREDIT_CONCEPT,
            concept_relation="exact",
            concept_authority="ledger-us",
            concept_evidence_notes=(
                "A second, independently sourced assertion of the same "
                "canonical/source concept pair with different evidence."
            ),
            legal_vintage="tax_year_2023",
        ),
    )

    alignments = collect_concept_alignments([first, second])

    assert len(alignments) == 2
    assert alignments[0].fact_key == build_fact_key(first)
    assert alignments[0].source_record_id == first.source_record_id
    assert alignments[0].evidence_notes == first.measure.concept_evidence_notes
    assert alignments[1].fact_key == build_fact_key(second)
    assert alignments[1].source_record_id == second.source_record_id
    assert alignments[1].evidence_notes == second.measure.concept_evidence_notes


def test_collect_concept_alignments_keeps_stable_input_order():
    facts = [
        _credit_fact(source_record_id=f"irs_soi.ty2023.synthetic.record.{index}")
        for index in range(3)
    ]

    alignments = collect_concept_alignments(facts)

    assert len(alignments) == 3
    assert [alignment.source_record_id for alignment in alignments] == [
        fact.source_record_id for fact in facts
    ]


def test_missing_evidence_is_not_hidden_by_a_sibling_records_evidence():
    fact_with_evidence = _credit_fact(
        source_record_id="irs_soi.ty2023.synthetic.indian_employment_credit.with_evidence",
    )
    fact_without_evidence = _credit_fact(
        source_record_id="irs_soi.ty2023.synthetic.indian_employment_credit.without_evidence",
        measure=Measure(
            concept=AXIOM_CREDIT_CONCEPT,
            unit="usd",
            source_concept=SOURCE_CREDIT_CONCEPT,
            concept_relation="exact",
            concept_authority="ledger-us",
            legal_vintage="tax_year_2023",
        ),
    )

    report = validate_concept_alignments([fact_with_evidence, fact_without_evidence])

    assert report.alignment_count == 2
    assert not report.valid
    assert [error.code for error in report.errors] == ["missing_concept_evidence"]
    assert report.errors[0].fact_key == build_fact_key(fact_without_evidence)


def test_validate_concept_alignments_warns_without_axiom_cli():
    report = validate_concept_alignments([_credit_fact()])

    assert report.valid
    assert report.alignment_count == 1
    assert report.checked_count == 0
    assert [warning.code for warning in report.warnings] == ["axiom_cli_not_configured"]


def test_exact_alignment_requires_evidence():
    fact = _credit_fact(
        measure=Measure(
            concept=AXIOM_CREDIT_CONCEPT,
            unit="usd",
            source_concept=SOURCE_CREDIT_CONCEPT,
            concept_relation="exact",
        )
    )

    report = validate_concept_alignments([fact])

    assert not report.valid
    assert [error.code for error in report.errors] == ["missing_concept_evidence"]


def test_validate_concept_alignments_checks_encoded_axiom_concept(tmp_path):
    axiom_cli = _write_fake_axiom_cli(tmp_path)

    report = validate_concept_alignments(
        [_credit_fact()],
        axiom_command=[str(axiom_cli)],
        axiom_roots=[tmp_path / "rules-us"],
    )

    assert report.valid
    assert report.alignment_count == 1
    assert report.checked_count == 1
    assert report.errors == ()


def test_validate_concept_alignments_reports_missing_axiom_concept(tmp_path):
    axiom_cli = _write_fake_axiom_cli(tmp_path)
    fact = _credit_fact(
        measure=Measure(
            concept=MISSING_CTC_CONCEPT,
            unit="usd",
            source_concept="irs_soi.child_tax_credit",
            concept_relation="exact",
            concept_authority="ledger-us",
            concept_evidence_notes="Synthetic CTC assertion before §24 exists.",
            legal_vintage="tax_year_2023",
        )
    )

    report = validate_concept_alignments(
        [fact],
        axiom_command=[str(axiom_cli)],
        axiom_roots=[tmp_path / "rules-us"],
    )

    assert not report.valid
    assert report.checked_count == 1
    assert [error.code for error in report.errors] == ["axiom_concept_invalid"]


def test_validate_concept_alignments_rejects_nonzero_exit_with_valid_true(tmp_path):
    axiom_cli = _write_scripted_axiom_cli(
        tmp_path,
        stdout_payload={
            "concept_id": AXIOM_CREDIT_CONCEPT,
            "valid": True,
            "errors": [],
        },
        exit_code=1,
    )

    report = validate_concept_alignments(
        [_credit_fact()],
        axiom_command=[str(axiom_cli)],
        axiom_roots=[tmp_path / "rules-us"],
    )

    assert not report.valid
    assert report.checked_count == 1
    assert [error.code for error in report.errors] == ["axiom_concept_invalid"]


def test_validate_concept_alignments_rejects_nonboolean_valid_field(tmp_path):
    axiom_cli = _write_scripted_axiom_cli(
        tmp_path,
        stdout_payload={
            "concept_id": AXIOM_CREDIT_CONCEPT,
            "valid": "true",
            "errors": [],
        },
        exit_code=0,
    )

    report = validate_concept_alignments(
        [_credit_fact()],
        axiom_command=[str(axiom_cli)],
        axiom_roots=[tmp_path / "rules-us"],
    )

    assert not report.valid
    assert report.checked_count == 1
    assert [error.code for error in report.errors] == ["axiom_concept_invalid"]


def test_validate_concept_alignments_rejects_missing_valid_field(tmp_path):
    axiom_cli = _write_scripted_axiom_cli(
        tmp_path,
        stdout_payload={"concept_id": AXIOM_CREDIT_CONCEPT},
        exit_code=0,
    )

    report = validate_concept_alignments(
        [_credit_fact()],
        axiom_command=[str(axiom_cli)],
        axiom_roots=[tmp_path / "rules-us"],
    )

    assert not report.valid
    assert report.checked_count == 1
    assert [error.code for error in report.errors] == ["axiom_concept_invalid"]


def test_validate_concept_alignments_rejects_non_object_json(tmp_path):
    scalar_cli = _write_raw_stdout_axiom_cli(tmp_path, "true", exit_code=0)

    report = validate_concept_alignments(
        [_credit_fact()],
        axiom_command=[str(scalar_cli)],
        axiom_roots=[tmp_path / "rules-us"],
    )

    assert not report.valid
    assert report.checked_count == 1
    assert [error.code for error in report.errors] == ["axiom_concept_invalid"]


def test_validate_concept_alignments_rejects_list_json(tmp_path):
    list_cli = _write_raw_stdout_axiom_cli(tmp_path, "[]", exit_code=0)

    report = validate_concept_alignments(
        [_credit_fact()],
        axiom_command=[str(list_cli)],
        axiom_roots=[tmp_path / "rules-us"],
    )

    assert not report.valid
    assert report.checked_count == 1
    assert [error.code for error in report.errors] == ["axiom_concept_invalid"]


def test_validate_concept_alignments_rejects_contradictory_valid_true_with_errors(
    tmp_path,
):
    axiom_cli = _write_scripted_axiom_cli(
        tmp_path,
        stdout_payload={
            "concept_id": AXIOM_CREDIT_CONCEPT,
            "valid": True,
            "errors": [{"code": "stale_review", "message": "Needs re-review."}],
        },
        exit_code=0,
    )

    report = validate_concept_alignments(
        [_credit_fact()],
        axiom_command=[str(axiom_cli)],
        axiom_roots=[tmp_path / "rules-us"],
    )

    assert not report.valid
    assert report.checked_count == 1
    assert [error.code for error in report.errors] == ["axiom_concept_invalid"]


def test_validate_concept_alignments_rejects_mismatched_concept_id(tmp_path):
    axiom_cli = _write_scripted_axiom_cli(
        tmp_path,
        stdout_payload={
            "concept_id": MISSING_CTC_CONCEPT,
            "valid": True,
            "errors": [],
        },
        exit_code=0,
    )

    report = validate_concept_alignments(
        [_credit_fact()],
        axiom_command=[str(axiom_cli)],
        axiom_roots=[tmp_path / "rules-us"],
    )

    assert not report.valid
    assert report.checked_count == 1
    assert [error.code for error in report.errors] == ["axiom_concept_invalid"]


def test_validate_concept_alignments_rejects_non_list_errors_field(tmp_path):
    for index, malformed_errors in enumerate(
        ["source unavailable", {"message": "bad concept"}, 1]
    ):
        axiom_cli = _write_scripted_axiom_cli(
            tmp_path,
            stdout_payload={
                "concept_id": AXIOM_CREDIT_CONCEPT,
                "valid": True,
                "errors": malformed_errors,
            },
            exit_code=0,
            name=f"axiom-malformed-errors-{index}",
        )

        report = validate_concept_alignments(
            [_credit_fact()],
            axiom_command=[str(axiom_cli)],
            axiom_roots=[tmp_path / "rules-us"],
        )

        assert not report.valid
        assert report.checked_count == 1
        assert [error.code for error in report.errors] == ["axiom_concept_invalid"]


def test_validate_concept_alignments_accepts_null_or_omitted_errors_field(tmp_path):
    for index, payload in enumerate(
        [
            {"concept_id": AXIOM_CREDIT_CONCEPT, "valid": True, "errors": None},
            {"concept_id": AXIOM_CREDIT_CONCEPT, "valid": True},
        ]
    ):
        axiom_cli = _write_scripted_axiom_cli(
            tmp_path,
            stdout_payload=payload,
            exit_code=0,
            name=f"axiom-null-errors-{index}",
        )

        report = validate_concept_alignments(
            [_credit_fact()],
            axiom_command=[str(axiom_cli)],
            axiom_roots=[tmp_path / "rules-us"],
        )

        assert report.valid
        assert report.checked_count == 1
        assert report.errors == ()


def test_validate_concept_alignments_rejects_explicit_null_concept_id(tmp_path):
    axiom_cli = _write_scripted_axiom_cli(
        tmp_path,
        stdout_payload={
            "concept_id": None,
            "valid": True,
            "errors": [],
        },
        exit_code=0,
    )

    report = validate_concept_alignments(
        [_credit_fact()],
        axiom_command=[str(axiom_cli)],
        axiom_roots=[tmp_path / "rules-us"],
    )

    assert not report.valid
    assert report.checked_count == 1
    assert [error.code for error in report.errors] == ["axiom_concept_invalid"]


def test_validate_concept_alignments_accepts_absent_concept_id_echo(tmp_path):
    axiom_cli = _write_scripted_axiom_cli(
        tmp_path,
        stdout_payload={"valid": True, "errors": []},
        exit_code=0,
    )

    report = validate_concept_alignments(
        [_credit_fact()],
        axiom_command=[str(axiom_cli)],
        axiom_roots=[tmp_path / "rules-us"],
    )

    assert report.valid
    assert report.checked_count == 1
    assert report.errors == ()


@pytest.mark.parametrize(
    "bad_timeout",
    [None, float("nan"), float("inf"), float("-inf"), 0, -1, -1.0, True, False, "5"],
)
def test_validate_concept_alignments_rejects_invalid_axiom_timeout(
    bad_timeout, tmp_path
):
    axiom_cli = _write_fake_axiom_cli(tmp_path)

    with pytest.raises(ValueError):
        validate_concept_alignments(
            [_credit_fact()],
            axiom_command=[str(axiom_cli)],
            axiom_roots=[tmp_path / "rules-us"],
            axiom_timeout=bad_timeout,
        )


@pytest.mark.parametrize("bad_timeout", [None, float("nan"), 0, True])
def test_axiom_timeout_validated_even_without_axiom_command(bad_timeout):
    with pytest.raises(ValueError):
        validate_concept_alignments([], axiom_timeout=bad_timeout)


def test_validate_concept_alignments_accepts_integer_axiom_timeout(tmp_path):
    axiom_cli = _write_fake_axiom_cli(tmp_path)

    report = validate_concept_alignments(
        [_credit_fact()],
        axiom_command=[str(axiom_cli)],
        axiom_roots=[tmp_path / "rules-us"],
        axiom_timeout=5,
    )

    assert report.valid
    assert report.checked_count == 1


def test_validate_concept_alignments_timeout_is_not_a_success(tmp_path):
    axiom_cli = _write_sleepy_axiom_cli(tmp_path, sleep_seconds=2)

    report = validate_concept_alignments(
        [_credit_fact()],
        axiom_command=[str(axiom_cli)],
        axiom_roots=[tmp_path / "rules-us"],
        axiom_timeout=0.05,
    )

    assert report.checked_count == 0
    assert report.errors == ()
    assert [warning.code for warning in report.warnings] == ["axiom_cli_timeout"]
    assert report.valid


def test_missing_command_leaves_all_assertions_unchecked(tmp_path):
    missing = tmp_path / "nonexistent-validator"
    assert not missing.exists()
    report = validate_concept_alignments(
        [_credit_fact(), _credit_fact(filters={"filing_status": "single"})],
        axiom_command=[str(missing)],
    )
    assert report.alignment_count == 2
    assert report.checked_count == 0
    assert report.errors == ()
    assert [warning.code for warning in report.warnings] == ["axiom_cli_unavailable"]


def test_zero_exit_cannot_override_explicit_invalid_concept(tmp_path):
    command = _write_scripted_axiom_cli(
        tmp_path, stdout_payload={"valid": False, "errors": []}, exit_code=0
    )
    report = validate_concept_alignments([_credit_fact()], axiom_command=[str(command)])
    assert report.checked_count == 1
    assert not report.valid
    assert [error.code for error in report.errors] == ["axiom_concept_invalid"]


def test_validate_concept_alignments_memoizes_repeated_concept_checks(tmp_path):
    axiom_cli, call_log = _write_call_counting_axiom_cli(tmp_path)
    first = _credit_fact(
        source_record_id="irs_soi.ty2023.synthetic.indian_employment_credit.first",
    )
    second = _credit_fact(
        source_record_id="irs_soi.ty2023.synthetic.indian_employment_credit.second",
        measure=Measure(
            concept=AXIOM_CREDIT_CONCEPT,
            unit="usd",
            source_concept=SOURCE_CREDIT_CONCEPT,
            concept_relation="exact",
            concept_authority="ledger-us",
            concept_evidence_notes=(
                "A second, independently sourced assertion of the same "
                "canonical/source concept pair with different evidence."
            ),
            legal_vintage="tax_year_2023",
        ),
    )

    report = validate_concept_alignments(
        [first, second],
        axiom_command=[str(axiom_cli)],
        axiom_roots=[tmp_path / "rules-us"],
    )

    assert report.alignment_count == 2
    assert report.checked_count == 2
    assert report.valid
    assert json.loads(call_log.read_text()) == [AXIOM_CREDIT_CONCEPT]


def test_validate_concept_alignment_cli_emits_json(tmp_path, capsys):
    axiom_cli = _write_fake_axiom_cli(tmp_path)
    fact_path = tmp_path / "facts.jsonl"
    save_facts_jsonl([_credit_fact()], fact_path)

    exit_code = harness_main(
        [
            "validate-concept-alignments",
            "--input",
            str(fact_path),
            "--axiom-cli",
            str(axiom_cli),
            "--axiom-root",
            str(tmp_path / "rules-us"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["valid"]
    assert payload["alignment_count"] == 1
    assert payload["checked_count"] == 1
    assert payload["errors"] == []


def _write_fake_axiom_cli(tmp_path):
    axiom_cli = tmp_path / "axiom"
    axiom_cli.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json
            import sys

            concept_id = sys.argv[sys.argv.index("validate") + 1]
            if concept_id == {AXIOM_CREDIT_CONCEPT!r}:
                print(json.dumps({{
                    "concept_id": concept_id,
                    "concept": {{
                        "concept_id": concept_id,
                        "status": "encoded",
                    }},
                    "errors": [],
                    "valid": True,
                }}))
                raise SystemExit(0)
            print(json.dumps({{
                "concept_id": concept_id,
                "errors": [
                    {{
                        "code": "concept_not_found",
                        "message": f"Concept {{concept_id}} is not available.",
                    }}
                ],
                "valid": False,
            }}))
            raise SystemExit(1)
            """
        )
    )
    axiom_cli.chmod(0o755)
    return axiom_cli


def _write_scripted_axiom_cli(tmp_path, *, stdout_payload, exit_code, name="axiom"):
    return _write_raw_stdout_axiom_cli(
        tmp_path,
        json.dumps(stdout_payload),
        exit_code=exit_code,
        name=name,
    )


def _write_raw_stdout_axiom_cli(tmp_path, stdout_text, *, exit_code, name="axiom"):
    axiom_cli = tmp_path / name
    axiom_cli.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            print({stdout_text!r})
            raise SystemExit({exit_code!r})
            """
        )
    )
    axiom_cli.chmod(0o755)
    return axiom_cli


def _write_sleepy_axiom_cli(tmp_path, *, sleep_seconds):
    axiom_cli = tmp_path / "axiom"
    axiom_cli.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import time
            time.sleep({sleep_seconds!r})
            print('{{"valid": true, "errors": []}}')
            raise SystemExit(0)
            """
        )
    )
    axiom_cli.chmod(0o755)
    return axiom_cli


def _write_call_counting_axiom_cli(tmp_path):
    call_log = tmp_path / "axiom-calls.json"
    call_log.write_text("[]")
    axiom_cli = tmp_path / "axiom"
    axiom_cli.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json
            import sys

            concept_id = sys.argv[sys.argv.index("validate") + 1]
            call_log = {str(call_log)!r}
            calls = json.loads(open(call_log).read())
            calls.append(concept_id)
            open(call_log, "w").write(json.dumps(calls))
            print(json.dumps({{
                "concept_id": concept_id,
                "errors": [],
                "valid": True,
            }}))
            raise SystemExit(0)
            """
        )
    )
    axiom_cli.chmod(0o755)
    return axiom_cli, call_log
