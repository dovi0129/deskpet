"""rc9-classic3 stage B: affection, daily summary, special days."""
from __future__ import annotations

from dataclasses import replace
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from affection import Affection, DAILY_CAPS
from calendar_facts import date_facts, valid_birthday
from classic_config import load_config
from daily_log import DailyLog
from event_memory import EventMemory
from pet_context import ContextBuilder
from pet_states import ALLOWED_NON_ASCII
from self_test import snap
from voice_engine import VoiceEngine
from unittest.mock import Mock


def tm(y, mo, d, h=12):
    return time.struct_time((y, mo, d, h, 0, 0, dt.date(y, mo, d).weekday(), 1, -1))


class AffectionTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.path = Path(self.d.name) / "affection.json"

    def tearDown(self):
        self.d.cleanup()

    def test_petting_is_capped_per_day(self):
        a = Affection(self.path)
        for _ in range(50):
            a.add(1.0, "pet")
        self.assertEqual(a.score, 50 + DAILY_CAPS["pet"])

    def test_persists_and_decays_for_missed_days(self):
        a = Affection(self.path, today=dt.date(2026, 9, 1))
        a.add(20.0)
        a.save()
        self.assertEqual(Affection(self.path, today=dt.date(2026, 9, 2)).score, 70.0)  # next day: no decay
        self.assertEqual(Affection(self.path, today=dt.date(2026, 9, 5)).score, 70.0 - 3 * 3)

    def test_opening_without_changes_still_counts_as_a_visit(self):
        a = Affection(self.path, today=dt.date(2026, 9, 1))
        a.add(10.0)
        a.save()
        Affection(self.path, today=dt.date(2026, 9, 2)).save()  # opened, nothing happened
        self.assertEqual(Affection(self.path, today=dt.date(2026, 9, 3)).score, 60.0)

    def test_levels_and_hearts(self):
        a = Affection(self.path)
        a.score = 10
        self.assertEqual((a.level, a.fact()), ("LOW", "AFFECTION_LOW"))
        a.score = 85
        self.assertEqual(a.level, "HIGH")
        for score in (0, 33, 50, 99, 100):
            a.score = score
            h = a.hearts()
            self.assertEqual(len(h), 5)
            self.assertTrue(set(h) <= ALLOWED_NON_ASCII, h)

    def test_corrupt_file_is_ignored(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(Affection(self.path).score, 50.0)


class CalendarTests(unittest.TestCase):
    def test_birthday_formats(self):
        for raw in ("2000.03.15", "2000-03-15", "03-15", "3/15", "2000/3/15"):
            self.assertEqual(valid_birthday(raw), "03-15", raw)
        for raw in ("", "13-01", "02-30x", 129, None, "abc"):
            self.assertEqual(valid_birthday(raw), "", raw)

    def test_config_keeps_only_month_day(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"schema_version": 6, "edition": "classic", "birthday": "2000.03.15",
                                     "cat_size": "huge", "walk": {"enabled": "yes"}}), encoding="utf-8")
            cfg = load_config(p, Mock())
        self.assertEqual(cfg["birthday"], "03-15")
        self.assertEqual(cfg["cat_size"], "small")
        self.assertFalse(cfg["walk"]["enabled"])
        self.assertEqual(cfg["walk"]["interval_min"], 3)  # default

    def test_walk_interval_accepts_three_minutes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            for value, expected in ((3, 3), (0, 3), (1, 1), (601, 3), ("5", 3)):
                p.write_text(json.dumps({"schema_version": 6, "edition": "classic",
                                         "walk": {"enabled": True, "interval_min": value}}), encoding="utf-8")
                self.assertEqual(load_config(p, Mock())["walk"]["interval_min"], expected, value)

    def test_date_facts(self):
        self.assertIn("DATE_BIRTHDAY", date_facts(tm(2027, 3, 15), "03-15"))
        self.assertNotIn("DATE_BIRTHDAY", date_facts(tm(2027, 3, 14), "03-15"))
        self.assertIn("DATE_FRIDAY_EVENING", date_facts(tm(2026, 9, 25, 19)))   # a Friday
        self.assertNotIn("DATE_FRIDAY_EVENING", date_facts(tm(2026, 9, 25, 10)))
        self.assertIn("DATE_MONDAY_MORNING", date_facts(tm(2026, 9, 28, 9)))
        self.assertIn("DATE_WEEKEND", date_facts(tm(2026, 9, 26)))
        self.assertIn("DATE_CHRISTMAS", date_facts(tm(2026, 12, 25)))


