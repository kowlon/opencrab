"""Tests for BP models changes: WAITING_INPUT status + supplemented_inputs field."""
from seeagent.bestpractice.models import SubtaskStatus, BPInstanceSnapshot


class TestSubtaskStatusWaitingInput:
    def test_waiting_input_exists(self):
        assert hasattr(SubtaskStatus, "WAITING_INPUT")
        assert SubtaskStatus.WAITING_INPUT.value == "waiting_input"

    def test_waiting_input_roundtrip(self):
        status = SubtaskStatus("waiting_input")
        assert status == SubtaskStatus.WAITING_INPUT

    def test_all_statuses_are_strings(self):
        for s in SubtaskStatus:
            assert isinstance(s.value, str)


class TestSupplementedInputs:
    def test_snapshot_has_supplemented_inputs(self):
        snap = BPInstanceSnapshot(
            bp_id="test",
            instance_id="bp-test",
            session_id="sess-1",
            created_at=0.0,
            subtask_statuses={},
            initial_input={},
            subtask_outputs={},
            context_summary="",
        )
        assert hasattr(snap, "supplemented_inputs")
        assert snap.supplemented_inputs == {}

    def test_supplemented_inputs_independent_per_subtask(self):
        snap = BPInstanceSnapshot(
            bp_id="test",
            instance_id="bp-test",
            session_id="sess-1",
            created_at=0.0,
            subtask_statuses={},
            initial_input={},
            subtask_outputs={},
            context_summary="",
        )
        snap.supplemented_inputs["st1"] = {"field_a": "value"}
        snap.supplemented_inputs["st2"] = {"field_b": 42}
        assert snap.supplemented_inputs["st1"] == {"field_a": "value"}
        assert snap.supplemented_inputs["st2"] == {"field_b": 42}

    def test_supplemented_inputs_in_serialize(self):
        snap = BPInstanceSnapshot(
            bp_id="test",
            instance_id="bp-test",
            session_id="sess-1",
            created_at=0.0,
            subtask_statuses={},
            initial_input={},
            subtask_outputs={},
            context_summary="",
        )
        snap.supplemented_inputs["st1"] = {"field_a": "value"}
        data = snap.serialize()
        assert "supplemented_inputs" in data
        assert data["supplemented_inputs"] == {"st1": {"field_a": "value"}}

    def test_supplemented_inputs_in_deserialize(self):
        data = {
            "bp_id": "test",
            "instance_id": "bp-test",
            "session_id": "sess-1",
            "created_at": 0.0,
            "subtask_statuses": {},
            "initial_input": {},
            "subtask_outputs": {},
            "context_summary": "",
            "supplemented_inputs": {"st1": {"field_a": "value"}},
        }
        snap = BPInstanceSnapshot.deserialize(data)
        assert snap.supplemented_inputs == {"st1": {"field_a": "value"}}

    def test_supplemented_inputs_deserialize_missing_key(self):
        """Deserializing old data without supplemented_inputs should default to {}."""
        data = {
            "bp_id": "test",
            "instance_id": "bp-test",
            "session_id": "sess-1",
            "created_at": 0.0,
            "subtask_statuses": {},
            "initial_input": {},
            "subtask_outputs": {},
            "context_summary": "",
        }
        snap = BPInstanceSnapshot.deserialize(data)
        assert snap.supplemented_inputs == {}


# ── ContextEnvelope / ContextArtifact ────────────────────────

import json
from seeagent.bestpractice.models import (
    ArtifactKind,
    ContextArtifact,
    ContextEnvelope,
    ContextLevel,
)


class TestContextArtifact:
    def test_autoPriorityTest(self):
        a = ContextArtifact(kind=ArtifactKind.PROGRESS, key="s1", content="data")
        assert a.priority == 10

    def test_autoSizeTest(self):
        a = ContextArtifact(kind=ArtifactKind.RAW_TEXT, key="k", content="hello")
        assert a.size == 5

    def test_kindFromStringTest(self):
        a = ContextArtifact(kind="progress", key="s1", content="data")
        assert a.kind == ArtifactKind.PROGRESS

    def test_customPriorityPreservedTest(self):
        a = ContextArtifact(kind=ArtifactKind.RAW_TEXT, key="k", content="x", priority=99)
        assert a.priority == 99


