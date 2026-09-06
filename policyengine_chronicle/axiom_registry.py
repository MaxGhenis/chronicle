"""Optional bridge to a pinned, actual Axiom Encode concept-registry reader.

This executes the original library from verified Git blobs, not a guessed CLI
or a replacement registry parser. No fact/field alignment or corpus is read.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
from typing import Any, Mapping
from uuid import uuid4


LOADER_PATH = "src/axiom_encode/concepts/registry.py"
DATA_PATH = "src/axiom_encode/concepts/data"
# Inspected in actual Axiom Encode 1daadad265dc9eaa1ab737df87b453a5e5e0cb46.
# A different implementation needs an explicitly reviewed bridge profile.
SUPPORTED_LOADER_SHA256 = (
    "a1f6c260de7a7c51bb3772af448ef22002149d24e72ae6464b798b4e980eba25"
)
REGISTRY_FORMAT = "axiom-encode/concepts/v1"
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class AxiomRegistryPin:
    """Caller-reviewed source identity; registry keys are flat YAML filenames."""

    repository: Path
    commit: str
    loader_sha256: str
    registry_files: Mapping[str, str]


class _Unavailable(ValueError):
    pass


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _digest(value: Any) -> str:
    return _sha(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    )


def observe_axiom_registry_runtime() -> dict[str, Any]:
    """Observe installed runtime identity for review; observing is not approval.

    Pass a previously reviewed copy as ``expected_runtime`` when querying. Pins
    bind the Python/Git executable bytes and the PyYAML package source/binaries,
    not every OS library or Python standard-library file.
    """
    import yaml

    git = shutil.which("git")
    if git is None:
        raise _Unavailable("git_unavailable")
    yaml_root = Path(yaml.__file__).resolve().parent
    members = {}
    for path in sorted(yaml_root.rglob("*")):
        if path.suffix not in {".py", ".so", ".pyd"}:
            continue
        if path.is_symlink() or not path.is_file():
            raise _Unavailable("yaml_runtime_layout")
        members[str(path.relative_to(yaml_root))] = _sha(path.read_bytes())
    if "__init__.py" not in members:
        raise _Unavailable("yaml_runtime_layout")
    for name, module in tuple(sys.modules.items()):
        if name != "yaml" and not name.startswith("yaml."):
            continue
        location = getattr(module, "__file__", None)
        if location is None or not Path(location).resolve().is_relative_to(yaml_root):
            raise _Unavailable("yaml_runtime_origin")
    python_path = Path(sys.executable).resolve()
    git_path = Path(git).resolve()
    return {
        "python_implementation": sys.implementation.name,
        "python_version": sys.version,
        "python_executable": str(python_path),
        "python_executable_sha256": _sha(python_path.read_bytes()),
        "git_executable": str(git_path),
        "git_executable_sha256": _sha(git_path.read_bytes()),
        "pyyaml_version": yaml.__version__,
        "pyyaml_package_path": str(yaml_root),
        "pyyaml_files": members,
        "pyyaml_tree_sha256": _digest(members),
    }


def _git(repository: Path, executable: str, *args: str) -> bytes:
    result = subprocess.run(
        [executable, "-C", str(repository), *args],
        capture_output=True,
        check=False,
        timeout=15,
    )
    if result.returncode:
        raise _Unavailable("git_source_unavailable")
    return result.stdout


def _read_pinned_source(
    pin: AxiomRegistryPin, runtime: Mapping[str, Any]
) -> tuple[bytes, dict[str, bytes], dict[str, Any]]:
    if not isinstance(pin.commit, str) or not _HEX40.fullmatch(pin.commit):
        raise _Unavailable("invalid_source_commit")
    if pin.loader_sha256 != SUPPORTED_LOADER_SHA256:
        raise _Unavailable("unsupported_loader_profile")
    expected = dict(pin.registry_files)
    if not expected or any(
        not isinstance(name, str)
        or re.fullmatch(r"[A-Za-z0-9_-]+\.yaml", name) is None
        or not isinstance(digest, str)
        or not _HEX64.fullmatch(digest)
        for name, digest in expected.items()
    ):
        raise _Unavailable("invalid_registry_pin")
    git = runtime["git_executable"]
    resolved = _git(
        pin.repository, git, "rev-parse", "--verify", pin.commit + "^{commit}"
    )
    if resolved.decode().strip() != pin.commit:
        raise _Unavailable("source_commit_mismatch")
    source = _git(pin.repository, git, "show", f"{pin.commit}:{LOADER_PATH}")
    if _sha(source) != pin.loader_sha256:
        raise _Unavailable("loader_digest_mismatch")
    tree = (
        _git(
            pin.repository,
            git,
            "ls-tree",
            "-r",
            "--name-only",
            pin.commit,
            "--",
            DATA_PATH,
        )
        .decode()
        .splitlines()
    )
    actual = {
        name.removeprefix(DATA_PATH + "/") for name in tree if name.endswith(".yaml")
    }
    if actual != set(expected):
        raise _Unavailable("registry_roster_mismatch")
    data = {}
    for name, expected_sha in sorted(expected.items()):
        content = _git(pin.repository, git, "show", f"{pin.commit}:{DATA_PATH}/{name}")
        if _sha(content) != expected_sha:
            raise _Unavailable("registry_digest_mismatch")
        data[name] = content
    identity = {
        "source_commit": pin.commit,
        "loader_path": LOADER_PATH,
        "loader_sha256": _sha(source),
        "registry_files": {name: _sha(content) for name, content in data.items()},
        "registry_snapshot_sha256": _digest(
            {"format": REGISTRY_FORMAT, "files": expected}
        ),
        "git_object_bytes_verified": True,
    }
    return source, data, identity


def _text(value: Any) -> bool:
    return type(value) is str and bool(value.strip())


def _project_registry(registry: Any, module: ModuleType, root: Path) -> dict[str, Any]:
    """Check the real reader's output protocol, without parsing registry YAML."""
    if type(registry) is not module.ConceptRegistry:
        raise _Unavailable("registry_runtime_protocol")
    issues = registry.validate()
    if type(issues) is not list or any(type(issue) is not str for issue in issues):
        raise _Unavailable("registry_runtime_protocol")
    if issues:
        raise _Unavailable("registry_validation_failed")
    for mapping in [
        registry.concepts_by_id,
        registry.canonical_to_concept,
        registry.synonym_to_concept,
    ]:
        if type(mapping) is not dict:
            raise _Unavailable("registry_runtime_protocol")
    projected = {}
    for identifier, concept in registry.concepts_by_id.items():
        if (
            type(concept) is not module.Concept
            or not _text(identifier)
            or concept.id != identifier
            or not _text(concept.canonical_name)
            or type(concept.producer_missing) is not bool
            or type(concept.has_producer) is not bool
            or type(concept.blocked_synonyms) is not tuple
            or any(not _text(name) for name in concept.blocked_synonyms)
            or (
                concept.producer_anchor is not None
                and not _text(concept.producer_anchor)
            )
            or (
                concept.description is not None and type(concept.description) is not str
            )
            or not isinstance(concept.source_file, Path)
            or concept.source_file.parent != root
        ):
            raise _Unavailable("concept_runtime_protocol")
        if registry.lookup_canonical(concept.canonical_name) is not concept:
            raise _Unavailable("canonical_lookup_inconsistent")
        if any(
            registry.lookup_synonym(name) is not concept
            for name in concept.blocked_synonyms
        ):
            raise _Unavailable("synonym_lookup_inconsistent")
        definition = {
            "id": identifier,
            "canonical_name": concept.canonical_name,
            "description": concept.description,
            "blocked_synonyms": list(concept.blocked_synonyms),
            "producer_anchor": concept.producer_anchor,
            "producer_missing": concept.producer_missing,
        }
        projected[identifier] = {
            "definition": definition,
            "source_filename": concept.source_file.name,
            "registry_has_producer": concept.has_producer,
            "definition_sha256": _digest(
                {"schema": "chronicle.axiom_registry_definition.v1", **definition}
            ),
        }
    if set(registry.canonical_to_concept) != {
        item["definition"]["canonical_name"] for item in projected.values()
    } or set(registry.synonym_to_concept) != {
        name
        for item in projected.values()
        for name in item["definition"]["blocked_synonyms"]
    }:
        raise _Unavailable("registry_index_inconsistent")
    return projected


