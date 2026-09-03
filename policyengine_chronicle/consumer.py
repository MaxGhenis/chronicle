"""Build and verify facts-only Chronicle consumer artifacts.

Chronicle publishes source-backed fact rows and the hashes needed to verify
them. Selection, measurement, period-alignment, and model-binding contracts
belong to consumers such as Microcosm.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from chronicle.consumer_contract import _LEGACY_FACT_KEY_PAYLOAD_FIELD, _hash_key
from chronicle.core import (
    ALLOWED_ASSERTIONS,
    ALLOWED_PROVENANCE_CLASSES,
    DEFAULT_ASSERTION,
)
from chronicle.epoch import (
    EMIT_EPOCH,
    HASH_DOMAINS,
    SCHEMA_IDS,
    Epoch,
    canonicalize_key,
    hash_domain,
    schema_id,
)
from policyengine_chronicle.schema import (
    CONSUMER_FACT_SCHEMA_SHA256,
    validate_consumer_fact_row,
)

CONSUMER_ARTIFACT_SCHEMA_VERSION = schema_id("consumer_artifact")


@dataclass(frozen=True)
class ConsumerArtifact:
    """A verified facts-only Chronicle consumer artifact."""

    path: Path
    manifest: dict[str, Any]
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ConsumerArtifactBuildReport:
    """Build summary for one facts-only consumer artifact."""

    schema_version: str
    output_dir: str
    fact_row_count: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return asdict(self)


def build_consumer_artifact(
    output_dir: str | Path,
    *,
    facts_path: str | Path,
    replace: bool = False,
    emit_epoch: Epoch = EMIT_EPOCH,
) -> ConsumerArtifactBuildReport:
    """Build a reproducible facts-only artifact from consumer fact rows.

    ``facts_path`` is a ``consumer_facts.jsonl`` file or a bundle directory
    containing one. The artifact contains canonical fact rows and a manifest
    that pins their schema and content hashes. Target contracts are packaged
    by the consumer, not Chronicle.
    """
    output_path = Path(output_dir)
    if output_path.exists():
        if not replace:
            raise FileExistsError(
                f"Output directory exists: {output_path}. Pass replace=True."
            )
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True)

    rows = _load_consumer_rows(_resolve_facts_path(facts_path), validate_schema=True)
    facts_out = output_path / "consumer_facts.jsonl"
    with facts_out.open("w") as file:
        for row in rows:
            file.write(json.dumps(row, sort_keys=True))
            file.write("\n")

    artifact_schema_version = schema_id("consumer_artifact", emit_epoch)
    manifest = {
        "schema_version": artifact_schema_version,
        "consumer_fact_schema_versions": sorted(
            {row.get("schema_version") for row in rows}
        ),
        "consumer_fact_schema_sha256": CONSUMER_FACT_SCHEMA_SHA256,
        "fact_row_count": len(rows),
        "facts_sha256": _sha256_file(facts_out),
    }
    _write_json(output_path / "manifest.json", manifest)

    return ConsumerArtifactBuildReport(
        schema_version=artifact_schema_version,
        output_dir=str(output_path),
        fact_row_count=len(rows),
    )


def load_consumer_artifact(path: str | Path) -> ConsumerArtifact:
    """Load a facts-only consumer artifact and verify its manifest hashes."""
    artifact_path = Path(path)
    manifest = json.loads((artifact_path / "manifest.json").read_text())
    manifest_schema_version = manifest.get("schema_version")
    try:
        SCHEMA_IDS["consumer_artifact"].infer_identifier_epoch(manifest_schema_version)
    except ValueError as error:
        raise ValueError(
            "Unsupported consumer artifact schema_version: "
            f"{manifest_schema_version!r}; accepted forms are "
            f"{SCHEMA_IDS['consumer_artifact'].ledger!r} and "
            f"{SCHEMA_IDS['consumer_artifact'].chronicle!r}."
        ) from error
    if "profiles" in manifest:
        raise ValueError(
            "Consumer artifact manifests must not contain profiles; target profiles "
            "are consumer-owned contracts and must be loaded by Microcosm."
        )
    declared_fact_schema_versions = manifest.get("consumer_fact_schema_versions")
    if declared_fact_schema_versions is not None:
        if not isinstance(declared_fact_schema_versions, list):
            raise ValueError(
                "Consumer artifact consumer_fact_schema_versions must be a list."
            )
        for row_schema_version in declared_fact_schema_versions:
            try:
                SCHEMA_IDS["consumer_fact"].infer_identifier_epoch(row_schema_version)
            except ValueError as error:
                raise ValueError(
                    "Unsupported consumer fact schema_version in artifact "
                    f"manifest: {row_schema_version!r}; accepted forms are "
                    f"{SCHEMA_IDS['consumer_fact'].ledger!r} and "
                    f"{SCHEMA_IDS['consumer_fact'].chronicle!r}."
                ) from error
    manifest_schema_sha256 = manifest.get("consumer_fact_schema_sha256")
    if (
        manifest_schema_sha256 is not None
        and manifest_schema_sha256 != CONSUMER_FACT_SCHEMA_SHA256
    ):
        raise ValueError(
            "Consumer artifact declares consumer_fact_schema_sha256 "
            f"{manifest_schema_sha256!r}, which does not match the packaged "
            f"consumer-fact schema {CONSUMER_FACT_SCHEMA_SHA256!r}."
        )

    facts_file = artifact_path / "consumer_facts.jsonl"
    actual_sha256 = _sha256_file(facts_file)
    if actual_sha256 != manifest["facts_sha256"]:
        raise ValueError(
            "Consumer artifact fact rows do not match the manifest hash: "
            f"{actual_sha256} != {manifest['facts_sha256']}."
        )
    rows = _load_consumer_rows(facts_file, validate_schema=True)
    actual_fact_schema_versions = sorted({row.get("schema_version") for row in rows})
    if (
        declared_fact_schema_versions is not None
        and declared_fact_schema_versions != actual_fact_schema_versions
    ):
        raise ValueError(
            "Consumer artifact manifest declares consumer_fact_schema_versions "
            f"{declared_fact_schema_versions!r} but its rows use "
            f"{actual_fact_schema_versions!r}."
        )
    declared_row_count = manifest.get("fact_row_count")
    if declared_row_count is not None and declared_row_count != len(rows):
        raise ValueError(
            "Consumer artifact manifest declares fact_row_count "
            f"{declared_row_count} but the feed carries {len(rows)} rows."
        )
    return ConsumerArtifact(
        path=artifact_path,
        manifest=manifest,
        rows=tuple(rows),
    )


def _resolve_facts_path(facts_path: str | Path) -> Path:
    path = Path(facts_path)
    if path.is_dir():
        candidate = path / "consumer_facts.jsonl"
        if not candidate.exists():
            raise FileNotFoundError(
                f"No consumer_facts.jsonl in bundle directory {path}."
            )
        return candidate
    if not path.exists():
        raise FileNotFoundError(f"No consumer facts file at {path}.")
    return path


def _reject_non_finite(value: Any) -> Any:
    raise ValueError(f"Consumer fact contains a non-finite JSON number: {value!r}.")


def _assert_finite_numbers(value: Any, *, line_number: int, path: Path) -> None:
    """Reject non-finite numbers, including those in nested structures."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(
            f"Row {line_number} of {path} contains a non-finite number: {value!r}."
        )
    if isinstance(value, dict):
        for item in value.values():
            _assert_finite_numbers(item, line_number=line_number, path=path)
    elif isinstance(value, list):
        for item in value:
            _assert_finite_numbers(item, line_number=line_number, path=path)


