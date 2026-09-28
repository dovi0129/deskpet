# DeskPet

Windows 바탕화면에 사는 작은 아스키 고양이입니다. 노트북 상태(CPU·RAM·GPU·NPU 사용률, 온도, 배터리)를
보고 표정과 말이 바뀝니다.

```
   /\_/\
  ( ^ω^ )
   > ^ <
```

## 받기 (Python 필요 없음)

1. [Releases](https://github.com/dovi0129/deskpet/releases/latest)에서 `DeskPet_classic3_windows.zip`을 받습니다.
2. 압축을 풀고 `DeskPet` 폴더 안의 `DeskPet.exe`를 실행합니다.
3. "Windows의 PC 보호" 창이 뜨면 [추가 정보] → [실행]을 누릅니다. 서명하지 않은 개인 프로그램이라 뜨는 창입니다.

`_internal` 폴더는 `DeskPet.exe`와 같이 두어야 합니다. 바탕화면·문서처럼 쓰기 가능한 곳에 풀어 주세요.

## 하는 일

- 상태에 따라 표정이 바뀝니다. 뜨거우면 헐떡이고, 배터리가 없으면 배고파하고, 한가하면 졸립니다.
- 더블클릭하면 카드가 펼쳐져 CPU/RAM/GPU/NPU 막대, 온도, 밥(배터리), 기분, 작업을 보여 줍니다.
  GPU가 두 개 이상이면(내장 + 외장 등) GPU마다 한 줄씩(`iGPU`·`dGPU` 또는 `GPU0`·`GPU1`) 보여 줍니다.
- 우클릭 메뉴: 고양이 크기(작은/큰), 산책, 말수, 투명 배경, 항상 위, 친밀도·결산 보기 등.
- 큰 고양이는 주로 옆으로 누워 있고, 가끔 식빵을 굽거나 앉습니다. 산책을 켜면 한가할 때 화면을 돌아다닙니다.
- 끌어 옮기면 목덜미를 잡힌 채 축 늘어집니다. 쓰다듬으면 친밀도가 오릅니다.
- 유튜브 영상을 틀어 두면 같이 봅니다. 창 제목에 " - YouTube"가 있는지만 보고, 제목은 저장하지 않습니다.
- 생일(설정 `birthday`, `"MM-DD"`), 새해, 크리스마스, 금요일 저녁 같은 날짜 대사가 있습니다.

## 개인정보

- 외부로 보내는 데이터가 없습니다. 소스로 실행할 때 psutil이 없으면 `start_deskpet.bat`가 pip로 설치를
  시도하는 것 말고는 네트워크를 쓰지 않습니다(exe판은 설치 단계 없음).
- 친밀도·하루 결산·진단 로그는 `%LOCALAPPDATA%\DeskPetClassic`에만 저장됩니다.
- 진단 ZIP은 사용자가 메뉴에서 만들 때만 생기며, 사용자 폴더 이름·토큰·IP를 가립니다.

## 소스로 실행

Python 3.10 이상, tkinter, psutil이 필요합니다.

```
pip install -r requirements.txt
python run_deskpet.py --windowed
```

또는 `start_deskpet.bat`를 실행합니다. 오류가 나면 `start_deskpet_debug.bat`로 원인을 볼 수 있습니다.

- 검사: `python -m unittest discover -s tests -v`
- 대사 원본: `tools/voice_lines.py` → `python tools/build_voice_catalog.py`로 `voice_catalog.json` 생성
- exe 만들기: psutil·PyInstaller가 있는 Python으로 `python tools/build_exe.py --out DeskPet_classic3_windows.zip`
- 진단 사용법: `DIAGNOSTICS.md`, 변경 기록: `CHANGELOG.md`
