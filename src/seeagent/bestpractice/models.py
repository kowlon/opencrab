"""BP 数据模型 — 枚举类型、上下文抽象与运行时快照。"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ──────────────────────────────────────────────────────


class RunMode(Enum):
    MANUAL = "manual"
    AUTO = "auto"


class BPStatus(Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SubtaskStatus(Enum):
    PENDING = "pending"
    CURRENT = "current"
    DONE = "done"
    STALE = "stale"
    FAILED = "failed"
    WAITING_INPUT = "waiting_input"


class TriggerType(Enum):
    COMMAND = "command"
    CONTEXT = "context"
    CRON = "cron"
    EVENT = "event"
    UI_CLICK = "ui_click"


class ContextLevel(Enum):
    BP_INSTANCE = "bp_instance"
    SUBTASK = "subtask"


class ArtifactKind(Enum):
    PROGRESS = "progress"
    USER_INTENT = "user_intent"
    SEMANTIC_SUMMARY = "semantic_summary"
    STRUCTURED_OUTPUT = "structured_output"
    RAW_TEXT = "raw_text"
    TOOL_RESULT = "tool_result"


# ── Context abstractions ──────────────────────────────────────


_ARTIFACT_DEFAULT_PRIORITY: dict[ArtifactKind, int] = {
    ArtifactKind.PROGRESS: 10,
    ArtifactKind.USER_INTENT: 9,
    ArtifactKind.SEMANTIC_SUMMARY: 8,
    ArtifactKind.STRUCTURED_OUTPUT: 7,
    ArtifactKind.RAW_TEXT: 3,
    ArtifactKind.TOOL_RESULT: 2,
}


@dataclass
class ContextArtifact:
    """A single piece of captured context data."""
    kind: ArtifactKind
    key: str
    content: str
    priority: int = 0
    size: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.kind, str):
            self.kind = ArtifactKind(self.kind)
        if not self.priority:
            self.priority = _ARTIFACT_DEFAULT_PRIORITY.get(self.kind, 5)
        if not self.size:
            self.size = len(self.content)


@dataclass
class ContextEnvelope:
    """Unified container for captured context at any execution level."""
    level: ContextLevel
    source_id: str
    artifacts: list[ContextArtifact] = field(default_factory=list)
    summary: str = ""
    compressed_at: float | None = None
    compression_method: str = "none"
    total_budget: int = 15000

    def __post_init__(self) -> None:
        if isinstance(self.level, str):
            self.level = ContextLevel(self.level)

    def serialize(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict (v2 format)."""
        return {
            "version": 2,
            "level": self.level.value,
            "source_id": self.source_id,
            "artifacts": [
                {
                    "kind": a.kind.value,
                    "key": a.key,
                    "content": a.content,
                    "priority": a.priority,
                }
                for a in self.artifacts
            ],
            "summary": self.summary,
            "compressed_at": self.compressed_at,
            "compression_method": self.compression_method,
            "total_budget": self.total_budget,
        }

    @classmethod
    def from_v1(cls, json_str: str) -> ContextEnvelope:
        """Parse legacy v1 context_summary JSON into ContextEnvelope."""
        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, TypeError):
            return cls(level=ContextLevel.BP_INSTANCE, source_id="")

        if data.get("version", 1) >= 2:
            return cls._from_v2(data)

        artifacts: list[ContextArtifact] = []

        for p in data.get("subtask_progress", []):
            artifacts.append(ContextArtifact(
                kind=ArtifactKind.PROGRESS,
                key=p.get("id", ""),
                content=json.dumps(p, ensure_ascii=False),
            ))

        for k, v in data.get("key_outputs", {}).items():
            artifacts.append(ContextArtifact(
                kind=ArtifactKind.STRUCTURED_OUTPUT,
                key=k,
                content=v if isinstance(v, str) else json.dumps(
                    v, ensure_ascii=False,
                ),
            ))

        semantic = data.get("semantic_summary", "")
        if semantic:
            artifacts.append(ContextArtifact(
                kind=ArtifactKind.SEMANTIC_SUMMARY,
                key="semantic",
                content=semantic,
            ))

        intent = data.get("user_intent", "")
        if intent:
            artifacts.append(ContextArtifact(
                kind=ArtifactKind.USER_INTENT,
                key="intent",
                content=intent,
            ))

        return cls(
            level=ContextLevel.BP_INSTANCE,
            source_id=data.get("bp_name", ""),
            artifacts=artifacts,
            summary=semantic,
            compressed_at=data.get("compressed_at"),
            compression_method=data.get("compression_method", "none"),
        )

    @classmethod
    def _from_v2(cls, data: dict[str, Any]) -> ContextEnvelope:
        """Deserialize from v2 format."""
        artifacts = [
            ContextArtifact(
                kind=ArtifactKind(a["kind"]),
                key=a["key"],
                content=a["content"],
                priority=a.get("priority", 0),
            )
            for a in data.get("artifacts", [])
        ]
        return cls(
            level=ContextLevel(data.get("level", "bp_instance")),
            source_id=data.get("source_id", ""),
            artifacts=artifacts,
            summary=data.get("summary", ""),
            compressed_at=data.get("compressed_at"),
            compression_method=data.get("compression_method", "none"),
            total_budget=data.get("total_budget", 15000),
        )

    def get_artifacts(self, kind: ArtifactKind) -> list[ContextArtifact]:
        """Filter artifacts by kind."""
        return [a for a in self.artifacts if a.kind == kind]

    def trim_to_budget(self) -> None:
        """Trim artifacts by priority to fit within total_budget."""
        self.artifacts.sort(key=lambda a: a.priority, reverse=True)
        total = 0
        kept: list[ContextArtifact] = []
        for a in self.artifacts:
            if total + a.size <= self.total_budget:
                kept.append(a)
                total += a.size
            else:
                remaining = self.total_budget - total
                if remaining > 100:
                    kept.append(ContextArtifact(
                        kind=a.kind,
                        key=a.key,
                        content=a.content[:remaining],
                        priority=a.priority,
                    ))
                break
        self.artifacts = kept


