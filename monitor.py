from __future__ import annotations

from diagnostics import get_diagnostics

from collections import deque
from dataclasses import dataclass, field
import ctypes
import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import sys
import threading
import time
import uuid
from typing import Dict, Optional

import psutil

from gpu_adapters import Gpu, list_gpus, per_gpu
from temperature_manager import TemperatureManager
from window_titles import youtube_on_screen


@dataclass
class BatteryAdvanced:
    remaining_mwh: Optional[float] = None
    design_mwh: Optional[float] = None
    full_mwh: Optional[float] = None
    cycle_count: Optional[int] = None
    voltage_mv: Optional[float] = None
    charge_rate_mw: Optional[float] = None
    discharge_rate_mw: Optional[float] = None
    power_online: Optional[bool] = None
    charging: Optional[bool] = None
    discharging: Optional[bool] = None


@dataclass
class AdvancedSnapshot:
    cpu_temp_c: Optional[float] = None
    cpu_temp_source: str = ""
    cpu_temp_sensor_name: str = ""
    cpu_temp_reliable: bool = False
    cpu_temp_diagnostics: str = ""
    thermal_zone_c: Optional[float] = None
    thermal_zone_source: str = ""
    thermal_zone_sensor_name: str = ""
    gpu_name: str = ""
    gpu_kind: str = "unknown"
    gpu_has_discrete: bool = False
    gpu_other_count: int = 0
    gpu_usage_scope: str = "all-adapters"
    gpu_overall: float = 0.0
    gpu_valid: bool = False
    gpu_3d: float = 0.0
    gpu_compute: float = 0.0
    gpu_video_decode: float = 0.0
    gpu_video_encode: float = 0.0
    gpu_copy: float = 0.0
    gpu_luids: dict[str, float] = field(default_factory=dict)  # adapter LUID -> busiest engine %
    npu_present: bool = False
    npu_overall: Optional[float] = None
    npu_source: str = ""
    battery: BatteryAdvanced = field(default_factory=BatteryAdvanced)
    probe_ok: bool = False
    probe_error: str = ""
    updated_at: float = 0.0


@dataclass
class Snapshot:
    cpu: float
    cpu_temp_c: Optional[float]
    cpu_temp_source: str
    cpu_temp_sensor_name: str
    cpu_temp_reliable: bool
    cpu_temp_confidence: str
    cpu_temp_diagnostics: str
    cpu_temp_raw_thermal_c: Optional[float]
    cpu_temp_raw_thermal_source: str
    cpu_temp_raw_thermal_sensor_name: str
    cpu_temp_calibration_summary: str
    ram: float
    ram_used_gb: float
    ram_total_gb: float
    battery_percent: Optional[float]
    plugged: bool
    idle_seconds: float
    uptime_seconds: int
    gpu_name: str
    gpu_kind: str
    gpu_has_discrete: bool
    gpu_other_count: int
    gpu_usage_scope: str
    gpu: float
    gpu_3d: float
    gpu_compute: float
    gpu_video_decode: float
    gpu_video_encode: float
    gpu_copy: float
    battery_flow_w: Optional[float]
    battery_flow_source: str
    battery_remaining_wh: Optional[float]
    battery_design_wh: Optional[float]
    battery_full_wh: Optional[float]
    battery_health_percent: Optional[float]
    battery_cycle_count: Optional[int]
    battery_voltage_v: Optional[float]
    charged_session_wh: float
    discharged_session_wh: float
    ai_running: bool
    ssh_running: bool
    tools_text: str
    probe_ok: bool
    probe_error: str
    npu_present: bool = False
    npu: Optional[float] = None
    npu_source: str = ""
    snapshot_id: int = 0
    session_id: str = ""
    received_at_mono: float = 0.0
    wall_time: float = 0.0
    cpu_valid: bool = True
    ram_valid: bool = True
    gpu_present: bool = False
    gpu_valid: bool = False
    advanced_age_s: Optional[float] = None
    npu_valid: bool = False
    temp_valid: bool = False
    temp_acquired_at: float = 0.0
    battery_present: Optional[bool] = None
    battery_flow_valid: bool = False
    battery_charging_flag: Optional[bool] = None
    battery_discharging_flag: Optional[bool] = None
    tools_present: frozenset[str] = frozenset()
    tools_scan_status: str = "UNKNOWN"
    tools_scan_seq: int = 0
    youtube_visible: Optional[bool] = None  # a browser window titled "YouTube" is on screen
    # Two or more GPUs: (label, %) per GPU, e.g. (("iGPU", 3.0), ("dGPU", 71.0)); None = not read.
    # Empty with one GPU (the card keeps its single GPU row, which is `gpu`).
    gpu_split: tuple[tuple[str, Optional[float]], ...] = ()


