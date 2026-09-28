"""Which real GPUs this PC has, and each one's use.

Windows' GPU Engine counters are keyed by adapter LUID, but that set also holds
adapters that are not GPUs: the software "Microsoft Basic Render Driver" and
compute-only devices such as an NPU (Intel AI Boost). DXGI lists only display
adapters with their LUIDs, so its list (minus software adapters) is the set of
GPUs shown on the card. With one GPU the card keeps its single "GPU" row.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import re
import sys
from typing import Mapping, Optional, Sequence

GIB = 1024 ** 3
MICROSOFT = 0x1414          # Basic Render / Basic Display drivers
DXGI_ADAPTER_FLAG_SOFTWARE = 2


@dataclass(frozen=True)
class Gpu:
    luid: str               # "0x00000000_0x0000FE6A", as in the counter instance names
    name: str
    kind: str               # integrated / discrete / unknown


def kind_of(name: str, vendor_id: int, dedicated_bytes: int) -> str:
    """Same name rules as deskpet_probe.ps1 (Get-GpuKind), with dedicated memory as the tie-break."""
    if vendor_id == 0x10DE or re.search(r"nvidia", name, re.I):
        return "discrete"
    if vendor_id == 0x8086 or re.search(r"intel", name, re.I):
        # Arc A/B-series cards are discrete; Core Ultra Arc 130V/140V, Iris and UHD are integrated.
        return "discrete" if re.search(r"\bArc(?:\(TM\))?\s+[AB]\d{3,4}\b", name, re.I) else "integrated"
    if vendor_id == 0x1002 or re.search(r"amd|radeon", name, re.I):
        if re.search(r"Radeon\s+(RX\b|Pro\s+W|VII\b|Instinct)|FirePro", name, re.I):
            return "discrete"
        if re.search(r"Radeon(?:\(TM\))?\s+Graphics|Radeon\s+[6789]\d{2}M\b|Vega\s+\d+\s+Graphics", name, re.I):
            return "integrated"
    if dedicated_bytes >= GIB:
        return "discrete"
    return "integrated" if dedicated_bytes > 0 else "unknown"


def labels_for(gpus: Sequence[Gpu]) -> list[str]:
    """Card labels: iGPU / dGPU for the usual laptop pair, otherwise GPU0, GPU1, ..."""
    kinds = [g.kind for g in gpus]
    if len(gpus) == 2 and sorted(kinds) == ["discrete", "integrated"]:
        return ["iGPU" if k == "integrated" else "dGPU" for k in kinds]
    return [f"GPU{i}" for i in range(len(gpus))]


def order(gpus: Sequence[Gpu]) -> list[Gpu]:
    """Integrated first, then discrete, keeping DXGI order inside each group."""
    rank = {"integrated": 0, "discrete": 1}
    return sorted(gpus, key=lambda g: rank.get(g.kind, 2))


def per_gpu(gpus: Sequence[Gpu], luid_use: Mapping[str, float]) -> tuple[tuple[str, Optional[float]], ...]:
    """(label, percent) per GPU; None when that GPU's LUID is missing from the counters."""
    use = {k.upper(): v for k, v in luid_use.items()}
    gpus = order(gpus)
    return tuple((label, use.get(g.luid.upper())) for label, g in zip(labels_for(gpus), gpus))


# ------------------------------------------------------------------ DXGI (Windows only)
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class _DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint),
                ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", _LUID), ("Flags", ctypes.c_uint)]


_IID_IDXGIFactory1 = _GUID(0x770AAE78, 0xF26F, 0x4DBA,
                           (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))


def _method(obj: ctypes.c_void_p, index: int, restype, *argtypes):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


def list_gpus() -> list[Gpu]:
    """Real GPUs from DXGI (software adapters left out). [] when DXGI is unavailable."""
    if sys.platform != "win32":
        return []
    try:
        dxgi = ctypes.WinDLL("dxgi")
    except OSError:
        return []
    factory = ctypes.c_void_p()
    if dxgi.CreateDXGIFactory1(ctypes.byref(_IID_IDXGIFactory1), ctypes.byref(factory)) != 0 or not factory:
        return []
    out: list[Gpu] = []
    seen: set[str] = set()
    try:
        enum = _method(factory, 12, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))  # EnumAdapters1
        i = 0
        while i < 16:
            adapter = ctypes.c_void_p()
            if enum(factory, i, ctypes.byref(adapter)) != 0:
                break  # DXGI_ERROR_NOT_FOUND: no more adapters
            try:
                d = _DXGI_ADAPTER_DESC1()
                if _method(adapter, 10, ctypes.c_long, ctypes.POINTER(_DXGI_ADAPTER_DESC1))(adapter, ctypes.byref(d)) == 0:
                    luid = f"0x{d.AdapterLuid.HighPart & 0xFFFFFFFF:08X}_0x{d.AdapterLuid.LowPart:08X}"
                    software = bool(d.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) or d.VendorId == MICROSOFT
                    if not software and luid not in seen:
                        seen.add(luid)
                        out.append(Gpu(luid, d.Description, kind_of(d.Description, d.VendorId,
                                                                    int(d.DedicatedVideoMemory))))
            finally:
                _method(adapter, 2, ctypes.c_ulong)(adapter)  # Release
            i += 1
    finally:
        _method(factory, 2, ctypes.c_ulong)(factory)
    return out
