"""Read-only live diagnostic viewer. Opening it never re-samples sensors."""
from __future__ import annotations
import json
from collections import deque
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from diagnostics import Diagnostics, readable_summary


class DiagnosticWindow:
    def __init__(self, root: tk.Tk, diag: Diagnostics):
        self.diag, self.results = diag, queue.Queue()
        self.window = tk.Toplevel(root)
        self.window.title("DeskPet · 통합 진단")
        scale = max(.75, min(3.0, float(root.tk.call("tk", "scaling"))/(96/72)))
        width = min(round(880*scale), max(600, root.winfo_screenwidth()-80))
        height = min(round(620*scale), max(420, root.winfo_screenheight()-80))
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(width, round(600*scale)), min(height, round(420*scale)))
        self.closed, self.busy = False, False
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        toolbar = ttk.Frame(self.window, padding=8)
        toolbar.pack(fill="x")
        self.category = tk.StringVar(value="ALL")
        self.level = tk.StringVar(value="INFO")
        self.paused = tk.BooleanVar(value=False)
        ttk.Label(toolbar, text="분야").pack(side="left")
        ttk.Combobox(toolbar, state="readonly", width=9, textvariable=self.category,
                     values=("ALL", "APP", "SENSOR", "TEMP", "SERVER", "NPU", "VOICE", "UI")).pack(side="left", padx=4)
        ttk.Combobox(toolbar, state="readonly", width=9, textvariable=self.level,
                     values=("DEBUG", "INFO", "WARNING", "ERROR")).pack(side="left", padx=4)
        ttk.Checkbutton(toolbar, text="화면 일시정지", variable=self.paused).pack(side="left")
        ttk.Button(toolbar, text="상세 기록 2분", command=lambda: diag.enable_detail()).pack(side="right")

        tabs = ttk.Notebook(self.window)
        tabs.pack(fill="both", expand=True, padx=8)
        self.summary = ScrolledText(tabs, wrap="word", font=("TkDefaultFont", 10), state="disabled")
        self.timeline = ScrolledText(tabs, wrap="word", font=("TkFixedFont", 9), state="disabled")
        tabs.add(self.summary, text="현재 상태 요약")
        tabs.add(self.timeline, text="시간순 로그")
        bottom = ttk.Frame(self.window, padding=8)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="요약 복사", command=self.copy).pack(side="left")
        ttk.Button(bottom, text="로그 폴더", command=self.open_log_folder).pack(side="left", padx=(8, 0))
        ttk.Button(bottom, text="지금 정리", command=self.cleanup_logs).pack(side="left", padx=(8, 0))
        self.export_button = ttk.Button(bottom, text="진단 ZIP 저장", command=self.export)
        self.export_button.pack(side="left", padx=8)
        self.status = ttk.Label(bottom, text="자동 업로드 없음 · 공유 전 내용 확인")
        self.status.pack(side="left")
        self._last_signature = None
        self._timeline_rows = deque()
        self._last_summary_text = None
        self._tick()

    @staticmethod
    def replace(widget, text, *, follow_tail=False):
        position = widget.yview()
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")
        if follow_tail and position[1] >= .99:
            widget.yview_moveto(1.0)
        else:
            widget.yview_moveto(position[0])



    def _tick(self):
        if self.closed:
            return
        if not self.paused.get():
            current_summary = self.diag.summary()
            text = readable_summary(current_summary)
            if text != self._last_summary_text:
                self.replace(self.summary, text)
                self._last_summary_text = text
            rows = self.diag.records(self.category.get(), self.level.get())[-500:]
            self._update_timeline(rows, (self.category.get(), self.level.get()))
        try:
            result, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.export_button.configure(state="normal")
            if error:
                messagebox.showerror("진단 저장 실패", error, parent=self.window)
            else:
                self.status.configure(text=f"저장됨: {result.name}")
                messagebox.showinfo("진단 ZIP 저장", f"{result}\n\n공유 전에 내용을 확인해줘.", parent=self.window)
        self._after = self.window.after(1000, self._tick)

    def _update_timeline(self, rows, signature):
        # Max 500 records and bounded widget text. Filter changes rebuild; ordinary
        # ticks append new records only. GUI never re-reads log files or sensors.
        widget = self.timeline
        position = widget.yview()
        rebuild = signature != self._last_signature
        self._last_signature = signature
        last_seq = self._timeline_rows[-1][0] if self._timeline_rows else -1
        new_rows = rows if rebuild else [r for r in rows if r["seq"] > last_seq]
        if not rebuild and not new_rows:
            return
        widget.configure(state="normal")
        if rebuild:
            widget.delete("1.0", "end")
            self._timeline_rows.clear()
        for r in new_rows:
            text = (f"{r['ts']}  +{r['elapsed_s']:.3f}s  #{r['seq']}  {r['level']} [{r['category']}] "
                    f"{r['event']}\n  {r['message']}\n  {json.dumps(r['details'], ensure_ascii=False)}\n\n")
            # Diagnostics already bounds its ring; also bound pathological text in
            # the UI. Full retained evidence remains available in exported logs.
            if len(text) > 16384:
                text = text[:16384] + "\n[화면 생략 · 전체 기록은 진단 ZIP]\n"
            widget.insert("end", text)
            self._timeline_rows.append((r["seq"], text.count("\n")))
        removed_lines = 0
        while len(self._timeline_rows) > 500:
            removed_lines += self._timeline_rows.popleft()[1]
        if removed_lines:
            widget.delete("1.0", f"{removed_lines + 1}.0")
        widget.configure(state="disabled")
        if position[1] >= .99:
            widget.yview_moveto(1.0)

    def copy(self):
        self.window.clipboard_clear()
        self.window.clipboard_append(readable_summary(self.diag.summary()))

    def open_log_folder(self):
        path = self.diag.log_root
        try:
            if os.name == "nt":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("로그 폴더", str(exc), parent=self.window)

    def cleanup_logs(self):
        try:
            result = self.diag.cleanup_history(reason="manual")
            self.status.configure(text=(f"로그 정리: {result['removed_sessions']}세션 · "
                                        f"{result['freed_bytes']/(1024**2):.1f} MiB 확보"))
        except Exception as exc:
            self.diag.exception("APP", "manual_log_cleanup_failed", exc)
            messagebox.showerror("로그 정리 실패", str(exc), parent=self.window)

    def export(self):
        if self.busy:
            return
        path = filedialog.asksaveasfilename(parent=self.window, defaultextension=".zip",
              initialfile=f"DeskPet_diagnostics_{self.diag.session_id}.zip",
              filetypes=[("ZIP report", "*.zip")])
        if not path:
            return
        self.busy = True
        self.export_button.configure(state="disabled")
        self.status.configure(text="진단 자료 저장 중")
        def run():
            try:
                self.results.put((self.diag.export(Path(path)), ""))
            except Exception as exc:
                self.diag.exception("APP", "report_export_failed", exc)
                self.results.put((None, str(exc)))
        threading.Thread(target=run, name="DeskPetReportExport", daemon=True).start()

    def close(self):
        self.closed = True
        if hasattr(self, "_after"):
            self.window.after_cancel(self._after)
        self.window.destroy()
