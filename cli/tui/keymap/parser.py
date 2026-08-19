"""
Binding string parser — parses user config overrides and validates against
the Definitions schema.

Unknown binding names are rejected at parse time to catch typos.
"""

from __future__ import annotations

from typing import Any

from .definitions import Definitions, Binding
from .aliases import expand_binding_string


def parse_binding_string(binding: str) -> list[str]:
    """Parse a comma-separated binding string into key sequence alternatives."""
    return expand_binding_string(binding)


def parse_keybinds(overrides: dict[str, str]) -> dict[str, list[str]]:
    """Parse user keybind overrides, validating against Definitions.

    Args:
        overrides: dict of binding_name -> key_sequence_string

    Returns:
        dict of binding_name -> list of key sequences

    Raises:
        ValueError: if an unknown binding name is encountered
    """
    result: dict[str, list[str]] = {}

    for key in overrides:
        if key not in Definitions:
            raise ValueError(f"Unrecognized keybind: {key}")

    for name, defn in Definitions.items():
        if name in overrides:
            result[name] = parse_binding_string(overrides[name])
        else:
            result[name] = parse_binding_string(defn.default)

    return result


def validate_overrides(overrides: dict[str, str]) -> list[str]:
    """Validate overrides without raising. Returns list of error messages."""
    errors: list[str] = []
    for key in overrides:
        if key not in Definitions:
            errors.append(f"Unrecognized keybind: {key}")
    return errors


def get_binding_description(name: str) -> str:
    """Get the description for a binding name."""
    defn = Definitions.get(name)
    return defn.description if defn else ""
