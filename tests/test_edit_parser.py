import pytest
from pathlib import Path

from zirconAgent.parsers.edit_parser import EditParser


@pytest.fixture
def parser():
    return EditParser()


@pytest.fixture
def py_file(tmp_path):
    p = tmp_path / "test.py"
    p.write_text(
        "class MyClass:\n"
        "    def method_one(self):\n"
        '        return "one"\n'
        "\n"
        "    def method_two(self, x):\n"
        '        return f"two: {x}"\n'
        "\n"
        "\n"
        "def standalone():\n"
        '    return "standalone"\n'
    )
    return p


class TestExactMatch:
    @pytest.mark.asyncio
    async def test_exact_single_line(self, parser, py_file):
        result = parser.apply(py_file, 'return "one"', 'return "ONE"')
        assert result.success
        assert result.matcher == "exact"
        assert "ONE" in py_file.read_text()

    @pytest.mark.asyncio
    async def test_exact_multiline(self, parser, py_file):
        result = parser.apply(
            py_file,
            "    def method_one(self):\n        return \"one\"",
            "    def method_one(self):\n        return 1",
        )
        assert result.success
        content = py_file.read_text()
        assert "return 1" in content
        assert "method_two" in content

    @pytest.mark.asyncio
    async def test_exact_no_match(self, parser, py_file):
        result = parser.apply(py_file, "this text does not exist", "x")
        assert not result.success


class TestFuzzyMatch:
    @pytest.mark.asyncio
    async def test_fuzzy_whitespace_diff(self, parser, py_file):
        result = parser.apply(
            py_file,
            "def method_one(self):\n        return \"one\"\n\n    def method_two(self, x):\n        return f\"two: {x}\"",
            "def method_one(self):\n        return 'ONE'\n\n    def method_two(self, x):\n        return f'two: {x}'",
        )
        assert result.success
        assert "ONE" in py_file.read_text()

    @pytest.mark.asyncio
    async def test_fuzzy_too_different(self, parser, py_file):
        result = parser.apply(py_file, "completely different text\nthat spans\nmultiple lines", "x")
        assert not result.success


class TestApplyLines:
    @pytest.mark.asyncio
    async def test_replace_single_line(self, parser, py_file):
        result = parser.apply_lines(py_file, 3, 3, '        return "CHANGED"')
        assert result.success
        assert "CHANGED" in py_file.read_text()

    @pytest.mark.asyncio
    async def test_replace_range(self, parser, py_file):
        result = parser.apply_lines(py_file, 1, 3, "class NewClass:\n    pass\n")
        assert result.success
        content = py_file.read_text()
        assert "NewClass" in content
        assert "MyClass" not in content

    @pytest.mark.asyncio
    async def test_insert_at_end(self, parser, py_file):
        result = parser.apply_lines(py_file, 10, 10, '    return "standalone"\n\n\ndef new_func():\n    pass')
        assert result.success
        assert "new_func" in py_file.read_text()


class TestSyntaxVerification:
    @pytest.mark.asyncio
    async def test_reject_invalid_python(self, parser, py_file):
        result = parser.apply(
            py_file,
            'return "one"',
            'return "one"  # ok but then\nthis is broken syntax',
        )
        assert not result.success
        assert "syntax error" in result.error.lower()

    @pytest.mark.asyncio
    async def test_allow_valid_python(self, parser, py_file):
        result = parser.apply(
            py_file,
            'return "one"',
            'return "one"  # perfectly valid',
        )
        assert result.success


class TestJsonFile:
    @pytest.mark.asyncio
    async def test_json_edit(self, parser, tmp_path):
        jf = tmp_path / "config.json"
        jf.write_text('{"key": "value", "count": 5}')
        result = parser.apply(jf, '"value"', '"updated"')
        assert result.success
        assert "updated" in jf.read_text()

    @pytest.mark.asyncio
    async def test_json_reject_invalid(self, parser, tmp_path):
        jf = tmp_path / "data.json"
        jf.write_text('{"key": "value"}')
        result = parser.apply(jf, '"value"', '{broken json')
        assert not result.success


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_missing_file(self, parser, tmp_path):
        result = parser.apply(tmp_path / "nope.py", "x", "y")
        assert not result.success

    @pytest.mark.asyncio
    async def test_empty_file(self, parser, tmp_path):
        ef = tmp_path / "empty.py"
        ef.write_text("")
        result = parser.apply(ef, "x", "y")
        assert not result.success

    @pytest.mark.asyncio
    async def test_apply_lines_start_beyond_file(self, parser, tmp_path):
        f = tmp_path / "short.py"
        f.write_text("x = 1\n")
        result = parser.apply_lines(f, 999, 999, "content")
        assert not result.success
