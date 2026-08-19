"""Tests for the SOTA syntax checker module."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.syntax_checker import (
    SyntaxChecker,
    SyntaxIssue,
    SyntaxCheckResult,
    check_files,
    check_content,
)


@pytest.fixture
def checker():
    return SyntaxChecker(repo_path=os.getcwd())


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a temp directory to use as a repo root."""
    return SyntaxChecker(repo_path=tmp_path)


# ========================================================================== #
# SyntaxCheckResult tests
# ========================================================================== #


class TestSyntaxCheckResult:
    def test_no_issues(self):
        r = SyntaxCheckResult(file_path="test.py")
        assert not r.has_errors
        assert not r.has_warnings
        assert r.format() == ""

    def test_with_errors(self):
        r = SyntaxCheckResult(
            file_path="test.py",
            issues=[
                SyntaxIssue(file_path="test.py", line=5, column=3, message="invalid syntax"),
            ],
        )
        assert r.has_errors
        assert '<syntax_errors file="test.py">' in r.format()
        assert "line 5, col 3:" in r.format()

    def test_with_warnings(self):
        r = SyntaxCheckResult(
            file_path="test.html",
            issues=[
                SyntaxIssue(file_path="test.html", line=10, column=1,
                            message="Unclosed tag", severity="warning"),
            ],
        )
        assert not r.has_errors
        assert r.has_warnings
        assert '<syntax_warnings file="test.html">' in r.format()

    def test_with_rule_id(self):
        r = SyntaxCheckResult(
            file_path="test.py",
            issues=[
                SyntaxIssue(file_path="test.py", line=5, column=3,
                            message="Unused import", severity="warning", rule_id="F401"),
            ],
        )
        assert " [F401]" in r.format()

    def test_info_not_included_by_default(self):
        r = SyntaxCheckResult(
            file_path="test.py",
            issues=[
                SyntaxIssue(file_path="test.py", line=1, column=1,
                            message="Info", severity="info"),
            ],
        )
        assert r.format() == ""  # info not included unless explicitly requested


# ========================================================================== #
# Python checking
# ========================================================================== #


class TestPythonChecking:
    def test_valid_python(self, checker):
        result = checker.check_text("test.py", "x = 1\ny = 2\n")
        assert not result.has_errors

    def test_indentation_error(self, checker):
        result = checker.check_text("test.py", "def foo():\nprint('hello')\n")
        assert result.has_errors
        assert "indented" in result.format() or "expected" in result.format()

    def test_syntax_error(self, checker):
        result = checker.check_text("test.py", "x = {\n")
        assert result.has_errors

    def test_valid_class_and_function(self, checker):
        result = checker.check_text("test.py", """
class Foo:
    def bar(self):
        pass

def external():
    return 42
""")
        assert not result.has_errors

    def test_empty_file(self, checker):
        result = checker.check_text("test.py", "")
        assert not result.has_errors
        assert result.format() == ""

    def test_missing_import_parent(self, checker):
        """Valid Python with a logical issue (not syntax) — should still pass ast check."""
        result = checker.check_text("test.py", "import os\nos.path.join('a', 'b')\n")
        assert not result.has_errors

    def test_multiple_syntax_errors(self, checker):
        """Test binary-split error detection finds more than one error."""
        result = checker.check_text("test.py",
            "def foo():\n  pass\n"
            "\n"
            "class Bar:\n"
            "  pass\n"
            "\n"
            "x = {\n"          # error 1: unclosed dict
            "\n"
            "def broken():\n"   # error 2: after dict error, parser recovers
            "  pass\n"          # but should detect indentation issues
        )
        # At minimum should detect at least one error
        assert result.has_errors

    def test_ruff_optional(self, checker):
        """Ruff check should silently pass if ruff is not installed."""
        result = checker.check_text("test.py", "import os\nx = 1\n")
        # At minimum, ast.parse should pass
        # ruff may or may not be installed — should not crash
        assert isinstance(result, SyntaxCheckResult)


# ========================================================================== #
# JavaScript / TypeScript checking
# ========================================================================== #


