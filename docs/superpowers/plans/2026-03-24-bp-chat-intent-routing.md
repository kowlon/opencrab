# BP Chat 意图路由 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users trigger all 5 BP operations (Start, Next, Answer, Edit, Cancel) via natural language chat input, converging to the same engine methods as UI operations.

**Architecture:** Mixed routing — deterministic command matching + state-aware routing in seecrab.py (before Agent), with LLM fallback for BP matching and Agent tools for complex intents. All paths converge to `engine.advance()`, `engine.answer()`, `engine.handle_edit_output()`, `sm.cancel()`.

**Tech Stack:** Python 3.11+ (FastAPI, asyncio), TypeScript/Vue 3 (frontend), Pydantic, pytest

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/seeagent/bestpractice/engine.py` | Fix | Fix `resp.text` → `resp.content` in `_conform_output()` |
| `src/seeagent/api/routes/bestpractice.py` | Fix | Fix DELETE endpoint persistence; modify POST /start for extracted_input |
| `src/seeagent/bestpractice/prompts/system_static.md` | Fix | Fix `bp_supplement_input` → `bp_answer`; add tool list |
| `src/seeagent/api/adapters/seecrab_adapter.py` | Fix | Unified `bp_*` event passthrough |
| `src/seeagent/api/routes/seecrab.py` | Modify | Add cancel/answer routing, LLM matching, next_loose, state init |
| `src/seeagent/bestpractice/facade.py` | Add+Modify | Add `llm_match_bp_from_message()`; enhance `get_dynamic_prompt_section()` |
| `src/seeagent/bestpractice/handler.py` | Fix+Add | Fix `_handle_start()`; add next/answer/cancel handlers + persist helper |
| `src/seeagent/bestpractice/tool_definitions.py` | Add | Add bp_next, bp_answer, bp_cancel tool schemas |
| `src/seeagent/bestpractice/prompts/bp_match.md` | Create | LLM BP matching prompt template |
| `apps/seecrab/src/types/index.ts` | Modify | Add `bp_cancelled` to SSEEventType |
| `apps/seecrab/src/stores/chat.ts` | Modify | Add `bp_cancelled` event handler |
| `apps/seecrab/src/stores/bestpractice.ts` | Modify | Add `handleCancelled()`; fix restore filtering |

---

## Task 1: Fix existing bugs (engine, REST API, prompt)

Three independent one-line-class fixes that form the foundation.

**Files:**
- Fix: `src/seeagent/bestpractice/engine.py:753`
- Fix: `src/seeagent/api/routes/bestpractice.py:593-603`
- Fix: `src/seeagent/bestpractice/prompts/system_static.md:18,27`
- Test: `tests/unit/bestpractice/test_engine_bugfixes.py` (new)
- Test: `tests/component/bestpractice/test_bp_endpoints.py` (modify)

### 1a: Fix engine `_conform_output()` resp.text → resp.content

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/bestpractice/test_engine_bugfixes.py
"""Tests for engine bug fixes."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from seeagent.bestpractice.engine import BPEngine
from seeagent.bestpractice.state_manager import BPStateManager


class TestConformOutputAttribute:
    """Verify _conform_output uses resp.content (not resp.text)."""

    @pytest.mark.asyncio
    async def test_conform_output_uses_content_attribute(self):
        """Brain.think_lightweight returns an object with .content, not .text."""
        sm = BPStateManager()
        engine = BPEngine(state_manager=sm)

        # Mock brain with .content attribute (correct)
        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"title": "mapped title"}'
        # Ensure .text does NOT exist to catch the bug
        del mock_resp.text
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)
        engine._get_brain = MagicMock(return_value=mock_brain)

        raw_output = {"raw_field": "some value"}
        output_schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }

        result = await engine._conform_output(raw_output, output_schema, session=None)
        assert "title" in result
        assert result["title"] == "mapped title"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/bestpractice/test_engine_bugfixes.py -x -v`
Expected: FAIL with `AttributeError: 'MagicMock' object has no attribute 'text'`

- [ ] **Step 3: Fix the bug — one line change**

```python
# src/seeagent/bestpractice/engine.py line 753
# Change:
#   text = resp.text if hasattr(resp, "text") else str(resp)
# To:
text = resp.content if hasattr(resp, "content") else str(resp)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/bestpractice/test_engine_bugfixes.py -x -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/bestpractice/test_engine_bugfixes.py src/seeagent/bestpractice/engine.py
git commit -m "fix(bp): use resp.content instead of resp.text in _conform_output"
```

### 1b: Fix DELETE endpoint missing persistence

- [ ] **Step 6: Write the failing test**

```python
# tests/component/bestpractice/test_bp_endpoints.py — ADD to existing file
class TestBPCancelEndpoint:
    @pytest.mark.asyncio
    async def test_cancel_persists_state_to_session(self, client, bp_setup):
        """DELETE /api/bp/{id} must persist cancelled state to session.metadata."""
        sm, instance_id, session = bp_setup
        response = await client.delete(f"/api/bp/{instance_id}")
        assert response.status_code == 200

        snap = sm.get(instance_id)
        assert snap.status.value == "cancelled"

        # Verify state was persisted to session metadata
        bp_state = session.metadata.get("bp_state")
        assert bp_state is not None
        assert any(
            inst.get("status") == "cancelled"
            for inst in bp_state.get("instances", [])
        )
```

Note: The existing `test_bp_endpoints.py` fixture structure needs to be checked. If `bp_setup` fixture doesn't provide `session`, extend it to return `(sm, instance_id, session)`.

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest tests/component/bestpractice/test_bp_endpoints.py::TestBPCancelEndpoint -x -v`
Expected: FAIL — `bp_state` is None (persistence missing)

- [ ] **Step 8: Fix DELETE endpoint — add persistence**

```python
# src/seeagent/api/routes/bestpractice.py lines 593-603
# Replace the entire bp_cancel function:
@router.delete("/{instance_id}")
async def bp_cancel(instance_id: str, request: Request):
    """Cancel BP instance."""
    sm = get_bp_state_manager()
    if not sm:
        return JSONResponse({"error": "BP system not initialized"}, status_code=500)
    snap = sm.get(instance_id)
    if not snap:
        return JSONResponse({"error": "Not found"}, status_code=404)
    sm.cancel(instance_id)
    # Persist cancelled state to session metadata
    session = _resolve_session(request, snap.session_id)
    if session:
        session.metadata["bp_state"] = sm.serialize_for_session(snap.session_id)
        session_mgr = _resolve_session_manager(request)
        if session_mgr:
            session_mgr.mark_dirty()
    return JSONResponse({"status": "ok"})
```

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest tests/component/bestpractice/test_bp_endpoints.py::TestBPCancelEndpoint -x -v`
Expected: PASS

- [ ] **Step 10: Fix system_static.md references**

Replace `bp_supplement_input` with `bp_answer` at lines 18 and 27, and add tool list:

```markdown
# src/seeagent/bestpractice/prompts/system_static.md — full replacement:

# 最佳实践能力

你拥有**最佳实践 (Best Practice)** 任务管理能力。可用的最佳实践模板:

${bp_list}

## 触发规则

系统已内置关键词匹配机制，当用户消息命中某个最佳实践的 CONTEXT 关键词时，**系统会自动向用户展示选择卡片**（自由模式/最佳实践模式）。
- **你不需要也不应该**使用 ask_user 重复询问是否启用最佳实践。
- 当用户回复"请启用最佳实践 (bp_id)"时，根据其中的 bp_id 直接调用 bp_start 启动对应的最佳实践。
- 当用户回复"自由模式"时，正常回答用户问题，不启动最佳实践。

## 可用工具

- `bp_start`: 启动最佳实践 (bp_id, input_data, run_mode)
- `bp_next`: 执行下一个子任务 (instance_id 可选，默认当前活跃实例)
- `bp_answer`: 补充子任务缺失的输入参数 (subtask_id, data)
- `bp_edit_output`: 修改已完成子任务的输出 (subtask_id, changes)
- `bp_cancel`: 取消当前最佳实践任务 (instance_id 可选)
- `bp_switch_task`: 切换到另一个挂起的 BP 实例 (target_instance_id)

## 交互规则

- 手动模式: 每个子任务完成后，使用 ask_user 展示选项让用户决定下一步
- 自动模式: 子任务完成后自动调用 bp_next，除非输入不完整
- 输入不完整时: 使用 ask_user 收集缺失字段，然后调用 bp_answer 补充
- Chat-to-Edit: 用户想修改已完成子任务的输出时，先调用 bp_get_output 获取当前内容，再调用 bp_edit_output 修改
- 任务切换: 用户想切换到另一个进行中的任务时，调用 bp_switch_task

## 补充输入流程

当 bp_start 或 bp_next 返回"输入不完整"的提示时:
1. 使用 ask_user 向用户列出缺失的必要字段
2. 收集用户提供的信息
3. 调用 bp_answer(subtask_id=..., data={...}) 补充数据
4. 调用 bp_next 继续执行
```

