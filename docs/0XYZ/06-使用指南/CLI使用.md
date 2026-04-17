# CLI 使用

## 安装

```bash
# 安装项目
pip install -e ".[dev]"

# 验证安装
seeagent --version
```

## 命令行模式

### 交互模式

```bash
seeagent
```

启动交互式 CLI 会话，可以连续对话。

### 单次任务模式

```bash
seeagent run "你的任务描述"
```

执行单个任务后退出。

### 指定 Agent 类型

```bash
seeagent run "写一个快排算法" --agent code-assistant
```

## 全局选项

| 选项 | 说明 |
|------|------|
| `--help, -h` | 显示帮助信息 |
| `--version, -v` | 显示版本 |
| `--config PATH` | 指定配置文件路径 |
| `--debug` | 启用调试模式 |
| `--verbose` | 详细输出 |

## 环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `ANTHROPIC_API_KEY` | Anthropic API Key | `sk-...` |
| `OPENAI_API_KEY` | OpenAI API Key | `sk-...` |
| `OPENCRAB_BASE_URL` | API 服务地址 | `http://localhost:8000` |

## 相关链接

- 上一页：[使用指南](../06-使用指南/README.md)
- 下一页：[API 使用](./API使用.md)
