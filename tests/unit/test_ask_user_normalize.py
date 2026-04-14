"""Unit tests for `_normalize_ask_user_question`.

Covers the regex that reformats LLM-emitted inline markdown bold lists
(common failure mode: `- **field1**: ... - **field2**: ...` all on one line)
into multi-line lists so frontend renderers can display them properly.
"""
from seeagent.core.reasoning_engine import _normalize_ask_user_question


class TestNormalizeAskUserQuestion:
    def test_splits_inline_bold_list_into_separate_lines(self):
        """典型失败样例: 两个 bold 列表项挤在一行，应被拆开。"""
        inline = (
            "请提供时间范围： - **开始时间**：起点，格式 YYYY-MM-DD "
            "- **结束时间**：终点，格式同上"
        )
        normalized = _normalize_ask_user_question(inline)
        assert normalized == (
            "请提供时间范围：\n- **开始时间**：起点，格式 YYYY-MM-DD"
            "\n- **结束时间**：终点，格式同上"
        )

    def test_preserves_already_multiline_lists(self):
        """已经换行的列表应该保持原样（幂等性）。"""
        already = (
            "请提供以下信息：\n"
            "- **开始时间**：起点\n"
            "- **结束时间**：终点"
        )
        assert _normalize_ask_user_question(already) == already

    def test_handles_three_plus_items(self):
        """3+ 个 bold 列表项混在一行也应被全部拆开。"""
        inline = "请提供： - **字段A**：说明 - **字段B**：说明 - **字段C**：说明"
        normalized = _normalize_ask_user_question(inline)
        assert normalized.count("\n- **") == 3

    def test_empty_or_none_input(self):
        """空字符串和 None 应安全返回。"""
        assert _normalize_ask_user_question("") == ""
        assert _normalize_ask_user_question(None) is None

    def test_no_bold_list_untouched(self):
        """不含 bold 列表项的文本不应被动到。"""
        plain = "你希望查找的时间范围是什么？"
        assert _normalize_ask_user_question(plain) == plain

    def test_non_bold_list_untouched(self):
        """只有 `- ` 但没 `**` 的不匹配（避免误伤普通文本）。"""
        text = "选项一 - 方案A 选项二 - 方案B"
        assert _normalize_ask_user_question(text) == text

    def test_mixed_content(self):
        """引导语 + 多字段 bold 列表 + 尾部说明，应只拆开列表项。"""
        inline = (
            "您想查找衡州大道的灰色小汽车，需要您提供时间范围： "
            "- **开始时间**：截帧开始时间（例如 2023-12-09T15:03:01） "
            "- **结束时间**：截帧结束时间（例如 2023-12-09T15:04:01）"
        )
        normalized = _normalize_ask_user_question(inline)
        lines = normalized.split("\n")
        # 3 行: 引导语 / 开始时间 / 结束时间
        assert len(lines) == 3
        assert lines[0].endswith("需要您提供时间范围：")
        assert lines[1].startswith("- **开始时间**")
        assert lines[2].startswith("- **结束时间**")
