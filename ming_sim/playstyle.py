"""Player-facing strategic briefing cards.

This module does not create new simulation state.  It exposes already-running
CK3/ROTK-style systems as compact, actionable hooks for the home screen:
private agendas, rivalries, faction pressure, army autonomy, and known secrets.
The goal is to make the living-world layer legible without calling the LLM.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Dict, List, Optional, Tuple

from ming_sim.db import GameDB
from ming_sim.models import GameState

BriefCard = Dict[str, object]

_TAB_AUDIENCE = "audience"
_TAB_REALM = "realm"
_TAB_DESK = "desk"
_TAB_EDICTS = "edicts"

_KIND_PRIORITY = {
    "decision": 0,
    "trap": 1,
    "directive_blocker": 2,
    "directive_followup": 3,
    "monthly_followup": 4,
    "patronage": 5,
    "trap_remedy": 6,
    "petition": 7,
    "favor": 8,
    "legacy": 9,
    "army": 10,
    "faction": 11,
    "agenda": 12,
    "rivalry": 13,
    "hook": 14,
}

_KIND_LABELS = {
    "decision": "裁断",
    "trap": "御案",
    "directive_blocker": "诏旨",
    "directive_followup": "复命",
    "monthly_followup": "候见",
    "patronage": "举主",
    "trap_remedy": "担责",
    "petition": "求援",
    "favor": "旧恩",
    "legacy": "余波",
    "army": "军镇",
    "faction": "派系",
    "agenda": "私图",
    "rivalry": "怨隙",
    "hook": "把柄",
}

_RANK_LABELS = {
    "danger": "危局",
    "warn": "急务",
    "info": "要事",
}

_RANK_BADGES = {
    "danger": "危",
    "warn": "急",
    "info": "要",
}

_AGENDA_LABELS = {
    "climb": "进取求用",
    "enrich": "自肥敛财",
    "protect": "护党植援",
    "entrench": "拥兵自重",
    "survive": "避祸自保",
    "revenge": "清议复仇",
}

_AGENDA_HINTS = {
    "climb": "声望与援引快要坐成，若用得其人是臂助，若纵其结援则成权臣。",
    "enrich": "钱粮和请托的风闻渐密，可借题召问，也可放长线查赃。",
    "protect": "其人正替本党铺路，放任则党势愈固，打断则会激起同党怨气。",
    "entrench": "军中根基渐深，若不早作羁縻或制衡，边镇会越来越不像朝廷的边镇。",
    "survive": "其人急于洗白避祸，正是逼问旧案、换取效忠的窗口。",
    "revenge": "旧怨将化为弹劾攻势，可顺势去一人，也可能纵成朝争。",
}


def briefing_payload(
    db: GameDB,
    state: Optional[GameState] = None,
    *,
    limit: int = 5,
    kind: str = "",
) -> Dict[str, object]:
    """Return a stable API payload for the home-screen strategic briefing."""

    safe_limit = max(1, min(8, int(limit or 5)))
    safe_kind = str(kind or "").strip()
    candidates = _briefing_candidates(db, state)
    filtered = [c for c in candidates if str(c.get("kind") or "") == safe_kind] if safe_kind in _KIND_LABELS else candidates
    cards = _select_brief_cards(filtered, limit=safe_limit)
    overview_cards = _select_brief_cards(candidates, limit=safe_limit)
    return {
        "cards": cards,
        "lead": cards[0] if cards else None,
        "limit": safe_limit,
        "filter": safe_kind if safe_kind in _KIND_LABELS else "",
        "shown": len(cards),
        "total": len(filtered),
        "hidden": max(0, len(filtered) - len(cards)),
        "buckets": _brief_kind_buckets(candidates, overview_cards),
        "ranks": _brief_rank_counts(filtered),
    }


def briefing_cards(db: GameDB, state: Optional[GameState] = None, *, limit: int = 5) -> List[BriefCard]:
    """Collect and rank actionable hooks from existing simulation tables."""

    return _select_brief_cards(_briefing_candidates(db, state), limit)


def petition_chat_context_brief(
    db: GameDB,
    minister_name: str,
    *,
    target: str = "",
) -> str:
    """Trusted context for a home-screen petition card entering audience chat.

    The frontend may carry a card id, but the actual prompt context must be
    rebuilt from live DB state. This keeps "NPC comes to ask for help" as
    dialogue-driven gameplay, not a client-side button pretending to be truth.
    """

    name = str(minister_name or "").strip()
    if not name or not _table_exists(db, "characters"):
        return ""
    row = db.conn.execute(
        """
        SELECT name, office, faction, ability, integrity, emp_trust, grievance
        FROM characters
        WHERE name=?
          AND status='active'
          AND power_id='ming'
          AND office_type!='后宫'
          AND name!='崇祯'
        """,
        (name,),
    ).fetchone()
    if row is None:
        return ""

    trust = _clamp_int(row["emp_trust"], 0, 100)
    grievance = _clamp_int(row["grievance"], 0, 100)
    requested_target = str(target or "").strip()
    rival = ""
    opinion = 0
    basis = ""
    if requested_target and _table_exists(db, "relationships"):
        relation = db.conn.execute(
            """
            SELECT r.b_name, r.opinion, r.basis
            FROM relationships r
            JOIN characters c ON c.name=r.b_name
            WHERE r.a_name=?
              AND r.b_name=?
              AND r.opinion<=-40
              AND c.status='active'
              AND c.power_id='ming'
              AND c.office_type!='后宫'
            LIMIT 1
            """,
            (name, requested_target),
        ).fetchone()
        if relation is not None:
            rival = str(relation["b_name"] or "")
            opinion = int(relation["opinion"] or 0)
            basis = str(relation["basis"] or "")
    if not rival:
        rival, opinion, basis = _worst_rival_of(db, name)

    pressure = grievance >= 50 or trust <= 48 or bool(rival)
    if not pressure:
        return ""

    office = _short_office(str(row["office"] or ""))
    faction = str(row["faction"] or "").strip()
    lines = [
        "【本次召对事项：主动求援请托】",
        f"- {office}{name}不是普通被动问策，而是带着困局求见皇帝；请安后应自然说出自己求陛下解哪一处难局。",
        f"- 当前心态：御前信任 {trust}，怨望 {grievance}"
        + (f"，派系 {faction}" if faction and faction not in {"无", "中立"} else "")
        + "。",
    ]
    if rival:
        lines.append(
            f"- 请托焦点：与{rival}因「{basis or '旧怨'}」相争，关系 {opinion}；"
            "他可能求台阶、求护持、求自辩，也可能借皇帝之手压政敌。"
        )
    else:
        lines.append("- 请托焦点：其言辞或称公事，骨子里是在求台阶、护身符或重新取得任事机会。")
    lines.extend([
        "- 对话玩法：先提出可谈方案，不要直接落库；只有皇帝明确答应、追问条件或命其换取差使后，才算进入奏对目的。",
        "- 可谈条件：明旨护持；自辩换难差；与政敌共办一事；若皇帝留中不应，则表现寒心与转入自保。",
        "- 口吻要求：按本人的身份、派系、性格和关系网说话；不要像全知旁白解释机制。",
    ])
    return "\n".join(lines)


def legacy_chat_context_brief(
    db: GameDB,
    minister_name: str,
    legacy_id: object,
) -> str:
    """Trusted context for discussing a long-running policy legacy in audience."""

    name = str(minister_name or "").strip()
    try:
        lid = int(legacy_id or 0)
    except (TypeError, ValueError):
        lid = 0
    if not name or lid <= 0 or not _table_exists(db, "legacies") or not _table_exists(db, "characters"):
        return ""
    minister = db.conn.execute(
        """
        SELECT name, office, office_type, faction
        FROM characters
        WHERE name=?
          AND status='active'
          AND power_id='ming'
          AND office_type!='后宫'
        """,
        (name,),
    ).fetchone()
    if minister is None:
        return ""
    row = db.conn.execute(
        """
        SELECT id, name, modifiers, narrative_hint, duration_months, legacy_key
        FROM legacies
        WHERE id=? AND status='active'
        """,
        (lid,),
    ).fetchone()
    if row is None:
        return ""
    policy = _legacy_policy_payload(row)
    if not policy["is_policy"]:
        return ""
    effects = policy_legacy_effect_labels_safe(row)
    effect_text = "；".join(str(item.get("label") or "") for item in effects if str(item.get("label") or "").strip())
    duration = int(policy["duration"])
    duration_text = "永久" if duration < 0 else f"仍余{duration}月"
    title = str(row["name"] or "旧政余波")
    hint = str(row["narrative_hint"] or "")
    stem = str(policy.get("stem") or "")
    office = _short_office(str(minister["office"] or ""))
    lines = [
        "【本次召对事项：长期政策余波】",
        f"- {office}{name}被召来讨论「{title}」：这是已经落库的长期后果，不是新政空谈。",
        f"- 余波事实：{hint or '旧政仍在拖动朝局。'}",
        f"- 持续性：{duration_text}" + (f"；当前修正：{effect_text}" if effect_text else "") + "。",
    ]
    if stem:
        lines.append(
            f"- 政策焦点：{stem}已成税负/钱粮旧账；可谈增收、民怨、地方承受、士绅阻力和善后路径。"
        )
    lines.extend([
        "- NPC 应提出有代价的善后方案，例如分年蠲免、换税源、查中间侵吞、以新差事换旧政缓和；不要给无成本完美答案。",
        "- 若皇帝要求立刻善后，先说清受益者、受损者、短期钱粮缺口与可能引发的党争。",
        "- 只有皇帝明确命其承办或要求拟旨，才进入奏对目的或拟旨；不要把本段机制文字复述给玩家。",
    ])
    return "\n".join(lines)


def agenda_chat_context_brief(
    db: GameDB,
    minister_name: str,
    *,
    target: str = "",
) -> str:
    """Trusted context for summoning a character because their private agenda is ripening."""

    name = str(minister_name or "").strip()
    if not name or not _table_exists(db, "characters") or not _table_exists(db, "npc_agendas"):
        return ""
    row = db.conn.execute(
        """
        SELECT a.name, a.kind, a.title, a.target_name, a.intensity, a.progress,
               c.office, c.office_type, c.faction, c.ability, c.integrity,
               c.emp_trust, c.grievance
        FROM npc_agendas a
        JOIN characters c ON c.name=a.name
        WHERE a.name=?
          AND a.status='active'
          AND c.status='active'
          AND c.power_id='ming'
          AND c.office_type!='后宫'
          AND c.name!='崇祯'
        LIMIT 1
        """,
        (name,),
    ).fetchone()
    if row is None:
        return ""
    progress = _clamp_int(row["progress"], 0, 100)
    if progress < 50:
        return ""

    kind = str(row["kind"] or "")
    label = _AGENDA_LABELS.get(kind, str(row["title"] or "私图将成"))
    hint = _AGENDA_HINTS.get(kind, "可召来探口风，再决定拉拢、压制或借力。")
    office = _short_office(str(row["office"] or ""))
    faction = str(row["faction"] or "").strip()
    intensity = _clamp_int(row["intensity"], 0, 100)
    ability = _clamp_int(row["ability"], 0, 100)
    integrity = _clamp_int(row["integrity"], 0, 100)
    trust = _clamp_int(row["emp_trust"], 0, 100)
    grievance = _clamp_int(row["grievance"], 0, 100)
    agenda_target = str(target or row["target_name"] or "").strip()
    if not agenda_target and kind == "revenge":
        agenda_target, _, _ = _worst_rival_of(db, name)

    posture = {
        "climb": "此人想求用、求大差、求更高名位；他会把私图包装成替皇帝任事。",
        "enrich": "此人已有钱粮/请托风闻；他会避重就轻，或用办事功劳换皇帝暂不深究。",
        "protect": "此人想护住本党与门生故旧；他会请求名分、官缺或缓查同党。",
        "entrench": "此人正在固结根基，尤其可能把军镇、人手或地方资源握成自己的筹码。",
        "survive": "此人急于避祸洗白；他会求台阶、求保全，也可能愿用实绩换赦免。",
        "revenge": "此人想借清议或弹劾清算政敌；他会称公义，内里要皇帝替他落刀。",
    }.get(kind, "此人带着私心入对，不宜只当普通问策。")
    bargain = {
        "climb": "可谈：给难差试用、许功后再迁、要求避嫌交账。",
        "enrich": "可谈：查账换效忠、令其吐出侵吞、以限期难差自证。",
        "protect": "可谈：令其举荐人才但连坐担保，或命其与敌派共办一事。",
        "entrench": "可谈：调饷、换将、遣监军、交出兵册；都要说明激变风险。",
        "survive": "可谈：旧案换新功、限期自证、交出同谋或把柄。",
        "revenge": "可谈：准其弹劾但要证据，或把私怨压成共办差使。",
    }.get(kind, "可谈：拉拢、压制、交换差使或设下期限。")

    lines = [
        "【本次召对事项：人物私图将成】",
        f"- {office}{name}不是普通被动问策；其私图「{label}」已推进到 {progress}%，强度 {intensity}。",
        f"- 当前人心：才干 {ability}，操守 {integrity}，御前信任 {trust}，怨望 {grievance}。",
        f"- 私图判断：{hint}",
        f"- 对话底色：{posture}",
        f"- 可用玩法：{bargain}",
    ]
    if faction and faction not in {"无", "中立"}:
        lines.append(f"- 派系牵连：{name}属{faction}；皇帝若拉拢或压制，可能影响本派满意、热度与党援。")
    if agenda_target:
        lines.append(f"- 牵涉对象：{agenda_target}；若谈及此人，应让{name}说明公义与私怨各占几分。")
    lines.extend([
        "- 交互要求：NPC 应先试探、求名分、求台阶或回避要害；不要一开口就坦白完整机制。",
        "- 只有皇帝明确许诺、命其承办、要求拟旨或设下期限，才进入奏对目的或履约账本；首次追问不直接落库。",
        "- 口吻要求：按身份、派系、信任、怨望和人物性格说话；不要像全知旁白解释系统。",
    ])
    return "\n".join(lines)


def favor_chat_context_brief(
    db: GameDB,
    minister_name: str,
    memory_id: object = 0,
) -> str:
    """Trusted context for summoning an official around an unpaid imperial favor."""

    name = str(minister_name or "").strip()
    try:
        mid = int(memory_id or 0)
    except (TypeError, ValueError):
        mid = 0
    if not name or not _table_exists(db, "event_memories") or not _table_exists(db, "characters"):
        return ""
    try:
        state = db.load_state()
        turn = int(state.turn)
    except Exception:
        turn = 0
    params: tuple
    where_id = ""
    if mid > 0:
        where_id = "AND m.id=?"
        params = (name, turn, mid)
    else:
        params = (name, turn)
    row = db.conn.execute(
        f"""
        SELECT m.id, m.title, m.cause, m.process, m.outcome, m.importance,
               c.office, c.faction, c.ability, c.integrity, c.emp_trust, c.grievance
        FROM event_memories m
        JOIN characters c ON c.name=m.subject_id
        WHERE m.subject_type='character'
          AND m.subject_id=?
          AND m.event_type='imperial_favor'
          AND (m.expires_turn IS NULL OR m.expires_turn>=?)
          AND c.status='active'
          AND c.power_id='ming'
          AND c.office_type!='后宫'
          {where_id}
        ORDER BY m.importance DESC, m.turn DESC, m.id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if row is None:
        return ""

    allies = []
    rivals = []
    try:
        from ming_sim import court
        allies = court.allies_of(db, name, limit=3)
        rivals = court.rivals_of(db, name, limit=3)
    except Exception:
        allies = []
        rivals = []
    office = _short_office(str(row["office"] or ""))
    faction = str(row["faction"] or "").strip()
    ability = _clamp_int(row["ability"], 0, 100)
    integrity = _clamp_int(row["integrity"], 0, 100)
    trust = _clamp_int(row["emp_trust"], 0, 100)
    grievance = _clamp_int(row["grievance"], 0, 100)
    title = str(row["title"] or f"旧恩未报：{name}")
    cause = str(row["cause"] or "皇帝昔日曾替其保全名节或任事余地。")
    process = str(row["process"] or "")
    outcome = str(row["outcome"] or "此后召对须记得旧恩，不宜装作两清。")
    lines = [
        "【本次召对事项：旧恩未报】",
        f"- {office}{name}曾受皇帝保全/复用，此次不是普通问策；这笔旧恩可被点明，也可能被他反向求赏。",
        f"- 旧恩账目：{title}；{cause}" + (f"；{process}" if process else "") + f"；{outcome}",
        f"- 当前人心：才干 {ability}，操守 {integrity}，御前信任 {trust}，怨望 {grievance}"
        + (f"，派系 {faction}" if faction and faction not in {"无", "中立"} else "")
        + "。",
        "- 对话玩法：皇帝可要求其以难差、查账、举荐担保、共办消怨来还恩；NPC 可感恩、试探边界，也可求赏、求保全同党或求缓查旧案。",
        "- 两难要求：不要把旧恩写成免费忠诚。还恩会牵动其党羽、政敌、名节和未来仕途；若皇帝逼得太急，他可能口头顺从、私下拖延。",
    ]
    if allies:
        lines.append("【党羽/同道人情】" + "、".join(f"{a['name']}（{a['basis']}）" for a in allies[:3])
                     + "：他可能替这些人求台阶或要名分。")
    if rivals:
        lines.append("【政敌/旧怨】" + "、".join(f"{r['name']}（{r['basis']}）" for r in rivals[:3])
                     + "：他可能借还恩之名要求打击政敌。")
    lines.extend([
        "- 只有皇帝明确命其承办、要求拟旨、设期限或双方确认条件，才进入奏对目的或履约账本；首次点明旧恩不直接落库。",
        "- 口吻要求：按身份、派系、信任、怨望和人物性格说话；不要像全知旁白解释系统。",
    ])
    return "\n".join(lines)


