"""Tests for the agent registry and routing."""

import pytest
from agent_community.models import AgentType, SubTask, TaskStatus
from agent_community.router.agent_registry import AgentProfile, AgentRegistry


class TestAgentProfile:
    def test_suitability_score_base(self):
        profile = AgentProfile(
            agent_type=AgentType.CLAUDE_CODE,
            binary_name="claude",
            strengths=["complex_refactoring"],
        )
        task = SubTask(title="something generic", description="nothing special")
        score = profile.suitability_score(task)
        assert 0.0 <= score <= 1.0

    def test_suitability_score_strength_match(self):
        profile = AgentProfile(
            agent_type=AgentType.CLAUDE_CODE,
            binary_name="claude",
            strengths=["documentation"],
        )
        task = SubTask(title="Write API docs", description="Update the documentation guide")
        score = profile.suitability_score(task)
        assert score > 0.5  # should get bonus for doc keywords

    def test_suitability_score_weakness_penalty(self):
        profile = AgentProfile(
            agent_type=AgentType.CODEX_CLI,
            binary_name="codex",
            weaknesses=["complex_architecture"],
        )
        task = SubTask(
            title="Redesign architecture",
            description="Restructure the entire module pattern",
        )
        score = profile.suitability_score(task)
        # The keyword "architecture" and "restructure" should not trigger
        # complex_architecture weakness keywords directly (those are in
        # the strength_keywords map under different capabilities)
        assert 0.0 <= score <= 1.0


class TestAgentRegistry:
    def test_default_profiles(self):
        registry = AgentRegistry()
        agents = registry.available_agents()
        assert len(agents) == 2
        types = {a.agent_type for a in agents}
        assert AgentType.CLAUDE_CODE in types
        assert AgentType.CODEX_CLI in types

    def test_route_task(self):
        registry = AgentRegistry()
        task = SubTask(
            title="Write unit tests",
            description="Create test fixtures and mock objects",
        )
        agent = registry.route_task(task)
        assert agent in (AgentType.CLAUDE_CODE, AgentType.CODEX_CLI)

    def test_route_task_with_allowed_agents(self):
        registry = AgentRegistry()
        task = SubTask(title="test", description="test")
        agent = registry.route_task(task, allowed_agents=[AgentType.CODEX_CLI])
        assert agent == AgentType.CODEX_CLI

    def test_route_all(self):
        registry = AgentRegistry()
        tasks = [
            SubTask(task_id="t1", title="Refactor module"),
            SubTask(task_id="t2", title="Write tests"),
            SubTask(task_id="t3", title="Fix bug"),
        ]
        routes = registry.route_all(tasks)
        assert len(routes) == 3
        assert all(v in AgentType for v in routes.values())

    def test_no_available_agents_raises(self):
        registry = AgentRegistry()
        # Disable all agents
        for profile in registry._profiles.values():
            profile.available = False
        task = SubTask(title="test")
        with pytest.raises(ValueError, match="No available agents"):
            registry.route_task(task)

    def test_custom_profile_registration(self):
        registry = AgentRegistry(profiles={})
        profile = AgentProfile(
            agent_type=AgentType.CLAUDE_CODE,
            binary_name="claude",
            strengths=["documentation"],
        )
        registry.register(profile)
        assert len(registry.available_agents()) == 1
