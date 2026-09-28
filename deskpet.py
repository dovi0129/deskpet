from __future__ import annotations

from collections import deque
import ctypes
import ctypes.wintypes
import json
import math
import os
from pathlib import Path
import platform
import re
import sys
import time
import tkinter as tk
from tkinter import Menu, messagebox
from typing import Optional

try:
    import psutil  # noqa: F401
except ImportError:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "DeskPet",
        "psutil이 설치되어 있지 않아.\n\n"
        "PowerShell에서 아래 명령을 한 번 실행해줘:\n\n"
        "py -m pip install psutil",
    )
    raise SystemExit(1)

from monitor import Snapshot, SystemMonitor
from sensor_service import SensorService
from resource_monitor import ResourceMonitor
from pet_states import FRAME_W, LARGE_H, LARGE_W, PetStateMachine, TICK_S
import random
from pet_context import ContextBuilder, PetContext
from event_memory import EventMemory
from voice_engine import VoiceEngine, SpeechDecision
from classic_voice import context_key, status_sentence
from diagnostics import get_diagnostics, VERSION
from diagnostics_ui import DiagnosticWindow
from cat_card import CatCard, CardModel, Row
from dataclasses import replace as dc_replace
from affection import Affection
from daily_log import DailyLog
from calendar_facts import date_facts, valid_birthday
from app_paths import data_root


APP_NAME = f"DeskPet {VERSION}"
SAMPLE_MS = 1000
# Short tick so a 0.15 s blink is visible; the canvas redraws only when a glyph changes.
ANIM_MS = 120
HOVER_POLL_MS = 120
TRANSPARENT = "#010203"
PANEL_BG = "#17171c"
PANEL_BORDER = "#34343d"
FG = "#f3f3f5"
MUTED = "#a9a9b2"
ACCENT = "#d7d7df"
PET_WIDTH = 250
PET_HEIGHT = 150
EXPANDED_PET_HEIGHT = 122
DETAIL_WIDTH = 250
DETAIL_HEIGHT = 236
DETAIL_HEIGHT_NPU = 250
DETAIL_CHARS = 25
DETAIL_CONTENT_WIDTH = 198
GRIP_BG = "#24242b"
LEFT_SLOT_W = 7
CAT_SLOT_W = 9
CAT_SLOT_W_LARGE = 13
RIGHT_SLOT_W = 7
PET_WIDTH_LARGE = 280
CALM_WALK = ("CHILL", "WORKING", "CHARGING", "WARM", "RELIEF", "RECOVERING")
# "지금 산책" goes out in any state except these, and says why instead of failing silently.
WALK_REFUSE = {"THERMAL_PANIC": "너무 뜨겁다. 산책은 나중에", "HOT": "뜨거워서 못 나간다",
               "LOW_BATTERY": "밥이 없다. 못 걷는다", "HEAVY_LOAD": "집이 난리다. 지키는 중"}
WALK_FIRST_S = 15.0  # after switching the walk on, the first stroll comes soon


_GENERIC_DEVICE_NAMES = {
    "", "system product name", "system product", "default string",
    "to be filled by o.e.m.", "to be filled by oem", "not applicable",
    "not specified", "unknown", "none",
}


