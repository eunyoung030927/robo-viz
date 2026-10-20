"""굽힘 이상 진단: 사람 관절각 vs DG-5F 리타겟 관절각 (합성 자세).

DG-5F 는 손가락마다 굽힘 3관절이 전부 독립 구동(RH5DG2 는 PIP–DIP mimic 결합)이라
손끝 위치(3D)만 맞추는 DexPilot 목적함수로는 관절 배분이 정해지지 않는다(여유 자유도).
변형 비교:
  base   — 현행 그대로
  flex0  — 굽힘 관절(2~4, 새끼 3~4) 하한 0° (뒤로 꺾임 금지)
  prior  — flex0 + 사람 관절각 사전항 λ·Σ(q−θ_human)²  (손끝 목적은 그대로)
"""

from __future__ import annotations

import numpy as np

import hands
import synth
from robosynapse.common.teleop.dex_retarget import flexion_deg, palm_frame

FING = {"index": (2, 5), "middle": (3, 10), "ring": (4, 15), "little": (5, 20)}   # urdf 손가락 번호, kp 기저


def human_angles(kp) -> dict:
    """{urdf joint: rad} — 사람 MCP/PIP/DIP 굽힘(+ 벌림)을 DG-5F 관절에 1:1 대응."""
    out = {}
    n, fwd = palm_frame(kp)
    lat = np.cross(fwd, n)
    for f, (u, b) in FING.items():
        mcp = flexion_deg(kp, b, b + 1, b + 2)
        pip = flexion_deg(kp, b + 1, b + 2, b + 3)
        dip = flexion_deg(kp, b + 2, b + 3, b + 4)
        prox = kp[b + 2] - kp[b + 1]
        abd = np.degrees(np.arctan2(prox @ lat, prox @ fwd))
        if f == "little":
            out.update({"rj_dg_5_3": mcp, "rj_dg_5_4": (pip + dip) / 2})
        else:
            out.update({f"rj_dg_{u}_2": mcp, f"rj_dg_{u}_3": pip, f"rj_dg_{u}_4": dip})
        out[f"rj_dg_{u}_{2 if f == 'little' else 1}"] = -abd    # 부호: 엄지쪽 = URDF −
    return {k: np.radians(v) for k, v in out.items()}


def make(variant: str, lam: float = 0.02):
    locks = None
    rt = hands.build("dg5f", knobs={"filter": None})
    if variant in ("flex0", "prior"):
        lo = rt._lo.copy()
        for i, (_, j, *_r) in enumerate(rt._map):
            u, k = int(j.split("_")[2]), int(j.split("_")[3])
            if (u in (2, 3, 4) and k >= 2) or (u == 5 and k >= 3):
                lo[i] = max(lo[i], 0.0)
        rt._lo = lo
        rt._bounds = list(zip(rt._lo, rt._hi))
        rt._q0 = np.clip(rt._q0, rt._lo, rt._hi)
    if variant == "prior":
        names = [j for _, j, *_ in rt._map]
        base_obj = rt._objective
        state = {"theta": None}

        def obj(x, ref, w, q_prev):
            loss, g = base_obj(x, ref, w, q_prev)
            th = state["theta"]
            for i, n in enumerate(names):
                if n in th:
                    d = x[i] - th[n]
                    loss += lam * d * d
                    g[i] += 2 * lam * d
            return loss, g
        rt._objective = obj
        base_step = rt._step

        def step(kp, filtered):
            state["theta"] = human_angles(np.asarray(kp, float))
            return base_step(kp, filtered)
        rt._step = step
    return rt


def run():
    poses = {"index_curl": synth.hand(curl={"index": 40.0}),
             "ring_curl": synth.hand(curl={"ring": 45.0}),
             "half": synth.PRESETS["half"](), "fist": synth.PRESETS["fist"](),
             "pinch_index": synth.PRESETS["pinch_index"](), "pinch_ring": synth.PRESETS["pinch_ring"]()}
    for pname, kp in poses.items():
        th = human_angles(kp)
        print(f"\n== {pname}")
        for v in ("base", "flex0", "prior"):
            rt = make(v)
            for _ in range(20):
                q = rt._step(kp, filtered=False)
            names = [j for _, j, *_ in rt._map]
            Q = dict(zip(names, np.degrees(q)))
            P, _ = rt.chain.fk(q, jac=False)
            err = np.linalg.norm(rt.to_robot(kp)[1:] - P[:rt._n_tip], axis=1) * 1e3
            rows = []
            for f, (u, _b) in FING.items():
                js = ([f"rj_dg_5_{k}" for k in (2, 3, 4)] if f == "little"
                      else [f"rj_dg_{u}_{k}" for k in (1, 2, 3, 4)])
                rows.append(f"{f[:3]} " + "/".join(f"{Q[j]:.0f}" for j in js))
            print(f"  {v:6s} tip_err[mm] {np.round(err).astype(int).tolist()}  " + "  ".join(rows))
        hrow = []
        for f, (u, _b) in FING.items():
            js = ([f"rj_dg_5_{k}" for k in (2, 3, 4)] if f == "little"
                  else [f"rj_dg_{u}_{k}" for k in (1, 2, 3, 4)])
            hrow.append(f"{f[:3]} " + "/".join(f"{np.degrees(th[j]):.0f}" for j in js))
        print("  human  (abd/MCP/PIP/DIP)           " + "  ".join(hrow))


if __name__ == "__main__":
    run()
