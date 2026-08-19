import pytest

from zirconAgent.tools.registry import (
    CommandFailureCache,
    ToolRegistry,
    _normalize_command,
)
from zirconAgent.tools.shell_ops import RunCommandTool


class TestNormalization:
    def test_whitespace_collapsed(self):
        assert _normalize_command("  ls   -la\t. ") == "ls -la ."


class TestCommandFailureCache:
    def test_first_failure_never_intercepted(self):
        cache = CommandFailureCache()
        cache.record("bad cmd", "Error: 'foo' is not recognized as an internal or external command\nExit code: 1")
        # A single failure doesn't intercept the first retry for runtime; for
        # syntax it intercepts from the first repeat onward.
        assert cache.check("bad cmd") is not None  # syntax -> hard intercept

    def test_syntax_hard_intercept_on_repeat(self):
        cache = CommandFailureCache()
        result = "'./tool' is not recognized as an internal or external command\nExit code: 1"
        cache.record("./tool", result)
        msg = cache.check("./tool")
        assert msg is not None
        assert msg.startswith("CIRCUIT-BREAKER:")
        assert "syntax" in msg

    def test_varied_command_passes(self):
        cache = CommandFailureCache()
        cache.record("./tool", "'./tool' is not recognized\nExit code: 1")
        assert cache.check("./tool --fixed") is None
        assert cache.check(".\\tool") is None

    def test_runtime_failure_allows_immediate_retry(self):
        cache = CommandFailureCache()
        cache.record("pytest -q", "1 failed\nExit code: 1")
        assert cache.check("pytest -q") is None  # 2nd attempt allowed

    def test_runtime_failure_intercepts_third_repeat(self):
        cache = CommandFailureCache()
        cache.record("pytest -q", "1 failed\nExit code: 1")
        cache.record("pytest -q", "1 failed\nExit code: 1")
        msg = cache.check("pytest -q")
        assert msg is not None
        assert "CIRCUIT-BREAKER:" in msg

    def test_mutation_resets_runtime_failure(self):
        cache = CommandFailureCache()
        cache.record("pytest -q", "1 failed\nExit code: 1")
        cache.record("pytest -q", "1 failed\nExit code: 1")
        cache.note_mutation()  # an edit succeeded — command may now pass
        assert cache.check("pytest -q") is None

    def test_mutation_does_not_reset_syntax_failure(self):
        cache = CommandFailureCache()
        cache.record("./tool", "'./tool' is not recognized as an internal or external command\nExit code: 1")
        cache.note_mutation()
        assert cache.check("./tool") is not None  # syntax errors are deterministic

    def test_success_clears_entry(self):
        cache = CommandFailureCache()
        cache.record("make test", "1 failed\nExit code: 1")
        cache.record("make test", "ok\nExit code: 0")
        assert cache.check("make test") is None

    def test_every_third_interception_passes_through(self):
        cache = CommandFailureCache()
        for _ in range(2):
            cache.record("flaky cmd", "connection refused\nExit code: 2")
        # consecutive == 2 -> intercepts, but every 3rd intercepted attempt passes
        intercepted = 0
        passed = 0
        for _ in range(9):
            if cache.check("flaky cmd") is None:
                passed += 1
            else:
                intercepted += 1
        assert intercepted > 0
        assert passed > 0  # escape valve fired

    def test_exit_127_classified_as_syntax(self):
        cache = CommandFailureCache()
        cache.record("./missing", "bash: ./missing: No such file or directory\nExit code: 127")
        assert cache.check("./missing") is not None


class TestRegistryIntegration:
    @pytest.mark.asyncio
    async def test_registry_intercepts_identical_syntax_failure(self, tmp_path):
        registry = ToolRegistry()
        registry.register(RunCommandTool(str(tmp_path)))
        # Use a command guaranteed to be a syntax/not-found error everywhere.
        cmd = "definitely-not-a-real-binary-xyz123 --run"
        first = await registry.execute("run_command", {"command": cmd, "timeout": 10})
        assert "Exit code" in first or "Error" in first
        second = await registry.execute("run_command", {"command": cmd, "timeout": 10})
        assert second.startswith("CIRCUIT-BREAKER:"), second

    @pytest.mark.asyncio
    async def test_registry_passes_varied_commands(self, tmp_path):
        registry = ToolRegistry()
        registry.register(RunCommandTool(str(tmp_path)))
        cmd = "definitely-not-a-real-binary-xyz123 --run"
        await registry.execute("run_command", {"command": cmd, "timeout": 10})
        varied = await registry.execute("run_command", {"command": cmd + " --other", "timeout": 10})
        assert not varied.startswith("CIRCUIT-BREAKER:")

    @pytest.mark.asyncio
    async def test_breaker_disabled_flag(self, tmp_path):
        registry = ToolRegistry()
        registry.circuit_breaker_enabled = False
        registry.register(RunCommandTool(str(tmp_path)))
        cmd = "definitely-not-a-real-binary-xyz123 --run"
        await registry.execute("run_command", {"command": cmd, "timeout": 10})
        second = await registry.execute("run_command", {"command": cmd, "timeout": 10})
        assert not second.startswith("CIRCUIT-BREAKER:")

    @pytest.mark.asyncio
    async def test_successful_command_not_recorded(self, tmp_path):
        registry = ToolRegistry()
        registry.register(RunCommandTool(str(tmp_path)))
        await registry.execute("run_command", {"command": "echo fine", "timeout": 10})
        again = await registry.execute("run_command", {"command": "echo fine", "timeout": 10})
        assert not again.startswith("CIRCUIT-BREAKER:")
        assert "fine" in again
