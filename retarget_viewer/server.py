"""리타겟팅 웹 검증기 — 사람 손 키포인트 → robo-synapse DexPilotRetarget → URDF 뷰어.

로봇에는 아무것도 보내지 않는다(손·팔 드라이버를 import 하지 않음). 입력 3종:
  * preset/synth — 합성 자세(synth.py)
  * live        — Quest WebXR. robo-synapse `WebXRHandSource` 를 **별도 포트(기본 8013)** 로
                  띄워 webctl(8012)과 부딪히지 않는다. 인증서는 robo-synapse 의 공용
                  config/teleop.local.{crt,key}, 토큰은 이 프로세스가 따로 만든다.
  * replay      — live 를 녹화한 recordings/*.npz

    python server.py [--port 8030] [--host 127.0.0.1] [--xr-port 8013]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import subprocess
import time
import traceback

import numpy as np
from aiohttp import WSMsgType, web

import hands
import synth
from tesollo_map import TesolloMap

HERE = pathlib.Path(__file__).resolve().parent
STATIC = HERE / "static"
RECORDINGS = HERE / "recordings"
FPS = 30.0

FINGER_ORDER = ("thumb", "index", "middle", "ring", "little")


# ------------------------------------------------------------------ live (Quest)
class Live:
    """WebXRHandSource 한 개(프로세스 공용). 첫 요청 때 시작."""

    def __init__(self, repo: pathlib.Path, port: int):
        self.repo, self.port = repo, port
        self.src = None
        self.err = ""

    def start(self) -> dict:
        if self.src is None:
            from robosynapse.common.teleop.webxr_source import WebXRHandSource
            cert, key = self.repo / "config/teleop.local.crt", self.repo / "config/teleop.local.key"
            have = cert.is_file() and key.is_file()
            self.src = WebXRHandSource(port=self.port, cert=str(cert) if have else None,
                                       key=str(key) if have else None,
                                       control_hand="right",
                                       public_host=_cert_host(cert) if have else None)
            # 토큰 고정: 서버를 재시작해도 헤드셋 주소(북마크)가 그대로이게 파일에 둔다
            self.src.token = _persistent_token()
            # 헤드셋 뼈대 색: 리타겟 검증 중엔 "추종 중"(파랑/초록)으로 고정
            self.src.state_fn = lambda: {"live": True, "engaged": True, "engages": 1,
                                         "overlay": True}
        if not self.src.start():
            self.err = self.src.err
            return {"ok": False, "error": self.err}
        return {"ok": True, "url": self.src.url}

    def status(self) -> dict:
        if self.src is None:
            return {"started": False}
        return {"started": True, "url": self.src.url, "connected": bool(self.src.connected),
                "fps": self.src.fps_measured if self.src.connected else 0.0,
                "err": self.src.err or ""}

    def keypoints(self, side: str = "right"):
        if self.src is None:
            return None
        f = self.src.latest()
        return None if f is None or f.keypoints is None else np.asarray(f.keypoints, float)


TOKEN_FILE = pathlib.Path.home() / ".config/robo-viz/xr_token"


def _persistent_token() -> str:
    """8013 접근 토큰 — 처음 한 번 만들어 ~/.config/robo-viz/xr_token(600)에 두고 재사용.
    지우면 다음 시작 때 새로 만든다(주소가 바뀜)."""
    import os
    import secrets
    try:
        t = TOKEN_FILE.read_text().strip()
        if len(t) >= 16:
            return t
    except OSError:
        pass
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(16)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t + "\n")
    return t


def _cert_host(cert: pathlib.Path):
    try:
        out = subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-subject"],
                             capture_output=True, text=True, timeout=5).stdout
        return out.split("CN", 1)[1].split("=", 1)[1].strip().split(",")[0] or None
    except Exception:
        return None


# ------------------------------------------------------------------ 세션(브라우저 1개)
class Session:
    def __init__(self, live: Live):
        self.live = live
        self.hand = "dg5f"
        self.knobs: dict = {}
        self.locks: dict = {}
        self.source = {"kind": "preset", "name": "open"}
        self.rt = None
        self.tm = None                         # Tesollo 공식 Manus 매핑(dg5f 만)
        self.backend = "dexpilot"              # 메인 손: "dexpilot" | "tesollo"
        self.compare = False                   # 다른 방식 손을 옆에 같이
        self.err = ""
        self.rec: list | None = None           # [(t, kp)] 녹화 중이면 list
        self.replay = None                     # (t[], kp[], t0)
        self.rebuild()

    def rebuild(self):
        try:
            self.rt = hands.build(self.hand, self.knobs, self.locks)
            self.tm = None
            if self.hand == "dg5f":
                lim = {j: (min(a, b), max(a, b)) for _d, j, a, b in hands.dof_space(self.hand)[1]}
                self.tm = TesolloMap(lim, calib=bool(self.knobs.get("tesollo_calib", True)),
                                     thumb_in_off=float(self.knobs.get("thumb_in_off", 0.0)),
                                     thumb_elev0=float(self.knobs.get("thumb_elev0", 15.0)),
                                     filter=(self.knobs.get("filter")
                                             or hands.DEFAULT_KNOBS["filter"]))
            self.err = ""
        except Exception as e:                 # 설정 오류는 화면에 띄우고 이전 rt 유지 안 함
            self.rt = None
            self.err = f"{type(e).__name__}: {e}"

    # --- input
    def keypoints(self):
        s = self.source
        if s["kind"] == "preset":
            return synth.PRESETS[s.get("name", "open")]()
        if s["kind"] == "synth":
            p = s.get("params", {})
            return synth.hand(curl=p.get("curl"), spread=p.get("spread"),
                              thumb_lift_deg=float(p.get("thumb_lift", 20)),
                              thumb_curl_deg=float(p.get("thumb_curl", 10)),
                              thumb_swing_deg=float(p.get("thumb_swing", 0)))
        if s["kind"] == "live":
            kp = self.live.keypoints()
            if kp is not None and self.rec is not None:
                self.rec.append((time.monotonic(), kp.copy()))
            return kp
        if s["kind"] == "replay" and self.replay is not None:
            t, kps, t0 = self.replay
            el = (time.monotonic() - t0) % max(t[-1], 1e-3)
            return kps[int(np.searchsorted(t, el, side="right")) - 1]
        return None

    # --- one tick
    def frame(self) -> dict:
        out = {"type": "frame", "hand": self.hand, "err": self.err,
               "live": self.live.status(), "rec": None if self.rec is None else len(self.rec)}
        if self.rt is None:
            return out
        kp = self.keypoints()
        if kp is None:
            out["nokp"] = True
            return out
        rt = self.rt
        names = [j for _, j, *_ in rt._map]
        t0 = time.perf_counter()
        filtered = self.source["kind"] in ("live", "replay")
        q_dp = rt._step(kp, filtered=filtered)       # 항상 돈다(warm start·투영 상태 유지)
        ms = (time.perf_counter() - t0) * 1e3
        if q_dp is None:
            out["nokp"] = True
            return out
        q_t = None
        if self.tm is not None:
            d = self.tm(kp, filtered=filtered)
            if d is not None and self.knobs.get("tesollo_thumb_dp", True):
                # 엄지는 DexPilot 해를 쓴다: 각도 대응만으로는 대립(손바닥 가로지르기)이 안 나온다
                # (Quest 에서 대립 중 손바닥 평면 안 벌림각이 0 근처로 무너져 thumb1 이 작아짐)
                for n, v in zip(names, q_dp):
                    if n.startswith("rj_dg_1_"):
                        d[n] = float(v)
            q_t = None if d is None else np.array([d[n] for n in names])
            out["ergo"] = {k: round(v, 1) for k, v in self.tm.ergo.items()}
        use_t = self.backend == "tesollo" and q_t is not None
        q = q_t if use_t else q_dp
        other = q_dp if use_t else q_t
        out["backend"] = "tesollo" if use_t else "dexpilot"
        if self.compare and other is not None:
            out["q_ghost"] = {n: float(v) for n, v in zip(names, other)}
            out["ghost_backend"] = "dexpilot" if use_t else "tesollo"
        P, _ = rt.chain.fk(q, jac=False)
        tips_r = P[:rt._n_tip]
        targets = rt.to_robot(kp)                                    # [root, tips…]
        # 사람 손 전체(25점)를 같은 손 프레임으로 — 표시용(뼈길이 정규화 전, 손 단위 scale)
        root_h, R_h = rt.human_frame(kp)
        skel = rt._to_hand_frame(kp - root_h, R_h) @ rt._R_r.T * rt.scale + rt._root_r
        lo, hi = rt._lo, rt._hi
        at_lim = [n for n, v, a, b in zip(names, q, lo, hi) if b - a > 1e-6 and
                  (v - a < np.radians(0.5) or b - v < np.radians(0.5))]
        pairs = []
        dh = rt.human_pair_dists(kp)
        for k in range(rt._n_pair):
            a, b = rt.fingers[rt._task[k] - 1], rt.fingers[rt._origin[k] - 1]
            pairs.append({"a": a, "b": b, "proj": bool(rt.projected[k]),
                          "human_mm": float(dh[k] * 1e3),
                          "robot_mm": float(np.linalg.norm(tips_r[rt._task[k] - 1]
                                                           - tips_r[rt._origin[k] - 1]) * 1e3)})
        out.update({
            "q": {n: float(v) for n, v in zip(names, q)},
            "fingers": rt.fingers,
            "skeleton": skel.tolist(),
            "targets": targets[1:].tolist(), "root": targets[0].tolist(),
            "robot_tips": tips_r.tolist(),
            "tip_err_mm": [float(np.linalg.norm(a - b) * 1e3) for a, b in zip(targets[1:], tips_r)],
            "pairs": pairs, "at_limit": at_lim, "solve_ms": ms,
        })
        return out

    # --- control messages
    def handle(self, m: dict) -> dict | None:
        t = m.get("type")
        if t == "config":
            self.hand = m.get("hand", self.hand)
            self.knobs = m.get("knobs", self.knobs)
            self.locks = {k: float(v) for k, v in (m.get("locks") or {}).items()}
            self.backend = m.get("backend", self.backend)
            self.compare = bool(m.get("compare", self.compare))
            self.rebuild()
        elif t == "source":
            self.source = {k: v for k, v in m.items() if k != "type"}
            if self.source["kind"] == "live":
                return {"type": "live", **self.live.start()}
            if self.source["kind"] == "replay":
                d = np.load(RECORDINGS / pathlib.Path(self.source["file"]).name)
                self.replay = (d["t"] - d["t"][0], d["kp"], time.monotonic())
            if self.rt is not None:
                self.rt.reset()
            if self.tm is not None:
                self.tm.reset()
        elif t == "record":
            if m.get("on"):
                self.rec = []
            elif self.rec is not None:
                RECORDINGS.mkdir(exist_ok=True)
                name = time.strftime("%y%m%d_%H%M%S") + ".npz"
                if self.rec:
                    np.savez(RECORDINGS / name, t=np.array([r[0] for r in self.rec]),
                             kp=np.stack([r[1] for r in self.rec]))
                n, self.rec = len(self.rec), None
                return {"type": "saved", "file": name if n else None, "frames": n}
        elif t == "reset" and self.rt is not None:
            self.rt.reset()
        return None


# ------------------------------------------------------------------ http
def hand_info() -> dict:
    out = {}
    for name, cfg in hands.HANDS.items():
        limits, jmap = hands.dof_space(name)
        out[name] = {
            "label": cfg["label"], "urdf": f"/desc/{name}/{pathlib.Path(cfg['urdf']).name}",
            "base_link": cfg["base_link"], "tip_links": cfg["tip_links"],
            "knuckle_links": cfg["knuckle_links"],
            "angle_prior": cfg.get("angle_fn") is not None,
            "tesollo": name == "dg5f",
            "joints": hands.urdf_joints(pathlib.Path(cfg["urdf"])),
            "dofs": [{"dof": d, "joint": j, "lo": min(a, b), "hi": max(a, b),
                      "wire": cfg.get("wire_names", {}).get(j, d)} for d, j, a, b in jmap],
        }
    return out


def make_app(live: Live) -> web.Application:
    app = web.Application()

    async def index(_):
        return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    async def api_hands(_):
        return web.json_response({"hands": hand_info(), "presets": list(synth.PRESETS),
                                  "knobs": hands.DEFAULT_KNOBS,
                                  "recordings": sorted(p.name for p in RECORDINGS.glob("*.npz"))
                                  if RECORDINGS.exists() else []})

    async def desc(req):
        cfg = hands.HANDS.get(req.match_info["hand"])
        if cfg is None:
            raise web.HTTPNotFound()
        root = pathlib.Path(cfg["desc_dir"]).resolve()
        p = (root / req.match_info["path"]).resolve()
        if root not in p.parents or not p.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(p)

    async def ws(req):
        sock = web.WebSocketResponse(heartbeat=10.0)
        await sock.prepare(req)
        sess = Session(live)

        async def pump():
            while not sock.closed:
                try:
                    await sock.send_str(json.dumps(sess.frame()))
                except ConnectionResetError:
                    return
                except Exception as e:
                    traceback.print_exc()
                    await sock.send_str(json.dumps({"type": "frame", "err": f"{type(e).__name__}: {e}"}))
                await asyncio.sleep(1.0 / FPS)

        task = asyncio.create_task(pump())
        try:
            async for msg in sock:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    reply = sess.handle(json.loads(msg.data))
                except Exception as e:
                    reply = {"type": "error", "error": f"{type(e).__name__}: {e}"}
                if reply:
                    await sock.send_str(json.dumps(reply))
        finally:
            task.cancel()
        return sock

    app.router.add_get("/", index)
    app.router.add_get("/api/hands", api_hands)
    app.router.add_get("/desc/{hand}/{path:.+}", desc)
    app.router.add_get("/ws", ws)
    app.router.add_static("/static", STATIC)
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8030)
    ap.add_argument("--xr-port", type=int, default=8013)
    ap.add_argument("--repo", default=str(pathlib.Path.home() / "workspace/robo-synapse"))
    a = ap.parse_args()
    live = Live(pathlib.Path(a.repo), a.xr_port)
    print(f"retarget viewer → http://{a.host}:{a.port}/  (원격: ssh -L {a.port}:127.0.0.1:{a.port} ro1)")
    web.run_app(make_app(live), host=a.host, port=a.port, print=None)


if __name__ == "__main__":
    main()
