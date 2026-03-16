"""L1 Unit Tests: Prompt guard (task classification and response guard)."""

import pytest

from seeagent.prompt.guard import classify_task, GuardConfig


class TestTaskClassification:
    def test_classify_question(self):
        result = classify_task("Python 的 GIL 是什么？")
        assert result is not None

    def test_classify_action(self):
        result = classify_task("帮我创建一个文件 test.py")
        assert result is not None

    def test_classify_empty(self):
        result = classify_task("")
        assert result is not None

    def test_classify_returns_consistent_type(self):
        r1 = classify_task("你好")
        r2 = classify_task("帮我搜索文件")
        # Both should return the same type (TaskKind enum)
        assert type(r1) == type(r2)


class TestGuardConfig:
    def test_default_config(self):
        config = GuardConfig()
        assert config.max_retries == 3
        assert config.enabled is True

    def test_custom_config(self):
        config = GuardConfig(max_retries=5, enabled=False)
        assert config.max_retries == 5
        assert config.enabled is False
