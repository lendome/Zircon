import asyncio
import platform

import pytest

from zirconAgent.core.shell_env import (
    CaptureResult,
    ShellSpec,
    _normalize,
    format_capture,
    reset_shell_cache,
    resolve_shell,
    run_capture,
    shell_syntax_hint,
)


@pytest.fixture(autouse=True)
def _clear_shell_cache():
    reset_shell_cache()
    yield
    reset_shell_cache()


class TestDetection:
    def test_pinning_disabled_returns_platform_default(self):
        spec = resolve_shell(False)
        if platform.system() == "Windows":
            assert spec.kind == "cmd"
        else:
            assert spec.kind == "sh"
        assert spec.uses_default_shell

    def test_resolved_shell_is_probed_working(self):
        # Whatever the detection picks, it must actually run a command.
        spec = resolve_shell(True)
        result = asyncio.run(run_capture("echo probe_ok", timeout=10, spec=spec))
        assert result.exit_code == 0
        assert "probe_ok" in result.stdout

    def test_result_is_cached(self):
        a = resolve_shell(True)
        b = resolve_shell(True)
        assert a is b


class TestRunCapture:
    def test_exit_code_captured(self):
        spec = resolve_shell(True)
        result = asyncio.run(run_capture("exit 3", timeout=10, spec=spec))
        assert result.exit_code == 3

    def test_stderr_captured_separately(self):
        spec = resolve_shell(True)
        if spec.kind in ("bash", "sh"):
            cmd = "echo out; echo err >&2"
        elif spec.kind == "powershell":
            cmd = "Write-Output out; [Console]::Error.WriteLine('err')"
        else:
            cmd = "echo out & echo err 1>&2"
        result = asyncio.run(run_capture(cmd, timeout=10, spec=spec))
        assert "out" in result.stdout
        assert "err" in result.stderr

    def test_timeout_kills_and_reports(self):
        spec = resolve_shell(True)
        result = asyncio.run(run_capture(
            'python -c "import time; time.sleep(30)"', timeout=1, spec=spec,
        ))
        assert result.timed_out

    def test_timeout_handover_keeps_process(self):
        spec = resolve_shell(True)
        result = asyncio.run(run_capture(
            'python -c "import time; time.sleep(30)"', timeout=1, spec=spec,
            kill_on_timeout=False,
        ))
        assert result.timed_out
        assert result.proc is not None
        try:
            result.proc.kill()
        except Exception:
            pass
        for t in result.drain_tasks:
            t.cancel()

    def test_launch_failure_never_raises(self):
        spec = ShellSpec(name="bogus", kind="bash", exe="C:\\definitely\\missing\\shell.exe", prefix_args=("-c",))
        result = asyncio.run(run_capture("echo hi", timeout=5, spec=spec))
        assert result.exit_code == -1
        assert "Error" in result.stderr


class TestFormatting:
    def test_format_capture_keeps_exit_code_trailer(self):
        result = CaptureResult(stdout="hello\n", stderr="", exit_code=0, duration=0.1)
        text = format_capture(result)
        assert "hello" in text
        assert "Exit code: 0" in text

    def test_format_capture_stderr_section(self):
        result = CaptureResult(stdout="", stderr="boom\n", exit_code=1)
        text = format_capture(result)
        assert "STDERR:" in text
        assert "boom" in text
        assert "Exit code: 1" in text

    def test_crlf_normalized(self):
        assert _normalize("a\r\nb\rc\n") == "a\nb\nc\n"

    def test_truncation(self):
        result = CaptureResult(stdout="x" * 200, exit_code=0)
        text = format_capture(result, max_chars=50)
        assert "truncated" in text


class TestHints:
    def test_bash_hint(self):
        spec = ShellSpec(name="git-bash", kind="bash", exe="bash.exe", prefix_args=("-c",))
        hint = shell_syntax_hint(spec)
        assert "git-bash" in hint and "bash" in hint

    def test_cmd_hint(self):
        spec = ShellSpec(name="cmd", kind="cmd", exe="")
        hint = shell_syntax_hint(spec)
        assert "cmd" in hint
