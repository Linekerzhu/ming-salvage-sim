"""Rule-layer policy consequences that should not depend on LLM extraction.

The edict outcome worker can produce rich JSON, but some gameplay contracts are
important enough to have deterministic fallbacks.  A decree that clearly turns
extra taxation into a standing policy should leave a persistent fiscal and
popular-burden trace even when the LLM returns no structured delta.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState
from ming_sim.paths import bundled_path

_TAX_STEMS = ("辽饷", "商税", "盐税", "矿税", "田赋")
_TAX_INCREASE_RE = re.compile(r"加派|加征|增征|增税|加税|加课|添派|摊派|征收|常例|常税")
_TAX_RELIEF_RE = re.compile(r"减免|蠲免|宽免|罢征|停征|减税|裁撤|罢税|豁免|停派|免派")
_PERMANENT_RE = re.compile(r"永久|永远|永为|常例|常税|定额|恒久|岁额|照旧征解|永著为例")
_TEMPORARY_DIRECTIVE_RE = re.compile(
    r"暂行|暂准|暂开|暂设|权宜|姑准|试行|试办|试开|限期|限[一二三四五六七八九十0-9]+"
    r"[日月年]|[一二三四五六七八九十0-9]+[日月年]为期|不得为例|事平即止|事竣即止|临时|救急|急需"
)
_DOCTRINE_PERMANENCE_RE = re.compile(
    r"基本国策|国是|定为国策|定策|永为|永远|永久|长期|恒久|常例|永著为例|以后照此|一体遵行"
)

_DOCTRINE_CACHE: Optional[Dict[str, object]] = None
_DIRECTIVE_CATEGORY_CACHE: Optional[Dict[str, object]] = None


# ── 轻量国策脊柱：doctrine → issue → legacy ────────────────────────────────

def load_policy_doctrines() -> Dict[str, object]:
    """Load the single doctrine config used by directives, issues, memorials.

    The file intentionally replaces several heavier proposed tables. Runtime
    state still lives in existing ``issues`` and ``legacies`` rows.
    """

    global _DOCTRINE_CACHE
    if _DOCTRINE_CACHE is None:
        with open(bundled_path("content", "policy_doctrines.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data.get("doctrines"), list):
            raise ValueError("policy_doctrines.json 缺少 doctrines 列表")
        _DOCTRINE_CACHE = data
    return _DOCTRINE_CACHE


def reset_doctrine_cache_for_tests() -> None:
    global _DOCTRINE_CACHE, _DIRECTIVE_CATEGORY_CACHE
    _DOCTRINE_CACHE = None
    _DIRECTIVE_CATEGORY_CACHE = None


def list_doctrines() -> List[Dict[str, object]]:
    return [dict(item) for item in (load_policy_doctrines().get("doctrines") or []) if isinstance(item, dict)]


def doctrine_by_id(doctrine_id: str) -> Optional[Dict[str, object]]:
    target = str(doctrine_id or "")
    for item in list_doctrines():
        if str(item.get("id") or "") == target:
            return item
    return None


def doctrine_legacy_key(doctrine_id: str) -> str:
    return f"doctrine:{str(doctrine_id or '').strip()}"


def _load_directive_categories() -> Dict[str, object]:
    global _DIRECTIVE_CATEGORY_CACHE
    if _DIRECTIVE_CATEGORY_CACHE is None:
        with open(bundled_path("content", "directive_categories.json"), encoding="utf-8") as fh:
            _DIRECTIVE_CATEGORY_CACHE = json.load(fh)
    return _DIRECTIVE_CATEGORY_CACHE


def directive_category_id(text: str) -> str:
    """Small mirror of lifecycle.classify_directive without importing lifecycle."""

    cfg = _load_directive_categories()
    best: Optional[Dict[str, object]] = None
    best_hits = 0
    for cat in cfg.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        hits = sum(1 for kw in (cat.get("keywords") or []) if kw and str(kw) in str(text or ""))
        if hits > best_hits:
            best = cat
            best_hits = hits
    if best is None:
        default_id = str(cfg.get("default_category") or "misc")
        best = next((c for c in cfg.get("categories") or [] if isinstance(c, dict) and c.get("id") == default_id), None)
    return str((best or {}).get("id") or "misc")


def classify_directive_doctrines(text: str, category_id: str = "", *, limit: int = 3) -> List[Dict[str, object]]:
    """Return likely doctrines touched by a decree/directive.

    This is deliberately a light classifier: existing directive categories do
    the heavy routing; doctrine adds the political route labels.
    """

    decree = str(text or "")
    cat = str(category_id or "") or directive_category_id(decree)
    matches: List[Dict[str, object]] = []
    for doctrine in list_doctrines():
        score = 0
        reasons: List[str] = []
        if cat and cat in [str(x) for x in (doctrine.get("supports_categories") or [])]:
            score += 5
            reasons.append(f"类别:{cat}")
        name = str(doctrine.get("name") or "")
        if name and name in decree:
            score += 5
            reasons.append(name)
        for kw in doctrine.get("keywords") or []:
            if kw and str(kw) in decree:
                score += 2
                reasons.append(str(kw))
        if score <= 0:
            continue
        matches.append({
            "id": str(doctrine.get("id") or ""),
            "name": name,
            "axis": str(doctrine.get("axis") or ""),
            "score": score,
            "reasons": reasons[:5],
        })
    matches.sort(key=lambda item: (-int(item["score"]), str(item["id"])))
    return matches[:max(1, int(limit))]


def directive_exception_mode(text: str) -> Dict[str, object]:
    """Detect short-term workaround language in a directive.

    This does not create another policy tier. It only labels decrees that
    knowingly violate an orthodox route as emergency exceptions instead of a
    bid to establish a rival basic policy.
    """

    decree = str(text or "")
    if not decree:
        return {}
    if _DOCTRINE_PERMANENCE_RE.search(decree):
        return {"mode": "doctrinal", "label": "定策", "risk_tags": []}
    if not _TEMPORARY_DIRECTIVE_RE.search(decree):
        return {}
    return {
        "mode": "temporary",
        "label": "权宜变通",
        "risk_tags": ["越制", "失范"],
    }


def active_doctrine_legacies(db: GameDB) -> Dict[str, Dict[str, object]]:
    rows = db.conn.execute(
        "SELECT id, name, legacy_key, narrative_hint FROM legacies "
        "WHERE status='active' AND legacy_key LIKE 'doctrine:%' ORDER BY id"
    ).fetchall()
    active: Dict[str, Dict[str, object]] = {}
    for row in rows:
        key = str(row["legacy_key"] or "")
        doctrine_id = key.split(":", 1)[1] if ":" in key else ""
        if doctrine_id:
            active[doctrine_id] = {
                "legacy_id": int(row["id"]),
                "name": str(row["name"] or ""),
                "legacy_key": key,
                "narrative_hint": str(row["narrative_hint"] or ""),
            }
    return active


def doctrine_issue_row(db: GameDB, doctrine_id: str, *, status: str = "active"):
    return db.conn.execute(
        "SELECT * FROM issues WHERE origin_kind='doctrine' AND origin_ref=? AND status=? "
        "ORDER BY id DESC LIMIT 1",
        (str(doctrine_id), str(status)),
    ).fetchone()


def doctrine_establishment_blockers(db: GameDB, doctrine_id: str) -> List[Dict[str, object]]:
    """Active basic policies that prevent this route from becoming orthodox."""

    doctrine = doctrine_by_id(doctrine_id) or {}
    active = active_doctrine_legacies(db)
    blockers: List[Dict[str, object]] = []
    for conflict_id in doctrine.get("conflicts") or []:
        cid = str(conflict_id or "")
        if cid not in active:
            continue
        cdoc = doctrine_by_id(cid) or {}
        blockers.append({
            "id": cid,
            "name": str(cdoc.get("name") or active[cid].get("name") or cid),
            "axis": str(cdoc.get("axis") or ""),
            "legacy_id": active[cid].get("legacy_id"),
        })
    return blockers


def directive_doctrine_review(
    db: GameDB,
    state: Optional[GameState],
    text: str,
    *,
    category_id: str = "",
    actor: str = "",
) -> Dict[str, object]:
    """Classify a directive and describe route conflicts without mutating state."""

    cat = str(category_id or "") or directive_category_id(text)
    matches = classify_directive_doctrines(text, cat, limit=4)
    if not matches:
        return {
            "category": cat,
            "matches": [],
            "conflicts": [],
            "risk_tags": [],
            "summary": "",
            "execution_gate": {"level": "clear", "resistance_delta": 0, "check_risk_delta": {}, "notes": []},
        }
    active = active_doctrine_legacies(db)
    primary = dict(matches[0])
    primary["status"] = "orthodox" if primary["id"] in active else "contested"
    doctrine = doctrine_by_id(str(primary["id"])) or {}
    conflicts = doctrine_establishment_blockers(db, str(primary["id"]))
    exception = directive_exception_mode(text)
    exception_mode = str(exception.get("mode") or "")
    temporary_exception = bool(conflicts and exception_mode == "temporary")
    risk_tags = ["路线反噬", "失范"] if conflicts else []
    for tag in exception.get("risk_tags") or []:
        if str(tag) not in risk_tags:
            risk_tags.append(str(tag))
    if temporary_exception and "权宜" not in risk_tags:
        risk_tags.append("权宜")
    if primary.get("status") == "contested":
        risk_tags.append("未成正统")
    if actor:
        stance = character_doctrine_stance(db, actor, str(primary["id"]))
        primary["actor_stance"] = stance
    summary = f"路线：{primary['name']}"
    if conflicts:
        summary += "；冲突：" + "、".join(str(item["name"]) for item in conflicts[:3])
        if temporary_exception:
            summary += "；仅作权宜变通"
    elif primary.get("status") == "orthodox":
        summary += "；符合既定国策"
    else:
        summary += "；尚待廷议成说"
    result = {
        "category": cat,
        "primary": primary,
        "matches": matches,
        "conflicts": conflicts,
        "risk_tags": risk_tags,
        "summary": summary,
        "exception_mode": exception_mode,
        "exception_label": str(exception.get("label") or ""),
        "temporary_exception": temporary_exception,
        "establishment_blocked": bool(conflicts and not temporary_exception),
        "establishment_blockers": conflicts,
    }
    result["execution_gate"] = directive_doctrine_execution_gate(result)
    return result


def directive_doctrine_execution_gate(review: Dict[str, object]) -> Dict[str, object]:
    """Translate doctrine fit into existing directive execution pressure.

    This keeps doctrine as a lightweight constraint layer: no new policy state,
    only resistance and anomaly-risk deltas consumed by lifecycle.build_chain.
    """

    if not review or not review.get("primary"):
        return {"level": "clear", "resistance_delta": 0, "check_risk_delta": {}, "notes": []}
    primary = review.get("primary") if isinstance(review.get("primary"), dict) else {}
    conflicts = review.get("conflicts") if isinstance(review.get("conflicts"), list) else []
    risk_tags = review.get("risk_tags") if isinstance(review.get("risk_tags"), list) else []
    resistance_delta = 0
    check_risk_delta: Dict[str, int] = {}
    notes: List[str] = []
    level = "clear"

    temporary_exception = bool(review.get("temporary_exception"))
    if conflicts:
        if temporary_exception:
            level = "temporary_exception"
            resistance_delta += min(20, 8 + 4 * len(conflicts))
            check_risk_delta["block"] = 10 + 2 * min(3, len(conflicts))
            check_risk_delta["delay"] = 5 + min(3, len(conflicts))
            check_risk_delta["surprise"] = 8
            notes.append("以权宜变通抵牾既定国策，承行可试但越制反噬上升")
        else:
            level = "conflict"
            resistance_delta += min(28, 12 + 6 * len(conflicts))
            check_risk_delta["block"] = 18 + 4 * min(3, len(conflicts))
            check_risk_delta["delay"] = 8 + 2 * min(3, len(conflicts))
            check_risk_delta["surprise"] = 5
            notes.append("违背既定基本国策，廷议与承行阻力上升")
    elif str(primary.get("status") or "") == "contested":
        level = "contested"
        resistance_delta += 5
        check_risk_delta["block"] = 5
        check_risk_delta["delay"] = 5
        notes.append("路线尚未成正统，承办需冒议论风险")

    stance = primary.get("actor_stance") if isinstance(primary.get("actor_stance"), dict) else {}
    stance_label = str(stance.get("stance") or "")
    if stance_label == "oppose":
        resistance_delta += 8
        check_risk_delta["block"] = int(check_risk_delta.get("block") or 0) + 8
        notes.append("主办官与此路线相悖，执行意愿不足")
        if level == "clear":
            level = "assignee_opposes"
    elif stance_label == "neutral" and risk_tags:
        resistance_delta += 2
        notes.append("主办官立场未定，需靠明旨压实")

    return {
        "level": level,
        "exception_mode": str(review.get("exception_mode") or ""),
        "temporary_exception": temporary_exception,
        "establishment_blocked": bool(conflicts and not temporary_exception),
        "resistance_delta": max(0, min(40, int(resistance_delta))),
        "check_risk_delta": {k: int(v) for k, v in check_risk_delta.items() if int(v) > 0},
        "notes": notes[:4],
    }


def ensure_doctrine_legacy(
    db: GameDB,
    state: GameState,
    doctrine_id: str,
    *,
    source_issue_id: int = 0,
) -> Dict[str, object]:
    """Create the active legacy for a resolved doctrine issue, idempotently."""

    doctrine = doctrine_by_id(doctrine_id)
    if not doctrine:
        return {"created": False, "reason": "unknown_doctrine", "doctrine_id": doctrine_id}
    key = doctrine_legacy_key(doctrine_id)
    existing = db.conn.execute(
        "SELECT id FROM legacies WHERE status='active' AND legacy_key=? LIMIT 1",
        (key,),
    ).fetchone()
    if existing is not None:
        return {"created": False, "legacy_id": int(existing["id"]), "duplicate": True}
    blockers = doctrine_establishment_blockers(db, doctrine_id)
    if blockers:
        return {
            "created": False,
            "blocked": True,
            "reason": "conflicting_orthodox_doctrine",
            "doctrine_id": doctrine_id,
            "blockers": blockers,
        }
    legacy_id = db.insert_legacy(
        state,
        name=f"基本国策：{str(doctrine.get('name') or doctrine_id)}",
        source_issue_id=int(source_issue_id) or None,
        modifiers=dict(doctrine.get("legacy_effects") or {}),
        narrative_hint=str(doctrine.get("summary") or "")[:200],
        duration_months=-1,
        legacy_key=key,
    )
    try:
        db.record_log(state, f"【基本国策】{doctrine.get('name')}成为朝廷定策。")
    except Exception:
        pass
    return {"created": True, "legacy_id": legacy_id, "doctrine_id": doctrine_id}


def retire_conflicting_doctrine_legacies(
    db: GameDB,
    state: GameState,
    doctrine_id: str,
    *,
    source_issue_id: int = 0,
    reason: str = "doctrine_reform",
) -> List[Dict[str, object]]:
    """Clear active doctrine legacies that block a decisive reform route."""

    doctrine = doctrine_by_id(doctrine_id) or {}
    blockers = doctrine_establishment_blockers(db, doctrine_id)
    retired: List[Dict[str, object]] = []
    for blocker in blockers:
        legacy_id = int(blocker.get("legacy_id") or 0)
        if legacy_id <= 0:
            continue
        row = db.conn.execute(
            "SELECT id, name, narrative_hint, legacy_key FROM legacies WHERE id=? AND status='active'",
            (legacy_id,),
        ).fetchone()
        if row is None:
            continue
        old_hint = str(row["narrative_hint"] or "")
        suffix = f"；因「{str(doctrine.get('name') or doctrine_id)}」改弦更张而退场"
        db.conn.execute(
            "UPDATE legacies SET status='cleared', narrative_hint=? WHERE id=?",
            ((old_hint + suffix)[:200], legacy_id),
        )
        retired.append({
            "id": str(blocker.get("id") or ""),
            "name": str(blocker.get("name") or row["name"] or ""),
            "legacy_id": legacy_id,
            "legacy_key": str(row["legacy_key"] or ""),
            "reason": reason,
            "source_issue_id": int(source_issue_id or 0),
        })
    if retired:
        try:
            db._legacy_mod_cache = None
        except Exception:
            pass
        names = "、".join(str(item.get("name") or item.get("id")) for item in retired[:3])
        try:
            db.record_log(state, f"【改弦更张】{doctrine.get('name') or doctrine_id}压倒旧策：{names}。")
        except Exception:
            pass
        db.conn.commit()
    return retired


def _is_decisive_doctrine_reform(trigger_kind: str) -> bool:
    # 玩家批红采纳路线奏疏，是最明确的“朕要改弦”的旧系统动作。
    return str(trigger_kind or "") == "memorial"


def ensure_doctrine_issue(
    db: GameDB,
    state: GameState,
    doctrine_id: str,
    *,
    trigger_kind: str = "directive",
    trigger_ref: str = "",
    delta_bar: int = 0,
    narrative: str = "",
) -> Dict[str, object]:
    """Ensure a contested doctrine exists as an initiative issue.

    If the doctrine is already orthodox, this is a no-op. If the issue crosses
    100 through this call, the matching legacy is created immediately.
    """

    doctrine = doctrine_by_id(doctrine_id)
    if not doctrine:
        return {"ok": False, "reason": "unknown_doctrine", "doctrine_id": doctrine_id}
    if doctrine_id in active_doctrine_legacies(db):
        return {"ok": True, "status": "orthodox", "doctrine_id": doctrine_id}

    row = doctrine_issue_row(db, doctrine_id, status="active")
    if row is None:
        cfg = load_policy_doctrines()
        issue_id = db.insert_issue(
            state,
            kind="initiative",
            title=f"路线争议：{str(doctrine.get('name') or doctrine_id)}",
            origin_kind="doctrine",
            origin_ref=doctrine_id,
            bar_value=int(cfg.get("default_issue_bar") or 42),
            bar_good_meaning="成为正统",
            bar_bad_meaning="被压制",
            inertia=0,
            stage_text=str(doctrine.get("summary") or "")[:120],
            severity=55,
            faction_hint="",
            tags=[f"doctrine:{doctrine_id}", f"axis:{str(doctrine.get('axis') or '')}"] + [
                str(tag) for tag in (doctrine.get("unlock_issue_tags") or [])
            ],
            cancellable="by_progress",
            effect_on_resolve={"legacy": {
                "name": f"基本国策：{str(doctrine.get('name') or doctrine_id)}",
                "duration_months": -1,
                "modifiers": dict(doctrine.get("legacy_effects") or {}),
                "legacy_key": doctrine_legacy_key(doctrine_id),
            }},
            effect_on_fail={"metrics": {"皇威": -1}},
        )
        row = db.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
    else:
        issue_id = int(row["id"])

    result: Dict[str, object] = {"ok": True, "status": "contested", "issue_id": issue_id, "doctrine_id": doctrine_id}
    delta = int(delta_bar or 0)
    if delta and row is not None and str(row["status"]) == "active":
        blockers = doctrine_establishment_blockers(db, doctrine_id)
        narrative_text = narrative or f"{doctrine.get('name')}路线因{trigger_kind}而更受朝议关注。"
        if delta > 0 and blockers:
            current_bar = int(row["bar_value"] or 0)
            cap = int((load_policy_doctrines().get("blocked_issue_bar_cap") or 95))
            if current_bar >= cap and _is_decisive_doctrine_reform(trigger_kind):
                retired = retire_conflicting_doctrine_legacies(
                    db,
                    state,
                    doctrine_id,
                    source_issue_id=int(issue_id),
                    reason=str(trigger_kind or "doctrine_reform"),
                )
                if retired:
                    result["retired_blockers"] = retired
                    names = "、".join(str(item.get("name") or item.get("id")) for item in retired[:3])
                    narrative_text = (
                        f"{narrative_text} 其议已逼成定局，遂改弦更张，旧策「{names}」退场。"
                    )
                blockers = doctrine_establishment_blockers(db, doctrine_id)
            if blockers:
                capped_delta = max(0, min(delta, cap - current_bar))
                names = "、".join(str(item.get("name") or item.get("id")) for item in blockers[:3])
                result["establishment_blocked"] = True
                result["establishment_blockers"] = blockers
                if capped_delta != delta:
                    narrative_text = (
                        f"{narrative_text} 但其与已定基本国策「{names}」相抵牾，"
                        "暂不得遽升为国是。"
                    )
                delta = capped_delta
        advanced = db.advance_issue(
            state,
            issue_id,
            trigger_kind=trigger_kind,
            trigger_ref=str(trigger_ref or ""),
            delta_bar=delta,
            stage_text=str(row["stage_text"] or doctrine.get("summary") or ""),
            narrative=narrative_text,
        )
        if advanced is not None:
            result["bar_value"] = int(advanced["bar_value"])
            result["issue_status"] = str(advanced["status"])
            if str(advanced["status"]) == "resolved":
                result["legacy"] = ensure_doctrine_legacy(
                    db, state, doctrine_id, source_issue_id=int(advanced["id"])
                )
    return result


def sync_doctrine_issue_result(db: GameDB, state: GameState, issue_row) -> Dict[str, object]:
    """Convert a resolved doctrine issue to a doctrine legacy."""

    if issue_row is None:
        return {}
    if str(issue_row["origin_kind"] or "") != "doctrine":
        return {}
    doctrine_id = str(issue_row["origin_ref"] or "")
    if str(issue_row["status"] or "") == "resolved":
        return ensure_doctrine_legacy(db, state, doctrine_id, source_issue_id=int(issue_row["id"]))
    return {"created": False, "doctrine_id": doctrine_id, "status": str(issue_row["status"] or "")}


def apply_directive_doctrine_effects(
    db: GameDB,
    state: GameState,
    *,
    directive_id: int,
    text: str,
    category_id: str = "",
    actor: str = "",
) -> Dict[str, object]:
    """Tag an issued directive with doctrine context and advance its route issue."""

    review = directive_doctrine_review(db, state, text, category_id=category_id, actor=actor)
    primary = review.get("primary") if isinstance(review.get("primary"), dict) else None
    if not primary:
        return review
    doctrine_id = str(primary.get("id") or "")
    cfg = load_policy_doctrines()
    delta = int(cfg.get("directive_issue_delta") or 8)
    temporary_exception = bool(review.get("temporary_exception"))
    if temporary_exception:
        review["issue"] = {
            "ok": True,
            "status": "temporary_exception",
            "doctrine_id": doctrine_id,
            "advanced": False,
            "reason": "short_term_workaround",
        }
        label = f"路线：{primary.get('name')}；权宜变通"
        if review.get("conflicts"):
            label += "；冲突：" + "、".join(str(item.get("name")) for item in review["conflicts"][:2])
        row = db.conn.execute("SELECT notes FROM turn_directives WHERE id=?", (int(directive_id),)).fetchone()
        if row is not None and label not in str(row["notes"] or ""):
            notes = (str(row["notes"] or "").strip() + f"；{label}").strip("；")
            db.conn.execute("UPDATE turn_directives SET notes=? WHERE id=?", (notes[:500], int(directive_id)))
            db.conn.commit()
        return review
    if review.get("conflicts"):
        delta = max(2, delta // 2)
    issue_result = ensure_doctrine_issue(
        db,
        state,
        doctrine_id,
        trigger_kind="directive",
        trigger_ref=str(directive_id),
        delta_bar=delta,
        narrative=f"旨意#{int(directive_id)}触及「{primary.get('name')}」路线，朝议有了具体落点。",
    )
    review["issue"] = issue_result
    label = f"路线：{primary.get('name')}"
    if review.get("conflicts"):
        label += "；冲突：" + "、".join(str(item.get("name")) for item in review["conflicts"][:2])
    row = db.conn.execute("SELECT notes FROM turn_directives WHERE id=?", (int(directive_id),)).fetchone()
    if row is not None and label not in str(row["notes"] or ""):
        notes = (str(row["notes"] or "").strip() + f"；{label}").strip("；")
        db.conn.execute("UPDATE turn_directives SET notes=? WHERE id=?", (notes[:500], int(directive_id)))
        db.conn.commit()
    return review


def apply_memorial_doctrine_effect(
    db: GameDB,
    state: GameState,
    memorial_row,
    action: str,
) -> Dict[str, object]:
    """Let approving/denying route memorials move the existing doctrine issue."""

    if memorial_row is None or str(memorial_row["ref_kind"] or "") != "issue":
        return {}
    issue = db.conn.execute("SELECT * FROM issues WHERE id=?", (str(memorial_row["ref_id"] or ""),)).fetchone()
    if issue is None or str(issue["origin_kind"] or "") != "doctrine" or str(issue["status"] or "") != "active":
        return {}
    doctrine_id = str(issue["origin_ref"] or "")
    doctrine = doctrine_by_id(doctrine_id) or {}
    kind = str(memorial_row["kind"] or "")
    approve = str(action) == "approve"
    cfg = load_policy_doctrines()
    base = int(cfg.get("memorial_issue_delta") or 12)
    # 支持性奏疏获准推进路线；反对性弹章获准阻滞路线。驳回时反向小幅波动。
    if kind == "弹章":
        delta = -base if approve else max(4, base // 2)
    else:
        delta = base if approve else -max(4, base // 2)
    result = {"doctrine_id": doctrine_id, "delta_bar": delta, "issue_id": int(issue["id"])}
    issue_result = ensure_doctrine_issue(
        db,
        state,
        doctrine_id,
        trigger_kind="memorial",
        trigger_ref=str(memorial_row["id"]),
        delta_bar=delta,
        narrative=f"{memorial_row['author_name']}{kind}获{'准' if approve else '驳'}，牵动「{doctrine.get('name') or doctrine_id}」路线。",
    )
    result["issue"] = issue_result
    if issue_result.get("legacy"):
        result["legacy"] = issue_result["legacy"]
    if issue_result.get("retired_blockers"):
        result["retired_blockers"] = issue_result["retired_blockers"]
    factions = _apply_doctrine_memorial_faction_reaction(
        db,
        doctrine_id,
        route_delta=delta,
        author_name=str(memorial_row["author_name"] or ""),
        author_won=approve,
    )
    if factions:
        result["factions"] = factions
    return result


def _apply_doctrine_memorial_faction_reaction(
    db: GameDB,
    doctrine_id: str,
    *,
    route_delta: int,
    author_name: str = "",
    author_won: bool = False,
) -> List[Dict[str, object]]:
    """Map a route memorial decision onto existing faction satisfaction/heat.

    No route-faction state is stored. The temporary alignment is derived at the
    moment a memorial is decided, then expressed through the old faction table.
    """

    if not str(doctrine_id or "") or int(route_delta or 0) == 0:
        return []
    alignment = doctrine_alignment_summary(db, doctrine_id, max_factions=6, max_figures=0)
    sat_delta: Dict[str, int] = {}
    heat_delta: Dict[str, int] = {}
    effects: List[Dict[str, object]] = []

    for row in alignment.get("factions") or []:
        if not isinstance(row, dict):
            continue
        faction = str(row.get("faction") or "")
        if not faction or faction in ("无",):
            continue
        stance = str(row.get("stance") or "neutral")
        if stance == "neutral":
            continue
        supports_route = stance == "support"
        pleased = (int(route_delta) > 0 and supports_route) or (int(route_delta) < 0 and not supports_route)
        sat = 2 if pleased else -2
        heat = -2 if pleased else 4
        sat_delta[faction] = sat_delta.get(faction, 0) + sat
        heat_delta[faction] = heat_delta.get(faction, 0) + heat

    if author_name:
        arow = db.conn.execute("SELECT faction FROM characters WHERE name=?", (str(author_name),)).fetchone()
        author_faction = str(arow["faction"] or "") if arow else ""
        if author_faction and author_faction not in ("无",):
            sat_delta[author_faction] = sat_delta.get(author_faction, 0) + (1 if author_won else -1)

    if sat_delta:
        db.adjust_factions({faction: {"satisfaction": delta} for faction, delta in sat_delta.items() if delta})
    for faction, delta in heat_delta.items():
        if not delta:
            continue
        try:
            from ming_sim.theater import adjust_faction_heat
            adjust_faction_heat(db, faction, delta, f"路线争议:{doctrine_id}")
        except Exception:
            continue

    for faction in sorted(set(sat_delta) | set(heat_delta)):
        sd = int(sat_delta.get(faction) or 0)
        hd = int(heat_delta.get(faction) or 0)
        if sd == 0 and hd == 0:
            continue
        effects.append({
            "faction": faction,
            "satisfaction_delta": sd,
            "heat_delta": hd,
            "tone": "good" if sd > 0 and hd <= 0 else "bad" if sd < 0 or hd > 0 else "neutral",
        })
    return effects


def character_doctrine_stance(db: GameDB, name: str, doctrine_id: str) -> Dict[str, object]:
    """Derive a minister's just-in-time stance from overrides, DB row, foundation."""

    doctrine = doctrine_by_id(doctrine_id)
    if not doctrine:
        return {"name": name, "doctrine_id": doctrine_id, "stance": "neutral", "score": 0.0, "reasons": []}
    cfg = load_policy_doctrines()
    return _character_doctrine_stance_from_context(
        str(name),
        str(doctrine_id),
        doctrine,
        cfg,
        _character_policy_row(db, str(name)),
        _foundation_policy_profile(str(name)),
    )


