from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
import re
import time
from typing import Iterable, Optional

from event_memory import Event, EventMemory
from pet_context import PetContext


VALID_FACTS = {
    "WORK_ACTIVE", "WORK_INACTIVE", "CPU_BUSY", "GPU_BUSY", "RAM_PRESSURE",
    "VIDEO_ENGINE_ACTIVE", "YOUTUBE_PLAYING", "LOW_LOAD",
    "POWER_NO_BATTERY", "POWER_CHARGING", "POWER_DISCHARGING", "POWER_AC_IDLE",
    "POWER_AC_UNKNOWN", "POWER_UNKNOWN",
    "BATTERY_NORMAL", "BATTERY_SOON", "BATTERY_LOW", "BATTERY_CRITICAL", "BATTERY_UNKNOWN",
    "THERMAL_NORMAL", "THERMAL_WARM", "THERMAL_HOT", "THERMAL_CRITICAL",
    "THERMAL_COOLING", "THERMAL_RELIEF", "THERMAL_UNKNOWN",
    "PRESENCE_PRESENT", "PRESENCE_AWAY", "PRESENCE_UNKNOWN",
    "TOOL_CLAUDE_PRESENT", "TOOL_CODEX_PRESENT", "TOOL_SSH_PRESENT",
    "WORK_LONG",
    "AFFECTION_LOW", "AFFECTION_NORMAL", "AFFECTION_HIGH",
    "DATE_BIRTHDAY", "DATE_NEWYEAR", "DATE_CHRISTMAS", "DATE_YEAREND",
    "DATE_FRIDAY_EVENING", "DATE_MONDAY_MORNING", "DATE_WEEKEND",
    "TIME_DAWN", "TIME_MORNING", "TIME_NOON", "TIME_AFTERNOON", "TIME_EVENING", "TIME_NIGHT",
}
VALID_EVENT_KINDS = {
    "WORK_ENTER", "WORK_EXIT", "CPU_BUSY_ENTER", "CPU_BUSY_EXIT", "GPU_BUSY_ENTER", "GPU_BUSY_EXIT",
    "THERMAL_HOT_ENTER", "THERMAL_CRITICAL_ENTER", "COOLING_ENTER", "RELIEF_ENTER",
    "POWER_CONNECTED", "POWER_DISCONNECTED", "CHARGING_STARTED", "CHARGING_STOPPED",
    "BATTERY_SOON_ENTER", "BATTERY_LOW_ENTER", "BATTERY_CRITICAL_ENTER",
    "TOOL_APPEARED", "TOOL_DISAPPEARED", "USER_RETURNED", "SITTING_LONG",
}
VALID_TOOLS = {"Claude", "Codex", "SSH"}

TEMP_TRUSTED_CONFIDENCE = {"HIGH", "MEDIUM"}
MIN_WORK_TIME_S = 600.0
MIN_AWAY_TIME_S = 300.0
_SLOT_RE = re.compile(r"\{([a-z_]+)\}")


def format_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}분"
    return f"{minutes // 60}시간"


def _slot_temp(ctx: PetContext, now: float) -> Optional[str]:
    t = ctx.temp_average_c
    if t is None or not math.isfinite(t) or str(ctx.temp_confidence).upper() not in TEMP_TRUSTED_CONFIDENCE:
        return None
    return str(int(round(t)))


def _slot_percent(value: Optional[float]) -> Optional[str]:
    if value is None or not math.isfinite(value) or not 0 <= value <= 100:
        return None
    return str(int(round(value)))


def _slot_work_time(ctx: PetContext, now: float) -> Optional[str]:
    if ctx.work_since_mono is None or now - ctx.work_since_mono < MIN_WORK_TIME_S:
        return None
    return format_duration(now - ctx.work_since_mono)


def _slot_sit_time(ctx: PetContext, now: float) -> Optional[str]:
    since = getattr(ctx, "sitting_since_mono", None)
    if since is None or now - since < 3600.0:
        return None
    return format_duration(now - since)


