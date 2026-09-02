"""Shared fixtures for the Chronicle test suite.

Chronicle is mid-rename (PolicyEngine/chronicle#143, mechanism 3), so its
settings answer to three prefixes at once: ``CHRONICLE_``, and the ledger-era
``POLICYENGINE_LEDGER_`` and ``LEDGER_``. Any of them can be set in an
operator's shell, and many tests assert the defaults those variables override.
Isolation therefore belongs to the whole suite, not to one module.
"""

from __future__ import annotations

import os

import pytest

from chronicle.env import (
    CHRONICLE_ENV_PREFIX,
    LEGACY_ENV_PREFIXES,
    reset_env_deprecation_state,
)

RENAME_WINDOW_PREFIXES = (CHRONICLE_ENV_PREFIX, *LEGACY_ENV_PREFIXES)


@pytest.fixture(autouse=True)
def isolated_rename_window_env(monkeypatch):
    """Run every test with no rename-window variable inherited from the shell."""
    for name in list(os.environ):
        if name.startswith(RENAME_WINDOW_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    reset_env_deprecation_state()
    yield
    reset_env_deprecation_state()