class AdvancedProbe:
    """Persistent PowerShell worker for Windows GPU Engine + ACPI battery WMI data."""

    def __init__(self, script_path: Path) -> None:
        self.script_path = script_path
        self.latest = AdvancedSnapshot()
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if sys.platform != "win32":
            return
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._proc = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(self.script_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            self._thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._thread.start()
        except Exception as exc:
            with self._lock:
                self.latest.probe_error = f"PowerShell 시작 실패: {exc}"

    def _reader_loop(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        threading.Thread(target=self._stderr_loop, args=(self._proc,), name="DeskPetProbeStderr", daemon=True).start()
        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                gpu = data.get("gpu") or {}
                npu = data.get("npu") or {}
                temp = data.get("temperature") or {}
                thermal_zone = data.get("thermal_zone") or {}
                bat = data.get("battery") or {}
                snap = AdvancedSnapshot(
                    cpu_temp_c=_opt_num(temp.get("cpu_c")),
                    cpu_temp_source=str(temp.get("source") or ""),
                    cpu_temp_sensor_name=str(temp.get("sensor_name") or ""),
                    cpu_temp_reliable=bool(temp.get("reliable", False)),
                    cpu_temp_diagnostics=str(temp.get("diagnostics") or ""),
                    thermal_zone_c=_opt_num(thermal_zone.get("cpu_c")),
                    thermal_zone_source=str(thermal_zone.get("source") or ""),
                    thermal_zone_sensor_name=str(thermal_zone.get("sensor_name") or ""),
                    gpu_name=str(data.get("gpu_name") or ""),
                    gpu_kind=str(data.get("gpu_kind") or "unknown"),
                    gpu_has_discrete=bool(data.get("gpu_has_discrete", False)),
                    gpu_other_count=_opt_int(data.get("gpu_other_count")) or 0,
                    gpu_usage_scope=str(data.get("gpu_usage_scope") or "all-adapters"),
                    gpu_overall=_num(gpu.get("overall")),
                    gpu_valid=bool(gpu.get("valid", False)),
                    gpu_3d=_num(gpu.get("3d")),
                    gpu_compute=_num(gpu.get("compute")),
                    gpu_video_decode=_num(gpu.get("video_decode")),
                    gpu_video_encode=_num(gpu.get("video_encode")),
                    gpu_copy=_num(gpu.get("copy")),
                    gpu_luids={str(k): _num(v) for k, v in (data.get("gpu_luids") or {}).items()},
                    npu_present=bool(npu.get("present", False)),
                    npu_overall=_opt_num(npu.get("overall")),
                    npu_source=str(npu.get("source") or ""),
                    battery=BatteryAdvanced(
                        remaining_mwh=_opt_num(bat.get("remaining_mwh")),
                        design_mwh=_opt_num(bat.get("design_mwh")),
                        full_mwh=_opt_num(bat.get("full_mwh")),
                        cycle_count=_opt_int(bat.get("cycle_count")),
                        voltage_mv=_opt_num(bat.get("voltage_mv")),
                        charge_rate_mw=_opt_num(bat.get("charge_rate_mw")),
                        discharge_rate_mw=_opt_num(bat.get("discharge_rate_mw")),
                        power_online=_opt_bool(bat.get("power_online")),
                        charging=_opt_bool(bat.get("charging")),
                        discharging=_opt_bool(bat.get("discharging")),
                    ),
                    probe_ok=True,
                    updated_at=time.monotonic(),
                )
                with self._lock:
                    self.latest = snap
            except Exception as exc:
                with self._lock:
                    self.latest.probe_error = f"probe parse: {type(exc).__name__}"
                get_diagnostics().exception("SENSOR", "probe_parse_error", exc)

        if not self._stop.is_set():
            with self._lock:
                self.latest.probe_ok = False
                self.latest.probe_error = "PowerShell probe 종료"
            get_diagnostics().event("SENSOR", "probe_exit", "센서 프로세스가 종료됨", level="WARNING")

    def _stderr_loop(self, proc) -> None:
        if proc.stderr is None:
            return
        for line in proc.stderr:
            if self._stop.is_set():
                break
            if line.strip():
                get_diagnostics().event("SENSOR", "probe_stderr", line.rstrip(), level="WARNING")

    def snapshot(self) -> AdvancedSnapshot:
        with self._lock:
            return AdvancedSnapshot(
                cpu_temp_c=self.latest.cpu_temp_c,
                cpu_temp_source=self.latest.cpu_temp_source,
                cpu_temp_sensor_name=self.latest.cpu_temp_sensor_name,
                cpu_temp_reliable=self.latest.cpu_temp_reliable,
                cpu_temp_diagnostics=self.latest.cpu_temp_diagnostics,
                thermal_zone_c=self.latest.thermal_zone_c,
                thermal_zone_source=self.latest.thermal_zone_source,
                thermal_zone_sensor_name=self.latest.thermal_zone_sensor_name,
                gpu_name=self.latest.gpu_name,
                gpu_kind=self.latest.gpu_kind,
                gpu_has_discrete=self.latest.gpu_has_discrete,
                gpu_other_count=self.latest.gpu_other_count,
                gpu_usage_scope=self.latest.gpu_usage_scope,
                gpu_overall=self.latest.gpu_overall,
                gpu_valid=self.latest.gpu_valid,
                gpu_3d=self.latest.gpu_3d,
                gpu_compute=self.latest.gpu_compute,
                gpu_video_decode=self.latest.gpu_video_decode,
                gpu_video_encode=self.latest.gpu_video_encode,
                gpu_copy=self.latest.gpu_copy,
                gpu_luids=dict(self.latest.gpu_luids),
                npu_present=self.latest.npu_present,
                npu_overall=self.latest.npu_overall,
                npu_source=self.latest.npu_source,
                battery=BatteryAdvanced(**vars(self.latest.battery)),
                probe_ok=self.latest.probe_ok,
                probe_error=self.latest.probe_error,
                updated_at=self.latest.updated_at,
            )

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def _num(value, default: float = 0.0) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except Exception:
        return default


def _opt_num(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        # ACPI/WMI commonly uses UINT max values as "unknown" sentinels.
        if abs(number) >= 10_000_000:
            return None
        return number
    except Exception:
        return None


def _opt_int(value) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _opt_bool(value) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"true", "1", "yes"}



class CoreTempSharedData(ctypes.Structure):
    """Original Core Temp shared-memory structure (4-byte packing)."""

    _pack_ = 4
    _fields_ = [
        ("uiLoad", ctypes.c_uint32 * 256),
        ("uiTjMax", ctypes.c_uint32 * 128),
        ("uiCoreCnt", ctypes.c_uint32),
        ("uiCPUCnt", ctypes.c_uint32),
        ("fTemp", ctypes.c_float * 256),
        ("fVID", ctypes.c_float),
        ("fCPUSpeed", ctypes.c_float),
        ("fFSBSpeed", ctypes.c_float),
        ("fMultiplier", ctypes.c_float),
        ("sCPUName", ctypes.c_char * 100),
        ("ucFahrenheit", ctypes.c_ubyte),
        ("ucDeltaToTjMax", ctypes.c_ubyte),
    ]


class CoreTempSharedDataEx(ctypes.Structure):
    """Core Temp v2 shared-memory structure (4-byte packing)."""

    _pack_ = 4
    _fields_ = CoreTempSharedData._fields_ + [
        ("ucTdpSupported", ctypes.c_ubyte),
        ("ucPowerSupported", ctypes.c_ubyte),
        ("uiStructVersion", ctypes.c_uint32),
        ("uiTdp", ctypes.c_uint32 * 128),
        ("fPower", ctypes.c_float * 128),
        ("fMultipliers", ctypes.c_float * 256),
    ]


class CoreTempSharedMemoryReader:
    """Read Core Temp's documented shared-memory block directly.

    Core Temp publishes per-core temperatures through the named mapping
    ``CoreTempMappingObjectEx``.  Reading the mapping avoids shipping Core
    Temp's helper DLL and does not require DeskPet to talk to a CPU driver.
    Core Temp itself must be running for this provider to be available.
    """

    FILE_MAP_READ = 0x0004
    MAPPINGS = (
        ("CoreTempMappingObjectEx", CoreTempSharedDataEx),
        ("CoreTempMappingObject", CoreTempSharedData),
    )

    def __init__(self) -> None:
        self.status = "not attempted"
        self.last_error = ""
        self.last_value: Optional[float] = None
        self.last_cpu_name = ""
        self.last_core_count = 0
        self.last_max_core_index: Optional[int] = None
        self.last_mapping = ""

    @staticmethod
    def _cpu_name(data) -> str:
        try:
            raw = bytes(data.sCPUName)
            return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
        except Exception:
            return ""

    @staticmethod
    def _extract_temperature(data) -> tuple[Optional[float], list[float], Optional[int]]:
        """Return (max_core_celsius, all_plausible_core_temperatures, max_index)."""
        try:
            core_count = int(data.uiCoreCnt)
            cpu_count = int(data.uiCPUCnt)
        except Exception:
            return None, [], None

        if not (1 <= core_count <= 256 and 1 <= cpu_count <= 128):
            return None, [], None
        total = core_count * cpu_count
        if total < 1 or total > 256:
            return None, [], None

        fahrenheit = bool(data.ucFahrenheit)
        delta_mode = bool(data.ucDeltaToTjMax)
        temps: list[float] = []
        indexed: list[tuple[int, float]] = []

        for index in range(total):
            try:
                raw = float(data.fTemp[index])
            except Exception:
                continue

            if not (-1000.0 < raw < 1000.0):
                continue

            if delta_mode:
                delta_c = raw * (5.0 / 9.0) if fahrenheit else raw
                cpu_index = min(cpu_count - 1, index // core_count)
                tj_raw = float(data.uiTjMax[cpu_index])
                if fahrenheit and tj_raw > 140.0:
                    tj_c = (tj_raw - 32.0) * (5.0 / 9.0)
                else:
                    tj_c = tj_raw
                value_c = tj_c - delta_c
            elif fahrenheit:
                value_c = (raw - 32.0) * (5.0 / 9.0)
            else:
                value_c = raw

            if 5.0 <= value_c <= 120.0:
                value_c = float(value_c)
                temps.append(value_c)
                indexed.append((index, value_c))

        if not indexed:
            return None, [], None
        max_index, max_value = max(indexed, key=lambda item: item[1])
        return max_value, temps, max_index

    def _read_mapping(self, mapping_name: str, struct_type) -> Optional[float]:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_mapping = kernel32.OpenFileMappingW
        open_mapping.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        open_mapping.restype = wintypes.HANDLE
        map_view = kernel32.MapViewOfFile
        map_view.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, ctypes.c_size_t,
        ]
        map_view.restype = ctypes.c_void_p
        unmap = kernel32.UnmapViewOfFile
        unmap.argtypes = [ctypes.c_void_p]
        unmap.restype = wintypes.BOOL
        close = kernel32.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL

        handle = open_mapping(self.FILE_MAP_READ, False, mapping_name)
        if not handle:
            err = ctypes.get_last_error()
            self.last_error = f"{mapping_name}: OpenFileMapping error {err}"
            return None

        view = None
        try:
            size = ctypes.sizeof(struct_type)
            view = map_view(handle, self.FILE_MAP_READ, 0, 0, size)
            if not view:
                err = ctypes.get_last_error()
                self.last_error = f"{mapping_name}: MapViewOfFile error {err}"
                return None

            raw = ctypes.string_at(view, size)
            data = struct_type.from_buffer_copy(raw)
            value, temps, max_index = self._extract_temperature(data)
            if value is None:
                self.last_error = (
                    f"{mapping_name}: no plausible core temperatures "
                    f"(cores={int(data.uiCoreCnt)}, cpus={int(data.uiCPUCnt)})"
                )
                return None

            self.last_value = float(value)
            self.last_cpu_name = self._cpu_name(data)
            self.last_core_count = len(temps)
            self.last_max_core_index = max_index
            self.last_mapping = mapping_name
            self.status = "Core Temp shared memory OK"
            self.last_error = ""
            return float(value)
        finally:
            if view:
                try:
                    unmap(view)
                except Exception:
                    pass
            try:
                close(handle)
            except Exception:
                pass

    def read(self) -> Optional[float]:
        if sys.platform != "win32":
            self.status = "non-Windows"
            return None

        errors: list[str] = []
        for mapping_name, struct_type in self.MAPPINGS:
            value = self._read_mapping(mapping_name, struct_type)
            if value is not None:
                return value
            if self.last_error:
                errors.append(self.last_error)

        self.status = "Core Temp not available"
        self.last_error = " | ".join(errors[-2:])
        return None

    def diagnostic_text(self) -> str:
        bits = [self.status]
        if self.last_value is not None:
            bits.append(f"last={self.last_value:.1f}C")
        if self.last_core_count:
            if self.last_max_core_index is not None:
                bits.append(f"max core #{self.last_max_core_index + 1} of {self.last_core_count}")
            else:
                bits.append(f"max of {self.last_core_count} cores")
        if self.last_cpu_name:
            bits.append(self.last_cpu_name)
        if self.last_mapping:
            bits.append(self.last_mapping)
        if self.last_error:
            bits.append(self.last_error)
        return "; ".join(bits)


class AsusFirmwareTempReader:
    """Read-only ASUS CPU temperature endpoint through the ATKACPI driver.

    ASUS notebooks with ASUS System Control Interface commonly expose the same
    firmware temperature endpoint used by G-Helper.  This reader only issues a
    DSTS (device-status) query; it never writes firmware settings.
    """

    FILE_NAME = r"\\.\ATKACPI"
    CONTROL_CODE = 0x0022240C
    DSTS = 0x53545344
    TEMP_CPU = 0x00120094
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x00000080

    def __init__(self) -> None:
        self.handle = None
        self.status = "not attempted"
        self.last_error = ""
        self.last_value: Optional[float] = None
        self.disabled = False
        if sys.platform == "win32":
            self._open()
        else:
            self.status = "non-Windows"

    def _open(self) -> None:
        try:
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_file = kernel32.CreateFileW
            create_file.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
            ]
            create_file.restype = wintypes.HANDLE

            handle = create_file(
                self.FILE_NAME,
                self.GENERIC_READ | self.GENERIC_WRITE,
                self.FILE_SHARE_READ | self.FILE_SHARE_WRITE,
                None,
                self.OPEN_EXISTING,
                self.FILE_ATTRIBUTE_NORMAL,
                None,
            )
            invalid = ctypes.c_void_p(-1).value
            handle_value = handle if isinstance(handle, int) else getattr(handle, "value", handle)
            if not handle or handle_value == invalid:
                err = ctypes.get_last_error()
                self.status = "ATKACPI unavailable"
                self.last_error = f"CreateFile error {err}"
                return

            self.handle = handle
            self.status = "ATKACPI connected"
        except Exception as exc:
            self.status = "ATKACPI open failed"
            self.last_error = f"{type(exc).__name__}: {exc}"

    def read(self) -> Optional[float]:
        if sys.platform != "win32" or self.handle is None or self.disabled:
            return None
        try:
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            ioctl = kernel32.DeviceIoControl
            ioctl.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                wintypes.LPVOID, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
            ]
            ioctl.restype = wintypes.BOOL

            args = struct.pack("<II", self.TEMP_CPU, 0)
            payload = struct.pack("<II", self.DSTS, len(args)) + args
            in_buf = ctypes.create_string_buffer(payload, len(payload))
            out_buf = ctypes.create_string_buffer(16)
            returned = wintypes.DWORD(0)
            ok = ioctl(
                self.handle,
                self.CONTROL_CODE,
                in_buf,
                len(payload),
                out_buf,
                len(out_buf),
                ctypes.byref(returned),
                None,
            )
            if not ok:
                err = ctypes.get_last_error()
                self.last_error = f"DeviceIoControl error {err}"
                # ERROR_INVALID_FUNCTION (1) is common on newer ASUS Control
                # Interface revisions.  Repeating the same unsupported IOCTL
                # every second is pointless; the PowerShell worker can still
                # read the same firmware endpoint through AsusAtkWmi_WMNB.DSTS.
                if err == 1:
                    self.disabled = True
                    self.status = "ATKACPI direct IOCTL unsupported; using WMI fallback"
                return None

            raw = struct.unpack("<i", out_buf.raw[:4])[0]
            value = raw - 65536
            if 10 <= value <= 120:
                self.last_value = float(value)
                self.status = "ASUS firmware OK"
                self.last_error = ""
                return float(value)

            self.last_error = f"endpoint returned {value} (raw {raw})"
            return None
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    def diagnostic_text(self) -> str:
        bits = [self.status]
        if self.last_value is not None:
            bits.append(f"last={self.last_value:.1f}C")
        if self.last_error:
            bits.append(self.last_error)
        return "; ".join(bits)

    def close(self) -> None:
        if sys.platform != "win32" or self.handle is None:
            return
        try:
            close_handle = ctypes.windll.kernel32.CloseHandle
            close_handle.argtypes = [ctypes.c_void_p]
            close_handle.restype = ctypes.c_int
            close_handle(self.handle)
        except Exception:
            pass
        self.handle = None