def _character_policy_row(db: GameDB, name: str):
    return db.conn.execute(
        "SELECT name, office, office_type, faction, style, ability FROM characters WHERE name=? LIMIT 1",
        (str(name),),
    ).fetchone()


def _foundation_policy_profile(name: str):
    try:
        from ming_sim import foundation
        return foundation.profile(str(name))
    except Exception:
        return None


def _row_value(row, key: str, default: Any = "") -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        if hasattr(row, "keys") and key not in row.keys():
            return default
        return row[key]
    except Exception:
        return default


def _character_doctrine_stance_from_context(
    name: str,
    doctrine_id: str,
    doctrine: Dict[str, object],
    cfg: Dict[str, object],
    row,
    profile,
) -> Dict[str, object]:
    overrides = cfg.get("overrides") if isinstance(cfg.get("overrides"), dict) else {}
    person_overrides = overrides.get(name) if isinstance(overrides.get(name), dict) else {}
    if doctrine_id in person_overrides:
        score = float(person_overrides.get(doctrine_id) or 0.0)
        return _stance_result(name, doctrine_id, score, [f"关键人物覆写:{score:+.2f}"])

    faction = str(_row_value(row, "faction", "") or "")
    text_blob = " ".join([
        str(_row_value(row, "office", "") or ""),
        str(_row_value(row, "office_type", "") or ""),
        faction,
        str(_row_value(row, "style", "") or ""),
    ])
    rules = doctrine.get("support_rules") if isinstance(doctrine.get("support_rules"), dict) else {}
    score = 0.0
    reasons: List[str] = []
    for fac, value in (rules.get("factions") or {}).items():
        if str(fac) == faction:
            score += float(value)
            reasons.append(f"派系{fac}{float(value):+.2f}")
    for kw, value in (rules.get("office_keywords") or {}).items():
        if str(kw) and str(kw) in text_blob:
            score += float(value)
            reasons.append(f"职分{kw}{float(value):+.2f}")
    for kw, value in (rules.get("style_keywords") or {}).items():
        if str(kw) and str(kw) in text_blob:
            score += float(value)
            reasons.append(f"底色{kw}{float(value):+.2f}")

    if profile:
        traits = {str(t.get("trait_code") or ""): str(t.get("trait_name") or "") for t in (profile.get("traits") or [])}
        for code, value in (rules.get("traits") or {}).items():
            if str(code) in traits:
                score += float(value)
                reasons.append(f"特质{traits[str(code)]}{float(value):+.2f}")
        ability = profile.get("ability") if isinstance(profile.get("ability"), dict) else {}
        for key, value in (rules.get("abilities") or {}).items():
            try:
                raw = float(ability.get(str(key)) or 0)
            except (TypeError, ValueError):
                raw = 0
            if raw:
                contrib = (raw - 10.0) * float(value)
                score += contrib
                if abs(contrib) >= 0.03:
                    reasons.append(f"能力{key}{contrib:+.2f}")
        persona = profile.get("persona") if isinstance(profile.get("persona"), dict) else {}
        for key, value in (rules.get("persona") or {}).items():
            try:
                raw = float(persona.get(str(key)) or 0)
            except (TypeError, ValueError):
                raw = 0
            if raw:
                contrib = raw * float(value)
                score += contrib
                if abs(contrib) >= 0.03:
                    reasons.append(f"心性{key}{contrib:+.2f}")
    elif row is not None:
        try:
            ability100 = float(_row_value(row, "ability", 50) or 50)
        except (TypeError, ValueError):
            ability100 = 50.0
        if ability100 >= 70:
            score += 0.04
            reasons.append("本档能力较高+0.04")
    return _stance_result(name, doctrine_id, score, reasons)