def _clean_device_text(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return "" if text.lower() in _GENERIC_DEVICE_NAMES else text


def _friendly_manufacturer(value: str) -> str:
    raw = _clean_device_text(value)
    low = raw.lower()
    aliases = (
        ("asustek", "ASUS"),
        ("micro-star", "MSI"),
        ("lenovo", "Lenovo"),
        ("dell", "Dell"),
        ("hewlett-packard", "HP"),
        ("hp", "HP"),
        ("lg electronics", "LG"),
        ("acer", "Acer"),
        ("gigabyte", "Gigabyte"),
        ("samsung", "Samsung"),
        ("microsoft", "Microsoft"),
    )
    for needle, friendly in aliases:
        if needle in low:
            return friendly
    return raw


def _friendly_model(value: str) -> str:
    model = _clean_device_text(value)
    if not model:
        return ""
    # Some OEMs repeat a model code after an underscore, e.g. ABC123_ABC123.
    model = re.sub(r"(?i)\b([A-Z0-9-]{4,})_\1\b", r"\1", model)
    model = model.replace("_", " ")
    return re.sub(r"\s+", " ", model).strip()


def _looks_like_model_code(text: str) -> bool:
    text = _clean_device_text(text)
    if not text:
        return False
    compact = re.sub(r"[\s_-]", "", text)
    if len(compact) < 5:
        return False
    alpha = sum(ch.isalpha() for ch in compact)
    digits = sum(ch.isdigit() for ch in compact)
    return digits >= 2 and alpha >= 1 and (alpha + digits) / len(compact) > 0.85


def _with_manufacturer(manufacturer: str, value: str) -> str:
    value = _clean_device_text(value)
    if not value:
        return ""
    if manufacturer and manufacturer.lower() not in value.lower():
        return f"{manufacturer} {value}".strip()
    return value


def detect_device_identity() -> tuple[str, str, str]:
    """Return (friendly display name, full hardware identity, source)."""
    if sys.platform == "win32":
        try:
            import winreg

            key_path = r"HARDWARE\DESCRIPTION\System\BIOS"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                def read(name: str) -> str:
                    try:
                        return _clean_device_text(winreg.QueryValueEx(key, name)[0])
                    except OSError:
                        return ""

                manufacturer = _friendly_manufacturer(read("SystemManufacturer"))
                model = _friendly_model(read("SystemProductName"))
                family = _friendly_model(read("SystemFamily"))

            # OEM model fields are often codes such as UX5406SA while SystemFamily
            # contains the human-facing product family (e.g. Zenbook S 14).
            # Prefer that friendly family only when the model looks code-like.
            if family and (_looks_like_model_code(model) or not model):
                display_value = family
            else:
                display_value = model or family

            display = _with_manufacturer(manufacturer, display_value)
            full_model = _with_manufacturer(manufacturer, model or family)
            if full_model and family and family.lower() not in full_model.lower():
                full = f"{full_model} · {family}"
            else:
                full = full_model or display

            if display:
                return display, full or display, "Windows BIOS registry"
        except Exception:
            pass

    host = _clean_device_text(platform.node()) or _clean_device_text(os.environ.get("COMPUTERNAME"))
    if host:
        return host, host, "computer name"
    return "Windows PC", "Windows PC", "fallback"


def compact_label(text: str, limit: int = 32) -> str:
    text = re.sub(r"\s+", " ", str(text or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


class DeskPet:
    def __init__(self, *, tk_scale=None) -> None:
        self.diag = get_diagnostics()
        self._closed = False
        self._diag_window = None
        self.base_dir = Path(__file__).resolve().parent
        # Packaged DeskPet.exe: read-only files live in _internal, the config sits next to the exe.
        config_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else self.base_dir
        self.config_path = config_dir / "config.json"
        self.config = self._load_config()

        self.pet_name = str(self.config.get("pet_name") or "DeskPet").strip() or "DeskPet"
        self.device_name_override = str(self.config.get("device_name") or "").strip()
        auto_display, auto_full, auto_source = detect_device_identity()
        if self.device_name_override:
            self.device_name = self.device_name_override
            self.device_full_name = self.device_name_override
            self.device_name_source = "config override"
        else:
            self.device_name = auto_display
            self.device_full_name = auto_full
            self.device_name_source = auto_source

        self.root = tk.Tk()
        if tk_scale is not None:
            self.root.tk.call("tk", "scaling", tk_scale)
        self.ui_scale = max(.75, min(3.0, float(self.root.tk.call("tk", "scaling")) / (96.0 / 72.0)))
        for name in ("PET_WIDTH", "PET_HEIGHT", "EXPANDED_PET_HEIGHT", "DETAIL_WIDTH", "DETAIL_HEIGHT", "DETAIL_HEIGHT_NPU", "DETAIL_CONTENT_WIDTH"):
            setattr(self, name, round(globals()[name] * self.ui_scale))
        self.root.title(f"{self.pet_name} · {VERSION}")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.config.get("always_on_top", True)))
        self.root.configure(bg=PANEL_BG)
        self.transparent_enabled = bool(self.config.get("transparent", True))

        self.monitor: Optional[SensorService] = None
        self.state_machine = PetStateMachine()
        self.context_builder = ContextBuilder()
        self.event_memory = EventMemory()
        voice_cfg = self.config.get("voice") if isinstance(self.config.get("voice"), dict) else {}
        self.voice = VoiceEngine(
            self.base_dir,
            level=str(voice_cfg.get("level") or "normal"),
            recent_limit=int(voice_cfg.get("recent_limit", 12)),
            family_cooldown_s=float(voice_cfg.get("family_cooldown_s", 60)),
        )
        self.latest: Optional[Snapshot] = None
        self.context: Optional[PetContext] = None
        self.speech: Optional[SpeechDecision] = None

        self.voice_mode = "rule"
        self._context_key = None
        self._context_epoch = 0
        self._manual_display_until = 0.0
        self._last_sample_at = 0.0
        self._status_line = ""
        self._status_until = 0.0
        self._status_epoch = -1
        self.resource_monitor = ResourceMonitor(self.diag, roles=self._resource_roles)
        self.resource_monitor.start()
        self._last_display_text = None


        self.anim_tick = 0
        self.details_visible = False
        self.hover_visible = False
        self.dragged = False
        self.drag_active = False
        self.drag_start_root = (0, 0)
        self.drag_start_mouse = (0, 0)
        self.collapsed_position = None

        self.pet_until = 0.0
        self.annoyed_until = 0.0
        self.pet_clicks = deque(maxlen=10)
        self._single_click_after = None
        self._ignore_click_until = 0.0
        self._override_message_text = ""
        self._notice = ""
        self._notice_until = 0.0
        self._cat_text = ""
        self._sleep_text = ""
        self._rows: tuple = ()
        self._summary: tuple = ()
        self._extra_rows = 0  # NPU row + one more GPU row per extra GPU
        self._pointer_trail: deque = deque(maxlen=12)
        self.affection = Affection(data_root() / "affection.json")
        self.daily = DailyLog(data_root() / "daily")
        self.birthday = valid_birthday(self.config.get("birthday", ""))
        self._sequence: deque = deque()
        self._sequence_next = 0.0
        self._stretch_asked_at: Optional[float] = None
        self._last_tick_at: Optional[float] = None
        self._walk: Optional[dict] = None
        self._next_walk_at = time.monotonic() + self._walk_interval_s()

        self._build_ui()
        self._build_menu()
        self._apply_transparency()
        self._sync_menu_vars()
        self._restore_position()
        self._bind_events()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = self._report_callback_exception
        self.animate_loop()
        self.hover_poll_loop()
        self._dialogue_loop()
        # Let the cat window appear first. Advanced Windows sensors are attached
        # after Tk has entered its event loop so a slow/broken provider cannot
        # make DeskPet look like it simply vanished at startup.
        self.root.after(120, self._start_monitoring)
        if bool(self.config.get("migration_notice_pending", False)):
            self.root.after(700, self._show_migration_notice)

    def _show_migration_notice(self) -> None:
        self.config["migration_notice_pending"] = False
        self._save_config()
        self.diag.event("APP", "migration_applied", "기존 설정을 보존하고 v2.4 설정으로 이관함")

    def _write_error_log(self, exc_type, exc_value, exc_tb) -> None:
        self.diag.exception("UI", "callback_error", exc_value.with_traceback(exc_tb))

    def _report_callback_exception(self, exc_type, exc_value, exc_tb) -> None:
        self._write_error_log(exc_type, exc_value, exc_tb)
        try:
            self._set_notice(f"오류 발생: {exc_type.__name__} · 통합 진단 확인")
        except Exception:
            pass

    def _start_monitoring(self) -> None:
        try:
            self.monitor = SensorService(SystemMonitor, diagnostics=self.diag)
            self._set_notice("센서 연결 중...")
            self.sample_loop()
        except Exception as exc:
            import traceback
            self._write_error_log(type(exc), exc, exc.__traceback__)
            self._set_notice(f"센서 시작 실패: {type(exc).__name__}", hold_s=3600.0)
            # Keep the pet alive even if advanced monitoring cannot start.

    # ---------- config / window ----------
    def _load_config(self) -> dict:
        from classic_config import load_config
        return load_config(self.config_path, self.diag)

    def _topmost_enabled(self) -> bool:
        value = self.root.attributes("-topmost")
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "no"}
        return bool(value)

    def _save_config(self) -> None:
        try:
            if self.details_visible and self.collapsed_position is not None:
                save_x, save_y = self.collapsed_position
            else:
                save_x, save_y = self.root.winfo_x(), self.root.winfo_y()
            voice_cfg = self.config.setdefault("voice", {})
            voice_cfg.update({
                "level": self.voice.level,
                "recent_limit": self.voice.recent_limit,
                "family_cooldown_s": self.voice.family_cooldown_s,
                "show_transient_when_collapsed": bool(voice_cfg.get("show_transient_when_collapsed", True)),
            })
            voice_cfg["mode"] = self.voice_mode
            self.config.update({
                "schema_version": 6,
                "x": save_x, "y": save_y,
                "always_on_top": self._topmost_enabled(),
                "transparent": self.transparent_enabled,
                "pet_name": self.pet_name,
                "device_name": self.device_name_override,
                "cat_size": self.config.get("cat_size", "small"),
            })
            tmp = self.config_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.config_path)
        except Exception as exc:
            self.diag.exception("APP", "config_save_failed", exc)

    def _apply_transparency(self) -> None:
        if sys.platform == "win32" and self.transparent_enabled:
            self.root.configure(bg=TRANSPARENT)
            try:
                self.root.wm_attributes("-transparentcolor", TRANSPARENT)
            except tk.TclError:
                self.transparent_enabled = False
        else:
            try:
                self.root.wm_attributes("-transparentcolor", "")
            except tk.TclError:
                pass
            self.root.configure(bg=PANEL_BG)

        if hasattr(self, "card"):
            # The bubble and card are opaque, so they double as the drag target
            # that color-key transparency otherwise takes away.
            self.pet_frame.configure(bg=TRANSPARENT if self.transparent_enabled else PANEL_BG)
            self.card.set_transparent(self.transparent_enabled)
            self._render_card()

    @staticmethod
    def _primary_work_area() -> tuple[int, int, int, int]:
        if sys.platform == "win32":
            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = RECT()
            try:
                ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
                return rect.left, rect.top, rect.right, rect.bottom
            except Exception:
                pass
        return 0, 0, 1920, 1080

    @staticmethod
    def _virtual_screen() -> tuple[int, int, int, int]:
        if sys.platform == "win32":
            try:
                u32 = ctypes.windll.user32
                x = u32.GetSystemMetrics(76)
                y = u32.GetSystemMetrics(77)
                w = u32.GetSystemMetrics(78)
                h = u32.GetSystemMetrics(79)
                return x, y, x + w, y + h
            except Exception:
                pass
        return 0, 0, 1920, 1080

    def _restore_position(self) -> None:
        self.root.update_idletasks()
        x = self.config.get("x")
        y = self.config.get("y")
        if isinstance(x, int) and isinstance(y, int) and self._position_is_visible(x, y):
            self.root.geometry(f"{self.PET_WIDTH}x{self.PET_HEIGHT}+{x}+{y}")
            return

        left, top, right, bottom = self._primary_work_area()
        x = max(left, right - self.PET_WIDTH - 24)
        y = max(top, bottom - self.PET_HEIGHT - 24)
        self.root.geometry(f"{self.PET_WIDTH}x{self.PET_HEIGHT}+{x}+{y}")

    def _position_is_visible(self, x: int, y: int) -> bool:
        left, top, right, bottom = self._virtual_screen()
        return left - 50 <= x <= right - 30 and top - 50 <= y <= bottom - 30

    def _clamp_current_window(self) -> None:
        self.root.update_idletasks()
        left, top, right, bottom = self._virtual_screen()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        x = min(max(x, left), max(left, right - w))
        y = min(max(y, top), max(top, bottom - h))
        self.root.geometry(f"+{x}+{y}")

    # ---------- UI ----------
    ROWS_BASE = 7  # CPU RAM GPU 온도 밥 기분 작업 (+ NPU when the device exists, + a row per extra GPU)
    GPU_ROW_WARN = 75.0  # with GPU_BUSY, only the GPU rows at or above this turn amber
    # Class-level defaults: some tests build a partial DeskPet without __init__.
    _notice = ""
    _notice_until = 0.0

    def _build_ui(self) -> None:
        bg = TRANSPARENT if self.transparent_enabled else PANEL_BG
        self.pet_frame = tk.Frame(self.root, bg=bg)
        self.pet_frame.pack(anchor="n", fill="both", expand=True)
        self.card = CatCard(self.pet_frame, width=self.PET_WIDTH, scale=self.ui_scale,
                            transparent=self.transparent_enabled)
        self.stage = self.card.canvas
        self.stage.pack(anchor="n")
        self.DETAIL_WIDTH = self.PET_WIDTH
        self.PET_HEIGHT = self.card.layout_height(False, 0)
        self._apply_cat_size(str(self.config.get("cat_size", "small")), save=False)

        # Quick buttons appear only while the pointer is over the pet.
        self.quick_buttons = tk.Frame(self.stage, bg=GRIP_BG)
        self.rule_button = tk.Button(self.quick_buttons, text="말", command=self.ask_rule_voice_now,
                                     font=("Segoe UI", 8), padx=4, pady=0, bd=0,
                                     bg=GRIP_BG, fg=FG, activebackground=PANEL_BORDER, activeforeground=FG)
        self.rule_button.pack(side="left", padx=(0, 2))
        self.status_button = tk.Button(self.quick_buttons, text="상태", command=self.inspect_status_now,
                                       font=("Segoe UI", 8), padx=4, pady=0, bd=0,
                                       bg=GRIP_BG, fg=FG, activebackground=PANEL_BORDER, activeforeground=FG)
        self.status_button.pack(side="left")
        self._buttons_item = self.stage.create_window(round(6 * self.ui_scale), round(4 * self.ui_scale),
                                                      window=self.quick_buttons, anchor="nw", state="hidden")
        self._cat_text = self._compose_stage(self.state_machine.frame(0))
        self._update_details()
        self._render_card()

    def _apply_cat_size(self, size: str, save: bool = True) -> None:
        size = "large" if size == "large" else "small"
        self.config["cat_size"] = size
        self.state_machine.size = size
        base = PET_WIDTH_LARGE if size == "large" else PET_WIDTH
        self.PET_WIDTH = self.DETAIL_WIDTH = round(base * self.ui_scale)
        self.card.set_width(self.PET_WIDTH, LARGE_H if size == "large" else 3)
        self.PET_HEIGHT = self.card.layout_height(False, 0)
        self._cat_text = self._compose_stage(self.state_machine.frame(0))
        self._sync_menu_vars()
        if save:
            x, y = self.root.winfo_x(), self.root.winfo_y()
            h = self._detail_height() if self.details_visible else self.PET_HEIGHT
            self.root.geometry(f"{self.PET_WIDTH}x{h}+{x}+{y}")
            self._render_card()
            self._clamp_current_window()
            self._save_config()

    def _sync_menu_vars(self) -> None:
        """Check marks always show the real state, even if a toggle failed."""
        if not hasattr(self, "walk_var"):
            return
        walk = self.config.get("walk") if isinstance(self.config.get("walk"), dict) else {}
        self.cat_size_var.set(str(self.config.get("cat_size", "small")))
        self.walk_var.set(bool(walk.get("enabled", False)))
        self.walk_interval_var.set(int(walk.get("interval_min", 3)))
        self.transparent_var.set(bool(self.transparent_enabled))
        self.topmost_var.set(self._topmost_enabled())

    # ---------- walk (roam) ----------
    # The cat wanders where it likes: 1-4 legs to random spots on its current
    # monitor, pausing to look around, groom or yawn, and settles wherever it ends up.
    ROAM_SPEED = (30.0, 70.0)   # px/s at 100 %, per leg
    ZOOMIES_SPEED = 180.0
    ZOOMIES_CHANCE = 0.08
    LEG_DISTANCE = (150.0, 600.0)
    PAUSE_S = (1.2, 4.0)
    _rng = random.Random()

    def _walk_interval_s(self) -> float:
        walk = self.config.get("walk") if isinstance(self.config.get("walk"), dict) else {}
        base = float(walk.get("interval_min", 3)) * 60.0
        return base * self._rng.uniform(0.6, 1.4)

    def set_walk_interval(self, minutes: int) -> None:
        walk = self.config.setdefault("walk", {"enabled": False, "interval_min": 3})
        walk["interval_min"] = max(1, min(600, int(minutes)))
        self._next_walk_at = time.monotonic() + self._walk_interval_s()
        self._save_config()
        self._sync_menu_vars()

    def _say(self, line: str, hold_s: float = 5.0) -> None:
        """A short direct reply in the bubble (menu actions)."""
        now = time.monotonic()
        self._manual_display_until = now + hold_s
        self._status_line = line
        self._status_until = now + hold_s
        self._status_epoch = self._context_epoch
        self._refresh_dialogue()

    def toggle_walk(self) -> None:
        walk = self.config.setdefault("walk", {"enabled": False, "interval_min": 3})
        walk["enabled"] = not bool(walk.get("enabled", False))
        if walk["enabled"]:
            # Show that it works: the first stroll soon, then every ~interval.
            self._next_walk_at = time.monotonic() + WALK_FIRST_S
            self._say(f"산책 켬. {int(walk.get('interval_min', 3))}분쯤마다 나간다")
        else:
            self._next_walk_at = time.monotonic() + self._walk_interval_s()
            self._say("산책 끔. 집에 있겠다")
        if not walk["enabled"] and self._walk is not None:
            self._stop_walk()
        self._save_config()
        self._sync_menu_vars()

    def _walk_allowed(self) -> bool:
        # The open card is the cat's cushion: it comes along. A pointer over the
        # window (reading the card, about to click) still keeps the cat home.
        return (not self.drag_active and not self.hover_visible
                and self.state_machine.state in CALM_WALK)

    def _monitor_work_area(self) -> tuple[int, int, int, int]:
        """Work area (no taskbar) of the monitor the cat is on."""
        if sys.platform == "win32":
            try:
                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                class MONITORINFO(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT),
                                ("dwFlags", ctypes.c_ulong)]

                u32 = ctypes.windll.user32
                u32.MonitorFromPoint.restype = ctypes.c_void_p
                u32.MonitorFromPoint.argtypes = [ctypes.wintypes.POINT, ctypes.c_ulong]
                u32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
                cx = self.root.winfo_x() + self.root.winfo_width() // 2
                cy = self.root.winfo_y() + self.root.winfo_height() // 2
                mon = u32.MonitorFromPoint(ctypes.wintypes.POINT(cx, cy), 2)  # MONITOR_DEFAULTTONEAREST
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if mon and u32.GetMonitorInfoW(mon, ctypes.byref(info)):
                    r = info.rcWork
                    return r.left, r.top, r.right, r.bottom
            except Exception:
                pass
        return self._primary_work_area()

    def _start_walk(self, force: bool = False) -> None:
        """force: the "지금 산책" menu. It goes in any state but WALK_REFUSE and
        does not wait for the pointer (the menu was just clicked on the cat)."""
        if self._walk is not None or self.drag_active:
            return
        if force:
            refuse = WALK_REFUSE.get(self.state_machine.state)
            if refuse:
                self._say(refuse)
                return
        elif not self._walk_allowed():
            return
        night = self.state_machine.night
        legs = self._rng.randint(1, 2) if night else self._rng.randint(1, 4)
        self._walk = {"legs_left": legs, "phase": "pause", "pause_until": 0.0, "target": None,
                      "speed": 0.0, "last": None, "fx": float(self.root.winfo_x()), "fy": float(self.root.winfo_y()),
                      "forced": force,
                      # Stop for the pointer only once it comes onto the cat, not because it
                      # was already resting there when the walk began.
                      "hover_armed": not self.hover_visible}

    def _next_leg(self, now: float) -> None:
        w = self._walk
        self.root.update_idletasks()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        ww, wh = self.root.winfo_width(), self.root.winfo_height()
        left, top, right, bottom = self._monitor_work_area()
        max_x, max_y = max(left, right - ww), max(top, bottom - wh)
        s = self.ui_scale
        tx, ty = x, y
        for _ in range(8):
            ang = self._rng.uniform(0, 6.2832)
            dist = self._rng.uniform(*self.LEG_DISTANCE) * s
            tx = min(max(left, round(x + dist * math.cos(ang))), max_x)
            ty = min(max(top, round(y + dist * math.sin(ang) * 0.6)), max_y)
            if abs(tx - x) + abs(ty - y) >= 60 * s:
                break
        zoom = self._rng.random() < self.ZOOMIES_CHANCE
        w.update(target=(tx, ty), phase="move", last=now, fx=float(x), fy=float(y),
                 speed=(self.ZOOMIES_SPEED if zoom else self._rng.uniform(*self.ROAM_SPEED)) * s)
        w["legs_left"] -= 1
        self.state_machine.walking = "left" if tx < x else "right"

    def _stop_walk(self) -> None:
        """End the trip where the cat is now; that spot becomes its place."""
        if self._walk is None:
            return
        self._walk = None
        self.state_machine.walking = None
        self._next_walk_at = time.monotonic() + self._walk_interval_s()
        if self.details_visible:  # folding the card later should not jump back
            self.collapsed_position = (self.root.winfo_x(), self.root.winfo_y())
        self._save_config()

    def _walk_tick(self, now: float) -> None:
        walk = self.config.get("walk") if isinstance(self.config.get("walk"), dict) else {}
        if self._walk is None:
            if bool(walk.get("enabled", False)) and now >= self._next_walk_at:
                if self._walk_allowed() and not (self.state_machine.night and self._rng.random() < 0.5):
                    self._start_walk()
                else:
                    # Not a good moment: try again a bit later (sooner for short intervals).
                    self._next_walk_at = now + min(120.0, self._walk_interval_s() / 2)
            return
        w = self._walk
        if not self.hover_visible:
            w["hover_armed"] = True
        state = self.state_machine.state
        unfit = state in WALK_REFUSE if w.get("forced") else state not in CALM_WALK
        if unfit or (self.hover_visible and w.get("hover_armed", True)):
            self._stop_walk()  # something needs attention: stop right here
            return
        if w["phase"] == "pause":
            if now < w["pause_until"]:
                return
            if w["legs_left"] <= 0:
                self._stop_walk()
                return
            self._next_leg(now)
            return
        dt = min(0.3, max(0.0, now - (w["last"] or now)))
        w["last"] = now
        tx, ty = w["target"]
        dx, dy = tx - w["fx"], ty - w["fy"]
        dist = (dx * dx + dy * dy) ** 0.5
        step = w["speed"] * dt
        if dist <= step or dist < 1:
            w["fx"], w["fy"] = float(tx), float(ty)
            self.root.geometry(f"+{tx}+{ty}")
            # Arrived: look around, sometimes groom or yawn, then maybe wander on.
            w["phase"] = "pause"
            w["pause_until"] = now + self._rng.uniform(*self.PAUSE_S)
            self.state_machine.walking = None
            self.state_machine.set_gaze(self._rng.choice(("left", "right", None)), now)
            idle = self._rng.choice(("groom", "yawn", "greet", None, None, None))
            if idle:
                self.state_machine.react(idle, now)
            return
        w["fx"] += dx / dist * step
        w["fy"] += dy / dist * step
        if abs(dx) > 2:
            self.state_machine.walking = "left" if dx < 0 else "right"
        self.root.geometry(f"+{round(w['fx'])}+{round(w['fy'])}")

    def toggle_cat_size(self) -> None:
        self._apply_cat_size("small" if self.config.get("cat_size") == "large" else "large")

    def _detail_height(self) -> int:
        return self.card.layout_height(True, self.ROWS_BASE + self._extra_rows)

    def _build_menu(self) -> None:
        self.menu = Menu(self.root, tearoff=0)
        self.menu.add_command(label="상세정보 열기/닫기", command=self.toggle_details)
        self.menu.add_command(label="한마디 듣기", command=self.ask_rule_voice_now)
        self.menu.add_command(label="상태 살펴보기", command=self.inspect_status_now)
        self.menu.add_command(label="친밀도 · 결산 보기", command=self.show_daily)
        self.menu.add_separator()
        # On/off items are checkbuttons so the current state is visible (a check mark).
        walk_cfg = self.config.get("walk") if isinstance(self.config.get("walk"), dict) else {}
        self.cat_size_var = tk.StringVar(value=str(self.config.get("cat_size", "small")))
        self.walk_var = tk.BooleanVar(value=bool(walk_cfg.get("enabled", False)))
        self.transparent_var = tk.BooleanVar(value=self.transparent_enabled)
        self.topmost_var = tk.BooleanVar(value=bool(self.config.get("always_on_top", True)))
        size_menu = Menu(self.menu, tearoff=0)
        for value, label in (("small", "작게"), ("large", "크게")):
            size_menu.add_radiobutton(label=label, value=value, variable=self.cat_size_var,
                                      command=lambda: self._apply_cat_size(self.cat_size_var.get()))
        self.menu.add_cascade(label="고양이 크기", menu=size_menu)
        self.walk_interval_var = tk.IntVar(value=int(walk_cfg.get("interval_min", 3)))
        self.menu.add_checkbutton(label="산책", variable=self.walk_var, command=self.toggle_walk)
        interval_menu = Menu(self.menu, tearoff=0)
        for minutes in (3, 10, 20, 45):
            interval_menu.add_radiobutton(label=f"평균 {minutes}분마다", value=minutes,
                                          variable=self.walk_interval_var,
                                          command=lambda: self.set_walk_interval(self.walk_interval_var.get()))
        self.menu.add_cascade(label="산책 간격", menu=interval_menu)
        self.menu.add_command(label="지금 산책", command=lambda: self._start_walk(force=True))
        self.voice_level_var = tk.StringVar(value=self.voice.level)
        voice_menu = Menu(self.menu, tearoff=0)
        for value, label in (("quiet", "조용히"), ("normal", "보통"), ("chatty", "수다")):
            voice_menu.add_radiobutton(label=label, value=value, variable=self.voice_level_var, command=self.change_voice_level)
        self.menu.add_cascade(label="말수", menu=voice_menu)
        self.menu.add_separator()
        self.menu.add_command(label="통합 진단 · 로그 · ZIP 저장", command=self.show_diagnostics)
        self.menu.add_command(label="온도 교정 초기화", command=self.reset_temperature_calibration)
        self.menu.add_checkbutton(label="투명 배경", variable=self.transparent_var, command=self.toggle_transparency)
        self.menu.add_checkbutton(label="항상 위", variable=self.topmost_var, command=self.toggle_topmost)
        self.menu.add_separator()
        self.menu.add_command(label="위치 초기화", command=self.reset_position)
        self.menu.add_command(label=f"{self.pet_name} 종료", command=self.close)

    def _bind_events(self) -> None:
        for event, handler in (("<ButtonPress-1>", self.start_drag), ("<B1-Motion>", self.drag),
                               ("<ButtonRelease-1>", self.end_drag), ("<Button-3>", self.show_menu),
                               ("<Double-Button-1>", self.on_double_click)):
            self.stage.bind(event, handler, add="+")
        self.stage.tag_bind("cat", "<ButtonRelease-1>", self.on_cat_release, add="+")
        self.stage.tag_bind("cat", "<Enter>", lambda _e: self.stage.configure(cursor="hand2"))
        self.stage.tag_bind("cat", "<Leave>", lambda _e: self.stage.configure(cursor=""))
        self.root.bind("<Escape>", lambda _e: self.close())

    # ---------- interaction ----------
    def start_drag(self, event) -> None:
        if self._walk is not None:
            self._stop_walk()  # picked up mid-stroll: the trip ends in your hand
        self.dragged = False
        self.drag_active = True
        self.drag_start_mouse = (event.x_root, event.y_root)
        self._drag_last_x = event.x_root
        self.drag_start_root = (self.root.winfo_x(), self.root.winfo_y())

    def drag(self, event) -> None:
        dx = event.x_root - self.drag_start_mouse[0]
        dy = event.y_root - self.drag_start_mouse[1]
        if abs(dx) > 3 or abs(dy) > 3:
            self.dragged = True
            self.state_machine.held = True
        last_x = getattr(self, "_drag_last_x", event.x_root)
        self.state_machine.carried(event.x_root - last_x, time.monotonic())
        self._drag_last_x = event.x_root
        self.root.geometry(
            f"+{self.drag_start_root[0] + dx}+{self.drag_start_root[1] + dy}"
        )

    def end_drag(self, _event) -> None:
        self.drag_active = False
        if self.state_machine.held:
            self.state_machine.held = False
            self.state_machine.react("groom", time.monotonic())
        if self.dragged:
            # If the expanded panel itself was moved, that new position should
            # become the collapsed pet position too.
            if self.details_visible:
                self.collapsed_position = (self.root.winfo_x(), self.root.winfo_y())
            self._save_config()
        self._update_details()

    def on_cat_release(self, _event) -> None:
        if self.dragged or time.monotonic() < self._ignore_click_until:
            return
        if self._single_click_after is not None:
            try:
                self.root.after_cancel(self._single_click_after)
            except Exception:
                pass
        self._single_click_after = self.root.after(230, self.pet_cat)

    def on_double_click(self, _event=None) -> None:
        if self.dragged:
            return
        self._ignore_click_until = time.monotonic() + 0.35
        if self._single_click_after is not None:
            try:
                self.root.after_cancel(self._single_click_after)
            except Exception:
                pass
            self._single_click_after = None
        self.toggle_details()

    def pet_cat(self) -> None:
        self._single_click_after = None
        now = time.monotonic()
        self.pet_clicks.append(now)
        while self.pet_clicks and now - self.pet_clicks[0] > 2.0:
            self.pet_clicks.popleft()
        level = self.affection.level
        if len(self.pet_clicks) >= 5:
            self.annoyed_until = now + 2.5
            self.pet_until = 0.0
            self._override_message_text = "그만하라고" if level == "LOW" else "그만."
            self.affection.add(-1.0)
        else:
            self.pet_until = now + 1.2
            words = {"HIGH": ("좋다", "더 해", "골골골", "헤헤"),
                     "LOW": ("...흥", "뭐야", "갑자기?", "나쁘진 않네")}.get(level, ("좋음", "헤헤", "쓰다듬 받는 중"))
            self._override_message_text = words[len(self.pet_clicks) % len(words)]
            self.affection.add(1.0, "pet")
            self.daily.count("pets")

    def _pointer_in_pet_area(self) -> bool:
        """Use global pointer coordinates so transparent pixels count as hover.

        Windows color-key transparency removes transparent pixels from normal
        Tk hit-testing, so the window rectangle is polled instead.
        """
        try:
            px, py = self.root.winfo_pointerx(), self.root.winfo_pointery()
            x, y = self.root.winfo_x(), self.root.winfo_y()
            return x <= px < x + self.root.winfo_width() and y <= py < y + self.root.winfo_height()
        except Exception:
            return False

    EVENT_REACTIONS = {"POWER_CONNECTED": "eat", "CHARGING_STARTED": "eat", "POWER_DISCONNECTED": "startle",
                       "THERMAL_CRITICAL_ENTER": "startle", "TOOL_APPEARED": "peek", "USER_RETURNED": "greet",
                       "SITTING_LONG": "stretch", "WORK_EXIT": "yawn"}

    def _track_pointer(self) -> None:
        """Gaze toward the pointer; a fast shake nearby is a laser pointer to chase."""
        try:
            px, py = self.root.winfo_pointerx(), self.root.winfo_pointery()
        except Exception:
            return
        now = time.monotonic()
        cb = self.card.cat_bbox
        cx = self.root.winfo_x() + (cb[0] + cb[2]) / 2
        cy = self.root.winfo_y() + (cb[1] + cb[3]) / 2
        dx, dy = px - cx, py - cy
        dist = (dx * dx + dy * dy) ** 0.5
        self._pointer_trail.append((now, px, py))
        while self._pointer_trail and now - self._pointer_trail[0][0] > 0.6:
            self._pointer_trail.popleft()
        path = sum(((b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2) ** 0.5
                   for a, b in zip(self._pointer_trail, list(self._pointer_trail)[1:]))
        span = self._pointer_trail[-1][0] - self._pointer_trail[0][0] if len(self._pointer_trail) > 1 else 0
        s = self.ui_scale
        chase = span > 0.25 and path / span > 2500 * s and dist < 450 * s
        near = dist < 700 * s
        direction = None
        if near and abs(dx) > 30 * s:
            direction = "left" if dx < 0 else "right"
        self.state_machine.set_gaze(direction, now, chase=chase)

    DAILY_COUNTS = {"THERMAL_HOT_ENTER": "hot", "THERMAL_CRITICAL_ENTER": "critical",
                    "CHARGING_STARTED": "charges", "USER_RETURNED": "returns", "SITTING_LONG": "stretch"}

    def _account_event(self, e, ctx) -> None:
        """Affection and daily counters from one new event."""
        self.affection.on_event(e.kind)
        if e.kind in self.DAILY_COUNTS:
            self.daily.count(self.DAILY_COUNTS[e.kind])
        if e.kind == "CHARGING_STARTED" and ctx.battery_band in ("LOW", "CRITICAL"):
            self.affection.add(3.0)  # fed when hungry
        if e.kind == "SITTING_LONG":
            self._stretch_asked_at = ctx.now_mono

    def _daily_tick(self, ctx) -> None:
        now = ctx.now_mono
        if self._last_tick_at is not None:
            self.daily.tick(now - self._last_tick_at, ctx.facts)
        self._last_tick_at = now
        self.affection.save()  # writes only after a change, so a crash loses at most one sample
        if self._stretch_asked_at is not None:
            if now - self._stretch_asked_at > 600:
                self._stretch_asked_at = None
            elif ctx.presence == "AWAY":
                self.affection.add(2.0)  # actually took the break
                self._stretch_asked_at = None
        d = self.daily.data
        if "DATE_BIRTHDAY" in ctx.facts and ctx.presence == "PRESENT" and not d["birthday_greeted"]:
            d["birthday_greeted"] = True
            self.affection.add(5.0)
            self.state_machine.react("greet", time.monotonic())
            self._play_sequence(["생일 축하한다", "딱히 챙긴 건 아니다", "오늘은 쓰다듬어도 봐준다"])
            self.daily.save()
        hour = time.localtime().tm_hour
        if (not d["summary_done"] and hour >= int(self.config.get("daily_summary_hour", 18))
                and "WORK_INACTIVE" in ctx.facts and ctx.presence == "PRESENT" and d["seen_s"] >= 1800):
            d["summary_done"] = True
            self._play_sequence(self.daily.summary_lines())
            self.daily.save()

    def _play_sequence(self, lines: list[str], each_s: float = 5.0) -> None:
        self._sequence.extend((line, each_s) for line in lines)
        self._sequence_next = 0.0

    def _advance_sequence(self) -> None:
        now = time.monotonic()
        if self._sequence and now >= self._sequence_next:
            line, each = self._sequence.popleft()
            self._manual_display_until = now + each
            self._status_line = line
            self._status_until = now + each
            self._status_epoch = self._context_epoch
            self._sequence_next = now + each
            self.state_machine.talk(now)

    def show_daily(self) -> None:
        """Last 7 days as a real grid: every cell is its own label, so Hangul
        widths can never push the numbers out of their columns."""
        win = tk.Toplevel(self.root)
        win.title(f"{self.pet_name} · 결산")
        win.attributes("-topmost", True)
        win.configure(bg=PANEL_BG)
        body = ("Segoe UI", 9)
        bold = ("Segoe UI", 9, "bold")
        tk.Label(win, text=f"친밀도  {self.affection.hearts()}   {self.affection.score:.0f} / 100",
                 font=("Consolas", 10, "bold"), bg=PANEL_BG, fg=FG).pack(anchor="w", padx=14, pady=(12, 6))
        grid = tk.Frame(win, bg=PANEL_BG)
        grid.pack(fill="x", padx=14)
        for col, name in enumerate(self.daily.HEADER):
            tk.Label(grid, text=name, font=body, bg=PANEL_BG, fg=MUTED,
                     anchor="w" if col == 0 else "e").grid(row=0, column=col, sticky="we", padx=(0, 14), pady=(0, 4))
        rows = self.daily.week_rows()
        for r, row in enumerate(rows, start=1):
            for col, cell in enumerate(row["cells"]):
                tk.Label(grid, text=cell, font=bold if row["today"] else body, bg=PANEL_BG,
                         fg=FG if row["today"] else ACCENT, anchor="w" if col == 0 else "e"
                         ).grid(row=r, column=col, sticky="we", padx=(0, 14), pady=1)
        if not rows:
            tk.Label(grid, text="아직 기록이 없다", font=body, bg=PANEL_BG, fg=MUTED).grid(row=1, column=0,
                                                                                    columnspan=6, sticky="w")
        self._daily_grid = grid  # for tests
        tk.Label(win, text="DeskPet이 켜져 있던 시간만 센다.", font=("Segoe UI", 8), bg=PANEL_BG,
                 fg=MUTED).pack(anchor="w", padx=14, pady=(8, 4))
        tk.Button(win, text="닫기", command=win.destroy, bd=0, padx=10, bg=GRIP_BG, fg=FG,
                  activebackground=PANEL_BORDER, activeforeground=FG).pack(pady=(2, 12))
        return win

    def hover_poll_loop(self) -> None:
        if not self.drag_active and hasattr(self, "card"):
            self._track_pointer()
        visible = self._pointer_in_pet_area()
        if visible != self.hover_visible:
            self.hover_visible = visible
            self.stage.itemconfigure(self._buttons_item, state="normal" if visible else "hidden")
        self.root.after(HOVER_POLL_MS, self.hover_poll_loop)

    def _current_detail_height(self) -> int:
        return self._detail_height()

    def _sync_row_layout(self, extra: int) -> None:
        extra = max(0, int(extra))
        if extra == self._extra_rows:
            return
        self._extra_rows = extra
        if self.details_visible:
            x, y = self.root.winfo_x(), self.root.winfo_y()
            self.root.geometry(f"{self.DETAIL_WIDTH}x{self._detail_height()}+{x}+{y}")
            self.root.update_idletasks()
            self._clamp_current_window()

    def toggle_details(self) -> None:
        if self._walk is not None:
            self._stop_walk()  # the window is about to change size; settle first
        self.details_visible = not self.details_visible
        current_x, current_y = self.root.winfo_x(), self.root.winfo_y()
        if self.details_visible:
            self.collapsed_position = (current_x, current_y)
            self._update_details()
            self.root.geometry(f"{self.DETAIL_WIDTH}x{self._detail_height()}+{current_x}+{current_y}")
            self._render_card()
            self.root.update_idletasks()
            self._clamp_current_window()
        else:
            if self.collapsed_position is not None:
                x, y = self.collapsed_position
            else:
                x, y = current_x, current_y
            self.root.geometry(f"{self.PET_WIDTH}x{self.PET_HEIGHT}+{x}+{y}")
            self._render_card()
            self.collapsed_position = None
        self._save_config()

    def toggle_topmost(self) -> None:
        value = not self._topmost_enabled()
        self.root.attributes("-topmost", value)
        self._save_config()
        self._sync_menu_vars()

    def toggle_transparency(self) -> None:
        self.transparent_enabled = not self.transparent_enabled
        self._apply_transparency()
        self._save_config()
        self._sync_menu_vars()

    def show_diagnostics(self) -> None:
        if self._diag_window is not None and not self._diag_window.closed:
            self._diag_window.window.deiconify()
            self._diag_window.window.lift()
            return
        self._diag_window = DiagnosticWindow(self.root, self.diag)
        self.diag.event("UI", "diagnostics_open", "통합 진단창 열림")

    def reset_temperature_calibration(self) -> None:
        if self.monitor is None:
            messagebox.showinfo(
                f"{self.pet_name} temperature",
                "온도 센서가 아직 시작되지 않았어.",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            f"{self.pet_name} temperature",
            "이 PC에서 학습한 THRM 온도 교정을 초기화할까?\n\nCore Temp나 직접 센서와 다시 비교하면서 자동으로 재학습해.",
            parent=self.root,
        ):
            return
        self.monitor.reset_temperature_calibration()
        messagebox.showinfo(
            f"{self.pet_name} temperature",
            "THRM 교정 초기화를 요청했어. 다음 센서 수집부터 다시 자동 교정해.",
            parent=self.root,
        )

    def change_voice_level(self) -> None:
        self.voice.set_level(str(self.voice_level_var.get() or "normal"))
        self._save_config()

    def ask_rule_voice_now(self) -> None:
        now = time.monotonic()
        self._manual_display_until = now + 8.0
        self._status_line = ""
        if self.context is None:
            self._status_line = "센서 살펴보는 중"
            self._status_until = now + 8.0
            self._status_epoch = self._context_epoch
        else:
            self.speech = self.voice.manual(self.context, self.event_memory)
            self.diag.event("VOICE", "rule_button", self.speech.text if self.speech else "",
                            snapshot_id=self.context.snapshot_id)
        self._refresh_dialogue()





    def inspect_status_now(self) -> None:
        self._manual_display_until = time.monotonic() + 8.0
        self._status_line = status_sentence(self.context, self.latest)
        self._status_until = self._manual_display_until
        self._status_epoch = self._context_epoch
        if self.monitor is not None:
            self.monitor.request_sample()
        self.diag.event("VOICE", "status_button", self._status_line)
        self._refresh_dialogue()

    def _dialogue_loop(self) -> None:
        if self._closed:
            return
        self._advance_sequence()
        self._refresh_dialogue()
        self.root.after(250, self._dialogue_loop)

    def _resource_roles(self):
        pid = self.monitor.probe_pid if self.monitor is not None else None
        return {pid: "Windows sensors"} if pid else {}




    def _refresh_dialogue(self):
        text = self._current_display_message()
        if text != self._last_display_text and text:
            self.state_machine.talk(time.monotonic())
        self._render_card()
        if text != self._last_display_text:
            self._last_display_text = text
            self.diag.event("VOICE", "display_changed", text, source="rules",
                            context_epoch=self._context_epoch)

    def reset_position(self) -> None:
        left, top, right, bottom = self._primary_work_area()
        if self.details_visible:
            w, h = self.DETAIL_WIDTH, self._detail_height()
        else:
            w, h = self.PET_WIDTH, self.PET_HEIGHT
        x = max(left, right - w - 24)
        y = max(top, bottom - h - 24)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        if not self.details_visible:
            self.collapsed_position = None
        self._save_config()

    def show_menu(self, event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    # ---------- monitor / rendering ----------
    def sample_loop(self) -> None:
        if self._closed:
            return
        if self.monitor is None:
            self.root.after(SAMPLE_MS, self.sample_loop)
            return
        try:
            s = self.monitor.sample()
            if s is None or (self.latest is not None and s.snapshot_id == self.latest.snapshot_id
                             and s.session_id == self.latest.session_id):
                self.root.after(SAMPLE_MS, self.sample_loop)
                return
            self.latest = s
            self._last_sample_at = s.received_at_mono
            ctx = self.context_builder.update(s, now=s.received_at_mono, observer_activity="OFF")
            extra = date_facts(birthday=self.birthday) | {self.affection.fact()}
            ctx = dc_replace(ctx, facts=ctx.facts | frozenset(extra))
            self.state_machine.affection = self.affection.level
            key = context_key(ctx, s)
            if key != self._context_key:
                self._context_epoch += 1
                self._context_key = key
                self._status_line = ""
                self.diag.event("APP", "context_changed", "규칙 판단 상태 변경",
                                context_epoch=self._context_epoch, snapshot_id=s.snapshot_id,
                                load=ctx.load, thermal=ctx.thermal, face=ctx.face_state, power=ctx.power,
                                battery_band=ctx.battery_band, temperature_source=ctx.temp_source_key,
                                temperature_average_c=ctx.temp_average_c,
                                temperature_trend_c_per_s=ctx.temp_trend_c_per_s,
                                temperature_confidence=ctx.temp_confidence)
            self.context = ctx
            self.state_machine.update_context(ctx)
            new_events = self.event_memory.update(ctx, s)
            for e in new_events:
                reaction = self.EVENT_REACTIONS.get(e.kind)
                if reaction:
                    self.state_machine.react(reaction, time.monotonic())
                self._account_event(e, ctx)
            self._daily_tick(ctx)
            self.speech = self.voice.choose(ctx, self.event_memory, reason="event" if new_events else "sample")
            self.diag.set_summary(state={"load": ctx.load, "thermal": ctx.thermal,
                              "face": ctx.face_state, "power": ctx.power, "battery_band": ctx.battery_band,
                              "temperature_average_c": ctx.temp_average_c,
                              "temperature_trend_c_per_s": ctx.temp_trend_c_per_s,
                              "temperature_confidence": ctx.temp_confidence,
                              "context_epoch": self._context_epoch, "snapshot_id": s.snapshot_id},
                                  voice={"mode": self.voice_mode, "level": self.voice.level,
                              "rule_line": self.speech.text if self.speech else "",
                              "engine": "rules + recent events",
                              "catalog_messages": len(self.voice.catalog.messages),
                              "catalog_errors": list(self.voice.catalog.errors)},
                                  # Not shown on the card; kept for the diagnostics report.
                                  # A bare computer name can identify a person, so it is not exported.
                                  device={"name": (self.device_full_name if self.device_name_source != "computer name"
                                                   else "(컴퓨터 이름 · 진단에서 생략)"),
                                          "source": self.device_name_source},
                                  battery={"percent": s.battery_percent,
                                           "flow_w": s.battery_flow_w if s.battery_flow_valid else None,
                                           "charged_session_wh": float(s.charged_session_wh or 0.0),
                                           "discharged_session_wh": float(s.discharged_session_wh or 0.0)})
            if not self.drag_active:
                self._update_details()
        except Exception as exc:
            self._write_error_log(type(exc), exc, exc.__traceback__)
            self._set_notice(f"상태 읽기 실패: {type(exc).__name__}")
        self.root.after(SAMPLE_MS, self.sample_loop)

    def _set_notice(self, text: str, hold_s: float = 10.0) -> None:
        """App-level notices (sensor start/failure) shown in the bubble."""
        self._notice = text
        self._notice_until = time.monotonic() + hold_s
        if hasattr(self, "card"):
            self._render_card()

    def _current_display_message(self) -> str:
        now = time.monotonic()
        if self._notice and now < self._notice_until and (self.context is None or "실패" in self._notice
                                                           or "오류" in self._notice):
            return self._notice
        if self.context is None:
            return self._status_line if now < self._status_until else "센서 살펴보는 중"
        if self._last_sample_at and now - self._last_sample_at > 10.0:
            return "센서 갱신 지연 · 마지막 측정값"
        d = self.voice.visible(self.context, self.event_memory)
        if d is not None and d.priority >= 90:
            return d.text
        if now < self.annoyed_until:
            return self._override_message_text or "그만."
        if now < self.pet_until:
            return self._override_message_text or "좋음"
        if self._status_line and self._status_epoch == self._context_epoch and now < self._status_until:
            return self._status_line
        return d.text if d is not None else ""

    def animate_loop(self) -> None:
        now = time.monotonic()
        override = None
        if now < self.annoyed_until:
            override = "ANNOYED"
        elif now < self.pet_until:
            override = "PETTING"

        # Keep drawing while carried, so the scruffed pose and its swing show.
        self._cat_text = self._compose_stage(
            self.state_machine.frame(self.anim_tick, override=override, now=now))
        sleeping = override is None and self.state_machine.state == "SLEEP"
        self._sleep_text = ("z", "zZ", "zZz")[int(now / TICK_S) % 3] if sleeping else ""
        self._render_card()
        self.anim_tick += 1
        if not self.drag_active:
            self._walk_tick(now)
        self.root.after(ANIM_MS, self.animate_loop)

    # Levels drive colour only: "normal" grey, "warn" amber, "crit" red.
    def _levels(self) -> dict[str, str]:
        lv = {k: "normal" for k in ("cpu", "ram", "gpu", "temp", "battery")}
        ctx = self.context
        if ctx is None:
            return lv
        if "CPU_BUSY" in ctx.facts: lv["cpu"] = "warn"
        if "GPU_BUSY" in ctx.facts: lv["gpu"] = "warn"
        if "RAM_PRESSURE" in ctx.facts: lv["ram"] = "warn"
        lv["temp"] = {"CRITICAL": "crit", "HOT": "warn"}.get(ctx.thermal, "normal")
        if ctx.power == "DISCHARGING":
            lv["battery"] = {"CRITICAL": "crit", "LOW": "warn"}.get(ctx.battery_band, "normal")
        return lv

    def _alert_level(self) -> str:
        lv = self._levels()
        if "crit" in (lv["temp"], lv["battery"]):
            return "crit"
        if "warn" in (lv["temp"], lv["battery"]):
            return "warn"
        return "normal"

    def _render_card(self) -> None:
        if not hasattr(self, "card"):
            return
        voice_cfg = self.config.get("voice") if isinstance(self.config.get("voice"), dict) else {}
        show_bubble = self.details_visible or bool(voice_cfg.get("show_transient_when_collapsed", True))
        message = self._current_display_message() if show_bubble else ""
        model = CardModel(cat_text=self._cat_text, bubble=message, bubble_level=self._alert_level(),
                          sleep_fx=self._sleep_text, expanded=self.details_visible,
                          summary=self._summary, rows=self._rows)
        self.card.render(model)
        want = self._detail_height() if self.details_visible else self.PET_HEIGHT
        if self.root.winfo_height() != want and self.root.winfo_ismapped():
            x, y = self.root.winfo_x(), self.root.winfo_y()
            self.root.geometry(f"{self.PET_WIDTH}x{want}+{x}+{y}")

    @staticmethod
    def _bar(value: float, width: int = 8) -> str:
        value = max(0.0, min(100.0, value))
        filled = int(round(width * value / 100.0))
        return "█" * filled + "░" * (width - filled)

    @staticmethod
    def _fmt(value: Optional[float], fmt: str = ".1f", suffix: str = "") -> str:
        if value is None:
            return "N/A"
        return f"{value:{fmt}}{suffix}"

    @staticmethod
    def _temperature_prefix(s: Snapshot) -> str:
        confidence = (s.cpu_temp_confidence or "NONE").upper()
        if confidence in {"MEDIUM", "LEGACY"}:
            return "≈"
        if confidence == "LOW":
            return "~"
        return ""

    @classmethod
    def _temperature_ui_text(cls, s: Snapshot) -> str:
        if not getattr(s, "temp_valid", True) or s.cpu_temp_c is None:
            return ""
        return f" · {cls._temperature_prefix(s)}{s.cpu_temp_c:.0f}°C"

    @classmethod
    def _temperature_diag_text(cls, s: Snapshot) -> str:
        if not getattr(s, "temp_valid", True) or s.cpu_temp_c is None:
            return "N/A"
        return f"{cls._temperature_prefix(s)}{s.cpu_temp_c:.1f} °C"

    @classmethod
    def _split_frame_lines(cls, frame_text: str) -> list[str]:
        # Keep every cat line at a fixed width (7 small / 11 large). Stripping and
        # re-centering shifted the paws by half a cell when a tail was added.
        raw = str(frame_text).splitlines()
        large = len(raw) > 3
        width, height = (LARGE_W, LARGE_H) if large else (FRAME_W, 3)
        lines = [(line or '').ljust(width)[:width] for line in raw]
        while len(lines) < height:
            lines.append(' ' * width)
        return lines[:height]

    def _left_accessory_lines(self, s: Optional[Snapshot]) -> list[str]:
        accessory = self.context.left_accessory if self.context is not None else ""
        return ["", "", accessory] if accessory else ["", "", ""]

    def _right_accessory_lines(self, s: Optional[Snapshot]) -> list[str]:
        if self.context is None or self.context.right_accessory != "TERMINAL":
            return ["", "", ""]
        return [".-----.", "| >_  |", "'-----'"]

    def _compose_stage(self, frame_text: str) -> str:
        cat_lines = self._split_frame_lines(frame_text)
        n = len(cat_lines)
        slot = CAT_SLOT_W_LARGE if n > 3 else CAT_SLOT_W
        # Accessories sit on the floor: align them with the cat's bottom lines.
        left_lines = [""] * (n - 3) + self._left_accessory_lines(self.latest)
        right_lines = [""] * (n - 3) + self._right_accessory_lines(self.latest)
        composed = []
        for left, cat, right in zip(left_lines, cat_lines, right_lines):
            composed.append(f"{left:>{LEFT_SLOT_W}}{cat:^{slot}}{right:<{RIGHT_SLOT_W}}")
        return "\n".join(composed)

    THERMAL_WORDS = {"NORMAL": "보통", "WARM": "따뜻", "HOT": "뜨거움", "CRITICAL": "위험",
                     "COOLING": "식는 중", "RELIEF": "회복", "UNKNOWN": ""}
    POWER_WORDS = {"CHARGING": "충전 중", "DISCHARGING": "배터리", "AC_IDLE": "연결됨",
                   "AC_UNKNOWN": "연결됨", "NO_BATTERY": "전원", "UNKNOWN": ""}

    def _percent_row(self, label: str, value, valid: bool, level: str) -> Row:
        if not valid or value is None:
            return Row(label, (("--", "muted"),), 0.0, "muted")
        return Row(label, ((f"{value:.0f}%", level),), value / 100.0, level)

    def _update_details(self) -> None:
        s = self.latest
        if s is None:
            self._rows = (Row("상태", (("센서 연결 중", "muted"),)),)
            self._summary = (("센서 연결 중", "muted"),)
            self._render_card()
            return
        lv = self._levels()
        split = tuple(getattr(s, "gpu_split", ()) or ())
        if len(split) < 2:
            split = ()
        self._sync_row_layout((1 if s.npu_present else 0) + max(0, len(split) - 1))
        rows = [
            self._percent_row("CPU", s.cpu, s.cpu_valid, lv["cpu"]),
            self._percent_row("RAM", s.ram, s.ram_valid, lv["ram"]),
        ]
        if split:
            # Two or more GPUs (e.g. iGPU + dGPU): one row each, like the server cats.
            for label, value in split:
                level = lv["gpu"] if value is not None and value >= self.GPU_ROW_WARN else "normal"
                rows.append(self._percent_row(label, value, bool(s.gpu_valid) and value is not None, level))
        else:
            rows.append(self._percent_row("GPU", s.gpu, s.gpu_valid, lv["gpu"]))
        if s.npu_present:
            rows.append(self._percent_row("NPU", s.npu, bool(s.npu_valid), "normal"))

        ctx = self.context
        temp_ok = bool(s.temp_valid) and s.cpu_temp_c is not None
        temp_text = f"{self._temperature_prefix(s)}{s.cpu_temp_c:.0f}°C" if temp_ok else "--"
        word = self.THERMAL_WORDS.get(ctx.thermal, "") if ctx is not None else ""
        temp_segments = [(temp_text, lv["temp"] if temp_ok else "muted")]
        if temp_ok and word:
            temp_segments.append((f" · {word}", "muted" if lv["temp"] == "normal" else lv["temp"]))
        rows.append(Row("온도", tuple(temp_segments)))

        if s.battery_percent is None:
            bat_segments = [("--", "muted")]
        else:
            power = self.POWER_WORDS.get(ctx.power, "") if ctx is not None else ("연결됨" if s.plugged else "배터리")
            bat_segments = [(f"{s.battery_percent:.0f}%", lv["battery"])]
            if power:
                bat_segments.append((f" · {power}", "muted"))
            if s.battery_flow_valid and s.battery_flow_w is not None and abs(s.battery_flow_w) >= 0.05:
                bat_segments.append((f"  {s.battery_flow_w:+.1f}W", "muted"))
        rows.append(Row("밥", tuple(bat_segments)))
        rows.append(Row("기분", ((self.affection.hearts(), "muted" if self.affection.level == "LOW" else "normal"),)))

        tools = " · ".join(sorted(s.tools_present, key=lambda x: (x == "SSH", x))) if s.tools_present else ""
        if s.tools_scan_status != "OK" and s.tools_present:
            tools += " ?"
        rows.append(Row("작업", ((tools, "normal"),) if tools else (("없음", "muted"),)))
        self._rows = tuple(rows)

        summary = []
        if s.cpu_valid:
            summary.append((f"CPU {s.cpu:.0f}%", lv["cpu"]))
        if temp_ok:
            summary.append((temp_text, lv["temp"]))
        if s.battery_percent is not None:
            summary.append((f"밥 {s.battery_percent:.0f}%", lv["battery"]))
        joined = []
        for i, seg in enumerate(summary):
            if i:
                joined.append(("  ·  ", "muted"))
            joined.append(seg)
        self._summary = tuple(joined) or (("센서 값 없음", "muted"),)
        self._render_card()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._walk is not None:
            self._walk = None  # wherever it is now is fine; saved just below
            self.state_machine.walking = None
            if self.details_visible:
                self.collapsed_position = (self.root.winfo_x(), self.root.winfo_y())
        self._save_config()
        self.affection.save()
        self.daily.save()
        self.resource_monitor.close()
        try:
            if self.monitor is not None:
                self.monitor.close()
        finally:
            if self._diag_window is not None and not self._diag_window.closed:
                self._diag_window.close()
            # Cancel Tk timers explicitly; avoids callbacks into a destroyed GUI.
            for callback in self.root.tk.call("after", "info"):
                try:
                    self.root.after_cancel(callback)
                except tk.TclError:
                    pass
            self.root.destroy()
            self.diag.close()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    DeskPet().run()


if __name__ == "__main__":
    main()
