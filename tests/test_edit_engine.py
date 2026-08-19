import pytest
from pathlib import Path

from zirconAgent.core.edit_engine import EditEngine, EditBlock


@pytest.fixture
def engine():
    return EditEngine()


@pytest.fixture
def py_file(tmp_path):
    p = tmp_path / "test.py"
    p.write_text(
        "class Calculator:\n"
        "    def add(self, a, b):\n"
        "        return a + b\n"
        "\n"
        "    def multiply(self, x, y):\n"
        "        return x * y\n"
        "\n"
        "\n"
        "def standalone():\n"
        '    return "hello"\n'
    )
    return p


class TestSearchReplace:
    def test_exact_match(self, engine, py_file):
        result = engine.apply_search_replace(py_file, 'return "hello"', 'return "world"')
        assert result.success
        assert result.method == "exact"
        assert "world" in py_file.read_text()

    def test_multiline_exact(self, engine, py_file):
        result = engine.apply_search_replace(
            py_file,
            "    def multiply(self, x, y):\n        return x * y",
            "    def multiply(self, x, y):\n        return x * y\n\n    def divide(self, x, y):\n        return x / y",
        )
        assert result.success
        assert "divide" in py_file.read_text()

    def test_fuzzy_match(self, engine, py_file):
        result = engine.apply_search_replace(
            py_file,
            "def multiply(self, x, y):\n        return x * y",
            "def multiply(self, a, b):\n        return a * b",
        )
        assert result.success
        assert result.method in ("fuzzy", "exact")

    def test_fuzzy_proposal_does_not_mutate_until_applied(self, engine, py_file):
        original = py_file.read_text()
        proposal = engine.propose_search_replace(
            py_file,
            "def multiply(self, x, y):\n        return x * z",
            "def multiply(self, a, b):\n        return a * b",
        )

        assert proposal.success
        assert proposal.method != "exact"
        assert proposal.confidence < 1.0
        assert py_file.read_text() == original

        result = engine.apply_proposal(py_file, proposal)
        assert result.success
        assert "return a * b" in py_file.read_text()

    def test_stale_proposal_is_rejected(self, engine, py_file):
        proposal = engine.propose_search_replace(py_file, 'return "hello"', 'return "world"')
        py_file.write_text(py_file.read_text() + "\n# concurrent change\n")

        result = engine.apply_proposal(py_file, proposal)

        assert not result.success
        assert "stale" in result.error.lower()
        assert 'return "world"' not in py_file.read_text()

    def test_whitespace_normalized(self, engine, py_file):
        content = py_file.read_text()
        lines = content.splitlines()
        search_text = "    def add(self, a, b):\n        return a + b"
        assert search_text in content
        result = engine.apply_search_replace(
            py_file,
            "def add(self, a, b):\n        return a + b",
            "def add(self, a, b):\n        return a + b + 1",
        )
        assert result.success

    def test_no_match(self, engine, py_file):
        result = engine.apply_search_replace(py_file, "this text does not exist", "x")
        assert not result.success

    def test_empty_search(self, engine, py_file):
        result = engine.apply_search_replace(py_file, "", "x")
        assert not result.success

    def test_syntax_verification_blocks_bad_edit(self, engine, py_file):
        result = engine.apply_search_replace(
            py_file,
            'return "hello"',
            'return "hello"\nthis is broken syntax {{',
        )
        assert not result.success

    def test_missing_file(self, engine, tmp_path):
        result = engine.apply_search_replace(tmp_path / "nope.py", "x", "y")
        assert not result.success


class TestLineEdit:
    def test_replace_range(self, engine, py_file):
        result = engine.apply_line_edit(py_file, 3, 3, "        return a + b + 1\n")
        assert result.success
        assert "a + b + 1" in py_file.read_text()

    def test_replace_multiple_lines(self, engine, py_file):
        result = engine.apply_line_edit(py_file, 2, 3, "    def add(self, *args):\n        return sum(args)\n")
        assert result.success
        content = py_file.read_text()
        assert "sum(args)" in content

    def test_beyond_file(self, engine, py_file):
        result = engine.apply_line_edit(py_file, 999, 999, "content")
        assert not result.success

    def test_syntax_check_blocks_bad_edit(self, engine, py_file):
        result = engine.apply_line_edit(py_file, 10, 10, '    return "hello"\ndef broken(\n')
        assert not result.success


