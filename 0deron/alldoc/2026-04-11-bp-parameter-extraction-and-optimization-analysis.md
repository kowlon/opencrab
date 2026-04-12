# 最佳实践（BP）参数提取与填充机制分析

> 日期：2026-04-11
> 范围：SeeCrab 聊天入口、BP 启动、子任务输入组装、`ask_user` 兜底、后续优化建议
> 状态：基于当前代码实现整理；文末优化建议均为“已分析、未实现”

---

## 一、文档目的

本文用于系统梳理当前最佳实践（Best Practice, BP）中“参数从哪里来、如何流转、何时缺失、如何补齐”的完整逻辑，并明确：

1. 当前已经实现的行为是什么；
2. 第一子任务与后续子任务的填充机制有何不同；
3. `upstream`、`input_mapping`、历史消息提取、`ask_user` 之间如何配合；
4. 当前机制的边界与已识别风险是什么；
5. 后续可考虑的优化建议有哪些，以及它们**当前尚未实现**。

---

## 二、关键概念

为了避免概念混淆，先统一几个核心术语：

| 概念 | 含义 | 当前存储位置 |
|---|---|---|
| `initial_input` | BP 启动时，从用户消息/历史消息中一次性提取出的结构化参数 | BP 实例快照 |
| `supplemented_inputs` | 面向某个具体子任务的补充输入；既可能来自启动时预分发，也可能来自后续 `ask_user` 回答 | `supplemented_inputs[subtask_id]` |
| `subtask_outputs` | 子任务实际执行后的输出结果 | `subtask_outputs[subtask_id]` |
| `upstream` | 当前子任务 `input_schema` 中声明“应由上游子任务产出”的字段集合 | `input_schema.upstream` 或分支内 `upstream` |
| `input_mapping` | 显式指定某字段从哪个上游子任务输出映射而来 | `subtask.input_mapping` |
| `resolved_input` | 某个子任务真正执行前，调度器组装出来的最终输入 | 运行时临时值 |
| `ask_user` | 当某子任务的 `required` 字段仍不完整时，进入等待用户补充参数的兜底机制 | SSE 事件 + `engine.answer()` |

---

## 三、整体结论

当前 BP 参数机制已经从“只围绕第一子任务的 `input_schema` 提取参数”演进为：

```text
历史消息 / 当前消息
    ↓
基于所有子任务的“非 upstream 字段”做统一提取
    ↓
生成 initial_input
    ↓
启动时把 initial_input 预分发到后续子任务的 supplemented_inputs
    ↓
子任务运行时再用：
  上游输出 / input_mapping
  + supplemented_inputs
  组装 resolved_input
    ↓
若 required 仍缺失，触发 ask_user
```

这意味着：

- **统一提取**只发生在启动阶段；
- **后续子任务并不会再次拿“所有子任务 schema”重新提取一次**；
- 后续子任务依赖的是“预分发结果 + 上游输出 + ask_user 补充”的组合。

---

## 四、当前实现的完整执行链路

### 4.1 触发阶段：先构建统一提取用的大 Schema

当前系统已经实现 `_build_combined_user_schema(bp_config)`，逻辑如下：

1. 遍历 BP 的全部子任务；
2. 对每个子任务读取 `input_schema`；
3. 收集 schema 中全部属性；
4. **排除被 `upstream` 标记的字段**；
5. 合并成一个“只包含用户侧可提供字段”的扁平 schema。

核心意图是：

- `upstream` 字段不应该向用户历史消息要；
- 统一提取只负责“用户已经说过的信息”；
- 上游产物仍由任务链内部生成。

对应代码：

- `src/seeagent/api/routes/bestpractice.py`
- 核心函数：`_build_combined_user_schema()`

---

### 4.2 启动前提取：从用户消息和历史消息生成 `initial_input`

在 SeeCrab 路由中，用户进入 BP 启动流程时，系统会优先尝试使用已有 `pending_offer.extracted_input`；如果没有，就会：

