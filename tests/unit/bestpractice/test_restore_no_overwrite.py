"""Regression: restoring from session metadata must not roll back in-memory progress."""

from __future__ import annotations

from copy import deepcopy

from seeagent.bestpractice.engine import BPStateManager
from seeagent.bestpractice.engine.scheduler import LinearScheduler
from seeagent.bestpractice.models import BestPracticeConfig, RunMode, SubtaskConfig


def _make_config() -> BestPracticeConfig:
    return BestPracticeConfig(
        id="test-bp",
        name="测试",
        subtasks=[
            SubtaskConfig(id="s1", name="调研", agent_profile="researcher"),
            SubtaskConfig(id="s2", name="分析", agent_profile="analyst"),
            SubtaskConfig(id="s3", name="报告", agent_profile="writer"),
        ],
        default_run_mode=RunMode.MANUAL,
    )


def test_restore_from_dict_does_not_overwrite_existing_instance():
    """
    Repro (from incident description):
    - advance() completes a subtask, moving current_subtask_index forward (e.g. to 2)
    - between yields, a concurrent restore_from_dict() happens with stale metadata (e.g. index=1)
    - old behavior overwrote the in-memory snapshot, causing /bp/next to "stick" on the same subtask

    Fix: restore_from_dict must never overwrite an existing in-memory instance_id.
    """
    cfg = _make_config()
    mgr = BPStateManager()
    inst_id = mgr.create_instance(cfg, "sess-1", initial_input={"q": "x"}, run_mode=RunMode.MANUAL)
    snap = mgr.get(inst_id)
    assert snap is not None

    # Simulate completing two subtasks (so next should be index=2 → third subtask).
    sched = LinearScheduler(cfg, snap)
    sched.complete_task("s1", {"ok": 1})
    sched.complete_task("s2", {"ok": 2})
    assert mgr.get(inst_id).current_subtask_index == 2
    assert mgr.get(inst_id).subtask_statuses["s2"] == "done"

    # Persist a stale snapshot (index=1, s2 pending) as if session metadata lagged.
    stale = deepcopy(snap.serialize())
    stale["current_subtask_index"] = 1
    stale["subtask_statuses"] = {"s1": "done", "s2": "pending", "s3": "pending"}
    payload = {"version": 1, "instances": [stale]}

    # This must NOT overwrite the existing in-memory snapshot.
    restored = mgr.restore_from_dict("sess-1", payload, config_map={"test-bp": cfg})
    assert restored == 0

    after = mgr.get(inst_id)
    assert after is not None
    assert after.current_subtask_index == 2
    assert after.subtask_statuses["s2"] == "done"