# ── Config dataclasses ─────────────────────────────────────────


@dataclass
class TriggerConfig:
    type: TriggerType
    pattern: str = ""
    conditions: list[str] = field(default_factory=list)
    cron: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.type, str):
            self.type = TriggerType(self.type)


@dataclass
class SubtaskConfig:
    id: str
    name: str
    agent_profile: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    input_mapping: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int | None = None
    max_retries: int = 0


@dataclass
class BestPracticeConfig:
    id: str
    name: str
    subtasks: list[SubtaskConfig]
    description: str = ""
    triggers: list[TriggerConfig] = field(default_factory=list)
    final_output_schema: dict[str, Any] | None = None
    default_run_mode: RunMode = RunMode.MANUAL

    def __post_init__(self) -> None:
        if isinstance(self.default_run_mode, str):
            self.default_run_mode = RunMode(self.default_run_mode)


# ── Runtime snapshot ───────────────────────────────────────────


@dataclass
class PendingContextSwitch:
    """由 bp_switch_task/bp_start 创建，由 Agent._prepare_session_context() 消费。"""
    suspended_instance_id: str
    target_instance_id: str
    created_at: float = field(default_factory=time.time)


@dataclass
class BPInstanceSnapshot:
    """单个 BP 实例的完整运行时状态快照。"""
    bp_id: str
    instance_id: str
    session_id: str
    status: BPStatus = BPStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    suspended_at: float | None = None
    current_subtask_index: int = 0
    run_mode: RunMode = RunMode.MANUAL
    subtask_statuses: dict[str, str] = field(default_factory=dict)
    initial_input: dict[str, Any] = field(default_factory=dict)
    subtask_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    subtask_raw_outputs: dict[str, str] = field(default_factory=dict)
    subtask_partial_results: dict[str, list[str]] = field(default_factory=dict)
    context_summary: str = ""
    supplemented_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    bp_config: BestPracticeConfig | None = field(default=None, repr=False)

    @staticmethod
    def new_instance_id() -> str:
        return f"bp-{uuid.uuid4().hex[:8]}"

    def serialize(self) -> dict[str, Any]:
        """序列化为可持久化的 dict（排除 bp_config 运行时引用）。"""
        return {
            "bp_id": self.bp_id,
            "instance_id": self.instance_id,
            "session_id": self.session_id,
            "status": self.status.value if isinstance(self.status, BPStatus) else self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "suspended_at": self.suspended_at,
            "current_subtask_index": self.current_subtask_index,
            "run_mode": self.run_mode.value if isinstance(self.run_mode, RunMode) else self.run_mode,
            "subtask_statuses": dict(self.subtask_statuses),
            "initial_input": dict(self.initial_input),
            "subtask_outputs": {k: dict(v) for k, v in self.subtask_outputs.items()},
            "subtask_raw_outputs": dict(self.subtask_raw_outputs),
            "subtask_partial_results": {
                k: list(v) for k, v in self.subtask_partial_results.items()
            },
            "context_summary": self.context_summary,
            "supplemented_inputs": {k: dict(v) for k, v in self.supplemented_inputs.items()},
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> BPInstanceSnapshot:
        """从 dict 反序列化（bp_config 需调用方回填）。"""
        return cls(
            bp_id=data["bp_id"],
            instance_id=data["instance_id"],
            session_id=data["session_id"],
            status=BPStatus(data.get("status", "active")),
            created_at=data.get("created_at", 0.0),
            completed_at=data.get("completed_at"),
            suspended_at=data.get("suspended_at"),
            current_subtask_index=data.get("current_subtask_index", 0),
            run_mode=RunMode(data.get("run_mode", "manual")),
            subtask_statuses=dict(data.get("subtask_statuses", {})),
            initial_input=dict(data.get("initial_input", {})),
            subtask_outputs={k: dict(v) for k, v in data.get("subtask_outputs", {}).items()},
            subtask_raw_outputs=dict(data.get("subtask_raw_outputs", {})),
            subtask_partial_results={
                k: list(v) for k, v in data.get("subtask_partial_results", {}).items()
            },
            context_summary=data.get("context_summary", ""),
            supplemented_inputs={
                k: dict(v) for k, v in data.get("supplemented_inputs", {}).items()
            },
            bp_config=None,
        )
