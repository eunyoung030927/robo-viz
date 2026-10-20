# retarget_viewer — 다지 손 리타겟팅 웹 검증기

사람 손(합성 / Quest 실시간 / 녹화) → robo-synapse `DexPilotRetarget` → 손 URDF 를 웹에 띄운다.
**로봇에는 아무것도 보내지 않는다** — 실기 구동 전에 리타겟팅이 내 손에 맞는지 보는 용도.

## 실행 (ro1)
```bash
cd ~/workspace/robo-viz/retarget_viewer
bash run.sh
```
- 브라우저: `http://127.0.0.1:8030/` — 로컬 PC 에서는 VS Code PORTS 탭에서 8030 포워딩, 또는
  `ssh -p 222 -L 8030:127.0.0.1:8030 ro1@<ro1 공인IP>`.
- 종료: Ctrl-C (백그라운드면 `kill <pid>` — `pkill` 금지).

## 입력
| 버튼 | 내용 |
|---|---|
| 합성 프리셋 | open / fist / half / spread / pinch_{index,middle,ring,little} (`synth.py`) |
| 합성 슬라이더 | 손가락별 굽힘·벌림, 엄지 들기·굽힘·스윙 |
| Quest 실시간 | robo-synapse `WebXRHandSource` 를 **8013** 에 따로 띄운다(webctl 8012 와 무관). 패널에 뜬 `https://ro1-…ts.net:8013/?k=…` 를 헤드셋에서 열고 Pass-through. 오른손만 사용. 토큰은 서버 재시작마다 바뀜 |
| 녹화 재생 | Quest 입력 중 `● 녹화` → `recordings/YYMMDD_HHMMSS.npz` (t, kp[N,25,3]) 를 반복 재생 |

## 화면
- 회색 뼈대 = 사람 손(손 프레임으로 옮긴 것), 색 큰 점 = 리타겟 **목표**(뼈길이 정규화 후),
  흰 점 = 로봇 손끝(FK), 색 선 = 목표–로봇 오차, 노란 선 = 접촉 투영된 손끝 쌍.
- 관절 표: URDF°, 최적화 범위 안 위치 막대, 한계에 붙은 관절은 주황.
- 노브 = `profile.teleop.dex.dexpilot` 와 같은 이름(scale, project/escape_dist, One-Euro filter, bone_scale).
- 관절 고정 = 그 관절의 최적화 범위를 한 점으로(사람 손에 1:1 대응이 없는 관절 실험용).

## 손
| 키 | DOF 공간 | 비고 |
|---|---|---|
| `dg5f` | **URDF 관절 그대로, URDF 한계** | 와이어°↔URDF rad 실측표가 아직 없음. URDF 한계 ≠ 실물(thumb1 상한 51 vs 77, little2 −24..35 vs −15..90 등) → 결과는 "URDF 한계 기준" |
| `rh5dg2` | 실기 매핑(드라이버 와이어 한계 + `model.WIRE2URDF`) | 실기에서 쓰는 경로와 동일 — 비교 기준 |

링크 설정(base/tip/knuckle)은 `hands.py`. 이 값과 노브가 확정되면 robo-synapse
`robots/ur7/profile.py` 의 `grippers.dg5f.teleop.dex.dexpilot` 로 옮긴다.

## 파일
- `server.py` aiohttp(HTTP + WebSocket 30 Hz), `hands.py` 손 설정·리타겟 생성, `synth.py` 합성 손,
  `smoke.py` 헤드리스 점검(`PYTHONPATH=~/workspace/robo-synapse python smoke.py`),
  `static/` 뷰어(three r160 + urdf-loader — work1 `hand_viewer_dexverse` vendor 복사).
- 알고리즘은 robo-synapse 를 import(복사 금지). robo-synapse 는 읽기만 한다.
