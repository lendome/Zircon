from __future__ import annotations

import json
from typing import Any


PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "description": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["explore", "edit", "verify", "research"],
                    },
                    "target_files": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["index", "description", "action"],
            },
        },
        "files_likely_needed": {
            "type": "array",
            "items": {"type": "string"},
        },
        "complexity": {
            "type": "string",
            "enum": ["simple", "moderate", "complex"],
        },
    },
    "required": ["steps"],
}


def extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace : last_brace + 1])
        except json.JSONDecodeError:
            pass

    return None