def monthly_followup_chat_context_brief(db: GameDB, minister_name: str) -> str:
    """Trusted context for an NPC who has a month-start reason to seek audience."""

    name = str(minister_name or "").strip()
    if not name:
        return ""
    try:
        state = db.load_state()
    except Exception:
        return ""
    followups = _monthly_followups_for_brief(db, state, limit=30)
    item = next((row for row in followups if str(row.get("minister_name") or "") == name), None)
    if not isinstance(item, dict):
        return ""

    title = str(item.get("title") or "本月可主动请安回奏。").strip()
    summary = str(item.get("summary") or "").strip()
    hooks = [str(hook) for hook in (item.get("memory_hooks") or []) if str(hook).strip()]
    reasons = [str(reason) for reason in (item.get("reason_types") or []) if str(reason).strip()]
    risks = [str(tag) for tag in (item.get("risk_tags") or []) if str(tag).strip()]
    truth_mode = str(item.get("truth_mode") or "").strip()
    preferred = str(item.get("preferred_stance") or "").strip()
    opening = str(item.get("suggested_opening") or "").strip()
    cue = str(item.get("personality_cue") or "").strip()
    lines = [
        "【本次召对事项：本月主动候见】",
        f"- {name}不是普通被动问策；他本月有理由主动请安、复命、求资源或求明旨。",
        f"- 候见主因：{title}",
    ]
    if summary and summary != title:
        lines.append(f"- 线索摘要：{summary}")
    if hooks:
        lines.append("- 记忆钩子：" + "；".join(hooks[:4]))
    if reasons:
        lines.append("- 系统理由：" + "、".join(_monthly_reason_label(reason) for reason in reasons[:5]))
    if truth_mode or preferred:
        lines.append(
            "- 说话倾向："
            + (f"{truth_mode}" if truth_mode else "按处境取舍真话")
            + (f"；立场偏{preferred}" if preferred else "")
            + "。"
        )
    if opening:
        lines.append(f"- 开场意图：{opening}")
    if cue:
        lines.append(f"- 性格/风险提示：{cue}")
    if risks:
        lines.append("- 风险标签：" + "、".join(risks[:6]))
    lines.extend([
        "- 对话玩法：NPC 应先主动复命或诉难处，再请皇帝给名分、人手、银粮、期限或保全边界；不要等玩家逐条逼问。",
        "- 两难要求：提出的方案必须有代价，可能牵动政敌、同党、钱粮、旧约或密令风险；不要给无成本完美答案。",
        "- 落库边界：只有皇帝明确命其承办、设期限、要求拟旨或双方确认条件，才进入奏对目的/履约账本；普通请安不直接落库。",
        "- 口吻要求：按身份、派系、信任、怨望和人物性格说话；不要像全知旁白解释系统。",
    ])
    return "\n".join(lines)


