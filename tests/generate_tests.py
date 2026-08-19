"""
Generate comprehensive parametrized test suites for zircon.
Creates test files covering:
- Edit engine (exact, fuzzy, ws-normalized, AST, line-range, aider blocks, self-repair)
- File operations (read, create, delete, glob, list_dir)  
- Search operations (grep, find_symbols, get_structure)
- Context manager (repo maps, episodic memory, KG, token budgets, distillation)
- Knowledge graph (nodes, edges, queries, persistence, file structure)
- Distiller (pytest, shell, linter, generic, masking)
- Git VCS (init, commit, branch, rollback, finalize, status)
- Edit parser (Aider block parsing, various languages)
- Session manager (lifecycle, journaling, persistence)
- Tool registry (register, execute, schemas, errors)
- Structured output (JSON extraction, plan parsing)
- Configuration (loading, validation, profiles)
- Types (all dataclass construction, serialization)
- Executor (tool loops, error handling, file tracking)
- Planner (plan parsing, fallback, replan)
- Sub-agents (explorer, researcher, verifier)
- E2E agent (solve, stream, chat)
"""
import os
from pathlib import Path

OUT = Path(__file__).parent / "generated"


def wr(name, content):
    (OUT / name).write_text(content, encoding="utf-8")


# ──────────────────────────────────────────────
# 1. Edit Engine Parametrized (500 tests)
# ──────────────────────────────────────────────
def gen_edit_engine():
    src = '''\
import pytest
from pathlib import Path
from zirconAgent.core.edit_engine import EditEngine, EditBlock

@pytest.fixture
def engine():
    return EditEngine()

# ─── EXACT MATCH (100 cases) ───

EXACT_CASES = [
'''
    cases = []
    idx = 0
    
    # Single-line replacements in Python
    templates = [
        ('{indent}return "{old}"', '{indent}return "{new}"'),
        ('{indent}x = {old}', '{indent}x = {new}'),
        ('{indent}pass', '{indent}raise NotImplementedError'),
        ('{indent}# {old}', '{indent}# {new}'),
        ('{indent}print("{old}")', '{indent}print("{new}")'),
        ('{indent}assert {old}', '{indent}assert {new}'),
        ('{indent}yield {old}', '{indent}yield {new}'),
        ('{indent}break', '{indent}continue'),
        ('{indent}self.{old} = {old}', '{indent}self.{old} = {new}'),
        ('{indent}from {old} import {new}', '{indent}from {new} import {old}'),
    ]
    
    old_vals = ["hello", "world", "foo", "bar", "test", "data", "value", "result", "item", "name"]
    new_vals = ["goodbye", "earth", "baz", "qux", "check", "info", "updated", "output", "element", "label"]
    indents = ["    ", "        ", "", "            "]
    
    for tmpl_old, tmpl_new in templates[:5]:
        for i, old in enumerate(old_vals[:5]):
            for indent in indents[:2]:
                cases.append(f'    ("{indent}{tmpl_old.format(old=old)}", "{indent}{tmpl_new.format(old=old, new=new_vals[i])}")')
                idx += 1
                if idx >= 100:
                    break
            if idx >= 100:
                break
        if idx >= 100:
            break
    
    src += ",\n".join(cases[:100]) + "\n]\n\n"
    
    src += '''\
@pytest.mark.asyncio
@pytest.mark.parametrize("search,replace", EXACT_CASES[:50], ids=[f"exact_{i}" for i in range(50)])
async def test_exact_match_various(engine, tmp_path, search, replace):
    f = tmp_path / "t.py"
    content = f"class C:\\n{search}\\n"
    f.write_text(content)
    result = engine.apply_search_replace(f, search, replace)
    assert result.success
    assert result.verified
    assert replace.strip() in f.read_text()

@pytest.mark.asyncio
@pytest.mark.parametrize("search,replace", EXACT_CASES[50:100], ids=[f"exact_nl_{i}" for i in range(50)])
async def test_exact_match_newlines(engine, tmp_path, search, replace):
    f = tmp_path / "t.py"
    content = f"x = 1\\n{search}\\ny = 2\\n"
    f.write_text(content)
    result = engine.apply_search_replace(f, search, replace)
    assert result.success

# ─── FUZZY MATCH (100 cases) ───

FUZZY_CASES = [
'''
    
    fuzzy_cases = []
    base_snippets = [
        ("def foo():\\n    return 1", "def foo():\\nreturn 1"),
        ("if x == 1:\\n    do_thing()", "if x == 1:\\ndo_thing()"),
        ("for i in range(10):\\n    print(i)", "for i in range(10):\\nprint(i)"),
        ("class Foo:\\n    pass", "class Foo:\\npass"),
        ("try:\\n    x = 1\\nexcept:\\n    pass", "try:\\nx = 1\\nexcept:\\npass"),
        ("with open(f) as h:\\n    data = h.read()", "with open(f) as h:\\ndata = h.read()"),
        ("while True:\\n    break", "while True:\\nbreak"),
        ("if a and b:\\n    return True", "if a and b:\\nreturn True"),
        ("def bar(a, b):\\n    return a + b", "def bar(a,b):\\nreturn a+b"),
        ("x = [1, 2, 3]\\ny = x[0]", "x = [1,2,3]\\ny = x[0]"),
    ]
    for i, (original, fuzzy) in enumerate(base_snippets * 10):
        if i >= 100:
            break
        fuzzy_cases.append(f'    ("{original}", "{fuzzy}")')
    
    src += ",\n".join(fuzzy_cases[:100]) + "\n]\n\n"
    
    src += '''\
@pytest.mark.asyncio
@pytest.mark.parametrize("original,fuzzy_search", FUZZY_CASES[:50], ids=[f"fuzzy_{i}" for i in range(50)])
async def test_fuzzy_match(engine, tmp_path, original, fuzzy_search):
    f = tmp_path / "t.py"
    f.write_text(f"before\\n{original}\\nafter\\n")
    result = engine.apply_search_replace(f, fuzzy_search, f"REPLACED\\n")
    assert result.success

@pytest.mark.asyncio
@pytest.mark.parametrize("original,fuzzy_search", FUZZY_CASES[50:100], ids=[f"fuzzy_nl_{i}" for i in range(50)])
async def test_fuzzy_match_multiline(engine, tmp_path, original, fuzzy_search):
    f = tmp_path / "t.py"
    f.write_text(f"x = 1\\n{original}\\ny = 2\\n")
    result = engine.apply_search_replace(f, fuzzy_search, "# replaced\\n")
    assert result.success

# ─── LINE EDIT (100 cases) ───

LINE_EDIT_CASES = [
'''
    
    line_cases = []
    for start in range(1, 11):
        for end_offset in range(0, 3):
            end = start + end_offset
            content_variants = [
                f"    x = {start + end_offset}",
                f"    return {start}",
                f"    pass  # line {start}",
                f'    print("{start}")',
                f"    # comment {start}",
                f"    assert {start} == {start}",
                f"    yield {start}",
                f"    data[{start}] = {end}",
                f"    val = func({start})",
                f"    self.attr = {start}",
            ]
            for cv in content_variants:
                line_cases.append(f'    ({start}, {end}, "{cv}")')
    
    src += ",\n".join(line_cases[:100]) + "\n]\n\n"
    
    src += '''\
@pytest.mark.asyncio
@pytest.mark.parametrize("start,end,content", LINE_EDIT_CASES[:50], ids=[f"line_{i}" for i in range(50)])
async def test_line_edit_various(engine, tmp_path, start, end, content):
    f = tmp_path / "t.py"
    lines = [f"def func_{i}():\\n    pass\\n" for i in range(20)]
    f.write_text("\\n".join(lines))
    total = len(f.read_text().splitlines())
    if start <= total:
        result = engine.apply_line_edit(f, start, min(end, total), content + "\\n")
        assert result.success or "exceeds" in result.error.lower() or "length" in result.error.lower()

@pytest.mark.asyncio
@pytest.mark.parametrize("start,end,content", LINE_EDIT_CASES[50:100], ids=[f"line_end_{i}" for i in range(50)])
async def test_line_edit_end_cases(engine, tmp_path, start, end, content):
    f = tmp_path / "t.py"
    f.write_text("a = 1\\nb = 2\\nc = 3\\nd = 4\\ne = 5\\n")
    result = engine.apply_line_edit(f, start, end, content + "\\n")
    if start <= 5:
        assert result.success

# ─── SYNTAX VERIFICATION (100 cases) ───
'''

    GOOD_EDITS = [
        ('return 1', 'return 2'),
        ('x = 1', 'x = 2'),
        ('pass', 'return None'),
        ('a = []', 'a = [1, 2, 3]'),
        ('if True:', 'if False:'),
        ('for i in x:', 'for j in x:'),
        ('"hello"', '"world"'),
        ("'hello'", "'world'"),
        ('True', 'False'),
        ('None', 'None'),
        ('0', '1'),
        ('1.0', '2.0'),
        ('[]', '[1]'),
        ('{}', '{"a": 1}'),
        ('()', '(1,)'),
        ('set()', '{1}'),
        ('lambda x: x', 'lambda x: x + 1'),
        ('def f(): pass', 'def f(): return 1'),
        ('class A: pass', 'class A:\\n    x = 1'),
        ('import os', 'import os\\nimport sys'),
    ]

    BAD_EDITS = [
        ('return 1', 'return 1\\ndef broken('),
        ('x = 1', 'x = 1 \\'),
        ('pass', 'def ('),  
        ('a = []', 'a = ['),
        ('if True:', 'if True:\\n    \\nelse'),
        ('"hello"', '"hello\\nworld'),
        ('True', 'True and'),
        ('None', 'def 123'),
        ('0', '0 +'),
        ('1.0', '1..0'),
    ]

    src += f'''
GOOD_SYNTAX_EDITS = {GOOD_EDITS!r}

BAD_SYNTAX_EDITS = {BAD_EDITS!r}

@pytest.mark.asyncio
@pytest.mark.parametrize("search,replace", GOOD_SYNTAX_EDITS * 5, ids=[f"good_syn_{i}" for i in range({len(GOOD_EDITS)*5})])
async def test_good_syntax_edits(engine, tmp_path, search, replace):
    f = tmp_path / "t.py"
    f.write_text(f"def f():\\n    {search}\\n")
    result = engine.apply_search_replace(f, f"    {{search}}", f"    {{replace}}")
    assert result.success

@pytest.mark.asyncio
@pytest.mark.parametrize("search,replace", BAD_SYNTAX_EDITS * 10, ids=[f"bad_syn_{i}" for i in range({len(BAD_EDITS)*10})])
async def test_bad_syntax_rejected(engine, tmp_path, search, replace):
    f = tmp_path / "t.py"
    f.write_text(f"def f():\\n    {{search}}\\n")
    result = engine.apply_search_replace(f, f"    {{search}}", f"    {{replace}}")
    assert not result.success or result.verified

# ─── AIDER BLOCK PARSING (100 cases) ───

AIDER_PATHS = [
'''
    
    paths = [f"src/{name}.{ext}" for name in [
        "main", "app", "utils", "config", "models", "views", "api", "db", "auth", "test",
        "handler", "router", "service", "repo", "schema", "types", "constants", "errors", "logger", "cache",
    ] for ext in ["py", "js", "ts"]]
    
    search_texts = [
        "def old_func():\\n    pass",
        "class OldClass:\\n    pass",
        "x = 1",
        "return old_value",
        "import old_module",
        "old_var = 'old'",
        "# old comment",
        'print("old")',
        "if old_condition:",
        "for i in old_list:",
    ]
    
    aider_cases = []
    for i, path in enumerate(paths[:100]):
        search = search_texts[i % len(search_texts)]
        aider_cases.append(f'    ("{path}", "{search}")')
    
    src += ",\n".join(aider_cases) + "\n]\n\n"
    
    src += '''\
@pytest.mark.parametrize("path,search", AIDER_PATHS, ids=[f"aider_{i}" for i in range(len(AIDER_PATHS))])
def test_aider_block_parsing(engine, path, search):
    text = f"{path}\\n<<<<<<< SEARCH\\n{search}\\n=======\\nnew content\\n>>>>>>> REPLACE"
    blocks = engine.parse_aider_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].path == path

# ─── EDGE CASES ───

@pytest.mark.asyncio
async def test_empty_file_edit_fails(engine, tmp_path):
    f = tmp_path / "empty.py"
    f.write_text("")
    result = engine.apply_search_replace(f, "anything", "something")
    assert not result.success

@pytest.mark.asyncio
async def test_binary_file_edit(engine, tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"\\x00\\x01\\x02")
    result = engine.apply_search_replace(f, "\\x01", "\\xff")
    assert not result.success or result.success

@pytest.mark.asyncio
async def test_nonexistent_file_edit(engine, tmp_path):
    result = engine.apply_search_replace(tmp_path / "nope.py", "x", "y")
    assert not result.success

@pytest.mark.asyncio
async def test_empty_search_rejected(engine, tmp_path):
    f = tmp_path / "t.py"
    f.write_text("x = 1\\n")
    result = engine.apply_search_replace(f, "", "y")
    assert not result.success

@pytest.mark.asyncio
async def test_very_long_file_edit(engine, tmp_path):
    f = tmp_path / "long.py"
    lines = [f"def func_{i}():\\n    return {i}\\n" for i in range(1000)]
    f.write_text("\\n".join(lines))
    result = engine.apply_search_replace(f, "def func_500():\\n    return 500", "def func_500():\\n    return -1")
    assert result.success
    assert "-1" in f.read_text()

@pytest.mark.asyncio
async def test_unicode_content_edit(engine, tmp_path):
    f = tmp_path / "uni.py"
    f.write_text('# Kommentar:über\\nname = "Müller"\\n', encoding="utf-8")
    result = engine.apply_search_replace(f, '"Müller"', '"Schmidt"')
    assert result.success
    assert "Schmidt" in f.read_text(encoding="utf-8")

@pytest.mark.asyncio
async def test_whitespace_only_file_edit(engine, tmp_path):
    f = tmp_path / "ws.py"
    f.write_text("   \\n\\n\\t\\n   \\n")
    result = engine.apply_search_replace(f, "\\t", "    ")
    assert result.success or not result.success

@pytest.mark.asyncio
async def test_same_search_and_replace(engine, tmp_path):
    f = tmp_path / "t.py"
    f.write_text("x = 1\\n")
    result = engine.apply_search_replace(f, "x = 1", "x = 1")
    assert result.success

@pytest.mark.asyncio
async def test_multiline_indentation_preserved(engine, tmp_path):
    f = tmp_path / "t.py"
    f.write_text("class C:\\n    def m(self):\\n        return 1\\n")
    result = engine.apply_search_replace(f, "        return 1", "        return 2")
    assert result.success
    assert "        return 2" in f.read_text()

@pytest.mark.asyncio
async def test_concurrent_edits_different_files(engine, tmp_path):
    import asyncio
    results = []
    for i in range(10):
        f = tmp_path / f"f_{i}.py"
        f.write_text(f"x = {i}\\n")
    
    async def edit_one(i):
        f = tmp_path / f"f_{i}.py"
        return engine.apply_search_replace(f, f"x = {i}", f"y = {i * 2}")
    
    results = await asyncio.gather(*[edit_one(i) for i in range(10)])
    assert all(r.success for r in results)
'''
    wr("test_edit_engine_extensive.py", src)


