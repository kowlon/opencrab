"""
Prompt Guard - 运行时守门模块

检查 LLM 响应是否符合规则：
- 任务型请求必须包含工具调用
- 不允许敷衍响应（如"我理解了"但无动作）

违规时触发重试，最多重试 3 次。
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..core.task_monitor import TaskMonitor

logger = logging.getLogger(__name__)


class TaskKind(Enum):
    """任务类型"""

    ACTION = "action"  # 任务型：需要执行操作
    DIALOGUE = "dialogue"  # 对话型：可以直接回复


class ViolationType(Enum):
    """违规类型"""

    NO_ACTION = "no_action"  # 任务型请求无工具调用
    EVASIVE_RESPONSE = "evasive"  # 敷衍响应
    INCOMPLETE_TASK = "incomplete"  # 任务未完成


@dataclass
class GuardConfig:
    """守门配置"""

    # 最大重试次数
    max_retries: int = 3

    # 敷衍响应关键词
    evasive_patterns: list = None

    # 是否启用守门
    enabled: bool = True

    def __post_init__(self):
        if self.evasive_patterns is None:
            self.evasive_patterns = [
                r"我理解了",
                r"我明白了",
                r"好的，我会",
                r"我来帮你",
                r"让我为你",
                r"我将为你",
                r"我可以帮",
            ]


@dataclass
class GuardResult:
    """守门结果"""

    passed: bool  # 是否通过
    violation: ViolationType | None  # 违规类型
    retry_hint: str | None  # 重试提示
    original_response: Any  # 原始响应


def classify_task(user_message: str) -> TaskKind:
    """
    分类任务类型

    Args:
        user_message: 用户消息

    Returns:
        TaskKind 枚举
    """
    # 对话型关键词
    dialogue_patterns = [
        r"^你好",
        r"^hi\b",
        r"^hello\b",
        r"^早上好",
        r"^晚上好",
        r"^谢谢",
        r"^感谢",
        r"^再见",
        r"^bye\b",
        r"什么是.+",
        r".+是什么",
        r"怎么理解",
        r"请解释",
        r"^好的$",
        r"^明白$",
        r"^知道了$",
    ]

    # 任务型关键词
    action_patterns = [
        r"打开",
        r"创建",
        r"写.+文件",
        r"查.+",
        r"搜索",
        r"提醒",
        r"帮我",
        r"执行",
        r"运行",
        r"删除",
        r"修改",
        r"更新",
        r"发送",
        r"截图",
        r"下载",
        r"安装",
        r"设置.+提醒",
        r"\d+分钟后",
        r"每天.+点",
    ]

    message_lower = user_message.lower().strip()

    # 先检查对话型
    for pattern in dialogue_patterns:
        if re.search(pattern, message_lower, re.IGNORECASE):
            return TaskKind.DIALOGUE

    # 再检查任务型
    for pattern in action_patterns:
        if re.search(pattern, message_lower, re.IGNORECASE):
            return TaskKind.ACTION

    # 默认为对话型（保守策略）
    return TaskKind.DIALOGUE


def guard_response(
    response: Any,
    user_message: str,
    tools_enabled: bool,
    config: GuardConfig | None = None,
) -> GuardResult:
    """
    检查 LLM 响应是否符合规则

    Args:
        response: LLM 响应对象
        user_message: 用户原始消息
        tools_enabled: 是否启用了工具
        config: 守门配置

    Returns:
        GuardResult 对象
    """
    if config is None:
        config = GuardConfig()

    if not config.enabled:
        return GuardResult(
            passed=True,
            violation=None,
            retry_hint=None,
            original_response=response,
        )

    # 分类任务
    task_kind = classify_task(user_message)

    # 对话型请求不检查
    if task_kind == TaskKind.DIALOGUE:
        return GuardResult(
            passed=True,
            violation=None,
            retry_hint=None,
            original_response=response,
        )

    # 任务型请求检查
    if task_kind == TaskKind.ACTION and tools_enabled:
        # 检查是否有工具调用
        has_tool_call = _check_tool_call(response)

        if not has_tool_call:
            # 检查是否有脚本执行意图（write_file + run_shell）
            has_script_intent = _check_script_intent(response)

            if not has_script_intent:
                # 检查是否是敷衍响应
                is_evasive = _check_evasive(response, config.evasive_patterns)

                if is_evasive:
                    return GuardResult(
                        passed=False,
                        violation=ViolationType.EVASIVE_RESPONSE,
                        retry_hint="你必须使用工具执行任务，不能只回复文字。请调用相关工具。",
                        original_response=response,
                    )
                else:
                    return GuardResult(
                        passed=False,
                        violation=ViolationType.NO_ACTION,
                        retry_hint="这是一个任务型请求，请使用工具完成。如果没有合适的工具，请使用 write_file + run_shell 创建脚本。",
                        original_response=response,
                    )

    return GuardResult(
        passed=True,
        violation=None,
        retry_hint=None,
        original_response=response,
    )


def _check_tool_call(response: Any) -> bool:
    """检查响应是否包含工具调用"""
    # 检查 Anthropic 格式
    if hasattr(response, "content"):
        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                return True

    # 检查字典格式
    if isinstance(response, dict):
        if "tool_calls" in response and response["tool_calls"]:
            return True
        if "content" in response:
            content = response["content"]
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        return True

    return False


def _check_script_intent(response: Any) -> bool:
    """检查是否有创建脚本的意图"""
    # 获取文本内容
    text = _get_response_text(response)
    if not text:
        return False

    # 检查是否提到脚本创建
    script_patterns = [
        r"write_file.*\.py",
        r"run_shell.*python",
        r"创建.+脚本",
        r"写.+代码",
    ]

    return any(re.search(pattern, text, re.IGNORECASE) for pattern in script_patterns)


def _check_evasive(response: Any, patterns: list) -> bool:
    """检查是否是敷衍响应"""
    text = _get_response_text(response)
    if not text:
        return False

    for pattern in patterns:
        if re.search(pattern, text):
            # 如果后面跟着具体行动描述，不算敷衍
            # 例如 "好的，我来打开百度" 不是敷衍
            action_words = ["打开", "创建", "执行", "查询", "搜索"]
            has_action = any(word in text for word in action_words)
            if not has_action:
                return True

    return False


def _get_response_text(response: Any) -> str:
    """从响应中提取文本内容"""
    if isinstance(response, str):
        return response

    if hasattr(response, "content"):
        texts = []
        for block in response.content:
            if hasattr(block, "text"):
                texts.append(block.text)
        return "\n".join(texts)

    if isinstance(response, dict):
        if "text" in response:
            return response["text"]
        if "content" in response:
            content = response["content"]
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = []
                for item in content:
                    if isinstance(item, dict) and "text" in item:
                        texts.append(item["text"])
                return "\n".join(texts)

    return ""


async def guard_and_retry(
    run_llm_fn: Callable,
    user_message: str,
    tools_enabled: bool,
    config: GuardConfig | None = None,
    task_monitor: Optional["TaskMonitor"] = None,
) -> Any:
    """
    带守门的 LLM 调用（自动重试）

    Args:
        run_llm_fn: 异步 LLM 调用函数，无参数
        user_message: 用户原始消息
        tools_enabled: 是否启用工具
        config: 守门配置
        task_monitor: TaskMonitor 实例（可选，用于记录重试）

    Returns:
        LLM 响应

    Raises:
        RuntimeError: 超过最大重试次数
    """
    if config is None:
        config = GuardConfig()

    retry_count = 0

    while retry_count <= config.max_retries:
        # 调用 LLM
        response = await run_llm_fn()

        # 检查响应
        result = guard_response(
            response=response,
            user_message=user_message,
            tools_enabled=tools_enabled,
            config=config,
        )

        if result.passed:
            return response

        # 记录违规
        retry_count += 1

        logger.warning(
            f"Guard violation: {result.violation.value}, retry {retry_count}/{config.max_retries}"
        )

        if task_monitor:
            task_monitor.record_error(f"GUARD:{result.violation.value}")

        # 如果还能重试，添加提示
        if retry_count <= config.max_retries:
            # 这里应该通过某种方式将 retry_hint 传递给下一次 LLM 调用
            # 具体实现取决于 run_llm_fn 的接口
            logger.info(f"Retry hint: {result.retry_hint}")

    # 超过重试次数
    raise RuntimeError(
        f"Guard failed after {config.max_retries} retries. Last violation: {result.violation.value}"
    )
