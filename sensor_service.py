"""One sensor owner and one latest snapshot; no GUI I/O or sample backlog."""
from __future__ import annotations
import threading
import time
from diagnostics import get_diagnostics


class SensorService:
    def __init__(self, factory, *, diagnostics=None, interval_s=1.0):
        self.diag = diagnostics or get_diagnostics()
        self._factory = factory
        self._interval = max(.05, float(interval_s))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._latest = None
        self._monitor = None
        self._reset = False
        self._log = False
        self._thread = threading.Thread(target=self._run, name="DeskPetSensors", daemon=True)
        self._thread.start()

    @property
    def probe_pid(self):
        monitor = self._monitor
        probe = getattr(monitor, "probe", None)
        proc = getattr(probe, "_proc", None)
        return getattr(proc, "pid", None)

    def sample(self):
        """Nonblocking handoff. Consumers must respect the acquisition timestamp."""
        with self._lock:
            return self._latest

    def request_sample(self):
        """Coalesce explicit refreshes; never sample on the GUI thread."""
        self._wake.set()

    def reset_temperature_calibration(self):
        with self._lock:
            self._reset = True
        self._wake.set()

    def write_sensor_log_now(self):
        with self._lock:
            self._log = True
        self._wake.set()
        return self.diag.path

    def _run(self):
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    if self._monitor is None:
                        self._monitor = self._factory()
                    with self._lock:
                        reset, log = self._reset, self._log
                        self._reset = self._log = False
                    if reset:
                        self._monitor.reset_temperature_calibration()
                    sample = self._monitor.sample()
                    if not self._stop.is_set():
                        with self._lock:
                            self._latest = sample
                    if log:
                        self._monitor.write_sensor_log_now()
                    self.diag.set_summary(sensor_service={"status": "RUNNING", "thread": "DeskPetSensors",
                        "sample_seconds": round(time.monotonic() - started, 4),
                        "snapshot_id": getattr(sample, "snapshot_id", None)})
                except Exception as exc:
                    self.diag.exception("SENSOR", "sample_failed", exc)
                    self.diag.set_summary(sensor_service={"status": "ERROR", "error": str(exc)})
                # Wake early only for explicit coalesced control requests.
                self._wake.wait(max(.01, self._interval - (time.monotonic() - started)))
                self._wake.clear()
        finally:
            monitor = self._monitor
            if monitor is not None:
                try:
                    monitor.close()
                except Exception as exc:
                    self.diag.exception("SENSOR", "close_failed", exc)
            self._monitor = None

    def close(self):
        self._stop.set()
        self._wake.set()
        # Never wait on a slow WMI/ASUS call on the Tk thread.
        self._thread.join(timeout=.15)
        # Ensure the external PowerShell child cannot survive app shutdown,
        # even when a different native sensor call is still blocked.
        probe = getattr(self._monitor, "probe", None)
        if self._thread.is_alive() and probe is not None:
            probe.stop()
