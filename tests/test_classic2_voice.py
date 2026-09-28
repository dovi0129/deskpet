"""rc9-classic2: catalog expansion, placeholders, daypart/WORK_LONG facts."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from event_memory import EventMemory
from pet_context import ContextBuilder
from self_test import snap
from voice_engine import VoiceCatalog, VoiceEngine, format_duration

SLOT_RE = re.compile(r"\{[a-z_]+\}")


def run(builder, start, seconds, hour=15, **kw):
    ctx = None
    for i in range(seconds + 1):
        t = start + i
        ctx = builder.update(snap(t, **kw), t, local_hour=hour)
    return ctx


def catalog_with(messages):
    d = tempfile.TemporaryDirectory()
    Path(d.name, "voice_catalog.json").write_text(
        json.dumps({"schema_version": 1, "messages": messages}, ensure_ascii=False), encoding="utf-8")
    return d


def msg(mid, text, **kw):
    m = {"id": mid, "intent": "T", "text": text, "family": mid, "requires_all": [],
         "requires_any": [], "forbids": [], "claims": [], "priority": 50, "cooldown_s": 0}
    m.update(kw)
    return m


class PlaceholderLoadTests(unittest.TestCase):
    def test_unknown_placeholder_rejects_catalog(self):
        with catalog_with([msg("a", "{nope}도")]) as d:
            cat = VoiceCatalog(Path(d))
        self.assertTrue(any("unknown placeholder" in e for e in cat.errors))

    def test_length_checked_with_widest_value(self):
        # 23 chars as written, 31 once {away_time} becomes "10시간".
        text = "가" * 27 + "{away_time}"
        with catalog_with([msg("a", text)]) as d:
            cat = VoiceCatalog(Path(d))
        self.assertTrue(any("too long" in e for e in cat.errors))

    def test_malformed_brace_rejected(self):
        with catalog_with([msg("a", "{temp 도")]) as d:
            cat = VoiceCatalog(Path(d))
        self.assertTrue(cat.errors)

    def test_duration_format(self):
        self.assertEqual(format_duration(600), "10분")
        self.assertEqual(format_duration(3599), "59분")
        self.assertEqual(format_duration(3600), "1시간")
        self.assertEqual(format_duration(2 * 3600 + 1799), "2시간")


class ContextFactTests(unittest.TestCase):
    def test_exactly_one_daypart(self):
        for hour, name in ((3, "DAWN"), (8, "MORNING"), (12, "NOON"), (15, "AFTERNOON"),
                           (19, "EVENING"), (23, "NIGHT")):
            ctx = run(ContextBuilder(), 0, 1, hour=hour)
            times = sorted(f for f in ctx.facts if f.startswith("TIME_"))
            self.assertEqual(times, [f"TIME_{name}"])
            self.assertEqual(ctx.local_hour, hour)

    def test_work_long_after_an_hour_of_continuous_work(self):
        b = ContextBuilder()
        ctx = run(b, 0, 3599, cpu=55.0)
        self.assertIn("WORK_ACTIVE", ctx.facts)
        self.assertNotIn("WORK_LONG", ctx.facts)
        ctx = run(b, 3600, 10, cpu=55.0)
        self.assertIn("WORK_LONG", ctx.facts)
        self.assertIsNotNone(ctx.work_since_mono)

    def test_work_time_resets_when_work_stops(self):
        b = ContextBuilder()
        run(b, 0, 700, cpu=55.0)
        ctx = run(b, 701, 20, cpu=5.0)
        self.assertNotIn("WORK_ACTIVE", ctx.facts)
        self.assertIsNone(ctx.work_since_mono)

    def test_away_duration_recorded_on_return(self):
        b = ContextBuilder()
        run(b, 0, 2, idle_seconds=0.0)
        run(b, 3, 2, idle_seconds=900.0)
        ctx = run(b, 6, 1, idle_seconds=1.0)
        self.assertEqual(ctx.presence, "PRESENT")
        self.assertEqual(ctx.last_away_s, 900.0)

    def test_battery_percent_only_when_valid(self):
        self.assertEqual(run(ContextBuilder(), 0, 1, battery_percent=42.0).battery_percent, 42.0)
        self.assertIsNone(run(ContextBuilder(), 0, 1, battery_percent=float("nan")).battery_percent)
        self.assertIsNone(run(ContextBuilder(), 0, 1, battery_percent=150.0).battery_percent)


class RenderTests(unittest.TestCase):
    def engine(self, messages):
        d = catalog_with(messages)
        self.addCleanup(d.cleanup)
        return VoiceEngine(Path(d.name), seed=1)

    def test_temperature_rendered_only_when_trusted(self):
        v = self.engine([msg("t", "{temp}도다"), msg("plain", "그냥 대사", priority=10)])
        ctx = run(ContextBuilder(), 0, 12, cpu_temp_c=61.4)
        d = v.manual(ctx, EventMemory())
        self.assertEqual(d.text, f"{round(ctx.temp_average_c)}도다")
        self.assertEqual(d.template, "{temp}도다")

        v = self.engine([msg("t", "{temp}도다"), msg("plain", "그냥 대사", priority=10)])
        ctx = run(ContextBuilder(), 0, 12, cpu_temp_c=61.4, cpu_temp_confidence="LOW")
        d = v.manual(ctx, EventMemory())
        self.assertEqual(d.message_id, "plain")

    def test_battery_and_percent_slots(self):
        v = self.engine([msg("b", "밥 {battery}%"), msg("c", "CPU {cpu}%", priority=40)])
        ctx = run(ContextBuilder(), 0, 2, battery_percent=37.4, cpu=88.2)
        self.assertEqual(v.manual(ctx, EventMemory()).text, "밥 37%")

    def test_unknown_value_never_rendered(self):
        v = self.engine([msg("w", "{work_time}째"), msg("plain", "대체", priority=10)])
        ctx = run(ContextBuilder(), 0, 30, cpu=55.0)  # work for 30 s < 10 min
        self.assertEqual(v.manual(ctx, EventMemory()).message_id, "plain")
        ctx = run(ContextBuilder(), 0, 700, cpu=55.0)
        self.assertEqual(v.manual(ctx, EventMemory()).text, "11분째")

    def test_repetition_judged_on_template(self):
        v = self.engine([msg("t", "{battery}% 남음"), msg("u", "다른 말")])
        b = ContextBuilder()
        first = v.manual(run(b, 0, 2, battery_percent=50.0), EventMemory())
        second = v.manual(run(b, 3, 2, battery_percent=49.0), EventMemory())
        self.assertNotEqual(first.template, second.template)


class ShippedCatalogTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads((BASE / "voice_catalog.json").read_text(encoding="utf-8"))["messages"]
        self.cat = VoiceCatalog(BASE)

    def test_loads_without_errors(self):
        self.assertFalse(self.cat.errors, self.cat.errors)
        self.assertEqual(len(self.cat.messages), len(self.raw))
        self.assertGreaterEqual(len(self.raw), 600)

    def test_no_duplicate_text(self):
        norm = [re.sub(r"[\s~.!?…。·,_-]+", "", m["text"].lower()) for m in self.raw]
        self.assertEqual(len(norm), len(set(norm)))

    def test_original_lines_preserved(self):
        base = {m["id"]: m for m in json.loads(
            (BASE / "tools" / "voice_catalog_base140.json").read_text(encoding="utf-8"))["messages"]}
        now = {m["id"]: m for m in self.raw}
        changed = sorted(i for i in base if base[i] != now.get(i))
        self.assertEqual(changed, ["battery_critical_007", "battery_critical_012", "work_enter_092"])

    def test_new_lines_keep_base_priority(self):
        base_pri = {}
        for m in self.raw:
            if "_c2_" not in m["id"]:
                base_pri.setdefault(m["intent"], m["priority"])
        for m in self.raw:
            self.assertEqual(m["priority"], base_pri[m["intent"]], m["id"])

    def test_every_intent_has_at_least_eight_lines(self):
        counts = {}
        for m in self.raw:
            counts[m["intent"]] = counts.get(m["intent"], 0) + 1
        self.assertGreaterEqual(min(counts.values()), 8, counts)

    def test_stable_idle_state_rotates_many_lines(self):
        v = VoiceEngine(BASE, seed=3, recent_limit=12)
        ctx = run(ContextBuilder(), 0, 12, cpu=5.0, gpu=0.0, cpu_temp_c=50.0, hour=15)
        mem = EventMemory()
        texts = []
        for i in range(40):
            ctx = replace(ctx, now_mono=100.0 + 200 * i, snapshot_id=1000 + i)
            texts.append(v.manual(ctx, mem).template)
        # ~60 truthful lines in this state; 40 weighted draws that skip the last 12
        # land in the mid-30s. The old catalog could never exceed 7 here.
        self.assertGreaterEqual(len(set(texts)), 30)
        for i in range(len(texts) - 12):
            self.assertNotIn(texts[i], texts[i + 1:i + 12])


if __name__ == "__main__":
    unittest.main()