def _slot_away_time(ctx: PetContext, now: float) -> Optional[str]:
    if ctx.last_away_s is None or ctx.last_away_s < MIN_AWAY_TIME_S:
        return None
    return format_duration(ctx.last_away_s)


# name -> (getter, longest plausible rendering used for the 30-character check)
SLOTS = {
    "temp": (_slot_temp, "105"),
    "battery": (lambda ctx, now: _slot_percent(ctx.battery_percent), "100"),
    "cpu": (lambda ctx, now: _slot_percent(ctx.cpu_percent), "100"),
    "gpu": (lambda ctx, now: _slot_percent(ctx.gpu_percent), "100"),
    "work_time": (_slot_work_time, "10시간"),
    "away_time": (_slot_away_time, "10시간"),
    "sit_time": (_slot_sit_time, "10시간"),
}
MAX_TEXT_LEN = 30


@dataclass(frozen=True)
class CatalogMessage:
    id: str
    intent: str
    text: str
    family: str
    requires_all: tuple[str, ...]
    requires_any: tuple[str, ...]
    forbids: tuple[str, ...]
    claims: tuple[str, ...]
    event_kind: str = ""
    event_subject: str = ""
    event_max_age_s: float = 0.0
    priority: int = 30
    weight: float = 1.0
    cooldown_s: float = 60.0
    ttl_s: float = 10.0
    slots: tuple[str, ...] = ()

    def render(self, ctx: PetContext, now: float) -> Optional[str]:
        """Fill placeholders, or None when any value is unknown/untrusted."""
        if not self.slots:
            return self.text
        values = {}
        for name in self.slots:
            v = SLOTS[name][0](ctx, now)
            if v is None:
                return None
            values[name] = v
        return _SLOT_RE.sub(lambda m: values[m.group(1)], self.text)


@dataclass(frozen=True)
class SpeechDecision:
    message_id: str
    text: str
    family_id: str
    source: str
    priority: int
    created_at_mono: float
    expires_at_mono: float
    snapshot_id: int
    required_facts: tuple[str, ...]
    forbidden_facts: tuple[str, ...]
    required_any_facts: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    repeat_relaxed: bool = False
    candidates_before_repeat: int = 0
    candidates_after_repeat: int = 0
    reason: str = ""
    template: str = ""


SAFE_DEFAULTS = [
    CatalogMessage("safe_observing_01", "SAFE_DEFAULT", "잠깐 살펴보는 중", "safe_observing", (), (), (), (), priority=5, cooldown_s=120),
]


