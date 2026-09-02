"""Chronicle source-data foundation.

Chronicle owns government-statistics releases: source artifacts, source-backed
facts, constraints, and provenance. Selection contracts, raw microdata storage,
source reconciliation, aging, imputation, target activation, and calibration
belong in downstream systems such as Microcosm.
"""

__all__ = [
    "bundle",
    "client",
    "concepts",
    "consumer_contract",
    "core",
    "database",
    "env",
    "facts",
    "harness",
    "jurisdictions",
    "mirror",
    "normalization",
    "source_package",
    "store",
    "sources",
    "suite",
    "targets",
]