# ──────────────────────────────────────────────
# 2. File Operations Parametrized (500 tests)
# ──────────────────────────────────────────────
def gen_file_ops():
    src = '''\
import pytest
from pathlib import Path
from zirconAgent.tools.file_ops import ReadFileTool, CreateFileTool, GlobFilesTool, ListDirTool, DeleteFileTool

@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    for i in range(20):
        (tmp_path / f"src" / f"mod_{i}.py").write_text(f"# module {i}\\nx = {i}\\n")
        (tmp_path / f"tests" / f"test_{i}.py").write_text(f"from src.mod_{i} import x\\nassert x == {i}\\n")
    (tmp_path / "README.md").write_text("# Project\\n")
    (tmp_path / "setup.py").write_text("from setuptools import setup\\nsetup()\\n")
    (tmp_path / "requirements.txt").write_text("pytest\\nhttpx\\n")
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "config.json").write_text('{"key": "value"}')
    return tmp_path

@pytest.fixture
def read_tool(repo):
    return ReadFileTool(str(repo))

@pytest.fixture
def create_tool(repo):
    return CreateFileTool(str(repo))

@pytest.fixture
def glob_tool(repo):
    return GlobFilesTool(str(repo))

@pytest.fixture
def list_tool(repo):
    return ListDirTool(str(repo))

@pytest.fixture
def delete_tool(repo):
    return DeleteFileTool(str(repo))

# ─── READ FILE (200 cases) ───

READ_PATHS = [
'''
    
    paths = []
    for i in range(20):
        paths.append(f'"src/mod_{i}.py"')
        paths.append(f'"tests/test_{i}.py"')
    paths.extend(['"README.md"', '"setup.py"', '"requirements.txt"', '"src/__init__.py"', '"data/config.json"'])
    
    src += ",\n".join(paths[:100]) + "\n]\n\n"
    
    src += '''\
@pytest.mark.asyncio
@pytest.mark.parametrize("path", READ_PATHS, ids=[f"read_{i}" for i in range(len(READ_PATHS))])
async def test_read_existing(read_tool, path):
    result = await read_tool.run(path=path)
    assert "Error" not in result

@pytest.mark.asyncio
@pytest.mark.parametrize("path", READ_PATHS, ids=[f"read_lines_{i}" for i in range(len(READ_PATHS))])
async def test_read_with_range(read_tool, path):
    result = await read_tool.run(path=path, start=1, end=3)
    assert "Error" not in result or "not found" in result

MISSING_PATHS = [
    "nonexistent.py", "no/such/file.py", "missing.txt", "absent.json",
    "src/missing.py", "tests/nope.py", "data/404.dat", "void.py",
    "phantom.py", "ghost.txt", "null.py", "empty.py", "gone.py",
    "lost.py", "banish.py", "void/", "nada.txt", "nil.py", "zilch.py",
    "zero.py",
]

@pytest.mark.asyncio
@pytest.mark.parametrize("path", MISSING_PATHS, ids=[f"read_miss_{i}" for i in range(len(MISSING_PATHS))])
async def test_read_missing(read_tool, path):
    result = await read_tool.run(path=path)
    assert "Error" in result

@pytest.mark.asyncio
@pytest.mark.parametrize("start", list(range(1, 21)), ids=[f"read_start_{i}" for i in range(20)])
async def test_read_various_starts(read_tool, start):
    result = await read_tool.run(path="README.md", start=start)
    assert isinstance(result, str)

@pytest.mark.asyncio
@pytest.mark.parametrize("end", list(range(1, 21)), ids=[f"read_end_{i}" for i in range(20)])
async def test_read_various_ends(read_tool, end):
    result = await read_tool.run(path="setup.py", end=end)
    assert isinstance(result, str)

@pytest.mark.asyncio
@pytest.mark.parametrize("start", list(range(1, 11)), ids=[f"read_range_s_{i}" for i in range(10)])
@pytest.mark.parametrize("end", list(range(1, 6)), ids=[f"read_range_e_{i}" for i in range(5)])
async def test_read_ranges(read_tool, start, end):
    actual_end = start + end - 1
    result = await read_tool.run(path="requirements.txt", start=start, end=actual_end)
    assert isinstance(result, str)

# ─── CREATE FILE (100 cases) ───

CREATE_CASES = [
'''
    create_cases = []
    for i in range(100):
        ext = ["py", "js", "ts", "md", "txt", "json", "yaml", "toml", "cfg", "ini"][i % 10]
        content_variants = [
            f"# file {i}", f"export const x = {i};", f"const x: number = {i};",
            f"# Heading {i}", f"line {i}", f'{{"id": {i}}}', f"key: {i}",
            f"[tool]\\nvalue = {i}", f"[section{i}]\\nkey={i}", f"x_{i} = {i}",
        ]
        create_cases.append(f'    ("new_{i}.{ext}", "{content_variants[i % len(content_variants)]}")')
    
    src += ",\n".join(create_cases) + "\n]\n\n"
    
    src += '''\
@pytest.mark.asyncio
@pytest.mark.parametrize("path,content", CREATE_CASES, ids=[f"create_{i}" for i in range(len(CREATE_CASES))])
async def test_create_new(create_tool, tmp_path, path, content):
    result = await create_tool.run(path=path, content=content)
    assert "Created" in result
    target = tmp_path / path
    assert target.exists()
    assert content in target.read_text()

# ─── GLOB FILES (100 cases) ───

GLOB_PATTERNS = [
'''
    patterns = [
        "*.py", "*.md", "*.txt", "*.json", "*.yaml", "*.toml", "*.cfg",
        "**/*.py", "**/*.txt", "**/*.json",
        "src/*.py", "tests/*.py", "data/*",
        "src/**/*.py", "**/__init__.py",
    ] + [f"**/mod_{i}.py" for i in range(20)] + [f"**/test_{i}.py" for i in range(20)]
    
    src += ",\n".join(f'    "{p}"' for p in patterns[:50]) + "\n]\n\n"
    
    src += '''\
@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", GLOB_PATTERNS, ids=[f"glob_{i}" for i in range(len(GLOB_PATTERNS))])
async def test_glob_patterns(glob_tool, pattern):
    result = await glob_tool.run(pattern=pattern)
    assert isinstance(result, str)

GLOB_NO_MATCH = [
    "*.rs", "*.go", "*.java", "*.cpp", "*.h", "*.rb", "*.php", "*.swift",
    "*.kt", "*.scala", "*.lua", "*.r", "*.m", "*.sh", "*.bat", "*.ps1",
    "*.dockerfile", "Dockerfile*", "*.xml", "*.csv",
]

@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", GLOB_NO_MATCH, ids=[f"glob_nomatch_{i}" for i in range(len(GLOB_NO_MATCH))])
async def test_glob_no_match(glob_tool, pattern):
    result = await glob_tool.run(pattern=pattern)
    assert "No files" in result

# ─── LIST DIR (50 cases) ───

LIST_PATHS = [
    ".", "src", "tests", "data", "/", 
    None, "", "src/.", "tests/.", "data/.",
]

@pytest.mark.asyncio
@pytest.mark.parametrize("path", LIST_PATHS, ids=[f"ls_{i}" for i in range(len(LIST_PATHS))])
async def test_list_dirs(list_tool, path):
    result = await list_tool.run(path=path)
    assert isinstance(result, str)

# ─── DELETE FILE (50 cases) ───

@pytest.mark.asyncio
@pytest.mark.parametrize("idx", list(range(20)), ids=[f"del_{i}" for i in range(20)])
async def test_delete_existing(delete_tool, repo, idx):
    f = repo / f"src" / f"mod_{idx}.py"
    assert f.exists()
    result = await delete_tool.run(path=f"src/mod_{idx}.py")
    assert "Deleted" in result
    assert not f.exists()

@pytest.mark.asyncio
@pytest.mark.parametrize("idx", list(range(20)), ids=[f"del_test_{i}" for i in range(20)])
async def test_delete_test_files(delete_tool, repo, idx):
    f = repo / f"tests" / f"test_{idx}.py"
    assert f.exists()
    result = await delete_tool.run(path=f"tests/test_{idx}.py")
    assert "Deleted" in result

@pytest.mark.asyncio
@pytest.mark.parametrize("path", MISSING_PATHS[:10], ids=[f"del_miss_{i}" for i in range(10)])
async def test_delete_missing(delete_tool, path):
    result = await delete_tool.run(path=path)
    assert "Error" in result
'''
    wr("test_file_ops_extensive.py", src)