class DailyLogTests(unittest.TestCase):
    def test_counts_time_and_ignores_sleep_gaps(self):
        with tempfile.TemporaryDirectory() as d:
            log = DailyLog(Path(d), today=dt.date(2026, 9, 25))
            for _ in range(120):
                log.tick(1.0, frozenset({"WORK_ACTIVE", "CPU_BUSY"}), today=dt.date(2026, 9, 25))
            log.tick(3600.0, frozenset({"WORK_ACTIVE"}), today=dt.date(2026, 9, 25))  # resume after sleep
            self.assertEqual((log.data["work_s"], log.data["busy_s"]), (120.0, 120.0))
            log.save()
            again = DailyLog(Path(d), today=dt.date(2026, 9, 25))
            self.assertEqual(again.data["work_s"], 120.0)

    def test_summary_lines_fit_the_bubble(self):
        with tempfile.TemporaryDirectory() as d:
            log = DailyLog(Path(d))
            log.data.update(work_s=36000.0, busy_s=35999.0, hot=123, critical=45, pets=999, stretch=77)
            lines = log.summary_lines()
            self.assertEqual(len(lines), 3)
            for line in lines:
                self.assertLessEqual(len(line), 30, line)
            self.assertIn("과열", lines[2])

    def test_old_files_pruned(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "20200101.json").write_text("{}", encoding="utf-8")
            log = DailyLog(Path(d), today=dt.date(2026, 9, 25))
            log.save()
            self.assertFalse((Path(d) / "20200101.json").exists())
            self.assertIn("켜짐", log.week_table())
            rows = log.week_rows()
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["today"])
            self.assertEqual(len(rows[0]["cells"]), len(DailyLog.HEADER))
            self.assertEqual(rows[0]["cells"][0], "09-25 (금)")


class SpecialDayVoiceTests(unittest.TestCase):
    def pick(self, extra, n=40, seed=5):
        ctx = None
        b = ContextBuilder()
        for i in range(13):
            ctx = b.update(snap(i, cpu=5.0, gpu=0.0, cpu_temp_c=50.0), i, local_hour=15)
        ctx = replace(ctx, facts=ctx.facts | frozenset(extra))
        v = VoiceEngine(BASE, seed=seed)
        out = []
        for i in range(n):
            ctx = replace(ctx, now_mono=100.0 + 200 * i, snapshot_id=500 + i)
            out.append(v.manual(ctx, EventMemory()))
        return out

    def test_birthday_lines_show_up_often(self):
        picks = self.pick({"DATE_BIRTHDAY", "AFFECTION_NORMAL"})
        share = sum("birthday" in d.family_id for d in picks) / len(picks)
        self.assertGreater(share, 0.15, share)

    def test_sulky_cat_is_never_fond(self):
        picks = self.pick({"AFFECTION_LOW"}, n=60)
        self.assertFalse([d for d in picks if d.family_id.endswith("_fond")])
        self.assertTrue([d for d in picks if d.family_id.endswith("_sulky")])


class DeskPetBondTests(unittest.TestCase):
    def make(self, d):
        from deskpet import DeskPet
        from pet_states import PetStateMachine
        from collections import deque
        app = DeskPet.__new__(DeskPet)
        app.affection = Affection(Path(d) / "a.json")
        app.daily = DailyLog(Path(d) / "daily")
        app.daily.data["seen_s"] = 4000.0
        app.state_machine = PetStateMachine(seed=1)
        app.config = {"daily_summary_hour": 0}
        app._sequence = deque()
        app._sequence_next = 0.0
        app._stretch_asked_at = None
        app._last_tick_at = None
        return app

    def ctx(self, facts, presence="PRESENT", now=10.0):
        b = ContextBuilder()
        c = b.update(snap(0, cpu=5.0, gpu=0.0), 0, local_hour=20)
        return replace(c, facts=c.facts | frozenset(facts), presence=presence, now_mono=now)

    def test_birthday_greeting_once_and_summary_once(self):
        with tempfile.TemporaryDirectory() as d:
            app = self.make(d)
            c = self.ctx({"DATE_BIRTHDAY", "WORK_INACTIVE"})
            app._daily_tick(c)
            app._daily_tick(replace(c, now_mono=11.0))
            lines = [x for x, _ in app._sequence]
            self.assertEqual(lines.count("생일 축하한다"), 1)
            self.assertEqual(lines.count("오늘 결산이다. 들어라"), 1)
            self.assertGreater(app.affection.score, 50)

    def test_taking_the_break_is_rewarded(self):
        with tempfile.TemporaryDirectory() as d:
            app = self.make(d)
            app.daily.data["summary_done"] = True
            app._stretch_asked_at = 5.0
            before = app.affection.score
            app._daily_tick(self.ctx(set(), presence="AWAY", now=100.0))
            self.assertEqual(app.affection.score, before + 2)


if __name__ == "__main__":
    unittest.main()
