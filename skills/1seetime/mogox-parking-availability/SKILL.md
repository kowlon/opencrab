---
name: mogox-parking-availability
description: 查询停车场的实时车位信息。支持两种模式：(1) 按名称查询特定停车场；(2) 按经纬度搜索周边停车场。当用户询问停车位、停车场空位情况、哪里可以停车，或提到具体停车场名称时使用此技能。适用于"朝阳大悦城停车场还有位置吗"、"查一下国贸附近的停车位"、"我在三里屯哪里能停车"、"附近哪个停车场有空位"等各种询问停车位或停车场状态的查询。
---

# 停车场车位查询技能

此技能帮助用户查询停车场的实时车位信息。支持两种查询模式：

- **名称查询**：从用户查询中提取停车场名称，调用停车 API，智能匹配最佳结果，返回单条结构化 JSON
- **周边搜索**：基于经纬度 + 搜索半径，返回周边所有停车场的实时车位信息（多条结果）

## 使用场景

当用户有以下需求时使用此技能：
- 询问特定地点的停车位情况（名称模式）
- 提到停车场名称或地标（名称模式）
- 想知道哪里可以停车（名称模式）
- 询问剩余车位数量（名称模式）
- 需要特定区域的停车信息（名称模式）
- 询问"附近/周边哪个停车场有空位"（周边模式）
- 提供了经纬度坐标，想搜索周边停车场（周边模式）

## Python 运行环境（.venv）

**必须遵守的约定**

- 本技能所需的 Python 虚拟环境**固定创建在技能根目录**下的 **`.venv`** 文件夹（与 `SKILL.md`、`requirements.txt` 同级），不要把 venv 建到其它路径或名称（如 `venv`、`env`）。
- **安装依赖**时只往该 `.venv` 里装（使用下面「创建与安装」中的 pip），保证技能可复现。
- **启动 / 执行脚本**时，必须使用 **`.venv` 内的 Python 解释器**（或先激活该环境再运行），不要使用系统全局 Python，避免缺包或版本不一致。

### 创建与安装

在 **`mogox-parking-availability/` 技能根目录**执行（路径中含 `SKILL.md` 的那一层）：

**一键初始化脚本**（推荐）：

```bash
bash scripts/setup_venv.sh
```

此脚本会自动：
- 创建 `.venv` 虚拟环境
- 配置清华 pip 镜像源（加速国内下载）
- 升级 pip
- 安装 `requirements.txt` 中的所有依赖
- 验证关键依赖是否正确安装

脚本是幂等的，可以安全地多次运行。如果虚拟环境已存在，会跳过创建步骤。

**注意事项**：
- 首次运行可能需要 2-5 分钟（创建虚拟环境 + 下载安装依赖）
- 如果在 agent 执行中超时，可以手动在终端运行此脚本
- Shell 脚本必须使用 Unix 换行符（LF），不能使用 Windows 换行符（CRLF）

### 本技能关键依赖（requirements.txt）

| 包 | 作用 |
|----|------|
| **requests**（≥2.31.0） | `query_parking.py` 请求停车 API；`select_best_match.py` 请求 DashScope 兼容 Chat Completions API。 |

完整版本约束以同目录 **`requirements.txt`** 为准；新增脚本若引入其它第三方库，须同步写入 `requirements.txt` 并在上表补充说明。

### 运行脚本时的命令形式

在技能根目录下，使用 **`run_in_venv.sh` 包装脚本**执行所有 Python 命令：

```bash
bash scripts/run_in_venv.sh python scripts/query_parking.py [args...]
bash scripts/run_in_venv.sh python scripts/select_best_match.py [args...]
bash scripts/run_in_venv.sh python scripts/emit_seeclaw_output.py [args...]
```

此包装脚本会：
- 自动激活 `.venv` 虚拟环境
- 优先使用现有虚拟环境（快速启动）
- 虚拟环境不存在或损坏时自动创建/修复
- 执行失败时自动重新安装依赖并重试

**跨平台兼容**：Windows（Git Bash）、macOS、Linux 均使用相同命令。

### 执行前环境检查

`run_in_venv.sh` 会自动处理虚拟环境的管理（创建、更新、重试），无需手动检查。

用户只需确保：
- 当前目录是 `mogox-parking-availability/` 技能根目录
- 所有 Python 命令通过 `bash scripts/run_in_venv.sh` 包装执行

