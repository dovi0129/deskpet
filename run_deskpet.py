"""One launch path for normal and debug mode; bootstrap errors use unified log."""
from __future__ import annotations
import argparse
import ctypes
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--bootstrap", action="store_true", help="install missing psutil only")
    parser.add_argument("--windowed", action="store_true")
    args = parser.parse_args()
    if args.windowed and sys.platform == "win32" and Path(sys.executable).name.lower() == "python.exe":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.is_file():
            child_args = [str(pythonw), str(Path(__file__).resolve()), *[x for x in sys.argv[1:] if x != "--windowed"]]
            try:
                subprocess.Popen(child_args, cwd=str(Path(__file__).resolve().parent))
                return 0
            except OSError as exc:
                from diagnostics import get_diagnostics
                diag = get_diagnostics(console=True)
                diag.exception("APP", "windowed_launch_failed", exc)
                diag.flush()
                ctypes.windll.user32.MessageBoxW(None, f"DeskPet 실행 실패\n{exc}\n\n통합 로그: {diag.path}", "DeskPet", 0x10)
                diag.close()
                return 1
    from diagnostics import get_diagnostics, install_exception_hooks
    diag = get_diagnostics(console=args.debug)
    install_exception_hooks(diag)
    try:
        try:
            import psutil
        except ImportError:
            if not args.bootstrap:
                raise
            diag.event("APP", "dependency_install", "기본 의존성 psutil 설치")
            interpreter = Path(sys.executable)
            if interpreter.name.lower() == "pythonw.exe":
                interpreter = interpreter.with_name("python.exe")
            result = subprocess.run([str(interpreter), "-m", "pip", "install", "psutil>=5.9"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                        encoding="utf-8", errors="replace", timeout=180,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0)
            diag.event("APP", "dependency_install_result", result.stdout, exit_code=result.returncode)
            if result.returncode:
                raise RuntimeError("psutil 설치 실패. Python 환경을 확인해줘")
        from deskpet import DeskPet
        app = DeskPet()
        if args.debug or args.diagnostics:
            app.root.after(250, app.show_diagnostics)
        app.run()
        return 0
    except BaseException as exc:
        diag.exception("APP", "startup_or_mainloop_error", exc)
        diag.flush()
        text = f"DeskPet을 실행하지 못했어: {type(exc).__name__}: {exc}\n\n통합 로그:\n{diag.path}"
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, text, "DeskPet 실행 오류", 0x10)
        elif sys.stderr:
            print(text, file=sys.stderr)
        return 1
    finally:
        diag.close()


if __name__ == "__main__":
    raise SystemExit(main())
