"""Statecraft center: HOI-style economy and bureaucracy synthesis.

The game already has detailed fiscal lines and organization diagnostics. This
module does not create a second simulation. It translates those sources into a
single "state machine" panel: stocks, monthly flows, bureaucratic capacities,
and bottlenecks.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping

from ming_sim.bureaucracy import infer_directive_domains, organization_diagnostics
from ming_sim.db import GameDB
from ming_sim.fiscal_center import fiscal_center_payload
from ming_sim.models import GameState


DOMAIN_LABELS: Dict[str, str] = {
    "fiscal": "财政署理",
    "military": "军政后勤",
    "construction": "营造军工",
    "local": "地方贯彻",
    "personnel": "铨选任官",
    "procedure": "票拟程序",
    "investigation": "厂卫监察",
    "inner": "内廷传旨",
    "oversight": "监察纠弹",
    "law": "刑名审覆",
    "diplomacy": "礼制外交",
    "education": "文翰教化",
    "coordination": "中枢协调",
}

DOMAIN_EFFECTS: Dict[str, str] = {
    "fiscal": "影响税源清查、军饷调度、亏空解释和财政改革执行。",
    "military": "影响调兵、边防、补饷、训练和军镇服从。",
    "construction": "影响工坊、炮厂、水利、仓廪和建筑维护效率。",
    "local": "影响诏令到省、州县承办、动乱治理和税收到账。",
    "personnel": "影响补缺、罢免、升调、考成和新官上任速度。",
    "procedure": "影响票拟、廷议、章程、名分和诏旨合法性。",
    "investigation": "影响密查、盘库、追赃、厂卫侦缉和反噬风险。",
    "inner": "影响批红、近身传旨、内廷工具和密令执行。",
    "oversight": "影响弹劾、纠偏、清查和官僚自净。",
    "law": "影响审覆、问罪、赦免和刑名程序。",
    "diplomacy": "影响册封、朝贡、外臣名分和礼制争议。",
    "education": "影响经筵、文书、史册和长期人才声望。",
    "coordination": "影响内阁统筹、跨部门协同和政策阻力消解。",
}


def _score_status(score: int) -> str:
    if score >= 78:
        return "充足"
    if score >= 62:
        return "可用"
    if score >= 45:
        return "吃紧"
    return "断裂"


def _score_tone(score: int) -> str:
    if score >= 78:
        return "good"
    if score >= 62:
        return "neutral"
    if score >= 45:
        return "warn"
    return "danger"


def _avg(values: Iterable[int], *, default: int = 50) -> int:
    nums = [int(v) for v in values]
    if not nums:
        return int(default)
    return round(sum(nums) / len(nums))


def _sum_rows(rows: Iterable[Mapping[str, Any]], *, account: str = "", family: str = "") -> int:
    total = 0
    for row in rows:
        if account and str(row.get("account") or "") != account:
            continue
        if family and str(row.get("family") or "") != family:
            continue
        total += int(row.get("amount") or 0)
    return total


def _building_capacity_rows(db: GameDB) -> List[Dict[str, Any]]:
    rows = db.conn.execute(
        "SELECT category, condition, maintenance, output_metric, output_amount, status FROM buildings"
    ).fetchall()
    grouped: Dict[str, Dict[str, int]] = defaultdict(lambda: {
        "count": 0,
        "active": 0,
        "condition_total": 0,
        "maintenance": 0,
        "monthly_output": 0,
        "risk_count": 0,
    })
    for row in rows:
        category = str(row["category"] or "未分类")
        item = grouped[category]
        condition = max(0, min(100, int(row["condition"] or 0)))
        item["count"] += 1
        item["condition_total"] += condition
        item["maintenance"] += max(0, int(row["maintenance"] or 0))
        status = str(row["status"] or "")
        if status in {"active", "正常", ""}:
            item["active"] += 1
        if condition < 50:
            item["risk_count"] += 1
        metric = str(row["output_metric"] or "")
        if metric in {"国库", "内库"} and int(row["output_amount"] or 0):
            item["monthly_output"] += round(int(row["output_amount"] or 0) * condition / 100)
    out: List[Dict[str, Any]] = []
    for category, item in sorted(grouped.items()):
        count = max(1, int(item["count"]))
        avg_condition = round(int(item["condition_total"]) / count)
        out.append({
            "category": category,
            "count": int(item["count"]),
            "active": int(item["active"]),
            "avg_condition": avg_condition,
            "maintenance": int(item["maintenance"]),
            "monthly_output": int(item["monthly_output"]),
            "risk_count": int(item["risk_count"]),
            "status": _score_status(avg_condition),
            "tone": _score_tone(avg_condition),
        })
    return out


def _capacity_rows(organization: Dict[str, Any]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for inst in organization.get("institutions", []) or []:
        if not isinstance(inst, dict):
            continue
        for domain in inst.get("domains") or []:
            buckets[str(domain)].append(inst)

    preferred = [
        "fiscal",
        "military",
        "construction",
        "local",
        "personnel",
        "procedure",
        "investigation",
        "inner",
        "oversight",
        "law",
    ]
    domains = [d for d in preferred if d in buckets] + sorted(d for d in buckets if d not in preferred)
    out: List[Dict[str, Any]] = []
    for domain in domains:
        institutions = buckets[domain]
        score = _avg((int(inst.get("readiness") or 0) for inst in institutions), default=50)
        weak = sorted(
            institutions,
            key=lambda item: (int(item.get("readiness") or 0), -int(item.get("vacancy_count") or 0)),
        )[:3]
        out.append({
            "domain": domain,
            "label": DOMAIN_LABELS.get(domain, domain),
            "score": score,
            "status": _score_status(score),
            "tone": _score_tone(score),
            "effect": DOMAIN_EFFECTS.get(domain, "影响相关诏旨的承办速度、阻力和复命可信度。"),
            "institutions": [
                {
                    "id": str(inst.get("id") or ""),
                    "name": str(inst.get("name") or ""),
                    "readiness": int(inst.get("readiness") or 0),
                    "vacancy_count": int(inst.get("vacancy_count") or 0),
                    "risks": (inst.get("risks") or [])[:2],
                }
                for inst in weak
            ],
        })
    return out


def _bottlenecks(
    fiscal: Dict[str, Any],
    organization: Dict[str, Any],
    capacity_rows: List[Dict[str, Any]],
    buildings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    totals = fiscal.get("totals") or {}
    cash_gap = int(totals.get("cash_gap_next_month") or 0)
    arrears = int(totals.get("army_arrears") or 0)
    if cash_gap > 0:
        out.append({
            "kind": "cash_gap",
            "title": f"下月现金缺口 {cash_gap} 万两",
            "detail": "财政月流为负且当前余额不足，需要裁支、增收、内帑拨补或延缓工程。",
            "tone": "danger",
        })
    if arrears > 0:
        out.append({
            "kind": "army_arrears",
            "title": f"军饷欠发 {arrears} 万两",
            "detail": "这是后勤缺口，不只是余额数字；会拖累军心和边防执行。",
            "tone": "danger",
        })
    for row in capacity_rows:
        score = int(row.get("score") or 0)
        if score < 55:
            out.append({
                "kind": f"capacity:{row.get('domain')}",
                "title": f"{row.get('label')}能力{row.get('status')}（{score}）",
                "detail": str(row.get("effect") or ""),
                "tone": row.get("tone") or "warn",
            })
    weak_buildings = [row for row in buildings if int(row.get("avg_condition") or 0) < 55 or int(row.get("risk_count") or 0) > 0]
    if weak_buildings:
        names = "、".join(str(row.get("category") or "") for row in weak_buildings[:3])
        out.append({
            "kind": "building_condition",
            "title": f"营造资产失修：{names}",
            "detail": "建筑状态低会降低产出但维护仍会扣账，等同 HoI 里的工厂被炸后仍占修复压力。",
            "tone": "warn",
        })
    risk_count = int(organization.get("risk_count") or 0)
    if risk_count > 0:
        out.append({
            "kind": "bureaucracy_risk",
            "title": f"{risk_count} 个机构执行风险偏高",
            "detail": str(organization.get("summary") or "空缺、超配或承办质量会增加诏旨阻力。"),
            "tone": "warn",
        })
    return out[:10]


def directive_statecraft_preflight(text: str, statecraft: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the state-machine constraints relevant to one directive.

    This is deliberately read-only: it helps the player understand which
    capacities a decree will consume before the LLM or lifecycle narrates the
    outcome.
    """

    domains = infer_directive_domains(text)
    capacity_by_domain = {
        str(row.get("domain") or ""): row
        for row in statecraft.get("capacity_rows", []) or []
        if isinstance(row, Mapping)
    }
    rows = [
        capacity_by_domain[domain]
        for domain in domains
        if domain in capacity_by_domain
    ]
    default_score = 50
    for item in statecraft.get("topbar", []) or []:
        if isinstance(item, Mapping) and str(item.get("key") or "") == "court_readiness":
            default_score = int(item.get("value") or 50)
            break
    score = _avg((int(row.get("score") or 0) for row in rows), default=default_score)
    domain_set = {str(domain) for domain in domains}
    relevant_bottlenecks: List[Dict[str, Any]] = []
    for item in statecraft.get("bottlenecks", []) or []:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "")
        if kind.startswith("capacity:") and kind.split(":", 1)[1] in domain_set:
            relevant_bottlenecks.append(dict(item))
        elif kind == "cash_gap" and "fiscal" in domain_set:
            relevant_bottlenecks.append(dict(item))
        elif kind == "army_arrears" and "military" in domain_set:
            relevant_bottlenecks.append(dict(item))
        elif kind == "building_condition" and "construction" in domain_set:
            relevant_bottlenecks.append(dict(item))
        elif kind == "bureaucracy_risk" and domain_set.intersection({"procedure", "personnel", "local"}):
            relevant_bottlenecks.append(dict(item))
    if score < 45:
        summary = "国家机器预审：相关产能断裂，此旨即使成命也很可能慢、贵或走样。"
    elif score < 62:
        summary = "国家机器预审：相关产能吃紧，宜先补人、拨款或缩小目标。"
    elif score < 78:
        summary = "国家机器预审：相关产能可用，但仍需盯住承办和复命水分。"
    else:
        summary = "国家机器预审：相关产能充足，可作为重点推进事项。"
    return {
        "domains": domains,
        "score": score,
        "status": _score_status(score),
        "tone": _score_tone(score),
        "summary": summary,
        "capacity_rows": [
            {
                "domain": str(row.get("domain") or ""),
                "label": str(row.get("label") or row.get("domain") or ""),
                "score": int(row.get("score") or 0),
                "status": str(row.get("status") or ""),
                "tone": str(row.get("tone") or ""),
                "effect": str(row.get("effect") or ""),
                "institutions": row.get("institutions") or [],
            }
            for row in rows[:4]
        ],
        "bottlenecks": relevant_bottlenecks[:4],
    }


