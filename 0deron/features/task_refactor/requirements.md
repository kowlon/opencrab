# BP 架构重构需求说明

> 日期: 2026-03-21
> 状态: 待确认
> 修订: v3 — 基于二轮 review 补充 R15-R21

---

## 一、核心变更：BP 编排控制权转移

**从**: MasterAgent (LLM) 控制 BP 全流程
**到**: BPEngine (确定性代码) 控制编排，MasterAgent 只做意图理解

---

## 二、具体需求项

### R1. TaskScheduler 抽象层

- 新建 `scheduler.py`，基类 `TaskScheduler` + 派生 `LinearScheduler`
- 本次只实现线性调度，DAG 不做，但接口预留
- `resolve_input()` 支持 `input_mapping`（DAG 就绪）和线性 fallback（前一个子任务输出）
- `resolve_input()` 中 `input_mapping` 格式必须与现有代码一致：`{field_name: upstream_subtask_id}`（整个上游输出赋给字段），不引入新格式
- `derive_output_schema()` 从 `schema_chain.py` 合并过来

### R2. BPEngine 新增 `advance()` 异步生成器

- 替代现有 `execute_subtask()`，直接 yield SSE 事件
- auto 模式：内部 while 循环，连续执行直到全部完成
- manual 模式：执行 1 个子任务后 yield `bp_waiting_next`，等前端调 `/bp/next`
- SubAgent 执行通过现有 `orchestrator.delegate()` + 临时 event_bus 捕获流式事件
- `advance()` 本身不 yield `done` 事件，由 API 路由层统一发射

### R3. BPEngine 新增 `answer()` 方法

- 处理 SubAgent ask_user 的回答
- ���并补充数据到子任务输入：使用 `BPInstanceSnapshot` 上的独立字段 `supplemented_inputs: dict[str, dict]`，不污染 `subtask_outputs`
- 重置子任务状态为 PENDING，内部复用 `advance()` 重新执行同一子任务

### R4. 新增 3 个 SSE 流端点

| 端点 | 触发者 | 作用 |
|------|--------|------|
| `POST /api/bp/start` | 前端点击"使用最佳实践" | 创建实例 + 执行第一个子任务 |
| `POST /api/bp/next` | 前端点击"进入下一步" | 推进到下一子任务 |
| `POST /api/bp/answer` | 前端提交补充数据 | 合并数据 + 重新执行当前子任务 |

### R5. 新增 2 个普通端点

| 端点 | 作用 |
|------|------|
| `GET /api/bp/output/{instance_id}/{subtask_id}` | 查询子任务输出 |
| `DELETE /api/bp/{instance_id}` | 取消 BP 实例 |

已有端点不变：`GET /api/bp/status`、`PUT /api/bp/run-mode`、`PUT /api/bp/edit-output`

### R6. MasterAgent 工具从 7 个减到 3 个

**保留:**

| 工具 | 理由 |
|------|------|
| `bp_start` | 需要 LLM 理解用户意图、提取参数。只创建实例，不执行子任务，通过 SSE event_bus 通知前端接管 |
| `bp_edit_output` | 需要 LLM 将自然语言转为结构化 changes |
| `bp_switch_task` | 需要 LLM 判断目标任务 |

**删除:**

| 工具 | 替代 |
|------|------|
| `bp_continue` | `POST /bp/next` |
| `bp_get_output` | `GET /bp/output/{id}/{subtask_id}` |
| `bp_cancel` | `DELETE /bp/{id}` |
| `bp_supplement_input` | `POST /bp/answer` |

### R7. SubtaskStatus 新增 `WAITING_INPUT`

- SubAgent 检测输入不足时，子任务进入此状态
- 实例级 BPStatus 不变（仍为 ACTIVE）
- 保持 `SubtaskStatus(Enum)` 基类不变（不改为 `str, Enum`），避免序列化兼容问题

### R8. SubAgent ask_user 机制

- SubAgent 无状态：检测到缺字段 → BPEngine yield `bp_ask_user` → SubAgent 销毁
- 用户填写后 → `POST /bp/answer` → 合并数据 → 重建 SubAgent 重新执行
- 输入完整性由 SubAgent LLM 判断为主
- 保留 `_check_input_completeness()` 作为快速路径：`input_schema.required` 中明显缺失的字段直接返回 `bp_ask_user`，不浪费 LLM 调用

### R9. return_direct 机制清理

- 迁移完成后移除 `reasoning_engine.py` 和 `tool_executor.py` 中的 `_return_direct` 逻辑
- 因为 `bp_continue` 已不存在，不再需要强制终止 ReAct 循环

### R10. BPEngine orchestrator 注入 (新增)

