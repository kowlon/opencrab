"""BP 系统入口 — 单例工厂。

提供:
- get_bp_engine(): 延迟初始化 BPEngine/BPStateManager/BPConfigLoader/BPToolHandler
- get_bp_handler(): 获取 BPToolHandler (用于 handler_registry.register())
- get_static_prompt_section(): 获取 BP 静态 system prompt 段
- get_dynamic_prompt_section(): 获取 BP 动态 system prompt 段
- match_bp_from_message(): 关键词触发匹配
- llm_match_bp_from_message(): LLM 回退匹配
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import BPConfigLoader
    from .engine import BPEngine, BPStateManager, ContextBridge
    from .handler import BPToolHandler
    from .prompt import BPMatcher, BPPromptBuilder, PromptTemplateLoader

logger = logging.getLogger(__name__)

# Singleton state
_bp_engine: BPEngine | None = None
_bp_handler: BPToolHandler | None = None
_bp_state_manager: BPStateManager | None = None
_bp_config_loader: BPConfigLoader | None = None
_bp_context_bridge: ContextBridge | None = None
_bp_prompt_loader: PromptTemplateLoader | None = None
_bp_matcher: BPMatcher | None = None
_bp_prompt_builder: BPPromptBuilder | None = None
_initialized = False


def _find_bp_dirs() -> list[Path]:
    """搜索 best_practice/ 目录位置。"""
    candidates = []

    # 1. 项目根目录 (CWD 或 git root)
    cwd = Path.cwd()
    bp_dir = cwd / "best_practice"
    if bp_dir.is_dir():
        candidates.append(bp_dir)

    # 2. 用户数据目录
    try:
        from seeagent.config import settings
        data_bp = Path(settings.data_dir) / "best_practice"
        if data_bp.is_dir() and data_bp not in candidates:
            candidates.append(data_bp)
    except (ImportError, Exception):
        pass

    return candidates


def init_bp_system(
    profile_store: Any = None,
    search_paths: list[Path] | None = None,
) -> bool:
    """初始化 BP 子系统。返回是否成功加载了配置。

    通常在 Agent._init_handlers() 或 main._init_orchestrator() 时调用。
    """
    global _bp_engine, _bp_handler, _bp_state_manager
    global _bp_config_loader, _bp_context_bridge, _bp_prompt_loader
    global _bp_matcher, _bp_prompt_builder
    global _initialized

    if _initialized:
        return bool(_bp_config_loader and _bp_config_loader.configs)

    from .config import BPConfigLoader
    from .engine import BPEngine, BPStateManager, ContextBridge
    from .handler import BPToolHandler
    from .prompt import BPMatcher, BPPromptBuilder, PromptTemplateLoader

    # 搜索路径
    paths = search_paths or _find_bp_dirs()
    if not paths:
        logger.debug("[BP] No best_practice/ directory found")
        _initialized = True
        return False

    # 初始化组件
    _bp_state_manager = BPStateManager()
    _bp_engine = BPEngine(state_manager=_bp_state_manager)
    _bp_context_bridge = ContextBridge(state_manager=_bp_state_manager)
    _bp_prompt_loader = PromptTemplateLoader()

    # 加载配置 + profiles
    _bp_config_loader = BPConfigLoader(
        search_paths=paths,
        profile_store=profile_store,
    )
    configs = _bp_config_loader.load_all()

    if not configs:
        logger.debug("[BP] No BP configs loaded")
        _initialized = True
        return False

    # 创建 matcher 和 prompt_builder
    _bp_matcher = BPMatcher(
        config_loader=_bp_config_loader,
        state_manager=_bp_state_manager,
        prompt_loader=_bp_prompt_loader,
    )
    _bp_prompt_builder = BPPromptBuilder(
        config_loader=_bp_config_loader,
        state_manager=_bp_state_manager,
        prompt_loader=_bp_prompt_loader,
    )

    # 创建 handler
    _bp_handler = BPToolHandler(
        engine=_bp_engine,
        state_manager=_bp_state_manager,
        context_bridge=_bp_context_bridge,
        config_registry=configs,
    )

    _initialized = True
    logger.info(f"[BP] System initialized with {len(configs)} configs: {list(configs.keys())}")
    return True


def get_bp_engine() -> BPEngine | None:
    if not _initialized:
        init_bp_system()
    return _bp_engine


def get_bp_handler() -> BPToolHandler | None:
    if not _initialized:
        init_bp_system()
    return _bp_handler


def get_bp_state_manager() -> BPStateManager | None:
    if not _initialized:
        init_bp_system()
    return _bp_state_manager


def get_bp_context_bridge() -> ContextBridge | None:
    if not _initialized:
        init_bp_system()
    return _bp_context_bridge


def set_bp_orchestrator(orchestrator) -> None:
    """注入 orchestrator 到 BPEngine，供 server.py 启动时调用。"""
    global _bp_engine
    if _bp_engine:
        _bp_engine.set_orchestrator(orchestrator)


def get_bp_config_loader() -> BPConfigLoader | None:
    if not _initialized:
        init_bp_system()
    return _bp_config_loader


# ── Trigger matching (delegates to BPMatcher) ────────────────────


def match_bp_from_message(user_message: str, session_id: str) -> dict | None:
    """Check user message against CONTEXT triggers of registered BPs."""
    if not _initialized:
        init_bp_system()
    if not _bp_matcher:
        return None
    return _bp_matcher.match_keyword(user_message, session_id)


async def llm_match_bp_from_message(
    user_message: str,
    session_id: str,
    brain,
) -> dict | None:
    """LLM fallback matching."""
    if not _initialized:
        init_bp_system()
    if not _bp_matcher:
        return None
    return await _bp_matcher.match_llm(user_message, session_id, brain)


# ── Prompt injection (delegates to BPPromptBuilder) ───────────────


def get_static_prompt_section() -> str:
    """BP static system prompt section."""
    if not _initialized:
        init_bp_system()
    if not _bp_prompt_builder:
        return ""
    return _bp_prompt_builder.build_static_section()


def get_dynamic_prompt_section(session_id: str) -> str:
    """BP dynamic system prompt section."""
    if not _initialized:
        init_bp_system()
    if not _bp_prompt_builder:
        return ""
    return _bp_prompt_builder.build_dynamic_section(session_id)
