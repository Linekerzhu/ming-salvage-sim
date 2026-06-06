"""Xinpan dynamic NPC influence layer.

The Tiangang table is a static personality/ability genome. Xinpan is the
runtime relationship layer: how each NPC currently reads the emperor.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from ming_sim.models import Character, GameState


POLITICAL_DIM_IDS = tuple(f"d{i:02d}" for i in range(1, 21))
PROFESSIONAL_DIM_IDS = tuple(f"d{i:02d}" for i in range(21, 37))

QUADRANT_GUGONG = "股肱"
QUADRANT_QUANFU = "权附"
QUADRANT_DAOYIN = "道隐"
QUADRANT_LIXIN = "离心"

XINPAN_MODEL_VERSION = 2
DAO_QUADRANT_CUTOFF = 15.0
SHI_QUADRANT_CUTOFF = 15.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _loads(raw: object, fallback: object) -> object:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        data = json.loads(str(raw or ""))
    except (TypeError, ValueError):
        return fallback
    return data if isinstance(data, type(fallback)) else fallback


def _dumps(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _character_from_db(db: Any, name: str) -> Optional[Character]:
    character = getattr(db.content, "characters", {}).get(name)
    if character is None:
        return None
    row = db.conn.execute(
        """
        SELECT office, office_type, faction, status, power_id, location
        FROM characters WHERE name=?
        """,
        (name,),
    ).fetchone()
    if row is None:
        return character
    merged = replace(character)
    merged.office = str(row["office"] or character.office)
    merged.office_type = str(row["office_type"] or character.office_type)
    merged.faction = str(row["faction"] or character.faction)
    merged.status = str(row["status"] or character.status)
    merged.power_id = str(row["power_id"] or character.power_id or "ming")
    merged.location = str(row["location"] or character.location)
    return merged


def _tiangang_entry(name: str) -> Dict[str, object]:
    # Delayed import avoids a module cycle with context.py -> db.py.
    from ming_sim.context import _effective_tiangang_entry

    entry = _effective_tiangang_entry(name)
    return entry if isinstance(entry, dict) else {}


def _dimension_map() -> Dict[str, Dict[str, object]]:
    from ming_sim.context import _tiangang_dimension_map

    return _tiangang_dimension_map()


def _values_for(name: str) -> Dict[str, int]:
    entry = _tiangang_entry(name)
    values = entry.get("values")
    if not isinstance(values, dict):
        return {}
    out: Dict[str, int] = {}
    for dim_id, raw in values.items():
        try:
            out[str(dim_id)] = int(raw)
        except (TypeError, ValueError):
            continue
    return out


def _identity_text(character: Character) -> str:
    return f"{character.office} {character.office_type} {character.faction} {character.style}"


def _is_inner(character: Character) -> bool:
    text = _identity_text(character)
    return bool(
        character.office_type in {"司礼监", "东厂", "内廷"}
        or character.faction in {"内廷", "皇党"}
        or re.search(r"太监|宦官|内官|司礼监|东厂|内廷|宫禁", text)
    )


def _forced_concern_dims(character: Character) -> List[str]:
    text = _identity_text(character)
    faction = character.faction
    dims: List[str] = []
    if _is_inner(character):
        dims += ["d02", "d03", "d04", "d10", "d06"]
    if faction in {"阉党", "内廷"} or "魏忠贤" in text:
        dims += ["d08", "d07", "d12", "d06"]
    if faction in {"清流", "东林", "东林党"} or re.search(r"言官|御史|翰林|士林|清议", text):
        dims += ["d01", "d11", "d13", "d20", "d03", "d04"]
    if character.office_type in {"边镇", "兵部"} or re.search(r"督师|总兵|经略|辽东|关宁|军", text):
        dims += ["d09", "d17", "d06"]
    if character.office_type == "户部" or re.search(r"户部|税|盐|商|钱粮|财政", text):
        dims += ["d19", "d11", "d20"]
    # Everyone has some loyalty/material axis, but it should not crowd out
    # genuinely extreme ideology.
    dims += ["d06", "d11", "d12"]
    seen: set[str] = set()
    ordered: List[str] = []
    for dim in dims:
        if dim not in seen:
            seen.add(dim)
            ordered.append(dim)
    return ordered


def build_core_concerns(character: Character, values: Dict[str, int]) -> List[Dict[str, object]]:
    dim_map = _dimension_map()
    scores: Dict[str, float] = {}
    reasons: Dict[str, str] = {}

    for dim_id in POLITICAL_DIM_IDS:
        value = int(values.get(dim_id, 3) or 3)
        extremity = abs(value - 3)
        if extremity >= 2:
            scores[dim_id] = max(scores.get(dim_id, 0.0), 1.4)
            reasons[dim_id] = "天罡极值"
        elif extremity == 1:
            scores[dim_id] = max(scores.get(dim_id, 0.0), 0.65)
            reasons.setdefault(dim_id, "天罡偏向")

    for index, dim_id in enumerate(_forced_concern_dims(character)):
        if dim_id not in POLITICAL_DIM_IDS:
            continue
        scores[dim_id] = max(scores.get(dim_id, 0.0), 1.15 - min(index, 6) * 0.08)
        reasons.setdefault(dim_id, "身份/派系关切")

    ranked = sorted(scores.items(), key=lambda item: (item[1], abs(int(values.get(item[0], 3)) - 3)), reverse=True)
    chosen = ranked[:5] or [("d06", 1.0), ("d11", 1.0), ("d12", 1.0)]
    total = sum(score for _dim, score in chosen) or 1.0
    concerns: List[Dict[str, object]] = []
    for dim_id, score in chosen:
        dim = dim_map.get(dim_id, {})
        concerns.append({
            "dim_id": dim_id,
            "symbol": str(dim.get("symbol") or dim_id),
            "name": str(dim.get("name") or dim_id),
            "npc_value": max(1, min(5, int(values.get(dim_id, 3) or 3))),
            "weight": round(float(score) / total, 4),
            "reason": reasons.get(dim_id, "核心关切"),
        })
    return concerns


def initial_perception(character: Character, values: Dict[str, int]) -> Dict[str, float]:
    perception: Dict[str, float] = {dim_id: 3.0 for dim_id in POLITICAL_DIM_IDS}
    faction = character.faction
    text = _identity_text(character)

    if _is_inner(character):
        perception.update({"d02": 4.5, "d03": 4.0, "d04": 4.0, "d10": 4.0, "d06": 3.0})
    if faction in {"阉党", "内廷"}:
        perception.update({"d08": 1.5, "d07": 2.0, "d12": 2.5, "d06": 2.5})
    if faction in {"清流", "东林", "东林党"} or re.search(r"言官|御史|翰林|士林|清议", text):
        perception.update({"d01": 2.0, "d11": 2.0, "d13": 2.0, "d20": 2.0, "d03": 2.0, "d04": 2.0})
    if character.office_type in {"边镇", "兵部"} or re.search(r"督师|总兵|经略|辽东|关宁|军", text):
        perception.update({"d09": 3.5, "d17": 2.0})

    return {dim: _clamp(value, 1.0, 5.0) for dim, value in perception.items()}


def compute_dao_he(values: Dict[str, int], concerns: List[Dict[str, object]], perception: Dict[str, float]) -> float:
    if not concerns:
        return 0.0
    raw = 0.0
    weight_total = 0.0
    for concern in concerns:
        dim_id = str(concern.get("dim_id") or "")
        if dim_id not in POLITICAL_DIM_IDS:
            continue
        try:
            npc_value = int(concern.get("npc_value") or values.get(dim_id, 3) or 3)
            perceived = float(perception.get(dim_id, 3.0) or 3.0)
            weight = float(concern.get("weight") or 0)
        except (TypeError, ValueError):
            continue
        score = 1.0 - abs(float(npc_value) - perceived) / 4.0
        raw += weight * _clamp(score, 0.0, 1.0)
        weight_total += weight
    if weight_total <= 0:
        return 0.0
    return round(_clamp(((raw / weight_total) * 100.0 - 50.0) * 2.0, -100.0, 100.0), 1)


def initial_shi_he(character: Character) -> float:
    faction = character.faction
    office = _identity_text(character)
    if getattr(character, "power_id", "ming",) != "ming":
        base = -30.0
    elif _is_inner(character):
        base = 42.0
    elif faction == "皇党":
        base = 35.0
    elif faction in {"阉党", "内廷"}:
        base = 18.0
    elif faction in {"清流", "东林", "东林党"}:
        base = 5.0
    elif re.search(r"总兵|督师|经略|边镇|军", office):
        base = 12.0
    else:
        base = 8.0
    base += (int(getattr(character, "loyalty", 50) or 50) - 60) / 5.0
    status = getattr(character, "status", "active")
    if status in {"dismissed", "imprisoned", "exiled", "retired"}:
        base -= 25
    if status == "dead":
        base -= 60
    return round(_clamp(base, -100.0, 100.0), 1)


def quadrant_for(dao_he: float, shi_he: float) -> str:
    if dao_he >= DAO_QUADRANT_CUTOFF and shi_he >= SHI_QUADRANT_CUTOFF:
        return QUADRANT_GUGONG
    if dao_he < DAO_QUADRANT_CUTOFF and shi_he >= SHI_QUADRANT_CUTOFF:
        return QUADRANT_QUANFU
    if dao_he >= DAO_QUADRANT_CUTOFF and shi_he < SHI_QUADRANT_CUTOFF:
        return QUADRANT_DAOYIN
    return QUADRANT_LIXIN


def patience_threshold(values: Dict[str, int]) -> int:
    yi_li = max(1, min(5, int(values.get("d11", 3) or 3)))
    return -(20 + (5 - yi_li) * 15)


def initial_state_for(db: Any, state: Optional[GameState], name: str) -> Optional[Dict[str, object]]:
    character = _character_from_db(db, name)
    if character is None:
        return None
    values = _values_for(name)
    if not values:
        return None
    concerns = build_core_concerns(character, values)
    perception = initial_perception(character, values)
    dao_he = compute_dao_he(values, concerns, perception)
    shi_he = initial_shi_he(character)
    fear = _clamp((int((state.metrics or {}).get("皇威", 20)) if state else 20) * 0.45, 5, 45)
    quadrant = quadrant_for(dao_he, shi_he)
    hatred = 4.0 if quadrant == QUADRANT_LIXIN else 0.0
    return {
        "character_name": name,
        "dao_he": dao_he,
        "shi_he": shi_he,
        "fear": round(fear, 1),
        "trust_coeff": 1.0,
        "hatred": hatred,
        "quadrant": quadrant,
        "core_concerns_json": _dumps(concerns),
        "perception_json": _dumps(perception),
        "flags_json": _dumps({"model_version": XINPAN_MODEL_VERSION}),
        "updated_turn": int(state.turn if state else 0),
    }


def _maybe_upgrade_initial_state(
    db: Any,
    state: Optional[GameState],
    name: str,
    row: Dict[str, object],
) -> Dict[str, object]:
    flags = _loads(row.get("flags_json"), {})
    version = int(flags.get("model_version") or 0) if isinstance(flags, dict) else 0
    if version >= XINPAN_MODEL_VERSION:
        return row
    logs = db.conn.execute(
        "SELECT 1 FROM xinpan_logs WHERE character_name=? LIMIT 1",
        (name,),
    ).fetchone()
    if logs is not None:
        merged_flags = flags if isinstance(flags, dict) else {}
        merged_flags["model_version"] = XINPAN_MODEL_VERSION
        db.conn.execute(
            "UPDATE xinpan_states SET flags_json=?, updated_at=CURRENT_TIMESTAMP WHERE character_name=?",
            (_dumps(merged_flags), name),
        )
        db.conn.commit()
        updated = dict(row)
        updated["flags_json"] = _dumps(merged_flags)
        return updated
    initial = initial_state_for(db, state, name)
    if initial is None:
        return row
    db.conn.execute(
        """
        UPDATE xinpan_states
        SET dao_he=?, shi_he=?, fear=?, trust_coeff=?, hatred=?, quadrant=?,
            core_concerns_json=?, perception_json=?, flags_json=?,
            updated_turn=?, updated_at=CURRENT_TIMESTAMP
        WHERE character_name=?
        """,
        (
            initial["dao_he"], initial["shi_he"], initial["fear"], initial["trust_coeff"],
            initial["hatred"], initial["quadrant"], initial["core_concerns_json"],
            initial["perception_json"], initial["flags_json"], initial["updated_turn"], name,
        ),
    )
    db.conn.commit()
    return dict(initial)


def ensure_xinpan_state(db: Any, state: Optional[GameState], name: str) -> Optional[Dict[str, object]]:
    clean = str(name or "").strip()
    if not clean:
        return None
    row = db.conn.execute("SELECT * FROM xinpan_states WHERE character_name=?", (clean,)).fetchone()
    if row is not None:
        return _maybe_upgrade_initial_state(db, state, clean, dict(row))
    initial = initial_state_for(db, state, clean)
    if initial is None:
        return None
    db.conn.execute(
        """
        INSERT INTO xinpan_states
            (character_name, dao_he, shi_he, fear, trust_coeff, hatred, quadrant,
             core_concerns_json, perception_json, flags_json, updated_turn)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            initial["character_name"], initial["dao_he"], initial["shi_he"], initial["fear"],
            initial["trust_coeff"], initial["hatred"], initial["quadrant"],
            initial["core_concerns_json"], initial["perception_json"],
            initial["flags_json"], initial["updated_turn"],
        ),
    )
    db.conn.commit()
    return dict(initial)


