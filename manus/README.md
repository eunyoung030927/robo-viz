# manus — Manus 장갑 브리지 (호스트에서 읽고, 컨테이너·뷰어는 받기만)

```
[Manus 장갑] ─BT─ [동글] ─USB─ 호스트: bridge.py (Manus SDK Integrated, 동글 점유)
                                   │ TCP 127.0.0.1:47000, 줄 단위 JSON, ~100 Hz
                 ┌─────────────────┼──────────────────┐
         retarget_viewer        컨테이너(--network host)    그 밖의 수신기
         (자동으로 브리지 사용)   client.py 로 수신
```

동글은 한 프로그램만 쓸 수 있다 → SDK 는 브리지 하나만 띄우고 나머지는 받기만 한다.
받는 쪽(`client.py`)은 표준 라이브러리 + numpy 뿐 — 컨테이너에 Manus SDK·USB 권한이 필요 없다.

## 호스트: 브리지 켜기
사전 준비(서버당 한 번): Manus SDK(Linux) 다운로드 → `install-dependencies.sh`(udev) → `cffi`.
순서는 노션/대화의 "Manus SDK (Linux) 세팅" 참고. SDK 경로 기본값 `~/workspace/manus_sdk/Python`
(다르면 `MANUS_SDK_PY` 환경변수).

```bash
cd ~/workspace/robo-viz/manus
python bridge.py              # 127.0.0.1:47000
python bridge.py --fake       # 장갑 없이 합성 손으로 배관 점검
python bridge.py --bind 0.0.0.0   # 다른 PC 에서도 받기(인증 없음 — 신뢰망에서만)
```
5초마다 `[bridge] seq=… gloves=[(id, side)] clients=N sdk=ok` 로그. 다른 Manus 프로그램(예제·대시보드)은 끌 것.

## 컨테이너(또는 다른 프로세스): 받기
`client.py` 한 파일만 있으면 된다(복사해도 됨).
```python
from client import ManusClient
mc = ManusClient("127.0.0.1", 47000).start()
r = mc.latest(prefer="right")   # (kp (25,3) m, ergo {이름: °} | None, side, glove id) | None
```
확인용: `python client.py` → 초당 한 줄(`glove 0x… right · 99 msg/s · …`).

## 데이터
- `kp`: WebXR 25관절 순서 (0 손목, 엄지 1-4 Metacarpal·Proximal·Distal·Tip, 검지 5-9 … 새끼 20-24
  Metacarpal·Proximal·Intermediate·Distal·Tip), m. Manus raw skeleton 노드 순서가 이와 같다(실측).
  손목 위치는 0(장갑엔 위치 추적이 없다) — 팔 추종은 별도 추적 필요.
- `ergo`: 손 하나 20개 관절각(°), 이름은 Tesollo `manus_retarget.py` 가 쓰던 것
  (`ThumbMCPSpread`=SDK ThumbCMCSpread, `ThumbMCPStretch`=CMCStretch, `ThumbPIPStretch`=MCPStretch,
  `ThumbDIPStretch`=IPStretch, `<Finger>Spread/MCPStretch/PIPStretch/DIPStretch`).
- ⚠ 장갑 캘리브레이션 전엔 손을 펴도 MCPStretch 가 −40° 근처 — `sdk_client.py` → `C` 로 먼저 캘리브레이션.

## 텔레옵 코드에서 (권장 진입점)
```python
import sys; sys.path.insert(0, "/workspace/manus")
from source import open_manus
manus = open_manus()                 # 기본: SDK 직접 / MANUS_BRIDGE_HOST 있으면 브리지 수신
r = manus.latest(prefer="right")     # (kp (25,3), ergo, side, glove id) | None
```
동글 위치가 바뀌어도(이 PC ↔ 다른 PC 의 브리지) 코드는 그대로, 환경변수만 바꾼다.

## 파일
- `sdk_reader.py` — Manus SDK Integrated 읽기(읽기 전용; 진동·페어링·캘리브레이션 안 함)
- `bridge.py` — SDK → TCP 송출 (`--fake` 지원)
- `client.py` — 수신기 (numpy 만)
- `source.py` — `open_manus()`: 위 둘 중 하나를 골라 같은 `latest()` 로 돌려준다
- `check.py` — 장갑 연결 확인(~12초)
- `mcal.py` — 캘리브레이션(.mcal) 이름 붙여 저장·불러오기·목록 (`python mcal.py list|save|load --side right --name <이름>`)
