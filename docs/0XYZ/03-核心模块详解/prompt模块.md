# prompt 模块

**位置**: `src/seeagent/prompt/`

**功能描述**: 编译身份文档为优化片段，组装系统提示词，处理 token 预算和工具调用守卫。

## 模块组成

| 文件 | 功能描述 |
|------|----------|
| `compiler.py` | 编译身份文档为优化片段 |
| `builder.py` | 分层组装系统提示词 |
| `budget.py` | Token 预算裁剪 |
| `guard.py` | 运行时工具调用守卫 |
| `retriever.py` | 用于提示词的记忆检索 |

## 提示词组装顺序

1. **Identity** — SOUL.md 原则
2. **Persona** — 人设
3. **Runtime** — 运行时
4. **Session Rules** — 会话规则
5. **AGENTS.md** — 项目上下文
6. **Catalogs** — 工具/技能/MCP 目录
7. **Memory** — 语义检索记忆
8. **User** — 用户信息

## 核心类

### PromptBuilder

**文件**: `prompt/builder.py`

系统提示词组装器。

**关键方法**:

| 方法 | 签名 | 说明 |
|------|------|------|
| `build` | `def build(context) -> str` | 组装完整系统提示词 |
| `add_layer` | `def add_layer(name, content)` | 添加提示词层 |

### PromptCompiler

**文件**: `prompt/compiler.py`

身份文档编译器。

**关键方法**:

| 方法 | 签名 | 说明 |
|------|------|------|
| `compile` | `def compile(identity_files) -> CompiledFragments` | 编译身份文档 |
| `check_outdated` | `def check_outdated() -> bool` | 检查是否过期 |

### TokenBudget

**文件**: `prompt/budget.py`

Token 预算管理。

**关键方法**:

| 方法 | 签名 | 说明 |
|------|------|------|
| `trim` | `def trim(messages, max_tokens) -> list` | 裁剪消息到预算内 |
| `estimate` | `def estimate(messages) -> int` | 估算 token 数量 |

## 模块依赖

```
prompt/
├── builder.py ──┬──► compiler.py (编译后的身份片段)
                ├──► memory/retrieval.py (记忆检索)
                └──► tools/catalog.py (工具目录)
```

## 相关链接

- 上一页：[memory 模块](./memory模块.md)
- 下一页：[llm 模块](./llm模块.md)
