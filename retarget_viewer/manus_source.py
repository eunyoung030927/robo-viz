"""뷰어의 Manus 입력 — 브리지가 떠 있으면 받기만 하고, 없으면 SDK 를 직접 띄운다.

동글은 한 프로그램만 쓸 수 있다. `../manus/bridge.py` 가 호스트에서 돌고 있으면(127.0.0.1:47000)
그걸 받아서 쓰고(컨테이너 시뮬레이터와 같은 데이터를 동시에 본다), 없으면 예전처럼 이 프로세스가
SDK Integrated 로 동글을 잡는다(`../manus/sdk_reader.py`).
"""

from __future__ import annotations

import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "manus"))
from client import ManusClient          # noqa: E402
from sdk_reader import MANUS as _SDK    # noqa: E402

BRIDGE = (os.environ.get("MANUS_BRIDGE_HOST", "127.0.0.1"),
          int(os.environ.get("MANUS_BRIDGE_PORT", "47000")))


class AutoManus:
    def __init__(self):
        self.mode = None                 # "bridge" | "sdk"
        self.client = ManusClient(*BRIDGE)

    def acquire(self):
        if self.mode is None:
            try:
                socket.create_connection(BRIDGE, timeout=0.5).close()
                self.mode = "bridge"
                self.client.start()
            except OSError:
                self.mode = "sdk"
        if self.mode == "sdk":
            _SDK.acquire()

    def release(self):
        if self.mode == "sdk":
            _SDK.release()

    def latest(self, prefer: str = "right"):
        if self.mode == "bridge":
            return self.client.latest(prefer)
        return _SDK.latest(prefer) if self.mode == "sdk" else None

    def status(self) -> dict:
        if self.mode == "bridge":
            c = self.client
            return {"via": "bridge", "started": True, "connected": c.connected, "err": c.err,
                    "gloves": [{**g, "n": c.n, "age_s": 0.0} for g in c.status()["gloves"]]}
        return {"via": "sdk", **_SDK.status()}


MANUS = AutoManus()
