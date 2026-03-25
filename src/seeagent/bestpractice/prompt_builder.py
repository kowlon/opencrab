"""BPPromptBuilder -- builds BP-related system prompt sections."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .models import TriggerType

if TYPE_CHECKING:
    from .config_loader import BPConfigLoader
    from .prompt_loader import PromptTemplateLoader
    from .state_manager import BPStateManager

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
            if config.subtasks and config.subtasks[0].input_schema:
                schema = config.subtasks[0].input_schema
                reqs = schema.get("required", [])
                props = schema.get("properties", {})
                if reqs:
                    hints = [
                        f"{req}({props.get(req, {}).get('description', '')})"
                        for req in reqs
                    ]
                    required_inputs = (
                        f"\n  必需参数: {', '.join(hints)} "
                        f"(调用 bp_start 时放入 input_data)"
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
                logger.info(
                    f"[BP-DEBUG] dynamic_prompt: instance={iid}, "
                    f"session_id={snap.session_id}, "
                    f"idx={snap.current_subtask_index}, "
                    f"status={snap.status.value}"
                )
        logger.info(
            f"[BP-DEBUG] dynamic_prompt: querying session_id={session_id}, "
            f"total_instances={len(all_instances)}"
        )

        status_table = self._state_manager.get_status_table(session_id)
        if not status_table:
            return ""

        active = self._state_manager.get_active(session_id)
        active_context = ""
        intent_routing = ""

        if active:
            bp_name = active.bp_config.name if active.bp_config else active.bp_id
            idx = active.current_subtask_index
            total = len(active.subtask_statuses)
            done = sum(1 for v in active.subtask_statuses.values() if v == "done")
            active_context = (
                f"**当前活跃任务**: {bp_name} (进度: {done}/{total})\n"
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
                    intent_routing = (
                        "用户可能想要:\n"
                        "A) 修改已完成子任务结果 (bp_edit_output)\n"
                        "B) 切换到其他任务 (bp_switch_task)\n"
                        "C) 取消当前任务 (bp_cancel)\n"
                        "D) 询问相关问题\n"
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
            intent_routing=intent_routing,
        )