def ensure_all_xinpan_states(db: Any, state: Optional[GameState]) -> int:
    count = 0
    rows = db.conn.execute("SELECT name FROM characters").fetchall()
    for row in rows:
        before = db.conn.execute(
            "SELECT 1 FROM xinpan_states WHERE character_name=?",
            (str(row["name"]),),
        ).fetchone()
        ensure_xinpan_state(db, state, str(row["name"]))
        if before is None:
            count += 1
    return count


def _row_state(row: Dict[str, Any]) -> Dict[str, object]:
    return {
        "character_name": str(row.get("character_name") or ""),
        "dao_he": float(row.get("dao_he") or 0),
        "shi_he": float(row.get("shi_he") or 0),
        "fear": float(row.get("fear") or 0),
        "trust_coeff": float(row.get("trust_coeff") or 1.0),
        "hatred": float(row.get("hatred") or 0),
        "quadrant": str(row.get("quadrant") or ""),
        "core_concerns": _loads(row.get("core_concerns_json"), []),
        "perception": _loads(row.get("perception_json"), {}),
        "flags": _loads(row.get("flags_json"), {}),
        "updated_turn": int(row.get("updated_turn") or 0),
    }


def _persist_state(db: Any, state: GameState, item: Dict[str, object]) -> None:
    db.conn.execute(
        """
        UPDATE xinpan_states
        SET dao_he=?, shi_he=?, fear=?, trust_coeff=?, hatred=?, quadrant=?,
            core_concerns_json=?, perception_json=?, flags_json=?,
            updated_turn=?, updated_at=CURRENT_TIMESTAMP
        WHERE character_name=?
        """,
        (
            float(item["dao_he"]), float(item["shi_he"]), float(item["fear"]),
            float(item["trust_coeff"]), float(item["hatred"]), str(item["quadrant"]),
            _dumps(item.get("core_concerns") or []),
            _dumps(item.get("perception") or {}),
            _dumps(item.get("flags") or {}),
            int(state.turn),
            str(item["character_name"]),
        ),
    )


