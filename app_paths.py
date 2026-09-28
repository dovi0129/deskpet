"""Where DeskPet keeps its data, plus a one-time, read-only import of an older temperature calibration."""
from __future__ import annotations
import json
import os
from pathlib import Path


def data_root() -> Path:
    override = os.environ.get("DESKPET_CLASSIC_DATA_DIR") or os.environ.get("DESKPET_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "DeskPetClassic"
    return Path.home() / ".deskpet-classic"


def calibration_path() -> Path:
    """Import only a small calibration file once; nothing else is read or deleted.

    The marker prevents a deliberate reset being silently undone on next launch.
    The temperature manager still validates machine identity and calibration quality.
    """
    root = data_root()
    target = root / "temperature_calibration.json"
    marker = root / ".calibration-import-checked"
    if target.exists():
        try:
            marker.touch(exist_ok=True)
        except OSError:
            pass
        return target
    if marker.exists():
        return target
    # Explicit data directories are isolated (including test environments).
    isolated = os.environ.get("DESKPET_CLASSIC_DATA_DIR") or os.environ.get("DESKPET_DATA_DIR")
    try:
        root.mkdir(parents=True, exist_ok=True)
        # Resolve the legacy location only when it may be read: an isolated data
        # directory never imports, and Path.home() can fail without HOME/USERPROFILE.
        source = None
        if not isolated:
            legacy = ((Path(os.environ["LOCALAPPDATA"]) / "DeskPet")
                      if os.environ.get("LOCALAPPDATA") else Path.home() / ".deskpet")
            source = legacy / "temperature_calibration.json"
        if source is not None and source.is_file() and source.stat().st_size <= 256 * 1024:
            raw = source.read_bytes()
            if isinstance(json.loads(raw.decode("utf-8")), dict):
                try:
                    with target.open("xb") as stream:
                        stream.write(raw)
                except FileExistsError:
                    pass
        marker.touch(exist_ok=True)
    except (OSError, ValueError, UnicodeError):
        # Failure to migrate must not disable sensor collection.
        pass
    return target
