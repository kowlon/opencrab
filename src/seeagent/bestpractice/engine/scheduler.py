"""BP 子任务调度器。

TaskScheduler 基类 + LinearScheduler 实现。
DAGScheduler 预留接口，后续扩展。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from ..models import SubtaskStatus, collect_all_properties, collect_all_upstream

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..models import BestPracticeConfig, BPInstanceSnapshot, SubtaskConfig


class TaskScheduler(ABC):
    """BP 子任务调度器基类。"""

    def __init__(self, bp_config: BestPracticeConfig, snapshot: BPInstanceSnapshot):
        self._config = bp_config
        self._snap = snapshot

    @abstractmethod
    def get_ready_tasks(self) -> list[SubtaskConfig]:
        """返回当前可执行的子任务列表。"""
        ...

    @abstractmethod
    def complete_task(self, subtask_id: str, output: dict) -> None:
        """标记子任务完成，更新输出。"""
        ...

    def resolve_input(self, subtask_id: str) -> dict:
        """解析子任务输入。

        优先使用 input_mapping，fallback 到前一个子任务输出。
        所有分支统一合并 supplemented_inputs (R19)。
        """
        subtask = self._find_subtask(subtask_id)
        if not subtask:
            logger.warning(f"[BP] resolve_input: subtask not found subtask_id={subtask_id}")
            return {}

        if subtask.input_mapping:
            base: dict[str, Any] = {}
            for field, upstream_id in subtask.input_mapping.items():
                base[field] = self._snap.subtask_outputs.get(upstream_id, {})
            logger.debug(
                f"[BP] resolve_input: subtask={subtask_id} source=input_mapping "
                f"keys={list(subtask.input_mapping.keys())}"
            )
        else:
            idx = self._get_subtask_index(subtask_id)
            if idx == 0:
                base = dict(self._snap.initial_input)
                logger.debug(
                    f"[BP] resolve_input: subtask={subtask_id} "
                    f"source=initial_input keys={list(base.keys())}"
                )
            else:
                prev_id = self._config.subtasks[idx - 1].id
                base = dict(self._snap.subtask_outputs.get(prev_id, {}))
                logger.debug(
                    f"[BP] resolve_input: subtask={subtask_id} "
                    f"source=prev_output prev={prev_id} keys={list(base.keys())}"
                )

        # 统一合并 supplemented_inputs
        supplement = self._snap.supplemented_inputs.get(subtask_id, {})
        if supplement:
            base.update(supplement)
            logger.debug(
                f"[BP] resolve_input: subtask={subtask_id} "
                f"supplemented keys={list(supplement.keys())}"
            )

        return base

    def is_done(self) -> bool:
        """所有子任务是否全部完成。用 .value 比较 (R21)。"""
        return all(
            self._snap.subtask_statuses.get(st.id) == SubtaskStatus.DONE.value
            for st in self._config.subtasks
        )

    def derive_output_schema(self, subtask_id: str) -> dict | None:
        """推导子任务的输出 schema。

        当下一个子任务的 input_schema 定义了 upstream 字段时，
        只返回 upstream 声明的 properties 子集（而非完整 input_schema），
        避免要求当前子任务输出本应由用户提供的字段。
        """
        idx = self._get_subtask_index(subtask_id)
        if idx >= len(self._config.subtasks) - 1:
            return self._config.final_output_schema or None
        next_subtask = self._config.subtasks[idx + 1]
        schema = next_subtask.input_schema
        if not schema:
            return None

        # 顶层 upstream 优先（向后兼容），否则从分支收集并集
        upstream = schema.get("upstream") or collect_all_upstream(schema)
        if not upstream:
            return None

        all_props = collect_all_properties(schema)
        filtered = {k: v for k, v in all_props.items() if k in upstream}
        return {
            "type": "object",
            "properties": filtered,
            "required": sorted(upstream),
        }

    def _find_subtask(self, subtask_id: str) -> SubtaskConfig | None:
        return next((s for s in self._config.subtasks if s.id == subtask_id), None)

    def _get_subtask_index(self, subtask_id: str) -> int:
        for i, s in enumerate(self._config.subtasks):
            if s.id == subtask_id:
                return i
        return -1


class LinearScheduler(TaskScheduler):
    """线性调度器: 子任务严格按顺序执行。"""

    def get_ready_tasks(self) -> list[SubtaskConfig]:
        idx = self._snap.current_subtask_index
        if idx >= len(self._config.subtasks):
            return []
        subtask = self._config.subtasks[idx]
        status = self._snap.subtask_statuses.get(subtask.id)
        if status in (SubtaskStatus.PENDING.value, SubtaskStatus.STALE.value, None):
            return [subtask]
        return []

    def complete_task(self, subtask_id: str, output: dict) -> None:
        self._snap.subtask_outputs[subtask_id] = output
        self._snap.subtask_statuses[subtask_id] = SubtaskStatus.DONE.value
        idx = self._get_subtask_index(subtask_id)
        if idx >= 0 and self._snap.current_subtask_index <= idx:
            self._snap.current_subtask_index = idx + 1
