"""Manus 장갑 캘리브레이션 — 단계를 순서대로 강제하고, 단계마다 결과를 찍고, 실패하면 그 자리에서 다시 한다.

SDK 예제(sdk_client.py `C`)와 같은 SDK 함수(CoreSdk_GloveCalibration*)를 쓰지만,
예제는 화면에 마지막 상태만 남겨 어느 단계가 빠졌는지 알 수 없고(Finish 가 "not all steps have been
finished" 로 거부), P 로 실행 없이 넘어갈 수 있다. 여기서는:
  * 1 → 마지막 단계를 순서대로만 진행(건너뛰기 없음),
  * 단계마다 Enter → 기록(손 고정) → 성공/실패 출력, 실패면 같은 단계를 다시,
  * 전부 성공해야 Finish, 끝나면 SDK 를 정상 종료(설정 파일에 장갑별로 저장).

    python calibrate.py --side left          # 왼손 장갑 캘리브레이션
    python calibrate.py --side right --list  # 단계 설명만 보기(캘리브레이션 시작 안 함)

동글을 쓰는 다른 Manus 프로그램(sdk_client·브리지·뷰어·텔레옵)은 끄고 실행할 것.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sdk_reader import SDK_PY  # noqa: E402  (SDK 위치 자동 탐색 재사용)

# SDK 가 주는 영어 제목 → 한국어 풀이 (모르는 제목은 영어 그대로)
KO = {
    "Place your hand on a horizontal non-metal surface":
        "금속이 아닌 평평한 책상에 손바닥을 대고 올림. 엄지는 옆으로 벌리고 나머지 네 손가락은 붙여서 쭉 폄",
    "Make a fist": "손을 들고 주먹. 엄지는 주먹 옆면에 붙임",
    "Pinch your thumb and little finger": "엄지 끝과 새끼손가락 끝을 쌀알 집듯 가볍게 맞대고 정지",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", choices=("left", "right"), required=True)
    ap.add_argument("--list", action="store_true", help="단계 설명만 출력하고 끝(캘리브레이션 시작 안 함)")
    ap.add_argument("--wait", type=float, default=15.0, help="장갑을 기다리는 시간 [s]")
    a = ap.parse_args()

    sys.path.insert(0, SDK_PY)
    from manus_sdk import ManusSDK, ffi, lib
    from manus_sdk.generated._enums import AxisPolarity, AxisView, SDKReturnCode, Side

    found = {}                                    # glove id → "left"/"right"

    def on_raw(si):
        for i in range(si.skeletonsCount):
            info = ffi.new("RawSkeletonInfo*")
            if lib.CoreSdk_GetRawSkeletonInfo(i, info) != 0 or info.gloveId in found:
                continue
            ni = ffi.new("NodeInfo[]", info.nodesCount)
            if lib.CoreSdk_GetRawSkeletonNodeInfoArray(info.gloveId, ni, info.nodesCount) == 0:
                found[info.gloveId] = "left" if Side(ni[0].side).name == "Left" else "right"

    sdk = ManusSDK()
    r = lib.CoreSdk_InitializeIntegrated()
    if r != SDKReturnCode.Success:
        sys.exit(f"SDK 초기화 실패: {SDKReturnCode(r).name} — 다른 Manus 프로그램이 동글을 쓰고 있지 않은지 확인")
    try:
        sdk.register_raw_skeleton_callback(on_raw)
        cs = ffi.new("CoordinateSystemVUH*")     # 연결 전에 필수 — 빠지면 동글이 안 잡힌다
        cs.view, cs.up, cs.handedness, cs.unitScale = (AxisView.XFromViewer, AxisPolarity.PositiveZ,
                                                       Side.Right, 1.0)
        lib.CoreSdk_InitializeCoordinateSystemWithVUH(cs[0], True)
        for _ in range(10):
            if lib.CoreSdk_LookForHosts(1, False) == 0:
                n = ffi.new("uint32_t*")
                lib.CoreSdk_GetNumberOfAvailableHostsFound(n)
                if n[0]:
                    h = ffi.new("ManusHost[]", n[0])
                    lib.CoreSdk_GetAvailableHostsFound(h, n[0])
                    if lib.CoreSdk_ConnectToHost(h[0]) == 0:
                        break
            time.sleep(1)
        t0 = time.time()
        while a.side not in found.values() and time.time() - t0 < a.wait:
            time.sleep(0.3)
        gid = next((g for g, s in found.items() if s == a.side), None)
        if gid is None:
            sys.exit(f"{a.side} 장갑이 {a.wait:.0f}초 안에 안 잡힘 (전원·배터리·거리 확인). 보인 장갑: "
                     + (", ".join(f"{g:#x}({s})" for g, s in found.items()) or "없음"))

        ca = ffi.new("GloveCalibrationArgs*"); ca.gloveId = gid
        n = ffi.new("uint32_t*")
        if lib.CoreSdk_GloveCalibrationGetNumberOfSteps(ca[0], n) != 0 or n[0] == 0:
            sys.exit("캘리브레이션 단계 수를 못 받음")
        steps = []
        for k in range(n[0]):
            sa = ffi.new("GloveCalibrationStepArgs*"); sa.gloveId = gid; sa.stepIndex = k
            sd = ffi.new("GloveCalibrationStepData*")
            lib.CoreSdk_GloveCalibrationGetStepData(sa[0], sd)
            steps.append((ffi.string(sd.title).decode(), ffi.string(sd.description).decode(), sd.time))
        print(f"\n장갑 {gid:#x} ({a.side}) — 캘리브레이션 {len(steps)}단계")
        for k, (t, d, s) in enumerate(steps, 1):
            print(f"  {k}. {t}\n     {d}" + (f"\n     → {KO[t]}" if t in KO else "")
                  + (f"\n     (약 {abs(s):.0f}초)" if s else ""))
        if a.list:
            return

        ok = ffi.new("bool*")
        if lib.CoreSdk_GloveCalibrationStart(ca[0], ok) != 0 or not ok[0]:
            sys.exit("캘리브레이션 시작 실패")
        print("\n단계마다: 자세를 잡고 Enter → 기록이 끝날 때까지 손을 고정. (q + Enter = 취소)\n")
        for k, (t, d, s) in enumerate(steps):
            while True:
                print(f"[{k + 1}/{len(steps)}] {t}\n        {d}" + (f"\n        → {KO[t]}" if t in KO else ""))
                if input("        자세를 잡았으면 Enter: ").strip().lower() == "q":
                    lib.CoreSdk_GloveCalibrationStop(ca[0], ok)
                    sys.exit("취소함 — 저장 안 됨")
                print("        기록 중… 움직이지 마세요", flush=True)
                sa = ffi.new("GloveCalibrationStepArgs*"); sa.gloveId = gid; sa.stepIndex = k
                r = lib.CoreSdk_GloveCalibrationStartStep(sa[0], ok)
                if r == 0 and ok[0]:
                    print(f"        ✓ {k + 1}단계 완료\n")
                    break
                print(f"        ✗ {k + 1}단계 실패({SDKReturnCode(r).name}, result={bool(ok[0])}) — 같은 단계를 다시 합니다\n")
        if lib.CoreSdk_GloveCalibrationFinish(ca[0], ok) == 0 and ok[0]:
            print(f"✓ 캘리브레이션 완료·적용: 장갑 {gid:#x} ({a.side}). 종료하면 설정 파일에 저장됩니다.")
        else:
            print("✗ Finish 실패 — 위 SDK 로그를 확인하세요")
    finally:
        lib.CoreSdk_ShutDown()                  # 정상 종료 = 설정 파일에 장갑별 캘리브레이션 기록


if __name__ == "__main__":
    main()
