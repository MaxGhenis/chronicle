"""Environment configuration for the Chronicle rename window.

Chronicle's operational stores migrate by dual-run (PolicyEngine/chronicle#143,
mechanism 3): every configuration variable gets a ``CHRONICLE_``-prefixed name
that is read first, while the ledger-era ``LEDGER_`` and
``POLICYENGINE_LEDGER_`` names keep working behind a deprecation warning. That
window lets downstream publish flows migrate on their own schedule instead of
breaking the moment Chronicle ships a rename.

Names that carry none of those three prefixes are read literally: this helper
renames the ledger-era surface, not every PolicyEngine variable.
"""

from __future__ import annotations

import os
from typing import TypeVar
import warnings

__all__ = [
    "CHRONICLE_ENV_PREFIX",
    "ChronicleEnvDeprecationWarning",
    "LEGACY_ENV_PREFIXES",
    "env_flag",
    "env_names",
    "env_value",
    "reset_env_deprecation_state",
]

CHRONICLE_ENV_PREFIX = "CHRONICLE_"

# Ordered most specific first so prefix stripping is unambiguous.
LEGACY_ENV_PREFIXES = ("POLICYENGINE_LEDGER_", "LEDGER_")

TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


class ChronicleEnvDeprecationWarning(FutureWarning):
    """A ledger-era environment variable supplied a Chronicle setting.

    Subclasses :class:`FutureWarning` rather than :class:`DeprecationWarning`
    so the notice reaches operators running the CLI, who are the people who
    have to move the variable. ``DeprecationWarning`` is silenced by default
    outside ``__main__``.
    """


_Default = TypeVar("_Default")

_WARNED_LEGACY_NAMES: set[str] = set()


def _env_suffix(name: str) -> str | None:
    """Return the rename-window suffix of ``name``, or None if it has none."""
    for prefix in (CHRONICLE_ENV_PREFIX, *LEGACY_ENV_PREFIXES):
        if name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix) :]
    return None


def env_names(name: str) -> tuple[str, ...]:
    """Return the lookup order for ``name``.

    The chronicle-preferred name comes first, then the ledger-era names that
    remain accepted during the migration window. A name outside the rename
    window is returned unchanged, as its own single-element lookup order.
    """
    suffix = _env_suffix(name)
    if suffix is None:
        return (name,)
    return (
        f"{CHRONICLE_ENV_PREFIX}{suffix}",
        *(f"{prefix}{suffix}" for prefix in LEGACY_ENV_PREFIXES),
    )


def _warn_legacy(found: str, preferred: str) -> None:
    """Warn once per process that a ledger-era variable supplied a value."""
    if found in _WARNED_LEGACY_NAMES:
        return
    _WARNED_LEGACY_NAMES.add(found)
    warnings.warn(
        f"{found} is a ledger-era Chronicle environment variable; "
        f"set {preferred} instead. The old name is still honored during the "
        "Chronicle rename window and will be removed once consumers migrate.",
        ChronicleEnvDeprecationWarning,
        stacklevel=3,
    )


def reset_env_deprecation_state() -> None:
    """Forget which legacy names have already warned. Test-support hook."""
    _WARNED_LEGACY_NAMES.clear()


def env_value(*names: str, default: _Default = None) -> str | _Default:
    """Read the first set value across ``names``, chronicle-preferred first.

    Each name is expanded through :func:`env_names`, so a caller can pass the
    chronicle name and still pick up a value set under a ledger-era name.
    Empty values are treated as unset, matching the helpers this replaces.
    """
    for name in names:
        candidates = env_names(name)
        preferred = candidates[0]
        for candidate in candidates:
            value = os.environ.get(candidate)
            if value:
                if candidate != preferred:
                    _warn_legacy(candidate, preferred)
                return value
    return default


def env_flag(*names: str) -> bool:
    """Return whether the first set value across ``names`` reads as true."""
    value = env_value(*names)
    if value is None:
        return False
    return value.strip().lower() in TRUTHY_ENV_VALUES