**环境初始化**（可选，仅首次使用）：
- 运行 `bash scripts/setup_venv.sh` 创建虚拟环境并安装依赖
- 或者直接运行 `bash scripts/run_in_venv.sh`，脚本会自动处理

**错误处理**：
- `run_in_venv.sh` 会自动处理虚拟环境不存在、损坏或依赖缺失的情况

**重要**：不要直接使用系统 Python 或 `.venv/bin/python` 执行脚本，始终使用 `run_in_venv.sh` 包装。

## 临时文件存储规范

所有产物输出到 `../../../data/skills_result/mogox-parking-availability/<任务时间>/`，任务时间格式为 `YYYYMMDD_HHMMSS`。

**目录基准（强制）**：
- 所有相对路径都以 `mogox-parking-availability/` 技能根目录为基准
- `run_in_venv.sh` 会自动切换到技能根目录执行命令，避免因调用时 cwd 不同而把文件写到错误位置
- 严禁使用 `data/...`（这会在技能目录下创建 `data/`）；必须使用 `../../../data/skills_result/...`

**强制一致性约束（新增）**：
- 单次任务必须先生成一次 `RUN_ID`，并在后续所有脚本命令中复用同一个 `OUTPUT_DIR`
- 严禁在步骤 2/3/4 分别使用 `$(date +%Y%m%d_%H%M%S)` 重新生成目录（会导致 `api_response.json` 与 `final_output.json` 不在同一路径）

```bash
RUN_ID="$(date +%Y%m%d_%H%M%S)"
OUTPUT_DIR="../../../data/skills_result/mogox-parking-availability/${RUN_ID}"
mkdir -p "${OUTPUT_DIR}"
```

可选：为了便于排查，可将脚本 stdout/stderr 同步落盘到同一目录：

```bash
LOG_FILE="${OUTPUT_DIR}/execution.log"
```

```
../../../data/skills_result/mogox-parking-availability/20260331_143000/

```
../../../data/skills_result/mogox-parking-availability/20260331_143000/
├── query_params.json        # 查询参数
├── api_response.json        # API 原始响应
├── selected_match.json      # LLM 选择的最佳匹配
├── final_output.json        # 最终标准化输出
└── execution.log            # 执行日志（可选）
```

输出路径需要在调用脚本时通过 `--output` 参数显式指定。

## 工作流程

### 步骤 0：初始化环境

首次使用时，在技能根目录执行：

```bash
bash scripts/setup_venv.sh
```

验证环境是否正确：

```bash
bash scripts/run_in_venv.sh python scripts/query_parking.py --help
```

若虚拟环境不存在，`run_in_venv.sh` 会提示先运行 `setup_venv.sh`。

### 步骤 1：判断查询类型并提取信息

首先判断用户的查询属于哪种模式：

**A. 名称查询模式** — 用户提到了具体停车场名称或地标：
- 提取停车场名称/关键词（如"朝阳大悦城停车场"、"国贸"、"三里屯"）

  **严格约束（必须遵守）**：
  - **只提取用户明确提到的停车场名称或地标，不得进行任何联想、扩展或替换**
  - **禁止将用户提到的地点替换为其他相似或附近的地点**
  - **自动追加"停车场"后缀**：如果提取的名称末尾不包含"停车场"三个字，必须自动追加"停车场"后缀再传入 `--query`
    - ✅ 用户说"朝阳大悦城" → `--query "朝阳大悦城停车场"`
    - ✅ 用户说"国贸停车场" → `--query "国贸停车场"`（已包含，无需追加）
    - ✅ 用户说"三里屯" → `--query "三里屯停车场"`
  - 例如：
    - ✅ 用户说"环球贸易中心" → 提取"环球贸易中心停车场"（可以）
    - ✅ 用户说"环球贸易中心" → 提取"环贸停车场"（公认简称，可以）
    - ❌ 用户说"环球贸易中心" → 提取"国贸停车场"（完全不同的地方，严禁）
    - ❌ 用户说"三里屯" → 提取"朝阳大悦城停车场"（不同地点，严禁）
  - **如果不确定用户提到的是哪个停车场，保持原始表述并追加"停车场"后缀，不要自行推测或替换**

- **城市提示（可选）**：用户提到的城市名称（如"绍兴"、"北京"、"上海"）
  - 必须规范化为带"市"后缀的格式（如"绍兴市"、"北京市"）
  - 如果用户未提及城市，则不传递此参数

**B. 周边搜索模式** — 用户询问"附近/周边停车场"并提供了经纬度：
- 提取必要参数：
  - `lat`（纬度）— 必须
  - `lng`（经度）— 必须
  - `radius`（搜索半径，米）— 可选，默认 2000
  - `cityHint`（城市提示）— 必须
- **如果用户未提供 lat、lng 或 cityHint，必须提示用户补充这些信息，不得自行猜测**

### 步骤 2：调用停车 API

#### 2A. 名称查询模式

使用内置的 `query_parking.py` 脚本调用停车位查询 API：

```bash
bash scripts/run_in_venv.sh python scripts/query_parking.py \
  --query "提取的停车场名称" \
  --city-hint "城市名称（可选，如'绍兴市'）" \
  --output "${OUTPUT_DIR}/api_response.json" \
  2>&1 | tee -a "${LOG_FILE}"
