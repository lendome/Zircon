import pytest

from zirconAgent.parsers.symbol_nav import (
    extract_body,
    extract_dependencies,
    find_definitions,
    find_references,
    get_ast_range,
    get_callers,
)
from zirconAgent.tools.nav_ops import (
    FindReferencesTool,
    GetAstRangeTool,
    GetCallersTool,
    GetFunctionBodyTool,
    GetFunctionDependenciesTool,
    GetSymbolDefinitionTool,
)


@pytest.fixture
def nav_repo(tmp_path):
    (tmp_path / "calc.py").write_text(
        'def add(a, b):\n'
        '    """Add two numbers."""\n'
        '    return a + b\n'
        '\n'
        '\n'
        'def outer():\n'
        '    def inner():\n'
        '        return add(1, 2)\n'
        '    return inner()\n'
        '\n'
        '\n'
        'class Calculator:\n'
        '    def multiply(self, a, b):\n'
        '        result = a * b\n'
        '        return result\n'
        '\n'
        '    def divide(self, a, b):\n'
        '        return a / b\n'
    )
    (tmp_path / "main.py").write_text(
        'from calc import add, Calculator\n'
        '\n'
        'def main():\n'
        '    c = Calculator()\n'
        '    print(add(2, 3))\n'
        '    print(c.multiply(4, 5))\n'
    )
    (tmp_path / "util.go").write_text(
        'package main\n'
        '\n'
        'func helper() int {\n'
        '	x := map[string]int{"a": 1}\n'
        '	if x["a"] > 0 {\n'
        '		return x["a"]\n'
        '	}\n'
        '	return 0\n'
        '}\n'
        '\n'
        'func main() {\n'
        '	println(helper())\n'
        '}\n'
    )
    (tmp_path / "app.js").write_text(
        'function renderPage(title) {\n'
        '    const header = `<h1>${title}</h1>`;\n'
        '    // closing brace in comment: }\n'
        '    const s = "string with } inside";\n'
        '    return header + s;\n'
        '}\n'
        '\n'
        'const init = () => {\n'
        '    console.log(renderPage("hi"));\n'
        '};\n'
    )
    return tmp_path


class TestFindDefinitions:
    def test_python_function(self, nav_repo):
        defs = find_definitions(nav_repo, "add")
        assert len(defs) == 1
        assert defs[0].path == "calc.py"
        assert defs[0].line == 1
        assert defs[0].end_line == 3

    def test_python_method_qualified_and_short(self, nav_repo):
        defs = find_definitions(nav_repo, "multiply")
        assert any(d.name == "Calculator.multiply" for d in defs)

    def test_go_brace_body_end(self, nav_repo):
        defs = find_definitions(nav_repo, "helper")
        assert len(defs) == 1
        assert defs[0].path == "util.go"
        # body spans lines 3..9 in the fixture
        assert defs[0].line == 3
        assert defs[0].end_line == 9

    def test_js_ignores_braces_in_strings_and_comments(self, nav_repo):
        defs = find_definitions(nav_repo, "renderPage")
        assert len(defs) == 1
        assert defs[0].line == 1
        assert defs[0].end_line == 6

    def test_js_arrow_function(self, nav_repo):
        defs = find_definitions(nav_repo, "init")
        assert len(defs) == 1
        assert defs[0].end_line == 10

    def test_unknown_symbol(self, nav_repo):
        assert find_definitions(nav_repo, "does_not_exist") == []

    def test_scope_filter(self, nav_repo):
        defs = find_definitions(nav_repo, "add", scope="calc.py")
        assert len(defs) == 1
        defs2 = find_definitions(nav_repo, "add", scope="main.py")
        assert defs2 == []


class TestExtractBody:
    def test_python_body_numbered(self, nav_repo):
        result = extract_body(nav_repo, "multiply")
        assert result["ok"]
        assert "result = a * b" in result["body"]
        assert result["line"] == 13
        assert result["end_line"] == 15

    def test_go_body(self, nav_repo):
        result = extract_body(nav_repo, "helper")
        assert result["ok"]
        assert 'x := map[string]int{"a": 1}' in result["body"]
        assert result["end_line"] == 9

    def test_max_lines_truncates(self, nav_repo):
        result = extract_body(nav_repo, "helper", max_lines=10)
        assert result["ok"]
        # helper is 7 lines; max_lines clamps at >=10 minimum
        assert "body" in result

    def test_unknown(self, nav_repo):
        result = extract_body(nav_repo, "nope")
        assert not result["ok"]
        assert "No definition" in result["error"]


