"""DexPilot + 사람 관절각 사전항 (DG-5F 처럼 굽힘 관절이 전부 독립 구동인 손용).

왜: DexPilot 목적함수는 손끝 위치(벡터)만 맞춘다. RH5DG2 는 PIP–DIP 가 mimic 으로 묶여
손가락당 자유도가 적어 해가 거의 하나지만, DG-5F 는 손가락당 굽힘 3관절이 독립이라
같은 손끝 위치를 내는 관절 배분이 무수히 많다(여유 자유도). 그러면 warm start 에서 가장
싼 방향 — 중간 관절(PIP)을 과하게 꺾고 MCP 는 거의 안 쓰는 — 으로 풀린다.
(합성 측정 2026-10-20: 사람 35/35/35° → 로봇 27/45/25°, 주먹 70/70/70 → 47/90/47,
 검지 핀치 31/30/30 → 5/87/60.)

처방: 손끝 목적은 그대로 두고 λ·Σ(q_j − θ_j)² 를 더한다. θ = 사람 손에서 잰 같은 관절의
각도(MCP/PIP/DIP 굽힘, 벌림). 여유 자유도의 배분만 사람 쪽으로 당기는 항이라 λ 가 작으면
손끝 오차는 수 mm 만 늘고, 접촉 투영(핀치) 가중치(200/400)에는 거의 영향이 없다.
λ = 0 이면 원래 DexPilot 과 비트 동일.

핀치 게이트(`pinch_scale`): 큰 λ 는 굽힘 모양을 사람과 맞추지만 손끝을 덜 붙인다(합성: λ=0.3 이면
핀치 간격 10~14 mm). 그래서 엄지와 접촉 투영된 손가락(DexPilot `projected` s1 플래그)만 그 틱에
λ×pinch_scale 로 낮춘다 — 쥐기 모양은 사람처럼, 핀치는 DexPilot 처럼 닫히게.
"""

from __future__ import annotations

import numpy as np

from robosynapse.common.teleop.dex_retarget import flexion_deg, palm_frame
from robosynapse.common.teleop.dexpilot_retarget import DexPilotRetarget

# DG-5F: (URDF 손가락 번호, WebXR metacarpal 인덱스)
_DG5F_FINGERS = {"index": (2, 5), "middle": (3, 10), "ring": (4, 15), "little": (5, 20)}


def dg5f_joint_finger(joint: str) -> str:
    """rj_dg_<u>_<k> → finger name (u: 1 엄지, 2 검지, 3 중지, 4 약지, 5 새끼)."""
    return ("thumb", "index", "middle", "ring", "little")[int(joint.split("_")[2]) - 1]


def dg5f_human_angles(kp) -> dict:
    """{urdf joint: rad} — 사람 굽힘·벌림을 DG-5F 관절에 1:1 대응. 엄지는 대응이 불분명해 제외.

    검지·중지·약지: j1=벌림, j2=MCP, j3=PIP, j4=DIP. 새끼: j1=손바닥 접힘(제외), j2=벌림,
    j3=MCP, j4=PIP·DIP 평균(새끼는 굽힘 관절이 둘뿐).
    벌림 부호: URDF 음수 = 엄지쪽 (합성 spread 로 확인: 사람 검지 +12° 엄지쪽 → index1 −12°)."""
    kp = np.asarray(kp, float)
    n, fwd = palm_frame(kp)
    lat = np.cross(fwd, n)
    out = {}
    for f, (u, b) in _DG5F_FINGERS.items():
        mcp = flexion_deg(kp, b, b + 1, b + 2)
        pip = flexion_deg(kp, b + 1, b + 2, b + 3)
        dip = flexion_deg(kp, b + 2, b + 3, b + 4)
        prox = kp[b + 2] - kp[b + 1]
        abd = float(np.degrees(np.arctan2(prox @ lat, prox @ fwd)))
        if f == "little":
            out.update({"rj_dg_5_2": -abd, "rj_dg_5_3": mcp, "rj_dg_5_4": (pip + dip) / 2})
        else:
            out.update({f"rj_dg_{u}_1": -abd, f"rj_dg_{u}_2": mcp,
                        f"rj_dg_{u}_3": pip, f"rj_dg_{u}_4": dip})
    return {k: float(np.radians(v)) for k, v in out.items()}


class PriorDexPilot(DexPilotRetarget):
    """DexPilotRetarget + λ·Σ(q − θ_human)². `angle_fn(kp) → {urdf joint: rad}`."""

    def __init__(self, *args, angle_prior: float = 0.0, angle_fn=None, finger_of=None,
                 pinch_scale: float = 1.0, **kw):
        super().__init__(*args, **kw)
        self.angle_prior = float(angle_prior)
        self.pinch_scale = float(pinch_scale)
        self._angle_fn = angle_fn
        self._finger_of = finger_of
        self._names = [j for _, j, *_ in self._map]
        self._theta = None

    def to_robot(self, kp):
        # `_step` 이 solve 직전에 (필터를 거친) 키포인트로 부르는 자리 — θ 도 같은 프레임에서 잰다
        if self.angle_prior > 0 and self._angle_fn is not None:
            try:
                th = self._angle_fn(kp)
                idx = np.array([i for i, n in enumerate(self._names) if n in th], int)
                self._theta = (idx, np.array([th[self._names[i]] for i in idx]))
            except Exception:
                self._theta = None             # 퇴화 프레임: 사전항 없이 원래대로
        return super().to_robot(kp)

    def _weights(self, idx) -> np.ndarray:
        """관절별 λ — 엄지와 접촉 투영 중인 손가락(과 엄지)은 λ×pinch_scale."""
        lam = np.full(len(idx), self.angle_prior)
        if self.pinch_scale == 1.0 or self._finger_of is None:
            return lam
        pinched = {self.fingers[k + 1] for k in range(self._n_s1) if self.projected[k]}
        if pinched:
            pinched.add("thumb")
            for n, i in enumerate(idx):
                if self._finger_of(self._names[i]) in pinched:
                    lam[n] *= self.pinch_scale
        return lam

    def _objective(self, x, ref, w, q_prev):
        loss, g = super()._objective(x, ref, w, q_prev)
        if self.angle_prior > 0 and self._theta is not None:
            idx, th = self._theta
            d = x[idx] - th
            lam = self._weights(idx)            # projected 는 solve 직전 _references 가 갱신
            loss += float((lam * d) @ d)
            g = g.copy()
            g[idx] += 2.0 * lam * d
        return loss, g
