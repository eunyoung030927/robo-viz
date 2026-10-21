"""Tesollo 공식 Manus 장갑 리타겟 공식을 Quest 손 키포인트에 적용 (비교용).

출처: tesollodelto/tesollo_ros2 (7379063) `dg5f_ros2/dg5f_driver/script/manus_retarget/
manus_retarget.py` `_compute_mqd_from_q` — Manus 장갑 "ergonomics" 관절각(°) → DG-5F 20관절 rad.
DexPilot 같은 손끝 최적화 없이 **관절각을 직접 대응**시키고 관절별 배율·오프셋, 뒤로 젖힘 금지,
URDF 한계 클램프만 한다.

장갑 대신 Quest 를 쓰므로 Manus ergonomics 값을 25관절 키포인트에서 기하로 잰다(`human_ergo`).
⚠ 근사: Manus 의 정확한 정의(특히 엄지 CMC "MCPSpread/MCPStretch" 의 기준)는 공개돼 있지
않다. 손가락 굽힘(MCP/PIP/DIP)·벌림은 정의가 분명해 그대로 맞고, 엄지 두 값은 DG-5F 관절 축에서
거꾸로 정한다(2026-10-20, 첫 판은 이름만 보고 정해 손을 펴도 엄지가 손바닥 앞으로 접혔다):
  thumb1 축 = 손바닥 법선 → 손바닥 평면 안 벌림.   공식 thumb1 = 58.5 − Stretch
  thumb2 축 = 엄지 길이 방향 → 손바닥 앞으로 도는 대립. 공식 thumb2 = −1.6·(Spread + 20)
그래서 Stretch := 58.5 − (평면 안 벌림각 + thumb_in_off),  Spread := (손바닥 쪽 들림각 − thumb_elev0) − 20
로 두면 공식이 thumb1 = 평면 안 벌림각(+오프셋), thumb2 = −1.6·(들림각 − 중립) 가 된다.
두 오프셋은 Quest·사람마다 달라 UI 노브로 맞춘다.
배율(1.3/1.7)·오프셋(−40/−30, 58.5, +20)은 **Manus 장갑 센서에 맞춘 값**이라 `calib=False`
로 끄면 오프셋 0·배율 1(엄지 공식의 상수항만 유지)로 본다.
"""

from __future__ import annotations

import numpy as np

from robosynapse.common.teleop.dex_retarget import flexion_deg

# WebXR 인덱스: metacarpal, proximal, intermediate, distal, tip
_F = {"Index": 5, "Middle": 10, "Ring": 15, "Pinky": 20}
_KNUCKLE = {"Index": 6, "Middle": 11, "Ring": 16, "Pinky": 21}

# DG-5F 관절 순서(모터 1..20) = URDF rj_dg_<finger>_<k>
JOINTS = [f"rj_dg_{u}_{k}" for u in range(1, 6) for k in range(1, 5)]


def _unit(v):
    n = np.linalg.norm(v)
    if not n > 1e-9:
        raise ValueError("degenerate hand frame")
    return v / n


def hand_axes(kp):
    """(forward, radial, palmar) — 너클·중수골로 잡는 손 프레임(DexPilot human_frame 과 같은 재료).
    forward = 중수골 기저 → 너클(손가락 방향), radial = 새끼 너클 → 검지 너클(엄지쪽),
    palmar = 손바닥이 향하는 쪽(오른손 기준; 손등 법선의 반대)."""
    kn = np.array([kp[i] for i in _KNUCKLE.values()])
    mb = np.array([kp[i] for i in _F.values()])
    fwd = _unit(kn.mean(0) - mb.mean(0))
    rad = kn[0] - kn[-1]
    dorsal = _unit(np.cross(fwd, rad))          # 오른손: f × (엄지쪽) = 손등
    rad = np.cross(dorsal, fwd)
    return fwd, _unit(rad), -dorsal


def human_ergo(kp, hand: str = "right", *, thumb_in_off: float = 0.0,
               thumb_elev0: float = 15.0) -> dict:
    """Manus ergonomics 이름 → 각도(°). Spread 는 엄지쪽(+). 엄지 두 값은 모듈 docstring 정의."""
    kp = np.asarray(kp, float)
    fwd, rad, palmar = hand_axes(kp)
    if hand == "left":
        rad = -rad
    e = {}
    for name, b in _F.items():
        e[f"{name}MCPStretch"] = flexion_deg(kp, b, b + 1, b + 2)
        e[f"{name}PIPStretch"] = flexion_deg(kp, b + 1, b + 2, b + 3)
        e[f"{name}DIPStretch"] = flexion_deg(kp, b + 2, b + 3, b + 4)
        prox = kp[b + 2] - kp[b + 1]
        e[f"{name}Spread"] = float(np.degrees(np.arctan2(prox @ rad, prox @ fwd)))
    # 엄지: 1 metacarpal(CMC), 2 proximal(MCP), 3 distal(IP), 4 tip
    m = kp[2] - kp[1]                            # 중수골 방향 (CMC 자세)
    inplane = np.hypot(m @ fwd, m @ rad)
    abd = float(np.degrees(np.arctan2(m @ rad, m @ fwd)))       # 평면 안 벌림 (엄지쪽 +)
    elev = float(np.degrees(np.arctan2(m @ palmar, inplane)))   # 손바닥 쪽 들림 (대립)
    e["ThumbInPlane"], e["ThumbElevation"] = abd, elev          # 표시·튜닝용 원값
    e["ThumbMCPStretch"] = 58.5 - (abd + thumb_in_off)
    e["ThumbMCPSpread"] = (elev - thumb_elev0) - 20.0
    e["ThumbPIPStretch"] = flexion_deg(kp, 1, 2, 3)
    e["ThumbDIPStretch"] = flexion_deg(kp, 2, 3, 4)
    return e


