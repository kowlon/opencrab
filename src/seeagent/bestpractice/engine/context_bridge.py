"""ContextBridge — 上下文压缩与恢复。

负责在 BP 实例切换时:
1. 压缩当前上下文 → context_summary (LLM 调用)
2. 恢复目标实例的上下文 → 注入恢复消息
3. 管理 PendingContextSwitch 生命周期
"""

from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import PendingContextSwitch  # noqa: F401
    from .state_manager import BPStateManager

logger = logging.getLogger(__name__)


class ContextBridge:
    """管理 BP 实例间的上下文切换。"""

    def __init__(self, state_manager: BPStateManager | None = None) -> None:
        self._state_manager = state_manager

    def set_state_manager(self, state_manager: BPStateManager) -> None:
        self._state_manager = state_manager

    async def execute_pending_switch(
        self,
        session_id: str,
        brain: Any = None,
        messages: list[dict] | None = None,
    ) -> bool:
        """消费 PendingContextSwitch，执行上下文切换。

        Called from Agent._prepare_session_context() step 10.5.

        Args:
            session_id: 当前会话 ID
            brain: Brain 实例（保留兼容，未使用）
            messages: 当前对话历史列表（来自 _prepare_session_context 局部变量）

        Returns: True if switch was executed, False if no pending switch.
        """
        if not self._state_manager:
            return False

        switch = self._state_manager.consume_pending_switch(session_id)
        if not switch:
            return False

        logger.info(
            f"[BP] Executing context switch: "
            f"{switch.suspended_instance_id} → {switch.target_instance_id}"
        )

        # 1. 压缩当前上下文
        if switch.suspended_instance_id:
            suspended = self._state_manager.get(switch.suspended_instance_id)
            summary = self._compress_context(messages=messages, snap=suspended)
            if suspended:
                suspended.context_summary = summary

        # 2. 恢复目标实例的上下文
        target = self._state_manager.get(switch.target_instance_id)
        if target and messages is not None:
            self._restore_context(messages, target)

        return True

    def _compress_context(
        self,
        messages: list[dict] | None = None,
        snap: Any = None,
    ) -> str:
        """压缩对话上下文为摘要。

        策略: 提取最近 10 条消息的文本内容（支持 str 和 list content blocks），
        并附加 BP 实例已完成子任务的输出摘要（如有）。
        """
        try:
            parts: list[str] = []

            # 1. 已完成子任务输出摘要（结构化数据，优先级最高）
            if snap and snap.subtask_outputs:
                output_lines = []
                for st_id, output in snap.subtask_outputs.items():
                    preview = json.dumps(output, ensure_ascii=False)[:300]
                    output_lines.append(f"  {st_id}: {preview}")
                if output_lines:
                    parts.append("Subtask outputs:\n" + "\n".join(output_lines))

            # 2. 最近对话消息
            if messages:
                summaries = []
                for msg in messages[-10:]:
                    content = msg.get("content", "")
                    text = self._extract_text(content)
                    if text:
                        role = msg.get("role", "?")
                        summaries.append(f"[{role}] {text[:300]}")
                if summaries:
                    parts.append("Recent messages:\n" + "\n".join(summaries))

            return "\n---\n".join(parts) if parts else ""
        except Exception as e:
            logger.warning(f"[BP] Context compression failed: {e}")
            return ""

    @staticmethod
    def _extract_text(content: Any) -> str:
        """Extract text from message content (str or list of content blocks)."""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        texts.append(f"[tool_result: {str(block.get('content', ''))[:100]}]")
            return " ".join(texts).strip()
        return ""

    def _restore_context(self, messages: list[dict], snap: Any) -> None:
        """将目标实例的 context_summary 注入为恢复消息。

        Args:
            messages: 对话历史列表（直接修改）
            snap: 目标 BP 实例快照
        """
        if not snap.context_summary:
            return
        try:
            recovery_msg = (
                f"[任务恢复] 你正在继续执行最佳实践任务。\n"
                f"之前的上下文摘要: {snap.context_summary}\n"
                f"请继续当前子任务的执行。"
            )
            # Ensure role alternation: if last message is user, merge into it
            if messages and messages[-1].get("role") == "user":
                content = messages[-1]["content"]
                if isinstance(content, str):
                    messages[-1]["content"] = content + f"\n\n{recovery_msg}"
                elif isinstance(content, list):
                    content.append({"type": "text", "text": recovery_msg})
            else:
                messages.append({"role": "user", "content": recovery_msg})
        except Exception as e:
            logger.warning(f"[BP] Context restore failed: {e}")

    def build_recovery_message(self, snap: Any) -> str:
        """生成任务恢复注入消息。用于前端展示或 prompt 注入。"""
        bp_name = snap.bp_config.name if snap.bp_config else snap.bp_id
        return (
            f"[任务恢复] 最佳实践「{bp_name}」\n"
            f"当前进度: 第 {snap.current_subtask_index + 1} 步\n"
            f"上下文摘要: {snap.context_summary or '(无)'}"
        )
