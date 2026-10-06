#!/usr/bin/env python3
"""Gate every change to the thesis-facts observation ledger.

The observation file is append-only with an immutable frozen prefix
(``ledger/immutable_prefix.json``). Resolver appends arrive as pull
requests; this checker is the deterministic review each proposal must pass
before merge:

- the frozen prefix is byte-identical (no rewrite, no truncation);
- against a base ref, the change only appends whole lines;
- every appended row parses, and carries the post-quarantine bindings:
  ``assertionVersion`` (content-addressed, recomputed here), ``retrievedAt``,
  ``sourceVintage``, ``ledgerRepoSha``, and a ``responseArchive`` digest;
- ``targetContentHash`` and ``sourceBindingProjection`` appear together or
  not at all, the projection's response digest matches the archive, and its
  unit matches the row's measure unit;
- a duplicate ``source_record_id`` is legal only as an explicit correction:
  the later row's ``assertionVersion.supersedes`` must name the version ID
  of the row it replaces.
- after witnessed genesis, every byte append carries exactly one next canonical
  release manifest and two independently anchored RFC 3161 receipts; all prior
  release files remain byte-immutable against the PR's base commit.

Usage:
    python3 scripts/check_thesis_facts_append.py [--base-ref REF]

With ``--base-ref`` (CI: the pull request's base commit) the append-only
diff is enforced; without it only the full-file invariants run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

from canonical_json import canonical_sha256
from verify_release_chain import (
    ANCHORS,
    MANIFEST_RE,
    ReleaseChainError,
    git_blob_bytes,
    git_file_entry,
    verify_base_release_chain,
    verify_release_chain,
    verify_release_history_immutable,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "ledger" / "official_observations.jsonl"
PREFIX_PATH = ROOT / "ledger" / "immutable_prefix.json"
RELEASE_MANIFEST_PREFIX = "releases/manifests/"
GENESIS_SUPPORT_FILES = {
    "releases/README.md",
    *(f"releases/anchors/{spec.filename}" for spec in ANCHORS.values()),
}

ASSERTION_CONTENT_KEYS = (
    "source_record_id",
    "value",
    "observed_at",
    "period",
    "geography",
    "entity",
    "aggregation",
    "filters",
    "domain",
)


class AppendError(ValueError):
    """The proposed ledger change violates an append invariant."""


def _lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.strip()]


def reject_non_append_bytes(text: str) -> None:
    """Reject blank/whitespace-only lines and any non-single trailing newline.

    ``_lines`` drops blank lines so row parsing is convenient, but that means a
    blank line inserted into the frozen JSONL would normalize away and pass both
    the prefix hash and the append-only diff. A JSONL row is exactly one
    non-empty line: a blank/whitespace-only line inside the covered region is a
    byte tamper, and the file must end with exactly one trailing newline.
    """
    parts = text.split("\n")
    if parts[-1] != "":
        raise AppendError("ledger must end with exactly one trailing newline")
    for index, part in enumerate(parts[:-1], start=1):
        if not part.strip():
            raise AppendError(
                f"line {index} is blank or whitespace-only; a JSONL row is one "
                "non-empty line and a stray blank line is a tamper"
            )


def expected_assertion_version_id(row: dict[str, Any]) -> str:
    """Recompute the content address the resolver must have written.

    Mirrors ``assertion_version`` in the Thesis resolver (av1 v2 spec): the ID
    commits to everything that changes what the assertion MEANS — identity,
    value, timing, population, the complete measure concept mapping, exact
    source lineage/digest, row/cell lineage, and the archived response digest —
    so an in-place edit is detectable and a correction must supersede
    explicitly. This projection must stay byte-identical to the Brier writer's
    ``assertion_version`` (both fed to the shared ``canonical_sha256``), so any
    change here is a coordinated schema migration on both sides.
    """
    measure = row.get("measure") or {}
    source = row.get("source") or {}
    projection = {key: row.get(key) for key in ASSERTION_CONTENT_KEYS}
    projection["measure"] = {
        "concept": measure.get("concept"),
        "unit": measure.get("unit"),
        "source_concept": measure.get("source_concept"),
        "concept_relation": measure.get("concept_relation"),
        "concept_authority": measure.get("concept_authority"),
        "legal_vintage": measure.get("legal_vintage"),
    }
    projection["source"] = {
        "source_name": source.get("source_name"),
        "source_table": source.get("source_table"),
        "source_file": source.get("source_file"),
        "url": source.get("url"),
        "vintage": source.get("vintage"),
        "source_sha256": source.get("source_sha256"),
    }
    projection["lineage"] = {
        "source_row_keys": row.get("source_row_keys"),
        "source_cell_keys": row.get("source_cell_keys"),
    }
    projection["responseArchiveSha256"] = (row.get("responseArchive") or {}).get(
        "sha256"
    )
    return f"av2:{canonical_sha256(projection)}"


def _effective_assertion_id(row: dict[str, Any]) -> str:
    """Return the row's effective assertion version ID.

    Post-cutover rows carry an explicit ``assertionVersion.id`` (validated
    against the recomputed content address in :func:`check_rows`); legacy
    pre-versioning rows are addressable by their recomputed content address.
    Either way every row has exactly one effective ID that a correction must
    name and that no later row may reissue.
    """
    version = row.get("assertionVersion")
    if isinstance(version, dict) and version.get("id"):
        return str(version["id"])
    return expected_assertion_version_id(row)


def effective_current_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the latest non-superseded row per assertion identity.

    A correction names the version it replaces via
    ``assertionVersion.supersedes``; the replaced row drops out of the current
    view. Aggregate-fact validation runs on this supersede-aware view so a
    legitimate correction (same semantic key, new value) is not mistaken for a
    duplicate key.
    """
    superseded: set[str] = set()
    for row in rows:
        version = row.get("assertionVersion")
        if isinstance(version, dict) and version.get("supersedes"):
            superseded.add(str(version["supersedes"]))
    return [row for row in rows if _effective_assertion_id(row) not in superseded]


