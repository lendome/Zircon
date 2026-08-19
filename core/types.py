from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from typing import Any


class TaskStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


class CompletionDisposition(str, Enum):
    """Why the tool loop terminated. Drives honest status reporting.

    - VERIFIED: model declared completion AND required evidence was observed.
    - DECLARED: model declared completion; no specific evidence required
      (e.g. read-only Q&A, or evidence category satisfied).
    - INCOMPLETE: model tried to stop but a required evidence category was
      missing; the loop was continued once and then terminated honestly.
    - BLOCKED: a command failed or a server URL was unreachable and the model
      could not resolve it.
    - TURN_LIMIT: the configured tool-turn budget was reached before the task
      finished. Distinct from success.
    """  # noqa: D205
    VERIFIED = "verified"
    DECLARED = "declared"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"
    TURN_LIMIT = "turn_limit"


class Tier(str, Enum):
    LOW = "low"
    BALANCED = "balanced"
    QUALITY = "quality"


@dataclass
class PlanStep:
    index: int
    description: str
    action: str
    target_files: list[str] = field(default_factory=list)

    @staticmethod
    def from_dict(d: dict) -> PlanStep:
        return PlanStep(
            index=d.get("index", 0),
            description=d.get("description", ""),
            action=d.get("action", "explore"),
            target_files=d.get("target_files", []),
        )


@dataclass
class Plan:
    steps: list[PlanStep]
    files_likely_needed: list[str] = field(default_factory=list)
    complexity: str = "moderate"
    spirit: dict[str, Any] | None = None
    """Optional "spirit of the prompt" reasoning captured from the planner's
    JSON output (literal_request / underlying_intent / cheap_ways_out). None
    for plans produced without the field (legacy or lightweight plans)."""

    @staticmethod
    def from_dict(d: dict) -> Plan:
        steps = [PlanStep.from_dict(s) for s in d.get("steps", [])]
        spirit = d.get("spirit")
        return Plan(
            steps=steps,
            files_likely_needed=d.get("files_likely_needed", []),
            complexity=d.get("complexity", "moderate"),
            spirit=spirit if isinstance(spirit, dict) else None,
        )


class StepAction(str, Enum):
    EXPLORE = "explore"
    EDIT = "edit"
    VERIFY = "verify"
    RESEARCH = "research"


@dataclass
class PlanDecision:
    needs_plan: bool
    reason: str


@dataclass
class SubAgentProgress:
    """Progress event emitted by sub-agents during execution."""
    agent_id: str
    agent_type: str
    status: str = "running"
    phase: str = ""
    step: int = 0
    total_steps: int = 0
    turn: int = 0
    detail: str = ""
    progress_label: str = ""
    files_modified: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)


@dataclass
class SubAgentResult:
    success: bool
    output: str
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    step_index: int = 0


@dataclass
class EditResult:
    success: bool
    matcher: str = ""
    error: str = ""
    new_content: str = ""


@dataclass
class TraceEvent:
    phase: str
    detail: str
    payload: Any = None
    progress_label: str = ""


@dataclass
class PlanInstruction:
    """A plan that acts as a user instruction, not an execution script.
    
    When approved, this is injected as context into the normal tool loop
    so the agent follows the steps naturally using its standard tools.
    """
    steps: list[PlanStep]
    files_likely_needed: list[str] = field(default_factory=list)
    complexity: str = "moderate"

    @staticmethod
    def from_plan(plan: Plan) -> PlanInstruction:
        return PlanInstruction(
            steps=plan.steps,
            files_likely_needed=plan.files_likely_needed,
            complexity=plan.complexity,
        )


