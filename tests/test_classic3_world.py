"""The walk."""
from __future__ import annotations

from pathlib import Path
import string
import sys
from types import SimpleNamespace
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from pet_states import ALLOWED_NON_ASCII, FRAME_W, WALK_B, PetStateMachine, enlarge, POSES

SAFE = (set(string.printable) - set("\t\n\r\x0b\x0c")) | ALLOWED_NON_ASCII


class WalkTests(unittest.TestCase):
    AREA = (0, 0, 1920, 1040)

    def make(self, seed=1):
        import random
        from deskpet import DeskPet
        app = DeskPet.__new__(DeskPet)
        pos = {"x": 800, "y": 500}

        def geometry(spec):
            parts = spec.split("+")
            pos["x"], pos["y"] = int(parts[1]), int(parts[2])

        app.root = SimpleNamespace(winfo_x=lambda: pos["x"], winfo_y=lambda: pos["y"], winfo_width=lambda: 250,
                                   winfo_height=lambda: 130, update_idletasks=lambda: None, geometry=geometry)
        app._monitor_work_area = lambda: self.AREA
        app._rng = random.Random(seed)
        app.saved = 0

        def save():
            app.saved += 1
        app._save_config = save
        app.ui_scale = 1.0
        app.details_visible = False
        app.collapsed_position = None
        app.drag_active = False
        app.hover_visible = False
        app.config = {"walk": {"enabled": True, "interval_min": 20}}
        app.state_machine = PetStateMachine(seed=1)
        app._walk = None
        app._next_walk_at = 0.0
        return app, pos

    def run_trip(self, app, pos, t=1.0, limit=600.0):
        app._walk_tick(t)
        self.assertIsNotNone(app._walk)
        path, pauses, was_walking = [], 0, False
        while app._walk is not None and t < limit:
            t += 0.12
            app._walk_tick(t)
            path.append((pos["x"], pos["y"]))
            walking = app.state_machine.walking is not None
            if was_walking and not walking:
                pauses += 1
            was_walking = walking
        return t, path, pauses

    def test_roams_to_new_places_inside_the_monitor_and_stays(self):
        for seed in range(1, 9):
            app, pos = self.make(seed)
            t, path, pauses = self.run_trip(app, pos)
            self.assertIsNone(app._walk, seed)
            self.assertGreaterEqual(pauses, 1, seed)
            l, top, r, b = self.AREA
            for x, y in path:  # the whole window stays on the work area
                self.assertTrue(l <= x <= r - 250 and top <= y <= b - 130, (seed, x, y))
            self.assertNotEqual((pos["x"], pos["y"]), (800, 500), seed)  # settles somewhere new
            self.assertEqual(app.saved, 1, seed)
            self.assertIsNone(app.state_machine.walking)
            self.assertGreater(app._next_walk_at, t + 60)

    def test_trips_have_several_legs_and_varied_direction(self):
        legs, dirs = [], set()
        for seed in range(1, 30):
            app, pos = self.make(seed)
            app._walk_tick(1.0)
            legs.append(app._walk["legs_left"])
            t = 1.0
            while app._walk is not None and t < 600:
                t += 0.12
                app._walk_tick(t)
                if app.state_machine.walking:
                    dirs.add(app.state_machine.walking)
        self.assertEqual(set(legs), {1, 2, 3, 4})
        self.assertEqual(dirs, {"left", "right"})

    def test_pointer_over_the_window_stops_it_where_it_is(self):
        app, pos = self.make()
        app._walk_tick(1.0)
        t = 1.0
        while app.state_machine.walking is None and t < 20:
            t += 0.12
            app._walk_tick(t)
        for _ in range(10):
            t += 0.12
            app._walk_tick(t)
        here = (pos["x"], pos["y"])
        self.assertNotEqual(here, (800, 500))
        app.hover_visible = True
        app._walk_tick(t + 0.12)
        self.assertIsNone(app._walk)
        self.assertEqual((pos["x"], pos["y"]), here)

    def test_walks_with_the_card_open_and_remembers_the_new_spot(self):
        # A monitoring cat usually has its card open; the walk must not wait for it to close.
        for seed in range(1, 6):
            app, pos = self.make(seed)
            app.details_visible = True
            app.collapsed_position = (800, 500)
            app._walk_tick(1.0)
            self.assertIsNotNone(app._walk, seed)
            t, path, _ = self.run_trip(app, pos)
            self.assertIsNone(app._walk, seed)
            self.assertNotEqual((pos["x"], pos["y"]), (800, 500), seed)
            self.assertEqual(app.collapsed_position, (pos["x"], pos["y"]), seed)

    def test_walk_now_from_the_menu_goes_and_explains_refusals(self):
        app, pos = self.make()
        said = []
        app._say = lambda line, *a: said.append(line)
        # The menu was clicked on the cat: the pointer is over the window when it starts.
        app.hover_visible = True
        app.state_machine.state = "HEAVY_CPU"   # busy, but "지금 산책" still goes
        app._start_walk(force=True)
        self.assertIsNotNone(app._walk)
        app._walk_tick(1.0)
        t = 1.0
        while app.state_machine.walking is None and t < 20:
            t += 0.12
            app._walk_tick(t)
        for _ in range(10):
            t += 0.12
            app._walk_tick(t)
        self.assertIsNotNone(app._walk)          # did not stop for the resting pointer
        self.assertNotEqual((pos["x"], pos["y"]), (800, 500))
        app.hover_visible = False
        app._walk_tick(t + 0.12)
        app.hover_visible = True                 # the pointer comes onto the cat
        app._walk_tick(t + 0.24)
        self.assertIsNone(app._walk)
        app.state_machine.state = "HOT"
        app._start_walk(force=True)
        self.assertIsNone(app._walk)
        self.assertEqual(said, ["뜨거워서 못 나간다"])

    def test_switching_the_walk_on_says_so_and_goes_soon(self):
        import time as _time
        app, pos = self.make()
        said = []
        app._say = lambda line, *a: said.append(line)
        app._sync_menu_vars = lambda: None
        app.config["walk"]["enabled"] = False
        app.toggle_walk()
        self.assertTrue(app.config["walk"]["enabled"])
        self.assertLessEqual(app._next_walk_at - _time.monotonic(), 15.5)
        app.toggle_walk()
        self.assertEqual(said, ["산책 켬. 20분쯤마다 나간다", "산책 끔. 집에 있겠다"])

    def test_disabled_or_busy_cat_does_not_walk(self):
        app, pos = self.make()
        app.config["walk"]["enabled"] = False
        app._walk_tick(1.0)
        self.assertIsNone(app._walk)
        app.config["walk"]["enabled"] = True
        app.state_machine.state = "HOT"
        app._walk_tick(1.0)
        self.assertIsNone(app._walk)

    def test_walking_frames_are_safe_in_both_sizes(self):
        m = PetStateMachine(seed=1)
        m.walking = "left"
        for t in range(40):
            f = m.frame(now=t * 0.13).split("\n")
            self.assertTrue(all(len(r) == FRAME_W and set(r) <= SAFE for r in f), f)
        for pose in POSES + ("walk_left", "walk_right"):
            for paws in (WALK_B, " > ^ < "):
                for line in enlarge([" /\\_/\\ ", "(^ω^  )", paws], pose):
                    self.assertEqual(len(line), 11, (pose, line))


if __name__ == "__main__":
    unittest.main()
