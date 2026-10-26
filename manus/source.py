"""텔레옵 코드가 쓰는 단일 진입점 — 동글을 직접 읽든 브리지로 받든 코드는 같다.

    import sys; sys.path.insert(0, "/workspace/manus")
    from source import open_manus
    manus = open_manus()
    r = manus.latest(prefer="right")    # (kp (25,3) m, ergo {이름: °} | None, side, glove id) | None

  * 기본: 이 프로세스가 Manus SDK(Integrated)로 동글을 직접 잡는다(`sdk_reader.py`).
    동글이 이 PC(또는 USB 를 넘겨받은 컨테이너)에 꽂혀 있을 때.
  * `MANUS_BRIDGE_HOST` 가 설정돼 있으면: 그 주소의 `bridge.py` 에서 받기만 한다(`client.py`).
    동글이 다른 PC 에 꽂혀 있고 거기서 브리지가 돌 때. 포트는 `MANUS_BRIDGE_PORT`(기본 47000).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def open_manus():
    host = os.environ.get("MANUS_BRIDGE_HOST", "").strip()
    if host:
        from client import ManusClient
        return ManusClient(host, int(os.environ.get("MANUS_BRIDGE_PORT", "47000"))).start()
    from sdk_reader import MANUS
    MANUS.acquire()
    return MANUS
