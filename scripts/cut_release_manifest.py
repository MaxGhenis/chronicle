#!/usr/bin/env python3
"""Cut the next witnessed thesis-ledger release for the working tree.

The cutter accepts a pending append after an already witnessed HEAD: it first
verifies the existing release chain without requiring that HEAD equal the
working-tree ledger, then proves that the old HEAD is the exact byte prefix of
the current ledger. A complete cut signs the exact manifest bytes, obtains both
RFC 3161 responses, and stages and verifies all four siblings before any release
file is created.
"""

from __future__ import annotations

import argparse
import http.client
import math
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from canonical_json import canonical_bytes
from verify_release_chain import (
    ANCHORS,
    DEFAULT_CLOCK_SKEW_SECONDS,
    LEDGER_RELATIVE,
    MANIFEST_RELATIVE,
    PREFIX_RELATIVE,
    ROOT,
    SCHEMA_VERSION,
    ChainVerification,
    ReleaseChainError,
    jsonl_line_offsets,
    load_manifest,
    manifest_filename,
    producer_signature_path_for_manifest,
    receipt_paths_for_manifest,
    sha256_bytes,
    validate_manifest_schema,
    verify_producer_signature,
    verify_producer_signature_bytes,
    verify_release_chain,
    verify_release_receipts,
)

MAX_TOKEN_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 45.0
TSA_ENDPOINTS = {
    "freetsa": "https://freetsa.org/tsr",
    # DigiCert's documented RFC 3161 endpoint is plain HTTP (their TLS
    # endpoint does not answer timestamp queries); the token itself is a
    # self-authenticating signature, verified against pinned anchors.
    "digicert": "http://timestamp.digicert.com",
}
Requester = Callable[[str, bytes, float], bytes]


class ReleaseCutError(RuntimeError):
    """The next release could not be constructed or safely written."""


