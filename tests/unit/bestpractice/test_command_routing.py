"""Tests for BP command routing in seecrab.py."""
import pytest
from seeagent.api.routes.seecrab import _match_bp_command, _normalize_bp_command


class TestNormalize:
    def test_strips_punctuation(self):
        assert _normalize_bp_command("下一步！") == "下一步"
        assert _normalize_bp_command("  继续。 ") == "继续"

    def test_lowercase(self):
        assert _normalize_bp_command("OK") == "ok"


class TestMatchBPCommand:
    def test_start_commands(self):
        assert _match_bp_command("进入最佳实践") == "start"
        assert _match_bp_command("最佳实践模式") == "start"
        assert _match_bp_command("开始最佳实践") == "start"

    def test_strict_next_commands(self):
        assert _match_bp_command("进入下一步") == "next"
        assert _match_bp_command("下一步") == "next"
        assert _match_bp_command("继续执行") == "next"
        assert _match_bp_command("继续") == "next"

    def test_new_strict_next_commands(self):
        assert _match_bp_command("好的继续") == "next"
        assert _match_bp_command("开始下一步") == "next"
        assert _match_bp_command("执行下一步") == "next"

    def test_loose_next_commands(self):
        assert _match_bp_command("好") == "next_loose"
        assert _match_bp_command("没问题") == "next_loose"
        assert _match_bp_command("ok") == "next_loose"
        assert _match_bp_command("确认") == "next_loose"
        assert _match_bp_command("好的下一步") == "next_loose"

    def test_cancel_commands(self):
        assert _match_bp_command("取消最佳实践") == "cancel"
        assert _match_bp_command("终止最佳实践") == "cancel"
        assert _match_bp_command("取消任务") == "cancel"
        assert _match_bp_command("终止任务") == "cancel"
        assert _match_bp_command("停止最佳实践") == "cancel"
        assert _match_bp_command("退出最佳实践") == "cancel"

    def test_no_match(self):
        assert _match_bp_command("你好") is None
        assert _match_bp_command("帮我写文章") is None
        assert _match_bp_command("") is None

    def test_punctuation_tolerance(self):
        assert _match_bp_command("取消任务！") == "cancel"
        assert _match_bp_command("好的，继续") == "next"
        assert _match_bp_command("OK!") == "next_loose"
