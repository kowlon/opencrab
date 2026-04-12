"""BPMatcher -- BP trigger matching (keyword + LLM fallback)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..models import TriggerType, collect_all_properties, collect_all_required

if TYPE_CHECKING:
    from ..config import BPConfigLoader
    from ..engine import BPStateManager
    from .loader import PromptTemplateLoader

logger = logging.getLogger(__name__)


class BPMatcher:
    """BP trigger matching: keyword-based and LLM-fallback."""

    def __init__(
        self,
        config_loader: BPConfigLoader,
        state_manager: BPStateManager,
        prompt_loader: PromptTemplateLoader,
    ) -> None:
        self._config_loader = config_loader
        self._state_manager = state_manager
        self._prompt_loader = prompt_loader

    # ── Keyword matching ──────────────────────────────────────

    def match_keyword(self, user_message: str, session_id: str) -> dict | None:
        """Check user message against CONTEXT triggers of registered BPs.

        Returns match metadata dict or None.
        Respects cooldown and skips if an active BP instance already exists.
        """
        if self._state_manager.get_cooldown(session_id) > 0:
            logger.debug(f"[BP] match_keyword: skipped, cooldown active session={session_id}")
            return None
        if self._state_manager.get_active(session_id):
            logger.debug(
                f"[BP] match_keyword: skipped, active instance exists session={session_id}"
            )
            return None

        for bp_id, config in self._config_loader.configs.items():
            if self._state_manager.is_bp_offered(session_id, bp_id):
                logger.debug(
                    f"[BP] match_keyword: skipped bp_id={bp_id}, "
                    f"already offered session={session_id}"
                )
                continue
            for trigger in config.triggers:
                if trigger.type == TriggerType.CONTEXT:
                    if any(kw in user_message for kw in trigger.conditions):
                        logger.info(
                            f"[BP] match_keyword: matched bp_id={bp_id} session={session_id}"
                        )
                        first_input_schema = (
                            config.subtasks[0].input_schema if config.subtasks else None
                        )
                        return {
                            "bp_id": bp_id,
                            "bp_name": config.name,
                            "description": config.description,
                            "subtask_count": len(config.subtasks),
                            "subtasks": [{"id": s.id, "name": s.name} for s in config.subtasks],
                            "user_query": user_message,
                            "first_input_schema": first_input_schema,
                        }
        logger.debug(
            f"[BP] match_keyword: no match session={session_id} "
            f"candidates={len(self._config_loader.configs)}"
        )
        return None

    # ── LLM fallback matching ─────────────────────────────────

    async def match_llm(
        self,
        user_message: str,
        session_id: str,
        brain: Any,
        history_context: str = "",
    ) -> dict | None:
        """LLM fallback: when keyword matching fails, use LLM to judge intent."""
        if not brain:
            return None
        if self._state_manager.get_cooldown(session_id) > 0:
            return None
        if self._state_manager.get_active(session_id):
            return None

        bp_list_lines = []
        from seeagent.api.routes.bestpractice import _build_combined_user_schema

        for bp_id, config in self._config_loader.configs.items():
            if self._state_manager.is_bp_offered(session_id, bp_id):
                continue
            combined_schema = _build_combined_user_schema(config) if config.subtasks else {}
            params_desc = ""
            if combined_schema:
                props = collect_all_properties(combined_schema)
                required = collect_all_required(combined_schema)
                param_lines = []
                for pname, pinfo in props.items():
                    req_mark = "必填" if pname in required else "选填"
                    param_lines.append(
                        f"   - {pname} ({pinfo.get('type', 'string')}, {req_mark}): "
                        f"{pinfo.get('description', '')}"
                    )
                if param_lines:
                    params_desc = "\n" + "\n".join(param_lines)

            bp_list_lines.append(
                f'- {bp_id}: "{config.name}"\n  描述: {config.description}{params_desc}'
            )

        if not bp_list_lines:
            return None

        logger.debug(
            f"[BP] match_llm: attempting session={session_id} candidates={len(bp_list_lines)}"
        )
        bp_list = "\n".join(bp_list_lines)

        try:
            prompt = self._prompt_loader.render(
                "bp_match",
                bp_list=bp_list,
                user_message=user_message,
                history_context=history_context or user_message,
            )
            resp = await brain.think_lightweight(prompt, max_tokens=512)
            text = resp.content if hasattr(resp, "content") else str(resp)

            from seeagent.bestpractice.engine import BPEngine

            parsed = BPEngine._parse_output(text)
            if not isinstance(parsed, dict):
                return None

            if not parsed.get("matched") or parsed.get("confidence", 0) < 0.7:
                logger.debug(
                    f"[BP] match_llm: rejected, confidence={parsed.get('confidence')} "
                    f"session={session_id}"
                )
                return None

            bp_id = parsed.get("bp_id", "")
            config = self._config_loader.configs.get(bp_id)
            if not config:
                return None

            if self._state_manager.is_bp_offered(session_id, bp_id):
                return None

            logger.info(
                f"[BP] match_llm: matched bp_id={bp_id} "
                f"confidence={parsed.get('confidence')} session={session_id}"
            )
            first_input_schema = config.subtasks[0].input_schema if config.subtasks else None
            return {
                "bp_id": bp_id,
                "bp_name": config.name,
                "description": config.description,
                "subtask_count": len(config.subtasks),
                "subtasks": [{"id": s.id, "name": s.name} for s in config.subtasks],
                "extracted_input": parsed.get("extracted_input", {}),
                "user_query": user_message,
                "first_input_schema": first_input_schema,
            }
        except Exception as e:
            logger.warning(f"[BP] LLM match failed: {e}")
            return None
