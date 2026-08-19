from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zirconAgent.core.artifact_registry import ArtifactRegistry
from zirconAgent.core.swarm_plan_builder import SwarmPlanBuilder, SWARM_DECOMPOSITION_PROMPT
from zirconAgent.core.types import (
    PlanStep,
    SwarmPlan,
    SwarmResult,
    SwarmTrack,
    Tier,
    TierConfig,
    TIER_PRESETS,
)




class TestArtifactRegistry:
    @pytest.fixture
    def registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = ArtifactRegistry(tmp)
            yield reg

    @pytest.mark.asyncio
    async def test_publish_and_get(self, registry):
        await registry.publish("agent-1", "schema", "api_schema", {"endpoints": ["/users"]})
        result = await registry.get("schema")
        assert result is not None
        assert result["producer_id"] == "agent-1"
        assert result["data"]["endpoints"] == ["/users"]

    @pytest.mark.asyncio
    async def test_consume_tracks_consumption(self, registry):
        await registry.publish("agent-1", "key1", "type1", "data1")
        result = await registry.consume("agent-2", "key1")
        assert result is not None
        assert result["data"] == "data1"

        progress = await registry.agent_progress("agent-2")
        assert "key1" in progress["consumed"]

    @pytest.mark.asyncio
    async def test_list_by_type(self, registry):
        await registry.publish("a1", "k1", "api", {"route": "/users"})
        await registry.publish("a1", "k2", "api", {"route": "/posts"})
        await registry.publish("a2", "k3", "frontend", {"component": "UserList"})

        api_artifacts = await registry.list_by_type("api")
        assert len(api_artifacts) == 2

        frontend_artifacts = await registry.list_by_type("frontend")
        assert len(frontend_artifacts) == 1

    @pytest.mark.asyncio
    async def test_wait_for_existing_artifact(self, registry):
        await registry.publish("a1", "schema", "api_schema", {"v1": "/api/v1"})
        result = await registry.wait_for("api_schema", timeout=5.0)
        assert result is not None
        assert result["key"] == "schema"

    @pytest.mark.asyncio
    async def test_wait_for_new_artifact(self, registry):
        async def delayed_publish():
            await asyncio.sleep(0.05)
            await registry.publish("a1", "schema", "api_schema", {"v1": "/api/v1"})

        async def waiter():
            return await registry.wait_for("api_schema", timeout=5.0)

        result = await asyncio.gather(delayed_publish(), waiter())
        wait_result = result[1]
        assert wait_result is not None
        assert wait_result["data"]["v1"] == "/api/v1"

    @pytest.mark.asyncio
    async def test_wait_for_timeout(self, registry):
        result = await registry.wait_for("nonexistent", timeout=0.1)
        assert result is None

    @pytest.mark.asyncio
    async def test_snapshot_persistence(self, registry):
        await registry.publish("a1", "k1", "type1", "hello")
        await registry.save_snapshot()

        new_registry = ArtifactRegistry(registry.repo_path)
        loaded = await new_registry.load_snapshot()
        assert loaded is True

        result = await new_registry.get("k1")
        assert result is not None
        assert result["data"] == "hello"

    @pytest.mark.asyncio
    async def test_concurrent_publish(self, registry):
        async def agent_publish(aid: str, key: str):
            for i in range(10):
                await registry.publish(aid, f"{key}_{i}", "test", {"value": i})
                await asyncio.sleep(0.001)

        await asyncio.gather(
            agent_publish("a1", "api"),
            agent_publish("a2", "frontend"),
            agent_publish("a3", "docker"),
        )

        all_artifacts = await registry.list_all()
        assert len(all_artifacts) == 30  # 3 agents * 10 artifacts

    @pytest.mark.asyncio
    async def test_reset(self, registry):
        await registry.publish("a1", "k1", "t", "data")
        await registry.reset()
        all_artifacts = await registry.list_all()
        assert len(all_artifacts) == 0




