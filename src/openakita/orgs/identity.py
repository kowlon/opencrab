"""
OrgIdentity — 节点身份解析与 MCP 配置管理

四级身份继承：
  Level 0: 零配置引用（全局 SOUL + AGENT + AgentProfile.custom_prompt）
  Level 1: 有 ROLE.md（全局 SOUL + AGENT + ROLE.md）
  Level 2: ROLE.md + 覆盖 AGENT.md
  Level 3: 完全独立身份（SOUL + AGENT + ROLE）

MCP 叠加继承：
  最终 MCP = 全局已启用 + AgentProfile 关联 + 节点额外 - 节点排除
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import EdgeType, OrgNode, Organization

logger = logging.getLogger(__name__)


@dataclass
class ResolvedIdentity:
    soul: str
    agent: str
    role: str
    level: int


class OrgIdentity:
    """Resolve per-node identity files with layered inheritance."""

    def __init__(self, org_dir: Path, global_identity_dir: Path | None = None) -> None:
        self._org_dir = org_dir
        self._nodes_dir = org_dir / "nodes"
        self._global_identity_dir = global_identity_dir

    def resolve(self, node: OrgNode, org: Organization) -> ResolvedIdentity:
        """Resolve the full identity for a node using 4-level inheritance."""
        node_identity_dir = self._nodes_dir / node.id / "identity"

        soul = self._read_file(node_identity_dir / "SOUL.md") or self._global_soul()
        agent = self._read_file(node_identity_dir / "AGENT.md") or self._global_agent()
        role = self._read_file(node_identity_dir / "ROLE.md")

        level = 3
        if role:
            if self._read_file(node_identity_dir / "AGENT.md"):
                if self._read_file(node_identity_dir / "SOUL.md"):
                    level = 3
                else:
                    level = 2
            else:
                level = 1
        else:
            level = 0
            if node.agent_profile_id:
                role = self._get_profile_prompt(node.agent_profile_id) or ""
            if not role and node.custom_prompt:
                role = node.custom_prompt
            if not role:
                role = self._auto_generate_role(node)

        return ResolvedIdentity(soul=soul, agent=agent, role=role, level=level)

    def build_org_context_prompt(
        self, node: OrgNode, org: Organization, identity: ResolvedIdentity,
        blackboard_summary: str = "",
        dept_summary: str = "",
        node_summary: str = "",
        pending_messages: str = "",
        policy_index: str = "",
    ) -> str:
        """Build the full organization context prompt for a node agent."""
        parent = org.get_parent(node.id)
        children = org.get_children(node.id)

        connected_peers: list[str] = []
        for e in org.edges:
            if e.edge_type != EdgeType.HIERARCHY:
                if e.source == node.id:
                    peer = org.get_node(e.target)
                    if peer:
                        connected_peers.append(f"{peer.role_title}({peer.department})")
                elif e.target == node.id:
                    peer = org.get_node(e.source)
                    if peer:
                        connected_peers.append(f"{peer.role_title}({peer.department})")

        org_chart = self._build_brief_org_chart(org)

        parts: list[str] = []

        if identity.soul:
            parts.append(identity.soul)
        if identity.agent:
            parts.append(identity.agent)

        parts.append(f"## 你的组织角色\n"
                     f"你在「{org.name}」中担任 **{node.role_title}**（{node.department}）。\n"
                     f"{identity.role}")

        parts.append(f"## 公司架构概览\n{org_chart}\n"
                     f"需要详情时用 org_get_org_chart 查看完整架构，不确定找谁时用 org_find_colleague 搜索。")

        rel_parts = []
        persona = org.user_persona
        if parent:
            rel_parts.append(f"- 直属上级：{parent.role_title}({parent.department})")
        elif persona and persona.label:
            desc = f"（{persona.description}）" if persona.description else "（用户）"
            rel_parts.append(f"- 直属上级：{persona.label}{desc}")
        if children:
            child_str = ", ".join(f"{c.role_title}" for c in children)
            rel_parts.append(f"- 直属下级：{child_str}")
        if connected_peers:
            rel_parts.append(f"- 协作伙伴：{', '.join(connected_peers)}")
        if rel_parts:
            parts.append("## 你的直接关系\n" + "\n".join(rel_parts))

        perm_parts = [
            f"- 委派任务：{'允许' if node.can_delegate else '不允许'}",
            f"- 上报问题：{'允许' if node.can_escalate else '不允许'}",
            f"- 申请扩编：{'允许' if node.can_request_scaling else '不允许'}",
            f"- 广播消息：{'允许（全组织）' if node.level == 0 else '允许（仅部门）'}",
        ]
        parts.append("## 你的权限\n" + "\n".join(perm_parts))

        parts.append(
            "## 制度与流程\n"
            "公司有完整的制度体系。当你不确定某个流程如何执行时：\n"
            "1. 先用 org_search_policy 搜索相关制度\n"
            "2. 用 org_read_policy 阅读具体制度内容\n"
            "3. 按制度规定执行\n"
            "不要猜测流程，查制度。重要决策前先查相关制度。"
        )
        if policy_index:
            parts.append(f"制度索引：\n{policy_index}")

        has_external = bool(node.external_tools)
        if has_external:
            from .tool_categories import expand_tool_categories, TOOL_CATEGORIES
            ext_names = expand_tool_categories(node.external_tools)
            cat_labels = [c for c in node.external_tools if c in TOOL_CATEGORIES]
            ext_desc = "、".join(cat_labels) if cat_labels else "、".join(sorted(ext_names)[:5])
            parts.append(
                "## 组织工具与行为约束\n"
                f"你拥有 org_* 组织协作工具和外部执行工具（{ext_desc}）。\n"
                "协作规则：\n"
                "- 与同事沟通、委派、汇报用 org_* 工具；搜索、写文件、制定计划等实际执行用外部工具\n"
                "- 外部工具得到的重要结果，用 org_write_blackboard 写入黑板共享给同事\n"
                "- 优先通过直接连线关系沟通（上下级、协作伙伴）\n"
                "- 非必要不跨级沟通\n"
                "- 回复要简洁，1-3 句话概括行动和结果即可\n\n"
                "任务交付流程：\n"
                "1. 收到任务（org_delegate_task）后开始工作\n"
                "2. 完成后用 **org_submit_deliverable** 提交交付物给委派人\n"
                "3. 委派人用 org_accept_deliverable（通过）或 org_reject_deliverable（打回）验收\n"
                "4. 被打回时根据反馈修改后重新提交\n"
                "5. 验收通过后任务完结\n\n"
                "缺少工具时，用 org_request_tools 向上级申请。"
            )
        else:
            parts.append(
                "## 组织工具与行为约束\n"
                "你**只能**使用 org_* 系列工具。不要调用 create_plan、write_file、read_file、"
                "run_shell、call_mcp_tool 等非组织工具，它们不可用。\n"
                "协作规则：\n"
                "- 优先通过直接连线关系沟通（上下级、协作伙伴）\n"
                "- 非必要不跨级沟通\n"
                "- 重要决策和方案写入 org_write_blackboard，写之前先 org_read_blackboard 检查避免重复\n"
                "- 回复要简洁，1-3 句话概括行动和结果即可\n\n"
                "任务交付流程：\n"
                "1. 收到任务（org_delegate_task）后开始工作\n"
                "2. 完成后用 **org_submit_deliverable** 提交交付物给委派人\n"
                "3. 委派人用 org_accept_deliverable（通过）或 org_reject_deliverable（打回）验收\n"
                "4. 被打回时根据反馈修改后重新提交\n"
                "5. 验收通过后任务完结\n\n"
                "缺少工具时，用 org_request_tools 向上级申请。"
            )

        if blackboard_summary:
            parts.append(f"## 当前组织简报\n{blackboard_summary}")
        if dept_summary:
            parts.append(f"## 部门近况\n{dept_summary}")
        if node_summary:
            parts.append(f"## 你的工作笔记\n{node_summary}")
        if pending_messages:
            parts.append(f"## 待处理消息\n{pending_messages}")

        return "\n\n".join(parts)

    def resolve_mcp_config(self, node: OrgNode) -> dict:
        """Resolve MCP configuration with overlay inheritance."""
        mcp_path = self._nodes_dir / node.id / "mcp_config.json"
        if not mcp_path.is_file():
            return {"mode": "inherit"}
        try:
            return json.loads(mcp_path.read_text(encoding="utf-8"))
        except Exception:
            return {"mode": "inherit"}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_brief_org_chart(self, org: Organization) -> str:
        """Build a compact org chart for prompt injection (~200-400 tokens)."""
        departments: dict[str, list[OrgNode]] = {}
        roots: list[OrgNode] = []
        for n in org.nodes:
            if n.level == 0:
                roots.append(n)
            dept = n.department or "未分配"
            departments.setdefault(dept, []).append(n)

        lines: list[str] = []
        for root in roots:
            lines.append(f"- {root.role_title} -- {root.role_goal[:30] if root.role_goal else ''}")
            for dept_name, members in sorted(departments.items()):
                dept_members = [m for m in members if m.id != root.id]
                if not dept_members:
                    continue
                member_str = ", ".join(
                    f"{m.role_title}" for m in dept_members[:6]
                )
                if len(dept_members) > 6:
                    member_str += f" 等{len(dept_members)}人"
                lines.append(f"  - {dept_name}: {member_str}")

        return "\n".join(lines) if lines else "(组织架构为空)"

    def _global_soul(self) -> str:
        if self._global_identity_dir:
            return self._read_file(self._global_identity_dir / "SOUL.md") or ""
        return ""

    def _global_agent(self) -> str:
        if self._global_identity_dir:
            core = self._read_file(self._global_identity_dir / "agent.core.md")
            if core:
                return core
            return self._read_file(self._global_identity_dir / "AGENT.md") or ""
        return ""

    def _get_profile_prompt(self, profile_id: str) -> str | None:
        try:
            from openakita.main import _orchestrator
            if _orchestrator and hasattr(_orchestrator, "_profile_store"):
                profile = _orchestrator._profile_store.get(profile_id)
                return profile.custom_prompt if profile else None
        except (ImportError, AttributeError):
            pass
        try:
            from openakita.agents.profile import ProfileStore
            from openakita.config import settings
            store = ProfileStore(settings.data_dir / "agents")
            profile = store.get(profile_id)
            return profile.custom_prompt if profile else None
        except Exception:
            return None

    def _auto_generate_role(self, node: OrgNode) -> str:
        parts = [f"你是{node.role_title}。"]
        if node.role_goal:
            parts.append(f"目标：{node.role_goal}。")
        if node.role_backstory:
            parts.append(f"背景：{node.role_backstory}。")
        return "".join(parts)

    @staticmethod
    def _read_file(path: Path) -> str | None:
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8").strip()
                return content if content else None
            except Exception:
                return None
        return None