def _log_change(
    db: Any,
    state: GameState,
    name: str,
    source_kind: str,
    source_id: str,
    event: str,
    before: Dict[str, object],
    after: Dict[str, object],
) -> None:
    def delta(key: str) -> float:
        return round(float(after.get(key) or 0) - float(before.get(key) or 0), 2)

    db.conn.execute(
        """
        INSERT INTO xinpan_logs
            (turn, year, period, character_name, source_kind, source_id, event,
             dao_delta, shi_delta, fear_delta, hatred_delta, trust_delta,
             before_json, after_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(state.turn), int(state.year), int(state.period), name,
            source_kind[:40], source_id[:80], event[:160],
            delta("dao_he"), delta("shi_he"), delta("fear"),
            delta("hatred"), delta("trust_coeff"),
            _dumps(_compact_state_for_log(before)),
            _dumps(_compact_state_for_log(after)),
        ),
    )


def _compact_state_for_log(item: Dict[str, object]) -> Dict[str, object]:
    return {
        "dao_he": round(float(item.get("dao_he") or 0), 1),
        "shi_he": round(float(item.get("shi_he") or 0), 1),
        "fear": round(float(item.get("fear") or 0), 1),
        "trust_coeff": round(float(item.get("trust_coeff") or 1), 3),
        "hatred": round(float(item.get("hatred") or 0), 1),
        "quadrant": str(item.get("quadrant") or ""),
    }


def _profile_trajectory(
    db: Any,
    state: Optional[GameState],
    name: str,
    item: Dict[str, object],
    *,
    limit: int = 10,
) -> List[Dict[str, object]]:
    logs = db.conn.execute(
        """
        SELECT id, turn, source_kind, event, before_json, after_json,
               dao_delta, shi_delta, fear_delta, hatred_delta, trust_delta
        FROM xinpan_logs
        WHERE character_name=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (name, max(1, min(30, int(limit or 10)))),
    ).fetchall()
    points: List[Dict[str, object]] = []

    def append_point(
        raw: object,
        *,
        turn: int,
        event: str,
        source_kind: str,
        deltas: Optional[Dict[str, object]] = None,
    ) -> None:
        data = _loads(raw, {})
        if not isinstance(data, dict):
            return
        point = {
            "turn": int(turn),
            "dao_he": round(float(data.get("dao_he") or 0), 1),
            "shi_he": round(float(data.get("shi_he") or 0), 1),
            "fear": round(float(data.get("fear") or 0), 1),
            "hatred": round(float(data.get("hatred") or 0), 1),
            "trust_coeff": round(float(data.get("trust_coeff") or 1), 3),
            "quadrant": str(data.get("quadrant") or quadrant_for(float(data.get("dao_he") or 0), float(data.get("shi_he") or 0))),
            "event": event,
            "source_kind": source_kind,
        }
        if deltas is not None:
            point.update({
                "has_delta": True,
                "dao_delta": round(float(deltas.get("dao_delta") or 0), 1),
                "shi_delta": round(float(deltas.get("shi_delta") or 0), 1),
                "fear_delta": round(float(deltas.get("fear_delta") or 0), 1),
                "hatred_delta": round(float(deltas.get("hatred_delta") or 0), 1),
                "trust_delta": round(float(deltas.get("trust_delta") or 0), 3),
            })
        if points and points[-1]["dao_he"] == point["dao_he"] and points[-1]["shi_he"] == point["shi_he"]:
            if source_kind == "current":
                return
        points.append(point)

    ordered = list(reversed(logs))
    if ordered:
        first = ordered[0]
        append_point(
            first["before_json"],
            turn=int(first["turn"] or 0),
            event="初始点",
            source_kind=str(first["source_kind"] or ""),
        )
        for row in ordered:
            append_point(
                row["after_json"],
                turn=int(row["turn"] or 0),
                event=str(row["event"] or ""),
                source_kind=str(row["source_kind"] or ""),
                deltas={
                    "dao_delta": row["dao_delta"],
                    "shi_delta": row["shi_delta"],
                    "fear_delta": row["fear_delta"],
                    "hatred_delta": row["hatred_delta"],
                    "trust_delta": row["trust_delta"],
                },
            )

    current = _compact_state_for_log(item)
    append_point(
        current,
        turn=int(item.get("updated_turn") or (state.turn if state else 0) or 0),
        event="当前",
        source_kind="current",
    )
    return points[-12:]