def patronage_chat_context_brief(
    db: GameDB,
    minister_name: str,
    *,
    target: str = "",
) -> str:
    """Trusted context for a recommendation bond: sponsor and candidate both have stakes."""

    name = str(minister_name or "").strip()
    other = str(target or "").strip()
    if not name or not other or not _table_exists(db, "relationships") or not _table_exists(db, "characters"):
        return ""
    relation = db.conn.execute(
        """
        SELECT opinion, basis
        FROM relationships
        WHERE a_name=? AND b_name=?
        LIMIT 1
        """,
        (name, other),
    ).fetchone()
    if relation is None:
        return ""
    basis = str(relation["basis"] or "")
    if not any(token in basis for token in ("举荐", "举主", "荐取", "入京", "挑补")):
        return ""
    self_row = db.conn.execute(
        """
        SELECT name, office, faction, emp_trust, grievance, summary
        FROM characters
        WHERE name=? AND status='active' AND power_id='ming' AND office_type!='后宫'
        """,
        (name,),
    ).fetchone()
    other_row = db.conn.execute(
        """
        SELECT name, office, faction, emp_trust, grievance, summary
        FROM characters
        WHERE name=? AND status='active' AND power_id='ming' AND office_type!='后宫'
        """,
        (other,),
    ).fetchone()
    if self_row is None or other_row is None:
        return ""
    reverse = db.conn.execute(
        "SELECT opinion, basis FROM relationships WHERE a_name=? AND b_name=? LIMIT 1",
        (other, name),
    ).fetchone()
    reverse_basis = str(reverse["basis"] or "") if reverse is not None else ""
    is_sponsor = "举荐" in basis or "荐取" in basis or "挑补" in basis or "入京" in basis
    if not is_sponsor and not ("举主" in basis or "举主" in reverse_basis):
        return ""
    sponsor = name if is_sponsor else other
    candidate = other if is_sponsor else name
    sponsor_row = self_row if sponsor == name else other_row
    candidate_row = other_row if candidate == other else self_row
    sponsor_opinion = int(relation["opinion"] or 0) if sponsor == name else int((reverse or relation)["opinion"] or 0)
    candidate_opinion = int((reverse or relation)["opinion"] or 0) if sponsor == name else int(relation["opinion"] or 0)
    sponsor_office = _short_office(str(sponsor_row["office"] or ""))
    candidate_office = _short_office(str(candidate_row["office"] or ""))
    candidate_summary = str(candidate_row["summary"] or "")
    sponsor_faction = str(sponsor_row["faction"] or "")
    candidate_faction = str(candidate_row["faction"] or "")
    lines = [
        "【本次召对事项：举主担保】",
        f"- {sponsor_office}{sponsor}曾举荐/引入{candidate_office}{candidate}，这不是无根用人，而是一条可追责的人情链。",
        f"- 关系账：举主对新人 {sponsor_opinion}，新人对举主 {candidate_opinion}；若用错人，举主名声与派系都要受牵连。",
    ]
    if sponsor_faction and sponsor_faction not in {"无", "中立"}:
        lines.append(f"- 举主派系：{sponsor}属{sponsor_faction}，可能借荐人伸手，也可能真有识人之功。")
    if candidate_faction and candidate_faction not in {"无", "中立"}:
        lines.append(f"- 新人标签：{candidate}属{candidate_faction}，入朝后未必只听皇帝，也会受举主/乡党牵引。")
    if candidate_summary:
        lines.append(f"- 新人小传：{_short_text(candidate_summary, 220)}")
    if name == sponsor:
        lines.extend([
            f"- 当前入对者是举主{sponsor}：应让他说明为何荐{candidate}、短板在哪里、愿用什么名节或差事担保。",
            "- 对话玩法：皇帝可要求举主连坐担保、限期带新人共办一事、交出可验差使，或明示若新人坏事则先问举主。",
        ])
    else:
        lines.extend([
            f"- 当前入对者是新人{candidate}：应让他证明自己不是{sponsor}的影子，说明能办什么、会得罪谁、如何避嫌交账。",
            "- 对话玩法：皇帝可给试差、要求避嫌、命其与举主分账，或用新人反查举主的人情网。",
        ])
    lines.extend([
        "- 两难要求：举荐可得人，也会喂大派系和门生故旧；不要把荐人写成免费人才池。",
        "- 落库边界：只有皇帝明确设期限、命共办、要求拟旨或确认担保条件，才进入奏对目的/履约账本。",
        "- 口吻要求：按身份、派系、信任、怨望和人物性格说话；不要像全知旁白解释系统。",
    ])
    return "\n".join(lines)


def _briefing_candidates(db: GameDB, state: Optional[GameState] = None) -> List[BriefCard]:
    """Collect all actionable hooks before the home-screen outliner chooses a subset."""

    cards: List[BriefCard] = []
    _pending_decision_cards(db, cards)
    _trap_cards(db, state, cards)
    _trap_remedy_cards(db, state, cards)
    _directive_blocker_cards(db, cards)
    _directive_followup_cards(db, state, cards)
    _monthly_followup_cards(db, state, cards)
    _patronage_cards(db, cards)
    _petition_cards(db, cards)
    _favor_cards(db, state, cards)
    _policy_legacy_cards(db, state, cards)
    _agenda_cards(db, cards)
    _rivalry_cards(db, cards)
    _army_cards(db, cards)
    _faction_cards(db, cards)
    _secret_cards(db, cards)
    return cards


def _card_rank(card: BriefCard) -> tuple:
    return (
        -int(card.get("urgency") or 0),
        _KIND_PRIORITY.get(str(card.get("kind") or ""), 50),
        str(card.get("title") or ""),
    )


def _select_brief_cards(cards: List[BriefCard], limit: int = 5) -> List[BriefCard]:
    """Pick a compact outliner: urgent first, but keep multiple gameplay systems visible."""

    safe_limit = max(1, min(8, int(limit or 5)))
    ranked = sorted(enumerate(cards), key=lambda item: _card_rank(item[1]))
    selected: List[int] = []
    per_kind: Dict[str, int] = {}

    def add(idx: int, card: BriefCard) -> None:
        selected.append(idx)
        kind = str(card.get("kind") or "")
        per_kind[kind] = per_kind.get(kind, 0) + 1

    for idx, card in ranked:
        kind = str(card.get("kind") or "")
        if per_kind.get(kind, 0) == 0:
            add(idx, card)
            if len(selected) >= safe_limit:
                return [cards[i] for i in selected]

    for idx, card in ranked:
        if idx in selected:
            continue
        kind = str(card.get("kind") or "")
        if per_kind.get(kind, 0) >= 2:
            continue
        add(idx, card)
        if len(selected) >= safe_limit:
            return [cards[i] for i in selected]

    for idx, card in ranked:
        if idx not in selected:
            add(idx, card)
            if len(selected) >= safe_limit:
                break
    return [cards[i] for i in selected]


def _brief_kind_buckets(candidates: List[BriefCard], selected: List[BriefCard]) -> List[Dict[str, object]]:
    """Summarize how many hooks each visible strategic system contributes."""

    totals: Dict[str, int] = {}
    shown: Dict[str, int] = {}
    by_kind: Dict[str, List[BriefCard]] = {}
    for card in candidates:
        kind = str(card.get("kind") or "")
        if kind:
            totals[kind] = totals.get(kind, 0) + 1
            by_kind.setdefault(kind, []).append(card)
    for card in selected:
        kind = str(card.get("kind") or "")
        if kind:
            shown[kind] = shown.get(kind, 0) + 1

    top_urgency = {
        kind: max((_brief_urgency(card) for card in kind_cards), default=0)
        for kind, kind_cards in by_kind.items()
    }

    def sort_key(kind: str) -> tuple:
        return (-top_urgency.get(kind, 0), _KIND_PRIORITY.get(kind, 50), kind)

    buckets: List[Dict[str, object]] = []
    for kind in sorted(totals, key=sort_key):
        total = totals[kind]
        visible = shown.get(kind, 0)
        bucket: Dict[str, object] = {
            "kind": kind,
            "label": _KIND_LABELS.get(kind, "机变"),
            "shown": visible,
            "total": total,
            "hidden": max(0, total - visible),
            "top_urgency": top_urgency.get(kind, 0),
        }
        bucket.update(_brief_top_rank(by_kind.get(kind, [])))
        buckets.append(bucket)
    return buckets


def _brief_rank_counts(cards: List[BriefCard]) -> List[Dict[str, object]]:
    """Summarize the urgency spread for the current strategic-brief view."""

    counts = _brief_rank_count_map(cards)
    return [
        {"level": level, "label": _RANK_LABELS[level], "count": count}
        for level, count in counts.items()
        if count > 0
    ]


def _brief_top_rank(cards: List[BriefCard]) -> Dict[str, object]:
    counts = _brief_rank_count_map(cards)
    for level in ("danger", "warn", "info"):
        count = counts.get(level, 0)
        if count > 0:
            return {"rank_level": level, "rank_label": _RANK_BADGES[level], "rank_count": count}
    return {}


def _brief_rank_count_map(cards: List[BriefCard]) -> Dict[str, int]:
    counts = {"danger": 0, "warn": 0, "info": 0}
    for card in cards:
        urgency = _brief_urgency(card)
        if urgency >= 90:
            counts["danger"] += 1
        elif urgency >= 78:
            counts["warn"] += 1
        elif urgency >= 65:
            counts["info"] += 1
    return counts


def _brief_urgency(card: BriefCard) -> int:
    try:
        return max(0, min(100, int(card.get("urgency") or 0)))
    except (TypeError, ValueError):
        return 0


def _card(
    *,
    kind: str,
    title: str,
    detail: str,
    urgency: int,
    tone: str,
    cta: str,
    tab: str,
    actor: str = "",
    target: str = "",
    meta: str = "",
    ref_kind: str = "",
    ref_id: str = "",
    effects: Optional[List[Dict[str, str]]] = None,
) -> BriefCard:
    card: BriefCard = {
        "kind": kind,
        "title": title,
        "detail": detail,
        "urgency": max(0, min(100, int(urgency))),
        "tone": tone,
        "cta": cta,
        "tab": tab,
        "actor": actor,
        "target": target,
        "meta": meta,
        "ref_kind": ref_kind,
        "ref_id": ref_id,
    }
    if effects:
        card["effects"] = effects
    return card


def _pending_decision_cards(db: GameDB, cards: List[BriefCard]) -> None:
    try:
        from ming_sim.court_events import pending_payload

        decision = pending_payload(db)
    except Exception:
        decision = None
    if not decision:
        return
    choices = decision.get("choices") or []
    cards.append(
        _card(
            kind="decision",
            title=f"请陛下裁断：{decision.get('title')}",
            detail=str(decision.get("narrative") or "")[:90],
            urgency=100,
            tone="danger",
            cta="去裁断",
            tab=_TAB_DESK,
            meta=f"{len(choices)}路待决" if choices else "待决",
            ref_kind="decision",
            ref_id=str(decision.get("id") or ""),
            effects=_decision_stakes(choices),
        )
    )


def _decision_stakes(choices: object) -> List[Dict[str, str]]:
    """Summarize what systems a pending decision can move, without merging option-specific numbers."""

    buckets: Dict[str, Tuple[str, List[str]]] = {}

    def add(bucket: str, label: str, tone: str) -> None:
        if bucket not in buckets:
            buckets[bucket] = (label, [])
        buckets[bucket][1].append(tone)

    for ch in choices if isinstance(choices, list) else []:
        if not isinstance(ch, dict):
            continue
        for eff in ch.get("effects") or []:
            if not isinstance(eff, dict):
                continue
            kind = str(eff.get("kind") or "")
            label = str(eff.get("label") or "")
            tone = str(eff.get("tone") or "neutral")
            if kind in {"shi"}:
                add("shi", "牵动君威", tone)
            elif kind in {"renshi"}:
                add("renshi", "牵动任事", tone)
            elif kind == "eunuch_power" or "权阉" in label:
                add("eunuch", "牵动权阉", tone)
            elif kind.startswith("faction_") or "满意" in label or "势力" in label:
                add("faction", "牵动派系", tone)
            elif kind == "metric" and "民心" in label:
                add("popular", "牵动民心", tone)
            elif kind == "metric" and "国库" in label:
                add("treasury", "牵动国库", tone)
            elif kind in {"status", "appoint"}:
                add("personnel", "人物去留", tone)
            elif kind in {"trust", "grievance"}:
                add("relationship", "信怨变化", tone)
            elif kind == "army" or "军镇" in label or "军心" in label:
                add("army", "牵动军镇", tone)
            elif kind == "supervise":
                add("supervise", "监军入局", tone)

    order = [
        "shi", "renshi", "popular", "treasury", "eunuch", "faction",
        "personnel", "relationship", "army", "supervise",
    ]
    out: List[Dict[str, str]] = []
    for key in order:
        if key not in buckets:
            continue
        label, tones = buckets[key]
        good = any(t == "good" for t in tones)
        bad = any(t == "bad" for t in tones)
        tone = "neutral" if good and bad else "good" if good else "bad" if bad else "neutral"
        out.append({"kind": key, "label": label, "tone": tone})
    return out[:6]


