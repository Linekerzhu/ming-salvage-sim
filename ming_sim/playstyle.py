"""Player-facing strategic briefing cards.

This module does not create new simulation state.  It exposes already-running
CK3/ROTK-style systems as compact, actionable hooks for the home screen:
private agendas, rivalries, faction pressure, army autonomy, and known secrets.
The goal is to make the living-world layer legible without calling the LLM.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from ming_sim.db import GameDB
from ming_sim.models import GameState

BriefCard = Dict[str, object]

_TAB_AUDIENCE = "audience"
_TAB_REALM = "realm"
_TAB_DESK = "desk"
_TAB_EDICTS = "edicts"
KV_DECISION_TESTIMONIES = "playstyle.decision_testimonies"

_KIND_PRIORITY = {
    "decision": 0,
    "trap": 1,
    "directive_blocker": 2,
    "directive_followup": 3,
    "monthly_followup": 4,
    "bargain": 5,
    "patronage": 6,
    "trap_remedy": 7,
    "petition": 8,
    "favor": 9,
    "relationship": 10,
    "legacy": 11,
    "army": 12,
    "faction": 13,
    "eunuch": 13,
    "agenda": 14,
    "rivalry": 15,
    "hook": 16,
}

_KIND_LABELS = {
    "decision": "裁断",
    "trap": "御案",
    "directive_blocker": "诏旨",
    "directive_followup": "复命",
    "monthly_followup": "候见",
    "bargain": "旧账",
    "patronage": "举主",
    "trap_remedy": "担责",
    "petition": "求援",
    "favor": "旧恩",
    "relationship": "人情",
    "legacy": "余波",
    "doctrine": "路线",
    "army": "军镇",
    "faction": "派系",
    "eunuch": "内廷",
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

_CARD_MOTIVES = {
    "decision": "请皇帝立判",
    "trap": "御案积压催办",
    "trap_remedy": "旧案求补救",
    "directive_blocker": "旨意被人掣肘",
    "directive_followup": "复命须追问",
    "monthly_followup": "旧约求闭环",
    "bargain": "御前旧账求了结",
    "patronage": "举荐要担保",
    "petition": "怨望求台阶",
    "favor": "旧恩来还账",
    "relationship": "人情求担保",
    "legacy": "旧政求善后",
    "army": "军镇来要价",
    "faction": "派系求名分",
    "eunuch": "权阉之势待权衡",
    "agenda": "私图可交易",
    "rivalry": "旧怨求边界",
    "hook": "把柄可试探",
}

_CARD_DEALS = {
    "decision": {
        "ask": "要皇帝立判",
        "exchange": "先交证据与担责人，再求裁断",
        "refusal": "拖久会让各方自行串联造势",
    },
    "trap": {
        "ask": "求御前尽快批红",
        "exchange": "先批急疏，暂缓低急请托",
        "refusal": "奏而不答会喂高避事风气",
    },
    "trap_remedy": {
        "ask": "求皇帝替其买单或起复",
        "exchange": "旧案复盘后领难差自证",
        "refusal": "继续观望会加重百官畏事",
    },
    "directive_blocker": {
        "ask": "求解释或松动旨意边界",
        "exchange": "当面交代掣肘来源并配合主办",
        "refusal": "暗阻会继续消磨旨意进度",
    },
    "directive_followup": {
        "ask": "求认可复命口径",
        "exchange": "交实绩、水分与下一步可验差使",
        "refusal": "赏罚不明会让成果难以续用",
    },
    "monthly_followup": {
        "ask": "求展限、资源或明旨护身",
        "exchange": "补证据、重定期限并写入履约账",
        "refusal": "旧约失信会转成怨望和推诿",
    },
    "bargain": {
        "ask": "求兑现、补证或重新给台阶",
        "exchange": "交证据、领难差、定期限后再清账",
        "refusal": "旧账悬而未决，会沉成怨望或拖延",
    },
    "patronage": {
        "ask": "求皇帝采纳举荐",
        "exchange": "举主连坐担保，新人领试差自证",
        "refusal": "门生故旧会转入派系人情账",
    },
    "petition": {
        "ask": "求体面台阶或御前护持",
        "exchange": "领可验难差，交证据、账目或把柄",
        "refusal": "记作被冷落，后续可能以公事泄私怨",
    },
    "favor": {
        "ask": "求把旧恩兑现成护持",
        "exchange": "用旧恩换难差、证据或效忠",
        "refusal": "旧恩冷却后会反向要赏或观望",
    },
    "relationship": {
        "ask": "求替故旧或同党留边界",
        "exchange": "连坐担保、共办差使并交避嫌账",
        "refusal": "人情链可能固成党援暗线",
    },
    "legacy": {
        "ask": "求旧政善后或暂缓追责",
        "exchange": "交账册、补缺口、定受益与受损者",
        "refusal": "民怨和钱粮缺口会继续滚动",
    },
    "army": {
        "ask": "求饷权、兵册或换将名分",
        "exchange": "交兵册、受监军、限期清欠饷",
        "refusal": "军中可能借欠饷和离心自保",
    },
    "faction": {
        "ask": "求名分、官缺或一件露脸差使",
        "exchange": "交人手、压弹章、限期办成急务",
        "refusal": "党争热度会转成逼宫式要价",
    },
    "agenda": {
        "ask": "求名分、台阶或差使",
        "exchange": "给期限、要证据、设担保后再任用",
        "refusal": "私图会转入结援、串供或报复",
    },
    "rivalry": {
        "ask": "求皇帝划清旧怨边界",
        "exchange": "先交证据，必要时与政敌共办",
        "refusal": "旧怨会转成弹劾、放话或暗中掣肘",
    },
    "hook": {
        "ask": "求暂不发作把柄",
        "exchange": "用把柄换效忠、难差或线索",
        "refusal": "逼急可能毁证或投靠他人",
    },
}


def _agenda_bargain_profile(kind: str, target: str = "") -> Dict[str, str]:
    """Readable stakes for a private agenda audience.

    The card should feel like a court bargain: what this person wants, what the
    emperor can demand, and what either answer costs.
    """

    target_text = str(target or "").strip()
    revenge_target = target_text or "政敌"
    profiles: Dict[str, Dict[str, str]] = {
        "climb": {
            "ask": "求名位或一件能露脸的大差",
            "exchange": "先领冷硬难差，功成再迁，并交避嫌账",
            "cost": "许之可得能臣，却会助长声望与党援",
            "refusal": "拒之可能转投派系，在外攒名望要价",
            "risk_label": "求名位",
            "cost_label": "声望坐大",
        },
        "enrich": {
            "ask": "求暂缓深查，保住肥缺与请托线",
            "exchange": "查账自证、吐出请托链，限期补亏空",
            "cost": "许之能换钱粮线索，却等于给贪名官员续命",
            "refusal": "逼急可能毁账串供，短期更难追赃",
            "risk_label": "查账自证",
            "cost_label": "肥缺续命",
        },
        "protect": {
            "ask": "求保门生故旧，替本党争官缺名分",
            "exchange": "举荐连坐担保，或命其与敌派共办一差",
            "cost": "许之能借派系办事，也会让党援更固",
            "refusal": "拒之会激出抱团怨气，暗中梗旨",
            "risk_label": "植党担保",
            "cost_label": "党援更固",
        },
        "entrench": {
            "ask": "求饷权、兵册和换将名分",
            "exchange": "交兵册、受监军、换亲信，限期清欠饷",
            "cost": "许之可暂稳军心，却让边镇更像私人根基",
            "refusal": "压得过急可能军中哗动，借欠饷自保",
            "risk_label": "军镇制衡",
            "cost_label": "兵权坐大",
        },
        "survive": {
            "ask": "求台阶，求旧案暂不深究",
            "exchange": "交同谋把柄，领一件见血新功",
            "cost": "许之能换效忠，但清议会疑皇帝护短",
            "refusal": "拒之可能破罐破摔，投靠敌派自保",
            "risk_label": "旧案洗白",
            "cost_label": "清议疑护短",
        },
        "revenge": {
            "ask": f"求准弹劾，借皇帝清算{revenge_target}",
            "exchange": f"先交证据，压住私怨，必要时与{revenge_target}共办差",
            "cost": "许之可借刀整肃，也会点燃朝争旧怨",
            "refusal": "拒之会转入清议放话，暗中逼宫",
            "risk_label": "借刀复仇",
            "cost_label": "朝争点火",
        },
    }
    return profiles.get(kind, {
        "ask": "求名分、求台阶或求差使",
        "exchange": "给期限、要证据、设担保，再看能否任用",
        "cost": "许之有短期收益，也会留下人情账",
        "refusal": "拒之可能转成怨望或暗中掣肘",
        "risk_label": "私图交易",
        "cost_label": "人情留账",
    })


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


def audience_summon_hints_payload(db: GameDB, state: Optional[GameState] = None) -> Dict[str, object]:
    """Return compact summon-sheet pressure hints and the best dialogue lead per NPC.

    The summon drawer is a player-facing routing surface, not a new simulation
    system.  It should reuse the same strategic-brief radar as the home screen
    so long-running policy scars, faction pressure, old promises, and active
    dilemmas point to the same people everywhere.
    """

    if not _table_exists(db, "characters"):
        return {"hints": {}}
    rows = db.conn.execute(
        """
        SELECT name, office_type, faction, power_id, status,
               ability, integrity, emp_trust, grievance
        FROM characters
        WHERE status = 'active'
        """
    ).fetchall()
    active_rows: Dict[str, sqlite3.Row] = {}
    hints: Dict[str, Dict[str, Any]] = {}

    def ensure(name: str) -> Dict[str, Any]:
        item = hints.setdefault(name, {"tags": [], "pressure_score": 0})
        if "tags" not in item or not isinstance(item.get("tags"), list):
            item["tags"] = []
        item["pressure_score"] = int(item.get("pressure_score") or 0)
        return item

    def add_tag(name: str, label: str, tone: str, pressure: int = 0, *, front: bool = False) -> None:
        clean = str(name or "").strip()
        text = _short_text(str(label or "").strip(), 8)
        if not clean or not text or clean not in active_rows:
            return
        item = ensure(clean)
        tags = item["tags"]
        if not isinstance(tags, list):
            tags = []
            item["tags"] = tags
        if any(str(tag.get("label") or "") == text for tag in tags if isinstance(tag, dict)):
            return
        tag = {"label": text, "tone": tone}
        if front:
            tags.insert(0, tag)
        else:
            tags.append(tag)
        item["pressure_score"] = int(item.get("pressure_score") or 0) + int(pressure or 0)

    def set_lead(name: str, card: BriefCard) -> None:
        clean = str(name or "").strip()
        if not clean or clean not in active_rows:
            return
        item = ensure(clean)
        current = item.get("lead")
        if isinstance(current, dict) and _brief_urgency(current) >= _brief_urgency(card):
            return
        item["lead"] = card

    for row in rows:
        name = str(row["name"] or "").strip()
        if not name:
            continue
        power_id = str(row["power_id"] or "ming").strip() or "ming"
        if power_id != "ming":
            continue
        if str(row["office_type"] or "").strip() == "后宫":
            continue
        active_rows[name] = row

    for card in _briefing_candidates(db, state):
        if str(card.get("tab") or "") != _TAB_AUDIENCE:
            continue
        actor = str(card.get("actor") or "").strip()
        if actor not in active_rows:
            continue
        kind = str(card.get("kind") or "").strip()
        tone_raw = str(card.get("tone") or "")
        tag_tone = "bad" if tone_raw == "danger" else "warn" if tone_raw == "warn" else "neutral"
        urgency = _brief_urgency(card)
        meta = str(card.get("meta") or "").strip()
        if kind == "legacy":
            label = f"余波{meta[:3]}" if meta else "余波"
            pressure = 24 + urgency // 4
        elif kind == "decision":
            label = "待裁"
            pressure = 32 + urgency // 4
        elif kind == "monthly_followup":
            label = meta or "候见"
            pressure = 18 + urgency // 5
        else:
            label = _KIND_LABELS.get(kind, "机变")
            pressure = 12 + urgency // 6
        add_tag(actor, label, tag_tone, pressure, front=True)
        set_lead(actor, card)

    try:
        from ming_sim import policies as doctrine_policies
        doctrine_route_states = doctrine_policies.doctrine_route_state_cache(db)
    except Exception:
        doctrine_policies = None
        doctrine_route_states = {}

    def row_int(row: sqlite3.Row, key: str, default: int) -> int:
        try:
            return int(row[key] if row[key] is not None else default)
        except Exception:
            return default

    for name, row in active_rows.items():
        grievance = row_int(row, "grievance", 20)
        trust = row_int(row, "emp_trust", 55)
        ability = row_int(row, "ability", 50)
        integrity = row_int(row, "integrity", 50)
        faction = str(row["faction"] or "").strip()

        if grievance >= 72:
            add_tag(name, f"怨{grievance}", "bad", 28)
        elif grievance >= 55:
            add_tag(name, f"怨{grievance}", "warn", 14)
        if trust <= 28:
            add_tag(name, f"信{trust}", "bad", 28)
        elif trust <= 42:
            add_tag(name, f"信{trust}", "warn", 14)
        elif trust >= 72:
            add_tag(name, f"信{trust}", "good", 0)
        if ability >= 78:
            add_tag(name, f"才{ability}", "good", 0)
        if integrity <= 32:
            add_tag(name, f"廉{integrity}", "bad", 10)
        elif integrity >= 78:
            add_tag(name, f"廉{integrity}", "good", 0)
        if faction and faction not in {"无", "中立", "皇党"}:
            add_tag(name, faction[:6], "warn", 4)

        try:
            ideals = doctrine_policies.character_policy_ideals(
                db,
                name,
                limit=1,
                context_row=row,
                route_states=doctrine_route_states,
            ) if doctrine_policies is not None else {}
        except Exception:
            ideals = {}
        supports = ideals.get("supports") if isinstance(ideals, dict) else []
        if isinstance(supports, list) and supports:
            route = supports[0] if isinstance(supports[0], dict) else {}
            route_name = str(route.get("name") or "").strip()
            route_id = str(route.get("id") or "").strip()
            score = float(route.get("score") or 0)
            status = str(route.get("status") or "latent")
            status_label = str(route.get("status_label") or "")
            if route_name and (status != "latent" or score >= 0.55):
                tone = "warn" if status == "contested" else "good" if status == "orthodox" else "neutral"
                pressure = 16 if status == "contested" else 8 if status == "orthodox" else 2
                add_tag(name, f"愿行{route_name}", tone, pressure, front=status != "latent")
                urgency = 46 if status == "contested" else 34 if status == "orthodox" else 22
                set_lead(name, {
                    "kind": "doctrine",
                    "title": f"路线问对：{name}与{route_name}",
                    "detail": (
                        f"{name}倾向「{route_name}」"
                        f"（{route.get('axis') or '国策路线'}，{status_label or '潜势'}）。"
                        "可问其如何推进、守护或规避反噬。"
                    ),
                    "urgency": urgency,
                    "tone": "warn" if status == "contested" else "info",
                    "cta": "召来问路线",
                    "tab": _TAB_AUDIENCE,
                    "actor": name,
                    "meta": f"{route_name}·{status_label}" if status_label else route_name,
                    "ref_kind": "doctrine",
                    "ref_id": route_id,
                    "motive": str((ideals.get("summary") if isinstance(ideals, dict) else "") or ""),
                })

    cleaned: Dict[str, Dict[str, Any]] = {}
    for name, item in hints.items():
        tags = [
            tag for tag in (item.get("tags") or [])
            if isinstance(tag, dict) and str(tag.get("label") or "").strip()
        ][:5]
        pressure = int(item.get("pressure_score") or 0)
        lead = item.get("lead")
        out: Dict[str, Any] = {}
        if tags:
            out["tags"] = tags
        if pressure:
            out["pressure_score"] = pressure
        if isinstance(lead, dict):
            out["lead"] = lead
        if out:
            cleaned[name] = out
    return {"hints": cleaned}


def doctrine_chat_context_brief(db: GameDB, minister_name: str, doctrine_id: object) -> str:
    """Trusted prompt context for a route-politics audience lead."""

    try:
        from ming_sim import policies
        payload = policies.doctrine_chat_context_payload(db, minister_name, doctrine_id)
    except Exception:
        return ""
    route = payload.get("route") if isinstance(payload, dict) else {}
    stance = payload.get("stance") if isinstance(payload, dict) else {}
    if not isinstance(route, dict) or not route:
        return ""
    route_name = str(route.get("name") or route.get("id") or doctrine_id)
    axis = str(route.get("axis") or "国策路线")
    return "\n".join([
        f"本次召对主题：国策路线「{route_name}」（{axis}）。",
        f"路线状态：{payload.get('status_text') or route.get('state_label') or '状态未明'}。",
        f"{minister_name}对此路线{payload.get('stance_label') or '立场可变'}，分值{stance.get('score')}; {payload.get('reason_text') or '理由未显'}。",
        "回答应围绕如何推进、阻挠、变通或承担该路线的政治代价，不要泛泛谈忠心。",
    ])


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
    ability = _clamp_int(row["ability"], 0, 100)
    integrity = _clamp_int(row["integrity"], 0, 100)
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
    lines.extend(_request_cost_profile(
        trust=trust,
        grievance=grievance,
        ability=ability,
        integrity=integrity,
        faction=faction,
        rival=rival,
    ))
    lines.extend([
        "- 对话玩法：先提出可谈方案，不要直接落库；只有皇帝明确答应、追问条件或命其换取差使后，才算进入奏对目的。",
        "- 可谈条件：明旨护持；自辩换难差；与政敌共办一事；若皇帝留中不应，则表现寒心与转入自保。",
        "- 口吻要求：按本人的身份、派系、性格和关系网说话；不要像全知旁白解释机制。",
    ])
    return "\n".join(lines)


def rivalry_chat_context_brief(
    db: GameDB,
    minister_name: str,
    *,
    target: str = "",
) -> str:
    """Trusted context for summoning a rival pair into mediation or co-work talk."""

    name = str(minister_name or "").strip()
    other = str(target or "").strip()
    if not name or not _table_exists(db, "characters") or not _table_exists(db, "relationships"):
        return ""
    minister = db.conn.execute(
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
    if minister is None:
        return ""
    if not other:
        other, _opinion, _basis = _worst_rival_of(db, name)
    if not other or other == name:
        return ""
    rival = db.conn.execute(
        """
        SELECT name, office, faction, ability, integrity, emp_trust, grievance
        FROM characters
        WHERE name=?
          AND status='active'
          AND power_id='ming'
          AND office_type!='后宫'
          AND name!='崇祯'
        """,
        (other,),
    ).fetchone()
    if rival is None:
        return ""
    rel = db.conn.execute(
        "SELECT opinion, basis FROM relationships WHERE a_name=? AND b_name=?",
        (name, other),
    ).fetchone()
    rev = db.conn.execute(
        "SELECT opinion, basis FROM relationships WHERE a_name=? AND b_name=?",
        (other, name),
    ).fetchone()
    if rel is None and rev is None:
        return ""
    opinion = _clamp_int(rel["opinion"] if rel is not None else rev["opinion"], -100, 100)
    basis = str((rel["basis"] if rel is not None else rev["basis"]) or "旧怨")
    reverse_opinion = _clamp_int(rev["opinion"] if rev is not None else opinion, -100, 100)
    reverse_basis = str((rev["basis"] if rev is not None else basis) or basis)
    if opinion > -30 and reverse_opinion > -30:
        return ""

    office = _short_office(str(minister["office"] or ""))
    rival_office = _short_office(str(rival["office"] or ""))
    faction = str(minister["faction"] or "").strip()
    rival_faction = str(rival["faction"] or "").strip()
    same_faction = bool(faction and rival_faction and faction == rival_faction and faction not in {"无", "中立"})
    cross_faction = bool(faction and rival_faction and faction != rival_faction and faction not in {"无", "中立"} and rival_faction not in {"无", "中立"})
    trust = _clamp_int(minister["emp_trust"], 0, 100)
    grievance = _clamp_int(minister["grievance"], 0, 100)
    ability = _clamp_int(minister["ability"], 0, 100)
    integrity = _clamp_int(minister["integrity"], 0, 100)

    pressure_bits = []
    if same_faction:
        pressure_bits.append(f"同属{faction}，内斗会伤一派可用之人")
    if cross_faction:
        pressure_bits.append(f"{faction}与{rival_faction}会把私怨读成党争")
    if grievance >= 60:
        pressure_bits.append(f"{name}怨望高，容易把公事说成旧账")
    if trust <= 40:
        pressure_bits.append(f"{name}信任低，可能把调停当作试探或圈套")
    if not pressure_bits:
        pressure_bits.append("旧怨虽深，仍可用差事和赏罚压住一段")

    if ability >= 70 and integrity >= 55:
        exchange = "令二人共办一件可验难差，按结果分别赏罚"
    elif integrity <= 42:
        exchange = "先令其交证据或账目，不许借调停索要空名分"
    elif same_faction:
        exchange = "让本派长者或举主作保，二人各退一步"
    else:
        exchange = "先各自退一件小事，再给短限复奏"

    lines = [
        "【本次召对事项：政敌怨隙/调停共办】",
        f"- {office}{name}被召来谈与{rival_office}{other}的怨隙；这不是普通问策，而是一次人情、党争和差事边界的谈判。",
        f"- 关系账：{name}视{other} {opinion}（{basis}）；{other}视{name} {reverse_opinion}（{reverse_basis}）。",
        f"- 入对者状态：御前信任 {trust}，怨望 {grievance}"
        + (f"，派系 {faction}" if faction and faction not in {"无", "中立"} else "")
        + "。",
        "- 压力来源：" + "；".join(pressure_bits) + "。",
        f"- 可谈交换：{exchange}；调停只能降温，不能把旧怨一笔勾销。",
        "- 对话玩法：NPC 可承认一部分旧怨、推责给对方、索取边界，或要求先看皇帝是否真给赏罚；不要表现成无私和解。",
        "- 落库边界：只有皇帝明确说合、命共办、设期限或确认双方条件，才进入调停/履约；首次试探不直接改变关系。",
        "- 口吻要求：按本人身份、派系、信任、怨望和性格说话；不要像全知旁白解释系统。",
    ]
    return "\n".join(lines)


def army_chat_context_brief(
    db: GameDB,
    minister_name: str,
    army_id: object = "",
) -> str:
    """Trusted context for summoning a commander about autonomy or arrears.

    Army cards are generated from deterministic simulation state; the LLM gets
    this rebuilt server-side so the commander can bargain over soldiers, pay,
    supervisors, and command autonomy without pretending to know the whole map.
    """

    name = str(minister_name or "").strip()
    if not name or not _table_exists(db, "armies") or not _table_exists(db, "characters"):
        return ""
    commander = _active_character_row(db, name)
    if commander is None:
        return ""

    has_autonomy = _has_column(db, "armies", "autonomy")
    has_supervisor = _has_column(db, "armies", "supervisor")
    autonomy_expr = "autonomy" if has_autonomy else "0"
    supervisor_expr = "supervisor" if has_supervisor else "''"
    ref_id = str(army_id or "").strip()
    params: tuple
    where = "owner_power='ming' AND commander=?"
    params = (name,)
    if ref_id:
        where += " AND id=?"
        params = (name, ref_id)
    rows = _safe_fetchall(
        db,
        f"""
        SELECT id, name, station, theater, commander, controller, troop_type, manpower,
               maintenance_per_turn, supply, morale, training, equipment, arrears, mobility,
               loyalty, status, {autonomy_expr} AS autonomy, {supervisor_expr} AS supervisor
        FROM armies
        WHERE {where}
        ORDER BY autonomy DESC, arrears DESC, loyalty ASC
        LIMIT 1
        """,
        params,
    )
    if not rows:
        return ""
    row = rows[0]

    army = str(row["name"] or "")
    station = str(row["station"] or "")
    theater = str(row["theater"] or "")
    troop_type = str(row["troop_type"] or "")
    status = str(row["status"] or "")
    controller = str(row["controller"] or "")
    supervisor = str(row["supervisor"] or "")
    manpower = _clamp_int(row["manpower"], 0, 500000)
    maint = max(1, int(row["maintenance_per_turn"] or 1))
    arrears = max(0, int(row["arrears"] or 0))
    arrears_months = arrears / maint
    autonomy = _clamp_int(row["autonomy"], 0, 100)
    loyalty = _clamp_int(row["loyalty"], 0, 100)
    morale = _clamp_int(row["morale"], 0, 100)
    supply = _clamp_int(row["supply"], 0, 100)
    training = _clamp_int(row["training"], 0, 100)
    equipment = _clamp_int(row["equipment"], 0, 100)
    mobility = _clamp_int(row["mobility"], 0, 100)

    office = _short_office(str(commander["office"] or ""))
    faction = str(commander["faction"] or "").strip()
    trust = _clamp_int(commander["emp_trust"], 0, 100)
    grievance = _clamp_int(commander["grievance"], 0, 100)
    ability = _clamp_int(commander["ability"], 0, 100)
    integrity = _clamp_int(commander["integrity"], 0, 100)

    pressure_bits = []
    if autonomy >= 70:
        pressure_bits.append("离心已近藩镇自专")
    elif autonomy >= 45:
        pressure_bits.append("主帅和亲兵已有自专苗头")
    if arrears_months >= 3:
        pressure_bits.append(f"欠饷约 {arrears_months:.1f} 月，兵心可被主帅拿来要价")
    if loyalty <= 45:
        pressure_bits.append(f"军心忠诚 {loyalty}，遇削权易生鼓噪")
    if morale <= 45:
        pressure_bits.append(f"士气 {morale}，若只压不抚会坏战力")
    if supervisor:
        pressure_bits.append(f"{supervisor}在镇监军，能牵制也会激起主帅怨气")
    else:
        pressure_bits.append("尚无监军耳目，朝廷少一只就近的眼睛")
    if trust <= 40:
        pressure_bits.append(f"{name}御前信任低，会把问军当成试探")
    if grievance >= 60:
        pressure_bits.append(f"{name}怨望高，容易把补饷谈成讨价还价")

    if autonomy >= 70 and arrears_months >= 3:
        exchange = "先核欠饷、分期补发一部，换主帅交兵册、限期整饬并接受监军或轮调亲兵"
    elif autonomy >= 70:
        exchange = "先问兵册、亲兵、关防和调动权，必要时以监军/换防削其自专"
    elif arrears_months >= 3:
        exchange = "先核实欠饷真数，许有限补发，换其约束军中鼓噪和交出虚冒名册"
    elif loyalty <= 45:
        exchange = "用短限差遣试其忠顺，赏罚和监军同时落下"
    else:
        exchange = "让主帅说明卡点，给短期限回奏，不急着一刀切"

    cost_bits = [
        "补饷会压国库和其他军镇公平",
        "遣监军会涨内廷插手军务，也可能触怒主帅",
        "骤然削权或换将可能激变",
        "只安抚不立规矩会继续养高军镇离心",
    ]
    if ability >= 70 and integrity >= 55:
        posture = "有才可用，适合用难差和可验账目拴住"
    elif integrity <= 42:
        posture = "可能借军情索饷索权，须先要账册证据"
    elif grievance >= 60:
        posture = "先诉委屈，再谈条件；需要皇帝给边界"
    else:
        posture = "可试探其底线，再决定补饷、监军或换防"

    lines = [
        "【本次召对事项：军镇离心/欠饷问对】",
        f"- {office}{name}被召来谈{army}军情；这不是泛泛问边事，而是一次兵权、钱粮和君臣信任的谈判。",
        f"- 军镇盘面：{army}驻{station or theater or '边镇'}，战区{theater or '未明'}，兵种{troop_type or '混编'}，兵力约{manpower}，状态「{status or '未明'}」。",
        f"- 军务数值：离心 {autonomy}，欠饷 {arrears_months:.1f} 月，军心忠诚 {loyalty}，士气 {morale}，补给 {supply}，训练 {training}，装备 {equipment}，机动 {mobility}。",
        f"- 统属线索：主帅 {name}"
        + (f"，控制权记为{controller}" if controller and controller != name else "")
        + (f"，监军 {supervisor}" if supervisor else "，暂无监军")
        + "。",
        f"- 入对者状态：御前信任 {trust}，怨望 {grievance}，才干 {ability}，操守 {integrity}"
        + (f"，派系 {faction}" if faction and faction not in {"无", "中立"} else "")
        + f"；谈判姿态：{posture}。",
        "- 压力来源：" + "；".join(pressure_bits) + "。",
        f"- 可谈交换：{exchange}。",
        "- 代价提醒：" + "；".join(cost_bits) + "。",
        "- 对话玩法：主帅可求饷、求名分、拒监军、交兵册、索期限、推责户部或同僚；也可借战功和兵心试探皇帝底线。",
        "- 落库边界：只有皇帝明确调饷、遣监军、换将、换防、限期交兵册或下旨整军，才进入旨意/履约；首次问话不直接改变军镇数值。",
        "- 口吻要求：按武臣或边帅身份说话，多讲兵、饷、关防和军心，不要像内阁大学士或全知旁白解释系统。",
    ]
    return "\n".join(lines)


def faction_chat_context_brief(
    db: GameDB,
    minister_name: str,
    *,
    faction: str = "",
) -> str:
    """Trusted context for audience with a representative of a heated faction."""

    name = str(minister_name or "").strip()
    requested = str(faction or "").strip()
    if not name or not _table_exists(db, "characters") or not _table_exists(db, "factions"):
        return ""
    minister = db.conn.execute(
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
    if minister is None:
        return ""
    own_faction = str(minister["faction"] or "").strip()
    fac = requested or own_faction
    if not fac or fac in {"无", "中立"}:
        return ""
    if own_faction and own_faction not in {"无", "中立"} and own_faction != fac:
        return ""

    has_heat = _has_column(db, "factions", "heat")
    heat_expr = "heat" if has_heat else "20"
    row = db.conn.execute(
        f"SELECT name, satisfaction, leverage, agenda, {heat_expr} AS heat FROM factions WHERE name=?",
        (fac,),
    ).fetchone()
    if row is None:
        return ""
    sat = _clamp_int(row["satisfaction"], 0, 100)
    lev = _clamp_int(row["leverage"], 0, 100)
    heat = _clamp_int(row["heat"], 0, 100)
    agenda = str(row["agenda"] or "求取任事空间")
    representative = _faction_representative(db, fac)
    office = _short_office(str(minister["office"] or ""))
    trust = _clamp_int(minister["emp_trust"], 0, 100)
    grievance = _clamp_int(minister["grievance"], 0, 100)
    ability = _clamp_int(minister["ability"], 0, 100)
    integrity = _clamp_int(minister["integrity"], 0, 100)

    member_rows = _safe_fetchall(
        db,
        """
        SELECT name, office, ability, grievance
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
          grievance DESC
        LIMIT 4
        """,
        (fac,),
    )
    members = [
        f"{_short_office(str(member['office'] or ''))}{str(member['name'] or '')}"
        for member in member_rows
        if str(member["name"] or "").strip()
    ]

    pressure_bits = []
    if lev >= 68:
        pressure_bits.append("势力已足以挟事要价")
    if sat <= 28:
        pressure_bits.append("满意低，容易借题串联")
    if heat >= 62:
        pressure_bits.append("党争热度高，一纸任免也会被读成信号")
    if trust <= 40:
        pressure_bits.append(f"{name}御前信任低，会先试探皇帝是否真给边界")
    if grievance >= 60:
        pressure_bits.append(f"{name}怨望高，可能把派系公议说成个人旧账")
    if not pressure_bits:
        pressure_bits.append("派势未必失控，但可借力也会长其筹码")

    if lev >= 70 and sat <= 35:
        bargain = "给一件难差和有限名分，换该派暂收锋芒；办坏则连坐问责"
    elif heat >= 62:
        bargain = "先让代表压住弹章和串联，再用短限差事检验诚意"
    elif sat <= 28:
        bargain = "给台阶不直接给实权，要求交出可验人手或证据"
    else:
        bargain = "借其办急务，但明说功成不等于派系坐大"

    cost_bits = []
    if lev >= 68:
        cost_bits.append("借力会让该派筹码更重")
    if heat >= 62:
        cost_bits.append("压错人会激起敌派或本派反噬")
    if integrity <= 42:
        cost_bits.append("代表本人可能把名分私用")
    if not cost_bits:
        cost_bits.append("皇帝须拿名分、差遣或查账权作抵押")

    lines = [
        "【本次召对事项：派系压力/借力安抚】",
        f"- {office}{name}被召来谈{fac}之势；这不是普通问政，而是皇帝同一派代表谈条件、压热度或借力任事。",
        f"- 派系盘面：满意 {sat}，势力 {lev}，党争热度 {heat}；所求：{agenda}。",
        f"- 入对者状态：御前信任 {trust}，怨望 {grievance}，能力 {ability}，清廉 {integrity}。",
        "- 压力来源：" + "；".join(pressure_bits) + "。",
        f"- 可谈交换：{bargain}；代价是{'；'.join(cost_bits)}。",
    ]
    if members:
        lines.append("- 派内可点名人物：" + "；".join(members) + "。")
    if representative and representative != name:
        lines.append(f"- 注意：{representative}更像{fac}台面代表，{name}入对时可能替本派说话，也可能借派势为自己要价。")
    lines.extend([
        "- 对话玩法：NPC 应提出派内要价、愿交出的筹码和不肯退让的底线；皇帝可安抚、借力、拆派或命其交证据。",
        "- 落库边界：只有皇帝明确命该派承办、设期限、给名分、查账或调停，才进入旨意/履约/调停；首次问话不直接改变派系数值。",
        "- 口吻要求：按本人身份、派系、信任、怨望和性格说话；不要像全知旁白解释系统。",
    ])
    return "\n".join(lines)


def _request_cost_profile(
    *,
    trust: int,
    grievance: int,
    ability: int,
    integrity: int,
    faction: str = "",
    rival: str = "",
) -> List[str]:
    """Turn relationship stats into concrete audience stakes for plea scenes."""

    clean_faction = str(faction or "").strip()
    if trust <= 35:
        ask = "保全边界"
        refusal = "转入自保，回话更谨慎，后续可能只给半真半假的消息"
    elif grievance >= 68:
        ask = "体面台阶"
        refusal = "记作被冷落，容易把公事拖成私怨"
    elif ability >= 72:
        ask = "难差换护持"
        refusal = "仍会办事，但会把功劳和风险算得很清"
    else:
        ask = "明旨护身"
        refusal = "退回观望，短期不敢替皇帝冒险"

    cost_bits = []
    if rival:
        cost_bits.append(f"{rival}及其同党会认为皇帝偏护")
    if clean_faction and clean_faction not in {"无", "中立"}:
        cost_bits.append(f"外朝会读成向{clean_faction}让步")
    if integrity <= 42:
        cost_bits.append("给名分或资源可能被其私用")
    elif integrity >= 76:
        cost_bits.append("若压得太狠，清议会说皇帝薄待敢言可用之臣")
    if not cost_bits:
        cost_bits.append("皇帝要拿名分、人情或钱粮作抵押")

    if ability >= 72 and integrity >= 55:
        exchange = "限期办一件可验难差，成则护持，败则自请处分"
    elif integrity <= 42:
        exchange = "先交账目、人证或把柄，再谈护持"
    elif grievance >= 68:
        exchange = "让他与政敌共办小事，以功过抵旧怨"
    else:
        exchange = "给短限复奏，拿事实换恩典"

    return [
        f"- 请托代价画像：他最可能先求「{ask}」；皇帝若答应，代价是{'；'.join(cost_bits)}。",
        f"- 可逼交换：{exchange}；若皇帝拒绝，他会{refusal}。",
    ]


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
        SELECT id, name, modifiers, narrative_hint, duration_months, start_month, legacy_key
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
    remaining = -1 if duration < 0 else duration
    if duration >= 0:
        try:
            remaining = db.legacy_remaining_months(row, db.load_state())
        except Exception:
            remaining = duration
    duration_text = "永久" if duration < 0 else f"仍余{remaining}月"
    title = str(row["name"] or "旧政余波")
    hint = str(row["narrative_hint"] or "")
    stem = str(policy.get("stem") or "")
    office = _short_office(str(minister["office"] or ""))
    stakeholders = _policy_legacy_stakeholders(db, row)
    fiscal_actor = str(stakeholders.get("fiscal_actor") or "")
    relief_actor = str(stakeholders.get("relief_actor") or "")
    beneficiary = str(stakeholders.get("beneficiary") or "国库与承办衙门")
    sufferer = str(stakeholders.get("sufferer") or "地方百姓与清议")
    if name == fiscal_actor:
        stance = "财政承压方，容易先护住钱粮缺口，再谈蠲缓"
    elif name == relief_actor:
        stance = "民怨善后方，容易先追问地方承受、浮收和清议反噬"
    else:
        stance = "被召来评估旧政两难，应按本职和派系选择立场"
    lines = [
        "【本次召对事项：长期政策余波】",
        f"- {office}{name}被召来讨论「{title}」：这是已经落库的长期后果，不是新政空谈。",
        f"- 余波事实：{hint or '旧政仍在拖动朝局。'}",
        f"- 持续性：{duration_text}" + (f"；当前修正：{effect_text}" if effect_text else "") + "。",
        f"- 两难结构：撑住/受益的是{beneficiary}；承压/反噬的是{sufferer}。",
        f"- 当前入对立场：{name}更像{stance}。",
    ]
    if fiscal_actor or relief_actor:
        lines.append(
            "- 可对质人选："
            + (f"财政承压方 {fiscal_actor}" if fiscal_actor else "财政承压方未明")
            + "；"
            + (f"民怨善后方 {relief_actor}" if relief_actor else "民怨善后方未明")
            + "。"
        )
    if stem:
        lines.append(
            f"- 政策焦点：{stem}已成税负/钱粮旧账；可谈增收、民怨、地方承受、士绅阻力和善后路径。"
        )
    lines.extend([
        "- NPC 应提出有代价的善后方案，例如分年蠲免、换税源、查中间侵吞、以新差事换旧政缓和；不要给无成本完美答案。",
        "- 奏对抓手：必须点明至少一类受益者/受损者（如户部、边军、地方胥吏、士绅、百姓）和一个可追问责任人；不要只谈抽象民心。",
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
    profile = _agenda_bargain_profile(kind, agenda_target)

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
        f"- 交易画像：他最可能求「{profile['ask']}」；皇帝可逼其「{profile['exchange']}」。",
        f"- 接受代价：{profile['cost']}",
        f"- 拒绝风险：{profile['refusal']}",
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


def bargain_chat_context_brief(
    db: GameDB,
    minister_name: str,
    memory_id: object = 0,
) -> str:
    """Trusted context for an NPC returning to settle a remembered court bargain."""

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
        params = (name, turn, turn, mid)
    else:
        params = (name, turn, turn)
    row = db.conn.execute(
        f"""
        SELECT m.id, m.title, m.cause, m.process, m.outcome, m.sentiment,
               m.importance, m.turn,
               c.office, c.faction, c.ability, c.integrity, c.emp_trust, c.grievance
        FROM event_memories m
        JOIN characters c ON c.name=m.subject_id
        WHERE m.subject_type='character'
          AND m.subject_id=?
          AND m.event_type='audience_bargain'
          AND m.turn<=?
          AND (m.expires_turn IS NULL OR m.expires_turn>=?)
          AND c.status='active'
          AND c.power_id='ming'
          AND c.office_type!='后宫'
          {where_id}
        ORDER BY m.turn DESC, m.importance DESC, m.id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if row is None:
        return ""

    office = _short_office(str(row["office"] or ""))
    faction = str(row["faction"] or "").strip()
    ability = _clamp_int(row["ability"], 0, 100)
    integrity = _clamp_int(row["integrity"], 0, 100)
    trust = _clamp_int(row["emp_trust"], 0, 100)
    grievance = _clamp_int(row["grievance"], 0, 100)
    sentiment = str(row["sentiment"] or "neutral").strip()
    title = str(row["title"] or f"御前旧账：{name}")
    cause = str(row["cause"] or "前番召对留下未清条件。")
    process = str(row["process"] or "")
    outcome = str(row["outcome"] or "此事尚未在御前清账。")
    if sentiment == "positive":
        posture = "他会先谢恩，但要试探天恩是否真能兑现；感恩不等于免费效忠。"
        bargain = "可谈兑现、领难差还恩、以证据或担保换正式差遣。"
    elif sentiment == "mixed":
        posture = "他认为陛下留下了条件，今日该带着证据、账册或担保来求确认。"
        bargain = "可谈补证、改期限、共办验真，或明示条件不足继续压着。"
    elif sentiment == "negative":
        posture = "他记得前番被拒，可能怨而不敢言；若不给边界，容易把私怨带进公事。"
        bargain = "可谈重新给台阶、派冷硬难差自证、交政敌线索，或当面划死边界。"
    else:
        posture = "他带着未清旧账入对，应先试探皇帝是否记得前话。"
        bargain = "可谈兑现、补证、展限、追责或另派共办。"

    lines = [
        "【本次召对事项：御前旧账】",
        f"- {office}{name}不是普通被动问策；他前番召对留下「{title}」，今日可主动求见清账。",
        f"- 旧账记录：事由「{cause}」" + (f"；御前话头「{process}」" if process else "") + f"；结果「{outcome}」。",
        f"- 当前人心：才干 {ability}，操守 {integrity}，御前信任 {trust}，怨望 {grievance}"
        + (f"，派系 {faction}" if faction and faction not in {"无", "中立"} else "")
        + "。",
        f"- 入对心态：{posture}",
        f"- 对话玩法：{bargain}",
        "- 两难要求：清旧账必须有代价，可能牵动党援、政敌、钱粮、期限或名节；不要给无成本完美答案。",
        "- 落库边界：只有皇帝明确兑现、继续索证、改期限、命其承办或正式拒绝，才再写入奏对交易记忆；普通寒暄不直接落库。",
        "- 口吻要求：按身份、派系、信任、怨望和人物性格说话；先说人话、旧话和怕处，不要像全知旁白解释系统。",
    ]
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
    obligation_states = [
        state for state in (item.get("obligation_states") or [])
        if isinstance(state, dict)
    ]
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
    if obligation_states:
        for state_item in obligation_states[:3]:
            title_text = str(state_item.get("title") or "未竟奏对")
            status_label = str(state_item.get("status_label") or state_item.get("status") or "未定")
            due_label = str(state_item.get("due_label") or "")
            score = _clamp_int(state_item.get("score"), 0, 100)
            threshold = _clamp_int(state_item.get("threshold"), 0, 100)
            pending = [str(x) for x in (state_item.get("pending_conditions") or []) if str(x).strip()]
            blockers = [str(x) for x in (state_item.get("blockers") or []) if str(x).strip()]
            pressure = str(state_item.get("pressure_label") or "").strip()
            pieces = [f"「{title_text}」", status_label]
            if threshold:
                pieces.append(f"心理/履约进度 {score}/{threshold}")
            if due_label:
                pieces.append(due_label)
            if pending:
                pieces.append("待证条件：" + "；".join(pending[:2]))
            if blockers:
                pieces.append("明面阻力：" + "；".join(blockers[:2]))
            if pressure:
                pieces.append(f"月度压力：{pressure}")
            lines.append("- 旧约状态：" + "；".join(pieces))
        lines.append("- 裁断玩法：皇帝可选择展限给资源、限期补证、公开追责或改派共办；NPC 必须承认旧约压力，不得把受阻/失期说成全无此事。")
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
    char = None
    if _table_exists(db, "characters"):
        char = db.conn.execute(
            """
            SELECT faction, ability, integrity, emp_trust, grievance
            FROM characters
            WHERE name=?
              AND status='active'
              AND power_id='ming'
              AND office_type!='后宫'
            """,
            (name,),
        ).fetchone()
    if char is not None:
        lines.extend(_request_cost_profile(
            trust=_clamp_int(char["emp_trust"], 0, 100),
            grievance=_clamp_int(char["grievance"], 0, 100),
            ability=_clamp_int(char["ability"], 0, 100),
            integrity=_clamp_int(char["integrity"], 0, 100),
            faction=str(char["faction"] or ""),
        ))
    lines.extend([
        "- 对话玩法：NPC 应先主动复命或诉难处，再请皇帝给名分、人手、银粮、期限或保全边界；不要等玩家逐条逼问。",
        "- 两难要求：提出的方案必须有代价，可能牵动政敌、同党、钱粮、旧约或密令风险；不要给无成本完美答案。",
        "- 落库边界：只有皇帝明确命其承办、设期限、要求拟旨或双方确认条件，才进入奏对目的/履约账本；普通请安不直接落库。",
        "- 口吻要求：按身份、派系、信任、怨望和人物性格说话；不要像全知旁白解释系统。",
    ])
    return "\n".join(lines)


