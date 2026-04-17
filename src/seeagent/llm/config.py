"""
LLM 端点配置加载

支持从 JSON 文件加载端点配置。
"""

import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from .types import ConfigurationError, EndpointConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 环境变量模板展开：${VAR} / ${VAR:-default}
# ---------------------------------------------------------------------------

class MissingEnvError(ConfigurationError):
    """环境变量缺失且无默认值时抛出"""


_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(raw: str) -> str:
    """展开字符串中的 ${VAR} / ${VAR:-default} 占位符。

    - 不含 ``${`` 的字符串原样返回。
    - 环境变量存在且非空时使用其值。
    - 环境变量缺失或为空时：有 ``:-default`` 则用默认值，否则抛 MissingEnvError。
    """
    if "${" not in raw:
        return raw

    missing: list[str] = []

    def _replacer(m: re.Match) -> str:
        var_name, default = m.group(1), m.group(2)
        value = os.environ.get(var_name)
        if value:
            return value
        if default is not None:
            return default
        missing.append(var_name)
        return m.group(0)  # 占位，下面会抛异常

    result = _ENV_PATTERN.sub(_replacer, raw)

    if missing:
        raise MissingEnvError(
            f"Environment variable(s) not set: {', '.join(missing)} "
            f"(in template: {raw!r})"
        )
    return result


def _safe_load_dotenv(env_path: Path) -> None:
    """Load a .env file with encoding fallback.

    Tries UTF-8 first (python-dotenv default). On ``UnicodeDecodeError``
    (e.g. GBK-encoded Chinese comments on Windows) retries with the
    platform default encoding so the backend can still start.
    """
    try:
        load_dotenv(env_path)
    except UnicodeDecodeError:
        logger.warning(
            "Failed to read %s as UTF-8; retrying with system encoding. "
            "Consider converting the file to UTF-8.",
            env_path,
        )
        try:
            load_dotenv(env_path, encoding=None)
        except Exception:
            logger.error("Could not load %s with any encoding, skipping.", env_path)


def _load_env():
    """Discover and load the nearest .env file.

    Search order: CWD (up to 3 levels) → package directory (up to 5 levels).
    """
    cwd = Path.cwd()
    current = cwd
    for _ in range(3):
        env_path = current / ".env"
        if env_path.exists():
            _safe_load_dotenv(env_path)
            return
        parent = current.parent
        if parent == current:
            break
        current = parent

    current = Path(__file__).parent
    for _ in range(5):
        env_path = current / ".env"
        if env_path.exists():
            _safe_load_dotenv(env_path)
            return
        current = current.parent


_load_env()


def get_default_config_path() -> Path:
    """获取默认配置文件路径

    搜索顺序：
    1. 环境变量 LLM_ENDPOINTS_CONFIG
    2. CWD 及其父级（最多 3 层）下的 data/llm_endpoints.json
    3. 包文件所在目录向上（最多 5 层）下的 data/llm_endpoints.json
    4. 兜底返回 CWD/data/llm_endpoints.json（即使不存在）
    """
    # 1) 环境变量优先
    env_path = os.environ.get("LLM_ENDPOINTS_CONFIG")
    if env_path:
        return Path(env_path)

    # 2) 从 CWD 向上搜索（pip install 场景：seeagent init 在 CWD 创建 data/）
    cwd = Path.cwd()
    current = cwd
    for _ in range(3):
        config_path = current / "data" / "llm_endpoints.json"
        if config_path.exists():
            return config_path
        parent = current.parent
        if parent == current:
            break
        current = parent

    # 3) 从包文件向上搜索（开发 / editable install 场景）
    current = Path(__file__).parent
    for _ in range(5):
        config_path = current / "data" / "llm_endpoints.json"
        if config_path.exists():
            return config_path
        current = current.parent

    # 4) 兜底：返回 CWD 下的默认位置（让调用方统一处理不存在的情况）
    return cwd / "data" / "llm_endpoints.json"


