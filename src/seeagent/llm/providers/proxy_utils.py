"""
代理和网络配置工具

从环境变量或配置中获取代理设置，以及 IPv4 强制配置。
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

# 缓存：避免重复打印日志
_ipv4_logged = False
_proxy_logged = False
_transport_cache: httpx.AsyncHTTPTransport | None = None


def _is_truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y", "on")


def is_proxy_disabled() -> bool:
    """是否禁用代理

    用于排查“明明没配代理但所有端点都超时”的情况：
    某些 Windows 环境会全局注入 HTTP(S)_PROXY/ALL_PROXY，导致请求被强制走代理。

    支持的开关（任一为真即禁用）：
    - LLM_DISABLE_PROXY=1
    - SEEAGENT_DISABLE_PROXY=1
    - DISABLE_PROXY=1
    """
    return (
        _is_truthy_env("LLM_DISABLE_PROXY")
        or _is_truthy_env("SEEAGENT_DISABLE_PROXY")
        or _is_truthy_env("DISABLE_PROXY")
    )


def _redact_proxy_url(proxy: str) -> str:
    """脱敏 proxy URL（避免日志泄露账号密码）"""
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(proxy)
        if parts.username or parts.password:
            # 组装 netloc：***:***@host:port
            host = parts.hostname or ""
            port = f":{parts.port}" if parts.port else ""
            netloc = f"***:***@{host}{port}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        return proxy
    except Exception:
        return proxy


def build_httpx_timeout(timeout_value: object, default: float = 60.0) -> httpx.Timeout:
    """从配置构造 httpx.Timeout

    兼容：
    - int/float：视作“读超时”（整体上限），并给 connect/write/pool 合理的更小默认值
    - dict：支持字段 connect/read/write/pool/total（秒）
    """

    def _to_float_or_none(v: object) -> float | None:
        if v is None:
            return None
        if isinstance(v, str) and v.strip().lower() in ("none", "null", "off", "disable", "disabled"):
            return None
        try:
            return float(v)  # type: ignore[arg-type]
        except Exception:
            return None

    # dict 形式：{"connect":10,"read":300,"write":30,"pool":30,"total":300}
    if isinstance(timeout_value, dict):
        total = _to_float_or_none(timeout_value.get("total"))  # type: ignore[union-attr]
        connect = _to_float_or_none(timeout_value.get("connect"))  # type: ignore[union-attr]
        read = _to_float_or_none(timeout_value.get("read"))  # type: ignore[union-attr]
        write = _to_float_or_none(timeout_value.get("write"))  # type: ignore[union-attr]
        pool = _to_float_or_none(timeout_value.get("pool"))  # type: ignore[union-attr]

        kwargs: dict = {}
        if total is not None:
            kwargs["timeout"] = total
        if connect is not None:
            kwargs["connect"] = connect
        if read is not None:
            kwargs["read"] = read
        if write is not None:
            kwargs["write"] = write
        if pool is not None:
            kwargs["pool"] = pool

        # 若 dict 无有效字段，回退到默认
        if not kwargs:
            return httpx.Timeout(default)
        return httpx.Timeout(**kwargs)

    # 数值形式：默认将 read 设为 t，connect/write/pool 设为较小值，避免“连接阶段卡满 t”
    try:
        t = float(timeout_value)  # type: ignore[arg-type]
    except Exception:
        t = float(default)

    t = max(1.0, t)
    return httpx.Timeout(
        connect=min(10.0, t),
        read=t,
        write=min(30.0, t),
        pool=min(30.0, t),
    )


def get_proxy_config() -> str | None:
    """获取代理配置

    优先级（从高到低）:
    1. ALL_PROXY 环境变量
    2. HTTPS_PROXY 环境变量
    3. HTTP_PROXY 环境变量
    4. 配置文件中的 all_proxy
    5. 配置文件中的 https_proxy
    6. 配置文件中的 http_proxy

    Returns:
        代理地址或 None
    """
    global _proxy_logged

    if is_proxy_disabled():
        if not _proxy_logged:
            logger.info("[Proxy] Proxy disabled (LLM_DISABLE_PROXY=1)")
            _proxy_logged = True
        return None

    # 先检查环境变量
    for env_var in [
        "ALL_PROXY",
        "all_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ]:
        proxy = os.environ.get(env_var)
        if proxy:
            if not _proxy_logged:
                logger.info(
                    f"[Proxy] LLM proxy enabled from env {env_var}: {_redact_proxy_url(proxy)}"
                )
                _proxy_logged = True
            return proxy

    # 再检查配置文件
    try:
        from ...config import settings

        if settings.all_proxy:
            if not _proxy_logged:
                logger.info(
                    f"[Proxy] LLM proxy enabled from config all_proxy: "
                    f"{_redact_proxy_url(settings.all_proxy)}"
                )
                _proxy_logged = True
            return settings.all_proxy
        if settings.https_proxy:
            if not _proxy_logged:
                logger.info(
                    f"[Proxy] LLM proxy enabled from config https_proxy: "
                    f"{_redact_proxy_url(settings.https_proxy)}"
                )
                _proxy_logged = True
            return settings.https_proxy
        if settings.http_proxy:
            if not _proxy_logged:
                logger.info(
                    f"[Proxy] LLM proxy enabled from config http_proxy: "
                    f"{_redact_proxy_url(settings.http_proxy)}"
                )
                _proxy_logged = True
            return settings.http_proxy
    except Exception as e:
        logger.debug(f"[Proxy] Failed to load config: {e}")

    return None


def is_ipv4_only() -> bool:
    """检查是否强制使用 IPv4

    通过环境变量 FORCE_IPV4=true 或配置文件 force_ipv4=true 启用
    """
    # 检查环境变量
    if os.environ.get("FORCE_IPV4", "").lower() in ("true", "1", "yes"):
        return True

    # 检查配置文件
    try:
        from ...config import settings

        return getattr(settings, "force_ipv4", False)
    except Exception:
        pass

    return False


def get_httpx_transport() -> httpx.AsyncHTTPTransport | None:
    """获取 httpx transport（支持 IPv4-only 模式）

    当 FORCE_IPV4=true 时，创建强制使用 IPv4 的 transport。
    这对于某些 VPN（如 LetsTAP）不支持 IPv6 的情况很有用。

    Returns:
        httpx.AsyncHTTPTransport 或 None
    """
    global _ipv4_logged

    if is_ipv4_only():
        # 只在第一次打印日志
        if not _ipv4_logged:
            logger.info("[Network] IPv4-only mode enabled (FORCE_IPV4=true)")
            _ipv4_logged = True
        # local_address="0.0.0.0" 强制使用 IPv4
        return httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    return None


def get_httpx_proxy_mounts() -> dict | None:
    """获取 httpx 代理配置

    Returns:
        httpx 代理 mounts 字典或 None
    """
    proxy = get_proxy_config()
    if proxy:
        return {
            "http://": proxy,
            "https://": proxy,
        }
    return None
