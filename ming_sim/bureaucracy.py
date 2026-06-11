"""Bureaucratic readiness and directive execution preflight.

This module keeps the court-organization display and the turn simulator anchored
to the same deterministic evidence: filled seats, key vacancies, overloaded
office holders, actor fitness, dialogue stance, agreement ledger, NPC network,
and faction posture.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ming_sim.db import effective_stored_office_type, normalize_office
from ming_sim.context import npc_dialogue_behavior_brief, npc_network_profile


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
        trait_modifier, trait_note, trait_risks = _actor_trait_modifier(db, actor, domains)
        actor_adjusted = _clamp_int(actor_score + actor_domain_bonus + trait_modifier, 0, 100)
        relationship_score, relationship_note = _relationship_score(db, state, actor)
        faction_score, faction_note = _faction_score(db, actor_row)
        stance_score, stance_note = _stance_score(db, getattr(state, "turn", 0), actor, text)
        stance_risks = _stance_execution_risks(db, getattr(state, "turn", 0), actor, text)
        score = _clamp_int(round(
            institution_score * 0.42
            + actor_adjusted * 0.30
            + relationship_score * 0.12
            + faction_score * 0.10
            + stance_score * 0.06
        ), 0, 100)
        risks = _directive_risks(
            relevant,
            actor_row,
            actor_domain_bonus,
            relationship_score,
            faction_score,
            stance_score,
            stance_risks=stance_risks,
        )
        risks = [
            *trait_risks,
            *[risk for risk in stance_risks if risk not in trait_risks],
            *[risk for risk in risks if risk not in trait_risks and risk not in stance_risks],
        ]
        drivers = [
            f"班子执行力{institution_score}",
            f"承办人适配{actor_adjusted}",
            trait_note,
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
            "actor_score": actor_adjusted,
            "trait_modifier": trait_modifier,
            "trait_note": trait_note,
            "relationship_score": relationship_score,
            "faction_score": faction_score,
            "stance_score": stance_score,
            "stance_risks": stance_risks,
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


def secret_order_actor_assessment(
    state: Any,
    db: Any,
    order: Dict[str, Any],
) -> Dict[str, Any]:
    """Deterministic assignee profile for secret-order simulation review."""
    actor = str((order or {}).get("minister_name") or "").strip()
    title = str((order or {}).get("title") or "")
    content = str((order or {}).get("content") or "")
    result = str((order or {}).get("result") or "")
    sim_note = str((order or {}).get("sim_note") or "")
    text = "\n".join(part for part in (title, content, result, sim_note) if part.strip())
    domains = infer_directive_domains(text or title)
    actor_row = _actor_row(db, actor)
    actor_score = _actor_fit_score(actor_row, domains)
    actor_domain_bonus = _actor_domain_bonus(actor_row, domains)
    trait_modifier, trait_note, trait_risks = _actor_trait_modifier(db, actor, domains)
    actor_adjusted = _clamp_int(actor_score + actor_domain_bonus + trait_modifier, 0, 100)
    relationship_score, relationship_note = _relationship_score(db, state, actor)
    faction_score, faction_note = _faction_score(db, actor_row)
    stance_score, stance_note = _stance_score(db, getattr(state, "turn", 0), actor, text)
    stance_risks = _stance_execution_risks(db, getattr(state, "turn", 0), actor, text)
    score = _clamp_int(round(
        actor_adjusted * 0.46
        + relationship_score * 0.22
        + stance_score * 0.20
        + faction_score * 0.12
    ), 0, 100)
    try:
        network = npc_network_profile(actor, db=db, limit=10)
    except Exception:
        network = {}
    relations: List[Dict[str, str]] = []
    for rel in (network.get("relations") if isinstance(network, dict) else []) or []:
        if not isinstance(rel, dict):
            continue
        relations.append({
            "target": str(rel.get("target") or ""),
            "type": str(rel.get("type") or ""),
            "note": str(rel.get("note") or "")[:100],
        })
        if len(relations) >= 6:
            break
    try:
        stances = db.list_minister_stances(turn=getattr(state, "turn", 0), minister_name=actor, limit=8)
    except Exception:
        stances = []
    related_stances = [
        {
            "topic": str(row.get("topic") or ""),
            "stance": str(row.get("stance") or ""),
            "handshake_status": str(row.get("handshake_status") or ""),
            "risk_tags": row.get("risk_tags_list") if isinstance(row.get("risk_tags_list"), list) else [],
            "execution_hint": str(row.get("execution_hint") or "")[:160],
        }
        for row in stances
        if _stance_relevant_to_directive(db, row, text)
    ][:4]
    risks = [
        *trait_risks,
        *[risk for risk in stance_risks if risk not in trait_risks],
    ]
    if actor_row is None:
        risks.append("密令承办人不在当前名册，核议应从严。")
    elif actor_domain_bonus < 0:
        risks.append("承办人职掌与密令类型不合，需额外核查线索来源。")
    if relationship_score < 45:
        risks.append("承办人缺少可信握手或履约背书，密令陈词可能变形。")
    if stance_score < 45:
        risks.append("相关召对未形成背书，密令核议不可只信承办人口供。")
    ability_logic = str((network or {}).get("ability_logic") if isinstance(network, dict) else "")
    growth = network.get("growth_arc") if isinstance(network, dict) and isinstance(network.get("growth_arc"), dict) else {}
    return {
        "actor": actor,
        "domains": domains,
        "score": score,
        "label": _score_label(score),
        "actor_score": actor_adjusted,
        "trait_modifier": trait_modifier,
        "trait_note": trait_note,
        "relationship_score": relationship_score,
        "faction_score": faction_score,
        "stance_score": stance_score,
        "drivers": [
            part for part in (
                f"密令承办适配{actor_adjusted}",
                trait_note,
                relationship_note,
                faction_note,
                stance_note,
            )
            if part
        ],
        "risks": list(dict.fromkeys(risks))[:8],
        "stance_risks": stance_risks,
        "related_stances": related_stances,
        "personality_behavior": npc_dialogue_behavior_brief(actor, text=text)[:700] if actor else "",
        "network_brief": {
            "ability_logic": ability_logic[:260],
            "growth_risk": str((growth or {}).get("risk") or "")[:180],
            "relations": relations,
        },
        "review_guidance": (
            "核议时同时看任务可行性、承办人能力/trait、相关召对是否已握手、"
            "同党护短或政敌清算风险、既有进展与自述 claim 是否一致。"
        ),
    }


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
    score = 50
    notes: List[str] = []
    try:
        stances = db.list_minister_stances(turn=getattr(state, "turn", 0), minister_name=actor, limit=5)
    except Exception:
        stances = []
    for stance in stances[:3]:
        value = str(stance.get("stance") or "")
        topic = str(stance.get("topic") or "本回合奏对")
        if value == "support":
            score += 14
            notes.append(f"{topic}已表支持")
        elif value == "caution":
            score += 2
            notes.append(f"{topic}仍附条件")
        elif value == "oppose":
            score -= 18
            notes.append(f"{topic}真实抵触")
    try:
        agreements = db.negotiation_agreement_ledger(state, minister_name=actor, limit=6)
    except Exception:
        agreements = []
    for agreement in agreements[:3]:
        topic = str(agreement.get("core_topic") or agreement.get("topic") or "履约事项")
        target_status = str(agreement.get("target_status") or agreement.get("status") or "")
        if target_status in {"achieved", "fulfilled"}:
            score += 12
            notes.append(f"{topic}已履约背书")
        elif target_status == "pending_conditions":
            score -= 4
            notes.append(f"{topic}条件未闭环")
        elif target_status == "failed":
            score -= 16
            notes.append(f"{topic}失信折损")
        elif target_status in {"blocked", "abandoned"}:
            score -= 10
            notes.append(f"{topic}未说服")
    try:
        profile = npc_network_profile(actor, db=db, limit=8)
    except Exception:
        profile = {}
    ability_logic = str((profile or {}).get("ability_logic") or "")
    if any(marker in ability_logic for marker in ("阳奉阴违", "善观风色", "猜忌多疑", "结党营私", "贪墨成性")):
        score -= 8
        notes.append("人物痼疾带来话术或执行走样风险")
    if "直言不讳" in ability_logic:
        notes.append("性格直切，奏对更可能明言边界")
    return _clamp_int(score, 0, 100), "；".join(notes[:4]) or "无本回合明确握手或履约背书"


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


_STANCE_STOP_TERMS = {
    "皇帝", "陛下", "微臣", "臣愿", "臣以", "奏对", "目的", "本次", "本轮",
    "明旨", "条件", "承办", "查办", "会审", "回奏", "支持", "反对", "保留",
}


def _stance_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", str(text or "")):
        if raw in _STANCE_STOP_TERMS:
            continue
        if len(raw) >= 2:
            terms.add(raw)
        if len(raw) >= 4:
            for size in (2, 3):
                for idx in range(0, len(raw) - size + 1):
                    part = raw[idx:idx + size]
                    if part not in _STANCE_STOP_TERMS:
                        terms.add(part)
    return terms


def _stance_text_related(left: str, right: str) -> bool:
    left = str(left or "").strip()
    right = str(right or "").strip()
    if not left or not right:
        return False
    chunks = [
        chunk for chunk in re.split(r"[\s,，;；。！？、（）()《》「」\"']+", left)
        if len(chunk) >= 4 and chunk not in _STANCE_STOP_TERMS
    ]
    if any(chunk and chunk in right for chunk in chunks[:12]):
        return True
    left_terms = _stance_terms(left)
    right_terms = _stance_terms(right)
    overlap = left_terms.intersection(right_terms)
    return any(len(term) >= 3 for term in overlap) or len(overlap) >= 2


def _stance_relevance_text(row: Dict[str, object]) -> str:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    psychological = row.get("psychological") if isinstance(row.get("psychological"), dict) else {}
    parts = [
        row.get("topic"),
        row.get("summary"),
        row.get("conditions"),
        row.get("user_message"),
        row.get("minister_answer"),
        row.get("execution_hint"),
    ]
    if isinstance(evidence, dict):
        parts.append(evidence.get("public_hint"))
        parts.append(evidence.get("private_reason"))
        parts.append(evidence.get("speech_profile_summary"))
    if isinstance(psychological, dict):
        parts.append(psychological.get("core_topic"))
        parts.append(psychological.get("target_text"))
        parts.append(psychological.get("private_reason"))
        parts.append(psychological.get("public_hint"))
    return "\n".join(str(part or "") for part in parts if str(part or "").strip())


def _stance_relevant_to_directive(db: Any, row: Dict[str, object], directive_text: str) -> bool:
    directive_text = str(directive_text or "").strip()
    if not directive_text:
        return True
    agreement_id = int(row.get("agreement_id") or 0)
    if agreement_id:
        try:
            agreement = db.conn.execute(
                "SELECT * FROM negotiation_agreements WHERE id=?",
                (agreement_id,),
            ).fetchone()
            if agreement is not None and db._agreement_relevant_in_context(dict(agreement), directive_text):
                return True
        except Exception:
            pass
    goal_id = int(row.get("goal_id") or 0)
    if goal_id:
        try:
            goal = db.get_conversation_goal(goal_id)
        except Exception:
            goal = None
        if isinstance(goal, dict):
            goal_text = "\n".join(str(goal.get(key) or "") for key in ("title", "target_text", "last_event_summary"))
            if _stance_text_related(goal_text, directive_text):
                return True
    return _stance_text_related(_stance_relevance_text(row), directive_text)


def _speech_profile_from_stance(row: Dict[str, object]) -> Dict[str, object]:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    psychological = row.get("psychological") if isinstance(row.get("psychological"), dict) else {}
    speech = evidence.get("speech_profile") if isinstance(evidence.get("speech_profile"), dict) else {}
    if not speech and isinstance(psychological.get("speech_profile"), dict):
        speech = psychological["speech_profile"]  # type: ignore[index]
    return speech if isinstance(speech, dict) else {}


def _stance_execution_risks(db: Any, turn: int, actor: str, directive_text: str = "") -> List[str]:
    if not actor:
        return []
    try:
        stances = db.list_minister_stances(turn=turn, minister_name=actor, limit=8)
    except Exception:
        stances = []
    risks: List[str] = []
    for row in stances:
        if not _stance_relevant_to_directive(db, row, directive_text):
            continue
        speech = _speech_profile_from_stance(row)
        acts = {str(item) for item in (speech.get("speech_acts") or [])}
        risk_tags = [str(item) for item in (row.get("risk_tags_list") or []) if str(item).strip()]
        topic = str(row.get("topic") or "本轮奏对")
        if acts.intersection({"misdirection", "selective_truth"}) or "话术不实" in risk_tags:
            risks.append(f"{topic}话术有保留，口头顺从不可直接等同真实履约。")
        if "accusation" in acts or "政敌告状" in risk_tags:
            risks.append(f"{topic}牵涉政敌告状，承办时可能借旨清算或扩大打击。")
        if "shielding" in acts or "人情护短" in risk_tags or "同党背书" in risk_tags:
            risks.append(f"{topic}牵涉同党/恩主人情，承办时可能拖延、转圜或护短。")
        if "旧事牵引" in risk_tags:
            risks.append(f"{topic}承接旧事或待证条件，须核查复命与履约闭环。")
        if len(risks) >= 5:
            break
    return list(dict.fromkeys(risks))[:5]


def _stance_score(db: Any, turn: int, actor: str, directive_text: str = "") -> tuple[int, str]:
    if not actor:
        return 45, "无召对背书"
    try:
        agreements = db.list_negotiation_agreements(minister_name=actor, limit=12)
    except Exception:
        agreements = []
    for agreement in agreements:
        try:
            relevant = db._agreement_relevant_in_context(agreement, directive_text)
        except Exception:
            relevant = bool(
                str(agreement.get("core_topic") or agreement.get("topic") or "") in directive_text
                or str(agreement.get("target_text") or "") in directive_text
            )
        if not relevant:
            continue
        target_status = str(agreement.get("target_status") or "")
        status = str(agreement.get("status") or "")
        topic = str(agreement.get("core_topic") or agreement.get("topic") or "奏对协议")
        if target_status == "achieved" or status == "fulfilled":
            return 88, f"履约背书已达成：{topic}"
        if target_status == "pending_conditions" or status == "pending":
            return 42, f"履约条件待证：{topic}"
        if target_status in {"blocked", "failed"} or status in {"blocked", "failed"}:
            return 28, f"履约未成：{topic}"
    try:
        stances = db.list_minister_stances(turn=turn, minister_name=actor, limit=4)
    except Exception:
        stances = []
    if not stances:
        return 50, "本月无明确召对立场"
    relevant_stances = [row for row in stances if _stance_relevant_to_directive(db, row, directive_text)]
    if directive_text and not relevant_stances:
        return 50, "本月召对立场未命中本旨"
    best = relevant_stances[0] if relevant_stances else stances[0]
    stance_agreement_id = int(best.get("agreement_id") or 0)
    if stance_agreement_id:
        try:
            row = db.conn.execute(
                "SELECT topic, core_topic, target_status, status FROM negotiation_agreements WHERE id=?",
                (stance_agreement_id,),
            ).fetchone()
        except Exception:
            row = None
        if row is not None:
            target_status = str(row["target_status"] or "")
            status = str(row["status"] or "")
            topic = str(row["core_topic"] or row["topic"] or "奏对协议")
            if target_status == "achieved" or status == "fulfilled":
                return 88, f"履约背书已达成：{topic}"
            if target_status == "pending_conditions" or status == "pending":
                return 42, f"履约条件待证：{topic}"
            if target_status in {"blocked", "failed"} or status in {"blocked", "failed"}:
                return 28, f"履约未成：{topic}"
    stance_goal_id = int(best.get("goal_id") or 0)
    if stance_goal_id:
        try:
            goal = db.get_conversation_goal(stance_goal_id)
        except Exception:
            goal = None
        if isinstance(goal, dict):
            goal_status = str(goal.get("status") or "")
            topic = str(goal.get("title") or goal.get("target_text") or "奏对目的")
            if goal_status == "waiting_conditions":
                return 42, f"奏对目的条件待证：{topic}"
            if goal_status in {"active", "expired"}:
                return 48, f"奏对目的未握手入账：{topic}"
            if goal_status in {"blocked", "abandoned"}:
                return 28, f"奏对目的未成：{topic}"
            if goal_status == "sealed":
                return 52, f"奏对目的已握手但未见 achieved 履约背书：{topic}"
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


def _actor_trait_modifier(db: Any, actor: str, domains: Sequence[str]) -> tuple[int, str, List[str]]:
    actor = str(actor or "").strip()
    if not actor:
        return 0, "", []
    try:
        content = getattr(db, "content", None)
        network = getattr(content, "npc_network", {}) if content is not None else {}
        entry = network.get(actor, {}) if isinstance(network, dict) else {}
    except Exception:
        entry = {}
    if not isinstance(entry, dict):
        return 0, "", []
    ability_logic = str(entry.get("ability_logic") or "")
    if not ability_logic:
        return 0, "", []
    domain_set = set(str(domain) for domain in domains)
    axes = _ability_axes_from_logic(ability_logic)
    positive = 0
    negative = 0
    notes: List[str] = []
    risks: List[str] = []

    def bump(amount: int, note: str = "", risk: str = "") -> None:
        nonlocal positive, negative
        if amount >= 0:
            positive += amount
        else:
            negative += amount
        if note and note not in notes:
            notes.append(note)
        if risk and risk not in risks:
            risks.append(risk)

    def axis_adjust(axis_names: Sequence[str], note: str) -> None:
        values = [axes[name] for name in axis_names if name in axes]
        if not values:
            return
        avg = sum(values) / len(values)
        if avg >= 17:
            bump(6, f"{note}能力轴高")
        elif avg >= 15:
            bump(3, f"{note}能力轴可用")
        elif avg <= 10:
            bump(-5, f"{note}能力轴短板", f"承办人{note}能力轴偏弱，执行需另给辅佐。")

    if "fiscal" in domain_set:
        axis_adjust(("治", "识"), "钱粮经世")
        if any(term in ability_logic for term in ("钱粮", "财政", "经世行政", "清丈", "盐课", "户部")):
            bump(8, "钱粮/经世强项")
        if any(term in ability_logic for term in ("贪墨成性", "营私", "贪")):
            bump(-10, "财务清望风险", "承办人有贪墨/营私风险，钱粮执行可能变形。")
    if "military" in domain_set:
        axis_adjust(("略", "韬"), "军务谋略")
        if any(term in ability_logic for term in ("军事", "边防", "统兵", "兵事", "辽事", "武略")):
            bump(8, "军事/边防强项")
        if any(term in ability_logic for term in ("怯懦", "沽名钓誉", "纸上")):
            bump(-7, "军务胆略风险", "承办人军务胆略或务实名声不足。")
    if "investigation" in domain_set:
        axis_adjust(("识", "略"), "查办判断")
        if any(term in ability_logic for term in ("侦缉", "厂卫", "权术", "审讯", "刑名", "查案", "耳目")):
            bump(7, "查办/权术强项")
        if any(term in ability_logic for term in ("暴戾恣睢", "构陷", "酷烈")):
            bump(-6, "查办酷烈风险", "查办可能扩大成酷烈清算。")
    if domain_set.intersection({"procedure", "personnel", "law"}):
        axis_adjust(("治", "望"), "制度人事")
        if any(term in ability_logic for term in ("礼法", "制度", "铨选", "台谏", "清议", "名分", "章程")):
            bump(6, "制度/名分强项")
        if any(term in ability_logic for term in ("门户之见", "结党营私")):
            bump(-7, "门户人事风险", "承办人门户牵引强，人事/程序可能护短或排异。")
    if "local" in domain_set:
        axis_adjust(("治", "望"), "地方治理")
        if any(term in ability_logic for term in ("地方", "抚民", "民生", "赈济", "州县", "巡抚")):
            bump(6, "地方治理强项")
    if "construction" in domain_set:
        axis_adjust(("识", "治"), "工程营造")
        if any(term in ability_logic for term in ("工程", "火器", "水利", "营造", "算学", "西学")):
            bump(7, "工程/火器强项")
    if "inner" in domain_set:
        axis_adjust(("治", "略"), "内廷执行")
        if any(term in ability_logic for term in ("内廷", "司礼监", "厂卫", "传旨", "宫禁")):
            bump(6, "内廷执行强项")

    if any(term in ability_logic for term in ("阳奉阴违", "善观风色")):
        bump(-5, "阳奉阴违/观风色", "承办人可能口头顺从、执行留活口。")
    if "猜忌多疑" in ability_logic:
        bump(-4, "猜忌多疑", "承办人猜忌多疑，协同成本上升。")
    if "直言不讳" in ability_logic:
        bump(2, "直言预警", "")

    modifier = _clamp_int(positive + negative, -18, 14)
    if not notes:
        return 0, "", risks
    sign = "+" if modifier > 0 else ""
    return modifier, f"能力/trait修正{sign}{modifier}（{'、'.join(notes[:4])}）", risks[:4]


def _ability_axes_from_logic(ability_logic: str) -> Dict[str, int]:
    axes: Dict[str, int] = {}
    for name, value in re.findall(r"([韬治识略望])\s*(\d+)", str(ability_logic or "")):
        try:
            axes[name] = int(value)
        except ValueError:
            continue
    return axes


def _directive_risks(
    relevant: Sequence[Dict[str, Any]],
    actor_row: Optional[Dict[str, Any]],
    actor_bonus: int,
    relationship_score: int,
    faction_score: int,
    stance_score: int,
    *,
    stance_risks: Optional[Sequence[str]] = None,
) -> List[str]:
    risks: List[str] = []
    for item in relevant:
        for risk in item.get("risks") or []:
            if risk not in risks:
                risks.append(str(risk))
    for risk in stance_risks or []:
        text = str(risk or "").strip()
        if text and text not in risks:
            risks.append(text)
    if actor_row is None:
        risks.append("未指定或未找到承办人。")
    elif actor_bonus < 0:
        risks.append("承办人官署与旨意类型不匹配。")
    if relationship_score < 45:
        risks.append("承办人缺少可信握手或履约背书，可能拖延、变形或阳奉阴违。")
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