def check_prefix(lines: list[str]) -> dict[str, Any]:
    prefix = json.loads(PREFIX_PATH.read_text())
    if prefix.get("schemaVersion") != "thesis_facts_immutable_prefix_v1":
        raise AppendError(
            f"unsupported prefix manifest schema {prefix.get('schemaVersion')!r}"
        )
    count = int(prefix["prefixLineCount"])
    hashes = prefix["lineSha256s"]
    if len(hashes) != count:
        raise AppendError("prefix manifest line hashes disagree with its count")
    if len(lines) < count:
        raise AppendError(
            f"ledger has {len(lines)} rows but the immutable prefix "
            f"requires at least {count}"
        )
    for index in range(count):
        digest = hashlib.sha256(lines[index].encode("utf-8")).hexdigest()
        if digest != hashes[index]:
            row_id = json.loads(lines[index]).get("source_record_id", "?")
            raise AppendError(
                f"immutable prefix line {index + 1} ({row_id}) was rewritten"
            )
    joined = hashlib.sha256(
        ("\n".join(lines[:count]) + "\n").encode("utf-8")
    ).hexdigest()
    if joined != prefix["prefixSha256"]:
        raise AppendError("immutable prefix cumulative hash mismatch")
    return prefix


def check_rows(lines: list[str], prefix_count: int) -> None:
    versions: dict[str, int] = {}
    active_by_record_id: dict[str, tuple[int, str | None]] = {}
    for number, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AppendError(f"line {number} is not valid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise AppendError(f"line {number} is not a JSON object")
        record_id = row.get("source_record_id")
        if not record_id:
            raise AppendError(f"line {number} lacks source_record_id")
        if not isinstance(row.get("value"), (int, float)):
            raise AppendError(f"line {number} ({record_id}) has no numeric value")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("observed_at", ""))):
            raise AppendError(f"line {number} ({record_id}) has no observed_at date")
        unit = (row.get("measure") or {}).get("unit")
        if not unit:
            raise AppendError(f"line {number} ({record_id}) has no measure unit")

        recomputed = expected_assertion_version_id(row)
        version = row.get("assertionVersion")
        supersedes = None
        if version is not None:
            if not isinstance(version, dict):
                raise AppendError(f"line {number} assertionVersion is not an object")
            version_id = str(version.get("id", ""))
            supersedes = version.get("supersedes")
            if version_id != recomputed:
                raise AppendError(
                    f"line {number} ({record_id}) assertionVersion.id does not "
                    f"match its content ({version_id} != {recomputed})"
                )
            effective_id = version_id
        else:
            # Pre-versioning rows are addressable by their recomputed content
            # address; that ID is reserved just like an explicit one so a legacy
            # synthetic ID cannot be silently reissued.
            effective_id = recomputed

        # Reserve the effective ID of EVERY row. A collision means two rows
        # claim the same assertion version — a duplicate legacy ID or an
        # A->B->A chain trying to restore a superseded value.
        if effective_id in versions:
            raise AppendError(
                f"line {number} restates assertion version {effective_id} "
                f"from line {versions[effective_id]}"
            )
        versions[effective_id] = number

        if number > prefix_count:
            for field in (
                "retrievedAt",
                "sourceVintage",
                "ledgerRepoSha",
                "responseArchive",
                "assertionVersion",
            ):
                if not row.get(field):
                    raise AppendError(
                        f"appended line {number} ({record_id}) lacks {field}"
                    )
            archive = row["responseArchive"]
            if not isinstance(archive, dict) or not archive.get("sha256"):
                raise AppendError(
                    f"appended line {number} responseArchive lacks a digest"
                )
            has_hash = bool(row.get("targetContentHash"))
            projection = row.get("sourceBindingProjection")
            if has_hash != bool(projection):
                raise AppendError(
                    f"appended line {number} ({record_id}) must carry "
                    "targetContentHash and sourceBindingProjection together"
                )
            if projection:
                if projection.get("responseSha256") != archive.get("sha256"):
                    raise AppendError(
                        f"appended line {number} projection digest does not "
                        "match its archived response"
                    )
                if projection.get("unit") != unit:
                    raise AppendError(
                        f"appended line {number} projection unit "
                        f"{projection.get('unit')!r} contradicts the row unit "
                        f"{unit!r}"
                    )

        previous = active_by_record_id.get(str(record_id))
        if previous is not None:
            previous_line, previous_version = previous
            if supersedes is None:
                raise AppendError(
                    f"line {number} duplicates {record_id} (line "
                    f"{previous_line}) without superseding an assertion "
                    "version — corrections must be explicit"
                )
            if supersedes != previous_version:
                raise AppendError(
                    f"line {number} supersedes {supersedes} but the active "
                    f"version of {record_id} is {previous_version}"
                )
        elif supersedes is not None:
            raise AppendError(
                f"line {number} supersedes {supersedes} but {record_id} has "
                "no earlier row"
            )
        active_by_record_id[str(record_id)] = (number, effective_id)


