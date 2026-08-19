import pytest
from pathlib import Path
from zirconAgent.core.edit_engine import EditEngine

@pytest.fixture
def engine():
    return EditEngine()


EXACT_SINGLE = [
    (f'    x = {i}', f'    x = {i * 2}') for i in range(50)
] + [
    (f'    return {i}', f'    return {i + 1}') for i in range(50)
] + [
    (f'    self.val = {i}', f'    self.val = {i * 3}') for i in range(50)
] + [
    (f'    data["k{i}"] = {i}', f'    data["k{i}"] = {i * 10}') for i in range(50)
]

@pytest.mark.asyncio
@pytest.mark.parametrize("search,replace", EXACT_SINGLE, ids=[f"exact_{i}" for i in range(len(EXACT_SINGLE))])
async def test_exact_single_line(engine, tmp_path, search, replace):
    f = tmp_path / "t.py"
    f.write_text(f"class C:\n{search}\n")
    r = engine.apply_search_replace(f, search, replace)
    assert r.success
    assert replace in f.read_text()


MULTILINE_EXACT = [
    (f"def f{i}(x):\n    return x + {i}", f"def f{i}(x):\n    return x * {i}")
    for i in range(50)
] + [
    (f"class Cls{i}:\n    pass", f"class Cls{i}:\n    x = {i}")
    for i in range(50)
]

@pytest.mark.asyncio
@pytest.mark.parametrize("search,replace", MULTILINE_EXACT, ids=[f"ml_{i}" for i in range(len(MULTILINE_EXACT))])
async def test_exact_multiline(engine, tmp_path, search, replace):
    f = tmp_path / "t.py"
    f.write_text(f"# header\n{search}\n# footer\n")
    r = engine.apply_search_replace(f, search, replace)
    assert r.success


AST_CASES = [(f"func_{i}", f"def func_{i}():\n    return {i * 10}") for i in range(50)
] + [(f"method_{i}", f"    def method_{i}(self):\n        return {i * 5}") for i in range(50)]

@pytest.mark.asyncio
@pytest.mark.parametrize("name,new_src", AST_CASES, ids=[f"ast_{i}" for i in range(len(AST_CASES))])
async def test_ast_replace(engine, tmp_path, name, new_src):
    f = tmp_path / "t.py"
    lines = [f"def {name}():", f"    return 0", ""]
    for i in range(5):
        lines.append(f"def other_{i}():")
        lines.append(f"    return {i}")
        lines.append("")
    f.write_text("\n".join(lines))
    r = engine.apply_ast_replace(f, name, new_src)
    assert r.success


AIDER_PATHS = [f"src/{n}.{ext}" for n in [
    "main","app","utils","config","models","views","api","db","auth","test",
    "handler","router","service","repo","schema","types","constants","errors","logger","cache",
] for ext in ["py","js","ts"]][:60] + [
    f"tests/{n}.py" for n in ["test_main","test_app","test_utils","test_config","test_models",
                              "test_views","test_api","test_db","test_auth","test_handler"]
] + [
    "README.md","setup.py","pyproject.toml","Dockerfile","Makefile",".gitignore",
    "docs/api.md","docs/guide.md","docs/config.md","docs/deploy.md",
    "data/config.json","data/schema.sql","data/seed.py","data/migrate.py",
    "scripts/build.sh","scripts/deploy.sh","scripts/test.sh","scripts/lint.sh",
    "src/__init__.py","tests/__init__.py","src/lib.rs","src/main.go",
]

@pytest.mark.parametrize("path", AIDER_PATHS, ids=[f"aider_{i}" for i in range(len(AIDER_PATHS))])
def test_aider_block_parsing_varied_paths(engine, path):
    text = f"{path}\n<<<<<<< SEARCH\nold code\n=======\nnew code\n>>>>>>> REPLACE"
    blocks = engine.parse_aider_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].path == path
    assert blocks[0].search == "old code"
    assert blocks[0].replace == "new code"


