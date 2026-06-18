"""抉择事件（CK3 化 P2）：朝局演化到某种张力，弹出一道「请陛下裁断」的抉择——
2-4 个选项各有真实后果（势/任事/民心/派系/好感网涟漪），玩家从"被动看推演"变为"主动落子"。

事件**涌现自活的宫廷**：宿敌互讦、孤忠蒙谤、朋党盈廷——触发条件读 court 的好感网与派系状态，
后果亦回写好感网（ripple）。一次至多一道待决；同类事件 60 日内不重复（cooldown）。
"""

from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState, period_label
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


def _revive_goal_conditions(goal: Dict[str, object], note: str) -> List[Dict[str, object]]:
    conditions: List[Dict[str, object]] = []
    for raw in goal.get("conditions") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if str(item.get("status") or "") in {"pending", "failed", "blocked"}:
            item["status"] = "pending"
            item["evidence"] = note
        conditions.append(item)
    return conditions


def _resource_goal_conditions(goal: Dict[str, object], note: str, support_tasks: object) -> List[Dict[str, object]]:
    conditions = _revive_goal_conditions(goal, note)
    existing = {str(item.get("description") or "").strip() for item in conditions if isinstance(item, dict)}
    for raw in support_tasks if isinstance(support_tasks, list) else []:
        text = str(raw or "").strip()[:180]
        if not text or text in existing:
            continue
        conditions.append({"description": text, "status": "pending", "evidence": note})
        existing.add(text)
    return conditions


def _fail_goal_conditions(goal: Dict[str, object], note: str) -> List[Dict[str, object]]:
    conditions: List[Dict[str, object]] = []
    for raw in goal.get("conditions") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if str(item.get("status") or "") == "pending":
            item["status"] = "failed"
            item["evidence"] = note
        conditions.append(item)
    return conditions


def _apply_goal_action(db: GameDB, state: GameState, item: Dict[str, object], day: int) -> str:
    goal_id = _intish(item.get("id") or item.get("goal_id"))
    if goal_id <= 0:
        return ""
    goal = db.get_conversation_goal(goal_id)
    if not goal:
        return ""
    minister = str(goal.get("minister_name") or "")
    title = str(goal.get("title") or goal.get("target_text") or "旧约").strip()
    action = str(item.get("action") or "extend").strip()
    note = str(item.get("evidence") or item.get("note") or "").strip()[:240]
    last_delta = dict(goal.get("last_delta") or {})
    last_delta["court_decision"] = {
        "source": "goal_obligation_help",
        "action": action,
        "turn": int(state.turn),
        "day": int(day),
        "note": note,
    }
    if action == "extend":
        months = max(1, min(12, _intish(item.get("months"), 1)))
        evidence = note or f"御前裁断：{minister}「{title}」展限{months}月，仍须补证复命。"
        due_turn = int(state.turn) + months
        db.update_conversation_goal(
            goal_id,
            state=state,
            event_kind="goal_extended",
            event_summary=evidence,
            status="waiting_conditions",
            condition_status="pending",
            expires_turn=due_turn + 2,
            conditions_json=_revive_goal_conditions(goal, evidence),
            blockers_json=[],
            last_delta_json={**last_delta, "due_turn": due_turn},
        )
        db.record_log(state, evidence)
        return f"{minister}旧约展限{months}月"
    if action == "resource":
        months = max(1, min(12, _intish(item.get("months"), 1)))
        evidence = note or f"御前裁断：拨助{minister}「{title}」，限{months}月内以新资源交账。"
        due_turn = int(state.turn) + months
        support_tasks = item.get("support_tasks") or []
        db.update_conversation_goal(
            goal_id,
            state=state,
            event_kind="goal_resource_support",
            event_summary=evidence,
            status="waiting_conditions",
            condition_status="pending",
            expires_turn=due_turn + 2,
            conditions_json=_resource_goal_conditions(goal, evidence, support_tasks),
            blockers_json=[],
            last_delta_json={
                **last_delta,
                "due_turn": due_turn,
                "support_tasks": support_tasks if isinstance(support_tasks, list) else [],
            },
        )
        db.record_log(state, evidence)
        return f"{minister}得助复办{months}月"
    if action == "fail":
        evidence = note or f"御前裁断：{minister}「{title}」旧约失期，按负约追责。"
        db.update_conversation_goal(
            goal_id,
            state=state,
            event_kind="goal_failed",
            event_summary=evidence,
            status="expired",
            condition_status="failed",
            conditions_json=_fail_goal_conditions(goal, evidence),
            last_delta_json=last_delta,
        )
        db.record_log(state, evidence)
        return f"{minister}旧约追责"
    return ""


def _is_audience_bargain_goal(goal: Dict[str, object]) -> bool:
    action_kind = str(goal.get("action_kind") or "").strip()
    title = str(goal.get("title") or goal.get("target_text") or "").strip()
    target = str(goal.get("target_text") or "").strip()
    last_delta = goal.get("last_delta") if isinstance(goal.get("last_delta"), dict) else {}
    source = str(last_delta.get("source") or "").strip()
    return (
        action_kind == "audience_bargain"
        or "audience_bargain" in source
        or "audience_bargain_commitment" in source
        or "旧账索证" in title
        or "兑现旧账" in title
        or "旧账索证" in target
        or "兑现旧账" in target
    )


def _goal_help_title(ctx: Dict[str, object]) -> str:
    if ctx.get("is_bargain"):
        return f"旧账逼问：{ctx['minister']}请清前账"
    return f"旧约求裁：{ctx['minister']}请陛下给话"


def _goal_help_narrative(ctx: Dict[str, object]) -> str:
    if ctx.get("is_bargain"):
        source_title = str(ctx.get("context_title") or ctx.get("title") or "前番奏对").strip()
        return (
            f"{ctx['office']}{ctx['minister']}因前番御前旧账「{source_title}」入殿求裁。"
            f"这不是寻常差使失期，而是陛下曾在奏对中亲自逼出的证据、兑现或让步；"
            f"眼下信任{ctx['trust']}、怨望{ctx['grievance']}，"
            + (
                f"同党{ '、'.join(ctx['allies']) }替他说情，"
                if ctx.get("allies") else ""
            )
            + (
                f"政敌{ '、'.join(ctx['rivals']) }等着把这笔账做成把柄，"
                if ctx.get("rivals") else ""
            )
            + "若护持，像是陛下替人抹去亲口旧账；若追责，又会让敢接话的人寒心。"
              "要给台阶、给资源、逼证据，还是当殿作废问责？"
        )
    return (
        f"{ctx['office']}{ctx['minister']}因「{ctx['title']}」入殿求见。"
        f"这笔{ctx['label']}已经发酵，眼下信任{ctx['trust']}、怨望{ctx['grievance']}，"
        + (
            f"同党{ '、'.join(ctx['allies']) }替他说情，"
            if ctx.get("allies") else ""
        )
        + (
            f"政敌{ '、'.join(ctx['rivals']) }则等着看笑话，"
            if ctx.get("rivals") else ""
        )
        + "若护持，恐开脱责之门；若公开申饬，旧约账本立住，却会寒任事之心。"
          "陛下要如何把这件旧约收束成可玩的政治后果？"
    )


def _goal_help_label(ctx: Dict[str, object], key: str) -> str:
    minister = str(ctx.get("minister") or "").strip()
    if ctx.get("is_bargain"):
        labels = {
            "protect": f"认前话，准{minister}补清旧账",
            "resource_support": f"拨人查证，令{minister}带责兑现",
            "demand_evidence": f"限{minister}一月补齐旧账证据",
            "public_rebuke": f"明示旧账作废，申饬{minister}负约",
            "self_prove": f"不护不罚，令{minister}自证旧账",
        }
        return labels[key]
    labels = {
        "protect": f"先护持{minister}，准其补办",
        "resource_support": f"拨给人手文书，令{minister}带责复办",
        "demand_evidence": f"限{minister}一月补证复命",
        "public_rebuke": f"公开申饬{minister}负约",
        "self_prove": f"不护不罚，令{minister}自行证明",
    }
    return labels[key]


def _goal_help_hint(ctx: Dict[str, object], key: str) -> str:
    if ctx.get("is_bargain"):
        hints = {
            "protect": "给台阶：保住御前旧话的连续性；但政敌会说陛下亲口旧账也能抹",
            "resource_support": "把空口旧账变成查证差使：耗小钱粮，但下月必须拿证据和结果",
            "demand_evidence": "折中逼证：不立刻治罪，却把旧账证据压力写回履约账本",
            "public_rebuke": "把旧账做成规矩：君威上涨，但本人和举主同党会记下这笔寒心账",
            "self_prove": "不给护身符也不给刀：留余地，但再拖会继续发酵成怨",
        }
        return hints[key]
    hints = {
        "protect": "给台阶：人心回暖、同党安心；但会显得皇帝替人抹账，政敌不服",
        "resource_support": "给真实资源，也给真实责任：任事心回升、国库小耗；若再无结果，后续追责更重",
        "demand_evidence": "折中：不立即治罪，但把证据压力写回旧约，后续仍会发酵",
        "public_rebuke": "把账本做实：立规矩、涨君威；本人和同党会记怨，政敌得势",
        "self_prove": "不给护身符：保规矩、留余地；本人压力仍在，若再拖会继续反噬",
    }
    return hints[key]