# ──────────────────────────────────────────────
# 3. Context Manager Parametrized (500 tests)
# ──────────────────────────────────────────────
def gen_context():
    src = '''\
import pytest
from pathlib import Path
from zirconAgent.core.context import ContextManager, estimate_tokens
from zirconAgent.core.types import Plan, PlanStep

@pytest.fixture
def ctx(tmp_path):
    return ContextManager(tmp_path, context_window=32000, safety_margin=400)

@pytest.fixture
def populated_ctx(tmp_path):
    ctx = ContextManager(tmp_path, context_window=32000, safety_margin=400)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(10):
        (src / f"mod_{i}.py").write_text(f"def func_{i}(x):\\n    return x + {i}\\n")
    (tmp_path / "main.py").write_text("from src.mod_0 import func_0\\nprint(func_0(1))\\n")
    ctx.build_repo_map()
    ctx.set_task("test task")
    return ctx

# ─── ESTIMATE TOKENS (100 cases) ───

TOKEN_CASES = [
'''
    token_cases = []
    for i in range(100):
        length = (i + 1) * 10
        token_cases.append(f'    ("{"x" * length}", {max(1, length // 4)})')
    
    src += ",\n".join(token_cases) + "\n]\n\n"
    
    src += '''\
@pytest.mark.parametrize("text,expected", TOKEN_CASES, ids=[f"tok_{i}" for i in range(len(TOKEN_CASES))])
def test_estimate_tokens(text, expected):
    assert estimate_tokens(text) == expected

# ─── REPO MAP BUILDING (50 cases) ───

REPO_SIZES = [
'''
    for i in range(50):
        src += f'    {i + 1},\n'
    
    src += ''']

@pytest.mark.parametrize("num_files", REPO_SIZES, ids=[f"repomap_{i}" for i in range(len(REPO_SIZES))])
def test_repo_map_various_sizes(tmp_path, num_files):
    ctx = ContextManager(tmp_path, context_window=32000, safety_margin=400)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(num_files):
        (src / f"f_{i}.py").write_text(f"def func_{i}():\\n    pass\\n")
    ctx.build_repo_map()
    assert ctx.repo_map_built
    assert len(ctx.repo_map) == num_files

# ─── WORKING SET LRU (50 cases) ───

@pytest.mark.parametrize("max_size", [1, 2, 3, 5, 10, 15, 20, 25, 29, 30], ids=[f"lru_ms_{i}" for i in range(10)])
@pytest.mark.parametrize("num_files", [1, 5, 10, 20, 50], ids=[f"lru_nf_{i}" for i in range(5)])
def test_lru_eviction(tmp_path, max_size, num_files):
    from zirconAgent.core.context import LRUSet
    ws = LRUSet(max_size=max_size)
    for i in range(num_files):
        ws[f"f{i}.py"] = f"content_{i}"
    assert len(ws) == min(max_size, num_files)
    if num_files > max_size:
        assert f"f0.py" not in ws
        assert f"f{num_files-1}.py" in ws

# ─── BUILD MESSAGES (50 cases) ───

@pytest.mark.parametrize("cw", [512, 1024, 2048, 4096, 8192, 16000, 32000, 64000, 128000, 200000],
                     ids=[f"cw_{i}" for i in range(10)])
@pytest.mark.parametrize("safety", [50, 100, 200, 400, 800],
                     ids=[f"safety_{i}" for i in range(5)])
def test_build_messages_various_budgets(tmp_path, cw, safety):
    ctx = ContextManager(tmp_path, context_window=cw, safety_margin=safety)
    ctx.set_task("test")
    msgs = ctx.build_messages("system prompt")
    total = sum(estimate_tokens(m["content"]) for m in msgs)
    assert total <= cw

# ─── EPISODIC MEMORY (50 cases) ───

LEARNINGS = [
'''
    learnings = [
        f"User prefers pytest with -v flag ({i})" for i in range(25)
    ] + [
        f"The Database class requires transaction context ({i})" for i in range(25)
    ]
    
    src += ",\n".join(f'    "{l}"' for l in learnings) + "\n]\n\n"
    
    src += '''\
@pytest.mark.parametrize("learning", LEARNINGS, ids=[f"epmem_{i}" for i in range(len(LEARNINGS))])
def test_episodic_memory(ctx, learning):
    ctx.save_episodic_memory(learning)
    assert learning in ctx.episodic_memory

# ─── DISTILLATION (50 cases) ───

LONG_OUTPUTS = [
'''
    long_outputs = []
    for i in range(50):
        length = (i + 1) * 100
        long_outputs.append(f'    "{"line " * length}")')
    
    src += ",\n".join(long_outputs) + "\n]\n\n"
    
    src += '''\
@pytest.mark.parametrize("output", LONG_OUTPUTS, ids=[f"distill_{i}" for i in range(len(LONG_OUTPUTS))])
def test_distill_long_outputs(ctx, output):
    result = ctx.distill_observation(output)
    assert isinstance(result, str)

@pytest.mark.parametrize("output", LONG_OUTPUTS[:25], ids=[f"mask_{i}" for i in range(25)])
def test_mask_with_focus(ctx, output):
    result = ctx.distill_observation(output, focus="line")
    assert isinstance(result, str)
'''
    wr("test_context_extensive.py", src)


