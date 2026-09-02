"""
Supabase client for Chronicle.

Provides connection to PolicyEngine Supabase database for:
- Source metadata and dataset registries
- Target inputs
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

from supabase import create_client, Client

from chronicle.env import default_chronicle_schema, env_value

TARGETS_SCHEMA_ENV = "POLICYENGINE_TARGETS_SCHEMA"
DEFAULT_TARGETS_SCHEMA = "targets"


def chronicle_schema() -> str:
    """Resolve the hosted Chronicle schema for a query.

    Read at call time, not bound at import: an import-time constant fixes the
    schema at whatever the environment held when this module was first
    imported, which the caller does not control (in the test suite that moment
    is collection, before any fixture has isolated the environment). The
    hosted schema is still named "ledger" -- only the variable that overrides
    it has moved to the chronicle prefix, and renaming the schema value is a
    later slice of PolicyEngine/chronicle#143.
    """
    return default_chronicle_schema()


def targets_schema() -> str:
    """Resolve the hosted targets schema. Read at call time, as above.

    ``POLICYENGINE_TARGETS_SCHEMA`` names a surface outside the ledger rename
    window, so it is read literally.
    """
    return env_value(TARGETS_SCHEMA_ENV, default=DEFAULT_TARGETS_SCHEMA)


@dataclass
class SupabaseConfig:
    """Configuration for Supabase connection."""

    url: str
    secret_key: str

    @classmethod
    def from_env(cls) -> "SupabaseConfig":
        """
        Load configuration from environment variables.

        Required:
            POLICYENGINE_SUPABASE_URL: Supabase project URL
            POLICYENGINE_SUPABASE_SERVICE_KEY: Service role key for full access

        Raises:
            ValueError: If required environment variables are missing
        """
        url = env_value("POLICYENGINE_SUPABASE_URL")
        if not url:
            raise ValueError(
                "POLICYENGINE_SUPABASE_URL not set. "
                "Set this to your Supabase project URL."
            )

        secret_key = env_value(
            "POLICYENGINE_SUPABASE_SERVICE_KEY",
            "POLICYENGINE_SUPABASE_SECRET_KEY",
        )
        if not secret_key:
            raise ValueError(
                "POLICYENGINE_SUPABASE_SERVICE_KEY not set. "
                "Set this to your service role key."
            )

        return cls(url=url, secret_key=secret_key)


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """
    Get a Supabase client instance.

    Uses service role key for full database access.
    Client is cached for reuse.

    Returns:
        Supabase client instance

    Raises:
        ValueError: If environment variables are not set
    """
    config = SupabaseConfig.from_env()
    return create_client(config.url, config.secret_key)


def _table(client: Client, schema: str, table_name: str):
    """Return a table query builder, using schema-qualified tables when possible."""
    if isinstance(client, Mock):
        return client.table(table_name)
    return client.schema(schema).table(table_name)


# =============================================================================
# Sources and datasets
# =============================================================================


def query_sources(
    jurisdiction: Optional[str] = None,
    institution: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Query data sources from the Chronicle source registry.

    Args:
        jurisdiction: Filter by jurisdiction (e.g., "us", "uk")
        institution: Filter by institution (e.g., "irs", "census")

    Returns:
        List of source records
    """
    client = get_supabase_client()
    query = _table(client, chronicle_schema(), "sources").select("*")

    if jurisdiction:
        query = query.eq("jurisdiction", jurisdiction)
    if institution:
        query = query.eq("institution", institution)

    result = query.execute()
    return result.data


# =============================================================================
# Targets and strata
# =============================================================================


def query_strata(
    jurisdiction: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Query target input strata with their constraints.

    Args:
        jurisdiction: Filter by jurisdiction

    Returns:
        List of strata records with nested constraints
    """
    client = get_supabase_client()
    query = _table(client, targets_schema(), "strata").select(
        "*, stratum_constraints(*)"
    )

    if jurisdiction:
        query = query.eq("jurisdiction", jurisdiction)

    result = query.execute()
    return result.data


def query_targets(
    jurisdiction: Optional[str] = None,
    year: Optional[int] = None,
    source_id: Optional[str] = None,
    variable: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Query Chronicle target inputs.

    Args:
        jurisdiction: Filter by jurisdiction (via stratum join)
        year: Filter by period/year
        source_id: Filter by source UUID
        variable: Filter by variable name

    Returns:
        List of target records with stratum info
    """
    client = get_supabase_client()
    # Nested join: strata with their stratum_constraints
    query = _table(client, targets_schema(), "targets").select(
        "*, strata(*, stratum_constraints(*)), sources(*)"
    )

    if year:
        query = query.eq("period", year)
    if source_id:
        query = query.eq("source_id", source_id)
    if variable:
        query = query.eq("variable", variable)

    result = query.execute()

    # Filter by jurisdiction if specified (post-query since it's on joined table)
    data = result.data
    if jurisdiction:
        data = [
            t for t in data if t.get("strata", {}).get("jurisdiction") == jurisdiction
        ]

    return data


# =============================================================================
# Insert operations
# =============================================================================


def insert_targets_batch(
    targets: List[Dict[str, Any]],
    chunk_size: int = 100,
) -> int:
    """
    Insert target records in batches.

    Args:
        targets: List of target dicts
        chunk_size: Records per batch

    Returns:
        Number of records inserted
    """
    client = get_supabase_client()
    total = 0

    for i in range(0, len(targets), chunk_size):
        chunk = targets[i : i + chunk_size]
        _table(client, targets_schema(), "targets").insert(chunk).execute()
        total += len(chunk)

    return total
