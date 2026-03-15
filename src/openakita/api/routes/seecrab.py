# src/openakita/api/routes/seecrab.py
"""SeeCrab API routes: SSE streaming chat + session management."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..schemas_seecrab import SeeCrabAnswerRequest, SeeCrabChatRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/seecrab")

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


@router.post("/chat")
async def seecrab_chat(body: SeeCrabChatRequest, request: Request):
    """SSE streaming chat via SeeCrabAdapter."""
    # Get agent from pool (per-session isolation) or fallback to global
    agent = await _get_agent(request, body.conversation_id, body.agent_profile_id)
    if agent is None:
        return JSONResponse({"error": "Agent not initialized"}, status_code=503)

    session_manager = getattr(request.app.state, "session_manager", None)
    conversation_id = body.conversation_id or f"seecrab_{uuid.uuid4().hex[:12]}"
    client_id = body.client_id or uuid.uuid4().hex[:8]

    # Busy-lock check
    if not await _mark_busy(conversation_id, client_id):
        return JSONResponse(
            {"error": "Another request is already processing this conversation"},
            status_code=409,
        )

    async def generate():
        from openakita.api.adapters.seecrab_adapter import SeeCrabAdapter

        # Disconnect watcher
        disconnect_event = asyncio.Event()

        async def _disconnect_watcher():
            while not disconnect_event.is_set():
                if await request.is_disconnected():
                    logger.info(f"[SeeCrab] Client disconnected: {conversation_id}")
                    if hasattr(agent, "cancel_current_task"):
                        agent.cancel_current_task("客户端断开连接", session_id=conversation_id)
                    disconnect_event.set()
                    return
                await asyncio.sleep(2)

        watcher_task = asyncio.create_task(_disconnect_watcher())
        adapter = None

        try:
            # Resolve session
            session = None
            session_messages: list[dict] = []
            user_messages: list[str] = []
            if session_manager and conversation_id:
                try:
                    session = session_manager.get_session(
                        channel="seecrab",
                        chat_id=conversation_id,
                        user_id="seecrab_user",
                        create_if_missing=True,
                    )
                    if session and body.message:
                        session.add_message("user", body.message)
                        session_messages = list(
                            session.context.messages
                        ) if hasattr(session, "context") else []
                        user_messages = [
                            m.get("content", "")
                            for m in session_messages
                            if m.get("role") == "user"
                        ][-5:]
                        session_manager.mark_dirty()
                except Exception as e:
                    logger.warning(f"[SeeCrab] Session error: {e}")

            if not user_messages and body.message:
                user_messages = [body.message]

            brain = getattr(agent, "brain", None)
            adapter = SeeCrabAdapter(brain=brain, user_messages=user_messages)
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

            # Dual-loop bridge if needed
            try:
                from openakita.core.engine_bridge import engine_stream, is_dual_loop
                if is_dual_loop():
                    raw_stream = engine_stream(raw_stream)
            except ImportError:
                pass

            full_reply = ""
            async for event in adapter.transform(raw_stream, reply_id=reply_id):
                if disconnect_event.is_set():
                    break
                payload = json.dumps(event, ensure_ascii=False)
                yield f"data: {payload}\n\n"
                if event.get("type") == "ai_text":
                    full_reply += event.get("content", "")

            # Save assistant reply to session
            if session and full_reply:
                try:
                    session.add_message("assistant", full_reply)
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
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions")
async def list_sessions(request: Request):
    """List conversation sessions."""
    sm = getattr(request.app.state, "session_manager", None)
    if sm is None:
        return JSONResponse({"sessions": []})
    try:
        sessions = sm.list_sessions(channel="seecrab")
        return JSONResponse({"sessions": [
            {
                "id": s.id,
                "title": getattr(s, "title", s.id),
                "updated_at": getattr(s, "updated_at", 0),
            }
            for s in sessions
        ]})
    except Exception:
        return JSONResponse({"sessions": []})


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
        if session is None:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        messages = []
        if hasattr(session, "context") and hasattr(session.context, "messages"):
            for m in session.context.messages:
                messages.append({
                    "role": m.get("role", ""),
                    "content": m.get("content", ""),
                    "timestamp": m.get("timestamp", 0),
                    "metadata": m.get("metadata", {}),
                })
        return JSONResponse({
            "session_id": session_id,
            "title": getattr(session, "title", session_id),
            "messages": messages,
        })
    except Exception as e:
        logger.warning(f"[SeeCrab] Get session error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/sessions")
async def create_session(request: Request):
    """Create a new conversation session."""
    session_id = f"seecrab_{uuid.uuid4().hex[:12]}"
    return JSONResponse({"session_id": session_id})


@router.post("/answer")
async def answer_ask_user(body: SeeCrabAnswerRequest, request: Request):
    """Submit answer to ask_user event.

    The Agent's ask_user mechanism works through gateway.check_interrupt(),
    which only supports IM channels. For SeeCrab (desktop/web), the answer
    should be sent as a new /api/seecrab/chat message with the same
    conversation_id. This endpoint acknowledges the answer and instructs
    the client accordingly.
    """
    return JSONResponse({
        "status": "ok",
        "conversation_id": body.conversation_id,
        "answer": body.answer,
        "hint": "Please send the answer as a new /api/seecrab/chat message with the same conversation_id",
    })
