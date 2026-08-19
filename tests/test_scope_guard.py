import pytest

from zirconAgent.core.agent import Agent, _SCOPE_COMPONENT_RE, _SCOPE_NAME_STOPWORDS
from zirconAgent.core.context import RepoMapEntry
from zirconAgent.tools.registry import ScopeGuard, ToolRegistry
from zirconAgent.tools.edit_ops import EditFileTool


def _agent_with_repo_map(repo_map: dict) -> Agent:
    """Minimal Agent shell for _arm_scope_guard: registry + context.repo_map."""
    a = Agent.__new__(Agent)
    a.registry = ToolRegistry()

    class _Ctx:
        pass

    a.context = _Ctx()
    a.context.repo_map = repo_map
    return a


def _entry(symbols: list[dict] | None = None) -> RepoMapEntry:
    return RepoMapEntry(path="", symbols=symbols or [], imports=[], line_count=10)


class TestScopeDetectionRegex:
    @pytest.mark.parametrize("task,name,kind", [
        ("fix the disassembler engine so it stops looping", "disassembler", "engine"),
        ("optimize the parser module", "parser", "module"),
        ("refactor the auth service to use tokens", "auth", "service"),
        ("the EditEngine component is slow", "EditEngine", "component"),
        ("improve the rendering pipeline please", "rendering", "pipeline"),
    ])
    def test_component_phrases_detected(self, task, name, kind):
        m = _SCOPE_COMPONENT_RE.search(task)
        assert m is not None, task
        assert m.group(1) == name
        assert m.group(2) == kind

    @pytest.mark.parametrize("task", [
        "fix the bug in main.py",
        "add a caching layer to the app",
        "what does the engine do?",
        "rewrite everything from scratch",
    ])
    def test_non_component_phrases_ignored(self, task):
        assert _SCOPE_COMPONENT_RE.search(task) is None

    def test_stopwords_cover_generic_names(self):
        for word in ("main", "whole", "entire", "codebase"):
            assert word in _SCOPE_NAME_STOPWORDS


class TestScopeGuard:
    def test_disarmed_by_default(self):
        guard = ScopeGuard()
        assert not guard.armed
        assert guard.check("edit_file", {"path": "any.py"}) is None
        assert guard.warn_if_outside("edit_file", {"path": "any.py"}) is None

    def test_block_mode_denies_outside_edit(self):
        guard = ScopeGuard()
        guard.mode = "block"
        guard.arm(["engine/core.py", "engine/utils.py"], "engine component")
        denial = guard.check("edit_file", {"path": "other/thing.py"})
        assert denial is not None
        assert denial.startswith("SCOPE-GUARD:")
        assert "engine component" in denial
        assert "INSIDE the component" in denial

    def test_block_mode_allows_inside_edit(self):
        guard = ScopeGuard()
        guard.mode = "block"
        guard.arm(["engine/core.py"], "engine component")
        assert guard.check("edit_file", {"path": "engine/core.py"}) is None
        # Windows-style separators normalize to the same path.
        assert guard.check("edit_file", {"path": "engine\\core.py"}) is None

    def test_directory_armed_guard_covers_new_files(self):
        guard = ScopeGuard()
        guard.mode = "block"
        guard.arm(["engine/core.py"], "engine component", dirs=["engine"])
        # New files created inside the armed directory are in scope.
        assert guard.check("create_file", {"path": "engine/helpers.py"}) is None
        assert guard.check("create_file", {"path": "engine/sub/deep.py"}) is None
        # Sibling directories remain out of scope.
        assert guard.check("create_file", {"path": "other/helpers.py"}) is not None

    def test_file_armed_guard_blocks_new_sibling_in_block_mode(self):
        # Without an explicit directory, block mode is strict: only the armed
        # files themselves are editable.
        guard = ScopeGuard()
        guard.mode = "block"
        guard.arm(["engine/core.py"], "engine component")
        assert guard.check("create_file", {"path": "engine/helpers.py"}) is not None

    def test_warn_mode_never_denies(self):
        guard = ScopeGuard()
        guard.mode = "warn"
        guard.arm(["engine/core.py"], "engine component")
        assert guard.check("edit_file", {"path": "other/thing.py"}) is None
        warn = guard.warn_if_outside("edit_file", {"path": "other/thing.py"})
        assert warn is not None
        assert warn.startswith("SCOPE-GUARD: WARNING")
        assert "was applied" in warn

    def test_warn_mode_silent_for_inside_edit(self):
        guard = ScopeGuard()
        guard.mode = "warn"
        guard.arm(["engine/core.py"], "engine component")
        assert guard.warn_if_outside("edit_file", {"path": "engine/core.py"}) is None

    def test_off_mode_noop(self):
        guard = ScopeGuard()
        guard.mode = "off"
        guard.arm(["engine/core.py"], "engine component")
        assert not guard.armed
        assert guard.check("edit_file", {"path": "other.py"}) is None
        assert guard.warn_if_outside("edit_file", {"path": "other.py"}) is None

    def test_aider_path_extracted_from_content(self):
        guard = ScopeGuard()
        guard.mode = "block"
        guard.arm(["engine/core.py"], "engine component")
        outside = {"content": "other/thing.py\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"}
        assert guard.check("aider_edit", outside) is not None
        inside = {"content": "engine/core.py\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"}
        assert guard.check("aider_edit", inside) is None

    def test_malformed_aider_content_not_blocked(self):
        guard = ScopeGuard()
        guard.mode = "block"
        guard.arm(["engine/core.py"], "engine component")
        assert guard.check("aider_edit", {"content": ""}) is None

    def test_disarm_clears_state(self):
        guard = ScopeGuard()
        guard.mode = "block"
        guard.arm(["engine/core.py"], "engine component")
        guard.disarm()
        assert not guard.armed
        assert guard.check("edit_file", {"path": "other.py"}) is None