def _goal_help_evidence(ctx: Dict[str, object], key: str) -> str:
    minister = str(ctx.get("minister") or "").strip()
    title = str(ctx.get("title") or "旧约").strip()
    if ctx.get("is_bargain"):
        source_title = str(ctx.get("context_title") or title).strip()
        evidence = {
            "protect": f"御前认前番旧账「{source_title}」仍须清结，准{minister}补办两月，但不得再以空话搪塞。",
            "resource_support": f"御前拨给{minister}人手文书查证旧账「{source_title}」，限一月交出证据、兑现结果与掣肘名单；再误则重责。",
            "demand_evidence": f"御前责{minister}一月内补齐旧账「{source_title}」的人证账册、兑现进度与责任边界。",
            "public_rebuke": f"御前明示旧账作废并公开申饬：{minister}前番承接「{source_title}」逾期不明，按负约负责。",
            "self_prove": f"御前不护不罚，令{minister}自行证明前番旧账「{source_title}」仍可交代。",
        }
        return evidence[key]
    evidence = {
        "protect": f"御前护持{minister}，准其就「{title}」补办两月，但仍须交账。",
        "resource_support": f"御前拨给{minister}人手文书办理「{title}」，但限一月交账；再误则重责。",
        "demand_evidence": f"御前责{minister}一月内补足「{title}」证据、责任边界与复命说法。",
        "public_rebuke": f"御前公开申饬：{minister}「{title}」逾期不明，按旧约负责。",
        "self_prove": f"御前不护不罚，令{minister}自行证明「{title}」仍可交账。",
    }
    return evidence[key]


def _goal_help_log(ctx: Dict[str, object], key: str) -> str:
    minister = str(ctx.get("minister") or "").strip()
    title = str(ctx.get("title") or "旧约").strip()
    if ctx.get("is_bargain"):
        logs = {
            "protect": f"旧账逼问：认前话护持{minister}，准其补清「{title}」。",
            "resource_support": f"旧账逼问：拨助{minister}查证兑现「{title}」，限一月交账。",
            "demand_evidence": f"旧账逼问：限{minister}一月补齐证据「{title}」。",
            "public_rebuke": f"旧账逼问：作废旧账并申饬{minister}负约「{title}」。",
            "self_prove": f"旧账逼问：令{minister}自证旧账「{title}」。",
        }
        return logs[key]
    logs = {
        "protect": f"旧约求裁：护持{minister}，准其补办「{title}」。",
        "resource_support": f"旧约求裁：拨助{minister}复办「{title}」，限一月交账。",
        "demand_evidence": f"旧约求裁：限{minister}一月补证复命「{title}」。",
        "public_rebuke": f"旧约求裁：公开申饬{minister}负约「{title}」。",
        "self_prove": f"旧约求裁：令{minister}自行证明「{title}」。",
    }
    return logs[key]


def _append_secret_order_court_line(state: GameState, prev: str, label: str, note: str) -> str:
    stamp = f"〔{period_label(state.year, state.period)}〕[{label}] "
    lines = [ln for ln in str(prev or "").split("\n") if ln.strip()]
    lines.append(f"{stamp}{str(note or '').strip()[:300]}")
    return "\n".join(lines)


def _apply_secret_order_action(db: GameDB, state: GameState, item: Dict[str, object], day: int) -> str:
    order_id = _intish(item.get("id") or item.get("order_id"))
    if order_id <= 0:
        return ""
    row = db.conn.execute(
        "SELECT id, title, status, result FROM secret_orders WHERE id=?",
        (order_id,),
    ).fetchone()
    if row is None:
        return ""
    status = str(row["status"] or "")
    if status not in {"active", "pending_review"}:
        return ""
    action = str(item.get("action") or "close").strip()
    title = str(row["title"] or "密令")
    label = str(item.get("label") or "圣裁").strip()[:24] or "圣裁"
    note = str(item.get("note") or "").strip() or "御前裁断，归档存照。"
    result = _append_secret_order_court_line(state, str(row["result"] or ""), label, note)

    if action == "extend":
        months = max(1, min(12, _intish(item.get("months"), 1)))
        due_turn = int(state.turn) + months
        db.conn.execute(
            """
            UPDATE secret_orders
            SET status='active', due_turn=?, result=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (due_turn, result, order_id),
        )
        return f"密令续查{months}月：{title}"

    if action == "close":
        new_status = str(item.get("status") or "done").strip()
        if new_status not in {"done", "failed"}:
            new_status = "done"
        db.conn.execute(
            """
            UPDATE secret_orders
            SET status=?, result=?, turn_closed=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (new_status, result, int(state.turn), order_id),
        )
        verb = "密令结案" if new_status == "done" else "密令封存"
        return f"{verb}：{title}"

    return ""


def _apply_memory_action(db: GameDB, state: GameState, item: Dict[str, object], day: int) -> str:
    subject_id = str(item.get("subject_id") or item.get("name") or "").strip()
    if not subject_id:
        return ""
    event_type = str(item.get("event_type") or "court_memory").strip()[:40]
    title = str(item.get("title") or "御前记忆").strip()[:80]
    source_id = str(item.get("source_id") or f"court_event:{event_type}:{subject_id}:{state.turn}").strip()[:120]
    memory_id = db.upsert_event_memory(
        state,
        subject_type=str(item.get("subject_type") or "character"),
        subject_id=subject_id,
        event_type=event_type,
        title=title,
        cause=str(item.get("cause") or "").strip()[:160],
        process=str(item.get("process") or "").strip()[:160],
        outcome=str(item.get("outcome") or "").strip()[:160],
        sentiment=str(item.get("sentiment") or "neutral"),
        importance=max(1, min(5, _intish(item.get("importance"), 3))),
        tags=[str(tag) for tag in (item.get("tags") or [])],
        source_kind=str(item.get("source_kind") or "court_event"),
        source_id=source_id,
        expires_turn=item.get("expires_turn") if isinstance(item.get("expires_turn"), int) else None,
    )
    if memory_id:
        return str(item.get("summary") or f"{subject_id}旧恩入账").strip()[:80]
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
    for goal in (eff.get("goals") or []):
        if isinstance(goal, dict):
            result = _apply_goal_action(db, state, goal, day)
            if result:
                parts.append(result)
    for so in (eff.get("secret_orders") or []):
        if isinstance(so, dict):
            result = _apply_secret_order_action(db, state, so, day)
            if result:
                parts.append(result)
    for mem in (eff.get("memories") or []):
        if isinstance(mem, dict):
            result = _apply_memory_action(db, state, mem, day)
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
    for goal in (eff.get("goals") or []):
        if not isinstance(goal, dict):
            continue
        action = str(goal.get("action") or "")
        if action == "extend":
            add("goal", f"旧约展限 {int(goal.get('months') or 1)}月", "warn")
        elif action == "resource":
            add("goal", f"旧约拨助 {int(goal.get('months') or 1)}月", "good")
        elif action == "fail":
            add("goal", "旧约追责", "bad")
    for so in (eff.get("secret_orders") or []):
        if not isinstance(so, dict):
            continue
        action = str(so.get("action") or "")
        if action == "extend":
            add("secret_order", f"密令续查 {int(so.get('months') or 1)}月", "warn")
        elif action == "close":
            status = str(so.get("status") or "done")
            add("secret_order", "密令结案" if status == "done" else "密令封存", "good" if status == "done" else "bad")
    for mem in (eff.get("memories") or []):
        if not isinstance(mem, dict):
            continue
        subject = str(mem.get("subject_id") or mem.get("name") or "").strip()
        event_type = str(mem.get("event_type") or "")
        label = "旧恩入账" if event_type == "imperial_favor" else "记忆入账"
        add("memory", f"{subject}{label}" if subject else label, "good")
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
        pinned = [item for item in out if item.get("kind") in {"obligation", "memory"}]
        if pinned:
            room = max(0, limit - len(pinned))
            return [item for item in out if item.get("kind") not in {"obligation", "memory"}][:room] + pinned[:limit]
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


