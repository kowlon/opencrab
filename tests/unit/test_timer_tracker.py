"""Tests for TimerTracker — TTFT/Total/Step Duration collection."""
from __future__ import annotations

from openakita.api.adapters.timer_tracker import TimerTracker


class TestStart:
    def test_start_creates_timer(self):
        tt = TimerTracker()
        tt.start("reply_1")
        assert tt.reply_timer is not None
        assert tt.reply_timer.reply_id == "reply_1"
        assert tt.ttft_triggered is False


class TestCheckTTFT:
    def test_first_call_triggers(self):
        tt = TimerTracker()
        tt.start("r1")
        event = tt.check_ttft()
        assert event is not None
        assert event["type"] == "timer_update"
        assert event["phase"] == "ttft"
        assert event["state"] == "done"
        assert event["value"] is not None
        assert event["value"] >= 0
        assert tt.ttft_triggered is True

    def test_second_call_returns_none(self):
        tt = TimerTracker()
        tt.start("r1")
        tt.check_ttft()
        assert tt.check_ttft() is None


class TestStepTiming:
    def test_start_and_end_step(self):
        tt = TimerTracker()
        tt.start("r1")
        tt.start_step("s1")
        assert "s1" in tt.reply_timer.step_timers
        duration = tt.end_step("s1")
        assert duration >= 0
        assert tt.reply_timer.step_timers["s1"].t_end is not None


class TestMakeEvent:
    def test_running_event_no_value(self):
        tt = TimerTracker()
        tt.start("r1")
        event = tt.make_event("total", "running")
        assert event["type"] == "timer_update"
        assert event["reply_id"] == "r1"
        assert event["phase"] == "total"
        assert event["state"] == "running"
        assert event["value"] is None

    def test_done_total_has_value(self):
        tt = TimerTracker()
        tt.start("r1")
        event = tt.make_event("total", "done")
        assert event["state"] == "done"
        assert event["value"] is not None
        assert event["value"] >= 0

    def test_cancelled_event(self):
        tt = TimerTracker()
        tt.start("r1")
        event = tt.make_event("total", "cancelled")
        assert event["state"] == "cancelled"
        assert event["value"] is not None