def character_policy_ideals(
    db: GameDB,
    name: str,
    *,
    limit: int = 3,
    context_row=None,
) -> Dict[str, object]:
    """Compact per-character doctrine ideals, derived on demand only.

    This is the NPC-facing mirror of doctrine politics: senior officials fight
    because they want certain routes to become orthodox, not because a new
    persistent ideology table says so.
    """

    person_name = str(name or "")
    status_rows = db.conn.execute(
        "SELECT substr(legacy_key, 10) AS doctrine_id, 'orthodox' AS route_status, 100 AS bar_value "
        "FROM legacies WHERE status='active' AND legacy_key LIKE 'doctrine:%' "
        "UNION ALL "
        "SELECT origin_ref AS doctrine_id, 'contested' AS route_status, bar_value "
        "FROM issues WHERE origin_kind='doctrine' AND status='active'"
    ).fetchall()
    route_by_doctrine: Dict[str, Dict[str, object]] = {}
    for row in status_rows:
        doctrine_id = str(row["doctrine_id"] or "")
        if not doctrine_id:
            continue
        status = str(row["route_status"] or "")
        if doctrine_id in route_by_doctrine and route_by_doctrine[doctrine_id].get("status") == "orthodox":
            continue
        route_by_doctrine[doctrine_id] = {
            "status": status,
            "status_label": "正统" if status == "orthodox" else "争议",
            "bar_value": int(row["bar_value"] or 0),
        }

    def route_status(doctrine_id: str) -> Dict[str, object]:
        if doctrine_id in route_by_doctrine:
            return dict(route_by_doctrine[doctrine_id])
        return {"status": "latent", "status_label": "潜势", "bar_value": 0}

    supports: List[Dict[str, object]] = []
    opposes: List[Dict[str, object]] = []
    cfg = load_policy_doctrines()
    row = context_row if context_row is not None else _character_policy_row(db, person_name)
    profile = _foundation_policy_profile(person_name)
    for doctrine in list_doctrines():
        doctrine_id = str(doctrine.get("id") or "")
        if not doctrine_id:
            continue
        stance = _character_doctrine_stance_from_context(person_name, doctrine_id, doctrine, cfg, row, profile)
        score = float(stance.get("score") or 0)
        if str(stance.get("stance") or "") == "neutral" and abs(score) < 0.18:
            continue
        item = {
            "id": doctrine_id,
            "name": str(doctrine.get("name") or doctrine_id),
            "axis": str(doctrine.get("axis") or ""),
            "stance": str(stance.get("stance") or "neutral"),
            "score": round(score, 3),
            "reasons": list(stance.get("reasons") or [])[:3],
            **route_status(doctrine_id),
        }
        if score >= 0:
            supports.append(item)
        else:
            opposes.append(item)
    status_rank = {"orthodox": 0, "contested": 1, "latent": 2}
    supports.sort(key=lambda item: (
        status_rank.get(str(item.get("status") or "latent"), 3),
        -float(item.get("score") or 0),
        str(item.get("id") or ""),
    ))
    opposes.sort(key=lambda item: (
        status_rank.get(str(item.get("status") or "latent"), 3),
        float(item.get("score") or 0),
        str(item.get("id") or ""),
    ))
    max_items = max(1, int(limit))
    primary = supports[0] if supports else None
    if primary:
        summary = f"治国所向：{primary['name']}，愿使其成为朝廷正途。"
    elif opposes:
        summary = f"最忌路线：{opposes[0]['name']}，遇此多半阻挠。"
    else:
        summary = "路线立场未显，更多随职分、恩怨与局势摇摆。"
    return {
        "name": person_name,
        "summary": summary,
        "supports": supports[:max_items],
        "opposes": opposes[:max_items],
    }