class TestContextEnvelope:
    def test_serializeRoundtripTest(self):
        env = ContextEnvelope(
            level=ContextLevel.BP_INSTANCE,
            source_id="test-bp",
            artifacts=[
                ContextArtifact(kind=ArtifactKind.PROGRESS, key="s1", content="done"),
            ],
            summary="test summary",
            compressed_at=1000.0,
            compression_method="llm",
        )
        data = env.serialize()
        assert data["version"] == 2
        assert data["level"] == "bp_instance"
        assert len(data["artifacts"]) == 1
        assert data["artifacts"][0]["kind"] == "progress"

    def test_fromV1CompatTest(self):
        v1 = json.dumps({
            "version": 1,
            "bp_name": "My BP",
            "subtask_progress": [
                {"id": "s1", "name": "Research", "status": "done"},
            ],
            "key_outputs": {"s1": "output data"},
            "semantic_summary": "user wants B2B",
            "user_intent": "AI adoption",
            "compressed_at": 1000.0,
            "compression_method": "llm",
        })
        env = ContextEnvelope.from_v1(v1)
        assert env.source_id == "My BP"
        assert env.summary == "user wants B2B"
        progress = env.get_artifacts(ArtifactKind.PROGRESS)
        assert len(progress) == 1
        outputs = env.get_artifacts(ArtifactKind.STRUCTURED_OUTPUT)
        assert len(outputs) == 1
        intents = env.get_artifacts(ArtifactKind.USER_INTENT)
        assert len(intents) == 1

    def test_fromV1InvalidJsonTest(self):
        env = ContextEnvelope.from_v1("not json")
        assert env.source_id == ""
        assert env.artifacts == []

    def test_fromV2FormatTest(self):
        v2 = json.dumps({
            "version": 2,
            "level": "bp_instance",
            "source_id": "bp1",
            "artifacts": [
                {"kind": "progress", "key": "s1", "content": "data", "priority": 10},
            ],
            "summary": "summary",
            "compressed_at": 2000.0,
            "compression_method": "mechanical",
            "total_budget": 10000,
        })
        env = ContextEnvelope.from_v1(v2)
        assert env.source_id == "bp1"
        assert env.total_budget == 10000
        assert len(env.artifacts) == 1

    def test_getArtifactsFilterTest(self):
        env = ContextEnvelope(
            level=ContextLevel.BP_INSTANCE,
            source_id="bp1",
            artifacts=[
                ContextArtifact(kind=ArtifactKind.PROGRESS, key="s1", content="a"),
                ContextArtifact(kind=ArtifactKind.RAW_TEXT, key="r1", content="b"),
                ContextArtifact(kind=ArtifactKind.PROGRESS, key="s2", content="c"),
            ],
        )
        progress = env.get_artifacts(ArtifactKind.PROGRESS)
        assert len(progress) == 2
        raw = env.get_artifacts(ArtifactKind.RAW_TEXT)
        assert len(raw) == 1

    def test_trimToBudgetTest(self):
        env = ContextEnvelope(
            level=ContextLevel.BP_INSTANCE,
            source_id="bp1",
            artifacts=[
                ContextArtifact(
                    kind=ArtifactKind.PROGRESS, key="s1",
                    content="a" * 100, priority=10,
                ),
                ContextArtifact(
                    kind=ArtifactKind.RAW_TEXT, key="r1",
                    content="b" * 100, priority=3,
                ),
            ],
            total_budget=120,
        )
        env.trim_to_budget()
        total_size = sum(a.size for a in env.artifacts)
        assert total_size <= 120


class TestSnapshotNewFields:
    def test_subtaskRawOutputsDefaultTest(self):
        snap = BPInstanceSnapshot(
            bp_id="test", instance_id="bp-test", session_id="sess-1",
        )
        assert snap.subtask_raw_outputs == {}

    def test_subtaskPartialResultsDefaultTest(self):
        snap = BPInstanceSnapshot(
            bp_id="test", instance_id="bp-test", session_id="sess-1",
        )
        assert snap.subtask_partial_results == {}

    def test_serializeNewFieldsTest(self):
        snap = BPInstanceSnapshot(
            bp_id="test", instance_id="bp-test", session_id="sess-1",
        )
        snap.subtask_raw_outputs["s1"] = "raw text"
        snap.subtask_partial_results["s2"] = ["result1", "result2"]
        data = snap.serialize()
        assert data["subtask_raw_outputs"] == {"s1": "raw text"}
        assert data["subtask_partial_results"] == {"s2": ["result1", "result2"]}

    def test_deserializeNewFieldsTest(self):
        data = {
            "bp_id": "test", "instance_id": "bp-test", "session_id": "sess-1",
            "subtask_raw_outputs": {"s1": "raw"},
            "subtask_partial_results": {"s2": ["r1"]},
            "subtask_statuses": {}, "initial_input": {},
            "subtask_outputs": {}, "context_summary": "",
        }
        snap = BPInstanceSnapshot.deserialize(data)
        assert snap.subtask_raw_outputs == {"s1": "raw"}
        assert snap.subtask_partial_results == {"s2": ["r1"]}

    def test_deserializeBackwardCompatTest(self):
        data = {
            "bp_id": "test", "instance_id": "bp-test", "session_id": "sess-1",
            "subtask_statuses": {}, "initial_input": {},
            "subtask_outputs": {}, "context_summary": "",
        }
        snap = BPInstanceSnapshot.deserialize(data)
        assert snap.subtask_raw_outputs == {}
        assert snap.subtask_partial_results == {}
