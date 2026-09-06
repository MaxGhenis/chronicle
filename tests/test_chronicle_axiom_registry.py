"""Invented protocol/refusal tests, not substitutes for real Axiom evidence.

The separately retained integration probe executes the genuine pinned registry.
None of the protocol fixtures below purports to implement a legal concept.
"""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from policyengine_chronicle import axiom_registry as bridge


@pytest.fixture
def pin():
    return bridge.AxiomRegistryPin(
        Path("invented-unused-repository"),
        "1" * 40,
        bridge.SUPPORTED_LOADER_SHA256,
        {"invented.yaml": "2" * 64},
    )


def probe(pin, **kwargs):
    return bridge.query_axiom_registry(
        pin,
        expected_runtime=kwargs.pop("expected_runtime", {"fixture": "runtime"}),
        query_kind=kwargs.pop("query_kind", "id"),
        query=kwargs.pop("query", "invented.fixture"),
        **kwargs,
    )


def never(*args, **kwargs):
    raise AssertionError("This dependency must not execute")


def test_unconfigured_bridge_does_not_observe_or_execute(monkeypatch, pin):
    monkeypatch.setattr(bridge, "observe_axiom_registry_runtime", never)
    assert probe(None)["reason"] == "not_configured"
    assert probe(pin, expected_runtime=None)["status"] == "unavailable"


@pytest.mark.parametrize(
    "options",
    [
        {"query_kind": "guessed_cli"},
        {"query": " "},
        {"query": 123},
        {"expected_definition_sha256": "not-a-digest"},
        {"expected_definition_sha256": True},
    ],
)
def test_invalid_request_never_executes_runtime(monkeypatch, pin, options):
    monkeypatch.setattr(bridge, "observe_axiom_registry_runtime", never)
    result = probe(pin, **options)
    assert result["status"] == "unavailable"
    assert result["concept"] is None


def test_runtime_pin_mismatch_stops_before_source_access(monkeypatch, pin):
    monkeypatch.setattr(
        bridge, "observe_axiom_registry_runtime", lambda: {"fixture": "changed"}
    )
    monkeypatch.setattr(bridge, "_read_pinned_source", never)
    assert probe(pin)["reason"] == "runtime_pin_mismatch"


@pytest.mark.parametrize(
    "error", [ImportError("missing"), OSError("missing"), ValueError("malformed")]
)
def test_missing_or_malformed_runtime_is_unavailable(monkeypatch, pin, error):
    def fail():
        raise error

    monkeypatch.setattr(bridge, "observe_axiom_registry_runtime", fail)
    result = probe(pin)
    assert result["status"] == "unavailable"
    assert result["source"] is None
    assert result["producer_coverage"]["executable"] == "not_checked"


def test_changed_runtime_after_reader_does_not_become_unknown(monkeypatch, pin):
    observations = iter([{"fixture": "runtime"}, {"fixture": "changed"}])
    monkeypatch.setattr(
        bridge, "observe_axiom_registry_runtime", lambda: next(observations)
    )
    monkeypatch.setattr(bridge, "_read_pinned_source", lambda *_: (b"", {}, {}))
    monkeypatch.setattr(bridge, "_query_actual_reader", lambda *_: (None, 0))
    result = probe(pin)
    assert result["status"] == "unavailable"
    assert result["reason"] == "runtime_changed"


@pytest.mark.parametrize("change", ["commit", "loader", "empty", "traversal", "digest"])
def test_invalid_source_pin_refused_before_git(monkeypatch, pin, change):
    if change == "commit":
        pin = replace(pin, commit="main")
    elif change == "loader":
        pin = replace(pin, loader_sha256="0" * 64)
    elif change == "empty":
        pin = replace(pin, registry_files={})
    elif change == "traversal":
        pin = replace(pin, registry_files={"../invented.yaml": "2" * 64})
    elif change == "digest":
        pin = replace(pin, registry_files={"invented.yaml": "bad"})
    monkeypatch.setattr(bridge, "_git", never)
    with pytest.raises(bridge._Unavailable):
        bridge._read_pinned_source(pin, {"git_executable": "unused"})