def _stance_result(name: str, doctrine_id: str, score: float, reasons: List[str]) -> Dict[str, object]:
    score = max(-1.0, min(1.0, float(score)))
    stance = "support" if score >= 0.25 else "oppose" if score <= -0.25 else "neutral"
    return {
        "name": str(name or ""),
        "doctrine_id": str(doctrine_id or ""),
        "stance": stance,
        "score": round(score, 3),
        "reasons": reasons[:6],
    }


def doctrine_faction_alignment(db: GameDB, doctrine_id: str) -> List[Dict[str, object]]:
    """Temporary route alliance for display/debug; does not persist factions."""

    rows = db.conn.execute(
        "SELECT name, faction FROM characters WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
        "ORDER BY ability DESC LIMIT 40"
    ).fetchall()
    buckets: Dict[str, Dict[str, object]] = {}
    for row in rows:
        name = str(row["name"])
        faction = str(row["faction"] or "中立")
        stance = character_doctrine_stance(db, name, doctrine_id)
        bucket = buckets.setdefault(faction, {"faction": faction, "support": 0, "oppose": 0, "neutral": 0, "figures": []})
        bucket[str(stance["stance"])] = int(bucket.get(str(stance["stance"])) or 0) + 1
        if abs(float(stance["score"])) >= 0.35 and len(bucket["figures"]) < 4:  # type: ignore[index]
            bucket["figures"].append({"name": name, "stance": stance["stance"], "score": stance["score"]})  # type: ignore[index]
    return list(buckets.values())


