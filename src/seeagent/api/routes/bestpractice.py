"""BP REST API: 状态查询、模式切换、前端启动。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from seeagent.bestpractice.facade import (
    get_bp_config_loader,
    get_bp_engine,
    get_bp_state_manager,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bp")


# ── BP state restoration (survives server restart) ────────────


async def _ensure_bp_restored(request: Request, session_id: str, sm) -> None:
    """Restore BP instances from SQLite (primary) or session metadata (fallback).

    After server restart, BPStateManager._instances is empty.
    Priority: SQLite → session.metadata["bp_state"] (legacy compatibility).
    """
    if not sm or not session_id:
        return
    # Already have instances for this session? Skip.
    if sm.get_all_for_session(session_id):
        return
    loader = get_bp_config_loader()
    config_map = dict(loader.configs) if loader and loader.configs else {}

    # Primary: restore from SQLite
    restored = await sm.restore_from_db(session_id, config_map=config_map)
    if restored:
        logger.info(f"[BP] Restored {restored} instance(s) for session {session_id} from SQLite")
        for inst in sm.get_all_for_session(session_id):
            sm.mark_bp_offered(session_id, inst.bp_id)
        return

    # Fallback: restore from session.metadata["bp_state"] (legacy / no-storage path)
    session = _resolve_session(request, session_id)
    if not session:
        return
    bp_state = session.metadata.get("bp_state")
    if not bp_state:
        return
    restored = sm.restore_from_dict(session_id, bp_state, config_map=config_map)
    if restored:
        logger.info(f"[BP] Restored {restored} instance(s) for session {session_id} from metadata")


# ── v1.1 Helpers ─────────────────────────────────────────────


def _build_instance_item(
    snap_or_row,
    config_map: dict,
    session_title_map: dict[str, str],
) -> dict:
    """Build unified v1.1 instance response dict from BPInstanceSnapshot or SQLite row."""
    from seeagent.bestpractice.models import BPInstanceSnapshot

    if isinstance(snap_or_row, BPInstanceSnapshot):
        snap = snap_or_row
        bp_id = snap.bp_id
        bp_name = snap.bp_config.name if snap.bp_config else bp_id
        statuses = {
            k: v.value if hasattr(v, "value") else v
            for k, v in snap.subtask_statuses.items()
        }
        cfg = snap.bp_config or config_map.get(bp_id)
        subtask_names = [s.name for s in cfg.subtasks] if cfg else list(statuses.keys())
        return {
            "instance_id": snap.instance_id,
            "bp_id": bp_id,
            "bp_name": bp_name,
            "session_id": snap.session_id,
            "session_title": session_title_map.get(snap.session_id, snap.session_id),
            "status": snap.status.value,
            "run_mode": snap.run_mode.value,
            "current_subtask_index": snap.current_subtask_index,
            "progress": f"{sum(1 for v in statuses.values() if v == 'done')}"
                        f"/{len(statuses)}",
            "subtask_count": len(statuses),
            "done_count": sum(1 for v in statuses.values() if v == "done"),
            "subtask_names": subtask_names,
            "subtask_statuses": statuses,
            "created_at": snap.created_at,
            "completed_at": snap.completed_at,
            "suspended_at": snap.suspended_at,
        }
    else:
        r = snap_or_row
        bp_id = r["bp_id"]
        cfg = config_map.get(bp_id)
        bp_name = cfg.name if cfg else bp_id
        raw_statuses = r.get("subtask_statuses", {})
        if isinstance(raw_statuses, str):
            import json as _json
            raw_statuses = _json.loads(raw_statuses) if raw_statuses else {}
        subtask_names = [s.name for s in cfg.subtasks] if cfg else list(raw_statuses.keys())
        done = sum(1 for v in raw_statuses.values() if v == "done")
        total = len(raw_statuses)
        return {
            "instance_id": r["instance_id"],
            "bp_id": bp_id,
            "bp_name": bp_name,
            "session_id": r["session_id"],
            "session_title": session_title_map.get(r["session_id"], r["session_id"]),
            "status": r["status"],
            "run_mode": r["run_mode"],
            "current_subtask_index": r["current_subtask_index"],
            "progress": f"{done}/{total}",
            "subtask_count": total,
            "done_count": done,
            "subtask_names": subtask_names,
            "subtask_statuses": raw_statuses,
            "created_at": r["created_at"],
            "completed_at": r.get("completed_at"),
            "suspended_at": r.get("suspended_at"),
        }


def _resolve_session_titles(request: Request, session_ids: set[str]) -> dict[str, str]:
    """Batch-resolve session titles. Falls back to session_id string."""
    result: dict[str, str] = {}
    for sid in session_ids:
        session = _resolve_session(request, sid)
        title = session.metadata.get("title", sid) if session else sid
        result[sid] = title or sid
    return result


# ── v1.1 Config endpoints ───────────────────────────────────


@router.get("/configs")
async def get_bp_configs():
    """返回所有已配置的 BP 模板概要信息。"""
    loader = get_bp_config_loader()
    if not loader or not loader.configs:
        return JSONResponse({"total": 0, "configs": []})

    configs = []
    for cfg in loader.configs.values():
        configs.append({
            "id": cfg.id,
            "name": cfg.name,
            "description": cfg.description,
            "subtask_count": len(cfg.subtasks),
            "default_run_mode": cfg.default_run_mode.value,
            "trigger_types": sorted({t.type.value for t in cfg.triggers}),
            "triggers": [
                {
                    "type": t.type.value,
                    "pattern": t.pattern,
                    "conditions": t.conditions,
                    "cron": t.cron,
                }
                for t in cfg.triggers
            ],
        })
    return JSONResponse({"total": len(configs), "configs": configs})


@router.get("/configs/{bp_id}")
async def get_bp_config_detail(bp_id: str):
    """返回单个 BP 模板的完整配置信息。"""
    loader = get_bp_config_loader()
    if not loader:
        return JSONResponse(
            {"error": "BP system not initialized"}, status_code=500
        )

    cfg = loader.configs.get(bp_id) if loader.configs else None
    if not cfg:
        return JSONResponse(
            {"error": f"BP config '{bp_id}' not found"}, status_code=404
        )

    return JSONResponse({
        "id": cfg.id,
        "name": cfg.name,
        "description": cfg.description,
        "subtask_count": len(cfg.subtasks),
        "default_run_mode": cfg.default_run_mode.value,
        "trigger_types": sorted({t.type.value for t in cfg.triggers}),
        "triggers": [
            {
                "type": t.type.value,
                "pattern": t.pattern,
                "conditions": t.conditions,
                "cron": t.cron,
            }
            for t in cfg.triggers
        ],
        "subtasks": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "agent_profile": s.agent_profile,
                "input_schema": s.input_schema,
                "depends_on": s.depends_on,
                "input_mapping": s.input_mapping,
                "timeout_seconds": s.timeout_seconds,
                "max_retries": s.max_retries,
            }
            for s in cfg.subtasks
        ],
        "final_output_schema": cfg.final_output_schema,
    })


# ── v1.1 Instance endpoints ─────────────────────────────────
# Route order matters: /instances/stats BEFORE /instances/{instance_id}


@router.get("/instances/stats")
async def get_bp_instance_stats(
    session_id: str | None = None,
    bp_id: str | None = None,
):
    """返回 BP 实例的聚合统计数据（总数 + 按状态分组）。"""
    sm = get_bp_state_manager()
    if not sm or not sm._storage:
        return JSONResponse(
            {"error": "BP storage not initialized"}, status_code=500
        )

    by_status = await sm._storage.count_by_status(
        session_id=session_id, bp_id=bp_id,
    )
    return JSONResponse({
        "total": sum(by_status.values()),
        "by_status": by_status,
    })


@router.get("/instances")
async def get_bp_instances(
    request: Request,
    session_id: str | None = None,
    status: str | None = None,
    bp_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """统一 BP 实例列表查询（v1.1 合并原 /status + /list）。"""
    sm = get_bp_state_manager()
    loader = get_bp_config_loader()
    config_map = dict(loader.configs) if loader and loader.configs else {}

    if session_id:
        # ── Memory-first path (with auto-restore) ───────────────
        if not sm:
            return JSONResponse({
                "total": 0, "limit": limit, "offset": offset,
                "active_id": None, "instances": [],
            })

        await _ensure_bp_restored(request, session_id, sm)
        snapshots = sm.get_all_for_session(session_id)

        if status:
            snapshots = [
                s for s in snapshots
                if (s.status.value if hasattr(s.status, "value") else s.status) == status
            ]
        if bp_id:
            snapshots = [s for s in snapshots if s.bp_id == bp_id]

        total = len(snapshots)
        page = snapshots[offset: offset + limit]
        active = sm.get_active(session_id)
        title_map = _resolve_session_titles(request, {session_id})
        items = [_build_instance_item(s, config_map, title_map) for s in page]

        return JSONResponse({
            "total": total,
            "limit": limit,
            "offset": offset,
            "active_id": active.instance_id if active else None,
            "instances": items,
        })

    else:
        # ── SQLite path (cross-session) ─────────────────────────
        if not sm or not sm._storage:
            return JSONResponse({
                "total": 0, "limit": limit, "offset": offset,
                "active_id": None, "instances": [],
            })

        storage = sm._storage
        if status and bp_id:
            total = await storage.count_instances(status=status, bp_id=bp_id)
            rows = await storage.load_instances_by_status_and_bp_id(
                status, bp_id, limit=limit, offset=offset,
            )
        elif status:
            total = await storage.count_instances(status=status)
            rows = await storage.load_instances_by_status(
                status, limit=limit, offset=offset,
            )
        elif bp_id:
            total = await storage.count_instances(bp_id=bp_id)
            rows = await storage.load_instances_by_bp_id(
                bp_id, limit=limit, offset=offset,
            )
        else:
            total = await storage.count_instances()
            rows = await storage.load_all_instances(limit=limit, offset=offset)

        sids = {r["session_id"] for r in rows}
        title_map = _resolve_session_titles(request, sids)
        items = [_build_instance_item(r, config_map, title_map) for r in rows]

        return JSONResponse({
            "total": total,
            "limit": limit,
            "offset": offset,
            "active_id": None,
            "instances": items,
        })


@router.get("/instances/{instance_id}")
async def bp_get_instance(instance_id: str, request: Request):
    """查询单实例完整信息（内存优先 → SQLite 回退）。"""
    sm = get_bp_state_manager()
    if not sm:
        return JSONResponse({"error": "BP system not initialized"}, status_code=500)

    snap = await sm.ensure_loaded(instance_id)
    if not snap:
        return JSONResponse({"error": "Not found"}, status_code=404)

    return JSONResponse({
        "instance_id": snap.instance_id,
        "bp_id": snap.bp_id,
        "bp_name": snap.bp_config.name if snap.bp_config else snap.bp_id,
        "session_id": snap.session_id,
        "status": snap.status.value,
        "run_mode": snap.run_mode.value,
        "current_subtask_index": snap.current_subtask_index,
        "created_at": snap.created_at,
        "completed_at": snap.completed_at,
        "suspended_at": snap.suspended_at,
        "subtask_statuses": {
            k: v.value if hasattr(v, "value") else v
            for k, v in snap.subtask_statuses.items()
        },
        "subtask_outputs": snap.subtask_outputs,
        "initial_input": snap.initial_input,
        "supplemented_inputs": snap.supplemented_inputs,
        "context_summary": snap.context_summary,
    })


@router.get("/instances/{instance_id}/output/{subtask_id}")
async def bp_get_output(instance_id: str, subtask_id: str):
    """Query subtask output (plain JSON). Falls back to SQLite if not in memory."""
    sm = get_bp_state_manager()
    if not sm:
        return JSONResponse({"error": "BP system not initialized"}, status_code=500)
    snap = await sm.ensure_loaded(instance_id)
    if not snap:
        return JSONResponse({"error": "Not found"}, status_code=404)
    output = snap.subtask_outputs.get(subtask_id)
    if output is None:
        return JSONResponse({"error": "No output"}, status_code=404)
    return JSONResponse({"output": output})


@router.put("/instances/{instance_id}/run-mode")
async def set_run_mode(instance_id: str, request: Request):
    """切换 BP 实例的运行模式 (manual/auto)。"""
    from seeagent.bestpractice.models import RunMode

    body = await request.json()
    run_mode_str = body.get("run_mode", "manual")

    sm = get_bp_state_manager()
    if not sm:
        return JSONResponse(
            {"success": False, "error": "BP system not initialized"}, 500
        )

    snap = await sm.ensure_loaded(instance_id)
    if not snap:
        return JSONResponse(
            {"success": False, "error": f"Instance {instance_id} not found"}, 404
        )

    snap.run_mode = (
        RunMode(run_mode_str) if run_mode_str in ("manual", "auto") else RunMode.MANUAL
    )
    await sm.persist_instance(instance_id)
    return JSONResponse({"success": True, "run_mode": snap.run_mode.value})


@router.put("/instances/{instance_id}/output")
async def edit_bp_output(instance_id: str, request: Request):
    """前端编辑子任务输出 (Chat-to-Edit)。"""
    body = await request.json()
    subtask_id = body.get("subtask_id", "")
    changes = body.get("changes", {})

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        return JSONResponse(
            {"success": False, "error": "BP system not initialized"}, 500
        )

    snap = await sm.ensure_loaded(instance_id)
    if not snap:
        return JSONResponse(
            {"success": False, "error": f"Instance {instance_id} not found"}, 404
        )

    bp_config = snap.bp_config
    if not bp_config:
        loader = get_bp_config_loader()
        bp_config = loader.get(snap.bp_id) if loader else None

    if not bp_config:
        return JSONResponse(
            {"success": False, "error": "BP config not found"}, 404
        )

    result = engine.handle_edit_output(instance_id, subtask_id, changes, bp_config)
    if result.get("success"):
        await sm.persist_subtask_output(instance_id, subtask_id)
        await sm.persist_subtask_progress(instance_id)
    return JSONResponse(result)


@router.delete("/instances/{instance_id}")
async def bp_cancel(instance_id: str, request: Request):
    """Cancel BP instance."""
    sm = get_bp_state_manager()
    if not sm:
        return JSONResponse({"error": "BP system not initialized"}, status_code=500)
    snap = await sm.ensure_loaded(instance_id)
    if not snap:
        return JSONResponse({"error": "Not found"}, status_code=404)
    sm.cancel(instance_id)
    await sm.persist_status_change(instance_id)
    session = _resolve_session(request, snap.session_id)
    if session and hasattr(session, "context"):
        dt = getattr(session.context, "_bp_delegate_task", None)
        if dt and not dt.done():
            dt.cancel()
    if session:
        session.metadata["bp_state"] = sm.serialize_for_session(snap.session_id)
        session_mgr = _resolve_session_manager(request)
        if session_mgr:
            session_mgr.mark_dirty()
    return JSONResponse({"status": "ok"})


# ── Busy-lock (R11, R16) ───────────────────────────────────────
_bp_busy_locks: dict[str, tuple[str, float, str]] = {}  # session_id → (source, timestamp, lock_id)
_bp_busy_mutex = asyncio.Lock()
_BP_LOCK_TTL = 600


async def _bp_mark_busy(session_id: str, source: str, lock_id: str) -> bool:
    """Try to acquire busy-lock. Returns False if already locked."""
    async with _bp_busy_mutex:
        now = time.time()
        expired = [k for k, (_, ts, _) in _bp_busy_locks.items() if now - ts > _BP_LOCK_TTL]
        for k in expired:
            del _bp_busy_locks[k]
        if session_id in _bp_busy_locks:
            existing_source, existing_ts, _ = _bp_busy_locks[session_id]
            age = round(now - existing_ts, 1)
            logger.warning(
                f"[BP] mark_busy DENIED: session={session_id} "
                f"held_by={existing_source} age={age}s"
            )
            return False
        _bp_busy_locks[session_id] = (source, now, lock_id)
        return True


def _bp_renew_busy(session_id: str) -> None:
    """Renew busy-lock timestamp to prevent TTL expiry during long auto-mode runs."""
    if session_id in _bp_busy_locks:
        source, _, lock_id = _bp_busy_locks[session_id]
        _bp_busy_locks[session_id] = (source, time.time(), lock_id)


def _bp_clear_busy(session_id: str, lock_id: str) -> None:
    entry = _bp_busy_locks.get(session_id)
    if entry:
        source, ts, existing_lock_id = entry
        if existing_lock_id == lock_id:
            del _bp_busy_locks[session_id]
            logger.info(
                f"[BP] clear_busy: session={session_id} source={source} held={round(time.time()-ts,1)}s"
            )
        else:
            logger.debug(
                f"[BP] clear_busy passed: session={session_id} (lock_id mismatch: {existing_lock_id} != {lock_id})"
            )


# ── SSE Helpers ────────────────────────────────────────────────
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# ── Reply state collection helpers ─────────────────────────────


def _new_reply_state() -> dict:
    """Create empty reply_state dict."""
    return {
        "thinking": "",
        "step_cards": [],
        "agent_thinking": {},
        "agent_summaries": {},
        "plan_checklist": None,
        "timer": {"ttft": None, "total": None},
        "bp_progress": None,
        "bp_subtask_output": None,
        "bp_subtask_outputs": [],  # ALL subtask outputs (auto mode produces multiple)
        "bp_subtask_complete": None,
        "bp_instance_created": None,
        "bp_ask_user": None,
    }


def _upsert_step_card(cards: list, event: dict) -> None:
    """Upsert step card by step_id."""
    step_id = event.get("step_id")
    for i, c in enumerate(cards):
        if c.get("step_id") == step_id:
            cards[i] = event
            return
    cards.append(event)


def _collect_reply_state(event: dict, reply_state: dict, full_reply: list) -> None:
    """Collect SSE event data into reply_state for persistence."""
    etype = event.get("type")
    if etype == "thinking":
        subtask_id = event.get("subtask_id")
        agent_id = event.get("agent_id")
        key = subtask_id or agent_id
        if key and key != "main":
            at = reply_state["agent_thinking"].setdefault(
                key, {"content": "", "done": False},
            )
            at["content"] += event.get("content", "")
            # Also store under agent_id if different, so both
            # subtask_id and agent_id lookups hit the same data
            if subtask_id and agent_id and agent_id != "main" and agent_id != subtask_id:
                reply_state["agent_thinking"][agent_id] = at
        else:
            reply_state["thinking"] += event.get("content", "")
    elif etype == "step_card":
        _upsert_step_card(reply_state["step_cards"], event)
    elif etype == "ai_text":
        agent_id = event.get("agent_id")
        if agent_id and agent_id != "main":
            reply_state["agent_summaries"][agent_id] = (
                reply_state["agent_summaries"].get(agent_id, "")
                + event.get("content", "")
            )
        else:
            full_reply.append(event.get("content", ""))
    elif etype == "bp_progress":
        reply_state["bp_progress"] = event
    elif etype == "bp_subtask_output":
        reply_state["bp_subtask_output"] = event
        reply_state["bp_subtask_outputs"].append(event)
    elif etype == "bp_subtask_complete":
        reply_state["bp_subtask_complete"] = event
        # Also store as bp_subtask_output for frontend restoration
        reply_state["bp_subtask_output"] = event
        reply_state["bp_subtask_outputs"].append(event)
    elif etype == "bp_instance_created":
        reply_state["bp_instance_created"] = event
    elif etype == "bp_ask_user":
        reply_state["bp_ask_user"] = event
    elif etype == "plan_checklist":
        reply_state["plan_checklist"] = event.get("steps")
    elif etype == "timer_update":
        phase = event.get("phase")
        if phase in reply_state["timer"] and event.get("state") == "done":
            reply_state["timer"][phase] = event.get("value")


# ── Session resolution (R15) ──────────────────────────────────


def _resolve_session_manager(request: Request):
    """Get session_manager from app state."""
    return getattr(request.app.state, "session_manager", None)


def _resolve_session(request: Request, session_id: str, *, create_if_missing: bool = False):
    """Get session from session_manager.
    /bp/start uses create_if_missing=True; /bp/next, /bp/answer use False.
    """
    sm = getattr(request.app.state, "session_manager", None)
    if sm and session_id:
        return sm.get_session(
            channel="seecrab", chat_id=session_id,
            user_id="seecrab_user", create_if_missing=create_if_missing,
        )
    return None


# ── State persistence (R12, R18) ──────────────────────────────


def _persist_user_message(session, message: str, session_manager=None) -> None:
    """Persist user interaction message to session history."""
    if session and message:
        try:
            session.add_message("user", message)
            if session_manager:
                session_manager.mark_dirty()
        except Exception:
            pass


def _persist_bp_to_session(
    session, instance_id: str, sm,
    *, reply_state: dict | None = None, full_reply: str = "",
    session_manager=None,
) -> None:
    """Persist BP state to session (R12, R18).
    Two layers: metadata for recovery + add_message for history.
    """
    if not session or not sm:
        return
    snap = sm.get(instance_id)
    if not snap:
        return
    try:
        session.metadata["bp_state"] = sm.serialize_for_session(snap.session_id)
    except Exception:
        pass
    try:
        bp_config = snap.bp_config
        bp_name = bp_config.name if bp_config else snap.bp_id
        done_count = sum(
            1 for s in snap.subtask_statuses.values()
            if (s.value if hasattr(s, "value") else s) == "done"
        )
        total = len(snap.subtask_statuses)
        summary = full_reply or f"[BP] 「{bp_name}」进度: {done_count}/{total}"

        rs = reply_state or {}
        session.add_message("assistant", summary, reply_state=rs)
    except Exception:
        pass
    if session_manager:
        try:
            session_manager.mark_dirty()
        except Exception:
            pass


# ── New SSE endpoints (R4) ────────────────────────────────────


@router.post("/start")
async def bp_start(request: Request):
    """Create BP instance and execute first subtask. Returns SSE stream."""
    from seeagent.bestpractice.models import RunMode

    body = await request.json()
    bp_id = body.get("bp_id", "")
    session_id = body.get("session_id", "")
    input_data = body.get("input_data", {})
    run_mode_str = body.get("run_mode", "manual")

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        return JSONResponse({"error": "BP system not initialized"}, status_code=500)

    loader = get_bp_config_loader()
    bp_config = loader.configs.get(bp_id) if loader and loader.configs else None
    if not bp_config:
        return JSONResponse({"error": f"BP '{bp_id}' not found"}, status_code=404)

    # 当前端未传 input_data 时，从 pending_offer 中提取用户原始 query 并用 LLM 解析
    if not input_data and bp_config.subtasks:
        pending_offer = sm.get_pending_offer(session_id)
        if pending_offer:
            user_query = pending_offer.get("user_query", "")
            first_schema = pending_offer.get("first_input_schema") or bp_config.subtasks[0].input_schema
            if user_query and first_schema:
                from seeagent.api.routes.seecrab import _extract_input_from_query
                agent = getattr(request.app.state, "agent", None)
                brain = getattr(agent, "brain", None) if agent else None
                input_data = await _extract_input_from_query(brain, user_query, first_schema)
                logger.info(f"[BP] Extracted input from pending offer query: {input_data}")

    run_mode = RunMode(run_mode_str) if run_mode_str in ("manual", "auto") else RunMode.MANUAL
    session = _resolve_session(request, session_id, create_if_missing=True)
    session_mgr = _resolve_session_manager(request)

    # If session is busy (previous BP still cleaning up), preempt it and wait for lock.
    # Race: delegate_task.cancel() was already called by pre-match, but await delegate_task
    # in _run_subtask_stream() may take several seconds to complete.
    if session_id in _bp_busy_locks:
        _active_now = sm.get_active(session.id) if session else None
        if _active_now and engine:
            await engine.request_suspend(_active_now.instance_id, session, "bp_start_preempt")
            logger.info(f"[BP] bp_start preempting active instance {_active_now.instance_id}")

    acquired = False
    lock_id = uuid.uuid4().hex
    for _ in range(30):  # wait up to 15s (30 * 0.5s)
        if await _bp_mark_busy(session_id, "bp_start", lock_id):
            acquired = True
            break
        await asyncio.sleep(0.5)
    if not acquired:
        return JSONResponse({"error": "Session is busy"}, status_code=409)

    _persist_user_message(session, body.get("user_message", ""), session_manager=session_mgr)

    async def generate():
        logger.info(f"[BP] generate() START: session_id={session_id} session.id={session.id if session else 'N/A'} bp_id={bp_id}")
        disconnect_event = asyncio.Event()
        reply_state = _new_reply_state()
        full_reply: list[str] = []
        instance_id: str | None = None

        async def _disconnect_watcher():
            while not disconnect_event.is_set():
                if await request.is_disconnected():
                    disconnect_event.set()
                    _dis_session_id = session.id if session else session_id
                    active = sm.get_active(_dis_session_id)
                    logger.info(
                        f"[BP] disconnect: session_id={session_id} "
                        f"dis_session_id={_dis_session_id} "
                        f"active={active.instance_id if active else None}"
                    )
                    if active:
                        await engine.request_suspend(
                            active.instance_id, session, "disconnect",
                        )
                    return
                await asyncio.sleep(2)

        watcher = asyncio.create_task(_disconnect_watcher())

        try:
            async for event in engine.start(bp_config, session, input_data, run_mode):
                if disconnect_event.is_set():
                    break
                if event.get("type") == "bp_instance_created":
                    instance_id = event.get("instance_id")
                yield _sse(event)
                _collect_reply_state(event, reply_state, full_reply)
                if event.get("type") in ("bp_subtask_complete", "bp_progress"):
                    _bp_renew_busy(session_id)

            # Skip if already persisted by seecrab.py suspend logic
            _snap = sm.get(instance_id) if instance_id else None
            _is_suspended = _snap and (
                getattr(_snap.status, "value", _snap.status) == "suspended"
            )
            if instance_id and not _is_suspended:
                _persist_bp_to_session(session, instance_id, sm,
                                       reply_state=reply_state,
                                       full_reply="".join(full_reply),
                                       session_manager=session_mgr)
            yield _sse({"type": "done"})
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})
            yield _sse({"type": "done"})
        finally:
            logger.info(f"[BP] generate() FINALLY: session_id={session_id} clearing busy")
            watcher.cancel()
            _bp_clear_busy(session_id, lock_id)

    async def _cleanup():
        _bp_clear_busy(session_id, lock_id)

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers=_SSE_HEADERS,
        background=BackgroundTask(_cleanup)
    )


@router.post("/next")
async def bp_next(request: Request):
    """Advance BP to next subtask. Returns SSE stream."""
    body = await request.json()
    instance_id = body.get("instance_id", "")
    session_id = body.get("session_id", "")

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        return JSONResponse({"error": "BP system not initialized"}, status_code=500)

    await _ensure_bp_restored(request, session_id, sm)

    lock_id = uuid.uuid4().hex
    if not await _bp_mark_busy(session_id, "bp_next", lock_id):
        return JSONResponse({"error": "Session is busy"}, status_code=409)

    session = _resolve_session(request, session_id)
    resume = await engine.resume_if_needed(instance_id, session)
    if not resume.get("success"):
        _bp_clear_busy(session_id, lock_id)
        status_code = 409 if resume.get("code") == "conflict" else 404
        return JSONResponse(
            {
                "error": resume.get("error", "Failed to resume BP instance"),
                "code": resume.get("code", "bp_resume_failed"),
                "active_instance_id": resume.get("active_instance_id"),
            },
            status_code=status_code,
        )
    session_mgr = _resolve_session_manager(request)
    _persist_user_message(session, body.get("user_message", ""), session_manager=session_mgr)

    async def generate():
        disconnect_event = asyncio.Event()
        reply_state = _new_reply_state()
        full_reply: list[str] = []

        async def _disconnect_watcher():
            while not disconnect_event.is_set():
                if await request.is_disconnected():
                    disconnect_event.set()
                    await engine.request_suspend(instance_id, session, "disconnect")
                    return
                await asyncio.sleep(2)

        watcher = asyncio.create_task(_disconnect_watcher())

        try:
            async for event in engine.advance(instance_id, session):
                if disconnect_event.is_set():
                    break
                yield _sse(event)
                _collect_reply_state(event, reply_state, full_reply)
                if event.get("type") in ("bp_subtask_complete", "bp_progress"):
                    _bp_renew_busy(session_id)

            _snap = sm.get(instance_id)
            _is_suspended = _snap and (
                getattr(_snap.status, "value", _snap.status) == "suspended"
            )
            if not _is_suspended:
                _persist_bp_to_session(session, instance_id, sm,
                                       reply_state=reply_state,
                                       full_reply="".join(full_reply),
                                       session_manager=session_mgr)
            yield _sse({"type": "done"})
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})
            yield _sse({"type": "done"})
        finally:
            watcher.cancel()
            _bp_clear_busy(session_id, lock_id)

    async def _cleanup():
        _bp_clear_busy(session_id, lock_id)

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers=_SSE_HEADERS,
        background=BackgroundTask(_cleanup)
    )


@router.post("/answer")
async def bp_answer(request: Request):
    """Submit ask_user answer and continue. Returns SSE stream."""
    body = await request.json()
    instance_id = body.get("instance_id", "")
    subtask_id = body.get("subtask_id", "")
    data = body.get("data", {})
    session_id = body.get("session_id", "")

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        return JSONResponse({"error": "BP system not initialized"}, status_code=500)

    await _ensure_bp_restored(request, session_id, sm)

    lock_id = uuid.uuid4().hex
    if not await _bp_mark_busy(session_id, "bp_answer", lock_id):
        return JSONResponse({"error": "Session is busy"}, status_code=409)

    session = _resolve_session(request, session_id)
    resume = await engine.resume_if_needed(instance_id, session)
    if not resume.get("success"):
        _bp_clear_busy(session_id, lock_id)
        status_code = 409 if resume.get("code") == "conflict" else 404
        return JSONResponse(
            {
                "error": resume.get("error", "Failed to resume BP instance"),
                "code": resume.get("code", "bp_resume_failed"),
                "active_instance_id": resume.get("active_instance_id"),
            },
            status_code=status_code,
        )
    session_mgr = _resolve_session_manager(request)
    _persist_user_message(session, body.get("user_message", ""), session_manager=session_mgr)

    async def generate():
        disconnect_event = asyncio.Event()
        reply_state = _new_reply_state()
        full_reply: list[str] = []

        async def _disconnect_watcher():
            while not disconnect_event.is_set():
                if await request.is_disconnected():
                    disconnect_event.set()
                    await engine.request_suspend(instance_id, session, "disconnect")
                    return
                await asyncio.sleep(2)

        watcher = asyncio.create_task(_disconnect_watcher())

        try:
            async for event in engine.answer(instance_id, subtask_id, data, session):
                if disconnect_event.is_set():
                    break
                yield _sse(event)
                _collect_reply_state(event, reply_state, full_reply)

            _snap = sm.get(instance_id)
            _is_suspended = _snap and (
                getattr(_snap.status, "value", _snap.status) == "suspended"
            )
            if not _is_suspended:
                _persist_bp_to_session(session, instance_id, sm,
                                       reply_state=reply_state,
                                       full_reply="".join(full_reply),
                                       session_manager=session_mgr)
            yield _sse({"type": "done"})
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})
            yield _sse({"type": "done"})
        finally:
            watcher.cancel()
            _bp_clear_busy(session_id, lock_id)

    async def _cleanup():
        _bp_clear_busy(session_id, lock_id)

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers=_SSE_HEADERS,
        background=BackgroundTask(_cleanup)
    )