class TestFindReferences:
    def test_references_exclude_definition(self, nav_repo):
        result = find_references(nav_repo, "add")
        assert result["ok"]
        ref_lines = {(r.path, r.line) for r in result["references"]}
        assert ("calc.py", 1) not in ref_lines  # definition excluded
        assert any(p == "main.py" for p, _ in ref_lines)
        assert any(p == "calc.py" and l == 8 for p, l in ref_lines)  # add(1, 2) call

    def test_word_boundary(self, nav_repo):
        (nav_repo / "extra.py").write_text(
            "additive = 1\naddress = add  # not a real ref to 'add' symbol name\n"
        )
        result = find_references(nav_repo, "add")
        paths_lines = {(r.path, r.line) for r in result["references"]}
        assert ("extra.py", 1) not in paths_lines  # 'additive' must not match

    def test_definitions_reported_separately(self, nav_repo):
        result = find_references(nav_repo, "multiply")
        assert result["ok"]
        assert any("Calculator.multiply" in d.name for d in result["definitions"])


class TestNavTools:
    @pytest.mark.asyncio
    async def test_get_symbol_definition(self, nav_repo):
        tool = GetSymbolDefinitionTool(str(nav_repo))
        out = await tool.run(symbol="add")
        assert "calc.py:1" in out
        assert "function add" in out

    @pytest.mark.asyncio
    async def test_get_symbol_definition_not_found(self, nav_repo):
        tool = GetSymbolDefinitionTool(str(nav_repo))
        out = await tool.run(symbol="zzz_missing")
        assert "No definition" in out

    @pytest.mark.asyncio
    async def test_get_function_body(self, nav_repo):
        tool = GetFunctionBodyTool(str(nav_repo))
        out = await tool.run(symbol="divide")
        assert "return a / b" in out
        assert "Calculator.divide" in out

    @pytest.mark.asyncio
    async def test_find_references(self, nav_repo):
        tool = FindReferencesTool(str(nav_repo))
        out = await tool.run(symbol="renderPage")
        assert "Definition" in out
        assert "app.js" in out
        assert "console.log(renderPage" in out

    @pytest.mark.asyncio
    async def test_find_references_none(self, nav_repo):
        tool = FindReferencesTool(str(nav_repo))
        out = await tool.run(symbol="helper", path="app.js")
        assert "No references" in out


class TestExtractDependencies:
    def test_python_resolves_repo_callees(self, nav_repo):
        result = extract_dependencies(nav_repo, "main", path="main.py")
        assert result["ok"]
        by_name = {r["name"]: r for r in result["resolved"]}
        # main() calls add(...) and c.multiply(...) — both resolve to calc.py.
        assert by_name["add"]["path"] == "calc.py"
        assert by_name["add"]["line"] == 1
        assert by_name["multiply"]["path"] == "calc.py"
        assert by_name["multiply"]["qualified"] == "Calculator.multiply"

    def test_builtins_are_unresolved_or_dropped(self, nav_repo):
        result = extract_dependencies(nav_repo, "main", path="main.py")
        assert result["ok"]
        resolved_names = {r["name"] for r in result["resolved"]}
        # print is a stoplisted builtin — never treated as a repo dependency.
        assert "print" not in resolved_names

    def test_locals_and_params_not_dependencies(self, tmp_path):
        (tmp_path / "mod.py").write_text(
            "def outer(factory, value):\n"
            "    helper = lambda x: x * 2\n"
            "    return factory(helper(value))\n"
        )
        result = extract_dependencies(tmp_path, "outer")
        assert result["ok"]
        assert result["resolved"] == []
        # factory/helper are params/locals — not repo-level dependencies.
        assert "factory" not in result["unresolved"]
        assert "helper" not in result["unresolved"]

    def test_go_dependencies(self, nav_repo):
        result = extract_dependencies(nav_repo, "main", path="util.go")
        assert result["ok"]
        by_name = {r["name"]: r for r in result["resolved"]}
        assert by_name["helper"]["path"] == "util.go"
        assert by_name["helper"]["line"] == 3

    def test_unknown_symbol(self, nav_repo):
        result = extract_dependencies(nav_repo, "does_not_exist")
        assert not result["ok"]
        assert "No definition" in result["error"]

    def test_ambiguous_symbol_returns_candidates(self, nav_repo):
        # "main" exists in both main.py and util.go.
        result = extract_dependencies(nav_repo, "main")
        assert not result["ok"]
        assert "ambiguous" in result["error"]
        assert len(result["candidates"]) == 2

    def test_no_calls(self, nav_repo):
        result = extract_dependencies(nav_repo, "divide")
        assert result["ok"]
        assert result["resolved"] == []

    @pytest.mark.asyncio
    async def test_tool_output(self, nav_repo):
        tool = GetFunctionDependenciesTool(str(nav_repo))
        out = await tool.run(symbol="main", path="main.py")
        assert "main.py" in out
        assert "add → calc.py:1" in out
        assert "multiply → calc.py" in out

    @pytest.mark.asyncio
    async def test_tool_unknown(self, nav_repo):
        tool = GetFunctionDependenciesTool(str(nav_repo))
        out = await tool.run(symbol="zzz_missing")
        assert "No definition" in out


