"""실물 DG-5F 관절각 **읽기 전용** 미러 — 와이어°=URDF° 가정 검증용.

손에 쓰는 요청은 하나도 없다: Modbus FC04(입력 레지스터 읽기)만 보낸다.
  IR 0 product id · IR 1 firmware · IR 6..25 관절각 0.1° (int16)
  (robo-synapse tesollo_dg5f/modbus.py 레지스터 표와 같은 값)

손은 한 번에 클라이언트 하나만 받는다. webctl 이 이미 붙어 있으면 직접 연결은 거부되므로,
그때는 webctl `/status` 의 dexhand 패널 값(j0..j19, 실측°)을 대신 읽는다 — webctl 연결을
빼앗지 않는다. webctl `/status` 는 한 번에 ~5 s 걸려 갱신이 느리다(자세를 잠시 멈추고 볼 것).
직접 연결 중이면 webctl 이 손에 다시 붙지 못하므로, 미러 입력을 끄면 소켓을 바로 닫는다.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request

from robosynapse.common.grippers.tesollo_dg5f import protocol as P
from robosynapse.common.grippers.tesollo_dg5f.modbus import ModbusTcp

IR_PRODUCT, IR_POS = 0, 6
DIRECT_HZ = 30.0
WEBCTL_RETRY_DIRECT_S = 10.0


class RealHand:
    def __init__(self, ip: str = "169.254.186.72", port: int = 502,
                 webctl: str = "http://127.0.0.1:8089/status"):
        self.ip, self.port, self.webctl = ip, port, webctl
        self._users = 0
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self.deg = None            # {DOF name: deg}
        self.minmax = {}           # {DOF name: [min, max]} — 범위 측정(초기화 전까지 누적)
        self.t = 0.0
        self.via = ""              # "modbus" | "webctl" | ""
        self.info = ""             # product / firmware / armed 등
        self.err = ""

    # --- 참조 카운트: 미러 입력을 보는 세션이 있을 때만 돈다
    def acquire(self):
        with self._lock:
            self._users += 1
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, daemon=True, name="real-hand")
                self._thread.start()

    def release(self):
        with self._lock:
            self._users = max(0, self._users - 1)
            if self._users == 0:
                self._stop.set()

    def _update(self, deg: dict):
        self.deg = deg
        self.t = time.monotonic()
        for n, v in deg.items():
            mm = self.minmax.get(n)
            if mm is None:
                self.minmax[n] = [v, v]
            else:
                mm[0], mm[1] = min(mm[0], v), max(mm[1], v)

    def reset_minmax(self):
        self.minmax = {}

    def snapshot(self) -> dict:
        age = time.monotonic() - self.t if self.t else None
        return {"via": self.via, "info": self.info, "err": self.err,
                "age_s": None if age is None else round(age, 1)}

    # --- worker
    def _run(self):
        while not self._stop.is_set():
            if not self._direct():
                self._via_webctl()

    def _held_by_other(self) -> bool:
        """다른 프로세스(webctl)가 손과 TCP 연결 중인가 — 그때는 두 번째 연결을 시도조차 하지
        않는다(펌웨어가 두 번째 클라이언트를 어떻게 다루는지 실측된 적 없음 → 건드리지 않는다)."""
        import subprocess
        try:
            out = subprocess.run(["ss", "-tnH", "state", "established",
                                  "dst", f"{self.ip}:{self.port}"],
                                 capture_output=True, text=True, timeout=3).stdout
        except Exception:
            return True                  # 모르면 안전하게 "잡혀 있음"
        return bool(out.strip())

    def _direct(self) -> bool:
        """직접 Modbus 읽기. 손이 다른 프로세스에 잡혀 있거나 연결이 안 되면 False(→ webctl 경유)."""
        if self._held_by_other():
            self.err = ""
            return False
        link = ModbusTcp(self.ip, self.port, 0.5)
        try:
            prod, fw = link.read_input(IR_PRODUCT, 2)
        except Exception as e:
            link.close()
            self.err = f"직접 연결 불가({type(e).__name__}) — webctl 이 손을 잡고 있으면 정상"
            return False
        self.via, self.err = "modbus", ""
        self.info = (f"{P.MODELS.get(prod & 0xFFFF, hex(prod & 0xFFFF))} · "
                     f"fw {(fw >> 8) & 0xFF}.{fw & 0xFF}")
        try:
            while not self._stop.is_set():
                raw = link.read_input(IR_POS, P.N_DOF)
                self._update({n: v / 10.0 for n, v in zip(P.DOF_NAMES, raw)})
                time.sleep(1.0 / DIRECT_HZ)
        except Exception as e:
            self.err = f"직접 읽기 끊김: {type(e).__name__}: {e}"
        finally:
            link.close()                 # webctl 이 다시 붙을 수 있게 바로 놓는다
        return True

    def _via_webctl(self):
        t_end = time.monotonic() + WEBCTL_RETRY_DIRECT_S
        while not self._stop.is_set() and time.monotonic() < t_end:
            try:
                with urllib.request.urlopen(self.webctl, timeout=15) as r:
                    st = json.load(r).get("dexhand") or {}
                vals = [v for k, v in sorted(((k, v) for k, v in st.items()
                                              if k.startswith("j") and " (" in k),
                                             key=lambda kv: int(kv[0][1:kv[0].index(" ")]))]
                if len(vals) != P.N_DOF:
                    self.err = f"webctl dexhand 관절 {len(vals)}개 — DG-5F 가 webctl 에 연결돼 있나?"
                    time.sleep(1.0)
                    continue
                self._update(dict(zip(P.DOF_NAMES, (float(v) for v in vals))))
                self.via = "webctl"
                self.info = (f"{st.get('model', '?')} · armed={st.get('armed')} · "
                             f"fwRun={st.get('fwRun')} · {st.get('note', '')}")
                self.err = ""
            except Exception as e:
                self.via = ""
                self.err = f"webctl 읽기 실패: {type(e).__name__}: {e}"
                time.sleep(1.0)