1. 取 `pending_offer.user_query`；
2. 基于 `_build_combined_user_schema()` 得到的统一 schema；
3. 调用 `_extract_input_from_query(brain, user_query, combined_schema)`；
4. 生成 `initial_input`。

注意：

- 这里的 `user_query` 现在已经不再是单条最新消息，而是最近若干轮对话拼接成的 `history_context`；
- 因此，启动提取已经具备“多轮历史消息”能力；
- 但这一步仍然只做一次，不会在每个子任务运行前反复执行。

此外，在 `bp_offer` 待确认阶段，`handler.py` 也会把最近会话消息写入 `pending_offer`，供后续 `/api/bp/start` 或 SeeCrab 启动使用。

---

### 4.3 创建实例：`initial_input` 被写入 BP 实例

BP 实例创建时：

1. `input_data` 写入实例的 `initial_input`；
2. 紧接着调用 `_distribute_initial_input()`；
3. 将用户侧字段预分发给后续子任务。

这一步不是“重新提取”，而是“把已经提好的字段按子任务拆包”。

---

### 4.4 预分发阶段：把用户字段提前放进后续子任务

`_distribute_initial_input(instance_id, bp_config)` 的真实逻辑是：

1. 遍历第 2 个及之后的子任务；
2. 读取该子任务的 `input_schema`；
3. 计算该 schema 中的全部属性；
4. 扣除 `upstream` 字段；
5. 如果这些非 `upstream` 字段出现在 `initial_input` 中，就拷贝到：

```python
snap.supplemented_inputs[subtask.id]
```

因此它的作用是：

- 第一个子任务直接使用 `initial_input`；
- 后续子任务先拿到“属于自己的用户字段预填值”；
- 这些字段在子任务真正运行前就已经放好了。

这个预分发逻辑非常关键，因为它解释了：

> 为什么现在即使启动时用的是“所有子任务的统一 schema 提取”，后续子任务仍然能各自得到正确的用户字段，而不是每次都重新跑一次全局提取。

---

### 4.5 第一子任务：直接从 `initial_input` 起跑

调度器 `resolve_input(subtask_id)` 对第一子任务的逻辑是：

- 如果没有 `input_mapping`；
- 且当前子任务是第一个；
- 则直接使用：

```python
base = dict(self._snap.initial_input)
```

也就是说，**第一子任务的输入来源是整份 `initial_input`**。

注意这里有一个重要细节：

- 第一子任务不是“重新根据自己单独的 schema 做提取”；
- 而是“启动阶段统一提取后，直接拿整份 `initial_input` 使用”；
- 真正决定“够不够执行”的，是后面的完整性校验。

---

### 4.6 后续子任务：运行时动态拼装输入

对后续子任务，`resolve_input()` 会按以下顺序组装输入：

#### 情况 A：配置了 `input_mapping`

如果子任务显式配置了 `input_mapping`，则调度器会：

1. 按字段读取指定上游子任务的输出；
2. 组装出 `base`。

#### 情况 B：没有 `input_mapping`

如果没配置，则在线性调度中：

1. 默认读取前一个子任务的输出；
2. 作为 `base`。

#### 然后统一合并 `supplemented_inputs`

无论 A 还是 B，调度器最后都会执行：

```python
base.update(supplement)
```

其中 `supplement` 就是：

```python
self._snap.supplemented_inputs.get(subtask_id, {})
```

因此，后续子任务的真实输入来源是：

```text
resolved_input
  = 上游输出 / input_mapping 结果
  + 当前子任务的 supplemented_inputs
```

而 `supplemented_inputs` 里可能包含两类数据：

1. 启动时统一提取后预分发进来的字段；
2. 后续 `ask_user` 追问后用户补充的字段。

---

### 4.7 完整性检查：最终看当前子任务自己的 `required`

子任务真正执行前，会走 `_check_input_completeness(subtask, input_data)`。

它的逻辑是：

- 如果是普通 schema，就检查当前 schema 的 `required` 字段是否都在 `input_data` 中；
- 如果是 `oneOf/anyOf`，就先根据当前输入匹配最合适的分支，再检查该分支的 `required`。

