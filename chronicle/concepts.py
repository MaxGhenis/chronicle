"""Concept-alignment validation for Chronicle facts."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from chronicle.core import AggregateFact, build_fact_key


@dataclass(frozen=True)
class ConceptAlignment:
    """One source-to-canonical concept alignment asserted by Chronicle."""

    canonical_concept: str
    source_concept: str
    relation: str
    fact_key: str
    source_record_id: str | None
    authority: str | None
    evidence_url: str | None
    evidence_notes: str | None
    legal_vintage: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable alignment."""
        data = asdict(self)
        return {key: value for key, value in data.items() if value is not None}


@dataclass(frozen=True)
class ConceptAlignmentIssue:
    """One concept-alignment validation issue."""

    code: str
    message: str
    canonical_concept: str | None = None
    source_concept: str | None = None
    fact_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable issue."""
        data = asdict(self)
        return {key: value for key, value in data.items() if value is not None}


@dataclass(frozen=True)
class ConceptAlignmentReport:
    """Validation report for source-to-canonical concept alignments."""

    alignment_count: int
    checked_count: int
    alignments: tuple[ConceptAlignment, ...]
    errors: tuple[ConceptAlignmentIssue, ...]
    warnings: tuple[ConceptAlignmentIssue, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether no validation errors were found."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "valid": self.valid,
            "alignment_count": self.alignment_count,
            "checked_count": self.checked_count,
            "alignments": [alignment.to_dict() for alignment in self.alignments],
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


def collect_concept_alignments(
    facts: list[AggregateFact],
) -> tuple[ConceptAlignment, ...]:
    """Collect unique source-to-canonical concept alignments from facts.

    Two facts collapse into one alignment only when every asserted field
    matches exactly (same fact identity, source record, authority, and
    evidence) — not merely the same source/canonical/relation/vintage
    tuple, which would otherwise hide distinct assertions' evidence.
    """
    alignments: dict[ConceptAlignment, None] = {}
    for fact in facts:
        measure = fact.measure
        if not measure.source_concept or not measure.concept_relation:
            continue
        alignment = ConceptAlignment(
            canonical_concept=measure.concept,
            source_concept=measure.source_concept,
            relation=measure.concept_relation,
            fact_key=build_fact_key(fact),
            source_record_id=fact.source_record_id,
            authority=measure.concept_authority,
            evidence_url=measure.concept_evidence_url,
            evidence_notes=measure.concept_evidence_notes,
            legal_vintage=measure.legal_vintage,
        )
        alignments.setdefault(alignment, None)
    return tuple(alignments.keys())


DEFAULT_AXIOM_VALIDATE_TIMEOUT_SECONDS = 30.0


def validate_concept_alignments(
    facts: list[AggregateFact],
    *,
    axiom_command: Sequence[str] | None = None,
    axiom_roots: Sequence[str | Path] = (),
    axiom_timeout: float = DEFAULT_AXIOM_VALIDATE_TIMEOUT_SECONDS,
) -> ConceptAlignmentReport:
    """Validate Chronicle concept alignments against optional Axiom CLI metadata."""
    alignments = collect_concept_alignments(facts)
    errors = list(_alignment_metadata_errors(alignments))
    warnings: list[ConceptAlignmentIssue] = []
    checked_count = 0

    if not alignments:
        return ConceptAlignmentReport(
            alignment_count=0,
            checked_count=0,
            alignments=(),
            errors=tuple(errors),
            warnings=(),
        )

    if axiom_command is None:
        warnings.append(
            ConceptAlignmentIssue(
                code="axiom_cli_not_configured",
                message="Axiom CLI validation skipped because no command was provided.",
            )
        )
        return ConceptAlignmentReport(
            alignment_count=len(alignments),
            checked_count=checked_count,
            alignments=alignments,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    result_cache: dict[str, dict[str, Any]] = {}
    for alignment in alignments:
        result = result_cache.get(alignment.canonical_concept)
        if result is None:
            try:
                result = _run_axiom_validate(
                    axiom_command,
                    alignment.canonical_concept,
                    axiom_roots=axiom_roots,
                    timeout=axiom_timeout,
                )
            except _AxiomValidateUnavailable as unavailable:
                warnings.append(
                    ConceptAlignmentIssue(
                        code=unavailable.code,
                        message=unavailable.message,
                        canonical_concept=alignment.canonical_concept,
                        source_concept=alignment.source_concept,
                        fact_key=alignment.fact_key,
                    )
                )
                break
            result_cache[alignment.canonical_concept] = result
        checked_count += 1
        if not result.get("valid"):
            message = _axiom_error_message(result)
            errors.append(
                ConceptAlignmentIssue(
                    code="axiom_concept_invalid",
                    message=message,
                    canonical_concept=alignment.canonical_concept,
                    source_concept=alignment.source_concept,
                    fact_key=alignment.fact_key,
                )
            )

    return ConceptAlignmentReport(
        alignment_count=len(alignments),
        checked_count=checked_count,
        alignments=alignments,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _alignment_metadata_errors(
    alignments: tuple[ConceptAlignment, ...],
) -> tuple[ConceptAlignmentIssue, ...]:
    errors = []
    for alignment in alignments:
        if alignment.relation == "exact" and not (
            alignment.evidence_url or alignment.evidence_notes
        ):
            errors.append(
                ConceptAlignmentIssue(
                    code="missing_concept_evidence",
                    message="Exact source-to-canonical concept alignments need evidence.",
                    canonical_concept=alignment.canonical_concept,
                    source_concept=alignment.source_concept,
                    fact_key=alignment.fact_key,
                )
            )
    return tuple(errors)


class _AxiomValidateUnavailable(Exception):
    """Raised when the Axiom CLI could not be invoked or timed out."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _run_axiom_validate(
    axiom_command: Sequence[str],
    concept_id: str,
    *,
    axiom_roots: Sequence[str | Path],
    timeout: float = DEFAULT_AXIOM_VALIDATE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run the Axiom CLI's concept-validate protocol for one concept.

    Returns a dict report (a synthesized invalid report if the CLI's
    response is malformed, contradictory, or for the wrong concept) or
    raises ``_AxiomValidateUnavailable`` if the CLI could not be run at
    all. Never treats an unavailable/timed-out run as a valid concept.
    """
    command = [
        *axiom_command,
        "concepts",
        "validate",
        concept_id,
        "--json",
    ]
    for root in axiom_roots:
        command.extend(["--root", str(root)])
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise _AxiomValidateUnavailable(
            code="axiom_cli_timeout",
            message=f"Axiom CLI validation timed out after {timeout:g}s.",
        ) from None
    except OSError as error:
        raise _AxiomValidateUnavailable(
            code="axiom_cli_unavailable",
            message=f"Axiom CLI validation skipped because command failed to run: {error}",
        ) from None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return _invalid_axiom_result(
            "invalid_axiom_cli_output",
            completed.stderr.strip() or "Axiom CLI did not emit JSON.",
        )
    if not isinstance(payload, dict):
        return _invalid_axiom_result(
            "invalid_axiom_cli_output",
            "Axiom CLI emitted non-object JSON.",
        )
    return _resolve_axiom_result(payload, concept_id, completed.returncode)


def _invalid_axiom_result(code: str, message: str) -> dict[str, Any]:
    return {
        "valid": False,
        "errors": [{"code": code, "message": message}],
    }


def _resolve_axiom_result(
    payload: dict[str, Any],
    concept_id: str,
    returncode: int,
) -> dict[str, Any]:
    """Apply strict boolean/exit-status checks to a parsed Axiom CLI response."""
    response_concept_id = payload.get("concept_id")
    if response_concept_id is not None and response_concept_id != concept_id:
        return _invalid_axiom_result(
            "axiom_cli_concept_mismatch",
            f"Axiom CLI response concept_id {response_concept_id!r} does not "
            f"match the requested concept {concept_id!r}.",
        )

    valid_field = payload.get("valid")
    if valid_field is not True and valid_field is not False:
        return _invalid_axiom_result(
            "invalid_axiom_cli_output",
            "Axiom CLI 'valid' field must be a boolean.",
        )

    errors_field = payload.get("errors")
    has_reported_errors = isinstance(errors_field, list) and bool(errors_field)

    if valid_field and has_reported_errors:
        return _invalid_axiom_result(
            "axiom_cli_contradictory_response",
            "Axiom CLI reported valid=true alongside validation errors.",
        )
    if valid_field and returncode != 0:
        return _invalid_axiom_result(
            "axiom_cli_exit_mismatch",
            f"Axiom CLI exited {returncode} but reported valid=true; "
            "treating the concept as invalid.",
        )
    return payload


def _axiom_error_message(result: dict[str, Any]) -> str:
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict) and first.get("message"):
            return str(first["message"])
    return "Axiom CLI reported invalid concept."