```

#### 2B. 周边搜索模式

```bash
bash scripts/run_in_venv.sh python scripts/query_parking.py \
  --lat 29.710166081 \
  --lng 120.2573191614 \
  --radius 2000 \
  --city-hint "诸暨市" \
  --output "${OUTPUT_DIR}/api_response.json" \
  2>&1 | tee -a "${LOG_FILE}"
```

脚本会：
- 根据参数自动判断搜索模式（有 `--lat` + `--lng` 时进入周边模式）
- 使用适当的参数调用 API
- 返回服务器排序的停车场结果
- 将结果保存到指定的输出文件
- 在 `query_params.json` 中记录 `search_mode: "nearby"` 或 `"name"`

### 步骤 3：选择匹配结果

#### 3A. 名称查询模式

API 返回多个候选停车场。使用内置的 `select_best_match.py` 脚本智能选择最相关的停车场：

```bash
bash scripts/run_in_venv.sh python scripts/select_best_match.py \
  --candidates "${OUTPUT_DIR}/api_response.json" \
  --user-query "原始用户查询" \
  --mode name \
  --output "${OUTPUT_DIR}/final_output.json" \
  2>&1 | tee -a "${LOG_FILE}"
```

此脚本使用 LLM 分析候选停车场，选择最符合用户询问的停车场，考虑因素包括：
- 名称相似度
- 位置相关性
- 用户查询的上下文

**重要约束**：
- 只有当候选停车场与用户查询的停车场名称高度匹配时，才能返回该停车场
- 如果所有候选停车场都与用户查询的停车场名称不匹配或相似度很低，必须返回"未找到匹配停车场"
- 严禁推荐用户未询问的停车场，即使该停车场在附近或看起来相关
- 宁可返回"未找到"，也不要返回不相关的停车场

#### 3B. 周边搜索模式

周边模式跳过 LLM 匹配，直接对所有候选停车场生成标准化输出：

```bash
bash scripts/run_in_venv.sh python scripts/select_best_match.py \
  --candidates "${OUTPUT_DIR}/api_response.json" \
  --user-query "附近停车场" \
  --mode nearby \
  --lat 29.710166081 --lng 120.2573191614 \
  --output "${OUTPUT_DIR}/final_output.json" \
  2>&1 | tee -a "${LOG_FILE}"
```

- `--lat`/`--lng`：搜索中心坐标，用于距离计算
- 无候选时返回 `{"totalFound": 0, "rankedList": []}`

### 步骤 4：输出结果（严禁手动构造）

**唯一允许的输出方式**：运行 `emit_seeclaw_output.py` 脚本，将其 stdout 原样作为最终回复。

```bash
bash scripts/run_in_venv.sh python scripts/emit_seeclaw_output.py \
  --input "${OUTPUT_DIR}/final_output.json" \
  --user-query "原始用户查询" \
  --mode nearby \
  2>&1 | tee -a "${LOG_FILE}"
```

周边模式需要加 `--mode nearby` 参数，以确保 fallback 输出结构正确。名称模式可省略 `--mode`（默认 `name`）。

脚本会自动完成：
- JSON 序列化校验
- 清理字符串中混入的代码块标记（防止 ` ```seeclaw-json-park ` 出现在 JSON 值中）
- 用 `seeclaw-json-park` 代码块包裹输出
- 若 `final_output.json` 缺失，自动生成 `no_camera` 兜底输出