def _goal_obligation_help(db: GameDB) -> Optional[Dict[str, object]]:
    """Blocked conversation goals can become active pleas for imperial handling."""

    rows = db.list_conversation_goals(statuses=["blocked"], limit=100)
    best: Optional[Dict[str, object]] = None
    best_score = -10**9
    for goal in rows:
        goal_id = int(goal.get("id") or 0)
        minister = str(goal.get("minister_name") or "").strip()
        if goal_id <= 0 or not minister:
            continue
        last_delta = goal.get("last_delta") if isinstance(goal.get("last_delta"), dict) else {}
        pressure = last_delta.get("monthly_pressure") if isinstance(last_delta.get("monthly_pressure"), dict) else {}
        if not pressure:
            continue
        row = db.conn.execute(
            """
            SELECT name, office, faction, ability, integrity, emp_trust, grievance
            FROM characters
            WHERE name=? AND status='active' AND power_id='ming'
            LIMIT 1
            """,
            (minister,),
        ).fetchone()
        if row is None:
            continue
        label = str(pressure.get("label") or "奏对旧约").strip()
        kind = str(pressure.get("kind") or "").strip()
        age = _intish(pressure.get("age"))
        is_bargain = _is_audience_bargain_goal(goal)
        context_title = str(last_delta.get("context_title") or goal.get("title") or goal.get("target_text") or "").strip()
        network_touch = pressure.get("network_touch") if isinstance(pressure.get("network_touch"), dict) else {}
        allies = [str(item) for item in (network_touch.get("allies") or []) if str(item).strip()]
        rivals = [str(item) for item in (network_touch.get("rivals") or []) if str(item).strip()]
        score = (
            30
            + age
            + int(row["grievance"] or 0) // 8
            + (8 if kind == "overdue" else 0)
            + (10 if is_bargain else 0)
            + len(allies)
            + len(rivals)
        )
        if score <= best_score:
            continue
        best = {
            "goal_id": goal_id,
            "cooldown_id": f"goal_obligation_help:{goal_id}",
            "minister": minister,
            "office": str(row["office"] or ""),
            "faction": str(row["faction"] or ""),
            "ability": int(row["ability"] or 50),
            "integrity": int(row["integrity"] or 50),
            "trust": int(row["emp_trust"] or 0),
            "grievance": int(row["grievance"] or 0),
            "title": str(goal.get("title") or goal.get("target_text") or "未竟奏对"),
            "target_text": str(goal.get("target_text") or goal.get("title") or ""),
            "label": label,
            "pressure_kind": kind,
            "is_bargain": is_bargain,
            "context_title": context_title,
            "age": age,
            "allies": allies[:3],
            "rivals": rivals[:3],
        }
        best_score = score
    return best


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


def _latest_secret_order_line(text: object) -> str:
    lines = [ln.strip() for ln in str(text or "").split("\n") if ln.strip()]
    if not lines:
        return ""
    return lines[-1][-180:]


def _secret_order_tags(raw: object) -> List[str]:
    try:
        data = json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        data = []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()][:6]