def doctrine_alignment_summary(
    db: GameDB,
    doctrine_id: str,
    *,
    max_factions: int = 4,
    max_figures: int = 4,
) -> Dict[str, object]:
    """Compact, transient route politics summary for issue cards.

    The underlying stances remain derived on demand from character/foundation
    data. This helper only trims them for UI payloads.
    """

    buckets = doctrine_faction_alignment(db, doctrine_id)
    faction_rows: List[Dict[str, object]] = []
    figure_rows: List[Dict[str, object]] = []
    for bucket in buckets:
        support = int(bucket.get("support") or 0)
        oppose = int(bucket.get("oppose") or 0)
        neutral = int(bucket.get("neutral") or 0)
        net = support - oppose
        stance = "support" if net > 0 else "oppose" if net < 0 else "neutral"
        faction_rows.append({
            "faction": str(bucket.get("faction") or "中立"),
            "support": support,
            "oppose": oppose,
            "neutral": neutral,
            "stance": stance,
            "net": net,
        })
        for fig in bucket.get("figures") or []:
            if not isinstance(fig, dict):
                continue
            figure_rows.append({
                "name": str(fig.get("name") or ""),
                "faction": str(bucket.get("faction") or "中立"),
                "stance": str(fig.get("stance") or "neutral"),
                "score": float(fig.get("score") or 0),
            })
    faction_rows.sort(
        key=lambda item: (
            -abs(int(item.get("net") or 0)),
            -(int(item.get("support") or 0) + int(item.get("oppose") or 0)),
            str(item.get("faction") or ""),
        )
    )
    figure_rows.sort(key=lambda item: (-abs(float(item.get("score") or 0)), str(item.get("name") or "")))
    return {
        "factions": faction_rows[:max(0, int(max_factions))],
        "figures": figure_rows[:max(0, int(max_figures))],
    }