class TestGetCallers:
    def test_finds_callers_of_function(self, nav_repo):
        # add() is called by inner() (nested in outer) and by main() in main.py.
        result = get_callers(nav_repo, "add")
        assert result["ok"]
        paths_lines = {(c["path"], c["line"]) for c in result["callers"]}
        # main() in main.py calls add(2, 3) directly.
        assert ("main.py", 3) in paths_lines
        # outer() in calc.py encloses inner() which calls add(1, 2); the
        # enclosing top-level function is reported as the caller.
        assert ("calc.py", 6) in paths_lines

    def test_no_callers(self, nav_repo):
        # divide() is never called anywhere.
        result = get_callers(nav_repo, "divide")
        assert result["ok"]
        assert result["callers"] == []

    def test_callers_of_method_via_attribute(self, nav_repo):
        # multiply is called as c.multiply(...) in main.py.
        result = get_callers(nav_repo, "multiply")
        assert result["ok"]
        assert any(c["path"] == "main.py" for c in result["callers"])

    def test_unknown_symbol(self, nav_repo):
        result = get_callers(nav_repo, "does_not_exist")
        assert not result["ok"]
        assert "No definition" in result["error"]

    def test_ambiguous_returns_candidates(self, nav_repo):
        # "main" is defined in both main.py and util.go.
        result = get_callers(nav_repo, "main")
        assert not result["ok"]
        assert "ambiguous" in result["error"]
        assert len(result["candidates"]) == 2

    @pytest.mark.asyncio
    async def test_tool_output(self, nav_repo):
        tool = GetCallersTool(str(nav_repo))
        out = await tool.run(symbol="add")
        assert "Target:" in out
        assert "main.py" in out
        assert "Callers" in out

    @pytest.mark.asyncio
    async def test_tool_no_callers(self, nav_repo):
        tool = GetCallersTool(str(nav_repo))
        out = await tool.run(symbol="divide")
        assert "No callers" in out


class TestGetAstRange:
    def test_expands_to_enclosing_function(self, nav_repo):
        # Line 14 is inside Calculator.multiply's body.
        result = get_ast_range(nav_repo, "calc.py", 14)
        assert result["ok"]
        assert result["kind"] == "function"
        assert result["name"] == "multiply"
        # Expanded beyond the single requested line.
        assert result["start_line"] <= 14 <= result["end_line"]
        assert result["expanded"]

    def test_expands_to_enclosing_if_block(self, tmp_path):
        (tmp_path / "cond.py").write_text(
            "def f(x):\n"
            "    if x > 0:\n"
            "        a = 1\n"
            "        b = 2\n"
            "    return a + b\n"
        )
        # Line 4 (b = 2) is inside the if-block, which is the tightest block.
        result = get_ast_range(tmp_path, "cond.py", 4)
        assert result["ok"]
        assert result["kind"] == "if_block"
        assert result["start_line"] == 2
        assert result["end_line"] == 4
        # enclosing scope chain includes the function.
        assert "cond.py:1 function f" in result["enclosing_scope"]

    def test_class_scope(self, nav_repo):
        # A line inside a method resolves with the class as enclosing scope.
        result = get_ast_range(nav_repo, "calc.py", 15)
        assert result["ok"]
        # The tightest block is the method (function); the class is in scope.
        assert "Calculator" in result["enclosing_scope"]

    def test_regex_language_enclosing_def(self, nav_repo):
        # Line 6 inside util.go helper() expands to the whole func.
        result = get_ast_range(nav_repo, "util.go", 6)
        assert result["ok"]
        assert result["start_line"] == 3
        assert result["end_line"] == 9

    def test_end_line_defaults_to_start(self, nav_repo):
        result = get_ast_range(nav_repo, "calc.py", 2)
        assert result["ok"]
        assert result["requested_end"] == 2

    def test_file_not_found(self, nav_repo):
        result = get_ast_range(nav_repo, "missing.py", 1)
        assert not result["ok"]
        assert "not found" in result["error"]

    def test_max_lines_truncates(self, tmp_path):
        body = "\n".join(f"    x{i} = {i}" for i in range(30))
        (tmp_path / "big.py").write_text("def big():\n" + body + "\n")
        # max_lines has a 20-line floor; a 31-line block truncates at 20.
        result = get_ast_range(tmp_path, "big.py", 5, max_lines=20)
        assert result["ok"]
        assert result["truncated"]
        assert result["end_line"] - result["start_line"] + 1 == 20

    @pytest.mark.asyncio
    async def test_tool_output(self, nav_repo):
        tool = GetAstRangeTool(str(nav_repo))
        out = await tool.run(path="calc.py", start_line=14)
        assert "calc.py" in out
        assert "function" in out
        assert "multiply" in out

    @pytest.mark.asyncio
    async def test_tool_not_found(self, nav_repo):
        tool = GetAstRangeTool(str(nav_repo))
        out = await tool.run(path="nope.py", start_line=1)
        assert "not found" in out
