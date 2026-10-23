"""장갑 연결 확인 — sdk_reader 로 ~12초 읽고 요약을 찍는다(읽기만). 끝날 때 나오는 "Stopping Service" 로그는 정상 종료.

    ./isaaclab.sh -p /workspace/manus/check.py        # 컨테이너 (MANUS_SDK_PY 는 docker run 에서 지정)
    MANUS_SDK_PY=~/manus_sdk/Python python check.py    # 호스트
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sdk_reader import MANUS  # noqa: E402

MANUS.acquire()
t0 = time.time()
r = None
while time.time() - t0 < 12:
    r = MANUS.latest()
    if r is not None and time.time() - t0 > 4:
        break
    time.sleep(0.2)
st = MANUS.status()
print("STATUS", {k: st[k] for k in ("connected", "err")}, st["gloves"])
if r is None:
    print("NO_GLOVE_DATA")
else:
    kp, ergo, side, gid = r
    n0 = st["gloves"][0]["n"] if st["gloves"] else 0
    time.sleep(2.0)
    n1 = MANUS.status()["gloves"][0]["n"]
    print(f"GLOVE {gid:#x} {side} rate~{(n1 - n0) / 2:.0f}Hz finite={bool(np.isfinite(kp).all())}")
    print("index tip (m):", np.round(kp[9], 3))
    if ergo:
        print("ergo:", {k: round(v, 1) for k, v in list(ergo.items())[:8]})
