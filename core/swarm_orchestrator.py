from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import Agent
from .artifact_registry import ArtifactRegistry
from .constants import ZIRCON_DIR, ensure_zircon_dir
from .swarm_plan_builder import SwarmPlanBuilder
from .types import (
    AgentResult,
    Plan,
    PlanStep,
    SubAgentProgress,
    SubAgentResult,
    SwarmPlan,
    SwarmResult,
    SwarmTrack,
    TaskStatus,
    Tier,
    TierConfig,
    TraceEvent,
    TIER_PRESETS,
)
from ..llm.prompts import (
    SYSTEM_SWARM_COORDINATOR,
    SYSTEM_AGENT_BALANCED,
)
from ..llm.router import ModelRouter
from ..tools.registry import ToolRegistry

logger = logging.getLogger("agent.swarm_orchestrator")

ProgressCallback = Callable[[SubAgentProgress], None]


@dataclass
class TrackRunState:
    track: SwarmTrack
    agent: Agent | None = None
    task: str = ""
    result: SubAgentResult | None = None
    error: str | None = None
    completed: bool = False
    started_at: float = 0.0
    finished_at: float = 0.0
    files_modified: list[str] = field(default_factory=list)


class SwarmOrchestrator:
    def __init__(
        self,
        repo_path: str | Path,
        router: ModelRouter,
        registry: ToolRegistry,
        tier_config: TierConfig | None = None,
        main_agent: Agent | None = None,
        progress_callback: ProgressCallback | None = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.router = router
        self.registry = registry
        self.tier = tier_config or TIER_PRESETS[Tier.BALANCED]
        self.main_agent = main_agent
        self._progress_callback = progress_callback

        self.artifact_registry = ArtifactRegistry(self.repo_path)
        self.swarm_builder = SwarmPlanBuilder(router, tier_config)
        self._trace: list[TraceEvent] = []
        self._all_modified_files: set[str] = set()

    def _emit_progress(self, agent_id: str, agent_type: str, phase: str, detail: str = "",
                       step: int = 0, total_steps: int = 1, **kw):
        if self._progress_callback:
            self._progress_callback(SubAgentProgress(
                agent_id=agent_id,
                agent_type=agent_type,
                status=TaskStatus.RUNNING,
                phase=phase,
                detail=detail,
                step=step,
                total_steps=total_steps,
                **kw,
            ))

    async def orchestrate(self, task: str, repo_map_text: str = "") -> SwarmResult:
        logger.info("Swarm orchestration starting: %s", task[:100])

        self._emit_progress("orchestrator", "coordinator", "start",
                            f"Swarm orchestrator starting for: {task[:100]}",
                            total_steps=1)

        self._trace.append(TraceEvent(phase="swarm_decompose", detail="Decomposing task into parallel tracks"))
        swarm_plan = await self.swarm_builder.decompose(task, repo_map_text)

        if len(swarm_plan.tracks) <= 1:
            logger.info("Swarm plan has only 1 track — running as single agent")
            self._emit_progress("orchestrator", "coordinator", "single_track",
                                "Only 1 track — running as single agent")
            return await self._run_single_agent(task, swarm_plan)

        self._trace.append(TraceEvent(
            phase="swarm_plan",
            detail=f"Decomposed into {len(swarm_plan.tracks)} tracks",
            payload={"tracks": [{"id": t.id, "role": t.role, "steps": len(t.steps), "deps": t.dependencies} for t in swarm_plan.tracks]},
        ))
        self._emit_progress("orchestrator", "coordinator", "decomposed",
                            f"Decomposed into {len(swarm_plan.tracks)} tracks",
                            total_steps=len(swarm_plan.tracks))

        layers = await self.swarm_builder.dependent_tracks(swarm_plan)
        self._trace.append(TraceEvent(
            phase="swarm_layers",
            detail=f"Topological sort: {len(layers)} layer(s)",
            payload={"layers": [[t.id for t in layer] for layer in layers]},
        ))

        all_track_states: dict[str, TrackRunState] = {}
        for layer_idx, layer in enumerate(layers):
            run_states: list[TrackRunState] = []
            for track in layer:
                state = TrackRunState(track=track)
                state.task = self._track_task_description(track, task)
                state.agent = self._create_agent_for_track(track)
                run_states.append(state)
                all_track_states[track.id] = state

            self._trace.append(TraceEvent(
                phase="swarm_layer_start",
                detail=f"Layer {layer_idx + 1}/{len(layers)}: {', '.join(t.id for t in layer)}",
                payload={"layer_index": layer_idx, "tracks": [t.id for t in layer]},
            ))
            self._emit_progress("orchestrator", "coordinator", "layer_start",
                                f"Layer {layer_idx + 1}/{len(layers)} starting",
                                step=layer_idx, total_steps=len(layers))

            track_tasks = [asyncio.create_task(self._run_track(state)) for state in run_states]

            done, pending = await asyncio.wait(
                track_tasks,
                timeout=self.tier.swarm_agent_timeout,
                return_when=asyncio.ALL_COMPLETED,
            )

            for task_obj in pending:
                task_obj.cancel()
                try:
                    await task_obj
                except (asyncio.CancelledError, Exception):
                    pass
            for state in run_states:
                if not state.completed and not state.error:
                    state.error = "Timeout: track exceeded max execution time"
                    self._trace.append(TraceEvent(
                        phase="swarm_timeout",
                        detail=f"Track {state.track.id} timed out after {self.tier.swarm_agent_timeout}s",
                    ))
                    self._emit_progress(state.track.id, "swarm_track", "timeout",
                                        f"Track {state.track.id} timed out",
                                        step=layer_idx, total_steps=len(layers))

            self._trace.append(TraceEvent(
                phase="swarm_layer_end",
                detail=f"Layer {layer_idx + 1} done: {sum(1 for s in run_states if s.completed)}/{len(run_states)} tracks completed",
            ))

            critical_track_ids = {swarm_plan.coordinator_role}
            for t in swarm_plan.tracks:
                for s in run_states:
                    if s.track.id == t.id and s.error:
                        for later_track in swarm_plan.tracks:
                            if t.id in later_track.dependencies:
                                critical_track_ids.add(t.id)

            failed_critical = [
                s for s in run_states
                if s.error and s.track.id in critical_track_ids
            ]
            if failed_critical:
                for s in failed_critical:
                    self._trace.append(TraceEvent(
                        phase="swarm_failure",
                        detail=f"Critical track {s.track.id} failed: {s.error}",
                        payload={"error": s.error},
                    ))
                    self._emit_progress(s.track.id, "swarm_track", "critical_fail",
                                        f"Critical track {s.track.id} failed: {s.error[:100]}")
                remaining = [
                    t for layer in layers[layer_idx + 1:]
                    for t in layer
                    if any(f.track.id in t.dependencies for f in failed_critical)
                ]
                if remaining:
                    self._trace.append(TraceEvent(
                        phase="swarm_skip",
                        detail=f"Skipping {len(remaining)} dependent track(s) due to critical failure",
                        payload={"skipped": [t.id for t in remaining]},
                    ))

            self._all_modified_files.update(
                f for s in run_states for f in s.files_modified
            )

        if len(swarm_plan.tracks) > 1:
            self._emit_progress("orchestrator", "coordinator", "integration",
                                "Running integration step",
                                step=len(layers), total_steps=len(layers))
            self._trace.append(TraceEvent(phase="swarm_integrate", detail="Running integration step"))
            integration_result = await self._run_integration(task, swarm_plan, all_track_states)
            self._all_modified_files.update(integration_result.files_modified)

        if self._all_modified_files and not self.tier.skip_final_verification:
            self._emit_progress("orchestrator", "coordinator", "verification",
                                "Running final verification",
                                step=len(layers) + 1, total_steps=len(layers) + 1)
            self._trace.append(TraceEvent(phase="swarm_verify", detail="Running final verification across all tracks"))
            verify_result = await self._run_final_verification()
            if not verify_result.success:
                self._trace.append(TraceEvent(
                    phase="swarm_verify_failed",
                    detail="Final verification failed",
                    payload={"output": verify_result.output[:500]},
                ))

        track_results = {}
        for tid, state in all_track_states.items():
            if state.result:
                track_results[tid] = state.result
            elif state.error:
                track_results[tid] = SubAgentResult(success=False, output=state.error)

        merged_artifacts = await self.artifact_registry.list_all()

        overall_success = all(
            r.success if r else False
            for r in track_results.values()
        ) if track_results else False

        self._trace.append(TraceEvent(
            phase="swarm_complete",
            detail=f"Swarm completed: {'success' if overall_success else 'partial failure'}",
            payload={
                "total_tracks": len(swarm_plan.tracks),
                "completed": sum(1 for r in track_results.values() if r and r.success),
                "failed": sum(1 for r in track_results.values() if r and not r.success),
                "files_modified": len(self._all_modified_files),
            },
        ))
        self._emit_progress("orchestrator", "coordinator", "complete",
                            f"Swarm completed: {'success' if overall_success else 'partial failure'}",
                            step=len(layers) + 2, total_steps=len(layers) + 2)

        await self.artifact_registry.save_snapshot()

        return SwarmResult(
            success=overall_success,
            track_results=track_results,
            merged_artifacts=merged_artifacts,
            trace=self._trace,
            files_modified=list(self._all_modified_files),
        )


    def _track_task_description(self, track: SwarmTrack, global_task: str) -> str:
        parts = [f"Overall task: {global_task}"]
        parts.append(f"Your role: {track.role}")
        parts.append(f"Track ID: {track.id}")
        if track.dependencies:
            deps = ", ".join(track.dependencies)
            parts.append(f"Dependencies (wait for these artifacts before starting): {deps}")
        parts.append("")
        parts.append(f"Execute the following steps in order:")
        for step in track.steps:
            files = f" ({', '.join(step.target_files)})" if step.target_files else ""
            parts.append(f"  {step.index}. [{step.action}] {step.description}{files}")
        return "\n".join(parts)

    def _create_agent_for_track(self, track: SwarmTrack) -> "Agent":
        from .agent import Agent  # lazy import to avoid circular dependency
        agent = Agent(
            repo_path=self.repo_path,
            router_config=self.main_agent.router.config if self.main_agent else None,
            agent_config=self.main_agent.config if self.main_agent else None,
            tier=self.main_agent.tier if self.main_agent else Tier.BALANCED,
        )
        return agent

    async def _run_track(self, state: TrackRunState) -> None:
        state.started_at = asyncio.get_event_loop().time()
        agent_id = state.track.id

        self._emit_progress(agent_id, "swarm_track", "waiting_deps",
                            f"Track {agent_id}: waiting for dependencies",
                            step=0, total_steps=1)

        for dep_id in state.track.dependencies:
            dep_artifacts = await self.artifact_registry.wait_for(
                f"track:{dep_id}:complete",
                timeout=self.tier.swarm_agent_timeout / 2,
            )
            if dep_artifacts:
                logger.info("Track %s: dependency %s satisfied", agent_id, dep_id)
            else:
                logger.warning("Track %s: dependency %s timed out", agent_id, dep_id)

        try:
            track_task = state.task
            self._emit_progress(agent_id, "swarm_track", "running",
                                f"Track {agent_id}: executing task",
                                step=0, total_steps=1)

            result = await state.agent.solve(track_task)

            state.files_modified = result.files_modified
            state.completed = True
            state.result = SubAgentResult(
                success=result.success,
                output=result.answer,
                files_read=[],  # We don't track reads at this level
                files_modified=result.files_modified,
            )

            if result.files_modified:
                await self.artifact_registry.publish(
                    agent_id=agent_id,
                    artifact_key=f"track:{agent_id}:files_modified",
                    artifact_type="modified_files",
                    data=result.files_modified,
                    max_tokens=self.tier.artifact_max_tokens,
                )

            await self.artifact_registry.publish(
                agent_id=agent_id,
                artifact_key=f"track:{agent_id}:output",
                artifact_type="track_output",
                data={
                    "success": result.success,
                    "answer": result.answer[:2000],
                    "files_modified": result.files_modified,
                    "status": "completed" if result.success else "failed",
                },
                max_tokens=self.tier.artifact_max_tokens,
            )

            await self.artifact_registry.publish(
                agent_id=agent_id,
                artifact_key=f"track:{agent_id}:complete",
                artifact_type=f"track:{agent_id}:complete",
                data={"status": "completed", "success": result.success},
                max_tokens=1000,
            )

            self._trace.extend([
                t for t in result.trace
                if t.phase not in ("start",)
            ])
            self._trace.append(TraceEvent(
                phase="swarm_track_done",
                detail=f"Track {agent_id} completed: {'success' if result.success else 'failed'} ({len(result.files_modified)} files)",
            ))
            self._emit_progress(agent_id, "swarm_track", "complete",
                                f"Track {agent_id} completed: {'success' if result.success else 'failed'} ({len(result.files_modified)} files)",
                                step=0, total_steps=1,
                                files_modified=result.files_modified)

        except Exception as e:
            state.error = str(e)
            logger.error("Track %s failed with exception: %s", agent_id, e)
            state.completed = True
            await self.artifact_registry.publish(
                agent_id=agent_id,
                artifact_key=f"track:{agent_id}:complete",
                artifact_type=f"track:{agent_id}:complete",
                data={"status": "failed", "error": str(e)},
                max_tokens=1000,
            )
            self._emit_progress(agent_id, "swarm_track", "failed",
                                f"Track {agent_id} failed: {str(e)[:200]}",
                                step=0, total_steps=1)

        state.finished_at = asyncio.get_event_loop().time()



    async def _validate_contracts(
        self,
        task: str,
        track_states: dict[str, TrackRunState],
    ) -> list[str]:
        issues: list[str] = []

        apis_defined: set[str] = set()
        frontend_refs: set[str] = set()
        all_files: set[str] = set()
        for tid, state in track_states.items():
            if state.files_modified:
                all_files.update(state.files_modified)

        import re
        for file_path in sorted(all_files):
            full_path = self.repo_path / file_path
            if not full_path.exists():
                continue
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for m in re.finditer(r'@(?:app|router)\.(?:get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)', content):
                apis_defined.add(m.group(1))
            for m in re.finditer(r'\.(?:get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)', content):
                apis_defined.add(m.group(1))
            for m in re.finditer(r'route:\s*["\']([^"\']+)', content):
                apis_defined.add(m.group(1))
            for m in re.finditer(r'path:\s*["\']([^"\']+)', content):
                apis_defined.add(m.group(1))

            for m in re.finditer(r'(?:fetch|axios|api)\s*\(?\s*["\']([^"\']+)', content):
                if m.group(1).startswith("/") or m.group(1).startswith("http"):
                    frontend_refs.add(m.group(1))

        for ref in sorted(frontend_refs):
            if apis_defined and ref not in apis_defined:
                matching = [a for a in apis_defined if ref in a or a in ref]
                if not matching:
                    issues.append(f"Frontend references '{ref}' but no API route matches it")
                elif matching:
                    issues.append(f"Frontend references '{ref}' — closest API route is '{matching[0]}' (may need alignment)")

        return issues

    async def _run_integration(
        self,
        task: str,
        swarm_plan: SwarmPlan,
        track_states: dict[str, TrackRunState],
    ) -> SubAgentResult:
        contract_issues = await self._validate_contracts(task, track_states)

        integration_task = (
            f"Integration step for task: {task[:200]}\n\n"
            f"Multiple agents have completed work in parallel. "
            f"Review the artifacts below and ensure consistency across tracks.\n\n"
            f"Modified files per track:\n"
        )
        for tid, state in track_states.items():
            if state.files_modified:
                integration_task += f"\n  {tid} ({state.track.role}):\n"
                for f in state.files_modified[:10]:
                    integration_task += f"    - {f}\n"
                if len(state.files_modified) > 10:
                    integration_task += f"    ... and {len(state.files_modified) - 10} more\n"

        if contract_issues:
            integration_task += "\n## Detected Contract Issues\n"
            for issue in contract_issues:
                integration_task += f"- WARNING: {issue}\n"
            integration_task += "\nPlease fix these issues.\n"

        integration_task += "\nReview the changes and ensure:\n"
        integration_task += "1. No conflicting edits between tracks\n"
        integration_task += "2. Shared interfaces/schemas are consistent\n"
        integration_task += "3. Import paths and references match\n"
        integration_task += "4. Build/test configuration is unified\n"

        from ..llm.prompts import SYSTEM_SWARM_COORDINATOR
        from ..core.executor import Executor

        try:
            executor = Executor(self.router, self.registry, tier_config=self.tier)
            messages = [
                {"role": "system", "content": SYSTEM_SWARM_COORDINATOR},
                {"role": "user", "content": integration_task},
            ]
            tools = self.registry.get_schemas(["read_file", "edit_file", "edit_lines", "grep_code", "run_command"])
            result = await executor.run_tool_loop(messages, tools, edit_mode=True)

            return SubAgentResult(
                success=result.success,
                output=result.output or "Integration completed",
                files_read=result.files_read,
                files_modified=result.files_modified,
            )
        except Exception as e:
            logger.warning("Integration step failed: %s", e)
            return SubAgentResult(success=False, output=f"Integration failed: {e}")

    async def _run_final_verification(self) -> SubAgentResult:
        from ..subagents.verifier import VerifierSubAgent
        verifier = VerifierSubAgent(self.router, self.registry, str(self.repo_path), tier_config=self.tier)
        files_str = "\n".join(sorted(self._all_modified_files))
        context = f"Modified files across all tracks:\n{files_str}"
        return await verifier.run("Verify all modified files work correctly", context)

    async def _run_single_agent(self, task: str, swarm_plan: SwarmPlan) -> SwarmResult:
        track = swarm_plan.tracks[0]
        agent = self._create_agent_for_track(track)
        self._emit_progress(track.id, "swarm_track", "running",
                            f"Single track {track.id}: executing task",
                            step=0, total_steps=1)
        result = await agent.solve(task)

        track_result = {track.id: SubAgentResult(
            success=result.success,
            output=result.answer,
            files_modified=result.files_modified,
        )}

        self._all_modified_files.update(result.files_modified)
        self._emit_progress(track.id, "swarm_track", "complete",
                            f"Single track {track.id} completed: {'success' if result.success else 'failed'}",
                            step=0, total_steps=1,
                            files_modified=result.files_modified)

        return SwarmResult(
            success=result.success,
            track_results=track_result,
            merged_artifacts={},
            trace=result.trace,
            files_modified=result.files_modified,
        )

    @property
    def trace(self) -> list[TraceEvent]:
        return list(self._trace)