def check_append_only(base_ref: str, lines: list[str]) -> int:
    relative = LEDGER_PATH.relative_to(ROOT).as_posix()
    try:
        base_text = subprocess.check_output(
            ["git", "show", f"{base_ref}:{relative}"], cwd=ROOT, text=True
        )
    except subprocess.CalledProcessError as exc:
        raise AppendError(f"cannot read {relative} at base {base_ref}") from exc
    base_lines = _lines(base_text)
    if len(lines) < len(base_lines):
        raise AppendError(
            f"change truncates the ledger: {len(base_lines)} -> {len(lines)} rows"
        )
    for index, line in enumerate(base_lines):
        if lines[index] != line:
            row_id = json.loads(line).get("source_record_id", "?")
            raise AppendError(
                f"change rewrites existing line {index + 1} ({row_id}); "
                "the ledger is append-only — supersede instead"
            )
    return len(lines) - len(base_lines)


def _manifest_at_ref(base_ref: str) -> dict[str, Any]:
    relative = PREFIX_PATH.relative_to(ROOT).as_posix()
    try:
        text = subprocess.check_output(
            ["git", "show", f"{base_ref}:{relative}"], cwd=ROOT, text=True
        )
    except subprocess.CalledProcessError as exc:
        raise AppendError(
            f"cannot read {relative} at base {base_ref}"
        ) from exc
    return json.loads(text)


