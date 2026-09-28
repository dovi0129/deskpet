"""rc9-classic3 stage A: pointer gaze/chase, pick-up, event reactions, stretch reminder."""
from __future__ import annotations

from collections import deque
from pathlib import Path
import string
import sys
from types import SimpleNamespace
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from event_memory import EventMemory
from pet_context import ContextBuilder
from pet_states import ALLOWED_NON_ASCII, FRAME_W, REACTIONS, PetStateMachine
from self_test import snap
from voice_engine import VALID_EVENT_KINDS, VoiceEngine

SAFE = (set(string.printable) - set("\t\n\r\x0b\x0c")) | ALLOWED_NON_ASCII


def fctx(state="CHILL"):
    return SimpleNamespace(face_state=state, now_mono=0.0, facts=frozenset({"TIME_AFTERNOON"}))


def ok(testcase, frame):
    rows = frame.split("\n")
    testcase.assertEqual(len(rows), 3)
    for r in rows:
        testcase.assertEqual(len(r), FRAME_W, r)
        testcase.assertTrue(set(r) <= SAFE, r)


class FaceLifeTests(unittest.TestCase):
    def test_every_reaction_frame_is_safe(self):
        for state in ("CHILL", "WORKING", "HOT", "SLEEP", "LOW_BATTERY"):
            m = PetStateMachine(seed=1)
            m.update_context(fctx(state))
            for name, dur in REACTIONS.items():
                m.react(name, 0.0)
                t = 0.0
                while t < dur + 0.2:
                    ok(self, m.frame(now=t))
                    t += 0.05

    def test_held_then_groom(self):
        m = PetStateMachine(seed=1)
        m.update_context(fctx())
        m.held = True
        f = m.frame(now=1.0)
        ok(self, f)
        self.assertIn("°ω°", f)                      # surprised at first
        self.assertEqual(f.split("\n")[0], " /\\^/\\ ")  # pinched scruff
        self.assertIn("u|u", f.split("\n")[2])
        self.assertIn("-_-", m.frame(now=2.0))       # then goes limp
        self.assertIn("¬_¬", m.frame(now=6.0))       # and sulks if it takes long
        m.carried(20, 6.0)                           # hand moves right: feet trail left
        f = m.frame(now=6.1)
        ok(self, f)
        self.assertEqual(f.split("\n")[2], " u|u   ")
        self.assertEqual(m.frame(now=6.5).split("\n")[2], "  u|u  ")  # swing settles
        m.held = False
        self.assertNotIn("u|u", m.frame(now=7.0))
        m.held = True
        self.assertIn("°ω°", m.frame(now=8.0))       # a new pickup starts surprised again
        m.held = False
        m.react("groom", 2.0)
        self.assertIn("˘ω˘", m.frame(now=2.5))
        self.assertNotIn("˘ω˘", m.frame(now=5.0))

    def test_gaze_follows_pointer_only_when_calm(self):
        m = PetStateMachine(seed=1)
        m.update_context(fctx())
        m._next_blink_at = m._next_action_at = 1e9
        m.set_gaze("left", 1.0)
        self.assertEqual(m.frame(now=1.0).split("\n")[1], "(^ω^  )")
        m.set_gaze("right", 1.1)
        self.assertEqual(m.frame(now=1.1).split("\n")[1], "(  ^ω^)")
        m.set_gaze(None, 1.2)
        self.assertEqual(m.frame(now=1.2).split("\n")[1], "( ^ω^ )")
        hot = PetStateMachine(seed=1)
        hot.update_context(fctx("HOT"))
        hot.set_gaze("left", 1.0)
        self.assertIn("( >", hot.frame(now=1.0))

    def test_chase_widens_the_eyes_briefly(self):
        m = PetStateMachine(seed=1)
        m.update_context(fctx())
        m._next_blink_at = m._next_action_at = 1e9
        m.set_gaze("right", 5.0, chase=True)
        self.assertIn("OωO", m.frame(now=5.2))
        self.assertNotIn("OωO", m.frame(now=6.5))

    def test_every_mapped_event_exists(self):
        from deskpet import DeskPet
        self.assertTrue(set(DeskPet.EVENT_REACTIONS) <= VALID_EVENT_KINDS)
        self.assertTrue(set(DeskPet.EVENT_REACTIONS.values()) <= set(REACTIONS))

    def test_pointer_shake_triggers_chase(self):
        from deskpet import DeskPet
        app = DeskPet.__new__(DeskPet)
        pos = [0, 0]
        app.root = SimpleNamespace(winfo_pointerx=lambda: pos[0], winfo_pointery=lambda: pos[1],
                                   winfo_x=lambda: 1000, winfo_y=lambda: 500)
        app.card = SimpleNamespace(cat_bbox=(80, 40, 170, 100))
        app.ui_scale = 1.0
        app._pointer_trail = deque(maxlen=12)
        app.state_machine = PetStateMachine(seed=1)
        app.state_machine.update_context(fctx())
        import deskpet
        clock = [100.0]
        real = deskpet.time.monotonic
        deskpet.time.monotonic = lambda: clock[0]
        try:
            pos[:] = [900, 540]  # left of the cat, still
            app._track_pointer()
            self.assertEqual(app.state_machine._gaze, "left")
            for i in range(8):  # shake 300 px every 60 ms right next to the cat
                clock[0] += 0.06
                pos[:] = [1100 + (300 if i % 2 else 0), 540]
                app._track_pointer()
            self.assertGreater(app.state_machine._chase_until, clock[0])
        finally:
            deskpet.time.monotonic = real


class StretchReminderTests(unittest.TestCase):
    def run_presence(self, b, mem, start, seconds, idle=0.0, step=30):
        events = []
        t = start
        ctx = None
        while t <= start + seconds:
            s = snap(t, idle_seconds=idle)
            ctx = b.update(s, t, local_hour=15)
            events += mem.update(ctx, s)
            t += step
        return ctx, events

    def test_one_event_per_hour_of_presence_and_reset_after_away(self):
        # Samples 30 s apart would be treated as sensor gaps, so step at 10 s.
        b, mem = ContextBuilder(), EventMemory()
        ctx, ev = self.run_presence(b, mem, 0, 7300, step=10)
        kinds = [e.kind for e in ev if e.kind == "SITTING_LONG"]
        self.assertEqual(len(kinds), 2)
        ctx, ev = self.run_presence(b, mem, 7310, 400, idle=600.0, step=10)  # away
        self.assertIsNone(ctx.sitting_since_mono)
        ctx, ev = self.run_presence(b, mem, 7720, 1800, step=10)
        self.assertFalse([e for e in ev if e.kind == "SITTING_LONG"])

    def test_reminder_line_is_chosen_with_duration(self):
        b, mem = ContextBuilder(), EventMemory()
        ctx, ev = self.run_presence(b, mem, 0, 3610, step=10)
        self.assertTrue([e for e in ev if e.kind == "SITTING_LONG"])
        v = VoiceEngine(BASE, seed=4)
        d = v.choose(ctx, mem, force=True)
        self.assertTrue(d.message_id.startswith("stretch_reminder_"), d.message_id)
        self.assertNotIn("{", d.text)
        if "{sit_time}" in d.template:
            self.assertIn("1시간", d.text)


if __name__ == "__main__":
    unittest.main()
