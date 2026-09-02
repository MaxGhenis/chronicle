"""Tests for the Chronicle namespace."""

import importlib

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
    """The schema names are import-time constants, so re-read them here.

    ``db.supabase_client`` resolves them from the environment when it is first
    imported, which happens at collection — before the suite-wide
    ``isolated_rename_window_env`` fixture clears an operator's
    ``CHRONICLE_SCHEMA``. Reloading under the cleared environment is what makes
    this a test of the defaults rather than of the shell.
    """
    import db.supabase_client

    supabase_client = importlib.reload(db.supabase_client)

    assert supabase_client.LEDGER_SCHEMA == "ledger"
    assert supabase_client.TARGETS_SCHEMA == "targets"


def test_chronicle_normalization_exports_helpers():
    assert callable(convert_units)
