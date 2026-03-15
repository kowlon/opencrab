"""TimerTracker: TTFT / Total / Step Duration collection."""
from __future__ import annotations

import time

from .seecrab_models import ReplyTimer, StepTimer


class TimerTracker:
    """Collects timing data and emits timer_update events."""

    def __init__(self):
        self.reply_timer: ReplyTimer | None = None
        self.ttft_triggered: bool = False

    def start(self, reply_id: str) -> None:
        """Start timing for a new reply."""
        self.reply_timer = ReplyTimer(
            reply_id=reply_id, t_request=time.monotonic()
        )
        self.ttft_triggered = False

    def check_ttft(self) -> dict | None:
        """Check if this is the first token. Returns timer_update event or None."""
        if self.ttft_triggered or self.reply_timer is None:
            return None
        self.ttft_triggered = True
        self.reply_timer.t_first_token = time.monotonic()
        return self.make_event("ttft", "done")

    def start_step(self, step_id: str) -> None:
        """Record step start time."""
        if self.reply_timer is None:
            return
        self.reply_timer.step_timers[step_id] = StepTimer(
            step_id=step_id, t_start=time.monotonic()
        )

    def end_step(self, step_id: str) -> float:
        """Record step end time, return duration in seconds (1 decimal)."""
        if self.reply_timer is None:
            return 0.0
        timer = self.reply_timer.step_timers.get(step_id)
        if timer is None:
            return 0.0
        timer.t_end = time.monotonic()
        return round(timer.t_end - timer.t_start, 1)

    def make_event(self, phase: str, state: str) -> dict:
        """Build a timer_update event dict."""
        if self.reply_timer is None:
            return {"type": "timer_update", "phase": phase, "state": state}

        value = None
        if state in ("done", "cancelled"):
            now = time.monotonic()
            if phase == "ttft" and self.reply_timer.t_first_token is not None:
                value = round(
                    self.reply_timer.t_first_token - self.reply_timer.t_request, 1
                )
            elif phase == "total":
                self.reply_timer.t_done = now
                value = round(now - self.reply_timer.t_request, 1)

        return {
            "type": "timer_update",
            "reply_id": self.reply_timer.reply_id,
            "phase": phase,
            "state": state,
            "value": value,
            "server_ts": time.time(),
        }
