"""Tests for the chronicle-first environment read window.

Chronicle's operational stores migrate by dual-run (PolicyEngine/chronicle#143,
mechanism 3): ``CHRONICLE_*`` names win, ledger-era names keep working behind a
deprecation warning. Every test here is hermetic — the fixture strips every
variable in the rename window from the ambient environment first.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

from chronicle.artifacts import (
    DEFAULT_R2_DERIVED_BUCKET,
    DEFAULT_R2_RAW_BUCKET,
    default_r2_derived_bucket,
    default_r2_raw_bucket,
)
from chronicle.env import (
    CHRONICLE_ENV_PREFIX,
    ChronicleEnvDeprecationWarning,
    LEGACY_ENV_PREFIXES,
    env_flag,
    env_names,
    env_value,
    reset_env_deprecation_state,
)
from chronicle.harness import main as harness_main
from chronicle.source_package import (
    SOURCE_ARTIFACT_CACHE_ENV,
    SOURCE_ARTIFACT_FETCH_ENV,
)

RENAME_WINDOW_PREFIXES = (CHRONICLE_ENV_PREFIX, *LEGACY_ENV_PREFIXES)


@pytest.fixture(autouse=True)
def isolated_rename_window_env(monkeypatch):
    """Run each test with no rename-window variable inherited from the shell."""
    for name in list(os.environ):
        if name.startswith(RENAME_WINDOW_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    reset_env_deprecation_state()
    yield
    reset_env_deprecation_state()


def _fake_wrangler(tmp_path, log):
    wrangler = tmp_path / "wrangler"
    wrangler.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\necho ok\n")
    wrangler.chmod(0o755)
    return wrangler


# ---------------------------------------------------------------------------
# Lookup order
# ---------------------------------------------------------------------------


def test_env_names_puts_chronicle_first_then_ledger_era_names():
    assert env_names("CHRONICLE_SOURCE_ARTIFACT_FETCH") == (
        "CHRONICLE_SOURCE_ARTIFACT_FETCH",
        "POLICYENGINE_LEDGER_SOURCE_ARTIFACT_FETCH",
        "LEDGER_SOURCE_ARTIFACT_FETCH",
    )


def test_env_names_expands_a_ledger_era_name_to_the_same_ladder():
    assert env_names("LEDGER_PE_US_DATA_ROOT") == env_names("CHRONICLE_PE_US_DATA_ROOT")


def test_env_names_leaves_variables_outside_the_rename_window_alone():
    assert env_names("POLICYENGINE_SUPABASE_URL") == ("POLICYENGINE_SUPABASE_URL",)
    assert env_names("POLICYENGINE_TARGETS_SCHEMA") == ("POLICYENGINE_TARGETS_SCHEMA",)


def test_bare_prefix_is_not_treated_as_a_renamed_variable():
    assert env_names("LEDGER_") == ("LEDGER_",)


# ---------------------------------------------------------------------------
# Precedence and the deprecation warning
# ---------------------------------------------------------------------------


def test_chronicle_name_wins_over_both_ledger_era_names(monkeypatch, recwarn):
    monkeypatch.setenv("CHRONICLE_PE_US_DATA_ROOT", "/chronicle")
    monkeypatch.setenv("LEDGER_PE_US_DATA_ROOT", "/ledger")
    monkeypatch.setenv("POLICYENGINE_LEDGER_PE_US_DATA_ROOT", "/policyengine-ledger")

    assert env_value("CHRONICLE_PE_US_DATA_ROOT") == "/chronicle"
    assert not [
        warning
        for warning in recwarn.list
        if issubclass(warning.category, ChronicleEnvDeprecationWarning)
    ]


def test_ledger_name_alone_still_works_and_warns(monkeypatch):
    monkeypatch.setenv("LEDGER_PE_US_DATA_ROOT", "/ledger")

    with pytest.warns(ChronicleEnvDeprecationWarning) as warnings_raised:
        assert env_value("CHRONICLE_PE_US_DATA_ROOT") == "/ledger"

    message = str(warnings_raised[0].message)
    assert "LEDGER_PE_US_DATA_ROOT" in message
    assert "CHRONICLE_PE_US_DATA_ROOT" in message


def test_policyengine_ledger_name_alone_still_works_and_warns(monkeypatch):
    monkeypatch.setenv("POLICYENGINE_LEDGER_SCHEMA", "ledger")

    with pytest.warns(ChronicleEnvDeprecationWarning) as warnings_raised:
        assert env_value("CHRONICLE_SCHEMA") == "ledger"

    message = str(warnings_raised[0].message)
    assert "POLICYENGINE_LEDGER_SCHEMA" in message
    assert "CHRONICLE_SCHEMA" in message


def test_deprecation_warning_is_raised_once_per_process(monkeypatch, recwarn):
    monkeypatch.setenv("LEDGER_PE_UK_DATA_ROOT", "/ledger")

    for _ in range(3):
        assert env_value("CHRONICLE_PE_UK_DATA_ROOT") == "/ledger"

    deprecations = [
        warning
        for warning in recwarn.list
        if issubclass(warning.category, ChronicleEnvDeprecationWarning)
    ]
    assert len(deprecations) == 1


def test_deprecation_warning_is_attributed_to_the_calling_module(monkeypatch):
    monkeypatch.setenv("LEDGER_SOURCE_ARTIFACT_FETCH", "1")

    with pytest.warns(ChronicleEnvDeprecationWarning) as warnings_raised:
        assert env_flag(SOURCE_ARTIFACT_FETCH_ENV)

    # env_flag and env_value must report at the same depth, or operators get a
    # notice pointing at Chronicle's own source instead of their call site.
    assert Path(warnings_raised[0].filename).name == "test_chronicle_env.py"


def test_unset_variables_fall_back_to_the_default():
    assert env_value("CHRONICLE_PE_US_DATA_ROOT") is None
    assert env_value("CHRONICLE_PE_US_DATA_ROOT", default="/fallback") == "/fallback"


def test_empty_values_count_as_unset(monkeypatch):
    monkeypatch.setenv("CHRONICLE_PE_US_DATA_ROOT", "")
    monkeypatch.setenv("LEDGER_PE_US_DATA_ROOT", "/ledger")

    with pytest.warns(ChronicleEnvDeprecationWarning):
        assert env_value("CHRONICLE_PE_US_DATA_ROOT") == "/ledger"


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_env_flag_accepts_truthy_spellings(monkeypatch, value):
    monkeypatch.setenv("CHRONICLE_SOURCE_ARTIFACT_FETCH", value)
    assert env_flag(SOURCE_ARTIFACT_FETCH_ENV)


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "maybe"])
def test_env_flag_rejects_other_values(monkeypatch, value):
    monkeypatch.setenv("CHRONICLE_SOURCE_ARTIFACT_FETCH", value)
    assert not env_flag(SOURCE_ARTIFACT_FETCH_ENV)


def test_env_flag_lets_the_chronicle_name_turn_a_legacy_flag_off(monkeypatch):
    monkeypatch.setenv("CHRONICLE_SOURCE_ARTIFACT_FETCH", "0")
    monkeypatch.setenv("LEDGER_SOURCE_ARTIFACT_FETCH", "1")

    # An operator who has migrated must be able to turn the flag off without
    # first hunting down the stale ledger-era variable.
    assert not env_flag(SOURCE_ARTIFACT_FETCH_ENV)


# ---------------------------------------------------------------------------
# Real call sites
# ---------------------------------------------------------------------------


def test_source_artifact_env_constants_are_chronicle_named():
    assert SOURCE_ARTIFACT_CACHE_ENV == "CHRONICLE_SOURCE_ARTIFACT_CACHE_DIR"
    assert SOURCE_ARTIFACT_FETCH_ENV == "CHRONICLE_SOURCE_ARTIFACT_FETCH"


@pytest.mark.parametrize(
    "name",
    ["CHRONICLE_SOURCE_ARTIFACT_CACHE_DIR", "LEDGER_SOURCE_ARTIFACT_CACHE_DIR"],
)
def test_source_artifact_cache_dir_honors_both_names(monkeypatch, tmp_path, name):
    from chronicle.source_package import _source_artifact_cache_path

    monkeypatch.setenv(name, str(tmp_path))

    cache_path = _source_artifact_cache_path(
        {"filename": "table.xlsx", "sha256": "abc123"}
    )

    assert cache_path == tmp_path / "abc123" / "table.xlsx"


@pytest.mark.parametrize(
    "name", ["CHRONICLE_PE_US_DATA_ROOT", "LEDGER_PE_US_DATA_ROOT"]
)
def test_pe_source_root_cli_default_honors_both_names(monkeypatch, name):
    from db.cli import _pe_source_root_env_default

    monkeypatch.setenv(name, "/pe-us")

    assert _pe_source_root_env_default("us") == "/pe-us"


def test_pe_source_inventory_env_constants_are_chronicle_named():
    from db.pe_source_inventory import PE_UK_DATA_ROOT_ENV, PE_US_DATA_ROOT_ENV

    assert PE_US_DATA_ROOT_ENV == "CHRONICLE_PE_US_DATA_ROOT"
    assert PE_UK_DATA_ROOT_ENV == "CHRONICLE_PE_UK_DATA_ROOT"


def test_db_cli_parser_builds_with_the_env_backed_defaults(monkeypatch, capsys):
    """The db CLI builds its parser before dispatching any subcommand.

    Its --pe-us-root/--pe-uk-root defaults call into the env helper, so an
    import error there breaks `chronicle init`, `load` and `stats` alike while
    the rest of the test suite stays green.
    """
    import db.cli

    monkeypatch.setenv("CHRONICLE_PE_US_DATA_ROOT", "/pe-us")
    monkeypatch.setattr("sys.argv", ["chronicle", "--help"])

    with pytest.raises(SystemExit) as exit_info:
        db.cli.main()

    assert exit_info.value.code == 0
    assert "Manage Chronicle target input data" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["CHRONICLE_SCHEMA", "POLICYENGINE_LEDGER_SCHEMA"])
def test_supabase_schema_honors_both_names(monkeypatch, name):
    import db.supabase_client

    monkeypatch.setenv(name, "chronicle_probe")
    try:
        reloaded = importlib.reload(db.supabase_client)
        assert reloaded.LEDGER_SCHEMA == "chronicle_probe"
    finally:
        monkeypatch.delenv(name, raising=False)
        importlib.reload(db.supabase_client)


def test_supabase_schema_default_is_unchanged():
    import db.supabase_client

    # The hosted schema name itself is out of this slice; only the variable
    # that overrides it moved.
    assert db.supabase_client.LEDGER_SCHEMA == "ledger"


# ---------------------------------------------------------------------------
# R2 bucket configuration
# ---------------------------------------------------------------------------


def test_r2_bucket_defaults_are_still_the_ledger_era_names():
    assert DEFAULT_R2_RAW_BUCKET == "ledger-raw"
    assert DEFAULT_R2_DERIVED_BUCKET == "ledger-derived"
    assert default_r2_raw_bucket() == "ledger-raw"
    assert default_r2_derived_bucket() == "ledger-derived"


def test_r2_buckets_follow_the_chronicle_env_vars(monkeypatch):
    monkeypatch.setenv("CHRONICLE_R2_RAW_BUCKET", "chronicle-raw")
    monkeypatch.setenv("CHRONICLE_R2_DERIVED_BUCKET", "chronicle-derived")

    assert default_r2_raw_bucket() == "chronicle-raw"
    assert default_r2_derived_bucket() == "chronicle-derived"


def test_r2_buckets_honor_ledger_era_names_with_a_warning(monkeypatch):
    monkeypatch.setenv("LEDGER_R2_RAW_BUCKET", "legacy-raw")

    with pytest.warns(ChronicleEnvDeprecationWarning):
        assert default_r2_raw_bucket() == "legacy-raw"


def test_bootstrap_r2_cli_creates_the_configured_buckets(monkeypatch, tmp_path):
    log = tmp_path / "wrangler.log"
    wrangler = _fake_wrangler(tmp_path, log)
    monkeypatch.setenv("CHRONICLE_R2_RAW_BUCKET", "chronicle-raw")
    monkeypatch.setenv("CHRONICLE_R2_DERIVED_BUCKET", "chronicle-derived")

    exit_code = harness_main(["bootstrap-r2", "--wrangler-command", str(wrangler)])

    commands = log.read_text()
    assert exit_code == 0
    assert "r2 bucket create chronicle-raw" in commands
    assert "r2 bucket create chronicle-derived" in commands


def test_bootstrap_r2_cli_flags_still_override_the_environment(monkeypatch, tmp_path):
    log = tmp_path / "wrangler.log"
    wrangler = _fake_wrangler(tmp_path, log)
    monkeypatch.setenv("CHRONICLE_R2_RAW_BUCKET", "chronicle-raw")

    harness_main(
        [
            "bootstrap-r2",
            "--raw-bucket",
            "explicit-raw",
            "--derived-bucket",
            "explicit-derived",
            "--wrangler-command",
            str(wrangler),
        ]
    )

    commands = log.read_text()
    assert "r2 bucket create explicit-raw" in commands
    assert "r2 bucket create explicit-derived" in commands
    assert "chronicle-raw" not in commands
