from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import deque
from pathlib import Path
import json
import math
import os
import platform
import statistics
import time
from typing import Deque, Optional


@dataclass
class TemperatureCalibration:
    schema_version: int = 2
    status: str = "legacy_unvalidated"  # valid / legacy_unvalidated
    machine_key: str = ""
    sensor_name: str = ""
    sensor_source: str = ""
    reference_kind: str = ""
    scale: float = 1.0
    offset_c: float = 0.0
    lag_seconds: int = 0
    mae_c: float = 999.0  # training/in-sample MAE
    correlation: float = 0.0
    samples: int = 0
    train_samples: int = 0
    validation_samples: int = 0
    validation_mae_c: float = 999.0
    validation_p95_c: float = 999.0
    raw_min_c: float = 0.0
    raw_max_c: float = 0.0
    updated_at: str = ""

    @property
    def base_plausible(self) -> bool:
        return (
            bool(self.machine_key)
            and bool(self.sensor_name)
            and 0.60 <= self.scale <= 1.40
            and -40.0 <= self.offset_c <= 40.0
            and 0 <= self.lag_seconds <= 30
            and self.mae_c <= 4.5
            and self.correlation >= 0.75
            and self.samples >= 50
        )

    @property
    def strict_valid(self) -> bool:
        return (
            self.status == "valid"
            and self.base_plausible
            and self.train_samples >= 120
            and self.validation_samples >= 40
            and self.validation_mae_c <= 4.5
            and self.validation_p95_c <= 8.0
            and self.raw_max_c > self.raw_min_c
        )

    @property
    def valid(self) -> bool:
        """Compatibility alias: usable includes preserved v1.4 legacy fits."""
        return self.base_plausible and self.status in {"valid", "legacy_unvalidated"}


@dataclass
class TemperatureReading:
    value_c: Optional[float]
    source: str
    sensor_name: str
    confidence: str  # HIGH / MEDIUM / LEGACY / LOW / NONE
    diagnostics: str = ""
    raw_thermal_c: Optional[float] = None
    raw_thermal_source: str = ""
    raw_thermal_sensor_name: str = ""
    calibration: Optional[TemperatureCalibration] = None