@pytest.mark.parametrize("failure", ["commit", "loader", "roster", "data"])
def test_git_blob_binding_refusals(monkeypatch, pin, failure):
    # These bytes are never executed. This fixture exercises only source binding.
    source = b"# invented binding refusal fixture; not an Axiom implementation\n"
    source_sha = bridge._sha(source)
    monkeypatch.setattr(bridge, "SUPPORTED_LOADER_SHA256", source_sha)
    pin = replace(pin, loader_sha256=source_sha)

    def git(_repo, _exe, *args):
        if args[0] == "rev-parse":
            return (("0" * 40 if failure == "commit" else pin.commit) + "\n").encode()
        if args[0] == "ls-tree":
            name = "extra.yaml" if failure == "roster" else "invented.yaml"
            return (bridge.DATA_PATH + "/" + name + "\n").encode()
        if args[-1].endswith(bridge.LOADER_PATH):
            return b"changed" if failure == "loader" else source
        return b"wrong pinned registry bytes"

    monkeypatch.setattr(bridge, "_git", git)
    with pytest.raises(
        bridge._Unavailable,
        match={
            "commit": "source_commit_mismatch",
            "loader": "loader_digest_mismatch",
            "roster": "registry_roster_mismatch",
            "data": "registry_digest_mismatch",
        }[failure],
    ):
        bridge._read_pinned_source(pin, {"git_executable": "unused"})


@pytest.fixture
def protocol(tmp_path):
    # Deliberately invented output objects; no registry parsing or legal rules.
    class Concept:
        pass

    class Registry:
        pass

    concept = Concept()
    concept.id = "invented.fixture"
    concept.canonical_name = "invented_canonical"
    concept.producer_missing = True
    concept.has_producer = False
    concept.producer_anchor = "invented:anchor"
    concept.description = "Invented protocol fixture"
    concept.blocked_synonyms = ("invented_blocked",)
    concept.source_file = tmp_path / "invented.yaml"
    registry = Registry()
    registry.concepts_by_id = {concept.id: concept}
    registry.canonical_to_concept = {concept.canonical_name: concept}
    registry.synonym_to_concept = {concept.blocked_synonyms[0]: concept}
    registry.lookup_canonical = lambda name: registry.canonical_to_concept.get(name)
    registry.lookup_synonym = lambda name: registry.synonym_to_concept.get(name)
    registry.validate = lambda: []
    module = SimpleNamespace(Concept=Concept, ConceptRegistry=Registry)
    return registry, concept, module, tmp_path


@pytest.mark.parametrize(
    "failure",
    [
        "wrong_registry_type",
        "truthy_validation",
        "validation_errors",
        "non_string_errors",
        "mapping",
        "wrong_concept_type",
        "truthy_missing",
        "truthy_producer",
        "synonym_type",
        "description",
        "source_path",
        "canonical_lookup",
        "synonym_lookup",
        "extra_index",
    ],
)
def test_malformed_actual_reader_protocol_is_refused(protocol, failure):
    registry, concept, module, root = protocol
    if failure == "wrong_registry_type":
        registry = SimpleNamespace()
    elif failure == "truthy_validation":
        registry.validate = lambda: True
    elif failure == "validation_errors":
        registry.validate = lambda: ["inconsistent registry"]
    elif failure == "non_string_errors":
        registry.validate = lambda: [False]
    elif failure == "mapping":
        registry.concepts_by_id = []
    elif failure == "wrong_concept_type":
        registry.concepts_by_id[concept.id] = SimpleNamespace()
    elif failure == "truthy_missing":
        concept.producer_missing = "false"
    elif failure == "truthy_producer":
        concept.has_producer = "false"
    elif failure == "synonym_type":
        concept.blocked_synonyms = "invented_blocked"
    elif failure == "description":
        concept.description = {}
    elif failure == "source_path":
        concept.source_file = root.parent / "outside.yaml"
    elif failure == "canonical_lookup":
        registry.lookup_canonical = lambda _: None
    elif failure == "synonym_lookup":
        registry.lookup_synonym = lambda _: None
    elif failure == "extra_index":
        registry.canonical_to_concept["unexpected"] = concept
    with pytest.raises(bridge._Unavailable):
        bridge._project_registry(registry, module, root)


def test_reader_exception_is_not_a_successful_lookup(monkeypatch, pin):
    monkeypatch.setattr(
        bridge, "observe_axiom_registry_runtime", lambda: {"fixture": "runtime"}
    )
    monkeypatch.setattr(bridge, "_read_pinned_source", lambda *_: (b"", {}, {}))

    def fail(*_):
        raise ValueError("Invented malformed reader response")

    monkeypatch.setattr(bridge, "_query_actual_reader", fail)
    result = probe(pin)
    assert result["status"] == "unavailable"
    assert result["semantic_equivalence"] == "not_assessed"
    assert result["legal_vintage"] == "not_resolved"
