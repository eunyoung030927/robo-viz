"""손별 리타겟팅 설정 + robo-synapse `DexPilotRetarget` 생성.

알고리즘은 robo-synapse 것을 **import 해서 그대로** 쓴다(복사·재구현 금지) — 웹에서 본
결과가 실기에서 도는 코드의 결과와 같아야 검증 의미가 있다.

DOF 공간:
  * dg5f   — 와이어°↔URDF rad 실측표가 아직 없다. dexpilot 은 URDF 공간에서 풀고 와이어 변환은
             마지막 단계뿐이라, 여기서는 DOF = URDF 관절 이름, 범위 = URDF 한계(°)인
             **항등 매핑**으로 돌린다. ⚠ URDF 한계 ≠ 실물 한계(thumb1·little2 등) — 결과는
             "URDF 한계 기준".
  * rh5dg2 — 실기와 같은 경로: 드라이버 와이어 한계(protocol.DOF_SPECS) + model.WIRE2URDF.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

from robosynapse.common.grippers.inspire_rh5dg2 import model as rh5dg2_model
from robosynapse.common.grippers.inspire_rh5dg2 import protocol as rh5dg2_protocol
from robosynapse.common.grippers.tesollo_dg5f import protocol as dg5f_protocol
from robosynapse.common.teleop.dexpilot_retarget import DexPilotRetarget

from prior import PriorDexPilot, dg5f_human_angles, dg5f_joint_finger

# profile.teleop.dex.dexpilot 와 같은 키. rh5dg2 값은 robots/ur7/profile.py 그대로.
HANDS = {
    "dg5f": {
        "label": "Tesollo DG-5F (20-DOF, URDF 한계)",
        "desc_dir": dg5f_protocol.DESCRIPTION_DIR,
        "urdf": dg5f_protocol.DESCRIPTION_URDF,
        "base_link": "rl_dg_palm",
        "tip_links": {"thumb": "rl_dg_1_tip", "index": "rl_dg_2_tip", "middle": "rl_dg_3_tip",
                      "ring": "rl_dg_4_tip", "little": "rl_dg_5_tip"},
        # MCP 굽힘 관절의 child 링크. 새끼는 1=손바닥 접힘, 2=벌림이라 굽힘이 3번부터.
        "knuckle_links": {"index": "rl_dg_2_2", "middle": "rl_dg_3_2", "ring": "rl_dg_4_2",
                          "little": "rl_dg_5_3"},
        "wire_names": {j: n for n, j, _lo, _hi in dg5f_protocol.DOF_SPECS},   # urdf joint → thumb1…
        # 굽힘 3관절 독립 구동 → 사람 관절각 사전항이 의미 있음 (prior.py)
        "angle_fn": dg5f_human_angles,
        "finger_of": dg5f_joint_finger,
    },
    "rh5dg2": {
        "label": "Inspire RH5DG2 (13-DOF, 실기 매핑)",
        "desc_dir": rh5dg2_model.DESCRIPTION_DIR,
        "urdf": rh5dg2_model.DESCRIPTION_DIR / "rh5dg2_right.urdf",     # 손만(팔 없는) 파일
        "base_link": "right_hand_base",
        "tip_links": {"thumb": "right_thumb_force_sensor", "index": "right_index_force_sensor",
                      "middle": "right_middle_force_sensor", "ring": "right_ring_force_sensor",
                      "little": "right_pinky_force_sensor"},
        "knuckle_links": {"index": "right_index_mcp", "middle": "right_middle_mcp",
                          "ring": "right_ring_mcp", "little": "right_pinky_mcp"},
    },
}

# profile.teleop.dex.dexpilot 기본(ur7) — UI 가 덮어쓴다.
DEFAULT_KNOBS = {"scale": 1.0, "bone_scale": True, "project_dist": 0.03, "escape_dist": 0.05,
                 "filter": {"min_cutoff": 1.0, "beta": 0.05, "d_cutoff": 1.0}, "angle_prior": 0.0,
                 "pinch_scale": 1.0}
KNOB_KEYS = ("scale", "bone_scale", "project_dist", "escape_dist", "eta1", "eta2",
             "huber_delta", "norm_delta", "filter", "out_smooth", "maxiter")
# robo-viz 전용(robo-synapse DexPilot 에 없는) 노브 — prior.py
EXTRA_KEYS = ("angle_prior", "pinch_scale")


def urdf_joints(urdf: Path) -> dict:
    """{joint: {"type", "lower", "upper", "mimic"}} (rad) — 단순 파서(뷰어 슬라이더·한계용)."""
    out = {}
    for j in ET.parse(urdf).getroot().findall("joint"):
        lim, mim = j.find("limit"), j.find("mimic")
        out[j.get("name")] = {
            "type": j.get("type"),
            "lower": float(lim.get("lower", 0)) if lim is not None else None,
            "upper": float(lim.get("upper", 0)) if lim is not None else None,
            "mimic": None if mim is None else {"joint": mim.get("joint"),
                                               "multiplier": float(mim.get("multiplier", 1)),
                                               "offset": float(mim.get("offset", 0))},
        }
    return out


def dof_space(hand: str, locks: dict | None = None):
    """(dof_limits {dof: (min°, max°)}, joint_map [(dof, urdf joint, rad@min, rad@max)]).

    `locks` = {urdf joint: rad} — 그 관절의 최적화 범위를 한 점으로 묶는다(고정 실험:
    새끼 손바닥 접힘·엄지 대립처럼 사람 손에 1:1 대응이 없는 관절)."""
    cfg = HANDS[hand]
    locks = locks or {}
    if hand == "rh5dg2":
        limits = {n: (lo / 10.0, hi / 10.0) for n, lo, hi in rh5dg2_protocol.DOF_SPECS}
        jmap = [tuple(r) for r in rh5dg2_model.WIRE2URDF]
    else:
        limits, jmap = {}, []
        for j, info in urdf_joints(cfg["urdf"]).items():
            if info["type"] != "revolute" or info["mimic"]:
                continue
            lo, hi = info["lower"], info["upper"]
            limits[j] = (math.degrees(lo), math.degrees(hi))
            jmap.append((j, j, lo, hi))
    if locks:
        by_joint = {r[1]: i for i, r in enumerate(jmap)}
        for j, v in locks.items():
            if j not in by_joint:
                raise ValueError(f"lock: 모르는 관절 {j!r}")
            d, jj, lo, hi = jmap[by_joint[j]]
            v = min(max(float(v), min(lo, hi)), max(lo, hi))
            jmap[by_joint[j]] = (d, jj, v, v)
    return limits, jmap


def build(hand: str, knobs: dict | None = None, locks: dict | None = None,
          control_hand: str = "right") -> DexPilotRetarget:
    """angle_prior = 0(기본) 또는 손에 angle_fn 이 없으면 robo-synapse DexPilotRetarget 그대로."""
    cfg = HANDS[hand]
    knobs = knobs or {}
    merged = {**DEFAULT_KNOBS, **knobs}
    k = {kk: v for kk, v in merged.items() if kk in KNOB_KEYS}
    limits, jmap = dof_space(hand, locks)
    common = dict(urdf=str(cfg["urdf"]), joint_map=jmap, base_link=cfg["base_link"],
                  tip_links=cfg["tip_links"], knuckle_links=cfg["knuckle_links"],
                  hand=control_hand, min_step_deg=0.0, **k)
    lam = float(merged.get("angle_prior") or 0.0)
    if lam > 0 and cfg.get("angle_fn") is not None:
        return PriorDexPilot(limits, angle_prior=lam, angle_fn=cfg["angle_fn"],
                             finger_of=cfg.get("finger_of"),
                             pinch_scale=float(merged.get("pinch_scale", 1.0)), **common)
    return DexPilotRetarget(limits, **common)
