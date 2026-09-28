from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import math
import re
import time
from typing import Dict, FrozenSet, Optional, Set


class Validity(str, Enum):
    VALID = "VALID"
    WARMUP = "WARMUP"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"


@dataclass(frozen=True)
class Evidence:
    fact: str
    truth: Optional[bool]
    source: str
    acquired_at: float
    snapshot_id: int
    validity: str = Validity.VALID.value


@dataclass(frozen=True)
class PetContext:
    snapshot_id: int
    session_id: str
    now_mono: float
    context_revision: int
    load: str
    memory: str
    thermal: str
    power: str
    battery_band: str
    presence: str
    tools_present: FrozenSet[str]
    tools_scan_status: str
    facts: FrozenSet[str]
    evidence: Dict[str, Evidence]
    face_state: str
    left_accessory: str
    right_accessory: str
    temp_average_c: Optional[float] = None
    temp_trend_c_per_s: float = 0.0
    temp_confidence: str = "NONE"
    temp_source_key: str = ""
    observer_activity: str = "OFF"
    # Values for catalog placeholders. None means unknown: a line that needs
    # an unknown value is never shown (it is not rendered with a guess).
    battery_percent: Optional[float] = None
    cpu_percent: Optional[float] = None
    gpu_percent: Optional[float] = None
    work_since_mono: Optional[float] = None
    last_away_s: Optional[float] = None
    sitting_since_mono: Optional[float] = None
    local_hour: Optional[int] = None

    def has(self, fact: str) -> bool:
        return fact in self.facts


class _HeldFlag:
    """Hysteretic boolean with time holds.

    ``signal`` is TRUE only above the enter threshold, FALSE only below the
    release threshold, and None inside the hysteresis band.  None therefore
    means "keep the last confirmed value", not "sensor missing".  Call
    :meth:`invalidate` when the underlying sensor is unavailable.
    """

    def __init__(self, initial: Optional[bool] = False) -> None:
        self.value: Optional[bool] = initial
        self._candidate: Optional[bool] = initial
        self._since = 0.0

    def reset(self, value: Optional[bool] = None) -> None:
        self.value = value
        self._candidate = value
        self._since = 0.0

    def invalidate(self) -> None:
        self.reset(None)

    def update(self, signal: Optional[bool], now: float, enter_s: float, exit_s: float) -> Optional[bool]:
        if signal is None:
            # In the dead-band: retain the confirmed state and abandon any
            # incomplete transition attempt.
            self._candidate = self.value
            self._since = now
            return self.value
        if signal == self.value:
            self._candidate = signal
            self._since = now
            return self.value
        if signal != self._candidate:
            self._candidate = signal
            self._since = now
            return self.value
        hold = enter_s if signal else exit_s
        if hold <= 0.0 or now - self._since >= hold:
            self.value = signal
            self._candidate = signal
            self._since = now
        return self.value


class _StableValue:
    def __init__(self, initial: str = "UNKNOWN") -> None:
        self.value = initial
        self._candidate = initial
        self._since = 0.0

    def reset(self, value: str = "UNKNOWN") -> None:
        self.value = value
        self._candidate = value
        self._since = 0.0

    def update(self, candidate: str, now: float, hold_s: float) -> str:
        if candidate == self.value:
            self._candidate = candidate
            self._since = now
            return self.value
        if candidate != self._candidate:
            self._candidate = candidate
            self._since = now
            if hold_s <= 0.0:
                self.value = candidate
            return self.value
        if hold_s <= 0.0 or now - self._since >= hold_s:
            self.value = candidate
            self._since = now
        return self.value