_SIGNAL_PATTERNS: Tuple[Tuple[str, float, str], ...] = (
    ("d01", 1.0, r"王道|仁政|德政|以民为本|百姓.*根本|不可竭泽而渔"),
    ("d01", 3.5, r"霸道|强征|权宜强推|乱世用重典"),
    ("d02", 5.0, r"乾纲独断|朕意|朕自决|皇权|君父|独断|不容置喙"),
    ("d02", 2.0, r"共治|公议|会推|廷议共定|与廷臣共商"),
    ("d03", 5.0, r"内廷.*主|司礼监.*主|内官.*办|宫中.*总摄"),
    ("d03", 1.5, r"限制内廷|裁内廷|内廷不得|内官不得干政"),
    ("d04", 5.0, r"厂卫.*耳目|东厂.*不可废|锦衣卫.*严查|密缉|厂卫.*严办"),
    ("d04", 1.5, r"裁撤东厂|裁厂卫|约束厂卫|禁厂卫|厂卫之弊"),
    ("d05", 1.5, r"变法|革新|新政|改制|开海|西法|火器新法"),
    ("d05", 4.5, r"祖制不可变|守成|复旧|祖宗成法|不得轻改"),
    ("d06", 2.0, r"社稷为重|国家为重|公义为先|不可因私废公"),
    ("d06", 3.0, r"忠于朕|奉朕|为朕所用|朕之臣"),
    ("d06", 5.0, r"自保|各寻退路|利害自择|不必讲虚义"),
    ("d07", 1.5, r"破朋党|禁朋党|党争误国|不许结党"),
    ("d07", 4.5, r"联络同党|借.*党|经营门生|结援"),
    ("d08", 1.0, r"清算先帝|追论魏忠贤|逆案|阉党余孽|清除旧党"),
    ("d08", 3.0, r"既往不咎|旧事不追|前朝旧人.*可用"),
    ("d08", 5.0, r"恢复先帝旧制|重用魏党|翻案"),
    ("d09", 1.5, r"文臣主议|崇文|以文制武"),
    ("d09", 4.5, r"重用武臣|尚武|边将自主|将帅专任"),
    ("d10", 1.5, r"外廷为主|归外廷|不得由内廷|六部主办"),
    ("d10", 4.5, r"内廷为主|以内制外|司礼监票拟|内外制衡"),
    ("d11", 1.0, r"舍利取义|义不可夺|名节|宁损财利|道义为先"),
    ("d11", 4.5, r"以利诱|利害为先|重赏之下|价码|交易"),
    ("d12", 1.5, r"光明正大|堂堂正正|公论自明|不设私谋"),
    ("d12", 4.5, r"权术|暗中|设局|钓出|借刀|离间|不择手段"),
    ("d13", 1.0, r"广开言路|许言官|容谏|纳谏|不罪言者"),
    ("d13", 4.5, r"禁言|钳口|言官.*空谈|压制言路|堵言路"),
    ("d14", 1.5, r"宽典|宽恕|赦免|从轻|教化"),
    ("d14", 4.5, r"重典|严刑|廷杖|下狱|诛|抄家|赐死"),
    ("d15", 1.5, r"礼法|名分|祖礼|纲常|体统"),
    ("d15", 4.5, r"权宜变通|礼可暂缓|不拘礼法|破格"),
    ("d17", 1.5, r"主动出击|北伐|毕其功于一役|出塞|决战"),
    ("d17", 4.0, r"坚守|避战|守关|固守|缓攻"),
    ("d19", 1.5, r"开海|通商|海贸|商税轻|兴商"),
    ("d19", 4.0, r"禁海|抑商|闭关|商贾逐利"),
    ("d20", 1.0, r"民为邦本|民本|百姓为先|赈民|不可扰民"),
    ("d20", 4.5, r"加征|三饷|摊派|搜括|民力可用"),
)