def tesollo_q(ergo: dict, *, calib: bool = True) -> dict:
    """manus_retarget._compute_mqd_from_q (오른손) 그대로 — {urdf joint: rad} (클램프 전)."""
    g = lambda k: float(ergo.get(k, 0.0))
    q = [g("ThumbMCPSpread"), g("ThumbMCPStretch"), g("ThumbPIPStretch"), g("ThumbDIPStretch"),
         g("IndexSpread"), g("IndexMCPStretch"), g("IndexPIPStretch"), g("IndexDIPStretch"),
         g("MiddleSpread"), g("MiddleMCPStretch"), g("MiddlePIPStretch"), g("MiddleDIPStretch"),
         g("RingSpread"), g("RingMCPStretch"), g("RingPIPStretch"), g("RingDIPStretch"),
         g("PinkySpread"), g("PinkyMCPStretch"), g("PinkyPIPStretch"), g("PinkyDIPStretch")]
    off_i, off_m = (40.0, 30.0) if calib else (0.0, 0.0)
    qd = [0.0] * 20
    qd[0] = 58.5 - q[1]
    qd[1] = q[0] + 20
    qd[2] = q[2]
    qd[3] = 0.5 * (q[2] + q[3])
    qd[4], qd[5], qd[6], qd[7] = q[4], q[5], q[6] - off_i, q[7]
    qd[8], qd[9], qd[10], qd[11] = q[8], q[9], q[10] - off_m, q[11]
    qd[12], qd[13], qd[14], qd[15] = q[12], q[13], q[14], q[15]
    qd[16] = abs(q[16]) * 2 if (q[17] > 55 and q[18] > 25) else abs(q[16]) / 1.5
    qd[17], qd[18], qd[19] = q[16], q[17], q[18]
    dir_r = [1, -1, 1, 1, -1, 1, 1, 1, -1, 1, 1, 1, -1, 1, 1, 1, 1, -1, 1, 1]
    cal = ([1, 1.6, 1.3, 1.3, 1, 1, 1.3, 1.7, 1, 1, 1.3, 1.7, 1, 1, 1.3, 1.7, 1, 1, 1, 1]
           if calib else [1] * 20)
    m = [np.radians(qd[i]) * cal[i] * dir_r[i] for i in range(20)]
    for i in range(20):                          # 후처리: 뒤로 젖힘 금지
        if i in (4, 8, 12, 16, 17):
            continue
        if i == 1:
            m[i] = min(m[i], 0.0)
        else:
            m[i] = max(m[i], 0.0)
    return dict(zip(JOINTS, m))


class TesolloMap:
    """키포인트 → (ergo, {joint: rad}) . One-Euro 필터는 DexPilot 과 같은 노브를 쓴다."""

    def __init__(self, limits_rad: dict, *, calib: bool = True, filter=None, clock=None,
                 thumb_in_off: float = 0.0, thumb_elev0: float = 15.0):
        from robosynapse.common.teleop.dexpilot_retarget import OneEuro
        import time
        self.limits = limits_rad
        self.calib = bool(calib)
        self.thumb_in_off, self.thumb_elev0 = float(thumb_in_off), float(thumb_elev0)
        self._f = OneEuro(**{k: float(v) for k, v in filter.items()}) if filter else None
        self._clock = clock or time.monotonic
        self.ergo = {}

    def reset(self):
        if self._f is not None:
            self._f.reset()

    def __call__(self, kp, filtered: bool = True, ergo: dict | None = None):
        """`ergo` 를 주면(Manus 장갑 ergonomics, Tesollo 이름) 키포인트에서 재지 않고 그 값을
        그대로 쓴다 — Tesollo 공식이 원래 받던 입력이라 근사가 없다."""
        if ergo is not None:
            self.ergo = {k: float(v) for k, v in ergo.items()}
            q = tesollo_q(self.ergo, calib=self.calib)
            return {j: float(np.clip(v, *self.limits[j])) for j, v in q.items()}
        kp = np.asarray(kp, float)
        if kp.shape != (25, 3) or not np.isfinite(kp[1:]).all():
            return None
        if filtered and self._f is not None:
            kp = self._f(kp, self._clock())
        try:
            self.ergo = human_ergo(kp, thumb_in_off=self.thumb_in_off,
                                   thumb_elev0=self.thumb_elev0)
        except ValueError:
            return None
        q = tesollo_q(self.ergo, calib=self.calib)
        return {j: float(np.clip(v, *self.limits[j])) for j, v in q.items()}
