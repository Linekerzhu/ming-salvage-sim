"""Player-facing strategic briefing cards.

This module does not create new simulation state.  It exposes already-running
CK3/ROTK-style systems as compact, actionable hooks for the home screen:
private agendas, rivalries, faction pressure, army autonomy, and known secrets.
The goal is to make the living-world layer legible without calling the LLM.
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState

BriefCard = Dict[str, object]

_TAB_AUDIENCE = "audience"
_TAB_REALM = "realm"
_TAB_DESK = "desk"

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


def briefing_payload(db: GameDB, state: Optional[GameState] = None, *, limit: int = 5) -> Dict[str, object]:
    """Return a stable API payload for the home-screen strategic briefing."""

    safe_limit = max(1, min(8, int(limit or 5)))
    return {
        "cards": briefing_cards(db, state, limit=safe_limit),
        "limit": safe_limit,
    }


def briefing_cards(db: GameDB, state: Optional[GameState] = None, *, limit: int = 5) -> List[BriefCard]:
    """Collect and rank actionable hooks from existing simulation tables."""

    cards: List[BriefCard] = []
    _pending_decision_cards(db, cards)
    _agenda_cards(db, cards)
    _rivalry_cards(db, cards)
    _army_cards(db, cards)
    _faction_cards(db, cards)
    _secret_cards(db, cards)
    cards.sort(
        key=lambda c: (
            -int(c.get("urgency") or 0),
            str(c.get("kind") or ""),
            str(c.get("title") or ""),
        )
    )
    return cards[: max(1, min(8, int(limit or 5)))]


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
) -> BriefCard:
    return {
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


def _pending_decision_cards(db: GameDB, cards: List[BriefCard]) -> None:
    try:
        from ming_sim.court_events import pending_payload

        decision = pending_payload(db)
    except Exception:
        decision = None
    if not decision:
        return
    cards.append(
        _card(
            kind="decision",
            title=f"请陛下裁断：{decision.get('title')}",
            detail=str(decision.get("narrative") or "")[:90],
            urgency=100,
            tone="danger",
            cta="去裁断",
            tab=_TAB_DESK,
            meta="待决",
            ref_kind="decision",
            ref_id=str(decision.get("id") or ""),
        )
    )


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
        progress = int(row["progress"] or 0)
        intensity = int(row["intensity"] or 50)
        label = _AGENDA_LABELS.get(kind, str(row["title"] or "私心将成"))
        prefix = "将成局" if progress >= 85 else "有苗头"
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
        maint = max(1, int(row["maintenance_per_turn"] or 1))
        arrears_months = int(row["arrears"] or 0) / maint
        supervisor = str(row["supervisor"] or "")
        hook = "已有监军钳制" if supervisor else "尚无近身制衡"
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
            )
        )


def _short_office(office: str) -> str:
    office = str(office or "").strip()
    if not office:
        return ""
    if len(office) <= 8:
        return office
    return office[:8]


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