def infer_stance_signals(text: str) -> Dict[str, float]:
    buckets: Dict[str, List[float]] = {}
    hay = re.sub(r"\s+", "", text or "")
    if not hay:
        return {}
    for dim_id, value, pattern in _SIGNAL_PATTERNS:
        if re.search(pattern, hay):
            buckets.setdefault(dim_id, []).append(float(value))
    return {
        dim_id: round(sum(values) / len(values), 2)
        for dim_id, values in buckets.items()
        if values
    }


def _apply_signals(
    perception: Dict[str, float],
    signals: Dict[str, float],
    *,
    strength: float,
    trust_coeff: float,
) -> Dict[str, float]:
    updated = {dim: float(perception.get(dim, 3.0) or 3.0) for dim in POLITICAL_DIM_IDS}
    multiplier = max(0.25, float(trust_coeff or 1.0))
    for dim_id, target in signals.items():
        if dim_id not in POLITICAL_DIM_IDS:
            continue
        old = float(updated.get(dim_id, 3.0))
        distance = float(target) - old
        if abs(distance) < 0.01:
            continue
        step = min(abs(distance), strength * multiplier)
        updated[dim_id] = round(_clamp(old + (step if distance > 0 else -step), 1.0, 5.0), 2)
    return updated


def _refresh_dao_and_quadrant(name: str, item: Dict[str, object]) -> None:
    values = _values_for(name)
    concerns = item.get("core_concerns")
    if not isinstance(concerns, list) or not concerns:
        character = None
        # Caller should have seeded concerns, so this path is only a guard.
        item["core_concerns"] = []
    perception = item.get("perception") if isinstance(item.get("perception"), dict) else {}
    item["dao_he"] = compute_dao_he(values, item.get("core_concerns") or [], perception)  # type: ignore[arg-type]
    item["quadrant"] = quadrant_for(float(item.get("dao_he") or 0), float(item.get("shi_he") or 0))


