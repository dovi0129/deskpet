"""rc9-classic2: idle face motion, and ASCII art that can never break."""
from __future__ import annotations

import string
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from pet_states import (ALLOWED_NON_ASCII, CALM, FRAME_W, FRAMES, PAWS, TAIL_A, TAIL_B,
                        PetStateMachine, all_glyphs)

PRINTABLE = set(string.printable) - set("\t\n\r\x0b\x0c")


def ctx(state, now=0.0, night=False):
    facts = frozenset({"TIME_NIGHT"} if night else {"TIME_AFTERNOON"})
    return SimpleNamespace(face_state=state, now_mono=now, facts=facts)


def simulate(state, *, seconds=900.0, step=0.05, night=False, override=None, seed=1, talk_every=None):
    m = PetStateMachine(seed=seed)
    m.update_context(ctx(state, 0.0, night))
    frames = []
    t = 0.0
    while t < seconds:
        if talk_every and int(t / step) % int(talk_every / step) == 0:
            m.talk(t)
        frames.append((t, m.frame(0, override=override, now=t)))
        t += step
    return frames


class AsciiIntegrityTests(unittest.TestCase):
    def assert_frame_ok(self, frame, where):
        rows = frame.split("\n")
        self.assertEqual(len(rows), 3, where)
        for r in rows:
            self.assertEqual(len(r), FRAME_W, (where, r))
            bad = {ch for ch in r if ch not in PRINTABLE and ch not in ALLOWED_NON_ASCII}
            self.assertFalse(bad, (where, r, bad))

    def test_every_produced_frame_is_7_columns_of_safe_glyphs(self):
        for state in FRAMES:
            for night in (False, True):
                for talk in (None, 3.0):
                    for t, f in simulate(state, night=night, talk_every=talk, seconds=600.0):
                        self.assert_frame_ok(f, (state, night, talk, round(t, 2)))
        for override in ("PETTING", "ANNOYED"):
            for t, f in simulate("CHILL", override=override, seconds=30.0, talk_every=2.0):
                self.assert_frame_ok(f, (override, round(t, 2)))

    def test_static_frame_table_uses_only_safe_glyphs(self):
        bad = {ch for ch in all_glyphs() if ch not in PRINTABLE and ch not in ALLOWED_NON_ASCII}
        self.assertFalse(bad, bad)

    def test_tail_and_glance_never_move_the_paws(self):
        self.assertEqual(PAWS.index(">"), TAIL_A.index(">"))
        self.assertEqual(PAWS.index(">"), TAIL_B.index(">"))
        for t, f in simulate("CHILL", seconds=900.0):
            paws = f.split("\n")[2]
            if "^" in paws and "<" in paws and not paws.startswith("<"):  # stretch spreads the arms on purpose
                self.assertEqual(paws.index(">"), 1, (t, paws))

    def test_composed_stage_keeps_cat_column_with_tail(self):
        from deskpet import DeskPet
        dummy = DeskPet.__new__(DeskPet)
        dummy.latest = None
        dummy.context = None
        plain = DeskPet._compose_stage(dummy, FRAMES["CHILL"][0]).splitlines()
        tail = DeskPet._compose_stage(dummy, " /\\_/\\ \n( ^ω^ )\n" + TAIL_A).splitlines()
        self.assertEqual([len(x) for x in plain], [len(x) for x in tail])
        self.assertEqual(plain[2].index(">"), tail[2].index(">"))
        self.assertEqual(plain[1].index("("), tail[1].index("("))

    @unittest.skipUnless(sys.platform == "win32", "Consolas glyph table is a Windows check")
    def test_consolas_has_every_glyph(self):
        import ctypes
        import ctypes.wintypes as wt
        gdi, user = ctypes.windll.gdi32, ctypes.windll.user32
        user.GetDC.restype = ctypes.c_void_p
        gdi.CreateFontW.restype = ctypes.c_void_p
        gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi.DeleteObject.argtypes = [ctypes.c_void_p]
        user.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi.GetGlyphIndicesW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int,
                                         ctypes.POINTER(wt.WORD), wt.DWORD]
        hdc = user.GetDC(None)
        font = gdi.CreateFontW(-16, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 0, 0, "Consolas")
        old = gdi.SelectObject(hdc, font)
        try:
            missing = []
            for ch in sorted(all_glyphs() - {" "}):
                idx = (wt.WORD * 1)()
                gdi.GetGlyphIndicesW(hdc, ch, 1, idx, 1)  # GGI_MARK_NONEXISTING_GLYPHS
                if idx[0] == 0xFFFF:
                    missing.append(ch)
            self.assertFalse(missing, f"not in Consolas: {missing}")
        finally:
            gdi.SelectObject(hdc, old)
            gdi.DeleteObject(font)
            user.ReleaseDC(None, hdc)

    def test_every_glyph_has_the_monospace_advance(self):
        try:
            import tkinter as tk
            import tkinter.font as tkfont
            root = tk.Tk()
        except Exception as exc:  # no display
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            root.withdraw()
            for size in (9, 12, 15, 18, 24, 30):
                f = tkfont.Font(root=root, font=("Consolas", size, "bold"))
                w = f.measure("a")
                wrong = [ch for ch in all_glyphs() if f.measure(ch) != w]
                self.assertFalse(wrong, (size, wrong))
        finally:
            root.destroy()


