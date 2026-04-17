# agents 模块

**位置**: `src/seeagent/agents/`

**功能描述**: 实现多智能体协作，支持子 Agent 创建、任务委派和实例池管理。

## 模块组成

| 文件 | 行数 | 功能描述 |
|------|------|----------|
| `orchestrator.py` | ~1205 | 中央多智能体协调器 |
| `factory.py` | ~442 | Agent 实例工厂 |
| `profile.py` | - | AgentProfile 配置类 |
| `presets.py` | - | 预设 Agent 定义 |
| `task_queue.py` | - | 优先级任务队列 |
| `packager.py` | - | Agent 打包分享 |
| `fallback.py` | - | 失败回退处理 |
| `lock_manager.py` | - | 分布式锁管理 |
| `manifest.py` | - | Agent 清单模式 |

## 核心类

### 1. AgentOrchestrator

**文件**: `agents/orchestrator.py`

中央协调器，负责消息路由和任务委派。

**最大委派深度**: 5

**关键方法**:

| 方法 | 签名 | 说明 |
|------|------|------|
| `route_message` | `async def route_message(session_id, message, ...)` | 路由消息到合适 Agent |
| `delegate` | `async def delegate(task, agent_type, depth=0)` | 委派任务给子 Agent |

### 2. AgentFactory

**文件**: `agents/factory.py`

创建差异化的 Agent 实例。

**关键方法**:

| 方法 | 签名 | 说明 |
|------|------|------|
| `create` | `def create(profile: AgentProfile) -> Agent` | 从配置创建 Agent |
| `create_brain` | `def create_brain(profile) -> Brain` | 根据类型创建 Brain |

### 3. AgentInstancePool

**文件**: `agents/factory.py`

按会话的实例管理。

**空闲超时**: 30 分钟

### 4. AgentProfile

**文件**: `agents/profile.py`

每个 Agent 类型的配置数据类。

```python
@dataclass
class AgentProfile:
    id: str
    name: str
    type: str  # default, code_assistant, content_creator 等
    description: str
    system_prompt: str
    tools: list[str]
    max_depth: int = 5
```

## 预设 Agent 类型

| 类型 | 名称 | 描述 |
|------|------|------|
| `default` | 小秋 | 通用助手 |
| `content-creator` | - | 社交媒体内容创作 |
| `video-planner` | - | 视频脚本规划 |
| `office-doc` | 文助 | 文档处理 |
| `code-assistant` | - | 编程助手 |
| `browser-agent` | - | 网页浏览 |
| `data-analyst` | - | 数据分析 |

## 模块依赖

```
agents/
├── orchestrator.py ──┬──► core/agent.py (创建子Agent)
                     ├──► profile.py (Agent配置)
                     └──► sessions/manager.py (会话管理)
```

## 相关链接

- 上一页：[core 模块](./core模块.md)
- 下一页：[tools 模块](./tools模块.md)