因此：

- 第一子任务虽然拿的是整份 `initial_input`，但只检查自己的 `required`；
- 后续子任务虽然输入可能由多源拼装而成，但同样只检查**当前子任务自身 schema 的 `required`**。

这是整个机制中“提取范围”和“执行约束范围”分离的关键点：

- 启动阶段提取范围更大（为了少打扰用户）；
- 执行前校验范围更窄（只对当前子任务负责）。

---

### 4.8 `upstream` 的真实作用

`upstream` 的真实职责不是“提取参数”，而是“声明该字段应该由上游子任务提供”。

它会同时影响三件事：

#### 1）统一提取时排除

`_build_combined_user_schema()` 会把 `upstream` 字段排除，不会要求 LLM 从历史消息里提取它们。

#### 2）预分发时排除

`_distribute_initial_input()` 也会把 `upstream` 字段排除，不会把 `initial_input` 里的同名字段塞给下游子任务。

#### 3）上游输出约束

`derive_output_schema()` 会根据“下一个子任务声明的 `upstream` 字段”，反推“当前子任务应该输出哪些字段”，以便为下游准备输入。

所以 `upstream` 的闭环是：

```text
下游 schema 声明 upstream
    ↓
统一提取阶段不处理这些字段
    ↓
预分发阶段不处理这些字段
    ↓
运行时由上游子任务输出提供
```

这就是“用户字段”和“上游字段”在设计上的职责边界。

---

### 4.9 `ask_user`：当自动填充仍不够时的兜底机制

如果 `_check_input_completeness()` 发现仍有必填字段缺失：

1. 当前子任务状态被设置为 `WAITING_INPUT`；
2. 引擎发出 `bp_ask_user` 事件；
3. 前端提示用户补充所缺字段；
4. 用户在聊天或表单中补充后，进入 `engine.answer()`。

`engine.answer()` 的行为是：

1. 将用户提供的新数据写入：

```python
snap.supplemented_inputs[subtask_id]
```

2. 把该子任务状态重置为 `PENDING`；
3. 重新走一次 `advance()`；
4. 再次执行 `resolve_input()` 和完整性检查。

因此，`ask_user` 不是改写 `initial_input`，也不是改写 `subtask_outputs`，而是：

> 面向当前子任务，把缺失的用户参数写回 `supplemented_inputs`，再重新组装一次输入。

---

## 五、字段来源的真实流转

下面给出一张简化的字段流转表，帮助快速判断一个字段可能从哪里来。

| 阶段 | 第一子任务 | 后续子任务（非 upstream 字段） | 后续子任务（upstream 字段） |
|---|---|---|---|
| 启动时统一提取 | 会进入 `initial_input` | 会进入 `initial_input` | 不参与 |
| 启动时预分发 | 不需要 | 会被写入 `supplemented_inputs[subtask_id]` | 不参与 |
| 运行时自动拼装 | 直接从 `initial_input` 取 | 从 `supplemented_inputs[subtask_id]` 合并进入 | 从上游输出 / `input_mapping` 进入 |
| 缺失时 ask_user | 可以问用户 | 可以问用户 | **当前实现下也可能最终落到 ask_user** |

最后一列是当前机制最需要注意的点：

虽然设计意图上 `upstream` 字段应该来自上游，但**当前实现并不会在完整性检查阶段特别区分它们**。如果最终 `resolved_input` 中缺失，仍会被纳入 `missing_fields`，从而进入 `ask_user` 兜底。

---

## 六、当前实现的边界与已知风险

### 6.1 当前实现的优势

- 已支持“多轮历史消息”统一提取；
- 能在启动时尽量把所有用户已提供的信息一次性提全；
- 后续子任务不必重复打扰用户；
- `upstream` 与用户字段在设计上已经分层；
- `ask_user` 兜底链路完整，可恢复性强。

### 6.2 当前实现的主要边界

#### 边界 1：`upstream` 缺失与用户字段缺失未显式区分

