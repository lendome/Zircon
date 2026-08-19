import pytest
from zirconAgent.llm.structured import extract_json, PLAN_SCHEMA


class TestExtractJson:
    def test_clean_json(self):
        text = '{"steps": []}'
        result = extract_json(text)
        assert result == {"steps": []}

    def test_json_in_markdown(self):
        text = '```json\n{"steps": []}\n```'
        result = extract_json(text)
        assert result == {"steps": []}

    def test_json_in_text(self):
        text = 'Here is the plan:\n{"steps": [{"index": 0}]}\nThat is all.'
        result = extract_json(text)
        assert result is not None
        assert result["steps"][0]["index"] == 0

    def test_nested_json(self):
        text = '{"steps": [{"index": 0, "description": "explore"}], "complexity": "simple"}'
        result = extract_json(text)
        assert result["complexity"] == "simple"

    def test_no_json(self):
        assert extract_json("just plain text") is None

    def test_empty_string(self):
        assert extract_json("") is None

    def test_malformed_json_fallback(self):
        text = 'no json here { broken'
        result = extract_json(text)
        assert result is None

    def test_markdown_with_language(self):
        text = '```json\n{"key": "value"}\n```'
        assert extract_json(text) == {"key": "value"}

    def test_markdown_without_language(self):
        text = '```\n{"key": "value"}\n```'
        assert extract_json(text) == {"key": "value"}


class TestPlanSchema:
    def test_schema_structure(self):
        assert PLAN_SCHEMA["type"] == "object"
        assert "steps" in PLAN_SCHEMA["properties"]
        assert "required" in PLAN_SCHEMA
        assert "steps" in PLAN_SCHEMA["required"]

    def test_step_action_enum(self):
        step_props = PLAN_SCHEMA["properties"]["steps"]["items"]
        action_enum = step_props["properties"]["action"]["enum"]
        assert "explore" in action_enum
        assert "edit" in action_enum
        assert "verify" in action_enum
        assert "research" in action_enum
