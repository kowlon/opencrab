"""
Web Search MCP 服务器

基于 DuckDuckGo 的网络搜索服务，无需 API Key。

启动方式：
    python -m seeagent.mcp_servers.web_search

工具：
    - web_search: 搜索网页
    - news_search: 搜索新闻
"""

import logging
import os
import re
import traceback
from html import unescape
from urllib.parse import quote_plus

import httpx
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)
_BOCHA_ENDPOINT = "https://api.bocha.cn/v1/web-search"
_CN_ENGINES = ("sogou", "so360")


def _ddgs_backend() -> str:
    return os.getenv("DDGS_BACKEND", "duckduckgo").strip() or "duckduckgo"


def _ddgs_timeout() -> int:
    raw = (os.getenv("DDGS_TIMEOUT") or "").strip()
    if raw.isdigit():
        return max(2, min(int(raw), 60))
    return 8


def _ddgs_proxy() -> str | None:
    for key in ("DDGS_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return None


def _bocha_api_key() -> str:
    return (os.getenv("BOCHA_API_KEY") or "").strip()

# 创建 MCP 服务器实例
mcp = FastMCP(
    name="web-search",
    instructions="""Web Search MCP Server - 基于 DuckDuckGo 的网络搜索服务。

可用工具：
- web_search: 搜索网页，返回标题、链接和摘要
- news_search: 搜索新闻，返回最新新闻文章

使用示例：
- 搜索信息：web_search(query="Python 教程", max_results=10)
- 搜索新闻：news_search(query="AI 最新进展", max_results=10)
""",
)


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


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _extract_html_results(
    html: str,
    title_link_pattern: re.Pattern[str],
    snippet_pattern: re.Pattern[str],
    max_results: int,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    snippets = [_strip_html(s) for s in snippet_pattern.findall(html)]
    for idx, m in enumerate(title_link_pattern.finditer(html)):
        href, title_html = m.group(1), m.group(2)
        title = _strip_html(title_html)
        if not href or not title:
            continue
        body = snippets[idx] if idx < len(snippets) else ""
        items.append({"title": title, "href": href, "body": body})
        if len(items) >= max_results:
            break
    return items


def _fetch_search_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
    resp.raise_for_status()
    return resp.text or ""


def _search_sogou(query: str, max_results: int) -> list[dict[str, str]]:
    url = f"https://www.sogou.com/web?query={quote_plus(query)}"
    html = _fetch_search_html(url)
    pattern = re.compile(
        r'<h3[^>]*class="[^"]*vr-title[^"]*"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?</h3>',
        re.IGNORECASE | re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<p[^>]*class="[^"]*star-warp[^"]*"[^>]*>(.*?)</p>',
        re.IGNORECASE | re.DOTALL,
    )
    return _extract_html_results(html, pattern, snippet_pattern, max_results)


def _search_so360(query: str, max_results: int) -> list[dict[str, str]]:
    url = f"https://www.so.com/s?q={quote_plus(query)}"
    html = _fetch_search_html(url)
    pattern = re.compile(
        r'<h3[^>]*class="[^"]*res-title[^"]*"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?</h3>',
        re.IGNORECASE | re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<p[^>]*class="[^"]*res-desc[^"]*"[^>]*>(.*?)</p>',
        re.IGNORECASE | re.DOTALL,
    )
    return _extract_html_results(html, pattern, snippet_pattern, max_results)


def _try_cn_free_search(query: str, max_results: int) -> list[dict[str, str]] | None:
    errors: list[str] = []
    for engine in _CN_ENGINES:
        try:
            results = _search_sogou(query, max_results) if engine == "sogou" else _search_so360(query, max_results)
            if results:
                logger.info("[RESULT] tool=mcp.web_search provider=%s", engine)
                return results
            errors.append(f"{engine}: empty")
        except Exception as e:
            errors.append(f"{engine}: {type(e).__name__}: {e}")
    logger.warning("[SEARCH_FAIL] mcp cn_free unavailable, fallback to DDGS: %s", " | ".join(errors))
    return None


def _extract_bocha_results(payload: dict) -> list[dict[str, str]]:
    web_pages = payload.get("data", {}).get("webPages", {}).get("value", [])
    normalized: list[dict[str, str]] = []
    for item in web_pages:
        normalized.append(
            {
                "title": item.get("name", "无标题"),
                "href": item.get("url", ""),
                "body": item.get("snippet", "") or item.get("summary", ""),
            }
        )
    return normalized


def _try_bocha_search(query: str, max_results: int) -> list[dict[str, str]] | None:
    api_key = _bocha_api_key()
    if not api_key:
        logger.warning("[SEARCH_FAIL] mcp bocha unavailable: BOCHA_API_KEY not set, fallback to CN/DDGS")
        return None
    payload = {"query": query, "freshness": "noLimit", "summary": True, "count": max_results}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(_BOCHA_ENDPOINT, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        results = _extract_bocha_results(data)
        if results:
            logger.info("[RESULT] tool=mcp.web_search provider=bocha")
            return results
        logger.warning("[SEARCH_FAIL] mcp bocha empty results, fallback to CN/DDGS")
        return None
    except Exception as e:
        logger.warning("[SEARCH_FAIL] mcp bocha failed, fallback to CN/DDGS: %s: %s", type(e).__name__, e)
        return None


@mcp.tool()
def web_search(
    query: str, max_results: int = 10, region: str = "wt-wt", safesearch: str = "moderate"
) -> str:
    """
    Search the web using DuckDuckGo.

    Args:
        query: Search query string
        max_results: Maximum number of results (default: 5, max: 20)
        region: Region code (default: "wt-wt" for worldwide, "cn-zh" for China)
        safesearch: Safe search level ("on", "moderate", "off")

    Returns:
        Formatted search results with title, URL, and snippet
    """
    # 限制结果数量
    max_results = min(max(1, max_results), 20)
    logger.info("[SEARCH] tool=mcp.web_search query=%r max_results=%s", query[:120], max_results)
    trace: list[str] = [f"- 请求: mcp.web_search(query={query[:60]!r}, max_results={max_results})"]

    # 1) Bocha 优先
    bocha_results = _try_bocha_search(query, max_results)
    if bocha_results:
        logger.info("[RESULT] tool=mcp.web_search provider=bocha")
        trace.append("- 路径: bocha (命中)")
        return _with_trace(trace, _format_web_results(bocha_results))

    # 2) 国内免费搜索补充
    cn_results = _try_cn_free_search(query, max_results)
    if cn_results:
        logger.info("[RESULT] tool=mcp.web_search provider=cn_free")
        trace.append("- 路径: bocha -> cn_free (命中)")
        return _with_trace(trace, _format_web_results(cn_results))

    # 3) DDGS 回退
    try:
        from ddgs import DDGS
    except ImportError:
        from seeagent.tools._import_helper import import_or_hint
        return f"错误：{import_or_hint('ddgs')}"

    backend = _ddgs_backend()
    timeout = _ddgs_timeout()
    proxy = _ddgs_proxy()

    try:
        with DDGS(proxy=proxy, timeout=timeout) as ddgs:
            results = list(
                ddgs.text(
                    query,
                    max_results=max_results,
                    region=region,
                    safesearch=safesearch,
                    backend=backend,
                )
            )
            logger.info("[RESULT] tool=mcp.web_search provider=ddgs backend=%s", backend)
            trace.append(f"- 路径: bocha -> cn_free -> ddgs(backend={backend}) (命中)")
            return _with_trace(trace, _format_web_results(results))
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[SEARCH_FAIL] Web search failed: {type(e).__name__}: {e}\n{tb}")
        trace.append("- 路径: bocha -> cn_free -> ddgs (失败)")
        return _with_trace(trace, f"搜索失败: {type(e).__name__}: {e}")


@mcp.tool()
def news_search(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timelimit: str | None = None,
) -> str:
    """
    Search news using DuckDuckGo.

    Args:
        query: Search query string
        max_results: Maximum number of results (default: 5, max: 20)
        region: Region code (default: "wt-wt" for worldwide)
        safesearch: Safe search level ("on", "moderate", "off")
        timelimit: Time limit ("d" for day, "w" for week, "m" for month)

    Returns:
        Formatted news results with title, source, date, URL, and excerpt
    """
    try:
        from ddgs import DDGS
    except ImportError:
        from seeagent.tools._import_helper import import_or_hint
        return f"错误：{import_or_hint('ddgs')}"

    # 限制结果数量
    max_results = min(max(1, max_results), 20)
    logger.info("[SEARCH] tool=mcp.news_search query=%r max_results=%s", query[:120], max_results)
    trace: list[str] = [f"- 请求: mcp.news_search(query={query[:60]!r}, max_results={max_results})"]
    backend = _ddgs_backend()
    timeout = _ddgs_timeout()
    proxy = _ddgs_proxy()

    try:
        with DDGS(proxy=proxy, timeout=timeout) as ddgs:
            results = ddgs.news(
                query,
                max_results=max_results,
                region=region,
                safesearch=safesearch,
                timelimit=timelimit,
                backend=backend,
            )
            logger.info("[RESULT] tool=mcp.news_search provider=ddgs backend=%s", backend)
            trace.append(f"- 路径: ddgs(backend={backend}) (命中)")
            return _with_trace(trace, _format_news_results(results))
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[SEARCH_FAIL] News search failed: {type(e).__name__}: {e}\n{tb}")
        trace.append("- 路径: ddgs (失败)")
        return _with_trace(trace, f"新闻搜索失败: {type(e).__name__}: {e}")


def _with_trace(trace: list[str], content: str) -> str:
    return "### 搜索执行过程\n" + "\n".join(trace) + "\n\n" + content


# 作为模块运行时启动服务器
if __name__ == "__main__":
    mcp.run()