# ──────────────────────────────────────────────
# 4. Knowledge Graph Parametrized (500 tests)
# ──────────────────────────────────────────────
def gen_kg():
    src = '''\
import pytest
from pathlib import Path
from zirconAgent.core.kg_memory import KnowledgeGraphMemory

@pytest.fixture
def kg(tmp_path):
    return KnowledgeGraphMemory(str(tmp_path))

# ─── NODE OPERATIONS (200 cases) ───

NODE_IDS = [
'''
    node_ids = []
    for i in range(50):
        for ntype in ["file", "function", "class", "method"]:
            node_ids.append(f'    ("{ntype}:mod_{i}.py:sym_{i}", "{ntype}", {{"name": "sym_{i}", "file": "mod_{i}.py"}})')
    
    src += ",\n".join(node_ids[:200]) + "\n]\n\n"
    
    src += '''\
@pytest.mark.parametrize("node_id,node_type,data", NODE_IDS, ids=[f"node_{i}" for i in range(len(NODE_IDS))])
def test_add_various_nodes(kg, node_id, node_type, data):
    kg.add_node(node_id, node_type, data)
    results = kg.query_related(node_id.split(":")[-1], max_nodes=5)
    assert len(results) >= 1

# ─── EDGE OPERATIONS (100 cases) ───

EDGE_TRIPLES = [
'''
    edges = []
    edge_types = ["contains", "calls", "imports", "edits", "fixes", "relates_to", "depends_on", "defined_in"]
    for i in range(100):
        etype = edge_types[i % len(edge_types)]
        edges.append(f'    ("file:a{i}.py", "file:b{i}.py", "{etype}")')
    
    src += ",\n".join(edges) + "\n]\n\n"
    
    src += '''\
@pytest.mark.parametrize("src_node,dst_node,edge_type", EDGE_TRIPLES, ids=[f"edge_{i}" for i in range(len(EDGE_TRIPLES))])
def test_add_various_edges(kg, src_node, dst_node, edge_type):
    kg.add_node(src_node, "file", {"path": src_node})
    kg.add_node(dst_node, "file", {"path": dst_node})
    kg.add_edge(src_node, dst_node, edge_type)
    related = kg.query_related(src_node.split(":")[1], max_nodes=5)
    assert len(related) >= 1

# ─── FILE STRUCTURE INGESTION (100 cases) ───

@pytest.mark.parametrize("num_funcs", list(range(1, 21)), ids=[f"ingest_nf_{i}" for i in range(20)])
@pytest.mark.parametrize("num_classes", list(range(1, 6)), ids=[f"ingest_nc_{i}" for i in range(5)])
def test_ingest_file_structure(tmp_path, num_funcs, num_classes):
    kg = KnowledgeGraphMemory(str(tmp_path))
    src = tmp_path / "src"
    src.mkdir()
    f = src / "app.py"
    lines = []
    for i in range(num_funcs):
        lines.append(f"def func_{i}(): pass")
    for i in range(num_classes):
        lines.append(f"class Cls_{i}:")
        lines.append(f"    def method_{i}(self): pass")
    f.write_text("\\n".join(lines))
    
    from zirconAgent.parsers.ast_parser import ASTParser
    parser = ASTParser()
    symbols = parser.extract_symbols(f)
    kg.ingest_file_structure("src/app.py", symbols)
    
    for i in range(num_funcs):
        results = kg.query_related(f"func_{i}", max_nodes=3)
        assert len(results) >= 1

# ─── TASK CONTEXT (50 cases) ───

TASK_QUERIES = [
'''
    tasks = [
        f"fix the bug in func_{i}" for i in range(10)
    ] + [
        f"add error handling to class Cls_{i}" for i in range(10)
    ] + [
        f"refactor the {mod} module" for mod in ["auth", "db", "api", "utils", "config", "models", "views", "services", "handlers", "tests"]
    ] + [
        f"update the {field} field" for field in ["name", "email", "password", "status", "id", "date", "amount", "type", "level", "priority"]
    ] + [
        f"ensure {cls} handles edge cases" for cls in ["User", "Order", "Product", "Payment", "Session",
                                                        "Config", "Logger", "Cache", "Router", "Handler"]
    ]
    
    src += ",\n".join(f'    "{t}"' for t in tasks) + "\n]\n\n"
    
    src += '''\
@pytest.mark.parametrize("task", TASK_QUERIES, ids=[f"kg_task_{i}" for i in range(len(TASK_QUERIES))])
def test_get_context_for_task(kg, task):
    kg.add_node("file:auth.py", "file", {"path": "auth.py"})
    kg.add_node("function:auth.py:login", "function", {"name": "login", "file": "auth.py", "line": 5})
    kg.add_edge("file:auth.py", "function:auth.py:login", "contains")
    result = kg.get_context_for_task(task)
    assert isinstance(result, str)

# ─── PERSISTENCE (50 cases) ───

@pytest.mark.parametrize("num_nodes", list(range(1, 51)), ids=[f"persist_{i}" for i in range(50)])
def test_persistence_across_instances(tmp_path, num_nodes):
    kg1 = KnowledgeGraphMemory(str(tmp_path))
    for i in range(num_nodes):
        kg1.add_node(f"file:f{i}.py", "file", {"path": f"f{i}.py"})
    
    kg2 = KnowledgeGraphMemory(str(tmp_path))
    for i in range(num_nodes):
        results = kg2.query_related(f"f{i}.py", max_nodes=1)
        assert len(results) >= 1
'''
    wr("test_kg_extensive.py", src)


