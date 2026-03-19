"""BP REST API: 状态查询、模式切换、前端启动。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bp")


@router.get("/status")
async def get_bp_status(session_id: str, request: Request):
    """返回指定会话的所有 BP 实例状态。"""
    from seeagent.bestpractice.facade import get_bp_state_manager

    sm = get_bp_state_manager()
    if not sm:
        return JSONResponse({"instances": [], "active_id": None})

    instances = sm.get_all_for_session(session_id)
    active = sm.get_active(session_id)
    return JSONResponse({
        "instances": [
            {
                "instance_id": snap.instance_id,
                "bp_id": snap.bp_id,
                "bp_name": snap.bp_config.name if snap.bp_config else snap.bp_id,
                "status": snap.status.value,
                "run_mode": snap.run_mode.value,
                "current_subtask_index": snap.current_subtask_index,
                "subtask_statuses": {
                    k: v.value if hasattr(v, "value") else v
                    for k, v in snap.subtask_statuses.items()
                },
                "subtask_outputs": snap.subtask_outputs,
            }
            for snap in instances
        ],
        "active_id": active.instance_id if active else None,
    })


@router.put("/run-mode")
async def set_run_mode(request: Request):
    """切换 BP 实例的运行模式 (manual/auto)。"""
    from seeagent.bestpractice.facade import get_bp_state_manager
    from seeagent.bestpractice.models import RunMode

    body = await request.json()
    instance_id = body.get("instance_id", "")
    run_mode_str = body.get("run_mode", "manual")

    sm = get_bp_state_manager()
    if not sm:
        return JSONResponse(
            {"success": False, "error": "BP system not initialized"}, 500
        )

    snap = sm.get(instance_id)
    if not snap:
        return JSONResponse(
            {"success": False, "error": f"Instance {instance_id} not found"}, 404
        )

    snap.run_mode = (
        RunMode(run_mode_str) if run_mode_str in ("manual", "auto") else RunMode.MANUAL
    )
    return JSONResponse({"success": True, "run_mode": snap.run_mode.value})


@router.put("/edit-output")
async def edit_bp_output(request: Request):
    """前端编辑子任务输出 (Chat-to-Edit)。"""
    from seeagent.bestpractice.facade import (
        get_bp_config_loader,
        get_bp_engine,
        get_bp_state_manager,
    )

    body = await request.json()
    instance_id = body.get("instance_id", "")
    subtask_id = body.get("subtask_id", "")
    changes = body.get("changes", {})

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        return JSONResponse(
            {"success": False, "error": "BP system not initialized"}, 500
        )

    snap = sm.get(instance_id)
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
    return JSONResponse(result)
