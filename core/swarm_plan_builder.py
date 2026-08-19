from __future__ import annotations

import json
import logging
from typing import Any

from .types import PlanStep, SwarmPlan, SwarmTrack, TierConfig
from ..llm.router import ModelRouter
from ..llm.structured import extract_json

logger = logging.getLogger("agent.swarm_plan_builder")

SWARM_DECOMPOSITION_PROMPT = """\
You are an expert task decomposition engine. Break the user's task into parallel
work tracks that can be executed concurrently by different AI agents.

## RULES
1. Identify 2-5 independent work streams. Each stream should be a coherent
   domain of work (e.g., "backend API", "frontend UI", "Docker config", "database schema").
2. Each track gets a unique ID, a role name, and a sequence of steps.
3. Steps use these action types: "explore", "edit", "verify", "research".
4. Dependencies between tracks MUST be explicit. For example, if the frontend
   depends on the API schema, the frontend track depends on the api track.
5. Tracks with NO dependencies can run in parallel.
6. Architecture/planning tracks should come first as they define shared contracts.
7. Be specific about target files in each step.

## OUTPUT FORMAT
Respond with valid JSON matching this schema:
{
  "tracks": [
    {
      "id": "arch",
      "role": "architect",
      "dependencies": [],
      "steps": [
        {
          "index": 0,
          "description": "Explore existing codebase structure",
          "action": "explore",
          "target_files": []
        },
        {
          "index": 1,
          "description": "Design shared API contract",
          "action": "edit",
          "target_files": ["api/contract.md"]
        }
      ]
    },
    {
      "id": "api",
      "role": "api-builder",
      "dependencies": ["arch"],
      "steps": [
        {
          "index": 0,
          "description": "Explore existing API patterns",
          "action": "explore",
          "target_files": []
        },
        {
          "index": 1,
          "description": "Build the API implementation",
          "action": "edit",
          "target_files": ["api/routes.py", "api/models.py"]
        },
        {
          "index": 2,
          "description": "Verify API with tests",
          "action": "verify",
          "target_files": ["api/tests/"]
        }
      ]
    },
    {
      "id": "frontend",
      "role": "frontend-builder",
      "dependencies": ["arch"],
      "steps": [
        {
          "index": 0,
          "description": "Explore frontend patterns",
          "action": "explore",
          "target_files": []
        },
        {
          "index": 1,
          "description": "Build UI components",
          "action": "edit",
          "target_files": ["frontend/src/"]
        }
      ]
    },
    {
      "id": "deploy",
      "role": "coordinator",
      "dependencies": ["api", "frontend"],
      "steps": [
        {
          "index": 0,
          "description": "Write Docker and compose files",
          "action": "edit",
          "target_files": ["Dockerfile", "docker-compose.yml"]
        }
      ]
    }
  ]
}

Guidelines:
- All tracks must have unique IDs.
- The coordinator role handles cross-cutting concerns (Docker, CI, integration).
- Every step must have a clear action and description.
- If a track has no target files yet, use an empty list.
"""


class SwarmPlanBuilder:
    def __init__(self, router: ModelRouter, tier_config: TierConfig | None = None):
        self.router = router
        self.tier = tier_config or TierConfig(name="balanced")

    async def decompose(
        self, task: str, repo_map_summary: str = ""
    ) -> SwarmPlan:
        context_parts = []
        if repo_map_summary:
            context_parts.append(f"Project context:\n{repo_map_summary[:2000]}")
        context_parts.append(f"Task: {task}")
        context = "\n\n".join(context_parts)

        messages = [
            {"role": "system", "content": SWARM_DECOMPOSITION_PROMPT},
            {"role": "user", "content": context},
        ]

        max_tokens = max(
            self.tier.planner_max_tokens * 2,
            2048,
        )
        try:
            response = await self.router.generate(
                role="planner",
                messages=messages,
                max_tokens=max_tokens,
            )
            return self._parse_swarm_plan(response.content)
        except Exception as e:
            logger.warning("Swarm decomposition failed (%s), falling back to single-track plan", e)
            return self._fallback_single_track(task)

    def _parse_swarm_plan(self, content: str) -> SwarmPlan:
        data = extract_json(content)
        if not data or "tracks" not in data:
            logger.warning("No valid tracks in swarm plan, using fallback")
            return self._fallback_single_track(content[:200])

        tracks = []
        for t in data["tracks"]:
            steps = [
                PlanStep(
                    index=s.get("index", i),
                    description=s.get("description", ""),
                    action=s.get("action", "explore"),
                    target_files=s.get("target_files", []),
                )
                for i, s in enumerate(t.get("steps", []))
            ]
            track = SwarmTrack(
                id=t.get("id", f"track-{len(tracks)}"),
                role=t.get("role", "default"),
                steps=steps,
                dependencies=t.get("dependencies", []),
            )
            tracks.append(track)

        coordinator_role = "coordinator"
        for t in tracks:
            if "coordinator" in t.role.lower() or "integration" in t.role.lower():
                coordinator_role = t.id
                break

        return SwarmPlan(tracks=tracks, coordinator_role=coordinator_role)

    def _fallback_single_track(self, task: str) -> SwarmPlan:
        track = SwarmTrack(
            id="main",
            role="default",
            steps=[
                PlanStep(index=0, description="Explore relevant code", action="explore"),
                PlanStep(index=1, description="Implement the task", action="edit"),
                PlanStep(index=2, description="Verify the changes", action="verify"),
            ],
            dependencies=[],
        )
        return SwarmPlan(tracks=[track], coordinator_role="main")

    async def dependent_tracks(self, plan: SwarmPlan) -> list[list[SwarmTrack]]:
        tracks_by_id = {t.id: t for t in plan.tracks}
        visited: set[str] = set()
        layers: list[list[SwarmTrack]] = []

        while len(visited) < len(plan.tracks):
            layer = [
                t
                for t in plan.tracks
                if t.id not in visited
                and all(dep in visited for dep in t.dependencies)
            ]
            if not layer:
                layer = [t for t in plan.tracks if t.id not in visited]
                if not layer:
                    break
            layers.append(layer)
            for t in layer:
                visited.add(t.id)

        return layers