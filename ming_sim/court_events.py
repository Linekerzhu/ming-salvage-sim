"""抉择事件（CK3 化 P2）：朝局演化到某种张力，弹出一道「请陛下裁断」的抉择——
2-4 个选项各有真实后果（势/任事/民心/派系/好感网涟漪），玩家从"被动看推演"变为"主动落子"。

事件**涌现自活的宫廷**：宿敌互讦、孤忠蒙谤、朋党盈廷——触发条件读 court 的好感网与派系状态，
后果亦回写好感网（ripple）。一次至多一道待决；同类事件 60 日内不重复（cooldown）。
"""

from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState
from ming_sim import court
from ming_sim.negotiation import HANDSHAKE_SEALED, promise_type_from_terms, stakes_from_terms
from ming_sim.upgrade_schema import (
    KV_RISK_AVERSION,
    KV_SHI,
    SHI_DEFAULT,
    adjust_belief,
    kv_int,
)

KV_PENDING = "upgrade.pending_decision"
KV_COOLDOWN = "upgrade.decision_cooldowns"
COOLDOWN_DAYS = 60


# ── 效果执行（声明式：选项后果是数据，统一落库）─────────────────────────────

def _create_obligation(db: GameDB, state: GameState, item: Dict[str, object], day: int) -> str:
    minister = str(item.get("minister") or "").strip()
    if not minister:
        return ""
    action_kind = str(item.get("action_kind") or "court_commitment").strip()[:40]
    title = str(item.get("title") or "御前待办").strip()[:120]
    target_text = str(item.get("target_text") or title).strip()[:240]
    tasks = [
        str(task or "").strip()[:180]
        for task in (item.get("tasks") or [])
        if str(task or "").strip()
    ][:6]
    conditions = str(item.get("conditions") or "；".join(tasks)).strip()[:400]
    due_turns = max(1, min(12, int(item.get("due_turns") or 3)))
    source = str(item.get("source") or f"court_event:{minister}:{title}").strip()[:120]

    existing = db.conn.execute(
        """
        SELECT id
        FROM conversation_goals
        WHERE minister_name=?
          AND status IN ('active', 'waiting_conditions')
          AND last_delta_json LIKE ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (minister, f"%{source}%"),
    ).fetchone()
    if existing is not None:
        return f"{minister}已有待办"

    last_delta = {
        "source": source,
        "day": int(day),
        "kind": "court_event_obligation",
        "tasks": tasks,
    }
    goal_id = db.create_conversation_goal(
        state,
        minister_name=minister,
        action_kind=action_kind,
        title=title,
        target_text=target_text,
        threshold=int(item.get("threshold") or 70),
        score=100,
        status="waiting_conditions",
        condition_status="pending",
        conditions=[{"description": task, "status": "pending"} for task in tasks],
        expires_turn=int(state.turn) + due_turns + 2,
        last_delta=last_delta,
    )
    if not goal_id:
        return ""
    agreement_id = db.create_negotiation_agreement(
        state,
        minister_name=minister,
        topic=title,
        action_kind=action_kind,
        status="pending",
        stance_id=0,
        handshake_status=HANDSHAKE_SEALED,
        psychological_score=100,
        threshold=int(item.get("threshold") or 70),
        verbal_only=False,
        core_topic=title,
        target_text=target_text,
        promise_type=str(item.get("promise_type") or promise_type_from_terms(action_kind, conditions, tasks)),
        stakes=str(item.get("stakes") or stakes_from_terms(action_kind, conditions, f"{title}\n{target_text}")),
        due_turn=int(state.turn) + due_turns,
        conditions=conditions,
        summary=str(item.get("summary") or f"{minister}奉旨领下待办：{title}")[:300],
        tasks=tasks,
        goal_id=goal_id,
    )
    db.update_conversation_goal(
        goal_id,
        state=state,
        event_kind="obligation_created",
        event_summary=f"{minister}因朝廷裁断负下待办：{title}",
        status="waiting_conditions",
        condition_status="pending",
        agreement_id=agreement_id,
        score=100,
        last_delta_json={**last_delta, "agreement_id": agreement_id},
    )
    return f"{minister}负约待办"


def _apply_agreement_action(db: GameDB, state: GameState, item: Dict[str, object], day: int) -> str:
    agreement_id = int(item.get("id") or item.get("agreement_id") or 0)
    if agreement_id <= 0:
        return ""
    row = db.conn.execute("SELECT * FROM negotiation_agreements WHERE id=?", (agreement_id,)).fetchone()
    if row is None:
        return ""
    agreement = dict(row)
    minister = str(agreement.get("minister_name") or "")
    topic = str(agreement.get("core_topic") or agreement.get("topic") or "履约事项")
    action = str(item.get("action") or "extend").strip()
    evidence = str(item.get("evidence") or "").strip()[:240]
    goal_id = int(agreement.get("goal_id") or 0)

    if action == "extend":
        months = max(1, min(12, int(item.get("months") or 1)))
        due_turn = int(state.turn) + months
        review = {
            "phase": "court_decision",
            "turn": int(state.turn),
            "status": "pending",
            "condition_status": "pending",
            "target_status": "pending_conditions",
            "condition_evidence": evidence or f"御前展限 {months} 月，仍待复命。",
            "llm_used": False,
        }
        db.conn.execute(
            """
            UPDATE negotiation_agreements
            SET status='pending', condition_status='pending', target_status='pending_conditions',
                due_turn=?, last_checked_turn=?, auto_review_json=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (due_turn, int(state.turn), json.dumps(review, ensure_ascii=False), agreement_id),
        )
        if goal_id:
            db.update_conversation_goal(
                goal_id,
                state=state,
                event_kind="agreement_extended",
                event_summary=evidence or f"{minister}「{topic}」奉旨展限 {months} 月。",
                status="waiting_conditions",
                condition_status="pending",
                expires_turn=due_turn + 2,
                last_delta_json={
                    "source": "overdue_obligation",
                    "action": "extend",
                    "due_turn": due_turn,
                    "evidence": evidence,
                },
            )
        db.record_log(state, evidence or f"{minister}「{topic}」奉旨展限 {months} 月。")
        return f"{minister}履约展限{months}月"

    if action == "fail":
        evidence = evidence or f"御前裁断：{minister}逾期未复命，履约失期。"
        tasks = db.conn.execute(
            "SELECT id, status FROM negotiation_tasks WHERE agreement_id=?",
            (agreement_id,),
        ).fetchall()
        for task in tasks:
            if str(task["status"] or "pending") == "pending":
                db.conn.execute(
                    """
                    UPDATE negotiation_tasks
                    SET status='failed', evidence=?, last_checked_turn=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (evidence, int(state.turn), int(task["id"])),
                )
        effect = db._apply_negotiation_political_effect(  # Centralized ledger consequence.
            state,
            agreement,
            new_status="failed",
            evidence=evidence,
        )
        review = {
            "phase": "court_decision",
            "turn": int(state.turn),
            "status": "failed",
            "condition_status": "failed",
            "target_status": "failed",
            "condition_score": 0,
            "condition_evidence": evidence,
            "target_evidence": evidence,
            "llm_used": False,
        }
        db.conn.execute(
            """
            UPDATE negotiation_agreements
            SET status='failed', condition_status='failed', target_status='failed',
                last_checked_turn=?, resolved_turn=?, fulfillment_score=0,
                fulfillment_evidence=?, target_evidence=?, political_effect_json=?,
                auto_review_json=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                int(state.turn),
                int(state.turn),
                evidence,
                evidence,
                json.dumps(effect, ensure_ascii=False),
                json.dumps(review, ensure_ascii=False),
                agreement_id,
            ),
        )
        if goal_id:
            db.update_conversation_goal(
                goal_id,
                state=state,
                event_kind="agreement_failed",
                event_summary=evidence,
                status="expired",
                condition_status="failed",
                last_delta_json={"source": "overdue_obligation", "action": "fail", "agreement_id": agreement_id},
            )
        return f"{minister}履约失期"

    return ""


