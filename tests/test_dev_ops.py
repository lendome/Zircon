import sys
from pathlib import Path

import pytest

from zirconAgent.core.profiling import (
    build_profile_command,
    detect_profiler,
    format_hotspots,
    parse_cprofile,
    parse_pprof_top,
    unsupported_guidance,
    Hotspot,
)
from zirconAgent.tools.dev_ops import RunTaskTool, VerifyDeterminismTool, RunProfilerTool


def _py() -> str:
    """sys.executable in a form the pinned shell can execute.

    Backslashes are escape characters in bash — a Windows path like
    C:\\Users\\... is mangled under Git Bash. Forward slashes work in
    bash, PowerShell, AND cmd, so always normalize.
    """
    return Path(sys.executable).as_posix()


# ---------------------------------------------------------------------------
# run_task
# ---------------------------------------------------------------------------


class TestRunTask:
    @pytest.mark.asyncio
    async def test_capture_sections_and_exit_code(self, tmp_path):
        tool = RunTaskTool(str(tmp_path))
        result = await tool.run(command="echo hello-task")
        assert "hello-task" in result
        assert "Exit code: 0" in result

    @pytest.mark.asyncio
    async def test_save_output_to_writes_file_with_lf(self, tmp_path):
        tool = RunTaskTool(str(tmp_path))
        result = await tool.run(
            command="echo line1 && echo line2" if _is_cmd() else "printf 'line1\nline2\n'",
            save_output_to="golden.txt",
        )
        saved = tmp_path / "golden.txt"
        assert saved.exists()
        raw = saved.read_bytes()
        assert b"\r\n" not in raw
        assert b"line1" in raw and b"line2" in raw
        assert "saved_to:" in result

    @pytest.mark.asyncio
    async def test_save_output_to_rejects_escape(self, tmp_path):
        tool = RunTaskTool(str(tmp_path))
        result = await tool.run(command="echo hi", save_output_to="../evil.txt")
        assert "Error" in result
        assert "inside the repo" in result

    @pytest.mark.asyncio
    async def test_capture_flags(self, tmp_path):
        tool = RunTaskTool(str(tmp_path))
        result = await tool.run(command="echo visible", capture_stdout=False)
        assert "visible" not in result
        assert "Exit code: 0" in result

    @pytest.mark.asyncio
    async def test_invalid_cwd(self, tmp_path):
        tool = RunTaskTool(str(tmp_path))
        result = await tool.run(command="echo hi", cwd="nope")
        assert "Error" in result


def _is_cmd() -> bool:
    from zirconAgent.core.shell_env import resolve_shell
    return resolve_shell(True).kind == "cmd"


# ---------------------------------------------------------------------------
# verify_determinism
# ---------------------------------------------------------------------------


class TestVerifyDeterminism:
    @pytest.mark.asyncio
    async def test_stable_command_is_deterministic(self, tmp_path):
        tool = VerifyDeterminismTool(str(tmp_path))
        result = await tool.run(command=f'{_py()} -c "print(42)"', runs=2)
        assert "DETERMINISTIC" in result
        assert "2/2" in result

    @pytest.mark.asyncio
    async def test_unstable_command_reports_first_diff(self, tmp_path):
        tool = VerifyDeterminismTool(str(tmp_path))
        result = await tool.run(
            command=f'{_py()} -c "import random; print(random.random())"',
            runs=2,
        )
        assert "NON-DETERMINISTIC" in result
        assert "First difference" in result or "line 1" in result

    @pytest.mark.asyncio
    async def test_runs_clamped(self, tmp_path):
        tool = VerifyDeterminismTool(str(tmp_path))
        # runs=1 must clamp up to 2 and still compare
        result = await tool.run(command=f'{_py()} -c "print(1)"', runs=1)
        assert "DETERMINISTIC" in result or "NON-DETERMINISTIC" in result

    @pytest.mark.asyncio
    async def test_exit_code_instability_reported(self, tmp_path):
        tool = VerifyDeterminismTool(str(tmp_path))
        script = tmp_path / "flaky.py"
        marker = tmp_path / "marker.txt"
        script.write_text(
            "import sys, pathlib\n"
            f"m = pathlib.Path(r'{marker}')\n"
            "if m.exists():\n"
            "    sys.exit(0)\n"
            "m.write_text('x')\n"
            "sys.exit(1)\n"
        )
        result = await tool.run(command=f'{_py()} flaky.py', runs=2)
        assert "NON-DETERMINISTIC" in result
        assert "exit codes" in result