class VoiceCatalog:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.errors: list[str] = []
        self.messages: list[CatalogMessage] = []
        self._load()

    @staticmethod
    def _message_from_dict(raw: dict) -> CatalogMessage:
        mid = str(raw.get("id") or "").strip()
        intent = str(raw.get("intent") or "").strip().upper()
        text = re.sub(r"\s+", " ", str(raw.get("text") or "").strip())
        family = str(raw.get("family") or mid).strip()
        if not mid or not intent or not text or not family:
            raise ValueError("message requires id/intent/text/family")
        slots = tuple(dict.fromkeys(_SLOT_RE.findall(text)))
        unknown_slots = set(slots) - set(SLOTS)
        if unknown_slots:
            raise ValueError(f"{mid}: unknown placeholder {sorted(unknown_slots)}")
        widest = _SLOT_RE.sub(lambda m: SLOTS[m.group(1)][1], text)
        if "{" in widest or "}" in widest:
            raise ValueError(f"{mid}: malformed placeholder")
        if len(widest) > MAX_TEXT_LEN:
            raise ValueError(f"{mid}: text too long")
        if re.search(r"(?:Desk\s*Pet|Assistant|답변)\s*[:：]", text, re.I):
            raise ValueError(f"{mid}: speaker prefix not allowed")
        requires_all = tuple(str(x).strip().upper() for x in raw.get("requires_all", []) or [])
        requires_any = tuple(str(x).strip().upper() for x in raw.get("requires_any", []) or [])
        forbids = tuple(str(x).strip().upper() for x in raw.get("forbids", []) or [])
        claims = tuple(str(x).strip().upper() for x in raw.get("claims", []) or [])
        all_facts = set(requires_all + requires_any + forbids + claims)
        unknown = all_facts - VALID_FACTS
        if unknown:
            raise ValueError(f"{mid}: unknown facts {sorted(unknown)}")
        if set(requires_all) & set(forbids):
            raise ValueError(f"{mid}: fact both required and forbidden")
        event = raw.get("event") or {}
        ek = str(event.get("kind") or "").strip().upper()
        es = str(event.get("subject") or "").strip()
        ema = float(event.get("max_age_s") or 0.0)
        if ek and ek not in VALID_EVENT_KINDS:
            raise ValueError(f"{mid}: unknown event kind {ek}")
        if es and es not in VALID_TOOLS:
            raise ValueError(f"{mid}: unknown event subject {es}")
        return CatalogMessage(
            id=mid,
            intent=intent,
            text=text,
            family=family,
            requires_all=requires_all,
            requires_any=requires_any,
            forbids=forbids,
            claims=claims,
            event_kind=ek,
            event_subject=es,
            event_max_age_s=ema,
            priority=int(raw.get("priority", 30)),
            weight=max(0.01, float(raw.get("weight", 1.0))),
            cooldown_s=max(0.0, float(raw.get("cooldown_s", 60.0))),
            ttl_s=max(1.0, min(30.0, float(raw.get("ttl_s", 10.0)))),
            slots=slots,
        )

    def _load_file(self, path: Path, *, overrides: bool = False) -> list[CatalogMessage]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or int(data.get("schema_version", 0)) != 1:
            raise ValueError(f"{path.name}: unsupported schema")
        raws = data.get("messages")
        if not isinstance(raws, list):
            raise ValueError(f"{path.name}: messages must be list")
        return [self._message_from_dict(x) for x in raws if isinstance(x, dict)]

    def _load(self) -> None:
        base = self.base_dir / "voice_catalog.json"
        try:
            loaded = self._load_file(base)
            ids = set()
            valid = []
            for m in loaded:
                if m.id in ids:
                    raise ValueError(f"duplicate id {m.id}")
                ids.add(m.id)
                valid.append(m)
            self.messages = valid
        except Exception as exc:
            self.errors.append(f"base catalog: {type(exc).__name__}: {exc}")
            self.messages = list(SAFE_DEFAULTS)

        user = self.base_dir / "voice_catalog.user.json"
        if user.exists():
            try:
                overrides = self._load_file(user, overrides=True)
                by_id = {m.id: m for m in self.messages}
                for m in overrides:
                    if m.id not in by_id:
                        raise ValueError(f"user override unknown id {m.id}")
                    original = by_id[m.id]
                    # User overrides may change wording/weight/cooldown but not remove facts.
                    if set(original.requires_all) - set(m.requires_all) or set(original.forbids) - set(m.forbids):
                        raise ValueError(f"{m.id}: user override cannot weaken required facts")
                    by_id[m.id] = m
                self.messages = list(by_id.values())
            except Exception as exc:
                self.errors.append(f"user catalog: {type(exc).__name__}: {exc}")


