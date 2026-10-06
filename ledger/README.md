# PolicyEngine Ledger

PolicyEngine Ledger records public source facts. It does not record predictions,
forecast distributions, agent traces, or forecast scores.

`official_observations.jsonl` contains source-backed `AggregateFact` rows for
official observations that downstream systems can use as resolution facts. Each
row should keep `source_record_id` stable and source-specific, because downstream
prediction systems resolve against that ID.

Proposed ledger states in the gated append flow are recorded in chained, RFC
3161-witnessed release manifests. See
[`../releases/README.md`](../releases/README.md) for the manifest schema, offline
verification procedure, and security limitations.