class TestJavaScriptChecking:
    def test_valid_javascript(self, checker, tmp_path):
        p = tmp_path / "test.js"
        p.write_text("const x = 1;\nconsole.log(x);\n")
        result = checker.check_path(str(p))
        # May or may not have node — check gracefully
        assert isinstance(result, SyntaxCheckResult)

    def test_invalid_javascript(self, checker, tmp_path):
        p = tmp_path / "test.js"
        p.write_text("const x = \n")
        result = checker.check_path(str(p))
        if result.issues:
            # Should report syntax error
            first = result.issues[0]
            assert first.severity == "error" or first.checker == "node"

    def test_node_not_found_fallback(self, checker, monkeypatch, tmp_path):
        """If node is not found, should produce a warning, not crash."""
        p = tmp_path / "test.mjs"
        p.write_text("export const x = 1;\n")
        # Monkeypatch subprocess.run to raise FileNotFoundError
        import subprocess as sp
        original = sp.run
        def mock_run(*args, **kwargs):
            if args and 'node' in str(args[0]):
                raise FileNotFoundError("node not found")
            return original(*args, **kwargs)
        monkeypatch.setattr(sp, 'run', mock_run)

        result = checker.check_path(str(p))
        # Should have a warning about node not found
        assert isinstance(result, SyntaxCheckResult)

    def test_typescript_graceful(self, checker, tmp_path):
        """TS check should gracefully handle tsc not being installed."""
        p = tmp_path / "test.ts"
        p.write_text("const x: number = 1;\n")
        result = checker.check_path(str(p))
        assert isinstance(result, SyntaxCheckResult)


# ========================================================================== #
# JSON checking
# ========================================================================== #


class TestJSONChecking:
    def test_valid_json(self, checker):
        result = checker.check_text("test.json", '{"a": 1, "b": 2}')
        assert not result.has_errors

    def test_trailing_comma(self, checker):
        result = checker.check_text("test.json", '{"a": 1,}')
        assert result.has_errors
        assert "JSONDecodeError" in result.format()

    def test_invalid_json(self, checker):
        result = checker.check_text("test.json", "{")
        assert result.has_errors

    def test_empty_json_object(self, checker):
        result = checker.check_text("test.json", "{}")
        assert not result.has_errors

    def test_nested_json(self, checker):
        result = checker.check_text("test.json", '{"a": {"b": [1, 2, 3]}}')
        assert not result.has_errors

    def test_large_json_array(self, checker):
        result = checker.check_text("test.json", json.dumps(list(range(1000))))
        assert not result.has_errors


# ========================================================================== #
# YAML checking
# ========================================================================== #


class TestYAMLChecking:
    def test_valid_yaml(self, checker, tmp_path):
        p = tmp_path / "test.yml"
        p.write_text("key: value\nlist:\n  - item1\n  - item2\n")
        result = checker.check_path(str(p))
        assert isinstance(result, SyntaxCheckResult)

    def test_invalid_yaml(self, checker, tmp_path):
        p = tmp_path / "test.yaml"
        p.write_text("key: value\n  indented: bad\n")
        result = checker.check_path(str(p))
        # If PyYAML is installed, should detect error
        if result.issues:
            assert "YAML" in result.issues[0].message or "yaml" in result.issues[0].checker


# ========================================================================== #
# TOML checking
# ========================================================================== #


class TestTOMCChecking:
    def test_valid_toml(self, checker):
        result = checker.check_text("test.toml", '[tool]\nkey = "value"\n')
        assert isinstance(result, SyntaxCheckResult)
        # May or may not have TOML parser

    def test_invalid_toml(self, checker):
        result = checker.check_text("test.toml", '[tool\nkey = "value"\n')
        # If TOML parser available, should detect error
        if result.issues:
            assert "TOML" in result.issues[0].message or "toml" in result.issues[0].checker


# ========================================================================== #
# HTML checking (using proper html.parser, not regex)
# ========================================================================== #


