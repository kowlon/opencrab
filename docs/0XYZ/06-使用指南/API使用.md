# API 使用

## 启动服务

```bash
seeagent serve
```

默认监听 `http://localhost:8000`

## API 文档

启动服务后访问：
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## 主要端点

### 聊天

```
POST /chat
```

发送消息并获取流式响应。

**请求体**:

```json
{
    "message": "用户输入",
    "session_id": "可选",
    "agent_type": "可选(default/code_assistant/...)"
}
```

**响应** (SSE):

```
event: message
data: {"content": "响应内容", "tool_calls": []}

event: done
data: {"summary": "任务完成"}
```

### 创建会话

```
POST /sessions
```

**请求体**:

```json
{
    "user_id": "user_123",
    "agent_type": "default"
}
```

**响应**:

```json
{
    "session_id": "sess_abc123",
    "created_at": "2024-01-01T00:00:00Z"
}
```

### 获取会话

```
GET /sessions/{session_id}
```

### 发送消息

```
POST /sessions/{session_id}/messages
```

**请求体**:

```json
{
    "content": "消息内容",
    "role": "user"
}
```

### 列出 Agent

```
GET /agents
```

### 健康检查

```
GET /health
```

**响应**:

```json
{
    "status": "healthy",
    "version": "1.26.2"
}
```

## WebSocket

```
WS /ws/{session_id}
```

建立 WebSocket 连接进行实时通信。

## 错误响应

```json
{
    "error": {
        "code": "INVALID_REQUEST",
        "message": "详细错误信息"
    }
}
```

## 相关链接

- 上一页：[CLI使用](./CLI使用.md)
- 下一页：[技能开发](./技能开发.md)
