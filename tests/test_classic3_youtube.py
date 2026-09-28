"""rc9-classic3: video detection tuned on the real laptop, and "watching YouTube"."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path
import inspect
import sys
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from event_memory import EventMemory
from pet_context import ContextBuilder
from self_test import snap
from voice_engine import VoiceEngine
import window_titles
from window_titles import youtube_among, youtube_on_screen


def run(seconds, start=0.0, builder=None, **kw):
    b = builder or ContextBuilder()
    ctx = None
    for i in range(seconds + 1):
        ctx = b.update(snap(start + i, **kw), start + i, local_hour=15)
    return b, ctx


class TitleTests(unittest.TestCase):
    def test_only_a_video_page_in_a_browser_counts(self):
        self.assertTrue(youtube_among([("chrome.exe", "고양이 영상 - YouTube")]))
        self.assertTrue(youtube_among([("explorer.exe", "x"), ("MSEDGE.EXE", "Song - YouTube Music")]))
        self.assertFalse(youtube_among([("chrome.exe", "YouTube")]))                       # home page
        self.assertFalse(youtube_among([("chrome.exe", "youtube - Google 검색")]))           # a search
        self.assertFalse(youtube_among([("notepad.exe", "notes - YouTube")]))              # not a browser
        self.assertFalse(youtube_among([]))

    def test_answer_is_yes_no_only(self):
        self.assertIn(youtube_on_screen(), (True, False, None))
        # The module must not keep or log titles anywhere.
        src = inspect.getsource(window_titles)
        for word in ("diag", "log", "print(", "open("):
            self.assertNotIn(word, src.split('"""', 2)[2], word)


class VideoContextTests(unittest.TestCase):
    def test_an_ordinary_video_is_detected(self):
        # Measured 3.4-4.9 % for a normal YouTube video; the old 20 % entry never fired.
        _, ctx = run(10, gpu_video_decode=3.4)
        self.assertIn("VIDEO_ENGINE_ACTIVE", ctx.facts)
        _, ctx = run(10, gpu_video_decode=0.0)
        self.assertNotIn("VIDEO_ENGINE_ACTIVE", ctx.facts)

    def test_youtube_needs_both_the_page_and_the_decoder(self):
        _, ctx = run(10, gpu_video_decode=4.0, youtube_visible=True)
        self.assertIn("YOUTUBE_PLAYING", ctx.facts)
        self.assertEqual(ctx.face_state, "VIDEO")
        for kw in (dict(gpu_video_decode=4.0, youtube_visible=False),   # a video call, another player
                   dict(gpu_video_decode=0.0, youtube_visible=True),    # paused
                   dict(gpu_video_decode=4.0, youtube_visible=None)):   # titles unreadable
            _, ctx = run(10, **kw)
            self.assertNotIn("YOUTUBE_PLAYING", ctx.facts, kw)
            self.assertNotEqual(ctx.face_state, "VIDEO", kw)

    def test_pausing_ends_it_after_a_moment(self):
        b, ctx = run(10, gpu_video_decode=4.0, youtube_visible=True)
        b, ctx = run(3, start=11, builder=b, gpu_video_decode=0.0, youtube_visible=True)
        self.assertIn("YOUTUBE_PLAYING", ctx.facts)          # a short pause does not flicker
        b, ctx = run(12, start=15, builder=b, gpu_video_decode=0.0, youtube_visible=True)
        self.assertNotIn("YOUTUBE_PLAYING", ctx.facts)

    def test_watching_is_not_being_away(self):
        _, ctx = run(10, gpu_video_decode=4.0, youtube_visible=True, idle_seconds=900.0)
        self.assertEqual(ctx.presence, "PRESENT")
        _, ctx = run(10, gpu_video_decode=0.0, youtube_visible=True, idle_seconds=900.0)
        self.assertEqual(ctx.presence, "AWAY")

    def test_alarms_still_win_over_the_video_face(self):
        _, ctx = run(10, gpu_video_decode=4.0, youtube_visible=True, cpu=95.0)
        self.assertNotEqual(ctx.face_state, "VIDEO")


class YoutubeVoiceTests(unittest.TestCase):
    def picks(self, **kw):
        _, ctx = run(20, **kw)
        v = VoiceEngine(BASE, seed=3)
        out = Counter()
        for i in range(60):
            d = v.manual(replace(ctx, now_mono=100.0 + 200 * i, snapshot_id=500 + i), EventMemory())
            out[d.family_id if d else None] += 1
        return out

    def test_youtube_lines_only_while_watching_youtube(self):
        yt = self.picks(gpu_video_decode=4.0, youtube_visible=True)
        self.assertGreater(yt["video_youtube"], 15, yt)
        other = self.picks(gpu_video_decode=4.0, youtube_visible=False)
        self.assertFalse([f for f in other if f and "youtube" in f], other)
        self.assertGreater(other["video"], 30, other)


if __name__ == "__main__":
    unittest.main()
