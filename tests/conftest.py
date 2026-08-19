import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from zirconAgent.core.agent import Agent
from zirconAgent.core.config import RouterConfig, AgentConfig
from zirconAgent.core.types import ModelProfile
from zirconAgent.llm.router import ModelRouter
from zirconAgent.tests.mocks import make_router


@pytest.fixture
def tmp_repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()

    (src / "main.py").write_text(
        'def greet(name: str) -> str:\n'
        '    return f"Hello, {name}!"\n'
        '\n'
        '\n'
        'def add(a: int, b: int) -> int:\n'
        '    return a + b\n'
        '\n'
        '\n'
        'if __name__ == "__main__":\n'
        '    print(greet("world"))\n'
    )

    (src / "utils.py").write_text(
        'import os\n'
        'import sys\n'
        '\n'
        '\n'
        'def load_config(path: str) -> dict:\n'
        '    """Load a JSON config file."""\n'
        '    with open(path) as f:\n'
        '        return json.load(f)\n'
        '\n'
        '\n'
        'def save_config(path: str, data: dict) -> None:\n'
        '    """Save a JSON config file."""\n'
        '    with open(path, "w") as f:\n'
        '        json.dump(data, f)\n'
        '\n'
        '\n'
        'class ConfigManager:\n'
        '    def __init__(self, config_path: str):\n'
        '        self.path = config_path\n'
        '        self._data = {}\n'
        '\n'
        '    def load(self) -> dict:\n'
        '        self._data = load_config(self.path)\n'
        '        return self._data\n'
        '\n'
        '    def save(self, data: dict) -> None:\n'
        '        self._data = data\n'
        '        save_config(self.path, data)\n'
        '\n'
        '    def get(self, key: str, default=None):\n'
        '        return self._data.get(key, default)\n'
    )

    (src / "__init__.py").write_text("")

    (tmp_path / "README.md").write_text("# Test Project\n\nA sample project for testing.\n")

    (tmp_path / "requirements.txt").write_text("pytest>=7.0\nhttpx\n")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_main.py").write_text(
        "from src.main import greet, add\n\n\n"
        "def test_greet():\n"
        '    assert greet("world") == "Hello, world!"\n\n\n'
        "def test_add():\n"
        "    assert add(1, 2) == 3\n"
    )

    return tmp_path


@pytest.fixture
def mock_router():
    return make_router()


@pytest.fixture
def agent_with_mock(tmp_repo, mock_router):
    cfg = AgentConfig()
    a = Agent.__new__(Agent)
    a.repo_path = tmp_repo
    a.config = cfg
    a.router = mock_router
    a.registry = Agent._register_tools(a) if False else None
    from zirconAgent.tools.registry import ToolRegistry
    from zirconAgent.tools.file_ops import ReadFileTool, CreateFileTool, GlobFilesTool, ListDirTool, DeleteFileTool
    from zirconAgent.tools.edit_ops import EditFileTool, EditLinesTool
    from zirconAgent.tools.search_ops import GrepCodeTool, FindSymbolsTool, GetStructureTool
    from zirconAgent.tools.shell_ops import RunCommandTool
    from zirconAgent.tools.web_ops import FetchUrlTool

    a.registry = ToolRegistry()
    rp = str(tmp_repo)
    a.registry.register_all([
        ReadFileTool(rp), CreateFileTool(rp), DeleteFileTool(rp),
        GlobFilesTool(rp), ListDirTool(rp),
        EditFileTool(rp), EditLinesTool(rp),
        GrepCodeTool(rp), FindSymbolsTool(rp), GetStructureTool(rp),
        RunCommandTool(rp), FetchUrlTool(),
    ])
    from zirconAgent.core.context import ContextManager
    from zirconAgent.core.session import SessionManager
    from zirconAgent.core.planner import Planner
    from zirconAgent.core.executor import Executor
    from zirconAgent.core.kg_memory import KnowledgeGraphMemory
    a.context = ContextManager(tmp_repo, context_window=32000, safety_margin=400, kg_memory=KnowledgeGraphMemory(str(tmp_repo)))
    a.kg = a.context.kg
    a.sessions = SessionManager(tmp_repo)
    a.planner = Planner(mock_router)
    a.executor = Executor(mock_router, a.registry)
    return a
