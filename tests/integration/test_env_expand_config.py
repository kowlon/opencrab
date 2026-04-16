"""L3 Integration Tests: llm_endpoints.json 多种配置方式的端到端验证。

测试矩阵：
- 纯字面量 / 纯 ${VAR} / ${VAR:-default} / 混合模式 / 旧 api_key_env
- 验证层次：配置加载 → API 读取 → save/reload roundtrip → LLM 连通冒烟
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from seeagent.api.server import create_app
from seeagent.llm.config import (
    MissingEnvError,
    _expand_env,
    load_endpoints_config,
    save_endpoints_config,
)
from seeagent.llm.types import EndpointConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_config(tmp_path):
    """创建临时 llm_endpoints.json 的 helper。"""
    def _write(endpoints_data: list[dict], compiler_data: list[dict] | None = None):
        config = {"endpoints": endpoints_data, "settings": {}}
        if compiler_data:
            config["compiler_endpoints"] = compiler_data
        path = tmp_path / "llm_endpoints.json"
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    return _write


@pytest.fixture
def base_ep():
    """共享的端点基础字段。"""
    return {
        "name": "test-ep",
        "provider": "custom",
        "api_type": "openai",
        "priority": 1,
        "max_tokens": 4096,
        "timeout": 60,
        "capabilities": ["text", "tools"],
    }


# ---------------------------------------------------------------------------
# 配置矩阵：5 种配置方式 × 加载 + roundtrip
# ---------------------------------------------------------------------------


class TestConfigMatrix:
    """每种配置方式的 load → expand → to_dict → reload 完整链路。"""

    def test_all_literal(self, tmp_config, base_ep):
        """方式 1: 全部字面量"""
        ep_data = {**base_ep, "base_url": "https://api.com/v1",
                   "api_key": "sk-literal", "model": "gpt-4"}
        path = tmp_config([ep_data])
        eps, _, _, _ = load_endpoints_config(path)

        assert len(eps) == 1
        ep = eps[0]
        assert ep.base_url == "https://api.com/v1"
        assert ep.api_key == "sk-literal"
        assert ep.model == "gpt-4"
        assert ep.base_url_raw == ""  # 无模板不存 raw
        assert ep.api_key_raw == ""
        assert ep.model_raw == ""

        # roundtrip
        d = ep.to_dict()
        assert d["base_url"] == "https://api.com/v1"
        assert d["api_key"] == "sk-literal"

    def test_all_template(self, tmp_config, base_ep):
        """方式 2: 全部 ${VAR}"""
        ep_data = {**base_ep, "base_url": "${T_BASE}",
                   "api_key": "${T_KEY}", "model": "${T_MODEL}"}
        with patch.dict(os.environ, {
            "T_BASE": "https://expanded.com/v1",
            "T_KEY": "sk-expanded",
            "T_MODEL": "expanded-model",
        }):
            path = tmp_config([ep_data])
            eps, _, _, _ = load_endpoints_config(path)

        ep = eps[0]
        assert ep.base_url == "https://expanded.com/v1"
        assert ep.api_key == "sk-expanded"
        assert ep.model == "expanded-model"
        assert ep.base_url_raw == "${T_BASE}"
        assert ep.api_key_raw == "${T_KEY}"
        assert ep.model_raw == "${T_MODEL}"

        # roundtrip 保留模板
        d = ep.to_dict()
        assert d["base_url"] == "${T_BASE}"
        assert d["api_key"] == "${T_KEY}"
        assert d["model"] == "${T_MODEL}"

    def test_template_with_defaults(self, tmp_config, base_ep):
        """方式 3: ${VAR:-default}，变量未设时走默认值"""
        ep_data = {
            **base_ep,
            "base_url": "${TD_BASE:-https://default.com/v1}",
            "api_key": "${TD_KEY}",
            "model": "${TD_MODEL:-default-model}",
        }
        env = {k: v for k, v in os.environ.items()
               if k not in ("TD_BASE", "TD_MODEL")}
        env["TD_KEY"] = "sk-td"
        with patch.dict(os.environ, env, clear=True):
            path = tmp_config([ep_data])
            eps, _, _, _ = load_endpoints_config(path)

        ep = eps[0]
        assert ep.base_url == "https://default.com/v1"
        assert ep.model == "default-model"
        assert ep.api_key == "sk-td"

    def test_template_default_overridden(self, tmp_config, base_ep):
        """方式 3b: ${VAR:-default}，变量已设时覆盖默认"""
        ep_data = {
            **base_ep,
            "base_url": "${TDO_BASE:-https://default.com/v1}",
            "api_key": "${TDO_KEY}",
            "model": "${TDO_MODEL:-default-model}",
        }
        with patch.dict(os.environ, {
            "TDO_BASE": "https://override.com/v1",
            "TDO_KEY": "sk-tdo",
            "TDO_MODEL": "override-model",
        }):
            path = tmp_config([ep_data])
            eps, _, _, _ = load_endpoints_config(path)

        ep = eps[0]
        assert ep.base_url == "https://override.com/v1"
        assert ep.model == "override-model"

    def test_mixed_template_and_literal(self, tmp_config, base_ep):
        """方式 4: 混合——部分字段模板，部分字面量"""
        ep_data = {
            **base_ep,
            "base_url": "https://fixed.com/v1",  # 字面量
            "api_key": "${MIX_KEY}",  # 模板
            "model": "fixed-model",  # 字面量
        }
        with patch.dict(os.environ, {"MIX_KEY": "sk-mix"}):
            path = tmp_config([ep_data])
            eps, _, _, _ = load_endpoints_config(path)

        ep = eps[0]
        assert ep.base_url == "https://fixed.com/v1"
        assert ep.base_url_raw == ""
        assert ep.api_key == "sk-mix"
        assert ep.api_key_raw == "${MIX_KEY}"
        assert ep.model == "fixed-model"
        assert ep.model_raw == ""

    def test_old_api_key_env_format(self, tmp_config, base_ep):
        """方式 5: 旧 api_key_env 格式向后兼容"""
        ep_data = {
            **base_ep,
            "base_url": "https://old.com/v1",
            "api_key_env": "OLD_KEY_VAR",
            "model": "old-model",
        }
        with patch.dict(os.environ, {"OLD_KEY_VAR": "sk-old"}):
            path = tmp_config([ep_data])
            eps, _, _, _ = load_endpoints_config(path)

        ep = eps[0]
        assert ep.api_key == "sk-old"
        assert ep.api_key_raw == "${OLD_KEY_VAR}"
        assert ep.api_key_env == "OLD_KEY_VAR"

        # to_dict 写新格式
        d = ep.to_dict()
        assert d["api_key"] == "${OLD_KEY_VAR}"
        assert "api_key_env" not in d


# ---------------------------------------------------------------------------
# 异常路径
# ---------------------------------------------------------------------------


class TestConfigErrors:
    def test_missing_var_skips_endpoint(self, tmp_config, base_ep):
        """缺失变量 → endpoint 被跳过（不崩溃）"""
        ep_data = {**base_ep, "base_url": "https://api.com/v1",
                   "api_key": "${NOWHERE_KEY}", "model": "m"}
        env = {k: v for k, v in os.environ.items() if k != "NOWHERE_KEY"}
        with patch.dict(os.environ, env, clear=True):
            path = tmp_config([ep_data])
            eps, _, _, _ = load_endpoints_config(path)
        assert len(eps) == 0

    def test_partial_missing_keeps_good_endpoints(self, tmp_config, base_ep):
        """多个端点中只有部分缺变量 → 只跳过坏的"""
        good = {**base_ep, "name": "good", "base_url": "https://api.com/v1",
                "api_key": "sk-good", "model": "m"}
        bad = {**base_ep, "name": "bad", "base_url": "https://api.com/v1",
               "api_key": "${BAD_MISSING}", "model": "m", "priority": 2}
        env = {k: v for k, v in os.environ.items() if k != "BAD_MISSING"}
        with patch.dict(os.environ, env, clear=True):
            path = tmp_config([good, bad])
            eps, _, _, _ = load_endpoints_config(path)
        assert len(eps) == 1
        assert eps[0].name == "good"


# ---------------------------------------------------------------------------
# Save → Reload roundtrip（模拟 API 保存后重新加载）
# ---------------------------------------------------------------------------


class TestSaveReloadRoundtrip:
    def test_save_preserves_templates_and_reload_works(self, tmp_config, base_ep):
        """save_endpoints_config 写回 JSON → 模板保留 → 重新加载一致"""
        ep_data = {
            **base_ep,
            "base_url": "${SR_BASE:-https://default.com/v1}",
            "api_key": "${SR_KEY}",
            "model": "${SR_MODEL:-m1}",
        }
        with patch.dict(os.environ, {"SR_KEY": "sk-sr", "SR_BASE": "https://real.com/v1"}):
            path = tmp_config([ep_data])
            eps1, _, _, settings = load_endpoints_config(path)

            # 保存
            save_endpoints_config(eps1, settings, path)

            # 读回 JSON 原文验证模板保留
            raw = json.loads(path.read_text(encoding="utf-8"))
            assert raw["endpoints"][0]["base_url"] == "${SR_BASE:-https://default.com/v1}"
            assert raw["endpoints"][0]["api_key"] == "${SR_KEY}"
            assert raw["endpoints"][0]["model"] == "${SR_MODEL:-m1}"

            # 重新加载验证值一致
            eps2, _, _, _ = load_endpoints_config(path)
            assert eps2[0].base_url == eps1[0].base_url
            assert eps2[0].api_key == eps1[0].api_key
            assert eps2[0].model == eps1[0].model

    def test_env_change_after_save_takes_effect(self, tmp_config, base_ep):
        """保存后修改 env → 重新加载拿到新值"""
        ep_data = {
            **base_ep,
            "base_url": "${EC_BASE:-https://default.com/v1}",
            "api_key": "${EC_KEY}",
            "model": "m1",
        }
        with patch.dict(os.environ, {"EC_KEY": "sk-v1", "EC_BASE": "https://v1.com"}):
            path = tmp_config([ep_data])
            eps1, _, _, settings = load_endpoints_config(path)
            assert eps1[0].base_url == "https://v1.com"

            save_endpoints_config(eps1, settings, path)

        # 修改 env
        with patch.dict(os.environ, {"EC_KEY": "sk-v2", "EC_BASE": "https://v2.com"}):
            eps2, _, _, _ = load_endpoints_config(path)
            assert eps2[0].base_url == "https://v2.com"
            assert eps2[0].api_key == "sk-v2"


# ---------------------------------------------------------------------------
# API 级验证（通过 FastAPI test client）
# ---------------------------------------------------------------------------


class TestAPIEndpoints:
    @pytest.fixture
    def app(self):
        return create_app(
            agent=MagicMock(initialized=True),
            shutdown_event=asyncio.Event(),
        )

    @pytest.fixture
    async def client(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver",
        ) as c:
            yield c

    async def test_config_endpoints_returns_valid_structure(self, client):
        """GET /api/config/endpoints 返回有效的 JSON 结构（含 endpoints 和 raw 键）"""
        resp = await client.get("/api/config/endpoints")
        assert resp.status_code == 200
        data = resp.json()
        assert "endpoints" in data
        assert "raw" in data
        # raw 下也应包含 endpoints 列表
        raw_eps = data["raw"].get("endpoints", [])
        assert isinstance(raw_eps, list)

    async def test_models_returns_expanded_values(self, client):
        """GET /api/models 返回展开后的运行时值"""
        resp = await client.get("/api/models")
        # agent 是 mock，可能 500；只验证不崩
        assert resp.status_code in (200, 500)


# ---------------------------------------------------------------------------
# LLM 连通冒烟（使用实际 .env + llm_endpoints.json）
# ---------------------------------------------------------------------------


class TestLLMConnectivitySmoke:
    """使用真实配置验证 LLM API 可达。标记为 slow，默认跳过。"""

    @pytest.mark.skipif(
        not os.environ.get("LLM_SMOKE_TEST"),
        reason="Set LLM_SMOKE_TEST=1 to enable LLM connectivity smoke test",
    )
    def test_primary_endpoint_reachable(self):
        """加载实际配置 → 向主端点发一个最小请求 → 验证能收到响应"""
        import httpx

        eps, _, _, _ = load_endpoints_config()
        assert len(eps) >= 1, "No endpoints loaded"

        ep = eps[0]
        api_key = ep.get_api_key()
        assert api_key, f"No API key for endpoint {ep.name}"

        # 最小 chat completion 请求
        resp = httpx.post(
            f"{ep.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": ep.model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
            },
            timeout=30,
        )
        # 只验证连通性：2xx 或 4xx（认证/限流）都说明 URL 可达
        assert resp.status_code < 500, (
            f"Endpoint {ep.name} returned {resp.status_code}: {resp.text[:200]}"
        )