def _trap_cards(db: GameDB, state: Optional[GameState], cards: List[BriefCard]) -> None:
    """Surface the Chongzhen trap as an actionable desk-management hook."""

    if not _table_exists(db, "memorials"):
        return
    try:
        from ming_sim.memorials import INFORMATIONAL_KINDS, attention_left, expire_deadline_days
        from ming_sim.upgrade_schema import KV_RISK_AVERSION, RISK_AVERSION_DEFAULT, get_current_day, kv_int
    except Exception:
        return

    day = get_current_day(db, getattr(state, "turn", 0) if state is not None else 0)
    rows = _safe_fetchall(
        db,
        """
        SELECT id, kind, urgency, summary, arrived_day, shelved_days
        FROM memorials
        WHERE status='pending'
        ORDER BY urgency DESC, arrived_day ASC
        LIMIT 80
        """,
    )
    request_rows = [r for r in rows if str(r["kind"]) not in INFORMATIONAL_KINDS]
    backlog = len(request_rows)
    urgent = 0
    oldest = 0
    for row in request_rows:
        shelved = max(int(row["shelved_days"] or 0), int(day) - int(row["arrived_day"] or day))
        oldest = max(oldest, shelved)
        left = max(0, expire_deadline_days(str(row["kind"]), int(row["urgency"] or 2)) - shelved)
        if 0 < left <= 7:
            urgent += 1

    ra = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
    renshi = max(0, min(100, 100 - ra))
    att = max(0, int(attention_left(db)))
    overloaded = backlog >= max(6, att + 4)
    crisis = urgent > 0 or (overloaded and renshi <= 45)
    chilling = renshi <= 35
    if not (crisis or chilling or overloaded):
        return

    if crisis:
        title = "御案壅塞，百官更怯任事"
        hazard = (
            f"{urgent} 封七日内将淹没"
            if urgent > 0
            else "积压已超过今日处理能力"
        )
        detail = (
            f"待裁奏疏 {backlog} 封，今日精力余 {att}。"
            f"{hazard}；奏而不答会让臣下继续请旨观望。"
        )
        urgency = 84 + urgent * 5 + max(0, backlog - att)
        tone = "danger"
    elif chilling:
        title = "百官避事成风"
        detail = (
            f"任事意愿仅 {renshi}。问责越重，越多人把难题推回御案；"
            "此时为忠臣买单或采纳直言，反而是破局手段。"
        )
        urgency = 78 + max(0, 35 - renshi)
        tone = "danger" if renshi <= 25 else "warn"
    else:
        title = "御案开始压住朝局"
        detail = (
            f"待裁奏疏 {backlog} 封，今日精力余 {att}，最久已压 {oldest} 日。"
            "先批急疏、少留中，才能避免小事滚成群臣观望。"
        )
        urgency = 70 + max(0, backlog - att)
        tone = "warn"

    effects = [
        {"kind": "renshi", "label": f"任事 {renshi}", "tone": "bad" if renshi <= 35 else "neutral"},
        {"kind": "backlog", "label": f"待裁 {backlog}", "tone": "bad" if overloaded or backlog >= 6 else "neutral"},
        {"kind": "attention", "label": f"精力 {att}", "tone": "bad" if att <= 2 else "neutral"},
    ]
    if urgent:
        effects.append({"kind": "deadline", "label": f"将淹没 {urgent}", "tone": "bad"})
    if oldest:
        effects.append({"kind": "shelved", "label": f"最久 {oldest}日", "tone": "bad" if oldest >= 20 else "neutral"})

    cards.append(
        _card(
            kind="trap",
            title=title,
            detail=detail,
            urgency=urgency,
            tone=tone,
            cta="看御案",
            tab=_TAB_DESK,
            meta=f"任事{renshi}/待{backlog}",
            ref_kind="belief",
            ref_id="risk_aversion",
            effects=effects,
        )
    )