def doctrine_legacy_payload(legacy_row) -> Dict[str, object]:
    """Single payload for an orthodox doctrine carried by an active legacy."""

    legacy_key = str(_row_value(legacy_row, "legacy_key", "") or "")
    if not legacy_key.startswith("doctrine:"):
        return {}
    doctrine_id = legacy_key.split(":", 1)[1]
    doctrine = doctrine_by_id(doctrine_id) or {}
    if not doctrine:
        return {}
    conflicts: List[Dict[str, object]] = []
    for conflict_id in doctrine.get("conflicts") or []:
        cid = str(conflict_id or "")
        conflict = doctrine_by_id(cid) or {}
        conflicts.append({
            "id": cid,
            "name": str(conflict.get("name") or cid),
            "axis": str(conflict.get("axis") or ""),
        })
    return {
        "id": doctrine_id,
        "name": str(doctrine.get("name") or doctrine_id),
        "axis": str(doctrine.get("axis") or ""),
        "level": str(doctrine.get("level") or "basic"),
        "summary": str(doctrine.get("summary") or ""),
        "status": "orthodox",
        "state_label": "基本国策",
        "legacy_id": int(_row_value(legacy_row, "id", 0) or 0),
        "legacy_key": legacy_key,
        "narrative_hint": str(_row_value(legacy_row, "narrative_hint", "") or ""),
        "conflicts": conflicts,
        "legacy_effects": dict(doctrine.get("legacy_effects") or {}),
    }