@dataclass
class AgentResult:
    success: bool
    answer: str
    files_modified: list[str] = field(default_factory=list)
    trace: list[TraceEvent] = field(default_factory=list)
    tokens_used: int = 0
    status: TaskStatus = TaskStatus.COMPLETED
    pending_plan: Plan | None = None


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    usage: dict = field(default_factory=dict)
    done: bool = True
    reasoning_content: str = ""
    finish_reason: str = ""

    @property
    def input_tokens(self) -> int:
        return self.usage.get("prompt_tokens", 0)

    @property
    def output_tokens(self) -> int:
        return self.usage.get("completion_tokens", 0)

    @property
    def cost(self) -> float:
        """Provider-reported USD cost, when supplied by the API."""
        for key in ("cost", "total_cost", "total_cost_usd"):
            value = self.usage.get(key)
            try:
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    @property
    def truncated(self) -> bool:
        """True when the model stopped because it hit the max_tokens limit."""
        return (self.finish_reason or "").lower() in {
            "length",
            "max_tokens",
            "max_output_tokens",
            "token_limit",
        }


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolCallDelta:
    """Incremental (streamed) fragment of a tool call being assembled.

    Carried by a StreamChunk emitted mid-stream, before the tool call is
    complete. `index` identifies which parallel tool call is being built.
    `arguments` is the raw partial JSON string accumulated so far; it is NOT
    parsed until the stream completes.
    """
    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class StreamChunk:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] | None = None
    tool_call_delta: ToolCallDelta | None = None
    tool_result: str = ""
    done: bool = False
    model: str = ""
    usage: dict = field(default_factory=dict)
    error: str | None = None
    status: TaskStatus | None = None
    progress_label: str = ""
    advisor_feedback: str = ""
    """Mid-loop advisor memo emitted by the executor (Advisor-Agent pattern).
    Rendered as a permanent panel by the TUI; empty for all other chunks."""
    advisor_plan: str = ""
    """Initial advisor Execution Plan emitted once per fresh task/message.
    Rendered as a permanent panel by the TUI; empty for all other chunks."""
    finish_reason: str = ""
    disposition: CompletionDisposition | None = None
    evidence: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)


@dataclass
class ModelProfile:
    name: str
    base_url: str
    api_key: str
    model: str
    max_tokens: int = 32768
    context_window: int = 128000
    roles: list[str] = field(default_factory=list)
    timeout: float = 120.0
    # OpenRouter reasoning control. reasoning_effort: "low"|"medium"|"high"|
    # "xhigh"|"max" (model-dependent). reasoning_enabled: set False to turn
    # reasoning off entirely. None = defer to the model's native default.
    reasoning_effort: str | None = None
    reasoning_enabled: bool | None = None