def _verify_consumer_row_keys(
    row: dict[str, Any],
    *,
    line_number: int,
    path: Path,
) -> None:
    """Verify every identity whose canonical payload is carried by the row."""
    source = row["source"]
    observed_measure = row["observed_measure"]
    universe_constraints = _canonical_universe_constraints(row)
    concept_alignment = row.get("concept_alignment")
    canonical_concept = (
        concept_alignment["canonical_concept"]
        if concept_alignment is not None
        else observed_measure["source_concept"]
    )

    ledger_keys = {
        "source_release_key": _verify_consumer_row_key(
            row,
            field_name="source_release_key",
            domain_name="source_release",
            payload={
                "source_name": source.get("source_name"),
                "source_table": source.get("source_table"),
                "source_file": source.get("source_file"),
                "url": source.get("url"),
                "vintage": source.get("vintage"),
                "source_sha256": source.get("source_sha256"),
                "source_size_bytes": source.get("source_size_bytes"),
                "raw_r2_uri": source.get("raw_r2_uri"),
            },
            line_number=line_number,
            path=path,
        ),
        "source_series_key": _verify_consumer_row_key(
            row,
            field_name="source_series_key",
            domain_name="source_series",
            payload={
                "source_name": source.get("source_name"),
                "source_table": source.get("source_table"),
                "record_set_spec_id": row.get("layout", {}).get("record_set_spec_id"),
            },
            line_number=line_number,
            path=path,
        ),
        "observed_measure_key": _verify_consumer_row_key(
            row,
            field_name="observed_measure_key",
            domain_name="observed_measure",
            payload=observed_measure,
            line_number=line_number,
            path=path,
        ),
        "dimension_set_key": _verify_consumer_row_key(
            row,
            field_name="dimension_set_key",
            domain_name="dimension_set",
            payload=row["dimensions"],
            line_number=line_number,
            path=path,
        ),
        "universe_constraint_set_key": _verify_consumer_row_key(
            row,
            field_name="universe_constraint_set_key",
            domain_name="universe_constraint_set",
            payload=universe_constraints,
            line_number=line_number,
            path=path,
        ),
    }

    legacy_fact_payload = _legacy_fact_payload(
        row,
        canonical_concept=canonical_concept,
        concept_alignment=concept_alignment,
        universe_constraints=universe_constraints,
        line_number=line_number,
        path=path,
    )
    _verify_consumer_row_key(
        row,
        field_name="legacy_fact_key",
        domain_name="fact",
        payload=legacy_fact_payload,
        line_number=line_number,
        path=path,
        clean_payload=False,
    )
    _verify_consumer_row_key(
        row,
        field_name="semantic_fact_key",
        domain_name="semantic_fact",
        payload={
            "canonical_measure": {
                "concept": canonical_concept,
                "unit": observed_measure["unit"],
            },
            "aggregation": row["aggregation"],
            "period": row["period"],
            "geography": row["geography"],
            "entity": row["entity"],
            "universe_constraint_set_key": ledger_keys["universe_constraint_set_key"],
            "assertion": _identity_assertion(row),
        },
        line_number=line_number,
        path=path,
    )
    if concept_alignment is not None:
        _verify_consumer_row_key(
            concept_alignment,
            field_name="concept_alignment_key",
            domain_name="concept_alignment",
            payload={
                "observed_measure_key": ledger_keys["observed_measure_key"],
                "canonical_concept": concept_alignment["canonical_concept"],
                "relation": concept_alignment["relation"],
                "authority": concept_alignment["authority"],
                "legal_vintage": concept_alignment.get("legal_vintage"),
            },
            line_number=line_number,
            path=path,
            location="concept_alignment/concept_alignment_key",
        )
    _verify_consumer_row_key(
        row,
        field_name="aggregate_fact_key",
        domain_name="aggregate_fact",
        payload={
            "source_release_key": ledger_keys["source_release_key"],
            "source_series_key": ledger_keys["source_series_key"],
            "observed_measure_key": ledger_keys["observed_measure_key"],
            "aggregation": row["aggregation"],
            "period": row["period"],
            "geography": row["geography"],
            "entity": row["entity"],
            "dimension_set_key": ledger_keys["dimension_set_key"],
            "universe_constraint_set_key": ledger_keys["universe_constraint_set_key"],
            "assertion": _identity_assertion(row),
        },
        line_number=line_number,
        path=path,
    )


