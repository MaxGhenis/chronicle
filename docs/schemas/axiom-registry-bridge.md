# Optional Axiom registry bridge

`policyengine_chronicle.axiom_registry` executes the actual Axiom Encode
`load_concept_registry` library from a caller-pinned Git commit. It checks concept
existence and registry-definition identity. It does not assert source-measure
equivalence, inspect a RuleSpec corpus, locate or execute a producer, resolve a
legal vintage, or create fact/field alignments. No existing fact key, alignment
identity, documentation schema or journal path changes.

## Explicit source and runtime pins

`AxiomRegistryPin` requires a local Axiom Encode Git repository, a full commit
SHA, the reviewed reader SHA-256 and a complete filename-to-SHA-256 map of its
registry YAML files. The bridge reads those committed blobs directly. A dirty or
stale working checkout is not used as source. It verifies the commit, loader,
registry roster and each data blob before execution. No network fetch occurs.

The current bridge profile supports the actual reader observed at Axiom Encode
`1daadad265dc9eaa1ab737df87b453a5e5e0cb46`: reader SHA
`a1f6c260de7a7c51bb3772af448ef22002149d24e72ae6464b798b4e980eba25`.
Its bytes match the earlier inspected `472807c9` reader. A different reader digest
is unavailable until a new profile has been reviewed. The original source is
compiled unchanged from verified bytes, avoiding stale bytecode and unrelated
encoder package initialization. The actual Axiom loader reads temporary copies
of the verified YAML bytes; Chronicle does not parse or reimplement that registry.

`observe_axiom_registry_runtime()` records Python and Git executable identities,
Python version, and PyYAML package source/binary hashes. Observation is not
approval: retain and review a runtime record, then pass it explicitly as
`expected_runtime` to `query_axiom_registry`. The bridge compares the runtime
before and after the lookup. Pins are local to that runtime; they do not attest
every OS library or Python standard-library file. This is a Git-source library
bridge, not evidence that an Axiom package or CLI is installed.

```python
import json
from pathlib import Path
from policyengine_chronicle.axiom_registry import AxiomRegistryPin, query_axiom_registry

source = AxiomRegistryPin(
    repository=Path("/reviewed/axiom-encode"),
    commit=reviewed_source_commit,
    loader_sha256=reviewed_loader_sha256,
    registry_files=reviewed_registry_files,
)
result = query_axiom_registry(
    source,
    expected_runtime=json.loads(Path("reviewed-runtime.json").read_text()),
    query_kind="id",
    query="snap.shelter.total_allowable_monthly_expenses",
    expected_definition_sha256=reviewed_definition_sha256,
)
```

## Separate answers

The result has no overloaded `valid` flag. Its status is `found`, `unknown`,
`definition_mismatch` or `unavailable`. Missing configuration, runtime mismatch,
unavailable source, unsupported reader and malformed output are never successful
lookups. `unknown` means absent only from the pinned registry and requested
namespace. An anchored RuleSpec reference absent from the SNAP concept registry
may still exist in a corpus; this bridge does not inspect that corpus.

The caller explicitly chooses `id`, `canonical_name` or `blocked_synonym`.
Lookups use Axiom's actual indexes/methods. A blocked synonym identifies a spelling
the registry discourages; it is not silently accepted as a canonical spelling.

Definition identity includes the source commit, reader hash, whole registry
snapshot digest and source-file digest. The separate per-concept digest uses
Chronicle's documented `chronicle.axiom_registry_definition.v1` projection:
canonical JSON (sorted keys, UTF-8, no insignificant whitespace) of schema name,
ID, canonical name, description, ordered blocked synonyms, producer anchor and
producer-missing flag as returned by Axiom. This is a bridge-defined revision,
not a publisher-issued identifier or legal effective date. Both snapshot and
definition identities are retained; YAML comments are covered by the file hash.
An expected definition mismatch preserves the observed existence result while
refusing the requested definition pin.

Producer anchor, `producer_missing` and the real `has_producer` property are
reported as registry declarations. Executable coverage stays `not_checked` even
when the registry declares a producer available. Semantic equivalence stays
`not_assessed`, and legal-vintage resolution stays `not_resolved` for every query.

The adapter checks returned-object types, strict booleans, registry validation
results, source-file binding and canonical/synonym index consistency. Registry
authoring semantics remain Axiom's responsibility. This wrapper does not claim
stronger YAML validation than the pinned Axiom implementation, which performs
some coercions. Reviewed source/data pins are required; arbitrary registry inputs
are not a substitute for authoring review.

## Existing subprocess protocol

Chronicle's separate `axiom concepts validate <id> --json --root ...` protocol
has not been verified against a production Axiom CLI here. The real inspected CLI
offers `axiom-encode concepts-audit --roots ... --json`, which audits corpus drift;
it is a different interface and is not executed by this bridge. Existing protocol
fixtures do not establish CLI compatibility. This adapter does not silently
replace that protocol or attach semantic links to any aggregate or survey field.
