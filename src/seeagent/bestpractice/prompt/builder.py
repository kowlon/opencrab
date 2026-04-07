"""BPPromptBuilder -- builds BP-related system prompt sections."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from ..models import ArtifactKind, ContextEnvelope, TriggerType

_DYNAMIC_OUTPUTS_BUDGET = 1000  # max chars for outputs_preview (~250-300 tokens)

if TYPE_CHECKING:
    from ..config import BPConfigLoader
    from ..engine import BPStateManager
    from .loader import PromptTemplateLoader

logger = logging.getLogger(__name__)


class BPPromptBuilder:
    """Builds static and dynamic BP system prompt sections."""

    def __init__(
        self,
        config_loader: BPConfigLoader,
        state_manager: BPStateManager,
        prompt_loader: PromptTemplateLoader,
    ) -> None:
        self._config_loader = config_loader
        self._state_manager = state_manager
        self._prompt_loader = prompt_loader

    # ── Static section ────────────────────────────────────────

    def build_static_section(self) -> str:
        """BP capabilities + available template list + interaction rules."""
        configs = self._config_loader.configs
        if not configs:
            return ""

        bp_list_lines = []
        for bp_id, config in configs.items():
            triggers_desc = ""
            for t in config.triggers:
                if t.type == TriggerType.COMMAND:
                    triggers_desc += f" (命令: \"{t.pattern}\")"
                elif t.type == TriggerType.CONTEXT:
                    triggers_desc += f" (关键词: {', '.join(t.conditions)})"

            subtask_names = " → ".join(s.name for s in config.subtasks)

            required_inputs = ""
            grouped_hints: dict[str, list[str]] = {}
            for subtask in config.subtasks:
                schema = subtask.input_schema
                if not schema:
                    continue
                upstream = set(schema.get("upstream", []))
                branches = schema.get("oneOf") or schema.get("anyOf")
                if branches:
                    seen: set[str] = set()
                    for branch in branches:
                        props = branch.get("properties", {})
                        for req in branch.get("required", []):
                            if req not in upstream and req not in seen:
                                seen.add(req)
                                desc = props.get(req, {}).get("description", "")
                                grouped_hints.setdefault(subtask.name, []).append(
                                    f"{req}({desc})"
                                )
                else:
                    props = schema.get("properties", {})
                    for req in schema.get("required", []):
                        if req not in upstream:
                            desc = props.get(req, {}).get("description", "")
                            grouped_hints.setdefault(subtask.name, []).append(
                                f"{req}({desc})"
                            )
            if grouped_hints:
                parts = [
                    f"    [{name}]: {', '.join(hints)}"
                    for name, hints in grouped_hints.items()
                ]
                required_inputs = (
                    "\n  必需参数 (调用 bp_start 时尽量从用户消息中提取所有参数放入 input_data):\n"
                    + "\n".join(parts)
                )

            bp_list_lines.append(
                f"- **{config.name}** (`{bp_id}`){triggers_desc}: "
                f"{config.description}\n"
                f"  流程: {subtask_names}{required_inputs}"
            )

        bp_list = "\n".join(bp_list_lines)
        return self._prompt_loader.render("system_static", bp_list=bp_list)

    # ── Dynamic section ───────────────────────────────────────

    def build_dynamic_section(self, session_id: str) -> str:
        """Current state + active context + intent routing."""
        all_instances = list(self._state_manager._instances.keys())
        if all_instances:
            for iid, snap in self._state_manager._instances.items():
                logger.debug(
                    f"[BP] dynamic_prompt: instance={iid}, "
                    f"session_id={snap.session_id}, "
                    f"idx={snap.current_subtask_index}, "
                    f"status={snap.status.value}"
                )
        logger.debug(
            f"[BP] dynamic_prompt: querying session_id={session_id}, "
            f"total_instances={len(all_instances)}"
        )

        status_table = self._state_manager.get_status_table(session_id)
        if not status_table:
            return ""

        active = self._state_manager.get_active(session_id)
        active_context = ""
        intent_routing = ""
        outputs_preview = ""
        user_preferences = ""

        if active:
            bp_name = active.bp_config.name if active.bp_config else active.bp_id
            idx = active.current_subtask_index
            total = len(active.subtask_statuses)
            done = sum(1 for v in active.subtask_statuses.values() if v == "done")
            active_context = (
                f"**当前活跃任务**: {bp_name} (进度: {done}/{total})\n"
            )

            # Outputs preview: show completed subtask outputs (budget-limited)
            if active.subtask_outputs:
                preview_lines: list[str] = []
                total_len = 0
                for st_id, output in active.subtask_outputs.items():
                    line = json.dumps(output, ensure_ascii=False)[:200]
                    if total_len + len(line) > _DYNAMIC_OUTPUTS_BUDGET:
                        preview_lines.append("...")
                        break
                    preview_lines.append(f"- {st_id}: {line}")
                    total_len += len(line)
                if preview_lines:
                    outputs_preview = (
                        "### 已完成子任务输出\n" + "\n".join(preview_lines)
                    )

            # User preferences: support both new plain-text and old JSON formats
            if active.context_summary:
                cs = active.context_summary
                if cs.strip().startswith("{"):
                    envelope = ContextEnvelope.from_v1(cs)
                    semantic = envelope.summary
                    if not semantic:
                        artifacts = envelope.get_artifacts(ArtifactKind.SEMANTIC_SUMMARY)
                        if artifacts:
                            semantic = artifacts[0].content
                else:
                    semantic = cs
                if semantic:
                    user_preferences = (
                        f"### 用户偏好/上下文\n{semantic}"
                    )

            if active.bp_config:
                statuses = list(active.subtask_statuses.values())
                current_status = statuses[idx] if idx < len(statuses) else ""
                prev_status = (
                    statuses[idx - 1] if idx > 0 and idx <= len(statuses) else ""
                )

                if current_status == "waiting_input":
                    intent_routing = (
                        "当前子任务等待用户输入参数。\n"
                        "如果用户提供了参数值，调用 bp_answer"
                        "(subtask_id=..., data={...}) 补充。\n"
                        "如果用户想取消，调用 bp_cancel。\n"
                    )
                elif prev_status == "done" or current_status == "done":
                    intent_routing = (
                        "上一步已完成。用户可能想要:\n"
                        "A) 继续下一步 → 调用 bp_next\n"
                        "B) 修改上一步结果 → 调用 "
                        "bp_edit_output(subtask_id=..., changes={...})\n"
                        "C) 取消任务 → 调用 bp_cancel\n"
                        "D) 询问其他问题（不涉及 BP 操作）\n"
                    )
                else:
                    suspended_list = self._format_suspended_list(session_id)
                    switch_hint = (
                        f"B) 切换到其他任务:\n{suspended_list}\n"
                        if suspended_list
                        else "B) 切换到其他任务 (bp_switch_task)\n"
                    )
                    intent_routing = (
                        "用户可能想要:\n"
                        "A) 修改已完成子任务结果 (bp_edit_output)\n"
                        f"{switch_hint}"
                        "C) 取消当前任务 (bp_cancel)\n"
                        "D) 询问相关问题\n"
                    )
                    if suspended_list:
                        logger.debug(
                            f"[BP] intent_routing(active+suspended): "
                            f"active={active.instance_id} suspended_list={suspended_list!r}"
                        )

        # When no active instance but suspended ones exist, guide the LLM
        if not active and not intent_routing:
            suspended_list = self._format_suspended_list(session_id)
            if suspended_list:
                logger.debug(
                    f"[BP] intent_routing(suspended-only): "
                    f"session={session_id} suspended_list={suspended_list!r}"
                )
                intent_routing = (
                    "当前有暂停的任务。用户可能想要:\n"
                    f"A) 恢复已暂停的任务:\n{suspended_list}\n"
                    "B) 开始新的最佳实践任务 → 调用 bp_start\n"
                    "C) 询问相关问题\n"
                )

        cooldown = self._state_manager.get_cooldown(session_id)
        if cooldown > 0:
            active_context += (
                f"\n⚠️ BP 推断冷却中 (剩余 {cooldown} 轮)，COMMAND 触发仍生效。\n"
            )

        return self._prompt_loader.render(
            "system_dynamic",
            status_table=status_table,
            active_context=active_context,
            outputs_preview=outputs_preview,
            user_preferences=user_preferences,
            intent_routing=intent_routing,
        )

    def _format_suspended_list(self, session_id: str) -> str:
        """Return bullet lines for suspended instances, for use in intent_routing."""
        from ..models import BPStatus

        suspended = [
            s
            for s in self._state_manager.get_all_for_session(session_id)
            if s.status == BPStatus.SUSPENDED
        ]
        lines = []
        for s in suspended:
            name = s.bp_config.name if s.bp_config else s.bp_id
            done = sum(1 for v in s.subtask_statuses.values() if v == "done")
            total = len(s.subtask_statuses)
            input_hint = ""
            if s.initial_input:
                input_hint = " | " + ", ".join(
                    f"{k}={v}" for k, v in list(s.initial_input.items())[:3]
                )
            lines.append(
                f'  - 「{name}{input_hint}」'
                f'→ bp_switch_task(target_instance_id="{s.instance_id}")'
                f" [进度: {done}/{total}]"
            )
        return "\n".join(lines)
