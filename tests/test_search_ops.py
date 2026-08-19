import pytest
from pathlib import Path

from zirconAgent.tools.search_ops import GrepCodeTool, FindSymbolsTool, GetStructureTool


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text(
        "def calculate(x, y):\n"
        "    return x + y\n"
        "\n"
        "\n"
        "def process_data(data):\n"
        '    result = calculate(len(data), 42)\n'
        "    return result\n"
    )
    (src / "models.py").write_text(
        "class User:\n"
        "    def __init__(self, name, email):\n"
        "        self.name = name\n"
        "        self.email = email\n"
        "\n"
        "    def get_display_name(self):\n"
        '        return f"{self.name} <{self.email}>"\n'
        "\n"
        "\n"
        "class Admin(User):\n"
        "    def can_delete(self):\n"
        "        return True\n"
    )
    return tmp_path


class TestGrepCodeTool:
    @pytest.fixture
    def tool(self, repo):
        return GrepCodeTool(str(repo))

    @pytest.mark.asyncio
    async def test_basic_search(self, tool):
        result = await tool.run(pattern="calculate")
        assert "calculate" in result

    @pytest.mark.asyncio
    async def test_search_class(self, tool):
        result = await tool.run(pattern="class ")
        assert "User" in result
        assert "Admin" in result

    @pytest.mark.asyncio
    async def test_search_no_match(self, tool):
        result = await tool.run(pattern="zzzznonexistent")
        assert "No matches" in result

    @pytest.mark.asyncio
    async def test_search_with_include(self, tool):
        result = await tool.run(pattern="def ", include="*.py")
        assert "calculate" in result or "process_data" in result

    @pytest.mark.asyncio
    async def test_invalid_regex(self, tool):
        result = await tool.run(pattern="[invalid regex")
        assert "Invalid regex" in result


class TestFindSymbolsTool:
    @pytest.fixture
    def tool(self, repo):
        return FindSymbolsTool(str(repo))

    @pytest.mark.asyncio
    async def test_find_function(self, tool):
        result = await tool.run(name="calculate")
        assert "calculate" in result

    @pytest.mark.asyncio
    async def test_find_class(self, tool):
        result = await tool.run(name="User")
        assert "User" in result
        assert "class" in result

    @pytest.mark.asyncio
    async def test_find_method(self, tool):
        result = await tool.run(name="get_display_name")
        assert "get_display_name" in result

    @pytest.mark.asyncio
    async def test_case_insensitive(self, tool):
        result = await tool.run(name="CALCULATE")
        assert "calculate" in result

    @pytest.mark.asyncio
    async def test_no_match(self, tool):
        result = await tool.run(name="nonexistent_symbol")
        assert "No symbols" in result

    @pytest.mark.asyncio
    async def test_filter_by_type(self, tool):
        result = await tool.run(name="User", type="class")
        assert "class" in result


class TestGetStructureTool:
    @pytest.fixture
    def tool(self, repo):
        return GetStructureTool(str(repo))

    @pytest.mark.asyncio
    async def test_structure_app(self, tool):
        result = await tool.run(path="src/app.py")
        assert "calculate" in result
        assert "process_data" in result
        assert "function" in result

    @pytest.mark.asyncio
    async def test_structure_models(self, tool):
        result = await tool.run(path="src/models.py")
        assert "User" in result
        assert "class" in result
        assert "Admin" in result
        assert "method" in result

    @pytest.mark.asyncio
    async def test_missing_file(self, tool):
        result = await tool.run(path="missing.py")
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_directory_outlines_every_source_file(self, tool):
        result = await tool.run(path="src")
        # One call covers all files in the folder, each with a header
        assert "── app.py" in result
        assert "── models.py" in result
        assert "calculate" in result
        assert "User" in result
        assert "lines)" in result

    @pytest.mark.asyncio
    async def test_recursive_directory_outline_is_bounded(self, tool, repo):
        nested = repo / "src" / "nested"
        nested.mkdir()
        (nested / "worker.py").write_text("def nested_worker():\n    pass\n")

        shallow = await tool.run(path="src")
        recursive = await tool.run(path="src", recursive=True, max_files=100)

        assert "nested_worker" not in shallow
        assert "── nested/worker.py" in recursive
        assert "nested_worker" in recursive