完整性检查阶段只关心“字段是否存在”，不关心“字段本来应该由谁提供”。

这会导致：

- 从表现上看，系统很难区分“上游产物缺失”与“用户参数没给”；
- 某些场景下，本应由上游生成的字段也可能最终要求用户补充。

#### 边界 2：字段来源缺少显式可观测性

当前系统持久化了值，但没有持久化“该值来自哪里”的元信息。

这会导致：

- 排查为什么 ask_user 时，需要人工回读代码与状态；
- 不容易快速回答“该字段是统一提取来的，还是上游给的，还是用户后来补的”。

#### 边界 3：统一提取范围广于单个子任务执行范围

这是刻意设计，不是 bug，但在理解时要特别注意：

- 提取阶段看的是“整个 BP 需要哪些用户字段”；
- 执行阶段看的是“当前子任务 `required` 是否满足”。

如果不了解这一点，容易误以为“后续子任务每次都重新做了全局提取”。

---

## 七、后续优化建议（当前均未实现）

下面列出的建议，均来自对当前实现的分析判断，**截至 2026-04-11 均未实现**。后续是否推进，需要根据实际场景、维护成本与回归风险再做决策。

---

### 建议 1：将缺失字段区分为 `upstream` 缺失与用户字段缺失

**提出时间**：2026-04-11  
**当前状态**：未实现  
**优先级建议**：P1

#### 建议内容

在完整性检查后，不再只返回平铺的 `missing_fields`，而是进一步分类为：

- `missing_upstream_fields`
- `missing_user_fields`

#### 预期收益

- 能明确知道当前缺失是“上游没产出”还是“用户没提供”；
- `ask_user` 可只针对用户字段触发；
- 调试与排障成本会明显下降。

#### 风险评估

- 风险低到中；
- 需要在完整性检查与 `ask_user` 事件生成之间插入一层分类逻辑；
- 但不需要重构 `initial_input → distribute → resolve_input → answer` 主链路。

#### 为什么这是熵减优化

因为它是“增强语义边界”，不是“重写状态流”。  
系统会更容易解释，也更容易维护。

---

### 建议 2：增加字段来源的日志与可观测性

**提出时间**：2026-04-11  
**当前状态**：未实现  
**优先级建议**：P1

#### 建议内容

在 `resolve_input()` 完成后，记录当前子任务输入字段的来源，例如：

- 来自 `initial_input`
- 来自预分发后的 `supplemented_inputs`
- 来自上游 `subtask_outputs`
- 来自 `ask_user` 回答

#### 预期收益

- 线上排障更快；
- 便于核对某个字段的真实来源；
- 有助于未来进一步收敛 `ask_user` 与 `upstream` 逻辑。

#### 风险评估

- 风险低；
- 首先可以只做日志，不改持久化结构；
- 对现有行为几乎没有侵入。

#### 为什么这是熵减优化

因为它不增加新状态，只增加解释能力。

---

### 建议 3：让 `ask_user` 默认只针对用户字段，而非上游字段

**提出时间**：2026-04-11  
**当前状态**：未实现  
**优先级建议**：P2（依赖建议 1）

#### 建议内容

在缺失分类能力具备之后，优先只对 `missing_user_fields` 触发 `ask_user`。  
若只缺 `upstream` 字段，则优先暴露为“上游输出不完整”或“子任务产物缺失”，而非直接要求用户补齐。

#### 预期收益

- 降低用户被错误追问的概率；
- 更符合 `upstream` 的设计语义；
- 减少“用户不理解为什么要补上游产物”的交互摩擦。

#### 风险评估

- 风险中等；
- 某些业务上，用户确实可以手工补一个本应由上游给出的字段；
- 因此不建议绝对禁止，只建议默认优先级调整。

#### 为什么这是熵减优化

它是在不破坏韧性的前提下，让职责更清晰。

---

### 建议 4：对 `oneOf/anyOf` 场景做更保守的预分发

