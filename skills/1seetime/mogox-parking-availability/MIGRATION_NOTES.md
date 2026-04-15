# 环境管理迁移说明

## 迁移内容

已从嵌入式 Python 环境管理（`env_guard.py`）迁移到外部 Shell 脚本方式。

## 变更文件

### 新增文件
- `scripts/setup_venv.sh` - 一键初始化虚拟环境脚本
- `scripts/run_in_venv.sh` - Python 命令包装脚本

### 修改文件
- `scripts/query_parking.py` - 移除 env_guard 导入和调用
- `scripts/select_best_match.py` - 移除 env_guard 导入和调用
- `scripts/emit_seeclaw_output.py` - 移除 env_guard 导入和调用
- `SKILL.md` - 更新所有命令示例和环境管理说明

### 保留文件
- `scripts/env_guard.py` - 保留但不再使用（已废弃）

## 使用方式

### 首次初始化
```bash
bash scripts/setup_venv.sh
```

### 执行 Python 脚本
```bash
bash scripts/run_in_venv.sh python scripts/query_parking.py [args...]
bash scripts/run_in_venv.sh python scripts/select_best_match.py [args...]
bash scripts/run_in_venv.sh python scripts/emit_seeclaw_output.py [args...]
```

## 优势

1. **明确的职责分离** - 环境管理与业务逻辑分离
2. **显式控制** - 用户明确知道何时初始化环境
3. **跨技能一致性** - 与 video-tracker 等其他技能保持一致
4. **易于调试** - 环境问题更容易定位和修复
5. **无自动重启** - 移除复杂的自动重启逻辑

## 迁移日期

2026-03-31

## 实际部署中发现的问题及修复

### 问题 1：Windows 换行符（CRLF）
**现象**：shell 脚本报错 `\r: command not found` 或类似错误

**原因**：在 Windows 系统上创建的文件默认使用 CRLF 换行符，Linux 需要 LF

**修复**：
1. 使用 `sed -i 's/\r$//' scripts/*.sh` 转换换行符
2. 添加 `.gitattributes` 文件强制 shell 脚本使用 LF
3. Git 会在 checkout 时自动处理换行符

### 问题 2：setup_venv.sh 执行超时
**现象**：首次运行 `setup_venv.sh` 时被 SIGTERM 杀掉

**原因**：创建虚拟环境 + 安装依赖可能需要 2-5 分钟，超过 agent 默认超时

**解决方案**：
1. 在脚本中添加进度提示："这可能需要几分钟，请耐心等待..."
2. 如果在 agent 中超时，建议手动在终端执行：
   ```bash
   cd mogox-parking-availability
   bash scripts/setup_venv.sh
   ```
3. 虚拟环境只需初始化一次，后续执行不会超时

### 最佳实践

1. **开发环境**：在 Windows 上开发时，确保 Git 配置正确：
   ```bash
   git config --global core.autocrlf input
   ```

2. **部署环境**：在 Linux 服务器上首次部署时，手动运行初始化：
   ```bash
   bash scripts/setup_venv.sh
   ```

3. **CI/CD**：在构建流程中分离环境准备和测试执行：
   ```yaml
   - name: Setup environment
     run: bash scripts/setup_venv.sh
   - name: Run tests
     run: bash scripts/run_in_venv.sh python -m pytest
   ```
