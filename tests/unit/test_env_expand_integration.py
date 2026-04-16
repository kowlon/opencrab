"""集成级自测脚本：验证 6 种配置场景"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from seeagent.llm.config import MissingEnvError, _expand_env, load_endpoints_config
from seeagent.llm.types import EndpointConfig


def test_scenario_a_old_api_key_env_compat():
    """旧格式 api_key_env 向后兼容"""
    data = {
        "name": "old-style", "provider": "custom", "api_type": "openai",
        "base_url": "https://api.example.com/v1",
        "api_key_env": "COMPAT_TEST_KEY",
        "model": "test-model",
    }
    with patch.dict(os.environ, {"COMPAT_TEST_KEY": "sk-compat-test"}):
        ep = EndpointConfig.from_dict(data)
    assert ep.api_key == "sk-compat-test"
    assert ep.api_key_raw == "${COMPAT_TEST_KEY}"
    assert ep.api_key_env == "COMPAT_TEST_KEY"  # 保留旧字段

    d = ep.to_dict()
    # 新格式写回 api_key 而非 api_key_env
    assert "api_key" in d
    assert d["api_key"] == "${COMPAT_TEST_KEY}"
    assert "api_key_env" not in d


def test_scenario_b_literal_values():
    """字面量值（无模板）"""
    data = {
        "name": "literal", "provider": "custom", "api_type": "openai",
        "base_url": "https://literal.com/v1",
        "api_key": "sk-plain-key",
        "model": "gpt-4",
    }
    ep = EndpointConfig.from_dict(data)
    assert ep.base_url == "https://literal.com/v1"
    assert ep.base_url_raw == ""
    assert ep.api_key_raw == ""
    assert ep.model_raw == ""

    d = ep.to_dict()
    assert d["base_url"] == "https://literal.com/v1"
    assert d["api_key"] == "sk-plain-key"
    assert d["model"] == "gpt-4"


def test_scenario_c_mixed_template_and_literal():
    """混合模式：部分字段用模板，部分用字面量"""
    with patch.dict(os.environ, {"MIX_KEY": "sk-mix123"}):
        data = {
            "name": "mixed", "provider": "custom", "api_type": "openai",
            "base_url": "https://api.example.com/v1",  # 字面量
            "api_key": "${MIX_KEY}",  # 模板
            "model": "literal-model",  # 字面量
        }
        ep = EndpointConfig.from_dict(data)
        assert ep.base_url == "https://api.example.com/v1"
        assert ep.base_url_raw == ""  # 字面量不存 raw
        assert ep.api_key == "sk-mix123"
        assert ep.api_key_raw == "${MIX_KEY}"
        assert ep.model == "literal-model"
        assert ep.model_raw == ""


def test_scenario_d_default_value_fallback():
    """${VAR:-default} 默认值生效"""
    env = {k: v for k, v in os.environ.items() if k != "NOEXIST_D"}
    with patch.dict(os.environ, env, clear=True):
        result = _expand_env("${NOEXIST_D:-my-fallback}")
        assert result == "my-fallback"


def test_scenario_d_default_overridden():
    """${VAR:-default} 有值时覆盖默认"""
    with patch.dict(os.environ, {"EXIST_D": "real-value"}):
        result = _expand_env("${EXIST_D:-my-fallback}")
        assert result == "real-value"


def test_scenario_e_missing_raises():
    """缺失变量且无默认值 => MissingEnvError"""
    env = {k: v for k, v in os.environ.items() if k != "GONE_E"}
    with patch.dict(os.environ, env, clear=True):
        try:
            _expand_env("${GONE_E}")
            assert False, "should have raised MissingEnvError"
        except MissingEnvError as e:
            assert "GONE_E" in str(e)


def test_scenario_f_endpoint_skipped_on_missing():
    """缺失变量 => endpoint 被跳过"""
    config = {
        "endpoints": [{
            "name": "will-skip", "provider": "custom", "api_type": "openai",
            "base_url": "https://api.com/v1",
            "api_key": "${SKIP_KEY_F}",
            "model": "test", "priority": 1,
        }],
        "settings": {},
    }
    env = {k: v for k, v in os.environ.items() if k != "SKIP_KEY_F"}
    with patch.dict(os.environ, env, clear=True):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config, f)
            f.flush()
            try:
                eps, _, _, _ = load_endpoints_config(Path(f.name))
            finally:
                os.unlink(f.name)
    assert len(eps) == 0


def test_scenario_g_real_config_loads():
    """加载实际 llm_endpoints.json + .env"""
    eps, comp, stt, _ = load_endpoints_config()
    assert len(eps) >= 1
    ep = eps[0]

    # 展开值非空
    assert ep.base_url.startswith("http")
    assert ep.api_key
    assert ep.model

    # raw 保留模板
    assert "${" in ep.base_url_raw
    assert "${" in ep.api_key_raw
    assert "${" in ep.model_raw

    # capabilities 未受影响
    assert isinstance(ep.capabilities, list)
    assert ep.has_capability("text")

    # compiler 也加载成功
    assert len(comp) >= 1
    assert comp[0].api_key


def test_scenario_h_save_preserves_templates():
    """save -> reload 模板不丢失"""
    from seeagent.llm.config import save_endpoints_config

    eps, comp, _, settings = load_endpoints_config()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        tmp_path = Path(f.name)
    try:
        save_endpoints_config(eps, settings, tmp_path, compiler_endpoints=comp)
        with open(tmp_path, encoding="utf-8") as f:
            saved = json.load(f)

        # JSON 里是模板，不是展开值
        ep_data = saved["endpoints"][0]
        assert "${" in ep_data["base_url"]
        assert "${" in ep_data["api_key"]
        assert "${" in ep_data["model"]

        # 重新加载依然正确
        eps2, comp2, _, _ = load_endpoints_config(tmp_path)
        assert eps2[0].base_url == eps[0].base_url
        assert eps2[0].api_key == eps[0].api_key
        assert eps2[0].model == eps[0].model
    finally:
        tmp_path.unlink(missing_ok=True)
