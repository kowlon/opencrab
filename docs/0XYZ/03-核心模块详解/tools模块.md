# tools 模块

**位置**: `src/seeagent/tools/`

**功能描述**: 提供 Agent 可调用的外部能力，包括文件操作、Shell 命令、浏览器自动化等。

## 模块组成

| 文件 | 功能描述 |
|------|----------|
| `catalog.py` | 工具目录，支持渐进式暴露 |
| `file.py` | 文件操作工具定义 |
| `shell.py` | Shell 命令工具定义 |
| `web.py` | 网页搜索工具定义 |
| `mcp.py` | MCP 客户端 |
| `mcp_catalog.py` | MCP 服务器目录 |
| `handlers/` | 工具实现目录 |
| `definitions/` | LLM 函数调用 JSON Schema |

## 工具处理器（handlers/）

| 文件 | 功能描述 |
|------|----------|
| `browser.py` | Playwright 浏览器自动化 |
| `filesystem.py` | 文件读写操作 |
| `shell.py` | 命令执行 |
| `memory.py` | 记忆操作 |
| `skills.py` | 技能管理（安装/发现） |
| `plan.py` | 规划工具 |
| `scheduled.py` | 定时任务管理 |
| `im_channel.py` | IM 渠道操作 |
| `agent.py` | 子 Agent 委派 |
| `config.py` | 配置工具 |
| `mcp.py` | MCP 工具代理 |
| `system.py` | 系统工具 |
| `web_search.py` | 网页搜索 |

## 核心类

### ToolCatalog

**文件**: `tools/catalog.py`

工具目录管理，支持渐进式暴露。

**关键方法**:

| 方法 | 签名 | 说明 |
|------|------|------|
| `get_tools_for_llm` | `def get_tools_for_llm(context) -> list[Tool]` | 根据上下文获取工具 |
| `register_handler` | `def register_handler(name, handler)` | 注册工具处理器 |
| `get_handler` | `def get_handler(name) -> ToolHandler` | 获取工具处理器 |

### 高频工具（全量注入）

- `run_shell` — 执行 Shell 命令
- `read_file` — 读取文件
- `write_file` — 写入文件
- `list_directory` — 列出目录
- `ask_user` — 询问用户

## 模块依赖

```
tools/
├── catalog.py ──┬──► definitions/ (JSON Schema)
                └──► handlers/* (具体实现)
```

## 相关链接

- 上一页：[agents 模块](./agents模块.md)
- 下一页：[memory 模块](./memory模块.md)
