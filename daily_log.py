"""Per-day counters for the evening summary (하루 결산).

Counts only while DeskPet is running; the summary says so. Stored as one small JSON
per day under the Classic data folder and pruned after KEEP_DAYS.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Optional

KEEP_DAYS = 60
COUNTERS = ("hot", "critical", "pets", "stretch", "charges", "returns")
TIMERS = ("seen_s", "work_s", "busy_s")


def fmt_hm(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 60:
        return f"{m}분"
    return f"{m // 60}시간" + (f" {m % 60}분" if m % 60 else "")


class DailyLog:
    def __init__(self, folder: Path, today: Optional[dt.date] = None) -> None:
        self.folder = folder
        self.day = (today or dt.date.today()).isoformat()
        self.data = self._blank()
        self._load()
        self._since_save = 0.0

    def _blank(self) -> dict:
        d = {k: 0 for k in COUNTERS}
        d.update({k: 0.0 for k in TIMERS})
        d.update({"summary_done": False, "birthday_greeted": False})
        return d

    def _path(self, day: str) -> Path:
        return self.folder / f"{day.replace('-', '')}.json"

    def _load(self) -> None:
        try:
            raw = json.loads(self._path(self.day).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for k, v in raw.items():
            if k in self.data and isinstance(v, type(self.data[k])) or (k in TIMERS and isinstance(v, (int, float))):
                self.data[k] = v

    def save(self) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            p = self._path(self.day)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            os.replace(tmp, p)
            self._since_save = 0.0
            self._prune()
        except OSError:
            pass

    def _prune(self) -> None:
        cutoff = (dt.date.fromisoformat(self.day) - dt.timedelta(days=KEEP_DAYS)).strftime("%Y%m%d")
        for p in self.folder.glob("*.json"):
            if p.stem.isdigit() and p.stem < cutoff:
                try:
                    p.unlink()
                except OSError:
                    pass

    def roll(self, today: Optional[dt.date] = None) -> None:
        d = (today or dt.date.today()).isoformat()
        if d != self.day:
            self.save()
            self.day = d
            self.data = self._blank()
            self._load()

    # ---------------------------------------------------------------- updates
    def tick(self, dt_s: float, facts: frozenset, today: Optional[dt.date] = None) -> None:
        if not 0 < dt_s <= 15:  # ignore sleep gaps
            return
        self.roll(today)
        self.data["seen_s"] += dt_s
        if "WORK_ACTIVE" in facts:
            self.data["work_s"] += dt_s
        if "CPU_BUSY" in facts or "GPU_BUSY" in facts:
            self.data["busy_s"] += dt_s
        self._since_save += dt_s
        if self._since_save >= 60:
            self.save()

    def count(self, key: str, n: int = 1) -> None:
        if key in COUNTERS:
            self.roll()
            self.data[key] += n

    # ---------------------------------------------------------------- reading
    def summary_lines(self) -> list[str]:
        """Three bubble-sized lines (each <= 30 characters)."""
        d = self.data
        lines = ["오늘 결산이다. 들어라"]
        work = f"일 {fmt_hm(d['work_s'])}" + (f", 풀가동 {fmt_hm(d['busy_s'])}" if d["busy_s"] >= 60 else "")
        lines.append(work)
        tail = []
        if d["hot"] or d["critical"]:
            tail.append(f"과열 {d['hot'] + d['critical']}번")
        if d["pets"]:
            tail.append(f"쓰다듬기 {d['pets']}번")
        if d["stretch"]:
            tail.append(f"스트레칭 알림 {d['stretch']}번")
        while tail and len(", ".join(tail) + ". 수고했다") > 30:
            tail.pop()  # drop the least important item rather than cutting a word
        lines.append((", ".join(tail) + ". 수고했다") if tail else "별일 없었다. 수고했다")
        return lines

    HEADER = ("날짜", "켜짐", "일", "풀가동", "과열", "쓰담")
    WEEKDAYS = "월화수목금토일"

    def week_rows(self, days: int = 7) -> list[dict]:
        """One dict per recorded day, oldest first. Cells are strings, UI aligns them."""
        out = []
        today = dt.date.fromisoformat(self.day)
        for i in range(days - 1, -1, -1):
            day = today - dt.timedelta(days=i)
            if day.isoformat() == self.day:
                d = self.data
            else:
                try:
                    d = json.loads(self._path(day.isoformat()).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
            out.append({
                "today": day.isoformat() == self.day,
                "cells": (f"{day:%m-%d} ({self.WEEKDAYS[day.weekday()]})", fmt_hm(d.get("seen_s", 0)),
                          fmt_hm(d.get("work_s", 0)), fmt_hm(d.get("busy_s", 0)),
                          str(d.get("hot", 0) + d.get("critical", 0)), str(d.get("pets", 0))),
            })
        return out

    def week_table(self, days: int = 7) -> str:
        """Plain-text version (diagnostics). Tab-separated: no padding to misalign."""
        lines = ["\t".join(self.HEADER)] + ["\t".join(r["cells"]) for r in self.week_rows(days)]
        return "\n".join(lines + ["", "DeskPet이 켜져 있던 시간만 센다."])
