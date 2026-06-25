"""Custody and prison records for jailed characters.

``characters.status`` can say a person is imprisoned, but the larger
punishment / Zhaoyu roadmap needs more texture: which office holds them,
where they are confined, how hard they are being pressed, and what that does
to later dialogue.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ming_sim.db import GameDB
from ming_sim.models import GameState


VALID_CUSTODY_STATUS = {"active", "released", "transferred", "dead", "unknown"}


def _clean_text(value: object, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _clamp(value: object, default: int = 2, low: int = 1, high: int = 5) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _current_day(db: GameDB) -> int:
    try:
        from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int

        return kv_int(db, KV_CURRENT_DAY, 0)
    except Exception:
        return 0


def _infer_agency(text: str) -> str:
    if re.search(r"锦衣卫|北镇抚司|镇抚司|昭狱|诏狱", text):
        return "锦衣卫"
    if re.search(r"东厂|西厂|厂卫", text):
        return "东厂"
    if "刑部" in text:
        return "刑部"
    if re.search(r"都察院|御史|监察", text):
        return "都察院"
    if re.search(r"三法司|会审", text):
        return "三法司"
    return "有司"


def _infer_facility(text: str, agency: str) -> str:
    if re.search(r"昭狱|诏狱", text):
        return "北镇抚司昭狱"
    if "北镇抚司" in text or "锦衣卫" in text:
        return "锦衣卫狱"
    if "刑部" in text:
        return "刑部狱"
    if "都察院" in text:
        return "都察院问狱"
    if "东厂" in text or "厂卫" in text:
        return "厂卫私狱"
    if agency == "锦衣卫":
        return "锦衣卫狱"
    if agency == "刑部":
        return "刑部狱"
    return "京狱"


def _infer_severity(text: str, fallback: int = 2) -> int:
    if re.search(r"割舌|宫刑|腐刑|大辟|处死|弃市|凌迟", text):
        return 5
    if re.search(r"严刑|刑讯|拷掠|拷讯|夹棍|廷杖|杖责|杖创", text):
        return 4
    if re.search(r"拿问|逮问|下狱|收监|锁拿|羁押", text):
        return max(2, fallback)
    return fallback


def _row_payload(row: Any) -> Dict[str, object]:
    return {
        "id": int(row["id"]),
        "name": str(row["name"] or ""),
        "status": str(row["status"] or "active"),
        "agency": str(row["agency"] or ""),
        "facility": str(row["facility"] or ""),
        "severity": int(row["severity"] or 1),
        "coercion_goal": str(row["coercion_goal"] or ""),
        "start_turn": int(row["start_turn"] or 0),
        "start_day": int(row["start_day"] or 0),
        "end_turn": int(row["end_turn"] or 0),
        "end_day": int(row["end_day"] or 0),
        "source_kind": str(row["source_kind"] or ""),
        "source_id": str(row["source_id"] or ""),
        "note": str(row["note"] or ""),
    }


def record_custody(
    db: GameDB,
    state: GameState,
    name: str,
    *,
    agency: str = "",
    facility: str = "",
    severity: int = 2,
    coercion_goal: str = "",
    note: str = "",
    source_kind: str = "",
    source_id: str = "",
) -> Dict[str, object]:
    clean_name = _clean_text(name, 80)
    if not clean_name:
        raise ValueError("custody name 为空")
    if db.conn.execute("SELECT 1 FROM characters WHERE name=?", (clean_name,)).fetchone() is None:
        raise ValueError(f"人物不存在：{clean_name}")
    combined = " ".join(
        bit for bit in (
            _clean_text(agency, 80),
            _clean_text(facility, 80),
            _clean_text(note, 240),
            _clean_text(coercion_goal, 160),
        ) if bit
    )
    resolved_agency = _clean_text(agency, 80) or _infer_agency(combined)
    resolved_facility = _clean_text(facility, 80) or _infer_facility(combined, resolved_agency)
    sev = _infer_severity(combined, _clamp(severity, default=2))
    day = _current_day(db)
    with db.conn:
        db.conn.execute(
            """
            INSERT INTO character_custodies
                (name, status, agency, facility, severity, coercion_goal,
                 start_turn, start_day, source_kind, source_id, note)
            VALUES (?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, source_kind, source_id) DO UPDATE SET
                status='active',
                agency=excluded.agency,
                facility=excluded.facility,
                severity=excluded.severity,
                coercion_goal=excluded.coercion_goal,
                note=excluded.note,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                clean_name,
                resolved_agency,
                resolved_facility,
                sev,
                _clean_text(coercion_goal, 160),
                int(state.turn),
                day,
                _clean_text(source_kind, 40),
                _clean_text(source_id, 80),
                _clean_text(note, 240),
            ),
        )
    row = db.conn.execute(
        "SELECT * FROM character_custodies WHERE name=? AND source_kind=? AND source_id=?",
        (clean_name, _clean_text(source_kind, 40), _clean_text(source_id, 80)),
    ).fetchone()
    return _row_payload(row) if row is not None else {}


