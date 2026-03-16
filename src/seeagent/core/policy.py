"""
集中策略引擎 (Agent Harness: Policy Engine)

将散落在 tool_executor、filesystem handler、self_check 中的安全策略
集中到一个可配置、可审计的策略引擎中。

策略类型:
- ToolPolicy: 工具级策略（允许/禁止、参数限制、需要确认）
- ScopePolicy: 范围策略（文件路径限制、网络访问限制）
- EscalationPolicy: 升级策略（什么情况需要人工介入）
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PolicyDecision(str, Enum):
    """策略判定结果"""
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"  # 需要用户确认


@dataclass
class PolicyResult:
    """策略引擎判定结果"""
    decision: PolicyDecision
    reason: str = ""
    policy_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolPolicyRule:
    """工具策略规则"""
    tool_name: str  # 工具名或 "*" 通配
    decision: PolicyDecision = PolicyDecision.ALLOW
    dangerous_patterns: list[str] = field(default_factory=list)  # 参数中的危险模式
    blocked_patterns: list[str] = field(default_factory=list)  # 参数中的禁止模式
    require_confirmation: bool = False
    max_execution_time: int = 120  # 秒


@dataclass
class ScopePolicyRule:
    """范围策略规则"""
    allowed_paths: list[str] = field(default_factory=list)  # glob 模式
    blocked_paths: list[str] = field(default_factory=list)  # glob 模式
    blocked_commands: list[str] = field(default_factory=list)  # Shell 命令黑名单


@dataclass
class PolicyConfig:
    """策略配置"""
    tool_policies: list[ToolPolicyRule] = field(default_factory=list)
    scope_policy: ScopePolicyRule = field(default_factory=ScopePolicyRule)
    auto_confirm: bool = False  # 是否自动确认危险操作


# 默认危险 Shell 模式
_DEFAULT_DANGEROUS_SHELL_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"format\s+",
    r"del\s+/[sS]",
    r"rmdir\s+/[sS]",
    r"mkfs\.",
    r"dd\s+if=",
]

# 默认禁止的 Shell 命令（Windows 系统级）
_DEFAULT_BLOCKED_COMMANDS = [
    "reg", "regedit", "netsh", "schtasks", "sc",
    "wmic", "bcdedit", "shutdown", "taskkill",
]


def _default_policy_config() -> PolicyConfig:
    """创建默认策略配置"""
    return PolicyConfig(
        tool_policies=[
            ToolPolicyRule(
                tool_name="run_shell",
                require_confirmation=True,
                dangerous_patterns=_DEFAULT_DANGEROUS_SHELL_PATTERNS,
                blocked_patterns=[],
            ),
        ],
        scope_policy=ScopePolicyRule(
            blocked_commands=_DEFAULT_BLOCKED_COMMANDS,
        ),
    )


class PolicyEngine:
    """
    集中策略引擎。

    在工具执行前调用 assert_tool_allowed() 检查是否允许执行。
    所有判定都会记录到追踪系统。
    """

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self._config = config or _default_policy_config()
        self._audit_log: list[dict[str, Any]] = []

    @property
    def config(self) -> PolicyConfig:
        return self._config

    def load_from_yaml(self, path: str | Path) -> None:
        """从 YAML 文件加载策略"""
        path = Path(path)
        if not path.exists():
            logger.debug(f"[Policy] Config file not found: {path}")
            return

        try:
            import yaml
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data or not isinstance(data, dict):
                return

            # 解析 tool_policies（YAML 配置覆盖同名默认规则）
            yaml_policies: list[ToolPolicyRule] = []
            yaml_tool_names: set[str] = set()
            for tp in data.get("tool_policies", []):
                if isinstance(tp, dict) and "tool_name" in tp:
                    decision = PolicyDecision(tp.get("decision", "allow"))
                    yaml_policies.append(ToolPolicyRule(
                        tool_name=tp["tool_name"],
                        decision=decision,
                        dangerous_patterns=tp.get("dangerous_patterns", []),
                        blocked_patterns=tp.get("blocked_patterns", []),
                        require_confirmation=tp.get("require_confirmation", False),
                        max_execution_time=tp.get("max_execution_time", 120),
                    ))
                    yaml_tool_names.add(tp["tool_name"])
            # 保留未被 YAML 覆盖的默认规则，再追加 YAML 规则
            kept = [r for r in self._config.tool_policies if r.tool_name not in yaml_tool_names]
            self._config.tool_policies = kept + yaml_policies

            # 解析 scope_policy
            sp = data.get("scope_policy", {})
            if sp:
                self._config.scope_policy.allowed_paths = sp.get("allowed_paths", [])
                self._config.scope_policy.blocked_paths = sp.get("blocked_paths", [])
                self._config.scope_policy.blocked_commands = sp.get(
                    "blocked_commands", self._config.scope_policy.blocked_commands,
                )

            # auto_confirm
            self._config.auto_confirm = data.get("auto_confirm", False)

            logger.info(f"[Policy] Loaded policy from {path}")

        except ImportError:
            logger.debug("[Policy] PyYAML not available, skipping YAML config")
        except Exception as e:
            logger.warning(f"[Policy] Failed to load policy from {path}: {e}")

    def assert_tool_allowed(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
    ) -> PolicyResult:
        """
        检查工具调用是否被策略允许。

        Returns:
            PolicyResult with decision (ALLOW/DENY/CONFIRM)
        """
        params = params or {}

        # 检查工具策略
        for rule in self._config.tool_policies:
            if rule.tool_name != "*" and rule.tool_name != tool_name:
                continue

            # 检查禁止模式
            if rule.blocked_patterns:
                param_str = str(params)
                for pattern in rule.blocked_patterns:
                    if re.search(pattern, param_str, re.IGNORECASE):
                        result = PolicyResult(
                            decision=PolicyDecision.DENY,
                            reason=f"Blocked pattern '{pattern}' matched in {tool_name} params",
                            policy_name="ToolPolicy",
                        )
                        self._audit(tool_name, params, result)
                        return result

            # 检查危险模式
            if rule.dangerous_patterns and not self._config.auto_confirm:
                param_str = str(params)
                for pattern in rule.dangerous_patterns:
                    if re.search(pattern, param_str, re.IGNORECASE):
                        result = PolicyResult(
                            decision=PolicyDecision.CONFIRM,
                            reason=f"Dangerous pattern '{pattern}' in {tool_name}",
                            policy_name="ToolPolicy",
                        )
                        self._audit(tool_name, params, result)
                        return result

            if rule.decision == PolicyDecision.DENY:
                result = PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason=f"Tool '{tool_name}' is blocked by policy",
                    policy_name="ToolPolicy",
                )
                self._audit(tool_name, params, result)
                return result

            if rule.require_confirmation and not self._config.auto_confirm:
                result = PolicyResult(
                    decision=PolicyDecision.CONFIRM,
                    reason=f"Tool '{tool_name}' requires confirmation",
                    policy_name="ToolPolicy",
                )
                self._audit(tool_name, params, result)
                return result

        # Shell 命令特殊检查
        if tool_name == "run_shell":
            shell_result = self._check_shell_command(params)
            if shell_result:
                return shell_result

        # 文件路径检查
        if tool_name in ("read_file", "write_file", "edit_file", "search_replace", "delete_file"):
            path = params.get("path", "") or params.get("file_path", "")
            if path:
                path_result = self._check_path_policy(path, tool_name)
                if path_result:
                    return path_result

        result = PolicyResult(decision=PolicyDecision.ALLOW)
        return result

    def _check_shell_command(self, params: dict[str, Any]) -> PolicyResult | None:
        """检查 Shell 命令策略"""
        command = str(params.get("command", ""))
        if not command:
            return None

        # 检查禁止的命令
        cmd_parts = command.strip().split()
        if cmd_parts:
            base_cmd = cmd_parts[0].lower()
            # 移除路径前缀
            if "/" in base_cmd or "\\" in base_cmd:
                base_cmd = base_cmd.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            # 移除 .exe 后缀
            if base_cmd.endswith(".exe"):
                base_cmd = base_cmd[:-4]

            for blocked in self._config.scope_policy.blocked_commands:
                if base_cmd == blocked.lower():
                    result = PolicyResult(
                        decision=PolicyDecision.DENY,
                        reason=f"Command '{blocked}' is blocked by policy",
                        policy_name="ScopePolicy",
                    )
                    self._audit("run_shell", params, result)
                    return result

        return None

    def _check_path_policy(self, path: str, tool_name: str) -> PolicyResult | None:
        """检查文件路径策略"""
        # 规范化路径
        normalized = path.replace("\\", "/")

        # 检查禁止路径
        for blocked_pattern in self._config.scope_policy.blocked_paths:
            if fnmatch.fnmatch(normalized, blocked_pattern):
                result = PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason=f"Path '{path}' matches blocked pattern '{blocked_pattern}'",
                    policy_name="ScopePolicy",
                )
                self._audit(tool_name, {"path": path}, result)
                return result

        # 如果定义了 allowed_paths，检查是否在允许范围内
        if self._config.scope_policy.allowed_paths:
            is_allowed = any(
                fnmatch.fnmatch(normalized, pattern)
                for pattern in self._config.scope_policy.allowed_paths
            )
            if not is_allowed:
                result = PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason=f"Path '{path}' not in allowed paths",
                    policy_name="ScopePolicy",
                )
                self._audit(tool_name, {"path": path}, result)
                return result

        return None

    def _audit(self, tool_name: str, params: dict, result: PolicyResult) -> None:
        """记录审计日志"""
        import time
        entry = {
            "timestamp": time.time(),
            "tool_name": tool_name,
            "params_preview": str(params)[:200],
            "decision": result.decision.value,
            "reason": result.reason,
            "policy": result.policy_name,
        }
        self._audit_log.append(entry)

        # 限制审计日志大小
        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-500:]

        if result.decision != PolicyDecision.ALLOW:
            logger.info(
                f"[Policy] {result.decision.value}: {tool_name} — {result.reason}"
            )

        # Decision Trace
        try:
            from ..tracing.tracer import get_tracer
            tracer = get_tracer()
            tracer.record_decision(
                decision_type="policy_check",
                reasoning=result.reason,
                outcome=result.decision.value,
                tool_name=tool_name,
                policy=result.policy_name,
            )
        except Exception:
            pass

    def get_audit_log(self) -> list[dict[str, Any]]:
        """获取审计日志"""
        return list(self._audit_log)


# 全局策略引擎
_global_policy_engine: PolicyEngine | None = None


def get_policy_engine() -> PolicyEngine:
    """获取全局策略引擎实例"""
    global _global_policy_engine
    if _global_policy_engine is None:
        _global_policy_engine = PolicyEngine()
        try:
            from ..config import settings
            yaml_path = settings.identity_path / "POLICIES.yaml"
        except Exception:
            yaml_path = Path("identity/POLICIES.yaml")
        _global_policy_engine.load_from_yaml(yaml_path)
    return _global_policy_engine


def set_policy_engine(engine: PolicyEngine) -> None:
    """设置全局策略引擎实例"""
    global _global_policy_engine
    _global_policy_engine = engine
