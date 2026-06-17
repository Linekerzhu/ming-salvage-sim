"""零-LLM 平衡/涌现验证：把权阉之势推高，跑长局，观察宫斗阴谋/阉祸/监军是否触发且平衡。

两种政策对比：
  - rely 倚阉到底（代批红scheming + 阉祸危机倚阉 + 冤陷准 + 封疆遣监军）→ 测黑暗路是否失控/螺旋
  - curb 亲政抑阉（收回代批红 + 阉祸purge + 冤陷protect）→ 测权阉是否回落、game 是否恢复

跑法：python -m ming_sim.cli.validate_intrigue
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict

from ming_sim import court_events, eunuch_power, intrigue, timeflow
from ming_sim.db import GameDB
from ming_sim.upgrade_schema import KV_CURRENT_DAY, KV_RISK_AVERSION, KV_SHI, kv_int

DAYS_PER_MONTH = 30


def _engaged_emperor(db, state):
    """勤政：清御案（弹章发部议、其余准），免死亡螺旋的奏疏洪流干扰。"""
    from ming_sim.memorials import attention_left, decide_memorial
    day = kv_int(db, KV_CURRENT_DAY, 0)
    rows = db.conn.execute("SELECT id, kind FROM memorials WHERE status='pending' ORDER BY arrived_day").fetchall()
    for r in rows:
        if attention_left(db) <= 1:
            break
        act = "refer" if str(r["kind"]) == "弹章" else "approve"
        try:
            decide_memorial(db, state, int(r["id"]), act, day=day)
        except Exception:
            pass


def _resolve_pending(db, state, policy: str, day: int, tally: Dict[str, int]) -> None:
    p = court_events.get_pending(db)
    if not p:
        return
    did = str(p.get("id"))
    tally[f"decision:{did}"] = tally.get(f"decision:{did}", 0) + 1
    choice = {
        "rely": {"eunuch_crisis": "rely", "eunuch_frame": "approve", "warlord": "supervise",
                 "loyal_slandered": "shelve", "rival_feud": "both", "faction_packing": "appease"},
        "curb": {"eunuch_crisis": "purge", "eunuch_frame": "protect", "warlord": "curb",
                 "loyal_slandered": "protect", "rival_feud": "both", "faction_packing": "curb"},
    }[policy].get(did)
    if choice is None:  # 动态选项（如 succession）：取第一个
        d = next((x for x in court_events._defs() if x["id"] == did), None)
        ch = court_events._choices_of(d, p.get("ctx") or {}) if d else []
        choice = ch[0]["key"] if ch else None
    if choice:
        r = court_events.resolve_decision(db, state, choice, day)
        if r.get("ok"):
            tally[f"choice:{did}:{choice}"] = tally.get(f"choice:{did}:{choice}", 0) + 1


def run_policy(policy: str, months: int = 36) -> None:
    tmp = tempfile.mkdtemp()
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    # 推高权阉：开代批红（默认掌印王体乾=scheming）+ 起步即权阉炽盛，速入阉祸场景
    from ming_sim.upgrade_schema import kv_set_int
    eunuch_power.set_daipihong(db, True, day=0)
    kv_set_int(db, eunuch_power.KV_EUNUCH_POWER, 70)

    tally: Dict[str, int] = {}
    traj = []
    bad = []

    def _snap(label):
        shi = kv_int(db, KV_SHI, 55)
        ra = kv_int(db, KV_RISK_AVERSION, 40)
        power = eunuch_power.get_eunuch_power(db)
        known = db.conn.execute("SELECT COUNT(*) c FROM secrets WHERE known_to_crown=1").fetchone()["c"]
        jailed = db.conn.execute("SELECT COUNT(*) c FROM characters WHERE status='imprisoned' AND power_id='ming'").fetchone()["c"]
        traj.append((label, shi, ra, power, known, jailed,
                     int(state.metrics.get("民心", 0)), int(state.metrics.get("国库", 0))))
        if not (0 <= shi <= 100): bad.append(f"M{label} 势越界 {shi}")
        if not (0 <= ra <= 100): bad.append(f"M{label} RA越界 {ra}")
        if int(state.metrics.get("民心", 0)) < 0: bad.append(f"M{label} 民心<0")

    last_turn = state.turn
    for _ in range(months * DAYS_PER_MONTH + months):  # 留余量给红事件停顿
        res = timeflow.advance_days(db, state, 1, stop_on_yellow=False)
        for rep in res["reports"]:
            for ev in rep["events"]:
                k = str(ev.get("kind") or "")
                if k in ("changwei_probe", "supervisor_smear", "eunuch_power", "eunuch_recruit",
                         "eunuch_reincarnation", "duishi_form", "duishi_scandal", "daipihong"):
                    tally[k] = tally.get(k, 0) + 1
        day = kv_int(db, KV_CURRENT_DAY, 0)
        if court_events.get_pending(db):
            _resolve_pending(db, state, policy, day, tally)
            continue
        _engaged_emperor(db, state)
        if state.turn != last_turn:    # 跨月 → 快照
            last_turn = state.turn
            _snap(state.turn - 1)
            if len(traj) >= months:
                break

    print(f"\n{'='*64}\n政策：{policy}（代批红scheming + 阉祸/冤陷/封疆按策；起步权阉70）\n{'='*64}")
    print(f"{'月':>3} {'势':>4} {'RA':>4} {'权阉':>4} {'把柄':>4} {'下狱':>4} {'民心':>4} {'国库':>5}")
    for row in traj[::3] + [traj[-1]]:
        print(f"{row[0]:>3} {row[1]:>4} {row[2]:>4} {row[3]:>4} {row[4]:>4} {row[5]:>4} {row[6]:>4} {row[7]:>5}")
    # 死亡螺旋检测：末月势是否钉0 / RA是否钉100
    last = traj[-1]
    spiral = (last[1] <= 2 and last[2] >= 98)
    print("\n涌现事件计数：", {k: v for k, v in sorted(tally.items()) if not k.startswith("decision:") and not k.startswith("choice:")})
    print("抉择触发：", {k: v for k, v in sorted(tally.items()) if k.startswith("decision:") or k.startswith("choice:")})
    print(f"末月：势{last[1]} RA{last[2]} 权阉{last[3]} 把柄{last[4]} 下狱{last[5]} 民心{last[6]} 国库{last[7]}")
    print(f"死亡螺旋：{'⚠️ 是' if spiral else '✓ 否'}　不变式破坏：{bad if bad else '✓ 无'}")


def main(argv=None) -> int:
    run_policy("rely")
    run_policy("curb")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