def check_prefix_anchored_to_base(base_ref: str, candidate_prefix: dict[str, Any]) -> int:
    """Require the frozen prefix manifest to be unchanged from the base.

    The immutable-prefix manifest lives beside the ledger and is candidate-
    controlled, so a PR could grow ``prefixLineCount`` over its own append and
    have every post-cutover binding skipped (the appended row would count as
    "prefix"). Growing the frozen prefix is an explicit, separately reviewed
    migration — never part of the automated append path — so under a base ref
    the count, cumulative hash, and per-line hashes must match the base exactly.
    Returns the BASE prefix line count, which callers use as the post-cutover
    binding boundary so a candidate-controlled count can never move it.
    """
    base_prefix = _manifest_at_ref(base_ref)
    for field in ("prefixLineCount", "prefixSha256", "lineSha256s"):
        if candidate_prefix.get(field) != base_prefix.get(field):
            raise AppendError(
                f"immutable prefix manifest {field} changed vs base {base_ref}; "
                "the frozen prefix cannot grow through the automated append path "
                "— growing it is an explicit reviewed migration"
            )
    return int(base_prefix["prefixLineCount"])


def _release_triple(
    new_files: set[str],
    expected_index: int,
    *,
    allowed_support_files: set[str] | None = None,
) -> pathlib.Path:
    """Require exactly one new manifest and its two mapped receipt files."""

    manifest_files = [
        relative
        for relative in new_files
        if relative.startswith(RELEASE_MANIFEST_PREFIX)
        and MANIFEST_RE.fullmatch(pathlib.PurePosixPath(relative).name)
    ]
    if len(manifest_files) != 1:
        raise AppendError(
            f"release proposal must add exactly one manifest for index "
            f"{expected_index}; found {sorted(manifest_files)}"
        )
    manifest_relative = manifest_files[0]
    manifest_name = pathlib.PurePosixPath(manifest_relative).name
    match = MANIFEST_RE.fullmatch(manifest_name)
    assert match is not None
    if int(match.group("index")) != expected_index:
        raise AppendError(
            f"release proposal index must be {expected_index}, not "
            f"{int(match.group('index'))}"
        )
    stem = pathlib.PurePosixPath(manifest_name).stem
    expected = {
        manifest_relative,
        *(f"{RELEASE_MANIFEST_PREFIX}{stem}.{tsa}.tsr" for tsa in ANCHORS),
    }
    allowed = expected | (allowed_support_files or set())
    if new_files != expected and not (
        expected <= new_files and new_files <= allowed
    ):
        raise AppendError(
            "release proposal must add its manifest and exactly the freetsa "
            "and digicert receipts with no other releases/ changes; "
            f"missing={sorted(expected - new_files)}, "
            f"extra={sorted(new_files - allowed)}"
        )
    return ROOT / pathlib.PurePosixPath(manifest_relative)


def _base_ledger_bytes(commit: str) -> bytes:
    relative = LEDGER_PATH.relative_to(ROOT).as_posix()
    return git_blob_bytes(ROOT, git_file_entry(ROOT, commit, relative))


def _check_exact_byte_append(base_bytes: bytes, candidate_bytes: bytes) -> bytes:
    if not candidate_bytes.startswith(base_bytes):
        raise AppendError(
            "change is not an exact byte append to the base JSONL; existing "
            "bytes, including line endings, are immutable"
        )
    return candidate_bytes[len(base_bytes) :]