def _migrate_capabilities(config_path: Path, data: dict) -> None:
    """自动补全旧配置中缺失的 capabilities。

    旧版 setup wizard 生成的配置可能只包含 ["text", "tools"]，缺少 thinking/vision 等能力。
    此函数在启动时对比 infer_capabilities() 的推断结果，补全缺失的能力并写回 JSON。

    安全策略：只新增能力，不删除用户手动添加的能力。
    """
    try:
        from .capabilities import get_provider_slug_from_base_url, infer_capabilities
    except ImportError:
        return

    updated_count = 0
    endpoint_keys = ["endpoints", "compiler_endpoints", "stt_endpoints"]

    for key in endpoint_keys:
        for ep in data.get(key, []):
            model = ep.get("model", "")
            if not model:
                continue

            base_url = ep.get("base_url", "")
            provider_slug = get_provider_slug_from_base_url(base_url) if base_url else None
            if not provider_slug:
                provider_slug = ep.get("provider") or None

            inferred = infer_capabilities(model, provider_slug=provider_slug)
            inferred_caps = {k for k, v in inferred.items() if v}

            current_caps = set(ep.get("capabilities") or [])

            # 只在推断结果有新增能力时更新（不删除用户手动添加的能力）
            new_caps = inferred_caps - current_caps
            if new_caps:
                merged = sorted(current_caps | inferred_caps)
                ep["capabilities"] = merged
                updated_count += 1
                logger.info(
                    f"[Config Migration] Endpoint '{ep.get('name', '?')}' "
                    f"(model={model}): capabilities updated "
                    f"{sorted(current_caps)} → {merged} (added: {sorted(new_caps)})"
                )

            # 一致性约束：thinking_only ⊂ thinking
            # thinking_only 模型无法关闭思考，端点筛选层按 "thinking" 过滤，
            # 若缺少 "thinking" 则端点会被跳过，provider 层的兜底来不及生效
            final_caps = set(ep.get("capabilities") or [])
            if "thinking_only" in final_caps and "thinking" not in final_caps:
                final_caps.add("thinking")
                ep["capabilities"] = sorted(final_caps)
                if not new_caps:  # 前面没有触发更新计数
                    updated_count += 1
                logger.info(
                    f"[Config Migration] Endpoint '{ep.get('name', '?')}' "
                    f"(model={model}): added 'thinking' (required by thinking_only)"
                )

    if updated_count > 0:
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            logger.info(
                f"[Config Migration] Updated capabilities for {updated_count} endpoint(s) "
                f"in {config_path}"
            )
        except Exception as e:
            logger.warning(
                f"[Config Migration] Failed to write back config: {e}. "
                f"Capabilities have been updated in memory (effective for this run), "
                f"but will need to be re-migrated on next startup."
            )


def load_endpoints_config(
    config_path: Path | None = None,
) -> tuple[list[EndpointConfig], list[EndpointConfig], list[EndpointConfig], dict]:
    """
    加载端点配置

    Args:
        config_path: 配置文件路径，默认使用 get_default_config_path()

    Returns:
        (endpoints, compiler_endpoints, stt_endpoints, settings):
        主端点列表、Prompt Compiler 专用端点列表、语音识别端点列表、全局设置

    Raises:
        ConfigurationError: 配置错误
    """
    if config_path is None:
        config_path = get_default_config_path()

    config_path = Path(config_path)

    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}, using empty config")
        return [], [], [], {}

    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigurationError(f"Invalid JSON in config file: {e}")
    except Exception as e:
        raise ConfigurationError(f"Failed to read config file: {e}")

    # 自动迁移：补全旧配置中缺失的 capabilities
    _migrate_capabilities(config_path, data)

    def _parse_endpoint_list(key: str) -> list[EndpointConfig]:
        result = []
        for ep_data in data.get(key, []):
            try:
                endpoint = EndpointConfig.from_dict(ep_data)
                if not endpoint.enabled:
                    logger.info(f"Skipping disabled endpoint '{endpoint.name}'")
                    continue
                if not endpoint.get_api_key():
                    tpl = endpoint.api_key_raw or endpoint.api_key_env or "(未配置)"
                    logger.warning(
                        f"API key not found for endpoint '{endpoint.name}': "
                        f"source={tpl}"
                    )
                result.append(endpoint)
            except MissingEnvError as e:
                logger.warning(f"Skipping endpoint ({key}): {e}")
                continue
            except Exception as e:
                logger.error(f"Failed to parse endpoint config ({key}): {e}")
                continue
        result.sort(key=lambda x: x.priority)
        return result

    # 解析主端点
    endpoints = _parse_endpoint_list("endpoints")
    if not endpoints:
        logger.warning("No valid endpoints found in config")

    # 解析 Prompt Compiler 专用端点
    compiler_endpoints = _parse_endpoint_list("compiler_endpoints")
    if compiler_endpoints:
        logger.info(f"Loaded {len(compiler_endpoints)} compiler endpoints")

    # 解析语音识别（STT）端点
    stt_endpoints = _parse_endpoint_list("stt_endpoints")
    if stt_endpoints:
        logger.info(f"Loaded {len(stt_endpoints)} STT endpoints")
    else:
        logger.debug("No STT endpoints configured")

    # 解析全局设置
    settings = data.get("settings", {})

    logger.info(f"Loaded {len(endpoints)} endpoints from {config_path}")

    return endpoints, compiler_endpoints, stt_endpoints, settings