def _query_actual_reader(
    source: bytes, data: Mapping[str, bytes], query_kind: str, query: str
) -> tuple[dict[str, Any] | None, int]:
    # Compile the checked original bytes directly: no stale .pyc or unrelated
    # axiom_encode package initialization. Only the real reader parses the YAML.
    # Each lookup owns its module slot, including concurrent callers.
    name = "_chronicle_pinned_axiom_registry_" + _sha(source) + "_" + uuid4().hex
    module = ModuleType(name)
    sys.modules[name] = module
    try:
        with TemporaryDirectory(prefix="chronicle-axiom-registry-") as temporary:
            root = Path(temporary)
            module.__file__ = str(root / "registry.py")
            exec(compile(source, module.__file__, "exec"), module.__dict__)
            if module.REGISTRY_FORMAT != REGISTRY_FORMAT:
                raise _Unavailable("registry_format_unsupported")
            for filename, content in data.items():
                path = root / filename
                path.write_bytes(content)
                path.chmod(0o400)
            registry = module.load_concept_registry(data_root=root)
            projected = _project_registry(registry, module, root)
            if any(item["source_filename"] not in data for item in projected.values()):
                raise _Unavailable("concept_source_unbound")
            if query_kind == "id":
                concept = registry.concepts_by_id.get(query)
            elif query_kind == "canonical_name":
                concept = registry.lookup_canonical(query)
            else:
                concept = registry.lookup_synonym(query)
            for filename, content in data.items():
                if (root / filename).read_bytes() != content:
                    raise _Unavailable("registry_input_changed")
            if concept is None:
                return None, len(projected)
            if type(concept) is not module.Concept or concept.id not in projected:
                raise _Unavailable("query_runtime_protocol")
            return projected[concept.id], len(projected)
    finally:
        sys.modules.pop(name, None)


