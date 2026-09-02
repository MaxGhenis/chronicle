"""Tests for the Chronicle namespace."""

from chronicle.client import get_supabase_client
from chronicle.normalization import convert_units
from chronicle.targets import (
    Target,
    TargetType,
    query_targets,
)
from db.schema import Target as DbTarget
from db.supabase_client import query_targets as db_query_targets


def test_chronicle_targets_reexport_schema_objects():
    assert Target is DbTarget
    assert TargetType.COUNT.value == "count"


def test_chronicle_targets_reexport_client_helpers():
    assert query_targets is db_query_targets


def test_chronicle_client_reexports_supabase_client():
    assert callable(get_supabase_client)


def test_chronicle_supabase_schema_boundaries_are_defaulted():
    """The schema names resolve per call, so this reads the cleared window.

    ``db.supabase_client`` is imported at collection, before any fixture runs.
    Resolving the schema there — as an import-time constant — would bind an
    operator's ``CHRONICLE_SCHEMA`` (or a ledger-era name, warning as it went)
    into the module for the whole session, and no fixture could take it back.
    Reading at call time is what makes this a test of the defaults rather than
    of the shell.
    """
    from db import supabase_client

    assert supabase_client.chronicle_schema() == "ledger"
    assert supabase_client.targets_schema() == "targets"


def test_chronicle_supabase_schema_follows_the_environment(monkeypatch):
    """The renamed variable reaches the client after it has been imported."""
    from db import supabase_client

    monkeypatch.setenv("CHRONICLE_SCHEMA", "chronicle_probe")
    monkeypatch.setenv("POLICYENGINE_TARGETS_SCHEMA", "targets_probe")

    assert supabase_client.chronicle_schema() == "chronicle_probe"
    assert supabase_client.targets_schema() == "targets_probe"


def test_chronicle_normalization_exports_helpers():
    assert callable(convert_units)