class ContextBuilder:
    """Turn raw monitor snapshots into one authoritative DeskPet context.

    UI, face/accessories, event memory and voice rules all consume this object so
    they cannot quietly redefine charging/work/thermal rules in separate places.
    """

    GAP_RESET_SECONDS = 15.0

    def __init__(self) -> None:
        self._work = _HeldFlag(False)
        self._cpu_busy = _HeldFlag(False)
        self._gpu_busy = _HeldFlag(False)
        self._ram_pressure = _HeldFlag(False)
        self._video = _HeldFlag(False)
        self._low_load = _HeldFlag(False)
        self._power = _StableValue("UNKNOWN")
        self._bowl_band = _StableValue("MIN")

        self._temp_hist: deque[tuple[float, float]] = deque(maxlen=40)
        self._temp_source_key = ""
        self._warm = _HeldFlag(False)
        self._hot = _HeldFlag(False)
        self._critical = _HeldFlag(False)
        self._cooling = _HeldFlag(False)
        self._relief_since: Optional[float] = None
        self._last_hot_at: Optional[float] = None
        self._relief_until = 0.0

        self._work_since: Optional[float] = None
        self._away_idle_max = 0.0
        self._last_away_s: Optional[float] = None
        self._prev_presence = "UNKNOWN"
        self._sitting_since: Optional[float] = None

        self._last_now: Optional[float] = None
        self._revision = 0
        self._last_signature: Optional[tuple] = None
        self._battery_band = "UNKNOWN"

    def reset_short_term(self) -> None:
        self._work.reset(None)
        self._cpu_busy.reset(None)
        self._gpu_busy.reset(None)
        self._ram_pressure.reset(None)
        self._video.reset(None)
        self._low_load.reset(None)
        self._power.reset("UNKNOWN")
        self._temp_hist.clear()
        self._warm.invalidate()
        self._hot.invalidate()
        self._critical.invalidate()
        self._cooling.invalidate()
        self._relief_since = None
        self._last_hot_at = None
        self._relief_until = 0.0
        # A sensor gap (sleep/resume) breaks "continuous work" and "sitting".
        self._work_since = None
        self._sitting_since = None

    @staticmethod
    def _finite(value: object) -> bool:
        return isinstance(value, (int, float)) and math.isfinite(float(value))

    def _battery_band_for(self, pct: Optional[float]) -> str:
        if pct is None or not self._finite(pct):
            return "UNKNOWN"
        p = float(pct)
        prev = self._battery_band
        # Downward thresholds: SOON 21-30, LOW 11-20, CRITICAL <=10.
        if p <= 10:
            band = "CRITICAL"
        elif p <= 20:
            band = "LOW"
        elif p <= 30:
            band = "SOON"
        else:
            band = "NORMAL"
        # Upward release hysteresis of 3 percentage points.
        if prev == "CRITICAL" and p < 13:
            band = "CRITICAL"
        elif prev == "LOW" and p < 23 and band in {"SOON", "NORMAL"}:
            band = "LOW"
        elif prev == "SOON" and p < 33 and band == "NORMAL":
            band = "SOON"
        self._battery_band = band
        return band

    def _power_candidate(self, s) -> tuple[str, float]:
        if getattr(s, "battery_present", None) is False:
            return "NO_BATTERY", 0.0
        if getattr(s, "battery_present", None) is None and getattr(s, "battery_percent", None) is None:
            return "NO_BATTERY", 0.0

        flow_valid = bool(getattr(s, "battery_flow_valid", False))
        flow = getattr(s, "battery_flow_w", None)
        plugged = bool(getattr(s, "plugged", False))
        charging_flag = getattr(s, "battery_charging_flag", None)
        discharging_flag = getattr(s, "battery_discharging_flag", None)

        if flow_valid and self._finite(flow):
            f = float(flow)
            if f > 0.3:
                return "CHARGING", 3.0
            if f < -0.3:
                return "DISCHARGING", 3.0
            if plugged:
                return "AC_IDLE", 5.0
            return "DISCHARGING", 3.0
        if charging_flag is True and discharging_flag is True:
            return ("AC_UNKNOWN" if plugged else "UNKNOWN"), 0.0
        if charging_flag is True:
            return "CHARGING", 3.0
        if discharging_flag is True:
            return "DISCHARGING", 3.0
        if plugged:
            return "AC_UNKNOWN", 0.0
        if getattr(s, "battery_percent", None) is not None:
            return "DISCHARGING", 3.0
        return "UNKNOWN", 0.0

    def _reset_temperature_state(self, *, clear_source: bool = False) -> None:
        self._temp_hist.clear()
        self._warm.invalidate()
        self._hot.invalidate()
        self._critical.invalidate()
        self._cooling.invalidate()
        self._relief_since = None
        self._last_hot_at = None
        self._relief_until = 0.0
        if clear_source:
            self._temp_source_key = ""

    def _temperature(self, s, now: float, high_load: bool) -> tuple[str, Optional[float], float, str]:
        valid = bool(getattr(s, "temp_valid", getattr(s, "cpu_temp_c", None) is not None))
        temp = getattr(s, "cpu_temp_c", None)
        conf = str(getattr(s, "cpu_temp_confidence", "NONE") or "NONE").upper()
        source = str(getattr(s, "cpu_temp_source", "") or "")
        sensor = str(getattr(s, "cpu_temp_sensor_name", "") or "")
        # Max-of-core is one aggregate measurement; the hottest core index is
        # not a sensor replacement and must not reset HOT/COOLING hysteresis.
        stable_sensor = re.sub(r"(?i)max core #\d+", "Max core", sensor) if "core temp" in source.lower() else sensor
        source_key = f"{source}|{stable_sensor}|{conf}"

        if not valid or not self._finite(temp) or not (10.0 <= float(temp) <= 115.0):
            self._reset_temperature_state()
            return "UNKNOWN", None, 0.0, source_key

        if self._temp_source_key and source_key != self._temp_source_key:
            # Provider/source switches break trend continuity.  A CoreTemp→THRM
            # jump is not evidence that the laptop suddenly cooled.
            self._reset_temperature_state()
        self._temp_source_key = source_key

        current = float(temp)
        self._temp_hist.append((now, current))
        while self._temp_hist and now - self._temp_hist[0][0] > 30.0:
            self._temp_hist.popleft()

        low_conf = conf not in {"HIGH", "MEDIUM"}
        # Legacy/LOW THRM is already smoothed/calibrated upstream.  A shorter
        # display window keeps the face from lagging several extra seconds after
        # the chassis actually cools, while threshold holds still suppress chatter.
        avg_window_s = 3.0 if low_conf else 5.0
        recent_avg = [v for t, v in self._temp_hist if now - t <= avg_window_s]
        avg = sum(recent_avg) / len(recent_avg) if recent_avg else current
        recent12 = [(t, v) for t, v in self._temp_hist if now - t <= 12.0]
        trend = 0.0
        if len(recent12) >= 2:
            t0, v0 = recent12[0]
            t1, v1 = recent12[-1]
            if t1 - t0 >= 2.0:
                trend = (v1 - v0) / (t1 - t0)

        warm_enter, warm_exit = (80.0, 78.0) if low_conf else (75.0, 72.0)
        hot_enter, hot_exit = (90.0, 86.0) if low_conf else (85.0, 82.0)
        # LOW/LEGACY THRM estimates are deliberately barred from CRITICAL, but
        # the face is cosmetic feedback and should not look oblivious at 80+ C.
        # Keep hysteresis, while making WARM responsive enough to be perceptible.
        warm_enter_s = 3.0 if low_conf else 5.0
        warm_exit_s = 5.0 if low_conf else 10.0
        hot_enter_s = 5.0
        hot_exit_s = 7.0 if low_conf else 10.0

        def threshold_signal(value: float, enter: float, release: float) -> Optional[bool]:
            if value >= enter:
                return True
            if value < release:
                return False
            return None

        warm = self._warm.update(threshold_signal(avg, warm_enter, warm_exit), now, warm_enter_s, warm_exit_s)
        hot = self._hot.update(threshold_signal(avg, hot_enter, hot_exit), now, hot_enter_s, hot_exit_s)

        if low_conf:
            # Raw/uncalibrated thermal zones may express broad heat but cannot
            # authorize the extreme CRITICAL face.
            self._critical.reset(False)
            critical = False
        else:
            critical = self._critical.update(threshold_signal(avg, 95.0, 92.0), now, 5.0, 10.0) is True

        if hot is True or critical:
            self._last_hot_at = now

        recent_hot = self._last_hot_at is not None and now - self._last_hot_at <= 120.0
        cooling_condition = recent_hot and avg >= 80.0 and trend <= -0.1 and not critical
        cooling_release = (not recent_hot) or avg < 80.0 or trend > -0.05 or critical
        cooling_signal: Optional[bool] = True if cooling_condition else (False if cooling_release else None)
        cooling = self._cooling.update(cooling_signal, now, 5.0, 5.0)

        relief_cond = recent_hot and current < 80.0 and avg < 80.0 and not high_load
        if relief_cond:
            if self._relief_since is None:
                self._relief_since = now
            elif now - self._relief_since >= 10.0:
                self._last_hot_at = None
                self._relief_until = now + 8.0
                self._relief_since = None
                self._cooling.reset(False)
                self._hot.reset(False)
        else:
            self._relief_since = None

        if critical:
            return "CRITICAL", avg, trend, source_key
        if cooling is True:
            return "COOLING", avg, trend, source_key
        if hot is True:
            return "HOT", avg, trend, source_key
        if now < self._relief_until and current < 82.0 and avg < 82.0:
            return "RELIEF", avg, trend, source_key
        if warm is True:
            return "WARM", avg, trend, source_key
        if warm is None:
            return "UNKNOWN", avg, trend, source_key
        return "NORMAL", avg, trend, source_key

    DAYPARTS = ((0, 6, "DAWN"), (6, 11, "MORNING"), (11, 14, "NOON"),
                (14, 18, "AFTERNOON"), (18, 21, "EVENING"), (21, 24, "NIGHT"))
    WORK_LONG_S = 3600.0

    @classmethod
    def daypart_for(cls, hour: int) -> str:
        for lo, hi, name in cls.DAYPARTS:
            if lo <= hour < hi:
                return name
        return "NIGHT"

    def update(self, s, now: Optional[float] = None, observer_activity: str = "OFF",
               local_hour: Optional[int] = None) -> PetContext:
        now = time.monotonic() if now is None else float(now)
        if local_hour is None:
            local_hour = time.localtime().tm_hour
        if self._last_now is not None and now - self._last_now > self.GAP_RESET_SECONDS:
            self.reset_short_term()
        self._last_now = now

        sid = int(getattr(s, "snapshot_id", 0))
        session_id = str(getattr(s, "session_id", "") or "")

        cpu_valid = bool(getattr(s, "cpu_valid", True)) and self._finite(getattr(s, "cpu", None))
        gpu_present = bool(getattr(s, "gpu_present", bool(getattr(s, "gpu_name", ""))))
        gpu_valid = bool(getattr(s, "gpu_valid", getattr(s, "probe_ok", False))) and self._finite(getattr(s, "gpu", None))
        ram_valid = bool(getattr(s, "ram_valid", True)) and self._finite(getattr(s, "ram", None))
        video_valid = gpu_valid and self._finite(getattr(s, "gpu_video_decode", None))

        cpu = float(getattr(s, "cpu", 0.0)) if cpu_valid else None
        gpu = float(getattr(s, "gpu", 0.0)) if gpu_valid else None
        ram = float(getattr(s, "ram", 0.0)) if ram_valid else None
        video = float(getattr(s, "gpu_video_decode", 0.0)) if video_valid else None

        # Each flag gets TRUE only beyond its enter threshold and FALSE only
        # beyond its release threshold.  The band between them is deliberately
        # neutral so transient boundary noise cannot chatter the state.
        work_enter = bool((cpu is not None and cpu >= 40.0) or (gpu is not None and gpu >= 35.0))
        work_exit_low = (
            cpu is not None and cpu < 25.0
            and ((not gpu_present) or (gpu is not None and gpu < 20.0))
        )
        work_missing_required = cpu is None or (gpu_present and gpu is None)
        if work_enter:
            # One known high sensor is enough to establish active work.
            work = self._work.update(True, now, 3.0, 10.0)
        elif work_exit_low:
            work = self._work.update(False, now, 3.0, 10.0)
        elif work_missing_required:
            # A stale/missing sensor must not preserve an old busy state forever.
            self._work.invalidate()
            work = None
        else:
            work = self._work.update(None, now, 3.0, 10.0)

        def update_threshold(flag: _HeldFlag, value: Optional[float], enter: float, release: float, enter_s: float, exit_s: float) -> Optional[bool]:
            if value is None:
                flag.invalidate()
                return None
            signal: Optional[bool]
            if value >= enter:
                signal = True
            elif value < release:
                signal = False
            else:
                signal = None
            return flag.update(signal, now, enter_s, exit_s)

        cpu_busy = update_threshold(self._cpu_busy, cpu, 80.0, 70.0, 5.0, 10.0)
        if not gpu_present:
            self._gpu_busy.reset(False)
            self._video.reset(False)
            gpu_busy = False
            video_active = False
        else:
            gpu_busy = update_threshold(self._gpu_busy, gpu, 85.0, 75.0, 5.0, 10.0)
            # Measured on the Lunar Lake laptop (2026-09-27): paused 0.0 %, a normal YouTube
            # video 3.4-4.9 %, HDR ~30 %. The old 20 % entry missed ordinary videos entirely.
            video_active = update_threshold(self._video, video, 2.0, 0.5, 5.0, 10.0)
        ram_pressure = update_threshold(self._ram_pressure, ram, 90.0, 85.0, 5.0, 10.0)

        low_known = cpu is not None and ((not gpu_present) or gpu is not None)
        if not low_known:
            self._low_load.invalidate()
            low_load = None
        else:
            low_enter = cpu < 25.0 and ((not gpu_present) or (gpu is not None and gpu < 20.0))
            low_exit = bool((cpu >= 40.0) or (gpu is not None and gpu >= 35.0))
            low_signal: Optional[bool] = True if low_enter else (False if low_exit else None)
            low_load = self._low_load.update(low_signal, now, 10.0, 0.0)

        if cpu_busy and gpu_busy:
            load = "BOTH_BUSY"
        elif cpu_busy:
            load = "CPU_BUSY"
        elif gpu_busy:
            load = "GPU_BUSY"
        elif work:
            load = "WORKING"
        elif low_load:
            load = "IDLE"
        else:
            load = "UNKNOWN"

        memory_state = "PRESSURE" if ram_pressure else ("NORMAL" if ram_pressure is False else "UNKNOWN")

        candidate_power, power_hold = self._power_candidate(s)
        power = self._power.update(candidate_power, now, power_hold)
        band = self._battery_band_for(getattr(s, "battery_percent", None))
        idle_s = float(getattr(s, "idle_seconds", 0.0) or 0.0)
        # A YouTube page on screen with the video decoder running: someone is watching.
        youtube_playing = video_active is True and getattr(s, "youtube_visible", None) is True
        # Watching needs no keyboard or mouse, so it does not count as being away.
        presence = "AWAY" if idle_s >= 300.0 and not youtube_playing else "PRESENT"
        if presence == "AWAY":
            self._away_idle_max = max(self._away_idle_max, idle_s)
        elif self._prev_presence == "AWAY":
            # Same transition that emits USER_RETURNED in EventMemory.
            self._last_away_s = self._away_idle_max
            self._away_idle_max = 0.0
        self._prev_presence = presence
        if presence == "PRESENT":
            if self._sitting_since is None:
                self._sitting_since = now
        else:
            self._sitting_since = None

        if work is True:
            if self._work_since is None:
                self._work_since = now
        else:
            self._work_since = None
        work_long = work is True and self._work_since is not None and now - self._work_since >= self.WORK_LONG_S

        high_load = bool(cpu_busy or gpu_busy or ram_pressure)
        thermal, temp_avg, temp_trend, temp_source_key = self._temperature(s, now, high_load)

        tools = frozenset(str(x) for x in (getattr(s, "tools_present", frozenset()) or frozenset()))
        scan_status = str(getattr(s, "tools_scan_status", "UNKNOWN") or "UNKNOWN")

        facts: Set[str] = set()
        if work is True: facts.add("WORK_ACTIVE")
        if work is False: facts.add("WORK_INACTIVE")
        if cpu_busy is True: facts.add("CPU_BUSY")
        if gpu_busy is True: facts.add("GPU_BUSY")
        if ram_pressure is True: facts.add("RAM_PRESSURE")
        if video_active is True: facts.add("VIDEO_ENGINE_ACTIVE")
        if youtube_playing: facts.add("YOUTUBE_PLAYING")
        if low_load is True: facts.add("LOW_LOAD")
        facts.add(f"POWER_{power}")
        facts.add(f"BATTERY_{band}")
        facts.add(f"THERMAL_{thermal}")
        facts.add(f"PRESENCE_{presence}")
        if work_long:
            facts.add("WORK_LONG")
        facts.add(f"TIME_{self.daypart_for(int(local_hour))}")
        for tool in tools:
            facts.add(f"TOOL_{tool.upper()}_PRESENT")

        left = ""
        pct = getattr(s, "battery_percent", None)
        if self._finite(pct) and 0 <= float(pct) <= 100:
            pct = float(pct)
            bowl = (r"\___/" if pct <= 20 else r"\_._/" if pct <= 40
                    else r"\.../" if pct <= 60 else r"\ooo/" if pct <= 80 else r"\OOO/")
            if power == "CHARGING":
                left = bowl
            elif power == "DISCHARGING" and pct <= 20:
                left = r"\___/"
            elif power == "AC_IDLE" and getattr(s, "plugged", None) is True:
                # Battery-care/charge-limit modes (e.g. ASUS 80%) report AC_IDLE
                # with 0 W even though external power is connected. Keep the bowl
                # visible and represent the *actual* battery percentage, not watts.
                left = bowl

        right = "TERMINAL" if (work is True or video_active is True) else ""

        # Visual face is still a single dominant expression, but it is derived
        # from the multi-dimensional context rather than erasing the other facts.
        if thermal == "CRITICAL":
            face = "THERMAL_PANIC"
        elif band in {"LOW", "CRITICAL"} and power == "DISCHARGING":
            face = "LOW_BATTERY"
        elif thermal == "HOT":
            face = "HOT"
        elif thermal == "COOLING":
            face = "COOLING"
        elif thermal == "RELIEF":
            face = "RELIEF"
        elif thermal == "WARM":
            face = "WARM"
        elif cpu_busy and gpu_busy:
            face = "HEAVY_LOAD"
        elif cpu_busy:
            face = "HEAVY_CPU"
        elif gpu_busy:
            face = "GPU_BUSY"
        elif ram_pressure:
            face = "HEAVY_LOAD"
        elif presence == "AWAY" and low_load:
            face = "SLEEP"
        elif youtube_playing:
            face = "VIDEO"
        elif work:
            face = "WORKING"
        elif power == "CHARGING":
            face = "CHARGING"
        elif load == "UNKNOWN" and memory_state == "UNKNOWN":
            face = "UNKNOWN"
        else:
            face = "CHILL"

        evidence: Dict[str, Evidence] = {}
        acquired = float(getattr(s, "received_at_mono", now) or now)
        for fact in facts:
            evidence[fact] = Evidence(fact, True, "PetContext", acquired, sid)

        signature = (load, memory_state, thermal, power, band, presence, tuple(sorted(tools)), face, left, right)
        if signature != self._last_signature:
            self._revision += 1
            self._last_signature = signature

        return PetContext(
            snapshot_id=sid,
            session_id=session_id,
            now_mono=now,
            context_revision=self._revision,
            load=load,
            memory=memory_state,
            thermal=thermal,
            power=power,
            battery_band=band,
            presence=presence,
            tools_present=tools,
            tools_scan_status=scan_status,
            facts=frozenset(facts),
            evidence=evidence,
            face_state=face,
            left_accessory=left,
            right_accessory=right,
            temp_average_c=temp_avg,
            temp_trend_c_per_s=temp_trend,
            temp_confidence=str(getattr(s, "cpu_temp_confidence", "NONE") or "NONE"),
            temp_source_key=temp_source_key,
            observer_activity=observer_activity,
            battery_percent=(float(pct) if self._finite(pct) and 0 <= float(pct) <= 100 else None),
            cpu_percent=cpu,
            gpu_percent=gpu,
            work_since_mono=self._work_since,
            last_away_s=self._last_away_s,
            sitting_since_mono=self._sitting_since,
            local_hour=int(local_hour),
        )
