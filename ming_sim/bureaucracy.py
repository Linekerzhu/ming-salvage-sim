"""Bureaucratic readiness and directive execution preflight.

This module keeps the court-organization display and the turn simulator anchored
to the same deterministic evidence: filled seats, key vacancies, overloaded
office holders, actor fitness, current xinpan, and faction posture.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ming_sim.db import effective_stored_office_type, normalize_office


InstitutionSpec = Dict[str, Any]
SlotSpec = Dict[str, Any]


def slot_spec(
    title: str,
    office_type: str = "",
    count: int = 1,
    *,
    match_terms: Optional[List[str]] = None,
    match_regex: str = "",
    open_pool: bool = False,
    office_types: Optional[List[str]] = None,
    office_type_only: bool = False,
    match_hint: str = "",
    weight: int = 0,
) -> SlotSpec:
    return {
        "title": title,
        "office_type": office_type,
        "office_types": office_types or ([office_type] if office_type else []),
        "count": count,
        "match_terms": match_terms or [title],
        "match_regex": match_regex,
        "open_pool": open_pool,
        "office_type_only": office_type_only,
        "match_hint": match_hint,
        "weight": int(weight or _default_slot_weight(title, office_type)),
    }


def base_institution_specs() -> List[InstitutionSpec]:
    ministries = [
        ("吏部", "铨选、考功、封勋，直接影响官员出仕与升黜。", ["personnel", "procedure"]),
        ("户部", "钱粮、户籍、田赋、仓储，是国库与地方财政接口。", ["fiscal", "local"]),
        ("礼部", "礼制、科举、册封、外交名分。", ["procedure", "diplomacy"]),
        ("兵部", "军政、调兵、武选、边镇题奏。", ["military", "personnel"]),
        ("刑部", "刑名、审覆、法司会审。", ["law", "investigation"]),
        ("工部", "营造、河工、军器与大型工程。", ["construction", "military"]),
    ]
    specs: List[InstitutionSpec] = [
        {
            "id": "cabinet",
            "name": "内阁",
            "category": "朝堂",
            "domains": ["procedure", "coordination"],
            "mandate": "票拟、辅政、总摄六部题本，是外朝政务中枢。",
            "slots": [
                slot_spec("首辅", "内阁", 1, match_terms=["内阁首辅", "首辅"], weight=10),
                slot_spec("次辅", "内阁", 1, match_terms=["内阁次辅", "次辅"], weight=8),
                slot_spec("大学士", "内阁", 4, match_terms=["大学士", "东阁大学士", "文渊阁大学士"], weight=5),
            ],
        },
        *[
            {
                "id": f"ministry-{name}",
                "name": name,
                "category": "六部",
                "domains": domains,
                "mandate": mandate,
                "slots": [
                    slot_spec(f"{name}尚书", name, 1, match_terms=[f"{name}尚书", f"南京{name}尚书"], weight=9 if name in {"吏部", "户部", "兵部"} else 8),
                    slot_spec(f"{name}侍郎", name, 2, match_regex=rf"{name}.*侍郎|侍郎.*{name}", match_hint="左右侍郎合并显示", weight=5),
                    slot_spec(
                        f"{name}属官",
                        name,
                        3,
                        match_terms=[f"{name}郎中", f"{name}主事", f"{name}给事中", f"{name}员外郎"],
                        match_regex=rf"{name}.*(郎中|主事|给事中|员外郎)",
                        weight=2,
                    ),
                ],
            }
            for name, mandate, domains in ministries
        ],
        {
            "id": "censorate",
            "name": "都察院",
            "category": "朝堂",
            "domains": ["oversight", "procedure", "local"],
            "mandate": "纠弹百官、巡按地方，也是清流、东林与阉党互相攻防的重要战场。",
            "slots": [
                slot_spec("左都御史", "都察院", 1, weight=8),
                slot_spec("右都御史", "都察院", 1, weight=7),
                slot_spec("御史 / 巡按", "都察院", 6, match_terms=["御史", "巡按", "给事中"], match_hint="监察言官合并显示", weight=4),
            ],
        },
        {
            "id": "hanlin",
            "name": "翰林院与詹事府",
            "category": "朝堂",
            "domains": ["procedure", "education"],
            "mandate": "翰林、詹事与讲官掌文翰、经筵、史册和储辅，是清议声望与文书能力的来源。",
            "slots": [
                slot_spec("少詹事 / 詹事", "翰林院", 2, match_terms=["少詹事", "詹事"], weight=5),
                slot_spec("翰林编检", "翰林院", 6, match_terms=["翰林", "编修", "检讨", "庶吉士", "掌南京翰林院"], weight=3),
                slot_spec("宫廷艺文", "翰林院", 1, match_terms=["宫廷乐师", "乐师", "讲官"], weight=2),
            ],
        },
        {
            "id": "inner-court",
            "name": "内廷二十四衙门 · 司礼监",
            "category": "内廷",
            "domains": ["inner", "procedure", "investigation"],
            "mandate": "司礼监居二十四衙门之首，掌批红、传旨、内廷文书与皇帝私人执行链。",
            "slots": [
                slot_spec("司礼监掌印太监", "司礼监", 1, match_terms=["司礼监掌印太监", "掌印太监"], weight=10),
                slot_spec("司礼监秉笔太监", "司礼监", 2, match_terms=["司礼监秉笔太监", "秉笔太监"], weight=8),
                slot_spec("司礼监随堂 / 随驾", "司礼监", 4, match_terms=["随堂太监", "随驾", "内官"], match_hint="含信邸旧内官", weight=3),
                slot_spec("监军太监", "司礼监", 3, match_terms=["监军太监", "监军"], weight=5),
                slot_spec("提督东厂", "东厂", 1, match_terms=["东厂提督", "提督东厂"], weight=9),
            ],
        },
        {
            "id": "guards",
            "name": "厂卫",
            "category": "内廷",
            "domains": ["investigation", "inner"],
            "mandate": "东厂、锦衣卫近皇权而行，用于侦缉、密奏、制衡外朝。",
            "slots": [
                slot_spec("锦衣卫指挥使", "锦衣卫", 1, match_regex=r"锦衣卫.*(指挥使|都指挥使)", weight=8),
                slot_spec("北镇抚司", "锦衣卫", 1, match_terms=["北镇抚司", "镇抚司", "理刑"], weight=6),
                slot_spec("锦衣卫缇骑", "锦衣卫", 3, match_terms=["千户", "校尉", "缇骑"], weight=2),
                slot_spec("东厂番役", "东厂", 2, match_terms=["东厂", "掌班", "番役"], weight=2),
            ],
        },
        {
            "id": "frontier",
            "name": "边镇与督师",
            "category": "军务",
            "domains": ["military", "local"],
            "mandate": "督师、总督、总兵与各镇将领承接边防、平寇和军饷压力。",
            "slots": [
                slot_spec("督师 / 经略", "边镇", 3, match_terms=["督师", "经略"], weight=9),
                slot_spec("总督 / 巡抚", "边镇", 4, match_terms=["总督", "巡抚"], weight=7),
                slot_spec("总兵 / 副将", "边镇", 10, match_terms=["总兵", "副总兵", "副将", "游击", "将军", "伯"], weight=4),
                slot_spec("海防与水师", "边镇", 3, match_terms=["东江镇", "福建总兵", "海商", "水师"], weight=5),
            ],
        },
        {
            "id": "local-admin",
            "name": "地方承宣布政使司",
            "category": "地方",
            "domains": ["local", "fiscal", "military"],
            "mandate": "地方三司、府县与督粮道承接中央命令，空缺会削弱诏令落地。",
            "slots": [
                slot_spec("督抚 / 参政", "地方", 6, match_terms=["巡抚", "总督", "督粮", "参政", "布政使", "按察使"], weight=6),
                slot_spec("府县官", "地方", 8, match_terms=["知府", "知县", "同知", "通判"], weight=3),
                slot_spec("地方武备", "地方", 3, match_terms=["总兵", "副将", "海商", "兵备"], weight=4),
            ],
        },
        {
            "id": "talent-pool",
            "name": "待铨与江湖外缘",
            "category": "人才池",
            "domains": ["reserve"],
            "mandate": "待铨、未仕、江湖异人和举贤入京者不占正式官缺，但应被皇帝看见。",
            "slots": [
                slot_spec("待铨 / 未仕", "待铨", 1, office_types=["待铨", "未仕"], open_pool=True, office_type_only=True, match_hint="开放名册，不计空缺", weight=0),
                slot_spec("江湖异人", "待铨", 1, match_terms=["江湖", "武当", "少林", "龙虎", "山庄", "游侠", "商人", "神医", "掌教", "法师", "侠女"], open_pool=True, weight=0),
            ],
        },
    ]
    return specs


def organization_diagnostics(
    db: Any,
    custom_institutions: Optional[Sequence[InstitutionSpec]] = None,
) -> Dict[str, Any]:
    rows = _active_ming_rows(db)
    specs = [*base_institution_specs(), *_normalize_custom_specs(custom_institutions or [])]
    holder_slot_counts: Dict[str, int] = defaultdict(int)
    prepared: List[Dict[str, Any]] = []

    for inst in specs:
        slots = []
        for raw_slot in inst.get("slots", []):
            slot = dict(raw_slot)
            holders = _holders_for_slot(rows, slot)
            for holder in holders:
                holder_slot_counts[str(holder["name"])] += 1
            slots.append({"slot": slot, "holders": holders})
        prepared.append({"institution": inst, "slots": slots})

    institution_diags: List[Dict[str, Any]] = []
    total_weight = 0
    weighted_score = 0.0
    high_risk = 0

    for item in prepared:
        inst = item["institution"]
        domains = _domains(inst.get("domains"))
        expected_weight = 0
        filled_weight = 0
        quality_weight = 0
        quality_total = 0.0
        vacancy_count = 0
        overflow_count = 0
        critical_vacancies: List[str] = []
        overloaded_names: set[str] = set()
        slot_diags: List[Dict[str, Any]] = []

        for slot_item in item["slots"]:
            slot = slot_item["slot"]
            holders = slot_item["holders"]
            count = max(1, int(slot.get("count") or 1))
            weight = max(0, int(slot.get("weight") or _default_slot_weight(str(slot.get("title") or ""), str(slot.get("office_type") or ""))))
            open_pool = bool(slot.get("open_pool"))
            effective_count = max(count, len(holders)) if open_pool else count
            filled = len(holders) if open_pool else min(len(holders), effective_count)
            vacancies = 0 if open_pool else max(0, effective_count - len(holders))
            overflow = 0 if open_pool else max(0, len(holders) - effective_count)
            holder_scores = [
                _character_execution_score(holder, domains or ["general"])
                for holder in holders[:effective_count]
            ]
            if not open_pool:
                expected_weight += weight * effective_count
                filled_weight += weight * filled
                vacancy_count += vacancies
                overflow_count += overflow
                if vacancies and weight >= 8:
                    critical_vacancies.append(str(slot.get("title") or "关键席位"))
            if holder_scores:
                quality_total += sum(holder_scores) * max(1, weight)
                quality_weight += len(holder_scores) * max(1, weight)
            for holder in holders:
                if holder_slot_counts[str(holder["name"])] >= 3:
                    overloaded_names.add(str(holder["name"]))
            slot_diags.append({
                "title": str(slot.get("title") or ""),
                "weight": weight,
                "filled": filled,
                "count": effective_count,
                "vacancies": vacancies,
                "overflow_count": overflow,
                "holder_names": [str(h["name"]) for h in holders],
                "holder_quality": round(sum(holder_scores) / len(holder_scores)) if holder_scores else 0,
                "open_pool": open_pool,
            })

        coverage = 100 if expected_weight <= 0 else round((filled_weight / expected_weight) * 100)
        quality = round(quality_total / quality_weight) if quality_weight else 50
        overload_penalty = min(14, overflow_count * 3 + len(overloaded_names) * 2)
        readiness = _clamp_int(round(coverage * 0.62 + quality * 0.38 - overload_penalty), 0, 100)
        risks: List[str] = []
        if critical_vacancies:
            risks.append(f"关键空缺：{'、'.join(critical_vacancies[:3])}")
        elif vacancy_count:
            risks.append(f"空缺 {vacancy_count} 席，执行链不满。")
        if overflow_count:
            risks.append(f"同类超配 {overflow_count} 人，易争权或互相卸责。")
        if overloaded_names:
            risks.append(f"{'、'.join(sorted(overloaded_names)[:3])} 兼差过重。")
        if quality < 50 and filled_weight:
            risks.append("在任者平均承办能力偏弱。")
        if readiness < 55:
            high_risk += 1

        total_weight += max(1, expected_weight)
        weighted_score += readiness * max(1, expected_weight)
        institution_diags.append({
            "id": str(inst.get("id") or inst.get("name") or ""),
            "name": str(inst.get("name") or ""),
            "category": str(inst.get("category") or "朝堂"),
            "domains": domains,
            "readiness": readiness,
            "coverage": coverage,
            "holder_quality": quality,
            "vacancy_count": vacancy_count,
            "overflow_count": overflow_count,
            "overloaded_holders": sorted(overloaded_names),
            "critical_vacancies": critical_vacancies,
            "risks": risks[:5],
            "summary": _institution_summary(readiness, vacancy_count, overflow_count, quality),
            "slots": slot_diags,
        })

    overloaded = [
        {"name": name, "slot_count": count}
        for name, count in sorted(holder_slot_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if count >= 3
    ][:12]
    court_readiness = round(weighted_score / total_weight) if total_weight else 50
    return {
        "court_readiness": court_readiness,
        "risk_count": high_risk,
        "summary": _court_summary(court_readiness, high_risk, overloaded),
        "institutions": institution_diags,
        "overloaded_holders": overloaded,
    }


def compact_bureaucracy_brief(diagnostics: Dict[str, Any], limit: int = 10) -> Dict[str, Any]:
    institutions = [
        item for item in diagnostics.get("institutions", [])
        if isinstance(item, dict) and (int(item.get("readiness") or 0) < 65 or item.get("risks"))
    ]
    institutions.sort(key=lambda item: (int(item.get("readiness") or 0), -int(item.get("vacancy_count") or 0)))
    return {
        "court_readiness": int(diagnostics.get("court_readiness") or 0),
        "risk_count": int(diagnostics.get("risk_count") or 0),
        "summary": str(diagnostics.get("summary") or ""),
        "risk_institutions": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "category": item.get("category"),
                "domains": item.get("domains") or [],
                "readiness": item.get("readiness"),
                "risks": item.get("risks") or [],
                "summary": item.get("summary") or "",
            }
            for item in institutions[:limit]
        ],
        "overloaded_holders": diagnostics.get("overloaded_holders", [])[:8],
    }


def directive_execution_assessments(
    state: Any,
    db: Any,
    directives: Optional[Sequence[sqlite3.Row]],
    *,
    organization: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    diagnostics = organization or organization_diagnostics(db)
    institutions = [item for item in diagnostics.get("institutions", []) if isinstance(item, dict)]
    out: List[Dict[str, Any]] = []
    for index, row in enumerate(directives or [], 1):
        text = str(_row_value(row, "text", "") or "")
        actor = str(_row_value(row, "actor", "") or "").strip()
        domains = infer_directive_domains(text)
        relevant = _relevant_institutions(institutions, domains)
        institution_score = _avg([int(item.get("readiness") or 50) for item in relevant], default=int(diagnostics.get("court_readiness") or 50))
        actor_row = _actor_row(db, actor)
        actor_score = _actor_fit_score(actor_row, domains)
        actor_domain_bonus = _actor_domain_bonus(actor_row, domains)
        relationship_score, relationship_note = _relationship_score(db, state, actor)
        faction_score, faction_note = _faction_score(db, actor_row)
        stance_score, stance_note = _stance_score(db, getattr(state, "turn", 0), actor)
        score = _clamp_int(round(
            institution_score * 0.42
            + (actor_score + actor_domain_bonus) * 0.30
            + relationship_score * 0.12
            + faction_score * 0.10
            + stance_score * 0.06
        ), 0, 100)
        risks = _directive_risks(relevant, actor_row, actor_domain_bonus, relationship_score, faction_score, stance_score)
        drivers = [
            f"班子执行力{institution_score}",
            f"承办人适配{_clamp_int(actor_score + actor_domain_bonus, 0, 100)}",
            relationship_note,
            faction_note,
            stance_note,
        ]
        label = _score_label(score)
        out.append({
            "index": index,
            "id": int(_row_value(row, "id", 0) or 0),
            "actor": actor,
            "domains": domains,
            "score": score,
            "label": label,
            "institution_score": institution_score,
            "actor_score": _clamp_int(actor_score + actor_domain_bonus, 0, 100),
            "relationship_score": relationship_score,
            "faction_score": faction_score,
            "stance_score": stance_score,
            "relevant_institutions": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "readiness": item.get("readiness"),
                    "risks": (item.get("risks") or [])[:2],
                }
                for item in relevant[:5]
            ],
            "drivers": [part for part in drivers if part],
            "risks": risks[:6],
            "execution_hint": _execution_hint(score, risks),
        })
    return out


def infer_directive_domains(text: str) -> List[str]:
    checks = [
        ("fiscal", r"银|钱|饷|税|粮|国库|内库|太仓|清丈|盐|商|赈|欠饷|禄米"),
        ("military", r"军|兵|边|辽|关宁|总兵|调防|建军|扩编|训练|战|守|援|剿"),
        ("investigation", r"查|密|厂卫|锦衣卫|东厂|拿问|抄家|追赃|缉|审|线索|口供"),
        ("construction", r"工|厂|炮|火器|营建|修|筑|造|工程|水利|河工|器械|试制"),
        ("personnel", r"任|罢|擢|调任|铨选|考功|官|尚书|侍郎|巡抚|总督"),
        ("local", r"地方|府县|州县|巡抚|总督|布政|按察|陕西|山西|河南|山东|湖广|江南|福建|广东|四川|江西|浙江|南直隶|北直隶"),
        ("procedure", r"廷议|会审|名分|章程|诏|礼|册封|票拟|成例|祖制|言官"),
        ("inner", r"司礼监|内廷|内库|太监|宦官|批红|传旨"),
    ]
    domains = [key for key, pattern in checks if re.search(pattern, text or "")]
    return domains[:5] or ["general"]


def _normalize_custom_specs(custom: Sequence[InstitutionSpec]) -> List[InstitutionSpec]:
    out: List[InstitutionSpec] = []
    for raw in custom:
        if not isinstance(raw, dict):
            continue
        slots = []
        for item in raw.get("slots", []):
            if isinstance(item, dict):
                slot = dict(item)
                slot.setdefault("weight", _default_slot_weight(str(slot.get("title") or ""), str(slot.get("office_type") or "")))
                slots.append(slot)
        out.append({
            **raw,
            "category": str(raw.get("category") or "非常规"),
            "domains": _domains(raw.get("domains")) or ["custom"],
            "slots": slots,
        })
    return out


def _active_ming_rows(db: Any) -> List[Dict[str, Any]]:
    rows = db.conn.execute(
        """
        SELECT name, office, office_type, faction, status, power_id, location,
               loyalty, ability, integrity, courage, force, wisdom, charm, luck, cultivation
        FROM characters
        WHERE status='active' AND office_type!='后宫' AND power_id='ming'
        ORDER BY rowid
        """
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["office"] = normalize_office(str(item.get("office") or ""))
        item["office_type"] = effective_stored_office_type(str(item.get("office") or ""), str(item.get("office_type") or ""))
        out.append(item)
    return out


def _holders_for_slot(rows: Sequence[Dict[str, Any]], slot: SlotSpec) -> List[Dict[str, Any]]:
    title = str(slot.get("title") or "").strip()
    terms = [str(item).strip() for item in (slot.get("match_terms") or [title]) if str(item).strip()]
    match_re = str(slot.get("match_regex") or "").strip()
    office_types = {str(item).strip() for item in (slot.get("office_types") or []) if str(item).strip()}
    holders: List[Dict[str, Any]] = []
    for row in rows:
        parts = _usable_parts(str(row.get("office") or ""))
        text = " ".join(parts)
        actual_type = str(row.get("office_type") or "")
        hit = False
        if slot.get("office_type_only") and office_types and actual_type in office_types:
            hit = True
        if not hit and terms:
            hit = any(term in part for term in terms for part in parts)
        if not hit and match_re:
            hit = any(re.search(match_re, part) for part in parts)
        if not hit and office_types and actual_type in office_types:
            hit = any(term in text for term in terms)
        if hit:
            holders.append(row)
    return holders


def _usable_parts(office: str) -> List[str]:
    return [
        part for part in [p.strip() for p in normalize_office(office).split(",") if p.strip()]
        if not re.search(r"^(前|原)|罢居|候补|归途|潜在|少年|诸生|待铨|未仕", part)
    ]


def _character_execution_score(row: Dict[str, Any], domains: Sequence[str]) -> int:
    scores = [_domain_character_score(row, domain) for domain in domains if domain != "reserve"]
    return _clamp_int(round(sum(scores) / len(scores)), 0, 100) if scores else 50


def _domain_character_score(row: Dict[str, Any], domain: str) -> float:
    loyalty = _num(row.get("loyalty"), 50)
    ability = _num(row.get("ability"), 50)
    integrity = _num(row.get("integrity"), 50)
    courage = _num(row.get("courage"), 50)
    force = _num(row.get("force"), 50)
    wisdom = _num(row.get("wisdom"), ability)
    charm = _num(row.get("charm"), 50)
    luck = _num(row.get("luck"), 50)
    if domain == "fiscal":
        return ability * 0.28 + wisdom * 0.28 + integrity * 0.28 + loyalty * 0.16
    if domain == "military":
        return ability * 0.20 + wisdom * 0.20 + courage * 0.22 + loyalty * 0.18 + force * 0.20
    if domain == "investigation":
        return wisdom * 0.28 + courage * 0.20 + ability * 0.20 + loyalty * 0.17 + luck * 0.15
    if domain == "construction":
        return ability * 0.36 + wisdom * 0.25 + integrity * 0.18 + courage * 0.11 + loyalty * 0.10
    if domain in {"procedure", "personnel", "oversight", "law"}:
        return wisdom * 0.28 + integrity * 0.27 + charm * 0.18 + ability * 0.17 + loyalty * 0.10
    if domain == "local":
        return ability * 0.25 + integrity * 0.22 + courage * 0.20 + wisdom * 0.20 + charm * 0.13
    if domain == "inner":
        return loyalty * 0.34 + ability * 0.21 + wisdom * 0.20 + courage * 0.15 + charm * 0.10
    return ability * 0.25 + wisdom * 0.20 + loyalty * 0.20 + integrity * 0.20 + courage * 0.15


def _actor_fit_score(row: Optional[Dict[str, Any]], domains: Sequence[str]) -> int:
    if not row:
        return 42
    if str(row.get("status") or "") != "active":
        return 25
    return _character_execution_score(row, domains)


def _actor_domain_bonus(row: Optional[Dict[str, Any]], domains: Sequence[str]) -> int:
    if not row:
        return -8
    office_type = str(row.get("office_type") or "")
    office_domains = set(_office_type_domains(office_type))
    overlap = office_domains.intersection(domains)
    if overlap:
        return 8
    if "general" in domains:
        return 0
    return -10


def _relationship_score(db: Any, state: Any, actor: str) -> tuple[int, str]:
    if not actor:
        return 45, "未指定承办人"
    try:
        profile = db.get_xinpan_profile(actor, state)
    except Exception:
        profile = {}
    quadrant = str((profile or {}).get("quadrant") or "")
    base = {"股肱": 78, "权附": 64, "道隐": 52, "离心": 32}.get(quadrant, 50)
    fear = float((profile or {}).get("fear") or 0)
    if quadrant == "离心" and fear >= 60:
        base += 5
    note = f"心盘{quadrant or '未明'}"
    if quadrant == "离心" and fear >= 60:
        note += "，畏惧压住公开抗命但暗阻仍在"
    return _clamp_int(base, 0, 100), note


def _faction_score(db: Any, actor_row: Optional[Dict[str, Any]]) -> tuple[int, str]:
    faction = str((actor_row or {}).get("faction") or "")
    if not faction:
        return 50, "派系未明"
    row = db.conn.execute("SELECT satisfaction, leverage FROM factions WHERE name=?", (faction,)).fetchone()
    if not row:
        return 50, f"{faction}无派系盘面"
    sat = int(row["satisfaction"])
    lev = int(row["leverage"])
    score = 50 + round((sat - 50) * 0.35) + round((lev - 50) * 0.12)
    if sat <= 35 and lev >= 60:
        score -= 14
    return _clamp_int(score, 0, 100), f"{faction}满意{sat}/影响{lev}"


def _stance_score(db: Any, turn: int, actor: str) -> tuple[int, str]:
    if not actor:
        return 45, "无召对背书"
    try:
        stances = db.list_minister_stances(turn=turn, minister_name=actor, limit=4)
    except Exception:
        stances = []
    if not stances:
        return 50, "本月无明确召对立场"
    best = stances[0]
    stance = str(best.get("stance") or "neutral")
    handshake = str(best.get("handshake_status") or "none")
    score = {"support": 70, "caution": 54, "neutral": 48, "oppose": 30}.get(stance, 48)
    if handshake == "sealed":
        score += 12
    elif handshake == "conditional":
        score -= 4
    elif handshake == "blocked":
        score -= 14
    return _clamp_int(score, 0, 100), f"召对{stance}/{handshake}"


def _directive_risks(
    relevant: Sequence[Dict[str, Any]],
    actor_row: Optional[Dict[str, Any]],
    actor_bonus: int,
    relationship_score: int,
    faction_score: int,
    stance_score: int,
) -> List[str]:
    risks: List[str] = []
    for item in relevant:
        for risk in item.get("risks") or []:
            if risk not in risks:
                risks.append(str(risk))
    if actor_row is None:
        risks.append("未指定或未找到承办人。")
    elif actor_bonus < 0:
        risks.append("承办人官署与旨意类型不匹配。")
    if relationship_score < 45:
        risks.append("承办人心盘离心，可能阳奉阴违。")
    if faction_score < 45:
        risks.append("承办派系满意不足，存在程序或人手阻滞。")
    if stance_score < 45:
        risks.append("召对未形成背书或已经受阻。")
    return risks


def _relevant_institutions(institutions: Sequence[Dict[str, Any]], domains: Sequence[str]) -> List[Dict[str, Any]]:
    domain_set = set(domains)
    hits = [
        item for item in institutions
        if domain_set.intersection(set(item.get("domains") or []))
        and str(item.get("id") or "") != "talent-pool"
    ]
    if hits:
        hits.sort(key=lambda item: int(item.get("readiness") or 0))
        return hits
    return [item for item in institutions if str(item.get("id") or "") in {"cabinet", "inner-court"}]


def _actor_row(db: Any, actor: str) -> Optional[Dict[str, Any]]:
    if not actor:
        return None
    row = db.conn.execute(
        """
        SELECT name, office, office_type, faction, status, power_id, location,
               loyalty, ability, integrity, courage, force, wisdom, charm, luck, cultivation
        FROM characters WHERE name=?
        """,
        (actor,),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["office"] = normalize_office(str(item.get("office") or ""))
    item["office_type"] = effective_stored_office_type(str(item.get("office") or ""), str(item.get("office_type") or ""))
    return item


def _office_type_domains(office_type: str) -> List[str]:
    mapping = {
        "内阁": ["procedure", "coordination"],
        "吏部": ["personnel", "procedure"],
        "户部": ["fiscal", "local"],
        "礼部": ["procedure", "diplomacy"],
        "兵部": ["military", "personnel"],
        "刑部": ["law", "investigation"],
        "工部": ["construction"],
        "都察院": ["oversight", "procedure", "local"],
        "翰林院": ["procedure", "education"],
        "司礼监": ["inner", "procedure", "investigation"],
        "东厂": ["investigation", "inner"],
        "锦衣卫": ["investigation"],
        "边镇": ["military", "local"],
        "地方": ["local", "fiscal"],
    }
    return mapping.get(office_type, ["general"])


def _default_slot_weight(title: str, office_type: str = "") -> int:
    text = f"{title} {office_type}"
    if re.search(r"首辅|掌印|督师|提督东厂", text):
        return 10
    if re.search(r"次辅|尚书|秉笔|指挥使|经略", text):
        return 8
    if re.search(r"总督|巡抚|都御史|镇抚|詹事", text):
        return 6
    if re.search(r"侍郎|总兵|监军|水师|海防", text):
        return 5
    if re.search(r"御史|巡按|府县|地方|副将", text):
        return 3
    if re.search(r"属官|缇骑|番役|编检|艺文", text):
        return 2
    return 3


def _institution_summary(readiness: int, vacancy_count: int, overflow_count: int, quality: int) -> str:
    if readiness >= 75:
        base = "班子充实，足以承重差。"
    elif readiness >= 60:
        base = "大体可用，遇急务仍会折损。"
    elif readiness >= 45:
        base = "执行链偏弱，需补缺或另给抓手。"
    else:
        base = "班子残缺，政令落地风险高。"
    details = []
    if vacancy_count:
        details.append(f"空缺{vacancy_count}席")
    if overflow_count:
        details.append(f"超配{overflow_count}人")
    if quality < 50:
        details.append("在任能力偏弱")
    return base + (" " + "，".join(details) + "。" if details else "")


def _court_summary(readiness: int, high_risk: int, overloaded: Sequence[Dict[str, Any]]) -> str:
    if readiness >= 75:
        base = "朝廷班子整体强，政令有较稳定的制度通道。"
    elif readiness >= 60:
        base = "朝廷班子可运转，但关键差事仍需点名承办和补足资源。"
    elif readiness >= 45:
        base = "朝廷班子偏弱，空缺与兼差会放大拖延。"
    else:
        base = "朝廷班子失衡，许多旨意会先卡在人手和官署链条上。"
    if high_risk:
        base += f" 高风险机构{high_risk}处。"
    if overloaded:
        base += f" 兼差拥挤者：{'、'.join(str(item.get('name')) for item in overloaded[:3])}。"
    return base


def _score_label(score: int) -> str:
    if score >= 75:
        return "顺行"
    if score >= 55:
        return "可行但折损"
    if score >= 35:
        return "高阻折损"
    return "搁置风险"


def _execution_hint(score: int, risks: Sequence[str]) -> str:
    label = _score_label(score)
    if score >= 75:
        return f"{label}：班子、承办人和政治背书基本相合；仍须按钱粮与地方实情检验。"
    if score >= 55:
        return f"{label}：可以启动，但应在奏章核销中写清折损环节。"
    if score >= 35:
        return f"{label}：必须写出具体阻力来源，不能写成顺利奉行。"
    return f"{label}：若无额外授权、补缺或资源，倾向搁置不行。"


def _domains(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    return []


def _row_value(row: Any, key: str, default: object = "") -> object:
    try:
        return row[key] if key in row.keys() else default
    except Exception:
        if isinstance(row, dict):
            return row.get(key, default)
        return default


def _num(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _avg(values: Iterable[int], default: int = 50) -> int:
    vals = list(values)
    return round(sum(vals) / len(vals)) if vals else default


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))

