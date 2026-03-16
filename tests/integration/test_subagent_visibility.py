"""Integration test: sub-agent event visibility in SeeCrab adapter."""
from __future__ import annotations

import asyncio

import pytest

from seeagent.api.adapters.seecrab_adapter import SeeCrabAdapter


@pytest.mark.asyncio
async def test_full_delegation_event_flow():
    """Simulate a full delegation: main agent -> sub-agent -> main agent.

    Verifies:
    1. agent_header events appear at boundaries
    2. Sub-agent tool calls produce step cards
    3. Main agent delegation card has dynamic title (not generic placeholder)
    4. Event ordering is correct
    5. Stream ends with a 'done' event
    """
    event_bus: asyncio.Queue = asyncio.Queue()

    async def raw_stream():
        # Main agent starts thinking
        yield {"type": "thinking_delta", "content": "Let me delegate..."}

        # Main agent calls delegate_to_agent
        yield {
            "type": "tool_call_start",
            "tool": "delegate_to_agent",
            "args": {"agent_id": "researcher", "message": "搜索论文", "reason": "需要调研"},
            "id": "d1",
        }

        # Simulate sub-agent events arriving via event_bus.
        # In production, these are pushed by the orchestrator while
        # the main stream blocks waiting for delegation result.
        await event_bus.put({
            "type": "agent_header",
            "agent_id": "researcher",
            "agent_name": "研究员",
            "agent_description": "专业研究助手",
        })
        await event_bus.put({
            "type": "thinking_delta",
            "content": "我来搜索...",
        })
        await event_bus.put({
            "type": "tool_call_start",
            "tool": "web_search",
            "args": {"query": "AI papers 2026"},
            "id": "s1",
        })
        await event_bus.put({
            "type": "tool_call_end",
            "tool": "web_search",
            "result": "Found 10 papers",
            "id": "s1",
            "is_error": False,
        })
        await event_bus.put({
            "type": "text_delta",
            "content": "搜索完成",
        })
        await event_bus.put({
            "type": "agent_header",
            "agent_id": "main",
            "agent_name": "SeeAgent",
        })

        # Give event loop a chance to process event_bus items
        await asyncio.sleep(0.05)

        # Main agent gets delegation result
        yield {
            "type": "tool_call_end",
            "tool": "delegate_to_agent",
            "result": "Found 10 papers about AI",
            "id": "d1",
            "is_error": False,
        }
        yield {"type": "text_delta", "content": "根据研究结果..."}

    adapter = SeeCrabAdapter(brain=None, user_messages=["帮我搜索最新的AI论文"])
    events: list[dict] = []
    async for e in adapter.transform(raw_stream(), reply_id="r1", event_bus=event_bus):
        events.append(e)

    types = [e["type"] for e in events]

    # -- 1. agent_header events present at boundaries --
    headers = [e for e in events if e["type"] == "agent_header"]
    assert len(headers) >= 2, f"Expected >=2 agent_header events, got {len(headers)}: {headers}"
    # First header switches to sub-agent
    assert headers[0]["agent_id"] == "researcher"
    assert headers[0]["agent_name"] == "研究员"
    # Second header switches back to main agent
    assert headers[1]["agent_id"] == "main"

    # -- 2. Sub-agent web_search produces step cards --
    step_cards = [e for e in events if e["type"] == "step_card"]
    assert len(step_cards) >= 1, f"Expected step cards, got none. Types: {types}"
    # The web_search card should mention AI papers in its title
    search_cards = [
        c for c in step_cards
        if c.get("title") and "AI papers" in c["title"]
    ]
    assert len(search_cards) >= 1, (
        f"Expected a step card mentioning 'AI papers', got titles: "
        f"{[c.get('title') for c in step_cards]}"
    )

    # -- 3. Delegation card has dynamic title (contains agent_id) --
    delegation_cards = [
        c for c in step_cards
        if c.get("title") and "researcher" in c["title"]
    ]
    assert len(delegation_cards) >= 1, (
        f"Expected delegation card with 'researcher' in title, got titles: "
        f"{[c.get('title') for c in step_cards]}"
    )

    # -- 4. Stream ends properly --
    assert "done" in types, f"'done' event missing from stream. Types: {types}"
    assert events[-1]["type"] == "done", f"Last event should be 'done', got: {events[-1]}"

    # -- 5. Thinking events are tagged with the correct agent_id --
    thinking_events = [e for e in events if e["type"] == "thinking"]
    # At least the main agent thinking + sub-agent thinking
    assert len(thinking_events) >= 2, (
        f"Expected >=2 thinking events, got {len(thinking_events)}"
    )
    # Sub-agent thinking should be tagged as "researcher"
    sub_thinking = [t for t in thinking_events if t.get("agent_id") == "researcher"]
    assert len(sub_thinking) >= 1, (
        f"Expected thinking tagged 'researcher', got agent_ids: "
        f"{[t.get('agent_id') for t in thinking_events]}"
    )


@pytest.mark.asyncio
async def test_event_bus_none_still_works():
    """When event_bus is None (no multi-agent), adapter still works normally."""

    async def raw_stream():
        yield {"type": "thinking_delta", "content": "Thinking..."}
        yield {"type": "text_delta", "content": "Hello!"}

    adapter = SeeCrabAdapter(brain=None, user_messages=["hello"])
    events: list[dict] = []
    async for e in adapter.transform(raw_stream(), reply_id="r2", event_bus=None):
        events.append(e)

    types = [e["type"] for e in events]
    assert "done" in types
    assert events[-1]["type"] == "done"
    assert any(e["type"] == "ai_text" for e in events)
