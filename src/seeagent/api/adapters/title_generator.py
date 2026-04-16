"""TitleGenerator: LLM-powered title generation + humanize fallback."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

TITLE_TIMEOUT = 30  # seconds
MAX_CONCURRENT = 3

SKILL_TITLE_PROMPT = """根据以下信息，生成一个简短、对用户友好的步骤标题：

用户最近消息：
{recent_messages}

正在执行的技能：
- 名称：{name}
- 描述：{description}
- 分类：{category}

要求：
- 使用动词开头（如"搜索"、"分析"、"生成"、"整理"）
- 体现用户意图，而非技术操作名称
- 简洁明了，不超过 15 个字
- 使用用户的语言（中文/英文跟随用户消息）
- 只输出标题文本，不要任何额外内容"""

MCP_TITLE_PROMPT = """根据以下信息，生成一个简短、对用户友好的步骤标题：

用户最近消息：
{recent_messages}

正在调用的外部服务：
- 服务名：{server_name}
- 服务描述：{server_description}
- 工具名：{tool_name}
- 工具描述：{tool_description}

要求：
- 使用动词开头
- 体现用户意图，而非 API 名称
- 简洁明了，不超过 15 个字
- 使用用户的语言
- 只输出标题文本，不要任何额外内容"""

AGENT_TITLE_PROMPT = """根据以下信息，生成一个简短、对用户友好的步骤标题：

用户最近消息：
{recent_messages}

委派给专家代理：
- 名称：{agent_name}
- 描述：{agent_description}

委派任务：{message}
委派原因：{reason}

要求：
- 使用动词开头（如"调研"、"分析"、"编写"）
- 体现用户意图和专家角色
- 简洁明了，不超过 15 个字
- 使用用户的语言（中文/英文跟随用户消息）
- 只输出标题文本，不要任何额外内容"""

HUMANIZE_MAP: dict[str, object] = {
    "web_search": lambda args: f'web_search搜索 {args.get("query", "")}',
    "news_search": lambda args: f'news_search搜索新闻 {args.get("query", "")}',
    "browser_task": lambda _: "browser_task浏览网页获取内容",
    "generate_image": lambda _: "generate_image生成插图",
    "list_skills": lambda _: "list_skills查看已安装技能",
    "get_skill_info": lambda args: f"get_skill_info查看技能详情",
    "run_skill_script": lambda args: f"run_skill_script运行技能脚本",
    "run_shell": lambda args: f"run_shell执行命令",
    "write_file": lambda args: f"write_file写入文件",
    "read_file": lambda args: f"read_file读取文件",
    "list_directory": lambda args: f"list_directory浏览目录",
    "deliver_artifacts": lambda _: "deliver_artifacts发送结果文件",
}


class TitleGenerator:
    """Generates semantic titles for step cards."""

    def __init__(self, brain: object | None, user_messages: list[str]):
        self.brain = brain
        self.user_messages = user_messages[-5:] if user_messages else []
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    def humanize_tool_title(self, tool_name: str, args: dict) -> str:
        """Generate title for whitelisted tools using humanize map (no LLM)."""
        fn = HUMANIZE_MAP.get(tool_name)
        if fn:
            try:
                title = fn(args)
                logger.info("[TitleGenerator] humanized tool title: tool=%s title=%s", tool_name, title)
                return title
            except Exception as e:
                logger.warning(
                    "[TitleGenerator] humanize failed, fallback to default: tool=%s error=%s",
                    tool_name,
                    e,
                )
        fallback = f"执行 {tool_name}"
        logger.info("[TitleGenerator] default tool title fallback: tool=%s title=%s", tool_name, fallback)
        return fallback

    async def generate_skill_title(self, skill_meta: dict) -> str:
        """Generate LLM title for a Skill step card."""
        if self.brain is None:
            return self._skill_fallback(skill_meta)

        prompt = SKILL_TITLE_PROMPT.format(
            recent_messages="\n".join(self.user_messages) or "(无)",
            name=skill_meta.get("name", "unknown"),
            description=skill_meta.get("description", ""),
            category=skill_meta.get("category", ""),
        )
        return await self._call_llm(prompt, fallback=self._skill_fallback(skill_meta))

    async def generate_mcp_title(self, server_meta: dict, tool_meta: dict) -> str:
        """Generate LLM title for an MCP step card."""
        if self.brain is None:
            return self._mcp_fallback(server_meta)

        prompt = MCP_TITLE_PROMPT.format(
            recent_messages="\n".join(self.user_messages) or "(无)",
            server_name=server_meta.get("name", "unknown"),
            server_description=server_meta.get("description", ""),
            tool_name=tool_meta.get("name", ""),
            tool_description=tool_meta.get("description", ""),
        )
        return await self._call_llm(prompt, fallback=self._mcp_fallback(server_meta))

    def delegation_instant_title(self, args: dict) -> str:
        """Generate instant title from delegation args (no LLM)."""
        agent_id = args.get("agent_id", "专家")
        reason = (args.get("reason") or "").strip()
        message = (args.get("message") or "").strip()
        text = reason or message
        desc = (text[:20] + "..." if len(text) > 20 else text) if text else ""
        return f"委派 {agent_id}: {desc}" if desc else f"委派 {agent_id} 处理"

    async def generate_delegation_title(
        self,
        agent_meta: dict,
        task_meta: dict,
    ) -> str:
        """Generate LLM title for a delegation step card."""
        if self.brain is None:
            return self._delegation_fallback(agent_meta, task_meta)
        prompt = AGENT_TITLE_PROMPT.format(
            recent_messages="\n".join(self.user_messages) or "(无)",
            agent_name=agent_meta.get("name", "unknown"),
            agent_description=agent_meta.get("description", ""),
            message=task_meta.get("message", ""),
            reason=task_meta.get("reason", ""),
        )
        return await self._call_llm(
            prompt,
            fallback=self._delegation_fallback(agent_meta, task_meta),
        )

    @staticmethod
    def _delegation_fallback(agent_meta: dict, task_meta: dict) -> str:
        name = agent_meta.get("name", "专家")
        reason = task_meta.get("reason", "")
        message = task_meta.get("message", "")
        text = reason or message
        desc = (text[:20] + "..." if len(text) > 20 else text) if text else ""
        return f"委派 {name}: {desc}" if desc else f"委派 {name} 处理"

    async def _call_llm(self, prompt: str, fallback: str) -> str:
        """Call brain.think_lightweight with timeout and fallback."""
        async with self._semaphore:
            try:
                resp = await asyncio.wait_for(
                    self.brain.think_lightweight(prompt),
                    timeout=TITLE_TIMEOUT,
                )
                title = resp.content.strip().strip("\"'")
                if not title:
                    logger.info("[TitleGenerator] empty llm title, use fallback=%s", fallback)
                    return fallback
                final_title = title[:30]
                logger.info("[TitleGenerator] llm title generated: title=%s", final_title)
                return final_title  # safety cap
            except Exception as e:
                logger.warning(f"[TitleGenerator] LLM title failed: {e}")
                return fallback

    @staticmethod
    def _skill_fallback(meta: dict) -> str:
        name = meta.get("name", "unknown")
        desc = meta.get("description", "")
        if desc:
            return f"{name}: {desc[:15]}"
        return f"执行 {name}"

    @staticmethod
    def _mcp_fallback(server_meta: dict) -> str:
        name = server_meta.get("name", "unknown")
        return f"调用 {name} 服务"