- [ ] **Step 11: Commit**

```bash
git add src/seeagent/bestpractice/engine.py src/seeagent/api/routes/bestpractice.py \
        src/seeagent/bestpractice/prompts/system_static.md \
        tests/unit/bestpractice/test_engine_bugfixes.py tests/component/bestpractice/test_bp_endpoints.py
git commit -m "fix(bp): fix conform_output attribute, cancel persistence, and stale prompt refs"
```

---

## Task 2: Fix SeeCrabAdapter BP event passthrough

**Files:**
- Fix: `src/seeagent/api/adapters/seecrab_adapter.py:149-155`
- Test: `tests/unit/test_seecrab_adapter.py` (modify — add BP event tests)

- [ ] **Step 1: Write failing tests for missing BP events**

```python
# tests/unit/test_seecrab_adapter.py — ADD these test cases to existing file

class TestBPEventPassthrough:
    """All bp_* events must pass through the adapter."""

    @pytest.mark.asyncio
    async def test_bp_subtask_start_passthrough(self, adapter):
        """bp_subtask_start was previously silently dropped."""
        event = {"type": "bp_subtask_start", "instance_id": "i1", "subtask_id": "s1"}
        result = await adapter._process_event(event)
        assert len(result) == 1
        assert result[0]["type"] == "bp_subtask_start"
        assert result[0]["subtask_id"] == "s1"

    @pytest.mark.asyncio
    async def test_bp_subtask_complete_passthrough(self, adapter):
        event = {
            "type": "bp_subtask_complete",
            "instance_id": "i1",
            "subtask_id": "s1",
            "summary": "done",
        }
        result = await adapter._process_event(event)
        assert len(result) == 1
        assert result[0]["type"] == "bp_subtask_complete"

    @pytest.mark.asyncio
    async def test_bp_waiting_next_passthrough(self, adapter):
        event = {"type": "bp_waiting_next", "instance_id": "i1"}
        result = await adapter._process_event(event)
        assert len(result) == 1
        assert result[0]["type"] == "bp_waiting_next"

    @pytest.mark.asyncio
    async def test_bp_ask_user_passthrough(self, adapter):
        event = {
            "type": "bp_ask_user",
            "instance_id": "i1",
            "subtask_id": "s1",
            "missing_fields": ["domain"],
        }
        result = await adapter._process_event(event)
        assert len(result) == 1
        assert result[0]["type"] == "bp_ask_user"
        assert result[0]["missing_fields"] == ["domain"]

    @pytest.mark.asyncio
    async def test_bp_complete_passthrough(self, adapter):
        event = {"type": "bp_complete", "instance_id": "i1"}
        result = await adapter._process_event(event)
        assert len(result) == 1
        assert result[0]["type"] == "bp_complete"

    @pytest.mark.asyncio
    async def test_bp_error_passthrough(self, adapter):
        event = {"type": "bp_error", "instance_id": "i1", "message": "fail"}
        result = await adapter._process_event(event)
        assert len(result) == 1
        assert result[0]["type"] == "bp_error"

    @pytest.mark.asyncio
    async def test_bp_cancelled_passthrough(self, adapter):
        event = {"type": "bp_cancelled", "instance_id": "i1", "bp_name": "Test"}
        result = await adapter._process_event(event)
        assert len(result) == 1
        assert result[0]["type"] == "bp_cancelled"
        assert result[0]["bp_name"] == "Test"

    @pytest.mark.asyncio
    async def test_bp_progress_flat_format(self, adapter):
        """advance() yields flat format (no data wrapper)."""
        event = {
            "type": "bp_progress",
            "instance_id": "i1",
            "current_subtask": 1,
            "total_subtasks": 3,
        }
        result = await adapter._process_event(event)
        assert len(result) == 1
        assert result[0]["type"] == "bp_progress"
        assert result[0]["current_subtask"] == 1

    @pytest.mark.asyncio
    async def test_bp_progress_data_wrapper_format(self, adapter):
        """_emit_progress() uses data wrapper format."""
        event = {
            "type": "bp_progress",
            "data": {"instance_id": "i1", "current_subtask": 1},
        }
        result = await adapter._process_event(event)
        assert len(result) == 1
        assert result[0]["type"] == "bp_progress"
        assert result[0]["instance_id"] == "i1"
        assert "data" not in result[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_seecrab_adapter.py::TestBPEventPassthrough -x -v`
Expected: FAIL — `bp_subtask_start` returns `[]`

- [ ] **Step 3: Replace BP event handling with unified passthrough**

In `src/seeagent/api/adapters/seecrab_adapter.py`, replace lines 149-155 (the `bp_instance_created`, `bp_progress/bp_subtask_output/bp_stale` blocks) with a single unified rule:

```python
        # BP events — unified passthrough for all bp_* event types
        # Two formats: flat (from engine.advance() yield) and data-wrapped (from _emit_*())
        if etype.startswith("bp_"):
            if "data" in event and isinstance(event["data"], dict):
                # data wrapper format → flatten
                return [{"type": etype, **event["data"]}]
            else:
                # flat format → pass through directly
                return [event]
```

This replaces:
- Line 149-151: `if etype == "bp_instance_created": return [event]`
- Line 153-155: `if etype in ("bp_progress", "bp_subtask_output", "bp_stale"): ...`

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_seecrab_adapter.py -x -v`
Expected: ALL PASS (both new and existing tests)

- [ ] **Step 5: Commit**

```bash
git add src/seeagent/api/adapters/seecrab_adapter.py tests/unit/test_seecrab_adapter.py
git commit -m "fix(bp): unified bp_* event passthrough in SeeCrabAdapter"
```

---

## Task 3: Add tool definitions for bp_next, bp_answer, bp_cancel

**Files:**
- Modify: `src/seeagent/bestpractice/tool_definitions.py`
- Test: `tests/unit/bestpractice/test_tool_definitions.py` (new)

- [ ] **Step 1: Write failing test**

```python
# tests/unit/bestpractice/test_tool_definitions.py
"""Tests for BP tool definitions completeness."""
from seeagent.bestpractice.tool_definitions import BP_TOOL_DEFINITIONS, get_bp_tool_names


class TestToolDefinitions:
    def test_all_six_tools_defined(self):
        names = get_bp_tool_names()
        expected = {"bp_start", "bp_edit_output", "bp_switch_task", "bp_next", "bp_answer", "bp_cancel"}
        assert set(names) == expected

    def test_bp_next_schema(self):
        bp_next = next(t for t in BP_TOOL_DEFINITIONS if t["name"] == "bp_next")
        props = bp_next["input_schema"]["properties"]
        assert "instance_id" in props

    def test_bp_answer_schema(self):
        bp_answer = next(t for t in BP_TOOL_DEFINITIONS if t["name"] == "bp_answer")
        assert "subtask_id" in bp_answer["input_schema"]["required"]
        assert "data" in bp_answer["input_schema"]["required"]

    def test_bp_cancel_schema(self):
        bp_cancel = next(t for t in BP_TOOL_DEFINITIONS if t["name"] == "bp_cancel")
        props = bp_cancel["input_schema"]["properties"]
        assert "instance_id" in props
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/bestpractice/test_tool_definitions.py -x -v`
Expected: FAIL — `StopIteration` (bp_next not found)

- [ ] **Step 3: Add three tool definitions**

Append to `BP_TOOL_DEFINITIONS` list in `src/seeagent/bestpractice/tool_definitions.py`:

```python
    {
        "name": "bp_next",
        "category": "Best Practice",
        "description": "执行下一个子任务（继续最佳实践流程）",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID (可选，默认当前活跃实例)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "bp_answer",
        "category": "Best Practice",
        "description": "补充子任务缺失的输入参数（响应 bp_ask_user）",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID (可选)",
                },
                "subtask_id": {
                    "type": "string",
                    "description": "等待输入的子任务 ID",
                },
                "data": {
                    "type": "object",
                    "description": "补充的参数数据 (字段名→值)",
                },
            },
            "required": ["subtask_id", "data"],
        },
    },
    {
        "name": "bp_cancel",
        "category": "Best Practice",
        "description": "取消当前最佳实践任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID (可选，默认当前活跃实例)",
                },
            },
            "required": [],
        },
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/bestpractice/test_tool_definitions.py -x -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/seeagent/bestpractice/tool_definitions.py tests/unit/bestpractice/test_tool_definitions.py
git commit -m "feat(bp): add bp_next, bp_answer, bp_cancel tool definitions"
```

---

## Task 4: Add handler methods (bp_next, bp_answer, bp_cancel) and fix bp_start

**Files:**
- Modify: `src/seeagent/bestpractice/handler.py`
- Test: `tests/unit/bestpractice/test_handler.py` (modify)
- Test: `tests/unit/bestpractice/test_handler_new_tools.py` (new)

- [ ] **Step 1: Write failing tests for new tools**

```python
# tests/unit/bestpractice/test_handler_new_tools.py
"""Tests for new BP tool handlers: bp_next, bp_answer, bp_cancel."""
import asyncio
import json

