"""헤드리스 점검: 합성 자세마다 리타겟 결과(관절°, 손끝 간 거리, solve ms)를 찍는다.

    python smoke.py [hand ...]      # 기본 dg5f rh5dg2
"""

from __future__ import annotations

import sys
import time

import numpy as np

import hands
import synth


def run(hand: str) -> None:
    print(f"\n=== {hand} — {hands.HANDS[hand]['label']}")
    for name, make in synth.PRESETS.items():
        rt = hands.build(hand, knobs={"filter": None})
        kp = make()
        t0 = time.perf_counter()
        for _ in range(15):                       # warm start 수렴
            q = rt._step(kp, filtered=False)
        ms = (time.perf_counter() - t0) / 15 * 1e3
        P, _ = rt.chain.fk(q, jac=False)
        tips = P[:rt._n_tip]
        d_ti = [np.linalg.norm(tips[0] - tips[i]) * 1e3 for i in range(1, rt._n_tip)]
        deg = {d: round(float(np.degrees(v)), 1) for d, v in zip(rt.dofs, q)}
        proj = "".join("1" if p else "0" for p in rt.projected[:rt._n_s1])
        print(f"- {name:13s} {ms:5.1f} ms  thumb→[i,m,r,l] {['%.0f' % x for x in d_ti]} mm  "
              f"proj s1={proj}")
        print("    ", "  ".join(f"{hands.HANDS[hand].get('wire_names', {}).get(d, d)}={v}"
                                for d, v in deg.items()))


if __name__ == "__main__":
    for h in sys.argv[1:] or ["dg5f", "rh5dg2"]:
        run(h)