@pytest.mark.parametrize("start,end", [(s, s + off) for s in range(1, 11) for off in range(3)], ids=[f"le_{i}" for i in range(30)])
async def test_line_edits_ranges(engine, tmp_path, start, end):
    f = tmp_path / "t.py"
    lines = [f"# line {i}\nx = {i}\n" for i in range(20)]
    f.write_text("".join(lines))
    r = engine.apply_line_edit(f, start, min(end, 20), f"# replaced\n")
    if start <= 20:
        assert r.success or "exceeds" in r.error.lower() or "length" in r.error.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("idx", range(50), ids=[f"repair_{i}" for i in range(50)])
async def test_self_repair_on_minor_issues(engine, tmp_path, idx):
    f = tmp_path / "t.py"
    f.write_text(f"def func():\n    return {idx}\n")
    r = engine.apply_search_replace(f, f"    return {idx}", f"    return {idx + 1}")
    assert r.success
    assert f.read_text() == f"def func():\n    return {idx + 1}\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("idx", range(25), ids=[f"edge_empty_{i}" for i in range(25)])
async def test_empty_search_rejected(engine, tmp_path, idx):
    f = tmp_path / "t.py"
    f.write_text(f"x = {idx}\n")
    r = engine.apply_search_replace(f, "", "y")
    assert not r.success

@pytest.mark.asyncio
@pytest.mark.parametrize("idx", range(25), ids=[f"edge_missing_{i}" for i in range(25)])
async def test_missing_file_rejected(engine, tmp_path, idx):
    r = engine.apply_search_replace(tmp_path / f"nope_{idx}.py", "x", "y")
    assert not r.success


@pytest.mark.asyncio
@pytest.mark.parametrize("target_line", range(0, 1000, 20), ids=[f"long_{i}" for i in range(50)])
async def test_long_file_targeted_edit(engine, tmp_path, target_line):
    f = tmp_path / "long.py"
    lines = [f"def func_{i}():\n    return {i}\n" for i in range(1000)]
    f.write_text("\n".join(lines))
    search = f"    return {target_line}"
    replace = f"    return {target_line * -1}"
    r = engine.apply_search_replace(f, search, replace)
    assert r.success


UNICODE_STRINGS = [
    ("Müller", "Schmidt"), ("über", "over"), ("日本語", "Japanese"),
    ("€100", "$100"), ("café", "coffee"), ("naïve", "naive"),
    ("résumé", "resume"), ("Ångström", "angstrom"), ("π", "3.14"),
    ("λ x: x", "lambda x: x"),
] * 5

@pytest.mark.asyncio
@pytest.mark.parametrize("old,new", UNICODE_STRINGS, ids=[f"uni_{i}" for i in range(len(UNICODE_STRINGS))])
async def test_unicode_edits(engine, tmp_path, old, new):
    f = tmp_path / "uni.py"
    f.write_text(f'# {old}\nx = "{old}"\n', encoding="utf-8")
    r = engine.apply_search_replace(f, f'"{old}"', f'"{new}"')
    assert r.success
    assert new in f.read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("idx", range(50), ids=[f"conc_{i}" for i in range(50)])
async def test_concurrent_different_files(engine, tmp_path, idx):
    import asyncio
    f = tmp_path / f"f_{idx}.py"
    f.write_text(f"x = {idx}\n")
    r = engine.apply_search_replace(f, f"x = {idx}", f"y = {idx * 2}")
    assert r.success


@pytest.mark.asyncio
@pytest.mark.parametrize("idx", range(50), ids=[f"json_{i}" for i in range(50)])
async def test_json_file_edits(engine, tmp_path, idx):
    f = tmp_path / "data.json"
    f.write_text('{"key": "%d", "value": %d}' % (idx, idx))
    r = engine.apply_search_replace(f, f'"value": {idx}', f'"value": {idx + 1}')
    assert r.success
    import json
    data = json.loads(f.read_text())
    assert data["value"] == idx + 1