class TemperatureManager:
    """CPU temperature selection plus per-PC causal THRM calibration.

    A direct CPU source (Core Temp / trusted CPU sensor) remains authoritative.
    While it is present, DeskPet collects paired Windows thermal-zone samples.
    New v2.3 calibrations reserve a chronological holdout and validate the exact
    causal runtime estimator (including lag trend compensation) against future
    samples before granting MEDIUM confidence.

    Older calibration JSONs are preserved as LEGACY: they can still show an
    approximate ``≈`` value, but callers can treat LEGACY conservatively for
    critical thermal behaviour until fresh validation succeeds.
    """

    MAX_HISTORY_SECONDS = 20 * 60
    MAX_LAG_SECONDS = 20
    RECALC_INTERVAL_SECONDS = 30.0
    MIN_TRAIN_PAIRS = 120
    MIN_VALIDATION_PAIRS = 40
    MIN_TOTAL_SPAN_SECONDS = 5 * 60

    def __init__(self, calibration_path: Optional[Path] = None) -> None:
        if calibration_path is None:
            from app_paths import calibration_path as classic_calibration_path
            calibration_path = classic_calibration_path()
        self.calibration_path = calibration_path
        self.machine_key = (platform.node() or os.environ.get("COMPUTERNAME") or "unknown").strip()

        self.reference_history: Deque[tuple[float, float]] = deque(maxlen=3000)
        self.thermal_history: Deque[tuple[float, float]] = deque(maxlen=3000)
        self._last_thermal_stamp: Optional[float] = None
        self._last_calibration_attempt = -1e9
        self._reference_kind = ""
        self._calibration = self._load_calibration()
        self.last_note = ""
        self.calibration_state = "legacy" if self._calibration and self._calibration.status == "legacy_unvalidated" else (
            "valid" if self._calibration and self._calibration.strict_valid else "collecting"
        )

    @property
    def calibration(self) -> Optional[TemperatureCalibration]:
        cal = self._calibration
        if cal and cal.valid and cal.machine_key == self.machine_key:
            return cal
        return None

    @staticmethod
    def _reference_kind_for(source: str, sensor_name: str) -> str:
        src = (source or "").lower()
        if "core temp" in src:
            return "CoreTemp Max Core"
        if "asus" in src:
            return "ASUS CPU Temp"
        if source or sensor_name:
            return f"{source}|{sensor_name}".strip("|")
        return ""

    def reset_calibration(self) -> None:
        self._calibration = None
        self.reference_history.clear()
        self.thermal_history.clear()
        self._last_thermal_stamp = None
        self._last_calibration_attempt = -1e9
        self.calibration_state = "collecting"
        try:
            self.calibration_path.unlink(missing_ok=True)
        except Exception:
            pass
        self.last_note = "temperature calibration reset"

    def _load_calibration(self) -> Optional[TemperatureCalibration]:
        try:
            raw = json.loads(self.calibration_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            legacy = "schema_version" not in raw or "status" not in raw
            cal = TemperatureCalibration()
            for key in TemperatureCalibration.__dataclass_fields__:
                if key in raw and raw[key] is not None:
                    setattr(cal, key, raw[key])
            if legacy:
                cal.schema_version = 1
                cal.status = "legacy_unvalidated"
                cal.train_samples = int(cal.samples or 0)
            if cal.machine_key != self.machine_key or not cal.valid:
                return None
            return cal
        except Exception:
            return None

    def _save_calibration(self, cal: TemperatureCalibration) -> None:
        try:
            self.calibration_path.parent.mkdir(parents=True, exist_ok=True)
            if self.calibration_path.exists():
                backup = self.calibration_path.with_suffix(".legacy.json")
                if not backup.exists():
                    try:
                        backup.write_text(self.calibration_path.read_text(encoding="utf-8"), encoding="utf-8")
                    except Exception:
                        pass
            tmp = self.calibration_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(cal), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.calibration_path)
        except Exception as exc:
            self.last_note = f"calibration save failed: {type(exc).__name__}"

    @staticmethod
    def _nearest_value(history: list[tuple[float, float]], target_t: float, tolerance: float = 1.6) -> Optional[float]:
        """Nearest chronological sample; later sample wins ties, as in rc7.

        Histories are appended in acquisition-time order. Binary search avoids
        rescanning the entire future tail for every candidate calibration lag.
        """
        if not history:
            return None
        from bisect import bisect_right
        key = lambda row: row[0]
        right = bisect_right(history, target_t, key=key)
        candidates = []
        if right:
            candidates.append(right - 1)
        if right < len(history):
            # Preserve rc7's last-insertion tie rule even for equal timestamps.
            candidates.append(bisect_right(history, history[right][0], key=key) - 1)
        best = min(candidates, key=lambda i: (abs(history[i][0] - target_t), -i))
        return history[best][1] if abs(history[best][0] - target_t) <= tolerance else None

    @staticmethod
    def _fit_linear(xs: list[float], ys: list[float]) -> Optional[tuple[float, float, float, float]]:
        if len(xs) < 3 or len(xs) != len(ys):
            return None
        mx = statistics.fmean(xs)
        my = statistics.fmean(ys)
        var_x = sum((x - mx) ** 2 for x in xs)
        var_y = sum((y - my) ** 2 for y in ys)
        if var_x <= 1e-9 or var_y <= 1e-9:
            return None
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        scale = cov / var_x
        offset = my - scale * mx
        corr = cov / math.sqrt(var_x * var_y)
        preds = [scale * x + offset for x in xs]
        mae = statistics.fmean(abs(p - y) for p, y in zip(preds, ys))
        return scale, offset, max(-1.0, min(1.0, corr)), mae

    @staticmethod
    def _trend_at(history: list[tuple[float, float]], index: int, window_s: float = 12.0) -> float:
        if index <= 0:
            return 0.0
        t1, v1 = history[index]
        j = index - 1
        while j > 0 and t1 - history[j - 1][0] <= window_s:
            j -= 1
        t0, v0 = history[j]
        dt = t1 - t0
        return (v1 - v0) / dt if dt >= 2.0 else 0.0

    @staticmethod
    def _p95(values: list[float]) -> float:
        if not values:
            return 999.0
        vals = sorted(values)
        idx = max(0, min(len(vals) - 1, math.ceil(0.95 * len(vals)) - 1))
        return vals[idx]

    def _trim_history(self, now: float) -> None:
        cutoff = now - self.MAX_HISTORY_SECONDS
        while self.reference_history and self.reference_history[0][0] < cutoff:
            self.reference_history.popleft()
        while self.thermal_history and self.thermal_history[0][0] < cutoff:
            self.thermal_history.popleft()

    def observe(
        self,
        *,
        now: float,
        reference_c: Optional[float],
        reference_source: str,
        reference_sensor_name: str,
        thermal_c: Optional[float],
        thermal_source: str,
        thermal_sensor_name: str,
        thermal_updated_at: Optional[float] = None,
    ) -> None:
        reference_kind = self._reference_kind_for(reference_source, reference_sensor_name)
        if reference_c is not None and 10.0 <= reference_c <= 115.0:
            if self._reference_kind and reference_kind and reference_kind != self._reference_kind:
                # Do not train one equation from different physical reference targets.
                self.reference_history.clear()
                self.thermal_history.clear()
                self._last_thermal_stamp = None
            self._reference_kind = reference_kind or self._reference_kind
            self.reference_history.append((now, float(reference_c)))

        thermal_stamp = thermal_updated_at if thermal_updated_at and thermal_updated_at > 0 else now
        if thermal_c is not None and 10.0 <= thermal_c <= 115.0:
            if self._last_thermal_stamp is None or abs(thermal_stamp - self._last_thermal_stamp) > 0.05:
                self.thermal_history.append((thermal_stamp, float(thermal_c)))
                self._last_thermal_stamp = thermal_stamp

        self._trim_history(now)

        if reference_c is None or thermal_c is None or not thermal_sensor_name:
            return
        if now - self._last_calibration_attempt < self.RECALC_INTERVAL_SECONDS:
            return
        self._last_calibration_attempt = now
        self._recalculate(thermal_source, thermal_sensor_name, self._reference_kind)

    def _recalculate(self, thermal_source: str, thermal_sensor_name: str, reference_kind: str) -> None:
        refs = list(self.reference_history)
        thermals = list(self.thermal_history)
        if len(thermals) < self.MIN_TRAIN_PAIRS + self.MIN_VALIDATION_PAIRS or len(refs) < self.MIN_TRAIN_PAIRS + self.MIN_VALIDATION_PAIRS:
            self.calibration_state = "collecting"
            self.last_note = f"calibration collecting: ref={len(refs)}, thrm={len(thermals)}"
            return
        if min(refs[-1][0], thermals[-1][0]) - max(refs[0][0], thermals[0][0]) < self.MIN_TOTAL_SPAN_SECONDS:
            self.calibration_state = "collecting"
            self.last_note = "calibration collecting: need >=5 min span"
            return

        ref_values = [v for _, v in refs]
        thermal_values = [v for _, v in thermals]
        if max(ref_values) - min(ref_values) < 6.0 or max(thermal_values) - min(thermal_values) < 4.0:
            self.calibration_state = "collecting"
            self.last_note = "calibration waiting for wider temperature range"
            return

        # Reserve the last >=40 thermal samples as a chronological future holdout.
        holdout_n = max(self.MIN_VALIDATION_PAIRS, int(round(len(thermals) * 0.25)))
        holdout_n = min(holdout_n, len(thermals) - self.MIN_TRAIN_PAIRS)
        train_thermals = thermals[:-holdout_n]
        holdout_thermals = thermals[-holdout_n:]
        best = None

        for lag in range(0, self.MAX_LAG_SECONDS + 1):
            xs: list[float] = []
            ys: list[float] = []
            for t_thermal, thermal_value in train_thermals:
                ref = self._nearest_value(refs, t_thermal - lag)
                if ref is None:
                    continue
                xs.append(thermal_value)
                ys.append(ref)
            if len(xs) < self.MIN_TRAIN_PAIRS:
                continue
            if max(xs) - min(xs) < 4.0 or max(ys) - min(ys) < 6.0:
                continue
            fit = self._fit_linear(xs, ys)
            if fit is None:
                continue
            scale, offset, corr, train_mae = fit
            if not (0.60 <= scale <= 1.40) or corr < 0.75 or train_mae > 4.5 or not (-40.0 <= offset <= 40.0):
                continue

            errors: list[float] = []
            holdout_start = len(thermals) - holdout_n
            for idx, (t_thermal, raw) in enumerate(holdout_thermals, start=holdout_start):
                ref_now = self._nearest_value(refs, t_thermal)
                if ref_now is None:
                    continue
                trend = self._trend_at(thermals, idx)
                correction = max(-8.0, min(8.0, scale * trend * lag)) if lag else 0.0
                pred = scale * raw + offset + correction
                errors.append(abs(pred - ref_now))
            if len(errors) < self.MIN_VALIDATION_PAIRS:
                continue
            val_mae = statistics.fmean(errors)
            val_p95 = self._p95(errors)
            if val_mae > 4.5 or val_p95 > 8.0:
                continue
            score = val_mae + 0.15 * train_mae + (1.0 - corr) * 1.5 + 0.005 * lag
            item = (score, lag, scale, offset, corr, train_mae, len(xs), len(errors), val_mae, val_p95, min(xs), max(xs))
            if best is None or item[0] < best[0]:
                best = item

        if best is None:
            self.calibration_state = "validating"
            self.last_note = "calibration validating: holdout quality not good enough yet"
            return

        _, lag, scale, offset, corr, train_mae, train_n, val_n, val_mae, val_p95, raw_min, raw_max = best
        cal = TemperatureCalibration(
            schema_version=2,
            status="valid",
            machine_key=self.machine_key,
            sensor_name=thermal_sensor_name,
            sensor_source=thermal_source,
            reference_kind=reference_kind,
            scale=float(scale), offset_c=float(offset), lag_seconds=int(lag),
            mae_c=float(train_mae), correlation=float(corr), samples=int(train_n + val_n),
            train_samples=int(train_n), validation_samples=int(val_n),
            validation_mae_c=float(val_mae), validation_p95_c=float(val_p95),
            raw_min_c=float(raw_min), raw_max_c=float(raw_max),
            updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._calibration = cal
        self.calibration_state = "valid"
        self._save_calibration(cal)
        self.last_note = (
            f"validated: train={train_n}, holdout={val_n}, r={corr:.2f}, "
            f"trainMAE={train_mae:.1f}C, holdoutMAE={val_mae:.1f}C, "
            f"P95={val_p95:.1f}C, lag={lag}s"
        )

    def _thermal_trend_c_per_s(self) -> float:
        hist = list(self.thermal_history)
        if len(hist) < 2:
            return 0.0
        return self._trend_at(hist, len(hist) - 1)

    def calibrated_estimate(self, raw_thermal_c: float, sensor_name: str, sensor_source: str = "") -> Optional[float]:
        cal = self.calibration
        if cal is None or cal.sensor_name != sensor_name:
            return None
        if cal.sensor_source and sensor_source and cal.sensor_source != sensor_source:
            self.calibration_state = "unavailable"
            self.last_note = "calibration not applied: thermal provider changed"
            return None
        if cal.strict_valid and (raw_thermal_c < cal.raw_min_c - 3.0 or raw_thermal_c > cal.raw_max_c + 3.0):
            self.calibration_state = "out_of_range"
            self.last_note = (
                f"calibration out_of_range: raw={raw_thermal_c:.1f}C, "
                f"trained={cal.raw_min_c:.1f}..{cal.raw_max_c:.1f}C"
            )
            return None
        value = cal.scale * float(raw_thermal_c) + cal.offset_c
        if cal.lag_seconds > 0:
            correction = cal.scale * self._thermal_trend_c_per_s() * cal.lag_seconds
            correction = max(-8.0, min(8.0, correction))
            value += correction
        if 10.0 <= value <= 115.0:
            self.calibration_state = "valid" if cal.strict_valid else "legacy"
            return float(value)
        return None

    def select(
        self,
        *,
        now: float,
        direct_c: Optional[float],
        direct_source: str,
        direct_sensor_name: str,
        direct_diagnostics: str,
        raw_thermal_c: Optional[float],
        raw_thermal_source: str,
        raw_thermal_sensor_name: str,
        raw_thermal_updated_at: Optional[float] = None,
    ) -> TemperatureReading:
        self.observe(
            now=now,
            reference_c=direct_c,
            reference_source=direct_source,
            reference_sensor_name=direct_sensor_name,
            thermal_c=raw_thermal_c,
            thermal_source=raw_thermal_source,
            thermal_sensor_name=raw_thermal_sensor_name,
            thermal_updated_at=raw_thermal_updated_at,
        )

        cal = self.calibration
        if direct_c is not None and 10.0 <= direct_c <= 115.0:
            return TemperatureReading(
                value_c=float(direct_c), source=direct_source, sensor_name=direct_sensor_name,
                confidence="HIGH", diagnostics=direct_diagnostics,
                raw_thermal_c=raw_thermal_c, raw_thermal_source=raw_thermal_source,
                raw_thermal_sensor_name=raw_thermal_sensor_name, calibration=cal,
            )

        if raw_thermal_c is not None and raw_thermal_sensor_name:
            estimated = self.calibrated_estimate(raw_thermal_c, raw_thermal_sensor_name, raw_thermal_source)
            if estimated is not None and cal is not None:
                confidence = "MEDIUM" if cal.strict_valid else "LEGACY"
                return TemperatureReading(
                    value_c=estimated,
                    source=f"Calibrated {raw_thermal_source}",
                    sensor_name=raw_thermal_sensor_name,
                    confidence=confidence,
                    diagnostics=self.last_note,
                    raw_thermal_c=float(raw_thermal_c), raw_thermal_source=raw_thermal_source,
                    raw_thermal_sensor_name=raw_thermal_sensor_name, calibration=cal,
                )
            if self.calibration_state not in {"out_of_range", "unavailable"}:
                self.calibration_state = "collecting" if cal is None else self.calibration_state
            return TemperatureReading(
                value_c=float(raw_thermal_c), source=raw_thermal_source,
                sensor_name=raw_thermal_sensor_name, confidence="LOW",
                diagnostics=self.last_note, raw_thermal_c=float(raw_thermal_c),
                raw_thermal_source=raw_thermal_source, raw_thermal_sensor_name=raw_thermal_sensor_name,
                calibration=cal,
            )

        self.calibration_state = "stale" if cal is not None else "unavailable"
        return TemperatureReading(
            value_c=None, source="", sensor_name="", confidence="NONE",
            diagnostics=direct_diagnostics or self.last_note, calibration=cal,
        )

    def calibration_summary(self) -> str:
        cal = self.calibration
        if cal is None:
            return f"{self.calibration_state} · {self.last_note or 'not calibrated'}"
        if cal.strict_valid:
            return (
                f"valid · {cal.sensor_name} · train={cal.train_samples} holdout={cal.validation_samples} · "
                f"r={cal.correlation:.2f} · trainMAE={cal.mae_c:.1f}C · "
                f"valMAE={cal.validation_mae_c:.1f}C P95={cal.validation_p95_c:.1f}C · "
                f"lag={cal.lag_seconds}s"
            )
        return (
            f"legacy_unvalidated · {cal.sensor_name} · n={cal.samples} · r={cal.correlation:.2f} · "
            f"MAE={cal.mae_c:.1f}C · lag={cal.lag_seconds}s"
        )