def doctrine_issue_payload(db: GameDB, issue_row) -> Dict[str, object]:
    """Single doctrine issue payload for web surfaces.

    Keep route visibility in one place: issue cards, future briefings, and
    diagnostics should not each recalculate blockers and reform readiness.
    """

    if issue_row is None or str(issue_row["origin_kind"] or "") != "doctrine":
        return {}
    doctrine_id = str(issue_row["origin_ref"] or "")
    doctrine = doctrine_by_id(doctrine_id) or {}
    if not doctrine:
        return {}
    bar_value = int(issue_row["bar_value"] or 0)
    cap = int((load_policy_doctrines().get("blocked_issue_bar_cap") or 95))
    blockers = doctrine_establishment_blockers(db, doctrine_id)
    reform_ready = bool(blockers and bar_value >= cap)
    state_label = "可改弦" if reform_ready else "正统受阻" if blockers else "路线争议"
    reform_hint = ""
    if reform_ready:
        reform_hint = "准奏支持此路线的奏疏，可改弦更张，使相冲旧策退场。"
    elif blockers:
        names = "、".join(str(item.get("name") or item.get("id")) for item in blockers[:3])
        reform_hint = f"与既定基本国策「{names}」相抵牾；须先把此路线推至待定策。"
    alignment = doctrine_alignment_summary(db, doctrine_id)
    return {
        "id": doctrine_id,
        "name": str(doctrine.get("name") or doctrine_id),
        "axis": str(doctrine.get("axis") or ""),
        "level": str(doctrine.get("level") or "basic"),
        "bar_value": bar_value,
        "phase": str(issue_row["phase"] or ""),
        "summary": str(doctrine.get("summary") or ""),
        "state_label": state_label,
        "blocked_bar_cap": cap,
        "establishment_blocked": bool(blockers),
        "reform_ready": reform_ready,
        "reform_hint": reform_hint,
        "active_conflicts": blockers,
        "establishment_blockers": blockers,
        "factions": alignment.get("factions") or [],
        "figures": alignment.get("figures") or [],
    }


def doctrine_memorial_payload(db: GameDB, memorial_row) -> Dict[str, object]:
    """Single payload for memorials attached to a doctrine issue."""

    if str(_row_value(memorial_row, "ref_kind", "") or "") != "issue":
        return {}
    ref_id = str(_row_value(memorial_row, "ref_id", "") or "").strip()
    if not ref_id:
        return {}
    issue = db.conn.execute("SELECT * FROM issues WHERE id=?", (ref_id,)).fetchone()
    if issue is None or str(issue["origin_kind"] or "") != "doctrine":
        return {}
    payload = doctrine_issue_payload(db, issue)
    if not payload:
        return {}
    kind = str(_row_value(memorial_row, "kind", "") or "")
    direction = "oppose" if kind == "弹章" else "support"
    author = str(_row_value(memorial_row, "author_name", "") or "")
    payload.update({
        "issue_id": int(issue["id"]),
        "direction": direction,
        "direction_label": "反对此路线" if direction == "oppose" else "推动此路线",
        "author_stance": character_doctrine_stance(db, author, str(payload.get("id") or "")) if author else {},
    })
    return payload