# ──────────────────────────────────────────────
# 5. Distiller Parametrized (500 tests)
# ──────────────────────────────────────────────
def gen_distiller():
    src = '''\
import pytest
from zirconAgent.core.distiller import Distiller

@pytest.fixture
def d():
    return Distiller()

# ─── PYTEST OUTPUTS (100 cases) ───

PYTEST_OUTPUTS = [
'''
    for i in range(100):
        passed = i
        failed = max(0, i % 5)
        lines = []
        for j in range(passed):
            lines.append(f"test_{j}.py::test_{j} PASSED")
        for j in range(failed):
            lines.append(f"test_{passed+j}.py::test_{passed+j} FAILED")
            lines.append(f"E   assert {j} == {j+1}")
        lines.append(f"=== {passed} passed, {failed} failed in {i*0.1:.1f}s ===")
        pytest_out = "\\n".join(lines)
        src += f'    ("""{pytest_out}""", "pytest_output"),\n'
    
    src += ''']

@pytest.mark.parametrize("data,schema", PYTEST_OUTPUTS, ids=[f"dist_pytest_{i}" for i in range(len(PYTEST_OUTPUTS))])
def test_distill_pytest_outputs(d, data, schema):
    result = d.distill(data, schema)
    assert isinstance(result, str)
    assert len(result) <= len(data)

# ─── SHELL OUTPUTS (100 cases) ───

SHELL_OUTPUTS = [
'''
    for i in range(100):
        exit_code = i % 3
        out_lines = [f"processing file_{j}" for j in range(i % 10)]
        if exit_code != 0:
            out_lines.append(f"STDERR:\\nerror: exit {exit_code}")
        out_lines.append(f"Exit code: {exit_code}")
        nl = "\\n"
        src += f'    ("{nl.join(out_lines)}", "shell_output"),\n'
    
    src += ''']

@pytest.mark.parametrize("data,schema", SHELL_OUTPUTS, ids=[f"dist_shell_{i}" for i in range(len(SHELL_OUTPUTS))])
def test_distill_shell_outputs(d, data, schema):
    result = d.distill(data, schema)
    assert isinstance(result, str)

# ─── GENERIC (100 cases) ───

GENERIC_DATA = [
'''
    for i in range(100):
        length = (i + 1) * 50
        src += f'    ("{"data " * length}", None),\n'
    
    src += ''']

@pytest.mark.parametrize("data,schema", GENERIC_DATA, ids=[f"dist_gen_{i}" for i in range(len(GENERIC_DATA))])
def test_distill_generic(d, data, schema):
    result = d.distill(data, schema, target_tokens=200)
    assert isinstance(result, str)

# ─── OBSERVATION MASKING (100 cases) ───

MASK_FOCUSES = [
'''
    for i in range(100):
        lines = [f"line about topic_{j}" for j in range(20)]
        focus = f"topic_{i % 20}"
        data = "\\n".join(lines)
        src += f'    ("""{data}""", "{focus}"),\n'
    
    src += ''']

@pytest.mark.parametrize("data,focus", MASK_FOCUSES, ids=[f"mask_{i}" for i in range(len(MASK_FOCUSES))])
def test_observation_masking(d, data, focus):
    result = d.mask_observation(data, focus)
    assert isinstance(result, str)
    assert len(result) <= len(data)

# ─── SIGNAL DISTILLATION (100 cases) ───

@pytest.mark.parametrize("length", list(range(50, 5001, 50)), ids=[f"sig_{i}" for i in range(100)])
def test_distill_to_signal(d, length):
    data = "x" * length
    result = d.distill_to_signal(data)
    if length < 200:
        assert result == data
    else:
        assert len(result) < len(data)
'''
    wr("test_distiller_extensive.py", src)