def _bureaucracy_rows(organization: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for inst in organization.get("institutions", []) or []:
        if not isinstance(inst, dict):
            continue
        rows.append({
            "id": str(inst.get("id") or ""),
            "name": str(inst.get("name") or ""),
            "category": str(inst.get("category") or ""),
            "domains": inst.get("domains") or [],
            "readiness": int(inst.get("readiness") or 0),
            "coverage": int(inst.get("coverage") or 0),
            "holder_quality": int(inst.get("holder_quality") or 0),
            "vacancy_count": int(inst.get("vacancy_count") or 0),
            "overflow_count": int(inst.get("overflow_count") or 0),
            "risks": (inst.get("risks") or [])[:4],
            "summary": str(inst.get("summary") or ""),
        })
    rows.sort(key=lambda row: (int(row["readiness"]), -int(row["vacancy_count"]), row["name"]))
    return rows


def statecraft_center_payload(
    db: GameDB,
    state: GameState,
    *,
    fiscal: Dict[str, Any] | None = None,
    organization: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    fiscal_payload = fiscal or fiscal_center_payload(db, state)
    organization_payload = organization or organization_diagnostics(db)
    capacities = _capacity_rows(organization_payload)
    building_rows = _building_capacity_rows(db)
    accounts = fiscal_payload.get("net_by_account") or {}
    guo = accounts.get("国库") or {}
    nei = accounts.get("内库") or {}
    revenue_rows = fiscal_payload.get("revenue_family_rows") or fiscal_payload.get("revenue_sources") or []
    expense_rows = fiscal_payload.get("expense_family_rows") or fiscal_payload.get("expense_sources") or []
    tax_income = _sum_rows(revenue_rows, account="国库", family="province_tax")
    if tax_income <= 0:
        tax_income = int((fiscal_payload.get("totals") or {}).get("province_dynamic_tax") or 0)
    state_expense = _sum_rows(expense_rows, account="国库")
    court_expense = _sum_rows(expense_rows, account="内库")
    construction_output = sum(int(row.get("monthly_output") or 0) for row in building_rows)
    construction_maintenance = sum(int(row.get("maintenance") or 0) for row in building_rows)
    top_capacity = {str(row.get("domain")): row for row in capacities}
    return {
        "model": {
            "reference": "Hearts of Iron-style state machine, translated to late-Ming politics.",
            "principle": "库存看现在能撑多久，月流看自然亏盈，产能看国家机器办事能力，瓶颈解释为什么命令会慢或变形。",
            "do_not_duplicate": "本中枢只聚合 FiscalCenter 与 organization_diagnostics，不另造经济或官僚状态表。",
        },
        "topbar": [
            {"key": "treasury", "label": "国库", "value": int(guo.get("balance") or 0), "unit": "万两", "note": "朝廷公开财政库存"},
            {"key": "privy", "label": "内库", "value": int(nei.get("balance") or 0), "unit": "万两", "note": "皇帝私帑库存"},
            {"key": "treasury_net", "label": "国库月净", "value": int(guo.get("net") or 0), "unit": "万两/月", "note": "国库自然月流"},
            {"key": "court_readiness", "label": "朝廷执行力", "value": int(organization_payload.get("court_readiness") or 0), "unit": "", "note": "官僚组织综合产能"},
            {"key": "arrears", "label": "欠饷", "value": int((fiscal_payload.get("totals") or {}).get("army_arrears") or 0), "unit": "万两", "note": "军队后勤缺口"},
        ],
        "economy_lanes": [
            {
                "id": "state_revenue",
                "label": "国库税源",
                "value": tax_income,
                "unit": "万两/月",
                "capacity_domain": "fiscal",
                "capacity_score": int((top_capacity.get("fiscal") or {}).get("score") or 0),
                "note": "田赋、辽饷、盐税、商税，经省份到账率折算。",
            },
            {
                "id": "state_expense",
                "label": "国库支出",
                "value": state_expense,
                "unit": "万两/月",
                "capacity_domain": "fiscal",
                "capacity_score": int((top_capacity.get("fiscal") or {}).get("score") or 0),
                "note": "军饷、宗室、官俸、工部、赈灾、建筑维护。",
            },
            {
                "id": "privy_flow",
                "label": "内库收支",
                "value": int(nei.get("net") or 0),
                "unit": "万两/月",
                "capacity_domain": "inner",
                "capacity_score": int((top_capacity.get("inner") or {}).get("score") or 0),
                "note": "皇庄、织造、矿税、宫廷和内廷常例开支。",
            },
            {
                "id": "construction_assets",
                "label": "营造资产",
                "value": construction_output - construction_maintenance,
                "unit": "万两/月",
                "capacity_domain": "construction",
                "capacity_score": int((top_capacity.get("construction") or {}).get("score") or 0),
                "note": "建筑产出扣维护后的净贡献；状态差会形成修复压力。",
            },
        ],
        "capacity_rows": capacities,
        "building_capacity_rows": building_rows,
        "bureaucracy_rows": _bureaucracy_rows(organization_payload),
        "bottlenecks": _bottlenecks(fiscal_payload, organization_payload, capacities, building_rows),
        "source_links": {
            "fiscal": "/api/fiscal_center",
            "organizations": "/api/organizations",
            "policy": "/api/policy_center",
        },
    }
