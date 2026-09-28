from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import json
import math
import time
import inspect

from event_memory import EventMemory
from monitor import CoreTempSharedDataEx, CoreTempSharedMemoryReader
from diagnostics import get_diagnostics
from pet_context import ContextBuilder, PetContext
from pet_states import FRAMES, PetStateMachine
from temperature_manager import TemperatureManager
from voice_engine import CatalogMessage, VoiceEngine

BASE_DIR = Path(__file__).resolve().parent


def snap(t: float = 0.0, **kw):
    defaults = dict(
        snapshot_id=int(t * 10 + 1), session_id="test", received_at_mono=t, wall_time=t,
        cpu=5.0, cpu_valid=True, ram=40.0, ram_valid=True,
        gpu=0.0, gpu_present=True, gpu_valid=True, gpu_name="Test GPU",
        gpu_3d=0.0, gpu_compute=0.0, gpu_video_decode=0.0, gpu_video_encode=0.0, gpu_copy=0.0,
        npu_present=False, npu=None, npu_valid=False,
        battery_percent=60.0, plugged=False, battery_present=True,
        battery_flow_w=-5.0, battery_flow_valid=True,
        battery_charging_flag=False, battery_discharging_flag=True,
        idle_seconds=0.0,
        cpu_temp_c=60.0, cpu_temp_confidence="HIGH", cpu_temp_source="Core Temp shared memory",
        cpu_temp_sensor_name="Max core #1 of 8", temp_valid=True, temp_acquired_at=t,
        tools_present=frozenset(), tools_scan_status="OK", tools_scan_seq=int(t // 5) + 1,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def advance(builder: ContextBuilder, start: float, seconds: int, **kw) -> PetContext:
    ctx = None
    for i in range(seconds + 1):
        t = start + i
        ctx = builder.update(snap(t, **kw), t)
    assert ctx is not None
    return ctx


def test_coretemp_decode():
    ct = CoreTempSharedDataEx()
    ct.uiCoreCnt = 4
    ct.uiCPUCnt = 1
    for i, value in enumerate((60.0, 71.0, 82.0, 75.0)):
        ct.fTemp[i] = value
    value, temps, max_index = CoreTempSharedMemoryReader._extract_temperature(ct)
    assert round(value or 0.0, 1) == 82.0 and len(temps) == 4 and max_index == 2

    ct.ucFahrenheit = 1
    for i, value in enumerate((140.0, 159.8, 179.6, 167.0)):
        ct.fTemp[i] = value
    value, _, max_index = CoreTempSharedMemoryReader._extract_temperature(ct)
    assert round(value or 0.0, 1) == 82.0 and max_index == 2

    ct.ucFahrenheit = 0
    ct.ucDeltaToTjMax = 1
    ct.uiTjMax[0] = 100
    for i, value in enumerate((40.0, 29.0, 18.0, 25.0)):
        ct.fTemp[i] = value
    value, _, max_index = CoreTempSharedMemoryReader._extract_temperature(ct)
    assert round(value or 0.0, 1) == 82.0 and max_index == 2


def test_context_load_and_tools():
    # T01: SSH presence alone is not work and does not summon the terminal.
    b = ContextBuilder()
    ctx = advance(b, 0, 11, cpu=8.9, gpu=0.0, tools_present=frozenset({"SSH"}))
    assert ctx.load == "IDLE"
    assert ctx.right_accessory == ""
    assert "TOOL_SSH_PRESENT" in ctx.facts
    assert "WORK_ACTIVE" not in ctx.facts

    # T03: Claude presence alone also stays separate from actual load.
    b = ContextBuilder()
    ctx = advance(b, 0, 11, cpu=3.0, gpu=0.0, tools_present=frozenset({"Claude"}))
    assert ctx.load == "IDLE" and ctx.right_accessory == ""

    # T04: one-second CPU spike cannot confirm CPU_BUSY.
    b = ContextBuilder()
    b.update(snap(0, cpu=85.0), 0)
    ctx = b.update(snap(1, cpu=5.0), 1)
    assert "CPU_BUSY" not in ctx.facts

    # T05: sustained high CPU confirms CPU_BUSY and terminal.
    b = ContextBuilder()
    ctx = advance(b, 0, 6, cpu=90.0, gpu=0.0)
    assert ctx.load == "CPU_BUSY" and ctx.right_accessory == "TERMINAL"

    # T06: GPU_BUSY uses entry and release hysteresis.
    b = ContextBuilder()
    ctx = advance(b, 0, 6, cpu=10.0, gpu=90.0)
    assert ctx.load == "GPU_BUSY"
    for i in range(1, 12):
        ctx = b.update(snap(6 + i, cpu=10.0, gpu=0.0), 6 + i)
    assert "GPU_BUSY" not in ctx.facts

    # T07: RAM pressure does not fabricate CPU/GPU busy.
    b = ContextBuilder()
    ctx = advance(b, 0, 6, cpu=8.0, gpu=0.0, ram=94.0)
    assert ctx.memory == "PRESSURE"
    assert "CPU_BUSY" not in ctx.facts and "GPU_BUSY" not in ctx.facts

    # T19: a stale GPU reading cannot keep yesterday's GPU busy/terminal alive.
    b = ContextBuilder()
    ctx = advance(b, 0, 6, cpu=10.0, gpu=95.0, gpu_valid=True, gpu_present=True)
    assert ctx.right_accessory == "TERMINAL"
    stale = b.update(snap(7, cpu=10.0, gpu=95.0, gpu_valid=False, gpu_present=True), 7)
    assert "GPU_BUSY" not in stale.facts and stale.right_accessory == ""


def test_context_power_and_bowl():
    # T08 AC with known zero flow => AC_IDLE. Charge-limit modes can stop
    # charging at e.g. 80%, so keep the bowl visible using actual battery %.
    b = ContextBuilder()
    ctx = advance(b, 0, 6, plugged=True, battery_percent=80.0, battery_flow_w=0.0, battery_flow_valid=True,
                  battery_charging_flag=False, battery_discharging_flag=False)
    assert ctx.power == "AC_IDLE" and ctx.left_accessory == r"\ooo/"

    # T09 AC with no usable flow/flag => AC_UNKNOWN, not charging.
    b = ContextBuilder()
    ctx = b.update(snap(0, plugged=True, battery_flow_w=None, battery_flow_valid=False,
                        battery_charging_flag=None, battery_discharging_flag=None), 0)
    assert ctx.power == "AC_UNKNOWN" and ctx.left_accessory == ""

    # T10 AC can still be discharging.
    b = ContextBuilder()
    ctx = advance(b, 0, 4, plugged=True, battery_flow_w=-4.0, battery_flow_valid=True,
                  battery_charging_flag=False, battery_discharging_flag=True)
    assert ctx.power == "DISCHARGING" and ctx.left_accessory == ""

    # T11 low battery + real work gives both accessories.
    b = ContextBuilder()
    ctx = advance(b, 0, 6, cpu=90.0, gpu=0.0, battery_percent=15.0, plugged=False,
                  battery_flow_w=-8.0, battery_flow_valid=True)
    assert ctx.battery_band == "LOW" and ctx.left_accessory == r"\___/"
    assert ctx.right_accessory == "TERMINAL"

    # T12 22% is SOON, not LOW/CRITICAL face.
    b = ContextBuilder()
    ctx = advance(b, 0, 11, cpu=5.0, gpu=0.0, battery_percent=22.0, plugged=False,
                  battery_flow_w=-4.0, battery_flow_valid=True)
    assert ctx.battery_band == "SOON"
    assert ctx.face_state != "LOW_BATTERY"

    # T13 bowl fill bands after stable charging power.
    for pct, expected in ((15.0, r"\___/"), (35.0, r"\_._/"), (55.0, r"\.../"), (75.0, r"\ooo/"), (95.0, r"\OOO/")):
        b = ContextBuilder()
        ctx = advance(b, 0, 10, plugged=True, battery_percent=pct, battery_flow_w=12.0, battery_flow_valid=True,
                      battery_charging_flag=True, battery_discharging_flag=False)
        assert ctx.power == "CHARGING" and ctx.left_accessory == expected, (pct, ctx.left_accessory)


def test_context_temperature():
    # T14 oscillating hot range must never manufacture RELIEF.
    b = ContextBuilder()
    states = []
    for i in range(35):
        temp = 84.0 if i % 2 == 0 else 90.0
        ctx = b.update(snap(i, cpu=30.0, cpu_temp_c=temp), i)
        states.append(ctx.thermal)
    assert "RELIEF" not in states

    # T15/T16: real HOT -> sustained downward trend -> COOLING -> <80 sustained RELIEF.
    b = ContextBuilder()
    for i in range(9):
        ctx = b.update(snap(i, cpu=55.0, cpu_temp_c=90.0), i)
    assert ctx.thermal == "HOT"
    cooling_seen = False
    seq = (90, 89, 88, 87, 86, 85, 84, 83, 82)
    for j, temp in enumerate(seq, start=9):
        ctx = b.update(snap(j, cpu=15.0, cpu_temp_c=float(temp)), j)
        cooling_seen |= ctx.thermal == "COOLING"
    assert cooling_seen
    relief_seen = False
    for j in range(18, 34):
        ctx = b.update(snap(j, cpu=10.0, cpu_temp_c=77.0), j)
        relief_seen |= ctx.thermal == "RELIEF"
    assert relief_seen

    # T17 source switch cannot itself be interpreted as cooling/relief.
    b = ContextBuilder()
    for i in range(8):
        b.update(snap(i, cpu_temp_c=90.0, cpu_temp_source="Core Temp shared memory",
                      cpu_temp_sensor_name="Max core #1 of 8", cpu_temp_confidence="HIGH"), i)
    ctx = b.update(snap(8, cpu_temp_c=50.0, cpu_temp_source="Windows thermal performance counter",
                        cpu_temp_sensor_name=r"\_TZ.THRM", cpu_temp_confidence="LOW"), 8)
    assert ctx.thermal not in {"COOLING", "RELIEF"}

    # T18 raw THRM can be HOT but never CRITICAL.
    b = ContextBuilder()
    for i in range(13):
        ctx = b.update(snap(i, cpu_temp_c=104.0, cpu_temp_source="Windows thermal performance counter",
                            cpu_temp_sensor_name=r"\_TZ.THRM", cpu_temp_confidence="LOW"), i)
    assert ctx.thermal == "HOT"
    assert ctx.face_state != "THERMAL_PANIC"


def test_events_and_voice():
    # T20 first successful tool scan is baseline; failed scan cannot close tools.
    mem = EventMemory(); b = ContextBuilder()
    c0 = b.update(snap(0, tools_present=frozenset({"SSH"}), tools_scan_status="OK", tools_scan_seq=1), 0)
    mem.update(c0, snap(0, tools_present=frozenset({"SSH"}), tools_scan_status="OK", tools_scan_seq=1))
    c1 = b.update(snap(5, tools_present=frozenset({"SSH"}), tools_scan_status="ERROR", tools_scan_seq=2), 5)
    mem.update(c1, snap(5, tools_present=frozenset({"SSH"}), tools_scan_status="ERROR", tools_scan_seq=2))
    assert not any(e.kind == "TOOL_DISAPPEARED" for e in mem.events)

    # T21/T22 two successful changed scans confirm the exact tool event.
    c2 = b.update(snap(10, tools_present=frozenset(), tools_scan_status="OK", tools_scan_seq=3), 10)
    mem.update(c2, snap(10, tools_present=frozenset(), tools_scan_status="OK", tools_scan_seq=3))
    c3 = b.update(snap(15, tools_present=frozenset(), tools_scan_status="OK", tools_scan_seq=4), 15)
    mem.update(c3, snap(15, tools_present=frozenset(), tools_scan_status="OK", tools_scan_seq=4))
    assert any(e.kind == "TOOL_DISAPPEARED" and e.subject == "SSH" for e in mem.events)

    c4 = b.update(snap(20, tools_present=frozenset({"Codex"}), tools_scan_status="OK", tools_scan_seq=5), 20)
    mem.update(c4, snap(20, tools_present=frozenset({"Codex"}), tools_scan_status="OK", tools_scan_seq=5))
    c5 = b.update(snap(25, tools_present=frozenset({"Codex"}), tools_scan_status="OK", tools_scan_seq=6), 25)
    mem.update(c5, snap(25, tools_present=frozenset({"Codex"}), tools_scan_status="OK", tools_scan_seq=6))
    assert any(e.kind == "TOOL_APPEARED" and e.subject == "Codex" for e in mem.events)
    assert not any(e.kind == "TOOL_APPEARED" and e.subject == "Claude" for e in mem.events)

    # T02: "없음" display strings never enter rule logic; charging without work
    # cannot select CHARGING_WORKING.
    b = ContextBuilder(); mem = EventMemory(); voice = VoiceEngine(BASE_DIR, seed=7)
    ctx = advance(b, 0, 11, cpu=3.0, gpu=0.0, plugged=True, battery_flow_w=10.0,
                  battery_flow_valid=True, battery_charging_flag=True, battery_discharging_flag=False,
                  tools_present=frozenset())
    mem.update(ctx, snap(11, cpu=3.0, gpu=0.0, plugged=True, battery_flow_w=10.0,
                         battery_flow_valid=True, battery_charging_flag=True, battery_discharging_flag=False,
                         tools_present=frozenset()))
    d = voice.manual(ctx, mem)
    assert d is not None and d.message_id.startswith("charging_") and not d.message_id.startswith("charging_work_")

    # T11 exact low-battery+work combination outranks generic work.
    b = ContextBuilder(); mem = EventMemory(); voice = VoiceEngine(BASE_DIR, seed=4)
    ctx = advance(b, 0, 7, cpu=90.0, gpu=0.0, battery_percent=15.0, plugged=False,
                  battery_flow_w=-7.0, battery_flow_valid=True)
    mem.update(ctx, snap(7, cpu=90.0, gpu=0.0, battery_percent=15.0, plugged=False,
                         battery_flow_w=-7.0, battery_flow_valid=True))
    d = voice.manual(ctx, mem)
    assert d is not None and d.message_id.startswith("low_battery_work_")

    # T23 six manual lines from a >=6-entry truthful pool avoid the previous five.
    b = ContextBuilder(); mem = EventMemory(); voice = VoiceEngine(BASE_DIR, seed=13)
    ctx = advance(b, 0, 11, cpu=5.0, gpu=0.0, battery_percent=80.0,
                  battery_flow_w=-3.0, battery_flow_valid=True, cpu_temp_c=60.0)
    seen = []
    for i in range(6):
        ctx = replace(ctx, now_mono=20.0 + i, snapshot_id=200+i)
        d = voice.manual(ctx, mem)
        assert d is not None
        assert d.text not in seen[-5:]
        seen.append(d.text)

    # T24 one eligible line may repeat; it must not borrow another state.
    only = CatalogMessage("only", "CHILL", "조용하다", "only", ("LOW_LOAD", "THERMAL_NORMAL"), (), (), (), priority=10)
    voice = VoiceEngine(BASE_DIR, seed=1)
    voice.catalog.messages = [only]
    d1 = voice.manual(ctx, mem); d2 = voice.manual(replace(ctx, now_mono=40), mem)
    assert d1 and d2 and d1.text == d2.text == "조용하다"

    # T25 a charging line loses display eligibility as soon as charging is false.
    b = ContextBuilder(); mem = EventMemory(); voice = VoiceEngine(BASE_DIR, seed=1)
    charging = advance(b, 0, 11, cpu=5.0, gpu=0.0, plugged=True, battery_flow_w=12.0,
                       battery_flow_valid=True, battery_charging_flag=True, battery_discharging_flag=False)
    d = voice.manual(charging, mem)
    assert d is not None and "POWER_CHARGING" in d.required_facts
    for i in range(12, 18):
        off = b.update(snap(i, cpu=5.0, gpu=0.0, plugged=False, battery_flow_w=-4.0,
                            battery_flow_valid=True, battery_charging_flag=False, battery_discharging_flag=True), i)
    assert voice.visible(off, mem) is None


def test_gap_and_accessory_layout():
    # T35 a long sample gap resets short-term trend/load rather than fabricating exit/cooling.
    b = ContextBuilder(); mem = EventMemory()
    hot = advance(b, 0, 8, cpu=90.0, cpu_temp_c=90.0)
    mem.update(hot, snap(8, cpu=90.0, cpu_temp_c=90.0))
    after = b.update(snap(30, cpu=5.0, cpu_temp_c=60.0), 30)
    evs = mem.update(after, snap(30, cpu=5.0, cpu_temp_c=60.0))
    assert after.thermal not in {"COOLING", "RELIEF"}
    assert any(e.kind == "SAMPLE_GAP_RECOVERED" for e in evs)

    # T31 stage slots are fixed: cat column and total line width never move.
    from deskpet import DeskPet
    dummy = DeskPet.__new__(DeskPet)
    dummy.latest = None
    dummy.context = None
    plain = DeskPet._compose_stage(dummy, FRAMES["CHILL"][0]).splitlines()
    ctx = replace(after, left_accessory=r"\ooo/", right_accessory="TERMINAL")
    dummy.context = ctx
    both = DeskPet._compose_stage(dummy, FRAMES["CHILL"][0]).splitlines()
    assert [len(x) for x in plain] == [len(x) for x in both]
    assert [x.index("/\\_/\\") if "/\\_/\\" in x else -1 for x in plain] == [x.index("/\\_/\\") if "/\\_/\\" in x else -1 for x in both]
    assert "\\ooo/" in both[2] and "'-----'" in both[2]


def test_temperature_calibration_v23():
    with TemporaryDirectory() as td:
        manager = TemperatureManager(Path(td) / "temperature_calibration.json")
        base = 1000.0; scale = 0.95; offset = 5.0; lag = 4
        latest_raw = None; latest_stamp = None
        def core(sec: float) -> float:
            return 78.0 + 12.0 * math.sin(sec / 13.0) + 5.0 * math.sin(sec / 37.0)
        for sec in range(420):
            now = base + sec
            direct = core(sec)
            if sec % 2 == 0:
                latest_raw = (core(max(0, sec-lag)) - offset) / scale
                latest_stamp = now
            manager.select(
                now=now, direct_c=direct, direct_source="Core Temp shared memory",
                direct_sensor_name="Max core #3 of 8", direct_diagnostics="synthetic",
                raw_thermal_c=latest_raw, raw_thermal_source="Windows thermal performance counter",
                raw_thermal_sensor_name=r"\_TZ.THRM", raw_thermal_updated_at=latest_stamp,
            )
        cal = manager.calibration
        assert cal is not None and cal.strict_valid
        assert cal.validation_samples >= 40 and cal.validation_mae_c <= 4.5 and cal.validation_p95_c <= 8.0

        # T37: outside trained THRM support drops to raw LOW rather than extrapolating MEDIUM.
        reading = manager.select(
            now=base+421, direct_c=None, direct_source="", direct_sensor_name="", direct_diagnostics="",
            raw_thermal_c=cal.raw_max_c + 10.0, raw_thermal_source=cal.sensor_source,
            raw_thermal_sensor_name=cal.sensor_name, raw_thermal_updated_at=base+421,
        )
        assert reading.confidence == "LOW" and manager.calibration_state == "out_of_range"

    # T36: excellent training relationship but deliberately bad chronological
    # holdout must not be approved as a new strict calibration.
    with TemporaryDirectory() as td:
        manager = TemperatureManager(Path(td) / "temperature_calibration.json")
        base = 5000.0
        refs = []
        thermals = []
        for i in range(400):
            t = base + i * 2.0
            ref = 70.0 + 12.0 * math.sin(i / 18.0)
            if i < 300:
                raw = (ref - 4.0) / 0.95
            else:
                # Future holdout breaks the relation in a structured way.
                raw = 92.0 - 0.35 * (ref - 70.0)
            refs.append((t, ref)); thermals.append((t, raw))
        manager.reference_history.extend(refs)
        manager.thermal_history.extend(thermals)
        manager._reference_kind = "CoreTemp Max Core"
        manager._recalculate("Windows thermal performance counter", r"\_TZ.THRM", manager._reference_kind)
        assert manager.calibration is None or not manager.calibration.strict_valid


def test_catalog_failure_and_model_free_config():
    from deskpet import DeskPet
    with TemporaryDirectory() as td:
        root = Path(td)
        legacy = {"schema_version": 2, "x": 123, "y": 456,
                  "observer": {"enabled": True, "model_path": "legacy-model-folder"},
                  "pet_name": "고양이", "transparent": False,
                  "voice": {"level": "quiet"}}
        (root / "config.json").write_text(json.dumps(legacy), encoding="utf-8")
        dummy = DeskPet.__new__(DeskPet)
        dummy.base_dir = root
        dummy.config_path = root / "config.json"
        dummy.diag = get_diagnostics()
        cfg = DeskPet._load_config(dummy)
        assert cfg["schema_version"] == 6
        assert cfg["voice"]["mode"] == "rule"
        assert cfg["voice"]["level"] == "quiet"
        assert cfg["x"] == 123 and cfg["y"] == 456 and cfg["pet_name"] == "고양이"
        assert "observer" not in cfg and "gemma" not in cfg
        assert (root / "config.pre-classic.backup.json").exists()

    # T38: broken user catalog cannot execute code or destroy the valid base catalog.
    with TemporaryDirectory() as td:
        root = Path(td)
        (root / "voice_catalog.json").write_text((BASE_DIR / "voice_catalog.json").read_text(encoding="utf-8"), encoding="utf-8")
        (root / "voice_catalog.user.json").write_text('{"schema_version":1,"messages":[{"id":"x","intent":"X","text":"x","requires_all":["__import__(\\"os\\")"],"requires_any":[],"forbids":[],"claims":[]}]}', encoding="utf-8")
        v = VoiceEngine(root)
        assert len(v.catalog.messages) >= 100
        assert v.catalog.errors


def run():
    test_coretemp_decode()
    test_context_load_and_tools()
    test_context_power_and_bowl()
    test_context_temperature()
    test_events_and_voice()
    test_gap_and_accessory_layout()
    test_temperature_calibration_v23()
    test_catalog_failure_and_model_free_config()
    assert len(set(FRAMES["SLEEP"])) == 1
    assert all("z" not in frame.lower() for frame in FRAMES["SLEEP"])
    print("DeskPet v2.4.0-rc9-classic3 regression self-test: OK")


if __name__ == "__main__":
    run()
