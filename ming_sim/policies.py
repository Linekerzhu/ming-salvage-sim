"""Rule-layer policy consequences that should not depend on LLM extraction.

The edict outcome worker can produce rich JSON, but some gameplay contracts are
important enough to have deterministic fallbacks.  A decree that clearly turns
extra taxation into a standing policy should leave a persistent fiscal and
popular-burden trace even when the LLM returns no structured delta.
"""

from __future__ import annotations

import json
import re
from typing import Dict, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState

_TAX_STEMS = ("辽饷", "商税", "盐税", "矿税", "田赋")
_TAX_INCREASE_RE = re.compile(r"加派|加征|增征|增税|加税|加课|添派|摊派|征收|常例|常税")
_TAX_RELIEF_RE = re.compile(r"减免|蠲免|宽免|罢征|停征|减税|裁撤|罢税|豁免|停派|免派")
_PERMANENT_RE = re.compile(r"永久|永远|永为|常例|常税|定额|恒久|岁额|照旧征解|永著为例")


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
