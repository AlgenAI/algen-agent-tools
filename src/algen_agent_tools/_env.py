"""``env://`` references: the only way a connector receives configuration and secrets."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable

from algen_agent_runtime.exceptions.errors import ConfigurationError

SecretResolver = Callable[[str], Awaitable[str]]

_REFERENCE = re.compile(r"^env://([A-Za-z_][A-Za-z0-9_]*)$")


def env_name(reference: str) -> str:
    """The variable name in ``env://NAME``; anything else is a configuration error."""
    match = _REFERENCE.fullmatch(reference)
    if match is None:
        raise ConfigurationError(
            f"{reference!r} must be an env:// reference such as env://ALGEN_TOOLS_TOKEN"
        )
    return match.group(1)


def env_names(*references: str | None) -> tuple[str, ...]:
    """The variable names of every reference given, in order, skipping ``None``."""
    return tuple(env_name(reference) for reference in references if reference is not None)


async def resolve_optional(secrets: SecretResolver, reference: str | None) -> str | None:
    """Resolve a reference whose variable may be unset; ``None`` when it is."""
    if reference is None:
        return None
    env_name(reference)
    try:
        value = await secrets(reference)
    except ConfigurationError:
        return None
    return value or None


def split_list(value: str | None) -> tuple[str, ...]:
    """A comma-separated setting as a tuple, without blanks."""
    return tuple(item.strip() for item in (value or "").split(",") if item.strip())


def missing_extra(package: str, extra: str) -> ConfigurationError:
    return ConfigurationError(
        f"the {extra} connector needs the {package!r} package; install it with "
        f"pip install 'algen-agent-tools[{extra}]'"
    )


def require_all(names: Iterable[str]) -> None:
    """Fail when a required configuration name is empty."""
    empty = [name for name in names if not name]
    if empty:
        raise ConfigurationError(f"missing required settings: {empty}")