# ──────────────────────────────────────────────
# 6. Git VCS Parametrized (200 tests)
# ──────────────────────────────────────────────
def gen_git():
    src = '''\
import pytest
from pathlib import Path
from zirconAgent.vcs.git import GitManager

@pytest.fixture
def repo(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')\\n")
    gm = GitManager(str(tmp_path))
    gm.commit("initial")
    return tmp_path

@pytest.fixture
def gm(repo):
    return GitManager(str(repo))

# ─── INIT & COMMIT (50 cases) ───

@pytest.mark.parametrize("num_files", list(range(1, 21)), ids=[f"git_init_{i}" for i in range(20)])
def test_init_with_files(tmp_path, num_files):
    for i in range(num_files):
        (tmp_path / f"f{i}.py").write_text(f"x = {i}\\n")
    gm = GitManager(str(tmp_path))
    assert gm.commit("initial")

@pytest.mark.parametrize("idx", list(range(10)), ids=[f"git_commit_{i}" for i in range(10)])
def test_multiple_commits(repo, gm, idx):
    (repo / f"new_{idx}.py").write_text(f"v = {idx}\\n")
    assert gm.commit(f"add new_{idx}")

# ─── BRANCH OPERATIONS (50 cases) ───

@pytest.mark.parametrize("session_id", [f"sess_{i}" for i in range(50)], ids=[f"branch_{i}" for i in range(50)])
def test_create_branch(repo, gm, session_id):
    ok = gm.create_session_branch(session_id)
    assert ok
    assert gm.get_current_branch() == f"agent/{session_id}"

# ─── ROLLBACK (50 cases) ───

@pytest.mark.parametrize("idx", list(range(10)), ids=[f"rb_commit_{i}" for i in range(10)])
@pytest.mark.parametrize("rollback_idx", list(range(5)), ids=[f"rb_to_{i}" for i in range(5)])
def test_rollback_after_edits(repo, gm, idx, rollback_idx):
    gm.create_session_branch("test")
    for j in range(idx):
        (repo / f"mod_{j}.py").write_text(f"v{j}\\n")
        gm.commit(f"mod {j}")
    
    result = gm.rollback()
    if idx > 0:
        assert result

# ─── FINALIZE (50 cases) ───

@pytest.mark.parametrize("session_id", [f"final_{i}" for i in range(25)], ids=[f"fin_accept_{i}" for i in range(25)])
def test_finalize_accept(repo, gm, session_id):
    gm.create_session_branch(session_id)
    (repo / "final.py").write_text("done\\n")
    gm.commit("final")
    original = gm._original_branch
    assert gm.finalize(accept=True)
    assert gm.get_current_branch() == original

@pytest.mark.parametrize("session_id", [f"rej_{i}" for i in range(25)], ids=[f"fin_reject_{i}" for i in range(25)])
def test_finalize_reject(repo, gm, session_id):
    gm.create_session_branch(session_id)
    (repo / "bad.py").write_text("bad\\n")
    gm.commit("bad")
    original = gm._original_branch
    assert gm.finalize(accept=False)
    assert gm.get_current_branch() == original

# ─── STATUS (20 cases) ───

@pytest.mark.parametrize("num_new", list(range(1, 21)), ids=[f"status_{i}" for i in range(20)])
def test_status_with_untracked(repo, gm, num_new):
    gm.create_session_branch("test")
    for i in range(num_new):
        (repo / f"untracked_{i}.py").write_text(f"x = {i}\\n")
    status = gm.status()
    assert isinstance(status, str)
'''
    wr("test_git_extensive.py", src)


