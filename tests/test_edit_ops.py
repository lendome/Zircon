import pytest

from zirconAgent.tools.edit_ops import EditFileTool, EditLinesTool


class DenyingGate:
    def __init__(self):
        self.requests = []

    async def request(self, name, arguments, reason):
        self.requests.append((name, arguments, reason))
        return False


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "code.py").write_text(
        "def hello():\n"
        '    return "hello"\n'
        "\n"
        "\n"
        "def goodbye():\n"
        '    return "goodbye"\n'
    )
    return tmp_path


class TestEditFileTool:
    @pytest.fixture
    def tool(self, repo):
        return EditFileTool(str(repo))

    @pytest.mark.asyncio
    async def test_exact_match_replace(self, tool, repo):
        result = await tool.run(
            path="code.py",
            search='return "hello"',
            replace='return "hello world"',
        )
        assert "Applied" in result
        assert "--- a/" in result
        content = (repo / "code.py").read_text()
        assert "hello world" in content
        assert "goodbye" in content

    @pytest.mark.asyncio
    async def test_multiline_replace(self, tool, repo):
        result = await tool.run(
            path="code.py",
            search="def hello():\n    return \"hello\"",
            replace="def hello(name):\n    return f\"hello {name}\"",
        )
        assert "Applied" in result
        content = (repo / "code.py").read_text()
        assert "hello {name}" in content

    @pytest.mark.asyncio
    async def test_no_match(self, tool, repo):
        result = await tool.run(
            path="code.py",
            search="nonexistent text here",
            replace="replacement",
        )
        assert "No match" in result

    @pytest.mark.asyncio
    async def test_empty_search(self, tool, repo):
        result = await tool.run(path="code.py", search="", replace="x")
        assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_missing_file(self, tool, repo):
        result = await tool.run(path="missing.py", search="x", replace="y")
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_fuzzy_match(self, tool, repo):
        result = await tool.run(
            path="code.py",
            search="def hello():\n    return 'hello'",
            replace="def hello():\n    return 'hi'",
        )
        assert "Applied" in result

    @pytest.mark.asyncio
    async def test_fuzzy_match_can_be_denied_before_mutation(self, repo):
        gate = DenyingGate()
        tool = EditFileTool(str(repo), approval_gate=gate)
        original = (repo / "code.py").read_text()

        result = await tool.run(
            path="code.py",
            search="def hello():\n    return 'hello'",
            replace="def hello():\n    return 'hi'",
        )

        assert result.startswith("Error: edit denied")
        assert (repo / "code.py").read_text() == original
        assert gate.requests[0][1]["diff"].startswith("--- a/")


class TestEditLinesTool:
    @pytest.fixture
    def tool(self, repo):
        return EditLinesTool(str(repo))

    @pytest.mark.asyncio
    async def test_replace_single_line(self, tool, repo):
        result = await tool.run(path="code.py", start=2, end=2, content='    return "HELLO"')
        assert "Replaced" in result
        assert "--- a/" not in result
        content = (repo / "code.py").read_text()
        assert "HELLO" in content

    @pytest.mark.asyncio
    async def test_replace_range(self, tool, repo):
        result = await tool.run(
            path="code.py",
            start=1,
            end=2,
            content="def hello():\n    return 'bonjour'",
        )
        assert "Replaced" in result
        content = (repo / "code.py").read_text()
        assert "bonjour" in content

    @pytest.mark.asyncio
    async def test_start_beyond_file(self, tool, repo):
        result = await tool.run(path="code.py", start=999, end=999, content="x")
        assert "edit failed" in result.lower() or "exceeds" in result.lower() or "length" in result.lower()

    @pytest.mark.asyncio
    async def test_insert_at_end(self, tool, repo):
        result = await tool.run(path="code.py", start=5, end=5, content='    return "standalone"\n\n\ndef extra():\n    pass\n')
        assert "Replaced" in result