**提出时间**：2026-04-11  
**当前状态**：未实现  
**优先级建议**：P2

#### 建议内容

当前预分发是以“该子任务所有非 `upstream` 字段”为目标做宽松填充。  
对于 `oneOf/anyOf` 场景，可考虑在后续增强为：

- 先估算更可能命中的分支；
- 再只分发该分支真正相关的字段；
- 避免给某些分支无关的字段提前灌值。

#### 预期收益

- 在复杂 schema 中减少噪音字段；
- 降低分支误判和二次补问的概率。

#### 风险评估

- 风险中等；
- 分支判定时机若前移过早，可能引入新的误判；
- 因此不建议在没有充分样例前大规模推进。

#### 为什么这是熵减优化

前提是范围控制得当；否则容易从“保守收敛”变成“复杂度外溢”。

---

## 八、当前不建议立即推进的方向

下面这些方向在理论上看起来更“高级”，但以当前代码和需求成熟度来看，短期内更容易增熵。

### 8.1 不建议：重构为统一字段依赖图引擎

原因：

- 当前 BP 调度仍以线性子任务流为主；
- 现有 `resolve_input()` 已足够直观；
- 过早上依赖图求值器，会显著提高理解和调试成本。

### 8.2 不建议：把 `initial_input`、`supplemented_inputs`、`subtask_outputs` 合并为一个总输入仓

原因：

- 三者语义边界不同；
- 现在虽然解释略复杂，但职责清楚；
- 一旦合并，覆盖优先级、回放、持久化都会更复杂。

### 8.3 不建议：完全禁止 `upstream` 字段走 `ask_user`

原因：

- 某些真实业务中，用户手工补一个“理论上应由上游产出”的字段是可接受的；
- 直接禁掉会让系统从“可恢复”退化成“硬失败”；
- 更合理的做法是先分类、再调整默认策略，而不是一刀切。

---

## 九、推荐的后续推进顺序

如果后续要做收敛优化，建议顺序如下：

### 第一阶段（最值得做）

1. 缺失字段按 `upstream` / `user` 分类；
2. 增加字段来源日志与观测能力。

### 第二阶段（在第一阶段稳定后）

3. `ask_user` 默认只对用户字段触发；
4. 复杂分支 schema 的预分发进一步收紧。

### 第三阶段（暂缓）

5. 字段依赖图；
6. 超级统一输入仓；
7. 硬性禁止 `upstream` 字段进入 `ask_user`。

---

## 十、最终判断

当前 BP 参数机制的核心方向是正确的：

- 启动时做一次统一提取，减少对用户的重复打扰；
- 运行时按上游输出与子任务补充输入组装最终参数；
- 缺失时用 `ask_user` 兜底。

因此，当前系统**不是必须推倒重来**，也不是“已经好到不必动”。  
更准确的判断是：

> 主链路已经合理，但在“字段职责边界”和“可观测性”上仍有明显收敛空间。

如果后续要优化，最值得优先投入的是：

1. 明确区分“谁该提供这个字段”；
2. 明确记录“这个字段最终是从哪里来的”。

这两类优化都属于典型的熵减动作：

- 不重构主链路；
- 不打破现有恢复能力；
- 却能显著降低后续维护和排障成本。

---

## 附录：建议阅读的相关代码位置

- `src/seeagent/api/routes/bestpractice.py`
  - `_build_combined_user_schema()`
- `src/seeagent/api/routes/seecrab.py`
  - `_extract_input_from_query()`
  - `_llm_extract_answer_fields()`
  - SeeCrab 聊天入口中 BP 启动与 waiting_input 处理逻辑
- `src/seeagent/bestpractice/handler.py`
  - `pending_offer` 中历史消息与提取输入的保存
- `src/seeagent/bestpractice/engine/core.py`
  - `start()`
  - `advance()`
  - `_distribute_initial_input()`
  - `_check_input_completeness()`
  - `answer()`
- `src/seeagent/bestpractice/engine/scheduler.py`
  - `resolve_input()`
  - `derive_output_schema()`
