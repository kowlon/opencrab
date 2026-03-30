"""ContextBridge tests -- compression, restoration, and full switch flow."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from seeagent.bestpractice.engine import BPStateManager, ContextBridge
from seeagent.bestpractice.models import (
    ArtifactKind,
    BPInstanceSnapshot,
    BPStatus,
    BestPracticeConfig,
    ContextArtifact,
    ContextEnvelope,
    PendingContextSwitch,
    SubtaskConfig,
)


@pytest.fixture
def bridge():
    sm = BPStateManager()
    return ContextBridge(state_manager=sm)


def _make_bp_config(subtasks=None):
    subtasks = subtasks or [
        SubtaskConfig(id="s1", name="Research", agent_profile="p1"),
        SubtaskConfig(id="s2", name="Outline", agent_profile="p2"),
        SubtaskConfig(id="s3", name="Writing", agent_profile="p3"),
    ]
    return BestPracticeConfig(id="test-bp", name="Test BP", subtasks=subtasks)


def _make_snap(**kwargs):
    defaults = {
        "context_summary": "",
        "subtask_outputs": {},
        "bp_config": None,
        "bp_id": "test-bp",
        "current_subtask_index": 0,
        "initial_input": {},
        "subtask_statuses": {},
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_brain(response_text="LLM summary text"):
    brain = SimpleNamespace()
    resp = SimpleNamespace(content=response_text)
    brain.think_lightweight = AsyncMock(return_value=resp)
    return brain


# ── Compression: LLM path ────────────────────────────────────


class TestCompressWithLlm:
    @pytest.mark.asyncio
    async def test_compressWithLlmTest(self, bridge):
        """LLM path returns plain-text semantic summary."""
        brain = _make_brain("User prefers B2B focus, decided on ROI metrics")
        snap = _make_snap(
            bp_config=_make_bp_config(),
            current_subtask_index=1,
            subtask_statuses={"s1": "done", "s2": "current", "s3": "pending"},
            subtask_outputs={"s1": {"result": "research done"}},
            initial_input={"topic": "AI adoption"},
        )
        messages = [
            {"role": "user", "content": "I want B2B focus"},
            {"role": "assistant", "content": "Got it, focusing on B2B"},
        ]

        result = await bridge._compress_context(
            messages=messages, snap=snap, brain=brain,
        )

        assert isinstance(result, str)
        assert not result.strip().startswith("{")
        assert "B2B" in result
        brain.think_lightweight.assert_awaited_once()


class TestCompressLlmFallback:
    @pytest.mark.asyncio
    async def test_compressLlmFallbackTest(self, bridge):
        """Brain raises exception, falls back to mechanical, returns plain text."""
        brain = _make_brain()
        brain.think_lightweight = AsyncMock(side_effect=RuntimeError("LLM down"))
        snap = _make_snap(bp_config=_make_bp_config())
        messages = [{"role": "user", "content": "important context"}]

        result = await bridge._compress_context(
            messages=messages, snap=snap, brain=brain,
        )

        assert not result.strip().startswith("{")
        assert "important context" in result


class TestCompressBrainNone:
    @pytest.mark.asyncio
    async def test_compressBrainNoneTest(self, bridge):
        """brain=None uses mechanical compression, returns plain text."""
        snap = _make_snap(bp_config=_make_bp_config())
        messages = [{"role": "user", "content": "some question"}]

        result = await bridge._compress_context(
            messages=messages, snap=snap, brain=None,
        )

        assert not result.strip().startswith("{")
        assert "some question" in result


class TestMechanicalFiltering:
    @pytest.mark.asyncio
    async def test_mechanicalFilteringTest(self, bridge):
        """Tool messages filtered, short assistant messages skipped."""
        messages = [
            {"role": "user", "content": "important user input"},
            {"role": "tool", "content": "tool result data"},
            {"role": "assistant", "content": "ok"},
            {"role": "assistant", "content": "Here is a detailed analysis of the data"},
        ]

        result = await bridge._compress_context(
            messages=messages, snap=_make_snap(), brain=None,
        )

        assert "important user input" in result
        assert "tool result data" not in result
        assert "[assistant] ok" not in result
        assert "detailed analysis" in result

    @pytest.mark.asyncio
    async def test_compressEmptyReturnsEmptyTest(self, bridge):
        """Empty messages + no snap returns empty string."""
        result = await bridge._compress_context(messages=[], snap=None)
        assert result == ""


class TestCompressListContentBlocks:
    @pytest.mark.asyncio
    async def test_compressListContentBlocksTest(self, bridge):
        """Content blocks (list format) are properly extracted."""
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello world"}]},
        ]
        result = await bridge._compress_context(
            messages=messages, snap=_make_snap(), brain=None,
        )
        assert "hello world" in result


# ── Restoration ───────────────────────────────────────────────


class TestRestoreStructured:
    def test_restoreStructuredTest(self, bridge):
        """Structured JSON recovery message has all sections."""
        summary = json.dumps({
            "version": 1,
            "bp_name": "Test BP",
            "current_subtask_index": 1,
            "total_subtasks": 3,
            "subtask_progress": [
                {"id": "s1", "name": "Research", "status": "done"},
                {"id": "s2", "name": "Outline", "status": "current"},
                {"id": "s3", "name": "Writing", "status": "pending"},
            ],
            "key_outputs": {"s1": '{"result": "research done"}'},
            "semantic_summary": "User wants B2B focus",
            "user_intent": '{"topic": "AI"}',
            "compressed_at": 1711353600.0,
            "compression_method": "llm",
        })
        # Recovery now reads from snap.subtask_outputs (full data)
        snap = _make_snap(
            context_summary=summary,
            current_subtask_index=1,
            subtask_outputs={"s1": {"result": "research done"}},
        )
        messages = [{"role": "assistant", "content": "previous response"}]

        bridge._restore_context(messages, snap)

        assert len(messages) == 2
        recovery = messages[1]["content"]
        assert "[Task Resumed]" in recovery
        assert "Test BP" in recovery
        assert "step 2/3" in recovery
        assert "[+] Research" in recovery
        assert "[>] Outline" in recovery
        assert "[ ] Writing" in recovery
        assert "research done" in recovery
        assert "B2B focus" in recovery
        assert "AI" in recovery
        assert "Please continue" in recovery


class TestRestoreLegacyText:
    def test_restoreLegacyTextTest(self, bridge):
        """Old plain text format is handled gracefully."""
        snap = _make_snap(context_summary="old plain text context")
        messages = [{"role": "assistant", "content": "prev"}]

        bridge._restore_context(messages, snap)

        assert len(messages) == 2
        assert "old plain text context" in messages[1]["content"]
        assert "Task Resumed" in messages[1]["content"]


class TestRestoreEmpty:
    def test_restoreEmptyTest(self, bridge):
        """No-op when context_summary is empty."""
        snap = _make_snap(context_summary="")
        messages = [{"role": "user", "content": "q"}]

        bridge._restore_context(messages, snap)

        assert len(messages) == 1
        assert messages[0]["content"] == "q"


class TestRoleAlternation:
    def test_roleAlternationMergeUserTest(self, bridge):
        """Merges into existing user message."""
        summary = json.dumps({
            "version": 1, "bp_name": "BP", "current_subtask_index": 0,
            "total_subtasks": 1, "subtask_progress": [],
            "key_outputs": {}, "semantic_summary": "ctx",
            "user_intent": "", "compressed_at": 0, "compression_method": "llm",
        })
        snap = _make_snap(context_summary=summary)
        messages = [{"role": "user", "content": "my question"}]

        bridge._restore_context(messages, snap)

        assert len(messages) == 1
        assert "my question" in messages[0]["content"]
        assert "[Task Resumed]" in messages[0]["content"]

    def test_roleAlternationAppendTest(self, bridge):
        """Appends new user message when last is assistant."""
        summary = json.dumps({
            "version": 1, "bp_name": "BP", "current_subtask_index": 0,
            "total_subtasks": 1, "subtask_progress": [],
            "key_outputs": {}, "semantic_summary": "ctx",
            "user_intent": "", "compressed_at": 0, "compression_method": "llm",
        })
        snap = _make_snap(context_summary=summary)
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]

        bridge._restore_context(messages, snap)

        assert len(messages) == 3
        assert messages[2]["role"] == "user"

    def test_roleAlternationMultimodalTest(self, bridge):
        """Handles list content blocks for user message merge."""
        summary = json.dumps({
            "version": 1, "bp_name": "BP", "current_subtask_index": 0,
            "total_subtasks": 1, "subtask_progress": [],
            "key_outputs": {}, "semantic_summary": "ctx",
            "user_intent": "", "compressed_at": 0, "compression_method": "llm",
        })
        snap = _make_snap(context_summary=summary)
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "see image"}]},
        ]

        bridge._restore_context(messages, snap)

        assert len(messages) == 1
        content = messages[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[1]["type"] == "text"


# ── Full switch flow ─────────────────────────────────────────


class TestFullSwitchFlow:
    @pytest.mark.asyncio
    async def test_fullSwitchFlowTest(self):
        """End-to-end: suspend -> compress -> restore."""
        sm = BPStateManager()
        cb = ContextBridge(state_manager=sm)
        brain = _make_brain("Suspended context about B2B strategy")

        bp_config = _make_bp_config()

        # Create two instances
        snap_a = BPInstanceSnapshot(
            bp_id="test-bp", instance_id="bp-aaaa",
            session_id="sess1", status=BPStatus.ACTIVE,
            subtask_statuses={"s1": "done", "s2": "current", "s3": "pending"},
            subtask_outputs={"s1": {"data": "result_a"}},
            current_subtask_index=1, bp_config=bp_config,
            initial_input={"goal": "test"},
        )
        snap_b = BPInstanceSnapshot(
            bp_id="test-bp", instance_id="bp-bbbb",
            session_id="sess1", status=BPStatus.SUSPENDED,
            subtask_statuses={"s1": "done", "s2": "pending", "s3": "pending"},
            subtask_outputs={"s1": {"data": "result_b"}},
            current_subtask_index=1, bp_config=bp_config,
            context_summary=json.dumps({
                "version": 1, "bp_name": "Test BP",
                "current_subtask_index": 1, "total_subtasks": 3,
                "subtask_progress": [
                    {"id": "s1", "name": "Research", "status": "done"},
                    {"id": "s2", "name": "Outline", "status": "pending"},
                    {"id": "s3", "name": "Writing", "status": "pending"},
                ],
                "key_outputs": {"s1": '{"data": "result_b"}'},
                "semantic_summary": "Previous B context",
                "user_intent": "", "compressed_at": 0,
                "compression_method": "llm",
            }),
        )
        sm._instances["bp-aaaa"] = snap_a
        sm._instances["bp-bbbb"] = snap_b

        # Set up pending switch: A -> B
        sm.set_pending_switch("sess1", PendingContextSwitch(
            suspended_instance_id="bp-aaaa",
            target_instance_id="bp-bbbb",
        ))

        messages = [
            {"role": "user", "content": "switch to B"},
            {"role": "assistant", "content": "Switching..."},
        ]

        result = await cb.execute_pending_switch(
            "sess1", brain=brain, messages=messages,
        )

        assert result is True
        # A should have compressed context as plain-text semantic summary
        assert snap_a.context_summary != ""
        assert not snap_a.context_summary.strip().startswith("{")
        # B's recovery message should be injected (snap_b has old JSON format → backward compat path)
        assert len(messages) == 3
        assert "[Task Resumed]" in messages[2]["content"]
        assert "Previous B context" in messages[2]["content"]


# ── PendingSwitch serialization ──────────────────────────────


class TestPendingSwitchSerialize:
    def test_pendingSwitchSerializeTest(self):
        """PendingSwitch round-trips through serialize/restore."""
        sm = BPStateManager()
        snap = BPInstanceSnapshot(
            bp_id="bp1", instance_id="bp-1111",
            session_id="sess1", status=BPStatus.ACTIVE,
            subtask_statuses={"s1": "current"},
        )
        sm._instances["bp-1111"] = snap
        sm.set_pending_switch("sess1", PendingContextSwitch(
            suspended_instance_id="bp-old",
            target_instance_id="bp-1111",
        ))

        data = sm.serialize_for_session("sess1")
        assert data["version"] == 2
        assert data["pending_switch"] is not None
        assert data["pending_switch"]["suspended_id"] == "bp-old"
        assert data["pending_switch"]["target_id"] == "bp-1111"

        # Restore into fresh state manager
        sm2 = BPStateManager()
        sm2.restore_from_dict("sess1", data)
        ps = sm2.consume_pending_switch("sess1")
        assert ps is not None
        assert ps.suspended_instance_id == "bp-old"
        assert ps.target_instance_id == "bp-1111"

    def test_pendingSwitchNoneSerializeTest(self):
        """No pending switch serializes as None."""
        sm = BPStateManager()
        snap = BPInstanceSnapshot(
            bp_id="bp1", instance_id="bp-2222",
            session_id="sess1", status=BPStatus.ACTIVE,
            subtask_statuses={},
        )
        sm._instances["bp-2222"] = snap

        data = sm.serialize_for_session("sess1")
        assert data["pending_switch"] is None

    def test_v1FormatCompatibilityTest(self):
        """Version 1 format (no pending_switch) restores without error."""
        sm = BPStateManager()
        data = {
            "version": 1,
            "instances": [{
                "bp_id": "bp1", "instance_id": "bp-3333",
                "session_id": "sess1", "status": "active",
                "subtask_statuses": {}, "initial_input": {},
                "subtask_outputs": {}, "context_summary": "",
                "supplemented_inputs": {}, "run_mode": "manual",
                "current_subtask_index": 0, "created_at": 0,
            }],
            "cooldown": 0,
            "offered_bps": [],
        }
        count = sm.restore_from_dict("sess1", data)
        assert count == 1
        assert sm.consume_pending_switch("sess1") is None


# ── Dynamic prompt budget ────────────────────────────────────


class TestDynamicPromptBudget:
    def test_dynamicPromptBudgetTest(self):
        """Suspended instances beyond max_suspended are hidden."""
        sm = BPStateManager()
        bp_config = _make_bp_config()

        # 1 active + 5 suspended
        active = BPInstanceSnapshot(
            bp_id="bp1", instance_id="bp-active",
            session_id="sess1", status=BPStatus.ACTIVE,
            subtask_statuses={"s1": "current"},
            bp_config=bp_config,
        )
        sm._instances["bp-active"] = active

        for i in range(5):
            s = BPInstanceSnapshot(
                bp_id="bp1", instance_id=f"bp-susp-{i}",
                session_id="sess1", status=BPStatus.SUSPENDED,
                suspended_at=float(i),
                subtask_statuses={"s1": "done"},
                bp_config=bp_config,
            )
            sm._instances[f"bp-susp-{i}"] = s

        table = sm.get_status_table("sess1", max_suspended=2)

        # Should show active + 2 most recent suspended
        assert "bp-active" in table
        assert "bp-susp-4" in table  # most recent
        assert "bp-susp-3" in table  # second most recent
        assert "bp-susp-0" not in table
        assert "3 more suspended" in table

    def test_dynamicPromptNoHiddenTest(self):
        """When suspended <= max, no hidden message."""
        sm = BPStateManager()
        bp_config = _make_bp_config()
        s = BPInstanceSnapshot(
            bp_id="bp1", instance_id="bp-one",
            session_id="sess1", status=BPStatus.SUSPENDED,
            suspended_at=1.0,
            subtask_statuses={"s1": "done"},
            bp_config=bp_config,
        )
        sm._instances["bp-one"] = s

        table = sm.get_status_table("sess1", max_suspended=3)
        assert "bp-one" in table
        assert "more suspended" not in table

    def test_completedExcludedTest(self):
        """Completed/cancelled instances are excluded from table."""
        sm = BPStateManager()
        bp_config = _make_bp_config()
        c = BPInstanceSnapshot(
            bp_id="bp1", instance_id="bp-done",
            session_id="sess1", status=BPStatus.COMPLETED,
            subtask_statuses={"s1": "done"},
            bp_config=bp_config,
        )
        sm._instances["bp-done"] = c

        table = sm.get_status_table("sess1")
        assert table == ""


# ── Extract text (preserved) ────────────────────────────────


class TestExtractText:
    def test_stringContentTest(self):
        assert ContextBridge._extract_text("hello") == "hello"

    def test_listContentTest(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "image", "source": {}},
            {"type": "text", "text": "world"},
        ]
        result = ContextBridge._extract_text(content)
        assert "hello" in result
        assert "world" in result

    def test_toolResultContentTest(self):
        content = [{"type": "tool_result", "content": "success"}]
        result = ContextBridge._extract_text(content)
        assert "tool_result" in result
        assert "success" in result

    def test_noneContentTest(self):
        assert ContextBridge._extract_text(None) == ""
        assert ContextBridge._extract_text(123) == ""


# ── build_recovery_message ───────────────────────────────────


class TestBuildRecoveryMessage:
    def test_structuredFormatTest(self, bridge):
        """build_recovery_message parses structured context_summary."""
        summary = json.dumps({
            "version": 1, "bp_name": "My BP",
            "current_subtask_index": 0, "total_subtasks": 2,
            "subtask_progress": [{"id": "s1", "name": "Step1", "status": "done"}],
            "key_outputs": {}, "semantic_summary": "important context",
            "user_intent": "", "compressed_at": 0, "compression_method": "llm",
        })
        snap = _make_snap(context_summary=summary)
        msg = bridge.build_recovery_message(snap)
        assert "My BP" in msg
        assert "important context" in msg

    def test_legacyFormatTest(self, bridge):
        """build_recovery_message handles plain text context_summary."""
        snap = _make_snap(context_summary="plain text summary")
        msg = bridge.build_recovery_message(snap)
        assert "plain text summary" in msg


# ── Empty summary fallback ───────────────────────────────────


class TestRestoreEmptySummaryWithOutputs:
    def test_restoreEmptySummaryWithOutputsTest(self, bridge):
        """Empty context_summary but has outputs → generates minimal recovery."""
        snap = _make_snap(
            context_summary="",
            subtask_outputs={"s1": {"result": "analysis done"}},
            initial_input={"topic": "AI"},
            bp_config=_make_bp_config(),
        )
        messages = [{"role": "assistant", "content": "prev"}]

        bridge._restore_context(messages, snap)

        assert len(messages) == 2
        recovery = messages[1]["content"]
        assert "[Task Resumed]" in recovery
        assert "analysis done" in recovery
        assert "AI" in recovery
        assert "Please continue" in recovery


class TestRestoreEmptySummaryNoData:
    def test_restoreEmptySummaryNoDataTest(self, bridge):
        """Empty context_summary and no data → no-op."""
        snap = _make_snap(
            context_summary="",
            subtask_outputs={},
            initial_input={},
        )
        messages = [{"role": "user", "content": "q"}]

        bridge._restore_context(messages, snap)

        assert len(messages) == 1
        assert messages[0]["content"] == "q"


# ── Dynamic prompt: outputs preview and user preferences ─────


class TestDynamicSectionOutputsPreview:
    def test_dynamicSectionOutputsPreviewTest(self):
        """build_dynamic_section includes outputs preview for active instance."""
        from unittest.mock import MagicMock
        from seeagent.bestpractice.prompt.builder import BPPromptBuilder
        from seeagent.bestpractice.prompt.loader import PromptTemplateLoader

        sm = BPStateManager()
        bp_config = _make_bp_config()

        active = BPInstanceSnapshot(
            bp_id="bp1", instance_id="bp-test",
            session_id="sess1", status=BPStatus.ACTIVE,
            subtask_statuses={"s1": "done", "s2": "current", "s3": "pending"},
            subtask_outputs={"s1": {"insights": "key finding"}},
            current_subtask_index=1,
            bp_config=bp_config,
        )
        sm._instances["bp-test"] = active

        config_loader = MagicMock()
        config_loader.configs = {"bp1": bp_config}
        prompt_loader = PromptTemplateLoader()

        builder = BPPromptBuilder(config_loader, sm, prompt_loader)
        result = builder.build_dynamic_section("sess1")

        assert "已完成子任务输出" in result
        assert "key finding" in result


# ── Full output restoration (not truncated) ──────────────────


class TestFullOutputRestoration:
    def test_recoveryUsesSnapOutputsNotSummaryTest(self, bridge):
        """Recovery prompt reads snap.subtask_outputs (full) instead of summary key_outputs."""
        full_output = {"analysis": "x" * 2000, "details": "long content here"}
        summary = json.dumps({
            "version": 1, "bp_name": "BP",
            "current_subtask_index": 1, "total_subtasks": 2,
            "subtask_progress": [
                {"id": "s1", "name": "Research", "status": "done"},
            ],
            "key_outputs": {"s1": "truncated to 300 chars"},
            "semantic_summary": "ctx",
            "user_intent": "", "compressed_at": 0, "compression_method": "llm",
        })
        snap = _make_snap(
            context_summary=summary,
            subtask_outputs={"s1": full_output},
        )

        result = ContextBridge._build_recovery_prompt(
            ContextEnvelope.from_v1(summary), snap,
        )

        # Should contain full output content from snap, not truncated summary
        assert "x" * 100 in result
        assert "long content here" in result
        assert "truncated to 300 chars" not in result


class TestRawOutputsInRecovery:
    def test_rawOutputsIncludedTest(self, bridge):
        """Raw outputs from snap are included in recovery prompt."""
        summary = json.dumps({
            "version": 1, "bp_name": "BP",
            "current_subtask_index": 1, "total_subtasks": 2,
            "subtask_progress": [],
            "key_outputs": {}, "semantic_summary": "ctx",
            "user_intent": "", "compressed_at": 0, "compression_method": "llm",
        })
        snap = _make_snap(
            context_summary=summary,
            subtask_outputs={},
            subtask_raw_outputs={"s1": "raw execution details here"},
        )

        result = ContextBridge._build_recovery_prompt(
            ContextEnvelope.from_v1(summary), snap,
        )

        assert "raw execution details" in result
        assert "Execution details" in result


class TestBudgetControl:
    def test_outputTruncatedAtBudgetTest(self, bridge):
        """Outputs exceeding _TOTAL_BUDGET are truncated."""
        large_output = {"data": "x" * 20000}
        summary = json.dumps({
            "version": 1, "bp_name": "BP",
            "current_subtask_index": 1, "total_subtasks": 2,
            "subtask_progress": [],
            "key_outputs": {}, "semantic_summary": "",
            "user_intent": "", "compressed_at": 0, "compression_method": "llm",
        })
        snap = _make_snap(
            context_summary=summary,
            subtask_outputs={"s1": large_output},
        )

        result = ContextBridge._build_recovery_prompt(
            ContextEnvelope.from_v1(summary), snap,
        )

        # Result should not contain all 20000 chars
        assert len(result) < 20000

    def test_minimalRecoveryBudgetTest(self, bridge):
        """_build_minimal_recovery also respects budget control."""
        snap = _make_snap(
            context_summary="",
            subtask_outputs={
                "s1": {"data": "y" * 20000},
            },
            initial_input={"topic": "test"},
            bp_config=_make_bp_config(),
        )

        result = ContextBridge._build_minimal_recovery(snap)

        assert len(result) < 20000
        assert "[Task Resumed]" in result


class TestMinimalRecoveryRawOutputs:
    def test_minimalRecoveryIncludesRawOutputsTest(self, bridge):
        """Minimal recovery path includes raw outputs."""
        snap = _make_snap(
            context_summary="",
            subtask_outputs={"s1": {"result": "data"}},
            subtask_raw_outputs={"s1": "raw details from execution"},
            initial_input={"topic": "test"},
            bp_config=_make_bp_config(),
        )

        result = ContextBridge._build_minimal_recovery(snap)

        assert "raw details" in result


class TestDynamicSectionUserPreferences:
    def test_dynamicSectionUserPreferencesTest(self):
        """build_dynamic_section includes user preferences from context_summary."""
        from unittest.mock import MagicMock
        from seeagent.bestpractice.prompt.builder import BPPromptBuilder
        from seeagent.bestpractice.prompt.loader import PromptTemplateLoader

        sm = BPStateManager()
        bp_config = _make_bp_config()

        active = BPInstanceSnapshot(
            bp_id="bp1", instance_id="bp-test",
            session_id="sess1", status=BPStatus.ACTIVE,
            subtask_statuses={"s1": "done", "s2": "current", "s3": "pending"},
            subtask_outputs={"s1": {"data": "result"}},
            current_subtask_index=1,
            bp_config=bp_config,
            context_summary=json.dumps({
                "version": 1,
                "semantic_summary": "User wants B2B focus only",
            }),
        )
        sm._instances["bp-test"] = active

        config_loader = MagicMock()
        config_loader.configs = {"bp1": bp_config}
        prompt_loader = PromptTemplateLoader()

        builder = BPPromptBuilder(config_loader, sm, prompt_loader)
        result = builder.build_dynamic_section("sess1")

        assert "用户偏好" in result
        assert "B2B focus" in result

    def test_dynamicSectionUserPreferencesV2Test(self):
        """build_dynamic_section also reads v2 ContextEnvelope summaries."""
        from unittest.mock import MagicMock
        from seeagent.bestpractice.prompt.builder import BPPromptBuilder
        from seeagent.bestpractice.prompt.loader import PromptTemplateLoader

        sm = BPStateManager()
        bp_config = _make_bp_config()

        envelope = ContextEnvelope(
            level="bp_instance",
            source_id="bp1",
            artifacts=[
                ContextArtifact(
                    kind=ArtifactKind.SEMANTIC_SUMMARY,
                    key="semantic",
                    content="User prefers concise B2B answers",
                ),
            ],
            summary="User prefers concise B2B answers",
            compression_method="mechanical",
        )
        active = BPInstanceSnapshot(
            bp_id="bp1", instance_id="bp-test-v2",
            session_id="sess1", status=BPStatus.ACTIVE,
            subtask_statuses={"s1": "done", "s2": "current", "s3": "pending"},
            subtask_outputs={"s1": {"data": "result"}},
            current_subtask_index=1,
            bp_config=bp_config,
            context_summary=json.dumps(envelope.serialize(), ensure_ascii=False),
        )
        sm._instances["bp-test-v2"] = active

        config_loader = MagicMock()
        config_loader.configs = {"bp1": bp_config}
        prompt_loader = PromptTemplateLoader()

        builder = BPPromptBuilder(config_loader, sm, prompt_loader)
        result = builder.build_dynamic_section("sess1")

        assert "用户偏好" in result
        assert "concise B2B" in result

    def test_dynamicSectionUserPreferencesPlainTextTest(self):
        """build_dynamic_section reads new plain-text context_summary directly."""
        from unittest.mock import MagicMock
        from seeagent.bestpractice.prompt.builder import BPPromptBuilder
        from seeagent.bestpractice.prompt.loader import PromptTemplateLoader

        sm = BPStateManager()
        bp_config = _make_bp_config()

        active = BPInstanceSnapshot(
            bp_id="bp1", instance_id="bp-plain-text",
            session_id="sess1", status=BPStatus.ACTIVE,
            subtask_statuses={"s1": "done", "s2": "current", "s3": "pending"},
            subtask_outputs={"s1": {"data": "result"}},
            current_subtask_index=1,
            bp_config=bp_config,
            context_summary="User prefers plain-text format answers",
        )
        sm._instances["bp-plain-text"] = active

        config_loader = MagicMock()
        config_loader.configs = {"bp1": bp_config}
        prompt_loader = PromptTemplateLoader()

        builder = BPPromptBuilder(config_loader, sm, prompt_loader)
        result = builder.build_dynamic_section("sess1")

        assert "用户偏好" in result
        assert "plain-text format" in result


# ── _build_snapshot_recovery_prompt ─────────────────────────


class TestSnapshotRecoveryPrompt:
    def test_buildWithFullSnapshotTest(self, bridge):
        """Builds complete recovery message from snapshot fields."""
        snap = _make_snap(
            bp_config=_make_bp_config(),
            context_summary="User prefers B2B focus, ROI metrics",
            current_subtask_index=1,
            subtask_statuses={"s1": "done", "s2": "current", "s3": "pending"},
            subtask_outputs={"s1": {"result": "research complete"}},
            initial_input={"topic": "AI adoption"},
        )

        result = ContextBridge._build_snapshot_recovery_prompt(snap)

        assert "[Task Resumed]" in result
        assert "Test BP" in result
        assert "step 2/3" in result
        assert "[+] Research" in result
        assert "[>] Outline" in result
        assert "[ ] Writing" in result
        assert "research complete" in result
        assert "B2B focus" in result
        assert "AI adoption" in result
        assert "Please continue" in result

    def test_buildWithNoBpConfigTest(self, bridge):
        """Degrades gracefully when bp_config is None."""
        snap = _make_snap(
            bp_config=None,
            bp_id="my-bp",
            context_summary="some semantic summary",
            subtask_outputs={"s1": {"r": "done"}},
        )

        result = ContextBridge._build_snapshot_recovery_prompt(snap)

        assert "my-bp" in result
        assert "some semantic summary" in result
        assert "r" in result

    def test_buildWithEmptySummaryTest(self, bridge):
        """No 'Context summary' section when context_summary is empty."""
        snap = _make_snap(
            bp_config=_make_bp_config(),
            context_summary="",
            subtask_outputs={"s1": {"r": "x"}},
        )

        result = ContextBridge._build_snapshot_recovery_prompt(snap)

        assert "Context summary" not in result
        assert "[Task Resumed]" in result

    def test_jsonSummaryNotIncludedTest(self, bridge):
        """JSON-format context_summary is not included in prompt text."""
        snap = _make_snap(
            bp_config=_make_bp_config(),
            context_summary='{"version": 2, "summary": "test"}',
            subtask_outputs={},
        )

        result = ContextBridge._build_snapshot_recovery_prompt(snap)

        assert '"version"' not in result
        assert "Context summary" not in result