def _trap_remedy_cards(db: GameDB, state: Optional[GameState], cards: List[BriefCard]) -> None:
    """Offer a concrete official to back when the court has become fear-bound."""

    if not _table_exists(db, "characters"):
        return
    try:
        from ming_sim.upgrade_schema import KV_RISK_AVERSION, RISK_AVERSION_DEFAULT, kv_int
    except Exception:
        return
    ra = kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT)
    if ra < 58:
        return
    rows = db.conn.execute(
        """
        SELECT name, office, status, status_reason, ability, integrity, courage, emp_trust, grievance
        FROM characters
        WHERE power_id='ming'
          AND office_type!='后宫'
          AND status IN ('imprisoned','dismissed','active')
          AND name!='崇祯'
          AND (
            status IN ('imprisoned','dismissed')
            OR grievance>=58
            OR emp_trust<=36
          )
        ORDER BY
          CASE WHEN status IN ('imprisoned','dismissed') THEN 0 ELSE 1 END,
          ability + integrity + courage DESC,
          grievance DESC,
          emp_trust ASC
        LIMIT 8
        """
    ).fetchall()
    row = None
    for cand in rows:
        if not _recently_backed_official(db, state, str(cand["name"])):
            row = cand
            break
    if row is None:
        return
    name = str(row["name"])
    status = str(row["status"] or "")
    office = _short_office(str(row["office"] or ""))
    reason = str(row["status_reason"] or "").strip()
    if status == "imprisoned":
        title = f"破局人选：复用{name}"
        detail = (
            f"{office}{name}仍在狱中。任事意愿低迷时，败后复用会折损一时威势，"
            "却能让敢任事者知道皇帝不只会问罪。"
        )
        cta = "查此人"
        meta = "可复用"
        back_kind = "reuse"
    elif status == "dismissed":
        title = f"破局人选：起复{name}"
        detail = (
            f"{office}{name}已罢。若旧案并非奸恶，公开复用是一记反直觉落子："
            "短期惹议，长期回暖任事。"
        )
        cta = "查此人"
        meta = "可起复"
        back_kind = "reuse"
    else:
        title = f"破局人选：替{name}担责"
        detail = (
            f"{office}{name}怨气重、信任低。公开担责或抚恤褒奖，会损一点君威，"
            "但能打断人人自保、事事请旨的循环。"
        )
        cta = "查此人"
        meta = "可买单"
        back_kind = "shoulder"
    if reason:
        detail += f"旧因：{_short_text(reason, 32)}。"
    try:
        from ming_sim.memorials import preview_back_official_effects
        effects = preview_back_official_effects(db, name, back_kind)
    except Exception:
        effects = []
    cards.append(
        _card(
            kind="trap_remedy",
            title=title,
            detail=detail,
            urgency=78 + min(18, max(0, ra - 58) // 2),
            tone="warn",
            cta=cta,
            tab=_TAB_DESK,
            actor=name,
            meta=meta,
            ref_kind="character",
            ref_id=name,
            effects=effects,
        )
    )


def _recently_backed_official(db: GameDB, state: Optional[GameState], name: str) -> bool:
    """Avoid repeating a back-this-official recommendation right after action."""

    if not name or not _table_exists(db, "turn_logs"):
        return False
    current_turn = int(getattr(state, "turn", 0) or 0)
    if current_turn <= 0:
        try:
            row = db.conn.execute("SELECT turn FROM game_state WHERE id=1").fetchone()
            current_turn = int(row["turn"] or 0) if row else 0
        except sqlite3.Error:
            current_turn = 0
    rows = _safe_fetchall(
        db,
        """
        SELECT 1
        FROM turn_logs
        WHERE turn>=?
          AND message LIKE '【买单】%'
          AND message LIKE ?
        LIMIT 1
        """,
        (max(1, current_turn - 1), f"%{name}%"),
    )
    return bool(rows)


def _directive_blocker_cards(db: GameDB, cards: List[BriefCard]) -> None:
    """Expose known decree blockers as high-value strategic hooks."""

    if not _table_exists(db, "turn_directives"):
        return
    rows = _safe_fetchall(
        db,
        """
        SELECT id, text, lifecycle_status, progress, assignee, chain, anomaly
        FROM turn_directives
        WHERE lifecycle_status IN ('in_transit','executing','stalled')
        ORDER BY
          CASE WHEN lifecycle_status='stalled' THEN 0 ELSE 1 END,
          id DESC
        LIMIT 24
        """,
    )
    count = 0
    for row in rows:
        meta = _json_dict(row["chain"])
        clue = meta.get("blocker_clue")
        if not isinstance(clue, dict):
            continue
        label = str(clue.get("name") or clue.get("label") or "").strip()
        if not label:
            continue
        if _blocker_action_covers_clue(meta, label, int(clue.get("day") or 0)):
            continue
        did = int(row["id"])
        status = str(row["lifecycle_status"] or "")
        stalled = status == "stalled"
        progress = int(row["progress"] or 0)
        assignee = str(row["assignee"] or "")
        directive = _short_text(str(row["text"] or ""), 30)
        title = f"{label}牵制旨意" if not stalled else f"{label}卡住旨意"
        detail = (
            f"{assignee or '主办官'}承办「{directive}」受{label}掣肘，"
            f"当前进度 {progress}%。可召问阻力，也可在诏旨中协调或申饬。"
        )
        if clue.get("detail"):
            detail += f"线索：{clue.get('detail')}。"
        effects = [
            {"kind": "directive_progress", "label": f"进度 {progress}%", "tone": "bad" if stalled or progress < 50 else "neutral"},
            {"kind": "directive_status", "label": "已卡住" if stalled else "受牵制", "tone": "bad"},
            {"kind": "blocker", "label": f"阻力 {label}", "tone": "bad"},
        ]
        if assignee:
            effects.append({"kind": "assignee", "label": f"主办 {assignee}", "tone": "neutral"})
        cards.append(
            _card(
                kind="directive_blocker",
                title=title,
                detail=detail,
                urgency=(96 if stalled else 82) + min(12, max(0, 70 - progress) // 6),
                tone="danger" if stalled else "warn",
                cta="处置旨意",
                tab=_TAB_EDICTS,
                actor=label if str(clue.get("kind") or "") == "person" else "",
                target=assignee,
                meta=f"{progress}%",
                ref_kind="directive",
                ref_id=str(did),
                effects=effects,
            )
        )
        count += 1
        if count >= 2:
            break


def _blocker_action_covers_clue(meta: Dict[str, object], label: str, clue_day: int) -> bool:
    action = meta.get("last_blocker_action")
    if not isinstance(action, dict):
        return False
    if str(action.get("label") or "").strip() != str(label or "").strip():
        return False
    try:
        action_day = int(action.get("day") or 0)
    except (TypeError, ValueError):
        action_day = 0
    return action_day >= max(0, int(clue_day or 0))


def _directive_followup_cards(db: GameDB, state: Optional[GameState], cards: List[BriefCard]) -> None:
    """Surface completed decrees that deserve a player follow-up conversation."""

    if not _table_exists(db, "turn_directives") or not _table_exists(db, "characters"):
        return
    current_turn = int(getattr(state, "turn", 0) or 0)
    if current_turn <= 0:
        try:
            row = db.conn.execute("SELECT turn FROM game_state WHERE id=1").fetchone()
            current_turn = int(row["turn"] or 0) if row else 0
        except sqlite3.Error:
            current_turn = 0
    recent_turn = max(1, current_turn - 2) if current_turn > 0 else 1
    rows = _safe_fetchall(
        db,
        """
        SELECT d.id, d.turn, d.text, d.assignee, d.progress, d.integrity_actual,
               d.integrity_reported, d.settle_note, d.outcome_status,
               d.chain,
               c.office, c.status AS character_status
        FROM turn_directives d
        LEFT JOIN characters c ON c.name=d.assignee
        WHERE d.status='issued'
          AND d.lifecycle_status='done'
          AND d.turn>=?
          AND COALESCE(d.assignee, '')!=''
          AND COALESCE(c.status, 'active')='active'
        ORDER BY
          CASE WHEN d.outcome_status='applied' THEN 0 ELSE 1 END,
          d.id DESC
        LIMIT 10
        """,
        (recent_turn,),
    )
    count = 0
    for row in rows:
        assignee = str(row["assignee"] or "").strip()
        if not assignee:
            continue
        did = int(row["id"] or 0)
        meta = _json_dict(row["chain"])
        if isinstance(meta.get("last_followup_action"), dict):
            continue
        directive = _short_text(str(row["text"] or ""), 34)
        settle = _short_text(str(row["settle_note"] or ""), 60)
        actual = _clamp_int(row["integrity_actual"], 0, 100)
        reported = _clamp_int(row["integrity_reported"], 0, 100)
        gap = max(0, reported - actual)
        applied = str(row["outcome_status"] or "") == "applied"
        office = _short_office(str(row["office"] or ""))

        if gap >= 18 or actual < 65:
            title = f"复命需追问：{assignee}"
            detail = (
                f"{office}{assignee}已回奏「{directive}」，但奏报 {reported}%、实绩 {actual}%。"
                "可当面问水分、追责或换下一手。"
            )
            urgency = 84 + min(12, gap // 3) + max(0, 65 - actual) // 4
            tone = "danger" if actual < 55 or gap >= 28 else "warn"
            meta = f"实{actual}/奏{reported}"
        else:
            title = f"复命后续：{assignee}"
            detail = (
                f"{office}{assignee}已办结「{directive}」。"
                "此时召来问成效、赏罚分明或顺势续下一道旨意，能把一次执行变成政治资本。"
            )
            urgency = 70 + min(12, actual // 10) + (4 if applied else 0)
            tone = "info" if actual >= 80 else "warn"
            meta = f"完成{actual}%"
        if settle:
            detail += f"复命摘录：{settle}。"

        effects = [
            {"kind": "directive_done", "label": "已复命", "tone": "good" if actual >= 75 else "neutral"},
            {"kind": "directive_actual", "label": f"实绩 {actual}%", "tone": "good" if actual >= 80 else "bad" if actual < 65 else "neutral"},
            {"kind": "directive_reported", "label": f"奏报 {reported}%", "tone": "bad" if gap >= 18 else "neutral"},
        ]
        if gap >= 12:
            effects.append({"kind": "report_gap", "label": f"水分 {gap}", "tone": "bad"})
        if applied:
            effects.append({"kind": "outcome_applied", "label": "结果落库", "tone": "good"})

        cards.append(
            _card(
                kind="directive_followup",
                title=title,
                detail=detail,
                urgency=urgency,
                tone=tone,
                cta="召主办",
                tab=_TAB_AUDIENCE,
                actor=assignee,
                meta=meta,
                ref_kind="directive",
                ref_id=str(did),
                effects=effects,
            )
        )
        count += 1
        if count >= 2:
            break


def _monthly_followups_for_brief(
    db: GameDB,
    state: GameState,
    *,
    limit: int = 12,
) -> List[Dict[str, object]]:
    """Build month-start followups, falling back when global content is not bound."""

    safe_limit = max(1, min(30, int(limit or 12)))
    try:
        from ming_sim.context import build_npc_monthly_followups

        rows = build_npc_monthly_followups(db, state, limit=safe_limit)
        if rows:
            return rows
    except Exception:
        pass
    return _fallback_monthly_followups(db, state, limit=safe_limit)


def _fallback_monthly_followups(
    db: GameDB,
    state: GameState,
    *,
    limit: int,
) -> List[Dict[str, object]]:
    """Minimal deterministic followups from persisted ledgers only."""

    turn = int(getattr(state, "turn", 0) or 0)
    bucket: Dict[str, Dict[str, object]] = {}

    def active(name: str) -> bool:
        if not name or not _table_exists(db, "characters"):
            return False
        row = db.conn.execute(
            """
            SELECT status, power_id, office_type
            FROM characters
            WHERE name=?
            """,
            (name,),
        ).fetchone()
        if row is None:
            return False
        return (
            str(row["status"] or "active") == "active"
            and str(row["power_id"] or "ming") == "ming"
            and str(row["office_type"] or "") != "后宫"
        )

    def add(name: str, reason: str, hook: str, priority: int, risks: Optional[List[str]] = None) -> None:
        name = str(name or "").strip()
        if not active(name):
            return
        item = bucket.setdefault(name, {
            "minister_name": name,
            "priority": 0,
            "reason_types": [],
            "memory_hooks": [],
            "risk_tags": [],
        })
        item["priority"] = int(item.get("priority") or 0) + int(priority)
        reasons = item["reason_types"] if isinstance(item.get("reason_types"), list) else []
        if reason and reason not in reasons:
            reasons.append(reason)
        hooks = item["memory_hooks"] if isinstance(item.get("memory_hooks"), list) else []
        if hook and hook not in hooks:
            hooks.append(hook[:140])
        tags = item["risk_tags"] if isinstance(item.get("risk_tags"), list) else []
        for risk in risks or []:
            text = str(risk or "").strip()
            if text and text not in tags:
                tags.append(text)

    if _table_exists(db, "secret_orders"):
        for row in _safe_fetchall(
            db,
            """
            SELECT id, minister_name, title, due_turn, status
            FROM secret_orders
            WHERE status IN ('active','pending_review')
            ORDER BY
              CASE WHEN due_turn>0 AND due_turn<=? THEN 0 ELSE 1 END,
              id DESC
            LIMIT 40
            """,
            (turn,),
        ):
            name = str(row["minister_name"] or "")
            title = str(row["title"] or "密令")
            due = bool(int(row["due_turn"] or 0) and int(row["due_turn"] or 0) <= turn)
            status = str(row["status"] or "")
            if status == "pending_review":
                add(name, "secret_order_pending_review", f"密令 #{row['id']}「{title}」已候月末核议，应回奏裁断结果。", 22, ["密令核议"])
            else:
                add(
                    name,
                    "secret_order_due" if due else "secret_order_active",
                    f"密令 #{row['id']}「{title}」{'已到限期' if due else '仍在查办'}，应请安回奏进展。",
                    26 if due else 13,
                    ["密令回奏"],
                )

    if _table_exists(db, "conversation_goals"):
        for row in _safe_fetchall(
            db,
            """
            SELECT minister_name, status, title, target_text
            FROM conversation_goals
            WHERE status IN ('active','waiting_conditions','blocked','expired')
            ORDER BY id DESC
            LIMIT 60
            """,
        ):
            name = str(row["minister_name"] or "")
            status = str(row["status"] or "")
            title = str(row["title"] or row["target_text"] or "未竟奏对")
            priority = 18 if status == "waiting_conditions" else 12 if status == "active" else 9
            add(name, f"conversation_goal:{status}", f"未完奏对「{title}」仍需复命或请旨。", priority, ["旧约未了"])

    if _table_exists(db, "negotiation_agreements"):
        for row in _safe_fetchall(
            db,
            """
            SELECT minister_name, status, target_status, core_topic, topic, due_turn
            FROM negotiation_agreements
            WHERE status IN ('pending','sealed')
              AND COALESCE(target_status, '')!='achieved'
            ORDER BY due_turn ASC, id DESC
            LIMIT 60
            """,
        ):
            name = str(row["minister_name"] or "")
            topic = str(row["core_topic"] or row["topic"] or "履约事项")
            due_turn = int(row["due_turn"] or 0)
            due = bool(due_turn and due_turn <= turn)
            target_status = str(row["target_status"] or row["status"] or "")
            add(
                name,
                "agreement_due" if due else f"agreement:{target_status}",
                f"履约账本「{topic}」{'已到回奏时限' if due else '仍待推进'}。",
                24 if due else 14,
                ["履约压力"],
            )

    rows: List[Dict[str, object]] = []
    for name, item in bucket.items():
        hooks = [str(hook) for hook in (item.get("memory_hooks") or []) if str(hook).strip()]
        reasons = [str(reason) for reason in (item.get("reason_types") or []) if str(reason).strip()]
        due = any("due" in reason or "expired" in reason or "blocked" in reason for reason in reasons)
        row = {
            **item,
            "priority": int(item.get("priority") or 0),
            "title": hooks[0] if hooks else "本月可主动请安回奏。",
            "summary": "；".join(hooks[:3]),
            "suggested_opening": (
                "请安时先回奏到期事项，再索要名分、人手、银粮或保全边界。"
                if due else
                "请安后可主动复命，请求明旨或资源，把事往前推。"
            ),
            "preferred_stance": "caution" if due else "neutral",
            "truth_mode": "按利害取舍真话",
            "personality_cue": "从本人掌握的事实与利害说起，不要表现得无所不知",
            "risk_tags": list(item.get("risk_tags") or [])[:6],
        }
        rows.append(row)
    rows.sort(key=lambda row: (int(row.get("priority") or 0), str(row.get("minister_name") or "")), reverse=True)
    return rows[:limit]


def _monthly_followup_cards(db: GameDB, state: Optional[GameState], cards: List[BriefCard]) -> None:
    """Surface NPCs who have month-start reasons to come seek audience."""

    if state is None or not _table_exists(db, "characters"):
        return
    followups = _monthly_followups_for_brief(db, state, limit=12)

    count = 0
    for item in followups:
        name = str(item.get("minister_name") or "").strip()
        if not name:
            continue
        if any(
            str(card.get("actor") or "") == name
            and str(card.get("kind") or "") in {"trap_remedy", "directive_followup"}
            for card in cards
        ):
            continue
        char = db.conn.execute(
            """
            SELECT office, faction, emp_trust, grievance
            FROM characters
            WHERE name=?
              AND status='active'
              AND power_id='ming'
              AND office_type!='后宫'
            """,
            (name,),
        ).fetchone()
        if char is None:
            continue
        reasons = [str(reason) for reason in (item.get("reason_types") or []) if str(reason).strip()]
        hooks = [str(hook) for hook in (item.get("memory_hooks") or []) if str(hook).strip()]
        risks = [str(tag) for tag in (item.get("risk_tags") or []) if str(tag).strip()]
        if not reasons and not hooks:
            continue
        office = _short_office(str(char["office"] or ""))
        trust = _clamp_int(char["emp_trust"], 0, 100)
        grievance = _clamp_int(char["grievance"], 0, 100)
        priority = _clamp_int(item.get("priority"), 0, 120)
        due = any("due" in reason or "expired" in reason or "blocked" in reason for reason in reasons)
        secret = any("secret_order" in reason for reason in reasons)
        agreement = any("agreement" in reason or "conversation_goal" in reason for reason in reasons)
        speech = any("stance" in reason or "speech" in reason for reason in reasons)
        title = str(item.get("title") or (hooks[0] if hooks else "本月可主动请安回奏。")).strip()
        summary = str(item.get("summary") or "").strip()
        urgency = min(98, 58 + priority + (8 if due else 0) + (4 if secret else 0))
        tone = "danger" if due and (secret or agreement) else "warn" if due or risks else "info"
        meta_bits = []
        if due:
            meta_bits.append("到期")
        if secret:
            meta_bits.append("密令")
        if agreement:
            meta_bits.append("旧约")
        if speech:
            meta_bits.append("口径")
        meta = "/".join(meta_bits[:3]) or _monthly_reason_label(reasons[0] if reasons else "请安")
        effects = [
            {"kind": "monthly_followup", "label": _monthly_reason_label(reasons[0] if reasons else "请安"), "tone": "bad" if due else "neutral"},
            {"kind": "trust", "label": f"信任 {trust}", "tone": "bad" if trust <= 36 else "neutral"},
            {"kind": "grievance", "label": f"怨望 {grievance}", "tone": "bad" if grievance >= 58 else "neutral"},
        ]
        if secret:
            effects.append({"kind": "secret_order", "label": "密令回奏", "tone": "bad" if due else "neutral"})
        if agreement:
            effects.append({"kind": "agreement", "label": "旧约待复", "tone": "bad" if due else "neutral"})
        if speech:
            effects.append({"kind": "speech", "label": "延续口径", "tone": "neutral"})
        if risks:
            effects.append({"kind": "risk", "label": risks[0], "tone": "bad" if due else "neutral"})
        detail = (
            f"{office}{name}本月有事候见。{summary or title}"
            f"{'；'.join(hooks[:2]) if hooks else ''}"
        )
        opening = str(item.get("suggested_opening") or "").strip()
        if opening:
            detail += f"其意在：{opening}"
        cards.append(
            _card(
                kind="monthly_followup",
                title=f"{name}候见：{_short_text(title, 24)}",
                detail=_short_text(detail, 150),
                urgency=urgency,
                tone=tone,
                cta="召来请安",
                tab=_TAB_AUDIENCE,
                actor=name,
                meta=meta,
                ref_kind="monthly_followup",
                ref_id=name,
                effects=effects[:6],
            )
        )
        count += 1
        if count >= 2:
            break


def _patronage_cards(db: GameDB, cards: List[BriefCard]) -> None:
    """Surface recommendation bonds as playable patronage accountability hooks."""

    if not _table_exists(db, "relationships") or not _table_exists(db, "characters"):
        return
    rows = _safe_fetchall(
        db,
        """
        SELECT r.a_name AS sponsor, r.b_name AS candidate, r.opinion AS sponsor_opinion,
               r.basis AS basis,
               rb.opinion AS candidate_opinion, rb.basis AS reverse_basis,
               cs.office AS sponsor_office, cs.faction AS sponsor_faction,
               cc.office AS candidate_office, cc.faction AS candidate_faction,
               cc.summary AS candidate_summary, cc.emp_trust AS candidate_trust,
               cc.grievance AS candidate_grievance
        FROM relationships r
        JOIN characters cs ON cs.name=r.a_name
        JOIN characters cc ON cc.name=r.b_name
        LEFT JOIN relationships rb ON rb.a_name=r.b_name AND rb.b_name=r.a_name
        WHERE cs.status='active'
          AND cc.status='active'
          AND cs.power_id='ming'
          AND cc.power_id='ming'
          AND cs.office_type!='后宫'
          AND cc.office_type!='后宫'
          AND r.opinion>=18
          AND (
            r.basis LIKE '%举荐%'
            OR r.basis LIKE '%荐取%'
            OR r.basis LIKE '%挑补%'
            OR r.basis LIKE '%入京%'
          )
        ORDER BY
          CASE WHEN cc.office LIKE '%待铨%' OR cc.office_type LIKE '%待铨%' THEN 0 ELSE 1 END,
          r.opinion DESC,
          cc.grievance DESC
        LIMIT 16
        """,
    )
    seen: set[tuple[str, str]] = set()
    count = 0
    for row in rows:
        sponsor = str(row["sponsor"] or "").strip()
        candidate = str(row["candidate"] or "").strip()
        if not sponsor or not candidate or sponsor == candidate:
            continue
        key = (sponsor, candidate)
        if key in seen:
            continue
        seen.add(key)
        if any(
            str(card.get("actor") or "") in {sponsor, candidate}
            and str(card.get("kind") or "") in {"trap_remedy", "directive_followup"}
            for card in cards
        ):
            continue
        sponsor_opinion = _clamp_int(row["sponsor_opinion"], -100, 100)
        candidate_opinion = _clamp_int(row["candidate_opinion"], -100, 100)
        if candidate_opinion == 0:
            candidate_opinion = sponsor_opinion
        basis = str(row["basis"] or "举荐入朝")
        sponsor_office = _short_office(str(row["sponsor_office"] or ""))
        candidate_office = _short_office(str(row["candidate_office"] or ""))
        sponsor_faction = str(row["sponsor_faction"] or "")
        candidate_faction = str(row["candidate_faction"] or "")
        summary = str(row["candidate_summary"] or "")
        trust = _clamp_int(row["candidate_trust"], 0, 100)
        grievance = _clamp_int(row["candidate_grievance"], 0, 100)
        faction_risk = (
            bool(sponsor_faction and candidate_faction and sponsor_faction == candidate_faction and sponsor_faction not in {"无", "中立"})
            or "风险" in summary
        )
        effects = [
            {"kind": "patronage", "label": f"举主 {sponsor}", "tone": "neutral"},
            {"kind": "candidate", "label": f"新人 {candidate}", "tone": "neutral"},
            {"kind": "opinion", "label": f"举主关系 {sponsor_opinion}", "tone": "good" if sponsor_opinion >= 30 else "neutral"},
            {"kind": "obligation", "label": f"恩义 {candidate_opinion}", "tone": "good" if candidate_opinion >= 30 else "neutral"},
        ]
        if sponsor_faction and sponsor_faction not in {"无", "中立"}:
            effects.append({"kind": "faction", "label": sponsor_faction, "tone": "bad" if faction_risk else "neutral"})
        if trust <= 45:
            effects.append({"kind": "trust", "label": f"新人信任 {trust}", "tone": "bad"})
        if grievance >= 45:
            effects.append({"kind": "grievance", "label": f"新人怨望 {grievance}", "tone": "bad"})
        detail = (
            f"{sponsor_office}{sponsor}以「{basis}」引{candidate_office}{candidate}入朝。"
            "这条人情链可用来得人，也可追问举主是否愿以名节担保。"
        )
        if summary:
            detail += f"新人档案：{_short_text(summary, 68)}"
        cards.append(
            _card(
                kind="patronage",
                title=f"举主担保：{sponsor}荐{candidate}",
                detail=detail,
                urgency=70 + min(18, sponsor_opinion // 4) + (8 if faction_risk else 0),
                tone="warn" if faction_risk else "info",
                cta="问举主",
                tab=_TAB_AUDIENCE,
                actor=sponsor,
                target=candidate,
                meta="连坐担保" if faction_risk else "人情链",
                ref_kind="relationship",
                ref_id=f"{sponsor}:{candidate}",
                effects=effects[:6],
            )
        )
        count += 1
        if count >= 2:
            break


def _petition_cards(db: GameDB, cards: List[BriefCard]) -> None:
    """Surface active characters who would plausibly seek imperial help.

    This is the home-screen equivalent of a CK3 character coming to court with a
    personal problem.  It uses only existing trust/grievance/relation data and
    does not create a new action panel.
    """

    if not _table_exists(db, "characters"):
        return
    rows = _safe_fetchall(
        db,
        """
        SELECT name, office, faction, ability, integrity, emp_trust, grievance
        FROM characters
        WHERE status='active'
          AND power_id='ming'
          AND office_type!='后宫'
          AND name!='崇祯'
          AND (
            grievance>=58
            OR emp_trust<=36
            OR (
              SELECT COUNT(1)
              FROM relationships r
              JOIN characters other ON other.name=r.b_name
              WHERE r.a_name=characters.name
                AND r.opinion<=-62
                AND other.status='active'
                AND other.power_id='ming'
            )>=1
          )
        ORDER BY grievance DESC, emp_trust ASC, ability DESC
        LIMIT 8
        """,
    )
    count = 0
    for row in rows:
        name = str(row["name"])
        if any(
            str(card.get("actor") or "") == name
            and str(card.get("kind") or "") in {"trap_remedy", "directive_followup", "monthly_followup"}
            for card in cards
        ):
            continue
        office = _short_office(str(row["office"] or ""))
        trust = _clamp_int(row["emp_trust"], 0, 100)
        grievance = _clamp_int(row["grievance"], 0, 100)
        faction = str(row["faction"] or "")
        rival, opinion, basis = _worst_rival_of(db, name)

        if grievance >= 72:
            title = f"{name}求见：旧怨压身"
            detail = (
                f"{office}{name}怨望已至 {grievance}、信任 {trust}。"
                "此人未必反叛，却正需要陛下给一个可下台阶的说法。"
            )
            urgency = 82 + min(14, (grievance - 72) // 2) + max(0, 38 - trust) // 3
            tone = "danger" if grievance >= 84 or trust <= 25 else "warn"
            meta = f"怨{grievance}/信{trust}"
        elif trust <= 30:
            title = f"{name}求自辩"
            detail = (
                f"{office}{name}对御前信任只余 {trust}。若放任，他会转入自保；"
                "若召来问清，或能逼出条件与可验差使。"
            )
            urgency = 78 + max(0, 30 - trust)
            tone = "warn"
            meta = f"信{trust}"
        else:
            title = f"{name}求陛下护持"
            detail = (
                f"{office}{name}同{rival or '政敌'}嫌隙渐深。"
                "他来求援未必全是公心，正可当面问代价、逼其交差。"
            )
            urgency = 74 + min(18, abs(opinion) // 4)
            tone = "warn"
            meta = f"怨{grievance}"

        effects = [
            {"kind": "trust", "label": f"信任 {trust}", "tone": "bad" if trust <= 36 else "neutral"},
            {"kind": "grievance", "label": f"怨望 {grievance}", "tone": "bad" if grievance >= 58 else "neutral"},
            {"kind": "petition", "label": "可安抚/可压榨", "tone": "neutral"},
        ]
        if faction and faction not in {"无", "中立"}:
            effects.append({"kind": "faction", "label": faction, "tone": "neutral"})
        if rival:
            effects.append({"kind": "rivalry", "label": f"政敌 {rival}", "tone": "bad"})
            if basis:
                detail += f"旧因：{basis}。"

        cards.append(
            _card(
                kind="petition",
                title=title,
                detail=detail,
                urgency=urgency,
                tone=tone,
                cta="召来听诉",
                tab=_TAB_AUDIENCE,
                actor=name,
                target=rival,
                meta=meta,
                ref_kind="character",
                ref_id=name,
                effects=effects,
            )
        )
        count += 1
        if count >= 2:
            break


def _favor_cards(db: GameDB, state: Optional[GameState], cards: List[BriefCard]) -> None:
    """Surface unpaid imperial favors as character-driven audience hooks."""

    if not _table_exists(db, "event_memories") or not _table_exists(db, "characters"):
        return
    current_turn = int(getattr(state, "turn", 0) or 0)
    if current_turn <= 0:
        try:
            current_turn = int(db.load_state().turn)
        except Exception:
            current_turn = 0
    rows = _safe_fetchall(
        db,
        """
        SELECT m.id, m.subject_id, m.title, m.cause, m.outcome, m.importance,
               m.turn, m.tags,
               c.office, c.faction, c.emp_trust, c.grievance
        FROM event_memories m
        JOIN characters c ON c.name=m.subject_id
        WHERE m.subject_type='character'
          AND m.event_type='imperial_favor'
          AND (m.expires_turn IS NULL OR m.expires_turn>=?)
          AND c.status='active'
          AND c.power_id='ming'
          AND c.office_type!='后宫'
          AND c.name!='崇祯'
        ORDER BY m.importance DESC, m.turn DESC, m.id DESC
        LIMIT 24
        """,
        (current_turn,),
    )
    grouped: Dict[str, List[sqlite3.Row]] = {}
    order: List[str] = []
    for row in rows:
        name = str(row["subject_id"] or "").strip()
        if not name:
            continue
        if name not in grouped:
            grouped[name] = []
            order.append(name)
        grouped[name].append(row)

    count = 0
    for name in order:
        if any(
            str(card.get("actor") or "") == name
            and str(card.get("kind") or "") in {"trap_remedy", "directive_followup", "petition"}
            for card in cards
        ):
            continue
        items = grouped.get(name) or []
        if not items:
            continue
        row = items[0]
        favor_count = len(items)
        office = _short_office(str(row["office"] or ""))
        trust = _clamp_int(row["emp_trust"], 0, 100)
        grievance = _clamp_int(row["grievance"], 0, 100)
        importance = _clamp_int(row["importance"], 1, 5)
        faction = str(row["faction"] or "")
        cause = _short_text(str(row["cause"] or ""), 54)
        outcome = _short_text(str(row["outcome"] or ""), 64)
        try:
            from ming_sim import court
            allies = court.allies_of(db, name, limit=3)
            rivals = court.rivals_of(db, name, limit=3)
        except Exception:
            allies = []
            rivals = []
        urgency = min(96, 60 + importance * 6 + favor_count * 5 + max(0, trust - 55) // 4 + max(0, grievance - 45) // 3)
        tone = "danger" if favor_count >= 2 and grievance >= 55 else "warn"
        effects = [
            {"kind": "favor", "label": f"旧恩 {favor_count}笔", "tone": "good"},
            {"kind": "trust", "label": f"信任 {trust}", "tone": "good" if trust >= 58 else "neutral"},
            {"kind": "grievance", "label": f"怨望 {grievance}", "tone": "bad" if grievance >= 55 else "neutral"},
        ]
        if faction and faction not in {"无", "中立"}:
            effects.append({"kind": "faction", "label": faction, "tone": "neutral"})
        if allies:
            effects.append({"kind": "network", "label": f"党羽 {len(allies)}人", "tone": "neutral"})
        if rivals:
            effects.append({"kind": "network", "label": f"政敌 {len(rivals)}人", "tone": "bad"})
        detail = (
            f"{office}{name}受过天恩，{cause or '旧恩还挂在账上'}。"
            f"{outcome or '召来可点明旧恩，逼其以差使、人脉或担保来还。'}"
            "这既是可用软钩子，也可能被他反过来要赏、求保或护党。"
        )
        cards.append(
            _card(
                kind="favor",
                title=f"旧恩未报：{name}",
                detail=detail,
                urgency=urgency,
                tone=tone,
                cta="召来还恩",
                tab=_TAB_AUDIENCE,
                actor=name,
                meta=f"{favor_count}笔",
                ref_kind="memory",
                ref_id=str(row["id"] or ""),
                effects=effects,
            )
        )
        count += 1
        if count >= 2:
            break


def _policy_legacy_cards(db: GameDB, state: Optional[GameState], cards: List[BriefCard]) -> None:
    """Expose active long-running policy scars as player-facing strategic hooks."""

    if not _table_exists(db, "legacies"):
        return
    rows = _safe_fetchall(
        db,
        """
        SELECT id, name, modifiers, narrative_hint, duration_months, start_month, legacy_key
        FROM legacies
        WHERE status='active'
        ORDER BY id DESC
        LIMIT 24
        """,
    )
    count = 0
    for row in rows:
        name = str(row["name"] or "")
        hint = str(row["narrative_hint"] or "")
        policy = _legacy_policy_payload(row)
        if not policy["is_policy"]:
            continue
        duration = int(policy["duration"])
        minxin = int(policy["minxin"])
        remaining = -1 if duration < 0 else duration
        if state is not None and duration >= 0:
            try:
                remaining = db.legacy_remaining_months(row, state)
            except Exception:
                remaining = duration
        duration_label = "永久" if duration < 0 else f"余{remaining}月"
        effects = policy_legacy_effect_labels_safe(row)
        actor = _policy_legacy_actor(db, row)
        if actor and any(
            str(card.get("actor") or "") == actor
            and str(card.get("kind") or "") in {"trap_remedy", "directive_followup", "monthly_followup", "petition", "favor"}
            for card in cards
        ):
            continue
        cards.append(
            _card(
                kind="legacy",
                title=f"政策余波：{name}",
                detail=hint or "此项旧政仍在拖动朝局。钱粮、民心与地方承受力不会因办结而立刻归零。",
                urgency=68 + min(24, abs(minxin) * 2) + (8 if duration < 0 else 0),
                tone="danger" if duration < 0 or minxin <= -10 else "warn",
                cta="召人问余波" if actor else "看天下",
                tab=_TAB_AUDIENCE if actor else _TAB_REALM,
                actor=actor,
                meta=duration_label,
                ref_kind="legacy",
                ref_id=str(row["id"]),
                effects=effects,
            )
        )
        count += 1
        if count >= 2:
            break


def _legacy_policy_payload(row) -> Dict[str, object]:
    name = str(row["name"] or "")
    hint = str(row["narrative_hint"] or "")
    key = str(row["legacy_key"] or "")
    try:
        modifiers = json.loads(str(row["modifiers"] or "{}"))
    except (TypeError, ValueError):
        modifiers = {}
    minxin = int(modifiers.get("民心") or 0) if isinstance(modifiers, dict) else 0
    try:
        duration = int(row["duration_months"] or 0)
    except (TypeError, ValueError):
        duration = 0
    stem = _legacy_tax_stem(name, hint, key)
    is_policy = (
        key.startswith("directive_tax:")
        or bool(stem)
        or any(token in name + hint for token in ("苛税", "税负", "加派", "加征", "常税"))
        or minxin <= -6
    )
    return {"is_policy": is_policy, "minxin": minxin, "duration": duration, "stem": stem}


def _legacy_tax_stem(name: str, hint: str, key: str) -> str:
    text = f"{name} {hint} {key}"
    for stem in ("辽饷", "商税", "盐税", "矿税", "田赋"):
        if stem in text:
            return stem
    if "税" in text or "饷" in text or "派" in text:
        return "税负"
    return ""


def policy_legacy_effect_labels_safe(row) -> List[Dict[str, str]]:
    try:
        from ming_sim.policies import policy_legacy_effect_labels
        return policy_legacy_effect_labels(row)
    except Exception:
        return []


def _policy_legacy_actor(db: GameDB, row) -> str:
    policy = _legacy_policy_payload(row)
    if not policy["is_policy"] or not _table_exists(db, "characters"):
        return ""
    stem = str(policy.get("stem") or "")
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
    row2 = db.conn.execute(
        f"""
        SELECT name
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
    return str(row2["name"] or "") if row2 is not None else ""


def _agenda_cards(db: GameDB, cards: List[BriefCard]) -> None:
    if not _table_exists(db, "npc_agendas") or not _has_column(db, "npc_agendas", "progress"):
        return
    rows = _safe_fetchall(
        db,
        """
        SELECT a.name, a.kind, a.title, a.target_name, a.intensity, a.progress,
               c.office, c.faction, c.ability
        FROM npc_agendas a
        JOIN characters c ON c.name=a.name
        WHERE a.status='active'
          AND c.status='active'
          AND c.power_id='ming'
          AND c.office_type!='后宫'
          AND a.progress>=62
        ORDER BY a.progress DESC, a.intensity DESC, c.ability DESC
        LIMIT 6
        """,
    )
    for row in rows:
        name = str(row["name"])
        if any(
            str(card.get("actor") or "") == name
            and str(card.get("kind") or "") in {"trap_remedy", "directive_followup", "monthly_followup", "petition", "favor", "legacy"}
            for card in cards
        ):
            continue
        kind = str(row["kind"] or "")
        office = _short_office(str(row["office"] or ""))
        faction = str(row["faction"] or "")
        progress = int(row["progress"] or 0)
        intensity = int(row["intensity"] or 50)
        label = _AGENDA_LABELS.get(kind, str(row["title"] or "私心将成"))
        prefix = "将成局" if progress >= 85 else "有苗头"
        risky = kind in {"enrich", "protect", "entrench", "revenge"}
        effects = [
            {"kind": "agenda_progress", "label": f"进度 {progress}%", "tone": "bad" if progress >= 85 else "neutral"},
            {"kind": "agenda_intensity", "label": f"强度 {intensity}", "tone": "bad" if intensity >= 75 else "neutral"},
            {"kind": "agenda_kind", "label": label, "tone": "bad" if risky else "neutral"},
        ]
        if faction and faction not in {"无", "中立"}:
            effects.append({"kind": "faction", "label": faction, "tone": "neutral"})
        cards.append(
            _card(
                kind="agenda",
                title=f"{name}{prefix}：{label}",
                detail=f"{office}{name}近来动作渐密。{_AGENDA_HINTS.get(kind, '可召来探口风，再决定拉拢、压制或借力。')}",
                urgency=progress + intensity // 6,
                tone="warn" if progress < 85 else "danger",
                cta="召来问对",
                tab=_TAB_AUDIENCE,
                actor=name,
                target=str(row["target_name"] or ""),
                meta=f"{progress}%",
                ref_kind="character",
                ref_id=name,
                effects=effects,
            )
        )


def _rivalry_cards(db: GameDB, cards: List[BriefCard]) -> None:
    if not _table_exists(db, "relationships"):
        return
    rows = _safe_fetchall(
        db,
        """
        SELECT r.a_name, r.b_name, r.opinion, r.basis,
               ca.office AS a_office, cb.office AS b_office
        FROM relationships r
        JOIN characters ca ON ca.name=r.a_name
        JOIN characters cb ON cb.name=r.b_name
        WHERE r.opinion<=-68
          AND ca.status='active'
          AND cb.status='active'
          AND ca.power_id='ming'
          AND cb.power_id='ming'
          AND ca.office_type!='后宫'
          AND cb.office_type!='后宫'
        ORDER BY r.opinion ASC
        LIMIT 24
        """,
    )
    seen = set()
    for row in rows:
        a = str(row["a_name"])
        b = str(row["b_name"])
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        opinion = int(row["opinion"] or 0)
        basis = str(row["basis"] or "旧怨")
        effects = [
            {"kind": "opinion", "label": f"关系 {opinion}", "tone": "bad"},
            {"kind": "rivalry_basis", "label": basis, "tone": "neutral"},
            {"kind": "rivalry", "label": "可借力/可失控", "tone": "bad"},
        ]
        cards.append(
            _card(
                kind="rivalry",
                title=f"{a}与{b}怨深",
                detail=f"{_short_office(str(row['a_office'] or ''))}{a}同{_short_office(str(row['b_office'] or ''))}{b}嫌隙已深（{basis}）。可坐视相攻，也可召一方借力。",
                urgency=70 + min(28, abs(opinion) // 3),
                tone="danger",
                cta="择人召对",
                tab=_TAB_AUDIENCE,
                actor=a,
                target=b,
                meta=str(opinion),
                ref_kind="relationship",
                ref_id=f"{a}:{b}",
                effects=effects,
            )
        )
        if len([c for c in cards if c.get("kind") == "rivalry"]) >= 2:
            break


def _army_cards(db: GameDB, cards: List[BriefCard]) -> None:
    if not _table_exists(db, "armies"):
        return
    rows = _safe_fetchall(
        db,
        """
        SELECT id, name, commander, arrears, maintenance_per_turn, loyalty, morale, autonomy, supervisor
        FROM armies
        WHERE owner_power='ming'
          AND (autonomy>=42 OR arrears>=maintenance_per_turn*3)
        ORDER BY autonomy DESC, arrears DESC, loyalty ASC
        LIMIT 5
        """,
    )
    for row in rows:
        army = str(row["name"])
        commander = str(row["commander"] or "")
        autonomy = int(row["autonomy"] or 0)
        loyalty = int(row["loyalty"] or 0)
        maint = max(1, int(row["maintenance_per_turn"] or 1))
        arrears_months = int(row["arrears"] or 0) / maint
        supervisor = str(row["supervisor"] or "")
        hook = "已有监军钳制" if supervisor else "尚无近身制衡"
        effects = [
            {"kind": "army_autonomy", "label": f"离心 {autonomy}", "tone": "bad" if autonomy >= 65 else "neutral"},
            {"kind": "army_arrears", "label": f"欠饷 {arrears_months:.1f}月", "tone": "bad" if arrears_months >= 3 else "neutral"},
        ]
        if loyalty <= 45:
            effects.append({"kind": "army_loyalty", "label": f"军心 {loyalty}", "tone": "bad"})
        effects.append(
            {"kind": "supervisor", "label": f"{supervisor}监军" if supervisor else "无监军制衡",
             "tone": "good" if supervisor else "bad"}
        )
        if autonomy >= 35:
            title = f"{army}离心渐重"
            detail = (
                f"{army}由{commander or '主帅'}统带，离心 {autonomy}，欠饷约 {arrears_months:.1f} 月，"
                f"{hook}。这是边镇割据的前兆。"
            )
            meta = f"离心{autonomy}"
        else:
            title = f"{army}欠饷压军心"
            detail = (
                f"{army}欠饷约 {arrears_months:.1f} 月，眼下未必离心，却会持续喂高主帅自专与兵卒怨气。"
                f"{hook}。"
            )
            meta = f"欠{arrears_months:.0f}月"
        cards.append(
            _card(
                kind="army",
                title=title,
                detail=detail,
                urgency=autonomy + int(arrears_months * 9) + (0 if supervisor else 8),
                tone="danger" if autonomy >= 65 else "warn",
                cta="看天下",
                tab=_TAB_REALM,
                actor=commander,
                meta=meta,
                ref_kind="army",
                ref_id=str(row["id"]),
                effects=effects,
            )
        )
        if len([c for c in cards if c.get("kind") == "army"]) >= 2:
            break


def _faction_cards(db: GameDB, cards: List[BriefCard]) -> None:
    if not _table_exists(db, "factions"):
        return
    has_heat = _has_column(db, "factions", "heat")
    heat_expr = "heat" if has_heat else "20"
    rows = _safe_fetchall(
        db,
        f"""
        SELECT name, satisfaction, leverage, agenda, {heat_expr} AS heat
        FROM factions
        WHERE name NOT IN ('无','中立')
          AND (satisfaction<=28 OR leverage>=68 OR {heat_expr}>=62)
        ORDER BY leverage DESC, {heat_expr} DESC, satisfaction ASC
        LIMIT 4
        """,
    )
    for row in rows:
        name = str(row["name"])
        sat = int(row["satisfaction"] or 0)
        lev = int(row["leverage"] or 0)
        heat = int(row["heat"] or 0)
        representative = _faction_representative(db, name)
        effects = [
            {"kind": "faction_leverage", "label": f"势力 {lev}", "tone": "bad" if lev >= 68 else "neutral"},
            {"kind": "faction_grievance", "label": f"怨气 {100 - sat}", "tone": "bad" if sat <= 28 else "neutral"},
        ]
        if heat >= 45:
            effects.append({"kind": "faction_heat", "label": f"党争热度 {heat}", "tone": "bad" if heat >= 62 else "neutral"})
        if representative:
            effects.append({"kind": "representative", "label": f"代表 {representative}", "tone": "neutral"})
        if lev >= 70 and sat <= 35:
            title = f"{name}势大而不满"
            detail = f"{name}势力 {lev}、满意 {sat}，一旦有人串联，朝争会变成逼宫式要价。"
            urgency = lev + (35 - sat) + heat // 3
            tone = "danger"
        elif lev >= 68:
            title = f"{name}势大可借"
            detail = f"{name}势力 {lev}、满意 {sat}。借其办事见效快，但会让朝臣觉得内廷或党援又进一层。"
            urgency = lev + max(0, heat - 40) // 3
            tone = "warn"
        elif heat >= 62:
            title = f"{name}敌意升温"
            detail = f"{name}热度 {heat}，党争正在抬头。此时一纸任免或一场问罪都会被读成信号。"
            urgency = heat + lev // 4
            tone = "warn"
        else:
            title = f"{name}怨气可用"
            detail = f"{name}满意 {sat}。安抚可换一时任事，放任则可能倒向更激烈的路数。"
            urgency = 62 + (35 - sat) + lev // 5
            tone = "warn"
        cards.append(
            _card(
                kind="faction",
                title=title,
                detail=detail,
                urgency=urgency,
                tone=tone,
                cta="看御案",
                tab=_TAB_DESK,
                actor=representative,
                meta=f"势{lev}/怨{100 - sat}",
                ref_kind="faction",
                ref_id=name,
                effects=effects,
            )
        )


def _secret_cards(db: GameDB, cards: List[BriefCard]) -> None:
    if not _table_exists(db, "secrets"):
        return
    rows = _safe_fetchall(
        db,
        """
        SELECT s.holder, s.kind, s.detail, s.severity, c.office
        FROM secrets s
        JOIN characters c ON c.name=s.holder
        WHERE s.known_to_crown=1
          AND s.used=0
          AND c.status='active'
          AND c.power_id='ming'
        ORDER BY s.severity DESC
        LIMIT 3
        """,
    )
    for row in rows:
        holder = str(row["holder"])
        severity = int(row["severity"] or 0)
        kind = str(row["kind"] or "把柄")
        detail = str(row["detail"] or "旧案在手")
        effects = [
            {"kind": "secret_kind", "label": kind, "tone": "bad" if severity >= 65 else "neutral"},
            {"kind": "secret_severity", "label": f"严重 {severity}", "tone": "bad" if severity >= 65 else "neutral"},
            {"kind": "secret_unused", "label": "未动用", "tone": "good"},
        ]
        cards.append(
            _card(
                kind="hook",
                title=f"把柄在手：{holder}",
                detail=f"{_short_office(str(row['office'] or ''))}{holder}有{kind}之柄：{detail}。可挟制、可问罪，也可暂留不用。",
                urgency=68 + severity // 2,
                tone="danger" if severity >= 65 else "warn",
                cta="召来试探",
                tab=_TAB_AUDIENCE,
                actor=holder,
                meta=f"重{severity}",
                ref_kind="character",
                ref_id=holder,
                effects=effects,
            )
        )


def _short_office(office: str) -> str:
    office = str(office or "").strip()
    if not office:
        return ""
    if len(office) <= 8:
        return office
    return office[:8]


def _short_text(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[:limit] + "…"


def _clamp_int(value: object, low: int, high: int) -> int:
    try:
        num = int(value or 0)
    except (TypeError, ValueError):
        num = low
    return max(low, min(high, num))


def _monthly_reason_label(reason: str) -> str:
    text = str(reason or "")
    if "secret_order_due" in text:
        return "密令到期"
    if "secret_order_pending_review" in text:
        return "密令核议"
    if "secret_order_active" in text:
        return "密令在办"
    if "agreement_due" in text:
        return "履约到期"
    if text.startswith("agreement:"):
        return "履约待推"
    if "conversation_goal:waiting_conditions" in text:
        return "旧约待复"
    if "conversation_goal:active" in text:
        return "奏对未完"
    if "conversation_goal:blocked" in text:
        return "奏对受阻"
    if "conversation_goal:expired" in text:
        return "奏对失期"
    if "last_month_stance" in text:
        return "上月口径"
    if "speech_continuity" in text:
        return "话术延续"
    if "gazette_mentioned" in text:
        return "邸报点名"
    return "主动请安"


def _worst_rival_of(db: GameDB, name: str) -> Tuple[str, int, str]:
    if not name or not _table_exists(db, "relationships"):
        return "", 0, ""
    row = db.conn.execute(
        """
        SELECT r.b_name, r.opinion, r.basis
        FROM relationships r
        JOIN characters c ON c.name=r.b_name
        WHERE r.a_name=?
          AND r.opinion<=-55
          AND c.status='active'
          AND c.power_id='ming'
          AND c.office_type!='后宫'
        ORDER BY r.opinion ASC
        LIMIT 1
        """,
        (name,),
    ).fetchone()
    if row is None:
        return "", 0, ""
    return str(row["b_name"] or ""), int(row["opinion"] or 0), str(row["basis"] or "")


def _faction_representative(db: GameDB, faction: str) -> str:
    """Pick one visible courtier who can embody an abstract faction pressure card."""

    faction = str(faction or "").strip()
    if not faction:
        return ""
    rows = _safe_fetchall(
        db,
        """
        SELECT name, office, office_type, ability, loyalty, grievance
        FROM characters
        WHERE faction=?
          AND status='active'
          AND power_id='ming'
          AND office_type!='后宫'
        ORDER BY
          CASE
            WHEN office_type IN ('内阁','司礼监','东厂') THEN 0
            WHEN office LIKE '%尚书%' OR office LIKE '%大学士%' THEN 1
            ELSE 2
          END,
          ability DESC,
          grievance DESC,
          loyalty ASC
        LIMIT 1
        """,
        (faction,),
    )
    return str(rows[0]["name"]) if rows else ""


def _safe_fetchall(db: GameDB, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
    try:
        return list(db.conn.execute(sql, params).fetchall())
    except sqlite3.Error:
        return []


def _json_dict(raw: object) -> Dict[str, object]:
    try:
        data = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _table_exists(db: GameDB, name: str) -> bool:
    row = db.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _has_column(db: GameDB, table: str, column: str) -> bool:
    if not _table_exists(db, table):
        return False
    try:
        return any(str(row["name"]) == column for row in db.conn.execute(f"PRAGMA table_info({table})"))
    except sqlite3.Error:
        return False
