"""
BPToolHandler — BP 工具路由。

6 个工具:
- bp_start: 启动 BP (创建实例并执行首个子任务)
- bp_edit_output: 修改子任务输出 (Chat-to-Edit)
- bp_switch_task: 切换活跃 BP 实例
- bp_next: 继续执行下一个子任务
- bp_answer: 补充缺失参数后继续执行
- bp_cancel: 取消当前 BP 实例
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from .models import BPStatus, RunMode

if TYPE_CHECKING:
    from .engine import BPEngine, BPStateManager, ContextBridge
    from .models import BestPracticeConfig

logger = logging.getLogger(__name__)

BP_TOOLS = ["bp_start", "bp_edit_output", "bp_switch_task", "bp_next", "bp_answer", "bp_cancel"]


class BPToolHandler:
    """Routes bp_* tool calls to BPEngine/BPStateManager."""

    TOOLS = BP_TOOLS

    def __init__(
        self,
        engine: BPEngine,
        state_manager: BPStateManager,
        context_bridge: ContextBridge,
        config_registry: dict[str, BestPracticeConfig],
    ) -> None:
        self.engine = engine
        self.state_manager = state_manager
        self.context_bridge = context_bridge
        self.config_registry = config_registry

    async def handle(self, tool_name: str, params: dict[str, Any], agent: Any) -> str:
        session = getattr(agent, "_current_session", None)
        if not session:
            return "❌ 无活跃会话"

        dispatch = {
            "bp_start": self._handle_start,
            "bp_edit_output": self._handle_edit_output,
            "bp_switch_task": self._handle_switch_task,
            "bp_next": self._handle_next,
            "bp_answer": self._handle_answer,
            "bp_cancel": self._handle_cancel,
        }

        handler = dispatch.get(tool_name)
        if not handler:
            return f"❌ Unknown BP tool: {tool_name}"

        return await handler(params, agent, session)

    # ── bp_start ───────────────────────────────────────────────

    async def _handle_start(self, params: dict, agent: Any, session: Any) -> str:
        bp_id = (params.get("bp_id") or "").strip()
        logger.info(f"[BP] handle_start: bp_id={bp_id} session={session.id}")
        if not bp_id:
            return "❌ bp_id is required"

        bp_config = self.config_registry.get(bp_id)
        if not bp_config:
            available = ", ".join(self.config_registry.keys())
            return f"❌ Best Practice '{bp_id}' 不存在。可用: {available}"

        # Prevent duplicate: same BP already active
        existing = self.state_manager.get_active(session.id)
        if existing and existing.bp_id == bp_id:
            return (
                f"✅ 「{bp_config.name}」已在运行中 (instance={existing.instance_id})。"
                f"该实例已在运行中，前端会自动接管执行。"
                f"无需重复启动。"
            )

        # Resume suspended instance with the same bp_id instead of creating new,
        # but only when caller did NOT supply new input_data that differs from the
        # suspended instance's initial_input (which indicates user wants a fresh run).
        input_data = params.get("input_data", {})
        suspended_same = [
            s for s in self.state_manager.get_all_for_session(session.id)
            if s.bp_id == bp_id and s.status == BPStatus.SUSPENDED
        ]
        if suspended_same:
            # Prefer the instance whose initial_input matches the incoming input_data;
            # fall back to the most recently suspended one.
            if input_data:
                target = next(
                    (s for s in suspended_same if (s.initial_input or {}) == input_data),
                    max(suspended_same, key=lambda s: s.suspended_at or 0.0),
                )
            else:
                target = max(suspended_same, key=lambda s: s.suspended_at or 0.0)
            new_input_differs = bool(input_data) and input_data != (target.initial_input or {})
            if not new_input_differs:
                logger.info(
                    f"[BP] handle_start: resuming suspended "
                    f"instance={target.instance_id} bp_id={bp_id}"
                )
                result = await self.engine.switch(target.instance_id, session)
                if result.get("success"):
                    await self._relay_events(
                        self.engine.advance(target.instance_id, session), session,
                    )
                    self.state_manager.persist_to_session(target.instance_id, session)
                    return f"✅ 已恢复并继续「{bp_config.name}」任务。"

        run_mode_str = params.get("run_mode", bp_config.default_run_mode.value)
        run_mode = RunMode(run_mode_str) if run_mode_str in ("manual", "auto") else RunMode.MANUAL

        await self._relay_events(
            self.engine.start(bp_config, session, input_data, run_mode), session,
        )
        return f"✅ 已创建并执行 BP 实例「{bp_config.name}」。"

    # ── bp_edit_output ─────────────────────────────────────────

    async def _handle_edit_output(self, params: dict, agent: Any, session: Any) -> str:
        instance_id = self._resolve_instance_id(params, session)
        if not instance_id:
            return "❌ 请指定 instance_id"

        subtask_id = (params.get("subtask_id") or "").strip()
        if not subtask_id:
            return "❌ subtask_id is required"

        changes = params.get("changes", {})
        if not changes:
            return "❌ changes is required"
        logger.info(
            f"[BP] handle_edit_output: instance={instance_id} "
            f"subtask={subtask_id} change_keys={list(changes.keys())}"
        )

        snap = self.state_manager.get(instance_id)
        if not snap:
            return f"❌ BP instance {instance_id} 不存在"

        if snap.session_id != session.id:
            return f"❌ BP instance {instance_id} 不属于当前会话"

        bp_config = self._get_config_for_instance(snap)
        if not bp_config:
            return f"❌ BP config {snap.bp_id} 不存在"

        result = self.engine.handle_edit_output(instance_id, subtask_id, changes, bp_config)

        if result.get("success"):
            await self.state_manager.persist_subtask_output(instance_id, subtask_id)
            await self.state_manager.persist_subtask_progress(instance_id)
            if result.get("stale_subtasks"):
                logger.info(
                    f"[BP] handle_edit_output: stale_subtasks="
                    f"{result['stale_subtasks']} instance={instance_id}"
                )
                await self.engine._emit_stale(
                    instance_id,
                    result["stale_subtasks"],
                    f"子任务 {subtask_id} 输出被编辑",
                    session,
                )

        if not result.get("success"):
            return f"❌ {result.get('error', 'Unknown error')}"

        stale = result.get("stale_subtasks", [])
        merged_preview = json.dumps(result["merged"], ensure_ascii=False)[:300]
        msg = f"✅ 子任务输出已合并更新。\n预览: {merged_preview}"
        if stale:
            msg += f"\n\n⚠️ 以下下游子任务已标记为 stale，需要重新执行: {stale}"
        if result.get("warning"):
            msg += f"\n⚠️ {result['warning']}"
        return msg

    # ── bp_switch_task ─────────────────────────────────────────

    async def _handle_switch_task(self, params: dict, agent: Any, session: Any) -> str:
        target_id = (params.get("target_instance_id") or "").strip()
        if not target_id:
            return "❌ target_instance_id is required"

        target = self.state_manager.get(target_id)
        if not target:
            return f"❌ BP instance {target_id} 不存在"

        if target.session_id != session.id:
            return f"❌ BP instance {target_id} 不属于当前会话"

        logger.info(
            f"[BP] switch_task: target={target_id} bp_id={target.bp_id} "
            f"status={target.status.value} session={session.id}"
        )
        result = await self.engine.switch(target_id, session)
        if not result.get("success"):
            if result.get("already_active"):
                logger.debug(f"[BP] switch_task: {target_id} already active, skip")
                return f"ℹ️ {target_id} 已经是当前活跃任务"
            logger.warning(f"[BP] switch_task: switch failed result={result}")
            return f"❌ {result.get('error', 'Unknown error')}"

        bp_config = self._get_config_for_instance(target)
        bp_name = bp_config.name if bp_config else target.bp_id
        logger.info(f"[BP] switch_task: switched to 「{bp_name}」, starting advance")

        await self._relay_events(self.engine.advance(target_id, session), session)
        self.state_manager.persist_to_session(target_id, session)
        logger.info(f"[BP] switch_task: advance complete, instance={target_id}")

        return f"✅ 已切换到任务「{bp_name}」并继续执行。"

    # ── bp_next ────────────────────────────────────────────────

    async def _handle_next(self, params: dict, agent: Any, session: Any) -> str:
        instance_id = self._resolve_instance_id(params, session)
        if not instance_id:
            return "❌ 当前没有活跃的最佳实践任务"
        snap = self.state_manager.get(instance_id)
        if not snap:
            return "❌ BP 实例不存在"
        logger.info(f"[BP] handle_next: instance={instance_id} session={session.id}")
        resume = await self.engine.resume_if_needed(instance_id, session)
        if not resume.get("success"):
            logger.warning(
                f"[BP] handle_next: resume failed instance={instance_id} "
                f"code={resume.get('code')}"
            )
            if resume.get("code") == "conflict":
                active_id = resume.get("active_instance_id")
                return (
                    "❌ 当前有其他最佳实践任务处于活跃状态，"
                    f"请先切换任务或继续当前活跃实例 ({active_id})。"
                )
            return f"❌ {resume.get('error', '无法恢复 BP 实例')}"
        await self._relay_events(self.engine.advance(instance_id, session), session)
        self.state_manager.persist_to_session(instance_id, session)
        return "✅ 子任务执行完成"

    # ── bp_answer ─────────────────────────────────────────────

    async def _handle_answer(self, params: dict, agent: Any, session: Any) -> str:
        instance_id = self._resolve_instance_id(params, session)
        subtask_id = (params.get("subtask_id") or "").strip()
        data = params.get("data", {})
        if not instance_id or not subtask_id or not data:
            return "❌ 需要 subtask_id 和 data 参数"
        logger.info(
            f"[BP] handle_answer: instance={instance_id} "
            f"subtask={subtask_id} data_keys={list(data.keys())}"
        )
        resume = await self.engine.resume_if_needed(instance_id, session)
        if not resume.get("success"):
            logger.warning(
                f"[BP] handle_answer: resume failed instance={instance_id} "
                f"code={resume.get('code')}"
            )
            if resume.get("code") == "conflict":
                active_id = resume.get("active_instance_id")
                return (
                    "❌ 当前有其他最佳实践任务处于活跃状态，"
                    f"请先切换任务或继续当前活跃实例 ({active_id})。"
                )
            return f"❌ {resume.get('error', '无法恢复 BP 实例')}"
        await self._relay_events(
            self.engine.answer(instance_id, subtask_id, data, session), session,
        )
        self.state_manager.persist_to_session(instance_id, session)
        return "✅ 参数已补充，子任务执行中"

    # ── bp_cancel ─────────────────────────────────────────────

    async def _handle_cancel(self, params: dict, agent: Any, session: Any) -> str:
        instance_id = self._resolve_instance_id(params, session)
        if not instance_id:
            return "❌ 当前没有活跃的最佳实践任务"
        snap = self.state_manager.get(instance_id)
        if not snap:
            return "❌ BP 实例不存在"
        bp_name = snap.bp_config.name if snap.bp_config else snap.bp_id
        logger.info(
            f"[BP] handle_cancel: instance={instance_id} "
            f"bp_name={bp_name} session={session.id}"
        )
        await self._relay_events(self.engine.cancel(instance_id, session), session)
        return f"✅ 已取消最佳实践任务「{bp_name}」(id={instance_id})"

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    async def _relay_events(event_gen, session: Any) -> None:
        """Forward events from an async generator to the SSE event_bus."""
        bus = (
            getattr(session.context, "_sse_event_bus", None)
            if hasattr(session, "context") else None
        )
        async for event in event_gen:
            if bus:
                await bus.put(event)

    def _resolve_instance_id(self, params: dict, session: Any) -> str | None:
        instance_id = (params.get("instance_id") or "").strip()
        if instance_id:
            return instance_id
        active = self.state_manager.get_active(session.id)
        return active.instance_id if active else None

    def _get_config_for_instance(self, snap: Any) -> Any:
        """获取实例对应的 BP 配置。优先用 snap.bp_config，fallback config_registry。"""
        if snap.bp_config:
            return snap.bp_config
        return self.config_registry.get(snap.bp_id)
