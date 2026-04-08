"""BPStateManager — BP 实例生命周期与状态管理。

Key decisions:
- 内存为主，通过 Session.metadata["bp_state"] 持久化 (NOT "_bp_state")
- 独立于 AgentState/TaskState/SessionContext
- 线程安全（同一 session 的并发操作通过 GIL 保护，异步通过单线程 eventloop）
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from ..models import (
    BPInstanceSnapshot,
    BPStatus,
    PendingContextSwitch,
    RunMode,
    SubtaskStatus,
)

if TYPE_CHECKING:
    from ..models import BestPracticeConfig
    from ..storage import BPStorage

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_TURNS = 3


class BPStateManager:
    """管理所有 BP 实例的内存状态。"""

    def __init__(self, storage: BPStorage | None = None) -> None:
        self._instances: dict[str, BPInstanceSnapshot] = {}
        self._pending_switches: dict[str, PendingContextSwitch] = {}  # session_id → switch
        self._cooldowns: dict[str, int] = {}  # session_id → remaining turns
        self._offered_bps: dict[str, set[str]] = {}  # session_id → set of offered bp_ids
        self._pending_offers: dict[str, dict[str, Any]] = {}  # session_id → offer payload
        self._storage: BPStorage | None = storage

    # ── Instance lifecycle ─────────────────────────────────────

    def create_instance(
        self,
        bp_config: BestPracticeConfig,
        session_id: str,
        initial_input: dict[str, Any] | None = None,
        run_mode: RunMode = RunMode.MANUAL,
    ) -> str:
        """创建新的 BP 实例。返回 instance_id。"""
        instance_id = BPInstanceSnapshot.new_instance_id()

        # 初始化所有子任务状态
        statuses = {s.id: SubtaskStatus.PENDING.value for s in bp_config.subtasks}

        snap = BPInstanceSnapshot(
            bp_id=bp_config.id,
            instance_id=instance_id,
            session_id=session_id,
            status=BPStatus.ACTIVE,
            created_at=time.time(),
            run_mode=run_mode,
            subtask_statuses=statuses,
            initial_input=dict(initial_input or {}),
            bp_config=bp_config,
        )
        self._instances[instance_id] = snap
        logger.info(f"[BP] Created instance {instance_id} for '{bp_config.id}' in session {session_id}")
        return instance_id

    def suspend(self, instance_id: str) -> None:
        snap = self._instances.get(instance_id)
        if snap and snap.status == BPStatus.ACTIVE:
            snap.status = BPStatus.SUSPENDED
            snap.suspended_at = time.time()
            logger.info(f"[BP] suspend: instance={instance_id} bp_id={snap.bp_id}")
        else:
            logger.debug(
                f"[BP] suspend: no-op instance={instance_id} "
                f"status={snap.status.value if snap else 'not_found'}"
            )

    def resume(self, instance_id: str) -> None:
        snap = self._instances.get(instance_id)
        if snap and snap.status == BPStatus.SUSPENDED:
            snap.status = BPStatus.ACTIVE
            snap.suspended_at = None
            reset_ids = []
            for st_id, status in snap.subtask_statuses.items():
                if status == SubtaskStatus.CURRENT.value:
                    snap.subtask_statuses[st_id] = SubtaskStatus.PENDING.value
                    reset_ids.append(st_id)
            logger.info(
                f"[BP] resume: instance={instance_id} bp_id={snap.bp_id} "
                f"reset_subtasks={reset_ids}"
            )
        else:
            logger.debug(
                f"[BP] resume: no-op instance={instance_id} "
                f"status={snap.status.value if snap else 'not_found'}"
            )

    def complete(self, instance_id: str) -> None:
        snap = self._instances.get(instance_id)
        if snap:
            snap.status = BPStatus.COMPLETED
            snap.completed_at = time.time()
            logger.info(f"[BP] complete: instance={instance_id} bp_id={snap.bp_id}")

    def cancel(self, instance_id: str) -> None:
        snap = self._instances.get(instance_id)
        if snap and snap.status in (BPStatus.ACTIVE, BPStatus.SUSPENDED):
            prev_status = snap.status.value
            snap.status = BPStatus.CANCELLED
            snap.completed_at = time.time()
            logger.info(
                f"[BP] cancel: instance={instance_id} bp_id={snap.bp_id} "
                f"prev_status={prev_status}"
            )

    # ── Subtask operations ─────────────────────────────────────

    def advance_subtask(self, instance_id: str) -> None:
        snap = self._instances.get(instance_id)
        if snap:
            old_idx = snap.current_subtask_index
            snap.current_subtask_index += 1
            logger.debug(
                f"[BP] advance_subtask: {instance_id} idx {old_idx} -> {snap.current_subtask_index}"
            )

    def update_subtask_status(self, instance_id: str, subtask_id: str, status: SubtaskStatus) -> None:
        snap = self._instances.get(instance_id)
        if snap:
            snap.subtask_statuses[subtask_id] = status.value

    def update_subtask_output(self, instance_id: str, subtask_id: str, output: dict[str, Any]) -> None:
        snap = self._instances.get(instance_id)
        if snap:
            snap.subtask_outputs[subtask_id] = dict(output)

    def merge_subtask_output(self, instance_id: str, subtask_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """深度合并 changes 到现有输出。返回合并后结果。"""
        snap = self._instances.get(instance_id)
        if not snap:
            return {}
        existing = snap.subtask_outputs.get(subtask_id, {})
        merged = self._deep_merge(existing, changes)
        snap.subtask_outputs[subtask_id] = merged
        return merged

    def mark_downstream_stale(
        self, instance_id: str, from_subtask_id: str, bp_config: BestPracticeConfig,
    ) -> list[str]:
        """将 from_subtask_id 之后的所有 DONE 子任务标记为 STALE。返回受影响的 subtask_id 列表。"""
        snap = self._instances.get(instance_id)
        if not snap:
            return []

        stale_ids: list[str] = []
        found = False
        for s in bp_config.subtasks:
            if s.id == from_subtask_id:
                found = True
                continue
            if found and snap.subtask_statuses.get(s.id) == SubtaskStatus.DONE.value:
                snap.subtask_statuses[s.id] = SubtaskStatus.STALE.value
                stale_ids.append(s.id)
        return stale_ids

    # ── Queries ────────────────────────────────────────────────

    def get(self, instance_id: str) -> BPInstanceSnapshot | None:
        return self._instances.get(instance_id)

    def get_active(self, session_id: str) -> BPInstanceSnapshot | None:
        """返回 session 中唯一的 ACTIVE 实例。"""
        for snap in self._instances.values():
            if snap.session_id == session_id and snap.status == BPStatus.ACTIVE:
                return snap
        return None

    def get_all_for_session(self, session_id: str) -> list[BPInstanceSnapshot]:
        return [s for s in self._instances.values() if s.session_id == session_id]

    def get_status_table(
        self, session_id: str, max_suspended: int = 3,
    ) -> str:
        """Generate status overview table for system prompt injection.

        Args:
            session_id: Session to query.
            max_suspended: Max suspended instances to show in the table.
                Excess suspended instances are summarised in a single line.
        """
        instances = self.get_all_for_session(session_id)
        if not instances:
            return ""

        # Partition: active/suspended shown, completed/cancelled excluded
        active = [
            i for i in instances
            if i.status == BPStatus.ACTIVE
        ]
        suspended = sorted(
            (i for i in instances if i.status == BPStatus.SUSPENDED),
            key=lambda i: i.suspended_at or 0,
            reverse=True,
        )
        shown_suspended = suspended[:max_suspended]
        hidden_count = len(suspended) - len(shown_suspended)

        visible = active + shown_suspended
        if not visible:
            return ""

        lines = [
            "| Instance | BP | Status | Progress | Current Step | RunMode |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for inst in visible:
            bp_name = inst.bp_config.name if inst.bp_config else inst.bp_id
            total = len(inst.subtask_statuses)
            done = sum(
                1 for v in inst.subtask_statuses.values()
                if v == SubtaskStatus.DONE.value
            )
            progress = f"{done}/{total}"
            current_step = ""
            if (
                inst.bp_config
                and 0 <= inst.current_subtask_index < len(inst.bp_config.subtasks)
            ):
                current_step = inst.bp_config.subtasks[
                    inst.current_subtask_index
                ].name
            input_summary = ""
            if inst.initial_input:
                input_summary = " (" + ", ".join(
                    f"{k}={v}" for k, v in list(inst.initial_input.items())[:2]
                ) + ")"
            lines.append(
                f"| {inst.instance_id} | {bp_name}{input_summary} | {inst.status.value} "
                f"| {progress} | {current_step} | {inst.run_mode.value} |"
            )

        if hidden_count > 0:
            lines.append(
                f"\n({hidden_count} more suspended task(s) hidden"
                f" -- use bp_switch_task to view)"
            )

        return "\n".join(lines)

    # ── PendingContextSwitch ──────────────────────────────────

    def set_pending_switch(self, session_id: str, switch: PendingContextSwitch) -> None:
        self._pending_switches[session_id] = switch

    def consume_pending_switch(self, session_id: str) -> PendingContextSwitch | None:
        return self._pending_switches.pop(session_id, None)

    def has_pending_switch(self, session_id: str) -> bool:
        return session_id in self._pending_switches

    # ── Cooldown ───────────────────────────────────────────────

    def set_cooldown(self, session_id: str, turns: int = DEFAULT_COOLDOWN_TURNS) -> None:
        self._cooldowns[session_id] = turns

    def tick_cooldown(self, session_id: str) -> int:
        """递减并返回剩余轮数。"""
        remaining = self._cooldowns.get(session_id, 0)
        if remaining > 0:
            remaining -= 1
            self._cooldowns[session_id] = remaining
        return remaining

    def get_cooldown(self, session_id: str) -> int:
        return self._cooldowns.get(session_id, 0)

    # ── Offered BPs (prevent duplicate trigger per session) ───

    def mark_bp_offered(self, session_id: str, bp_id: str) -> None:
        """记录该 session 已向用户提示过此 bp_id。"""
        self._offered_bps.setdefault(session_id, set()).add(bp_id)

    def is_bp_offered(self, session_id: str, bp_id: str) -> bool:
        """该 bp_id 是否已在此 session 中提示过。"""
        return bp_id in self._offered_bps.get(session_id, set())

    def set_pending_offer(self, session_id: str, offer: dict[str, Any]) -> None:
        self._pending_offers[session_id] = dict(offer)

    def get_pending_offer(self, session_id: str) -> dict[str, Any] | None:
        offer = self._pending_offers.get(session_id)
        return dict(offer) if offer else None

    def clear_pending_offer(self, session_id: str) -> None:
        self._pending_offers.pop(session_id, None)

    # ── Persistence ────────────────────────────────────────────

    def persist_to_session(self, instance_id: str, session: Any) -> None:
        """Persist BP state to session metadata (unified entry point)."""
        snap = self.get(instance_id)
        if not snap or not session:
            return
        try:
            data = self.serialize_for_session(snap.session_id)
            if hasattr(session, "metadata"):
                session.metadata["bp_state"] = data
        except Exception as e:
            logger.warning(f"[BP] Persist failed: {e}")

    def serialize_for_session(self, session_id: str) -> dict[str, Any]:
        """序列化 session 的所有实例 → 可存入 Session.metadata["bp_state"]。"""
        instances = self.get_all_for_session(session_id)
        pending = self._pending_switches.get(session_id)
        return {
            "version": 2,
            "instances": [inst.serialize() for inst in instances],
            "cooldown": self._cooldowns.get(session_id, 0),
            "offered_bps": sorted(self._offered_bps.get(session_id, set())),
            "pending_switch": {
                "suspended_id": pending.suspended_instance_id,
                "target_id": pending.target_instance_id,
            } if pending else None,
        }

    def restore_from_dict(
        self,
        session_id: str,
        data: dict[str, Any],
        config_map: dict[str, BestPracticeConfig] | None = None,
    ) -> int:
        """从序列化 dict 恢复实例。返回恢复的实例数。"""
        if not data:
            return 0
        config_map = config_map or {}
        count = 0
        for inst_data in data.get("instances", []):
            snap = BPInstanceSnapshot.deserialize(inst_data)
            snap.bp_config = config_map.get(snap.bp_id)
            # Safety: never overwrite an existing in-memory instance.
            # Overwrites can roll back progress if the persisted metadata is stale
            # (e.g. advance() updated index but _persist_bp_to_session hasn't run yet).
            existing = self._instances.get(snap.instance_id)
            if existing:
                if existing.bp_config is None and snap.bp_config is not None:
                    existing.bp_config = snap.bp_config
                logger.debug(
                    f"[BP] restore_from_dict: skipped {snap.instance_id} "
                    f"(already in memory, idx={existing.current_subtask_index})"
                )
                continue

            logger.info(
                f"[BP] restore_from_dict: {snap.instance_id} idx={snap.current_subtask_index}"
            )
            self._instances[snap.instance_id] = snap
            count += 1
        if "cooldown" in data:
            self._cooldowns[session_id] = data["cooldown"]
        if "offered_bps" in data:
            self._offered_bps[session_id] = set(data["offered_bps"])
        ps_data = data.get("pending_switch")
        if ps_data:
            self._pending_switches[session_id] = PendingContextSwitch(
                suspended_instance_id=ps_data["suspended_id"],
                target_instance_id=ps_data["target_id"],
            )
        return count

    # ── SQLite persist ──────────────────────────────────────────────

    async def persist_instance(self, instance_id: str) -> None:
        """全量写入，用于创建或 run_mode 变更。"""
        if not self._storage:
            return
        snap = self.get(instance_id)
        if not snap:
            return
        try:
            await self._storage.save_instance(snap)
        except Exception as e:
            logger.warning(f"[BP] persist_instance failed: {e}")

    async def persist_status_change(self, instance_id: str) -> None:
        """更新 status / completed_at / suspended_at。"""
        if not self._storage:
            return
        snap = self.get(instance_id)
        if not snap:
            return
        try:
            await self._storage.update_instance_status(
                instance_id,
                snap.status.value if isinstance(snap.status, BPStatus) else snap.status,
                completed_at=snap.completed_at,
                suspended_at=snap.suspended_at,
            )
        except Exception as e:
            logger.warning(f"[BP] persist_status_change failed: {e}")

    async def persist_subtask_progress(self, instance_id: str) -> None:
        """更新 current_subtask_index + subtask_statuses。"""
        if not self._storage:
            return
        snap = self.get(instance_id)
        if not snap:
            return
        try:
            await self._storage.update_subtask_progress(
                instance_id,
                snap.current_subtask_index,
                dict(snap.subtask_statuses),
            )
        except Exception as e:
            logger.warning(f"[BP] persist_subtask_progress failed: {e}")

    async def persist_subtask_output(self, instance_id: str, subtask_id: str) -> None:
        """更新指定 subtask 的 output。"""
        if not self._storage:
            return
        snap = self.get(instance_id)
        if not snap:
            return
        output = snap.subtask_outputs.get(subtask_id, {})
        try:
            await self._storage.update_subtask_output(instance_id, subtask_id, output)
        except Exception as e:
            logger.warning(f"[BP] persist_subtask_output failed: {e}")

    async def persist_context_summary(self, instance_id: str) -> None:
        """更新 context_summary（switch 后 context_bridge 调用）。"""
        if not self._storage:
            return
        snap = self.get(instance_id)
        if not snap:
            return
        try:
            await self._storage.update_context_summary(instance_id, snap.context_summary)
        except Exception as e:
            logger.warning(f"[BP] persist_context_summary failed: {e}")

    async def persist_supplemented_input(self, instance_id: str, subtask_id: str) -> None:
        """更新用户补充参数。"""
        if not self._storage:
            return
        snap = self.get(instance_id)
        if not snap:
            return
        data = snap.supplemented_inputs.get(subtask_id, {})
        try:
            await self._storage.update_supplemented_input(instance_id, subtask_id, data)
        except Exception as e:
            logger.warning(f"[BP] persist_supplemented_input failed: {e}")

    async def ensure_loaded(self, instance_id: str) -> BPInstanceSnapshot | None:
        """内存优先 → SQLite 回退的统一入口。路由层统一调用此方法。"""
        snap = self.get(instance_id)
        if snap is not None or not self._storage:
            return snap
        try:
            row = await self._storage.load_instance(instance_id)
        except Exception as e:
            logger.warning(f"[BP] ensure_loaded load failed: {e}")
            return None
        if not row:
            return None
        from ..facade import get_bp_config_loader
        loader = get_bp_config_loader()
        config_map = dict(loader.configs) if loader and loader.configs else {}
        await self.restore_from_db(row["session_id"], config_map=config_map)
        return self.get(instance_id)

    async def restore_from_db(
        self,
        session_id: str,
        config_map: dict[str, Any] | None = None,
    ) -> int:
        """从 SQLite 恢复 session 的所有实例。返回恢复数量。

        继承 restore_from_dict() 的安全保护：不覆盖内存中已有实例。
        """
        if not self._storage:
            return 0
        config_map = config_map or {}
        count = 0
        try:
            rows = await self._storage.load_instances_by_session(session_id)
        except Exception as e:
            logger.warning(f"[BP] restore_from_db load failed: {e}")
            return 0

        for row in rows:
            snap = BPInstanceSnapshot.deserialize(row)
            snap.bp_config = config_map.get(snap.bp_id)

            existing = self._instances.get(snap.instance_id)
            if existing:
                if existing.bp_config is None and snap.bp_config is not None:
                    existing.bp_config = snap.bp_config
                logger.debug(
                    f"[BP] restore_from_db: skipped {snap.instance_id} "
                    f"(already in memory, idx={existing.current_subtask_index})"
                )
                continue

            logger.info(
                f"[BP] restore_from_db: {snap.instance_id} idx={snap.current_subtask_index}"
            )
            self._instances[snap.instance_id] = snap
            count += 1

        return count

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _deep_merge(base: dict, overlay: dict) -> dict:
        """递归合并 overlay 到 base。数组完整替换。"""
        result = dict(base)
        for k, v in overlay.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = BPStateManager._deep_merge(result[k], v)
            else:
                result[k] = v
        return result