@dataclass
class TierConfig:
    name: str
    context_window: int = 128000
    working_set_max_files: int = 15
    tokens_per_file: int = 500
    modified_file_tokens: int = 750
    repo_map_max_files: int = 60
    repo_map_detail: str = "standard"  # minimal, standard, full
    history_keep_exchanges: int = 8
    history_compact_threshold: int = 15000
    episodic_memory_count: int = 5
    kg_context_nodes: int = 5
    semantic_search_enabled: bool = False

    # --- Verifiable Feedback Loops (Syntax Checking) ---
    syntax_check_enabled: bool = True
    """Automatically check syntax after every file edit and feed errors back to LLM."""
    syntax_check_max_attempts: int = 3
    """Max times to auto-fix syntax before giving up."""
    syntax_route_minor_to_fast: bool = True
    """Route minor syntax errors to a cheap fast model."""

    # --- State & Rollback (Git Integration) ---
    git_auto_commit: bool = True
    """Auto-commit before every edit for rollback capability."""
    git_auto_rollback: bool = True
    """Rollback on critical loop failure or recovery exhaustion."""
    git_session_branches: bool = True
    """Create isolated session branches for each agent run."""

    # --- Prompt Caching ---
    prompt_caching_enabled: bool = False
    """Enable cache_control breakpoints for Anthropic-style prompt caching."""
    prompt_cache_type: str = "ephemeral"
    """Cache type: 'ephemeral' (Anthropic) or 'semantic' (custom key-based)."""
    prompt_cache_min_interval: int = 200
    """Minimum tokens between cache_control breakpoints."""

    # --- Error Routing ---
    error_routing_enabled: bool = True
    """Route minor errors to cheap fast models automatically."""
    error_routing_max_fast_attempts: int = 2
    """Max attempts using fast model before escalating to top-tier."""

    # --- Sandbox ---
    sandbox_enabled: bool = False
    """Enable Docker-based sandboxed execution."""
    sandbox_image: str = "python:3.11-slim"
    """Docker image for sandbox."""
    sandbox_timeout: int = 60
    """Default timeout for sandboxed commands."""
    sandbox_memory: str = "1g"
    """Memory limit for sandbox container."""
    sandbox_network: bool = False
    """Network access in sandbox (disabled by default)."""

    # --- Shell Environment ---
    shell_pinning_enabled: bool = True
    """Pin one detected shell per machine (Git Bash -> pwsh -> cmd on Windows)
    for run_command/shell_start/run_task instead of always using the platform
    default. The active shell is named in tool descriptions so the model
    writes matching syntax. Disable to restore legacy cmd.exe behavior."""

    # --- Command Circuit Breaker ---
    command_circuit_breaker_enabled: bool = True
    """Intercept byte-identical repeated commands that already failed: hard
    block for shell-syntax failures (deterministic), soft block for other
    consecutive failures without an intervening edit (every 3rd attempt still
    passes through). Applied at the registry so all agents are covered."""

    # --- Read-Call Deduplication ---
    read_dedup_enabled: bool = True
    """Hard-intercept exact-duplicate read-only tool calls (read_file,
    grep_code, glob_files, list_dir, find_symbols, get_structure, the nav
    tools) with a SYSTEM ERROR telling the agent to use its earlier output.
    Automatically invalidated by any successful mutation — a re-read after an
    edit always executes. Failed reads are never recorded, so retrying an
    error is always allowed. Applied at the registry."""

    # --- Edit Failure Circuit Breaker ---
    edit_failure_breaker_enabled: bool = True
    """Intercept a byte-identical edit resubmitted after it already failed
    twice in a row with no intervening mutation: the SEARCH text cannot match
    the file, so the agent is told to re-read the region instead of
    resubmitting. Any successful edit clears the state."""

    # --- Component Scope Guard ---
    scope_guard_mode: str = "warn"
    """When the user's task explicitly names a component ("the disassembler
    engine"), the Agent resolves that component's files and arms the scope
    guard on mutation tools. Modes: "warn" (default) lets outside-scope edits
    through with a warning prefix; "block" denies them; "off" disables the
    guard entirely. If the component resolves to zero files the guard stays
    disarmed, so it never blocks on a bad guess."""

    # --- Advisor Tool Vetoes ---
    advisor_veto_enabled: bool = True
    """Allow the mid-loop advisor to temporarily disable a mutation/search
    tool (whitelist only, never read/nav tools, never run_command) when the
    agent is thrashing with it. Vetoes auto-expire after a bounded number of
    turns and are subject to a cooldown before re-issue."""
    advisor_veto_max_turns: int = 3
    """Maximum turns a single advisor veto can disable a tool for."""

    max_tool_turns: int = 15
    subagent_enabled: bool = False
    """When False, plan steps are NOT dispatched to sub-agents.
    Instead, the plan is injected as an instruction into the main tool loop.
    """
    subagent_max_turns: int = 8
    research_max_turns: int = 20
    """Turn budget for web-research work (researcher subagent / research plan
    steps). Research tasks legitimately need more search-fetch-reason
    iterations than code steps — per published guidance, research agents
    warrant 15-20 searches while simple lookups need 1-3."""
    edit_repair_attempts: int = 2
    skip_final_verification: bool = False
    replanning_enabled: bool = True
    max_replans: int = 1
    skip_synthesis: bool = False
    synthesis_max_tokens: int = 512

    gatekeeper_mode: str = "hybrid"  # rule_only, hybrid, llm_only
    planner_max_tokens: int = 1024
    gatekeeper_max_tokens: int = 128
    default_max_tokens: int = 4096
    skip_planner: bool = False
    lightweight_plan_max_steps: int = 0

    history_distill: str = "tiered"  # ultra, tiered, gradual

    chain_of_draft: bool = False
    reflection_loop: bool = False
    reflection_min_files: int = 2
    multi_sample_consensus: bool = False
    multi_sample_n: int = 1
    diff_working_set: bool = False

    loop_detection_window: int = 8
    loop_max_repetitions: int = 3
    loop_stagnation_threshold: int = 6
    loop_read_only_warn_turns: int = 6
    loop_same_file_reread_warn: int = 4
    loop_consecutive_chunk_warning: int = 3
    loop_consecutive_chunk_critical: int = 5

    # --- Trajectory Reduction (AgentDiet) ---
    trajectory_pruning_enabled: bool = True
    """Compress older tool-result messages in the active conversation when it
    approaches the context budget. A no-op while well under budget, so it is
    unobtrusive for short sessions."""
    trajectory_prune_threshold_fraction: float = 0.6
    """Fraction of the context window at which trajectory pruning activates."""
    trajectory_protected_turns: int = 3
    """Most recent turns kept verbatim; only older turns are compressed."""
    trajectory_min_messages: int = 8
    """Minimum active-message count before pruning is even considered."""

    # --- In-Loop Context Window Guard ---
    context_guard_enabled: bool = True
    """Keep the ACTIVE tool-loop conversation under the model's real context
    limit: compact older turns (LLM summary) once the estimated request size
    crosses the soft threshold, and stub stale tool output at the hard
    ceiling. Prevents the loop from dying mid-task on a provider
    'context length exceeded' error (which used to surface as the agent
    stopping for no reason and resuming out of context)."""
    context_guard_soft_fraction: float = 0.70
    """Fraction of the context window at which in-loop compaction triggers."""
    context_guard_hard_fraction: float = 0.92
    """Fraction of the context window at which stale tool results are stubbed
    outright (last resort; pairing is always preserved)."""
    context_guard_protected_turns: int = 4
    """Most recent turns never compacted/stubbed by the in-loop guard."""

    recovery_attempts_max: int = 0
    recovery_prompt_after_exhausted: bool = False

    system_prompt_style: str = "balanced"  # minimal, balanced, thorough

    reasoning_effort: str = "medium"
    """OpenRouter-style reasoning effort: max, xhigh, high, medium, low, minimal, none."""

    swarm_mode: bool = False
    max_parallel_agents: int = 3
    swarm_agent_timeout: float = 300.0  # seconds before a swarm agent is considered stuck
    max_artifacts_per_track: int = 5
    artifact_max_tokens: int = 10000

    # --- Advisor-Agent Pattern ---
    advisor_enabled: bool = False
    """When True, a large 'advisor' model generates a structured Execution Plan
    note before the tool loop, and the worker model executes with strict
    adherence. Runs once per fresh task; failures degrade to no guidance."""
    advisor_max_tokens: int = 2048
    """Token budget for the advisor call — the plan should be concise."""
    advisor_context_max_chars: int = 4000
    """Trim applied to repo-map text passed to the advisor as project context."""
    advisor_checkin_interval: int = 10
    """Every N tool-loop turns the advisor reviews a digest of the recent
    trajectory and injects a feedback memo (approvals, criticisms, ideas,
    must-fix items) mid-loop. 0 disables check-ins."""

    # --- Plans Disabled Mode ---
    plans_disabled: bool = True
    """When True (default), planning is completely bypassed: the gatekeeper always returns 'no plan',
    and the planner is never invoked. All tasks go directly to the tool loop.
    Set to False to re-enable planning (e.g. via --plan-mode flag)."""
    plan_disabled_turns_multiplier: int = 3
    """Multiplier applied to max_tool_turns when plans_disabled=True, to compensate
    for the lack of structured planning."""

    def effective_max_tool_turns(self) -> int:
        base = self.max_tool_turns
        if self.plans_disabled:
            base *= max(1, self.plan_disabled_turns_multiplier)
        return base

    def effective_subagent_max_turns(self) -> int:
        return self.subagent_max_turns

    def effective_research_max_turns(self) -> int:
        return self.research_max_turns