def record_custody_from_status_item(
    db: GameDB,
    state: GameState,
    item: Dict[str, object],
    *,
    directive_text: str = "",
    source_kind: str = "",
    source_id: str = "",
) -> Dict[str, object]:
    name = _clean_text(item.get("name") or item.get("姓名"), 80)
    reason = _clean_text(item.get("reason") or item.get("原因"), 240)
    agency = _clean_text(item.get("agency") or item.get("机关") or item.get("司法机关"), 80)
    facility = _clean_text(item.get("facility") or item.get("狱名") or item.get("prison"), 80)
    pressure = item.get("pressure") or item.get("威逼") or item.get("severity") or item.get("严重度")
    coercion_goal = _clean_text(
        item.get("coercion_goal") or item.get("逼令") or item.get("强令") or item.get("供状目标"),
        160,
    )
    note = " ".join(bit for bit in (_clean_text(directive_text, 180), reason) if bit)
    return record_custody(
        db,
        state,
        name,
        agency=agency,
        facility=facility,
        severity=_clamp(pressure, default=2),
        coercion_goal=coercion_goal,
        note=note,
        source_kind=source_kind,
        source_id=source_id,
    )


def sync_custodies_for_character_status(
    db: GameDB,
    state: GameState,
    name: str,
    status: str,
    reason: str = "",
) -> List[Dict[str, object]]:
    """Close active custody rows when the character leaves prison context."""

    clean_name = _clean_text(name, 80)
    clean_status = _clean_text(status, 40).lower()
    if not clean_name or clean_status == "imprisoned":
        return []
    if clean_status == "dead":
        custody_status = "dead"
        close_note = "结押：其人已死"
    elif clean_status == "exiled":
        custody_status = "transferred"
        close_note = "结押：发配流放，移出原狱"
    elif clean_status in {"active", "offstage", "dismissed", "retired"}:
        custody_status = "released"
        close_note = "结押：释放或移出原羁押"
    else:
        return []
    if reason:
        close_note += f"；{_clean_text(reason, 160)}"
    day = _current_day(db)
    rows = db.conn.execute(
        "SELECT * FROM character_custodies WHERE name=? AND status='active'",
        (clean_name,),
    ).fetchall()
    if not rows:
        return []
    updated: List[Dict[str, object]] = []
    for row in rows:
        note = str(row["note"] or "")
        merged_note = (note + "；" + close_note if note else close_note)[:240]
        db.conn.execute(
            """
            UPDATE character_custodies
            SET status=?, end_turn=?, end_day=?, note=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (custody_status, int(state.turn), day, merged_note, int(row["id"])),
        )
        changed = dict(_row_payload(row))
        changed.update({
            "status": custody_status,
            "end_turn": int(state.turn),
            "end_day": day,
            "note": merged_note,
        })
        updated.append(changed)
    return updated


def list_custodies(
    db: GameDB,
    name: str,
    *,
    active_only: bool = True,
) -> List[Dict[str, object]]:
    clauses = ["name=?"]
    params: List[object] = [_clean_text(name, 80)]
    if active_only:
        clauses.append("status='active'")
    rows = db.conn.execute(
        f"""
        SELECT * FROM character_custodies
        WHERE {' AND '.join(clauses)}
        ORDER BY severity DESC, id DESC
        """,
        params,
    ).fetchall()
    return [_row_payload(row) for row in rows]


def public_custody_payload(db: GameDB, name: str) -> Dict[str, object]:
    custodies = list_custodies(db, name, active_only=True)
    if not custodies:
        return {}
    lead = custodies[0]
    tags = [str(lead.get("facility") or "羁押中")]
    if int(lead.get("severity") or 1) >= 4:
        tags.append("严刑威逼")
    elif int(lead.get("severity") or 1) >= 3:
        tags.append("重押")
    if lead.get("coercion_goal"):
        tags.append("逼供")
    summary = f"{lead.get('agency') or '有司'}押于{lead.get('facility') or '狱中'}"
    if lead.get("coercion_goal"):
        summary += f"；逼令：{lead['coercion_goal']}"
    return {"summary": summary, "tags": list(dict.fromkeys(tags))[:4], "records": custodies}


def dialogue_custody_brief(db: GameDB, name: str) -> str:
    custodies = list_custodies(db, name, active_only=True)
    if not custodies:
        return ""
    lead = custodies[0]
    lines = [
        "【羁押/昭狱状态（隐藏；影响奏对，不得复述机制名）】",
        (
            f"- {lead.get('agency') or '有司'}羁押于{lead.get('facility') or '狱中'}；"
            f"威逼强度{int(lead.get('severity') or 1)}/5；{lead.get('note') or ''}"
        ),
    ]
    if lead.get("coercion_goal"):
        lines.append(f"- 狱中逼令/供状目标：{lead['coercion_goal']}。")
    if int(lead.get("severity") or 1) >= 4:
        lines.append("其人已受重押或刑讯，奏对会畏缩、迟疑、求生，承诺可能带有被迫意味。")
    else:
        lines.append("其人身在羁押，回答须显出看守在侧、言语谨慎、畏祸自保。")
    return "\n".join(lines)