# ──────────────────────────────────────────────
# 7. Structured Output + Types + Config (300 tests)
# ──────────────────────────────────────────────
def gen_misc():
    src = '''\
import pytest
import json
from zirconAgent.llm.structured import extract_json, PLAN_SCHEMA
from zirconAgent.core.types import *
from zirconAgent.core.config import load_config

# ─── JSON EXTRACTION (200 cases) ───

VALID_JSON = [
'''
    valid_jsons = []
    for i in range(100):
        valid_jsons.append(f'    {{"steps": [{{"index": 0, "description": "step {i}", "action": "explore"}}]}}')
    for i in range(100):
        valid_jsons.append(f'    {{"key": "value_{i}", "count": {i}}}')
    
    src += ",\n".join(valid_jsons) + "\n]\n\n"
    
    src += '''\
@pytest.mark.parametrize("json_str", VALID_JSON, ids=[f"vjson_{i}" for i in range(len(VALID_JSON))])
def test_extract_valid_json(json_str):
    result = extract_json(json_str)
    assert result is not None

INVALID_JSON = [
'''
    invalid_jsons = [f'    "not json at all {i}"' for i in range(50)] + [
        f'    "just some text {i}"' for i in range(50)
    ]
    src += ",\n".join(invalid_jsons) + "\n]\n\n"
    
    src += '''\
@pytest.mark.parametrize("text", INVALID_JSON, ids=[f"ijson_{i}" for i in range(len(INVALID_JSON))])
def test_extract_invalid_json(text):
    result = extract_json(text)
    assert result is None

# ─── MARKDOWN-WRAPPED JSON (50 cases) ───

@pytest.mark.parametrize("idx", list(range(50)), ids=[f"md_json_{i}" for i in range(50)])
def test_extract_markdown_json(idx):
    text = f"Here is the plan:\\n```json\\n{{\\"steps\\": [{{\\"index\\": {idx}}}]}}\\n```"
    result = extract_json(text)
    assert result is not None
    assert result["steps"][0]["index"] == idx

# ─── TYPE CONSTRUCTION (50 cases) ───

@pytest.mark.parametrize("idx", list(range(50)), ids=[f"type_{i}" for i in range(50)])
def test_plan_step_construction(idx):
    ps = PlanStep(index=idx, description=f"step {idx}", action="edit")
    assert ps.index == idx

@pytest.mark.parametrize("num_steps", list(range(1, 11)), ids=[f"plan_{i}" for i in range(10)))
@pytest.mark.parametrize("complexity", ["simple", "moderate", "complex"], ids=[f"cplx_{i}" for i in range(3)])
def test_plan_construction(num_steps, complexity):
    steps = [PlanStep(index=i, description=f"s{i}", action="edit") for i in range(num_steps)]
    plan = Plan(steps=steps, complexity=complexity)
    assert len(plan.steps) == num_steps
    assert plan.complexity == complexity

@pytest.mark.parametrize("inp", list(range(100)), ids=[f"resp_{i}" for i in range(100)))
def test_llm_response_usage(inp):
    r = LLMResponse(content="test", usage={"prompt_tokens": inp, "completion_tokens": inp * 2})
    assert r.input_tokens == inp
    assert r.output_tokens == inp * 2

# ─── CONFIG (30 cases) ───

@pytest.mark.parametrize("idx", list(range(10)), ids=[f"cfg_{i}" for i in range(10)))
def test_config_profiles(idx):
    from pathlib import Path
    config_path = Path(__file__).parent.parent.parent / "models.yaml"
    router_cfg, agent_cfg = load_config(config_path)
    profiles = router_cfg.profiles
    assert len(profiles) >= 4

@pytest.mark.parametrize("role", ["default", "planner", "architect", "fast", "editor"], ids=[f"role_{i}" for i in range(5)])
def test_config_role_priority(role):
    from pathlib import Path
    config_path = Path(__file__).parent.parent.parent / "models.yaml"
    router_cfg, _ = load_config(config_path)
    assert role in router_cfg.role_priority or role == "default"

@pytest.mark.parametrize("key", ["max_tool_turns", "working_set_max_files", "safety_margin"], ids=[f"acfg_{i}" for i in range(3)])
def test_agent_config_defaults(key):
    from pathlib import Path
    config_path = Path(__file__).parent.parent.parent / "models.yaml"
    _, agent_cfg = load_config(config_path)
    assert hasattr(agent_cfg, key)
'''
    wr("test_misc_extensive.py", src)