def _intish(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _legacy_modifiers(row) -> Dict[str, object]:
    try:
        data = json.loads(str(row["modifiers"] or "{}"))
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _append_legacy_note(hint: str, note: str) -> str:
    hint = str(hint or "").strip()
    note = str(note or "").strip()
    if not note:
        return hint[:200]
    if note in hint:
        return hint[:200]
    if not hint:
        return note[:200]
    return f"{hint[:128]}；{note[:68]}"[:200]


def _apply_legacy_action(db: GameDB, state: GameState, item: Dict[str, object], day: int) -> str:
    legacy_id = _intish(item.get("id") or item.get("legacy_id"))
    if legacy_id <= 0:
        return ""
    row = db.conn.execute(
        "SELECT * FROM legacies WHERE id=? AND status='active'",
        (legacy_id,),
    ).fetchone()
    if row is None:
        return ""

    action = str(item.get("action") or "soften").strip()
    modifier = str(item.get("modifier") or "民心").strip()
    amount = max(1, min(30, _intish(item.get("amount"), 1)))
    modifiers = _legacy_modifiers(row)
    name = str(row["name"] or "旧政余波")
    current = _intish(modifiers.get(modifier), 0)
    label = str(item.get("label") or name).strip()
    note = str(item.get("note") or "").strip()

    if action == "soften":
        modifiers[modifier] = min(0, current + amount) if current < 0 else current + amount
        verb = "旧政缓和"
    elif action == "worsen":
        modifiers[modifier] = current - amount
        verb = "旧政加重"
    elif action == "extend":
        months = max(1, min(60, _intish(item.get("months"), 1)))
        duration = _intish(row["duration_months"])
        if duration >= 0:
            db.conn.execute(
                "UPDATE legacies SET duration_months=? WHERE id=?",
                (duration + months, legacy_id),
            )
        verb = "余波延长"
    else:
        return ""

    if action in {"soften", "worsen"}:
        db.conn.execute(
            """
            UPDATE legacies
            SET modifiers=?, narrative_hint=?
            WHERE id=?
            """,
            (
                json.dumps(modifiers, ensure_ascii=False),
                _append_legacy_note(str(row["narrative_hint"] or ""), note),
                legacy_id,
            ),
        )
    elif note:
        db.conn.execute(
            "UPDATE legacies SET narrative_hint=? WHERE id=?",
            (_append_legacy_note(str(row["narrative_hint"] or ""), note), legacy_id),
        )
    db._legacy_mod_cache = None
    try:
        db.record_log(state, f"【{verb}】{label}：{note or name}")
    except Exception:
        pass
    return f"{verb}：{label}"


def _apply_effect(db: GameDB, state: GameState, eff: Dict[str, object], day: int) -> str:
    parts: List[str] = []
    shi = int(eff.get("shi") or 0)
    if shi:
        adjust_belief(db, KV_SHI, shi, str(eff.get("log") or "抉择"), day=day)
        parts.append(f"君威{'+' if shi > 0 else ''}{shi}")
    renshi = int(eff.get("renshi") or 0)
    if renshi:  # 任事意愿↑ ⇔ 风险厌恶↓
        # 崇祯猜忌多疑 → 任事的负向波动被放大（崇祯陷阱加深），正向不变。
        if renshi < 0:
            try:
                from ming_sim.traits import emperor_renshi_amplifier
                renshi = int(round(renshi * emperor_renshi_amplifier(db)))
            except ImportError:
                pass
        adjust_belief(db, KV_RISK_AVERSION, -renshi, str(eff.get("log") or "抉择"), day=day)
        parts.append(f"任事{'+' if renshi > 0 else ''}{renshi}")
    for k, v in (eff.get("metrics") or {}).items():
        cur = int(state.metrics.get(k, 0))
        state.metrics[k] = max(0, min(100, cur + int(v))) if k in ("民心", "皇威") else cur + int(v)
        parts.append(f"{k}{'+' if int(v) > 0 else ''}{int(v)}")
    fac = eff.get("faction") or {}
    if fac:
        db.adjust_factions(fac)
        for fn, fv in fac.items():
            sd = int(fv.get("satisfaction") or 0) if isinstance(fv, dict) else 0
            if sd:
                parts.append(f"{fn}满意{'+' if sd > 0 else ''}{sd}")
            heat = int(fv.get("heat") or 0) if isinstance(fv, dict) else 0
            if heat:
                try:
                    from ming_sim.theater import adjust_faction_heat
                    adjust_faction_heat(db, str(fn), heat, str(eff.get("log") or "抉择"))
                except Exception:
                    pass
                parts.append(f"{fn}热度{'+' if heat > 0 else ''}{heat}")
    for op in (eff.get("opinion") or []):
        court.adjust_opinion(db, str(op["a"]), str(op["b"]), int(op["delta"]),
                             str(op.get("basis") or ""), day=day)
    for rp in (eff.get("ripple") or []):
        court.ripple_personnel(db, str(rp["name"]), str(rp.get("kind") or "oust"), day=day)
    for ch in (eff.get("char") or []):
        court._adjust_char(db, str(ch["name"]),
                           emp_trust=int(ch.get("emp_trust") or 0),
                           grievance=int(ch.get("grievance") or 0))
    for am in (eff.get("army") or []):
        sets, params = [], []
        if "autonomy" in am:
            sets.append("autonomy=MAX(0,MIN(100,autonomy+?))"); params.append(int(am["autonomy"]))
        if "loyalty" in am:
            sets.append("loyalty=MAX(0,MIN(100,loyalty+?))"); params.append(int(am["loyalty"]))
        if "arrears" in am:
            sets.append("arrears=MAX(0,arrears+?)"); params.append(int(am["arrears"]))
        if sets:
            db.conn.execute(f"UPDATE armies SET {', '.join(sets)} WHERE id=?", (*params, am["id"]))
    ap = eff.get("appoint")
    if ap and ap.get("name") and ap.get("office"):
        db.conn.execute("UPDATE characters SET office=? WHERE name=? AND status='active'",
                        (str(ap["office"]), str(ap["name"])))
        court.ripple_personnel(db, str(ap["name"]), "favor", day=day)
        parts.append(f"擢{ap['name']}")
    ep = int(eff.get("eunuch_power") or 0)
    if ep:
        try:
            from ming_sim.eunuch_power import adjust_eunuch_power
            adjust_eunuch_power(db, ep, str(eff.get("log") or "阉祸抉择"), day=day)
            parts.append(f"权阉{'+' if ep > 0 else ''}{ep}")
        except Exception:
            pass
    for st in (eff.get("status") or []):
        try:
            db.set_character_status(state, str(st["name"]), str(st["status"]), str(st.get("reason") or ""))
            if str(st["status"]) in ("dismissed", "imprisoned", "exiled", "dead"):
                court.ripple_personnel(db, str(st["name"]), "oust", day=day)
        except Exception:
            pass
    if eff.get("daipihong_off"):
        try:
            from ming_sim.eunuch_power import set_daipihong
            set_daipihong(db, False, day=day)
        except Exception:
            pass
    sv = eff.get("supervise")
    if sv and sv.get("army_id") and sv.get("eunuch"):
        try:
            from ming_sim.frontier import dispatch_supervisor
            dispatch_supervisor(db, state, str(sv["army_id"]), str(sv["eunuch"]), day)
            parts.append(f"遣{sv['eunuch']}监军")
        except Exception:
            pass
    for ob in (eff.get("obligations") or []):
        if isinstance(ob, dict):
            result = _create_obligation(db, state, ob, day)
            if result:
                parts.append(result)
    for ag in (eff.get("agreements") or []):
        if isinstance(ag, dict):
            result = _apply_agreement_action(db, state, ag, day)
            if result:
                parts.append(result)
    for lg in (eff.get("legacy") or []):
        if isinstance(lg, dict):
            result = _apply_legacy_action(db, state, lg, day)
            if result:
                parts.append(result)
    db.conn.commit()
    db.save_state(state)
    if eff.get("log"):
        db.record_log(state, str(eff["log"]))
    return "；".join(parts) if parts else "圣意已决"


def _signed(n: int) -> str:
    return f"+{n}" if n > 0 else str(n)


def _tone(delta: int, *, inverse: bool = False) -> str:
    if delta == 0:
        return "neutral"
    good = delta > 0
    if inverse:
        good = not good
    return "good" if good else "bad"


def _status_label(status: str) -> str:
    return {
        "dismissed": "罢黜",
        "imprisoned": "下狱",
        "exiled": "流放",
        "dead": "处死",
        "active": "复出",
    }.get(status, status)


def _preview_effects(eff: Dict[str, object]) -> List[Dict[str, str]]:
    """前端用：把声明式后果压成玩家可扫读的 CK3 式影响 chip。"""
    out: List[Dict[str, str]] = []

    def add(kind: str, label: str, tone: str = "neutral") -> None:
        if label:
            out.append({"kind": kind, "label": label, "tone": tone})

    shi = int(eff.get("shi") or 0)
    if shi:
        add("shi", f"君威 {_signed(shi)}", _tone(shi))
    renshi = int(eff.get("renshi") or 0)
    if renshi:
        add("renshi", f"任事 {_signed(renshi)}", _tone(renshi))
    ep = int(eff.get("eunuch_power") or 0)
    if ep:
        add("eunuch_power", f"权阉 {_signed(ep)}", _tone(ep, inverse=True))

    for k, v in (eff.get("metrics") or {}).items():
        delta = int(v)
        add("metric", f"{k} {_signed(delta)}", _tone(delta))

    for fn, fv in (eff.get("faction") or {}).items():
        if not isinstance(fv, dict):
            continue
        sd = int(fv.get("satisfaction") or 0)
        if sd:
            add("faction_satisfaction", f"{fn}满意 {_signed(sd)}", _tone(sd))
        lev = int(fv.get("leverage") or 0)
        if lev:
            add("faction_leverage", f"{fn}势力 {_signed(lev)}", _tone(lev, inverse=True))
        heat = int(fv.get("heat") or 0)
        if heat:
            add("faction_heat", f"{fn}热度 {_signed(heat)}", _tone(heat, inverse=True))

    for ch in (eff.get("char") or []):
        name = str(ch.get("name") or "")
        trust = int(ch.get("emp_trust") or 0)
        grievance = int(ch.get("grievance") or 0)
        if name and trust:
            add("trust", f"{name}信任 {_signed(trust)}", _tone(trust))
        if name and grievance:
            add("grievance", f"{name}怨望 {_signed(grievance)}", _tone(grievance, inverse=True))

    for st in (eff.get("status") or []):
        name = str(st.get("name") or "")
        status = str(st.get("status") or "")
        if name and status:
            good = status == "active"
            add("status", f"{_status_label(status)} {name}", "good" if good else "bad")

    for am in (eff.get("army") or []):
        autonomy = int(am.get("autonomy") or 0)
        loyalty = int(am.get("loyalty") or 0)
        arrears = int(am.get("arrears") or 0)
        if autonomy:
            add("army", f"军镇离心 {_signed(autonomy)}", _tone(autonomy, inverse=True))
        if loyalty:
            add("army", f"军心 {_signed(loyalty)}", _tone(loyalty))
        if arrears:
            add("army", f"欠饷 {_signed(arrears)}", _tone(arrears, inverse=True))

    ap = eff.get("appoint")
    if isinstance(ap, dict) and ap.get("name") and ap.get("office"):
        add("appoint", f"擢{ap['name']}补{ap['office']}", "good")
    sv = eff.get("supervise")
    if isinstance(sv, dict) and sv.get("eunuch"):
        add("supervise", f"遣{sv['eunuch']}监军", "neutral")
    if eff.get("daipihong_off"):
        add("daipihong", "停代批红", "good")
    for ob in (eff.get("obligations") or []):
        if isinstance(ob, dict):
            minister = str(ob.get("minister") or "").strip()
            if minister:
                add("obligation", f"履约账本：{minister}", "neutral")
    for ag in (eff.get("agreements") or []):
        if not isinstance(ag, dict):
            continue
        action = str(ag.get("action") or "")
        if action == "extend":
            add("agreement", f"履约展限 {int(ag.get('months') or 1)}月", "warn")
        elif action == "fail":
            add("agreement", "履约追责", "bad")
    for lg in (eff.get("legacy") or []):
        if not isinstance(lg, dict):
            continue
        action = str(lg.get("action") or "soften")
        label = str(lg.get("label") or lg.get("stem") or "旧政").strip()
        if action == "soften":
            add("legacy", f"旧政缓和：{label}", "good")
        elif action == "worsen":
            add("legacy", f"旧政加重：{label}", "bad")
        elif action == "extend":
            add("legacy", f"余波延长：{label}", "warn")

    limit = 10
    if len(out) > limit:
        obligations = [item for item in out if item.get("kind") == "obligation"]
        if obligations:
            room = max(0, limit - len(obligations))
            return [item for item in out if item.get("kind") != "obligation"][:room] + obligations[:limit]
    return out[:limit]


# ── 触发条件助手 ──────────────────────────────────────────────────────────────

def _deepest_rivalry(db: GameDB) -> Optional[Dict[str, object]]:
    """宿敌互讦：当一封弹章把两个宿敌（opinion ≤ -55）的积怨摆上台面时触发——
    抉择系于"事发"（弹章已上），而非开局的静态恩怨，故不会一推进就弹。"""
    row = db.conn.execute(
        "SELECT m.author_name AS a, m.ref_id AS b, r.opinion AS op, r.basis AS basis "
        "FROM memorials m "
        "JOIN relationships r ON r.a_name=m.author_name AND r.b_name=m.ref_id "
        "JOIN characters ca ON ca.name=m.author_name AND ca.status='active' AND ca.power_id='ming' "
        "JOIN characters cb ON cb.name=m.ref_id AND cb.status='active' AND cb.power_id='ming' "
        "WHERE m.kind='弹章' AND m.status='pending' AND m.ref_kind='character' "
        "AND r.opinion<=-55 ORDER BY r.opinion ASC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    a, b = str(row["a"]), str(row["b"])
    oa = db.conn.execute("SELECT office, faction FROM characters WHERE name=?", (a,)).fetchone()
    ob = db.conn.execute("SELECT office, faction FROM characters WHERE name=?", (b,)).fetchone()
    return {"a": a, "b": b, "basis": str(row["basis"] or "夙怨"),
            "a_office": str(oa["office"] or ""), "b_office": str(ob["office"] or ""),
            "a_faction": str(oa["faction"] or ""), "b_faction": str(ob["faction"] or "")}


def _slandered_loyal(db: GameDB) -> Optional[Dict[str, object]]:
    """高节高忠却被政敌弹劾的孤臣：有在朝政敌 + 近期一封针对其的弹章。"""
    row = db.conn.execute(
        "SELECT m.author_name AS accuser, m.ref_id AS victim, m.id AS mid "
        "FROM memorials m JOIN characters cv ON cv.name=m.ref_id "
        "WHERE m.kind='弹章' AND m.status='pending' AND m.ref_kind='character' "
        "AND cv.status='active' AND cv.power_id='ming' AND cv.integrity>=68 AND cv.loyalty>=65 "
        "ORDER BY m.id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    victim, accuser = str(row["victim"]), str(row["accuser"])
    vo = db.conn.execute("SELECT office FROM characters WHERE name=?", (victim,)).fetchone()
    return {"victim": victim, "accuser": accuser,
            "victim_office": str(vo["office"] or "") if vo else "", "mid": int(row["mid"])}


def _eunuch_crisis(db: GameDB) -> Optional[Dict[str, object]]:
    """阉祸危机（宦官恶趣味 E3）：权阉之势炽极（≥75）、主弱臣强——司礼监东厂权倾朝野，
    内外勾连、把柄在手、阉党盈廷。请陛下裁断：翦除？隐忍？抑或索性倚为腹心（天启故事）？"""
    try:
        from ming_sim.eunuch_power import get_eunuch_power
        power = get_eunuch_power(db)
    except Exception:
        return None
    if power < 75:
        return None
    try:
        from ming_sim.intrigue import dongchang_chief
        chief = dongchang_chief(db)
    except Exception:
        chief = None
    if chief is None:
        return None
    row = db.conn.execute("SELECT office FROM characters WHERE name=?", (chief,)).fetchone()
    # 阉党党羽数（在朝）——示其盘根
    packed = db.conn.execute(
        "SELECT COUNT(*) c FROM characters WHERE status='active' AND power_id='ming' "
        "AND faction='阉党'").fetchone()["c"]
    return {"eunuch": chief, "power": int(power), "shi": kv_int(db, KV_SHI, SHI_DEFAULT),
            "office": str(row["office"] or "") if row else "司礼监", "packed": int(packed)}


def _eunuch_frame(db: GameDB) -> Optional[Dict[str, object]]:
    """阉党自发冤陷东林（宫斗阴谋 P3·涌现版构陷）：权阉炽盛（≥60）、东厂在手，
    厂卫不待圣意、已锻炼一桩诏狱劾某清流——请陛下裁断：准其下狱？力保？抑或廷议核实？
    锻炼六君子式的阉祸场景。目标有真把柄则"师出有名"，无则纯属冤陷（民心损更重）。"""
    try:
        from ming_sim.eunuch_power import get_eunuch_power
        if get_eunuch_power(db) < 60:
            return None
    except Exception:
        return None
    try:
        from ming_sim.intrigue import dongchang_chief, secrets_for
    except Exception:
        return None
    chief = dongchang_chief(db)
    if chief is None:
        return None
    row = db.conn.execute(
        "SELECT name, office, integrity FROM characters WHERE status='active' AND power_id='ming' "
        "AND office_type!='后宫' AND (faction LIKE '%东林%' OR faction LIKE '%清流%' OR integrity>=70) "
        "AND faction NOT LIKE '%阉%' ORDER BY integrity DESC LIMIT 1").fetchone()
    if row is None:
        return None
    target = str(row["name"])
    known = secrets_for(db, target)
    return {"eunuch": chief, "target": target, "office": str(row["office"] or ""),
            "charge": (known["label"] if known else "结党乱政、谤讪朝廷"),
            "real": bool(known)}


def _succession(db: GameDB) -> Optional[Dict[str, object]]:
    """要职出缺（重臣病逝）：取候选三人——党羽续统 / 异党新进 / 不党能臣，请陛下简替。"""
    from ming_sim.lifespan import pop_vacancy, vacancies
    vs = vacancies(db)
    if not vs:
        return None
    vac = vs[0]
    office = str(vac.get("office") or "")
    deceased = str(vac.get("deceased") or "")
    dfac = str(vac.get("faction") or "")

    def _pick(where: str, params: tuple) -> Optional[str]:
        row = db.conn.execute(
            "SELECT name FROM characters WHERE status='active' AND power_id='ming' "
            "AND office_type!='后宫' AND name!=? " + where + " ORDER BY ability DESC LIMIT 1",
            (deceased, *params)).fetchone()
        return str(row["name"]) if row else None

    cand: List[Dict[str, str]] = []
    seen = set()
    ally = court.allies_of(db, deceased, limit=1)
    if ally and ally[0]["name"] not in seen:
        cand.append({"name": ally[0]["name"], "flavor": "党羽续统（萧规曹随，本派得安）"}); seen.add(ally[0]["name"])
    if dfac and dfac not in ("", "无", "中立"):
        rn = _pick("AND faction!=? AND faction NOT IN ('无','中立')", (dfac,))
        if rn and rn not in seen:
            cand.append({"name": rn, "flavor": "异党新进（锐意任事，借机掺沙）"}); seen.add(rn)
    neu = _pick("AND (faction='中立' OR faction='无')", ())
    if neu and neu not in seen:
        cand.append({"name": neu, "flavor": "不党能臣（持平无私，两不开罪）"}); seen.add(neu)
    while len(cand) < 2:
        extra = _pick("AND name NOT IN (%s)" % (",".join("?" * len(seen)) or "''"), tuple(seen))
        if not extra or extra in seen:
            break
        cand.append({"name": extra, "flavor": "资深堪任"}); seen.add(extra)
    if len(cand) < 2:
        return None
    pop_vacancy(db)  # 既已立为待决，从队列取出（待决本身即记录）
    return {"office": office, "deceased": deceased, "candidates": cand[:3]}


def _warlord(db: GameDB) -> Optional[Dict[str, object]]:
    """封疆跋扈：某镇离心≥72 入队 → 请陛下裁断（加饷羁縻/削权裁抑/暂事姑息）。"""
    from ming_sim.frontier import pop_warlord, warlord_queue
    q = warlord_queue(db)
    if not q:
        return None
    head = q[0]
    # 校验该镇仍在且仍跋扈
    row = db.conn.execute("SELECT autonomy, arrears, maintenance_per_turn FROM armies WHERE id=?",
                          (head["army_id"],)).fetchone()
    if row is None or int(row["autonomy"] or 0) < 60:
        pop_warlord(db)
        return None
    pop_warlord(db)
    # 监军候选（遣往钳制之内臣）：东厂提督优先，否则任一在朝宦官。
    cand = ""
    try:
        from ming_sim.intrigue import dongchang_chief
        cand = dongchang_chief(db) or ""
    except Exception:
        cand = ""
    if not cand:
        try:
            from ming_sim.eunuch import list_candidates
            for c in list_candidates(db):
                if c.get("is_eunuch"):
                    cand = str(c["name"])
                    break
        except Exception:
            cand = ""
    return {"army_id": head["army_id"], "army": str(head["army"]),
            "commander": str(head["commander"]), "autonomy": int(row["autonomy"]),
            "arrears": int(row["arrears"] or 0), "supervisor_cand": cand}


def _packed_faction(db: GameDB) -> Optional[Dict[str, object]]:
    """坐大的朋党：满意≥62 且 势力≥60 且 气焰≥45。"""
    row = db.conn.execute(
        "SELECT name, satisfaction, leverage, heat FROM factions "
        "WHERE name NOT IN ('无','中立') AND satisfaction>=62 AND leverage>=60 AND heat>=45 "
        "ORDER BY (satisfaction+leverage+heat) DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {"faction": str(row["name"]), "leverage": int(row["leverage"])}


def _imperial_petition(db: GameDB) -> Optional[Dict[str, object]]:
    """NPC 主动求援：怨望/低信任/政敌重压积累到必须请皇帝给话。

    首页风向可以轻量提示；这里阈值更高，只有矛盾进入政治成本区才升格为
    CK3 式裁断事件。
    """

    row = db.conn.execute(
        """
        SELECT c.name, c.office, c.faction, c.ability, c.integrity,
               c.emp_trust, c.grievance
        FROM characters c
        WHERE c.status='active'
          AND c.power_id='ming'
          AND c.office_type!='后宫'
          AND c.name!='崇祯'
          AND (
            c.grievance>=78
            OR c.emp_trust<=28
            OR EXISTS (
              SELECT 1
              FROM relationships r
              JOIN characters other ON other.name=r.b_name
              WHERE r.a_name=c.name
                AND r.opinion<=-74
                AND (c.grievance>=50 OR c.emp_trust<=42)
                AND other.status='active'
                AND other.power_id='ming'
                AND other.office_type!='后宫'
            )
          )
        ORDER BY
          CASE WHEN c.grievance>=78 OR c.emp_trust<=28 THEN 0 ELSE 1 END,
          c.grievance DESC,
          c.emp_trust ASC,
          c.ability DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    name = str(row["name"])
    rival = db.conn.execute(
        """
        SELECT r.b_name, r.opinion, r.basis, cb.office, cb.faction
        FROM relationships r
        JOIN characters cb ON cb.name=r.b_name
        WHERE r.a_name=?
          AND r.opinion<=-55
          AND cb.status='active'
          AND cb.power_id='ming'
          AND cb.office_type!='后宫'
        ORDER BY r.opinion ASC
        LIMIT 1
        """,
        (name,),
    ).fetchone()
    if rival is None:
        rival_ctx = {"name": "", "opinion": 0, "basis": "", "office": "", "faction": ""}
    else:
        rival_ctx = {
            "name": str(rival["b_name"] or ""),
            "opinion": int(rival["opinion"] or 0),
            "basis": str(rival["basis"] or "旧怨"),
            "office": str(rival["office"] or ""),
            "faction": str(rival["faction"] or ""),
        }
    return {
        "petitioner": name,
        "office": str(row["office"] or ""),
        "faction": str(row["faction"] or ""),
        "ability": int(row["ability"] or 50),
        "integrity": int(row["integrity"] or 50),
        "trust": int(row["emp_trust"] or 0),
        "grievance": int(row["grievance"] or 0),
        "rival": rival_ctx["name"],
        "rival_office": rival_ctx["office"],
        "rival_faction": rival_ctx["faction"],
        "rival_opinion": rival_ctx["opinion"],
        "basis": rival_ctx["basis"],
    }


def _overdue_agreement(db: GameDB) -> Optional[Dict[str, object]]:
    """履约失期：已到回奏期限、仍未完成的奏对承诺，转成一次君前追责抉择。"""

    state = db.load_state()
    row = db.conn.execute(
        """
        SELECT a.id, a.minister_name, a.topic, a.core_topic, a.target_text,
               a.action_kind, a.promise_type, a.stakes, a.due_turn,
               c.office, c.faction, c.emp_trust, c.grievance
        FROM negotiation_agreements a
        JOIN characters c ON c.name=a.minister_name
        WHERE a.status IN ('pending', 'sealed')
          AND a.target_status='pending_conditions'
          AND a.due_turn>0
          AND a.due_turn<=?
          AND c.status='active'
          AND c.power_id='ming'
        ORDER BY a.due_turn ASC, a.id DESC
        LIMIT 1
        """,
        (int(state.turn),),
    ).fetchone()
    if row is None:
        return None
    tasks = db.conn.execute(
        """
        SELECT description, task_kind, status
        FROM negotiation_tasks
        WHERE agreement_id=?
        ORDER BY id
        LIMIT 4
        """,
        (int(row["id"]),),
    ).fetchall()
    task_texts = [str(t["description"] or "") for t in tasks if str(t["description"] or "").strip()]
    minister = str(row["minister_name"] or "")
    topic = str(row["core_topic"] or row["topic"] or "履约事项")
    favors = court.favor_memories(db, minister, limit=2)
    favor_head = favors[0] if favors else {}
    return {
        "agreement_id": int(row["id"]),
        "cooldown_id": f"overdue_obligation:{int(row['id'])}",
        "minister": minister,
        "office": str(row["office"] or ""),
        "faction": str(row["faction"] or ""),
        "topic": topic,
        "target_text": str(row["target_text"] or topic),
        "promise_type": str(row["promise_type"] or "奏对承诺"),
        "stakes": str(row["stakes"] or "一般政务"),
        "due_turn": int(row["due_turn"] or 0),
        "overdue_by": max(0, int(state.turn) - int(row["due_turn"] or 0)),
        "trust": int(row["emp_trust"] or 0),
        "grievance": int(row["grievance"] or 0),
        "tasks": task_texts,
        "favor_count": len(favors),
        "favor_title": str(favor_head.get("title") or ""),
        "favor_outcome": str(favor_head.get("outcome") or favor_head.get("cause") or ""),
    }


def _meaningful_faction(name: object) -> str:
    faction = str(name or "").strip()
    return "" if faction in {"", "无", "中立"} else faction


def _petition_faction_effect(
    ctx: Dict[str, object],
    *,
    petitioner_sat: int = 0,
    petitioner_lev: int = 0,
    petitioner_heat: int = 0,
    rival_sat: int = 0,
    rival_lev: int = 0,
    rival_heat: int = 0,
) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}

    def add(faction: str, sat: int, lev: int, heat: int) -> None:
        fac = _meaningful_faction(faction)
        if not fac:
            return
        bucket = out.setdefault(fac, {})
        if sat:
            bucket["satisfaction"] = int(bucket.get("satisfaction", 0)) + sat
        if lev:
            bucket["leverage"] = int(bucket.get("leverage", 0)) + lev
        if heat:
            bucket["heat"] = int(bucket.get("heat", 0)) + heat

    add(str(ctx.get("faction") or ""), petitioner_sat, petitioner_lev, petitioner_heat)
    add(str(ctx.get("rival_faction") or ""), rival_sat, rival_lev, rival_heat)
    return out


def _petition_opinion_effect(
    ctx: Dict[str, object],
    petitioner_to_rival: int,
    rival_to_petitioner: int,
    basis: str,
) -> List[Dict[str, object]]:
    petitioner = str(ctx.get("petitioner") or "")
    rival = str(ctx.get("rival") or "")
    if not petitioner or not rival:
        return []
    return [
        {"a": petitioner, "b": rival, "delta": petitioner_to_rival, "basis": basis},
        {"a": rival, "b": petitioner, "delta": rival_to_petitioner, "basis": basis},
    ]


def _policy_actor_char_effect(ctx: Dict[str, object], trust: int = 0, grievance: int = 0) -> List[Dict[str, object]]:
    actor = str(ctx.get("actor") or "")
    if not actor:
        return []
    out: Dict[str, object] = {"name": actor}
    if trust:
        out["emp_trust"] = trust
    if grievance:
        out["grievance"] = grievance
    return [out]


def _policy_actor_faction_effect(
    ctx: Dict[str, object],
    *,
    sat: int = 0,
    lev: int = 0,
    heat: int = 0,
) -> Dict[str, Dict[str, int]]:
    faction = _meaningful_faction(ctx.get("actor_faction"))
    if not faction:
        return {}
    out: Dict[str, int] = {}
    if sat:
        out["satisfaction"] = sat
    if lev:
        out["leverage"] = lev
    if heat:
        out["heat"] = heat
    return {faction: out} if out else {}


def _legacy_tax_stem(name: str, hint: str, key: str) -> str:
    text = f"{name} {hint} {key}"
    for stem in ("辽饷", "商税", "盐税", "矿税", "田赋"):
        if stem in text:
            return stem
    if any(token in text for token in ("税", "饷", "派", "赋", "课")):
        return "税负"
    return ""


def _policy_legacy_actor_context(db: GameDB, stem: str) -> Dict[str, object]:
    fiscal_first = bool(stem and any(token in stem for token in ("税", "饷", "田赋", "商税", "盐税", "矿税")))
    order_clause = (
        """
        CASE
          WHEN office LIKE '%户部%' OR office_type LIKE '%户部%' THEN 0
          WHEN office LIKE '%内阁%' OR office_type LIKE '%内阁%' THEN 1
          WHEN office LIKE '%都察院%' OR office_type LIKE '%都察院%' THEN 2
          ELSE 3
        END,
        """
        if fiscal_first
        else ""
    )
    row = db.conn.execute(
        f"""
        SELECT name, office, faction, emp_trust, grievance, ability, integrity
        FROM characters
        WHERE status='active'
          AND power_id='ming'
          AND office_type!='后宫'
          AND name!='崇祯'
        ORDER BY
          {order_clause}
          ability DESC,
          integrity DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return {}
    return {
        "name": str(row["name"] or ""),
        "office": str(row["office"] or ""),
        "faction": str(row["faction"] or ""),
        "trust": int(row["emp_trust"] or 0),
        "grievance": int(row["grievance"] or 0),
        "ability": int(row["ability"] or 50),
        "integrity": int(row["integrity"] or 50),
    }


def _policy_legacy_aftershock(db: GameDB) -> Optional[Dict[str, object]]:
    """长期政策余波：重税类 legacy 不只是静态 debuff，会周期性索要皇帝态度。"""

    state = db.load_state()
    db.expire_legacies(state)
    rows = db.conn.execute(
        """
        SELECT id, name, modifiers, narrative_hint, duration_months,
               start_month, legacy_key
        FROM legacies
        WHERE status='active'
        ORDER BY id DESC
        LIMIT 24
        """
    ).fetchall()
    best: Optional[Dict[str, object]] = None
    best_score = -10**9
    for row in rows:
        name = str(row["name"] or "")
        hint = str(row["narrative_hint"] or "")
        key = str(row["legacy_key"] or "")
        modifiers = _legacy_modifiers(row)
        minxin = _intish(modifiers.get("民心"))
        duration = _intish(row["duration_months"])
        stem = _legacy_tax_stem(name, hint, key)
        taxish = (
            key.startswith("directive_tax:")
            or bool(stem)
            or any(token in name + hint for token in ("苛税", "税负", "加派", "加征", "常税"))
        )
        if not taxish or minxin > -6:
            continue
        remaining = -1
        if duration >= 0:
            remaining = db.legacy_remaining_months(row, state)
        score = abs(minxin) * 3 + (12 if duration < 0 else 0) + (8 if key.startswith("directive_tax:") else 0)
        if score <= best_score:
            continue
        legacy_id = int(row["id"])
        actor = _policy_legacy_actor_context(db, stem or "税负")
        actor_name = str(actor.get("name") or "")
        best = {
            "legacy_id": legacy_id,
            "cooldown_id": f"policy_aftershock:{legacy_id}",
            "name": name or "旧政余波",
            "hint": hint,
            "stem": stem or "税负",
            "minxin": minxin,
            "duration": duration,
            "remaining": remaining,
            "actor": actor_name,
            "actor_office": str(actor.get("office") or ""),
            "actor_faction": str(actor.get("faction") or ""),
            "actor_trust": int(actor.get("trust") or 0),
            "actor_grievance": int(actor.get("grievance") or 0),
            "actor_ability": int(actor.get("ability") or 50),
            "actor_integrity": int(actor.get("integrity") or 50),
        }
        best_score = score
    return best


# ── 事件定义（涌现自活的宫廷）─────────────────────────────────────────────────

def _succession_choices(ctx: Dict[str, object]) -> List[Dict[str, object]]:
    """据候选动态生成选项：擢某人补缺 + 各自的派系/好感涟漪。"""
    out = []
    for cand in ctx.get("candidates") or []:
        nm = str(cand["name"])
        out.append({
            "key": f"pick:{nm}",
            "label": f"擢 {nm}（{cand['flavor']}）",
            "hint": "简替要缺，受擢者感念效死，落选者各有心思",
            "effect": (lambda c=ctx, n=nm: {
                "shi": 1,
                "appoint": {"name": n, "office": c["office"]},
                "log": f"简{n}补{c['deceased']}遗缺（{c['office']}）。",
            }),
        })
    return out


def _defs() -> List[Dict[str, object]]:
    return [
        {
            "id": "eunuch_crisis",
            "priority": 45,  # 阉祸危及社稷，最优先
            "when": _eunuch_crisis,
            "title": lambda c: f"阉祸临头：{c['eunuch']}权倾朝野",
            "narrative": lambda c: (
                f"{c['office']}{c['eunuch']}秉权阉之势已极（{c['power']}），司礼监代批红、东厂操诏狱，"
                f"阉党盘踞要津{c['packed']}人，内外勾连、把柄在手，进退人物渐由其口——主弱臣强，俨然魏珰再世。"
                "再不裁处，恐成阉祸、社稷为其所窃。陛下何以决之？"),
            "choices": [
                {"key": "purge", "label": lambda c: f"乾纲独断，翦除阉党、下{c['eunuch']}诏狱",
                 "hint": "雷霆除奸：快意立威、权阉土崩、清流额手——然君威未立则仓促，阉党狗急、政局动荡",
                 "effect": lambda c: ({"shi": 8, "renshi": 6, "eunuch_power": -50,
                                       "status": [{"name": c["eunuch"], "status": "imprisoned", "reason": "翦除阉党、下诏狱"}],
                                       "faction": {"阉党": {"leverage": -28, "satisfaction": -22},
                                                   "东林": {"satisfaction": 12, "leverage": 8}},
                                       "metrics": {"民心": 5, "皇威": 6},
                                       "daipihong_off": True,
                                       "log": f"乾纲独断，翦除阉党、下{c['eunuch']}诏狱。"}
                                      if c["shi"] >= 45 else
                                      {"shi": -4, "renshi": -3, "eunuch_power": -25,
                                       "status": [{"name": c["eunuch"], "status": "imprisoned", "reason": "翦除阉党（仓促）"}],
                                       "faction": {"阉党": {"leverage": -12, "satisfaction": -15}},
                                       "metrics": {"民心": -3, "皇威": -2},
                                       "daipihong_off": True,
                                       "log": f"君威未立而仓促除阉，{c['eunuch']}虽下狱，阉党狗急、政局为之动荡。"})},
                {"key": "endure", "label": lambda c: "隐忍周旋，徐图分化其党",
                 "hint": "不动声色：低风险、略抑权阉，但不除根——隐患仍在，恐复炽",
                 "effect": lambda c: {"shi": -1, "eunuch_power": -12,
                                      "faction": {"阉党": {"satisfaction": -3}},
                                      "log": f"暂示优容，徐图分化{c['eunuch']}之党。"}},
                {"key": "rely", "label": lambda c: f"索性倚{c['eunuch']}为腹心理政",
                 "hint": "天启故事：省心、阉党效死，然权阉冲顶、东林夺气——主弱臣强，阉祸长蚀国本",
                 "effect": lambda c: {"shi": 2, "renshi": -6, "eunuch_power": 12,
                                      "faction": {"阉党": {"leverage": 12, "satisfaction": 10},
                                                  "东林": {"satisfaction": -10, "leverage": -8}},
                                      "metrics": {"民心": -4},
                                      "log": f"索性倚{c['eunuch']}理政，阉党益横、东林夺气——阉祸成。"}},
            ],
        },
        {
            "id": "succession",
            "priority": 35,
            "when": _succession,
            "title": lambda c: f"要缺简替：{c['deceased']}遗缺（{c['office']}）",
            "narrative": lambda c: (
                f"{c['deceased']}既殁，{c['office']}一缺旷悬，关乎枢机。"
                f"廷推数人，皆有人援引、亦各有所图。陛下简谁补此要任？"),
            "choices": _succession_choices,  # 动态：据候选生成
        },
        {
            "id": "warlord",
            "priority": 40,  # 封疆跋扈最紧急
            "when": _warlord,
            "title": lambda c: f"封疆跋扈：{c['army']}尾大不掉",
            "narrative": lambda c: (
                f"{c['army']}（{c['commander']}）拥兵自重、几不奉诏，欠饷积至{c['arrears']}万两，"
                f"离心已极（自专{c['autonomy']}）。处置失当，恐成藩镇之祸。陛下何以制之？"),
            "choices": [
                {"key": "appease", "label": lambda c: "如数补饷、加官羁縻",
                 "hint": "花钱买安：离心骤降、将帅感恩，然国库大耗、长其骄",
                 "effect": lambda c: {"shi": -1, "metrics": {"国库": -max(8, c["arrears"])},
                                      "army": [{"id": c["army_id"], "autonomy": -45, "loyalty": 12}],
                                      "log": f"补{c['army']}欠饷、加官羁縻。"}},
                {"key": "curb", "label": lambda c: "下诏切责、削权夺兵",
                 "hint": "乾纲独断：成则立威慑藩，败则逼反（离心反弹、士气挫）",
                 "effect": lambda c: ({"shi": 6, "army": [{"id": c["army_id"], "autonomy": -30, "loyalty": -8}],
                                       "log": f"削{c['army']}之权，以儆跋扈。"}
                                      if c["autonomy"] < 85 else
                                      {"shi": -4, "renshi": -4,
                                       "army": [{"id": c["army_id"], "autonomy": 10, "loyalty": -15}],
                                       "metrics": {"民心": -2},
                                       "log": f"切责{c['army']}，反激其变、几至称兵。"}),
                 },
                {"key": "tolerate", "label": lambda c: "暂事姑息、徐图后计",
                 "hint": "拖：不花钱不担险，但纵其坐大、君威日替",
                 "effect": lambda c: {"shi": -3,
                                      "army": [{"id": c["army_id"], "autonomy": 5}],
                                      "log": f"对{c['army']}暂事姑息。"}},
                {"key": "supervise", "label": lambda c: f"遣{c.get('supervisor_cand') or '内臣'}监军就近钳制",
                 "hint": "监军太监：天子耳目镇之、离心骤抑，然掣肘军务、侵饷、主帅含怨、权阉伸入军（明季祸辽之患）",
                 "effect": lambda c: {"shi": 1, "renshi": -2,
                                      "army": [{"id": c["army_id"], "autonomy": -25}],
                                      "supervise": {"army_id": c["army_id"], "eunuch": c.get("supervisor_cand")},
                                      "metrics": {"皇威": 1},
                                      "log": f"遣{c.get('supervisor_cand') or '内臣'}监{c['army']}军，就近钳制。"},
                 "available": lambda c: bool(c.get("supervisor_cand"))},
            ],
        },
        {
            "id": "eunuch_frame",
            "priority": 33,  # 厂卫锻炼诏狱，关乎清流存亡，仅次于要缺/阉祸/封疆
            "when": _eunuch_frame,
            "title": lambda c: f"厂卫锻炼诏狱：{c['eunuch']}劾{c['target']}",
            "narrative": lambda c: (
                f"{c['eunuch']}秉东厂之势，已锻炼一桩诏狱，劾{c['office']}{c['target']}「{c['charge']}」，"
                + ("其事虽有端绪，然罗织深文、意在锄异。" if c["real"]
                   else "查无实据、纯属罗织——分明借厂卫锄除清流异己。")
                + "诏狱已具，只待圣意一准。陛下裁之？"),
            "choices": [
                {"key": "approve", "label": lambda c: f"准其奏，下{c['target']}诏狱",
                 "hint": "照厂卫意：省事、权阉益横、阉党弹冠——然清流夺气、冤狱伤民心，养成阉祸",
                 "effect": lambda c: {"eunuch_power": 5, "shi": 1, "renshi": -3,
                                      "status": [{"name": c["target"], "status": "imprisoned", "reason": f"厂卫锻炼诏狱·{c['charge']}"}],
                                      "faction": {"阉党": {"leverage": 6, "satisfaction": 5}, "东林": {"satisfaction": -8, "leverage": -5}},
                                      "metrics": {"民心": -3 if c["real"] else -5},
                                      "ripple": [{"name": c["target"], "kind": "oust"}],
                                      "log": f"准{c['eunuch']}所奏，下{c['target']}诏狱。"}},
                {"key": "protect", "label": lambda c: f"斥厂卫罗织，力保{c['target']}",
                 "hint": "顶住权阉：清流感恩、君威自立、权阉受挫——然开罪厂卫，阉党衔恨",
                 "effect": lambda c: {"eunuch_power": -6, "shi": 2,
                                      "char": [{"name": c["target"], "emp_trust": 8, "grievance": -5}],
                                      "faction": {"阉党": {"satisfaction": -6, "leverage": -4}, "东林": {"satisfaction": 8, "leverage": 4}},
                                      "metrics": {"民心": 2},
                                      "log": f"斥{c['eunuch']}罗织，力保{c['target']}。"}},
                {"key": "review", "label": lambda c: "下三法司核实",
                 "hint": "走程序：看似公允，却让清流寒心、任事者更怕出头，权阉徐图",
                 "effect": lambda c: {"renshi": -3, "eunuch_power": -1,
                                      "char": [{"name": c["target"], "emp_trust": -3, "grievance": 3}],
                                      "log": f"{c['target']}被劾事，下三法司核实。"}},
            ],
        },
        {
            "id": "overdue_obligation",
            "priority": 31,
            "cooldown": "ctx",
            "when": _overdue_agreement,
            "title": lambda c: (
                f"忘恩负约：{c['minister']}未复命"
                if int(c.get("favor_count") or 0) > 0 else
                f"履约失期：{c['minister']}未复命"
            ),
            "narrative": lambda c: (
                f"{c['office']}{c['minister']}先前领下「{c['topic']}」，御限已至"
                f"{'，逾期' + str(c['overdue_by']) + '月' if int(c.get('overdue_by') or 0) else ''}，"
                f"至今未有足以交账的回奏。此事关涉{c['stakes']}，若轻轻揭过，履约账本便成虚文；"
                + (
                    f"更要紧的是，{c['minister']}尚有「{c.get('favor_title') or '旧恩未报'}」在身，"
                    f"{c.get('favor_outcome') or '不宜装作两清'}。若受恩者也可失约，天恩便成空话；"
                    if int(c.get("favor_count") or 0) > 0 else
                    ""
                )
                + "若过严，又恐人人只求自保。陛下如何处置？"
            ),
            "choices": [
                {"key": "call_favor", "label": lambda c: f"点明旧恩，勒{c['minister']}一月内还账",
                 "hint": "把天恩变成政治债：压力更强、任事不至全寒，但本人和同党会感到被拿捏",
                 "available": lambda c: int(c.get("favor_count") or 0) > 0,
                 "effect": lambda c: {"shi": 2, "renshi": 1,
                                      "char": [{"name": c["minister"], "emp_trust": -1, "grievance": 6}],
                                      "faction": ({_meaningful_faction(c.get("faction")): {"satisfaction": -2, "heat": 2}}
                                                  if _meaningful_faction(c.get("faction")) else {}),
                                      "agreements": [{
                                          "id": c["agreement_id"],
                                          "action": "extend",
                                          "months": 1,
                                          "evidence": f"御前点明旧恩，责{c['minister']}「{c['topic']}」一月内还账复命；不得装作两清。"
                                      }],
                                      "log": f"忘恩负约：点明旧恩，勒{c['minister']}一月内还账复命。"}},
                {"key": "press", "label": lambda c: f"严旨催{c['minister']}，限一月复命",
                 "hint": "不即治罪，但把账压回他身上；威令略立，臣下压力上升",
                 "effect": lambda c: {"shi": 1, "renshi": -1,
                                      "char": [{"name": c["minister"], "emp_trust": -2, "grievance": 4}],
                                      "faction": ({_meaningful_faction(c.get("faction")): {"satisfaction": -1, "heat": 1}}
                                                  if _meaningful_faction(c.get("faction")) else {}),
                                      "agreements": [{
                                          "id": c["agreement_id"],
                                          "action": "extend",
                                          "months": 1,
                                          "evidence": f"御前严催{c['minister']}「{c['topic']}」，限一月内据实复命。"
                                      }],
                                      "log": f"履约失期：严催{c['minister']}「{c['topic']}」一月内复命。"}},
                {"key": "grant_time", "label": lambda c: f"宽{c['minister']}三月，许其补办",
                 "hint": "保任事余地：人心稍安，但君威受损，旁人也会试探边界",
                 "effect": lambda c: {"shi": -1, "renshi": 1,
                                      "char": [{"name": c["minister"], "emp_trust": 3, "grievance": -3}],
                                      "faction": ({_meaningful_faction(c.get("faction")): {"satisfaction": 1}}
                                                  if _meaningful_faction(c.get("faction")) else {}),
                                      "agreements": [{
                                          "id": c["agreement_id"],
                                          "action": "extend",
                                          "months": 3,
                                          "evidence": f"御前宽{c['minister']}「{c['topic']}」三月，仍须补办复命。"
                                      }],
                                      "log": f"履约失期：宽{c['minister']}三月补办「{c['topic']}」。"}},
                {"key": "punish", "label": lambda c: f"以失期问责，申饬{c['minister']}",
                 "hint": "把账本做实：立威、断拖延，但寒任事之心，本人与其党会记怨",
                 "effect": lambda c: {"shi": 2, "renshi": -2,
                                      "char": [{"name": c["minister"], "emp_trust": -8, "grievance": 9}],
                                      "faction": ({_meaningful_faction(c.get("faction")): {"satisfaction": -4, "heat": 3}}
                                                  if _meaningful_faction(c.get("faction")) else {}),
                                      "agreements": [{
                                          "id": c["agreement_id"],
                                          "action": "fail",
                                          "evidence": f"御前裁断：{c['minister']}「{c['topic']}」逾期未复命，按失期问责。"
                                      }],
                                      "log": f"履约失期：{c['minister']}「{c['topic']}」逾期未复命，申饬问责。"}},
            ],
        },
        {
            "id": "policy_aftershock",
            "priority": 29,
            "cooldown": "ctx",
            "when": _policy_legacy_aftershock,
            "title": lambda c: (
                f"旧政求裁：{c['actor']}奏{c['name']}"
                if c.get("actor") else
                f"旧政反噬：{c['name']}"
            ),
            "narrative": lambda c: (
                (
                    f"{c.get('actor_office') or ''}{c['actor']}求见，称自己夹在催科、边饷与民怨之间："
                    if c.get("actor") else
                    ""
                )
                + f"旧日旨意留下的「{c['name']}」仍在民间发酵。"
                f"{c['hint'] or '户部称钱粮不可骤停，地方却说催科已成怨府。'}"
                f"眼下这项遗产使民心 {c['minxin']}%，"
                f"{'且已成永久常例' if int(c.get('duration') or 0) < 0 else '尚余' + str(c.get('remaining')) + '月'}。"
                + (
                    f"{c['actor']}现信任{c.get('actor_trust')}、怨望{c.get('actor_grievance')}，"
                    "若没有圣意担责，便会把这笔民怨记在朝廷和自己头上。"
                    if c.get("actor") else
                    ""
                )
                + "若立刻蠲缓，国库与边饷要吃紧；若照旧严征，民怨会被坐实为长疮。陛下何以裁之？"
            ),
            "choices": [
                {"key": "keep_collecting", "label": lambda c: f"钱粮不可骤停，{c['stem']}照旧严征",
                 "hint": "国库立刻见长，皇命显得硬；但旧政更难回头，民怨会继续加深",
                 "effect": lambda c: {"shi": 1, "renshi": -2,
                                      "metrics": {"国库": 6, "民心": -2, "皇威": 1},
                                      "char": _policy_actor_char_effect(c, trust=1, grievance=5),
                                      "faction": _policy_actor_faction_effect(c, sat=-2, heat=1),
                                      "legacy": [{
                                          "id": c["legacy_id"],
                                          "action": "worsen",
                                          "modifier": "民心",
                                          "amount": 2,
                                          "label": c["stem"],
                                          "note": f"御前裁断{c['stem']}照旧严征，旧怨更深。"
                                      }],
                                      "log": f"旧政反噬：{c['stem']}照旧严征，以济急需。"}},
                {"key": "relieve_now", "label": lambda c: f"先蠲缓一半{c['stem']}，给百姓喘息",
                 "hint": "民心立刻回暖，长期余波缓和；但钱粮短缺会压到国库与边防",
                 "effect": lambda c: {"shi": -1, "renshi": 2,
                                      "metrics": {"国库": -8, "民心": 4},
                                      "char": _policy_actor_char_effect(c, trust=4, grievance=-5),
                                      "faction": _policy_actor_faction_effect(c, sat=2, heat=-1),
                                      "legacy": [{
                                          "id": c["legacy_id"],
                                          "action": "soften",
                                          "modifier": "民心",
                                          "amount": 4,
                                          "label": c["stem"],
                                          "note": f"御前许先蠲缓一半{c['stem']}，民怨稍解。"
                                      }],
                                      "log": f"旧政反噬：先蠲缓一半{c['stem']}以苏民困。"}},
                {"key": "audit_middlemen", "label": lambda c: f"命{c['actor']}清查加派侵吞，税额暂不全废",
                 "hint": "折中但会形成后续账本：查出中间盘剥可缓民怨，查不出则拖成新麻烦",
                 "available": lambda c: bool(c.get("actor")),
                 "effect": lambda c: {"shi": 2, "renshi": 1,
                                      "metrics": {"民心": 1},
                                      "char": _policy_actor_char_effect(c, trust=2, grievance=2),
                                      "faction": _policy_actor_faction_effect(c, sat=-1),
                                      "legacy": [{
                                          "id": c["legacy_id"],
                                          "action": "soften",
                                          "modifier": "民心",
                                          "amount": 2,
                                          "label": c["stem"],
                                          "note": f"御前命{c['actor']}清查{c['stem']}加派侵吞，先禁层层浮收。"
                                      }],
                                      "obligations": [{
                                          "minister": c["actor"],
                                          "title": f"清查{c['stem']}加派侵吞",
                                          "target_text": f"{c['actor']}须就「{c['name']}」查明地方层层加派、侵吞与浮收，并回奏可执行的减负清单。",
                                          "tasks": [
                                              f"核出{c['stem']}现行征收、地方加派与实际入库差额。",
                                              "列出三条可立即禁革的浮收名目，并说明会影响的边饷或国库缺口。",
                                              "三月内回奏查核证据与处置名单，不得只称百姓困苦。"
                                          ],
                                          "source": f"policy_aftershock:audit:{c['legacy_id']}",
                                          "due_turns": 3,
                                          "summary": f"御前命{c['actor']}清查{c['stem']}加派侵吞，以查弊而非一概废税。"
                                      }],
                                      "log": f"旧政反噬：命{c['actor']}清查{c['stem']}加派侵吞。"}},
            ],
        },
        {
            "id": "imperial_petition",
            "priority": 28,
            "when": _imperial_petition,
            "title": lambda c: f"求援请托：{c['petitioner']}请陛下给话",
            "narrative": lambda c: (
                f"{c['office']}{c['petitioner']}求见，称近来信任仅{c['trust']}、怨望已{c['grievance']}。"
                + (
                    f"又与{c['rival_office']}{c['rival']}因「{c['basis']}」积怨甚深，恐被政敌乘势逼入绝路。"
                    if c.get("rival") else
                    "其言辞虽称公事，骨子里却是求陛下给一个台阶与护身符。"
                )
                + "陛下一句话，可救一人任事，也可能开请托之门。何以处之？"),
            "choices": [
                {"key": "protect", "label": lambda c: f"明旨护持{c['petitioner']}，给他台阶",
                 "hint": "买一颗人心：任事回暖、本人死力，但显得偏护，政敌与敌派会记账",
                 "effect": lambda c: {"shi": -1, "renshi": 4,
                                      "char": [{"name": c["petitioner"], "emp_trust": 10, "grievance": -12}]
                                      + ([{"name": c["rival"], "emp_trust": -3, "grievance": 5}] if c.get("rival") else []),
                                      "opinion": _petition_opinion_effect(c, -4, -8, "御前偏护"),
                                      "faction": _petition_faction_effect(
                                          c, petitioner_sat=4, petitioner_lev=1, petitioner_heat=-3,
                                          rival_sat=-4, rival_heat=5),
                                      "log": f"明旨护持{c['petitioner']}，给其台阶以收任事之心。"}},
                {"key": "demand_service", "label": lambda c: f"许其自辩，但命{c['petitioner']}领难差自证",
                 "hint": "把请托变成交易：不给白护身符，须拿可验差使来换；人心略回，仍有压力",
                 "effect": lambda c: {"shi": 1, "renshi": 3 if int(c.get("ability") or 50) >= 55 else 2,
                                      "char": [{"name": c["petitioner"], "emp_trust": 6, "grievance": -5}],
                                      "faction": _petition_faction_effect(c, petitioner_sat=1, petitioner_heat=-1),
                                      "obligations": [{
                                          "minister": c["petitioner"],
                                          "title": f"难差自证：{c['petitioner']}",
                                          "target_text": f"{c['petitioner']}因御前求援请托，须承领一件可验差使，以成效换取护持。",
                                          "tasks": [
                                              f"三日内回奏一件可验难差的进展、证据与下一步时限，不得只求护持。"
                                          ],
                                          "source": f"imperial_petition:demand_service:{c['petitioner']}",
                                          "due_turns": 3,
                                          "summary": f"求援不白给护身符，{c['petitioner']}须领难差自证。"
                                      }],
                                      "log": f"许{c['petitioner']}自辩，命其领难差自证。"}},
                {"key": "co_work", "label": lambda c: f"令{c['petitioner']}与{c['rival']}共办一事",
                 "hint": "把私怨压成公事：可降一点党争热度，但两人都不痛快，办坏了会一起怨上",
                 "available": lambda c: bool(c.get("rival")),
                 "effect": lambda c: {"shi": 2, "renshi": 1,
                                      "char": [{"name": c["petitioner"], "emp_trust": 2, "grievance": 3},
                                               {"name": c["rival"], "emp_trust": 2, "grievance": 2}],
                                      "opinion": _petition_opinion_effect(c, 6, 6, "御前责令共办"),
                                      "faction": _petition_faction_effect(
                                          c, petitioner_sat=-2, petitioner_heat=-3,
                                          rival_sat=-2, rival_heat=-3),
                                      "obligations": [
                                          {
                                              "minister": c["petitioner"],
                                              "title": f"共办消怨：{c['petitioner']}与{c['rival']}",
                                              "target_text": f"{c['petitioner']}须与{c['rival']}共办一件公事，把私怨压成可验结果。",
                                              "tasks": [
                                                  f"三日内与{c['rival']}共同回奏共办事项、分工、风险与已办证据。",
                                                  f"不得再以「{c['basis']}」互相阻挠，若事败须说明责任。"
                                              ],
                                              "source": f"imperial_petition:co_work:{c['petitioner']}:{c['rival']}",
                                              "due_turns": 3,
                                              "summary": f"御前责令{c['petitioner']}与{c['rival']}共办一事，以公事压私怨。"
                                          },
                                          {
                                              "minister": c["rival"],
                                              "title": f"共办消怨：{c['rival']}与{c['petitioner']}",
                                              "target_text": f"{c['rival']}须与{c['petitioner']}共办一件公事，把私怨压成可验结果。",
                                              "tasks": [
                                                  f"三日内与{c['petitioner']}共同回奏共办事项、分工、风险与已办证据。",
                                                  f"不得再以「{c['basis']}」互相阻挠，若事败须说明责任。"
                                              ],
                                              "source": f"imperial_petition:co_work:{c['rival']}:{c['petitioner']}",
                                              "due_turns": 3,
                                              "summary": f"御前责令{c['rival']}与{c['petitioner']}共办一事，以公事压私怨。"
                                          }
                                      ],
                                      "log": f"令{c['petitioner']}与{c['rival']}共办一事，以公事压私怨。"}},
                {"key": "shelve", "label": lambda c: "留中不应，示以不纳私请",
                 "hint": "不为请托开门：略立规矩，却寒其任事之心，相关派系怨气升温",
                 "effect": lambda c: {"shi": 1, "renshi": -4,
                                      "char": [{"name": c["petitioner"], "emp_trust": -8, "grievance": 10}]
                                      + ([{"name": c["rival"], "emp_trust": 4}] if c.get("rival") else []),
                                      "opinion": _petition_opinion_effect(c, -6, -2, "御前留中不应"),
                                      "faction": _petition_faction_effect(
                                          c, petitioner_sat=-5, petitioner_heat=6,
                                          rival_sat=2, rival_lev=1, rival_heat=2),
                                      "log": f"{c['petitioner']}求援请托，留中不应。"}},
            ],
        },
        {
            "id": "rival_feud",
            "priority": 30,
            "when": _deepest_rivalry,
            "title": lambda c: f"宿敌互讦：{c['a']} 与 {c['b']}",
            "narrative": lambda c: (
                f"{c['a_office']}{c['a']}与{c['b_office']}{c['b']}因「{c['basis']}」势同水火，"
                f"连日交章互讦，各执一词、互指对方植党欺君。朝堂为之鼎沸，百官观望陛下如何裁断。"),
            "choices": [
                {"key": "side_a", "label": lambda c: f"偏袒{c['a']}，申斥{c['b']}",
                 "hint": "压一方、抬一方——快刀，但寒了被压一方及其党羽的心",
                 "effect": lambda c: {"shi": 2, "renshi": -3,
                                      "ripple": [{"name": c["b"], "kind": "oust"}],
                                      "char": [{"name": c["a"], "emp_trust": 6}],
                                      "faction": ({c["b_faction"]: {"satisfaction": -4}} if c["b_faction"] not in ("", "无", "中立") else {}),
                                      "log": f"廷争裁断：申斥{c['b']}、慰留{c['a']}。"}},
                {"key": "side_b", "label": lambda c: f"偏袒{c['b']}，申斥{c['a']}",
                 "hint": "反向落子，后果对称",
                 "effect": lambda c: {"shi": 2, "renshi": -3,
                                      "ripple": [{"name": c["a"], "kind": "oust"}],
                                      "char": [{"name": c["b"], "emp_trust": 6}],
                                      "faction": ({c["a_faction"]: {"satisfaction": -4}} if c["a_faction"] not in ("", "无", "中立") else {}),
                                      "log": f"廷争裁断：申斥{c['a']}、慰留{c['b']}。"}},
                {"key": "both", "label": lambda c: "各打五十大板，俱夺俸",
                 "hint": "和稀泥也是表态：立威但两边都凉",
                 "effect": lambda c: {"shi": 3, "renshi": -5,
                                      "char": [{"name": c["a"], "grievance": 5}, {"name": c["b"], "grievance": 5}],
                                      "log": f"廷争裁断：{c['a']}、{c['b']}各夺俸申饬。"}},
                {"key": "ignore", "label": lambda c: "留中不发，由他们去",
                 "hint": "回避＝纵容党争：失威、堕任事之心",
                 "effect": lambda c: {"shi": -3, "renshi": -3,
                                      "opinion": [{"a": c["a"], "b": c["b"], "delta": -8, "basis": c["basis"]}],
                                      "log": f"{c['a']}与{c['b']}之争，留中不发。"}},
            ],
        },
        {
            "id": "loyal_slandered",
            "priority": 25,
            "when": _slandered_loyal,
            "title": lambda c: f"孤忠蒙谤：{c['victim']} 遭弹劾",
            "narrative": lambda c: (
                f"{c['victim_office']}{c['victim']}素以清节孤忠著称，今为{c['accuser']}所劾，"
                f"指其种种不法。然其平日操守，朝野共见。陛下信谁？"),
            "choices": [
                {"key": "protect", "label": lambda c: f"力保{c['victim']}，斥言官风闻",
                 "hint": "护忠臣：得其死力与清流之心，但坐实『君护短』、政敌不平",
                 "effect": lambda c: {"shi": 1, "renshi": 4,
                                      "char": [{"name": c["victim"], "emp_trust": 10, "grievance": -6}],
                                      "ripple": [{"name": c["accuser"], "kind": "oust"}],
                                      "log": f"力保{c['victim']}，斥{c['accuser']}风闻言事。"}},
                {"key": "investigate", "label": lambda c: "下廷议查核",
                 "hint": "走程序：看似公允，却让孤臣寒心、任事者更怕出头",
                 "effect": lambda c: {"renshi": -4,
                                      "char": [{"name": c["victim"], "emp_trust": -6, "grievance": 6}],
                                      "log": f"{c['victim']}被劾事，下廷议查核。"}},
                {"key": "shelve", "label": lambda c: "留中，两不偏袒",
                 "hint": "拖：暂稳，但忠臣见疑、谗者得计",
                 "effect": lambda c: {"shi": -1,
                                      "char": [{"name": c["victim"], "emp_trust": -3}],
                                      "log": f"{c['victim']}被劾事留中。"}},
            ],
        },
        {
            "id": "faction_packing",
            "priority": 20,
            "when": _packed_faction,
            "title": lambda c: f"朋党盈廷：{c['faction']} 坐大",
            "narrative": lambda c: (
                f"{c['faction']}近来气焰日炽，要害之地多其党羽，进退人物渐由其口。"
                f"长此以往，恐成尾大不掉之势。陛下何以处之？"),
            "choices": [
                {"key": "curb", "label": lambda c: f"裁抑{c['faction']}，调离要津",
                 "hint": "乾纲独断：长君威、削其势，但激其怨、短期任事跌",
                 "effect": lambda c: {"shi": 4, "renshi": -4,
                                      "faction": {c["faction"]: {"satisfaction": -10, "leverage": -8}},
                                      "log": f"裁抑{c['faction']}，调离要津。"}},
                {"key": "appease", "label": lambda c: f"暂加恩抚，徐图分化",
                 "hint": "怀柔：稳其心、保任事，但纵其坐大、伤威",
                 "effect": lambda c: {"shi": -2,
                                      "faction": {c["faction"]: {"satisfaction": 5, "leverage": 3}},
                                      "log": f"暂加恩抚于{c['faction']}。"}},
                {"key": "balance", "label": lambda c: "掺沙子，引他党制衡",
                 "hint": "以党制党：势稳，但党争更烈（民心微损）",
                 "effect": lambda c: {"shi": 1,
                                      "faction": {c["faction"]: {"satisfaction": -3}},
                                      "metrics": {"民心": -1},
                                      "log": f"引他党掺沙子，制衡{c['faction']}。"}},
            ],
        },
    ]


# ── 冷却 / 待决持久化 ─────────────────────────────────────────────────────────

def _cooldowns(db: GameDB) -> Dict[str, int]:
    try:
        d = json.loads(db.kv_get(KV_COOLDOWN) or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _on_cooldown(db: GameDB, def_id: str, day: int) -> bool:
    return (day - int(_cooldowns(db).get(def_id, -10**9))) < COOLDOWN_DAYS


def get_pending(db: GameDB) -> Optional[Dict[str, object]]:
    try:
        d = json.loads(db.kv_get(KV_PENDING) or "")
        return d if isinstance(d, dict) else None
    except (ValueError, TypeError):
        return None


def _choices_of(d: Dict[str, object], ctx: Dict[str, object]) -> List[Dict[str, object]]:
    """choices 可为静态 list 或 据 ctx 动态生成的 callable；带 available(ctx) 的选项条件不满足则隐去。"""
    ch = d["choices"]
    items = ch(ctx) if callable(ch) else ch
    out = []
    for c in items:
        avail = c.get("available")
        if avail is not None and not _call(avail, ctx):
            continue
        out.append(c)
    return out


def _call(v, ctx):
    """label/hint 可能是 fn(ctx)、无参 fn 或常量。统一求值。"""
    if callable(v):
        try:
            return v(ctx)
        except TypeError:
            return v()
    return v


def pending_payload(db: GameDB) -> Optional[Dict[str, object]]:
    """前端用：把待决事件按 ctx 渲染成 {id,title,narrative,choices:[{key,label,hint}]}。"""
    p = get_pending(db)
    if not p:
        return None
    ctx = p.get("ctx") or {}
    d = next((x for x in _defs() if x["id"] == p.get("id")), None)
    if d is None:
        return None
    return {
        "id": d["id"],
        "title": d["title"](ctx),
        "narrative": d["narrative"](ctx),
        "choices": [
            {
                "key": ch["key"],
                "label": _call(ch["label"], ctx),
                "hint": _call(ch.get("hint", ""), ctx),
                "effects": _preview_effects(_call(ch.get("effect", {}), ctx)),
            }
            for ch in _choices_of(d, ctx)
        ],
    }


def evaluate_decisions(db: GameDB, state: GameState, day: int) -> Optional[Dict[str, object]]:
    """无待决时，按优先级扫描触发；命中则置为待决并返回 payload。一次至多一道。"""
    if get_pending(db):
        return None
    for d in sorted(_defs(), key=lambda x: -int(x["priority"])):
        if str(d.get("cooldown") or "") != "ctx" and _on_cooldown(db, str(d["id"]), day):
            continue
        ctx = d["when"](db)
        if ctx:
            cooldown_key = str(ctx.get("cooldown_id") or d["id"]) if isinstance(ctx, dict) else str(d["id"])
            if _on_cooldown(db, cooldown_key, day):
                continue
            db.kv_set(KV_PENDING, json.dumps({
                "id": d["id"],
                "ctx": ctx,
                "day": day,
                "cooldown_key": cooldown_key,
            }, ensure_ascii=False))
            return pending_payload(db)
    return None


def resolve_decision(db: GameDB, state: GameState, choice_key: str, day: int) -> Dict[str, object]:
    p = get_pending(db)
    if not p:
        return {"ok": False, "message": "当前无待决之事。"}
    ctx = p.get("ctx") or {}
    d = next((x for x in _defs() if x["id"] == p.get("id")), None)
    if d is None:
        db.kv_set(KV_PENDING, "")
        return {"ok": False, "message": "待决事件已失效。"}
    choice = next((c for c in _choices_of(d, ctx) if c["key"] == choice_key), None)
    if choice is None:
        return {"ok": False, "message": f"无此抉择：{choice_key}"}
    effect = _call(choice["effect"], ctx)
    effects = _preview_effects(effect)
    summary = _apply_effect(db, state, effect, day)
    cds = _cooldowns(db)
    cds[str(p.get("cooldown_key") or d["id"])] = day
    db.kv_set(KV_COOLDOWN, json.dumps(cds, ensure_ascii=False))
    db.kv_set(KV_PENDING, "")
    return {
        "ok": True,
        "title": d["title"](ctx),
        "choice": _call(choice["label"], ctx),
        "effect": summary,
        "effects": effects,
    }
