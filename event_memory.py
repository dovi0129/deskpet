from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import time
import uuid
from typing import Deque, Iterable, Optional

from pet_context import PetContext


@dataclass(frozen=True)
class Event:
    event_id: str
    session_id: str
    kind: str
    subject: str
    happened_at_mono: float
    wall_time: float
    before: str
    after: str
    snapshot_id: int
    valid_until_mono: float
    origin: str = "SENSOR"
    annotations: frozenset[str] = field(default_factory=frozenset)

    def age(self, now: float) -> float:
        return max(0.0, now - self.happened_at_mono)

    def valid(self, now: float) -> bool:
        return now <= self.valid_until_mono


class EventMemory:
    MAX_EVENTS = 64
    MAX_AGE_S = 1800.0

    def __init__(self) -> None:
        self._events: Deque[Event] = deque(maxlen=self.MAX_EVENTS)
        self._previous: Optional[PetContext] = None
        self._last_update_mono: Optional[float] = None
        self._tool_scan_seq: Optional[int] = None
        self._tool_confirmed: Optional[frozenset[str]] = None
        self._tool_candidate: Optional[frozenset[str]] = None
        self._tool_candidate_count = 0
        self._inference_overlap_until = 0.0
        self._sitting_anchor: Optional[float] = None
        self._sitting_reminders = 0

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def mark_inference_overlap(self, until_mono: float) -> None:
        self._inference_overlap_until = max(self._inference_overlap_until, float(until_mono))

    def _add(
        self,
        ctx: PetContext,
        kind: str,
        *,
        subject: str = "",
        before: str = "",
        after: str = "",
        ttl: float = 60.0,
        origin: str = "SENSOR",
    ) -> Event:
        annotations = set()
        if ctx.now_mono <= self._inference_overlap_until:
            annotations.add("SELF_INFERENCE_OVERLAP")
        event = Event(
            event_id=uuid.uuid4().hex[:12],
            session_id=ctx.session_id,
            kind=kind,
            subject=subject,
            happened_at_mono=ctx.now_mono,
            wall_time=time.time(),
            before=before,
            after=after,
            snapshot_id=ctx.snapshot_id,
            valid_until_mono=ctx.now_mono + ttl,
            origin=origin,
            annotations=frozenset(annotations),
        )
        # Debounce exact duplicates in a two-second window.
        if self._events:
            last = self._events[-1]
            if (
                last.kind == event.kind
                and last.subject == event.subject
                and last.after == event.after
                and event.happened_at_mono - last.happened_at_mono < 2.0
            ):
                return last
        self._events.append(event)
        return event

    def prune(self, now: float) -> None:
        cutoff = now - self.MAX_AGE_S
        while self._events and self._events[0].happened_at_mono < cutoff:
            self._events.popleft()

    def valid_events(self, now: float, max_age_s: Optional[float] = None) -> list[Event]:
        self.prune(now)
        out = []
        for e in self._events:
            if not e.valid(now):
                continue
            if max_age_s is not None and e.age(now) > max_age_s:
                continue
            out.append(e)
        return out

    def recent(self, now: float, limit: int = 12) -> list[Event]:
        return self.valid_events(now)[-limit:] if limit > 0 else []

    def count_recent(self, kind: str, now: float, window_s: float, subject: str = "") -> int:
        return sum(
            1
            for e in self._events
            if e.kind == kind
            and (not subject or e.subject == subject)
            and now - e.happened_at_mono <= window_s
        )

    def _process_tools(self, ctx: PetContext, snapshot) -> None:
        status = ctx.tools_scan_status.upper()
        seq = int(getattr(snapshot, "tools_scan_seq", -1))
        if status != "OK" or seq < 0 or seq == self._tool_scan_seq:
            return
        self._tool_scan_seq = seq
        current = frozenset(ctx.tools_present)
        if self._tool_confirmed is None:
            # First successful scan establishes the baseline, not an appearance event.
            self._tool_confirmed = current
            self._tool_candidate = current
            self._tool_candidate_count = 0
            return
        if current == self._tool_confirmed:
            self._tool_candidate = current
            self._tool_candidate_count = 0
            return
        if current != self._tool_candidate:
            self._tool_candidate = current
            self._tool_candidate_count = 1
            return
        self._tool_candidate_count += 1
        if self._tool_candidate_count < 2:
            return

        before = self._tool_confirmed
        after = current
        for tool in sorted(after - before):
            self._add(ctx, "TOOL_APPEARED", subject=tool, before="absent", after="present", ttl=30)
        for tool in sorted(before - after):
            self._add(ctx, "TOOL_DISAPPEARED", subject=tool, before="present", after="absent", ttl=30)
        self._tool_confirmed = current
        self._tool_candidate_count = 0

    def update(self, ctx: PetContext, snapshot=None) -> list[Event]:
        now = ctx.now_mono
        self.prune(now)
        before_ids = {e.event_id for e in self._events}

        gap = self._last_update_mono is not None and now - self._last_update_mono > 15.0
        self._last_update_mono = now
        if gap:
            self._add(ctx, "SAMPLE_GAP_RECOVERED", ttl=5, origin="SYSTEM")
            # Do not interpret the gap as a transition between before/after states.
            self._previous = ctx
            if snapshot is not None:
                self._process_tools(ctx, snapshot)
            return [e for e in self._events if e.event_id not in before_ids]

        prev = self._previous
        if prev is not None:
            # Work/load transitions.
            pwork = "WORK_ACTIVE" in prev.facts
            cwork = "WORK_ACTIVE" in ctx.facts
            if pwork != cwork:
                self._add(ctx, "WORK_ENTER" if cwork else "WORK_EXIT", before=str(pwork), after=str(cwork), ttl=60)
            for fact, enter_kind, exit_kind in (
                ("CPU_BUSY", "CPU_BUSY_ENTER", "CPU_BUSY_EXIT"),
                ("GPU_BUSY", "GPU_BUSY_ENTER", "GPU_BUSY_EXIT"),
            ):
                a, b = fact in prev.facts, fact in ctx.facts
                if a != b:
                    self._add(ctx, enter_kind if b else exit_kind, before=str(a), after=str(b), ttl=60)

            # Thermal transition events are intentionally narrow.
            if ctx.thermal != prev.thermal:
                if ctx.thermal == "HOT":
                    self._add(ctx, "THERMAL_HOT_ENTER", before=prev.thermal, after=ctx.thermal, ttl=120)
                elif ctx.thermal == "CRITICAL":
                    self._add(ctx, "THERMAL_CRITICAL_ENTER", before=prev.thermal, after=ctx.thermal, ttl=120)
                elif ctx.thermal == "COOLING":
                    self._add(ctx, "COOLING_ENTER", before=prev.thermal, after=ctx.thermal, ttl=120)
                elif ctx.thermal == "RELIEF":
                    self._add(ctx, "RELIEF_ENTER", before=prev.thermal, after=ctx.thermal, ttl=30)

            # Power transitions.
            pac = prev.power in {"CHARGING", "AC_IDLE", "AC_UNKNOWN"}
            cac = ctx.power in {"CHARGING", "AC_IDLE", "AC_UNKNOWN"}
            if pac != cac:
                self._add(ctx, "POWER_CONNECTED" if cac else "POWER_DISCONNECTED", ttl=30)
            pchg, cchg = prev.power == "CHARGING", ctx.power == "CHARGING"
            if pchg != cchg:
                self._add(ctx, "CHARGING_STARTED" if cchg else "CHARGING_STOPPED", ttl=30)

            # Battery band downward entries.
            rank = {"UNKNOWN": -1, "NORMAL": 0, "SOON": 1, "LOW": 2, "CRITICAL": 3}
            if rank.get(ctx.battery_band, -1) > rank.get(prev.battery_band, -1):
                self._add(ctx, f"BATTERY_{ctx.battery_band}_ENTER", before=prev.battery_band, after=ctx.battery_band, ttl=300)

            if prev.presence == "AWAY" and ctx.presence == "PRESENT":
                self._add(ctx, "USER_RETURNED", before="AWAY", after="PRESENT", ttl=30, origin="USER_INTERACTION")

        self._process_sitting(ctx)
        if snapshot is not None:
            self._process_tools(ctx, snapshot)
        self._previous = ctx
        return [e for e in self._events if e.event_id not in before_ids]

    SITTING_REMINDER_S = 3600.0

    def _process_sitting(self, ctx: PetContext) -> None:
        """One SITTING_LONG event per full hour of uninterrupted presence."""
        since = getattr(ctx, "sitting_since_mono", None)
        if since != self._sitting_anchor:
            self._sitting_anchor = since
            self._sitting_reminders = 0
        if since is None:
            return
        if ctx.now_mono - since >= self.SITTING_REMINDER_S * (self._sitting_reminders + 1):
            self._sitting_reminders += 1
            self._add(ctx, "SITTING_LONG", after=str(self._sitting_reminders), ttl=90, origin="USER_INTERACTION")

    @staticmethod
    def render_event(event: Event) -> str:
        if event.kind == "TOOL_APPEARED":
            return f"{event.subject} 실행됨"
        if event.kind == "TOOL_DISAPPEARED":
            return f"{event.subject} 종료됨"
        mapping = {
            "WORK_ENTER": "작업 부하 시작",
            "WORK_EXIT": "작업 부하 종료",
            "CPU_BUSY_ENTER": "CPU 고부하 진입",
            "CPU_BUSY_EXIT": "CPU 고부하 종료",
            "GPU_BUSY_ENTER": "GPU 고부하 진입",
            "GPU_BUSY_EXIT": "GPU 고부하 종료",
            "POWER_CONNECTED": "전원 연결",
            "POWER_DISCONNECTED": "전원 분리",
            "CHARGING_STARTED": "충전 시작",
            "CHARGING_STOPPED": "충전 중단",
            "THERMAL_HOT_ENTER": "고온 진입",
            "THERMAL_CRITICAL_ENTER": "매우 높은 온도 진입",
            "COOLING_ENTER": "냉각 추세 진입",
            "RELIEF_ENTER": "온도 회복",
            "USER_RETURNED": "사용자 복귀",
            "SITTING_LONG": "오래 앉아 있음",
        }
        return mapping.get(event.kind, event.kind)