class MotionTests(unittest.TestCase):
    def test_idle_cat_is_not_static(self):
        distinct = {f for _, f in simulate("CHILL", seconds=600.0)}
        self.assertGreaterEqual(len(distinct), 8, distinct)

    def test_blink_is_short_and_regular(self):
        frames = simulate("CHILL", seconds=120.0, step=0.02)
        runs, start = [], None
        for t, f in frames:
            closed = "( -ω- )" in f
            if closed and start is None:
                start = t
            if not closed and start is not None:
                runs.append(t - start)
                start = None
        short = [r for r in runs if r <= 0.2]
        self.assertGreaterEqual(len(short), 10, runs)  # ~every 3-7 s over two minutes

    def test_default_faces_follow_the_persona(self):
        for state, face in (("CHILL", "( ^ω^ )"), ("WORKING", "( -ω- )")):
            frames = [f for _, f in simulate(state, seconds=600.0)]
            share = sum(face in f for f in frames) / len(frames)
            self.assertGreater(share, 0.6, (state, share))
        working = {f for _, f in simulate("WORKING", seconds=900.0)}
        self.assertTrue(any("¬_¬" in f for f in working), "working cat never side-eyes")

    def test_talking_moves_the_mouth_then_stops(self):
        m = PetStateMachine(seed=2)
        m.update_context(ctx("CHILL"))
        m._next_blink_at = m._next_action_at = 1e9  # isolate the mouth
        m.talk(10.0)
        seen = {m.frame(now=10.0 + i * 0.05).split("\n")[1] for i in range(24)}
        self.assertIn("( ^o^ )", seen)
        self.assertEqual(m.frame(now=12.0).split("\n")[1], "( ^ω^ )")

    def test_sleep_is_still_and_silent(self):
        m = PetStateMachine(seed=3)
        m.update_context(ctx("SLEEP"))
        m.talk(0.0)
        self.assertEqual(len({m.frame(now=i * 0.1) for i in range(200)}), 1)

    def test_alarm_faces_keep_their_cycle(self):
        frames = {f for _, f in simulate("HOT", seconds=10.0)}
        self.assertEqual(frames, {"\n".join(r.ljust(FRAME_W) for r in x.split("\n")) for x in FRAMES["HOT"]})

    def test_yawns_more_at_night(self):
        def yawns(night):
            return sum("( >O< )" in f for _, f in simulate("CHILL", seconds=1800.0, step=0.1, night=night))
        self.assertGreater(yawns(True), yawns(False))

    def test_deterministic_without_clock(self):
        m = PetStateMachine()
        for state in CALM:
            m.state = state
            self.assertEqual(m.frame(0), "\n".join(r.ljust(FRAME_W) for r in FRAMES[state][0].split("\n")))


if __name__ == "__main__":
    unittest.main()
