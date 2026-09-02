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
    "CHRONICLE_SCHEMA_ENV",
    "ChronicleEnvDeprecationWarning",
    "DEFAULT_CHRONICLE_SCHEMA",
    "LEGACY_ENV_PREFIXES",
    "default_chronicle_schema",
    "env_flag",
    "env_names",
    "env_value",
    "reset_env_deprecation_state",
]

CHRONICLE_ENV_PREFIX = "CHRONICLE_"

# Ordered most specific first so prefix stripping is unambiguous.
LEGACY_ENV_PREFIXES = ("POLICYENGINE_LEDGER_", "LEDGER_")

TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})

CHRONICLE_SCHEMA_ENV = "CHRONICLE_SCHEMA"

# The hosted Postgres schema is still named "ledger". Renaming the schema value
# is a later slice of PolicyEngine/chronicle#143, coordinated with the CI
# writers that already target it; only the variable that overrides the name has
# moved to the chronicle prefix.
DEFAULT_CHRONICLE_SCHEMA = "ledger"


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
    """Warn once per process that a ledger-era variable supplied a value.

    ``stacklevel=4`` walks out through :func:`_first_set` and its public
    wrapper so the notice points at the code that asked for the setting.
    """
    if found in _WARNED_LEGACY_NAMES:
        return
    _WARNED_LEGACY_NAMES.add(found)
    warnings.warn(
        f"{found} is a ledger-era Chronicle environment variable; "
        f"set {preferred} instead. The old name is still honored during the "
        "Chronicle rename window and will be removed once consumers migrate.",
        ChronicleEnvDeprecationWarning,
        stacklevel=4,
    )


def reset_env_deprecation_state() -> None:
    """Forget which legacy names have already warned. Test-support hook."""
    _WARNED_LEGACY_NAMES.clear()


def _first_set(names: tuple[str, ...]) -> str | None:
    """Return the first set value across ``names``, warning on a legacy hit.

    Both public readers call this at the same stack depth so the deprecation
    warning is always attributed to their caller, not to this module.
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
    return None


def env_value(*names: str, default: _Default = None) -> str | _Default:
    """Read the first set value across ``names``, chronicle-preferred first.

    Each name is expanded through :func:`env_names`, so a caller can pass the
    chronicle name and still pick up a value set under a ledger-era name.
    Empty values are treated as unset, matching the helpers this replaces.
    """
    value = _first_set(names)
    return default if value is None else value


def default_chronicle_schema() -> str:
    """Resolve the Chronicle schema: ``$CHRONICLE_SCHEMA``, else the default.

    Every reader of the setting goes through this function so the lookup ladder
    and the default have one home. It resolves at call time rather than at
    import: a module-level constant binds whatever the shell held when the
    module was first imported, which for a library means an arbitrary moment
    the caller cannot control, and for the test suite means collection.
    """
    return env_value(CHRONICLE_SCHEMA_ENV, default=DEFAULT_CHRONICLE_SCHEMA)


def env_flag(*names: str) -> bool:
    """Return whether the first set value across ``names`` reads as true.

    The chronicle-preferred name wins even when it reads false, so an operator
    who has migrated can turn a flag off without unsetting the legacy name.
    """
    value = _first_set(names)
    if value is None:
        return False
    return value.strip().lower() in TRUTHY_ENV_VALUES
