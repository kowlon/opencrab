"""
OrgRuntime — 组织运行时引擎

负责组织生命周期管理、节点 Agent 按需激活、
任务调度、消息分发、WebSocket 事件广播。
集成心跳、定时任务、扩编、收件箱、通知、制度管理等子系统。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .blackboard import OrgBlackboard
from .event_store import OrgEventStore
from .identity import OrgIdentity, ResolvedIdentity
from .messenger import OrgMessenger
from .models import (
    MsgType,
    NodeStatus,
    OrgMessage,
    OrgNode,
    OrgStatus,
    Organization,
    _now_iso,
)
from .tool_handler import OrgToolHandler
from .tools import ORG_NODE_TOOLS

if TYPE_CHECKING:
    from .manager import OrgManager
    from .heartbeat import OrgHeartbeat
    from .node_scheduler import OrgNodeScheduler
    from .scaler import OrgScaler
    from .inbox import OrgInbox
    from .notifier import OrgNotifier
    from .policies import OrgPolicies
    from .reporter import OrgReporter

logger = logging.getLogger(__name__)

AGENT_CACHE_MAX = 10
AGENT_CACHE_TTL = 600


class _CachedAgent:
    """Wrapper for a cached Agent instance with TTL tracking."""
    __slots__ = ("agent", "last_used", "session_id")

    def __init__(self, agent: Any, session_id: str):
        self.agent = agent
        self.session_id = session_id
        self.last_used = time.monotonic()

    def touch(self) -> None:
        self.last_used = time.monotonic()

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.last_used) > AGENT_CACHE_TTL


class OrgRuntime:
    """Core runtime engine for organization orchestration."""

    def __init__(self, manager: OrgManager) -> None:
        self._manager = manager
        self._messengers: dict[str, OrgMessenger] = {}
        self._blackboards: dict[str, OrgBlackboard] = {}
        self._event_stores: dict[str, OrgEventStore] = {}
        self._identities: dict[str, OrgIdentity] = {}
        self._policies: dict[str, OrgPolicies] = {}
        self._tool_handler = OrgToolHandler(self)

        from .heartbeat import OrgHeartbeat
        from .node_scheduler import OrgNodeScheduler
        from .scaler import OrgScaler
        from .inbox import OrgInbox
        from .notifier import OrgNotifier

        self._heartbeat = OrgHeartbeat(self)
        self._scheduler = OrgNodeScheduler(self)
        self._scaler = OrgScaler(self)
        self._inbox = OrgInbox(self)
        self._notifier = OrgNotifier(self)

        from .reporter import OrgReporter
        self._reporter = OrgReporter(self)

        self._agent_cache: OrderedDict[str, _CachedAgent] = OrderedDict()

        self._running_tasks: dict[str, dict[str, asyncio.Task]] = {}

        self._active_orgs: dict[str, Organization] = {}

        self._cascade_depth: dict[str, int] = {}
        self.max_cascade_depth: int = 5
        self.max_concurrent_per_node: int = 2

        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialize runtime, recover active organizations."""
        if self._started:
            return
        self._started = True
        logger.info("[OrgRuntime] Starting...")

        for info in self._manager.list_orgs(include_archived=False):
            org = self._manager.get(info["id"])
            if org and org.status in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                self._activate_org(org)
                await self._heartbeat.start_for_org(org)
                await self._scheduler.start_for_org(org)

                await self._recover_pending_tasks(org)
                logger.info(f"[OrgRuntime] Recovered org: {org.name} ({org.status.value})")

        logger.info("[OrgRuntime] Started.")

    async def shutdown(self) -> None:
        """Gracefully shut down all active organizations."""
        logger.info("[OrgRuntime] Shutting down...")

        await self._heartbeat.stop_all()
        await self._scheduler.stop_all()

        for org_id, tasks in list(self._running_tasks.items()):
            for node_id, task in tasks.items():
                if not task.done():
                    task.cancel()
            tasks.clear()

        for key, cached in list(self._agent_cache.items()):
            try:
                if hasattr(cached.agent, "shutdown"):
                    await cached.agent.shutdown()
            except Exception:
                pass
        self._agent_cache.clear()

        for org_id in list(self._active_orgs.keys()):
            self._save_state(org_id)
            messenger = self._messengers.get(org_id)
            if messenger:
                await messenger.stop_background_tasks()

        self._active_orgs.clear()
        self._messengers.clear()
        self._blackboards.clear()
        self._event_stores.clear()
        self._identities.clear()
        self._policies.clear()

        self._started = False
        logger.info("[OrgRuntime] Shutdown complete.")

    # ------------------------------------------------------------------
    # Lifecycle state machine
    # ------------------------------------------------------------------

    _VALID_TRANSITIONS: dict[OrgStatus, set[OrgStatus]] = {
        OrgStatus.DORMANT: {OrgStatus.ACTIVE},
        OrgStatus.ACTIVE: {OrgStatus.RUNNING, OrgStatus.PAUSED, OrgStatus.DORMANT, OrgStatus.ARCHIVED},
        OrgStatus.RUNNING: {OrgStatus.ACTIVE, OrgStatus.PAUSED, OrgStatus.DORMANT},
        OrgStatus.PAUSED: {OrgStatus.ACTIVE, OrgStatus.DORMANT, OrgStatus.ARCHIVED},
        OrgStatus.ARCHIVED: set(),
    }

    def _check_transition(self, org: Organization, target: OrgStatus) -> None:
        valid = self._VALID_TRANSITIONS.get(org.status, set())
        if target not in valid:
            raise ValueError(
                f"无效状态转换: {org.status.value} -> {target.value} "
                f"(允许的目标: {', '.join(s.value for s in valid) or '无'})"
            )

    # ------------------------------------------------------------------
    # Organization lifecycle
    # ------------------------------------------------------------------

    async def start_org(self, org_id: str) -> Organization:
        """Start an organization, transitioning it to ACTIVE."""
        org = self._manager.get(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")

        self._check_transition(org, OrgStatus.ACTIVE)

        org.status = OrgStatus.ACTIVE
        org.updated_at = _now_iso()
        self._manager.update(org_id, {"status": org.status.value})
        self._activate_org(org)

        await self._heartbeat.start_for_org(org)
        await self._scheduler.start_for_org(org)

        policies = self.get_policies(org_id)
        if policies:
            tpl_data = None
            try:
                tpl_data = getattr(org, "_source_template", None)
            except Exception:
                pass
            existing = policies.list_policies()
            if not existing:
                policies.install_default_policies("default")

        self.get_event_store(org_id).emit("org_started", "system")
        await self._broadcast_ws("org:status_change", {
            "org_id": org_id, "status": "active"
        })

        return org

    async def stop_org(self, org_id: str) -> Organization:
        """Stop an organization."""
        org = self._active_orgs.get(org_id) or self._manager.get(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")

        self._check_transition(org, OrgStatus.DORMANT)

        await self._heartbeat.stop_for_org(org_id)
        await self._scheduler.stop_for_org(org_id)

        org_tasks = self._running_tasks.pop(org_id, {})
        for node_id, task in org_tasks.items():
            if not task.done():
                task.cancel()
        for node_id, task in org_tasks.items():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        org.status = OrgStatus.DORMANT
        org.updated_at = _now_iso()
        self._manager.update(org_id, {"status": org.status.value})
        await self._deactivate_org(org_id)

        self.get_event_store(org_id).emit("org_stopped", "system")
        await self._broadcast_ws("org:status_change", {
            "org_id": org_id, "status": "dormant"
        })

        return org

    async def pause_org(self, org_id: str) -> Organization:
        org = self._active_orgs.get(org_id) or self._manager.get(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")
        self._check_transition(org, OrgStatus.PAUSED)
        org.status = OrgStatus.PAUSED
        org.updated_at = _now_iso()
        self._manager.update(org_id, {"status": org.status.value})
        self.get_event_store(org_id).emit("org_paused", "system")
        return org

    async def resume_org(self, org_id: str) -> Organization:
        org = self._active_orgs.get(org_id) or self._manager.get(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")
        self._check_transition(org, OrgStatus.ACTIVE)
        org.status = OrgStatus.ACTIVE
        org.updated_at = _now_iso()
        self._manager.update(org_id, {"status": org.status.value})
        if org_id not in self._active_orgs:
            self._activate_org(org)
        self.get_event_store(org_id).emit("org_resumed", "system")
        return org

    # ------------------------------------------------------------------
    # User commands
    # ------------------------------------------------------------------

    async def send_command(
        self, org_id: str, target_node_id: str | None, content: str
    ) -> dict:
        """Send a user command to an organization node."""
        org = self._active_orgs.get(org_id)
        if not org:
            org = self._manager.get(org_id)
            if not org:
                raise ValueError(f"Organization not found: {org_id}")
            if org.status == OrgStatus.PAUSED:
                org = await self.resume_org(org_id)
            elif org.status not in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
                org = await self.start_org(org_id)
        elif org.status == OrgStatus.PAUSED:
            org = await self.resume_org(org_id)

        if not target_node_id:
            roots = org.get_root_nodes()
            if not roots:
                raise ValueError("Organization has no root nodes")
            target_node_id = roots[0].id

        target = org.get_node(target_node_id)
        if not target:
            raise ValueError(f"Node not found: {target_node_id}")

        self.get_event_store(org_id).emit(
            "user_command", "user",
            {"target": target_node_id, "content": content[:200]},
        )

        persona = org.user_persona
        if persona and persona.label:
            tagged_content = f"[来自 {persona.label}] {content}"
        else:
            tagged_content = content

        result = await self._activate_and_run(org, target, tagged_content)
        return result

    # ------------------------------------------------------------------
    # Node activation
    # ------------------------------------------------------------------

    async def _activate_and_run(
        self, org: Organization, node: OrgNode, prompt: str
    ) -> dict:
        """Activate a node agent and run a task."""
        if node.status == NodeStatus.FROZEN:
            return {"error": f"{node.role_title} 已被冻结，无法执行任务"}
        if node.status == NodeStatus.OFFLINE:
            return {"error": f"{node.role_title} 已下线"}

        cache_key = f"{org.id}:{node.id}"
        agent = await self._get_or_create_agent(org, node)

        node.status = NodeStatus.BUSY
        self._save_org(org)

        self.get_event_store(org.id).emit(
            "node_activated", node.id, {"prompt": prompt[:200]},
        )
        await self._broadcast_ws("org:node_status", {
            "org_id": org.id, "node_id": node.id, "status": "busy",
        })

        try:
            session_id = f"org:{org.id}:node:{node.id}"

            result_text = await self._run_agent_task(agent, prompt, session_id, org, node)

            node.status = NodeStatus.IDLE
            org.total_tasks_completed += 1
            self._save_org(org)

            self.get_event_store(org.id).emit(
                "task_completed", node.id,
                {"result_preview": result_text[:200] if result_text else ""},
            )
            await self._broadcast_ws("org:task_complete", {
                "org_id": org.id, "node_id": node.id,
            })

            return {"node_id": node.id, "result": result_text}

        except Exception as e:
            logger.error(f"[OrgRuntime] Task error on {node.id}: {e}")
            node.status = NodeStatus.ERROR
            self._save_org(org)
            self.get_event_store(org.id).emit(
                "task_failed", node.id, {"error": str(e)[:200]},
            )
            return {"node_id": node.id, "error": str(e)}

    async def _run_agent_task(
        self, agent: Any, prompt: str, session_id: str,
        org: Organization, node: OrgNode,
    ) -> str:
        """Run a single agent task with timeout. Returns the agent's response text."""
        timeout = node.timeout_s if node.timeout_s > 0 else 300
        try:
            response = await asyncio.wait_for(
                agent.chat(prompt, session_id=session_id),
                timeout=timeout,
            )
            return response or ""
        except asyncio.TimeoutError:
            logger.warning(f"[OrgRuntime] Task timeout ({timeout}s) for {node.id}")
            return f"(任务超时，超过 {timeout} 秒限制)"
        except asyncio.CancelledError:
            logger.info(f"[OrgRuntime] Task cancelled for {node.id}")
            return "(任务已取消)"
        except Exception as e:
            logger.error(f"[OrgRuntime] Agent task error: {e}")
            raise

    async def _get_or_create_agent(self, org: Organization, node: OrgNode) -> Any:
        """Get cached agent or create a new one."""
        cache_key = f"{org.id}:{node.id}"

        if cache_key in self._agent_cache:
            cached = self._agent_cache[cache_key]
            if not cached.expired:
                cached.touch()
                self._agent_cache.move_to_end(cache_key)
                return cached.agent

        self._evict_expired_agents()

        agent = await self._create_node_agent(org, node)

        session_id = f"org:{org.id}:node:{node.id}"
        self._agent_cache[cache_key] = _CachedAgent(agent, session_id)

        if len(self._agent_cache) > AGENT_CACHE_MAX:
            oldest_key, oldest = self._agent_cache.popitem(last=False)
            logger.debug(f"[OrgRuntime] Evicted agent cache: {oldest_key}")

        return agent

    async def _create_node_agent(self, org: Organization, node: OrgNode) -> Any:
        """Create a new Agent instance for a node."""
        from openakita.agents.factory import AgentFactory

        factory = AgentFactory()

        identity = self._get_identity(org.id)
        resolved = identity.resolve(node, org)

        bb = self.get_blackboard(org.id)
        blackboard_summary = bb.get_org_summary() if bb else ""
        dept_summary = bb.get_dept_summary(node.department) if bb and node.department else ""
        memory_owner = node.clone_source if node.is_clone and node.clone_source else node.id
        node_summary = bb.get_node_summary(memory_owner) if bb else ""

        org_context_prompt = identity.build_org_context_prompt(
            node, org, resolved,
            blackboard_summary=blackboard_summary,
            dept_summary=dept_summary,
            node_summary=node_summary,
        )

        profile = self._build_profile_for_node(node, org_context_prompt)

        agent = await factory.create(profile)

        from .tool_categories import expand_tool_categories

        _KEEP = frozenset({"get_tool_info"})
        allowed_external = expand_tool_categories(node.external_tools)

        if hasattr(agent, "tool_catalog"):
            for tool_def in ORG_NODE_TOOLS:
                agent.tool_catalog.add_tool(tool_def)
            non_org = [
                n for n in agent.tool_catalog.list_tools()
                if not n.startswith("org_") and n not in _KEEP
                and n not in allowed_external
            ]
            for n in non_org:
                agent.tool_catalog.remove_tool(n)

        if hasattr(agent, "_tools"):
            seen: set[str] = set()
            filtered: list[dict] = []
            for t in agent._tools:
                name = t.get("name", "")
                if (name.startswith("org_") or name in _KEEP
                        or name in allowed_external) and name not in seen:
                    seen.add(name)
                    filtered.append(t)
            for t in ORG_NODE_TOOLS:
                name = t["name"]
                if name not in seen:
                    seen.add(name)
                    filtered.append(t)
            agent._tools = filtered

        _MCP_TOOL_NAMES = {"call_mcp_tool", "list_mcp_servers", "get_mcp_instructions"}
        if node.mcp_servers and (
            "mcp" in (node.external_tools or []) or _MCP_TOOL_NAMES & allowed_external
        ):
            self._connect_node_mcp_servers(agent, node.mcp_servers)

        self._override_system_prompt_for_org(agent, org_context_prompt)

        agent._org_context = {
            "org_id": org.id,
            "node_id": node.id,
            "tool_handler": self._tool_handler,
        }

        self._register_org_tool_handler(agent, org.id, node.id)

        return agent

    @staticmethod
    def _override_system_prompt_for_org(agent: Any, org_context: str) -> None:
        """Replace the agent's bloated system prompt with an org-focused one."""
        org_tool_lines: list[str] = []
        ext_tool_lines: list[str] = []

        for t in getattr(agent, "_tools", []):
            name = t.get("name", "")
            desc = t.get("description", "")
            schema = t.get("input_schema", {})
            required = schema.get("required", [])
            props = schema.get("properties", {})
            params = ", ".join(
                f"{p}" + (" *" if p in required else "")
                for p in props
            )
            line = f"- **{name}**({params}): {desc}"
            if name.startswith("org_") or name == "get_tool_info":
                org_tool_lines.append(line)
            else:
                ext_tool_lines.append(line)

        org_section = "\n".join(org_tool_lines) if org_tool_lines else "(无)"
        has_external = bool(ext_tool_lines)

        parts = [org_context]

        parts.append(f"## 组织协作工具（org_*）\n\n{org_section}")

        if has_external:
            ext_section = "\n".join(ext_tool_lines)
            parts.append(f"## 外部执行工具\n\n{ext_section}")

        parts.append(
            "参数带 * 为必填。用 get_tool_info(tool_name) 可查看工具完整参数。"
        )

        if has_external:
            parts.append(
                "## 行为准则\n\n"
                "1. **协作用 org_* 工具，执行用外部工具**。与同事沟通、委派、汇报用 org_* 工具；"
                "搜索信息、写文件、制定计划等实际执行工作用外部工具。\n"
                "2. **执行结果要共享**。用外部工具得到的重要结果，用 org_write_blackboard 写入黑板，方便同事查阅。\n"
                "3. **简洁回复**。完成工具调用后，用 1-2 句话总结结果即可。\n"
                "4. **先查再做**。不确定找谁时用 org_find_colleague；不确定流程时用 org_search_policy。\n"
                "5. **不要重复写入**。写黑板前先用 org_read_blackboard 检查是否已有相似内容。\n"
                "6. **任务交付流程**。收到任务后完成工作，用 org_submit_deliverable 提交给委派人验收。被打回时修改后重新提交。\n"
                "7. **缺少工具时申请**。如果任务需要你没有的工具，用 org_request_tools 向上级申请。"
            )
        else:
            parts.append(
                "## 行为准则\n\n"
                "1. **只使用上述 org_* 工具**。不要调用 create_plan、write_file、read_file、run_shell 等非组织工具。\n"
                "2. **简洁回复**。完成工具调用后，用 1-2 句话总结结果即可。\n"
                "3. **先查再做**。不确定找谁时用 org_find_colleague；不确定流程时用 org_search_policy。\n"
                "4. **重要信息写黑板**。决策、方案、进度等用 org_write_blackboard 记录，方便同事查阅。\n"
                "5. **不要重复写入**。写黑板前先用 org_read_blackboard 检查是否已有相似内容。\n"
                "6. **任务交付流程**。收到任务后完成工作，用 org_submit_deliverable 提交给委派人验收。被打回时修改后重新提交。\n"
                "7. **缺少工具时申请**。如果任务需要你没有的工具，用 org_request_tools 向上级申请。"
            )

        lean_prompt = "\n\n".join(parts)

        ctx = getattr(agent, "_context", None)
        if ctx and hasattr(ctx, "system"):
            ctx.system = lean_prompt

    def _build_profile_for_node(self, node: OrgNode, org_prompt: str) -> Any:
        """Build an AgentProfile-like object for factory.create()."""
        from openakita.agents.profile import AgentProfile, SkillsMode

        if node.agent_profile_id:
            try:
                base = self._get_shared_profile(node.agent_profile_id)
                if base:
                    profile = AgentProfile(
                        id=f"org_node_{node.id}",
                        name=node.role_title,
                        icon=base.icon,
                        custom_prompt=org_prompt,
                        skills=node.skills if node.skills else base.skills,
                        skills_mode=SkillsMode(node.skills_mode) if node.skills_mode != "all" else base.skills_mode,
                        preferred_endpoint=node.preferred_endpoint or base.preferred_endpoint,
                    )
                    return profile
            except Exception as e:
                logger.warning(f"[OrgRuntime] Failed to load profile {node.agent_profile_id}: {e}")

        return AgentProfile(
            id=f"org_node_{node.id}",
            name=node.role_title,
            custom_prompt=org_prompt,
            skills=node.skills,
            skills_mode=SkillsMode(node.skills_mode) if node.skills_mode != "all" else SkillsMode.ALL,
            preferred_endpoint=node.preferred_endpoint,
        )

    def _get_shared_profile(self, profile_id: str) -> Any:
        """Get an AgentProfile from the shared ProfileStore via orchestrator."""
        try:
            from openakita.main import _orchestrator
            if _orchestrator and hasattr(_orchestrator, "_profile_store"):
                return _orchestrator._profile_store.get(profile_id)
        except (ImportError, AttributeError):
            pass
        try:
            from openakita.agents.profile import ProfileStore
            from openakita.config import settings
            store = ProfileStore(settings.data_dir / "agents")
            return store.get(profile_id)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Message handler (called by messenger when a node receives a message)
    # ------------------------------------------------------------------

    async def _on_node_message(self, org_id: str, node_id: str, msg: OrgMessage) -> None:
        """Handle an incoming message for a node — activate and process."""
        org = self._active_orgs.get(org_id) or self._manager.get(org_id)
        if not org:
            return
        node = org.get_node(node_id)
        if not node or node.status in (NodeStatus.FROZEN, NodeStatus.OFFLINE):
            return

        depth = msg.metadata.get("_cascade_depth", 0)
        if depth >= self.max_cascade_depth:
            logger.warning(
                f"[OrgRuntime] Cascade depth limit ({self.max_cascade_depth}) "
                f"reached for {node_id}, queuing message instead of activating"
            )
            self.get_event_store(org_id).emit(
                "cascade_limited", node_id,
                {"depth": depth, "msg_id": msg.id, "from": msg.from_node},
            )
            return

        active_key = f"{org_id}:{node_id}"
        running = self._running_tasks.get(org_id, {})
        active_count = sum(
            1 for k, t in running.items()
            if k.startswith(f"{node_id}:") and not t.done()
        )

        messenger = self.get_messenger(org_id)
        pending = messenger.get_pending_count(node_id) if messenger else 0

        if active_count >= self.max_concurrent_per_node:
            target_clone = self._try_route_to_clone(org, node, msg, pending)
            if target_clone:
                task_prompt = self._format_incoming_message(msg)
                self._cascade_depth[f"{org_id}:{target_clone.id}"] = depth
                await self._activate_and_run(org, target_clone, task_prompt)
                return

            if node.auto_clone_enabled and pending >= node.auto_clone_threshold:
                new_clone = self._scaler.maybe_auto_clone(org_id, node_id, pending)
                if new_clone:
                    self._register_clone_in_messenger(org_id, new_clone)
                    task_prompt = self._format_incoming_message(msg)
                    self._cascade_depth[f"{org_id}:{new_clone.id}"] = depth
                    await self._activate_and_run(org, new_clone, task_prompt)
                    return

            logger.info(
                f"[OrgRuntime] Node {node_id} already has {active_count} "
                f"active tasks, message {msg.id} stays in mailbox"
            )
            return

        self._cascade_depth[active_key] = depth

        task_prompt = self._format_incoming_message(msg)
        await self._activate_and_run(org, node, task_prompt)

    def _try_route_to_clone(
        self, org: Organization, node: OrgNode, msg: OrgMessage, pending: int
    ) -> OrgNode | None:
        """Try to find an available clone for this task."""
        clones = [n for n in org.nodes if n.clone_source == node.id
                   and n.status not in (NodeStatus.FROZEN, NodeStatus.OFFLINE)]
        if not clones:
            return None

        chain_id = msg.metadata.get("task_chain_id")
        if chain_id:
            messenger = self.get_messenger(org.id)
            if messenger:
                affinity = messenger.get_task_affinity(chain_id)
                if affinity:
                    for c in clones:
                        if c.id == affinity and c.status == NodeStatus.IDLE:
                            return c

        idle_clones = [c for c in clones if c.status == NodeStatus.IDLE]
        if idle_clones:
            return idle_clones[0]

        return None

    def _register_clone_in_messenger(self, org_id: str, clone: OrgNode) -> None:
        """Register a newly created clone in the messenger system."""
        messenger = self.get_messenger(org_id)
        if not messenger:
            return
        org = self._active_orgs.get(org_id)
        if org:
            messenger.update_org(org)

        async def _handler(msg: OrgMessage, _nid=clone.id, _oid=org_id):
            task = asyncio.create_task(self._on_node_message(_oid, _nid, msg))
            self._running_tasks.setdefault(_oid, {})[f"{_nid}:{msg.id}"] = task
        messenger.register_handler(clone.id, _handler)

    def _format_incoming_message(self, msg: OrgMessage) -> str:
        """Format an OrgMessage into a prompt for the receiving agent."""
        type_labels = {
            MsgType.TASK_ASSIGN: "收到任务",
            MsgType.TASK_RESULT: "收到任务结果",
            MsgType.TASK_DELIVERED: "收到任务交付",
            MsgType.TASK_ACCEPTED: "任务已通过验收",
            MsgType.TASK_REJECTED: "任务被打回",
            MsgType.REPORT: "收到汇报",
            MsgType.QUESTION: "收到提问",
            MsgType.ANSWER: "收到回答",
            MsgType.ESCALATE: "收到上报",
            MsgType.BROADCAST: "收到组织公告",
            MsgType.DEPT_BROADCAST: "收到部门公告",
            MsgType.FEEDBACK: "收到反馈",
            MsgType.HANDSHAKE: "收到握手请求",
        }
        label = type_labels.get(msg.msg_type, "收到消息")
        prefix = f"[{label}] 来自 {msg.from_node}"
        if msg.reply_to:
            prefix += f" (回复消息 {msg.reply_to})"

        chain_id = msg.metadata.get("task_chain_id", "")
        if chain_id:
            prefix += f" [任务链: {chain_id[:12]}]"

        extra = ""
        if msg.msg_type == MsgType.TASK_DELIVERED:
            deliverable = msg.metadata.get("deliverable", "")
            summary = msg.metadata.get("summary", "")
            if deliverable:
                extra = f"\n交付内容: {deliverable}"
            if summary:
                extra += f"\n工作简述: {summary}"
            extra += "\n请用 org_accept_deliverable 或 org_reject_deliverable 进行验收。"
        elif msg.msg_type == MsgType.TASK_REJECTED:
            reason = msg.metadata.get("rejection_reason", "")
            if reason:
                extra = f"\n打回原因: {reason}\n请根据反馈修改后重新用 org_submit_deliverable 提交。"
        elif msg.msg_type == MsgType.TASK_ASSIGN:
            extra = f"\n完成后请用 org_submit_deliverable 提交交付物，task_chain_id={chain_id}"

        return f"{prefix}:\n{msg.content}{extra}"

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_org(self, org_id: str) -> Organization | None:
        return self._active_orgs.get(org_id) or self._manager.get(org_id)

    def get_messenger(self, org_id: str) -> OrgMessenger | None:
        return self._messengers.get(org_id)

    def get_blackboard(self, org_id: str) -> OrgBlackboard | None:
        return self._blackboards.get(org_id)

    def get_event_store(self, org_id: str) -> OrgEventStore:
        if org_id not in self._event_stores:
            org_dir = self._manager._org_dir(org_id)
            self._event_stores[org_id] = OrgEventStore(org_dir, org_id)
        return self._event_stores[org_id]

    def get_inbox(self, org_id: str) -> OrgInbox:
        return self._inbox

    def get_scaler(self) -> OrgScaler:
        return self._scaler

    def get_heartbeat(self) -> OrgHeartbeat:
        return self._heartbeat

    def get_scheduler(self) -> OrgNodeScheduler:
        return self._scheduler

    def get_notifier(self) -> OrgNotifier:
        return self._notifier

    def get_reporter(self) -> OrgReporter:
        return self._reporter

    def get_policies(self, org_id: str) -> OrgPolicies:
        if org_id not in self._policies:
            from .policies import OrgPolicies as _P
            org_dir = self._manager._org_dir(org_id)
            self._policies[org_id] = _P(org_dir)
        return self._policies[org_id]

    def _get_identity(self, org_id: str) -> OrgIdentity:
        if org_id not in self._identities:
            org_dir = self._manager._org_dir(org_id)
            global_identity = None
            try:
                from openakita.config import settings
                global_identity = Path(settings.project_root) / "identity"
            except Exception:
                pass
            self._identities[org_id] = OrgIdentity(org_dir, global_identity)
        return self._identities[org_id]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _activate_org(self, org: Organization) -> None:
        """Set up runtime infrastructure for an organization."""
        org_dir = self._manager._org_dir(org.id)
        self._active_orgs[org.id] = org
        self._messengers[org.id] = OrgMessenger(org, org_dir)
        self._blackboards[org.id] = OrgBlackboard(org_dir, org.id)
        self._event_stores[org.id] = OrgEventStore(org_dir, org.id)

        messenger = self._messengers[org.id]
        for node in org.nodes:
            async def _handler(msg: OrgMessage, _nid=node.id, _oid=org.id):
                task = asyncio.create_task(self._on_node_message(_oid, _nid, msg))
                self._running_tasks.setdefault(_oid, {})[f"{_nid}:{msg.id}"] = task
            messenger.register_handler(node.id, _handler)

        async def _on_deadlock(cycles: list[list[str]], _oid=org.id) -> None:
            es = self.get_event_store(_oid)
            for cycle in cycles:
                es.emit("conflict_detected", "system", {
                    "type": "deadlock", "cycle": cycle,
                })
            inbox = self.get_inbox(_oid)
            inbox.push_warning(
                _oid, "system",
                title="检测到死锁",
                body=f"以下节点间存在循环等待: {cycles}",
            )
        messenger.set_deadlock_handler(_on_deadlock)

        task = asyncio.ensure_future(messenger.start_background_tasks())
        task.add_done_callback(
            lambda t: logger.error(f"[OrgRuntime] Messenger bg tasks failed: {t.exception()}")
            if t.done() and not t.cancelled() and t.exception() else None
        )

    async def _deactivate_org(self, org_id: str) -> None:
        messenger = self._messengers.get(org_id)
        if messenger:
            try:
                await messenger.stop_background_tasks()
            except Exception as e:
                logger.error(f"[OrgRuntime] Messenger stop failed for {org_id}: {e}")
        self._active_orgs.pop(org_id, None)
        self._messengers.pop(org_id, None)
        self._blackboards.pop(org_id, None)
        self._event_stores.pop(org_id, None)
        self._identities.pop(org_id, None)
        self._policies.pop(org_id, None)

        keys_to_remove = [k for k in self._agent_cache if k.startswith(f"{org_id}:")]
        for k in keys_to_remove:
            self._agent_cache.pop(k, None)

    def _save_org(self, org: Organization) -> None:
        org.updated_at = _now_iso()
        self._manager.update(org.id, org.to_dict())
        self._manager.invalidate_cache(org.id)

    def _save_state(self, org_id: str) -> None:
        org = self._active_orgs.get(org_id)
        if not org:
            return
        state = {
            "status": org.status.value,
            "saved_at": _now_iso(),
            "node_statuses": {n.id: n.status.value for n in org.nodes},
        }
        self._manager.save_state(org_id, state)

    async def _recover_pending_tasks(self, org: Organization) -> None:
        """Check for tasks that were interrupted by a restart and reset node states."""
        saved = self._manager.load_state(org.id)
        if not saved:
            return

        recovered_count = 0
        saved_node_statuses = saved.get("node_statuses", {})

        for node in org.nodes:
            prev_status = saved_node_statuses.get(node.id)
            if prev_status in ("busy", "waiting"):
                es = self.get_event_store(org.id)
                pending = es.get_last_pending(node.id)

                node.status = NodeStatus.IDLE
                recovered_count += 1

                es.emit("node_recovered", node.id, {
                    "previous_status": prev_status,
                    "had_pending": bool(pending),
                })

                if pending:
                    logger.info(
                        f"[OrgRuntime] Node {node.role_title} was {prev_status}, "
                        f"reset to idle (pending event: {pending.get('event_type', '?')})"
                    )

        if recovered_count > 0:
            self._save_org(org)
            logger.info(f"[OrgRuntime] Recovered {recovered_count} nodes for {org.name}")

    def _evict_expired_agents(self) -> None:
        expired = [k for k, v in self._agent_cache.items() if v.expired]
        for k in expired:
            self._agent_cache.pop(k, None)

    def evict_node_agent(self, org_id: str, node_id: str) -> None:
        """Evict a specific node's cached agent so it gets rebuilt with fresh config."""
        cache_key = f"{org_id}:{node_id}"
        self._agent_cache.pop(cache_key, None)

    @staticmethod
    def _connect_node_mcp_servers(agent: Any, mcp_servers: list[str]) -> None:
        """Best-effort connect MCP servers listed on the node."""
        try:
            client = getattr(agent, "mcp_client", None)
            if not client:
                return
            for server_name in mcp_servers:
                if hasattr(client, "connect"):
                    import asyncio
                    try:
                        loop = asyncio.get_running_loop()
                        task = loop.create_task(client.connect(server_name))
                        task.add_done_callback(
                            lambda t, s=server_name: (
                                logger.warning(f"[OrgRuntime] MCP connect '{s}' failed: {t.exception()}")
                                if t.exception() else None
                            )
                        )
                    except RuntimeError:
                        pass
        except Exception as e:
            logger.debug(f"[OrgRuntime] MCP connect for node failed: {e}")

    async def _broadcast_ws(self, event: str, data: dict) -> None:
        try:
            from openakita.api.routes.websocket import broadcast_event
            await broadcast_event(event, data)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Tool call integration
    # ------------------------------------------------------------------

    async def handle_org_tool(
        self, tool_name: str, arguments: dict, org_id: str, node_id: str
    ) -> str:
        """Public entry point for org tool execution."""
        return await self._tool_handler.handle(tool_name, arguments, org_id, node_id)

    def _register_org_tool_handler(
        self, agent: Any, org_id: str, node_id: str
    ) -> None:
        """Patch agent's ToolExecutor to intercept org_* tool calls."""
        if not hasattr(agent, "reasoning_engine"):
            return
        engine = agent.reasoning_engine
        if not hasattr(engine, "_tool_executor"):
            return
        executor = engine._tool_executor

        original_execute = executor.execute_tool
        tool_handler = self._tool_handler

        async def _patched_execute(tool_name: str, tool_input: dict, **kwargs) -> str:
            if tool_name.startswith("org_"):
                return await tool_handler.handle(tool_name, tool_input, org_id, node_id)
            return await original_execute(tool_name, tool_input, **kwargs)

        executor.execute_tool = _patched_execute