TIER_PRESETS: dict[Tier, TierConfig] = {
    Tier.LOW: TierConfig(
        name="low",
        context_window=32000,
        subagent_enabled=False,
        working_set_max_files=8,
        tokens_per_file=300,
        modified_file_tokens=300,
        repo_map_max_files=30,
        repo_map_detail="minimal",
        history_keep_exchanges=4,
        history_compact_threshold=999999,
        episodic_memory_count=0,
        kg_context_nodes=0,
        semantic_search_enabled=False,
        # 4x tool budget
        max_tool_turns=32,
        subagent_max_turns=20,
        research_max_turns=10,
        edit_repair_attempts=4,
        skip_final_verification=True,
        replanning_enabled=False,
        max_replans=0,
        skip_synthesis=True,
        synthesis_max_tokens=0,
        gatekeeper_mode="rule_only",
        planner_max_tokens=2048,
        gatekeeper_max_tokens=256,
        default_max_tokens=4096,
        skip_planner=True,
        lightweight_plan_max_steps=12,
        history_distill="ultra",
        chain_of_draft=False,
        reflection_loop=False,
        multi_sample_consensus=False,
        system_prompt_style="minimal",
        loop_detection_window=24,
        loop_max_repetitions=3,
        loop_stagnation_threshold=4,
        loop_read_only_warn_turns=8,
        loop_same_file_reread_warn=5,
        loop_consecutive_chunk_warning=3,
        loop_consecutive_chunk_critical=4,
        recovery_attempts_max=0,
        recovery_prompt_after_exhausted=False,
        swarm_mode=False,
        max_parallel_agents=2,
        swarm_agent_timeout=120.0,
        syntax_check_enabled=True,
        syntax_check_max_attempts=1,
        syntax_route_minor_to_fast=True,
        git_auto_commit=False,
        git_auto_rollback=False,
        git_session_branches=False,
        prompt_caching_enabled=False,
        error_routing_enabled=True,
        error_routing_max_fast_attempts=2,
        sandbox_enabled=False,
    ),
    Tier.BALANCED: TierConfig(
        name="balanced",
        context_window=128000,
        subagent_enabled=False,
        working_set_max_files=15,
        tokens_per_file=500,
        modified_file_tokens=750,
        repo_map_max_files=60,
        repo_map_detail="standard",
        history_keep_exchanges=8,
        history_compact_threshold=50000,
        episodic_memory_count=5,
        kg_context_nodes=5,
        semantic_search_enabled=False,
        # 4x tool budget
        max_tool_turns=60,
        subagent_max_turns=32,
        research_max_turns=20,
        edit_repair_attempts=8,
        skip_final_verification=False,
        replanning_enabled=True,
        max_replans=1,
        skip_synthesis=False,
        synthesis_max_tokens=512,
        gatekeeper_mode="hybrid",
        planner_max_tokens=4096,
        gatekeeper_max_tokens=512,
        default_max_tokens=32768,
        skip_planner=False,
        history_distill="tiered",
        chain_of_draft=False,
        reflection_loop=False,
        multi_sample_consensus=False,
        system_prompt_style="balanced",
        loop_detection_window=32,
        loop_max_repetitions=3,
        loop_stagnation_threshold=5,
        loop_read_only_warn_turns=6,
        loop_same_file_reread_warn=4,
        loop_consecutive_chunk_warning=3,
        loop_consecutive_chunk_critical=5,
        recovery_attempts_max=12,
        recovery_prompt_after_exhausted=True,
        swarm_mode=False,
        max_parallel_agents=3,
        swarm_agent_timeout=300.0,
        syntax_check_enabled=True,
        syntax_check_max_attempts=2,
        syntax_route_minor_to_fast=True,
        git_auto_commit=True,
        git_auto_rollback=True,
        git_session_branches=True,
        prompt_caching_enabled=True,
        error_routing_enabled=True,
        error_routing_max_fast_attempts=2,
        sandbox_enabled=False,
    ),
    Tier.QUALITY: TierConfig(
        name="quality",
        context_window=256000,
        subagent_enabled=False,
        working_set_max_files=30,
        tokens_per_file=1500,
        modified_file_tokens=2000,
        repo_map_max_files=120,
        repo_map_detail="full",
        history_keep_exchanges=16,
        history_compact_threshold=75000,
        episodic_memory_count=50,
        kg_context_nodes=20,
        semantic_search_enabled=True,
        # 4x tool budget
        max_tool_turns=120,
        subagent_max_turns=60,
        research_max_turns=30,
        edit_repair_attempts=20,
        skip_final_verification=False,
        replanning_enabled=True,
        max_replans=3,
        skip_synthesis=False,
        synthesis_max_tokens=1024,
        gatekeeper_mode="llm_only",
        planner_max_tokens=8192,
        gatekeeper_max_tokens=1024,
        default_max_tokens=32768,
        skip_planner=False,
        history_distill="gradual",
        chain_of_draft=True,
        reflection_loop=True,
        reflection_min_files=2,
        multi_sample_consensus=True,
        multi_sample_n=12,
        diff_working_set=False,
        system_prompt_style="thorough",
        loop_detection_window=24,
        loop_max_repetitions=3,
        loop_stagnation_threshold=4,
        recovery_attempts_max=12,
        recovery_prompt_after_exhausted=True,
        swarm_mode=False,
        max_parallel_agents=4,
        swarm_agent_timeout=600.0,
        max_artifacts_per_track=10,
        artifact_max_tokens=20000,
        syntax_check_enabled=True,
        syntax_check_max_attempts=3,
        syntax_route_minor_to_fast=True,
        git_auto_commit=True,
        git_auto_rollback=True,
        git_session_branches=True,
        prompt_caching_enabled=True,
        error_routing_enabled=True,
        error_routing_max_fast_attempts=2,
        sandbox_enabled=True,
        sandbox_image="python:3.11-slim",
        sandbox_timeout=120,
        sandbox_memory="2g",
        sandbox_network=False,
        advisor_enabled=True,
    ),
}