def decision_chat_context_brief(
    db: GameDB,
    minister_name: str,
    decision_id: object = "",
    *,
    target: str = "",
) -> str:
    """Trusted context for summoning an actor before resolving a pending court decision."""

    name = str(minister_name or "").strip()
    if not name:
        return ""
    try:
        from ming_sim.court_events import get_pending, pending_payload

        pending = get_pending(db)
        payload = pending_payload(db)
    except Exception:
        return ""
    if not isinstance(pending, dict) or not isinstance(payload, dict):
        return ""
    payload_id = str(payload.get("id") or "").strip()
    expected_id = str(decision_id or "").strip()
    if expected_id and payload_id and expected_id != payload_id:
        return ""

    actor, extracted_target = _decision_actor_target(db, pending)
    explicit_target = str(target or "").strip()
    if explicit_target and _active_character_row(db, explicit_target) is None:
        explicit_target = ""
    related = {item for item in (actor, extracted_target, explicit_target) if item}
    if related and name not in related:
        return ""
    if not related and _active_character_row(db, name) is None:
        return ""

    other = ""
    for candidate in (explicit_target, extracted_target, actor):
        if candidate and candidate != name:
            other = candidate
            break
    role = "当事人" if name == actor else "牵涉人" if name in {extracted_target, explicit_target} else "可问询人物"
    self_row = _active_character_row(db, name)
    other_row = _active_character_row(db, other) if other else None
    office = _short_office(str(self_row["office"] or "")) if self_row is not None else ""
    faction = str(self_row["faction"] or "").strip() if self_row is not None else ""
    ability = _clamp_int(self_row["ability"], 0, 100) if self_row is not None else 0
    integrity = _clamp_int(self_row["integrity"], 0, 100) if self_row is not None else 0
    trust = _clamp_int(self_row["emp_trust"], 0, 100) if self_row is not None else 0
    grievance = _clamp_int(self_row["grievance"], 0, 100) if self_row is not None else 0
    other_office = _short_office(str(other_row["office"] or "")) if other_row is not None else ""
    title = str(payload.get("title") or "请陛下裁断").strip()
    narrative = _short_text(str(payload.get("narrative") or ""), 320)
    choices = [item for item in (payload.get("choices") or []) if isinstance(item, dict)]

    lines = [
        "【本次召对事项：裁断前问话】",
        f"- 御前已有待决事件「{title}」。皇帝此刻先召{name}问话，不等于已经裁断。",
        f"- 当前入对身份：{office}{name}是这桩裁断里的{role}。"
        + (f"牵涉对象：{other_office}{other}。" if other else ""),
        f"- 当前人心：才干 {ability}，操守 {integrity}，御前信任 {trust}，怨望 {grievance}"
        + (f"，派系 {faction}" if faction and faction not in {"无", "中立"} else "")
        + "。",
    ]
    if narrative:
        lines.append(f"- 案情表面叙事：{narrative}")
    if choices:
        lines.append("- 可见裁断路数：")
        for idx, choice in enumerate(choices[:5], start=1):
            label = str(choice.get("label") or choice.get("key") or "一策").strip()
            hint = str(choice.get("hint") or "").strip()
            effects = [
                str(eff.get("label") or "").strip()
                for eff in (choice.get("effects") or [])
                if isinstance(eff, dict) and str(eff.get("label") or "").strip()
            ]
            parts = [f"{idx}. {label}"]
            if hint:
                parts.append(hint)
            if effects:
                parts.append("影响：" + "、".join(effects[:4]))
            lines.append("  " + "；".join(parts))
    lines.extend([
        "- 对话玩法：NPC 应从自己的身份和利害出发，先陈述证据、怕处、反噬和可担责任；不要像旁白逐条解释选项。",
        "- 皇帝可以追问：谁得利、谁受损、证据是什么、几日能验、若裁错谁担责、政敌或同党会如何反扑。",
        "- 落库边界：本轮召对只是在裁断前听人；只有玩家去「御案/裁断」选择裁断选项，才真正结算该事件。",
        "- 口吻要求：按身份、派系、信任、怨望和人物性格说话；不得假装已经知道皇帝最终会选哪一策。",
    ])
    return "\n".join(lines)