def apply_chat_xinpan_update(
    db: Any,
    state: GameState,
    minister_name: str,
    user_text: str,
    answer: str,
    *,
    stance: str = "neutral",
    handshake_status: str = "none",
    psychological_score: int = 0,
    source_chat_turn_id: int = 0,
    goal_context: Optional[Dict[str, object]] = None,
) -> Optional[Dict[str, object]]:
    row = ensure_xinpan_state(db, state, minister_name)
    if row is None:
        return None
    item = _row_state(row)
    before = _compact_state_for_log(item)
    signals = infer_stance_signals(user_text)
    if signals:
        item["perception"] = _apply_signals(
            item.get("perception") if isinstance(item.get("perception"), dict) else {},
            signals,
            strength=1.15,
            trust_coeff=float(item.get("trust_coeff") or 1.0),
        )

    shi_delta = 0.0
    if handshake_status == "sealed":
        shi_delta += 3.0 + min(5.0, max(0, int(psychological_score or 0)) / 20.0)
    elif handshake_status == "conditional":
        shi_delta += 1.0
    elif handshake_status == "blocked":
        shi_delta -= 4.0
    elif stance == "support":
        shi_delta += 2.0
    elif stance == "oppose":
        shi_delta -= 3.0
    elif stance == "caution":
        shi_delta -= 0.5

    combined = f"{user_text}\n{answer}"
    if re.search(r"赏|赐|保全|擢|升|重任|委以", user_text):
        shi_delta += 2.5
    if re.search(r"罢|贬|廷杖|下狱|抄家|赐死|诛|问罪|严办", user_text):
        shi_delta -= 5.0
        item["fear"] = _clamp(float(item.get("fear") or 0) + 5.0, 0, 100)
        item["trust_coeff"] = _clamp(float(item.get("trust_coeff") or 1.0) * 0.96, 0.25, 1.0)
    if re.search(r"强旨|不许推辞|必须奉行|若不从", combined):
        item["fear"] = _clamp(float(item.get("fear") or 0) + 2.0, 0, 100)
        shi_delta -= 1.5

    goal_context = goal_context if isinstance(goal_context, dict) else {}
    goal_event = str(goal_context.get("event") or "")
    goal_status = str(goal_context.get("status") or "")
    action_kind = str(goal_context.get("action_kind") or "")
    if goal_event == "sealed":
        shi_delta += 2.0
        item["trust_coeff"] = _clamp(float(item.get("trust_coeff") or 1.0) * 1.02, 0.25, 1.0)
        if action_kind in {"castration", "emancipation"}:
            item["hatred"] = _clamp(float(item.get("hatred") or 0) - 1.0, 0, 100)
    elif goal_event == "conditions_satisfied":
        shi_delta += 5.0
        item["trust_coeff"] = _clamp(float(item.get("trust_coeff") or 1.0) * 1.05, 0.25, 1.0)
        item["hatred"] = _clamp(float(item.get("hatred") or 0) - 2.0, 0, 100)
    elif goal_event == "waiting_conditions":
        shi_delta += 0.5
    elif goal_event == "blocked":
        shi_delta -= 3.0
        item["trust_coeff"] = _clamp(float(item.get("trust_coeff") or 1.0) * 0.96, 0.25, 1.0)
        if goal_context.get("pressure"):
            item["fear"] = _clamp(float(item.get("fear") or 0) + 2.5, 0, 100)
            item["hatred"] = _clamp(float(item.get("hatred") or 0) + 3.0, 0, 100)
    elif goal_event == "abandoned":
        shi_delta -= 1.0
        item["trust_coeff"] = _clamp(float(item.get("trust_coeff") or 1.0) * 0.98, 0.25, 1.0)
    elif goal_event == "switched":
        shi_delta -= 2.0
        item["trust_coeff"] = _clamp(float(item.get("trust_coeff") or 1.0) * 0.95, 0.25, 1.0)
        item["hatred"] = _clamp(float(item.get("hatred") or 0) + 1.5, 0, 100)
    elif goal_status == "waiting_conditions":
        shi_delta += 0.5

    item["shi_he"] = round(_clamp(float(item.get("shi_he") or 0) + shi_delta, -100, 100), 1)
    _refresh_dao_and_quadrant(minister_name, item)
    if item["quadrant"] == QUADRANT_LIXIN and (shi_delta < 0 or float(item["dao_he"]) < float(before["dao_he"])):
        item["hatred"] = _clamp(float(item.get("hatred") or 0) + 1.5 + max(0.0, -shi_delta) / 4.0, 0, 100)

    after = _compact_state_for_log(item)
    if before == after:
        return item
    _persist_state(db, state, item)
    _log_change(
        db, state, minister_name, "chat", str(source_chat_turn_id or ""),
        "私下召对更新心盘感知与势合",
        before, after,
    )
    db.conn.commit()
    return item


def apply_direct_xinpan_adjustment(
    db: Any,
    state: GameState,
    name: str,
    *,
    shi_delta: float = 0.0,
    fear_delta: float = 0.0,
    hatred_delta: float = 0.0,
    trust_multiplier: float = 1.0,
    event: str = "直接处置更新心盘",
    source_kind: str = "direct",
    source_id: str = "",
) -> Optional[Dict[str, object]]:
    row = ensure_xinpan_state(db, state, name)
    if row is None:
        return None
    item = _row_state(row)
    before = _compact_state_for_log(item)
    item["shi_he"] = round(_clamp(float(item.get("shi_he") or 0) + float(shi_delta or 0), -100, 100), 1)
    item["fear"] = round(_clamp(float(item.get("fear") or 0) + float(fear_delta or 0), 0, 100), 1)
    item["hatred"] = round(_clamp(float(item.get("hatred") or 0) + float(hatred_delta or 0), 0, 100), 1)
    item["trust_coeff"] = round(_clamp(float(item.get("trust_coeff") or 1.0) * float(trust_multiplier or 1.0), 0.25, 1.0), 3)
    _refresh_dao_and_quadrant(name, item)
    if item["quadrant"] == QUADRANT_LIXIN and float(shi_delta or 0) < 0:
        item["hatred"] = round(_clamp(float(item.get("hatred") or 0) + min(8.0, abs(float(shi_delta or 0)) / 4.0), 0, 100), 1)

    after = _compact_state_for_log(item)
    if before == after:
        return item
    _persist_state(db, state, item)
    _log_change(
        db, state, name, source_kind, source_id,
        event,
        before, after,
    )
    db.conn.commit()
    return item


def _faction_shi_delta(applied: Dict[str, object], faction: str) -> float:
    if not faction:
        return 0.0
    total = 0.0
    faction_delta = applied.get("faction_delta")
    if isinstance(faction_delta, dict):
        raw = faction_delta.get(faction)
        if isinstance(raw, dict):
            total += float(raw.get("satisfaction") or 0) * 0.35
            total += float(raw.get("leverage") or 0) * 0.18
        else:
            try:
                total += float(raw) * 0.35
            except (TypeError, ValueError):
                pass
    for reaction in applied.get("political_reactions") or []:
        if not isinstance(reaction, dict):
            continue
        delta = reaction.get("faction_delta")
        if not isinstance(delta, dict):
            continue
        raw = delta.get(faction)
        if isinstance(raw, dict):
            total += float(raw.get("satisfaction") or 0) * 0.3
            total += float(raw.get("leverage") or 0) * 0.15
    return total


