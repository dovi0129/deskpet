"""Edition-specific config migration; old model settings are not used."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path

DEFAULT = {
    "schema_version": 6, "edition": "classic", "x": None, "y": None,
    "always_on_top": True, "transparent": True, "pet_name": "DeskPet", "device_name": "",
    "voice": {"mode": "rule", "level": "normal", "recent_limit": 12,
              "family_cooldown_s": 60, "show_transient_when_collapsed": True},
    "migration_notice_pending": False,
    # classic3
    "birthday": "",              # "MM-DD" only; the year is never stored
    "daily_summary_hour": 18,    # evening summary after this local hour
    "cat_size": "small",         # "small" | "large"
    "walk": {"enabled": False, "interval_min": 3},
    # "servers" / "server_cat" were dropped: lab servers are watched by ZenPet\ServerCat.
}


def load_config(path: Path, diag) -> dict:
    config = copy.deepcopy(DEFAULT)
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("configuration exceeds 1 MiB")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("configuration must be an object")
    except FileNotFoundError:
        return config
    except (OSError, ValueError, UnicodeError) as exc:
        diag.exception("APP", "config_read_failed", exc)
        return config
    if data.get("schema_version") != 6 or data.get("edition") != "classic":
        backup = path.with_name("config.pre-classic.backup.json")
        try:
            with backup.open("xb") as out:
                out.write(path.read_bytes())
        except FileExistsError:
            pass
        except OSError as exc:
            diag.exception("APP", "config_backup_failed", exc)
        diag.event("APP", "config_migration", "Classic 설정 이관", to_schema=6)
    for key in ("x", "y"):
        value = data.get(key)
        config[key] = value if isinstance(value, int) and not isinstance(value, bool) and abs(value) <= 100000 else None
    for key in ("always_on_top", "transparent"):
        if isinstance(data.get(key), bool):
            config[key] = data[key]
    for key in ("pet_name", "device_name"):
        if isinstance(data.get(key), str):
            config[key] = data[key].strip()[:100]
    voice = data.get("voice", {})
    if isinstance(voice, dict):
        if voice.get("level") in {"quiet", "normal", "chatty"}:
            config["voice"]["level"] = voice["level"]
        for key, lo, hi, cast in (("recent_limit", 1, 12, int), ("family_cooldown_s", 0, 3600, float)):
            try:
                value = cast(voice.get(key, config["voice"][key]))
                if math.isfinite(value):
                    config["voice"][key] = max(lo, min(hi, value))
            except (TypeError, ValueError, OverflowError):
                pass
        if isinstance(voice.get("show_transient_when_collapsed"), bool):
            config["voice"]["show_transient_when_collapsed"] = voice["show_transient_when_collapsed"]
    from calendar_facts import valid_birthday
    config["birthday"] = valid_birthday(data.get("birthday", ""))
    hour = data.get("daily_summary_hour")
    if isinstance(hour, int) and not isinstance(hour, bool) and 0 <= hour <= 23:
        config["daily_summary_hour"] = hour
    if data.get("cat_size") in ("small", "large"):
        config["cat_size"] = data["cat_size"]
    walk = data.get("walk")
    if isinstance(walk, dict):
        if isinstance(walk.get("enabled"), bool):
            config["walk"]["enabled"] = walk["enabled"]
        iv = walk.get("interval_min")
        if isinstance(iv, (int, float)) and not isinstance(iv, bool) and 1 <= iv <= 600:
            config["walk"]["interval_min"] = iv
    return config