def decision_testimonies_for_pending(db: GameDB) -> List[Dict[str, object]]:
    """Return testimony already gathered for the current pending decision."""

    try:
        from ming_sim.court_events import get_pending, pending_payload

        pending = get_pending(db)
        payload = pending_payload(db)
    except Exception:
        return []
    if not isinstance(pending, dict) or not isinstance(payload, dict):
        return []
    return decision_testimonies_for_case(db, pending, payload)


def decision_testimonies_for_case(
    db: GameDB,
    pending: Dict[str, object],
    payload: Dict[str, object],
) -> List[Dict[str, object]]:
    """Return testimony for a supplied pending-decision snapshot."""

    key = _decision_case_key(pending, payload)
    if not key:
        return []
    store = _load_decision_testimony_store(db)
    items = store.get(key)
    if not isinstance(items, list):
        return []
    cleaned: List[Dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("minister") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if not name or not summary:
            continue
        cleaned.append({
            "minister": name,
            "role": str(item.get("role") or "问询人").strip() or "问询人",
            "target": str(item.get("target") or "").strip(),
            "ask": str(item.get("ask") or "").strip(),
            "summary": summary,
            "stance": str(item.get("stance") or "陈情").strip() or "陈情",
            "turn": _clamp_int(item.get("turn"), 0, 1000000),
            "day": _clamp_int(item.get("day"), 0, 1000000),
        })
    return cleaned[-8:]


def record_decision_testimony(
    db: GameDB,
    state: GameState,
    minister_name: str,
    decision_id: object = "",
    user_text: str = "",
    answer: str = "",
    *,
    target: str = "",
    semantic_review: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Persist a summoned person's pre-decision testimony as case-file context."""

    if semantic_review is not None and (
        not isinstance(semantic_review, dict) or not semantic_review.get("allow")
    ):
        return {}
    name = str(minister_name or "").strip()
    if not name:
        return {}
    try:
        from ming_sim.court_events import get_pending, pending_payload

        pending = get_pending(db)
        payload = pending_payload(db)
    except Exception:
        return {}
    if not isinstance(pending, dict) or not isinstance(payload, dict):
        return {}
    payload_id = str(payload.get("id") or "").strip()
    expected_id = str(decision_id or "").strip()
    if expected_id and payload_id and expected_id != payload_id:
        return {}

    actor, extracted_target = _decision_actor_target(db, pending)
    explicit_target = str(target or "").strip()
    if explicit_target and _active_character_row(db, explicit_target) is None:
        explicit_target = ""
    related = {item for item in (actor, extracted_target, explicit_target) if item}
    if related and name not in related:
        return {}
    if not related and _active_character_row(db, name) is None:
        return {}

    key = _decision_case_key(pending, payload)
    if not key:
        return {}
    summary = _short_text(answer, 180)
    if not summary:
        return {}
    other = ""
    for candidate in (explicit_target, extracted_target, actor):
        if candidate and candidate != name:
            other = candidate
            break
    role = "当事人" if name == actor else "牵涉人" if name in {extracted_target, explicit_target} else "问询人"
    semantic_kind = ""
    if isinstance(semantic_review, dict):
        semantic_kind = str(semantic_review.get("kind") or "").strip()
    stance = _decision_testimony_stance_from_kind(semantic_kind) or _decision_testimony_stance(user_text, answer)
    try:
        day = int(pending.get("day") or db.kv_get("upgrade.current_day") or 0)
    except (TypeError, ValueError):
        day = 0
    item: Dict[str, object] = {
        "minister": name,
        "role": role,
        "target": other,
        "ask": _short_text(user_text, 90),
        "summary": summary,
        "stance": stance,
        "turn": int(getattr(state, "turn", 0) or 0),
        "day": day,
    }
    store = _load_decision_testimony_store(db)
    items = [x for x in store.get(key, []) if isinstance(x, dict)]
    items = [x for x in items if str(x.get("minister") or "") != name]
    items.append(item)
    store[key] = items[-8:]
    _prune_decision_testimony_store(store, keep_key=key)
    db.kv_set(KV_DECISION_TESTIMONIES, json.dumps(store, ensure_ascii=False, sort_keys=True))
    title = str(payload.get("title") or "裁断").strip()
    return {
        "title": "证词入案",
        "message": f"{name}的奏对已入「{_short_text(title, 18)}」案卷。",
        "effects": [
            {"kind": "decision_testimony", "label": f"{role}入案", "tone": "neutral"},
            {"kind": "stance", "label": stance, "tone": "neutral"},
        ],
    }


_DECISION_CASE_CTX_KEYS: Tuple[str, ...] = (
    "agreement_id",
    "goal_id",
    "source_id",
    "legacy_id",
    "order_id",
    "army_id",
    "a",
    "b",
    "petitioner",
    "rival",
    "actor",
    "target",
    "sponsor",
    "candidate",
    "minister",
    "victim",
    "accuser",
    "eunuch",
    "commander",
    "supervisor_cand",
    "deceased",
)


def _decision_case_key(pending: Dict[str, object], payload: Dict[str, object]) -> str:
    ctx = pending.get("ctx") if isinstance(pending.get("ctx"), dict) else {}
    parts = [
        str(pending.get("id") or payload.get("id") or "").strip(),
        str(pending.get("day") or "").strip(),
        str(pending.get("cooldown_key") or "").strip(),
    ]
    if isinstance(ctx, dict):
        for key in _DECISION_CASE_CTX_KEYS:
            value = ctx.get(key)
            if value in (None, "", []):
                continue
            if isinstance(value, (list, tuple)):
                text = ",".join(str(x).strip() for x in value if str(x).strip())
            elif isinstance(value, dict):
                text = json.dumps(value, ensure_ascii=False, sort_keys=True)
            else:
                text = str(value).strip()
            if text:
                parts.append(f"{key}={text}")
    return "|".join(part for part in parts if part)[:600]


def _load_decision_testimony_store(db: GameDB) -> Dict[str, List[Dict[str, object]]]:
    try:
        data = json.loads(db.kv_get(KV_DECISION_TESTIMONIES) or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    store: Dict[str, List[Dict[str, object]]] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, list):
            continue
        store[key] = [item for item in value if isinstance(item, dict)]
    return store


def _prune_decision_testimony_store(store: Dict[str, List[Dict[str, object]]], *, keep_key: str) -> None:
    if len(store) <= 12:
        return
    keys = sorted(
        store,
        key=lambda key: max((_clamp_int(item.get("turn"), 0, 1000000) for item in store.get(key, []) if isinstance(item, dict)), default=0),
    )
    for key in keys:
        if len(store) <= 12:
            break
        if key != keep_key:
            store.pop(key, None)


def _decision_testimony_stance(user_text: str, answer: str) -> str:
    text = f"{user_text}\n{answer}"
    checks = (
        ("证据", ("证", "账", "账册", "人证", "物证", "旧约", "实据", "查验", "查账")),
        ("担责", ("担责", "连坐", "担保", "愿担", "请罪", "领罪")),
        ("党争", ("反扑", "政敌", "同党", "清流", "阉党", "党争", "党羽")),
        ("求护", ("求", "保全", "护持", "台阶", "保住", "恳请")),
        ("自辩", ("不敢", "不是", "冤", "辩", "辩白", "开脱")),
    )
    for label, needles in checks:
        if any(needle in text for needle in needles):
            return label
    return "陈情"


def _decision_testimony_stance_from_kind(kind: str) -> str:
    return {
        "evidence": "证据",
        "liability": "担责",
        "faction": "党争",
        "protection": "求护",
        "defense": "自辩",
        "statement": "陈情",
    }.get(str(kind or "").strip(), "")


def relationship_chat_context_brief(
    db: GameDB,
    minister_name: str,
    *,
    target: str = "",
) -> str:
    """Trusted context for summoning someone about a concrete ally/rival tie."""

    name = str(minister_name or "").strip()
    other = str(target or "").strip()
    if not name or not other or name == other:
        return ""
    if not _table_exists(db, "relationships") or not _table_exists(db, "characters"):
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
    self_row = db.conn.execute(
        """
        SELECT name, office, faction, ability, integrity, emp_trust, grievance
        FROM characters
        WHERE name=? AND status='active' AND power_id='ming' AND office_type!='后宫' AND name!='崇祯'
        """,
        (name,),
    ).fetchone()
    other_row = db.conn.execute(
        """
        SELECT name, office, faction, emp_trust, grievance
        FROM characters
        WHERE name=? AND status='active' AND power_id='ming' AND office_type!='后宫'
        """,
        (other,),
    ).fetchone()
    if self_row is None or other_row is None:
        return ""

    opinion = _clamp_int(relation["opinion"], -100, 100)
    basis = str(relation["basis"] or "人情往来")
    office = _short_office(str(self_row["office"] or ""))
    other_office = _short_office(str(other_row["office"] or ""))
    faction = str(self_row["faction"] or "").strip()
    ability = _clamp_int(self_row["ability"], 0, 100)
    integrity = _clamp_int(self_row["integrity"], 0, 100)
    trust = _clamp_int(self_row["emp_trust"], 0, 100)
    grievance = _clamp_int(self_row["grievance"], 0, 100)
    positive = opinion >= 0
    label = "党援担保" if positive else "旧怨调停"
    relation_line = (
        f"- 关系焦点：{office}{name}对{other_office}{other}的好感为 {opinion}，旧因「{basis}」。"
        "这场召对要围绕这段关系问，不是普通问策。"
    )
    lines = [
        f"【本次召对事项：人情关系·{label}】",
        relation_line,
        f"- 当前人心：才干 {ability}，操守 {integrity}，御前信任 {trust}，怨望 {grievance}"
        + (f"，派系 {faction}" if faction and faction not in {"无", "中立"} else "")
        + "。",
    ]
    if positive:
        lines.extend([
            f"- 可谈玩法：皇帝可要求{name}替{other}担保、举荐、共办小差或交出避嫌条件；NPC 可以护短、讨价还价，也可把人情换成差使。",
            "- 两难要求：党援不是免费资源。答应担保会增强人情网，也会留下植党、连坐和政敌反扑的风险。",
            "- 口吻要求：不要把同党说成抽象资源，要以故旧、同乡、举主、门生、同道等具体人情说话。",
        ])
    else:
        lines.extend([
            f"- 可谈玩法：皇帝可追问{name}与{other}的旧怨，命其共办、各退一步，或借一方制衡另一方。",
            "- 两难要求：调停只能降温，不能凭一句话清零旧怨；偏袒任何一方都会改变信任、怨望和派系观感。",
            "- 口吻要求：NPC 应承认或辩解自己的私怨，同时提出边界、证据、差使或皇帝需承担的代价。",
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
    _bargain_cards(db, state, cards)
    _monthly_followup_cards(db, state, cards)
    _patronage_cards(db, cards)
    _favor_cards(db, state, cards)
    _petition_cards(db, cards)
    _relationship_cards(db, cards)
    _policy_legacy_cards(db, state, cards)
    _agenda_cards(db, cards)
    _rivalry_cards(db, cards)
    _army_cards(db, cards)
    _faction_cards(db, cards)
    _eunuch_cards(db, state, cards)
    _secret_cards(db, cards)
    resolved = _resolved_briefing_card_keys(db)
    if not resolved:
        return cards
    return [card for card in cards if str(card.get("card_key") or "").strip() not in resolved]


def _resolved_briefing_card_keys(db: GameDB) -> Set[str]:
    try:
        return set(db.resolved_briefing_card_keys())
    except Exception:
        return set()


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


def _card_stakes(kind: str) -> List[Dict[str, str]]:
    """Compact player-facing bargain cues for strategic cards.

    These chips are UI translation only.  The authoritative state and LLM
    context still come from the specific card data and chat-context builders.
    """

    profiles: Dict[str, List[Tuple[str, str, str]]] = {
        "decision": [
            ("gain", "可立威", "good"),
            ("cost", "多路后患", "bad"),
            ("ask", "先看代价", "neutral"),
        ],
        "trap": [
            ("gain", "清御案", "good"),
            ("cost", "臣下畏事", "bad"),
            ("ask", "先批急疏", "neutral"),
        ],
        "trap_remedy": [
            ("gain", "暖任事", "good"),
            ("cost", "替人买单", "bad"),
            ("ask", "问旧案", "neutral"),
        ],
        "directive_blocker": [
            ("gain", "旨意可通", "good"),
            ("cost", "暗阻未清", "bad"),
            ("ask", "问谁掣肘", "neutral"),
        ],
        "directive_followup": [
            ("gain", "成果续用", "good"),
            ("cost", "奏报有水", "bad"),
            ("ask", "论赏罚", "neutral"),
        ],
        "monthly_followup": [
            ("gain", "旧约可续", "good"),
            ("cost", "失信成怨", "bad"),
            ("ask", "重定期限", "neutral"),
        ],
        "bargain": [
            ("gain", "清旧账", "good"),
            ("cost", "再拖成怨", "bad"),
            ("ask", "证据期限", "neutral"),
        ],
        "patronage": [
            ("gain", "可得新人", "good"),
            ("cost", "举主坐大", "bad"),
            ("ask", "连坐担保", "neutral"),
        ],
        "petition": [
            ("gain", "护持换差", "good"),
            ("cost", "偏护生怨", "bad"),
            ("ask", "问代价", "neutral"),
        ],
        "favor": [
            ("gain", "旧恩驱动", "good"),
            ("cost", "反向要赏", "bad"),
            ("ask", "换难差", "neutral"),
        ],
        "relationship": [
            ("gain", "借人情成事", "good"),
            ("cost", "党援坐大", "bad"),
            ("ask", "连坐担保", "neutral"),
        ],
        "legacy": [
            ("gain", "缓民怨", "good"),
            ("cost", "钱粮缺口", "bad"),
            ("ask", "问谁补", "neutral"),
        ],
        "army": [
            ("gain", "稳兵心", "good"),
            ("cost", "主帅坐大", "bad"),
            ("ask", "兵册监军", "neutral"),
        ],
        "faction": [
            ("gain", "借派成事", "good"),
            ("cost", "党势坐大", "bad"),
            ("ask", "限期交账", "neutral"),
        ],
        "eunuch": [
            ("gain", "解壅塞·得耳目", "good"),
            ("cost", "纵之坐大", "bad"),
            ("ask", "倚阉或亲政", "neutral"),
        ],
        "agenda": [
            ("gain", "借私图办事", "good"),
            ("cost", "权势坐大", "bad"),
            ("ask", "逼其交账", "neutral"),
        ],
        "rivalry": [
            ("gain", "共办降温", "good"),
            ("cost", "旧怨不消", "bad"),
            ("ask", "问边界", "neutral"),
        ],
        "hook": [
            ("gain", "把柄可用", "good"),
            ("cost", "逼急毁证", "bad"),
            ("ask", "换效忠", "neutral"),
        ],
    }
    return [
        {"kind": stake_kind, "label": label, "tone": tone}
        for stake_kind, label, tone in profiles.get(kind, [])
    ]


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
    stakes: Optional[List[Dict[str, str]]] = None,
    deal: Optional[Dict[str, str]] = None,
) -> BriefCard:
    card_key = _brief_card_key(
        kind=kind,
        ref_kind=ref_kind,
        ref_id=ref_id,
        actor=actor,
        target=target,
        title=title,
    )
    card: BriefCard = {
        "card_key": card_key,
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
        "source_type": ref_kind or kind,
        "source_id": ref_id or f"{actor}:{target}".strip(":") or title,
    }
    if effects:
        card["effects"] = effects
    stake_items = stakes if stakes is not None else _card_stakes(kind)
    card.update(_card_contract(kind, stake_items, deal=deal))
    if stake_items:
        card["stakes"] = stake_items
    return card


def _brief_card_key(
    *,
    kind: str,
    ref_kind: str = "",
    ref_id: str = "",
    actor: str = "",
    target: str = "",
    title: str = "",
) -> str:
    raw = "|".join(
        str(item or "").strip()
        for item in (kind, ref_kind, ref_id, actor, target, title)
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]
    safe_kind = "".join(ch for ch in str(kind or "brief") if ch.isalnum() or ch in {"_", "-"})
    return f"{safe_kind or 'brief'}:{digest}"


def _eunuch_cards(db: GameDB, state: Optional[GameState], cards: List[BriefCard]) -> None:
    """权阉之势的中段张力卡：让「倚阉得用 ↔ 抑阉防坐大」成为可反复权衡的活选择，
    而非只在 ≥75 弹一次「阉祸危机」抉择。仅在中段 45–74 提醒（低则不足虑、≥75 走抉择）。"""
    try:
        from ming_sim.eunuch_power import (
            get_eunuch_power, is_daipihong_on, daipihong_keeper, keeper_disposition)
    except Exception:
        return
    try:
        power = int(get_eunuch_power(db))
    except Exception:
        return
    if power < 45 or power >= 75:
        return
    on = bool(is_daipihong_on(db))
    keeper = daipihong_keeper(db) if on else ""
    upright = (keeper_disposition(db, keeper) == "upright") if keeper else False
    if on and not upright:
        detail = (f"权阉之势已炽（{power}）。代批红假手{keeper}，奏壅虽解，然此辈藉势植党、"
                  "劾己之章入手即销。收回批红可抑其势，御案壅塞却复来——倚之得用、抑之失耳目，在陛下权衡。")
        tone, urgency = "warn", 56 + (power - 45)
    elif on and upright:
        detail = (f"权阉之势中平（{power}）。代批红托于忠谨之{keeper}，章奏据实呈览，暂无坐大之虞；"
                  "然权柄假于内臣终非长策，宜留意其势消长。")
        tone, urgency = "info", 46
    else:
        detail = (f"权阉之势渐起（{power}）。东厂厂卫可为耳目、令掌印代批红可解御案壅塞，然纵之则养虎自遗患；"
                  "亲裁则劳形而百官畏服。倚阉与亲政，权在陛下一念。")
        tone, urgency = "info", 50 + (power - 45) // 2
    cards.append(_card(
        kind="eunuch",
        title=f"权阉之势 {power}：倚之抑之",
        detail=detail,
        urgency=urgency,
        tone=tone,
        cta="赴御案权衡代批红",
        tab="desk",
        meta=f"权阉{power}",
        ref_kind="eunuch_power", ref_id="",
        deal={"ask": "求陛下定倚阉之策",
              "exchange": "倚之则代批红解壅塞、厂卫供耳目；抑之则亲裁防坐大",
              "refusal": "纵之坐大终成阉祸，骤抑则失内助、御案复壅"},
    ))


def _card_contract(kind: str, stakes: List[Dict[str, str]], deal: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """One-line player contract: why this hook asks for attention and what it costs."""

    motive = _CARD_MOTIVES.get(kind, "机变待问")
    gain = ""
    cost = ""
    for item in stakes:
        item_kind = str(item.get("kind") or "")
        label = str(item.get("label") or "").strip()
        if item_kind == "gain" and label and not gain:
            gain = label
        elif item_kind == "cost" and label and not cost:
            cost = label
    out = {"motive": motive}
    if gain:
        out["gain"] = gain
    if cost:
        out["cost"] = cost
    deal_profile = dict(_CARD_DEALS.get(kind, {}))
    if deal:
        deal_profile.update({k: v for k, v in deal.items() if str(v or "").strip()})
    for key in ("ask", "exchange", "refusal"):
        value = str(deal_profile.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def _decision_active_name(db: GameDB, ctx: object, keys: Tuple[str, ...]) -> str:
    if not isinstance(ctx, dict):
        return ""
    for key in keys:
        raw = ctx.get(key)
        candidates: List[object]
        if isinstance(raw, list):
            candidates = raw
        else:
            candidates = [raw]
        for item in candidates:
            if isinstance(item, dict):
                value = item.get("name") or item.get("minister") or item.get("actor")
            else:
                value = item
            row = _active_character_row(db, str(value or ""))
            if row is not None:
                return str(row["name"])
    return ""


def _decision_actor_target(db: GameDB, pending: object) -> Tuple[str, str]:
    if not isinstance(pending, dict):
        return "", ""
    ctx = pending.get("ctx") or {}
    pairs: Tuple[Tuple[Tuple[str, ...], Tuple[str, ...]], ...] = (
        (("petitioner",), ("rival",)),
        (("minister",), ("target", "rivals", "allies")),
        (("actor",), ("target",)),
        (("sponsor",), ("candidate",)),
        (("a",), ("b",)),
        (("eunuch",), ("target",)),
        (("victim",), ("accuser",)),
        (("commander",), ("supervisor_cand",)),
        (("target",), ("eunuch",)),
        (("candidates",), ("deceased",)),
    )
    for actor_keys, target_keys in pairs:
        actor = _decision_active_name(db, ctx, actor_keys)
        if not actor:
            continue
        target = _decision_active_name(db, ctx, target_keys)
        if target == actor:
            target = ""
        return actor, target
    return "", ""


def _pending_decision_cards(db: GameDB, cards: List[BriefCard]) -> None:
    try:
        from ming_sim.court_events import get_pending, pending_payload

        decision = pending_payload(db)
        pending = get_pending(db)
    except Exception:
        decision = None
        pending = None
    if not decision:
        return
    choices = decision.get("choices") or []
    actor, target = _decision_actor_target(db, pending)
    narrative = str(decision.get("narrative") or "")
    people = "；".join(item for item in (
        f"当事：{actor}" if actor else "",
        f"牵涉：{target}" if target else "",
    ) if item)
    effects = _decision_stakes(choices)
    if target:
        effects.insert(0, {"kind": "target", "label": f"牵涉：{target}", "tone": "neutral"})
    if actor:
        effects.insert(0, {"kind": "actor", "label": f"当事：{actor}", "tone": "neutral"})
    cards.append(
        _card(
            kind="decision",
            title=f"请陛下裁断：{decision.get('title')}",
            detail=(f"{people}。{narrative}" if people else narrative)[:110],
            urgency=100,
            tone="danger",
            cta="去裁断",
            tab=_TAB_DESK,
            actor=actor,
            target=target,
            meta=f"{len(choices)}路待决" if choices else "待决",
            ref_kind="decision",
            ref_id=str(decision.get("id") or ""),
            effects=effects,
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

    def goal_state_from_row(row: sqlite3.Row) -> Dict[str, object]:
        status = str(row["status"] or "")
        status_label = {
            "active": "仍在推进",
            "waiting_conditions": "待条件闭环",
            "blocked": "受阻待裁",
            "expired": "已经失期",
        }.get(status, status or "未定")
        try:
            conditions_raw = json.loads(str(row["conditions_json"] or "[]"))
        except (TypeError, ValueError):
            conditions_raw = []
        pending_conditions: List[str] = []
        if isinstance(conditions_raw, list):
            for condition in conditions_raw:
                if not isinstance(condition, dict):
                    continue
                if str(condition.get("status") or "pending") == "done":
                    continue
                desc = str(condition.get("description") or "").strip()
                if desc:
                    pending_conditions.append(desc[:80])
        try:
            blockers_raw = json.loads(str(row["blockers_json"] or "[]"))
        except (TypeError, ValueError):
            blockers_raw = []
        blockers = [str(item).strip()[:80] for item in blockers_raw if str(item).strip()] if isinstance(blockers_raw, list) else []
        try:
            last_delta = json.loads(str(row["last_delta_json"] or "{}"))
        except (TypeError, ValueError):
            last_delta = {}
        pressure = last_delta.get("monthly_pressure") if isinstance(last_delta, dict) and isinstance(last_delta.get("monthly_pressure"), dict) else {}
        try:
            expires_turn = int(row["expires_turn"] or 0)
        except (TypeError, ValueError):
            expires_turn = 0
        current_turn = int(getattr(state, "turn", 0) or 0)
        if expires_turn <= 0:
            due_label = "未设明限"
        elif expires_turn <= current_turn:
            due_label = f"已到第{expires_turn}月限"
        else:
            due_label = f"距第{expires_turn}月限尚{expires_turn - current_turn}月"
        return {
            "title": str(row["title"] or row["target_text"] or "未竟奏对")[:80],
            "status": status,
            "status_label": status_label,
            "condition_status": str(row["condition_status"] or ""),
            "score": _clamp_int(row["score"], 0, 100),
            "threshold": _clamp_int(row["threshold"], 0, 100),
            "expires_turn": expires_turn,
            "due_label": due_label,
            "pending_conditions": pending_conditions[:3],
            "blockers": blockers[:3],
            "pressure_label": str(pressure.get("label") or "").strip()[:60] if isinstance(pressure, dict) else "",
        }

    def add(
        name: str,
        reason: str,
        hook: str,
        priority: int,
        risks: Optional[List[str]] = None,
        obligation_state: Optional[Dict[str, object]] = None,
    ) -> None:
        name = str(name or "").strip()
        if not active(name):
            return
        item = bucket.setdefault(name, {
            "minister_name": name,
            "priority": 0,
            "reason_types": [],
            "memory_hooks": [],
            "risk_tags": [],
            "obligation_states": [],
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
        if obligation_state:
            states = item["obligation_states"] if isinstance(item.get("obligation_states"), list) else []
            states.append(obligation_state)

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
            SELECT minister_name, status, title, target_text, score, threshold,
                   condition_status, conditions_json, blockers_json, expires_turn, last_delta_json
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
            try:
                last_delta = json.loads(str(row["last_delta_json"] or "{}"))
            except (TypeError, ValueError):
                last_delta = {}
            court_decision = last_delta.get("court_decision") if isinstance(last_delta, dict) and isinstance(last_delta.get("court_decision"), dict) else {}
            semantic_blob = "\n".join([
                title,
                str(row["target_text"] or ""),
                str(row["conditions_json"] or ""),
                json.dumps(last_delta, ensure_ascii=False) if isinstance(last_delta, dict) else "",
            ])
            resource_followup = (
                str(court_decision.get("action") or "") == "resource"
                or "support_tasks" in (last_delta if isinstance(last_delta, dict) else {})
                or any(token in semantic_blob for token in ("资源复办", "新拨人手", "已用资源", "拨给人手文书"))
            )
            reason = "resource_support_followup" if resource_followup else f"conversation_goal:{status}"
            hook = (
                f"资源复办「{title}」仍需交账：须说明新拨人手、文书或银粮用在何处，哪些阻力仍未解。"
                if resource_followup else
                f"未完奏对「{title}」仍需复命或请旨。"
            )
            risks = ["资源复办", "国库小耗", "再误重责"] if resource_followup else ["旧约未了"]
            if resource_followup:
                priority += 6
            add(
                name,
                reason,
                hook,
                priority,
                risks,
                obligation_state=goal_state_from_row(row),
            )

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
        resource_followup = any("resource_support" in reason for reason in reasons)
        row = {
            **item,
            "priority": int(item.get("priority") or 0),
            "title": hooks[0] if hooks else "本月可主动请安回奏。",
            "summary": "；".join(hooks[:3]),
            "suggested_opening": (
                "请安时先交代新拨资源如何使用、已成何事、尚有何人掣肘；不要只谢恩。"
                if resource_followup else
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
            and str(card.get("kind") or "") in {"trap_remedy", "directive_followup", "bargain"}
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
        obligation_states = [
            state for state in (item.get("obligation_states") or [])
            if isinstance(state, dict)
        ]
        if not reasons and not hooks:
            continue
        office = _short_office(str(char["office"] or ""))
        trust = _clamp_int(char["emp_trust"], 0, 100)
        grievance = _clamp_int(char["grievance"], 0, 100)
        priority = _clamp_int(item.get("priority"), 0, 120)
        old_statuses = {str(state.get("status") or "") for state in obligation_states}
        expired_old = "expired" in old_statuses
        blocked_old = "blocked" in old_statuses
        waiting_old = "waiting_conditions" in old_statuses
        due = (
            any("due" in reason or "expired" in reason or "blocked" in reason for reason in reasons)
            or expired_old
            or blocked_old
        )
        secret = any("secret_order" in reason for reason in reasons)
        title = str(item.get("title") or (hooks[0] if hooks else "本月可主动请安回奏。")).strip()
        summary = str(item.get("summary") or "").strip()
        semantic_basis = " ".join([*reasons, title, summary, *hooks])
        patronage = any("patronage" in reason for reason in reasons) or any(
            token in semantic_basis for token in ("举主", "举荐", "荐人", "保新人")
        )
        co_work = any("co_work" in reason for reason in reasons) or any(
            token in semantic_basis for token in ("共办", "同办", "试差")
        )
        policy_audit = any("policy_audit" in reason for reason in reasons) or any(
            token in semantic_basis for token in ("旧政", "清查", "浮收", "侵吞")
        )
        resource_support = any("resource_support" in reason for reason in reasons) or any(
            token in semantic_basis for token in ("资源复办", "得助复办", "新拨人手", "新拨资源")
        )
        agreement = bool(obligation_states) or any("agreement" in reason or "conversation_goal" in reason for reason in reasons)
        speech = any("stance" in reason or "speech" in reason for reason in reasons)
        urgency = min(98, 58 + priority + (8 if due else 0) + (4 if secret else 0))
        tone = "danger" if due and (secret or agreement) else "warn" if due or risks else "info"
        meta_bits = []
        if due:
            meta_bits.append("到期")
        if expired_old:
            meta_bits.append("失期")
        elif blocked_old:
            meta_bits.append("受阻")
        elif waiting_old:
            meta_bits.append("待条件")
        if agreement:
            meta_bits.append("旧约")
        if secret:
            meta_bits.append("密令")
        if patronage:
            meta_bits.append("举主")
        if co_work:
            meta_bits.append("共办")
        if policy_audit:
            meta_bits.append("旧政")
        if resource_support:
            meta_bits.append("资源")
        if speech:
            meta_bits.append("口径")
        meta = "/".join(meta_bits[:4]) or _monthly_reason_label(reasons[0] if reasons else "请安")
        primary_label = _monthly_reason_label(reasons[0] if reasons else "请安")
        if patronage:
            primary_label = "举主担保"
        elif co_work:
            primary_label = "共办回奏"
        elif resource_support:
            primary_label = "资源复办"
        elif policy_audit:
            primary_label = "旧政清查"
        effects = [
            {"kind": "monthly_followup", "label": primary_label, "tone": "bad" if due else "neutral"},
            {"kind": "trust", "label": f"信任 {trust}", "tone": "bad" if trust <= 36 else "neutral"},
            {"kind": "grievance", "label": f"怨望 {grievance}", "tone": "bad" if grievance >= 58 else "neutral"},
        ]
        if expired_old:
            effects.append({"kind": "obligation_status", "label": "旧约失期", "tone": "bad"})
        elif blocked_old:
            effects.append({"kind": "obligation_status", "label": "旧约受阻", "tone": "bad"})
        elif waiting_old:
            effects.append({"kind": "obligation_status", "label": "待证闭环", "tone": "warn"})
        if obligation_states:
            first_state = obligation_states[0]
            score = _clamp_int(first_state.get("score"), 0, 100)
            threshold = _clamp_int(first_state.get("threshold"), 0, 100)
            if threshold:
                effects.append({
                    "kind": "obligation_progress",
                    "label": f"进度 {score}/{threshold}",
                    "tone": "bad" if score < threshold else "neutral",
                })
            pending = [str(x) for x in (first_state.get("pending_conditions") or []) if str(x).strip()]
            blockers = [str(x) for x in (first_state.get("blockers") or []) if str(x).strip()]
            if pending:
                effects.append({"kind": "obligation_condition", "label": _short_text(f"待证：{pending[0]}", 18), "tone": "warn"})
            elif blockers:
                effects.append({"kind": "obligation_blocker", "label": _short_text(f"阻力：{blockers[0]}", 18), "tone": "bad"})
        if secret:
            effects.append({"kind": "secret_order", "label": "密令回奏", "tone": "bad" if due else "neutral"})
        if patronage:
            effects.append({"kind": "patronage", "label": "举主担保", "tone": "warn"})
        if co_work:
            effects.append({"kind": "co_work", "label": "共办回奏", "tone": "warn"})
        if resource_support:
            effects.append({"kind": "resource_support", "label": "资源复办", "tone": "warn"})
        if policy_audit and not resource_support:
            effects.append({"kind": "policy_audit", "label": "旧政清查", "tone": "warn"})
        if agreement and not (patronage or co_work or policy_audit or resource_support):
            effects.append({"kind": "agreement", "label": "旧约待复", "tone": "bad" if due else "neutral"})
        if speech:
            effects.append({"kind": "speech", "label": "延续口径", "tone": "neutral"})
        if risks:
            effects.append({"kind": "risk", "label": risks[0], "tone": "bad" if due else "neutral"})
        deduped_effects: List[Dict[str, object]] = []
        seen_effect_labels: Set[str] = set()
        for effect in effects:
            label = str(effect.get("label") or "").strip()
            if label and label in seen_effect_labels:
                continue
            if label:
                seen_effect_labels.add(label)
            deduped_effects.append(effect)
        detail = (
            f"{office}{name}本月有事候见。{summary or title}"
            f"{'；'.join(hooks[:2]) if hooks else ''}"
        )
        if obligation_states:
            state_item = obligation_states[0]
            status_label = str(state_item.get("status_label") or "").strip()
            due_label = str(state_item.get("due_label") or "").strip()
            pending = [str(x) for x in (state_item.get("pending_conditions") or []) if str(x).strip()]
            blockers = [str(x) for x in (state_item.get("blockers") or []) if str(x).strip()]
            state_bits = [bit for bit in (status_label, due_label) if bit]
            if pending:
                state_bits.append(f"待证：{pending[0]}")
            if blockers:
                state_bits.append(f"阻力：{blockers[0]}")
            if state_bits:
                detail += "旧约状态：" + "；".join(state_bits) + "。"
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
                effects=deduped_effects[:6],
                stakes=_monthly_followup_stakes(
                    expired=expired_old,
                    blocked=blocked_old,
                    waiting=waiting_old,
                    secret=secret,
                    patronage=patronage,
                    co_work=co_work,
                    policy_audit=policy_audit,
                    resource_support=resource_support,
                    due=due,
                    agreement=agreement,
                ),
            )
        )
        count += 1
        if count >= 2:
            break


def _monthly_followup_stakes(
    *,
    expired: bool = False,
    blocked: bool = False,
    waiting: bool = False,
    secret: bool = False,
    patronage: bool = False,
    co_work: bool = False,
    policy_audit: bool = False,
    resource_support: bool = False,
    due: bool = False,
    agreement: bool = False,
) -> List[Dict[str, str]]:
    """Make old-promise audience hooks read as different bargains, not one status bucket."""

    if expired:
        profile = [
            ("gain", "最后展限", "good"),
            ("cost", "问罪伤怨", "bad"),
            ("ask", "谁担责", "neutral"),
        ]
    elif blocked:
        profile = [
            ("gain", "裁断阻力", "good"),
            ("cost", "皇帝背书", "bad"),
            ("ask", "补证据", "neutral"),
        ]
    elif secret:
        profile = [
            ("gain", "密令续查", "good"),
            ("cost", "惊动线索", "bad"),
            ("ask", "补人证", "neutral"),
        ]
    elif patronage:
        profile = [
            ("gain", "验新人", "good"),
            ("cost", "举主坐大", "bad"),
            ("ask", "连坐担保", "neutral"),
        ]
    elif co_work:
        profile = [
            ("gain", "压私怨", "good"),
            ("cost", "共办翻脸", "bad"),
            ("ask", "分工画押", "neutral"),
        ]
    elif resource_support:
        profile = [
            ("gain", "给资源", "good"),
            ("cost", "国库小耗", "bad"),
            ("ask", "再误重责", "neutral"),
        ]
    elif policy_audit:
        profile = [
            ("gain", "查中间人", "good"),
            ("cost", "钱粮缺口", "bad"),
            ("ask", "账册限期", "neutral"),
        ]
    elif waiting:
        profile = [
            ("gain", "闭环旧约", "good"),
            ("cost", "空口拖延", "bad"),
            ("ask", "核条件", "neutral"),
        ]
    elif due or agreement:
        profile = [
            ("gain", "旧约可续", "good"),
            ("cost", "失信成怨", "bad"),
            ("ask", "重定期限", "neutral"),
        ]
    else:
        profile = [
            ("gain", "主动复命", "good"),
            ("cost", "听后要断", "bad"),
            ("ask", "问下一手", "neutral"),
        ]
    return [{"kind": kind, "label": label, "tone": tone} for kind, label, tone in profile]


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


def _bargain_deal(sentiment: str) -> Dict[str, str]:
    """Conversation terms for a remembered audience bargain."""

    if sentiment == "positive":
        return {
            "ask": "求兑现御前许诺或领差还恩",
            "exchange": "以证据、效忠或一件难差清账",
            "refusal": "旧恩冷却，转为观望或反向求赏",
        }
    if sentiment == "mixed":
        return {
            "ask": "求确认条件是否足够",
            "exchange": "补交账册、人证、担保或期限",
            "refusal": "条件悬空，继续拖延成怨",
        }
    if sentiment == "negative":
        return {
            "ask": "求重新给台阶或说明拒请边界",
            "exchange": "领可验难差或交政敌线索",
            "refusal": "怨望加深，可能借公事泄私怨",
        }
    return {}


def _bargain_cards(db: GameDB, state: Optional[GameState], cards: List[BriefCard]) -> None:
    """Surface remembered audience bargains as proactive follow-up summons."""

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
        SELECT m.id, m.subject_id, m.title, m.cause, m.process, m.outcome,
               m.sentiment, m.importance, m.turn, m.tags,
               c.office, c.faction, c.ability, c.integrity, c.emp_trust, c.grievance
        FROM event_memories m
        JOIN characters c ON c.name=m.subject_id
        WHERE m.subject_type='character'
          AND m.event_type='audience_bargain'
          AND m.turn<=?
          AND (m.expires_turn IS NULL OR m.expires_turn>=?)
          AND c.status='active'
          AND c.power_id='ming'
          AND c.office_type!='后宫'
          AND c.name!='崇祯'
        ORDER BY m.turn DESC, m.importance DESC, m.id DESC
        LIMIT 24
        """,
        (current_turn, current_turn),
    )
    seen: Set[str] = set()
    count = 0
    for row in rows:
        name = str(row["subject_id"] or "").strip()
        if not name or name in seen:
            continue
        if any(
            str(card.get("actor") or "") == name
            and str(card.get("kind") or "") in {
                "decision", "trap_remedy", "directive_blocker",
                "directive_followup",
            }
            for card in cards
        ):
            continue
        seen.add(name)
        office = _short_office(str(row["office"] or ""))
        trust = _clamp_int(row["emp_trust"], 0, 100)
        grievance = _clamp_int(row["grievance"], 0, 100)
        ability = _clamp_int(row["ability"], 0, 100)
        integrity = _clamp_int(row["integrity"], 0, 100)
        importance = _clamp_int(row["importance"], 1, 5)
        sentiment = str(row["sentiment"] or "neutral").strip()
        title = str(row["title"] or "御前旧账").strip()
        cause = _short_text(str(row["cause"] or ""), 48)
        process = _short_text(str(row["process"] or ""), 48)
        outcome = _short_text(str(row["outcome"] or ""), 58)
        faction = str(row["faction"] or "").strip()

        if sentiment == "positive":
            card_title = f"旧账求兑现：{name}"
            lead = "前番御前已经给过口风，今日最怕空许不兑现。"
            urgency = 66 + importance * 5 + max(0, trust - 55) // 5 + max(0, grievance - 45) // 4
            tone = "warn" if grievance >= 55 else "info"
            meta = "旧账/许诺"
        elif sentiment == "mixed":
            card_title = f"条件待证：{name}"
            lead = "前番不是准也不是驳，而是留了条件；如今该问证据是否凑齐。"
            urgency = 74 + importance * 5 + max(0, grievance - 40) // 3 + max(0, 55 - trust) // 4
            tone = "warn"
            meta = "旧账/索证"
        elif sentiment == "negative":
            card_title = f"拒请余波：{name}"
            lead = "前番御前拒了他的所求，这笔怨气若不设边界，会转入公事拖延。"
            urgency = 78 + importance * 4 + max(0, grievance - 40) // 2 + max(0, 55 - trust) // 3
            tone = "danger" if grievance >= 68 or trust <= 35 else "warn"
            meta = "旧账/拒请"
        else:
            card_title = f"御前旧账：{name}"
            lead = "前番奏对留下未清条件，今日可召来重定边界。"
            urgency = 68 + importance * 5 + max(0, grievance - 45) // 4
            tone = "warn"
            meta = "旧账"
        urgency = max(60, min(96, urgency))
        detail_bits = [f"{office}{name}上次奏对留下「{title}」。", lead]
        if cause:
            detail_bits.append(f"事由：{cause}。")
        if process:
            detail_bits.append(f"御前话头：{process}。")
        if outcome:
            detail_bits.append(f"旧账结果：{outcome}。")
        detail_bits.append("这次应让他先说明要兑现、补证还是领责，别让旧账只沉在聊天记录里。")

        effects = [
            {"kind": "memory", "label": meta, "tone": "neutral"},
            {"kind": "trust", "label": f"信任 {trust}", "tone": "bad" if trust <= 38 else "good" if trust >= 62 else "neutral"},
            {"kind": "grievance", "label": f"怨望 {grievance}", "tone": "bad" if grievance >= 55 else "neutral"},
            {"kind": "ability", "label": f"才{ability}/廉{integrity}", "tone": "neutral"},
        ]
        if faction and faction not in {"无", "中立"}:
            effects.append({"kind": "faction", "label": faction, "tone": "neutral"})

        cards.append(
            _card(
                kind="bargain",
                title=card_title,
                detail="".join(detail_bits),
                urgency=urgency,
                tone=tone,
                cta="召来清账",
                tab=_TAB_AUDIENCE,
                actor=name,
                meta=meta,
                ref_kind="memory",
                ref_id=str(row["id"] or ""),
                effects=effects,
                deal=_bargain_deal(sentiment),
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
            and str(card.get("kind") or "") in {"trap_remedy", "directive_followup", "monthly_followup", "bargain", "favor"}
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
            and str(card.get("kind") or "") in {"trap_remedy", "directive_followup", "bargain"}
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


def _relationship_cards(db: GameDB, cards: List[BriefCard]) -> None:
    """Surface strong positive ties as playable guarantor / patronage bargains."""

    if not _table_exists(db, "relationships") or not _table_exists(db, "characters"):
        return
    rows = _safe_fetchall(
        db,
        """
        SELECT r.a_name, r.b_name, r.opinion, r.basis,
               rr.opinion AS reverse_opinion, rr.basis AS reverse_basis,
               ca.office AS a_office, ca.faction AS a_faction, ca.emp_trust AS a_trust,
               ca.grievance AS a_grievance,
               cb.office AS b_office, cb.faction AS b_faction, cb.emp_trust AS b_trust,
               cb.grievance AS b_grievance
        FROM relationships r
        JOIN characters ca ON ca.name=r.a_name
        JOIN characters cb ON cb.name=r.b_name
        LEFT JOIN relationships rr ON rr.a_name=r.b_name AND rr.b_name=r.a_name
        WHERE r.opinion>=55
          AND ca.status='active'
          AND cb.status='active'
          AND ca.power_id='ming'
          AND cb.power_id='ming'
          AND ca.office_type!='后宫'
          AND cb.office_type!='后宫'
          AND ca.name!='崇祯'
          AND cb.name!='崇祯'
        ORDER BY r.opinion DESC, COALESCE(rr.opinion, 0) DESC, ca.ability DESC
        LIMIT 24
        """,
    )
    seen: Set[tuple[str, str]] = set()
    count = 0
    for row in rows:
        actor = str(row["a_name"] or "").strip()
        target = str(row["b_name"] or "").strip()
        if not actor or not target or actor == target:
            continue
        key = tuple(sorted((actor, target)))
        if key in seen:
            continue
        seen.add(key)
        basis = str(row["basis"] or "人情往来")
        reverse_basis = str(row["reverse_basis"] or "")
        if any(token in f"{basis} {reverse_basis}" for token in ("举荐", "荐取", "挑补", "入京", "举主")):
            continue
        if any(
            str(card.get("actor") or "") in {actor, target}
            and str(card.get("kind") or "") in {
                "trap_remedy", "directive_followup", "monthly_followup", "patronage",
                "petition", "favor", "legacy", "agenda",
            }
            for card in cards
        ):
            continue
        opinion = _clamp_int(row["opinion"], -100, 100)
        reverse_opinion = _clamp_int(row["reverse_opinion"], -100, 100)
        actor_office = _short_office(str(row["a_office"] or ""))
        target_office = _short_office(str(row["b_office"] or ""))
        actor_faction = str(row["a_faction"] or "")
        target_faction = str(row["b_faction"] or "")
        same_faction = bool(
            actor_faction
            and target_faction
            and actor_faction == target_faction
            and actor_faction not in {"无", "中立"}
        )
        trust = _clamp_int(row["a_trust"], 0, 100)
        grievance = _clamp_int(row["a_grievance"], 0, 100)
        effects = [
            {"kind": "relationship", "label": f"关系 {opinion}", "tone": "good"},
            {"kind": "basis", "label": basis[:18], "tone": "neutral"},
            {"kind": "guarantee", "label": "可担保/可植党", "tone": "warn"},
            {"kind": "trust", "label": f"信任 {trust}", "tone": "bad" if trust <= 36 else "neutral"},
            {"kind": "grievance", "label": f"怨望 {grievance}", "tone": "bad" if grievance >= 58 else "neutral"},
        ]
        if reverse_opinion:
            effects.append({"kind": "reverse_opinion", "label": f"回敬 {reverse_opinion}", "tone": "good" if reverse_opinion >= 40 else "neutral"})
        if same_faction:
            effects.append({"kind": "faction", "label": f"同派 {actor_faction}", "tone": "warn"})
        detail = (
            f"{actor_office}{actor}与{target_office}{target}关系深厚（{basis}，好感 {opinion}）。"
            "可召来逼其担保、共办或交出避嫌条件；借得动人情，也会喂大党援。"
        )
        cards.append(
            _card(
                kind="relationship",
                title=f"人情担保：{actor}护{target}",
                detail=detail,
                urgency=64 + min(18, opinion // 5) + (6 if same_faction else 0) + max(0, grievance - 55) // 5,
                tone="warn" if same_faction or grievance >= 58 else "info",
                cta="召来问担保",
                tab=_TAB_AUDIENCE,
                actor=actor,
                target=target,
                meta="同派党援" if same_faction else "人情链",
                ref_kind="relationship",
                ref_id=f"{actor}:{target}",
                effects=effects[:6],
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
        stakeholders = _policy_legacy_stakeholders(db, row)
        actor = str(stakeholders.get("fiscal_actor") or "")
        target = str(stakeholders.get("relief_actor") or "")
        beneficiary = str(stakeholders.get("beneficiary") or "")
        sufferer = str(stakeholders.get("sufferer") or "")
        if beneficiary:
            effects.append({"kind": "beneficiary", "label": f"受益 {beneficiary}", "tone": "good"})
        if sufferer:
            effects.append({"kind": "sufferer", "label": f"承压 {sufferer}", "tone": "bad"})
        if actor and any(
            str(card.get("actor") or "") == actor
            and str(card.get("kind") or "") in {"trap_remedy", "directive_followup", "monthly_followup", "petition", "favor"}
            for card in cards
        ):
            continue
        if target == actor:
            target = ""
        detail = hint or "此项旧政仍在拖动朝局。钱粮、民心与地方承受力不会因办结而立刻归零。"
        if actor and target:
            detail += f" 可召{actor}谈钱粮缺口，也可召{target}追问民怨反噬。"
        elif actor:
            detail += f" 可召{actor}问清受益者、受损者和善后代价。"
        cards.append(
            _card(
                kind="legacy",
                title=f"政策余波：{name}",
                detail=detail,
                urgency=68 + min(24, abs(minxin) * 2) + (8 if duration < 0 else 0),
                tone="danger" if duration < 0 or minxin <= -10 else "warn",
                cta="召人问余波" if actor else "看天下",
                tab=_TAB_AUDIENCE if actor else _TAB_REALM,
                actor=actor,
                target=target,
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


def _policy_legacy_stakeholders(db: GameDB, row) -> Dict[str, str]:
    policy = _legacy_policy_payload(row)
    if not policy["is_policy"]:
        return {}
    stem = str(policy.get("stem") or "")
    fiscal_actor = _policy_legacy_actor(db, row)
    relief_actor = _policy_legacy_relief_actor(db, exclude=fiscal_actor)
    beneficiary, sufferer = _policy_legacy_side_labels(stem)
    return {
        "fiscal_actor": fiscal_actor,
        "relief_actor": relief_actor,
        "beneficiary": beneficiary,
        "sufferer": sufferer,
    }


def _policy_legacy_relief_actor(db: GameDB, *, exclude: str = "") -> str:
    if not _table_exists(db, "characters"):
        return ""
    row = db.conn.execute(
        """
        SELECT name
        FROM characters
        WHERE status='active'
          AND power_id='ming'
          AND office_type!='后宫'
          AND name!='崇祯'
          AND name!=?
        ORDER BY
          CASE
            WHEN office LIKE '%都察院%' OR office_type LIKE '%都察院%' THEN 0
            WHEN office LIKE '%御史%' OR office LIKE '%给事%' THEN 1
            WHEN faction='东林' THEN 2
            WHEN office_type IN ('地方','翰林院') THEN 3
            ELSE 4
          END,
          integrity DESC,
          grievance DESC,
          ability DESC
        LIMIT 1
        """,
        (str(exclude or ""),),
    ).fetchone()
    return str(row["name"] or "") if row is not None else ""


def _policy_legacy_side_labels(stem: str) -> Tuple[str, str]:
    text = str(stem or "")
    if text == "辽饷":
        return "边军/户部", "地方百姓/士绅"
    if text == "商税":
        return "国库/军需", "商贾/城市"
    if text == "盐税":
        return "国库/盐法衙门", "灶户/盐商"
    if text == "矿税":
        return "内库/矿监", "矿区百姓/地方官"
    if text == "田赋":
        return "国库/州县粮道", "粮户/地方士绅"
    if text:
        return "国库/承办衙门", "地方百姓/清议"
    return "受益衙门", "承压人群"


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
        agenda_target = str(row["target_name"] or "")
        profile = _agenda_bargain_profile(kind, agenda_target)
        prefix = "将成局" if progress >= 85 else "有苗头"
        risky = kind in {"enrich", "protect", "entrench", "revenge"}
        effects = [
            {"kind": "agenda_progress", "label": f"进度 {progress}%", "tone": "bad" if progress >= 85 else "neutral"},
            {"kind": "agenda_intensity", "label": f"强度 {intensity}", "tone": "bad" if intensity >= 75 else "neutral"},
            {"kind": "agenda_kind", "label": label, "tone": "bad" if risky else "neutral"},
            {"kind": "agenda_bargain", "label": profile["risk_label"], "tone": "warn" if risky else "neutral"},
            {"kind": "agenda_cost", "label": profile["cost_label"], "tone": "bad" if progress >= 85 else "warn"},
        ]
        if faction and faction not in {"无", "中立"}:
            effects.append({"kind": "faction", "label": faction, "tone": "neutral"})
        cards.append(
            _card(
                kind="agenda",
                title=f"{name}{prefix}：{label}",
                detail=(
                    f"{office}{name}近来动作渐密。{_AGENDA_HINTS.get(kind, '可召来探口风，再决定拉拢、压制或借力。')}"
                    f" 他多半要「{profile['ask']}」；可逼其「{profile['exchange']}」。"
                    f" 若拒绝，{profile['refusal']}。"
                ),
                urgency=progress + intensity // 6,
                tone="warn" if progress < 85 else "danger",
                cta="召来问对",
                tab=_TAB_AUDIENCE,
                actor=name,
                target=agenda_target,
                meta=f"{progress}%/{profile['risk_label']}",
                ref_kind="character",
                ref_id=name,
                effects=effects,
                deal={
                    "ask": profile["ask"],
                    "exchange": profile["exchange"],
                    "refusal": profile["refusal"],
                },
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
        commander_active = _active_character_row(db, commander) is not None
        actor = commander if commander_active else ""
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
                cta="召将问军" if commander_active else "看天下",
                tab=_TAB_AUDIENCE if commander_active else _TAB_REALM,
                actor=actor,
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
                cta="召代表问势" if representative else "看御案",
                tab=_TAB_AUDIENCE if representative else _TAB_DESK,
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
    if "patronage_followup" in text:
        return "举主担保"
    if "co_work_followup" in text:
        return "共办回奏"
    if "policy_audit_followup" in text:
        return "旧政清查"
    if "resource_support_followup" in text:
        return "资源复办"
    if "secret_evidence_followup" in text:
        return "补证密令"
    if "favor_service_followup" in text:
        return "偿恩差使"
    if "bargain_followup" in text:
        return "旧账复命"
    if "petition_service_followup" in text:
        return "难差自证"
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


def _active_character_row(db: GameDB, name: str) -> Optional[sqlite3.Row]:
    clean = str(name or "").strip()
    if not clean or not _table_exists(db, "characters"):
        return None
    try:
        return db.conn.execute(
            """
            SELECT name, office, office_type, faction, ability, integrity, emp_trust, grievance
            FROM characters
            WHERE name=?
              AND status='active'
              AND power_id='ming'
              AND office_type!='后宫'
              AND name!='崇祯'
            """,
            (clean,),
        ).fetchone()
    except sqlite3.Error:
        return None


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