class VoiceEngine:
    LEVEL_INTERVALS = {"quiet": 180.0, "normal": 60.0, "chatty": 30.0}
    EVENT_INTERVALS = {"quiet": 15.0, "normal": 10.0, "chatty": 5.0}

    def __init__(
        self,
        base_dir: Path,
        *,
        level: str = "normal",
        recent_limit: int = 5,
        family_cooldown_s: float = 60.0,
        seed: Optional[int] = None,
    ) -> None:
        self.catalog = VoiceCatalog(base_dir)
        self.level = level if level in self.LEVEL_INTERVALS else "normal"
        self.recent_limit = max(1, min(12, int(recent_limit)))
        self.family_cooldown_s = max(0.0, float(family_cooldown_s))
        self.rng = random.Random(seed)
        self._recent_ids: deque[str] = deque(maxlen=self.recent_limit)
        self._recent_texts: deque[str] = deque(maxlen=self.recent_limit)
        self._family_last: dict[str, float] = {}
        self._message_last: dict[str, float] = {}
        self._consumed_events: set[str] = set()
        self.current: Optional[SpeechDecision] = None
        self.last_decision_trace: deque[dict] = deque(maxlen=200)
        self._last_change_at = 0.0
        self._last_state_speech_at = -1e9
        self._last_event_speech_at = -1e9

    def set_level(self, level: str) -> None:
        if level in self.LEVEL_INTERVALS:
            self.level = level

    @property
    def recent_texts(self) -> tuple[str, ...]:
        return tuple(self._recent_texts)

    @staticmethod
    def fact_truth(ctx: PetContext, fact: str) -> Optional[bool]:
        fact = fact.upper()
        if fact in ctx.facts:
            return True
        if fact.startswith("POWER_"):
            if ctx.power == "UNKNOWN": return None
            return fact == f"POWER_{ctx.power}"
        if fact.startswith("BATTERY_"):
            if ctx.battery_band == "UNKNOWN": return None
            return fact == f"BATTERY_{ctx.battery_band}"
        if fact.startswith("THERMAL_"):
            if ctx.thermal == "UNKNOWN": return None
            return fact == f"THERMAL_{ctx.thermal}"
        if fact.startswith("PRESENCE_"):
            if ctx.presence == "UNKNOWN": return None
            return fact == f"PRESENCE_{ctx.presence}"
        if fact == "WORK_ACTIVE":
            if ctx.load == "UNKNOWN": return None
            return ctx.load in {"WORKING", "CPU_BUSY", "GPU_BUSY", "BOTH_BUSY"}
        if fact == "WORK_INACTIVE":
            if ctx.load == "UNKNOWN": return None
            return ctx.load == "IDLE"
        if fact == "CPU_BUSY":
            if ctx.load == "UNKNOWN": return None
            return ctx.load in {"CPU_BUSY", "BOTH_BUSY"}
        if fact == "GPU_BUSY":
            if ctx.load == "UNKNOWN": return None
            return ctx.load in {"GPU_BUSY", "BOTH_BUSY"}
        if fact == "RAM_PRESSURE":
            if ctx.memory == "UNKNOWN": return None
            return ctx.memory == "PRESSURE"
        if fact == "LOW_LOAD":
            if ctx.load == "UNKNOWN": return None
            return ctx.load == "IDLE"
        if fact in ("VIDEO_ENGINE_ACTIVE", "YOUTUBE_PLAYING"):
            # This fact is emitted only when TRUE. If work sensors are otherwise
            # known, absence means false; with unknown load preserve UNKNOWN.
            return None if ctx.load == "UNKNOWN" else False
        if fact.startswith("TIME_"):
            # Exactly one daypart fact is present whenever the local clock was read.
            if not any(f.startswith("TIME_") for f in ctx.facts): return None
            return False
        if fact == "WORK_LONG":
            if ctx.load == "UNKNOWN": return None
            return False
        if fact.startswith("TOOL_") and fact.endswith("_PRESENT"):
            if ctx.tools_scan_status != "OK": return None
            tool = fact[len("TOOL_"):-len("_PRESENT")].title()
            if tool == "Ssh": tool = "SSH"
            return tool in ctx.tools_present
        return None

    def _event_for(self, m: CatalogMessage, events: Iterable[Event], now: float) -> Optional[Event]:
        if not m.event_kind:
            return None
        for e in reversed(list(events)):
            if e.event_id in self._consumed_events:
                continue
            if e.kind != m.event_kind:
                continue
            if m.event_subject and e.subject != m.event_subject:
                continue
            if m.event_max_age_s and e.age(now) > m.event_max_age_s:
                continue
            if not e.valid(now):
                continue
            return e
        return None

    def _eligible(self, m: CatalogMessage, ctx: PetContext, events: Iterable[Event], now: float) -> tuple[bool, Optional[Event]]:
        for f in m.requires_all:
            if self.fact_truth(ctx, f) is not True:
                return False, None
        if m.requires_any:
            if not any(self.fact_truth(ctx, f) is True for f in m.requires_any):
                return False, None
        for f in m.forbids:
            # Forbids must be explicitly false; UNKNOWN is not safe enough.
            if self.fact_truth(ctx, f) is not False:
                return False, None
        event = self._event_for(m, events, now)
        if m.event_kind and event is None:
            return False, None
        # Claims must not exceed known TRUE facts.
        for f in m.claims:
            if self.fact_truth(ctx, f) is not True:
                return False, None
        return True, event

    def decision_valid(self, d: Optional[SpeechDecision], ctx: PetContext, events: Iterable[Event], now: float) -> bool:
        if d is None or now > d.expires_at_mono:
            return False
        for f in d.required_facts:
            if self.fact_truth(ctx, f) is not True:
                return False
        if d.required_any_facts and not any(self.fact_truth(ctx, f) is True for f in d.required_any_facts):
            return False
        for f in d.forbidden_facts:
            if self.fact_truth(ctx, f) is not False:
                return False
        valid_ids = {e.event_id for e in events if e.valid(now)}
        if d.event_ids and not all(eid in valid_ids for eid in d.event_ids):
            return False
        return True

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[\s~.!?…。·,_-]+", "", text.lower())

    def _pick_weighted(self, items: list[tuple[CatalogMessage, Optional[Event]]]) -> tuple[CatalogMessage, Optional[Event]]:
        weights = [m.weight for m, _ in items]
        return self.rng.choices(items, weights=weights, k=1)[0]

    def choose(
        self,
        ctx: PetContext,
        event_memory: EventMemory,
        *,
        force: bool = False,
        reason: str = "sample",
    ) -> Optional[SpeechDecision]:
        now = ctx.now_mono
        events = event_memory.valid_events(now)
        self._consumed_events.intersection_update(e.event_id for e in events)
        current_facts_valid = self._decision_facts_valid(self.current, ctx, events)
        current_visible = current_facts_valid and self.current is not None and now <= self.current.expires_at_mono

        eligible: list[tuple[CatalogMessage, Optional[Event]]] = []
        rendered: dict[str, str] = {}
        for m in self.catalog.messages:
            ok, event = self._eligible(m, ctx, events, now)
            if not ok:
                continue
            text = m.render(ctx, now)
            if text is None:
                continue
            rendered[m.id] = text
            eligible.append((m, event))
        if not eligible:
            eligible = [(SAFE_DEFAULTS[0], None)]
            rendered[SAFE_DEFAULTS[0].id] = SAFE_DEFAULTS[0].text

        # Highest truthful priority wins.  Randomness is used only to choose how
        # to phrase that already-established intent.
        max_priority = max(m.priority for m, _ in eligible)
        top = [(m, e) for m, e in eligible if m.priority == max_priority]
        top_has_event = any(e is not None for _, e in top)

        if current_visible and not force:
            cur_pri = self.current.priority if self.current else -1
            if max_priority <= cur_pri:
                return self.current

        # A displayed line expires after its TTL, but a stable situation should
        # not immediately pick another line.  Stay quiet until the configured
        # cadence, unless a new higher-priority event/state arrived or the user
        # explicitly asked for a line.
        if not force:
            previous_priority = self.current.priority if self.current is not None else -1
            important_state_change = (
                self.current is not None
                and (max_priority > previous_priority or (not current_facts_valid and max_priority >= previous_priority))
            )
            if top_has_event:
                if now - self._last_event_speech_at < self.EVENT_INTERVALS[self.level]:
                    non_event = [(m, e) for m, e in eligible if e is None]
                    if not non_event:
                        return self.current if current_visible else None
                    non_max = max(m.priority for m, _ in non_event)
                    top = [(m, e) for m, e in non_event if m.priority == non_max]
                    top_has_event = False
            if (
                not top_has_event
                and not important_state_change
                and now - self._last_state_speech_at < self.LEVEL_INTERVALS[self.level]
            ):
                return self.current if current_visible else None

        before_repeat = len(top)
        # Repetition is judged on the template, so "{temp}도" with a new number
        # still counts as the same line.
        normalized_recent = {self._normalize(x) for x in self._recent_texts}
        non_repeat = []
        for m, e in top:
            if m.id in self._recent_ids:
                continue
            if self._normalize(m.text) in normalized_recent:
                continue
            if now - self._message_last.get(m.id, -1e9) < m.cooldown_s:
                continue
            non_repeat.append((m, e))

        # Family cooldown is a preference, not a reason to repeat one of the last
        # five lines when another truthful sentence in the same intent exists.
        family_fresh = [
            (m, e) for m, e in non_repeat
            if now - self._family_last.get(m.family, -1e9) >= self.family_cooldown_s
        ]
        repeat_relaxed = False
        if family_fresh:
            pool = family_fresh
        elif non_repeat:
            pool = non_repeat
        else:
            # Relax repetition/cooldown only. Truth conditions and priority stay
            # fixed, so a fresh but false line can never win over repetition.
            pool = top
            repeat_relaxed = True
        fresh = non_repeat

        m, event = self._pick_weighted(pool)
        event_ids = (event.event_id,) if event is not None else ()
        source = "RULE_EVENT" if event else ("RULE_COMBO" if len(m.requires_all) >= 2 else "RULE_STATE")
        d = SpeechDecision(
            message_id=m.id,
            text=rendered[m.id],
            family_id=m.family,
            source=source,
            priority=m.priority,
            created_at_mono=now,
            expires_at_mono=now + m.ttl_s,
            snapshot_id=ctx.snapshot_id,
            required_facts=tuple(dict.fromkeys(m.requires_all + m.claims)),
            required_any_facts=m.requires_any,
            forbidden_facts=m.forbids,
            event_ids=event_ids,
            repeat_relaxed=repeat_relaxed,
            candidates_before_repeat=before_repeat,
            candidates_after_repeat=len(fresh),
            reason=reason,
            template=m.text,
        )
        if not self._decision_facts_valid(d, ctx, events):
            return self.current if current_visible else None

        self.current = d
        self._recent_ids.append(m.id)
        self._recent_texts.append(m.text)
        self._family_last[m.family] = now
        self._message_last[m.id] = now
        self._last_change_at = now
        if event is not None:
            self._consumed_events.add(event.event_id)
            self._last_event_speech_at = now
        else:
            self._last_state_speech_at = now
        self.last_decision_trace.append({
            "time": now, "message_id": m.id, "text": rendered[m.id], "template": m.text, "source": source,
            "priority": m.priority, "facts": sorted(ctx.facts),
            "event_ids": list(event_ids), "candidate_before": before_repeat,
            "candidate_after": len(fresh), "repeat_relaxed": repeat_relaxed, "reason": reason,
        })
        return d

    def _decision_facts_valid(
        self, d: Optional[SpeechDecision], ctx: PetContext, events: Iterable[Event]
    ) -> bool:
        if d is None:
            return False
        for f in d.required_facts:
            if self.fact_truth(ctx, f) is not True:
                return False
        if d.required_any_facts and not any(self.fact_truth(ctx, f) is True for f in d.required_any_facts):
            return False
        for f in d.forbidden_facts:
            if self.fact_truth(ctx, f) is not False:
                return False
        if d.event_ids:
            valid_ids = {e.event_id for e in events if e.valid(ctx.now_mono)}
            if not all(eid in valid_ids for eid in d.event_ids):
                return False
        return True

    def supports_decision(self, d: Optional[SpeechDecision], ctx: PetContext, event_memory: EventMemory) -> bool:
        """Check present evidence separately from the original display TTL."""
        return self._decision_facts_valid(d, ctx, event_memory.valid_events(ctx.now_mono))

    def visible(self, ctx: PetContext, event_memory: EventMemory) -> Optional[SpeechDecision]:
        d = self.current
        if d is None or ctx.now_mono > d.expires_at_mono:
            return None
        return d if self._decision_facts_valid(d, ctx, event_memory.valid_events(ctx.now_mono)) else None

    def manual(self, ctx: PetContext, event_memory: EventMemory) -> Optional[SpeechDecision]:
        return self.choose(ctx, event_memory, force=True, reason="manual")
