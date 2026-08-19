import pytest
from pathlib import Path
from zirconAgent.tools.file_ops import ReadFileTool, CreateFileTool, GlobFilesTool, ListDirTool, DeleteFileTool

@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    for i in range(30):
        (tmp_path / "src" / f"mod_{i}.py").write_text(f"# module {i}\nx = {i}\n")
        (tmp_path / "tests" / f"test_{i}.py").write_text(f"from src.mod_{i} import x\nassert x == {i}\n")
    (tmp_path / "README.md").write_text("# Project\n")
    (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup()\n")
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "config.json").write_text('{"key": "value"}')
    return tmp_path

@pytest.fixture
def read_tool(repo): return ReadFileTool(str(repo))
@pytest.fixture
def create_tool(repo): return CreateFileTool(str(repo))
@pytest.fixture
def glob_tool(repo): return GlobFilesTool(str(repo))
@pytest.fixture
def list_tool(repo): return ListDirTool(str(repo))
@pytest.fixture
def delete_tool(repo): return DeleteFileTool(str(repo))

READ_EXISTING = [f"src/mod_{i}.py" for i in range(30)] + [f"tests/test_{i}.py" for i in range(30)] + [
    "README.md", "setup.py", "src/__init__.py", "tests/__init__.py", "data/config.json"
]

@pytest.mark.asyncio
@pytest.mark.parametrize("path", READ_EXISTING, ids=[f"read_{i}" for i in range(len(READ_EXISTING))])
async def test_read_existing_files(read_tool, path):
    r = await read_tool.run(path=path)
    assert "Error" not in r

@pytest.mark.asyncio
@pytest.mark.parametrize("path", READ_EXISTING, ids=[f"read_range_{i}" for i in range(len(READ_EXISTING))])
async def test_read_with_line_range(read_tool, path):
    r = await read_tool.run(path=path, start=1, end=2)
    assert "Error" not in r

MISSING = [f"nonexistent_{i}.py" for i in range(30)] + [
    "no/such/file.py", "missing.txt", "absent.json", "phantom.py", "ghost.py", "void.py", "nada.py"
]

@pytest.mark.asyncio
@pytest.mark.parametrize("path", MISSING, ids=[f"read_miss_{i}" for i in range(len(MISSING))])
async def test_read_missing_files(read_tool, path):
    r = await read_tool.run(path=path)
    assert "Error" in r

@pytest.mark.asyncio
@pytest.mark.parametrize("start", list(range(1, 21)), ids=[f"rs_{i}" for i in range(20)])
@pytest.mark.parametrize("end", list(range(1, 6)), ids=[f"re_{i}" for i in range(5)])
async def test_read_range_combos(read_tool, start, end):
    r = await read_tool.run(path="README.md", start=start, end=start + end)
    assert isinstance(r, str)

CREATE_FILES = [(f"new_dir_{i // 10}/file_{i}.{ext}", f"content {i}")
    for i in range(100)
    for ext in [["py","js","ts","md","txt","json","yaml","toml","cfg","ini"][i % 10]]
]

@pytest.mark.asyncio
@pytest.mark.parametrize("path,content", CREATE_FILES, ids=[f"create_{i}" for i in range(len(CREATE_FILES))])
async def test_create_files(create_tool, tmp_path, path, content):
    r = await create_tool.run(path=path, content=content)
    assert "Created" in r
    assert (tmp_path / path).exists()

GLOB_PATTERNS = [
    "*.py", "*.md", "*.txt", "*.json", "*.yaml", "*.toml",
    "**/*.py", "**/*.txt", "**/*.json",
    "src/*.py", "tests/*.py", "data/*",
    "src/**/*.py", "**/__init__.py",
] + [f"**/mod_{i}.py" for i in range(30)] + [f"**/test_{i}.py" for i in range(30)]

@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", GLOB_PATTERNS, ids=[f"glob_{i}" for i in range(len(GLOB_PATTERNS))])
async def test_glob_patterns(glob_tool, pattern):
    r = await glob_tool.run(pattern=pattern)
    assert isinstance(r, str)

GLOB_MISS = [f"*.rs", "*.go", "*.java", "*.cpp", "*.h", "*.rb", "*.php", "*.swift",
             "*.kt", "*.scala", "*.lua", "*.r", "*.m", "*.sh", "*.bat", "*.ps1",
             "*.dockerfile", "Dockerfile*", "*.xml", "*.csv"]

@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", GLOB_MISS, ids=[f"glob_miss_{i}" for i in range(len(GLOB_MISS))])
async def test_glob_no_matches(glob_tool, pattern):
    r = await glob_tool.run(pattern=pattern)
    assert "No files" in r

@pytest.mark.asyncio
@pytest.mark.parametrize("idx", list(range(30)), ids=[f"del_mod_{i}" for i in range(30)])
async def test_delete_src_files(delete_tool, repo, idx):
    r = await delete_tool.run(path=f"src/mod_{idx}.py")
    assert "Deleted" in r
    assert not (repo / f"src/mod_{idx}.py").exists()

@pytest.mark.asyncio
@pytest.mark.parametrize("idx", list(range(30)), ids=[f"del_test_{i}" for i in range(30)])
async def test_delete_test_files(delete_tool, repo, idx):
    r = await delete_tool.run(path=f"tests/test_{idx}.py")
    assert "Deleted" in r

@pytest.mark.asyncio
@pytest.mark.parametrize("path", MISSING[:20], ids=[f"del_miss_{i}" for i in range(20)])
async def test_delete_missing(delete_tool, path):
    r = await delete_tool.run(path=path)
    assert "Error" in r

LIST_DIRS = [None, ".", "src", "tests", "data"] + [f"src" for _ in range(10)] + [f"tests" for _ in range(10)]

@pytest.mark.asyncio
@pytest.mark.parametrize("path", LIST_DIRS, ids=[f"ls_{i}" for i in range(len(LIST_DIRS))])
async def test_list_dirs(list_tool, path):
    r = await list_tool.run(path=path)
    assert isinstance(r, str)
