"""L4 E2E Tests: Plan system end-to-end — create, step management, complete, cancel."""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent / "fixtures"))
from mock_llm import MockLLMClient, MockBrain

from seeagent.tools.handlers.plan import (
    has_active_plan,
    register_active_plan,
    unregister_active_plan,
    clear_session_plan_state,
    cancel_plan,
    register_plan_handler,
    get_plan_handler_for_session,
    get_active_plan_prompt,
    should_require_plan,
)


def _make_mock_agent():
    """Create a minimal mock agent for PlanHandler."""
    agent = MagicMock()
    agent.brain = MockBrain(MockLLMClient())
    agent._current_session_id = "plan-test-session"
    agent._current_conversation_id = "plan-test-conv"
    agent.get_current_session_id = MagicMock(return_value="plan-test-session")
    agent.skill_registry = MagicMock()
    agent.skill_registry.list_all.return_value = []
    return agent


class TestPlanLifecycle:
    """Test full plan lifecycle: create → update steps → complete."""

    def test_create_plan_registers(self):
        sid = "lifecycle-1"
        clear_session_plan_state(sid)
        register_active_plan(sid, "plan-lc-1")
        assert has_active_plan(sid) is True

    def test_complete_plan_unregisters(self):
        sid = "lifecycle-2"
        clear_session_plan_state(sid)
        register_active_plan(sid, "plan-lc-2")
        unregister_active_plan(sid)
        assert has_active_plan(sid) is False

    def test_cancel_active_plan(self):
        sid = "lifecycle-3"
        clear_session_plan_state(sid)
        register_active_plan(sid, "plan-lc-3")
        cancel_plan(sid)
        # After cancel, plan should be inactive
        assert has_active_plan(sid) is False


