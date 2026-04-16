"""
${VAR} 环境变量模板展开 & EndpointConfig 双存储 单元测试
"""

import os
from unittest.mock import patch

import pytest

from seeagent.llm.config import MissingEnvError, _expand_env


# ---------------------------------------------------------------------------
# _expand_env 基础行为
# ---------------------------------------------------------------------------


class TestExpandEnv:
    def test_literal_passthrough(self):
        """字面量原样返回"""
        assert _expand_env("https://api.example.com/v1") == "https://api.example.com/v1"
        assert _expand_env("") == ""
        assert _expand_env("glm-5") == "glm-5"

    def test_full_var(self):
        """${VAR} 完整替换"""
        with patch.dict(os.environ, {"MY_KEY": "sk-abc123"}):
            assert _expand_env("${MY_KEY}") == "sk-abc123"

    def test_partial_substitution(self):
        """${HOST}/v1 部分替换"""
        with patch.dict(os.environ, {"LLM_HOST": "https://api.example.com"}):
            assert _expand_env("${LLM_HOST}/v1") == "https://api.example.com/v1"

    def test_default_used_when_missing(self):
        """${VAR:-default}，VAR 未设时取 default"""
        env = {k: v for k, v in os.environ.items() if k != "NONEXISTENT_VAR"}
        with patch.dict(os.environ, env, clear=True):
            assert _expand_env("${NONEXISTENT_VAR:-fallback}") == "fallback"

    def test_default_overridden_when_set(self):
        """${VAR:-default}，VAR 已设时取 env 值"""
        with patch.dict(os.environ, {"MY_MODEL": "gpt-4"}):
            assert _expand_env("${MY_MODEL:-glm-5}") == "gpt-4"

    def test_missing_raises(self):
        """纯 ${VAR} 且 VAR 未设时抛 MissingEnvError"""
        env = {k: v for k, v in os.environ.items() if k != "TOTALLY_MISSING"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(MissingEnvError, match="TOTALLY_MISSING"):
                _expand_env("${TOTALLY_MISSING}")

    def test_multiple_vars(self):
        """一个字符串中多个 ${VAR} 都展开"""
        with patch.dict(os.environ, {"PROTO": "https", "HOST": "api.x.com"}):
            assert _expand_env("${PROTO}://${HOST}/v1") == "https://api.x.com/v1"

    def test_empty_default(self):
        """${VAR:-} 空默认值"""
        env = {k: v for k, v in os.environ.items() if k != "EMPTY_DEFAULT_VAR"}
        with patch.dict(os.environ, env, clear=True):
            assert _expand_env("${EMPTY_DEFAULT_VAR:-}") == ""


# ---------------------------------------------------------------------------
# EndpointConfig from_dict / to_dict / get_api_key
# ---------------------------------------------------------------------------


class TestEndpointConfigEnvExpand:
    @pytest.fixture
    def base_data(self):
        return {
            "name": "test-ep",
            "provider": "custom",
            "api_type": "openai",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-plain",
            "model": "test-model",
            "priority": 1,
        }

    def test_from_dict_preserves_raw(self, base_data):
        """模板原始值写入 *_raw，展开值写入正字段"""
        from seeagent.llm.types import EndpointConfig

        base_data["base_url"] = "${TEST_BASE:-https://fallback.com/v1}"
        base_data["api_key"] = "${TEST_KEY}"
        base_data["model"] = "${TEST_MODEL:-default-model}"

        with patch.dict(os.environ, {"TEST_KEY": "sk-expanded", "TEST_BASE": "https://real.com"}):
            ep = EndpointConfig.from_dict(base_data)

        assert ep.base_url == "https://real.com"
        assert ep.base_url_raw == "${TEST_BASE:-https://fallback.com/v1}"
        assert ep.api_key == "sk-expanded"
        assert ep.api_key_raw == "${TEST_KEY}"
        assert ep.model == "default-model"
        assert ep.model_raw == "${TEST_MODEL:-default-model}"

    def test_to_dict_roundtrip(self, base_data):
        """from_dict → to_dict → from_dict 保持模板不变"""
        from seeagent.llm.types import EndpointConfig

        base_data["base_url"] = "${RT_BASE:-https://rt.com/v1}"
        base_data["api_key"] = "${RT_KEY}"
        base_data["model"] = "${RT_MODEL:-m1}"

        with patch.dict(os.environ, {"RT_KEY": "sk-rt"}):
            ep1 = EndpointConfig.from_dict(base_data)
            d = ep1.to_dict()

        assert d["base_url"] == "${RT_BASE:-https://rt.com/v1}"
        assert d["api_key"] == "${RT_KEY}"
        assert d["model"] == "${RT_MODEL:-m1}"

        # 再次 from_dict 确认行为一致
        with patch.dict(os.environ, {"RT_KEY": "sk-rt"}):
            ep2 = EndpointConfig.from_dict(d)
        assert ep2.base_url == "https://rt.com/v1"
        assert ep2.api_key == "sk-rt"
        assert ep2.model == "m1"

    def test_api_key_env_backward_compat(self, base_data):
        """旧 api_key_env 等效于新 api_key: ${VAR}"""
        from seeagent.llm.types import EndpointConfig

        del base_data["api_key"]
        base_data["api_key_env"] = "COMPAT_KEY"

        with patch.dict(os.environ, {"COMPAT_KEY": "sk-compat"}):
            ep = EndpointConfig.from_dict(base_data)

        assert ep.api_key == "sk-compat"
        assert ep.api_key_raw == "${COMPAT_KEY}"
        assert ep.get_api_key() == "sk-compat"

    def test_get_api_key_simplified(self, base_data):
        """get_api_key() 直接返回展开后的 api_key"""
        from seeagent.llm.types import EndpointConfig

        base_data["api_key"] = "${SIMPLE_KEY}"
        with patch.dict(os.environ, {"SIMPLE_KEY": "sk-simple"}):
            ep = EndpointConfig.from_dict(base_data)

        assert ep.get_api_key() == "sk-simple"

    def test_literal_values_no_raw(self, base_data):
        """字面量字段不写 *_raw"""
        from seeagent.llm.types import EndpointConfig

        ep = EndpointConfig.from_dict(base_data)
        assert ep.base_url_raw == ""
        assert ep.api_key_raw == ""
        assert ep.model_raw == ""

        d = ep.to_dict()
        assert d["base_url"] == "https://api.example.com/v1"
        assert d["api_key"] == "sk-plain"

    def test_endpoint_skipped_on_missing_env(self, base_data):
        """_parse_endpoint_list 捕获 MissingEnvError 后跳过并记 warning"""
        import json
        import tempfile
        from pathlib import Path

        from seeagent.llm.config import load_endpoints_config

        base_data["api_key"] = "${MISSING_FOR_SKIP}"
        config = {"endpoints": [base_data], "settings": {}}

        env = {k: v for k, v in os.environ.items() if k != "MISSING_FOR_SKIP"}
        with patch.dict(os.environ, env, clear=True):
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                json.dump(config, f)
                f.flush()
                try:
                    endpoints, _, _, _ = load_endpoints_config(Path(f.name))
                finally:
                    os.unlink(f.name)

        # 端点被跳过
        assert len(endpoints) == 0

    def test_all_three_endpoint_kinds(self):
        """主端点、compiler、stt 都能应用模板"""
        import json
        import tempfile
        from pathlib import Path

        from seeagent.llm.config import load_endpoints_config

        ep_tpl = {
            "name": "tpl-ep",
            "provider": "custom",
            "api_type": "openai",
            "base_url": "${KINDS_BASE:-https://default.com/v1}",
            "api_key": "${KINDS_KEY}",
            "model": "${KINDS_MODEL:-m1}",
            "priority": 1,
        }

        config = {
            "endpoints": [{**ep_tpl, "name": "main-ep"}],
            "compiler_endpoints": [{**ep_tpl, "name": "compiler-ep"}],
            "stt_endpoints": [{**ep_tpl, "name": "stt-ep"}],
            "settings": {},
        }

        with patch.dict(os.environ, {"KINDS_KEY": "sk-kinds"}):
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                json.dump(config, f)
                f.flush()
                try:
                    endpoints, compiler, stt, _ = load_endpoints_config(Path(f.name))
                finally:
                    os.unlink(f.name)

        assert len(endpoints) == 1
        assert endpoints[0].base_url == "https://default.com/v1"
        assert endpoints[0].api_key == "sk-kinds"
        assert endpoints[0].model == "m1"

        assert len(compiler) == 1
        assert compiler[0].api_key == "sk-kinds"

        assert len(stt) == 1
        assert stt[0].api_key == "sk-kinds"