# ---------------------------------------------------------------------------
# run_profiler — detection/rewriting (pure) + cProfile end-to-end
# ---------------------------------------------------------------------------


class TestProfilerDetection:
    def test_python_detected(self):
        assert detect_profiler("python script.py --flag") == "cprofile"
        assert detect_profiler("python3 x.py") == "cprofile"

    def test_node_detected(self):
        assert detect_profiler("node app.js") == "node"

    def test_go_test_detected(self):
        assert detect_profiler("go test -bench=. .") == "go"

    def test_unsupported_shapes(self):
        assert detect_profiler("go run .") is None
        assert detect_profiler("./disasm.exe -js vm.js") is None
        assert detect_profiler("pytest -q") is None
        assert detect_profiler("") is None

    def test_unsupported_guidance_mentions_recipe(self):
        msg = unsupported_guidance("go run . -js vm.js")
        assert "bench_test.go" in msg
        assert "cProfile" in msg

    def test_build_go_plan_inserts_flags(self, tmp_path):
        plan = build_profile_command("go test -run=^$ .", "go", tmp_path)
        assert plan is not None
        assert "-cpuprofile=" in plan.command
        assert "-memprofile=" in plan.command
        assert "-mutexprofile=" in plan.command
        assert plan.extra_profiles["memory"].endswith("mem.out")

    def test_format_hotspots_renders_table(self):
        hotspots = [Hotspot(function="main.hot", location="x.py:10", self_time=1.5, cum_time=2.0, percent=75.0)]
        text = format_hotspots("Top 1:", hotspots, command="python x.py")
        assert "main.hot" in text
        assert "x.py:10" in text
        assert "75.0%" in text

    def test_parse_pprof_top(self):
        sample = (
            "      flat  flat%   sum%        cum   cum%\n"
            "     1.50s 75.00% 75.00%     2.00s 100.00%  main.hotLoop\n"
            "     0.20s 10.00% 85.00%     0.30s 15.00%  runtime.gc\n"
        )
        hotspots = parse_pprof_top(sample, 5)
        assert len(hotspots) == 2
        assert hotspots[0].function == "main.hotLoop"
        assert abs(hotspots[0].self_time - 1.5) < 1e-6

    @pytest.mark.asyncio
    async def test_cprofile_end_to_end(self, tmp_path):
        script = tmp_path / "hot.py"
        script.write_text(
            "def spin():\n"
            "    total = 0\n"
            "    for i in range(200000):\n"
            "        total += i * i\n"
            "    return total\n"
            "\n"
            "def main():\n"
            "    spin()\n"
            "\n"
            "main()\n"
        )
        tool = RunProfilerTool(str(tmp_path))
        result = await tool.run(command=f"{_py()} hot.py", timeout=60)
        assert "cProfile" in result
        assert "spin" in result

    @pytest.mark.asyncio
    async def test_unsupported_command_returns_guidance(self, tmp_path):
        tool = RunProfilerTool(str(tmp_path))
        result = await tool.run(command="echo hello")
        assert "does not support" in result
        assert "verify_determinism" in result

    def test_parse_cprofile_on_real_profile(self, tmp_path):
        import cProfile
        prof = tmp_path / "x.out"

        def work():
            return sum(range(10000))

        cProfile.runctx("work()", {"work": work}, {}, str(prof))
        hotspots = parse_cprofile(str(prof), 5)
        assert hotspots
        assert any("work" in h.function for h in hotspots)
