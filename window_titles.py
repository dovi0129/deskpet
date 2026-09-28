"""Is a YouTube video page on screen? One yes/no answer, nothing else.

A video page is titled "<video title> - YouTube"; the home page ("YouTube") and a
search for the word ("youtube - Google 검색") do not match.

Privacy: window titles are read into memory only to look for "YouTube" in a
browser window, then dropped. No title is returned, stored or logged.
"""
from __future__ import annotations

import sys
from typing import Iterable, Optional

BROWSERS = frozenset({"chrome.exe", "msedge.exe", "firefox.exe", "whale.exe", "brave.exe", "opera.exe",
                      "vivaldi.exe", "arc.exe", "zen.exe"})


def youtube_among(windows: Iterable[tuple[str, str]]) -> bool:
    """windows: (exe name, title) of windows that are on screen (visible, not minimized)."""
    return any(exe.lower() in BROWSERS and " - youtube" in title.lower() for exe, title in windows)


def _screen_windows() -> list[tuple[str, str]]:
    import ctypes
    import ctypes.wintypes as w
    import psutil

    u32 = ctypes.windll.user32
    found: list[tuple[str, str]] = []
    names: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)
    def visit(hwnd, _):
        if not u32.IsWindowVisible(hwnd) or u32.IsIconic(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        pid = w.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in names:
            try:
                names[pid.value] = psutil.Process(pid.value).name()
            except psutil.Error:
                names[pid.value] = ""
        if names[pid.value].lower() not in BROWSERS:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        found.append((names[pid.value], buf.value))
        return True

    u32.EnumWindows(visit, 0)
    return found


def youtube_on_screen() -> Optional[bool]:
    """True / False, or None when the windows cannot be read (not Windows, API failure)."""
    if sys.platform != "win32":
        return None
    try:
        return youtube_among(_screen_windows())
    except Exception:
        return None