import pytest

from seeagent.bestpractice.config import BestPracticeConfig
from seeagent.bestpractice.context_bridge import ContextBridge
from seeagent.bestpractice.engine import BPEngine
from seeagent.bestpractice.handler import BP_TOOLS, BPToolHandler
from seeagent.bestpractice.models import RunMode, SubtaskConfig, SubtaskStatus
from seeagent.bestpractice.state_manager import BPStateManager


class MockEventBus:
    def __init__(self):
        self.events = []

    async def put(self, event):
        self.events.append(event)


class MockSession:
    def __init__(self):
        self.id = "test-session"
        self.metadata = {}

        class Ctx:
            _sse_event_bus = None
        self.context = Ctx()


class MockAgent:
    def __init__(self):
        self._current_session = MockSession()
        self._current_session.context._sse_event_bus = MockEventBus()


@pytest.fixture
def bp_config():
    return BestPracticeConfig(
        id="test-bp", name="Test BP", description="desc",
        subtasks=[
            SubtaskConfig(
                id="s1", name="Step 1", agent_profile="agent-a",
                input_schema={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            ),
            SubtaskConfig(id="s2", name="Step 2", agent_profile="agent-b"),
        ],
    )


@pytest.fixture
def handler(bp_config):
    state_mgr = BPStateManager()
    engine = BPEngine(state_manager=state_mgr)
    bridge = ContextBridge(state_manager=state_mgr)
    return BPToolHandler(
        engine=engine, state_manager=state_mgr,
        context_bridge=bridge, config_registry={bp_config.id: bp_config},
    )


class TestBPToolsList:
    def test_all_tools_registered(self):
        assert "bp_next" in BP_TOOLS
        assert "bp_answer" in BP_TOOLS
        assert "bp_cancel" in BP_TOOLS


class TestBPNext:
    @pytest.mark.asyncio
    async def test_next_no_active_instance(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_next", {}, agent)
        assert "没有活跃" in result

    @pytest.mark.asyncio
    async def test_next_nonexistent_instance(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_next", {"instance_id": "fake"}, agent)
        assert "不存在" in result


class TestBPAnswer:
    @pytest.mark.asyncio
    async def test_answer_missing_params(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_answer", {}, agent)
        assert "需要" in result or "required" in result.lower()

    @pytest.mark.asyncio
    async def test_answer_missing_data(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_answer", {"subtask_id": "s1"}, agent)
        assert "需要" in result or "data" in result.lower()


class TestBPCancel:
    @pytest.mark.asyncio
    async def test_cancel_no_active_instance(self, handler):
        agent = MockAgent()
        result = await handler.handle("bp_cancel", {}, agent)
        assert "没有活跃" in result

    @pytest.mark.asyncio
    async def test_cancel_active_instance(self, handler):
        agent = MockAgent()
        # Create an instance first
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "x"}}, agent)
        active = handler.state_manager.get_active("test-session")
        assert active is not None

        result = await handler.handle("bp_cancel", {}, agent)
        assert "已取消" in result

        # Verify instance is cancelled
        snap = handler.state_manager.get(active.instance_id)
        assert snap.status.value == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_sets_cooldown(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "x"}}, agent)
        await handler.handle("bp_cancel", {}, agent)
        cooldown = handler.state_manager.get_cooldown("test-session")
        assert cooldown > 0

    @pytest.mark.asyncio
    async def test_cancel_emits_bp_cancelled_event(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "x"}}, agent)
        active = handler.state_manager.get_active("test-session")
        bus = agent._current_session.context._sse_event_bus

        await handler.handle("bp_cancel", {}, agent)

        cancelled_events = [e for e in bus.events if e.get("type") == "bp_cancelled"]
        assert len(cancelled_events) == 1
        assert cancelled_events[0]["instance_id"] == active.instance_id
        assert cancelled_events[0]["bp_name"] == "Test BP"

    @pytest.mark.asyncio
    async def test_cancel_persists_to_session(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "x"}}, agent)
        await handler.handle("bp_cancel", {}, agent)
        bp_state = agent._current_session.metadata.get("bp_state")
        assert bp_state is not None


class TestPersistToSession:
    @pytest.mark.asyncio
    async def test_persist_writes_metadata(self, handler):
        agent = MockAgent()
        await handler.handle("bp_start", {"bp_id": "test-bp", "input_data": {"q": "x"}}, agent)
        active = handler.state_manager.get_active("test-session")
        handler._persist_to_session(active.instance_id, agent._current_session)
        assert "bp_state" in agent._current_session.metadata
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/bestpractice/test_handler_new_tools.py -x -v`
Expected: FAIL — `bp_next` not in BP_TOOLS

- [ ] **Step 3: Implement handler changes**

Replace `src/seeagent/bestpractice/handler.py` completely:

Key changes:
1. Update `BP_TOOLS` to include all 6 tools
2. Update dispatch table
3. Add `_handle_next()`, `_handle_answer()`, `_handle_cancel()`
4. Add `_persist_to_session()` helper
5. Fix `_handle_start()` to call `engine.advance()` and persist

The implementation should follow the design doc sections 3.6.1-3.6.6 exactly. Key code for new handlers:

```python
BP_TOOLS = ["bp_start", "bp_edit_output", "bp_switch_task", "bp_next", "bp_answer", "bp_cancel"]

# In handle() dispatch:
dispatch = {
    "bp_start": self._handle_start,
    "bp_edit_output": self._handle_edit_output,
    "bp_switch_task": self._handle_switch_task,
    "bp_next": self._handle_next,
    "bp_answer": self._handle_answer,
    "bp_cancel": self._handle_cancel,
}

# _handle_next:
async def _handle_next(self, params, agent, session):
    instance_id = self._resolve_instance_id(params, session)
    if not instance_id:
        return "❌ 当前没有活跃的最佳实践任务"
    snap = self.state_manager.get(instance_id)
    if not snap:
        return "❌ BP 实例不存在"
    bus = getattr(session.context, "_sse_event_bus", None) if hasattr(session, "context") else None
    async for event in self.engine.advance(instance_id, session):
        if bus:
            await bus.put(event)
    self._persist_to_session(instance_id, session)
    return "✅ 子任务执行完成"

# _handle_answer:
async def _handle_answer(self, params, agent, session):
    instance_id = self._resolve_instance_id(params, session)
    subtask_id = (params.get("subtask_id") or "").strip()
    data = params.get("data", {})
    if not instance_id or not subtask_id or not data:
        return "❌ 需要 subtask_id 和 data 参数"
    bus = getattr(session.context, "_sse_event_bus", None) if hasattr(session, "context") else None
    async for event in self.engine.answer(instance_id, subtask_id, data, session):
        if bus:
            await bus.put(event)
    self._persist_to_session(instance_id, session)
    return "✅ 参数已补充，子任务执行中"

