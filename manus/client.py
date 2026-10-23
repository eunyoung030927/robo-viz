"""Manus 브리지 수신기 — 표준 라이브러리 + numpy 만 쓴다(컨테이너에 Manus SDK·USB 불필요).

    from client import ManusClient
    mc = ManusClient()                    # 127.0.0.1:47000 (브리지 기본값)
    mc.start()
    r = mc.latest(prefer="right")         # (kp (25,3) m, ergo {이름: °} | None, side, glove id) | None

`--network host` 컨테이너면 호스트의 브리지에 127.0.0.1 로 바로 붙는다. 브리지가 꺼졌다 켜져도
알아서 다시 붙는다.

    python client.py [--host H] [--port P]     # 받는 값 확인용(초당 한 줄 출력)
"""

from __future__ import annotations

import json
import socket
import threading
import time

import numpy as np


class ManusClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 47000, stale_s: float = 0.5):
        self.host, self.port, self.stale_s = host, int(port), float(stale_s)
        self._lock = threading.Lock()
        self._msg = None
        self._t = 0.0
        self._thread = None
        self.connected = False
        self.err = ""
        self.n = 0

    def start(self) -> "ManusClient":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="manus-client")
            self._thread.start()
        return self

    def _run(self):
        while True:
            try:
                with socket.create_connection((self.host, self.port), timeout=2.0) as s:
                    s.settimeout(2.0)
                    self.connected, self.err = True, ""
                    buf = b""
                    while True:
                        chunk = s.recv(65536)
                        if not chunk:
                            raise ConnectionError("bridge closed")
                        buf += chunk
                        *lines, buf = buf.split(b"\n")
                        if lines:
                            msg = json.loads(lines[-1])          # 최신 줄만 쓴다
                            with self._lock:
                                self._msg, self._t = msg, time.monotonic()
                                self.n += len(lines)
            except Exception as e:
                self.connected = False
                self.err = f"브리지 {self.host}:{self.port} 연결 안 됨 ({type(e).__name__})"
                time.sleep(1.0)

    def gloves(self) -> list:
        """[{"id", "side", "kp" (25,3), "ergo"}] — 최근 `stale_s` 안에 받은 것만."""
        with self._lock:
            msg, t = self._msg, self._t
        if msg is None or time.monotonic() - t > self.stale_s:
            return []
        out = []
        for g in msg.get("gloves", []):
            if g.get("age_s", 0) > self.stale_s:
                continue
            out.append({"id": g["id"], "side": g["side"], "ergo": g.get("ergo"),
                        "kp": np.asarray(g["kp"], float).reshape(25, 3)})
        return out

    def latest(self, prefer: str = "right"):
        """(kp, ergo, side, glove id) — `prefer` 쪽 손이 있으면 그쪽, 없으면 다른 손. 없으면 None."""
        gl = self.gloves()
        if not gl:
            return None
        gl.sort(key=lambda g: g["side"] != prefer)
        g = gl[0]
        return g["kp"], g["ergo"], g["side"], g["id"]

    def status(self) -> dict:
        return {"via": "bridge", "connected": self.connected, "err": self.err, "msgs": self.n,
                "gloves": [{"id": f"{g['id']:#x}", "side": g["side"]} for g in self.gloves()]}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=47000)
    a = ap.parse_args()
    mc = ManusClient(a.host, a.port).start()
    last = 0
    while True:
        time.sleep(1.0)
        r = mc.latest()
        rate = mc.n - last
        last = mc.n
        if r is None:
            print(f"[client] {mc.err or '장갑 데이터 없음'} ({rate} msg/s)", flush=True)
        else:
            kp, ergo, side, gid = r
            idx = None if ergo is None else round(ergo.get("IndexMCPStretch", float('nan')), 1)
            print(f"[client] glove {gid:#x} {side} · {rate} msg/s · index tip {np.round(kp[9], 3)} "
                  f"· IndexMCPStretch {idx}", flush=True)
