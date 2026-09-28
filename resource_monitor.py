"""Low-frequency process-tree accounting; no model imports or command lines."""
from __future__ import annotations
import os
import threading
import time
import psutil


class ResourceMonitor:
    def __init__(self, diagnostics, *, roles=None, interval_s=5.0):
        self.diag = diagnostics
        self.roles = roles or (lambda: {})
        self.interval = max(.1, float(interval_s))
        self._stop = threading.Event()
        self._thread = None
        self._root = psutil.Process(os.getpid())

    def collect(self):
        labels = self.roles()
        try:
            processes = [self._root] + self._root.children(recursive=True)
        except psutil.Error:
            processes = [self._root]
        rows = []
        for proc in processes:
            try:
                with proc.oneshot():
                    memory = proc.memory_info()
                    name = proc.name()
                    role = labels.get(proc.pid, "UI / rules" if proc.pid == self._root.pid else "Child process")
                    if "powershell" in name.lower():
                        role = "Windows sensors"
                    rows.append({"pid": proc.pid, "name": name, "role": role,
                                 "rss_bytes": memory.rss,
                                 "private_bytes": getattr(memory, "private", None)})
            except (psutil.Error, OSError):
                continue
        summary = {"sampled_at": time.time(), "processes": rows,
                   "rss_sum_bytes": sum(r["rss_bytes"] for r in rows),
                   "note": "RSS sum may double-count shared pages. Windows private bytes are committed, not resident bytes."}
        self.diag.set_summary(resources=summary)
        return summary

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="DeskPetMemory", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.collect()
            except Exception as exc:
                self.diag.event("APP", "resource_sample_failed", str(exc), level="WARNING")
            self._stop.wait(self.interval)

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=.2)