def _verify_consumer_row_key(
    container: Mapping[str, Any],
    *,
    field_name: str,
    domain_name: str,
    payload: dict[str, Any],
    line_number: int,
    path: Path,
    location: str | None = None,
    clean_payload: bool = True,
) -> str:
    """Verify one declared key and return its Ledger-canonical spelling."""
    declared_key = container[field_name]
    epoch = HASH_DOMAINS[domain_name].infer_key_epoch(declared_key)
    domain = hash_domain(domain_name, epoch)
    recomputed = (
        _hash_key(domain, payload)
        if clean_payload
        else _hash_uncleaned_key(domain, payload)
    )
    if declared_key != recomputed:
        key_location = location or field_name
        raise ValueError(
            f"Row {line_number} of {path} declares {key_location} "
            f"{declared_key!r} but its content hashes to {recomputed!r}; the "
            "identity key does not match the row."
        )
    return canonicalize_key(domain_name, declared_key)


def _canonical_universe_constraints(row: Mapping[str, Any]) -> dict[str, Any]:
    universe_constraints = row["universe_constraints"]
    constraints = sorted(
        universe_constraints.get("constraints", []),
        key=lambda constraint: json.dumps(
            constraint,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    return {
        "domain": universe_constraints["domain"],
        "constraints": constraints,
    }


def _legacy_fact_payload(
    row: Mapping[str, Any],
    *,
    canonical_concept: str,
    concept_alignment: Mapping[str, Any] | None,
    universe_constraints: Mapping[str, Any],
    line_number: int,
    path: Path,
) -> dict[str, Any]:
    """Rebuild the legacy fact-v1 identity payload represented by a row."""
    observed_measure = row["observed_measure"]
    source = row["source"]
    alignment = concept_alignment or {}
    payload = {
        "period": row["period"],
        "geography": {
            "level": row["geography"]["level"],
            "id": row["geography"]["id"],
            "vintage": row["geography"].get("vintage"),
        },
        "entity": {
            "name": row["entity"]["name"],
            "role": row["entity"].get("role"),
        },
        "measure": {
            "concept": canonical_concept,
            "unit": observed_measure["unit"],
            "source_concept": alignment.get("source_concept"),
            "concept_relation": alignment.get("relation"),
            "concept_authority": alignment.get("authority"),
            "concept_evidence_url": alignment.get("evidence_url"),
            "concept_evidence_notes": alignment.get("evidence_notes"),
            "legal_vintage": alignment.get("legal_vintage"),
        },
        "aggregation": {
            "method": row["aggregation"]["method"],
            "denominator": row["aggregation"].get("denominator"),
        },
        "domain": universe_constraints["domain"],
        "filters": {
            key: value
            for key, value in sorted(row["dimensions"].items())
            if value is not None
        },
        "source": {
            "source_name": source.get("source_name"),
            "source_table": source.get("source_table"),
            "source_file": source.get("source_file"),
            "vintage": source.get("vintage"),
        },
    }
    constraints = universe_constraints.get("constraints")
    if constraints:
        payload["constraints"] = _visible_legacy_constraints(constraints)
    assertion = _identity_assertion(row)
    if assertion is not None:
        payload["assertion"] = assertion

    envelope = row.get("layout", {}).get(_LEGACY_FACT_KEY_PAYLOAD_FIELD)
    if envelope is None:
        return payload
    if not isinstance(envelope, dict):
        raise _legacy_payload_mismatch(line_number=line_number, path=path)

    projected = dict(envelope)
    envelope_constraints = projected.get("constraints")
    if envelope_constraints is not None:
        if not isinstance(envelope_constraints, list) or any(
            not isinstance(constraint, dict)
            or set(constraint)
            != {"variable", "operator", "value", "unit", "role", "label"}
            for constraint in envelope_constraints
        ):
            raise _legacy_payload_mismatch(line_number=line_number, path=path)
        projected["constraints"] = _visible_legacy_constraints(envelope_constraints)

    if concept_alignment is None:
        envelope_measure = projected.get("measure")
        optional_measure_fields = {
            "source_concept",
            "concept_relation",
            "concept_authority",
            "concept_evidence_url",
            "concept_evidence_notes",
            "legal_vintage",
        }
        if not isinstance(envelope_measure, dict) or set(envelope_measure) != {
            "concept",
            "unit",
            *optional_measure_fields,
        }:
            raise _legacy_payload_mismatch(line_number=line_number, path=path)
        alignment_trigger_fields = {
            "source_concept",
            "concept_relation",
            "concept_authority",
            "legal_vintage",
        }
        if any(envelope_measure[field_name] for field_name in alignment_trigger_fields):
            raise _legacy_payload_mismatch(line_number=line_number, path=path)
        projected_measure = dict(envelope_measure)
        for field_name in optional_measure_fields:
            projected_measure[field_name] = None
        projected["measure"] = projected_measure

    if json.dumps(projected, sort_keys=True, separators=(",", ":")) != json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ):
        raise _legacy_payload_mismatch(line_number=line_number, path=path)
    return envelope


def _visible_legacy_constraints(
    constraints: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    visible = [
        {
            "variable": constraint["variable"],
            "operator": constraint["operator"],
            "value": constraint["value"],
            "unit": constraint.get("unit"),
            "role": constraint["role"],
            "label": None,
        }
        for constraint in constraints
    ]
    return sorted(
        visible,
        key=lambda constraint: json.dumps(
            constraint,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _legacy_payload_mismatch(*, line_number: int, path: Path) -> ValueError:
    return ValueError(
        f"Row {line_number} of {path} has layout."
        f"{_LEGACY_FACT_KEY_PAYLOAD_FIELD} content that does not match the "
        "consumer row."
    )


def _identity_assertion(row: Mapping[str, Any]) -> str | None:
    assertion = row.get("assertion")
    return None if assertion == DEFAULT_ASSERTION else assertion


def _hash_uncleaned_key(namespace: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{namespace}:{digest}"


def _load_consumer_rows(
    path: Path,
    *,
    validate_schema: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    with path.open() as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line, parse_constant=_reject_non_finite)
            _assert_finite_numbers(row, line_number=line_number, path=path)
            if validate_schema:
                validate_consumer_fact_row(row, line_number, path)
            _validate_consumer_row_provenance(row, line_number=line_number, path=path)
            assertion = row.setdefault("assertion", DEFAULT_ASSERTION)
            if assertion not in ALLOWED_ASSERTIONS:
                raise ValueError(
                    f"Row {line_number} of {path} has unsupported assertion "
                    f"{assertion!r}."
                )
            key = row.get("aggregate_fact_key")
            if validate_schema:
                _verify_consumer_row_keys(
                    row,
                    line_number=line_number,
                    path=path,
                )
            canonical_key = canonicalize_key("aggregate_fact", key)
            if canonical_key in seen_keys:
                raise ValueError(
                    f"Row {line_number} of {path} repeats aggregate_fact_key "
                    f"{key!r}; consumer artifact fact rows must be unique."
                )
            seen_keys.add(canonical_key)
            rows.append(row)
    return rows


def load_consumer_rows(path: str | Path) -> tuple[dict[str, Any], ...]:
    """Load and verify consumer-fact JSONL rows from either accepted epoch."""

    return tuple(_load_consumer_rows(Path(path), validate_schema=True))


def _validate_consumer_row_provenance(
    row: Mapping[str, Any],
    *,
    line_number: int,
    path: Path,
) -> None:
    if "provenance_class" not in row:
        raise ValueError(
            f"Row {line_number} of {path} is missing required provenance_class."
        )
    provenance_class = row["provenance_class"]
    if type(provenance_class) is not str or (
        provenance_class not in ALLOWED_PROVENANCE_CLASSES
    ):
        raise ValueError(
            f"Row {line_number} of {path} has unsupported provenance_class "
            f"{provenance_class!r}."
        )
    has_survey_instrument = "survey_instrument" in row
    survey_instrument = row.get("survey_instrument")
    if provenance_class == "survey_aggregate":
        if type(survey_instrument) is not str or not survey_instrument.strip():
            raise ValueError(
                f"Row {line_number} of {path} needs a non-empty "
                "survey_instrument for survey_aggregate provenance."
            )
    elif has_survey_instrument:
        raise ValueError(
            f"Row {line_number} of {path} has survey_instrument outside "
            "survey_aggregate provenance."
        )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "CONSUMER_ARTIFACT_SCHEMA_VERSION",
    "ConsumerArtifact",
    "ConsumerArtifactBuildReport",
    "build_consumer_artifact",
    "load_consumer_artifact",
    "load_consumer_rows",
]
