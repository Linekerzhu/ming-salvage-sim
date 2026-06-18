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
    "trap_remedy": 4,
    "army": 5,
    "faction": 6,
    "agenda": 7,
    "rivalry": 8,
    "hook": 9,
}

_KIND_LABELS = {
    "decision": "裁断",
    "trap": "御案",
    "directive_blocker": "诏旨",
    "directive_followup": "复命",
    "trap_remedy": "担责",
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


def _briefing_candidates(db: GameDB, state: Optional[GameState] = None) -> List[BriefCard]:
    """Collect all actionable hooks before the home-screen outliner chooses a subset."""

    cards: List[BriefCard] = []
    _pending_decision_cards(db, cards)
    _trap_cards(db, state, cards)
    _trap_remedy_cards(db, state, cards)
    _directive_blocker_cards(db, cards)
    _directive_followup_cards(db, state, cards)
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