def check_release_proposal(
    base_ref: str,
    *,
    anchor_dir: pathlib.Path | None = None,
) -> int | None:
    """Verify the base chain and the one allowed candidate transition.

    A pre-genesis base keeps legacy append proposals valid only while they do
    not touch ``releases/``.  Genesis may add the two prescribed anchors and
    README alongside its exact manifest/receipt triple.  Once genesis exists,
    all base release files are byte- and mode-immutable and a ledger byte
    append must carry exactly one next release triple.
    """

    try:
        commit, new_files, base_release_entries = (
            verify_release_history_immutable(ROOT, base_ref)
        )
    except ReleaseChainError as exc:
        raise AppendError(str(exc)) from exc

    base_has_chain = any(
        relative.startswith(RELEASE_MANIFEST_PREFIX)
        for relative in base_release_entries
    )
    candidate_has_chain = any(
        path.is_file()
        for path in (ROOT / "releases" / "manifests").glob("*.json")
    ) if (ROOT / "releases" / "manifests").is_dir() else False
    base_bytes = _base_ledger_bytes(commit)
    candidate_bytes = LEDGER_PATH.read_bytes()
    appended_bytes = _check_exact_byte_append(base_bytes, candidate_bytes)
    ledger_changed = bool(appended_bytes)
    enforce_production_pins = anchor_dir is None

    if not base_has_chain:
        if not candidate_has_chain:
            if new_files:
                raise AppendError(
                    "legacy pre-genesis proposal must not change releases/; "
                    "add a complete genesis manifest and both receipts or no "
                    f"release files at all (changed={sorted(new_files)})"
                )
            return None
        _release_triple(
            new_files,
            0,
            allowed_support_files=GENESIS_SUPPORT_FILES,
        )
        try:
            verification = verify_release_chain(
                ROOT,
                anchor_dir=anchor_dir,
                require_chain=True,
                verify_state=True,
                enforce_production_pins=enforce_production_pins,
            )
        except ReleaseChainError as exc:
            raise AppendError(str(exc)) from exc
        if len(verification.releases) != 1:
            raise AppendError(
                "genesis proposal must create exactly one release at index 0"
            )
        return 0

    try:
        base_verification = verify_base_release_chain(
            ROOT,
            commit,
            base_release_entries,
            anchor_dir=anchor_dir,
            enforce_production_pins=enforce_production_pins,
        )
    except ReleaseChainError as exc:
        raise AppendError(f"base release chain is invalid: {exc}") from exc
    assert base_verification.head is not None
    expected_index = base_verification.head.release_index + 1

    if ledger_changed:
        _release_triple(new_files, expected_index)
    elif new_files:
        raise AppendError(
            "release-only proposal is forbidden after genesis; a next release "
            "must witness an actual ledger byte append"
        )

    try:
        candidate_verification = verify_release_chain(
            ROOT,
            anchor_dir=anchor_dir,
            require_chain=True,
            verify_state=True,
            enforce_production_pins=enforce_production_pins,
        )
    except ReleaseChainError as exc:
        raise AppendError(str(exc)) from exc
    expected_length = len(base_verification.releases) + (1 if ledger_changed else 0)
    if len(candidate_verification.releases) != expected_length:
        raise AppendError(
            f"release chain length must be {expected_length} for this proposal; "
            f"found {len(candidate_verification.releases)}"
        )
    return candidate_verification.head.release_index


def check_release_chain_without_base(
    *, anchor_dir: pathlib.Path | None = None
) -> int | None:
    """On push, verify any initialized chain against working-tree state."""

    manifest_directory = ROOT / "releases" / "manifests"
    if not manifest_directory.is_dir() or not any(manifest_directory.iterdir()):
        return None
    try:
        verification = verify_release_chain(
            ROOT,
            anchor_dir=anchor_dir,
            require_chain=True,
            verify_state=True,
            enforce_production_pins=anchor_dir is None,
        )
    except ReleaseChainError as exc:
        raise AppendError(str(exc)) from exc
    assert verification.head is not None
    return verification.head.release_index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-ref",
        help="enforce an append-only diff against this git ref",
    )
    parser.add_argument(
        "--release-anchor-dir",
        type=pathlib.Path,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    text = LEDGER_PATH.read_text(encoding="utf-8")
    try:
        reject_non_append_bytes(text)
        lines = _lines(text)
        prefix = check_prefix(lines)
        # The post-cutover binding boundary is the BASE prefix count under a
        # base ref, so a PR cannot grandfather an unbound append by growing the
        # candidate manifest over it. Without a base ref (push) there is nothing
        # to anchor against, so the candidate manifest is trusted for the
        # full-file invariants only — base-anchoring requires the PR path.
        binding_boundary = int(prefix["prefixLineCount"])
        appended = None
        if args.base_ref:
            binding_boundary = check_prefix_anchored_to_base(args.base_ref, prefix)
            appended = check_append_only(args.base_ref, lines)
        check_rows(lines, binding_boundary)
        release_index = (
            check_release_proposal(
                args.base_ref,
                anchor_dir=args.release_anchor_dir,
            )
            if args.base_ref
            else check_release_chain_without_base(
                anchor_dir=args.release_anchor_dir
            )
        )
    except AppendError as exc:
        print(f"thesis-facts append check failed: {exc}", file=sys.stderr)
        return 1
    suffix = f", +{appended} appended vs base" if appended is not None else ""
    release_suffix = (
        f", release {release_index}" if release_index is not None else ""
    )
    print(
        f"thesis-facts append check OK: {len(lines)} rows, immutable prefix "
        f"{prefix['prefixLineCount']}{suffix}{release_suffix}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
