import pytest
from pathlib import Path

from zirconAgent.tools.file_ops import ReadFileTool, CreateFileTool, GlobFilesTool, ListDirTool, DeleteFileTool


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\nprint('world')\n")
    (tmp_path / "src" / "util.py").write_text("def helper(): pass\n")
    (tmp_path / "README.md").write_text("# Test\n")
    return tmp_path


class TestReadFileTool:
    @pytest.fixture
    def tool(self, repo):
        return ReadFileTool(str(repo))

    @pytest.mark.asyncio
    async def test_read_full_file(self, tool, repo):
        result = await tool.run(path=str(repo / "README.md"))
        assert "# Test" in result

    @pytest.mark.asyncio
    async def test_read_with_line_numbers(self, tool, repo):
        result = await tool.run(path=str(repo / "src" / "app.py"))
        assert "1:" in result
        assert "2:" in result

    @pytest.mark.asyncio
    async def test_read_line_range(self, tool, repo):
        result = await tool.run(path=str(repo / "src" / "app.py"), start=1, end=1)
        assert "print('hello')" in result
        assert "world" not in result

    @pytest.mark.asyncio
    async def test_read_missing_file(self, tool):
        result = await tool.run(path="nonexistent.py")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_read_relative_path(self, tool, repo):
        result = await tool.run(path="README.md")
        assert "# Test" in result

    @pytest.mark.asyncio
    async def test_read_partial_lines(self, tool, repo):
        result = await tool.run(path="src/app.py", start=2, end=2)
        assert "world" in result
        assert "hello" not in result


class TestCreateFileTool:
    @pytest.fixture
    def tool(self, repo):
        return CreateFileTool(str(repo))

    @pytest.mark.asyncio
    async def test_create_new_file(self, tool, repo):
        result = await tool.run(path="new.txt", content="hello world")
        assert "Created" in result
        assert (repo / "new.txt").read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_create_in_subdirectory(self, tool, repo):
        result = await tool.run(path="deep/nested/file.py", content="pass")
        assert "Created" in result
        assert (repo / "deep" / "nested" / "file.py").exists()

    @pytest.mark.asyncio
    async def test_create_existing_file_fails(self, tool, repo):
        result = await tool.run(path="README.md", content="overwrite")
        assert "already exists" in result


class TestGlobFilesTool:
    @pytest.fixture
    def tool(self, repo):
        return GlobFilesTool(str(repo))

    @pytest.mark.asyncio
    async def test_glob_py_files(self, tool):
        result = await tool.run(pattern="**/*.py")
        assert "app.py" in result
        assert "util.py" in result

    @pytest.mark.asyncio
    async def test_glob_md_files(self, tool):
        result = await tool.run(pattern="*.md")
        assert "README.md" in result

    @pytest.mark.asyncio
    async def test_glob_no_matches(self, tool):
        result = await tool.run(pattern="*.rust")
        assert "No files" in result


class TestListDirTool:
    @pytest.fixture
    def tool(self, repo):
        return ListDirTool(str(repo))

    @pytest.mark.asyncio
    async def test_list_root(self, tool):
        result = await tool.run()
        assert "src/" in result
        assert "README.md" in result

    @pytest.mark.asyncio
    async def test_list_subdir(self, tool):
        result = await tool.run(path="src")
        assert "app.py" in result

    @pytest.mark.asyncio
    async def test_list_nonexistent(self, tool):
        result = await tool.run(path="nope")
        assert "Error" in result


class TestDeleteFileTool:
    @pytest.fixture
    def tool(self, repo):
        return DeleteFileTool(str(repo))

    @pytest.mark.asyncio
    async def test_delete_existing(self, tool, repo):
        result = await tool.run(path="README.md")
        assert "Deleted" in result
        assert not (repo / "README.md").exists()

    @pytest.mark.asyncio
    async def test_delete_missing(self, tool):
        result = await tool.run(path="nope.txt")
        assert "Error" in result
