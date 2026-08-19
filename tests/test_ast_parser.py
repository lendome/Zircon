import pytest
from pathlib import Path

from zirconAgent.parsers.ast_parser import ASTParser


@pytest.fixture
def parser():
    return ASTParser()


@pytest.fixture
def py_file(tmp_path):
    p = tmp_path / "sample.py"
    p.write_text(
        '"""Module docstring."""\n'
        "\n"
        "import os\n"
        "from typing import List\n"
        "\n"
        "\n"
        "def standalone_func(x: int) -> str:\n"
        '    """A standalone function."""\n'
        "    return str(x)\n"
        "\n"
        "\n"
        "class MyClass:\n"
        '    """A sample class."""\n'
        "\n"
        "    def __init__(self, name: str):\n"
        "        self.name = name\n"
        "\n"
        "    def greet(self) -> str:\n"
        '        return f"Hello, {self.name}"\n'
        "\n"
        "    async def async_method(self):\n"
        "        pass\n"
        "\n"
        "\n"
        "async def async_func():\n"
        "    pass\n"
    )
    return p


class TestExtractSymbols:
    def test_functions(self, parser, py_file):
        symbols = parser.extract_symbols(py_file)
        names = [s["name"] for s in symbols]
        assert "standalone_func" in names
        assert "async_func" in names

    def test_classes(self, parser, py_file):
        symbols = parser.extract_symbols(py_file)
        classes = [s for s in symbols if s["kind"] == "class"]
        assert len(classes) == 1
        assert classes[0]["name"] == "MyClass"

    def test_methods(self, parser, py_file):
        symbols = parser.extract_symbols(py_file)
        methods = [s for s in symbols if s["kind"] == "method"]
        method_names = [m["name"] for m in methods]
        assert "MyClass.__init__" in method_names
        assert "MyClass.greet" in method_names
        assert "MyClass.async_method" in method_names

    def test_line_numbers(self, parser, py_file):
        symbols = parser.extract_symbols(py_file)
        standalone = next(s for s in symbols if s["name"] == "standalone_func")
        assert standalone["line"] == 7

    def test_function_args(self, parser, py_file):
        symbols = parser.extract_symbols(py_file)
        func = next(s for s in symbols if s["name"] == "standalone_func")
        assert "x" in func["args"]

    def test_method_parent(self, parser, py_file):
        symbols = parser.extract_symbols(py_file)
        init = next(s for s in symbols if s["name"] == "MyClass.__init__")
        assert init["parent"] == "MyClass"

    def test_standalone_no_parent(self, parser, py_file):
        symbols = parser.extract_symbols(py_file)
        func = next(s for s in symbols if s["name"] == "standalone_func")
        assert func["parent"] is None

    def test_syntax_error_returns_empty(self, parser, tmp_path):
        bad = tmp_path / "bad.py"
        bad.write_text("def broken(\n")
        assert parser.extract_symbols(bad) == []

    def test_empty_file(self, parser, tmp_path):
        empty = tmp_path / "empty.py"
        empty.write_text("")
        assert parser.extract_symbols(empty) == []

    def test_unsupported_language_returns_empty(self, parser, tmp_path):
        rb = tmp_path / "app.rb"
        rb.write_text("def hello\n  'hi'\nend\n")
        assert parser.extract_symbols(rb) == []

    def test_javascript_symbols(self, parser, tmp_path):
        js = tmp_path / "app.js"
        js.write_text(
            "export function hello() { return 'hi'; }\n"
            "const world = async (x) => x + 1;\n"
            "export class Greeter {}\n"
        )
        syms = {(s["name"], s["kind"]) for s in parser.extract_symbols(js)}
        assert ("hello", "function") in syms
        assert ("world", "function") in syms
        assert ("Greeter", "class") in syms

    def test_go_symbols(self, parser, tmp_path):
        go = tmp_path / "main.go"
        go.write_text(
            "package main\n\n"
            "type VMAddress struct {\n\tPC int\n}\n\n"
            "func (a VMAddress) String() string { return \"\" }\n\n"
            "func Disassemble(data []byte) error { return nil }\n"
        )
        syms = {(s["name"], s["kind"]) for s in parser.extract_symbols(go)}
        assert ("VMAddress", "class") in syms
        assert ("VMAddress.String", "method") in syms
        assert ("Disassemble", "function") in syms

    def test_rust_symbols(self, parser, tmp_path):
        rs = tmp_path / "lib.rs"
        rs.write_text(
            "pub struct Engine;\n\n"
            "impl Engine {\n    pub fn new() -> Self { Engine }\n}\n\n"
            "pub fn run(e: &Engine) {}\n"
        )
        syms = {(s["name"], s["kind"]) for s in parser.extract_symbols(rs)}
        assert ("Engine", "class") in syms
        assert ("run", "function") in syms


class TestGetRepoMap:
    def test_basic_map(self, parser, tmp_path):
        (tmp_path / "a.py").write_text("def func_a(): pass\n")
        (tmp_path / "b.py").write_text("class ClsB:\n    def meth(self): pass\n")
        result = parser.get_repo_map(tmp_path)
        assert "a.py" in result
        assert "func_a" in result
        assert "b.py" in result
        assert "ClsB" in result

    def test_skips_hidden_dirs(self, parser, tmp_path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("def secret(): pass\n")
        (tmp_path / "visible.py").write_text("def visible(): pass\n")
        result = parser.get_repo_map(tmp_path)
        assert "visible" in result
        assert "secret" not in result

    def test_max_files(self, parser, tmp_path):
        for i in range(20):
            (tmp_path / f"file_{i:02d}.py").write_text(f"def f_{i}(): pass\n")
        result = parser.get_repo_map(tmp_path, max_files=5)
        assert "file_00" in result