**agent 必须将脚本的 stdout 原样输出给用户，不得修改、重新格式化、或手动拼接任何部分。**

⚠️ **以下行为严格禁止**：
- agent 自己编写或拼接 `seeclaw-json-park` 代码块
- 复制脚本输出后手动修改字段
- 因为 API 返回空或脚本报错就跳过脚本、自行构造 JSON
- 在代码块前后添加解释性文字

## 输出数据结构

- **名称查询模式**：`parkingScenarios` 返回 1 条（最佳匹配停车场）
- **周边搜索模式**：`totalFound` + `rankedList` 结构（不再是 `parkingScenarios`）

输出格式由 `select_best_match.py` 的 `build_standard_output()` 函数定义，经 `emit_seeclaw_output.py` 校验和包裹后输出。agent 不需要了解具体字段细节，只需按步骤 4 运行脚本即可。

## 错误处理

如果未找到匹配的停车场：
- 仍然必须通过 `emit_seeclaw_output.py` 脚本输出，**严禁 agent 手动拼接 JSON**
- 当 API 返回空结果或无候选时，仍然执行 `select_best_match.py`（传入空候选），脚本会自动生成正确的 `no_camera` 兜底输出
- 如果 `select_best_match.py` 已执行但 `final_output.json` 缺失，`emit_seeclaw_output.py` 的 `--user-query` 参数会触发内置 fallback
- **严禁推荐其他停车场**（名称模式）：即使 API 返回了其他候选停车场，如果它们与用户查询的停车场名称不匹配，也不能推荐给用户

**周边搜索模式缺少参数**：
- 如果用户未提供 `lat`（纬度）或 `lng`（经度），必须提示用户补充坐标信息
- 如果用户未提供 `cityHint`（城市提示），必须提示用户补充城市信息
- 不得自行猜测或使用默认坐标/城市

**禁止手动构造输出（最高优先级）**：
- 任何情况下，agent 都**不得自行拼写、拼接或构造** `seeclaw-json-park` JSON
- 所有输出必须且只能来自 `emit_seeclaw_output.py` 脚本的 stdout
- 即使 API 返回为空、脚本报错、或 agent 认为自己"知道"正确格式，也必须通过脚本输出
- 手动构造的 JSON 会导致字段缺失、多余或取值错误（如给 `no_camera` 状态添加 `dataTimestamp`、`availability` 等不该存在的字段）

如果 API 调用失败：
- 告知用户停车服务暂时不可用
- 不要输出格式错误的 JSON

## 重要提示

- **查询参数提取原则（最高优先级）**：
  - 从用户消息中提取停车场名称时，**严格使用用户的原始表述，不得进行任何联想、扩展或替换**
  - 禁止将用户提到的地点替换为其他相似、附近或看似相关的地点
  - 例如：用户说"环球贸易中心"，不能替换为"国贸"；用户说"三里屯"，不能替换为"朝阳大悦城"
  - 如果不确定用户提到的是哪个停车场，保持原始表述传递给 API，让 API 和后续的匹配逻辑处理
- 输出格式严格，因为前端会直接解析
- 返回前务必验证输出是有效的 JSON
- `select_best_match.py` 脚本处理智能匹配逻辑，信任其选择结果
- 如果多个停车场看起来同样合适，脚本会根据用户查询中的上下文线索进行选择
- **匹配原则**：只返回与用户查询高度匹配的停车场。如果 API 返回的候选停车场都与用户查询不匹配，必须返回"未找到匹配停车场"（`status: "no_camera"`），严禁推荐用户未询问的其他停车场
- **强制实时查询规则**：即使在同一个 agent 会话中，用户连续两次（或多次）询问同一停车场，也必须重新完整执行 `query_parking.py` 和 `select_best_match.py`。禁止直接复用上一轮的 API 响应、`selected_match.json`、`final_output.json` 或记忆中的旧结果进行回复。
- **强制脚本输出规则**：所有 `seeclaw-json-park` 代码块必须且只能由 `emit_seeclaw_output.py` 生成。agent 在任何情况下都不得手动编写或拼接 JSON 输出，包括但不限于：API 返回空、无匹配、脚本异常等场景。