@dataclass
class SwarmRole:
    id: str
    description: str
    dependencies: list[str] = field(default_factory=list)


@dataclass
class SwarmTrack:
    id: str
    role: str
    steps: list[PlanStep]
    context: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)


@dataclass
class SwarmPlan:
    tracks: list[SwarmTrack]
    coordinator_role: str = "coordinator"


@dataclass
class AgentMessage:
    sender_id: str
    recipient_id: str
    msg_type: str  # "artifact", "status_update", "request_review", "feedback"
    payload: Any = None


@dataclass
class SwarmAgentStatus:
    agent_id: str
    status: TaskStatus
    progress: float = 0.0
    artifacts_produced: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class SwarmResult:
    success: bool
    track_results: dict[str, SubAgentResult] = field(default_factory=dict)
    merged_artifacts: dict[str, Any] = field(default_factory=dict)
    trace: list[TraceEvent] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)


# ==========================================================================
# Hierarchical Fault Localization (FL) result types
#
# Produced by a strict top-down localization pipeline that runs BEFORE the
# active repair agent loop, so the repair agent receives a surgical context
# snippet instead of a whole codebase to explore.
# ==========================================================================


@dataclass
class FileTarget:
    """Phase 1 result — a file ranked relevant to the bug report."""
    path: str
    score: float
    source: str = "fusion"  # bm25 | embed | fusion
    reason: str = ""


@dataclass
class FunctionSuspect:
    """Phase 2 result — a class/function identified as likely to contain the bug."""
    file: str
    symbol: str
    kind: str = "function"  # function | method | class | module
    line: int = 0
    end_line: int = 0
    args: list[str] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0


@dataclass
class LineWindow:
    """Phase 3 result — the exact line range believed to contain the bug."""
    file: str
    symbol: str
    start_line: int = 0
    end_line: int = 0
    rationale: str = ""
    confidence: float = 0.0


@dataclass
class LocalizationResult:
    """Full output of the hierarchical fault-localization pipeline."""
    bug_report: str
    targets: list[FileTarget] = field(default_factory=list)
    suspects: list[FunctionSuspect] = field(default_factory=list)
    windows: list[LineWindow] = field(default_factory=list)
    primary_window: LineWindow | None = None
    snippet: str = ""
    used_embeddings: bool = False

    @property
    def ok(self) -> bool:
        return self.primary_window is not None or bool(self.snippet)
