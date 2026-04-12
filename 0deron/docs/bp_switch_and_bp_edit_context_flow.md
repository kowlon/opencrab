# bp_switch_task 与 bp_edit_output 上下文与处理流程深度梳理

本文档详细梳理了最佳实践（Best Practice, BP）引擎中，涉及任务切换 (`bp_switch_task`) 与输出修改 (`bp_edit_output`) 两个核心交互指令的上下文整体结构、LLM 决策注入机制以及后端的完整处理流程。

## 1. 上下文整体结构与注入机制

在 `seeagent` 的 BP 引擎中，为了让 LLM 能够智能地在合适时机调用 `bp_switch_task` 和 `bp_edit_output`，系统通过 `BPPromptBuilder` 将上下文分为**静态能力声明**和**动态状态感知**两部分注入到 System Prompt 中。

### 1.1 静态上下文 (Static Section)
静态上下文定义在 `system_static.md` 中，负责告知 LLM 当前可用的工具列表及其基本定义：
- **`bp_switch_task`**: 明确告知 LLM，当用户想要切换到另一个挂起的 BP 实例时，调用 `bp_switch_task(target_instance_id)`。
- **`bp_edit_output`**: 明确告知 LLM，修改已完成子任务的输出时，调用 `bp_edit_output(subtask_id, changes)`，并说明 Chat-to-Edit 的工作模式。

### 1.2 动态上下文 (Dynamic Section)
动态上下文由 `BPPromptBuilder.build_dynamic_section` 实时生成，包含当前会话的所有 BP 状态、活动任务上下文及**意图路由 (Intent Routing)**。这是 LLM 决策的最核心依据：
1. **状态表 (Status Table)**：展示当前活跃 (ACTIVE) 和最近暂停 (SUSPENDED) 的任务列表。超出 3 个暂停任务时会折叠提示。
2. **活跃上下文 (Active Context)**：展示当前活跃任务的名称、进度，以及历史子任务输出预览 (Outputs Preview，有 Token 截断预算) 和用户偏好。
3. **意图路由 (Intent Routing)**：根据当前活跃任务的状态，动态告诉 LLM 应该推荐什么工具：
   - 如果**上一步已完成 (`done`)**：提示用户可能想要“A) 继续下一步 (bp_next)”，“B) 修改上一步结果 (bp_edit_output)”，“C) 取消任务”。
   - 如果**没有下一步或者处于其他闲置状态**：提示用户可能想要“A) 修改已完成子任务结果 (bp_edit_output)”，“B) 切换到其他任务 (展示挂起列表及 `bp_switch_task` 命令)”。
   - 如果**当前没有活跃任务但存在挂起任务**：提示用户可以“恢复已暂停的任务 (bp_switch_task)”。

---

## 2. bp_switch_task 处理流程

`bp_switch_task` 用于在多个并发的最佳实践任务之间进行上下文和状态的切换。

### 2.1 触发时机与上下文感知
- **LLM 触发**：当用户明确表达“切换到之前的任务”、“继续写代码那个任务”等意图时，LLM 会读取动态注入的挂起任务列表（带有 `instance_id`），并返回 `tool_use: bp_switch_task(target_instance_id="...")`。
- **API 拦截/恢复**：部分恢复意图直接在 API 层 (如 `seecrab.py`) 绕过普通匹配，直接交由 Agent 携带 BP 状态上下文调用 `bp_switch_task`。

### 2.2 后端执行流程 (`BPToolHandler._handle_switch_task`)
1. **参数校验与实例查找**：
   - 提取 `target_instance_id`，通过 `BPStateManager` 查询目标实例。
   - 校验实例是否存在且属于当前 `session_id`。
2. **引擎切换 (`engine.switch`)**：
   - 调用 `BPEngine.switch`，该步骤会将当前 ACTIVE 的实例状态改为 SUSPENDED，并将目标实例状态改为 ACTIVE。
   - 如果目标已经是 ACTIVE，则直接返回。
3. **状态与上下文恢复 (`engine.advance`)**：
   - 成功切换后，调用 `engine.advance(target_id)` 恢复执行。
   - `advance` 会触发内部逻辑恢复任务的执行进度，并通过 SSE 总线向前端广播事件（如 `bp_switched` 等），使前端 UI 状态同步更新。
4. **持久化 (`persist_to_session`)**：
   - 将更新后的状态表序列化，写入 `Session.metadata["bp_state"]`，并同步持久化到 SQLite 数据库。

---

## 3. bp_edit_output 处理流程

`bp_edit_output` 支持所谓的 Chat-to-Edit 模式，允许用户通过自然语言对已经完成的子任务输出进行结构化的修改。

### 3.1 触发时机与上下文感知
- 当一个子任务执行完成（状态为 `done`），动态 Prompt 的意图路由会明确指出：若要修改上一步结果，调用 `bp_edit_output`。
- LLM 会将用户的自然语言修改意图（如“把名字改成 X”，“在报告里加一段 Y”）转化为 JSON Diff（即 `changes` 字典），调用 `bp_edit_output(subtask_id, changes)`。

### 3.2 后端执行流程 (`BPToolHandler._handle_edit_output`)
1. **参数校验**：
   - 提取 `instance_id`（默认取当前活跃任务）、`subtask_id` 和 `changes` 字典。
   - 验证实例的归属与合法性。
2. **数据合并与引擎处理 (`engine.handle_edit_output`)**：
   - 调用 `BPEngine.handle_edit_output`。
   - 引擎内部调用 `state_manager.merge_subtask_output` 对现有的 output 和传入的 `changes` 进行**深度合并 (Deep Merge)**，生成完整的、修改后的 output 结果。
3. **下游失效标记 (Stale 机制)**：
   - 如果当前子任务的输出被修改，引擎会调用 `mark_downstream_stale`。
   - 遍历 BP 配置的流水线，将被编辑的子任务之后的所有状态为 `DONE` 的下游子任务标记为 `STALE`。
   - 这意味着下游依赖该输出的子任务数据已过期，后续调用 `bp_next` 时需要重新执行这些子任务。
4. **事件广播与持久化**：
   - 如果产生了 `stale_subtasks`，通过 `_emit_stale` 向上层或前端广播。
   - 调用 `persist_subtask_output` 和 `persist_subtask_progress` 更新 SQLite 和内存状态。
   - 返回给 LLM 完整的修改后结果及 Stale 警告，使 LLM 的 `Brain.Context` 中包含最新数据。

---

## 4. 总结

| 特性 | `bp_switch_task` | `bp_edit_output` |
| --- | --- | --- |
| **核心作用** | 并发任务管理、挂起与恢复 | 任务纠偏、Chat-to-Edit 局部更新 |
| **LLM 依赖上下文** | 动态 Prompt 注入的暂停任务列表及 `target_instance_id` | 动态 Prompt 注入的已完成子任务列表及 `outputs_preview` |
| **核心副作用** | 当前任务 SUSPENDED，目标任务 ACTIVE | `changes` 深度合并，下游 `DONE` 子任务降级为 `STALE` |
| **持久化机制** | 实例级状态 (`BPStatus`) 持久化 | 字段级更新 (`subtask_outputs` 和 `subtask_statuses`) |

通过 `BPPromptBuilder` 的动态感知注入与 `BPToolHandler` 的精确状态流转，SeeAgent 实现了高度解耦且可预测的复杂多轮任务管理能力。