class TestSwarmPlanBuilder:
    @pytest.fixture
    def mock_router(self):
        router = MagicMock()
        router.generate = AsyncMock()
        return router

    @pytest.mark.asyncio
    async def test_decompose_fallback_on_failure(self, mock_router):
        mock_router.generate.side_effect = Exception("API Error")
        builder = SwarmPlanBuilder(mock_router)
        plan = await builder.decompose("Build a simple app")
        assert len(plan.tracks) == 1
        assert plan.tracks[0].id == "main"

    @pytest.mark.asyncio
    async def test_decompose_parses_valid_json(self, mock_router):
        mock_router.generate.return_value = MagicMock(
            content=json.dumps({
                "tracks": [
                    {
                        "id": "arch",
                        "role": "architect",
                        "dependencies": [],
                        "steps": [
                            {"index": 0, "description": "Explore", "action": "explore", "target_files": []},
                        ],
                    },
                    {
                        "id": "api",
                        "role": "api-builder",
                        "dependencies": ["arch"],
                        "steps": [
                            {"index": 0, "description": "Build API", "action": "edit", "target_files": ["api/routes.py"]},
                        ],
                    },
                ],
            })
        )

        builder = SwarmPlanBuilder(mock_router)
        plan = await builder.decompose("Build API and frontend")
        assert len(plan.tracks) == 2
        assert plan.tracks[0].id == "arch"
        assert plan.tracks[1].id == "api"
        assert plan.tracks[1].dependencies == ["arch"]

    @pytest.mark.asyncio
    async def test_dependent_tracks_single_layer(self, mock_router):
        mock_router.generate.return_value = MagicMock(
            content=json.dumps({
                "tracks": [
                    {"id": "a", "role": "r1", "dependencies": [], "steps": [{"index": 0, "description": "X", "action": "explore"}]},
                    {"id": "b", "role": "r2", "dependencies": [], "steps": [{"index": 0, "description": "Y", "action": "explore"}]},
                ],
            })
        )
        builder = SwarmPlanBuilder(mock_router)
        plan = await builder.decompose("test")
        layers = await builder.dependent_tracks(plan)
        assert len(layers) == 1
        assert len(layers[0]) == 2

    @pytest.mark.asyncio
    async def test_dependent_tracks_multi_layer(self, mock_router):
        mock_router.generate.return_value = MagicMock(
            content=json.dumps({
                "tracks": [
                    {"id": "arch", "role": "architect", "dependencies": [], "steps": [{"index": 0, "description": "X", "action": "explore"}]},
                    {"id": "api", "role": "api-builder", "dependencies": ["arch"], "steps": [{"index": 0, "description": "Y", "action": "edit"}]},
                    {"id": "frontend", "role": "frontend-builder", "dependencies": ["api"], "steps": [{"index": 0, "description": "Z", "action": "edit"}]},
                ],
            })
        )
        builder = SwarmPlanBuilder(mock_router)
        plan = await builder.decompose("test")
        layers = await builder.dependent_tracks(plan)
        assert len(layers) == 3
        assert layers[0][0].id == "arch"
        assert layers[1][0].id == "api"
        assert layers[2][0].id == "frontend"

    @pytest.mark.asyncio
    async def test_dependent_tracks_parallel_within_layer(self, mock_router):
        mock_router.generate.return_value = MagicMock(
            content=json.dumps({
                "tracks": [
                    {"id": "arch", "role": "architect", "dependencies": [], "steps": [{"index": 0, "description": "X", "action": "explore"}]},
                    {"id": "api", "role": "api-builder", "dependencies": ["arch"], "steps": [{"index": 0, "description": "Y", "action": "edit"}]},
                    {"id": "frontend", "role": "frontend-builder", "dependencies": ["arch"], "steps": [{"index": 0, "description": "Z", "action": "edit"}]},
                    {"id": "deploy", "role": "coordinator", "dependencies": ["api", "frontend"], "steps": [{"index": 0, "description": "W", "action": "edit"}]},
                ],
            })
        )
        builder = SwarmPlanBuilder(mock_router)
        plan = await builder.decompose("test")
        layers = await builder.dependent_tracks(plan)
        assert len(layers) == 3
        assert len(layers[1]) == 2
        assert {t.id for t in layers[1]} == {"api", "frontend"}




class TestSwarmTypes:
    def test_swarm_plan_creation(self):
        track = SwarmTrack(
            id="api",
            role="api-builder",
            steps=[
                PlanStep(index=0, description="Explore", action="explore"),
                PlanStep(index=1, description="Build", action="edit"),
            ],
            dependencies=["arch"],
        )
        plan = SwarmPlan(tracks=[track])
        assert len(plan.tracks) == 1
        assert plan.tracks[0].id == "api"
        assert plan.tracks[0].steps[1].action == "edit"

    def test_swarm_result_creation(self):
        from zirconAgent.core.types import SubAgentResult
        result = SwarmResult(
            success=True,
            track_results={
                "api": SubAgentResult(success=True, output="API done", files_modified=["api/routes.py"]),
                "frontend": SubAgentResult(success=True, output="UI done", files_modified=["frontend/src/App.tsx"]),
            },
            files_modified=["api/routes.py", "frontend/src/App.tsx"],
        )
        assert result.success is True
        assert len(result.track_results) == 2
        assert "api" in result.track_results
        assert len(result.files_modified) == 2




class TestDecompositionPrompt:
    def test_prompt_has_required_sections(self):
        assert "You are an expert task decomposition engine" in SWARM_DECOMPOSITION_PROMPT
        assert "tracks" in SWARM_DECOMPOSITION_PROMPT
        assert "RULES" in SWARM_DECOMPOSITION_PROMPT
        assert "OUTPUT FORMAT" in SWARM_DECOMPOSITION_PROMPT
        assert "Guidelines" in SWARM_DECOMPOSITION_PROMPT

    def test_prompt_includes_json_schema(self):
        assert '"tracks"' in SWARM_DECOMPOSITION_PROMPT
        assert '"dependencies"' in SWARM_DECOMPOSITION_PROMPT
        assert '"target_files"' in SWARM_DECOMPOSITION_PROMPT
        import re
        json_match = re.search(r'\{[^}]+\}', SWARM_DECOMPOSITION_PROMPT, re.DOTALL)
        assert json_match is not None




class TestSwarmSubagentImports:
    def test_coordinator_import(self):
        from zirconAgent.subagents.swarm.coordinator import CoordinatorSwarmAgent
        assert CoordinatorSwarmAgent is not None

    def test_integration_agent_import(self):
        from zirconAgent.subagents.swarm.integration_agent import IntegrationSwarmAgent
        assert IntegrationSwarmAgent is not None

    def test_api_builder_import(self):
        from zirconAgent.subagents.swarm.api_builder import ApiBuilderSwarmAgent
        assert ApiBuilderSwarmAgent is not None

    def test_frontend_builder_import(self):
        from zirconAgent.subagents.swarm.frontend_builder import FrontendBuilderSwarmAgent
        assert FrontendBuilderSwarmAgent is not None

    def test_backend_builder_import(self):
        from zirconAgent.subagents.swarm.backend_builder import BackendBuilderSwarmAgent
        assert BackendBuilderSwarmAgent is not None
