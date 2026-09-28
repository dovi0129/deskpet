"""Small, deterministic manual summaries. Unknown never means zero or healthy."""
from __future__ import annotations

def context_key(ctx, snapshot) -> tuple:
    """Meaning epoch, NOT a new epoch on every 1-second sample.

    Raw power connection changes invalidate immediately, even while the authoritative
    power rule is waiting through its debounce period. Changing sample IDs alone does not.
    """
    return (ctx.session_id, ctx.context_revision, ctx.temp_source_key,
            getattr(snapshot, "plugged", None),
            getattr(snapshot, "battery_charging_flag", None),
            getattr(snapshot, "battery_discharging_flag", None),
            bool(getattr(snapshot, "temp_valid", False)),
            bool(getattr(snapshot, "gpu_valid", False)),
            bool(getattr(snapshot, "cpu_valid", False)))


def status_sentence(ctx, snapshot) -> str:
    if ctx is None or snapshot is None:
        return "센서 살펴보는 중"
    # The existing thermal rules, not a new raw-threshold shortcut, decide urgency.
    if ctx.thermal == "CRITICAL":
        return "많이 뜨거워. 온도부터 봐줘."
    if ctx.thermal == "HOT":
        return "아직 뜨겁다. 열 좀 식히자."
    if ctx.battery_band == "CRITICAL" and ctx.power != "CHARGING":
        return "배터리가 얼마 안 남았어."
    load = {"CPU_BUSY": "CPU 일하는 중", "GPU_BUSY": "GPU 일하는 중",
            "BOTH_BUSY": "CPU·GPU 일하는 중", "WORKING": "일하는 중", "IDLE": "잠깐 쉬는 중", "SLEEP": "조용히 쉬는 중"}.get(ctx.load, "부하 확인 중")
    thermal = {"COOLING": "조금씩 식는 중", "RELIEF": "열이 좀 가셨네",
               "WARM": "살짝 따뜻하네", "NORMAL": "온도 안정적",
               "COOL": "온도 안정적"}.get(ctx.thermal, "온도 확인 중")
    if not getattr(snapshot, "temp_valid", False) or ctx.temp_confidence in {"NONE", "LOW"}:
        thermal = "온도 확인 필요"
    if ctx.temp_confidence == "LEGACY":
        thermal = "온도는 근사값"
    return f"{load} · {thermal}"
