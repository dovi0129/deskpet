"""Bounded, single-writer diagnostics. No OpenVINO or GUI dependency.

All subprocess output is bridged to the owning process. Processes never share a
rotating FileHandler. Privacy filtering is best effort, not a secrets guarantee.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import atexit
import json
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile

VERSION = "2.4.0-rc9-classic3"
LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
LOG_KEEP_DAYS = 7
LOG_MIN_SESSIONS = 20
LOG_MAX_TOTAL_BYTES = 200 * 1024 * 1024
_SESSION_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-fA-F]{8}$")


def data_root() -> Path:
    from app_paths import data_root as classic_data_root
    return classic_data_root()


def clean(value, key: str = ""):
    """Redact before disk AND UI; never dump environment or arbitrary config."""
    if key.lower() in {"password", "token", "api_key", "secret", "authorization", "command_line", "cmdline", "terminal", "window_title", "prompt", "hostname", "username"}:
        return "<omitted>"
    if isinstance(value, dict):
        return {str(k): clean(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [clean(x) for x in value]
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        import math
        return value if math.isfinite(value) else None
    text = str(value)
    homes = {str(Path.home()), os.environ.get("USERPROFILE", "")}
    for home in sorted(homes, key=len, reverse=True):
        if home and len(home) > 2:
            text = text.replace(home, "<HOME>").replace(home.replace("\\", "/"), "<HOME>")
    text = re.sub(r"(?i)[A-Z]:[\\/]Users[\\/][^\\/\r\n]+", "<HOME>", text)
    text = re.sub(r"(?i)\b(?:hf_|sk-)[A-Za-z0-9_-]{8,}", "<TOKEN>", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer <TOKEN>", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<EMAIL>", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<IP>", text)
    # Never retain terminal escape sequences in a report or text widget.
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return text if len(text) <= 24000 else text[:24000] + " <TRUNCATED>"


def _tree_size(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file() and not item.is_symlink():
                    total += item.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def _last_activity(path: Path) -> float:
    latest = 0.0
    try:
        latest = path.stat().st_mtime
    except OSError:
        pass
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file() and not item.is_symlink():
                    latest = max(latest, item.stat().st_mtime)
            except OSError:
                pass
    except OSError:
        pass
    return latest


def cleanup_log_sessions(logs_root: Path, *, active_session_id: str = "",
                         keep_days: int = LOG_KEEP_DAYS,
                         min_sessions: int = LOG_MIN_SESSIONS,
                         max_total_bytes: int = LOG_MAX_TOTAL_BYTES,
                         now: float | None = None) -> dict:
    """Bound DeskPet's own session logs without touching exported reports.

    Only directories matching DeskPet's session-id pattern are candidates.  The
    active session and the newest ``min_sessions`` are always retained.  Older
    sessions are removed first when past ``keep_days`` and then, if necessary,
    to approach the total-size cap.  If the protected minimum itself exceeds
    the cap, it is preserved and the condition is reported instead of deleting
    recent evidence.
    """
    logs_root = Path(logs_root)
    now = time.time() if now is None else float(now)
    cutoff = now - max(0, int(keep_days)) * 86400
    rows = []
    try:
        entries = list(logs_root.iterdir()) if logs_root.is_dir() else []
    except OSError:
        entries = []
    for path in entries:
        if not path.is_dir() or path.is_symlink() or not _SESSION_RE.match(path.name):
            continue
        rows.append({"path": path, "name": path.name, "mtime": _last_activity(path), "bytes": _tree_size(path)})
    rows.sort(key=lambda r: (r["mtime"], r["name"]), reverse=True)
    protected_names = {r["name"] for r in rows[:max(0, int(min_sessions))]}
    if active_session_id:
        protected_names.add(active_session_id)
    total = sum(r["bytes"] for r in rows)
    removed = []

    def remove(row, reason):
        nonlocal total
        try:
            shutil.rmtree(row["path"])
        except OSError:
            return False
        total -= row["bytes"]
        removed.append({"session_id": row["name"], "bytes": row["bytes"], "reason": reason})
        return True

    # First enforce time retention for unprotected sessions.
    for row in sorted(rows, key=lambda r: (r["mtime"], r["name"])):
        if row["name"] in protected_names or row["mtime"] >= cutoff:
            continue
        remove(row, "age")

    removed_names = {r["session_id"] for r in removed}
    # Then enforce aggregate size by deleting oldest unprotected sessions, even
    # if they are younger than keep_days.  Never violate the protected minimum.
    if total > max_total_bytes:
        for row in sorted(rows, key=lambda r: (r["mtime"], r["name"])):
            if total <= max_total_bytes:
                break
            if row["name"] in protected_names or row["name"] in removed_names:
                continue
            if remove(row, "size"):
                removed_names.add(row["name"])

    return {
        "removed_sessions": len(removed),
        "freed_bytes": sum(r["bytes"] for r in removed),
        "remaining_sessions": len(rows) - len(removed),
        "remaining_bytes": max(0, total),
        "keep_days": int(keep_days),
        "min_sessions": int(min_sessions),
        "max_total_bytes": int(max_total_bytes),
        "cap_exceeded_by_protected_sessions": total > max_total_bytes,
        "removed": removed,
    }


class Diagnostics:
    def __init__(self, root: Path | None = None, *, console: bool = False,
                 max_bytes: int = 5 * 1024 * 1024, backups: int = 2,
                 queue_size: int = 4096):
        self.session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.started = time.monotonic()
        self.console = console
        self.max_bytes, self.backups = max(1024, max_bytes), max(1, backups)
        self._q: queue.Queue = queue.Queue(maxsize=queue_size)
        self._ring = deque(maxlen=2000)
        self._lock, self._io = threading.RLock(), threading.Lock()
        self._seq = 0
        self._closed = False
        self._stop = threading.Event()
        self.dropped = 0
        self.write_error = ""
        self.verbose_until = 0.0
        self.latest = {}
        self._fp = None
        self.last_cleanup = {}
        requested = (root or data_root()) / "logs" / self.session_id
        self.log_root = requested.parent
        self.directory = requested
        try:
            requested.mkdir(parents=True, exist_ok=False)
            self.path = requested / "deskpet.log"
            self._fp = self.path.open("a", encoding="utf-8", buffering=1)
        except OSError as exc:
            self.write_error = str(clean(exc))
            try:
                self.directory = Path(tempfile.mkdtemp(prefix="DeskPet-diagnostics-"))
            except OSError:
                self.directory = Path(tempfile.gettempdir()) / ("DeskPet-memory-only-" + self.session_id)
            self.path = self.directory / "deskpet.log"
            try:
                self._fp = self.path.open("a", encoding="utf-8", buffering=1)
            except OSError as fallback_exc:
                self.write_error += "; " + str(clean(fallback_exc))
        self._writer = threading.Thread(target=self._run, name="DeskPetLogWriter", daemon=True)
        self._writer.start()
        self.event("APP", "session_start", "진단 기록 시작", version=VERSION,
                   python=platform.python_version(), os=platform.system(),
                   os_release=platform.release(), log_path=str(self.path),
                   log_fallback=bool(self.write_error))
        if not self.write_error and self.directory == requested:
            self.cleanup_history(reason="startup")
        atexit.register(self.close)

    @property
    def verbose(self) -> bool:
        return time.monotonic() < self.verbose_until

    def enable_detail(self, seconds: float = 120.0) -> None:
        self.verbose_until = time.monotonic() + min(600.0, max(1.0, seconds))
        self.event("APP", "detail_enabled", "상세 기록을 임시로 켰어", duration_s=seconds)

    def cleanup_history(self, *, reason: str = "manual") -> dict:
        result = cleanup_log_sessions(self.log_root, active_session_id=self.session_id)
        self.last_cleanup = clean(result)
        level = "WARNING" if result.get("cap_exceeded_by_protected_sessions") else "INFO"
        if result.get("removed_sessions") or reason == "manual" or level == "WARNING":
            self.event("APP", "log_cleanup",
                       f"로그 정리: {result['removed_sessions']}개 세션 삭제, {result['freed_bytes']/1024**2:.1f} MiB 확보",
                       level=level, reason=reason, **result)
        return result

    def event(self, category: str, event: str, message: str = "", *, level: str = "INFO", **details) -> dict:
        if self._closed or (level == "DEBUG" and not self.verbose):
            return {}
        with self._lock:
            self._seq += 1
            row = clean({"ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                         "elapsed_s": round(time.monotonic() - self.started, 3),
                         "session_id": self.session_id, "seq": self._seq,
                         "level": level, "category": category, "event": event,
                         "message": message, "details": details})
            self._ring.append(row)
            try:
                self._q.put_nowait(row)
            except queue.Full:
                # The cat must never wait for disk. Loss is explicit in summary/export.
                self.dropped += 1
            return row

    def exception(self, category: str, event: str, exc: BaseException, **details) -> None:
        self.event(category, event, f"{type(exc).__name__}: {exc}", level="ERROR",
                   traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), **details)

    def set_summary(self, **sections) -> None:
        with self._lock:
            self.latest.update(clean(sections))

    def summary(self) -> dict:
        with self._lock:
            return {"version": VERSION, "session_id": self.session_id,
                    "session_elapsed_s": round(time.monotonic() - self.started, 1),
                    "logger": {"path": str(clean(self.path)), "log_root": str(clean(self.log_root)),
                               "write_error": self.write_error,
                               "dropped_records": self.dropped,
                               "detail_recording": self.verbose,
                               "last_sequence": self._seq,
                               "cleanup": self.last_cleanup,
                               "retention": {"keep_days": LOG_KEEP_DAYS,
                                             "min_sessions": LOG_MIN_SESSIONS,
                                             "max_total_bytes": LOG_MAX_TOTAL_BYTES}},
                    **json.loads(json.dumps(self.latest))}

    def records(self, category: str = "ALL", min_level: str = "DEBUG") -> list[dict]:
        with self._lock:
            return [r for r in self._ring if (category == "ALL" or r["category"] == category)
                    and LEVELS.get(r["level"], 20) >= LEVELS.get(min_level, 10)]

    def _rotate(self) -> None:
        if not self._fp or self._fp.tell() < self.max_bytes:
            return
        self._fp.close()
        for i in range(self.backups, 0, -1):
            dest = self.path.with_name(self.path.name + f".{i}")
            src = self.path if i == 1 else self.path.with_name(self.path.name + f".{i-1}")
            if src.exists():
                os.replace(src, dest)
        self._fp = self.path.open("a", encoding="utf-8", buffering=1)

    def _run(self) -> None:
        while not self._stop.is_set() or not self._q.empty():
            try:
                row = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                line = json.dumps(row, ensure_ascii=False, allow_nan=False)
                with self._io:
                    if self._fp:
                        self._rotate()
                        self._fp.write(line + "\n")
                if self.console and sys.stderr:
                    try:
                        print(f"[{row['elapsed_s']:8.3f}] {row['level']:7} {row['category']:6} "
                              f"{row['event']}: {row['message']} {json.dumps(row['details'], ensure_ascii=False)}",
                              file=sys.stderr, flush=True)
                    except (OSError, UnicodeError):
                        pass
            except Exception as exc:
                self.write_error = str(clean(f"{type(exc).__name__}: {exc}"))
            finally:
                self._q.task_done()

    def flush(self, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while self._q.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        with self._io:
            if self._fp and not self._fp.closed:
                try:
                    self._fp.flush()
                except OSError as exc:
                    self.write_error = str(clean(exc))
        return self._q.unfinished_tasks == 0

    def export(self, destination: Path) -> Path:
        """Call from a background thread; snapshot is consistent, filtered on input."""
        self.event("APP", "report_export", "진단 ZIP 생성")
        drained = self.flush()
        summary = self.summary()
        summary["export_queue_drained"] = drained
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A private temp filename also avoids collisions between independent exports.
        fd, name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            with tempfile.TemporaryDirectory(prefix="DeskPet-export-") as staging:
                copies = []
                with self._io:
                    for p in sorted(self.directory.glob("deskpet.log*")):
                        if p.is_file():
                            target = Path(staging) / p.name
                            with p.open("rb") as src, target.open("wb") as dst:
                                shutil.copyfileobj(src, dst, length=64 * 1024)
                            copies.append(target)
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as out:
                    out.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
                    out.writestr("summary.txt", readable_summary(summary))
                    for p in copies:
                        out.write(p, p.name)
                    with out.open("recent_events.jsonl", "w") as recent:
                        for row in self.records():
                            recent.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
                    out.writestr("READ_ME.txt", "DeskPet diagnostic report. No automatic upload.\n"
                                  "Logs are redacted best-effort. Review before sharing.\n"
                                  "No configuration, terminal commands, process command lines or model weights.\n"
                                  "RSS totals can double-count shared pages; private bytes are not RSS.\n")
            os.replace(temporary, destination)
            return destination
        finally:
            temporary.unlink(missing_ok=True)

    def close(self) -> None:
        if self._closed:
            return
        self.event("APP", "session_end", "종료")
        self._closed = True
        atexit.unregister(self.close)
        self._stop.set()
        self._writer.join(timeout=3.0)
        with self._io:
            if self._fp and not self._fp.closed:
                self._fp.close()


def readable_summary(summary: dict) -> str:
    temp = summary.get("temperature", {})
    npu = summary.get("npu_counter", {})
    state = summary.get("state", {})
    voice = summary.get("voice", {})
    sensors = summary.get("sensors", {})
    servers = summary.get("servers", {})
    logger = summary.get("logger", {})
    device = summary.get("device", {})
    battery = summary.get("battery", {})
    def show(value, unit=""):
        return "알 수 없음" if value is None or value == "" else f"{value}{unit}"
    server_lines = []   # servers moved to ServerCat; the section only appears for old reports
    if servers:
        server_lines.append("[서버]")
        for name in sorted(servers):
            item = servers.get(name) or {}
            ping = item.get("ping_ok")
            ping_text = "생략" if ping is None else ("OK" if ping else "FAIL")
            server_lines.append(
                f"{name}: {item.get('status', 'UNKNOWN')} / SSH TCP {'OK' if item.get('tcp_ok') else 'FAIL'} "
                f"/ ping {ping_text} / 연속 실패 {item.get('fail_streak', 0)}"
            )
        server_lines.append("")

    resource_lines = ["[프로세스별 메모리]"]
    for item in summary.get("resources", {}).get("processes", []):
        rss = item.get("rss_bytes", 0) / (1024**2)
        private = item.get("private_bytes")
        private_text = "-" if private is None else f"{private/(1024**2):.1f} MiB"
        resource_lines.append(f"{item.get('role', '-')} / {item.get('name', '-')} "
                              f"PID {item.get('pid', '-')} / RSS {rss:.1f} MiB / private {private_text}")
    resource_lines += ["RSS 합계는 공유 페이지가 중복될 수 있어. private는 상주 RAM이 아닌 전용 커밋량이야.", ""]
    return "\n".join([
        f"DeskPet Classic {summary.get('version', VERSION)} · 통합 진단",
        f"실행 세션: {summary.get('session_id', '-')}",
        f"이번 실행 시간: {show(summary.get('session_elapsed_s'), '초')}", "",
        "[온도]",
        f"표시 값: {show(temp.get('value_c'), '°C')}  /  신뢰도: {temp.get('confidence', 'NONE')}",
        f"선택한 센서: {temp.get('source') or '없음'} · {temp.get('sensor') or '-'}",
        f"원본 THRM: {show(temp.get('raw_thermal_c'), '°C')}  /  측정 경과: {show(temp.get('age_s'), '초')}",
        f"교정: {temp.get('calibration') or '-'}",
        f"선택 상세: {temp.get('selection_details') or '-'}", "",
        "[NPU 사용률 — Windows 센서]",
        f"장치 감지: {show(npu.get('present'))}  /  카운터 유효: {show(npu.get('counter_valid'))}",
        f"사용률: {show(npu.get('utilization_percent'), '%')}  /  출처: {npu.get('source') or '-'}", "",
        "[기기 · 배터리]",
        f"기기: {device.get('name') or '-'}  /  출처: {device.get('source') or '-'}",
        f"배터리: {show(battery.get('percent'), '%')}  /  전력 흐름: {show(battery.get('flow_w'), ' W')}",
        f"이번 세션: 충전 +{battery.get('charged_session_wh', 0.0):.3f} Wh / 방전 -{battery.get('discharged_session_wh', 0.0):.3f} Wh", "",
        "[Classic 대사 엔진]",
        "실행 방식: 규칙 + 최근 사건 기억 · 로컬/원격 언어 모델 없음",
        f"대사 수: {voice.get('catalog_messages', '-')} / 말수: {voice.get('level', '-')}",
        f"대사집 오류: {voice.get('catalog_errors') or '없음'}", "",
        "[규칙 판단 / 대사]",
        f"작업: {state.get('load', '-')} / 온도: {state.get('thermal', '-')} / 표정: {state.get('face', '-')} / 전원: {state.get('power', '-')}",
        f"온도 판단 입력: 평균 {show(state.get('temperature_average_c'), '°C')} / 추세 {show(state.get('temperature_trend_c_per_s'), '°C/s')} / 신뢰도 {state.get('temperature_confidence', '-')}",
        f"대사: {voice.get('rule_line') or '-'}",
        f"센서 연결: {show(sensors.get('probe_ok'))} / {sensors.get('probe_error') or '별도 오류 없음'}", "",
        *server_lines,
        *resource_lines,
        "[기록 상태]",
        f"통합 로그: {logger.get('path', '-')}",
        f"쓰기 오류: {logger.get('write_error') or '없음'} / 유실 기록 수: {logger.get('dropped_records', 0)}",
        f"상세 기록: {'켜짐 (자동 종료)' if logger.get('detail_recording') else '꺼짐'}",
        f"자동 정리: {logger.get('retention', {}).get('keep_days', 7)}일 · 최근 {logger.get('retention', {}).get('min_sessions', 20)}세션 보존 · 최대 {logger.get('retention', {}).get('max_total_bytes', 0)/(1024**2):.0f} MiB",
        f"최근 정리: {logger.get('cleanup', {}).get('removed_sessions', 0)}세션 삭제 · {logger.get('cleanup', {}).get('freed_bytes', 0)/(1024**2):.1f} MiB 확보", "",
        "진단 ZIP에는 요약, 시간순 통합 로그, 최근 사건이 들어가. 자동 업로드는 하지 않아.",
        "식별 정보는 가능한 범위에서 가리지만, 공유 전에 파일 내용을 확인해줘.",
    ])


_global: Diagnostics | None = None
_global_lock = threading.Lock()


def get_diagnostics(*, console: bool = False) -> Diagnostics:
    global _global
    with _global_lock:
        if _global is None or _global._closed:
            _global = Diagnostics(console=console)
        elif console:
            _global.console = True
        return _global


def install_exception_hooks(diag: Diagnostics) -> None:
    def main_hook(typ, exc, tb):
        diag.exception("APP", "uncaught_exception", exc.with_traceback(tb))
        diag.flush()
    def thread_hook(args):
        diag.exception("APP", "thread_exception", args.exc_value,
                       thread=args.thread.name if args.thread else "unknown")
    sys.excepthook = main_hook
    threading.excepthook = thread_hook
