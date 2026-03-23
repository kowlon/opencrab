# MasterAgent 上下文类型与最佳实践任务管理分析

## 一、MasterAgent 加载的上下文类型

MasterAgent 通过 `prompt/builder.py` 的 v2 编译管线组装系统提示词，上下文分为以下层次：

| 层次 | 内容 | 来源 |
|---|---|---|
| Identity 层 | `SOUL.md`（完整注入，约 60% 预算）+ `agent.core.md` + `agent.tooling.md`（编译后） | `identity/` → `identity/runtime/` |
| Persona 层 | 当前激活的人格描述 + 性格特征 | `identity/personas/` + memory |
| Runtime 层 | 版本、部署模式、时间、OS、CWD、Python 环境、PATH 工具、浏览器状态 | 动态采集 |
| Session 规则 | CLI/IM 分流规则、消息分类规则、artifact 投递方式 | `builder._build_session_type_rules()` |
| 项目指南 | `AGENTS.md`（向上遍历 3 级目录，max 8000 chars，缓存 60s） | 工作目录 |
| Catalog 层 | 工具目录（33%）+ 技能目录（55%）+ MCP 目录（10%），三级渐进式暴露 | `tools/catalog.py`、`skills/catalog.py` |
| BP 层 | 静态能力描述 + 动态会话状态（活跃实例表） | `bestpractice/facade.py` |
| Memory 层 | Scratchpad + Pinned Rules + Core Memory（`MEMORY.md`）+ Experience Hints（top5） | `memory/manager.py`、`prompt/retriever.py` |
| User 层 | `USER.md` 编译摘要（约 120 tokens） | `identity/USER.md` |
| System Policies | 三条红线 + 意图声明 + 工具上下文隔离 | 代码层（不可删除）+ 文件层（可定制） |

上下文预算管理：通过 `ContextManager` 管理，软限制为 85%。超限时触发 LLM 分块摘要压缩；单条 `tool_result` ≥ 5000 tokens 时独立压缩。

## 二、最佳实践（BP）任务的上下文管理

### 1）BP 状态核心数据结构

`BPInstanceSnapshot`（`bestpractice/models.py:99-161`）：

```text
bp_id, instance_id, session_id
status: ACTIVE / SUSPENDED / COMPLETED / CANCELLED
current_subtask_index: int
subtask_statuses: dict[str, str]      # PENDING → CURRENT → DONE/STALE/FAILED
subtask_outputs: dict[str, dict]
context_summary: str
supplemented_inputs: dict
bp_config: BestPracticeConfig
```

### 2）上下文注入方式

每轮推理循环中，`PromptAssembler._build_bp_section()` 注入两部分：

- 静态部分（`get_static_prompt_section()`）：BP 能力描述列表 + 触发条件
- 动态部分（`get_dynamic_prompt_section(session_id)`）：当前会话所有 BP 实例状态表（Markdown table）+ 活跃实例上下文 + 意图路由

### 3）持久化与恢复

- 序列化：`BPInstanceSnapshot.serialize()` → `Session.metadata["bp_state"]` → `SessionManager.mark_dirty()`
- 恢复：请求到达 → `_ensure_bp_restored()` → 从 `session.metadata` 反序列化 → 重载 `bp_config`

## 三、`bp_switch_task` 实现

核心文件：`bestpractice/handler.py:170-204`

流程：

```text
前端/Agent 调用 bp_switch_task(target_instance_id)
         │
         ▼
BPToolHandler._handle_switch_task()
         │
         ├─ 1. 验证目标实例存在
         ├─ 2. 挂起当前活跃实例: state_manager.suspend(current_id)
         │      → snap.status = SUSPENDED, snap.suspended_at = time.time()
         ├─ 3. 恢复目标实例: state_manager.resume(target_id)
         │      → snap.status = ACTIVE, snap.suspended_at = None
         └─ 4. 创建 PendingContextSwitch 对象
                → state_manager.set_pending_switch(session_id, switch)
                   │
                   ▼
         Agent._pre_reasoning_hook()（推理循环间消费）
                   │
                   ▼
         ContextBridge.execute_pending_switch()
                   │
                   ├─ _compress_context(): 取最近 5 条消息压缩（~200 chars/条）
                   └─ _restore_context(): 注入目标实例的恢复消息到 brain
```

`PendingContextSwitch` 数据结构（`models.py:91-96`）：

```python
@dataclass
class PendingContextSwitch:
    suspended_instance_id: str
    target_instance_id: str
    created_at: float = field(default_factory=time.time)
```

关键设计：切换操作不在工具调用内直接修改对话上下文，而是通过 `PendingContextSwitch` 延迟到推理循环间的安全执行点。

## 四、`bp_edit_output` 实现

核心文件：`bestpractice/engine.py:613-644` + `state_manager.py:260-269`

流程：

```text
前端 SubtaskOutput 编辑弹窗 → PUT /api/bp/edit-output
         │
         ▼
bestpractice.py: edit_bp_output()
         │
         ▼
BPEngine.handle_edit_output(instance_id, subtask_id, changes, bp_config)
         │
         ├─ 1. 深度合并: state_manager.merge_subtask_output()
         │      → _deep_merge(base, overlay): dict 递归合并，数组完整替换
         │
         ├─ 2. 标记下游 STALE: state_manager.mark_downstream_stale()
         │      → 遍历 subtasks 配置，从 from_subtask_id 之后
         │      → 所有已 DONE 的子任务标记为 STALE
         │      → 返回 stale_ids 列表
         │
         ├─ 3. 软校验: _validate_output_soft()
         │      → 检查输出是否缺少 outputSchema 要求的必填字段
         │
         └─ 4. 返回: { success, merged, stale_subtasks, warning }
                   │
                   ▼
         前端接收 → bpStore.markStale() 更新 UI
```

深度合并逻辑（`state_manager.py`）：

```python
@staticmethod
def _deep_merge(base: dict, overlay: dict) -> dict:
    result = dict(base)
    for k, v in overlay.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = BPStateManager._deep_merge(result[k], v)
        else:
            result[k] = v
    return result
```

`STALE` 传播逻辑：编辑某个子任务输出后，其下游所有已完成子任务自动标记为 `STALE`，表示输入依赖已变化，可能需要重新执行，从而保证流水线式子任务链的数据一致性。

## 总结：关键设计理念

1. 上下文分层：Identity → Persona → Runtime → Catalog → BP → Memory，每层有独立预算与更新频率。
2. 渐进式暴露：工具/技能采用三级展示（索引 → 详情 → 资源），节省 tokens。
3. 状态与上下文分离：BP 状态由 `BPStateManager` 独立管理，通过 `PendingContextSwitch` 延迟注入对话上下文。
4. 编辑传播：`bp_edit_output` 的 `STALE` 标记机制保证流水线子任务链数据一致性。
5. 双层持久化：内存快照 + Session metadata 序列化，支持服务重启恢复。
