"""Affection (친밀도): how the cat feels about its human, 0-100, saved between runs.

It moves with things the human actually does: petting, feeding (charging) when the
battery is low, cooling the laptop down, taking a break when told to. Neglect
(critical battery, overheating, days without opening DeskPet) lowers it.
Only the score and a few daily counters are stored; no activity content.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Optional

START = 50.0
LEVELS = ((70.0, "HIGH"), (30.0, "NORMAL"), (0.0, "LOW"))
DAILY_CAPS = {"pet": 6.0, "returned": 2.0}  # max gain per day from each repeatable source
DECAY_PER_DAY = 3.0


class Affection:
    def __init__(self, path: Path, today: Optional[dt.date] = None, clock=None) -> None:
        self.path = path
        if clock is None:
            clock = (lambda: today) if today is not None else dt.date.today
        self._clock = clock
        self.score = START
        self.day = self._clock().isoformat()
        self.gained_today: dict[str, float] = {}
        self._dirty = False
        self._load(self._clock())

    # ---------------------------------------------------------------- storage
    def _load(self, today: dt.date) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            score = float(raw.get("score", START))
            last = dt.date.fromisoformat(str(raw.get("day")))
        except (OSError, ValueError, TypeError):
            return
        if not 0 <= score <= 100:
            return
        # A day or more without DeskPet: the cat sulks a little per missed day.
        missed = max(0, (today - last).days - 1)
        self.score = max(0.0, score - DECAY_PER_DAY * missed)
        if last == today and isinstance(raw.get("gained_today"), dict):
            self.gained_today = {k: float(v) for k, v in raw["gained_today"].items()
                                 if isinstance(v, (int, float))}
        # Any new day must be written back, or a day opened without changes would later
        # count as a missed day and wrongly lower the score.
        self._dirty = last != today

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"score": round(self.score, 2), "day": self.day,
                                       "gained_today": self.gained_today}), encoding="utf-8")
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError:
            pass

    # ---------------------------------------------------------------- changes
    def roll_day(self, today: Optional[dt.date] = None) -> None:
        d = (today or self._clock()).isoformat()
        if d != self.day:
            self.day = d
            self.gained_today = {}
            self._dirty = True

    def add(self, amount: float, source: str = "") -> float:
        """Apply a change; repeatable sources are capped per day. Returns the applied delta."""
        self.roll_day()
        if amount > 0 and source in DAILY_CAPS:
            room = DAILY_CAPS[source] - self.gained_today.get(source, 0.0)
            amount = max(0.0, min(amount, room))
            if amount:
                self.gained_today[source] = self.gained_today.get(source, 0.0) + amount
        before = self.score
        self.score = max(0.0, min(100.0, self.score + amount))
        if self.score != before:
            self._dirty = True
        return self.score - before

    # Event kind -> change. Keys are EventMemory kinds; see DeskPet for context-dependent ones.
    EVENT_DELTAS = {
        "USER_RETURNED": (0.5, "returned"),
        "RELIEF_ENTER": (1.0, ""),
        "BATTERY_CRITICAL_ENTER": (-2.0, ""),
        "THERMAL_CRITICAL_ENTER": (-1.0, ""),
    }

    def on_event(self, kind: str) -> float:
        if kind in self.EVENT_DELTAS:
            amount, source = self.EVENT_DELTAS[kind]
            return self.add(amount, source)
        return 0.0

    # ---------------------------------------------------------------- reading
    @property
    def level(self) -> str:
        for floor, name in LEVELS:
            if self.score >= floor:
                return name
        return "LOW"

    def hearts(self, total: int = 5) -> str:
        """'♥♥♥··' — only glyphs verified for Consolas."""
        full = int(self.score / 100.0 * total + 0.5)  # round half up: 50 -> 3 of 5
        return "♥" * full + "·" * (total - full)

    def fact(self) -> str:
        return f"AFFECTION_{self.level}"