def detect_tax_burden_policy(text: str) -> Optional[Dict[str, object]]:
    """Return a compact policy spec when decree text clearly increases taxes."""

    decree = str(text or "")
    if not decree or _TAX_RELIEF_RE.search(decree):
        return None
    if not _TAX_INCREASE_RE.search(decree):
        return None
    stem = _detect_tax_stem(decree)
    if not stem:
        return None
    permanent = bool(_PERMANENT_RE.search(decree))
    severity = 12 if permanent else 8
    if re.search(r"重|倍|大加|严征|急征|军需|辽东|边饷|补饷", decree):
        severity += 4
    return {
        "stem": stem,
        "permanent": permanent,
        "duration_months": -1 if permanent else 36,
        "severity": min(20, severity),
    }


def apply_directive_policy_legacies(
    db: GameDB,
    state: GameState,
    *,
    directive_id: int,
    text: str,
    extracted: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Apply deterministic long-tail effects for completed policy decrees.

    Returns a small summary that can be attached to the drain result.  It is
    idempotent by ``legacy_key`` and safe to call for every extracted directive.
    """

    spec = detect_tax_burden_policy(text)
    if not spec:
        return {}
    stem = str(spec["stem"])
    legacy_key = f"directive_tax:{int(directive_id)}:{stem}"
    existing = db.conn.execute(
        "SELECT id FROM legacies WHERE legacy_key=? AND status='active' LIMIT 1",
        (legacy_key,),
    ).fetchone()
    if existing is not None:
        return {"legacy_id": int(existing["id"]), "stem": stem, "duplicate": True}

    if _extraction_already_touched_tax(stem, extracted or {}):
        fiscal = {"changed": False, "reason": "already_extracted", "stem": stem}
    else:
        fiscal = _increase_tax_config(db, stem, int(spec["severity"]))
    duration = int(spec["duration_months"])
    severity = int(spec["severity"])
    name = f"苛税余波：{stem}"
    duration_text = "已入常例" if duration < 0 else "三年内难消"
    modifiers = {"民心": -max(4, min(18, severity // 2 + 3))}
    legacy_id = db.insert_legacy(
        state,
        name=name,
        modifiers=modifiers,
        narrative_hint=f"旨意#{int(directive_id)}加重{stem}，{duration_text}；钱粮见长，民心恢复受压。",
        duration_months=duration,
        legacy_key=legacy_key,
    )
    try:
        db.record_log(
            state,
            f"【长期税负】旨意#{int(directive_id)}加重{stem}，"
            f"{'永久' if duration < 0 else str(duration) + '月'}生效。",
        )
    except Exception:
        pass
    return {
        "legacy_id": legacy_id,
        "stem": stem,
        "duration_months": duration,
        "modifiers": modifiers,
        "fiscal": fiscal,
    }


def _detect_tax_stem(text: str) -> str:
    for stem in _TAX_STEMS:
        if stem in text:
            return stem
    if re.search(r"边饷|军饷|辽东|关宁", text):
        return "辽饷"
    if re.search(r"商贾|关税|榷关|市舶|商旅", text):
        return "商税"
    if re.search(r"盐课|盐引|盐法", text):
        return "盐税"
    if re.search(r"矿|矿课|矿监", text):
        return "矿税"
    if re.search(r"田亩|清丈|粮户|地丁|田税|赋役|民田", text):
        return "田赋"
    return "田赋" if re.search(r"税|赋|课|派", text) else ""


def _extraction_already_touched_tax(stem: str, extracted: Dict[str, object]) -> bool:
    if not isinstance(extracted, dict):
        return False
    keys: list[str] = []
    for block_name in ("fiscal_changes", "fiscal_creates", "fiscal_removes"):
        block = extracted.get(block_name)
        if not isinstance(block, list):
            continue
        for item in block:
            if isinstance(item, dict) and item.get("key"):
                keys.append(str(item.get("key") or ""))
    return any(_stem_of_key(key) == stem for key in keys)


def _stem_of_key(key: str) -> str:
    text = str(key or "")
    if text.endswith("_base") or text.endswith("_rate"):
        return text[:-5]
    return text


def _increase_tax_config(db: GameDB, stem: str, severity: int) -> Dict[str, object]:
    cfg = db.get_fiscal_config()
    if stem == "田赋":
        key = "田赋_rate"
        current = int(cfg.get(key) or 0)
        delta = max(4, min(12, severity // 2))
    else:
        key = f"{stem}_base"
        current = int(cfg.get(key) or 0)
        delta = max(1, round(max(current, 10) * min(0.18, 0.08 + severity / 200.0)))
    if current <= 0 and key not in cfg:
        return {"key": key, "changed": False, "reason": "missing_fiscal_key"}
    new_value = max(0, current + delta)
    db.set_fiscal_config(key, new_value)
    ratio = (new_value / current) if current > 0 else 1.0
    touched = 0
    if stem == "田赋":
        touched = db.scale_tian_fu(ratio)
    elif stem in db._DYNAMIC_REGION_FIELD:
        touched = db.apply_dynamic_fiscal_scale(stem, ratio)
    return {
        "key": key,
        "old": current,
        "new": new_value,
        "delta": delta,
        "region_rows": touched,
    }


def policy_legacy_effect_labels(row) -> list[Dict[str, str]]:
    """Small display helper for active policy legacies."""

    try:
        modifiers = json.loads(str(row["modifiers"] or "{}"))
    except Exception:
        modifiers = {}
    effects: list[Dict[str, str]] = []
    for key, value in modifiers.items():
        if key not in {"民心", "皇威", "国库", "内库"}:
            continue
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if not n:
            continue
        tone = "good" if n > 0 else "bad"
        effects.append({"kind": "legacy", "label": f"{key} {n:+d}%", "tone": tone})
    try:
        dur = int(row["duration_months"])
    except (TypeError, ValueError):
        dur = 0
    effects.append({"kind": "duration", "label": "永久" if dur < 0 else f"{dur}月", "tone": "bad" if dur < 0 else "neutral"})
    return effects