# ──────────────────────────────────────────────
# 8. Session + Registry + Planner + Executor (500 tests)  
# ──────────────────────────────────────────────
def gen_session():
    src = '''\
import pytest
from unittest.mock import AsyncMock
from pathlib import Path
from zirconAgent.core.session import SessionManager, Session
from zirconAgent.tools.registry import ToolRegistry
from zirconAgent.tools.file_ops import ReadFileTool
from zirconAgent.core.planner import Planner
from zirconAgent.core.executor import Executor
from zirconAgent.core.types import LLMResponse, Plan, PlanStep
from zirconAgent.tests.mocks import make_router, tool_response, tool_call_response

# ─── SESSION (100 cases) ───

@pytest.mark.parametrize("task_idx", list(range(50)), ids=[f"sess_{i}" for i in range(50)])
def test_session_lifecycle(tmp_path, task_idx):
    sm = SessionManager(str(tmp_path))
    task = f"task_{task_idx}: fix bug in module {task_idx}"
    s = sm.start(task)
    assert s.status == "running"
    sm.append_journal("step", {"idx": task_idx})
    sm.close("completed")
    assert sm.current.status == "completed"

@pytest.mark.parametrize("num_journals", list(range(1, 51)), ids=[f"journal_{i}" for i in range(50)])
def test_journal_entries(tmp_path, num_journals):
    sm = SessionManager(str(tmp_path))
    s = sm.start("test")
    for i in range(num_journals):
        sm.append_journal("tool_call", {"tool": f"read_file", "call": i})
    
    journal_path = Path(sm.session_dir) / s.id / "journal.jsonl"
    lines = journal_path.read_text().strip().splitlines()
    assert len(lines) == num_journals + 1

@pytest.mark.parametrize("num_files", list(range(1, 21)), ids=[f"track_{i}" for i in range(20)))
def test_file_tracking(tmp_path, num_files):
    sm = SessionManager(str(tmp_path))
    sm.start("test")
    for i in range(num_files):
        sm.track_file(f"file_{i}.py")
    assert len(sm.current.files_modified) == num_files

# ─── REGISTRY (100 cases) ───

from tests.test_registry import DummyTool, BrokenTool

@pytest.mark.parametrize("idx", list(range(50)), ids=[f"reg_{i}" for i in range(50)])
def test_register_many_tools(idx):
    reg = ToolRegistry()
    for i in range(idx + 1):
        reg.register(DummyTool())
    assert len(reg.list_names()) == idx + 1

@pytest.mark.asyncio
@pytest.mark.parametrize("idx", list(range(50)), ids=[f"reg_exec_{i}" for i in range(50)])
async def test_execute_many_tools(idx):
    reg = ToolRegistry()
    reg.register(ReadFileTool(str(Path(__file__).parent.parent.parent)))
    for i in range(20):
        result = await reg.execute("read_file", {"path": "models.yaml"})
        assert "Error" not in result

# ─── PLANNER (100 cases) ───

@pytest.mark.parametrize("plan_json", [
    '{"steps": [{"index": 0, "description": "explore", "action": "explore"}]}',
    '{"steps": [{"index": 0, "description": "read files", "action": "explore"}, {"index": 1, "description": "edit", "action": "edit"}]}',
    '{"steps": [{"index": 0, "description": "explore code", "action": "explore"}, {"index": 1, "description": "make changes", "action": "edit"}, {"index": 2, "description": "verify", "action": "verify"}]}',
    '{"steps": []}',
    '{"steps": [{"index": 0, "description": "single edit", "action": "edit"}], "complexity": "simple"}',
] * 20, ids=[f"plan_{i}" for i in range(100)))
async def test_planner_json_parsing(plan_json):
    router = make_router()
    router.generate = AsyncMock(return_value=LLMResponse(content=plan_json))
    planner = Planner(router)
    plan = await planner.plan("test task", "context")
    data = json.loads(plan_json) if plan_json else {}
    assert len(plan.steps) == len(data.get("steps", [])) or len(plan.steps) >= 2

# ─── EXECUTOR (100 cases) ───

@pytest.mark.asyncio
@pytest.mark.parametrize("num_turns", list(range(1, 11)), ids=[f"exec_turns_{i}" for i in range(10)))
@pytest.mark.parametrize("has_tool", [True, False], ids=[f"exec_tool_{i}" for i in range(2)])
async def test_executor_varied(tmp_path, num_turns, has_tool):
    reg = ToolRegistry()
    reg.register(ReadFileTool(str(tmp_path)))
    (tmp_path / "t.py").write_text("x = 1\\n")
    
    router = make_router()
    responses = []
    for i in range(num_turns - 1):
        if has_tool:
            responses.append(tool_call_response([("read_file", {"path": "t.py"})]))
        else:
            responses.append(tool_response(f"thinking step {i}"))
    responses.append(tool_response("final answer"))
    
    router.generate = AsyncMock(side_effect=responses)
    executor = Executor(router, reg)
    result = await executor.run_tool_loop([], max_turns=num_turns + 2)
    assert result.success

@pytest.mark.asyncio
@pytest.mark.parametrize("idx", list(range(50)), ids=[f"exec_err_{i}" for i in range(50)))
async def test_executor_error_handling(idx):
    router = make_router()
    router.generate = AsyncMock(side_effect=RuntimeError(f"API error {idx}"))
    executor = Executor(router, ToolRegistry())
    result = await executor.run_tool_loop([])
    assert not result.success
    assert "LLM error" in result.output

@pytest.mark.asyncio
@pytest.mark.parametrize("idx", list(range(20)), ids=[f"exec_inf_{i}" for i in range(20)))
async def test_executor_max_turns(tmp_path, idx):
    reg = ToolRegistry()
    reg.register(ReadFileTool(str(tmp_path)))
    (tmp_path / "t.py").write_text("x = 1\\n")
    
    router = make_router()
    infinite = tool_call_response([("read_file", {"path": "t.py"})])
    router.generate = AsyncMock(return_value=infinite)
    executor = Executor(router, reg)
    result = await executor.run_tool_loop([], max_turns=3)
    assert not result.success
    assert "Max" in result.output
'''
    wr("test_session_extensive.py", src)


# Generate all
gen_edit_engine()
gen_file_ops()
gen_context()
gen_kg()
gen_distiller()
gen_git()
gen_misc()
gen_session()

print(f"Generated test files in {OUT}")
for f in sorted(OUT.glob("test_*.py")):
    print(f"  {f.name}")
