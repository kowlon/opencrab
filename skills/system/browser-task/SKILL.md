---
name: browser-task
description: Smart browser task agent - describe what you want done in natural language and it completes automatically. PREFERRED tool for multi-step browser operations like searching, form filling, and data extraction.
system: true
handler: browser
tool-name: browser_task
category: Browser
priority: high
---

# browser_task - 智能浏览器任务

**推荐优先使用** - 这是浏览器操作的首选工具。

基于 [browser-use](https://github.com/browser-use/browser-use) 开源项目实现。

## 用法

```python
browser_task(
    task="要完成的任务描述",
    max_steps=15  # 可选，默认 15
)
```

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| task | string | 是 | 任务描述，用自然语言描述你想完成的操作 |
| max_steps | integer | 否 | 最大执行步骤数，根据任务复杂度选择（见下方指导） |

## max_steps 复杂度选择指南

根据任务复杂度选择合适的 max_steps，避免过度搜索：

| 任务类型 | 复杂度 | max_steps | 示例 |
|---------|--------|-----------|------|
| 简单搜索/导航 | 低 | 3-5 | "搜索五泄风景区门票价格"、"打开百度" |
| 多步骤搜索+信息提取 | 中 | 5-8 | "搜索五泄风景区攻略，提取开放时间和门票信息" |
| 复杂交互（登录、填表、多页面） | 高 | 10-15 | "登录淘宝，搜索机械键盘，按销量排序" |
| 超复杂多页面操作 | 极高 | 15-20 | "在携程上完成酒店预订全流程" |

**判断方法**：
- 简单任务：1-2个页面即可完成，不需要翻页或多次筛选
- 复杂任务：需要登录、多步筛选、翻页、填写表单等

## 何时使用（优先）

- 任何涉及多步骤的浏览器操作
- 网页搜索、表单填写、信息提取
- 不确定具体操作步骤时
- 复杂的网页交互流程

## 何时不用 browser_task（用 browser_navigate 代替）

- **单次搜索或导航**：直接拼 URL 用 `browser_navigate` 更高效
  - ✅ `browser_navigate(url="https://www.baidu.com/s?wd=五泄风景区")`
  - ❌ `browser_task(task="用百度搜索五泄风景区")`
- **只需获取单个页面内容**：直接 `browser_navigate` + `browser_get_content`
- **已知 URL 的简单访问**：直接打开 URL，无需 AI 规划

## 示例

### 简单搜索任务（用 browser_navigate）
```python
# 简单搜索直接拼URL，更高效
browser_navigate(url="https://www.baidu.com/s?wd=五泄风景区门票")
```

### 信息提取任务（中等复杂度）
```python
browser_task(
    task="搜索五泄风景区攻略，提取开放时间、门票价格、交通指南",
    max_steps=5
)
```

### 复杂交互任务
```python
browser_task(
    task="登录携程，搜索北京到上海的机票，筛选晚上6-9点，截图保存结果",
    max_steps=12
)
```

### 截图任务
```python
browser_task(
    task="打开五泄风景区官网，截图保存首页",
    max_steps=3
)
```

## 浏览器工具选用指引

系统提供三条浏览器链路，按场景选择：

| 场景 | 工具 | 说明 |
|------|------|------|
| Agent 自主执行多步任务 | `browser_task`（首选） | 搜索、填表、抓取等，自动规划步骤 |
| 仅需单步操作 | `browser_navigate` / `browser_screenshot` 等 | task 失败时手动介入，或只做截图/导航 |
| 操作用户已登录的 Chrome | `call_mcp_tool("chrome-devtools", ...)` | 保留登录态和 Cookie，需用户 Chrome 开启调试端口 |

决策顺序：优先 `browser_task` → 单步退化到细粒度工具 → 需要登录态时用 chrome-devtools MCP。

## 何时使用细粒度工具

仅在以下情况使用 `browser_navigate`、`browser_click` 等细粒度工具：

- `browser_task` 执行失败需要手动介入
- 仅需单步操作（如只截图 `browser_screenshot`）
- 需要精确控制特定元素

## 返回值

```json
{
    "success": true,
    "result": {
        "task": "打开百度搜索福建福州",
        "steps_taken": 5,
        "final_result": "搜索完成，已显示福建福州相关结果",
        "message": "任务完成: 打开百度搜索福建福州"
    }
}
```

## 注意事项

1. 任务描述要清晰具体，避免歧义
2. 复杂任务可能需要增加 max_steps
3. 首次使用会自动启动浏览器（可见模式）
4. **自动继承系统 LLM 配置**，无需额外配置 API Key

## 技术细节

- 通过 CDP (Chrome DevTools Protocol) 复用 SeeAgent 已启动的浏览器
- 自动继承 SeeAgent 系统配置的 LLM（来自 llm_endpoints.json）
- 基于 [browser-use](https://github.com/browser-use/browser-use) 开源项目

## 高级：操作用户已打开的 Chrome

如果想让 SeeAgent 操作你已打开的 Chrome 页面，需要以调试模式启动 Chrome：

**Windows:**
```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

**macOS:**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
```

**Linux:**
```bash
google-chrome --remote-debugging-port=9222
```

启动后，SeeAgent 会自动检测并连接，可以操作你已打开的标签页。

## 相关技能

- `browser_screenshot` - 单独截图
- `browser_navigate` - 单独导航
- `deliver_artifacts` - 发送结果给用户
