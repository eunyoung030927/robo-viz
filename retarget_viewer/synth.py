"""합성 사람 손 키포인트 (25,3) — 헤드셋 없이 리타겟팅을 보는 입력.

WebXR 25관절 규약(robo-synapse source.py): 손목 0, 엄지 1-4(metacarpal·proximal·distal·tip),
검지 5-9 / 중지 10-14 / 약지 15-19 / 새끼 20-24 (metacarpal·proximal·intermediate·distal·tip).
좌표는 robo-synapse tests/common/teleop/fixtures.hand 와 같은 틀: 손가락 +y, 손바닥 평면 xy,
굽힘은 +z(손바닥 쪽 — 오른손: 검지 +x, 손등 법선 −z; robo-synapse
test_dexpilot_retarget.human 과 같은 규약. ⚠ fixtures.hand 는 −z 로 말려 거울상이다). 뼈 길이는 성인 손 대략값(새끼가 짧다 — bone_scale 이 보는 차이).
"""

from __future__ import annotations

import numpy as np

FINGERS = ("index", "middle", "ring", "little")
_BASE = {"index": 5, "middle": 10, "ring": 15, "little": 20}
_X = {"index": 0.024, "middle": 0.002, "ring": -0.018, "little": -0.036}
_META = {"index": 0.068, "middle": 0.066, "ring": 0.060, "little": 0.054}       # 손목→너클 y
_BONES = {"index": (0.040, 0.024, 0.020), "middle": (0.044, 0.028, 0.021),
          "ring": (0.041, 0.026, 0.020), "little": (0.032, 0.019, 0.018)}


def hand(curl=None, spread=None, thumb_lift_deg=20.0, thumb_curl_deg=10.0, thumb_swing_deg=0.0):
    """`curl` {finger: deg}(세 관절에 같은 값), `spread` {finger: deg}(+ = 엄지쪽)."""
    curl = curl or {}
    spread = spread or {}
    kp = np.zeros((25, 3))
    for f in FINGERS:
        b, x = _BASE[f], _X[f]
        kp[b] = (x * 0.6, 0.012, 0.0)                         # metacarpal 기저
        d = np.radians(spread.get(f, 0.0))
        p = np.array([x, _META[f], 0.0])                       # 너클(proximal 시작)
        kp[b + 1] = p
        heading = np.array([np.sin(d), np.cos(d), 0.0])
        c = np.radians(curl.get(f, 0.0))
        for k, L in enumerate(_BONES[f]):
            a = c * (k + 1)
            step = L * (np.cos(a) * heading + np.array([0.0, 0.0, np.sin(a)]))
            kp[b + 2 + k] = kp[b + 1 + k] + step
    # 엄지: 손바닥 옆(+x)에서 시작, lift = 손바닥 앞(+z, 손바닥 쪽)으로, swing = 검지쪽 회전
    t, s, cc = np.radians(thumb_lift_deg), np.radians(thumb_swing_deg), np.radians(thumb_curl_deg)
    kp[1] = (0.030, 0.010, 0.005)
    dirv = np.array([np.cos(s) * 0.55, 0.83, 0.0])
    dirv = dirv / np.linalg.norm(dirv)
    dirv = np.cos(t) * dirv + np.sin(t) * np.array([0.0, 0.0, 1.0])
    kp[2] = kp[1] + 0.040 * dirv
    bend = np.array([-np.sin(cc), 0.0, np.sin(cc) * 0.5])
    kp[3] = kp[2] + 0.032 * (dirv * np.cos(cc) + bend)
    kp[4] = kp[3] + 0.028 * (dirv * np.cos(2 * cc) + 2 * bend)
    return kp


def pinch(finger: str = "index", gap_m: float = 0.0):
    """엄지 끝을 `finger` 끝에 붙인 자세(그 손가락은 적당히 굽힘)."""
    kp = hand(curl={finger: 30.0}, thumb_lift_deg=45.0)
    tip = kp[_BASE[finger] + 4] + np.array([0.0, 0.0, gap_m])
    kp[4] = tip
    kp[3] = kp[2] + 0.55 * (tip - kp[2]) + np.array([0.0, 0.0, 0.008])
    return kp


PRESETS = {
    "open": lambda: hand(),
    "fist": lambda: hand(curl={f: 70.0 for f in FINGERS}, thumb_lift_deg=40.0, thumb_curl_deg=35.0),
    "half": lambda: hand(curl={f: 35.0 for f in FINGERS}, thumb_lift_deg=30.0, thumb_curl_deg=20.0),
    "spread": lambda: hand(spread={"index": 12.0, "middle": 3.0, "ring": -6.0, "little": -15.0}),
    "pinch_index": lambda: pinch("index"),
    "pinch_middle": lambda: pinch("middle"),
    "pinch_ring": lambda: pinch("ring"),
    "pinch_little": lambda: pinch("little"),
}