def _secret_order_review(db: GameDB) -> Optional[Dict[str, object]]:
    """到期密令核议：暗线不能只进日志，要变成玩家亲自担责的公开/密押/问责/封存选择。"""

    row = db.conn.execute(
        """
        SELECT so.id, so.minister_name, so.title, so.content, so.tags, so.importance,
               so.result, so.sim_note, so.turn_issued, so.due_turn,
               c.office, c.faction, c.emp_trust, c.grievance, c.ability, c.integrity
        FROM secret_orders so
        LEFT JOIN characters c ON c.name=so.minister_name
        WHERE so.status='pending_review'
        ORDER BY so.importance DESC,
                 CASE WHEN so.due_turn>0 THEN so.due_turn ELSE so.turn_issued END ASC,
                 so.id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    sim_note = str(row["sim_note"] or "")
    result = str(row["result"] or "")
    leak = any(token in sim_note for token in ("泄", "走漏", "风声", "反弹", "惊动"))
    thin = not any(token in result for token in ("证", "账", "供", "名单", "实据", "拿获", "查明"))
    tags = _secret_order_tags(row["tags"])
    return {
        "order_id": int(row["id"]),
        "cooldown_id": f"secret_order_review:{int(row['id'])}",
        "minister": str(row["minister_name"] or ""),
        "office": str(row["office"] or ""),
        "faction": str(row["faction"] or ""),
        "title": str(row["title"] or "密令"),
        "content": str(row["content"] or ""),
        "tags": "、".join(tags),
        "importance": int(row["importance"] or 4),
        "claim": _latest_secret_order_line(result) or "承办人只称事已可办，未附详证。",
        "sim_note": _latest_secret_order_line(sim_note),
        "leak": leak,
        "thin": thin,
        "trust": int(row["emp_trust"] or 0),
        "grievance": int(row["grievance"] or 0),
        "ability": int(row["ability"] or 50),
        "integrity": int(row["integrity"] or 50),
    }


def _secret_order_faction_effect(
    ctx: Dict[str, object],
    *,
    sat: int = 0,
    lev: int = 0,
    heat: int = 0,
) -> Dict[str, Dict[str, int]]:
    faction = _meaningful_faction(ctx.get("faction"))
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


def _active_goal_exists(db: GameDB, source_fragment: str) -> bool:
    row = db.conn.execute(
        """
        SELECT 1
        FROM conversation_goals
        WHERE status IN ('active', 'waiting_conditions')
          AND last_delta_json LIKE ?
        LIMIT 1
        """,
        (f"%{source_fragment}%",),
    ).fetchone()
    return row is not None


def _private_distress_target(db: GameDB, actor: str, agenda_kind: str) -> Dict[str, object]:
    if agenda_kind in {"protect", "climb", "entrench"}:
        rel = db.conn.execute(
            """
            SELECT r.b_name AS name, r.opinion, r.basis, c.office, c.faction
            FROM relationships r
            JOIN characters c ON c.name=r.b_name
            WHERE r.a_name=?
              AND r.opinion>=24
              AND c.status='active'
              AND c.power_id='ming'
              AND c.office_type!='后宫'
            ORDER BY r.opinion DESC
            LIMIT 1
            """,
            (actor,),
        ).fetchone()
    else:
        rel = db.conn.execute(
            """
            SELECT r.b_name AS name, r.opinion, r.basis, c.office, c.faction
            FROM relationships r
            JOIN characters c ON c.name=r.b_name
            WHERE r.a_name=?
              AND r.opinion<=-24
              AND c.status='active'
              AND c.power_id='ming'
              AND c.office_type!='后宫'
            ORDER BY r.opinion ASC
            LIMIT 1
            """,
            (actor,),
        ).fetchone()
    if rel is None:
        return {"name": "", "opinion": 0, "basis": "", "office": "", "faction": ""}
    return {
        "name": str(rel["name"] or ""),
        "opinion": int(rel["opinion"] or 0),
        "basis": str(rel["basis"] or ""),
        "office": str(rel["office"] or ""),
        "faction": str(rel["faction"] or ""),
    }


def _private_distress_kind(kind: str, target: str) -> Dict[str, str]:
    if kind == "protect":
        return {
            "label": "护持故旧",
            "stake": f"门生故旧{target}被人逼迫" if target else "本党门生被人逼迫",
            "ask": "求陛下给一句护持，莫使清议与私怨逼死人。",
        }
    if kind == "survive":
        return {
            "label": "自保求生",
            "stake": f"政敌{target}步步相逼" if target else "旧案风声渐紧",
            "ask": "求陛下留一条自明身家的路，不要让廷臣一拥而上。",
        }
    if kind == "revenge":
        return {
            "label": "借手伸怨",
            "stake": f"与{target}旧怨未平" if target else "夙怨未平",
            "ask": "求陛下准其追究旧案，说是为朝廷，其实也为一口气。",
        }
    if kind == "entrench":
        return {
            "label": "保境避祸",
            "stake": f"地方/军镇差使牵连{target or '故旧'}" if target else "地方/军镇差使牵连甚广",
            "ask": "求陛下给边界、银粮或人手，好让他不至独背黑锅。",
        }
    return {
        "label": "求恩求进",
        "stake": f"想替{target}争一个台阶" if target else "想替自己争一个台阶",
        "ask": "求陛下给个名分，日后愿以差使报效。",
    }


def _private_distress(db: GameDB) -> Optional[Dict[str, object]]:
    """NPC 私人困局：不是大案，却是 CK3 式角色麻烦，救与不救都会变成人情账。"""

    rows = db.conn.execute(
        """
        SELECT c.name, c.office, c.faction, c.ability, c.integrity,
               c.emp_trust, c.grievance, a.kind, a.title, a.intensity
        FROM characters c
        JOIN npc_agendas a ON a.name=c.name AND a.status='active'
        WHERE c.status='active'
          AND c.power_id='ming'
          AND c.office_type!='后宫'
          AND c.name!='崇祯'
          AND a.kind IN ('protect','survive','revenge','climb','entrench')
          AND (
            c.grievance BETWEEN 42 AND 77
            OR c.emp_trust BETWEEN 29 AND 48
            OR a.intensity>=72
          )
        ORDER BY
          (a.intensity + c.grievance + (60 - c.emp_trust)) DESC,
          c.ability DESC
        LIMIT 16
        """
    ).fetchall()
    for row in rows:
        actor = str(row["name"] or "")
        if _active_goal_exists(db, f"private_distress:{actor}:"):
            continue
        kind = str(row["kind"] or "")
        target = _private_distress_target(db, actor, kind)
        target_name = str(target.get("name") or "")
        if not target_name and kind in {"protect", "survive", "revenge"}:
            continue
        if target_name:
            formal = db.conn.execute(
                """
                SELECT 1
                FROM memorials
                WHERE status='pending'
                  AND ref_kind='character'
                  AND (
                    (author_name=? AND ref_id=?)
                    OR (author_name=? AND ref_id=?)
                  )
                LIMIT 1
                """,
                (actor, target_name, target_name, actor),
            ).fetchone()
            if formal is not None:
                continue
        source_id = f"private_distress:{actor}:{target_name or kind}"
        recent = db.conn.execute(
            """
            SELECT 1
            FROM event_memories
            WHERE subject_type='character'
              AND subject_id=?
              AND source_kind='court_event'
              AND source_id LIKE ?
              AND (expires_turn IS NULL OR expires_turn>=?)
            LIMIT 1
            """,
            (actor, f"{source_id}%", int(db.load_state().turn)),
        ).fetchone()
        if recent is not None:
            continue
        flavor = _private_distress_kind(kind, target_name)
        return {
            "actor": actor,
            "office": str(row["office"] or ""),
            "faction": str(row["faction"] or ""),
            "ability": int(row["ability"] or 50),
            "integrity": int(row["integrity"] or 50),
            "trust": int(row["emp_trust"] or 0),
            "grievance": int(row["grievance"] or 0),
            "agenda_kind": kind,
            "agenda_title": str(row["title"] or ""),
            "intensity": int(row["intensity"] or 0),
            "target": target_name,
            "target_office": str(target.get("office") or ""),
            "target_faction": str(target.get("faction") or ""),
            "relation_basis": str(target.get("basis") or "旧情"),
            "relation_opinion": int(target.get("opinion") or 0),
            "plea_label": flavor["label"],
            "stake": flavor["stake"],
            "ask": flavor["ask"],
            "cooldown_id": f"private_distress:{actor}",
            "source_id": source_id,
        }
    return None


def _favor_debt_pressure(db: GameDB) -> Optional[Dict[str, object]]:
    """Imperial favors can mature into an active court dilemma."""

    try:
        state = db.load_state()
    except Exception:
        state = GameState()
    turn = int(getattr(state, "turn", 0) or 0)
    rows = db.conn.execute(
        """
        SELECT m.id, m.subject_id, m.title, m.cause, m.process, m.outcome,
               m.importance, m.source_id, m.turn,
               c.office, c.faction, c.ability, c.integrity, c.emp_trust, c.grievance
        FROM event_memories m
        JOIN characters c ON c.name=m.subject_id
        WHERE m.subject_type='character'
          AND m.event_type='imperial_favor'
          AND (m.expires_turn IS NULL OR m.expires_turn>=?)
          AND m.turn < ?
          AND c.status='active'
          AND c.power_id='ming'
          AND c.office_type!='后宫'
          AND c.name!='崇祯'
        ORDER BY m.importance DESC, (?-m.turn) DESC, c.grievance DESC, c.emp_trust DESC
        LIMIT 24
        """,
        (turn, turn, turn),
    ).fetchall()
    for row in rows:
        memory_id = int(row["id"] or 0)
        actor = str(row["subject_id"] or "").strip()
        if memory_id <= 0 or not actor:
            continue
        source = str(row["source_id"] or "").strip()
        if source and _active_goal_exists(db, source):
            continue
        if _active_goal_exists(db, f"favor_debt:{memory_id}:"):
            continue
        rivals = court.rivals_of(db, actor, limit=2, threshold=-18)
        allies = court.allies_of(db, actor, limit=2, threshold=18)
        age = max(1, turn - int(row["turn"] or turn))
        return {
            "memory_id": memory_id,
            "cooldown_id": f"favor_debt_pressure:{memory_id}",
            "actor": actor,
            "office": str(row["office"] or ""),
            "faction": str(row["faction"] or ""),
            "ability": int(row["ability"] or 50),
            "integrity": int(row["integrity"] or 50),
            "trust": int(row["emp_trust"] or 0),
            "grievance": int(row["grievance"] or 0),
            "title": str(row["title"] or "旧恩未报"),
            "cause": str(row["cause"] or ""),
            "process": str(row["process"] or ""),
            "outcome": str(row["outcome"] or ""),
            "importance": int(row["importance"] or 3),
            "age": age,
            "allies": [str(item.get("name") or "") for item in allies if str(item.get("name") or "").strip()][:2],
            "rivals": [str(item.get("name") or "") for item in rivals if str(item.get("name") or "").strip()][:2],
        }
    return None


def _patronage_accountability(db: GameDB) -> Optional[Dict[str, object]]:
    """举主担保：把“推荐来的人”从免费人才池变成可追责的人情链。"""

    rows = db.conn.execute(
        """
        SELECT r.a_name AS sponsor, r.b_name AS candidate, r.opinion AS sponsor_opinion,
               r.basis AS basis,
               COALESCE(rr.opinion, 0) AS candidate_opinion,
               s.office AS sponsor_office, s.faction AS sponsor_faction,
               s.emp_trust AS sponsor_trust, s.grievance AS sponsor_grievance,
               c.office AS candidate_office, c.faction AS candidate_faction,
               c.emp_trust AS candidate_trust, c.grievance AS candidate_grievance,
               c.ability AS candidate_ability, c.integrity AS candidate_integrity,
               c.summary AS candidate_summary
        FROM relationships r
        JOIN characters s ON s.name=r.a_name
        JOIN characters c ON c.name=r.b_name
        LEFT JOIN relationships rr ON rr.a_name=r.b_name AND rr.b_name=r.a_name
        WHERE r.a_name!=r.b_name
          AND r.opinion>=18
          AND s.status='active'
          AND c.status='active'
          AND s.power_id='ming'
          AND c.power_id='ming'
          AND s.office_type!='后宫'
          AND c.office_type!='后宫'
          AND s.name!='崇祯'
          AND c.name!='崇祯'
          AND (
            r.basis LIKE '%举荐%'
            OR r.basis LIKE '%荐取%'
            OR r.basis LIKE '%挑补%'
            OR r.basis LIKE '%入京%'
            OR c.summary LIKE '%举荐来源%'
            OR c.summary LIKE '%举主%'
          )
        ORDER BY
          CASE WHEN c.summary LIKE '%风险%' THEN 0 ELSE 1 END,
          r.opinion DESC,
          c.ability DESC,
          c.integrity ASC
        LIMIT 12
        """
    ).fetchall()
    for row in rows:
        sponsor = str(row["sponsor"] or "")
        candidate = str(row["candidate"] or "")
        if not sponsor or not candidate:
            continue
        source = f"patronage_accountability:{sponsor}:{candidate}"
        if _active_goal_exists(db, source):
            continue
        return {
            "sponsor": sponsor,
            "candidate": candidate,
            "basis": str(row["basis"] or "举荐入朝"),
            "sponsor_opinion": int(row["sponsor_opinion"] or 0),
            "candidate_opinion": int(row["candidate_opinion"] or 0),
            "sponsor_office": str(row["sponsor_office"] or ""),
            "sponsor_faction": str(row["sponsor_faction"] or ""),
            "sponsor_trust": int(row["sponsor_trust"] or 0),
            "sponsor_grievance": int(row["sponsor_grievance"] or 0),
            "candidate_office": str(row["candidate_office"] or ""),
            "candidate_faction": str(row["candidate_faction"] or ""),
            "candidate_trust": int(row["candidate_trust"] or 0),
            "candidate_grievance": int(row["candidate_grievance"] or 0),
            "candidate_ability": int(row["candidate_ability"] or 50),
            "candidate_integrity": int(row["candidate_integrity"] or 50),
            "candidate_summary": str(row["candidate_summary"] or "")[:180],
            "cooldown_id": source,
            "source_id": source,
        }
    return None


def _private_faction_effect(ctx: Dict[str, object], sat: int = 0, lev: int = 0, heat: int = 0) -> Dict[str, Dict[str, int]]:
    faction = _meaningful_faction(ctx.get("faction"))
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


def _private_opinion_effect(ctx: Dict[str, object], delta: int, basis: str) -> List[Dict[str, object]]:
    actor = str(ctx.get("actor") or "")
    target = str(ctx.get("target") or "")
    if not actor or not target:
        return []
    return [{"a": actor, "b": target, "delta": delta, "basis": basis}]


def _private_memory(ctx: Dict[str, object], choice: str, outcome: str, sentiment: str = "positive") -> Dict[str, object]:
    actor = str(ctx.get("actor") or "")
    return {
        "subject_id": actor,
        "event_type": "imperial_favor",
        "title": f"旧恩未报：{ctx.get('plea_label') or '御前求援'}",
        "cause": f"{ctx.get('stake') or '私事求援'}；{ctx.get('ask') or ''}",
        "process": f"御前裁断：{choice}",
        "outcome": outcome,
        "sentiment": sentiment,
        "importance": 4,
        "tags": ["私请", "旧恩", str(ctx.get("agenda_kind") or "")],
        "source_id": f"{ctx.get('source_id') or 'private_distress'}:{choice}",
        "summary": f"{actor}旧恩入账",
    }


def _favor_debt_memory(ctx: Dict[str, object], choice: str, outcome: str) -> Dict[str, object]:
    actor = str(ctx.get("actor") or "")
    return {
        "subject_id": actor,
        "event_type": "imperial_favor",
        "title": f"旧恩未报：{choice}",
        "cause": str(ctx.get("cause") or ctx.get("title") or "御前旧恩").strip(),
        "process": f"御前再裁旧恩：{choice}",
        "outcome": outcome,
        "sentiment": "positive",
        "importance": 4,
        "tags": ["旧恩", "再恩", str(ctx.get("faction") or "")],
        "source_kind": "court_decision",
        "source_id": f"favor_debt:{choice}:{ctx.get('memory_id') or actor}",
        "summary": f"{actor}旧恩入账",
    }


def _petition_favor_memory(ctx: Dict[str, object]) -> Dict[str, object]:
    petitioner = str(ctx.get("petitioner") or "").strip()
    rival = str(ctx.get("rival") or "").strip()
    faction = _meaningful_faction(ctx.get("faction"))
    basis = str(ctx.get("basis") or "求援请托").strip()
    tags = ["求援", "旧恩", "护持"]
    if faction:
        tags.append(faction)
    if rival:
        tags.append(rival)
    return {
        "subject_id": petitioner,
        "event_type": "imperial_favor",
        "title": "旧恩未报：求援护持",
        "cause": (
            f"{petitioner}因{basis}求陛下给台阶"
            + (f"，牵涉政敌{rival}" if rival else "")
            + "。"
        ),
        "process": "御前明旨护持，暂保其任事与名节。",
        "outcome": "此后召对可追问其如何还恩：领难差、交证据、收束政敌旧怨或替朝廷担责。",
        "sentiment": "positive",
        "importance": 4,
        "tags": [tag for tag in tags if tag],
        "source_kind": "court_decision",
        "source_id": f"imperial_petition:protect:{petitioner}:{rival or 'none'}",
        "summary": f"{petitioner}旧恩入账",
    }


def _resource_support_memory(ctx: Dict[str, object]) -> Dict[str, object]:
    minister = str(ctx.get("minister") or "").strip()
    title = str(ctx.get("title") or "旧约").strip()
    faction = _meaningful_faction(ctx.get("faction"))
    tags = [minister, "旧恩", "人情债", "资源复办", "旧约"]
    if faction:
        tags.append(faction)
    return {
        "subject_id": minister,
        "event_type": "imperial_favor",
        "title": "旧恩未报：资源复办",
        "cause": f"陛下拨给人手文书，助{minister}复办「{title}」。",
        "process": f"{minister}旧约受阻，御前没有只按负约问罪，而是给资源、给期限，也给更重交账责任。",
        "outcome": "此后召对须记得这笔资源复办之恩；若再误事，不许装作两清。",
        "sentiment": "positive",
        "importance": 4,
        "tags": [tag for tag in tags if tag],
        "source_kind": "court_decision",
        "source_id": f"resource_support:{ctx.get('goal_id') or minister}",
        "summary": f"{minister}旧恩入账",
    }


def _patronage_faction_effect(
    ctx: Dict[str, object],
    *,
    sponsor_sat: int = 0,
    sponsor_lev: int = 0,
    sponsor_heat: int = 0,
    candidate_sat: int = 0,
    candidate_lev: int = 0,
    candidate_heat: int = 0,
) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}

    def add(faction: object, sat: int, lev: int, heat: int) -> None:
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

    add(ctx.get("sponsor_faction"), sponsor_sat, sponsor_lev, sponsor_heat)
    add(ctx.get("candidate_faction"), candidate_sat, candidate_lev, candidate_heat)
    return out


def _patronage_opinion_effect(
    ctx: Dict[str, object],
    sponsor_to_candidate: int,
    candidate_to_sponsor: int,
    basis: str,
) -> List[Dict[str, object]]:
    sponsor = str(ctx.get("sponsor") or "")
    candidate = str(ctx.get("candidate") or "")
    if not sponsor or not candidate:
        return []
    return [
        {"a": sponsor, "b": candidate, "delta": sponsor_to_candidate, "basis": basis},
        {"a": candidate, "b": sponsor, "delta": candidate_to_sponsor, "basis": basis},
    ]


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
            "id": "secret_order_review",
            "priority": 32,
            "cooldown": "ctx",
            "when": _secret_order_review,
            "title": lambda c: f"密令核议：{c['minister']}回奏「{c['title']}」",
            "narrative": lambda c: (
                f"{c['office']}{c['minister']}所承密令「{c['title']}」已到核议。"
                f"承办自述：{c['claim']}。"
                + (f"暗线另报：{c['sim_note']}。" if c.get("sim_note") else "")
                + (f"此案关涉{c['tags']}，" if c.get("tags") else "")
                + (
                    "风声已有走漏，若公开收网，或能立威，却也会惊动余党；"
                    if c.get("leak") else
                    "事尚在暗处，若公开收网，朝中会知道陛下另有耳目；"
                )
                + (
                    "且证据尚薄，贸然拿人会留下罗织之议。"
                    if c.get("thin") else
                    "证据已有几分轮廓，但仍要陛下替这条暗线担责。"
                )
                + "此事是收，是押，是问责承办，还是压下封存？"
            ),
            "choices": [
                {"key": "publish", "label": lambda c: "据密令公开收网，拿结果立威",
                 "hint": "把暗线变成明案：皇威上升、密探体系显形；若风声已泄或证据太薄，民心和任事会受损",
                 "effect": lambda c: {"shi": 3, "renshi": -2 if c.get("thin") else -1,
                                      "metrics": {"皇威": 2, "民心": -2 if c.get("thin") else -1},
                                      "char": [{"name": c["minister"], "emp_trust": 3, "grievance": 1}],
                                      "faction": _secret_order_faction_effect(c, sat=1, heat=2 if c.get("leak") else 1),
                                      "secret_orders": [{
                                          "id": c["order_id"],
                                          "action": "close",
                                          "status": "done",
                                          "label": "圣裁公开",
                                          "note": f"据{c['minister']}密令回奏，准予公开收网；成败由朝廷担责。"
                                      }],
                                      "log": f"密令核议：准{c['minister']}所奏，公开收网「{c['title']}」。"}},
                {"key": "seal_continue", "label": lambda c: "仍密押续查，两月后再议",
                 "hint": "不急着亮牌：暗线继续发酵，承办人有空间；但皇帝显得迟疑，拖久可能再生反弹",
                 "effect": lambda c: {"shi": -1, "renshi": 2,
                                      "char": [{"name": c["minister"], "emp_trust": 2, "grievance": 2}],
                                      "faction": _secret_order_faction_effect(c, sat=1),
                                      "secret_orders": [{
                                          "id": c["order_id"],
                                          "action": "extend",
                                          "months": 2,
                                          "label": "密押续查",
                                          "note": f"御前不许贸然公开，命{c['minister']}继续密押续查，两月后再行核议。"
                                      }],
                                      "log": f"密令核议：命{c['minister']}继续密押续查「{c['title']}」。"}},
                {"key": "question_assignee", "label": lambda c: f"召{c['minister']}问责，限一月补证",
                 "hint": "把暗线压回承办人身上：保留案件，也形成履约账本；本人会承压记怨",
                 "effect": lambda c: {"shi": 2, "renshi": 1,
                                      "char": [{"name": c["minister"], "emp_trust": -3, "grievance": 6}],
                                      "faction": _secret_order_faction_effect(c, sat=-2, heat=1),
                                      "secret_orders": [{
                                          "id": c["order_id"],
                                          "action": "extend",
                                          "months": 1,
                                          "label": "补证问责",
                                          "note": f"御前召{c['minister']}问责，命其一月内补足「{c['title']}」人证、物证与处置名单。"
                                      }],
                                      "obligations": [{
                                          "minister": c["minister"],
                                          "title": f"补证密令：{c['title']}",
                                          "target_text": f"{c['minister']}须就密令「{c['title']}」补足可公开核验的人证、物证与处置名单。",
                                          "tasks": [
                                              "一月内回奏至少两条可核验证据，不得只称风闻。",
                                              "列明若公开收网会惊动何人、牵连何派、损益何在。",
                                              "说明继续密押、公开处置、压下封存三种方案的后果。"
                                          ],
                                          "source": f"secret_order_review:question:{c['order_id']}",
                                          "due_turns": 1,
                                          "summary": f"御前命{c['minister']}补证密令「{c['title']}」，一月内复命。"
                                      }],
                                      "log": f"密令核议：召{c['minister']}问责，限一月补证「{c['title']}」。"}},
                {"key": "bury", "label": lambda c: "压下不发，封存此案",
                 "hint": "止损：避免暗线曝光和罗织之议；但承办人寒心，君威受损，后续线索也断了",
                 "effect": lambda c: {"shi": -2, "renshi": -2,
                                      "metrics": {"皇威": -1},
                                      "char": [{"name": c["minister"], "emp_trust": -5, "grievance": 7}],
                                      "faction": _secret_order_faction_effect(c, sat=-1),
                                      "secret_orders": [{
                                          "id": c["order_id"],
                                          "action": "close",
                                          "status": "failed",
                                          "label": "压下封存",
                                          "note": f"御前以证据不足或牵连过广为由，压下「{c['title']}」不发，档案封存。"
                                      }],
                                      "log": f"密令核议：压下{c['minister']}所承「{c['title']}」，封存不发。"}},
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
            "id": "goal_obligation_help",
            "priority": 30,
            "cooldown": "ctx",
            "when": _goal_obligation_help,
            "title": _goal_help_title,
            "narrative": _goal_help_narrative,
            "choices": [
                {"key": "protect", "label": lambda c: _goal_help_label(c, "protect"),
                 "hint": lambda c: _goal_help_hint(c, "protect"),
                 "effect": lambda c: {"shi": -1, "renshi": 2,
                                      "char": [{"name": c["minister"], "emp_trust": 5, "grievance": -6}],
                                      "faction": ({_meaningful_faction(c.get("faction")): {"satisfaction": 2, "heat": -1}}
                                                  if _meaningful_faction(c.get("faction")) else {}),
                                      "goals": [{
                                          "id": c["goal_id"],
                                          "action": "extend",
                                          "months": 2,
                                          "evidence": _goal_help_evidence(c, "protect")
                                      }],
                                      "log": _goal_help_log(c, "protect")}},
                {"key": "resource_support", "label": lambda c: _goal_help_label(c, "resource_support"),
                 "hint": lambda c: _goal_help_hint(c, "resource_support"),
                 "effect": lambda c: {"shi": 1, "renshi": 2,
                                      "metrics": {"国库": -3},
                                      "char": [{"name": c["minister"], "emp_trust": 4, "grievance": -4}],
                                      "faction": ({_meaningful_faction(c.get("faction")): {"satisfaction": 1, "heat": 1}}
                                                  if _meaningful_faction(c.get("faction")) else {}),
                                      "memories": [_resource_support_memory(c)],
                                      "goals": [{
                                          "id": c["goal_id"],
                                          "action": "resource",
                                          "months": 1,
                                          "support_tasks": [
                                              "列明新拨人手、文书或银粮分别用在何处，不得转作私恩。",
                                              "一月内回奏已用资源、可验证结果与剩余阻力。",
                                              "若仍不能成事，须自请处分并交代谁从中掣肘。"
                                          ],
                                          "evidence": _goal_help_evidence(c, "resource_support")
                                      }],
                                      "log": _goal_help_log(c, "resource_support")}},
                {"key": "demand_evidence", "label": lambda c: _goal_help_label(c, "demand_evidence"),
                 "hint": lambda c: _goal_help_hint(c, "demand_evidence"),
                 "effect": lambda c: {"shi": 1, "renshi": 1,
                                      "char": [{"name": c["minister"], "emp_trust": -1, "grievance": 2}],
                                      "faction": ({_meaningful_faction(c.get("faction")): {"heat": 1}}
                                                  if _meaningful_faction(c.get("faction")) else {}),
                                      "goals": [{
                                          "id": c["goal_id"],
                                          "action": "extend",
                                          "months": 1,
                                          "evidence": _goal_help_evidence(c, "demand_evidence")
                                      }],
                                      "log": _goal_help_log(c, "demand_evidence")}},
                {"key": "public_rebuke", "label": lambda c: _goal_help_label(c, "public_rebuke"),
                 "hint": lambda c: _goal_help_hint(c, "public_rebuke"),
                 "effect": lambda c: {"shi": 2, "renshi": -2,
                                      "char": [{"name": c["minister"], "emp_trust": -7, "grievance": 9}],
                                      "faction": ({_meaningful_faction(c.get("faction")): {"satisfaction": -3, "heat": 3}}
                                                  if _meaningful_faction(c.get("faction")) else {}),
                                      "goals": [{
                                          "id": c["goal_id"],
                                          "action": "fail",
                                          "evidence": _goal_help_evidence(c, "public_rebuke")
                                      }],
                                      "log": _goal_help_log(c, "public_rebuke")}},
                {"key": "self_prove", "label": lambda c: _goal_help_label(c, "self_prove"),
                 "hint": lambda c: _goal_help_hint(c, "self_prove"),
                 "effect": lambda c: {"shi": 1, "renshi": 0,
                                      "char": [{"name": c["minister"], "emp_trust": 1, "grievance": 1}],
                                      "goals": [{
                                          "id": c["goal_id"],
                                          "action": "extend",
                                          "months": 1,
                                          "evidence": _goal_help_evidence(c, "self_prove")
                                      }],
                                      "log": _goal_help_log(c, "self_prove")}},
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
                                      "memories": [_petition_favor_memory(c)],
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
                {"key": "inquest", "label": lambda c: "据证词廷鞫核验，限期补证",
                 "hint": lambda c: (
                     f"已问{c.get('_testimony_count') or 0}人：不立刻偏袒任一方，命入案者补呈可验凭据；"
                     "若证伪，后续追责更重"
                 ),
                 "available": _has_decision_testimony,
                 "effect": lambda c: {"shi": 2, "renshi": 1,
                                      "char": _rival_feud_testimony_char_effect(c),
                                      "opinion": [
                                          {"a": c["a"], "b": c["b"], "delta": 4, "basis": "御前听证缓断"},
                                          {"a": c["b"], "b": c["a"], "delta": 4, "basis": "御前听证缓断"},
                                      ],
                                      "obligations": _rival_feud_testimony_obligations(c),
                                      "log": f"廷争裁断：据已问证词，命{c['a']}与{c['b']}案下补证。"}},
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
            "id": "private_distress",
            "priority": 24,
            "cooldown": "ctx",
            "when": _private_distress,
            "title": lambda c: f"{c['plea_label']}：{c['actor']}私下求见",
            "narrative": lambda c: (
                f"{c['office']}{c['actor']}递话求见。此人平日私心是「{c['agenda_title']}」，"
                f"如今信任{c['trust']}、怨望{c['grievance']}，事情压到「{c['stake']}」。"
                + (
                    f"牵涉{c['target_office']}{c['target']}，二人关系为「{c['relation_basis']}」"
                    f"（好感{c['relation_opinion']}）。"
                    if c.get("target") else ""
                )
                + f"{c['ask']}这不是奏疏大案，却是一个活人的身家与私心。"
                "陛下若救他，他会记恩；若趁机索差，也能把私请变成账本；若公断或拒绝，则人情与清议各有代价。"
            ),
            "choices": [
                {"key": "grant_private_grace", "label": lambda c: f"给{c['actor']}一个私恩，先护住人",
                 "hint": "收买一颗人心：本人和相关故旧记恩，但朝中会觉得皇帝开了私请口子",
                 "effect": lambda c: {"shi": -1, "renshi": 2,
                                      "metrics": {"皇威": -1},
                                      "char": [{"name": c["actor"], "emp_trust": 9, "grievance": -9}]
                                      + ([{"name": c["target"], "emp_trust": 4, "grievance": -3}] if c.get("target") else []),
                                      "opinion": _private_opinion_effect(c, 8, "御前私恩"),
                                      "faction": _private_faction_effect(c, sat=3, heat=-2),
                                      "memories": [
                                          _private_memory(
                                              c,
                                              "私恩护持",
                                              "陛下曾私下护持，不宜装作两清；日后有差使须知报效。",
                                          )
                                      ],
                                      "log": f"私下护持{c['actor']}所请：{c['stake']}。"}},
                {"key": "trade_for_service", "label": lambda c: f"许其所请，但命{c['actor']}领差偿恩",
                 "hint": "把人情变债务：本人得救且必须回报，形成后续履约账本；本人会感到被拿捏",
                 "effect": lambda c: {"shi": 1, "renshi": 3,
                                      "char": [{"name": c["actor"], "emp_trust": 6, "grievance": -4}]
                                      + ([{"name": c["target"], "emp_trust": 2, "grievance": -2}] if c.get("target") else []),
                                      "opinion": _private_opinion_effect(c, 5, "以差换恩"),
                                      "faction": _private_faction_effect(c, sat=1, heat=1),
                                      "memories": [
                                          _private_memory(
                                              c,
                                              "以差换恩",
                                              "陛下准其所请，但此恩未报；必须以可验差使偿还。",
                                          )
                                      ],
                                      "obligations": [{
                                          "minister": c["actor"],
                                          "title": f"偿恩差使：{c['plea_label']}",
                                          "target_text": f"{c['actor']}因「{c['stake']}」得御前护持，须领一件可验差使偿还私恩。",
                                          "tasks": [
                                              "两月内回奏一件可验证据、成效或名单，不得只以谢恩搪塞。",
                                              f"说明此事与「{c['agenda_title']}」的牵连，避免借圣恩扩张私党。",
                                              "若事涉故旧或政敌，列明会激怒何人以及如何收束。"
                                          ],
                                          "source": f"{c['source_id']}:trade_for_service",
                                          "due_turns": 2,
                                          "summary": f"御前准{c['actor']}私请，但命其领差偿恩。"
                                      }],
                                      "log": f"准{c['actor']}所请，但命其领差偿恩：{c['stake']}。"}},
                {"key": "public_review", "label": lambda c: "交廷议公断，不许私下徇情",
                 "hint": "走公论：能保制度名分，避免私恩泛滥；求援者会觉得皇帝不肯担人情",
                 "effect": lambda c: {"shi": 2, "renshi": -1,
                                      "char": [{"name": c["actor"], "emp_trust": -2, "grievance": 5}]
                                      + ([{"name": c["target"], "grievance": 2}] if c.get("target") else []),
                                      "faction": _private_faction_effect(c, sat=-1, heat=2),
                                      "log": f"{c['actor']}私下求援，交廷议公断，不许私下徇情。"}},
                {"key": "refuse_private", "label": lambda c: "斥为私请，令其退下",
                 "hint": "不为私情开门：皇威略立，但此人和同党会寒心，日后更可能自保或结援",
                 "effect": lambda c: {"shi": 1, "renshi": -3,
                                      "char": [{"name": c["actor"], "emp_trust": -7, "grievance": 11}],
                                      "opinion": _private_opinion_effect(c, -5, "御前斥私请"),
                                      "faction": _private_faction_effect(c, sat=-4, heat=4),
                                      "log": f"斥{c['actor']}私下求援，不许开私请之门。"}},
            ],
        },
        {
            "id": "favor_debt_pressure",
            "priority": 23,
            "cooldown": "ctx",
            "when": _favor_debt_pressure,
            "title": lambda c: f"旧恩求偿：{c['actor']}试探圣意",
            "narrative": lambda c: (
                f"{c['office']}{c['actor']}昔日受过天恩「{c['title']}」，至今已{c['age']}月。"
                f"旧账上写着：{c.get('cause') or '皇帝曾替其保全名节或任事余地'}；"
                f"{c.get('outcome') or '此恩不宜装作两清'}。"
                + (
                    f"同道人情牵着{ '、'.join(c['allies']) }，"
                    if c.get("allies") else ""
                )
                + (
                    f"政敌{ '、'.join(c['rivals']) }也等着看陛下是否偏私，"
                    if c.get("rivals") else ""
                )
                + f"眼下信任{c['trust']}、怨望{c['grievance']}。"
                  "是把天恩变成可验差使，还是继续施恩、公开点账，或任它冷下去？"
            ),
            "choices": [
                {"key": "call_service", "label": lambda c: f"点明旧恩，命{c['actor']}领差还恩",
                 "hint": "把软人情变成硬账本：任事上升，但本人会感到被拿捏",
                 "effect": lambda c: {"shi": 2, "renshi": 2,
                                      "char": [{"name": c["actor"], "emp_trust": -1, "grievance": 4}],
                                      "faction": ({_meaningful_faction(c.get("faction")): {"satisfaction": -1, "heat": 1}}
                                                  if _meaningful_faction(c.get("faction")) else {}),
                                      "obligations": [{
                                          "minister": c["actor"],
                                          "title": f"还恩差使：{c['actor']}",
                                          "target_text": f"{c['actor']}因旧恩「{c['title']}」未报，须领一件可验差使偿还天恩。",
                                          "tasks": [
                                              "两月内回奏一件可验证据、成效或名单，不得只称感恩。",
                                              "说明此差会牵动何党羽、政敌或旧案，并列担责边界。",
                                              "若借还恩之名求赏、护党或拖延，须自请处分。"
                                          ],
                                          "source": f"favor_debt:{c['memory_id']}:call_service",
                                          "due_turns": 2,
                                          "summary": f"御前点明旧恩，命{c['actor']}领差还恩。"
                                      }],
                                      "log": f"旧恩求偿：命{c['actor']}领差还恩。"}},
                {"key": "renew_grace", "label": lambda c: f"再给{c['actor']}一层恩赏",
                 "hint": "继续收心：本人更感恩，派系也安；但旧恩越滚越大，旁人会说皇帝偏护",
                 "effect": lambda c: {"shi": -1, "renshi": 2,
                                      "metrics": {"国库": -2},
                                      "char": [{"name": c["actor"], "emp_trust": 7, "grievance": -6}],
                                      "faction": ({_meaningful_faction(c.get("faction")): {"satisfaction": 2, "heat": -1}}
                                                  if _meaningful_faction(c.get("faction")) else {}),
                                      "memories": [_favor_debt_memory(
                                          c,
                                          "再加护持",
                                          "陛下旧恩之外又给恩赏；此人更须以差使、人脉或证据报效，不得只求保全。"
                                      )],
                                      "log": f"旧恩求偿：再加恩赏于{c['actor']}，暂收其心。"}},
                {"key": "public_account", "label": lambda c: f"当众点破旧恩，令{c['actor']}自重",
                 "hint": "立规矩：君威上涨，旧恩不再是私相授受；本人和同党会觉得难堪",
                 "effect": lambda c: {"shi": 2, "renshi": -1,
                                      "char": [{"name": c["actor"], "emp_trust": -3, "grievance": 7}],
                                      "faction": ({_meaningful_faction(c.get("faction")): {"satisfaction": -2, "heat": 2}}
                                                  if _meaningful_faction(c.get("faction")) else {}),
                                      "log": f"旧恩求偿：当众点破{c['actor']}旧恩，令其避嫌自重。"}},
                {"key": "let_cool", "label": lambda c: "暂不点破，让旧恩冷下去",
                 "hint": "不花政治成本：短期无事；但旧恩不兑现，会变成观望或反向求赏",
                 "effect": lambda c: {"shi": -1, "renshi": -1,
                                      "char": [{"name": c["actor"], "emp_trust": -2, "grievance": 3}],
                                      "log": f"旧恩求偿：暂不点破{c['actor']}旧恩，任其冷却。"}},
            ],
        },
        {
            "id": "patronage_accountability",
            "priority": 23,
            "cooldown": "ctx",
            "when": _patronage_accountability,
            "title": lambda c: f"举主担保：{c['sponsor']}荐{c['candidate']}",
            "narrative": lambda c: (
                f"{c['sponsor_office']}{c['sponsor']}先前以「{c['basis']}」荐入"
                f"{c['candidate_office']}{c['candidate']}。"
                f"此人小传称：{c['candidate_summary'] or '初入朝局，才具与来路尚待验证。'}"
                f"眼下举主对新人好感{c['sponsor_opinion']}，新人对举主好感{c['candidate_opinion']}，"
                f"才干{c['candidate_ability']}、操守{c['candidate_integrity']}。"
                "若不追担保，举荐便成免费人情；若骤然信用，又可能喂大门生故旧。"
                "陛下要如何处置这条举主链？"
            ),
            "choices": [
                {"key": "joint_trial", "label": lambda c: f"命{c['sponsor']}与{c['candidate']}共办试差，举主连坐",
                 "hint": "把荐人变成可验账本：新人得机会，举主也要拿名节担保；办坏了两人一起回奏",
                 "effect": lambda c: {"shi": 2, "renshi": 2,
                                      "char": [{"name": c["sponsor"], "emp_trust": 2, "grievance": 2},
                                               {"name": c["candidate"], "emp_trust": 5, "grievance": 1}],
                                      "opinion": _patronage_opinion_effect(c, 4, 6, "御前共办试差"),
                                      "faction": _patronage_faction_effect(c, sponsor_sat=1, sponsor_heat=1),
                                      "obligations": [
                                          {
                                              "minister": c["sponsor"],
                                              "title": f"举主连坐：{c['sponsor']}保{c['candidate']}",
                                              "target_text": f"{c['sponsor']}须与{c['candidate']}共办一件可验试差，并说明荐人短板、担保边界与误事追责。",
                                              "tasks": [
                                                  f"两月内与{c['candidate']}共同回奏试差成果、证据与下一步期限。",
                                                  f"列明为何荐{c['candidate']}、短板何在，若误事愿受何责。",
                                                  "说明此荐是否牵动派系、乡党或门生故旧，不得只称识才。"
                                              ],
                                              "source": f"{c['source_id']}:joint_trial:sponsor",
                                              "due_turns": 2,
                                              "summary": f"御前命{c['sponsor']}为{c['candidate']}连坐担保，共办试差。"
                                          },
                                          {
                                              "minister": c["candidate"],
                                              "title": f"新人试差：{c['candidate']}",
                                              "target_text": f"{c['candidate']}须以可验试差证明自己不是只靠{c['sponsor']}荐书入朝。",
                                              "tasks": [
                                                  "两月内回奏一件可验证据、成效或名单，不得只借举主名义。",
                                                  f"说明与{c['sponsor']}的关系如何避嫌，如何向御前直接交账。"
                                              ],
                                              "source": f"{c['source_id']}:joint_trial:candidate",
                                              "due_turns": 2,
                                              "summary": f"{c['candidate']}得御前试差，须独立交账。"
                                          }
                                      ],
                                      "log": f"命{c['sponsor']}与{c['candidate']}共办试差，举主连坐担保。"}},
                {"key": "sponsor_bond", "label": lambda c: f"先令{c['sponsor']}具保结，暂缓实授{c['candidate']}",
                 "hint": "谨慎用人：先锁住举主责任，降低误用风险；但新人会觉得被压在举主阴影下",
                 "effect": lambda c: {"shi": 1, "renshi": -1,
                                      "char": [{"name": c["sponsor"], "emp_trust": 1, "grievance": 4},
                                               {"name": c["candidate"], "grievance": 3}],
                                      "faction": _patronage_faction_effect(c, sponsor_sat=-1, sponsor_heat=1),
                                      "obligations": [{
                                          "minister": c["sponsor"],
                                          "title": f"保结荐人：{c['candidate']}",
                                          "target_text": f"{c['sponsor']}须为举荐{c['candidate']}具明保结，列才具、短板、关系与可试之差。",
                                          "tasks": [
                                              f"一月内具明保结：{c['candidate']}可办何差、何处短板、何人可证。",
                                              "说明若新人误事，举主愿承担何种名节或职事责任。"
                                          ],
                                          "source": f"{c['source_id']}:sponsor_bond",
                                          "due_turns": 1,
                                          "summary": f"御前暂缓实授{c['candidate']}，命{c['sponsor']}先具保结。"
                                      }],
                                      "log": f"暂缓实授{c['candidate']}，令{c['sponsor']}具保结。"}},
                {"key": "separate_trial", "label": lambda c: f"越过举主，给{c['candidate']}一件独立试差",
                 "hint": "验新人、削人情：新人可直接向御前交账；举主不悦，派系会觉得皇帝防着他们",
                 "effect": lambda c: {"shi": 1, "renshi": 2,
                                      "char": [{"name": c["sponsor"], "emp_trust": -2, "grievance": 5},
                                               {"name": c["candidate"], "emp_trust": 6, "grievance": -2}],
                                      "opinion": _patronage_opinion_effect(c, -5, -4, "御前拆分举荐链"),
                                      "faction": _patronage_faction_effect(c, sponsor_sat=-2, sponsor_heat=2),
                                      "obligations": [{
                                          "minister": c["candidate"],
                                          "title": f"避嫌试差：{c['candidate']}",
                                          "target_text": f"{c['candidate']}须避开{c['sponsor']}的人情链，独立办成一件可验差使。",
                                          "tasks": [
                                              "两月内回奏独立试差成果、证据和受阻之处。",
                                              f"不得以{c['sponsor']}名义压人，也不得替举主递私请。"
                                          ],
                                          "source": f"{c['source_id']}:separate_trial",
                                          "due_turns": 2,
                                          "summary": f"御前越过举主，给{c['candidate']}独立试差。"
                                      }],
                                      "log": f"越过举主，给{c['candidate']}独立试差，以验其才。"}},
                {"key": "reject_chain", "label": lambda c: "不用此人，斩断这条荐人链",
                 "hint": "立规矩：不让门生故旧借荐人伸手；但可能错失人才，举主与新人都会记怨",
                 "effect": lambda c: {"shi": 2, "renshi": -3,
                                      "char": [{"name": c["sponsor"], "emp_trust": -5, "grievance": 8},
                                               {"name": c["candidate"], "emp_trust": -4, "grievance": 8}],
                                      "opinion": _patronage_opinion_effect(c, -10, -8, "荐人链被御前斩断"),
                                      "faction": _patronage_faction_effect(c, sponsor_sat=-3, sponsor_heat=3),
                                      "log": f"斩断{c['sponsor']}举荐{c['candidate']}之链，暂不用其人。"}},
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


def _has_decision_testimony(ctx: Dict[str, object]) -> bool:
    return int(ctx.get("_testimony_count") or 0) > 0


def _testimony_names(ctx: Dict[str, object]) -> List[str]:
    raw = ctx.get("_testimony_ministers")
    if not isinstance(raw, list):
        return []
    names: List[str] = []
    for item in raw:
        name = str(item or "").strip()
        if name and name not in names:
            names.append(name)
    return names[:4]


def _rival_feud_testimony_obligations(ctx: Dict[str, object]) -> List[Dict[str, object]]:
    a = str(ctx.get("a") or "").strip()
    b = str(ctx.get("b") or "").strip()
    title = f"廷鞫核证：{a}与{b}互讦" if a and b else "廷鞫核证"
    names = _testimony_names(ctx)
    if not names:
        return []
    case_key = str(ctx.get("_testimony_case_key") or f"{a}:{b}:rival_feud").strip()[:80]
    tasks = [
        "呈所称账册、人证、往来文书或可验名单",
        "明言若证伪愿受何责，不许再以浮词互讦",
        "交代本党或门生是否代为鼓噪",
    ]
    return [
        {
            "minister": name,
            "action_kind": "evidence_inquiry",
            "title": title,
            "target_text": f"就{a}与{b}互讦案补呈可核证据",
            "tasks": tasks,
            "conditions": "；".join(tasks),
            "due_turns": 1,
            "source": f"decision_testimony:{case_key}:{name}",
            "promise_type": "裁断前补证",
            "stakes": "证实可保其奏对可信；证伪则加重御前追责与政敌反噬。",
            "summary": f"{name}奉旨就宿敌互讦案补证，以证词换缓断。",
        }
        for name in names
    ]


def _rival_feud_testimony_char_effect(ctx: Dict[str, object]) -> List[Dict[str, object]]:
    names = _testimony_names(ctx)
    return [{"name": name, "emp_trust": 2, "grievance": -2} for name in names]


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
    ctx = _ctx_with_testimony(db, p)
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
    ctx = _ctx_with_testimony(db, p)
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


def _ctx_with_testimony(db: GameDB, pending: Dict[str, object]) -> Dict[str, object]:
    ctx = dict(pending.get("ctx") or {})
    payload = {"id": str(pending.get("id") or "")}
    try:
        from ming_sim.playstyle import decision_testimonies_for_case

        testimonies = decision_testimonies_for_case(db, pending, payload)
    except Exception:
        testimonies = []
    names: List[str] = []
    stances: List[str] = []
    summaries: List[str] = []
    for item in testimonies:
        name = str(item.get("minister") or "").strip()
        if name and name not in names:
            names.append(name)
        stance = str(item.get("stance") or "").strip()
        if stance and stance not in stances:
            stances.append(stance)
        summary = str(item.get("summary") or "").strip()
        if summary:
            summaries.append(summary)
    ctx["_testimony_count"] = len(testimonies)
    ctx["_testimony_ministers"] = names
    ctx["_testimony_stances"] = stances
    ctx["_testimony_summary"] = "；".join(summaries[:3])
    ctx["_testimony_case_key"] = "|".join(
        str(part or "").strip()
        for part in (pending.get("id"), pending.get("day"), pending.get("cooldown_key"), ctx.get("a"), ctx.get("b"))
        if str(part or "").strip()
    )[:160]
    return ctx
