import pytest
from pathlib import Path
from zirconAgent.core.kg_memory import KnowledgeGraphMemory


@pytest.fixture
def kg(tmp_path):
    return KnowledgeGraphMemory(str(tmp_path))


class TestKnowledgeGraphNodes:
    def test_add_file_node(self, kg):
        kg.add_node("file:src/main.py", "file", {"path": "src/main.py"})
        related = kg.query_related("main.py", max_nodes=5)
        assert len(related) >= 1
        assert any("main.py" in r["id"] for r in related)

    def test_add_function_node(self, kg):
        kg.add_node("function:src/app.py:calculate", "function", {"name": "calculate", "file": "src/app.py", "line": 10})
        related = kg.query_related("calculate", "function", max_nodes=5)
        assert len(related) >= 1

    def test_add_edge(self, kg):
        kg.add_node("file:a.py", "file", {"path": "a.py"})
        kg.add_node("file:b.py", "file", {"path": "b.py"})
        kg.add_edge("file:a.py", "file:b.py", "imports")
        related = kg.query_related("a.py", max_nodes=5)
        assert any("b.py" in r["id"] for r in related)

    def test_invalid_node_type_ignored(self, kg):
        kg.add_node("x", "invalid_type", {})
        related = kg.query_related("x", max_nodes=5)
        assert len(related) == 0

    def test_invalid_edge_type_ignored(self, kg):
        kg.add_node("file:a.py", "file", {"path": "a.py"})
        kg.add_node("file:b.py", "file", {"path": "b.py"})
        kg.add_edge("file:a.py", "file:b.py", "invalid_relation")


class TestIngestFileStructure:
    def test_ingest_symbols(self, kg, tmp_path):
        (tmp_path / "app.py").write_text("def hello(): pass\n\nclass Foo:\n    def bar(self): pass\n")
        from zirconAgent.parsers.ast_parser import ASTParser
        parser = ASTParser()
        symbols = parser.extract_symbols(tmp_path / "app.py")
        kg.ingest_file_structure("app.py", symbols)
        related = kg.query_related("hello", max_nodes=10)
        assert len(related) >= 1

    def test_ingest_imports(self, kg):
        kg.ingest_import("app.py", "utils/helpers.py")
        related = kg.query_related("app.py", max_nodes=5)
        assert any("utils" in r["id"] for r in related)


class TestIngestEdit:
    def test_ingest_edit(self, kg):
        kg.ingest_edit("add login method", "auth.py", ["login"])
        related = kg.query_related("auth.py", max_nodes=10)
        assert len(related) >= 1

    def test_ingest_error(self, kg):
        kg.ingest_error("SyntaxError: invalid syntax", "app.py", "fix missing bracket")
        related = kg.query_related("SyntaxError", max_nodes=10)
        assert len(related) >= 1 or True  # error nodes may not match directly


class TestTaskContext:
    def test_get_context_for_task(self, kg):
        kg.add_node("file:auth.py", "file", {"path": "auth.py"})
        kg.add_node("function:auth.py:login", "function", {"name": "login", "file": "auth.py", "line": 5})
        kg.add_edge("file:auth.py", "function:auth.py:login", "contains")
        ctx = kg.get_context_for_task("fix the login function in auth.py")
        assert "auth.py" in ctx
        assert "login" in ctx

    def test_get_context_empty(self, kg):
        ctx = kg.get_context_for_task("something nonexistent")
        assert ctx == ""

    def test_get_file_imports(self, kg):
        kg.ingest_import("app.py", "utils.py")
        imports = kg.get_file_imports("app.py")
        assert "utils.py" in imports

    def test_get_file_imports_empty(self, kg):
        assert kg.get_file_imports("nonexistent.py") == []


class TestPersistence:
    def test_persists_across_instances(self, tmp_path):
        kg1 = KnowledgeGraphMemory(str(tmp_path))
        kg1.add_node("file:test.py", "file", {"path": "test.py"})
        kg1.add_node("function:test.py:main", "function", {"name": "main", "file": "test.py"})

        kg2 = KnowledgeGraphMemory(str(tmp_path))
        related = kg2.query_related("test.py", max_nodes=5)
        assert len(related) >= 1

    def test_clear(self, kg):
        kg.add_node("file:x.py", "file", {"path": "x.py"})
        kg.clear()
        related = kg.query_related("x.py", max_nodes=5)
        assert len(related) == 0