class TestASTReplace:
    def test_replace_function(self, engine, py_file):
        result = engine.apply_ast_replace(
            py_file,
            "standalone",
            "def standalone():\n    return 'replaced'",
        )
        assert result.success
        assert "replaced" in py_file.read_text()

    def test_replace_class_method(self, engine, py_file):
        result = engine.apply_ast_replace(
            py_file,
            "add",
            "    def add(self, a, b):\n        return a + b + 100",
        )
        assert result.success

    def test_nonexistent_symbol(self, engine, py_file):
        result = engine.apply_ast_replace(py_file, "nonexistent", "def x(): pass")
        assert not result.success

    def test_non_python_rejected(self, engine, tmp_path):
        js = tmp_path / "app.js"
        js.write_text("function hello() { return 1; }")
        result = engine.apply_ast_replace(js, "hello", "function hello() { return 2; }")
        assert not result.success


class TestAiderBlocks:
    def test_parse_single_block(self, engine):
        text = (
            "src/main.py\n"
            "<<<<<<< SEARCH\n"
            "def old():\n"
            "=======\n"
            "def new():\n"
            ">>>>>>> REPLACE"
        )
        blocks = engine.parse_aider_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].path == "src/main.py"
        assert "old" in blocks[0].search
        assert "new" in blocks[0].replace

    def test_parse_multiple_blocks(self, engine):
        text = (
            "a.py\n<<<<<<< SEARCH\nold1\n=======\nnew1\n>>>>>>> REPLACE\n"
            "b.py\n<<<<<<< SEARCH\nold2\n=======\nnew2\n>>>>>>> REPLACE"
        )
        blocks = engine.parse_aider_blocks(text)
        assert len(blocks) == 2

    def test_parse_no_blocks(self, engine):
        blocks = engine.parse_aider_blocks("just some text without blocks")
        assert len(blocks) == 0

    def test_apply_aider_blocks(self, engine, tmp_path):
        target = tmp_path / "target.py"
        target.write_text("def old_func():\n    return 1\n")
        text = (
            f"target.py\n"
            "<<<<<<< SEARCH\n"
            "def old_func():\n"
            "=======\n"
            "def new_func():\n"
            ">>>>>>> REPLACE"
        )
        results = engine.apply_aider_blocks(text, tmp_path)
        assert len(results) == 1
        assert results[0].success
        assert "new_func" in target.read_text()


class TestSelfRepair:
    def test_syntax_fix_attempted(self, engine, py_file):
        result = engine.apply_search_replace(
            py_file,
            "def standalone():",
            "def standalone():\n    pass\n    return 'hello'",
        )
        assert result.success
        assert result.verified


class TestHTMLVerification:
    """Regression tests for _verify_html false positives.

    The old handle_data check flagged HTML-like strings inside <script>
    and <style> blocks as errors, rejecting valid edits to JS template
    literals and CSS strings.
    """

    def test_js_template_literal_with_html(self, engine, tmp_path):
        html_file = tmp_path / "page.html"
        html_file.write_text(
            '<!DOCTYPE html>\n'
            '<html>\n'
            '<head><title>Test</title></head>\n'
            '<body>\n'
            '<script>\n'
            'const msg = `<div>Hello</div>`;\n'
            '</script>\n'
            '</body>\n'
            '</html>\n'
        )
        result = engine.apply_search_replace(
            html_file,
            "const msg = `<div>Hello</div>`;",
            "const msg = `<div>Hello World</div>`;",
        )
        assert result.success, f"Edit rejected: {result.error}"

    def test_css_with_html_like_string(self, engine, tmp_path):
        html_file = tmp_path / "app.html"
        html_file.write_text(
            '<!DOCTYPE html>\n'
            '<html><body>\n'
            '<style>\n'
            '.btn { content: "<button>Click</button>"; }\n'
            '</style>\n'
            '</body></html>\n'
        )
        result = engine.apply_search_replace(
            html_file,
            '.btn { content: "<button>Click</button>"; }',
            '.btn { content: "<button>Submit</button>"; }',
        )
        assert result.success, f"Edit rejected: {result.error}"

    def test_real_structural_error_still_caught(self, engine, tmp_path):
        """Unclosed tags in actual HTML must still be rejected."""
        html_file = tmp_path / "broken.html"
        html_file.write_text(
            '<!DOCTYPE html>\n'
            '<html><body>\n'
            '<div><p>hello</div>\n'
            '</body></html>\n'
        )
        result = engine.apply_search_replace(
            html_file,
            "<div><p>hello</div>",
            "<div><p>hello</p>",
        )
        assert not result.success
        assert "Syntax error" in result.error