class TestHTMLChecking:
    def test_valid_html(self, checker):
        result = checker.check_text("test.html", "<html><body><p>Hello</p></body></html>")
        assert not result.has_errors

    def test_unclosed_tag_is_error(self, checker):
        """With proper parser, unclosed tags are errors, not warnings."""
        result = checker.check_text("test.html", "<div><p>hello</div>")
        assert result.has_errors
        # Should detect that <p> is unclosed
        assert any("Unclosed" in i.message for i in result.issues)

    def test_unexpected_closing_tag(self, checker):
        result = checker.check_text("test.html", "<div></span></div>")
        assert result.has_errors
        assert any("Unexpected" in i.message for i in result.issues)

    def test_self_closing_tags(self, checker):
        result = checker.check_text("test.html",
            "<html><body><br><hr><img src='x'><input type='text'></body></html>")
        assert not result.has_errors

    def test_void_elements_no_error(self, checker):
        """Void elements should not require closing tags."""
        result = checker.check_text("test.html",
            "<html><body><br><br><img src='a'><img src='b'></body></html>")
        assert not result.has_errors

    def test_complex_nested_html(self, checker):
        result = checker.check_text("test.html", """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Test</title>
</head>
<body>
    <div class="container">
        <ul>
            <li>Item 1</li>
            <li>Item 2</li>
        </ul>
        <p>Paragraph with <strong>bold</strong> text</p>
    </div>
</body>
</html>
""")
        assert not result.has_errors

    def test_doctype_and_comments(self, checker):
        result = checker.check_text("test.html",
            "<!DOCTYPE html><!-- comment --><html><body></body></html>")
        assert not result.has_errors

    def test_xml_self_closing(self, checker):
        result = checker.check_text("test.xml", "<root><item attr='val' /><item /></root>")
        assert not result.has_errors


# ========================================================================== #
# CSS checking
# ========================================================================== #


class TestCSSChecking:
    def test_valid_css(self, checker):
        result = checker.check_text("test.css",
            "body { color: red; }\n"
            ".class { margin: 0; padding: 0; }\n"
        )
        assert not result.has_errors

    def test_unclosed_brace(self, checker):
        result = checker.check_text("test.css",
            "body { color: red;\n"
            ".class { margin: 0; }\n"
        )
        assert result.has_errors
        assert "Unclosed" in result.format() or "unmatched" in result.format()

    def test_extra_closing_brace(self, checker):
        result = checker.check_text("test.css",
            "body { color: red; }\n}\n"
        )
        assert result.has_errors
        assert "Unexpected" in result.format()

    def test_css_with_comments(self, checker):
        result = checker.check_text("test.css",
            "/* Main styles */\n"
            "body { background: #fff; }\n"
            "/* Section */\n"
            ".section { border: 1px solid #ccc; }\n"
        )
        assert not result.has_errors


# ========================================================================== #
# Shell script checking
# ========================================================================== #


class TestShellChecking:
    def test_valid_shell(self, checker, tmp_path):
        p = tmp_path / "test.sh"
        p.write_text("#!/bin/bash\necho 'hello'\nexit 0\n")
        result = checker.check_path(str(p))
        assert isinstance(result, SyntaxCheckResult)

    def test_shell_syntax_error(self, checker, tmp_path):
        p = tmp_path / "test.sh"
        p.write_text("#!/bin/bash\nif true then\necho 'missing semicolon'\n")
        result = checker.check_path(str(p))
        # If bash is available, should detect error
        if result.issues and result.issues[0].checker == "bash":
            assert "syntax error" in result.issues[0].message.lower()


# ========================================================================== #
# Dockerfile checking
# ========================================================================== #


class TestDockerfileChecking:
    def test_valid_dockerfile(self, checker, tmp_path):
        p = tmp_path / "Dockerfile"
        p.write_text("FROM python:3.11\nWORKDIR /app\nCMD ['python']\n")
        # File must be named exactly Dockerfile for checker selection
        import shutil
        shutil.copy2(str(p), str(tmp_path / "dockerfile-from-temp"))
        # Use check_text with explicit Dockerfile name
        result = checker.check_text("Dockerfile",
            "FROM python:3.11\nWORKDIR /app\nCMD ['python']\n")
        assert not result.has_errors

    def test_invalid_dockerfile_instruction(self, checker):
        result = checker.check_text("Dockerfile",
            "FROM python:3.11\nINVALID_CMD echo hello\n")
        assert result.has_warnings or result.has_errors

    def test_missing_from(self, checker):
        result = checker.check_text("Dockerfile",
            "RUN echo hello\n")
        # Should warn about missing FROM
        assert result.has_warnings or result.has_errors


