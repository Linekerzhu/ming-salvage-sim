"""Political blackboard summaries for player-facing audit trails."""

from __future__ import annotations

from typing import Dict, List

from ming_sim.db import GameDB
from ming_sim.models import GameState


STANCE_LABEL = {
    "support": "支持",
    "oppose": "反对",
    "caution": "附条件",
    "neutral": "未定",
}


def _signed(value: object) -> str:
    try:
        num = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)
    return f"+{num}" if num > 0 else str(num)


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _compact_drivers(evidence: object, limit: int = 4) -> List[str]:
    if not isinstance(evidence, dict):
        return []
    drivers = evidence.get("drivers")
    if not isinstance(drivers, list):
        return []
    output: List[str] = []
    for raw in drivers:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip()
        text = str(raw.get("text") or "").strip()
        if kind and text:
            output.append(f"{kind}：{text}")
        if len(output) >= limit:
            break
    return output


def build_turn_causal_notes(
    db: GameDB,
    state: GameState,
    decree_text: str,
    applied: Dict[str, object],
) -> List[Dict[str, object]]:
    """Create a bounded, deterministic receipt of why the turn resolved as it did.

    This is not a second simulation. It only summarizes already-recorded stances and
    applied extraction results, so it stays cheap and auditable.
    """
    notes: List[Dict[str, object]] = []

    for row in db.list_minister_stances(turn=state.turn, limit=80):
        stance = str(row.get("stance") or "neutral")
        if stance == "neutral" and int(row.get("confidence") or 0) < 4:
            continue
        risks = row.get("risk_tags_list") if isinstance(row.get("risk_tags_list"), list) else []
        notes.append({
            "kind": "stance",
            "tone": {"support": "good", "caution": "warn", "oppose": "bad"}.get(stance, "neutral"),
            "title": f"{row.get('minister_name')}：{STANCE_LABEL.get(stance, stance)}",
            "summary": str(row.get("summary") or "").strip(),
            "drivers": _compact_drivers(row.get("evidence")),
            "risks": [str(item) for item in risks[:6]],
            "execution_hint": str(row.get("execution_hint") or "").strip(),
        })
        if len(notes) >= 8:
            break

    metric_delta = applied.get("metric_delta")
    if isinstance(metric_delta, dict) and metric_delta:
        parts = []
        for key, value in metric_delta.items():
            num = _int_or_none(value)
            if num:
                parts.append(f"{key}{_signed(num)}")
        if parts:
            notes.append({
                "kind": "state",
                "tone": "neutral",
                "title": "国势变动",
                "summary": "、".join(parts),
            })

    faction_delta = applied.get("faction_delta")
    if isinstance(faction_delta, dict):
        for faction, raw in list(faction_delta.items())[:6]:
            if isinstance(raw, dict):
                parts = [f"{field}{_signed(delta)}" for field, delta in raw.items()]
            else:
                parts = [f"满意{_signed(raw)}"]
            notes.append({
                "kind": "faction",
                "tone": "warn",
                "title": f"{faction}波动",
                "summary": "、".join(parts),
            })

    political_reactions = applied.get("political_reactions")
    if isinstance(political_reactions, list):
        for item in political_reactions[:8]:
            if not isinstance(item, dict):
                continue
            notes.append({
                "kind": str(item.get("kind") or "political_reaction"),
                "tone": str(item.get("tone") or "warn"),
                "title": str(item.get("title") or "朝局反应"),
                "summary": str(item.get("summary") or "").strip(),
                "drivers": [str(driver) for driver in (item.get("drivers") or [])[:6]]
                if isinstance(item.get("drivers"), list) else [],
                "faction_delta": item.get("faction_delta") if isinstance(item.get("faction_delta"), dict) else {},
            })

    issue_summary = applied.get("issue_summary")
    if isinstance(issue_summary, dict):
        for item in issue_summary.get("advances", []) or []:
            if not isinstance(item, dict):
                continue
            notes.append({
                "kind": "issue",
                "tone": "good" if int(item.get("to_value") or 0) >= int(item.get("from_value") or 0) else "warn",
                "title": f"局势推进：{item.get('title') or '#'+str(item.get('issue_id'))}",
                "summary": str(item.get("narrative") or item.get("stage_text") or "").strip(),
            })
        for item in issue_summary.get("closes", []) or []:
            if not isinstance(item, dict):
                continue
            reason = str(item.get("reason") or "")
            notes.append({
                "kind": "issue",
                "tone": "good" if reason == "resolved" else "bad",
                "title": f"{'结案' if reason == 'resolved' else '失败'}：{item.get('title')}",
                "summary": str(item.get("narrative") or "").strip(),
            })
        for item in issue_summary.get("new", []) or []:
            if not isinstance(item, dict) or item.get("rejected"):
                continue
            notes.append({
                "kind": "issue",
                "tone": "warn",
                "title": f"新局势：{item.get('title')}",
                "summary": "诏书或候选情势已转为长期盘面，后续需持续承办。",
            })

    for key, title in [
        ("economy_moves", "钱粮落账"),
        ("office_changes", "官职任免"),
        ("character_status_changes", "人物状态"),
        ("secret_order_closes", "密令核议"),
    ]:
        values = applied.get(key)
        if not isinstance(values, list) or not values:
            continue
        notes.append({
            "kind": key,
            "tone": "neutral",
            "title": title,
            "summary": f"本类共 {len(values)} 笔，详见邸报详明。",
        })

    if not notes and decree_text.strip():
        notes.append({
            "kind": "decree",
            "tone": "neutral",
            "title": "本月诏书已核销",
            "summary": "未形成额外结构化成因；请以月末邸报为准。",
        })
    return notes[:20]
