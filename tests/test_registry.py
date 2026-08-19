import pytest
from zirconAgent.tools.registry import ToolRegistry
from zirconAgent.tools.base import Tool
from typing import Any


class DummyTool(Tool):
    @property
    def name(self) -> str:
        return "dummy"

    @property
    def description(self) -> str:
        return "A dummy tool for testing"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "input": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["input"],
        }

    async def run(self, **kwargs) -> str:
        return f"ran with {kwargs}"


class BrokenTool(Tool):
    @property
    def name(self) -> str:
        return "broken"

    @property
    def description(self) -> str:
        return "Always errors"

    @property
    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def run(self, **kwargs) -> str:
        raise RuntimeError("intentional error")


class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        tool = DummyTool()
        reg.register(tool)
        assert reg.get("dummy") is tool

    def test_get_missing(self):
        reg = ToolRegistry()
        assert reg.get("nonexistent") is None

    def test_list_names(self):
        reg = ToolRegistry()
        reg.register(DummyTool())
        assert reg.list_names() == ["dummy"]

    def test_register_all(self):
        reg = ToolRegistry()
        reg.register_all([DummyTool(), BrokenTool()])
        assert len(reg.list_names()) == 2

    @pytest.mark.asyncio
    async def test_execute_success(self):
        reg = ToolRegistry()
        reg.register(DummyTool())
        result = await reg.execute("dummy", {"input": "hello", "count": 3})
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        reg = ToolRegistry()
        result = await reg.execute("missing", {})
        assert "Error" in result
        assert "unknown tool" in result

    @pytest.mark.asyncio
    async def test_execute_error_handling(self):
        reg = ToolRegistry()
        reg.register(BrokenTool())
        result = await reg.execute("broken", {})
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_execute_missing_required_arg(self):
        reg = ToolRegistry()
        reg.register(DummyTool())
        result = await reg.execute("dummy", {})
        assert "Error" in result
        assert "Missing required argument 'input'" in result

    @pytest.mark.asyncio
    async def test_execute_wrong_type_arg(self):
        reg = ToolRegistry()
        reg.register(DummyTool())
        result = await reg.execute("dummy", {"input": 123})
        assert "Error" in result
        assert "must be of type string" in result

    def test_get_schemas_all(self):
        reg = ToolRegistry()
        reg.register(DummyTool())
        schemas = reg.get_schemas()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "dummy"
        assert "parameters" in schemas[0]

    def test_get_schemas_filtered(self):
        reg = ToolRegistry()
        reg.register_all([DummyTool(), BrokenTool()])
        schemas = reg.get_schemas(["dummy"])
        assert len(schemas) == 1
        assert schemas[0]["name"] == "dummy"

    def test_get_schemas_missing_name_ignored(self):
        reg = ToolRegistry()
        reg.register(DummyTool())
        schemas = reg.get_schemas(["nonexistent"])
        assert len(schemas) == 0

    def test_tool_descriptions(self):
        reg = ToolRegistry()
        reg.register(DummyTool())
        desc = reg.tool_descriptions()
        assert "dummy" in desc
        assert "input" in desc

    def test_to_openai_schema(self):
        tool = DummyTool()
        schema = tool.to_openai_schema()
        assert schema["name"] == "dummy"
        assert schema["description"] == "A dummy tool for testing"
        assert "parameters" in schema
