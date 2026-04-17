# api 模块

**位置**: `src/seeagent/api/`

**功能描述**: FastAPI 服务器，提供 RESTful API 和 WebSocket 支持。

## 模块组成

| 文件 | 行数 | 功能描述 |
|------|------|----------|
| `server.py` | ~543 | FastAPI 应用设置 |
| `auth.py` | - | Web 认证中间件 |
| `routes/` | - | API 端点实现 |

## 主要路由

| 路由文件 | 端点前缀 | 功能 |
|----------|----------|------|
| `chat.py` | `/chat` | 聊天接口（SSE 流式） |
| `agents.py` | `/agents` | 多 Agent 管理 |
| `config.py` | `/config` | 配置接口 |
| `skills.py` | `/skills` | 技能管理 |
| `memory.py` | `/memory` | 记忆操作 |
| `scheduler.py` | `/scheduler` | 任务调度 |
| `mcp.py` | `/mcp` | MCP 服务器管理 |
| `orgs.py` | `/orgs` | 组织管理 |
| `health.py` | `/health` | 健康检查 |
| `websocket.py` | `/ws` | WebSocket 支持 |
| `sessions.py` | `/sessions` | 会话管理 |

## 核心类

### SeeAgentServer

**文件**: `api/server.py`

FastAPI 应用类。

**关键方法**:

| 方法 | 签名 | 说明 |
|------|------|------|
| `create_app` | `def create_app() -> FastAPI` | 创建 FastAPI 应用 |
| `setup_routes` | `def setup_routes(app)` | 设置路由 |
| `setup_middleware` | `def setup_middleware(app)` | 设置中间件 |

### 认证

**文件**: `api/auth.py`

Web 认证中间件。

**关键方法**:

| 方法 | 签名 | 说明 |
|------|------|------|
| `verify_token` | `def verify_token(token) -> User` | 验证 Token |
| `create_token` | `def create_token(user) -> str` | 创建 Token |

## API 端点示例

### 聊天端点

```
POST /chat
Request:
{
    "message": "用户输入",
    "session_id": "可选",
    "agent_type": "可选"
}

Response (SSE):
event: message
data: {"content": "响应内容", "tool_calls": [...]}
```

### 健康检查

```
GET /health

Response:
{
    "status": "healthy",
    "version": "1.26.2"
}
```

## 模块依赖

```
api/routes/* ──┬──► agents/orchestrator.py
               ├──► memory/manager.py
               ├──► skills/loader.py
               └──► sessions/manager.py
```

## 相关链接

- 上一页：[channels 模块](./channels模块.md)
- 下一页：[接口定义](../04-接口定义/README.md)