def query_axiom_registry(
    pin: AxiomRegistryPin | None,
    *,
    expected_runtime: Mapping[str, Any] | None,
    query_kind: str,
    query: str,
    expected_definition_sha256: str | None = None,
) -> dict[str, Any]:
    """Query a real pinned registry, keeping different claims separate.

    ``unknown`` means absent from this registry snapshot and query namespace,
    not absent from the law or RuleSpec corpus. ``definition_mismatch`` still
    reports existence but refuses the requested definition pin. A blocked
    synonym lookup identifies a prohibited alternative spelling, not an
    endorsement of that spelling. Producer flags are registry declarations;
    no producer is located, compiled or executed by this bridge.
    """
    result: dict[str, Any] = {
        "schema_version": "chronicle.axiom_registry_probe.v1",
        "status": "unavailable",
        "reason": "not_configured",
        "query_kind": query_kind,
        "query": query,
        "source": None,
        "runtime": None,
        "concept": None,
        "definition": None,
        "producer_coverage": {
            "registry_declaration": "unknown",
            "executable": "not_checked",
        },
        "semantic_equivalence": "not_assessed",
        "legal_vintage": "not_resolved",
    }
    if pin is None or expected_runtime is None:
        return result
    try:
        if query_kind not in {"id", "canonical_name", "blocked_synonym"} or not _text(
            query
        ):
            raise _Unavailable("invalid_query")
        if expected_definition_sha256 is not None and (
            not isinstance(expected_definition_sha256, str)
            or not _HEX64.fullmatch(expected_definition_sha256)
        ):
            raise _Unavailable("invalid_definition_pin")
        runtime = observe_axiom_registry_runtime()
        if runtime != dict(expected_runtime):
            raise _Unavailable("runtime_pin_mismatch")
        source, data, identity = _read_pinned_source(pin, runtime)
        item, count = _query_actual_reader(source, data, query_kind, query)
        if observe_axiom_registry_runtime() != runtime:
            raise _Unavailable("runtime_changed")
        result.update(source=identity, runtime=runtime, registry_concept_count=count)
        if item is None:
            result.update(
                status="unknown", reason="absent_from_pinned_registry_namespace"
            )
            return result
        if item["source_filename"] not in data:
            raise _Unavailable("concept_source_unbound")
        definition_matches = (
            None
            if expected_definition_sha256 is None
            else item["definition_sha256"] == expected_definition_sha256
        )
        result.update(
            status="definition_mismatch" if definition_matches is False else "found",
            reason="definition_pin_mismatch"
            if definition_matches is False
            else "registry_lookup",
            concept=item["definition"],
            definition={
                "scheme": "chronicle.axiom_registry_definition.v1",
                "sha256": item["definition_sha256"],
                "expected_sha256": expected_definition_sha256,
                "matches_expected": definition_matches,
                "source_filename": item["source_filename"],
                "source_file_sha256": identity["registry_files"][
                    item["source_filename"]
                ],
            },
            producer_coverage={
                "registry_declaration": "declared_available"
                if item["registry_has_producer"]
                else "declared_missing_or_unanchored",
                "registry_has_producer": item["registry_has_producer"],
                "executable": "not_checked",
            },
        )
        return result
    except _Unavailable as error:
        result.update(
            status="unavailable", reason=str(error), source=None, runtime=None
        )
    except (OSError, subprocess.SubprocessError, ImportError):
        result.update(
            status="unavailable",
            reason="runtime_or_source_unavailable",
            source=None,
            runtime=None,
        )
    except Exception:
        result.update(
            status="unavailable",
            reason="malformed_runtime_or_registry",
            source=None,
            runtime=None,
        )
    return result