def get_windows_battery_state() -> dict:
    """Best-effort battery state from PowrProf!CallNtPowerInformation.

    SYSTEM_BATTERY_STATE exposes aggregate capacity and an estimated battery
    rate on many Windows laptops even when ACPI WMI omits ChargeRate.
    """
    if sys.platform != "win32":
        return {}

    class SYSTEM_BATTERY_STATE(ctypes.Structure):
        _fields_ = [
            ("AcOnLine", ctypes.c_ubyte),
            ("BatteryPresent", ctypes.c_ubyte),
            ("Charging", ctypes.c_ubyte),
            ("Discharging", ctypes.c_ubyte),
            ("Spare1", ctypes.c_ubyte * 4),
            ("MaxCapacity", ctypes.c_ulong),
            ("RemainingCapacity", ctypes.c_ulong),
            ("Rate", ctypes.c_long),
            ("EstimatedTime", ctypes.c_ulong),
            ("DefaultAlert1", ctypes.c_ulong),
            ("DefaultAlert2", ctypes.c_ulong),
        ]

    state = SYSTEM_BATTERY_STATE()
    try:
        fn = ctypes.windll.powrprof.CallNtPowerInformation
        fn.argtypes = [
            ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong,
            ctypes.c_void_p, ctypes.c_ulong
        ]
        fn.restype = ctypes.c_ulong
        # POWER_INFORMATION_LEVEL.SystemBatteryState == 5
        status = fn(5, None, 0, ctypes.byref(state), ctypes.sizeof(state))
        if status != 0 or not bool(state.BatteryPresent):
            return {}

        rate_mw = int(state.Rate)
        # SYSTEM_BATTERY_STATE.Rate is a signed LONG: positive while charging,
        # negative while discharging, and zero when there is no battery flow.
        # Firmware may also return sentinel/implausible values, which we ignore.
        if abs(rate_mw) >= 1_000_000:
            flow_w = None
        else:
            flow_w = rate_mw / 1000.0
            if abs(flow_w) < 0.05:
                flow_w = 0.0

        max_cap = int(state.MaxCapacity)
        rem_cap = int(state.RemainingCapacity)
        return {
            "ac_online": bool(state.AcOnLine),
            "charging": bool(state.Charging),
            "discharging": bool(state.Discharging),
            "flow_w": flow_w,
            "full_mwh": float(max_cap) if 0 < max_cap < 10_000_000 else None,
            "remaining_mwh": float(rem_cap) if 0 < rem_cap < 10_000_000 else None,
        }
    except Exception:
        return {}


