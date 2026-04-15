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

    def export_resolved_inputs(self) -> dict[str, dict]:
        resolved_inputs: dict[str, dict] = {}
        for subtask in self._config.subtasks:
            resolved = self.resolve_input(subtask.id)
            if resolved:
                resolved_inputs[subtask.id] = resolved
        return resolved_inputs

    def _aggregate_upstream_from(self, subtask_id: str, output: dict) -> dict:
        """为最后一个子任务的 output 自动补充 final_output_schema.upstream_from 声明的字段。

        upstream_from 的 schema:
            upstream_from: {field_name: source_subtask_id, ...}

        语义: 如果 LLM 没有产出某个字段(在 upstream_from 里声明),
        引擎会从 source_subtask_id 的 subtask_outputs 里取对应值填进来。
        如果 LLM 已经产出该字段,则不覆盖(LLM 的版本优先)。

        只对最后一个子任务生效。非最后子任务直接原样返回 output。
        """
        idx = self._get_subtask_index(subtask_id)
        if idx != len(self._config.subtasks) - 1:
            return output
        final_schema = self._config.final_output_schema or {}
        upstream_from = final_schema.get("upstream_from")
        if not upstream_from or not isinstance(upstream_from, dict):
            return output
        merged = dict(output)
        for field, source_subtask_id in upstream_from.items():
            if field in merged:
                continue  # LLM 已经产出,不覆盖
            source_output = self._snap.subtask_outputs.get(source_subtask_id, {})
            # 防御性检查: subtask_outputs 的类型合约是 dict[str, dict],
            # 但持久化恢复或自定义 scheduler 可能违反合约,这里加一道 runtime 守卫
            # 避免后续 `in` / `.keys()` 触发 AttributeError 或奇怪的子串匹配行为。
            if not isinstance(source_output, dict):
                logger.warning(
                    f"[BP] upstream_from: source subtask '{source_subtask_id}' "
                    f"output is not a dict (got {type(source_output).__name__}); "
                    f"skipped field '{field}'"
                )
                continue
            if field in source_output:
                merged[field] = source_output[field]
                logger.debug(
                    f"[BP] upstream_from: aggregated '{field}' from "
                    f"'{source_subtask_id}' into '{subtask_id}' output"
                )
            else:
                logger.warning(
                    f"[BP] upstream_from: field '{field}' not found in source "
                    f"subtask '{source_subtask_id}' output; skipped (got keys: "
                    f"{list(source_output.keys())})"
                )
        return merged

    def _collect_upstream_from_fields_for(self, subtask_id: str) -> dict[str, dict]:
        """收集 final_output_schema.upstream_from 里声明由 subtask_id 提供的字段。

        返回 {field_name: property_schema_dict},property schema 从
        final_output_schema.properties 取。

        这是方案 D 的核心: 让 upstream_from 的声明能**反向驱动** source subtask
        产出对应字段,不依赖下游 input_schema.upstream 的存在。
        """
        final_schema = self._config.final_output_schema or {}
        upstream_from = final_schema.get("upstream_from")
        if not upstream_from or not isinstance(upstream_from, dict):
            return {}
        final_props = final_schema.get("properties", {}) or {}
        result: dict[str, dict] = {}
        for field, src_id in upstream_from.items():
            if src_id == subtask_id and field in final_props:
                result[field] = final_props[field]
        return result

    def derive_output_schema(self, subtask_id: str) -> dict | None:
        """推导子任务的输出 schema。

        - 最后一个子任务: 使用 final_output_schema,并过滤掉 upstream_from 声明的字段
          (这些字段由引擎从 source subtask 自动聚合,无需 LLM 产出)
        - 非最后子任务: 下列来源取并集
            a) 下一个子任务的 input_schema.upstream 声明的字段 (原有机制)
            b) final_output_schema.upstream_from 中指向当前 subtask 的字段 (方案 D 新增)
          后者让 upstream_from 声明自给自足,不再隐式依赖 a)
        """
        idx = self._get_subtask_index(subtask_id)
        if idx >= len(self._config.subtasks) - 1:
            schema = self._config.final_output_schema
            if not schema:
                return None
            upstream_from = schema.get("upstream_from")
            if upstream_from and isinstance(upstream_from, dict):
                # 过滤掉 upstream_from 声明的字段 —— 引擎会在 complete_task 里自动聚合。
                # 用浅拷贝 + 局部覆盖而非重建,保留原 schema 的所有其他顶层元信息
                # (如 description / additionalProperties / oneOf / anyOf / 自定义 x-* 扩展等),
                # 避免对 _schema_to_example 等下游消费方造成行为回归。
                upstream_fields = set(upstream_from.keys())
                filtered = dict(schema)
                filtered["properties"] = {
                    k: v for k, v in schema.get("properties", {}).items()
                    if k not in upstream_fields
                }
                filtered["required"] = [
                    r for r in schema.get("required", [])
                    if r not in upstream_fields
                ]
                # upstream_from 是引擎内部 marker,不应泄漏到 LLM 看到的输出 schema
                filtered.pop("upstream_from", None)
                return filtered
            return schema

        # ── 非最后子任务 ──────────────────────────────────────────
        next_subtask = self._config.subtasks[idx + 1]
        schema = next_subtask.input_schema or {}

        # 来源 a: 下一个子任务的 upstream 声明(原有机制)
        downstream_upstream = schema.get("upstream") or collect_all_upstream(schema) or []
        downstream_props = collect_all_properties(schema) if schema else {}

        # 来源 b: final_output_schema.upstream_from 指向本 subtask 的字段(方案 D 新增)
        upstream_from_fields = self._collect_upstream_from_fields_for(subtask_id)

        # 没有任何来源 → 维持原语义返回 None
        if not downstream_upstream and not upstream_from_fields:
            return None

        # 合并 properties: 下游 input_schema 优先,final_output_schema 兜底
        merged_props: dict[str, dict] = {}
        for field in downstream_upstream:
            if field in downstream_props:
                merged_props[field] = downstream_props[field]
        for field, spec in upstream_from_fields.items():
            if field not in merged_props:
                merged_props[field] = spec

        if not merged_props:
            return None

        return {
            "type": "object",
            "properties": merged_props,
            "required": sorted(merged_props.keys()),
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
        # WAITING_INPUT 也视为 ready:
        # 用户再次触发 bp_next/advance 时应重新检查 input 完整性，
        # 若仍缺失则重发 bp_ask_user 事件（而非静默无响应）。
        if status in (
            SubtaskStatus.PENDING.value,
            SubtaskStatus.STALE.value,
            SubtaskStatus.WAITING_INPUT.value,
            None,
        ):
            return [subtask]
        return []

    def complete_task(self, subtask_id: str, output: dict) -> None:
        # 对最后一个子任务,聚合 final_output_schema.upstream_from 声明的字段
        output = self._aggregate_upstream_from(subtask_id, output)
        self._snap.subtask_outputs[subtask_id] = output
        self._snap.subtask_statuses[subtask_id] = SubtaskStatus.DONE.value
        idx = self._get_subtask_index(subtask_id)
        if idx >= 0 and self._snap.current_subtask_index <= idx:
            self._snap.current_subtask_index = idx + 1
