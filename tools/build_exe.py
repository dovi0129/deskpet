"""Build a Python-free Windows copy for sharing: DeskPet\\DeskPet.exe, zipped.

Run with a Python that has psutil and PyInstaller (e.g. C:\\venvs\\deskpet-build):
    python tools\\build_exe.py --out ..\\..\\DeskPet_classic3_windows.zip

The shared copy starts from clean defaults (no birthday). The user's own config.json is
never copied. DeskPet no longer watches lab servers (that is ZenPet\\ServerCat), so no
addresses can leak into the shared copy.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

APP = Path(__file__).resolve().parents[1]
NOT_SHIPPED = {"gui_smoke.py", "self_test.py"}
DATA_FILES = ("voice_catalog.json", "deskpet_probe.ps1")
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

README = """DeskPet Classic 2.4.0-rc9-classic3 (Windows)

실행
- 압축을 푼 뒤 DeskPet 폴더 안의 DeskPet.exe를 더블클릭합니다.
- 처음 실행할 때 "Windows의 PC 보호" 창이 뜨면 [추가 정보] → [실행]을 누릅니다.
  (서명하지 않은 개인 프로그램이라 뜨는 창입니다.)
- _internal 폴더는 지우거나 옮기지 마세요. DeskPet.exe와 같이 있어야 합니다.
- 바탕화면·문서처럼 쓰기 가능한 곳에 풀어 주세요(Program Files 안은 설정 저장이 막힐 수 있음).

사용
- 고양이를 우클릭하면 메뉴(상세정보, 고양이 크기, 산책, 말수 등)가 나옵니다.
- 종료: 우클릭 메뉴의 "DeskPet 종료" 또는 고양이를 선택하고 Esc.

설정 (DeskPet.exe 옆 config.json, 한 번 실행·종료하면 생김)
- 생일: "birthday": "03-15" 처럼 월-일만 넣으면 그날 축하해 줍니다.
- 설정을 고칠 때는 DeskPet을 끈 상태에서 고치세요.

기록 위치
- 친밀도·하루 결산·진단 로그: %LOCALAPPDATA%\\DeskPetClassic
- 지우려면 DeskPet 폴더와 위 폴더를 삭제하면 됩니다. 외부로 보내는 데이터는 없습니다.
"""


def stage(work: Path) -> Path:
    src = work / "src"
    src.mkdir(parents=True)
    for p in APP.glob("*.py"):
        if p.name not in NOT_SHIPPED:
            shutil.copy2(p, src / p.name)
    for name in DATA_FILES:
        shutil.copy2(APP / name, src / name)
    for p in src.glob("*.py"):  # no server address may reach a shared copy
        assert not IPV4.search(p.read_text(encoding="utf-8")), p.name
    return src


def build(src: Path, work: Path) -> Path:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "DeskPet",
           "--distpath", str(work / "dist"), "--workpath", str(work / "build"), "--specpath", str(work),
           *[a for name in DATA_FILES for a in ("--add-data", f"{src / name};.")],
           str(src / "run_deskpet.py")]
    subprocess.run(cmd, check=True, cwd=src)
    dist = work / "dist" / "DeskPet"
    assert (dist / "DeskPet.exe").is_file()
    assert not (dist / "config.json").exists()
    (dist / "읽어주세요.txt").write_text(README, encoding="utf-8-sig", newline="\r\n")
    return dist


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True, help="zip to write")
    ap.add_argument("--work", type=Path, help="build folder (default: a new temp folder)")
    args = ap.parse_args()
    work = args.work or Path(tempfile.mkdtemp(prefix="deskpet-exe-"))
    if work.exists() and any(work.iterdir()):
        raise SystemExit(f"work folder is not empty: {work}")
    dist = build(stage(work), work)
    out = args.out.resolve()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(dist.rglob("*")):
            if p.is_file():
                z.write(p, Path("DeskPet") / p.relative_to(dist))
    print("dist:", dist)
    print("zip:", out, out.stat().st_size, "bytes")
    print("sha256:", hashlib.sha256(out.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
