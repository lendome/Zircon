import pytest
from zirconAgent.core.context import ContextManager, estimate_tokens
from zirconAgent.core.types import Plan, PlanStep

@pytest.fixture
def ctx(tmp_path):
    return ContextManager(tmp_path, context_window=32000, safety_margin=400)

TOKEN_LENGTHS = list(range(0, 2001, 4))

@pytest.mark.parametrize("length", TOKEN_LENGTHS, ids=[f"tok_{i}" for i in range(len(TOKEN_LENGTHS))])
def test_estimate_tokens(length):
    text = "x" * length
    result = estimate_tokens(text)
    assert result == max(1, length // 4)

@pytest.mark.parametrize("num_files", list(range(1, 51)), ids=[f"rmap_{i}" for i in range(50)])
def test_repo_map_sizes(tmp_path, num_files):
    ctx = ContextManager(tmp_path, context_window=32000, safety_margin=400)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(num_files):
        (src / f"f{i}.py").write_text(f"def func{i}():\n    return {i}\n")
    ctx.build_repo_map()
    assert ctx.repo_map_built
    assert len(ctx.repo_map) == num_files

@pytest.mark.parametrize("max_size", [1, 2, 3, 5, 10, 15, 20, 25, 29, 30])
@pytest.mark.parametrize("num_files", [5, 10, 30, 50, 100])
def test_lru_eviction(tmp_path, max_size, num_files):
    from zirconAgent.core.context import LRUSet
    ws = LRUSet(max_size=max_size)
    for i in range(num_files):
        ws[f"f{i}.py"] = f"content_{i}"
    assert len(ws) == min(max_size, num_files)
    if num_files > max_size:
        assert "f0.py" not in ws
        assert f"f{num_files - 1}.py" in ws

CW_SIZES = [512, 1024, 2048, 4096, 8192, 16000, 32000, 64000, 128000, 200000]
SAFETY_MARGINS = [50, 100, 200, 400, 800]

@pytest.mark.parametrize("cw", CW_SIZES, ids=[f"cw_{i}" for i in range(len(CW_SIZES))])
@pytest.mark.parametrize("safety", SAFETY_MARGINS, ids=[f"sm_{i}" for i in range(len(SAFETY_MARGINS))])
def test_build_messages_within_budget(tmp_path, cw, safety):
    ctx = ContextManager(tmp_path, context_window=cw, safety_margin=safety)
    ctx.set_task("test")
    msgs = ctx.build_messages("system")
    total = sum(estimate_tokens(m["content"]) for m in msgs)
    assert total <= cw

LEARNINGS = [f"Learning {i}: prefer approach {i % 5} for {['testing','linting','formatting','debugging','refactoring'][i % 5]}" for i in range(100)]

@pytest.mark.parametrize("learning", LEARNINGS, ids=[f"epmem_{i}" for i in range(len(LEARNINGS))])
def test_episodic_memory(ctx, learning):
    ctx.save_episodic_memory(learning)
    assert learning in ctx.episodic_memory

LONG_OUTPUTS = [("x" * ((i + 1) * 100), "line") for i in range(100)]

@pytest.mark.parametrize("output,focus", LONG_OUTPUTS, ids=[f"distill_{i}" for i in range(len(LONG_OUTPUTS))])
def test_distill_outputs(ctx, output, focus):
    r = ctx.distill_observation(output, focus)
    assert isinstance(r, str)
    assert len(r) <= len(output)

PLAN_STEPS = [(n_steps, complexity) for n_steps in range(1, 16) for complexity in ["simple", "moderate", "complex"]]

@pytest.mark.parametrize("n_steps,complexity", PLAN_STEPS, ids=[f"plan_{i}" for i in range(len(PLAN_STEPS))])
def test_context_with_plans(tmp_path, n_steps, complexity):
    ctx = ContextManager(tmp_path, context_window=32000, safety_margin=400)
    ctx.set_task("test")
    steps = [PlanStep(index=i, description=f"step {i}", action="edit") for i in range(n_steps)]
    plan = Plan(steps=steps, complexity=complexity)
    ctx.set_plan(plan)
    msgs = ctx.build_messages("system")
    all_content = " ".join(m["content"] for m in msgs)
    assert "step 0" in all_content

WORKING_SET_SIZES = [(total, max_ws) for total in [5, 10, 20, 30, 50] for max_ws in [5, 10, 30]]

@pytest.mark.parametrize("total,max_ws", WORKING_SET_SIZES, ids=[f"ws_{i}" for i in range(len(WORKING_SET_SIZES))])
def test_working_set_management(tmp_path, total, max_ws):
    ctx = ContextManager(tmp_path, context_window=32000, safety_margin=400)
    ctx.working_set = type(ctx.working_set)(max_size=max_ws)
    for i in range(total):
        ctx.add_file_to_working_set(f"f{i}.py", f"content {i}")
    assert len(ctx.working_set) == min(max_ws, total)
