import pytest
from zirconAgent.core.tool_search import ToolSearchIndex
from zirconAgent.tools.base import Tool
from typing import Any


class DummyTool(Tool):
    def __init__(self, n, desc):
        self._name = n
        self._desc = desc

    @property
    def name(self): return self._name

    @property
    def description(self): return self._desc

    @property
    def schema(self): return {"type": "object", "properties": {}}

    async def run(self, **kwargs): return "ok"


@pytest.fixture
def idx():
    tsi = ToolSearchIndex()
    tsi.register_all([
        DummyTool("read_file", "Read the contents of a file"),
        DummyTool("edit_file", "Edit a file using search and replace"),
        DummyTool("create_file", "Create a new file"),
        DummyTool("grep_code", "Search file contents for a regex pattern"),
        DummyTool("run_command", "Execute a shell command"),
        DummyTool("fetch_url", "Fetch the content of a URL"),
    ])
    return tsi


class TestKeywordMatching:
    def test_edit_query(self, idx):
        tools = idx.get_relevant_tools("change the function in app.py")
        names = [t.name for t in tools]
        assert "edit_file" in names

    def test_read_query(self, idx):
        tools = idx.get_relevant_tools("read the contents of main.py")
        names = [t.name for t in tools]
        assert "read_file" in names

    def test_search_query(self, idx):
        tools = idx.get_relevant_tools("search for all TODO comments")
        names = [t.name for t in tools]
        assert "grep_code" in names

    def test_shell_query(self, idx):
        tools = idx.get_relevant_tools("run the test suite")
        names = [t.name for t in tools]
        assert "run_command" in names

    def test_web_query(self, idx):
        tools = idx.get_relevant_tools("fetch the API docs from the website")
        names = [t.name for t in tools]
        assert "fetch_url" in names


class TestMaxTools:
    def test_limits_results(self, idx):
        tools = idx.get_relevant_tools("edit and read files and search", max_tools=2)
        assert len(tools) <= 2


class TestGetSchemas:
    def test_schemas_for_query(self, idx):
        schemas = idx.get_schemas_for_query("edit the file")
        assert len(schemas) >= 1
        assert all("name" in s for s in schemas)

    def test_all_schemas(self, idx):
        schemas = idx.get_all_schemas()
        assert len(schemas) == 6


class TestFallback:
    def test_unrelated_query_returns_tools(self, idx):
        tools = idx.get_relevant_tools("quantum physics equation")
        assert len(tools) >= 1