# _handle_cancel:
async def _handle_cancel(self, params, agent, session):
    instance_id = self._resolve_instance_id(params, session)
    if not instance_id:
        return "❌ 当前没有活跃的最佳实践任务"
    snap = self.state_manager.get(instance_id)
    if not snap:
        return "❌ BP 实例不存在"
    bp_name = snap.bp_config.name if snap.bp_config else snap.bp_id
    self.state_manager.cancel(instance_id)
    self.state_manager.set_cooldown(snap.session_id)
    bus = getattr(session.context, "_sse_event_bus", None) if hasattr(session, "context") else None
    if bus:
        await bus.put({
            "type": "bp_cancelled",
            "instance_id": instance_id,
            "bp_name": bp_name,
        })
    self._persist_to_session(instance_id, session)
    return f"✅ 已取消最佳实践任务「{bp_name}」(id={instance_id})"

# _persist_to_session helper:
def _persist_to_session(self, instance_id, session):
    snap = self.state_manager.get(instance_id)
    if not snap or not session:
        return
    try:
        session.metadata["bp_state"] = self.state_manager.serialize_for_session(snap.session_id)
    except Exception:
        pass
```

Also fix `_handle_start()` — add `engine.advance()` call after creating instance (per design doc 3.6.1):

```python
# After existing bus.put(bp_instance_created) block, ADD:
async for event in self.engine.advance(inst_id, session):
    if bus:
        await bus.put(event)
self._persist_to_session(inst_id, session)
```

And change the return message from `"前端将自动开始执行"` to `"已创建并执行"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/bestpractice/test_handler_new_tools.py -x -v`
Expected: PASS

- [ ] **Step 5: Run existing handler tests to ensure no regression**

Run: `pytest tests/unit/bestpractice/test_handler.py tests/unit/bestpractice/test_handler_start.py -x -v`
Expected: PASS (note: `test_handler_start.py` assertion about "不执行子任务" may need updating since `_handle_start` now calls `advance()`)

- [ ] **Step 6: Commit**

```bash
git add src/seeagent/bestpractice/handler.py \
        tests/unit/bestpractice/test_handler_new_tools.py \
        tests/unit/bestpractice/test_handler.py \
        tests/unit/bestpractice/test_handler_start.py
git commit -m "feat(bp): add bp_next/bp_answer/bp_cancel handlers; fix bp_start to call advance()"
```

---

## Task 5: Extend command routing (cancel, next_loose, waiting_input guard)

**Files:**
- Modify: `src/seeagent/api/routes/seecrab.py:27-105`
- Test: `tests/unit/bestpractice/test_command_routing.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/bestpractice/test_command_routing.py
"""Tests for BP command routing in seecrab.py."""
import pytest
from seeagent.api.routes.seecrab import _match_bp_command, _normalize_bp_command


class TestNormalize:
    def test_strips_punctuation(self):
        assert _normalize_bp_command("下一步！") == "下一步"
        assert _normalize_bp_command("  继续。 ") == "继续"

    def test_lowercase(self):
        assert _normalize_bp_command("OK") == "ok"


class TestMatchBPCommand:
    # Existing start commands
    def test_start_commands(self):
        assert _match_bp_command("进入最佳实践") == "start"
        assert _match_bp_command("最佳实践模式") == "start"
        assert _match_bp_command("开始最佳实践") == "start"

    # Existing strict next commands
    def test_strict_next_commands(self):
        assert _match_bp_command("进入下一步") == "next"
        assert _match_bp_command("下一步") == "next"
        assert _match_bp_command("继续执行") == "next"
        assert _match_bp_command("继续") == "next"

    # New strict next commands
    def test_new_strict_next_commands(self):
        assert _match_bp_command("好的继续") == "next"
        assert _match_bp_command("开始下一步") == "next"
        assert _match_bp_command("执行下一步") == "next"

    # Loose next commands (only match with active BP)
    def test_loose_next_commands(self):
        assert _match_bp_command("好") == "next_loose"
        assert _match_bp_command("没问题") == "next_loose"
        assert _match_bp_command("ok") == "next_loose"
        assert _match_bp_command("确认") == "next_loose"
        assert _match_bp_command("好的下一步") == "next_loose"

    # Cancel commands
    def test_cancel_commands(self):
        assert _match_bp_command("取消最佳实践") == "cancel"
        assert _match_bp_command("终止最佳实践") == "cancel"
        assert _match_bp_command("取消任务") == "cancel"
        assert _match_bp_command("终止任务") == "cancel"
        assert _match_bp_command("停止最佳实践") == "cancel"
        assert _match_bp_command("退出最佳实践") == "cancel"

    # Non-matching
    def test_no_match(self):
        assert _match_bp_command("你好") is None
        assert _match_bp_command("帮我写文章") is None
        assert _match_bp_command("") is None

    # Punctuation tolerance
    def test_punctuation_tolerance(self):
        assert _match_bp_command("取消任务！") == "cancel"
        assert _match_bp_command("好的，继续") == "next"
        assert _match_bp_command("OK!") == "next_loose"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/bestpractice/test_command_routing.py -x -v`
Expected: FAIL — `test_new_strict_next_commands` etc. fail

- [ ] **Step 3: Implement command set changes in seecrab.py**

Replace the command constants and `_match_bp_command()` at lines 27-105:

```python
_BP_START_COMMANDS = {
    "进入最佳实践",
    "最佳实践模式",
    "开始最佳实践",
}

# Strict next: always match, return "next"
_BP_NEXT_COMMANDS_STRICT = {
    "进入下一步", "下一步", "继续执行", "继续",
    "好的继续", "开始下一步", "执行下一步",
}

# Loose next: only match when active BP exists, return "next_loose"
_BP_NEXT_COMMANDS_LOOSE = {
    "好", "没问题", "ok", "确认", "好的下一步",
}

_BP_CANCEL_COMMANDS = {
    "取消最佳实践", "终止最佳实践", "取消任务", "终止任务",
    "停止最佳实践", "退出最佳实践",
}


def _match_bp_command(message: str) -> str | None:
    normalized = _normalize_bp_command(message)
    if normalized in _BP_START_COMMANDS:
        return "start"
    if normalized in _BP_NEXT_COMMANDS_STRICT:
        return "next"
    if normalized in _BP_NEXT_COMMANDS_LOOSE:
        return "next_loose"
    if normalized in _BP_CANCEL_COMMANDS:
        return "cancel"
    return None
```

Also keep `_BP_NEXT_COMMANDS` as an alias for backward compat if referenced elsewhere (check first — it's only used in `_match_bp_command` so safe to remove).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/bestpractice/test_command_routing.py -x -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/seeagent/api/routes/seecrab.py tests/unit/bestpractice/test_command_routing.py
git commit -m "feat(bp): extend command routing with cancel, loose next, and new next variants"
```

---

## Task 6: Add cancel and next edge-case handling in seecrab_chat()

**Files:**
- Modify: `src/seeagent/api/routes/seecrab.py:335-520` (the `generate()` inner function)
- Test: `tests/unit/bestpractice/test_seecrab_chat_routing.py` (new)

This task modifies the `generate()` function inside `seecrab_chat()` to handle:
1. **Step 0**: Call `_ensure_bp_restored()` + `tick_cooldown()` at the top
2. **Cancel command**: Route to `_cancel_bp_from_chat()`
3. **next_loose**: Only act when active BP exists, otherwise fall through
4. **next + waiting_input**: Return friendly prompt instead of silent empty response
5. **brain variable**: Move `brain = getattr(agent, "brain", None)` before BP matching

Also adds the `_cancel_bp_from_chat()` function.

- [ ] **Step 1: Write failing test for cancel routing**