def _validate_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReleaseCutError("timeout_seconds must be a finite positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ReleaseCutError("timeout_seconds must be a finite positive number")
    return result


def request_timestamp(endpoint: str, query: bytes, timeout_seconds: float) -> bytes:
    """POST one DER RFC 3161 query and return a size-bounded response."""

    if endpoint not in TSA_ENDPOINTS.values():
        raise ReleaseCutError(f"refusing unapproved TSA endpoint: {endpoint!r}")
    if type(query) is not bytes or not query:
        raise ReleaseCutError("RFC 3161 query must be non-empty bytes")
    timeout = _validate_timeout(timeout_seconds)
    request = urllib.request.Request(
        endpoint,
        data=query,
        headers={
            "Content-Type": "application/timestamp-query",
            "User-Agent": "Thesis-Ledger-Release-Witness/1",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        token = response.read(MAX_TOKEN_BYTES + 1)
    if not token:
        raise ReleaseCutError(f"TSA returned an empty response: {endpoint}")
    if len(token) > MAX_TOKEN_BYTES:
        raise ReleaseCutError(
            f"TSA response exceeds the one-megabyte limit: {endpoint}"
        )
    return token


def _run_git(root: pathlib.Path, arguments: list[str], label: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ReleaseCutError(
            f"git is required to auto-detect producer {label}"
        ) from exc
    if completed.returncode != 0:
        diagnostic = (completed.stderr or completed.stdout).strip()
        raise ReleaseCutError(
            f"cannot auto-detect producer {label}: {diagnostic or 'git command failed'}"
        )
    output = completed.stdout.strip()
    if not output or "\n" in output or "\r" in output:
        raise ReleaseCutError(f"cannot auto-detect a unique non-empty producer {label}")
    return output


def _validate_provenance(value: str, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ReleaseCutError(
            f"producer {label} must be a non-empty string without outer whitespace"
        )
    if len(value) > 1024 or any(
        ord(char) < 0x20 or ord(char) == 0x7F for char in value
    ):
        raise ReleaseCutError(
            f"producer {label} contains control characters or is too long"
        )
    return value


_SCP_REMOTE_RE = re.compile(r"(?:[^/@:]+@)?(?P<host>[^/:]+):(?P<path>[^?#]+)\Z")


def _normalize_remote(remote: str) -> str:
    """Return credential-free repository provenance from a Git remote URL."""

    remote = _validate_provenance(remote, "remote URL")
    host: str
    path: str
    if "://" in remote:
        parsed = urllib.parse.urlsplit(remote)
        if parsed.scheme not in {"https", "ssh", "git"}:
            raise ReleaseCutError(
                "cannot safely normalize the origin URL; pass --repo explicitly"
            )
        if parsed.query or parsed.fragment or not parsed.hostname:
            raise ReleaseCutError(
                "cannot safely normalize the origin URL; pass --repo explicitly"
            )
        host = parsed.hostname
        try:
            port = parsed.port
        except ValueError as exc:
            raise ReleaseCutError(
                "cannot safely normalize the origin URL; pass --repo explicitly"
            ) from exc
        if port is not None:
            host = f"{host}:{port}"
        path = parsed.path
    else:
        match = _SCP_REMOTE_RE.fullmatch(remote)
        if match is None:
            raise ReleaseCutError(
                "origin is not a network repository URL; pass --repo explicitly"
            )
        host = match.group("host")
        path = match.group("path")

    pieces = path.strip("/").split("/")
    if pieces and pieces[-1].endswith(".git"):
        pieces[-1] = pieces[-1][:-4]
    if not pieces or any(piece in {"", ".", ".."} for piece in pieces):
        raise ReleaseCutError(
            "cannot safely normalize the origin URL; pass --repo explicitly"
        )
    if host.lower() in {"github.com", "www.github.com"}:
        if len(pieces) != 2:
            raise ReleaseCutError(
                "GitHub origin is not an owner/repository URL; pass --repo explicitly"
            )
        return _validate_provenance("/".join(pieces), "repo")
    return _validate_provenance(f"{host}/{'/'.join(pieces)}", "repo")


def _git_root(root: pathlib.Path) -> None:
    detected = pathlib.Path(
        _run_git(root, ["rev-parse", "--show-toplevel"], "Git worktree")
    ).resolve()
    if detected != root:
        raise ReleaseCutError(
            f"--root must be the Git worktree root ({detected}), not {root}"
        )


def detect_producer(
    root: pathlib.Path,
    *,
    repo: str | None,
    branch: str | None,
) -> dict[str, str]:
    """Resolve producer claims without invoking a shell or recording credentials."""

    if repo is not None:
        selected_repo = _validate_provenance(repo, "repo")
    else:
        _git_root(root)
        selected_repo = _normalize_remote(
            _run_git(root, ["remote", "get-url", "origin"], "repo")
        )

    if branch is not None:
        selected_branch = _validate_provenance(branch, "branch")
    else:
        _git_root(root)
        selected_branch = _validate_provenance(
            _run_git(
                root,
                ["symbolic-ref", "--quiet", "--short", "HEAD"],
                "branch (detached HEAD; pass --branch explicitly)",
            ),
            "branch",
        )
    return {"repo": selected_repo, "branch": selected_branch}


def _regular_bytes(path: pathlib.Path, label: str) -> bytes:
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise ReleaseCutError(f"required {label} is missing: {path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ReleaseCutError(f"required {label} is not a regular file: {path}")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _created_at_utc(now: datetime | None) -> str:
    current = now or datetime.now(timezone.utc)
    if (
        not isinstance(current, datetime)
        or current.tzinfo is None
        or current.utcoffset() is None
    ):
        raise ReleaseCutError("now must be a timezone-aware datetime")
    try:
        current_utc = current.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ReleaseCutError("now cannot be represented as UTC") from exc
    return current_utc.isoformat(timespec="seconds").replace("+00:00", "Z")


def _build_manifest(
    ledger: bytes,
    immutable_prefix: bytes,
    existing: ChainVerification,
    producer: dict[str, str],
    *,
    now: datetime | None,
) -> tuple[dict[str, Any], bytes]:
    offsets = jsonl_line_offsets(ledger, LEDGER_RELATIVE.as_posix())
    line_count = len(offsets) - 1
    head = existing.head
    if head is None:
        release_index = 0
        previous_hash = None
        append = None
    else:
        previous_count = head.manifest["state"]["lineCount"]
        if previous_count > line_count:
            raise ReleaseCutError(
                f"working-tree ledger has {line_count} rows, fewer than witnessed "
                f"HEAD's {previous_count} rows"
            )
        witnessed_prefix = ledger[: offsets[previous_count]]
        witnessed_digest = sha256_bytes(witnessed_prefix)
        if witnessed_digest != head.manifest["state"]["jsonlSha256"]:
            raise ReleaseCutError(
                "working-tree ledger does not begin with the exact byte state "
                "committed by the witnessed HEAD"
            )
        immutable_digest = sha256_bytes(immutable_prefix)
        if immutable_digest != head.manifest["state"]["immutablePrefixSha256"]:
            raise ReleaseCutError(
                "ledger/immutable_prefix.json differs from the witnessed HEAD"
            )
        if line_count == previous_count:
            raise ReleaseCutError(
                "working-tree ledger has no pending rows after the witnessed HEAD"
            )
        release_index = head.release_index + 1
        previous_hash = head.sha256
        suffix = ledger[offsets[previous_count] :]
        append = {
            "previousLineCount": previous_count,
            "appendedRowCount": line_count - previous_count,
            "appendedBytesSha256": sha256_bytes(suffix),
        }

    manifest: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "releaseIndex": release_index,
        "previousManifestSha256": previous_hash,
        "state": {
            "path": LEDGER_RELATIVE.as_posix(),
            "jsonlSha256": sha256_bytes(ledger),
            "lineCount": line_count,
            "immutablePrefixSha256": sha256_bytes(immutable_prefix),
        },
        "append": append,
        "createdAtUtc": _created_at_utc(now),
        "producer": producer,
    }
    validate_manifest_schema(manifest)
    return manifest, canonical_bytes(manifest) + b"\n"


def _sign_manifest(
    manifest: bytes,
    signing_key: pathlib.Path,
    timeout_seconds: float,
) -> bytes:
    """Sign exact manifest bytes without reading private-key material in Python."""

    try:
        key_metadata = signing_key.lstat()
    except FileNotFoundError as exc:
        raise ReleaseCutError("producer signing key path does not exist") from exc
    if not stat.S_ISREG(key_metadata.st_mode):
        raise ReleaseCutError("producer signing key path is not a regular file")

    with tempfile.TemporaryDirectory(prefix="thesis-release-signature-") as name:
        temporary = pathlib.Path(name)
        manifest_path = temporary / "manifest.json"
        signature_path = temporary / "producer.sig"
        manifest_path.write_bytes(manifest)
        try:
            completed = subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-sign",
                    "-inkey",
                    str(signing_key),
                    "-rawin",
                    "-in",
                    str(manifest_path),
                    "-out",
                    str(signature_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env={**os.environ, "LC_ALL": "C", "OPENSSL_CONF": "/dev/null"},
            )
        except FileNotFoundError as exc:
            raise ReleaseCutError(
                "OpenSSL 3 is required for Ed25519 producer signing"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ReleaseCutError("OpenSSL producer signing timed out") from exc
        if completed.returncode != 0:
            diagnostic = (completed.stderr or completed.stdout).strip()
            raise ReleaseCutError(
                "OpenSSL producer signing failed: "
                f"{diagnostic[-1000:] or 'no diagnostic'}"
            )
        signature = _regular_bytes(signature_path, "producer signature")
    if len(signature) != 64:
        raise ReleaseCutError(
            "OpenSSL producer signing did not emit a 64-byte raw Ed25519 signature"
        )
    return signature


def _build_timestamp_query(manifest: bytes, timeout_seconds: float) -> bytes:
    with tempfile.TemporaryDirectory(prefix="thesis-release-query-") as name:
        temporary = pathlib.Path(name)
        manifest_path = temporary / "manifest.json"
        query_path = temporary / "request.tsq"
        manifest_path.write_bytes(manifest)
        try:
            completed = subprocess.run(
                [
                    "openssl",
                    "ts",
                    "-query",
                    "-config",
                    "/dev/null",
                    "-data",
                    str(manifest_path),
                    "-sha256",
                    "-cert",
                    "-out",
                    str(query_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env={**os.environ, "LC_ALL": "C", "OPENSSL_CONF": "/dev/null"},
            )
        except FileNotFoundError as exc:
            raise ReleaseCutError(
                "openssl is required to construct the RFC 3161 query"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ReleaseCutError(
                "OpenSSL timestamp-query construction timed out"
            ) from exc
        if completed.returncode != 0:
            diagnostic = (completed.stderr or completed.stdout).strip()
            raise ReleaseCutError(
                "OpenSSL timestamp-query construction failed: "
                f"{diagnostic[-1000:] or 'no diagnostic'}"
            )
        query = _regular_bytes(query_path, "RFC 3161 query")
    if not query or len(query) > MAX_TOKEN_BYTES:
        raise ReleaseCutError("OpenSSL produced an invalid-sized RFC 3161 query")
    return query


def _request_receipts(
    query: bytes,
    *,
    requester: Requester,
    timeout_seconds: float,
) -> dict[str, bytes]:
    receipts: dict[str, bytes] = {}
    for tsa, endpoint in TSA_ENDPOINTS.items():
        try:
            token = requester(endpoint, query, timeout_seconds)
        except (
            http.client.HTTPException,
            OSError,
            ReleaseCutError,
            urllib.error.URLError,
        ) as exc:
            raise ReleaseCutError(f"{tsa} timestamp request failed: {exc}") from exc
        except Exception as exc:
            raise ReleaseCutError(f"{tsa} timestamp request failed: {exc}") from exc
        if type(token) is not bytes or not token:
            raise ReleaseCutError(f"{tsa} TSA must return non-empty bytes")
        if len(token) > MAX_TOKEN_BYTES:
            raise ReleaseCutError(f"{tsa} TSA response exceeds the one-megabyte limit")
        receipts[tsa] = token
    return receipts


def _write_staged_tree(
    stage: pathlib.Path,
    existing: ChainVerification,
    ledger: bytes,
    immutable_prefix: bytes,
    filename: str,
    manifest: bytes,
    receipts: Mapping[str, bytes],
    producer_signature: bytes | None,
) -> None:
    ledger_path = stage / LEDGER_RELATIVE
    prefix_path = stage / PREFIX_RELATIVE
    manifest_dir = stage / MANIFEST_RELATIVE
    ledger_path.parent.mkdir(parents=True)
    manifest_dir.mkdir(parents=True)
    ledger_path.write_bytes(ledger)
    prefix_path.write_bytes(immutable_prefix)
    for record in existing.releases:
        (manifest_dir / record.path.name).write_bytes(record.raw)
        for tsa, receipt_path in record.receipt_paths.items():
            (manifest_dir / receipt_path.name).write_bytes(
                _regular_bytes(receipt_path, f"existing {tsa} receipt")
            )
        (manifest_dir / record.producer_signature_path.name).write_bytes(
            _regular_bytes(
                record.producer_signature_path,
                "existing producer signature",
            )
        )
    candidate = manifest_dir / filename
    candidate.write_bytes(manifest)
    for tsa, token in receipts.items():
        receipt_paths_for_manifest(candidate)[tsa].write_bytes(token)
    if producer_signature is not None:
        producer_signature_path_for_manifest(candidate).write_bytes(
            producer_signature
        )


def _verify_staged_release(
    existing: ChainVerification,
    ledger: bytes,
    immutable_prefix: bytes,
    filename: str,
    manifest: bytes,
    receipts: Mapping[str, bytes],
    producer_signature: bytes | None,
    *,
    anchor_dir: pathlib.Path,
    enforce_production_pins: bool,
    clock_skew_seconds: int,
) -> None:
    with tempfile.TemporaryDirectory(prefix="thesis-release-stage-") as name:
        stage = pathlib.Path(name)
        _write_staged_tree(
            stage,
            existing,
            ledger,
            immutable_prefix,
            filename,
            manifest,
            receipts,
            producer_signature,
        )
        candidate = stage / MANIFEST_RELATIVE / filename
        if set(receipts) == set(ANCHORS) and producer_signature is not None:
            verify_release_chain(
                stage,
                anchor_dir=anchor_dir,
                require_chain=True,
                verify_state=True,
                enforce_production_pins=enforce_production_pins,
                clock_skew_seconds=clock_skew_seconds,
            )
            return

        loaded, loaded_raw, digest = load_manifest(candidate)
        if loaded_raw != manifest:
            raise ReleaseCutError("staged manifest differs from constructed bytes")
        if receipts:
            if set(receipts) != set(ANCHORS):
                raise ReleaseCutError("staged release has an incomplete receipt set")
            staged_receipts = receipt_paths_for_manifest(candidate)
            verify_release_receipts(
                loaded,
                digest,
                staged_receipts,
                anchor_dir=anchor_dir,
                enforce_production_pins=enforce_production_pins,
                clock_skew_seconds=clock_skew_seconds,
                previous_times=(
                    existing.head.receipt_times if existing.head is not None else None
                ),
            )
        if producer_signature is not None:
            verify_producer_signature(
                loaded_raw,
                producer_signature_path_for_manifest(candidate),
                anchor_dir=anchor_dir,
                enforce_production_pin=enforce_production_pins,
            )


def _check_output_ancestors(root: pathlib.Path) -> None:
    for directory in (root / "releases", root / MANIFEST_RELATIVE):
        try:
            metadata = directory.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(metadata.st_mode):
            raise ReleaseCutError(
                f"release output parent is not a real directory: {directory}"
            )


def _check_targets_absent(paths: list[pathlib.Path]) -> None:
    for path in paths:
        if os.path.lexists(path):
            raise ReleaseCutError(f"refusing to overwrite release file: {path}")


def _exclusive_batch_write(
    root: pathlib.Path,
    payloads: Mapping[pathlib.Path, bytes],
    *,
    reserved_paths: list[pathlib.Path],
    verify_written: Callable[[], None],
) -> None:
    if not payloads:
        raise ReleaseCutError("no release files were provided for writing")
    reserved = list(reserved_paths)
    if not reserved:
        raise ReleaseCutError("no release paths were reserved for writing")
    parents = {path.parent for path in [*payloads, *reserved]}
    if parents != {root / MANIFEST_RELATIVE}:
        raise ReleaseCutError("release outputs escaped releases/manifests")
    reserved_by_name = {path.name: path for path in reserved}
    if len(reserved_by_name) != len(reserved):
        raise ReleaseCutError("duplicate release paths were reserved for writing")
    if not set(payloads).issubset(set(reserved)):
        raise ReleaseCutError("release payloads were not included in reserved paths")
    payload_names = {path.name for path in payloads}
    omitted_names = set(reserved_by_name) - payload_names
    _check_output_ancestors(root)
    _check_targets_absent(reserved)
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    root_fd: int | None = None
    releases_fd: int | None = None
    directory_fd: int | None = None
    root_identity: tuple[int, int] | None = None
    releases_identity: tuple[int, int] | None = None
    directory_identity: tuple[int, int] | None = None
    created_releases = False
    created_manifests = False
    created_entries: list[tuple[str, int, int]] = []
    succeeded = False

    def identity(metadata: os.stat_result) -> tuple[int, int]:
        return metadata.st_dev, metadata.st_ino

    def open_child_directory(parent_fd: int, name: str) -> int:
        try:
            descriptor = os.open(name, directory_flags, dir_fd=parent_fd)
        except OSError as exc:
            raise ReleaseCutError(
                f"release output component is not a stable real directory: {name}"
            ) from exc
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            os.close(descriptor)
            raise ReleaseCutError(
                f"release output component is not a real directory: {name}"
            )
        return descriptor

    def make_child_directory(parent_fd: int, name: str) -> bool:
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            return False
        except OSError as exc:
            raise ReleaseCutError(
                f"cannot create release output directory component: {name}"
            ) from exc
        return True

    def stat_entry(name: str) -> os.stat_result | None:
        assert directory_fd is not None
        try:
            return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def assert_names_absent(names: set[str], phase: str) -> None:
        for name in sorted(names):
            if stat_entry(name) is not None:
                raise ReleaseCutError(
                    "reserved release file appeared concurrently "
                    f"{phase}: {reserved_by_name[name]}"
                )

    def assert_output_path_matches(phase: str) -> None:
        assert root_identity is not None
        assert releases_identity is not None
        assert directory_identity is not None
        check_root_fd: int | None = None
        check_releases_fd: int | None = None
        check_directory_fd: int | None = None
        try:
            try:
                check_root_fd = os.open(root, directory_flags)
            except OSError as exc:
                raise ReleaseCutError(
                    f"repository root path changed {phase}: {root}"
                ) from exc
            if identity(os.fstat(check_root_fd)) != root_identity:
                raise ReleaseCutError(
                    f"repository root path was replaced {phase}: {root}"
                )
            check_releases_fd = open_child_directory(check_root_fd, "releases")
            if identity(os.fstat(check_releases_fd)) != releases_identity:
                raise ReleaseCutError(
                    f"release output parent was replaced {phase}: {root / 'releases'}"
                )
            check_directory_fd = open_child_directory(
                check_releases_fd,
                MANIFEST_RELATIVE.name,
            )
            if identity(os.fstat(check_directory_fd)) != directory_identity:
                raise ReleaseCutError(
                    "release manifest directory was replaced "
                    f"{phase}: {root / MANIFEST_RELATIVE}"
                )
        finally:
            for descriptor in (
                check_directory_fd,
                check_releases_fd,
                check_root_fd,
            ):
                if descriptor is not None:
                    os.close(descriptor)

    def assert_created_entries_match(phase: str) -> None:
        for name, expected_device, expected_inode in created_entries:
            descriptor: int | None = None
            try:
                flags = os.O_RDONLY
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                try:
                    descriptor = os.open(name, flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise ReleaseCutError(
                        f"created release file disappeared {phase}: {name}"
                    ) from exc
                metadata = os.fstat(descriptor)
                if identity(metadata) != (expected_device, expected_inode):
                    raise ReleaseCutError(
                        "created release file was replaced concurrently "
                        f"{phase}: {name}"
                    )
                expected_payload = payloads[reserved_by_name[name]]
                with os.fdopen(descriptor, "rb") as stream:
                    descriptor = None
                    actual_payload = stream.read(len(expected_payload) + 1)
                if actual_payload != expected_payload:
                    raise ReleaseCutError(
                        f"created release file bytes changed {phase}: {name}"
                    )
                current = stat_entry(name)
                if current is None or identity(current) != (
                    expected_device,
                    expected_inode,
                ):
                    raise ReleaseCutError(
                        "created release file was replaced concurrently "
                        f"{phase}: {name}"
                    )
            finally:
                if descriptor is not None:
                    os.close(descriptor)

    def remove_created_directory_if_owned(
        parent_fd: int,
        name: str,
        expected_identity: tuple[int, int] | None,
    ) -> None:
        if expected_identity is None:
            return
        try:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            return
        if identity(current) != expected_identity:
            return
        try:
            os.rmdir(name, dir_fd=parent_fd)
        except OSError:
            pass

    try:
        try:
            root_fd = os.open(root, directory_flags)
        except OSError as exc:
            raise ReleaseCutError(
                f"repository root is not a stable real directory: {root}"
            ) from exc
        root_identity = identity(os.fstat(root_fd))
        created_releases = make_child_directory(root_fd, "releases")
        releases_fd = open_child_directory(root_fd, "releases")
        releases_identity = identity(os.fstat(releases_fd))
        created_manifests = make_child_directory(
            releases_fd,
            MANIFEST_RELATIVE.name,
        )
        directory_fd = open_child_directory(
            releases_fd,
            MANIFEST_RELATIVE.name,
        )
        directory_identity = identity(os.fstat(directory_fd))
        assert_output_path_matches("before batch write")
        assert_names_absent(set(reserved_by_name), "before batch write")
        for path, payload in payloads.items():
            if stat_entry(path.name) is not None:
                raise ReleaseCutError(f"refusing to overwrite release file: {path}")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path.name, flags, 0o644, dir_fd=directory_fd)
            try:
                metadata = os.fstat(descriptor)
                created_entries.append(
                    (path.name, metadata.st_dev, metadata.st_ino)
                )
                stream = os.fdopen(descriptor, "wb")
                descriptor = -1
                with stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        os.fsync(directory_fd)
        assert_output_path_matches("before verification")
        assert_names_absent(omitted_names, "before verification")
        assert_created_entries_match("before verification")
        verify_written()
        assert_output_path_matches("after verification")
        assert_names_absent(omitted_names, "after verification")
        assert_created_entries_match("after verification")
        succeeded = True
    except BaseException as exc:
        cleanup_conflicts: list[str] = []
        if directory_fd is not None:
            for name, expected_device, expected_inode in reversed(created_entries):
                try:
                    current = stat_entry(name)
                except OSError as cleanup_error:
                    cleanup_conflicts.append(
                        f"{name} (cannot inspect: {cleanup_error})"
                    )
                    continue
                if current is None:
                    continue
                if (current.st_dev, current.st_ino) != (
                    expected_device,
                    expected_inode,
                ):
                    cleanup_conflicts.append(
                        f"{name} (concurrent replacement preserved)"
                    )
                    continue
                try:
                    os.unlink(name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
                except OSError as cleanup_error:
                    cleanup_conflicts.append(f"{name} ({cleanup_error})")
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
        if cleanup_conflicts:
            raise ReleaseCutError(
                "release write failed and rollback could not remove all reserved "
                "paths due to cleanup conflicts; concurrent files were preserved: "
                f"{cleanup_conflicts}; original error: {exc}"
            ) from exc
        raise
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        if not succeeded and created_manifests and releases_fd is not None:
            remove_created_directory_if_owned(
                releases_fd,
                MANIFEST_RELATIVE.name,
                directory_identity,
            )
        if releases_fd is not None:
            os.close(releases_fd)
        if not succeeded and created_releases and root_fd is not None:
            remove_created_directory_if_owned(
                root_fd,
                "releases",
                releases_identity,
            )
        if root_fd is not None:
            os.close(root_fd)


def _snapshot_existing_files(
    verification: ChainVerification,
) -> dict[pathlib.Path, bytes]:
    snapshot: dict[pathlib.Path, bytes] = {}
    for record in verification.releases:
        snapshot[record.path] = record.raw
        for tsa, path in record.receipt_paths.items():
            snapshot[path] = _regular_bytes(path, f"existing {tsa} receipt")
        snapshot[record.producer_signature_path] = _regular_bytes(
            record.producer_signature_path,
            "existing producer signature",
        )
    return snapshot


def _assert_unchanged(
    ledger_path: pathlib.Path,
    prefix_path: pathlib.Path,
    ledger: bytes,
    immutable_prefix: bytes,
    history: Mapping[pathlib.Path, bytes],
) -> None:
    if _regular_bytes(ledger_path, "ledger JSONL") != ledger:
        raise ReleaseCutError("ledger changed while the release was being cut")
    if _regular_bytes(prefix_path, "immutable-prefix manifest") != immutable_prefix:
        raise ReleaseCutError(
            "immutable-prefix manifest changed while the release was being cut"
        )
    for path, expected in history.items():
        if _regular_bytes(path, "existing release file") != expected:
            raise ReleaseCutError(
                f"existing release file changed while cutting the release: {path}"
            )


def cut_release_manifest(
    root: pathlib.Path = ROOT,
    *,
    repo: str | None = None,
    branch: str | None = None,
    no_tsa: bool = False,
    signing_key: pathlib.Path | None = None,
    no_sign: bool = False,
    anchor_dir: pathlib.Path | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS,
    requester: Requester | None = None,
    now: datetime | None = None,
) -> pathlib.Path:
    """Build and safely write the next release, returning its manifest path."""

    if type(no_tsa) is not bool:
        raise ReleaseCutError("no_tsa must be a boolean")
    if type(no_sign) is not bool:
        raise ReleaseCutError("no_sign must be a boolean")
    if no_sign and signing_key is not None:
        raise ReleaseCutError("signing_key and no_sign are mutually exclusive")
    if not no_sign and signing_key is None:
        raise ReleaseCutError("signing_key is required unless no_sign is set")
    if type(clock_skew_seconds) is not int or clock_skew_seconds < 0:
        raise ReleaseCutError("clock_skew_seconds must be a non-negative integer")
    timeout = _validate_timeout(timeout_seconds)
    try:
        selected_root = pathlib.Path(root).resolve(strict=True)
    except FileNotFoundError as exc:
        raise ReleaseCutError(f"repository root does not exist: {root}") from exc
    if not selected_root.is_dir():
        raise ReleaseCutError(f"repository root is not a directory: {selected_root}")
    selected_anchors = (
        pathlib.Path(anchor_dir).resolve()
        if anchor_dir is not None
        else selected_root / "releases" / "anchors"
    )
    enforce_production_pins = anchor_dir is None
    selected_signing_key = (
        pathlib.Path(signing_key).absolute() if signing_key is not None else None
    )

    existing = verify_release_chain(
        selected_root,
        anchor_dir=selected_anchors,
        require_chain=False,
        verify_state=True,
        allow_pending_append=True,
        enforce_production_pins=enforce_production_pins,
        clock_skew_seconds=clock_skew_seconds,
    )
    ledger_path = selected_root / LEDGER_RELATIVE
    prefix_path = selected_root / PREFIX_RELATIVE
    ledger = _regular_bytes(ledger_path, "ledger JSONL")
    immutable_prefix = _regular_bytes(prefix_path, "immutable-prefix manifest")
    producer = detect_producer(
        selected_root,
        repo=repo,
        branch=branch,
    )
    _manifest, raw = _build_manifest(
        ledger,
        immutable_prefix,
        existing,
        producer,
        now=now,
    )
    filename = manifest_filename(_manifest["releaseIndex"], raw)
    manifest_path = selected_root / MANIFEST_RELATIVE / filename
    receipt_paths = receipt_paths_for_manifest(manifest_path)
    producer_signature_path = producer_signature_path_for_manifest(manifest_path)
    all_targets = [
        manifest_path,
        *receipt_paths.values(),
        producer_signature_path,
    ]
    _check_output_ancestors(selected_root)
    _check_targets_absent(all_targets)
    history = _snapshot_existing_files(existing)

    producer_signature: bytes | None = None
    if selected_signing_key is not None:
        producer_signature = _sign_manifest(raw, selected_signing_key, timeout)
        verify_producer_signature_bytes(
            raw,
            producer_signature,
            anchor_dir=selected_anchors,
            enforce_production_pin=enforce_production_pins,
            label=producer_signature_path.name,
        )

    receipts: dict[str, bytes] = {}
    if not no_tsa:
        query = _build_timestamp_query(raw, timeout)
        receipts = _request_receipts(
            query,
            requester=request_timestamp if requester is None else requester,
            timeout_seconds=timeout,
        )

    _verify_staged_release(
        existing,
        ledger,
        immutable_prefix,
        filename,
        raw,
        receipts,
        producer_signature,
        anchor_dir=selected_anchors,
        enforce_production_pins=enforce_production_pins,
        clock_skew_seconds=clock_skew_seconds,
    )
    payloads: dict[pathlib.Path, bytes] = {manifest_path: raw}
    payloads.update(
        {receipt_paths[tsa]: token for tsa, token in receipts.items()}
    )
    if producer_signature is not None:
        payloads[producer_signature_path] = producer_signature

    def verify_committed_release() -> None:
        _assert_unchanged(
            ledger_path,
            prefix_path,
            ledger,
            immutable_prefix,
            history,
        )
        if set(receipts) == set(ANCHORS) and producer_signature is not None:
            verify_release_chain(
                selected_root,
                anchor_dir=selected_anchors,
                require_chain=True,
                verify_state=True,
                enforce_production_pins=enforce_production_pins,
                clock_skew_seconds=clock_skew_seconds,
            )
            return

        loaded, loaded_raw, digest = load_manifest(manifest_path)
        if loaded != _manifest or loaded_raw != raw:
            raise ReleaseCutError("written manifest differs from constructed bytes")
        if receipts:
            verify_release_receipts(
                loaded,
                digest,
                receipt_paths,
                anchor_dir=selected_anchors,
                enforce_production_pins=enforce_production_pins,
                clock_skew_seconds=clock_skew_seconds,
                previous_times=(
                    existing.head.receipt_times if existing.head is not None else None
                ),
            )
        if producer_signature is not None:
            verify_producer_signature(
                loaded_raw,
                producer_signature_path,
                anchor_dir=selected_anchors,
                enforce_production_pin=enforce_production_pins,
            )

    _assert_unchanged(
        ledger_path,
        prefix_path,
        ledger,
        immutable_prefix,
        history,
    )

    _exclusive_batch_write(
        selected_root,
        payloads,
        reserved_paths=all_targets,
        verify_written=verify_committed_release,
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="cut the next witnessed thesis-ledger release manifest"
    )
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=ROOT,
        help="repository root (default: this script's repository)",
    )
    parser.add_argument(
        "--anchor-dir",
        type=pathlib.Path,
        help="override TSA and producer anchors (intended for offline tests)",
    )
    parser.add_argument("--repo", help="override producer.repo provenance")
    parser.add_argument("--branch", help="override producer.branch provenance")
    signing = parser.add_mutually_exclusive_group(required=True)
    signing.add_argument(
        "--signing-key",
        type=pathlib.Path,
        help="Ed25519 private-key PEM path passed directly to OpenSSL",
    )
    signing.add_argument(
        "--no-sign",
        action="store_true",
        help="omit the producer signature for an explicitly partial release",
    )
    parser.add_argument(
        "--no-tsa",
        action="store_true",
        help="do not request or write TSA receipts",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--clock-skew-seconds",
        type=int,
        default=DEFAULT_CLOCK_SKEW_SECONDS,
    )
    args = parser.parse_args()
    try:
        manifest_path = cut_release_manifest(
            args.root,
            repo=args.repo,
            branch=args.branch,
            no_tsa=args.no_tsa,
            signing_key=args.signing_key,
            no_sign=args.no_sign,
            anchor_dir=args.anchor_dir,
            timeout_seconds=args.timeout_seconds,
            clock_skew_seconds=args.clock_skew_seconds,
        )
    except (OSError, ReleaseChainError, ReleaseCutError, ValueError) as exc:
        print(f"release cut failed: {exc}", file=sys.stderr)
        return 1
    if args.no_tsa and args.no_sign:
        print(
            "release manifest written without TSA receipts or producer signature: "
            f"{manifest_path}"
        )
    elif args.no_tsa:
        print(f"producer-signed release written without TSA receipts: {manifest_path}")
    elif args.no_sign:
        print(f"timestamped release written without producer signature: {manifest_path}")
    else:
        print(f"witnessed release written: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