class TestPlanWithHandler:
    """Test PlanHandler integration with session management."""

    def test_register_and_retrieve_handler(self):
        from seeagent.tools.handlers.plan import PlanHandler
        sid = "handler-test-1"
        clear_session_plan_state(sid)
        agent = _make_mock_agent()
        handler = PlanHandler(agent)
        register_plan_handler(sid, handler)
        retrieved = get_plan_handler_for_session(sid)
        assert retrieved is handler
        clear_session_plan_state(sid)

    def test_plan_prompt_when_no_plan(self):
        sid = "prompt-test-1"
        clear_session_plan_state(sid)
        prompt = get_active_plan_prompt(sid)
        assert isinstance(prompt, str)

    @pytest.mark.asyncio
    async def test_research_plan_removes_query_script_steps(self):
        from seeagent.tools.handlers.plan import PlanHandler

        agent = _make_mock_agent()
        handler = PlanHandler(agent)

        await handler.handle(
            "create_plan",
            {
                "task_summary": "制定详细的五泄风景区游玩攻略",
                "steps": [
                    {
                        "id": "step_1",
                        "description": "搜索景区门票与开放时间",
                        "tool": "web_search",
                        "skills": ["web-search"],
                    },
                    {
                        "id": "step_2",
                        "description": "写 Python 查询脚本到 data/temp/wuxi_search.py",
                        "tool": "write_file",
                        "skills": ["write-file"],
                    },
                    {
                        "id": "step_3",
                        "description": "运行查询脚本抓取攻略信息",
                        "tool": "run_shell",
                        "skills": ["run-shell"],
                    },
                    {
                        "id": "step_4",
                        "description": "搜索交通与周边美食",
                        "tool": "web_search",
                        "skills": ["web-search"],
                    },
                ],
            },
        )

        plan = handler.get_plan_for("plan-test-conv")
        assert plan is not None
        assert [step["tool"] for step in plan["steps"]] == ["web_search", "web_search"]
        prompt = handler.get_plan_prompt_section("plan-test-conv")
        assert "Do NOT write temporary Python search scripts" in prompt

    @pytest.mark.asyncio
    async def test_non_research_plan_keeps_write_and_shell_steps(self):
        from seeagent.tools.handlers.plan import PlanHandler

        agent = _make_mock_agent()
        handler = PlanHandler(agent)

        await handler.handle(
            "create_plan",
            {
                "task_summary": "生成数据迁移脚本并执行校验",
                "steps": [
                    {
                        "id": "step_1",
                        "description": "写入迁移脚本",
                        "tool": "write_file",
                        "skills": ["write-file"],
                    },
                    {
                        "id": "step_2",
                        "description": "执行迁移脚本",
                        "tool": "run_shell",
                        "skills": ["run-shell"],
                    },
                ],
            },
        )

        plan = handler.get_plan_for("plan-test-conv")
        assert plan is not None
        assert [step["tool"] for step in plan["steps"]] == ["write_file", "run_shell"]

    @pytest.mark.asyncio
    async def test_research_plan_also_blocks_hyphenated_script_tools(self):
        from seeagent.tools.handlers.plan import PlanHandler

        agent = _make_mock_agent()
        handler = PlanHandler(agent)

        await handler.handle(
            "create_plan",
            {
                "task_summary": "整理五泄风景区最新游玩攻略",
                "steps": [
                    {
                        "id": "step_1",
                        "description": "综合搜索景区门票、开放时间和交通信息",
                        "tool": "web_search",
                        "skills": ["web-search"],
                    },
                    {
                        "id": "step_2",
                        "description": "把查询脚本写到 data/temp/wuxie_search.py",
                        "tool": "write-file",
                        "skills": ["write-file"],
                    },
                    {
                        "id": "step_3",
                        "description": "运行脚本抓取更多攻略页面",
                        "tool": "run-shell",
                        "skills": ["run-shell"],
                    },
                ],
            },
        )

        plan = handler.get_plan_for("plan-test-conv")
        assert plan is not None
        assert [step["tool"] for step in plan["steps"]] == ["web_search"]

    @pytest.mark.asyncio
    async def test_complete_plan_rejects_pending_steps(self):
        from seeagent.tools.handlers.plan import PlanHandler, has_active_plan

        agent = _make_mock_agent()
        handler = PlanHandler(agent)

        await handler.handle(
            "create_plan",
            {
                "task_summary": "整理一份景区攻略",
                "steps": [
                    {
                        "id": "step_1",
                        "description": "搜索门票信息",
                        "tool": "web_search",
                        "skills": ["web-search"],
                    },
                    {
                        "id": "step_2",
                        "description": "整理路线信息",
                        "tool": "web_search",
                        "skills": ["web-search"],
                    },
                ],
            },
        )
        await handler.handle(
            "update_plan_step",
            {"step_id": "step_1", "status": "completed", "result": "已完成门票信息整理"},
        )

        result = await handler.handle(
            "complete_plan",
            {"summary": "尝试在步骤未完成时结束计划"},
        )

        assert "仍有未完成步骤" in result
        plan = handler.get_plan_for("plan-test-conv")
        assert plan is not None
        assert plan["status"] == "in_progress"
        assert has_active_plan("plan-test-conv") is True


class TestPlanDetection:
    """Test whether complex messages trigger plan requirement."""

    @pytest.mark.parametrize("msg,expected_type", [
        ("你好", bool),
        ("帮我重构整个项目代码，写完整测试，然后部署到服务器", bool),
        ("查一下天气", bool),
        ("1. 创建数据库 2. 写API 3. 加认证 4. 写文档 5. 部署", bool),
    ])
    def test_should_require_plan(self, msg, expected_type):
        result = should_require_plan(msg)
        assert isinstance(result, expected_type)


class TestMultiSessionPlanIsolation:
    """Verify plans are isolated between sessions."""

    def test_two_sessions_independent(self):
        s1, s2 = "iso-session-1", "iso-session-2"
        clear_session_plan_state(s1)
        clear_session_plan_state(s2)

        register_active_plan(s1, "plan-s1")
        assert has_active_plan(s1) is True
        assert has_active_plan(s2) is False

        register_active_plan(s2, "plan-s2")
        assert has_active_plan(s1) is True
        assert has_active_plan(s2) is True

        cancel_plan(s1)
        assert has_active_plan(s1) is False
        assert has_active_plan(s2) is True

        clear_session_plan_state(s1)
        clear_session_plan_state(s2)
