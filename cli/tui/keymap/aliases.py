"""
Key aliases — common alternative names for keys.

Users can type 'enter' or 'return' interchangeably in config. The expander
normalizes aliases before matching.
"""

from __future__ import annotations


KeyAliases: dict[str, str] = {
    "enter": "return",
    "esc": "escape",
    "pgdown": "pagedown",
    "pgup": "pageup",
    "del": "delete",
    "bs": "backspace",
    "tab": "tab",
    "space": "space",
    "ret": "return",
    "cr": "return",
    "lf": "return",
}


def expand_key_aliases(key: str) -> str:
    """Normalize a key name using aliases."""
    lower = key.lower()
    if lower in KeyAliases:
        return KeyAliases[lower]
    return lower


def expand_binding_string(binding: str) -> list[str]:
    """Expand a comma-separated binding string into a list of key sequences."""
    return [seq.strip() for seq in binding.split(",") if seq.strip()]


def normalize_key_sequence(seq: str) -> str:
    """Normalize a full key sequence, expanding aliases in each part."""
    parts = seq.split("+")
    normalized = [expand_key_aliases(p) for p in parts]
    return "+".join(normalized)