class TestScopeGuardRegistryIntegration:
    @pytest.mark.asyncio
    async def test_warn_mode_prefixes_successful_outside_edit(self, tmp_path):
        (tmp_path / "in.py").write_text("x = 1\n")
        (tmp_path / "out.py").write_text("y = 1\n")
        registry = ToolRegistry()
        registry.register(EditFileTool(str(tmp_path)))
        registry.scope_guard.mode = "warn"
        registry.scope_guard.arm(["in.py"], "in component")
        result = await registry.execute(
            "edit_file", {"path": "out.py", "search": "y = 1", "replace": "y = 2"}
        )
        assert result.startswith("SCOPE-GUARD: WARNING"), result
        assert "Applied" in result  # edit executed despite the warning

    @pytest.mark.asyncio
    async def test_block_mode_denies_outside_edit(self, tmp_path):
        (tmp_path / "in.py").write_text("x = 1\n")
        (tmp_path / "out.py").write_text("y = 1\n")
        registry = ToolRegistry()
        registry.register(EditFileTool(str(tmp_path)))
        registry.scope_guard.mode = "block"
        registry.scope_guard.arm(["in.py"], "in component")
        result = await registry.execute(
            "edit_file", {"path": "out.py", "search": "y = 1", "replace": "y = 2"}
        )
        assert result.startswith("SCOPE-GUARD:"), result
        assert (tmp_path / "out.py").read_text() == "y = 1\n"  # untouched
        inside = await registry.execute(
            "edit_file", {"path": "in.py", "search": "x = 1", "replace": "x = 2"}
        )
        assert inside.startswith("Applied"), inside


class TestAgentScopeArming:
    def test_file_stem_match_arms_guard(self):
        a = _agent_with_repo_map({
            "core/disassembler.py": _entry(),
            "core/executor.py": _entry(),
        })
        Agent._arm_scope_guard(a, "fix the disassembler engine so it stops looping")
        assert a._scope_guard_label == "disassembler engine"
        assert a.registry.scope_guard.armed
        assert a.registry.scope_guard.allowed_files() == ["core/disassembler.py"]

    def test_directory_match_arms_directory(self):
        a = _agent_with_repo_map({
            "core/engine/decoder.py": _entry(),
            "core/engine/tables.py": _entry(),
            "cli/app.py": _entry(),
        })
        Agent._arm_scope_guard(a, "optimize the engine module")
        guard = a.registry.scope_guard
        assert guard.armed
        assert len(guard.allowed_files()) == 2
        # The directory is armed: new files inside it are in scope.
        guard.mode = "block"
        assert guard.check("create_file", {"path": "core/engine/new_helper.py"}) is None
        assert guard.check("edit_file", {"path": "cli/app.py"}) is not None

    def test_symbol_match_resolves_class_name(self):
        a = _agent_with_repo_map({
            "core/edit_engine.py": _entry(symbols=[{"name": "EditEngine", "kind": "class"}]),
            "core/other.py": _entry(symbols=[{"name": "helper", "kind": "function"}]),
        })
        Agent._arm_scope_guard(a, "the EditEngine component is slow")
        assert a.registry.scope_guard.allowed_files() == ["core/edit_engine.py"]

    def test_zero_files_stays_disarmed(self):
        a = _agent_with_repo_map({"core/executor.py": _entry()})
        Agent._arm_scope_guard(a, "fix the nonexistent engine")
        assert not a.registry.scope_guard.armed
        assert a._scope_guard_label == ""

    def test_stopword_names_stay_disarmed(self):
        a = _agent_with_repo_map({"main.py": _entry(), "core/engine.py": _entry()})
        Agent._arm_scope_guard(a, "rewrite the main module")
        assert not a.registry.scope_guard.armed

    def test_no_component_phrase_stays_disarmed(self):
        a = _agent_with_repo_map({"core/engine.py": _entry()})
        Agent._arm_scope_guard(a, "add a retry loop to the executor")
        assert not a.registry.scope_guard.armed

    def test_off_mode_never_arms(self):
        a = _agent_with_repo_map({"core/disassembler.py": _entry()})
        a.registry.scope_guard.mode = "off"
        Agent._arm_scope_guard(a, "fix the disassembler engine")
        assert not a.registry.scope_guard.armed