def get_idle_seconds() -> float:
    if sys.platform != "win32":
        return 0.0

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    try:
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        tick = ctypes.windll.kernel32.GetTickCount()
        return ((int(tick) - int(info.dwTime)) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return 0.0


class SystemMonitor:
    def __init__(self) -> None:
        self.probe = AdvancedProbe(Path(__file__).with_name("deskpet_probe.ps1"))
        self.probe.start()
        self._gpus: list[Gpu] = []
        self._gpu_luids_seen: Optional[frozenset[str]] = None
        self._gpus_scanned_at = -1e9
        self._scan_gpus(time.monotonic(), None)
        self.coretemp = CoreTempSharedMemoryReader()
        self.asus_temp = AsusFirmwareTempReader()
        self.temperature = TemperatureManager()
        self.diag = get_diagnostics()
        self._sensor_log_path = self.diag.path
        self.latest_snapshot = None
        self._last_log_key = None
        self._last_log_at = 0.0
        self._last_probe_key = None
        self._sensor_log_written = False
        self._sensor_started_at = time.monotonic()
        psutil.cpu_percent(interval=None)
        self.session_id = uuid.uuid4().hex[:12]
        self._snapshot_id = 0

        self._capacity_history = deque(maxlen=120)  # monotonic, remaining_mWh
        self._last_energy_t = time.monotonic()
        self.charged_session_wh = 0.0
        self.discharged_session_wh = 0.0
        self._last_process_scan = 0.0
        self._ai_running = False
        self._ssh_running = False
        self._tools_text = "없음"
        self._tools_present: frozenset[str] = frozenset()
        self._tools_scan_status = "UNKNOWN"
        self._tools_scan_seq = 0
        self._youtube_visible: Optional[bool] = None

    def _write_sensor_log(self, adv: AdvancedSnapshot, temp_c: Optional[float], source: str) -> None:
        self.diag.event("SENSOR", "provider_report", "센서 연결 진단",
                        coretemp=self.coretemp.diagnostic_text(),
                        asus=self.asus_temp.diagnostic_text(), probe_ok=adv.probe_ok,
                        probe_error=adv.probe_error, attempts=adv.cpu_temp_diagnostics,
                        selected_c=temp_c, selected_source=source,
                        calibration=self.temperature.calibration_summary())

    def _record_snapshot(self, s: Snapshot) -> None:
        temp = {"value_c": s.cpu_temp_c, "confidence": s.cpu_temp_confidence,
                "source": s.cpu_temp_source, "sensor": s.cpu_temp_sensor_name,
                "valid": s.temp_valid, "raw_thermal_c": s.cpu_temp_raw_thermal_c,
                "raw_source": s.cpu_temp_raw_thermal_source, "calibration": s.cpu_temp_calibration_summary,
                "age_s": max(0, s.received_at_mono - s.temp_acquired_at) if s.temp_valid else None,
                "selection_details": s.cpu_temp_diagnostics}
        npu = {"present": s.npu_present, "counter_valid": s.npu_valid,
               "utilization_percent": s.npu if s.npu_valid else None, "source": s.npu_source}
        sensors = {"snapshot_id": s.snapshot_id, "cpu_percent": s.cpu if s.cpu_valid else None,
                   "ram_percent": s.ram if s.ram_valid else None,
                   "gpu_percent": s.gpu if s.gpu_valid else None,
                   "battery_percent": s.battery_percent, "plugged": s.plugged,
                   "battery_flow_w": s.battery_flow_w, "probe_ok": s.probe_ok,
                   "probe_error": s.probe_error, "advanced_age_s": s.advanced_age_s}
        self.diag.set_summary(temperature=temp, npu_counter=npu, sensors=sensors)
        # A max-core index change is not a source change.
        key = (s.cpu_temp_source, s.cpu_temp_confidence, s.temp_valid)
        if key != self._last_log_key:
            self.diag.event("TEMP", "source_changed", "표시 온도 소스/신뢰도 변경",
                            previous=self._last_log_key, snapshot_id=s.snapshot_id, **temp)
            self._last_log_key = key
            self._write_sensor_log(self.probe.snapshot(), s.cpu_temp_c, s.cpu_temp_source)
        probe_key = (s.probe_ok, s.probe_error, s.npu_present, s.npu_valid)
        if probe_key != self._last_probe_key:
            self.diag.event("SENSOR", "availability_changed", "센서/NPU 카운터 가용성 변경",
                            snapshot_id=s.snapshot_id, probe_ok=s.probe_ok,
                            probe_error=s.probe_error, npu=npu)
            self._last_probe_key = probe_key
        interval = 2.0 if self.diag.verbose else 30.0
        if s.received_at_mono - self._last_log_at >= interval:
            self.diag.event("SENSOR", "snapshot", "정기 센서 요약", temperature=temp, npu=npu, **sensors)
            self._last_log_at = s.received_at_mono

    GPU_RESCAN_S = 60.0

    def _scan_gpus(self, now: float, luids: Optional[frozenset[str]]) -> None:
        try:
            self._gpus = list_gpus()
        except Exception as exc:  # DXGI missing or refused: keep the single overall GPU row
            self._gpus = []
            get_diagnostics().exception("SENSOR", "gpu_list_error", exc)
        self._gpu_luids_seen = luids
        self._gpus_scanned_at = now

    def _gpu_split(self, adv: AdvancedSnapshot, valid: bool, now: float) -> tuple[tuple[str, Optional[float]], ...]:
        """Per-GPU use when there are two or more GPUs; () otherwise."""
        luids = frozenset(k.upper() for k in adv.gpu_luids)
        if valid and luids and self._gpu_luids_seen is None:
            self._gpu_luids_seen = luids  # first sample after the start-up scan
        # A GPU was added, removed or restarted (new LUID): list the GPUs again, at most once a minute.
        if valid and luids and luids != self._gpu_luids_seen and now - self._gpus_scanned_at >= self.GPU_RESCAN_S:
            self._scan_gpus(now, luids)
        if len(self._gpus) < 2:
            return ()
        split = per_gpu(self._gpus, adv.gpu_luids if valid else {})
        return tuple((label, (None if v is None else float(v))) for label, v in split)

    def _process_scan(self, now: float) -> None:
        if now - self._last_process_scan < 5:
            return
        self._last_process_scan = now
        names = []
        try:
            for proc in psutil.process_iter(["name"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                if name:
                    names.append(name)
        except Exception:
            # A failed scan is not equivalent to every tool closing. Keep the
            # last confirmed set and expose the failure to EventMemory.
            self._tools_scan_status = "ERROR"
            self._tools_scan_seq += 1
            return

        codex = any(n in {"codex.exe", "codex"} for n in names)
        claude = any(n in {"claude.exe", "claude"} for n in names)
        ssh = any(n in {"ssh.exe", "ssh"} for n in names)
        self._ai_running = codex or claude
        self._ssh_running = ssh

        parts = []
        if codex:
            parts.append("Codex")
        if claude:
            parts.append("Claude")
        if ssh:
            parts.append("SSH")
        self._tools_present = frozenset(parts)
        self._tools_text = " / ".join(parts) if parts else "없음"
        self._tools_scan_status = "OK"
        self._tools_scan_seq += 1
        # Same 5 s cadence: yes/no only, titles are never kept (window_titles.py).
        self._youtube_visible = youtube_on_screen()

    def _battery_flow(
        self, adv: AdvancedSnapshot, now: float, win_bat: Optional[dict] = None, *, adv_fresh: bool = True
    ) -> tuple[Optional[float], str]:
        b = adv.battery if adv_fresh else BatteryAdvanced()

        # Direct ACPI/WMI rate is preferred. These fields are typically mW.
        if (b.charging is True or b.power_online is True) and b.charge_rate_mw is not None and b.charge_rate_mw > 0:
            return b.charge_rate_mw / 1000.0, "WMI"
        if (b.discharging is True or b.power_online is False) and b.discharge_rate_mw is not None and b.discharge_rate_mw > 0:
            return -(b.discharge_rate_mw / 1000.0), "WMI"

        # Windows aggregate battery state often exposes Rate even when the WMI
        # BatteryStatus class leaves ChargeRate/DischargeRate empty.
        if win_bat:
            win_flow = win_bat.get("flow_w")
            if isinstance(win_flow, (int, float)):
                return float(win_flow), "WINAPI"

        # Final fallback: estimate from RemainingCapacity across a >=30 s window.
        remaining_mwh = b.remaining_mwh
        if remaining_mwh is None and win_bat:
            remaining_mwh = win_bat.get("remaining_mwh")
        if remaining_mwh is not None and remaining_mwh > 0:
            self._capacity_history.append((now, remaining_mwh))
            if len(self._capacity_history) >= 2:
                newest_t, newest_cap = self._capacity_history[-1]
                oldest_t, oldest_cap = self._capacity_history[0]
                for t, cap in self._capacity_history:
                    if newest_t - t <= 60:
                        oldest_t, oldest_cap = t, cap
                        break
                dt = newest_t - oldest_t
                if dt >= 30:
                    delta_mwh = newest_cap - oldest_cap
                    # mWh change / seconds -> W: delta_mWh * 3.6 / dt
                    estimated_w = delta_mwh * 3.6 / dt
                    if abs(estimated_w) >= 0.15:
                        return estimated_w, "EST"

        return None, "N/A"

    def sample(self) -> Snapshot:
        now = time.monotonic()
        self._snapshot_id += 1
        cpu = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        try:
            batt = psutil.sensors_battery()
        except Exception:
            batt = None

        battery_percent = float(batt.percent) if batt is not None else None
        plugged = bool(batt.power_plugged) if batt is not None else False

        adv = self.probe.snapshot()
        adv_age = (now - adv.updated_at) if adv.updated_at > 0 else None
        adv_fresh = bool(adv.probe_ok and adv_age is not None and adv_age <= 10.0)

        # Temperature manager:
        # - HIGH: Core Temp / ASUS direct / reliable hardware-monitor CPU sensor
        # - MEDIUM: per-PC calibrated Windows THRM fallback
        # - LOW: raw Windows THRM fallback
        core_temp = self.coretemp.read()
        direct_c = None
        direct_source = ""
        direct_sensor_name = ""
        direct_diagnostics = ""

        if core_temp is not None:
            direct_c = core_temp
            direct_source = "Core Temp shared memory"
            if self.coretemp.last_max_core_index is not None:
                direct_sensor_name = (
                    f"Max core #{self.coretemp.last_max_core_index + 1} "
                    f"of {self.coretemp.last_core_count}"
                )
            else:
                direct_sensor_name = f"Max of {self.coretemp.last_core_count} cores"
            direct_diagnostics = self.coretemp.diagnostic_text()
        else:
            asus_temp = self.asus_temp.read()
            if asus_temp is not None:
                direct_c = asus_temp
                direct_source = "ASUS firmware (ATKACPI)"
                direct_sensor_name = "Temp_CPU 0x00120094"
                direct_diagnostics = self.asus_temp.diagnostic_text()
            elif adv_fresh and adv.cpu_temp_c is not None and adv.cpu_temp_reliable:
                direct_c = adv.cpu_temp_c
                direct_source = adv.cpu_temp_source
                direct_sensor_name = adv.cpu_temp_sensor_name
                direct_diagnostics = adv.cpu_temp_diagnostics

        reading = self.temperature.select(
            now=now,
            direct_c=direct_c,
            direct_source=direct_source,
            direct_sensor_name=direct_sensor_name,
            direct_diagnostics=direct_diagnostics or adv.cpu_temp_diagnostics,
            raw_thermal_c=(adv.thermal_zone_c if adv_fresh else None),
            raw_thermal_source=(adv.thermal_zone_source if adv_fresh else ""),
            raw_thermal_sensor_name=(adv.thermal_zone_sensor_name if adv_fresh else ""),
            raw_thermal_updated_at=(adv.updated_at if adv_fresh else None),
        )
        cpu_temp_c = reading.value_c
        cpu_temp_source = reading.source
        cpu_temp_sensor_name = reading.sensor_name
        cpu_temp_confidence = reading.confidence
        cpu_temp_reliable = cpu_temp_confidence == "HIGH"
        cpu_temp_diagnostics = reading.diagnostics

        # Provider availability is reported to the unified timeline. A missing
        # optional sensor alone is not a fatal application error.
        if not self._sensor_log_written and (cpu_temp_c is not None or now - self._sensor_started_at >= 8.0):
            self._write_sensor_log(adv, cpu_temp_c, cpu_temp_source)
            self._sensor_log_written = True

        win_bat = get_windows_battery_state()
        flow_w, flow_source = self._battery_flow(adv, now, win_bat, adv_fresh=adv_fresh)
        flow_valid = flow_w is not None

        # Integrate only real/estimated battery-side flow. This is session energy,
        # not wall/adapter energy.
        raw_dt = max(0.0, now - self._last_energy_t)
        dt = 0.0 if raw_dt > 15.0 else min(5.0, raw_dt)
        self._last_energy_t = now
        if flow_valid and dt > 0:
            wh = abs(flow_w) * dt / 3600.0
            if flow_w > 0:
                self.charged_session_wh += wh
            elif flow_w < 0:
                self.discharged_session_wh += wh

        self._process_scan(now)

        b = adv.battery if adv_fresh else BatteryAdvanced()
        remaining_mwh = b.remaining_mwh if b.remaining_mwh is not None else win_bat.get("remaining_mwh")
        full_mwh = b.full_mwh if b.full_mwh is not None else win_bat.get("full_mwh")
        remaining_wh = remaining_mwh / 1000.0 if remaining_mwh else None
        design_wh = b.design_mwh / 1000.0 if b.design_mwh else None
        full_wh = full_mwh / 1000.0 if full_mwh else None
        health = None
        if design_wh and full_wh and design_wh > 0:
            health = 100.0 * full_wh / design_wh

        result = Snapshot(
            cpu=cpu,
            cpu_temp_c=cpu_temp_c,
            cpu_temp_source=cpu_temp_source,
            cpu_temp_sensor_name=cpu_temp_sensor_name,
            cpu_temp_reliable=cpu_temp_reliable,
            cpu_temp_confidence=cpu_temp_confidence,
            cpu_temp_diagnostics=cpu_temp_diagnostics,
            cpu_temp_raw_thermal_c=reading.raw_thermal_c,
            cpu_temp_raw_thermal_source=reading.raw_thermal_source,
            cpu_temp_raw_thermal_sensor_name=reading.raw_thermal_sensor_name,
            cpu_temp_calibration_summary=self.temperature.calibration_summary(),
            ram=float(vm.percent),
            ram_used_gb=vm.used / (1024 ** 3),
            ram_total_gb=vm.total / (1024 ** 3),
            battery_percent=battery_percent,
            plugged=plugged,
            idle_seconds=get_idle_seconds(),
            uptime_seconds=max(0, int(time.time() - psutil.boot_time())),
            gpu_name=adv.gpu_name,
            gpu_kind=adv.gpu_kind,
            gpu_has_discrete=adv.gpu_has_discrete,
            gpu_other_count=adv.gpu_other_count,
            gpu_usage_scope=adv.gpu_usage_scope,
            gpu=adv.gpu_overall,
            gpu_3d=adv.gpu_3d,
            gpu_compute=adv.gpu_compute,
            gpu_video_decode=adv.gpu_video_decode,
            gpu_video_encode=adv.gpu_video_encode,
            gpu_copy=adv.gpu_copy,
            battery_flow_w=flow_w,
            battery_flow_source=flow_source,
            battery_remaining_wh=remaining_wh,
            battery_design_wh=design_wh,
            battery_full_wh=full_wh,
            battery_health_percent=health,
            battery_cycle_count=b.cycle_count,
            battery_voltage_v=(b.voltage_mv / 1000.0 if b.voltage_mv else None),
            charged_session_wh=self.charged_session_wh,
            discharged_session_wh=self.discharged_session_wh,
            ai_running=self._ai_running,
            ssh_running=self._ssh_running,
            tools_text=self._tools_text,
            probe_ok=adv.probe_ok,
            probe_error=adv.probe_error,
            npu_present=bool(adv.npu_present),
            npu=(adv.npu_overall if adv_fresh else None),
            npu_source=(adv.npu_source if adv_fresh else ""),
            snapshot_id=self._snapshot_id,
            session_id=self.session_id,
            received_at_mono=now,
            wall_time=time.time(),
            cpu_valid=True,
            ram_valid=True,
            gpu_present=bool(adv.gpu_name),
            gpu_valid=bool(adv_fresh and adv.gpu_name and adv.gpu_valid),
            advanced_age_s=adv_age,
            npu_valid=bool(adv_fresh and adv.npu_present and adv.npu_overall is not None),
            temp_valid=cpu_temp_c is not None,
            temp_acquired_at=(now if direct_c is not None else (adv.updated_at if adv_fresh else 0.0)),
            battery_present=(bool(win_bat) if batt is None else True),
            battery_flow_valid=flow_valid,
            battery_charging_flag=(win_bat.get("charging") if win_bat else (b.charging if adv_fresh else None)),
            battery_discharging_flag=(win_bat.get("discharging") if win_bat else (b.discharging if adv_fresh else None)),
            tools_present=self._tools_present,
            tools_scan_status=self._tools_scan_status,
            tools_scan_seq=self._tools_scan_seq,
            youtube_visible=self._youtube_visible,
            gpu_split=self._gpu_split(adv, bool(adv_fresh and adv.gpu_valid), now),
        )
        self.latest_snapshot = result
        self._record_snapshot(result)
        return result

    def write_sensor_log_now(self) -> Path:
        # Read cached values only: opening diagnostics must not train calibration.
        self.diag.event("SENSOR", "manual_snapshot", "현재 수집값으로 진단 기록", cached=True)
        if self.latest_snapshot is not None:
            self._record_snapshot(self.latest_snapshot)
        return self.diag.path

    def reset_temperature_calibration(self) -> None:
        self.temperature.reset_calibration()
        self.diag.event("TEMP", "calibration_reset", "사용자가 온도 교정을 초기화함")

    def close(self) -> None:
        self.probe.stop()
        self.asus_temp.close()