def _direct_personnel_delta(name: str, applied: Dict[str, object]) -> Tuple[float, float, float, str]:
    shi = 0.0
    fear = 0.0
    hatred = 0.0
    note = ""
    for item in applied.get("office_changes") or []:
        if not isinstance(item, dict) or item.get("rejected"):
            continue
        if str(item.get("name") or "") == name:
            kind = str(item.get("kind") or "")
            forced = bool(item.get("forced"))
            if kind == "castration":
                if forced:
                    shi -= 48.0
                    fear += 24.0
                    hatred += 52.0
                    note = "强旨净身"
                else:
                    shi += 16.0
                    fear -= 2.0
                    hatred -= 3.0
                    note = "自愿净身"
            elif kind == "emancipation":
                if forced:
                    shi -= 36.0
                    fear += 10.0
                    hatred += 58.0
                    note = "强旨脱籍"
                else:
                    shi += 6.0
                    fear -= 2.0
                    note = "自愿脱籍"
            elif kind in {"appoint", "transfer"}:
                old_office = str(item.get("old_office") or "")
                shi += 18.0 if not old_office else 10.0
                note = "人事任用"
    status_impact = {
        "dismissed": (-24.0, 5.0, 10.0),
        "imprisoned": (-38.0, 18.0, 18.0),
        "exiled": (-42.0, 20.0, 18.0),
        "retired": (-12.0, 2.0, 4.0),
        "dead": (-90.0, 35.0, 30.0),
        "offstage": (-8.0, 0.0, 2.0),
    }
    for item in applied.get("character_status_changes") or []:
        if not isinstance(item, dict) or item.get("rejected"):
            continue
        if str(item.get("name") or "") != name:
            continue
        impact = status_impact.get(str(item.get("status") or ""))
        if impact:
            ds, df, dh = impact
            shi += ds
            fear += df
            hatred += dh
            note = "直接处置"
    for item in applied.get("character_power_changes") or []:
        if not isinstance(item, dict) or item.get("rejected"):
            continue
        if str(item.get("name") or "") == name:
            shi -= 22.0
            hatred += 8.0
            note = "人物易主"
    return shi, fear, hatred, note


def apply_turn_xinpan_update(
    db: Any,
    state: GameState,
    decree_text: str,
    narrative: str,
    applied: Dict[str, object],
) -> Dict[str, object]:
    ensure_all_xinpan_states(db, state)
    signals = infer_stance_signals(f"{decree_text}\n{narrative}")
    touched = 0
    quadrant_counts: Dict[str, int] = {}
    rows = db.conn.execute(
        """
        SELECT xs.*, c.faction, c.status
        FROM xinpan_states xs
        JOIN characters c ON c.name = xs.character_name
        WHERE c.status != 'offstage'
        """
    ).fetchall()
    for row in rows:
        name = str(row["character_name"])
        item = _row_state(dict(row))
        before = _compact_state_for_log(item)
        faction = str(row["faction"] or "")

        shi = float(item.get("shi_he") or 0)
        if shi > 0:
            shi -= 2.0
        elif shi < 0:
            shi += min(0.5, abs(shi))
        shi += _faction_shi_delta(applied, faction)

        direct_shi, direct_fear, direct_hatred, direct_note = _direct_personnel_delta(name, applied)
        shi += direct_shi
        item["fear"] = _clamp(float(item.get("fear") or 0) - 1.0 + direct_fear, 0, 100)
        item["hatred"] = _clamp(float(item.get("hatred") or 0) + direct_hatred, 0, 100)
        item["shi_he"] = round(_clamp(shi, -100, 100), 1)

        if signals:
            item["perception"] = _apply_signals(
                item.get("perception") if isinstance(item.get("perception"), dict) else {},
                signals,
                strength=2.35,
                trust_coeff=1.0,
            )
        _refresh_dao_and_quadrant(name, item)
        if item["quadrant"] == QUADRANT_LIXIN:
            item["hatred"] = _clamp(float(item.get("hatred") or 0) + 2.0, 0, 100)

        after = _compact_state_for_log(item)
        quadrant_counts[str(after["quadrant"])] = quadrant_counts.get(str(after["quadrant"]), 0) + 1
        if before == after:
            continue
        event = "公共诏令广播与月末衰减"
        if direct_note:
            event += f"；{direct_note}"
        _persist_state(db, state, item)
        _log_change(db, state, name, "turn", str(state.turn), event, before, after)
        touched += 1
    db.conn.commit()
    return {"updated": touched, "quadrants": quadrant_counts, "signals": signals}


def _top_professional_abilities(values: Dict[str, int], limit: int = 5) -> List[Dict[str, object]]:
    dim_map = _dimension_map()
    rows: List[Tuple[str, int]] = []
    for dim_id in PROFESSIONAL_DIM_IDS:
        value = int(values.get(dim_id, 3) or 3)
        if value >= 4:
            rows.append((dim_id, value))
    rows.sort(key=lambda item: item[1], reverse=True)
    out: List[Dict[str, object]] = []
    for dim_id, value in rows[:limit]:
        dim = dim_map.get(dim_id, {})
        out.append({
            "dim_id": dim_id,
            "symbol": str(dim.get("symbol") or dim_id),
            "name": str(dim.get("name") or dim_id),
            "band": "强" if value == 4 else "极强",
        })
    return out


