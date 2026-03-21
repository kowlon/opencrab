"""Tests for TaskScheduler abstraction and LinearScheduler."""
import pytest
from seeagent.bestpractice.models import (
    BestPracticeConfig, SubtaskConfig, SubtaskStatus,
    BPInstanceSnapshot, RunMode,
)
from seeagent.bestpractice.scheduler import LinearScheduler


def _make_config(subtask_count=3, input_mappings=None):
    subtasks = []
    for i in range(subtask_count):
        sid = f"s{i+1}"
        mapping = (input_mappings or {}).get(sid, {})
        subtasks.append(SubtaskConfig(
            id=sid, name=f"Step {i+1}", agent_profile="default",
            input_schema={
                "type": "object",
                "properties": {"data": {"type": "string"}},
                "required": ["data"],
            },
            input_mapping=mapping,
        ))
    return BestPracticeConfig(
        id="test_bp", name="Test BP", subtasks=subtasks,
        final_output_schema={
            "type": "object",
            "properties": {"result": {"type": "string"}},
        },
    )


def _make_snapshot(bp_id="test_bp", session_id="sess", subtask_ids=("s1", "s2", "s3"),
                   current_index=0, statuses=None, outputs=None,
                   initial_input=None, supplemented_inputs=None):
    sts = statuses or {sid: SubtaskStatus.PENDING.value for sid in subtask_ids}
    return BPInstanceSnapshot(
        bp_id=bp_id, instance_id="bp-test123", session_id=session_id,
        created_at=0.0, current_subtask_index=current_index,
        subtask_statuses=sts, initial_input=initial_input or {},
        subtask_outputs=outputs or {}, context_summary="",
        supplemented_inputs=supplemented_inputs or {},
    )


class TestLinearSchedulerGetReadyTasks:
    def test_first_task_ready(self):
        cfg = _make_config()
        snap = _make_snapshot()
        sched = LinearScheduler(cfg, snap)
        ready = sched.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "s1"

    def test_no_ready_when_all_done(self):
        cfg = _make_config()
        snap = _make_snapshot(
            current_index=3,
            statuses={"s1": "done", "s2": "done", "s3": "done"},
        )
        sched = LinearScheduler(cfg, snap)
        assert sched.get_ready_tasks() == []

    def test_stale_task_is_ready(self):
        cfg = _make_config()
        snap = _make_snapshot(
            current_index=1,
            statuses={"s1": "done", "s2": "stale", "s3": "pending"},
        )
        sched = LinearScheduler(cfg, snap)
        ready = sched.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "s2"

    def test_current_task_not_ready(self):
        cfg = _make_config()
        snap = _make_snapshot(
            current_index=0,
            statuses={"s1": "current", "s2": "pending", "s3": "pending"},
        )
        sched = LinearScheduler(cfg, snap)
        assert sched.get_ready_tasks() == []

    def test_waiting_input_not_ready(self):
        cfg = _make_config()
        snap = _make_snapshot(
            current_index=0,
            statuses={"s1": "waiting_input", "s2": "pending", "s3": "pending"},
        )
        sched = LinearScheduler(cfg, snap)
        assert sched.get_ready_tasks() == []


class TestLinearSchedulerCompleteTask:
    def test_complete_advances_index(self):
        cfg = _make_config()
        snap = _make_snapshot()
        sched = LinearScheduler(cfg, snap)
        sched.complete_task("s1", {"data": "output1"})
        assert snap.subtask_statuses["s1"] == SubtaskStatus.DONE.value
        assert snap.subtask_outputs["s1"] == {"data": "output1"}
        assert snap.current_subtask_index == 1

    def test_complete_last_task(self):
        cfg = _make_config()
        snap = _make_snapshot(
            current_index=2,
            statuses={"s1": "done", "s2": "done", "s3": "pending"},
        )
        sched = LinearScheduler(cfg, snap)
        sched.complete_task("s3", {"result": "final"})
        assert snap.current_subtask_index == 3
        assert sched.is_done()


class TestLinearSchedulerResolveInput:
    def test_first_subtask_uses_initial_input(self):
        cfg = _make_config()
        snap = _make_snapshot(initial_input={"data": "user_input"})
        sched = LinearScheduler(cfg, snap)
        assert sched.resolve_input("s1") == {"data": "user_input"}

    def test_subsequent_subtask_uses_previous_output(self):
        cfg = _make_config()
        snap = _make_snapshot(current_index=1, outputs={"s1": {"data": "step1_out"}})
        sched = LinearScheduler(cfg, snap)
        assert sched.resolve_input("s2") == {"data": "step1_out"}

    def test_input_mapping_overrides_linear_fallback(self):
        cfg = _make_config(input_mappings={"s3": {"upstream_data": "s1"}})
        snap = _make_snapshot(current_index=2, outputs={"s1": {"x": 1}, "s2": {"y": 2}})
        sched = LinearScheduler(cfg, snap)
        assert sched.resolve_input("s3") == {"upstream_data": {"x": 1}}

    def test_supplemented_inputs_merged_linear(self):
        """R19: supplemented_inputs merged in linear fallback path."""
        cfg = _make_config()
        snap = _make_snapshot(
            initial_input={"data": "original"},
            supplemented_inputs={"s1": {"extra": "added"}},
        )
        sched = LinearScheduler(cfg, snap)
        assert sched.resolve_input("s1") == {"data": "original", "extra": "added"}

    def test_supplemented_inputs_merged_with_input_mapping(self):
        """R19: supplemented_inputs merged even when input_mapping is used."""
        cfg = _make_config(input_mappings={"s2": {"from_s1": "s1"}})
        snap = _make_snapshot(
            current_index=1, outputs={"s1": {"a": 1}},
            supplemented_inputs={"s2": {"manual_field": "user_value"}},
        )
        sched = LinearScheduler(cfg, snap)
        assert sched.resolve_input("s2") == {
            "from_s1": {"a": 1}, "manual_field": "user_value",
        }

    def test_unknown_subtask_returns_empty(self):
        cfg = _make_config()
        snap = _make_snapshot()
        sched = LinearScheduler(cfg, snap)
        assert sched.resolve_input("nonexistent") == {}


class TestLinearSchedulerIsDone:
    def test_not_done_with_pending(self):
        cfg = _make_config(subtask_count=2)
        snap = _make_snapshot(
            subtask_ids=("s1", "s2"), statuses={"s1": "done", "s2": "pending"},
        )
        sched = LinearScheduler(cfg, snap)
        assert not sched.is_done()

    def test_done_when_all_complete(self):
        cfg = _make_config(subtask_count=2)
        snap = _make_snapshot(
            subtask_ids=("s1", "s2"), statuses={"s1": "done", "s2": "done"},
        )
        sched = LinearScheduler(cfg, snap)
        assert sched.is_done()


class TestDeriveOutputSchema:
    def test_middle_subtask_gets_next_input_schema(self):
        cfg = _make_config()
        snap = _make_snapshot()
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s1")
        assert schema == {
            "type": "object",
            "properties": {"data": {"type": "string"}},
            "required": ["data"],
        }

    def test_last_subtask_gets_final_output_schema(self):
        cfg = _make_config()
        snap = _make_snapshot()
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s3")
        assert schema == {"type": "object", "properties": {"result": {"type": "string"}}}

    def test_no_final_schema_returns_none(self):
        cfg = _make_config()
        cfg.final_output_schema = None
        snap = _make_snapshot()
        sched = LinearScheduler(cfg, snap)
        assert sched.derive_output_schema("s3") is None