- BPEngine 需要获取 orchestrator 引用才能调用 `delegate()`
- 当前 orchestrator 通过 `BPToolHandler._get_orchestrator(agent)` 从 agent 实例获取，新架构无 agent 对象
- 方案：facade 层提供 `set_orchestrator()` 懒注入，server.py 启动时注入；API 路由也可从 `request.app.state` 获取后传入

### R11. SSE 端点 busy-lock (新增)

- `/bp/start`、`/bp/next`、`/bp/answer` 均为 SSE 长连接，需要 busy-lock 防止并发
- 锁粒度：per instance_id（同一 BP 实例不能并发执行）
- 与 `/chat` 端点的锁互斥：同一 session_id 下 `/chat` 和 `/bp/*` SSE 不能同时运行
- 复用 seecrab.py 的 busy-lock 模式（TTL + async mutex）

### R12. Session 消息持久化 (新增)

- `/bp/next`、`/bp/start`、`/bp/answer` 的执行结果需持久化到 session history
- 每个子任务完成后调 `session.add_message("assistant", summary, reply_state={...})` 保存
- 确保会话历史中能看到 BP 执行过程，支持前端刷新后状态恢复

### R13. 客户端断开处理 (新增)

- 新 SSE 端点需要 disconnect watcher（与 `/chat` 相同模式）
- 客户端断开时取消正在执行的 SubAgent delegate 任务
- 清理 event_bus 引用，释放 busy-lock

### R14. bp_start → /bp/next 时序约束 (新增)

- MasterAgent 调 bp_start 工具后推 `bp_instance_created` 到 SSE event_bus
- 前端必须等 `/chat` 流的 `done` 事件（busy-lock 释放）后才能调 `/bp/next`
- 时序：`/chat` 流结束 → busy-lock 释放 → 前端调 `/bp/next`

### R15. _resolve_session 的 create_if_missing 区分 (新增)

- `/bp/start` 使用 `create_if_missing=True`（session 可能尚不存在，如前端直接调用）
- `/bp/next` 和 `/bp/answer` 使用 `create_if_missing=False`（session 必须已存在）

### R16. Auto 模式 busy-lock 续期 (新增)

- Auto 模式 `advance()` 内部 while 循环可能长时间运行，超过 busy-lock TTL (600s)
- SSE 端点在每个 `bp_subtask_complete` / `bp_progress` 事件后刷新锁时间戳
- 新增 `_bp_renew_busy(session_id)` 辅助函数

### R17. Disconnect watcher 取消 delegate_task (新增)

- disconnect watcher 检测到断开后，需显式取消正在执行的 `delegate_task`
- `_run_subtask_stream()` 将 `delegate_task` 引用存入 `session.context._bp_delegate_task`
- watcher 通过 `dt.cancel()` 取消，finally 块清理引用

### R18. _persist_bp_to_session 满足 R12 完整要求 (新增)

- 除了 `session.metadata["bp_state"]` 元数据持久化
- 还需调用 `session.add_message("assistant", summary, reply_state={...})` 写入会话历史
- summary 包含 BP 名称和进度信息，reply_state 含 subtask_statuses

### R19. resolve_input 所有分支统一合并 supplemented_inputs (新增)

- 当 `input_mapping` 分支命中时，不能直接 return，需继续合并 `supplemented_inputs`
- 所有分支（input_mapping / 线性 fallback）最终统一合并后再返回

### R20. delegate 异常标记子任务为 FAILED (新增)

- `advance()` 中 `_run_subtask_stream()` 异常时，先调 `update_subtask_status(FAILED)`
- 然后 yield `bp_error` 事件
- 防止前端重试时 scheduler 认为子任务仍是 CURRENT/PENDING

### R21. TaskScheduler enum vs string 比较 (新增)

- `subtask_statuses` 存储字符串值（`.value`），非枚举对象
- `is_done()` 使用 `SubtaskStatus.DONE.value` 比较
- `get_ready_tasks()` 使用 `SubtaskStatus.PENDING.value` / `SubtaskStatus.STALE.value` 比较
- `complete_task()` 存储 `SubtaskStatus.DONE.value`

---

## 三、不变的部分

- `BPStateManager` — 核心逻辑不变
- `ContextBridge` — 任务切换逻辑不变
- `orchestrator.delegate()` — SubAgent 执行机制不变
- `SeeCrabAdapter` — `/chat` 的 SSE 适配不变
- BP 触发检测 (`match_bp_from_message`) — 不变
- BP 配置加载 (`BPConfigLoader`) — 不变

---

## 四、实施顺序

1. **Phase 1 新建**：scheduler.py → engine.py 新增方法 → bestpractice.py 新端点（不破坏现有）
2. **Phase 2 前端切换**：前端改调新端点
3. **Phase 3 清理**：删旧工具、旧方法、return_direct