def _behavior_hint(quadrant: str, abilities: List[Dict[str, object]], fear: float) -> str:
    names = "、".join(f"{a.get('symbol')}{a.get('name')}" for a in abilities[:3]) or "常规官僚手段"
    if quadrant == QUADRANT_GUGONG:
        return f"倾向主动承办、预警和护主；可用强项：{names}。"
    if quadrant == QUADRANT_QUANFU:
        return f"倾向交易式服从，危急时会重新估价；可用强项：{names}。"
    if quadrant == QUADRANT_DAOYIN:
        return f"价值上仍可沟通，但需先解利益/名分困境；可用强项：{names}。"
    restraint = "高畏惧会压低公开反抗，更多转为暗中阻挠。" if fear >= 60 else "畏惧不足时更可能公开叫板。"
    return f"倾向阳奉阴违或结党对抗；{restraint} 可用强项：{names}。"


def public_profile(db: Any, state: Optional[GameState], name: str) -> Dict[str, object]:
    row = ensure_xinpan_state(db, state, name)
    if row is None:
        return {}
    item = _row_state(row)
    values = _values_for(name)
    concerns = item.get("core_concerns") if isinstance(item.get("core_concerns"), list) else []
    perception = item.get("perception") if isinstance(item.get("perception"), dict) else {}
    enriched_concerns = []
    for raw in concerns:
        if not isinstance(raw, dict):
            continue
        dim_id = str(raw.get("dim_id") or "")
        enriched_concerns.append({
            "dim_id": dim_id,
            "symbol": str(raw.get("symbol") or dim_id),
            "name": str(raw.get("name") or dim_id),
            "reason": str(raw.get("reason") or ""),
        })
    abilities = _top_professional_abilities(values)
    dao = float(item.get("dao_he") or 0)
    shi = float(item.get("shi_he") or 0)
    fear = float(item.get("fear") or 0)
    trust = float(item.get("trust_coeff") or 1.0)
    hatred = float(item.get("hatred") or 0)
    quadrant = str(item.get("quadrant") or quadrant_for(dao, shi))
    warnings: List[str] = []
    if quadrant == QUADRANT_DAOYIN and shi <= patience_threshold(values):
        warnings.append("势合已逼近忍耐阈值，道合可能被长期亏待消磨。")
    if hatred >= 80:
        warnings.append("仇恨已入强制对抗区，需防主动破局。")
    elif hatred >= 50:
        warnings.append("仇恨已入预警区，需防暗中联络与蓄谋。")
    return {
        "quadrant": quadrant,
        "dao_he": round(dao, 1),
        "shi_he": round(shi, 1),
        "fear": round(fear, 1),
        "trust_coeff": round(trust, 2),
        "hatred": round(hatred, 1),
        "patience_threshold": patience_threshold(values),
        "dao_cutoff": DAO_QUADRANT_CUTOFF,
        "shi_cutoff": SHI_QUADRANT_CUTOFF,
        "core_concerns": enriched_concerns,
        "top_abilities": abilities,
        "behavior_hint": _behavior_hint(quadrant, abilities, fear),
        "warnings": warnings,
        "trajectory": _profile_trajectory(db, state, name, item),
        "updated_turn": int(item.get("updated_turn") or 0),
    }


def agent_brief(db: Any, state: Optional[GameState], name: str) -> str:
    profile = public_profile(db, state, name)
    if not profile:
        return ""
    concerns = "、".join(
        f"{c.get('symbol')}{c.get('name')}"
        for c in (profile.get("core_concerns") or [])[:5]
        if isinstance(c, dict)
    )
    warnings = "；".join(str(x) for x in (profile.get("warnings") or []) if str(x).strip())
    return "\n".join([
        "心盘关系摘要（主角-NPC动态层；不得向玩家复述感知表原始细项）：",
        f"- 当前象限：{profile.get('quadrant')}（道合{profile.get('dao_he')}，势合{profile.get('shi_he')}）",
        f"- 畏惧/信言/仇恨：{profile.get('fear')} / {profile.get('trust_coeff')} / {profile.get('hatred')}",
        f"- 核心关切：{concerns or '未显著'}",
        f"- 行为倾向：{profile.get('behavior_hint')}",
        (f"- 风险预警：{warnings}" if warnings else ""),
    ]).strip()


def simulator_brief_rows(db: Any, state: Optional[GameState], limit: int = 80) -> List[Dict[str, object]]:
    ensure_all_xinpan_states(db, state)
    rows = db.conn.execute(
        """
        SELECT xs.character_name, xs.dao_he, xs.shi_he, xs.fear, xs.trust_coeff,
               xs.hatred, xs.quadrant, c.office, c.office_type, c.faction, c.status
        FROM xinpan_states xs
        JOIN characters c ON c.name = xs.character_name
        WHERE c.status != 'offstage' AND c.office_type != '后宫'
        ORDER BY
          CASE xs.quadrant WHEN '离心' THEN 0 WHEN '道隐' THEN 1 WHEN '权附' THEN 2 ELSE 3 END,
          ABS(xs.dao_he) + ABS(xs.shi_he) DESC
        LIMIT ?
        """,
        (max(1, min(200, int(limit or 80))),),
    ).fetchall()
    output: List[Dict[str, object]] = []
    for row in rows:
        output.append({
            "name": str(row["character_name"]),
            "office": str(row["office"] or ""),
            "office_type": str(row["office_type"] or ""),
            "faction": str(row["faction"] or ""),
            "status": str(row["status"] or ""),
            "quadrant": str(row["quadrant"] or ""),
            "dao_he": round(float(row["dao_he"] or 0), 1),
            "shi_he": round(float(row["shi_he"] or 0), 1),
            "fear": round(float(row["fear"] or 0), 1),
            "trust_coeff": round(float(row["trust_coeff"] or 1), 2),
            "hatred": round(float(row["hatred"] or 0), 1),
        })
    return output
