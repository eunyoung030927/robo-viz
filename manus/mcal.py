"""Manus 장갑 캘리브레이션(.mcal) 파일 관리 — 이름 붙여 저장해 두고, 쓸 때 골라 불러온다.

.mcal = 장갑 하나의 캘리브레이션 (Manus SDK `CoreSdk_GetGloveCalibration` 바이트 그대로; Windows MANUS Core
에서 내보낸 것과 같은 형식. Metaglove ↔ Metaglove Pro 사이는 호환 안 됨).

    python mcal.py list                                   # 저장된 파일
    python mcal.py save --side right --name eun           # 지금 장갑에 적용된 캘리브레이션 → <dir>/eun_right.mcal
    python mcal.py load --side right --name hscho         # <dir>/hscho_right.mcal → 오른손 장갑에 적용
    python mcal.py load --side right --file /path/x.mcal  # 임의 경로의 파일 적용

저장 폴더: `MANUS_MCAL_DIR`, 없으면 `~/.config/Manus/mcal` (bs1 컨테이너에선 /workspace/manus_config/mcal).
SDK 위치는 자동으로 찾는다(MANUS_SDK_PY → /workspace/manus_sdk/Python → ~/manus_sdk/Python …).
bs1 컨테이너: `source /workspace/dexverse_env.sh` (cffi 있는 환경) 후 `python /workspace/robo-viz/manus/mcal.py …`
save/load 는 동글을 직접 잡는다 → 브리지·텔레옵·sdk_client 등 다른 Manus 프로그램을 끄고 실행할 것.
load 로 적용한 값은 SDK 설정 파일(장갑별)에도 저장돼, 이후엔 그 장갑에 자동으로 쓰인다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.environ.get("MANUS_MCAL_DIR") or os.path.expanduser("~/.config/Manus/mcal")


def _path(args) -> str:
    if getattr(args, "file", None):
        return os.path.abspath(args.file)
    if not args.name:
        sys.exit("--name 또는 --file 이 필요합니다")
    return os.path.join(args.dir, f"{args.name}_{args.side}.mcal")


def cmd_list(args):
    if not os.path.isdir(args.dir):
        print(f"(없음) {args.dir}")
        return
    rows = sorted(f for f in os.listdir(args.dir) if f.endswith(".mcal"))
    print(f"{args.dir}  ({len(rows)}개)")
    for f in rows:
        p = os.path.join(args.dir, f)
        meta = {}
        try:
            with open(p[:-5] + ".json") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            pass
        t = dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")
        extra = f"  glove {meta['glove_id']}" if "glove_id" in meta else ""
        note = f"  — {meta['note']}" if meta.get("note") else ""
        print(f"  {f:32s} {os.path.getsize(p):7d} B  {t}{extra}{note}")


def _open_glove(side: str, timeout: float):
    """SDK(Integrated) 시작 → `side` 장갑이 데이터를 보낼 때까지 기다려 (lib, ffi, glove_id)."""
    sys.path.insert(0, HERE)
    from sdk_reader import MANUS
    MANUS.acquire()
    t0 = time.time()
    while time.time() - t0 < timeout:
        if MANUS.err and "초기화 실패" in MANUS.err:
            sys.exit(f"[mcal] {MANUS.err}")
        with MANUS._lock:
            ids = [gid for gid, g in MANUS.gloves.items() if g.get("side") == side and g.get("kp") is not None]
        if ids:
            return MANUS._lib, MANUS._ffi, ids[0]
        time.sleep(0.2)
    sys.exit(f"[mcal] {side} 장갑이 {timeout:.0f}초 안에 안 보임 — 장갑 전원, 다른 Manus 프로그램(브리지 등) 종료 확인 "
             f"(SDK 상태: {MANUS.status()})")


def cmd_save(args):
    out = _path(args)
    if os.path.exists(out) and not args.force:
        sys.exit(f"[mcal] 이미 있음: {out} (덮어쓰려면 --force)")
    lib, ffi, gid = _open_glove(args.side, args.timeout)
    size = ffi.new("uint32_t*")
    r = lib.CoreSdk_GetGloveCalibrationSize(gid, size)
    if r != 0 or size[0] == 0:
        sys.exit(f"[mcal] 캘리브레이션 크기 읽기 실패 (code {r}, size {size[0]})")
    buf = ffi.new(f"unsigned char[{size[0]}]")
    r = lib.CoreSdk_GetGloveCalibration(buf, size[0])
    if r != 0:
        sys.exit(f"[mcal] 캘리브레이션 읽기 실패 (code {r})")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(bytes(ffi.buffer(buf, size[0])))
    with open(out[:-5] + ".json", "w") as f:
        json.dump({"glove_id": f"{gid:#x}", "side": args.side, "saved": dt.datetime.now().isoformat(timespec="seconds"),
                   "bytes": int(size[0]), "note": args.note or ""}, f, ensure_ascii=False, indent=1)
    print(f"[mcal] 저장: {out} ({size[0]} B, glove {gid:#x})")


def cmd_load(args):
    src = _path(args)
    if not os.path.isfile(src):
        sys.exit(f"[mcal] 파일 없음: {src}  (python mcal.py list 로 확인)")
    with open(src, "rb") as f:
        data = f.read()
    lib, ffi, gid = _open_glove(args.side, args.timeout)
    from manus_sdk.generated._enums import SetGloveCalibrationReturnCode
    buf = ffi.new(f"unsigned char[{len(data)}]")
    ffi.memmove(buf, data, len(data))
    res = ffi.new("SetGloveCalibrationReturnCode*")
    r = lib.CoreSdk_SetGloveCalibration(gid, buf, len(data), res)
    code = SetGloveCalibrationReturnCode(res[0]).name
    if r != 0 or code != "Success":
        sys.exit(f"[mcal] 적용 실패: SDK code {r}, result {code} "
                 f"(WrongSideError=손이 다름, VersionError/DeserializationError=다른 장갑 종류·버전 파일)")
    time.sleep(1.0)                      # 설정 파일 기록 여유
    print(f"[mcal] 적용: {src} → {args.side} 장갑 {gid:#x}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", default=DEFAULT_DIR, help=f"저장 폴더 (기본 {DEFAULT_DIR})")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name in ("save", "load"):
        p = sub.add_parser(name)
        p.add_argument("--side", choices=("left", "right"), required=True)
        p.add_argument("--name", help="이름 → <dir>/<name>_<side>.mcal")
        p.add_argument("--file", help="(load) 임의 경로의 .mcal / (save) 저장 경로 직접 지정")
        p.add_argument("--timeout", type=float, default=15.0, help="장갑을 기다릴 시간(초)")
        if name == "save":
            p.add_argument("--force", action="store_true", help="같은 이름 덮어쓰기")
            p.add_argument("--note", default="", help="메모(누구 손, 무슨 상황 등)")
    args = ap.parse_args()
    {"list": cmd_list, "save": cmd_save, "load": cmd_load}[args.cmd](args)


if __name__ == "__main__":
    main()
