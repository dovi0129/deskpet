"""rc9-classic3 stage C: large cat (5 x 11) with sit / loaf / lie poses."""
from __future__ import annotations

from pathlib import Path
import string
import sys
from types import SimpleNamespace
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from pet_states import (ALLOWED_NON_ASCII, FRAMES, LARGE_H, LARGE_W, POSES, REACTIONS, PetStateMachine,
                        all_glyphs, enlarge)

SAFE = (set(string.printable) - set("\t\n\r\x0b\x0c")) | ALLOWED_NON_ASCII


def machine(state, night=False, seed=1):
    m = PetStateMachine(seed=seed)
    m.size = "large"
    m.update_context(SimpleNamespace(face_state=state, now_mono=0.0,
                                     facts=frozenset({"TIME_NIGHT"} if night else {"TIME_AFTERNOON"})))
    return m


class LargeFrameTests(unittest.TestCase):
    def assert_ok(self, frame, where):
        rows = frame.split("\n")
        self.assertEqual(len(rows), LARGE_H, where)
        for r in rows:
            self.assertEqual(len(r), LARGE_W, (where, r))
            self.assertTrue(set(r) <= SAFE, (where, r))

    def test_every_large_frame_is_safe(self):
        for state in FRAMES:
            for night in (False, True):
                m = machine(state, night)
                t = 0.0
                while t < 900.0:
                    if int(t * 10) % 300 == 0:
                        m.talk(t)
                    self.assert_ok(m.frame(now=t), (state, night, round(t, 1)))
                    t += 0.1
        m = machine("CHILL")
        for name in REACTIONS:
            m.react(name, 1000.0)
            self.assert_ok(m.frame(now=1000.5), name)
        m.held = True
        self.assert_ok(m.frame(now=1001.0), "held")
        for dx, t in ((0, 1002.0), (30, 1003.0), (-30, 1004.0)):
            m.carried(dx, t)
            f = m.frame(now=t + 0.1)
            self.assert_ok(f, ("carried", dx))
            rows = f.split("\n")
            self.assertEqual(rows[0].strip(), "")  # no drawn hand: a '|' there looked like a rope
            self.assertEqual(rows[1].index("^"), 5)  # pinched scruff stays centred
            self.assertEqual(rows[4].index("|") - 5, 2 * (rows[3].index("(") - 3))  # feet swing further
        self.assertTrue(all_glyphs() <= SAFE)

    def test_poses(self):
        self.assertIn("`u-u---'", machine("SLEEP").frame(now=5.0))  # sleeping cat lies on its side
        m = machine("CHILL", seed=2)
        seen = set()
        t = 0.0
        while t < 4 * 3600:
            m.frame(now=t)
            seen.add(m.pose)
            t += 5.0
        self.assertEqual(seen, set(POSES))
        hot = machine("HOT")
        hot._state_since = -1e6
        self.assertIn("`-u-u-'", hot.frame(now=500.0))  # alarms always sit up

    def test_lying_is_the_usual_pose(self):
        m = machine("CHILL", seed=3)
        counts = {p: 0 for p in POSES}
        t = 0.0
        while t < 24 * 3600:
            m.frame(now=t)
            counts[m.pose] += 1
            t += 10.0
        self.assertEqual(max(counts, key=counts.get), "lie", counts)
        self.assertLess(counts["sit"], counts["lie"] / 2, counts)

    def test_walking_is_a_side_view_that_faces_the_way(self):
        m = machine("CHILL")
        m.walking = "left"
        left = m.frame(now=10.0).split("\n")
        m.walking = "right"
        right = m.frame(now=10.0).split("\n")
        self.assertTrue(left[2].startswith("(") and left[2].endswith("`."), left)
        self.assertTrue(right[2].startswith(".'") and right[2].endswith(")"), right)
        self.assertTrue(left[3].endswith("~") and right[3].startswith("~"))  # tail trails behind
        m.walking = "left"
        steps = {m.frame(now=t / 20).split("\n")[4] for t in range(40)}
        self.assertEqual(steps, {"  /|   /|  ", "  |\\   |\\  "})  # the legs really step
        for f in (left, right):
            self.assertTrue(all(len(r) == LARGE_W and set(r) <= SAFE for r in f), f)

    def test_face_column_is_fixed_within_a_pose(self):
        for pose in POSES:
            cols = {enlarge([" /\\_/\\ ", face, paws], pose)[{"sit": 1, "loaf": 1, "lie": 2}[pose]].index("(")
                    for face in ("( ^ω^ )", "(^ω^  )", "(  ^ω^)") for paws in (" > ^ < ", " > ^ <~")}
            self.assertEqual(len(cols), 1, pose)

    def test_loaf_is_a_round_loaf_that_breathes_peeks_and_bakes(self):
        rows = enlarge([" /\\_/\\ ", "( -ω- )", " > ^ < "], "loaf")
        self.assertEqual(rows, ["   /\\_/\\   ", "  ( -ω- )  ", ".-'     '-.", "(         )", " `-u---u-'~"])
        for inhale in (False, True):
            for paws_out in (False, True):
                for cuts in range(6):
                    f = enlarge([" /\\_/\\ ", "( ^ω^ )", " > ♥ <_"], "loaf", (inhale, paws_out, cuts))
                    self.assertTrue(all(len(r) == LARGE_W and set(r) <= SAFE for r in f), f)
                    self.assertEqual(f[2][5], "♥")                   # chest mark stays centred
                    self.assertEqual(f[4][-1], "_")                  # tail flick shows
                    self.assertEqual(f[3].count("/"), min(cuts, 4))
                    self.assertEqual("u" in f[4], paws_out)
        # Over time: the back rises and falls, paws peek out sometimes, cuts appear once a minute.
        m = machine("CHILL", seed=4)
        m.pose, m._pose_until, m._loaf_since, m._state_since = "loaf", 1e9, 100.0, -1e6
        m._next_blink_at = m._next_action_at = 1e9
        backs, paws, cuts = set(), set(), []
        t = 100.0
        while t < 100.0 + 250:
            f = m.frame(now=t).split("\n")
            backs.add(f[2])
            paws.add("u" in f[4])
            cuts.append(f[3].count("/"))
            t += 0.5
        self.assertEqual(len(backs), 2)
        self.assertEqual(paws, {True, False})
        self.assertEqual((cuts[0], cuts[-1]), (0, 4))
        self.assertEqual(cuts, sorted(cuts))

    def test_faces_do_not_read_as_letters_or_sleep(self):
        for size in ("small", "large"):
            for state in ("THERMAL_PANIC", "HEAVY_LOAD", "HEAVY_CPU"):
                m = machine(state)
                m.size = size
                m.talk(0.0, 30.0)
                t = 0.0
                while t < 20.0:
                    f = m.frame(now=t)
                    for word in ("xox", "XoX", "@o@", "OoO", "ooo"):
                        self.assertNotIn(word, f, (size, state, t))
                    t += 0.05
        sleeping = {f.split("\n")[1] for f in FRAMES["SLEEP"]}
        annoyed = {f.split("\n")[1] for f in FRAMES["ANNOYED"]}
        self.assertFalse(sleeping & annoyed)

    def test_every_pose_shares_one_style(self):
        # Paws are "u" and the tail is always drawn (flicks to "_") in every resting pose.
        for pose in POSES:
            for paws, tail in ((" > ^ < ", "~"), (" > ^ <~", "~"), (" > ^ <_", "_"), (" > ! < ", "~")):
                rows = enlarge([" /\\_/\\ ", "( ^ω^ )", paws], pose)
                body = "".join(rows[3:])
                self.assertIn(tail, body, (pose, paws))
                self.assertIn("u", rows[4], (pose, paws))
                self.assertNotIn("^", rows[4], (pose, paws))

    def test_accessories_stand_on_the_floor(self):
        from deskpet import DeskPet
        dummy = DeskPet.__new__(DeskPet)
        dummy.latest = None
        dummy.context = SimpleNamespace(left_accessory=r"\ooo/", right_accessory="TERMINAL")
        frame = machine("CHILL").frame(0)
        lines = DeskPet._compose_stage(dummy, frame).splitlines()
        self.assertEqual(len(lines), LARGE_H)
        self.assertEqual(len({len(x) for x in lines}), 1)
        self.assertIn("\\ooo/", lines[-1])
        self.assertIn("'-----'", lines[-1])
        self.assertIn(".-----.", lines[-3])


if __name__ == "__main__":
    unittest.main()