```python
# tests/unit/bestpractice/test_seecrab_chat_routing.py
"""Tests for seecrab_chat BP routing logic."""
import json

import pytest

from seeagent.api.routes.seecrab import _cancel_bp_from_chat


class MockStateManager:
    def __init__(self):
        self._instances = {}
        self._cooldowns = {}
        self._cancelled = []

    def get(self, instance_id):
        return self._instances.get(instance_id)

    def cancel(self, instance_id):
        self._cancelled.append(instance_id)
        snap = self._instances.get(instance_id)
        if snap:
            snap.status = "cancelled"

    def set_cooldown(self, session_id, turns=3):
        self._cooldowns[session_id] = turns

    def serialize_for_session(self, session_id):
        return {"version": 1, "instances": [], "cooldown": self._cooldowns.get(session_id, 0)}


class MockSnap:
    def __init__(self, instance_id, session_id, bp_name="Test BP"):
        self.instance_id = instance_id
        self.session_id = session_id
        self.status = "active"

        class MockConfig:
            name = bp_name
        self.bp_config = MockConfig()


class MockSession:
    def __init__(self):
        self.metadata = {}


class MockSessionManager:
    def mark_dirty(self):
        pass


class TestCancelBPFromChat:
    @pytest.mark.asyncio
    async def test_cancel_yields_bp_cancelled_event(self):
        events = []
        async for event in _cancel_bp_from_chat(
            session_id="s1",
            instance_id="inst1",
            bp_name="Test BP",
            sm=MockStateManager(),
            session=MockSession(),
            session_manager=MockSessionManager(),
        ):
            events.append(event)

        assert any(e.get("type") == "bp_cancelled" for e in events)
        cancelled = next(e for e in events if e.get("type") == "bp_cancelled")
        assert cancelled["instance_id"] == "inst1"
        assert cancelled["bp_name"] == "Test BP"

    @pytest.mark.asyncio
    async def test_cancel_sets_cooldown(self):
        sm = MockStateManager()
        async for _ in _cancel_bp_from_chat(
            session_id="s1", instance_id="inst1", bp_name="Test",
            sm=sm, session=MockSession(), session_manager=MockSessionManager(),
        ):
            pass
        assert sm._cooldowns.get("s1", 0) > 0

    @pytest.mark.asyncio
    async def test_cancel_persists_to_session(self):
        session = MockSession()
        sm = MockStateManager()
        async for _ in _cancel_bp_from_chat(
            session_id="s1", instance_id="inst1", bp_name="Test",
            sm=sm, session=session, session_manager=MockSessionManager(),
        ):
            pass
        assert "bp_state" in session.metadata
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/bestpractice/test_seecrab_chat_routing.py -x -v`
Expected: FAIL — `_cancel_bp_from_chat` does not exist

- [ ] **Step 3: Add `_cancel_bp_from_chat()` function to seecrab.py**

Add after `_stream_bp_next_from_chat()` (after line 311):

```python
async def _cancel_bp_from_chat(
    *,
    session_id: str,
    instance_id: str,
    bp_name: str,
    sm,
    session,
    session_manager,
):
    """Chat path cancel. Calls sm.cancel() — same as DELETE /api/bp/{id}."""
    sm.cancel(instance_id)
    sm.set_cooldown(session_id)

    yield {
        "type": "bp_cancelled",
        "instance_id": instance_id,
        "bp_name": bp_name,
    }

    if session:
        session.metadata["bp_state"] = sm.serialize_for_session(session_id)
        if session_manager:
            session_manager.mark_dirty()

    yield {"type": "done"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/bestpractice/test_seecrab_chat_routing.py -x -v`
Expected: PASS

- [ ] **Step 5: Modify `generate()` in `seecrab_chat()` — add Step 0 + cancel/next_loose/waiting_input handling**

In the `generate()` function (starting at line 335), make these changes:

**A. Add Step 0 — after session resolution (~line 382), before BP command check (~line 387):**

```python
            # ── Step 0: BP state restoration + cooldown tick ──
            from seeagent.bestpractice.facade import get_bp_state_manager
            bp_sm = get_bp_state_manager()
            if bp_sm:
                from seeagent.api.routes.bestpractice import _ensure_bp_restored
                _ensure_bp_restored(request, bp_session_id, bp_sm)
                bp_sm.tick_cooldown(bp_session_id)

            # Pre-fetch brain for LLM matching and extraction
            brain = getattr(agent, "brain", None)
```

**B. Replace the bp_cmd block (lines 387-451) with expanded routing:**

The full logic handles: start (unchanged), next + waiting_input guard, next_loose + active-only, cancel.

See design doc section 3.2 for the complete flow. Key additions:

```python
            bp_cmd = _match_bp_command(body.message or "")
            if bp_cmd:
                if not bp_sm:
                    from seeagent.bestpractice.facade import get_bp_state_manager
                    bp_sm = get_bp_state_manager()

                if bp_cmd == "start":
                    # ... (existing logic, unchanged)
                    ...

                if bp_cmd == "cancel":
                    active = bp_sm.get_active(bp_session_id) if bp_sm else None
                    if active:
                        bp_name = active.bp_config.name if active.bp_config else active.bp_id
                        async for event in _cancel_bp_from_chat(
                            session_id=bp_session_id,
                            instance_id=active.instance_id,
                            bp_name=bp_name,
                            sm=bp_sm,
                            session=session,
                            session_manager=session_manager,
                        ):
                            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        return
                    else:
                        fallback = {"type": "ai_text", "content": "当前没有进行中的最佳实践任务。"}
                        yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
                        yield 'data: {"type": "done"}\n\n'
                        return

                if bp_cmd in ("next", "next_loose"):
                    active = bp_sm.get_active(bp_session_id) if bp_sm else None
                    # next_loose without active BP → fall through to agent
                    if bp_cmd == "next_loose" and not active:
                        pass  # fall through
                    elif active:
                        # Check waiting_input — can't advance, need params first
                        has_waiting = any(
                            s == "waiting_input"
                            for s in active.subtask_statuses.values()
                        )
                        if has_waiting:
                            fallback = {
                                "type": "ai_text",
                                "content": "当前子任务正在等待您补充参数，请先提供所需信息，或输入"取消任务"退出。",
                            }
                            yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
                            yield 'data: {"type": "done"}\n\n'
                            return
                        if _has_bp_next_step(active):
                            async for event in _stream_bp_next_from_chat(
                                request,
                                session_id=bp_session_id,
                                instance_id=active.instance_id,
                                session=session,
                                session_manager=session_manager,
                                disconnect_event=disconnect_event,
                            ):
                                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                            return
                        fallback = {"type": "ai_text", "content": "当前最佳实践已完成或没有下一步可执行。"}
                        yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
                        yield 'data: {"type": "done"}\n\n'
                        return
                    elif bp_cmd == "next":
                        fallback = {"type": "ai_text", "content": "当前没有可继续的最佳实践任务。"}
                        yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
                        yield 'data: {"type": "done"}\n\n'
                        return
```

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/unit/bestpractice/ -x -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/seeagent/api/routes/seecrab.py tests/unit/bestpractice/test_seecrab_chat_routing.py
git commit -m "feat(bp): add cancel routing, next_loose, waiting_input guard, state init in seecrab_chat"
```

---

## Task 7: Add Chat Answer path (waiting_input → engine.answer())

**Files:**
- Modify: `src/seeagent/api/routes/seecrab.py`
- Test: `tests/unit/bestpractice/test_chat_answer.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/bestpractice/test_chat_answer.py
"""Tests for _stream_bp_answer_from_chat and _llm_extract_answer_fields."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from seeagent.api.routes.seecrab import _llm_extract_answer_fields


class TestLLMExtractAnswerFields:
    @pytest.mark.asyncio
    async def test_single_field_extraction(self):
        """When brain returns valid JSON, extract matching fields."""
        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"domain": "科技"}'
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)

        result = await _llm_extract_answer_fields(
            user_message="科技",
            missing_fields=["domain"],
            input_schema={
                "type": "object",
                "properties": {"domain": {"type": "string", "description": "领域"}},
            },
            brain=mock_brain,
        )
        assert result == {"domain": "科技"}

    @pytest.mark.asyncio
    async def test_filters_non_missing_fields(self):
        """Only return fields that are in missing_fields list."""
        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"domain": "科技", "extra": "ignore"}'
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)

        result = await _llm_extract_answer_fields(
            user_message="科技",
            missing_fields=["domain"],
            input_schema={"type": "object", "properties": {"domain": {"type": "string"}}},
            brain=mock_brain,
        )
        assert "extra" not in result
        assert "domain" in result

    @pytest.mark.asyncio
    async def test_returns_empty_on_no_brain(self):
        result = await _llm_extract_answer_fields(
            user_message="test",
            missing_fields=["field1"],
            input_schema={"type": "object", "properties": {}},
            brain=None,
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        mock_brain = AsyncMock()
        mock_brain.think_lightweight = AsyncMock(side_effect=Exception("LLM error"))

        result = await _llm_extract_answer_fields(
            user_message="test",
            missing_fields=["field1"],
            input_schema={"type": "object", "properties": {}},
            brain=mock_brain,
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_no_missing_fields(self):
        result = await _llm_extract_answer_fields(
            user_message="test",
            missing_fields=[],
            input_schema={"type": "object", "properties": {}},
            brain=AsyncMock(),
        )
        assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/bestpractice/test_chat_answer.py -x -v`
Expected: FAIL — `_llm_extract_answer_fields` does not exist

- [ ] **Step 3: Add `_llm_extract_answer_fields()` to seecrab.py**

Add after `_extract_input_from_query()` (~line 150):

```python
async def _llm_extract_answer_fields(
    user_message: str,
    missing_fields: list[str],
    input_schema: dict,
    brain,
) -> dict:
    """从用户消息中提取指定的缺失字段值。"""
    if not brain or not missing_fields:
        return {}

    props = input_schema.get("properties", {})
    fields_desc = "\n".join(
        f"- {name}: {props.get(name, {}).get('description', '无描述')} "
        f"(type: {props.get(name, {}).get('type', 'string')})"
        for name in missing_fields
    )
    prompt = (
        "从用户消息中提取以下字段，输出一个 JSON 对象。\n"
        "只提取消息中明确提到或可推断的字段，没有提到的字段不要包含。\n"
        "只输出 JSON，不要其他文字。\n\n"
        f"## 需要提取的字段\n{fields_desc}\n\n"
        f"## 用户消息\n{user_message}"
    )
    try:
        from seeagent.bestpractice.engine import BPEngine
        resp = await brain.think_lightweight(prompt, max_tokens=512)
        text = resp.content if hasattr(resp, "content") else str(resp)
        parsed = BPEngine._parse_output(text)
        if isinstance(parsed, dict):
            return {k: v for k, v in parsed.items() if k in missing_fields}
    except Exception as e:
        logger.warning(f"[BP] Failed to extract answer fields: {e}")
    return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/bestpractice/test_chat_answer.py -x -v`
Expected: PASS

- [ ] **Step 5: Add `_stream_bp_answer_from_chat()` function**

Add after `_cancel_bp_from_chat()`:

```python
async def _stream_bp_answer_from_chat(
    request: Request,
    *,
    session_id: str,
    instance_id: str,
    subtask_id: str,
    data: dict,
    session,
    session_manager,
    disconnect_event: asyncio.Event,
):
    """Chat path answer. Calls engine.answer() — same as POST /api/bp/answer."""
    from seeagent.api.routes.bestpractice import (
        _bp_clear_busy,
        _bp_mark_busy,
        _collect_reply_state,
        _new_reply_state,
        _persist_bp_to_session,
    )
    from seeagent.bestpractice.facade import get_bp_engine, get_bp_state_manager

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        yield {"type": "error", "message": "BP system not initialized", "code": "bp"}
        yield {"type": "done"}
        return

    # Use bestpractice.py's _bp_busy_locks for concurrency safety with UI path
    if not await _bp_mark_busy(session_id, "seecrab_bp_answer"):
        yield {"type": "error", "message": "Session is busy", "code": "bp"}
        yield {"type": "done"}
        return

    reply_state = _new_reply_state()
    full_reply: list[str] = []
    try:
        async for event in engine.answer(instance_id, subtask_id, data, session):
            if disconnect_event.is_set():
                break
            yield event
            _collect_reply_state(event, reply_state, full_reply)

        _persist_bp_to_session(
            session, instance_id, sm,
            reply_state=reply_state,
            full_reply="".join(full_reply),
            session_manager=session_manager,
        )
        yield {"type": "done"}
    except Exception as e:
        yield {"type": "error", "message": str(e), "code": "bp"}
        yield {"type": "done"}
    finally:
        _bp_clear_busy(session_id)
```

- [ ] **Step 6: Add waiting_input routing in `generate()` — after bp_cmd block, before bp_match**

Insert between the bp_cmd block and the `match_bp_from_message` block (~line 452):

```python
            # ── Step 2: waiting_input → route to answer ──
            if not bp_sm:
                from seeagent.bestpractice.facade import get_bp_state_manager
                bp_sm = get_bp_state_manager()
            active = bp_sm.get_active(bp_session_id) if bp_sm else None
            if active:
                waiting_subtask_id = None
                for st_id, st_status in active.subtask_statuses.items():
                    if st_status == "waiting_input":
                        waiting_subtask_id = st_id
                        break
                if waiting_subtask_id:
                    # Determine missing fields for smart extraction
                    from seeagent.bestpractice.scheduler import LinearScheduler
                    subtask_config = None
                    for st in active.bp_config.subtasks:
                        if st.id == waiting_subtask_id:
                            subtask_config = st
                            break

                    data = {}
                    if subtask_config:
                        required = subtask_config.input_schema.get("required", [])
                        scheduler = LinearScheduler(active.bp_config, active)
                        resolved_input = scheduler.resolve_input(waiting_subtask_id)
                        still_missing = [f for f in required if f not in resolved_input]

                        if len(still_missing) == 1:
                            data = {still_missing[0]: body.message}
                        elif len(still_missing) > 1:
                            data = await _llm_extract_answer_fields(
                                body.message, still_missing,
                                subtask_config.input_schema, brain,
                            )

                    if not data:
                        field_hints = ", ".join(still_missing) if subtask_config else "必填参数"
                        fallback = {
                            "type": "ai_text",
                            "content": f"无法从您的消息中识别参数，请按字段提供：{field_hints}",
                        }
                        yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
                        yield 'data: {"type": "done"}\n\n'
                        return

                    async for event in _stream_bp_answer_from_chat(
                        request,
                        session_id=bp_session_id,
                        instance_id=active.instance_id,
                        subtask_id=waiting_subtask_id,
                        data=data,
                        session=session,
                        session_manager=session_manager,
                        disconnect_event=disconnect_event,
                    ):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    return
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/unit/bestpractice/ -x -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/seeagent/api/routes/seecrab.py tests/unit/bestpractice/test_chat_answer.py
git commit -m "feat(bp): add chat answer path with waiting_input routing and LLM field extraction"
```

---

## Task 8: Add LLM BP matching (facade + prompt template)

**Files:**
- Create: `src/seeagent/bestpractice/prompts/bp_match.md`
- Modify: `src/seeagent/bestpractice/facade.py`
- Modify: `src/seeagent/api/routes/seecrab.py` (integrate LLM matching as Step 4)
- Test: `tests/unit/bestpractice/test_llm_match.py` (new)

- [ ] **Step 1: Create bp_match.md prompt template**

```markdown
# src/seeagent/bestpractice/prompts/bp_match.md

你是一个意图分类器。判断用户消息是否匹配以下最佳实践模板。
如果匹配，同时从用户消息中提取第一步需要的参数。

## 可用的最佳实践

${bp_list}

## 用户消息

"${user_message}"

## 要求

1. 判断用户消息是否明确表达了想要完成某个最佳实践能处理的任务
2. 如果匹配，从用户消息中提取第一步需要的参数值（仅提取消息中明确提到的，不要推测）
3. confidence: 1.0=完全确定, 0.7=较确定, 0.5以下=不确定
4. 只返回 JSON，不要其他内容

匹配: {"matched": true, "bp_id": "<id>", "confidence": <0.0-1.0>, "extracted_input": {<参数>}}
不匹配: {"matched": false}
```

- [ ] **Step 2: Write failing test for `llm_match_bp_from_message()`**

```python
# tests/unit/bestpractice/test_llm_match.py
"""Tests for LLM BP matching."""
import pytest
from unittest.mock import AsyncMock, MagicMock

import seeagent.bestpractice.facade as facade
from seeagent.bestpractice.facade import llm_match_bp_from_message
from seeagent.bestpractice.models import (
    BestPracticeConfig,
    SubtaskConfig,
    TriggerConfig,
    TriggerType,
)
from seeagent.bestpractice.state_manager import BPStateManager


@pytest.fixture(autouse=True)
def reset_facade():
    facade._initialized = False
    facade._bp_engine = None
    facade._bp_handler = None
    facade._bp_state_manager = None
    facade._bp_config_loader = None
    facade._bp_context_bridge = None
    facade._bp_prompt_loader = None
    yield
    facade._initialized = False


@pytest.fixture
def setup_llm_match():
    """Wire up facade for LLM matching tests."""
    config = BestPracticeConfig(
        id="content-pipeline",
        name="内容创作流水线",
        description="从选题调研到内容发布",
        subtasks=[
            SubtaskConfig(
                id="topic-research", name="选题调研", agent_profile="default",
                input_schema={
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "description": "内容领域"},
                        "platform": {"type": "string", "description": "发布平台"},
                    },
                    "required": ["domain"],
                },
            ),
        ],
        triggers=[TriggerConfig(type=TriggerType.CONTEXT, conditions=["写文章"])],
    )

    mock_loader = MagicMock()
    mock_loader.configs = {"content-pipeline": config}

    mock_prompt_loader = MagicMock()
    mock_prompt_loader.render = MagicMock(return_value="rendered prompt")

    state_mgr = BPStateManager()

    facade._initialized = True
    facade._bp_config_loader = mock_loader
    facade._bp_state_manager = state_mgr
    facade._bp_prompt_loader = mock_prompt_loader
    return state_mgr, config


class TestLLMMatchBPFromMessage:
    @pytest.mark.asyncio
    async def test_match_returns_bp_info(self, setup_llm_match):
        sm, config = setup_llm_match
        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"matched": true, "bp_id": "content-pipeline", "confidence": 0.9, "extracted_input": {"domain": "科技"}}'
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)

        result = await llm_match_bp_from_message("帮我写一篇科技文章", "s1", mock_brain)
        assert result is not None
        assert result["bp_id"] == "content-pipeline"
        assert result["extracted_input"]["domain"] == "科技"
        assert result["user_query"] == "帮我写一篇科技文章"

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, setup_llm_match):
        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"matched": false}'
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)

        result = await llm_match_bp_from_message("今天天气怎么样", "s1", mock_brain)
        assert result is None

    @pytest.mark.asyncio
    async def test_low_confidence_returns_none(self, setup_llm_match):
        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"matched": true, "bp_id": "content-pipeline", "confidence": 0.5, "extracted_input": {}}'
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)

        result = await llm_match_bp_from_message("也许写点什么", "s1", mock_brain)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_bp_id_returns_none(self, setup_llm_match):
        mock_brain = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"matched": true, "bp_id": "nonexistent", "confidence": 0.9, "extracted_input": {}}'
        mock_brain.think_lightweight = AsyncMock(return_value=mock_resp)

        result = await llm_match_bp_from_message("写文章", "s1", mock_brain)
        assert result is None

    @pytest.mark.asyncio
    async def test_already_offered_bp_skipped(self, setup_llm_match):
        sm, _ = setup_llm_match
        sm.mark_bp_offered("s1", "content-pipeline")

        mock_brain = AsyncMock()
        result = await llm_match_bp_from_message("写一篇文章", "s1", mock_brain)
        assert result is None

    @pytest.mark.asyncio
    async def test_active_instance_skipped(self, setup_llm_match):
        sm, config = setup_llm_match
        sm.create_instance(config, "s1")

        mock_brain = AsyncMock()
        result = await llm_match_bp_from_message("写文章", "s1", mock_brain)
        assert result is None

    @pytest.mark.asyncio
    async def test_cooldown_skipped(self, setup_llm_match):
        sm, _ = setup_llm_match
        sm.set_cooldown("s1", 3)

        mock_brain = AsyncMock()
        result = await llm_match_bp_from_message("写文章", "s1", mock_brain)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_brain_returns_none(self, setup_llm_match):
        result = await llm_match_bp_from_message("写文章", "s1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_exception_returns_none(self, setup_llm_match):
        mock_brain = AsyncMock()
        mock_brain.think_lightweight = AsyncMock(side_effect=Exception("timeout"))

        result = await llm_match_bp_from_message("写文章", "s1", mock_brain)
        assert result is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/bestpractice/test_llm_match.py -x -v`
Expected: FAIL — `llm_match_bp_from_message` does not exist

- [ ] **Step 4: Implement `llm_match_bp_from_message()` in facade.py**

Add after `match_bp_from_message()`:

```python
async def llm_match_bp_from_message(
    user_message: str,
    session_id: str,
    brain,
) -> dict | None:
    """LLM 回退匹配：当关键词匹配失败时，用 LLM 判断用户意图是否匹配某个 BP。"""
    if not _initialized:
        init_bp_system()
    if not _bp_state_manager or not _bp_config_loader or not _bp_prompt_loader or not brain:
        return None

    # Pre-checks: cooldown, active instance
    if _bp_state_manager.get_cooldown(session_id) > 0:
        return None
    if _bp_state_manager.get_active(session_id):
        return None

    # Build BP list for prompt, filtering already-offered BPs
    bp_list_lines = []
    for bp_id, config in _bp_config_loader.configs.items():
        if _bp_state_manager.is_bp_offered(session_id, bp_id):
            continue
        first_schema = config.subtasks[0].input_schema if config.subtasks else {}
        params_desc = ""
        if first_schema:
            props = first_schema.get("properties", {})
            required = set(first_schema.get("required", []))
            param_lines = []
            for pname, pinfo in props.items():
                req_mark = "必填" if pname in required else "选填"
                param_lines.append(
                    f"   - {pname} ({pinfo.get('type', 'string')}, {req_mark}): "
                    f"{pinfo.get('description', '')}"
                )
            if param_lines:
                params_desc = "\n" + "\n".join(param_lines)

        bp_list_lines.append(
            f"- {bp_id}: \"{config.name}\"\n"
            f"  描述: {config.description}"
            f"{params_desc}"
        )

    if not bp_list_lines:
        return None

    bp_list = "\n".join(bp_list_lines)

    try:
        prompt = _bp_prompt_loader.render("bp_match", bp_list=bp_list, user_message=user_message)
        resp = await brain.think_lightweight(prompt, max_tokens=512)
        text = resp.content if hasattr(resp, "content") else str(resp)

        from seeagent.bestpractice.engine import BPEngine
        parsed = BPEngine._parse_output(text)
        if not isinstance(parsed, dict):
            return None

        if not parsed.get("matched") or parsed.get("confidence", 0) < 0.7:
            return None

        bp_id = parsed.get("bp_id", "")
        config = _bp_config_loader.configs.get(bp_id)
        if not config:
            return None

        if _bp_state_manager.is_bp_offered(session_id, bp_id):
            return None

        first_input_schema = config.subtasks[0].input_schema if config.subtasks else None
        return {
            "bp_id": bp_id,
            "bp_name": config.name,
            "description": config.description,
            "subtask_count": len(config.subtasks),
            "subtasks": [{"id": s.id, "name": s.name} for s in config.subtasks],
            "extracted_input": parsed.get("extracted_input", {}),
            "user_query": user_message,
            "first_input_schema": first_input_schema,
        }
    except Exception as e:
        logger.warning(f"[BP] LLM match failed: {e}")
        return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/bestpractice/test_llm_match.py -x -v`
Expected: PASS

- [ ] **Step 6: Integrate LLM matching into seecrab.py (Step 4 in flow)**

After the keyword `match_bp_from_message` block (~line 523), add LLM fallback:

```python
            # Step 3-4: BP matching (keyword → LLM fallback)
            try:
                from seeagent.bestpractice.facade import match_bp_from_message
                bp_match = match_bp_from_message(body.message or "", bp_session_id)

                # Step 4: LLM fallback if keyword didn't match
                if not bp_match and brain:
                    from seeagent.bestpractice.facade import llm_match_bp_from_message
                    bp_match = await llm_match_bp_from_message(
                        body.message or "", bp_session_id, brain,
                    )

                if bp_match:
                    # ... (existing bp_offer logic, but add extracted_input to pending_offer)
                    ...
                    bp_sm.set_pending_offer(
                        bp_session_id,
                        {
                            "bp_id": bp_id,
                            "bp_name": bp_name,
                            "subtasks": bp_match.get("subtasks", []),
                            "default_run_mode": "manual",
                            "user_query": bp_match.get("user_query", ""),
                            "first_input_schema": bp_match.get("first_input_schema"),
                            "extracted_input": bp_match.get("extracted_input", {}),  # NEW
                        },
                    )
                    ...
```

Also modify the start command handler to use `extracted_input` from pending_offer:

```python
                    # In bp_cmd == "start" block, replace extracted_input logic:
                    extracted_input = pending_offer.get("extracted_input", {})
                    if not extracted_input:
                        user_query = pending_offer.get("user_query", "")
                        first_schema = pending_offer.get("first_input_schema")
                        if user_query and first_schema:
                            extracted_input = await _extract_input_from_query(
                                brain, user_query, first_schema,
                            )
```

- [ ] **Step 7: Run all tests**

Run: `pytest tests/unit/bestpractice/ -x -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/seeagent/bestpractice/prompts/bp_match.md \
        src/seeagent/bestpractice/facade.py \
        src/seeagent/api/routes/seecrab.py \
        tests/unit/bestpractice/test_llm_match.py
git commit -m "feat(bp): add LLM BP matching with fallback and extracted_input integration"
```

---

## Task 9: Enhance dynamic prompt section for intent routing

**Files:**
- Modify: `src/seeagent/bestpractice/facade.py:249-308`
- Test: `tests/unit/bestpractice/test_facade.py` (modify)

- [ ] **Step 1: Write failing test**

```python
# Add to tests/unit/bestpractice/test_facade.py TestFacadeInit class:

    def test_dynamic_prompt_waiting_input_routing(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        init_bp_system(search_paths=[bp_base_path])
        mgr = get_bp_state_manager()
        handler = get_bp_handler()
        config = list(handler.config_registry.values())[0]
        inst_id = mgr.create_instance(config, "test-session", {"topic": "test"})

        # Set first subtask to waiting_input
        from seeagent.bestpractice.models import SubtaskStatus
        first_subtask_id = config.subtasks[0].id
        mgr.update_subtask_status(inst_id, first_subtask_id, SubtaskStatus.WAITING_INPUT)

        section = get_dynamic_prompt_section("test-session")
        assert "bp_answer" in section or "等待" in section

    def test_dynamic_prompt_done_routing(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        init_bp_system(search_paths=[bp_base_path])
        mgr = get_bp_state_manager()
        handler = get_bp_handler()
        config = list(handler.config_registry.values())[0]
        inst_id = mgr.create_instance(config, "test-session", {"topic": "test"})

        from seeagent.bestpractice.models import SubtaskStatus
        first_subtask_id = config.subtasks[0].id
        mgr.update_subtask_status(inst_id, first_subtask_id, SubtaskStatus.DONE)
        mgr.advance_subtask(inst_id)

        section = get_dynamic_prompt_section("test-session")
        assert "bp_next" in section or "bp_edit_output" in section or "bp_cancel" in section
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/bestpractice/test_facade.py::TestFacadeInit::test_dynamic_prompt_waiting_input_routing -x -v`
Expected: FAIL — `bp_answer` not in section

- [ ] **Step 3: Enhance `get_dynamic_prompt_section()` in facade.py**

Replace the intent_routing logic (lines 284-296):

```python
        if active.bp_config:
            # Determine current subtask status for routing guidance
            statuses = list(active.subtask_statuses.values())
            current_status = statuses[idx] if idx < len(statuses) else ""
            prev_status = statuses[idx - 1] if idx > 0 and idx <= len(statuses) else ""

            if current_status == "waiting_input":
                intent_routing = (
                    "当前子任务等待用户输入参数。\n"
                    "如果用户提供了参数值，调用 bp_answer(subtask_id=..., data={...}) 补充。\n"
                    "如果用户想取消，调用 bp_cancel。\n"
                )
            elif prev_status == "done" or current_status == "done":
                intent_routing = (
                    "上一步已完成。用户可能想要:\n"
                    "A) 继续下一步 → 调用 bp_next\n"
                    "B) 修改上一步结果 → 调用 bp_edit_output(subtask_id=..., changes={...})\n"
                    "C) 取消任务 → 调用 bp_cancel\n"
                    "D) 询问其他问题（不涉及 BP 操作）\n"
                )
            else:
                intent_routing = (
                    "用户可能想要:\n"
                    "A) 修改已完成子任务结果 (bp_edit_output)\n"
                    "B) 切换到其他任务 (bp_switch_task)\n"
                    "C) 取消当前任务 (bp_cancel)\n"
                    "D) 询问相关问题\n"
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/bestpractice/test_facade.py -x -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/seeagent/bestpractice/facade.py tests/unit/bestpractice/test_facade.py
git commit -m "feat(bp): enhance dynamic prompt intent routing for waiting_input and done states"
```

---

## Task 10: Frontend changes (bp_cancelled event)

**Files:**
- Modify: `apps/seecrab/src/types/index.ts`
- Modify: `apps/seecrab/src/stores/chat.ts`
- Modify: `apps/seecrab/src/stores/bestpractice.ts`

Note: Frontend has no automated test framework in the project. Changes are minimal and can be verified manually.

- [ ] **Step 1: Add `bp_cancelled` to SSEEventType**

In `apps/seecrab/src/types/index.ts`, add `'bp_cancelled'` to the SSEEventType union:

```typescript
// Find the SSEEventType union and add bp_cancelled
export type SSEEventType =
  | 'ai_text'
  | ... // existing types
  | 'bp_cancelled'  // ADD THIS
```

- [ ] **Step 2: Add handler in chat.ts**

In `apps/seecrab/src/stores/chat.ts`, in the `dispatchEvent()` switch, add after the `bp_error` case:

```typescript
      case 'bp_cancelled': {
        const bpStore = useBestPracticeStore()
        bpStore.handleCancelled(event.instance_id)
        break
      }
```

- [ ] **Step 3: Add `handleCancelled()` in bestpractice.ts**

In `apps/seecrab/src/stores/bestpractice.ts`:

```typescript
    handleCancelled(instanceId: string) {
      const instance = this.instances.get(instanceId)
      if (instance) {
        instance.status = 'cancelled'
      }
      if (this.activeInstanceId === instanceId) {
        this.activeInstanceId = null
      }
    },
```

Also modify the restore logic (if there is a `restoreFromSession` or similar method) to filter cancelled instances — don't set them as `activeInstanceId`:

```typescript
    // In any restore/init logic that sets activeInstanceId:
    // if (instance.status === 'cancelled') skip setting as active
```

- [ ] **Step 4: Build frontend to verify no TypeScript errors**

Run: `cd apps/seecrab && npm run build`
Expected: Build succeeds with no type errors

- [ ] **Step 5: Commit**

```bash
git add apps/seecrab/src/types/index.ts \
        apps/seecrab/src/stores/chat.ts \
        apps/seecrab/src/stores/bestpractice.ts
git commit -m "feat(bp): add bp_cancelled event handling in frontend"
```

---

## Task 11: Integration verification — run full test suite

- [ ] **Step 1: Run unit tests**

Run: `pytest tests/unit/ -x -v`
Expected: ALL PASS

- [ ] **Step 2: Run component tests**

Run: `pytest tests/component/ -x -v`
Expected: ALL PASS

- [ ] **Step 3: Run linter**

Run: `ruff check src/seeagent/bestpractice/ src/seeagent/api/routes/seecrab.py src/seeagent/api/adapters/seecrab_adapter.py`
Expected: No errors

- [ ] **Step 4: Run type checker (if configured)**

Run: `mypy src/seeagent/bestpractice/ src/seeagent/api/routes/seecrab.py --ignore-missing-imports`
Expected: No new errors

- [ ] **Step 5: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix(bp): lint and type fixes for chat intent routing"
```

---

## Summary of new test files

| Test File | Tests | Coverage |
|---|---|---|
| `tests/unit/bestpractice/test_engine_bugfixes.py` | 1 | `_conform_output` attribute fix |
| `tests/unit/bestpractice/test_tool_definitions.py` | 4 | bp_next/answer/cancel schemas |
| `tests/unit/bestpractice/test_handler_new_tools.py` | 11 | All new handler methods |
| `tests/unit/bestpractice/test_command_routing.py` | 9 | Command matching (cancel, loose, new next) |
| `tests/unit/bestpractice/test_seecrab_chat_routing.py` | 3 | `_cancel_bp_from_chat` |
| `tests/unit/bestpractice/test_chat_answer.py` | 5 | `_llm_extract_answer_fields` |
| `tests/unit/bestpractice/test_llm_match.py` | 9 | `llm_match_bp_from_message` |

Modified test files:
- `tests/unit/test_seecrab_adapter.py` — 10 new BP event passthrough tests
- `tests/unit/bestpractice/test_facade.py` — 2 new dynamic prompt tests
- `tests/component/bestpractice/test_bp_endpoints.py` — 1 new cancel persistence test

**Total new tests: ~55**