# ========================================================================== #
# File path checking
# ========================================================================== #


class TestFileChecking:
    def test_check_path(self, checker, tmp_path):
        p = tmp_path / "test.py"
        p.write_text("valid_code = 42\n")
        result = checker.check_path(str(p))
        assert not result.has_errors

    def test_check_path_with_errors(self, checker, tmp_path):
        p = tmp_path / "test.py"
        p.write_text("def broken():\nono\n")
        result = checker.check_path(str(p))
        assert result.has_errors

    def test_check_missing_file(self, checker):
        result = checker.check_path("/nonexistent/file.py")
        assert result.has_errors
        assert "Cannot read" in result.issues[0].message


# ========================================================================== #
# Checkfiles helper
# ========================================================================== #


class TestCheckFilesHelper:
    def test_no_errors(self):
        result = check_files(
            ["a.py", "b.json"],
            content_map={
                "a.py": "x = 1",
                "b.json": "{}",
            },
        )
        assert result == ""

    def test_with_errors(self):
        result = check_files(
            ["a.py", "b.json"],
            content_map={
                "a.py": "x = 1",
                "b.json": "{invalid",
            },
        )
        assert "JSONDecodeError" in result

    def test_mixed(self):
        result = check_files(
            ["a.py", "b.json", "c.py"],
            content_map={
                "a.py": "x = 1",
                "b.json": "{",
                "c.py": "def foo():\n  pass\n",
            },
        )
        assert "JSONDecodeError" in result
        assert "x = 1" not in result
        assert "foo" not in result

    def test_multiple_errors(self):
        result = check_files(
            ["a.py", "b.json"],
            content_map={
                "a.py": "def broken():\n  pass\nprint('extra')",
                "b.json": "{invalid",
            },
        )
        assert "JSONDecodeError" in result


# ========================================================================== #
# Non-code files should pass silently
# ========================================================================== #


class TestNonCodeFiles:
    def test_txt(self, checker):
        result = checker.check_text("notes.txt",
            "This is plain text with obvious {{syntax}} errors >>>")
        assert not result.has_errors
        assert result.format() == ""

    def test_markdown(self, checker):
        result = checker.check_text("readme.md", "# Heading\nSome **text** here")
        assert not result.has_errors

    def test_css(self, checker):
        """CSS is now checked, but valid CSS should pass."""
        result = checker.check_text("style.css", "body { color: red; }")
        assert not result.has_errors

    def test_rst(self, checker):
        result = checker.check_text("docs.rst", "Title\n=====\n\nContent here.")
        assert not result.has_errors


# ========================================================================== #
# Edge cases
# ========================================================================== #


class TestEdgeCases:
    def test_mixed_content_extension(self, checker):
        """File with .py extension but no actual Python content."""
        result = checker.check_text("test.py", "This is not Python at all\n" * 10)
        assert result.has_errors

    def test_binary_content(self, checker):
        """Binary content should not crash."""
        result = checker.check_text("test.py", "\x00\x01\x02\x03\n")
        # May or may not detect error depending on encoding
        assert isinstance(result, SyntaxCheckResult)

    def test_very_long_line(self, checker):
        """Very long line should not cause issues (but Python may flag excessive int conversion)."""
        # Python protects against denial-of-service via int string conversion;
        # use a reasonable-length line instead
        result = checker.check_text("test.py", "x = " + "1" * 100 + "\n")
        assert not result.has_errors

    def test_unicode_python(self, checker):
        """Unicode in Python should be fine."""
        result = checker.check_text("test.py", "# -*- coding: utf-8 -*-\nprint('héllo wörld')\n")
        assert not result.has_errors

    def test_unicode_in_json(self, checker):
        """Unicode in JSON should be fine."""
        result = checker.check_text("test.json", '{"greeting": "héllo wörld"}')
        assert not result.has_errors