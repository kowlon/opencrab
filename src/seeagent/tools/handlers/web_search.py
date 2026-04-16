"""
Web Search 处理器

直接使用 ddgs 库执行网络搜索，无需通过 MCP。
"""

import asyncio
import re
import logging
import os
import traceback
from html import unescape
from typing import Any
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

_REGION_FALLBACKS = ("us-en", "wt-wt")
_BOCHA_ENDPOINT = "https://api.bocha.cn/v1/web-search"
_CN_ENGINES = ("sogou", "so360")


def _ddgs_backend() -> str:
    """DDGS backend 策略（默认避免 auto 扫描全引擎超时风暴）。"""
    return os.getenv("DDGS_BACKEND", "duckduckgo").strip() or "duckduckgo"


def _ddgs_timeout() -> int:
    """DDGS 超时秒数，默认 8 秒。"""
    raw = (os.getenv("DDGS_TIMEOUT") or "").strip()
    if raw.isdigit():
        return max(2, min(int(raw), 60))
    return 8


def _ddgs_proxy() -> str | None:
    """优先 DDGS_PROXY，其次兼容常见代理环境变量。"""
    for key in ("DDGS_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return None


def _sync_web_search(
    query: str,
    max_results: int,
    region: str,
    safesearch: str,
    backend: str,
    timeout: int,
    proxy: str | None,
) -> list[dict[str, Any]]:
    """在独立线程中执行同步的 ddgs 搜索（避免事件循环冲突）"""
    from ddgs import DDGS

    with DDGS(proxy=proxy, timeout=timeout) as ddgs:
        return ddgs.text(
            query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
            backend=backend,
        )


def _sync_news_search(
    query: str,
    max_results: int,
    region: str,
    safesearch: str,
    timelimit: str | None,
    backend: str,
    timeout: int,
    proxy: str | None,
) -> list[dict[str, Any]]:
    """在独立线程中执行同步的 ddgs 新闻搜索"""
    from ddgs import DDGS

    with DDGS(proxy=proxy, timeout=timeout) as ddgs:
        return ddgs.news(
            query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
            timelimit=timelimit,
            backend=backend,
        )


class WebSearchHandler:
    """Web Search 处理器"""

    TOOLS = ["web_search", "news_search"]

    def __init__(self, agent: Any = None):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "web_search":
            return await self._web_search(params)
        elif tool_name == "news_search":
            return await self._news_search(params)
        else:
            return f"Unknown web search tool: {tool_name}"

    async def _web_search(self, params: dict[str, Any]) -> str:
        """搜索网页"""
        query = params.get("query", "")
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, params.get("max_results", 5)), 20)
        region = str(params.get("region", "us-en") or "us-en")
        safesearch = params.get("safesearch", "moderate")
        logger.info("[SEARCH] tool=web_search query=%r max_results=%s", query[:120], max_results)

        trace: list[str] = [f"- 请求: web_search(query={query[:60]!r}, max_results={max_results})"]

        # 优先使用 bocha-web-search-1.0.1 能力（Bocha API）
        bocha_result = await self._try_bocha_search(query=query, max_results=max_results)
        if bocha_result is not None:
            logger.info("[RESULT] tool=web_search provider=bocha")
            trace.append("- 路径: bocha (命中)")
            return self._with_trace(trace, bocha_result)

        # 国内免费搜索补充（无 API Key）
        cn_result = await self._try_cn_free_search(query=query, max_results=max_results)
        if cn_result is not None:
            logger.info("[RESULT] tool=web_search provider=cn_free")
            trace.append("- 路径: bocha -> cn_free (命中)")
            return self._with_trace(trace, cn_result)

        try:
            from ddgs import DDGS  # noqa: F401
        except ImportError:
            from seeagent.tools._import_helper import import_or_hint
            return f"错误：{import_or_hint('ddgs')}"

        backend = _ddgs_backend()
        timeout = _ddgs_timeout()
        proxy = _ddgs_proxy()
        logger.info(
            f"[WebSearch] DDGS fallback config: backend={backend}, timeout={timeout}, "
            f"proxy={'set' if proxy else 'none'}"
        )

        errors: list[str] = []
        regions = [region] + [r for r in _REGION_FALLBACKS if r != region]
        for rg in regions:
            try:
                results = await asyncio.to_thread(
                    _sync_web_search,
                    query=query,
                    max_results=max_results,
                    region=rg,
                    safesearch=safesearch,
                    backend=backend,
                    timeout=timeout,
                    proxy=proxy,
                )
                logger.info("[RESULT] tool=web_search provider=ddgs region=%s", rg)
                trace.append(f"- 路径: bocha -> cn_free -> ddgs(region={rg}) (命中)")
                return self._with_trace(trace, self._format_web_results(results))
            except Exception as e:
                errors.append(f"{rg}: {type(e).__name__}: {e}")
        tb = traceback.format_exc()
        logger.error(f"[SEARCH_FAIL] Web search failed after region fallback: {' | '.join(errors)}\n{tb}")
        trace.append("- 路径: bocha -> cn_free -> ddgs (失败)")
        return (
            self._with_trace(trace, "搜索失败：Bocha / CN free / DDGS 均不可用。") + "\n"
            f"尝试区域: {', '.join(regions)}\n"
            f"错误摘要: {errors[-1] if errors else '未知错误'}\n"
            "建议：切换网络/代理后重试，或改用 MCP 的 web-search 服务。"
        )

    async def _news_search(self, params: dict[str, Any]) -> str:
        """搜索新闻"""
        query = params.get("query", "")
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, params.get("max_results", 5)), 20)
        region = str(params.get("region", "us-en") or "us-en")
        safesearch = params.get("safesearch", "moderate")
        timelimit = params.get("timelimit")
        trace: list[str] = [f"- 请求: news_search(query={query[:60]!r}, max_results={max_results})"]
        logger.info("[SEARCH] tool=news_search query=%r max_results=%s", query[:120], max_results)

        try:
            from ddgs import DDGS  # noqa: F401
        except ImportError:
            from seeagent.tools._import_helper import import_or_hint
            return f"错误：{import_or_hint('ddgs')}"

        backend = _ddgs_backend()
        timeout = _ddgs_timeout()
        proxy = _ddgs_proxy()
        logger.info(
            f"[WebSearch] DDGS news config: backend={backend}, timeout={timeout}, "
            f"proxy={'set' if proxy else 'none'}"
        )

        errors: list[str] = []
        regions = [region] + [r for r in _REGION_FALLBACKS if r != region]
        for rg in regions:
            try:
                results = await asyncio.to_thread(
                    _sync_news_search,
                    query=query,
                    max_results=max_results,
                    region=rg,
                    safesearch=safesearch,
                    timelimit=timelimit,
                    backend=backend,
                    timeout=timeout,
                    proxy=proxy,
                )
                logger.info("[RESULT] tool=news_search provider=ddgs region=%s", rg)
                trace.append(f"- 路径: ddgs(region={rg}) (命中)")
                return self._with_trace(trace, self._format_news_results(results))
            except Exception as e:
                errors.append(f"{rg}: {type(e).__name__}: {e}")
        tb = traceback.format_exc()
        logger.error(f"[SEARCH_FAIL] News search failed after region fallback: {' | '.join(errors)}\n{tb}")
        trace.append("- 路径: ddgs (失败)")
        return (
            self._with_trace(trace, "新闻搜索失败：当前网络环境无法访问 DDGS 搜索引擎。") + "\n"
            f"尝试区域: {', '.join(regions)}\n"
            f"错误摘要: {errors[-1] if errors else '未知错误'}\n"
            "建议：切换网络/代理后重试，或改用 MCP 的 web-search 服务。"
        )

    @staticmethod
    def _format_web_results(results: list) -> str:
        """格式化网页搜索结果"""
        if not results:
            return "未找到相关结果"

        output = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            url = r.get("href", r.get("link", ""))
            body = r.get("body", r.get("snippet", ""))
            output.append(f"**{i}. {title}**\n{url}\n{body}\n")

        return "\n".join(output)

    @staticmethod
    def _format_news_results(results: list) -> str:
        """格式化新闻搜索结果"""
        if not results:
            return "未找到相关新闻"

        output = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            url = r.get("url", r.get("link", ""))
            body = r.get("body", r.get("excerpt", ""))
            date = r.get("date", "")
            source = r.get("source", "")

            header = f"**{i}. {title}**"
            if source or date:
                header += f" ({source} {date})"

            output.append(f"{header}\n{url}\n{body}\n")

        return "\n".join(output)

    async def _try_bocha_search(self, query: str, max_results: int) -> str | None:
        """尝试使用 Bocha API 搜索；成功返回格式化结果，失败返回 None。"""
        api_key = os.getenv("BOCHA_API_KEY", "").strip()
        if not api_key:
            logger.warning("[SEARCH_FAIL] bocha unavailable: BOCHA_API_KEY not set, fallback to CN/DDGS")
            return None

        payload = {
            "query": query,
            "freshness": "noLimit",
            "summary": True,
            "count": max_results,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(_BOCHA_ENDPOINT, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            results = self._extract_bocha_results(data)
            if not results:
                logger.warning("[SEARCH_FAIL] bocha empty results, fallback to CN/DDGS")
                return None
            logger.info("[RESULT] tool=web_search provider=bocha")
            return self._format_web_results(results)
        except Exception as e:
            logger.warning(f"[SEARCH_FAIL] bocha failed, fallback to CN/DDGS: {type(e).__name__}: {e}")
            return None

    async def _try_cn_free_search(self, query: str, max_results: int) -> str | None:
        """尝试国内可用免费搜索引擎（搜狗/360），失败返回 None。"""
        errors: list[str] = []
        for engine in _CN_ENGINES:
            try:
                if engine == "sogou":
                    results = await self._search_sogou(query, max_results)
                else:
                    results = await self._search_so360(query, max_results)

                if results:
                    logger.info("[RESULT] tool=web_search provider=%s", engine)
                    return self._format_web_results(results)
                errors.append(f"{engine}: empty")
            except Exception as e:
                errors.append(f"{engine}: {type(e).__name__}: {e}")

        logger.warning(f"[SEARCH_FAIL] CN free engines unavailable, fallback to DDGS: {' | '.join(errors)}")
        return None

    async def _search_sogou(self, query: str, max_results: int) -> list[dict[str, Any]]:
        """通过搜狗网页检索抓取公开结果。"""
        url = f"https://www.sogou.com/web?query={quote_plus(query)}"
        html = await self._fetch_search_html(url)
        pattern = re.compile(
            r'<h3[^>]*class="[^"]*vr-title[^"]*"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?</h3>',
            re.IGNORECASE | re.DOTALL,
        )
        snippet_pattern = re.compile(
            r'<p[^>]*class="[^"]*star-warp[^"]*"[^>]*>(.*?)</p>',
            re.IGNORECASE | re.DOTALL,
        )
        return self._extract_html_results(html, pattern, snippet_pattern, max_results)

    async def _search_so360(self, query: str, max_results: int) -> list[dict[str, Any]]:
        """通过 360 搜索网页抓取公开结果。"""
        url = f"https://www.so.com/s?q={quote_plus(query)}"
        html = await self._fetch_search_html(url)
        pattern = re.compile(
            r'<h3[^>]*class="[^"]*res-title[^"]*"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?</h3>',
            re.IGNORECASE | re.DOTALL,
        )
        snippet_pattern = re.compile(
            r'<p[^>]*class="[^"]*res-desc[^"]*"[^>]*>(.*?)</p>',
            re.IGNORECASE | re.DOTALL,
        )
        return self._extract_html_results(html, pattern, snippet_pattern, max_results)

    async def _fetch_search_html(self, url: str) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text or ""

    @staticmethod
    def _strip_html(text: str) -> str:
        text = re.sub(r"<[^>]+>", "", text)
        return unescape(re.sub(r"\s+", " ", text)).strip()

    def _extract_html_results(
        self,
        html: str,
        title_link_pattern: re.Pattern[str],
        snippet_pattern: re.Pattern[str],
        max_results: int,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        snippets = [self._strip_html(s) for s in snippet_pattern.findall(html)]
        for idx, m in enumerate(title_link_pattern.finditer(html)):
            href, title_html = m.group(1), m.group(2)
            title = self._strip_html(title_html)
            if not href or not title:
                continue
            body = snippets[idx] if idx < len(snippets) else ""
            items.append({"title": title, "href": href, "body": body})
            if len(items) >= max_results:
                break
        return items

    @staticmethod
    def _with_trace(trace: list[str], content: str) -> str:
        """在工具输出前追加简洁执行过程，便于委派过程卡片展示。"""
        return "### 搜索执行过程\n" + "\n".join(trace) + "\n\n" + content

    @staticmethod
    def _extract_bocha_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """将 Bocha 返回结构归一化为 _format_web_results 可消费的格式。"""
        web_pages = (
            payload.get("data", {})
            .get("webPages", {})
            .get("value", [])
        )
        normalized: list[dict[str, Any]] = []
        for item in web_pages:
            normalized.append({
                "title": item.get("name", "无标题"),
                "href": item.get("url", ""),
                "body": item.get("snippet", "") or item.get("summary", ""),
            })
        return normalized


def create_handler(agent: Any = None):
    """创建 WebSearchHandler 实例并返回 handle 方法"""
    handler = WebSearchHandler(agent)
    return handler.handle