def save_endpoints_config(
    endpoints: list[EndpointConfig],
    settings: dict | None = None,
    config_path: Path | None = None,
    compiler_endpoints: list[EndpointConfig] | None = None,
    stt_endpoints: list[EndpointConfig] | None = None,
):
    """
    保存端点配置

    Args:
        endpoints: 主端点配置列表
        settings: 全局设置
        config_path: 配置文件路径
        compiler_endpoints: Prompt Compiler 专用端点列表（可选）
        stt_endpoints: 语音识别端点列表（可选）
    """
    if config_path is None:
        config_path = get_default_config_path()

    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {
        "endpoints": [ep.to_dict() for ep in endpoints],
    }

    if compiler_endpoints:
        data["compiler_endpoints"] = [ep.to_dict() for ep in compiler_endpoints]

    if stt_endpoints:
        data["stt_endpoints"] = [ep.to_dict() for ep in stt_endpoints]

    data["settings"] = settings or {
        "retry_count": 2,
        "retry_delay_seconds": 2,
        "health_check_interval": 60,
        "fallback_on_error": True,
    }

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"Saved {len(endpoints)} endpoints to {config_path}")


def create_default_config(config_path: Path | None = None):
    """
    创建默认配置文件

    Args:
        config_path: 配置文件路径
    """
    default_endpoints = [
        EndpointConfig(
            name="claude-primary",
            provider="anthropic",
            api_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key="${ANTHROPIC_API_KEY}",
            model="claude-sonnet-4-20250514",
            priority=1,
            max_tokens=0,
            timeout=180,
            capabilities=["text", "vision", "tools"],
        ),
        EndpointConfig(
            name="qwen-backup",
            provider="dashscope",
            api_type="openai",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="${DASHSCOPE_API_KEY}",
            model="qwen-plus",
            priority=2,
            max_tokens=0,
            timeout=180,
            capabilities=["text", "tools", "thinking"],
            extra_params={"enable_thinking": True},
        ),
    ]

    save_endpoints_config(default_endpoints, config_path=config_path)


def validate_config(config_path: Path | None = None) -> list[str]:
    """
    验证配置文件

    Returns:
        错误列表（空列表表示没有错误）
    """
    errors = []

    try:
        endpoints, compiler_endpoints, stt_endpoints, settings = load_endpoints_config(config_path)
    except ConfigurationError as e:
        return [str(e)]

    if not endpoints:
        errors.append("No endpoints configured")

    def _validate_endpoints(eps: list[EndpointConfig], label: str = "") -> None:
        prefix = f"[{label}] " if label else ""
        for ep in eps:
            # 检查 API Key
            if not ep.get_api_key():
                src = ep.api_key_raw or ep.api_key_env or "(未配置)"
                errors.append(
                    f"{prefix}Endpoint '{ep.name}': API key not available (source: {src})"
                )

            # 检查 API 类型
            if ep.api_type not in ("anthropic", "openai"):
                errors.append(f"{prefix}Endpoint '{ep.name}': Invalid api_type '{ep.api_type}'")

            # 检查 base_url
            if not ep.base_url.startswith(("http://", "https://")):
                errors.append(f"{prefix}Endpoint '{ep.name}': Invalid base_url '{ep.base_url}'")

    _validate_endpoints(endpoints)
    _validate_endpoints(compiler_endpoints, label="compiler")
    _validate_endpoints(stt_endpoints, label="stt")

    return errors
