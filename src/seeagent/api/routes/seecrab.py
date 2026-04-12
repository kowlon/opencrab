# src/seeagent/api/routes/seecrab.py
"""SeeCrab API routes: SSE streaming chat + session management."""
from __future__ import annotations

from typing import Any

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..schemas_seecrab import (
    SeeCrabAnswerRequest,
    SeeCrabChatRequest,
    SeeCrabSessionUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/seecrab")

_BP_START_COMMANDS = {
    "进入最佳实践",
    "最佳实践模式",
    "开始最佳实践",
}

# Strict next: always match, return "next"
_BP_NEXT_COMMANDS_STRICT = {
    "进入下一步",
    "下一步",
    "继续执行",
    "继续",
    "好的继续",
    "开始下一步",
    "执行下一步",
}

# Loose next: only match when active BP exists, return "next_loose"
_BP_NEXT_COMMANDS_LOOSE = {
    "好",
    "没问题",
    "ok",
    "确认",
    "好的下一步",
}

_BP_CANCEL_COMMANDS = {
    "取消最佳实践",
    "终止最佳实践",
    "取消任务",
    "终止任务",
    "停止最佳实践",
    "退出最佳实践",
}

# Resume intent keywords: skip BP matcher, let agent handle via bp_switch_task
_BP_RESUME_INTENT_KEYWORDS = {"恢复", "继续之前", "回到之前"}

def _upsert_step_card(cards: list[dict], event: dict) -> None:
    """Upsert a step_card event into the cards list by step_id."""
    step_id = event.get("step_id")
    card = {k: v for k, v in event.items() if k != "type"}
    for i, c in enumerate(cards):
        if c.get("step_id") == step_id:
            cards[i] = card
            return
    cards.append(card)


# ── Busy-lock (per-conversation, same pattern as chat.py) ──

_busy_locks: dict[str, tuple[str, float]] = {}  # conv_id → (client_id, timestamp)
_busy_lock_mutex = asyncio.Lock()
_busy_thread_lock = __import__("threading").Lock()
_LOCK_TTL = 600  # seconds — consistent with chat.py BUSY_TIMEOUT_SECONDS


async def _mark_busy(conv_id: str, client_id: str) -> bool:
    """Try to acquire busy-lock. Returns True if acquired."""
    async with _busy_lock_mutex:
        _expire_stale_locks()
        if conv_id in _busy_locks:
            existing_client, _ = _busy_locks[conv_id]
            if existing_client != client_id:
                return False
        _busy_locks[conv_id] = (client_id, time.time())
        return True


def _clear_busy(conv_id: str) -> None:
    # Use thread-safe lock to support cross-loop calls (same pattern as chat.py)
    with _busy_thread_lock:
        _busy_locks.pop(conv_id, None)


def _expire_stale_locks() -> None:
    now = time.time()
    expired = [k for k, (_, ts) in _busy_locks.items() if now - ts > _LOCK_TTL]
    for k in expired:
        del _busy_locks[k]


async def _get_agent(request: Request, conversation_id: str | None, profile_id: str | None = None):
    """Get per-session agent from pool, or fallback to global agent."""
    pool = getattr(request.app.state, "agent_pool", None)
    if pool is not None and conversation_id:
        try:
            return pool.get_or_create(conversation_id, profile_id)
        except Exception:
            pass
    return getattr(request.app.state, "agent", None)


def _normalize_bp_command(message: str) -> str:
    punct = " \t\r\n，。！？,.!?：:；;\"'`（）()【】[]"
    return "".join(ch for ch in (message or "").strip().lower() if ch not in punct)


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


def _has_bp_next_step(snap) -> bool:
    if not snap:
        return False
    total = len(snap.bp_config.subtasks) if snap.bp_config else len(snap.subtask_statuses or {})
    if total <= 0:
        return False
    return int(getattr(snap, "current_subtask_index", 0) or 0) < total


def _resolve_chat_bp_instance(sm, session_id: str):
    if not sm:
        return None
    active = sm.get_active(session_id)
    if active:
        return active
    try:
        from seeagent.bestpractice.models import BPStatus

        suspended = [
            snap for snap in sm.get_all_for_session(session_id) if snap.status == BPStatus.SUSPENDED
        ]
    except Exception:
        return None
    if not suspended:
        return None
    suspended.sort(
        key=lambda snap: (snap.suspended_at or 0.0, snap.created_at or 0.0),
        reverse=True,
    )
    return suspended[0]


async def _extract_input_from_query(
    brain: Any,
    user_query: str,
    input_schema: dict,
) -> dict:
    """用 LLM 从用户原始 query 中提取符合 input_schema 的结构化参数。"""
    if not brain or not user_query or not input_schema:
        return {}

    branches = input_schema.get("oneOf") or input_schema.get("anyOf")
    is_multi_branch = bool(branches)
    if not is_multi_branch:
        branches = [input_schema]

    branch_desc_list = []
    for idx, branch in enumerate(branches):
        props = branch.get("properties", {})
        if not props:
            continue

        fields = "\n".join(
            f"- {name}: {info.get('description', '无描述')} (type: {info.get('type', 'string')})"
            for name, info in props.items()
        )

        if is_multi_branch:
            title = branch.get("title", f"分支 {idx + 1}")
            desc = branch.get("description", "无描述")
            branch_desc_list.append(f"### {title}\n描述：{desc}\n字段定义：\n{fields}")
        else:
            branch_desc_list.append(fields)

    if not branch_desc_list:
        return {}

    all_branches_desc = "\n\n".join(branch_desc_list)

    if is_multi_branch:
        instruction = "分析以下对话上下文，判断其符合哪一种意图分支，并仅提取该分支下定义的字段。"
        schema_section = f"## 可选意图分支\n{all_branches_desc}"
    else:
        instruction = "从以下对话上下文中提取所需的字段。"
        schema_section = f"## 字段定义\n{all_branches_desc}"

    prompt = (
        f"{instruction}\n"
        "输出一个 JSON 对象。只提取明确提到或可推断的字段，没有提到的字段不要包含。\n"
        "只输出 JSON，不要其他文字。\n\n"
        f"{schema_section}\n\n"
        f"## 对话上下文\n{user_query}"
    )

    try:
        from seeagent.bestpractice.engine import BPEngine

        resp = await brain.think_lightweight(prompt, max_tokens=512)
        text = resp.content if hasattr(resp, "content") else str(resp)
        parsed = BPEngine._parse_output(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception as e:
        logger.warning(f"[BP] Failed to extract input from query: {e}")
    return {}


async def _llm_extract_answer_fields(
    history_context: str,
    missing_fields: list[str],
    input_schema: dict,
    brain,
) -> dict:
    """从对话上下文中提取指定的缺失字段值。"""
    if not brain or not missing_fields:
        return {}

    from seeagent.bestpractice.models import collect_all_properties

    props = collect_all_properties(input_schema)
    fields_desc = "\n".join(
        f"- {name}: {props.get(name, {}).get('description', '无描述')} "
        f"(type: {props.get(name, {}).get('type', 'string')})"
        for name in missing_fields
    )
    prompt = (
        "根据以下对话上下文，提取所需的补充参数字段。\n"
        "输出一个 JSON 对象。只提取上下文中明确提到或可推断的字段，没有提到的字段不要包含。\n"
        "只输出 JSON，不要其他文字。\n\n"
        f"## 需要提取的字段\n{fields_desc}\n\n"
        f"## 对话上下文\n{history_context}"
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


async def _stream_bp_start_from_chat(
    request: Request,
    *,
    session_id: str,
    bp_id: str,
    run_mode_str: str,
    input_data: dict,
    session,
    session_manager,
    disconnect_event: asyncio.Event,
):
    from seeagent.api.routes.bestpractice import (
        _bp_clear_busy,
        _bp_mark_busy,
        _bp_renew_busy,
        _collect_reply_state,
        _new_reply_state,
        _persist_bp_to_session,
    )
    from seeagent.bestpractice.facade import (
        get_bp_config_loader,
        get_bp_engine,
        get_bp_state_manager,
    )
    from seeagent.bestpractice.models import BPStatus, RunMode

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        yield {"type": "error", "message": "BP system not initialized", "code": "bp"}
        yield {"type": "done"}
        return
    loader = get_bp_config_loader()
    bp_config = loader.configs.get(bp_id) if loader and loader.configs else None
    if not bp_config:
        yield {"type": "error", "message": f"BP '{bp_id}' not found", "code": "bp"}
        yield {"type": "done"}
        return
    lock_id = uuid.uuid4().hex
    if not await _bp_mark_busy(session_id, "seecrab_bp_start", lock_id):
        yield {"type": "error", "message": "Session is busy", "code": "bp"}
        yield {"type": "done"}
        return

    run_mode = RunMode(run_mode_str) if run_mode_str in ("manual", "auto") else RunMode.MANUAL
    reply_state = _new_reply_state()
    full_reply: list[str] = []
    instance_id: str | None = None

    # Resume check: if there's a suspended same-bp_id instance whose initial_input
    # matches the incoming input_data (or no new input), resume instead of creating new.
    _sm_sid = session.id if session else session_id
    suspended_same = [
        s
        for s in sm.get_all_for_session(_sm_sid)
        if s.bp_id == bp_id and s.status == BPStatus.SUSPENDED
    ]
    if suspended_same:
        # Prefer instance with matching initial_input; fall back to most recently suspended
        if input_data:
            target = next(
                (s for s in suspended_same if (s.initial_input or {}) == input_data),
                max(suspended_same, key=lambda s: s.suspended_at or 0.0),
            )
        else:
            target = max(suspended_same, key=lambda s: s.suspended_at or 0.0)
        new_input_differs = bool(input_data) and input_data != (target.initial_input or {})
        if not new_input_differs:
            result = await engine.switch(target.instance_id, session)
            if result.get("success"):
                instance_id = target.instance_id
                try:
                    async for event in engine.advance(target.instance_id, session):
                        if disconnect_event.is_set():
                            break
                        yield event
                        _collect_reply_state(event, reply_state, full_reply)
                        if event.get("type") in ("bp_subtask_complete", "bp_progress"):
                            _bp_renew_busy(session_id)
                    snap = sm.get(instance_id)
                    is_suspended = (
                        snap and getattr(snap.status, "value", snap.status) == "suspended"
                    )
                    if not is_suspended:
                        _persist_bp_to_session(
                            session,
                            instance_id,
                            sm,
                            reply_state=reply_state,
                            full_reply="".join(full_reply),
                            session_manager=session_manager,
                        )
                    sm.clear_pending_offer(session_id)
                    yield {"type": "done"}
                except Exception as e:
                    yield {"type": "error", "message": str(e), "code": "bp"}
                    yield {"type": "done"}
                finally:
                    _bp_clear_busy(session_id, lock_id)
                return

    try:
        async for event in engine.start(bp_config, session, input_data, run_mode):
            if disconnect_event.is_set():
                break
            if event.get("type") == "bp_instance_created":
                instance_id = event.get("instance_id")
            yield event
            _collect_reply_state(event, reply_state, full_reply)
            if event.get("type") in ("bp_subtask_complete", "bp_progress"):
                _bp_renew_busy(session_id)
        snap = sm.get(instance_id) if instance_id else None
        is_suspended = snap and getattr(snap.status, "value", snap.status) == "suspended"
        if instance_id and not is_suspended:
            _persist_bp_to_session(
                session,
                instance_id,
                sm,
                reply_state=reply_state,
                full_reply="".join(full_reply),
                session_manager=session_manager,
            )
        sm.clear_pending_offer(session_id)
        yield {"type": "done"}
    except Exception as e:
        yield {"type": "error", "message": str(e), "code": "bp"}
        yield {"type": "done"}
    finally:
        _bp_clear_busy(session_id, lock_id)


async def _stream_bp_next_from_chat(
    request: Request,
    *,
    session_id: str,
    instance_id: str,
    session,
    session_manager,
    disconnect_event: asyncio.Event,
):
    from seeagent.api.routes.bestpractice import (
        _bp_clear_busy,
        _bp_mark_busy,
        _bp_renew_busy,
        _collect_reply_state,
        _ensure_bp_restored,
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
    await _ensure_bp_restored(request, session_id, sm)
    snap = sm.get(instance_id)
    if not snap:
        yield {"type": "ai_text", "content": "当前没有可继续的最佳实践任务。"}
        yield {"type": "done"}
        return
    if not _has_bp_next_step(snap):
        yield {"type": "ai_text", "content": "当前最佳实践已完成或没有下一步可执行。"}
        yield {"type": "done"}
        return
    lock_id = uuid.uuid4().hex
    if not await _bp_mark_busy(session_id, "seecrab_bp_next", lock_id):
        yield {"type": "error", "message": "Session is busy", "code": "bp"}
        yield {"type": "done"}
        return
    resume = await engine.resume_if_needed(instance_id, session)
    if not resume.get("success"):
        yield {
            "type": "error",
            "message": resume.get("error", "Failed to resume BP instance"),
            "code": resume.get("code", "bp_resume_failed"),
            "active_instance_id": resume.get("active_instance_id"),
        }
        yield {"type": "done"}
        _bp_clear_busy(session_id, lock_id)
        return

    reply_state = _new_reply_state()
    full_reply: list[str] = []
    try:
        async for event in engine.advance(instance_id, session):
            if disconnect_event.is_set():
                break
            yield event
            _collect_reply_state(event, reply_state, full_reply)
            if event.get("type") in ("bp_subtask_complete", "bp_progress"):
                _bp_renew_busy(session_id)
        snap = sm.get(instance_id)
        is_suspended = snap and getattr(snap.status, "value", snap.status) == "suspended"
        if not is_suspended:
            _persist_bp_to_session(
                session,
                instance_id,
                sm,
                reply_state=reply_state,
                full_reply="".join(full_reply),
                session_manager=session_manager,
            )
        yield {"type": "done"}
    except Exception as e:
        yield {"type": "error", "message": str(e), "code": "bp"}
        yield {"type": "done"}
    finally:
        _bp_clear_busy(session_id, lock_id)


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

    # Cancel running delegate task if any
    if session and hasattr(session, "context"):
        dt = getattr(session.context, "_bp_delegate_task", None)
        if dt and not dt.done():
            dt.cancel()

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
    lock_id = uuid.uuid4().hex
    if not await _bp_mark_busy(session_id, "seecrab_bp_answer", lock_id):
        yield {"type": "error", "message": "Session is busy", "code": "bp"}
        yield {"type": "done"}
        return
    resume = await engine.resume_if_needed(instance_id, session)
    if not resume.get("success"):
        yield {
            "type": "error",
            "message": resume.get("error", "Failed to resume BP instance"),
            "code": resume.get("code", "bp_resume_failed"),
            "active_instance_id": resume.get("active_instance_id"),
        }
        yield {"type": "done"}
        _bp_clear_busy(session_id, lock_id)
        return

    reply_state = _new_reply_state()
    full_reply: list[str] = []
    try:
        async for event in engine.answer(instance_id, subtask_id, data, session):
            if disconnect_event.is_set():
                break
            yield event
            _collect_reply_state(event, reply_state, full_reply)

        snap = sm.get(instance_id)
        is_suspended = snap and getattr(snap.status, "value", snap.status) == "suspended"
        if not is_suspended:
            _persist_bp_to_session(
                session,
                instance_id,
                sm,
                reply_state=reply_state,
                full_reply="".join(full_reply),
                session_manager=session_manager,
            )
        yield {"type": "done"}
    except Exception as e:
        yield {"type": "error", "message": str(e), "code": "bp"}
        yield {"type": "done"}
    finally:
        _bp_clear_busy(session_id, lock_id)


@router.post("/chat")
async def seecrab_chat(body: SeeCrabChatRequest, request: Request):
    """SSE streaming chat via SeeCrabAdapter."""
    logger.info(f"[BP-DEBUG] /chat received: msg={body.message!r}, conv_id={body.conversation_id}")
    # Get agent from pool (per-session isolation) or fallback to global
    agent = await _get_agent(request, body.conversation_id, body.agent_profile_id)
    if agent is None:
        return JSONResponse({"error": "Agent not initialized"}, status_code=503)

    session_manager = getattr(request.app.state, "session_manager", None)
    conversation_id = body.conversation_id or f"seecrab_{uuid.uuid4().hex[:12]}"
    client_id = body.client_id or uuid.uuid4().hex[:8]

    # Busy-lock check
    if not await _mark_busy(conversation_id, client_id):
        logger.warning(
            f"[BP-DEBUG] 409 BUSY LOCK for conv_id={conversation_id}, client_id={client_id}"
        )
        return JSONResponse(
            {"error": "Another request is already processing this conversation"},
            status_code=409,
        )

    async def generate():
        from seeagent.api.adapters.seecrab_adapter import SeeCrabAdapter

        # Disconnect watcher
        disconnect_event = asyncio.Event()
        session = None

        async def _disconnect_watcher():
            while not disconnect_event.is_set():
                if await request.is_disconnected():
                    logger.info(f"[SeeCrab] Client disconnected: {conversation_id}")
                    try:
                        from seeagent.bestpractice.facade import (
                            get_bp_engine as _get_bp_engine,
                            get_bp_state_manager as _get_bp_sm,
                        )

                        _bp_engine = _get_bp_engine()
                        _bp_sm = _get_bp_sm()
                        _bp_active = (
                            _resolve_chat_bp_instance(
                                _bp_sm,
                                session.id if session else conversation_id,
                            )
                            if _bp_sm
                            else None
                        )
                        if _bp_engine and _bp_active:
                            await _bp_engine.request_suspend(
                                _bp_active.instance_id,
                                session,
                                "disconnect",
                            )
                    except Exception:
                        pass
                    if hasattr(agent, "cancel_current_task"):
                        agent.cancel_current_task("客户端断开连接", session_id=conversation_id)
                    disconnect_event.set()
                    return
                await asyncio.sleep(2)

        watcher_task = asyncio.create_task(_disconnect_watcher())
        adapter = None

        try:
            # SSE comment heartbeat — forces the first byte out immediately,
            # preventing HTTP/2 proxies / LBs from timing out during init.
            yield ": heartbeat\n\n"

            # Resolve session
            session_messages: list[dict] = []
            user_messages: list[str] = []
            history_context: str = ""
            if session_manager and conversation_id:
                try:
                    session = session_manager.get_session(
                        channel="seecrab",
                        chat_id=conversation_id,
                        user_id="seecrab_user",
                        create_if_missing=True,
                    )
                    if session and body.message:
                        # Persist active BP reply BEFORE user message
                        # so message order is correct on page refresh
                        try:
                            from seeagent.bestpractice.facade import (
                                get_bp_state_manager as _get_bp_sm,
                            )

                            _bp_sm = _get_bp_sm()
                            if _bp_sm:
                                _bp_active = _bp_sm.get_active(session.id)
                                if _bp_active:
                                    from seeagent.api.routes.bestpractice import (
                                        _persist_bp_to_session,
                                    )

                                    _persist_bp_to_session(
                                        session,
                                        _bp_active.instance_id,
                                        _bp_sm,
                                        session_manager=session_manager,
                                    )
                        except Exception:
                            pass
                        session.add_message("user", body.message)
                        session_messages = (
                            list(session.context.messages) if hasattr(session, "context") else []
                        )
                        user_messages = [
                            m.get("content", "")
                            for m in session_messages
                            if m.get("role") == "user"
                        ][-5:]

                        history_lines = []
                        for m in session_messages[-10:]:
                            role_name = "用户" if m.get("role") == "user" else "助手"
                            history_lines.append(f"[{role_name}]: {m.get('content', '')}")
                        history_context = "\n".join(history_lines)

                        session_manager.mark_dirty()
                except Exception as e:
                    logger.warning(f"[SeeCrab] Session error: {e}")

            if not user_messages and body.message:
                user_messages = [body.message]
                history_context = f"[用户]: {body.message}"

            # 使用 conversation_id(=chat_id)，与前端 activeSessionId 一致，
            # 确保 /api/bp/start 能通过 get_pending_offer(session_id) 找到 pending_offer
            bp_session_id = conversation_id

            # BPStateManager 内部用 session.id 存储实例（含 channel/timestamp/random）
            # 而 bp_session_id = conversation_id（无时间戳后缀）
            # 两者不同：直接用 bp_session_id 做 get_all_for_session 查询会返回空
            # 所以 state_manager 内部查询（restore / cooldown / matcher）统一用 session.id
            _sm_sid = session.id if session else bp_session_id
            logger.info(
                f"[BP] session_id mapping: bp_session_id={bp_session_id!r} session.id={_sm_sid!r}"
            )

            # ── Step 0: BP state restoration + cooldown tick ──
            from seeagent.bestpractice.facade import get_bp_state_manager

            bp_sm = get_bp_state_manager()
            if bp_sm:
                from seeagent.api.routes.bestpractice import _ensure_bp_restored

                await _ensure_bp_restored(request, _sm_sid, bp_sm)
                bp_sm.tick_cooldown(_sm_sid)

            # Pre-fetch brain for LLM operations
            brain = getattr(agent, "brain", None)

            bp_cmd = _match_bp_command(body.message or "")
            if bp_cmd:
                if not bp_sm:
                    from seeagent.bestpractice.facade import get_bp_state_manager

                    bp_sm = get_bp_state_manager()

                if bp_cmd == "start":
                    pending_offer = bp_sm.get_pending_offer(bp_session_id) if bp_sm else None
                    if pending_offer and pending_offer.get("bp_id"):
                        # Prefer pre-extracted input, fallback to LLM extraction
                        extracted_input = pending_offer.get("extracted_input", {})
                        if not extracted_input:
                            user_query = pending_offer.get("user_query", "")
                            from seeagent.bestpractice.facade import get_bp_config_loader
                            from seeagent.api.routes.bestpractice import _build_combined_user_schema

                            loader = get_bp_config_loader()
                            bp_config = (
                                loader.configs.get(pending_offer.get("bp_id"))
                                if loader and loader.configs
                                else None
                            )
                            combined_schema = (
                                _build_combined_user_schema(bp_config)
                                if bp_config and user_query
                                else None
                            )
                            if user_query and combined_schema:
                                extracted_input = await _extract_input_from_query(
                                    brain,
                                    user_query,
                                    combined_schema,
                                )
                                logger.info(f"[BP] Extracted input from query: {extracted_input}")
                        async for event in _stream_bp_start_from_chat(
                            request,
                            session_id=bp_session_id,
                            bp_id=pending_offer.get("bp_id", ""),
                            run_mode_str=pending_offer.get("default_run_mode", "manual"),
                            input_data=extracted_input,
                            session=session,
                            session_manager=session_manager,
                            disconnect_event=disconnect_event,
                        ):
                            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        return
                    fallback = {
                        "type": "ai_text",
                        "content": "当前没有可进入的最佳实践，请先触发最佳实践推荐。",
                    }
                    yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
                    yield 'data: {"type": "done"}\n\n'
                    return

                if bp_cmd == "cancel":
                    active = bp_sm.get_active(_sm_sid) if bp_sm else None
                    if active:
                        bp_name = active.bp_config.name if active.bp_config else active.bp_id
                        async for event in _cancel_bp_from_chat(
                            session_id=_sm_sid,
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
                    current_bp = _resolve_chat_bp_instance(bp_sm, _sm_sid)
                    # next_loose without resumable BP → fall through to agent
                    if bp_cmd == "next_loose" and not current_bp:
                        pass  # fall through
                    elif current_bp:
                        # Check waiting_input — can't advance, need params first
                        has_waiting = any(
                            s == "waiting_input" for s in current_bp.subtask_statuses.values()
                        )
                        if has_waiting:
                            fallback = {
                                "type": "ai_text",
                                "content": "当前子任务正在等待您补充参数，请先提供所需信息，或输入“取消任务”退出。",
                            }
                            yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
                            yield 'data: {"type": "done"}\n\n'
                            return
                        if _has_bp_next_step(current_bp):
                            async for event in _stream_bp_next_from_chat(
                                request,
                                session_id=bp_session_id,
                                instance_id=current_bp.instance_id,
                                session=session,
                                session_manager=session_manager,
                                disconnect_event=disconnect_event,
                            ):
                                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                            return
                        fallback = {
                            "type": "ai_text",
                            "content": "当前最佳实践已完成或没有下一步可执行。",
                        }
                        yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
                        yield 'data: {"type": "done"}\n\n'
                        return
                    elif bp_cmd == "next":
                        fallback = {"type": "ai_text", "content": "当前没有可继续的最佳实践任务。"}
                        yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
                        yield 'data: {"type": "done"}\n\n'
                        return

            # ── Step 2: waiting_input → route to answer ──
            if not bp_sm:
                from seeagent.bestpractice.facade import get_bp_state_manager

                bp_sm = get_bp_state_manager()
            current_bp = _resolve_chat_bp_instance(bp_sm, _sm_sid)
            if current_bp:
                waiting_subtask_id = None
                for st_id, st_status in current_bp.subtask_statuses.items():
                    if st_status == "waiting_input":
                        waiting_subtask_id = st_id
                        break
                if waiting_subtask_id:
                    # Determine missing fields for smart extraction
                    subtask_config = None
                    for st in current_bp.bp_config.subtasks:
                        if st.id == waiting_subtask_id:
                            subtask_config = st
                            break

                    data = {}
                    still_missing = []
                    if subtask_config:
                        from seeagent.bestpractice.engine import LinearScheduler
                        from seeagent.bestpractice.facade import get_bp_engine

                        scheduler = LinearScheduler(current_bp.bp_config, current_bp)
                        resolved_input = scheduler.resolve_input(waiting_subtask_id)

                        engine = get_bp_engine()
                        missing_fields, matched_schema = engine._check_input_completeness(
                            subtask_config, resolved_input
                        )
                        still_missing = missing_fields

                        target_schema = matched_schema or subtask_config.input_schema

                        if len(still_missing) == 1:
                            data = {still_missing[0]: body.message}
                        elif len(still_missing) > 1:
                            data = await _llm_extract_answer_fields(
                                history_context,
                                still_missing,
                                target_schema,
                                brain,
                            )

                    if not data:
                        field_hints = ", ".join(still_missing) if still_missing else "必填参数"
                        fallback = {
                            "type": "ai_text",
                            "content": f"无法从您的消息中识别参数，请按字段提供：{field_hints}",
                        }
                        yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
                        yield 'data: {"type": "done"}\n\n'
                        return

                    async for event in _stream_bp_answer_from_chat(
                        request,
                        session_id=_sm_sid,
                        instance_id=current_bp.instance_id,
                        subtask_id=waiting_subtask_id,
                        data=data,
                        session=session,
                        session_manager=session_manager,
                        disconnect_event=disconnect_event,
                    ):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    return

            # Suspend active BP before keyword/LLM match so that the matcher's
            # active-instance guard doesn't block re-triggering when the user
            # explicitly requests a new BP while one is already running.
            try:
                from seeagent.bestpractice.facade import (
                    get_bp_engine as _get_engine_pre,
                    get_bp_state_manager as _get_sm_pre,
                )

                _sm_pre = _get_sm_pre()
                _engine_pre = _get_engine_pre()
                if _sm_pre and _engine_pre:
                    _pre_session_id = session.id if session else bp_session_id
                    _active_pre = _sm_pre.get_active(_pre_session_id)
                    logger.info(
                        f"[BP] pre-match: bp_session_id={bp_session_id} "
                        f"_pre_session_id={_pre_session_id} "
                        f"found={_active_pre.instance_id if _active_pre else None}"
                    )
                    if _active_pre:
                        await _engine_pre.request_suspend(
                            _active_pre.instance_id,
                            session,
                            "free_form_chat",
                        )
                        logger.info(
                            f"[BP] Pre-match suspended {_active_pre.instance_id} "
                            f"for free-form chat message"
                        )
            except Exception:
                pass

            # Resume intent bypass: if user says "恢复" etc., skip matcher
            # and let agent handle via bp_switch_task with full BP state context.
            _has_resume_intent = any(
                kw in (body.message or "") for kw in _BP_RESUME_INTENT_KEYWORDS
            )

            try:
                bp_match = None
                if not _has_resume_intent:
                    from seeagent.bestpractice.facade import match_bp_from_message

                    bp_match = match_bp_from_message(body.message or "", _sm_sid)
                    # Step 4: LLM fallback if keyword didn't match
                    if not bp_match and brain:
                        from seeagent.bestpractice.facade import llm_match_bp_from_message

                        bp_match = await llm_match_bp_from_message(
                            body.message or "",
                            _sm_sid,
                            brain,
                            history_context,
                        )
                if bp_match:
                    bp_name = bp_match["bp_name"]
                    bp_id = bp_match["bp_id"]
                    subtask_names = " → ".join(s["name"] for s in bp_match.get("subtasks", []))
                    question = (
                        f"检测到您的需求匹配最佳实践「{bp_name}」，"
                        f"该任务包含 {bp_match['subtask_count']} 个子任务："
                        f"{subtask_names}。是否使用最佳实践流程？"
                    )

                    # Emit session_title for first message
                    is_first_message = len(user_messages) <= 1
                    if is_first_message and body.message:
                        title = body.message[:30] + ("..." if len(body.message) > 30 else "")
                        title_event = json.dumps(
                            {
                                "type": "session_title",
                                "session_id": conversation_id,
                                "title": title,
                            },
                            ensure_ascii=False,
                        )
                        yield f"data: {title_event}\n\n"
                        if session:
                            session.metadata["title"] = title
                            session_manager.mark_dirty()

                    ask_event = json.dumps(
                        {
                            "type": "bp_offer",
                            "bp_id": bp_id,
                            "bp_name": bp_name,
                            "subtasks": bp_match.get("subtasks", []),
                            "default_run_mode": "manual",
                        },
                        ensure_ascii=False,
                    )
                    yield f"data: {ask_event}\n\n"

                    # Mark this BP as offered so it won't re-trigger in this session
                    from seeagent.bestpractice.facade import get_bp_state_manager

                    bp_sm = get_bp_state_manager()
                    if bp_sm:
                        bp_sm.mark_bp_offered(_sm_sid, bp_id)
                        bp_sm.set_pending_offer(
                            bp_session_id,
                            {
                                "bp_id": bp_id,
                                "bp_name": bp_name,
                                "subtasks": bp_match.get("subtasks", []),
                                "default_run_mode": "manual",
                                "user_query": history_context,
                                "first_input_schema": bp_match.get("first_input_schema"),
                                "extracted_input": bp_match.get("extracted_input", {}),
                            },
                        )

                    if session:
                        session.add_message(
                            "assistant",
                            question,
                            reply_state={
                                "bp_offer": {
                                    "bp_id": bp_id,
                                    "bp_name": bp_name,
                                    "subtasks": bp_match.get("subtasks", []),
                                }
                            },
                        )
                        if session_manager:
                            session_manager.mark_dirty()

                    yield 'data: {"type": "done"}\n\n'
                    return  # Skip LLM stream — wait for user choice
            except Exception as e:
                logger.warning(f"[BP] match phase failed: {e}", exc_info=True)

            brain = getattr(agent, "brain", None)
            adapter = SeeCrabAdapter(brain=brain, user_messages=user_messages)
            event_bus = asyncio.Queue()
            if session and hasattr(session, "context"):
                session.context._sse_event_bus = event_bus
            reply_id = f"reply_{uuid.uuid4().hex[:12]}"

            raw_stream = agent.chat_with_session_stream(
                message=body.message,
                session_messages=session_messages,
                session_id=conversation_id,
                session=session,
                plan_mode=body.plan_mode,
                endpoint_override=body.endpoint,
                thinking_mode=body.thinking_mode,
                thinking_depth=body.thinking_depth,
                attachments=body.attachments,
            )
            logger.info(
                f"[BP-DEBUG] agent.chat_with_session_stream started for msg={body.message!r}"
            )

            # Dual-loop bridge if needed
            try:
                from seeagent.core.engine_bridge import engine_stream, is_dual_loop

                if is_dual_loop():
                    raw_stream = engine_stream(raw_stream)
            except ImportError:
                pass

            # Emit session_title from first user message
            is_first_message = len(user_messages) <= 1
            if is_first_message and body.message:
                title = body.message[:30] + ("..." if len(body.message) > 30 else "")
                title_event = json.dumps(
                    {
                        "type": "session_title",
                        "session_id": conversation_id,
                        "title": title,
                    },
                    ensure_ascii=False,
                )
                yield f"data: {title_event}\n\n"
                # Persist title in metadata (survives to_dict/from_dict)
                if session:
                    session.metadata["title"] = title
                    session_manager.mark_dirty()

            full_reply = ""
            reply_state = {
                "thinking": "",
                "step_cards": [],
                "agent_thinking": {},
                "agent_summaries": {},
                "plan_checklist": None,
                "timer": {"ttft": None, "total": None},
                "bp_progress": None,
                "bp_subtask_output": None,
            }

            from seeagent.api.sse_utils import sse_heartbeat_stream, _HEARTBEAT_COMMENT

            async for event in sse_heartbeat_stream(
                adapter.transform(raw_stream, reply_id=reply_id, event_bus=event_bus)
            ):
                if disconnect_event.is_set():
                    break
                if event is None:
                    yield _HEARTBEAT_COMMENT
                    continue
                payload = json.dumps(event, ensure_ascii=False)
                yield f"data: {payload}\n\n"

                # Collect reply_state for persistence
                etype = event.get("type")
                if etype == "ai_text":
                    aid = event.get("agent_id")
                    if aid and aid != "main":
                        reply_state["agent_summaries"][aid] = reply_state["agent_summaries"].get(
                            aid, ""
                        ) + event.get("content", "")
                    else:
                        full_reply += event.get("content", "")
                elif etype == "thinking":
                    aid = event.get("agent_id")
                    if aid and aid != "main":
                        at = reply_state["agent_thinking"].setdefault(
                            aid,
                            {"content": "", "done": False},
                        )
                        at["content"] += event.get("content", "")
                    else:
                        reply_state["thinking"] += event.get("content", "")
                elif etype == "step_card":
                    _upsert_step_card(reply_state["step_cards"], event)
                elif etype == "plan_checklist":
                    reply_state["plan_checklist"] = event.get("steps")
                elif etype == "timer_update":
                    phase = event.get("phase")
                    if phase in reply_state["timer"] and event.get("state") == "done":
                        reply_state["timer"][phase] = event.get("value")
                elif etype == "bp_progress":
                    reply_state["bp_progress"] = event
                elif etype in ("bp_subtask_output", "bp_subtask_complete"):
                    reply_state["bp_subtask_output"] = event

            # Save assistant reply with reply_state to session
            if session and full_reply:
                try:
                    session.add_message("assistant", full_reply, reply_state=reply_state)
                    if session_manager:
                        session_manager.mark_dirty()
                except Exception:
                    pass

        except Exception as e:
            logger.exception(f"[SeeCrab] Chat error: {e}")
            err = json.dumps(
                {"type": "error", "message": str(e), "code": "internal"},
                ensure_ascii=False,
            )
            yield f"data: {err}\n\n"
            yield 'data: {"type": "done"}\n\n'
        finally:
            # Cleanup: flush aggregator to cancel any pending title tasks
            if adapter is not None:
                try:
                    await adapter.aggregator.flush()
                except Exception:
                    pass
            # Remove stale event_bus reference from session context
            if session and hasattr(session, "context"):
                session.context._sse_event_bus = None
            watcher_task.cancel()
            try:
                await watcher_task
            except (asyncio.CancelledError, Exception):
                pass
            _clear_busy(conversation_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions")
async def list_sessions(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    title: str = "",
):
    """List conversation sessions with pagination and optional title search."""
    sm = getattr(request.app.state, "session_manager", None)
    if sm is None:
        return JSONResponse({"total": 0, "limit": limit, "offset": offset, "sessions": []})
    try:
        sessions = sm.list_sessions(channel="seecrab")
        sessions.sort(key=lambda s: s.last_active, reverse=True)
        if title:
            keyword = title.lower()
            sessions = [
                s for s in sessions if keyword in s.metadata.get("title", s.chat_id).lower()
            ]
        total = len(sessions)
        sessions = sessions[offset : offset + limit]
        result = []
        for s in sessions:
            messages = s.context.messages if hasattr(s, "context") else []
            last_msg = ""
            if messages:
                # Prefer last assistant reply, fallback to last user message
                for m in reversed(messages):
                    role = m.get("role", "")
                    if role == "assistant":
                        last_msg = m.get("content", "")[:80]
                        break
                    if role == "user" and not last_msg:
                        last_msg = m.get("content", "")[:80]
            result.append(
                {
                    "id": s.chat_id,
                    "title": s.metadata.get("title", ""),
                    "pinned": s.metadata.get("pinned", False),
                    "icon": s.metadata.get("icon", ""),
                    "updated_at": getattr(s, "last_active", datetime.now()).timestamp() * 1000,
                    "message_count": len(messages),
                    "last_message": last_msg,
                }
            )
        return JSONResponse(
            {
                "total": total,
                "limit": limit,
                "offset": offset,
                "sessions": result,
            }
        )
    except Exception:
        return JSONResponse({"total": 0, "limit": limit, "offset": offset, "sessions": []})


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    """Get session detail with message history (for SSE reconnect state recovery)."""
    sm = getattr(request.app.state, "session_manager", None)
    if sm is None:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)
    try:
        session = sm.get_session(
            channel="seecrab",
            chat_id=session_id,
            user_id="seecrab_user",
            create_if_missing=False,
        )
        # Fallback: session_id may be the composite Session.id rather than chat_id
        if session is None:
            session = sm.get_session_by_id(session_id)
        if session is None:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        messages = []
        if hasattr(session, "context") and hasattr(session.context, "messages"):
            for m in session.context.messages:
                msg_dict = {
                    "role": m.get("role", ""),
                    "content": m.get("content", ""),
                    "timestamp": m.get("timestamp", 0),
                    "metadata": m.get("metadata", {}),
                }
                if m.get("reply_state"):
                    msg_dict["reply_state"] = m["reply_state"]
                messages.append(msg_dict)
        return JSONResponse(
            {
                "session_id": session_id,
                "title": session.metadata.get("title", ""),
                "pinned": session.metadata.get("pinned", False),
                "icon": session.metadata.get("icon", ""),
                "messages": messages,
            }
        )
    except Exception as e:
        logger.warning(f"[SeeCrab] Get session error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/sessions")
async def create_session(request: Request):
    """Create a new conversation session."""
    session_id = f"seecrab_{uuid.uuid4().hex[:12]}"
    sm = getattr(request.app.state, "session_manager", None)
    if sm:
        sm.get_session(
            channel="seecrab",
            chat_id=session_id,
            user_id="seecrab_user",
            create_if_missing=True,
        )
        sm.mark_dirty()
    return JSONResponse({"session_id": session_id})


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str,
    body: SeeCrabSessionUpdateRequest,
    request: Request,
):
    """Update session metadata (title, etc.)."""
    sm = getattr(request.app.state, "session_manager", None)
    if sm is None:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)
    session = sm.get_session(
        channel="seecrab",
        chat_id=session_id,
        user_id="seecrab_user",
        create_if_missing=False,
    )
    if session is None:
        session = sm.get_session_by_id(session_id)
    if session is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    if body.title is not None:
        session.set_metadata("title", body.title)
    if body.pinned is not None:
        session.set_metadata("pinned", body.pinned)
    if body.icon is not None:
        session.set_metadata("icon", body.icon)
    sm.mark_dirty()
    return JSONResponse({"status": "ok"})


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    """Delete a conversation session."""
    sm = getattr(request.app.state, "session_manager", None)
    if sm is None:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)
    session_key = f"seecrab:{session_id}:seecrab_user"
    if sm.close_session(session_key):
        return JSONResponse({"status": "ok"})
    # Fallback: session_id may be composite Session.id — resolve to session_key
    fallback = sm.get_session_by_id(session_id)
    if fallback and sm.close_session(fallback.session_key):
        return JSONResponse({"status": "ok"})
    return JSONResponse({"error": "Session not found"}, status_code=404)


@router.post("/answer")
async def answer_ask_user(body: SeeCrabAnswerRequest, request: Request):
    """Submit answer to ask_user event.

    The Agent's ask_user mechanism works through gateway.check_interrupt(),
    which only supports IM channels. For SeeCrab (desktop/web), the answer
    should be sent as a new /api/seecrab/chat message with the same
    conversation_id. This endpoint acknowledges the answer and instructs
    the client accordingly.
    """
    return JSONResponse(
        {
            "status": "ok",
            "conversation_id": body.conversation_id,
            "answer": body.answer,
            "hint": "Please send the answer as a new /api/seecrab/chat message with the same conversation_id",
        }
    )
