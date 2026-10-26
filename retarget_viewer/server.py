"""리타겟팅 웹 검증기 — 사람 손 키포인트 → robo-synapse DexPilotRetarget → URDF 뷰어.

로봇에는 아무것도 보내지 않는다(손·팔 드라이버를 import 하지 않음). 입력 3종:
  * preset/synth — 합성 자세(synth.py)
  * live        — Quest WebXR. robo-synapse `WebXRHandSource` 를 **별도 포트(기본 8013)** 로
                  띄워 webctl(8012)과 부딪히지 않는다. 인증서는 robo-synapse 의 공용
                  config/teleop.local.{crt,key}, 토큰은 이 프로세스가 따로 만든다.
  * replay      — live 를 녹화한 recordings/*.npz (양손: kp_r/kp_l)
  * manus       — Manus 장갑(manus_source.py)
  * real        — 실물 DG-5F 읽기 전용 미러(real_hand.py)

사람 손은 오른손·왼손·양손(`config.sides`). 손마다 Lane(DexPilot + Tesollo 매핑)을 따로 돌리고,
왼손은 DexPilot 이 반사해 오른손 로봇 문제로 풀어 화면에서 거울로 뒤집어 그린다.

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
from real_hand import RealHand
from manus_source import MANUS

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
            cert, key = self.repo / "config/teleop.local.crt", self.repo / "config/teleop.local.key"
            have = cert.is_file() and key.is_file()
            self.src = _both_hands_source()(port=self.port, cert=str(cert) if have else None,
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
        """그 손의 최신 25관절(0.5 s 넘게 안 오면 None — 추적 끊김)."""
        if self.src is None:
            return None
        e = self.src.both.get(side)
        if e is None or time.monotonic() - e[0] > STALE_S:
            return None
        return e[1]


STALE_S = 0.5


def _both_hands_source():
    """robo-synapse `WebXRHandSource` + 양손 저장. 원본은 control_hand 한 손의 프레임만 남기고
    다른 손은 클러치로만 쓰므로, 같은 페이로드에서 양손 25관절을 따로 보관한다(robo-synapse 무수정)."""
    from robosynapse.common.teleop.vr_vuer import joint_matrices
    from robosynapse.common.teleop.webxr_source import WebXRHandSource

    class BothHands(WebXRHandSource):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.both = {}                     # side -> (monotonic t, kp (25,3))

        def _on_hands(self, v):
            super()._on_hands(v)
            now = time.monotonic()
            for side in ("right", "left"):
                m = joint_matrices(v.get(side))
                if m is not None:
                    self.both[side] = (now, np.array(m[:, :3, 3], float))

    return BothHands


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
REAL = RealHand()                 # 실물 DG-5F 읽기 전용 미러 (프로세스 공용, 참조 카운트)


SIDES = {"right": ("right",), "left": ("left",), "both": ("right", "left")}


def mirror_kp(kp):
    """오른손 키포인트 ↔ 왼손 키포인트 (x 반전 = 거울상). 합성 손을 왼손으로 쓸 때."""
    out = np.array(kp, float, copy=True)
    out[:, 0] *= -1.0
    return out


class Lane:
    """사람 손 하나 → 로봇 손 하나. 왼손은 DexPilot 이 손바닥 평면 반사로 오른손 로봇 문제로 풀고,
    화면은 오른손 URDF 를 거울로 뒤집어 왼손으로 그린다(DG-5F LEFT = RIGHT 의 거울상)."""

    def __init__(self, side: str):
        self.side = side
        self.rt = None
        self.tm = None
        self.ergo = None                       # 최근 Manus ergonomics (Tesollo 이름)

    def reset(self):
        if self.rt is not None:
            self.rt.reset()
        if self.tm is not None:
            self.tm.reset()


class Session:
    def __init__(self, live: Live):
        self.live = live
        self._real_on = False
        self._manus_on = False
        self.sides = "right"                   # 사람 손: "right" | "left" | "both"
        self.lanes: dict[str, Lane] = {}
        self.hand = "dg5f"
        self.knobs: dict = {}
        self.locks: dict = {}
        self.source = {"kind": "preset", "name": "open"}
        self.backend = "dexpilot"              # 메인 손: "dexpilot" | "tesollo"
        self.compare = False                   # 다른 방식 손을 옆에 같이(한 손 모드만)
        self.err = ""
        self.rec: list | None = None           # [(t, kp_r|NaN, kp_l|NaN)] 녹화 중이면 list
        self.replay = None                     # (t[], {side: kp[]}, t0)
        self.rebuild()

    def rebuild(self):
        lanes = {}
        try:
            for side in SIDES.get(self.sides, ("right",)):
                ln = Lane(side)
                ln.rt = hands.build(self.hand, self.knobs, self.locks, control_hand=side)
                if self.hand == "dg5f":
                    lim = {j: (min(a, b), max(a, b))
                           for _d, j, a, b in hands.dof_space(self.hand)[1]}
                    ln.tm = TesolloMap(lim, calib=bool(self.knobs.get("tesollo_calib", True)),
                                       thumb_in_off=float(self.knobs.get("thumb_in_off", 0.0)),
                                       thumb_elev0=float(self.knobs.get("thumb_elev0", 15.0)),
                                       filter=(self.knobs.get("filter")
                                               or hands.DEFAULT_KNOBS["filter"]),
                                       side=side)
                lanes[side] = ln
            self.err = ""
        except Exception as e:                 # 설정 오류는 화면에 띄우고 레인 없음
            lanes = {}
            self.err = f"{type(e).__name__}: {e}"
        self.lanes = lanes

    # --- input: {side: kp (25,3) | None}
    def keypoints(self) -> dict:
        s, sides = self.source, list(self.lanes)
        if s["kind"] in ("preset", "synth"):
            if s["kind"] == "preset":
                base = synth.PRESETS[s.get("name", "open")]()
            else:
                p = s.get("params", {})
                base = synth.hand(curl=p.get("curl"), spread=p.get("spread"),
                                  thumb_lift_deg=float(p.get("thumb_lift", 20)),
                                  thumb_curl_deg=float(p.get("thumb_curl", 10)),
                                  thumb_swing_deg=float(p.get("thumb_swing", 0)))
            return {sd: (base if sd == "right" else mirror_kp(base)) for sd in sides}
        if s["kind"] == "live":
            kps = {sd: self.live.keypoints(sd) for sd in ("right", "left")}
            if self.rec is not None and any(v is not None for v in kps.values()):
                nan = np.full((25, 3), np.nan)
                self.rec.append((time.monotonic(),
                                 nan if kps["right"] is None else kps["right"].copy(),
                                 nan if kps["left"] is None else kps["left"].copy()))
            return {sd: kps[sd] for sd in sides}
        if s["kind"] == "manus":
            out = {}
            for sd in sides:
                r = MANUS.latest(sd)           # 그 손 장갑이 없으면 다른 손을 돌려준다 → 거른다
                ok = r is not None and r[2] == sd
                out[sd] = r[0] if ok else None
                self.lanes[sd].ergo = r[1] if ok else None
            return out
        if s["kind"] == "replay" and self.replay is not None:
            t, kps, t0 = self.replay
            el = (time.monotonic() - t0) % max(t[-1], 1e-3)
            i = int(np.searchsorted(t, el, side="right")) - 1
            out = {}
            for sd in sides:
                arr = kps.get(sd)
                v = None if arr is None else arr[i]
                out[sd] = None if v is None or not np.isfinite(v[1:]).all() else v
            return out
        return {sd: None for sd in sides}

    def set_manus(self, on: bool):
        if on and not self._manus_on:
            MANUS.acquire()
        elif not on and self._manus_on:
            MANUS.release()
        self._manus_on = on

    def set_real(self, on: bool):
        if on and not self._real_on:
            REAL.acquire()
        elif not on and self._real_on:
            REAL.release()
        self._real_on = on

    def real_frame(self, out: dict) -> dict:
        """실물 관절각(와이어°)을 **변환 없이** URDF 관절에 그대로 — 와이어°=URDF° 가정 검증."""
        from robosynapse.common.grippers.tesollo_dg5f import protocol as P
        out["real"] = REAL.snapshot()
        deg = REAL.deg
        if deg is None:
            out["nokp"] = True
            return out
        urdf = {j: info for j, info in hands.urdf_joints(hands.HANDS["dg5f"]["urdf"]).items()}
        rows = []
        for name, j, mlo, mhi in P.DOF_SPECS:
            v = deg[name]
            lo, hi = np.degrees(urdf[j]["lower"]), np.degrees(urdf[j]["upper"])
            mm = REAL.minmax.get(name)
            rows.append({"dof": name, "joint": j, "deg": v, "urdf": [lo, hi], "manual": [mlo, mhi],
                         "meas": mm and [mm[0], mm[1]],
                         "out_urdf": bool(v < lo - 2 or v > hi + 2),
                         "out_manual": bool(v < mlo - 2 or v > mhi + 2)})
        out.update({"q": {r["joint"]: float(np.radians(r["deg"])) for r in rows}, "real_rows": rows})
        return out

    # --- one tick
    def frame(self) -> dict:
        out = {"type": "frame", "hand": self.hand, "err": self.err, "sides": list(self.lanes),
               "live": self.live.status(), "rec": None if self.rec is None else len(self.rec)}
        if self.source.get("kind") == "real":
            return self.real_frame(out)
        if self.source.get("kind") == "manus":
            out["manus"] = MANUS.status()
        if not self.lanes:
            return out
        kps = self.keypoints()
        filtered = self.source["kind"] in ("live", "replay", "manus")
        compare = self.compare and len(self.lanes) == 1
        out["lanes"] = {sd: self.lane_frame(ln, kps.get(sd), filtered, compare)
                        for sd, ln in self.lanes.items()}
        return out

    def lane_frame(self, ln: Lane, kp, filtered: bool, compare: bool) -> dict:
        if kp is None:
            return {"nokp": True}
        rt = ln.rt
        names = [j for _, j, *_ in rt._map]
        t0 = time.perf_counter()
        q_dp = rt._step(kp, filtered=filtered)       # 항상 돈다(warm start·투영 상태 유지)
        ms = (time.perf_counter() - t0) * 1e3
        if q_dp is None:
            return {"nokp": True}
        out = {}
        q_t = None
        if ln.tm is not None:
            d = ln.tm(kp, filtered=filtered, ergo=ln.ergo)
            if d is not None and self.knobs.get("tesollo_thumb_dp", True):
                # 엄지는 DexPilot 해를 쓴다: 각도 대응만으로는 대립(손바닥 가로지르기)이 안 나온다
                # (Quest 에서 대립 중 손바닥 평면 안 벌림각이 0 근처로 무너져 thumb1 이 작아짐)
                for n, v in zip(names, q_dp):
                    if n.startswith("rj_dg_1_"):
                        d[n] = float(v)
            q_t = None if d is None else np.array([d[n] for n in names])
            out["ergo"] = {k: round(v, 1) for k, v in ln.tm.ergo.items()}
        use_t = self.backend == "tesollo" and q_t is not None
        q = q_t if use_t else q_dp
        other = q_dp if use_t else q_t
        out["backend"] = "tesollo" if use_t else "dexpilot"
        if compare and other is not None:
            out["q_ghost"] = {n: float(v) for n, v in zip(names, other)}
            out["ghost_backend"] = "dexpilot" if use_t else "tesollo"
        P, _ = rt.chain.fk(q, jac=False)
        tips_r = P[:rt._n_tip]
        targets = rt.to_robot(kp)                                    # [root, tips…]
        # 사람 손 전체(25점)를 같은 손 프레임으로 — 표시용(뼈길이 정규화 전, 손 단위 scale).
        # 왼손은 _to_hand_frame 이 반사해 오른손 로봇 프레임에 놓는다(화면이 거울로 되돌림).
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
            sides = m.get("sides", self.sides)
            self.sides = sides if sides in SIDES else "right"
            self.rebuild()
        elif t == "source":
            self.source = {k: v for k, v in m.items() if k != "type"}
            self.set_real(self.source["kind"] == "real")
            self.set_manus(self.source["kind"] == "manus")
            if self.source["kind"] == "live":
                for ln in self.lanes.values():
                    ln.reset()
                return {"type": "live", **self.live.start()}
            if self.source["kind"] == "replay":
                d = np.load(RECORDINGS / pathlib.Path(self.source["file"]).name)
                # 양손 녹화(kp_r/kp_l, 없는 손은 NaN) · 옛 녹화(kp = 오른손)
                kps = ({"right": d["kp_r"], "left": d["kp_l"]} if "kp_r" in d.files
                       else {"right": d["kp"]})
                self.replay = (d["t"] - d["t"][0], kps, time.monotonic())
            for ln in self.lanes.values():
                ln.reset()
        elif t == "record":
            if m.get("on"):
                self.rec = []
            elif self.rec is not None:
                RECORDINGS.mkdir(exist_ok=True)
                name = time.strftime("%y%m%d_%H%M%S") + ".npz"
                if self.rec:
                    kp_r = np.stack([r[1] for r in self.rec])
                    np.savez(RECORDINGS / name, t=np.array([r[0] for r in self.rec]),
                             kp_r=kp_r, kp_l=np.stack([r[2] for r in self.rec]),
                             kp=kp_r)          # 옛 형식 호환(오른손)
                n, self.rec = len(self.rec), None
                return {"type": "saved", "file": name if n else None, "frames": n}
        elif t == "real_reset":
            REAL.reset_minmax()
        elif t == "real_save":
            from robosynapse.common.grippers.tesollo_dg5f import protocol as P
            RECORDINGS.mkdir(exist_ok=True)
            name = "dg5f_ranges_" + time.strftime("%y%m%d_%H%M%S") + ".json"
            urdf = hands.urdf_joints(hands.HANDS["dg5f"]["urdf"])
            data = {"note": "실물 DG-5F 관절 범위 실측(손으로 끝까지 밀어 기록, 와이어°=URDF°)",
                    "via": REAL.via, "info": REAL.info,
                    "joints": {n: {"urdf_joint": j, "measured_deg": REAL.minmax.get(n),
                                   "manual_deg": [lo, hi],
                                   "urdf_deg": [round(float(np.degrees(urdf[j]["lower"])), 1),
                                                round(float(np.degrees(urdf[j]["upper"])), 1)]}
                               for n, j, lo, hi in P.DOF_SPECS}}
            (RECORDINGS / name).write_text(json.dumps(data, ensure_ascii=False, indent=1))
            return {"type": "real_saved", "file": str(RECORDINGS / name)}
        elif t == "reset":
            for ln in self.lanes.values():
                ln.reset()
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
            sess.set_real(False)          # 창을 닫으면 실물 연결도 놓는다
            sess.set_manus(False)
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
