"""Explainable fiscal-center payloads.

The budget source of truth stays in ``flows.compute_budget_lines``. This module
only turns that budget plus province, army, and ledger rows into a UI-friendly
accounting panel.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ming_sim.db import GameDB
from ming_sim.flows import calc_province_fiscal, compute_budget_lines
from ming_sim.models import GameState


_TAX_STREAMS = (
    ("田赋", "田赋基数", "省级 tax_per_turn 扣除辽饷、盐税、商税后的田赋残差，再按到账率折算。"),
    ("辽饷", "辽饷基数", "各省 fiscal.liao_xiang 月摊派，另受皇威影响折算。"),
    ("盐税", "盐税基数", "各省 fiscal.salt_tax 月税基，按腐败、士绅、动乱折算。"),
    ("商税", "商税基数", "各省 fiscal.commerce_tax 月税基，按腐败、士绅、动乱折算。"),
)

_EXPENSE_EXPLAINERS = {
    "各军军饷": ("army_pay", "明军每月维护费。国库余额不足时，不会凭空发完，会转成各军 arrears 欠饷。"),
    "宗室禄米": ("clan_stipend", "宗藩固定禄米，属于国库刚性支出。"),
    "百官俸禄": ("official_salary", "官僚体系俸禄，维持朝廷和地方日常运转。"),
    "工部": ("works", "工部日常维护与工程口支出。"),
    "赈灾备用": ("relief_reserve", "制度性赈济预留，不是单次事件。"),
    "建筑维护": ("building_maintenance", "建筑每月维护费。内廷建筑扣内库，其余扣国库。"),
    "宫廷开支": ("palace_cost", "皇室日常用度。"),
    "内廷俸禄": ("palace_salary", "太监宫女等内廷人员月俸。"),
    "妃嫔供奉": ("consort_allowance", "后宫月度供奉。"),
    "恩赏赍予": ("imperial_gifts", "内帑赏赍宗藩、勋戚、近侍的常例开支。"),
}


def _turn_payload(state: GameState) -> Dict[str, int]:
    return {"year": int(state.year), "period": int(state.period), "turn": int(state.turn)}


def _budget_source_rows(budget: Dict[str, Dict[str, list]], direction: str) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for account in ("国库", "内库"):
        for line in budget.get(account, {}).get(direction, []):
            rows.append({
                "account": account,
                "direction": direction,
                "name": str(line.get("name") or ""),
                "amount": int(line.get("amount") or 0),
                "note": str(line.get("note") or ""),
            })
    return rows


def _amount_text(amount: int) -> str:
    sign = "+" if amount > 0 else ""
    return f"{sign}{int(amount)}万两"


def _join_top_amounts(rows: List[Dict[str, object]], *, limit: int = 3) -> str:
    top = sorted(rows, key=lambda item: int(item.get("amount") or 0), reverse=True)[:limit]
    if not top:
        return "无"
    return "、".join(f"{row.get('name')} {int(row.get('amount') or 0)}万两" for row in top)


def _net_by_account(
    budget: Dict[str, Dict[str, list]],
    state: GameState,
) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for account in ("国库", "内库"):
        income_total = sum(int(line.get("amount") or 0) for line in budget.get(account, {}).get("income", []))
        expense_total = sum(int(line.get("amount") or 0) for line in budget.get(account, {}).get("expense", []))
        balance = int(state.metrics.get(account, 0))
        net = income_total - expense_total
        out[account] = {
            "balance": balance,
            "income_total": income_total,
            "expense_total": expense_total,
            "net": net,
            "operating_gap": max(0, expense_total - income_total),
            "cash_gap_next_month": max(0, expense_total - income_total - balance),
            "projected_balance": max(0, balance + net),
        }
    return out


def _revenue_family_rows(
    budget: Dict[str, Dict[str, list]],
    province_tax_rows: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for name, base_key, note in _TAX_STREAMS:
        rows.append({
            "account": "国库",
            "direction": "income",
            "family": "province_tax",
            "name": name,
            "amount": sum(int(row.get(name) or 0) for row in province_tax_rows),
            "base_amount": sum(int(row.get(base_key) or 0) for row in province_tax_rows),
            "note": note,
        })

    for line in _budget_source_rows(budget, "income"):
        name = str(line.get("name") or "")
        if name == "田赋辽饷盐商":
            continue
        if name == "皇庄":
            family = "privy_estate"
        elif name == "建筑产出":
            family = "building_output"
        else:
            family = "fixed_income"
        rows.append({
            **line,
            "family": family,
            "base_amount": int(line.get("amount") or 0),
        })
    return rows


def _expense_family_rows(budget: Dict[str, Dict[str, list]]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for line in _budget_source_rows(budget, "expense"):
        name = str(line.get("name") or "")
        family, explanation = _EXPENSE_EXPLAINERS.get(
            name,
            ("fixed_expense", str(line.get("note") or "月度固定支出。")),
        )
        rows.append({
            **line,
            "family": family,
            "why": explanation,
            "note": str(line.get("note") or explanation),
        })
    return rows


def _army_pay_rows(db: GameDB) -> List[Dict[str, object]]:
    rows = db.conn.execute(
        "SELECT id, name, station, theater, commander, controller, maintenance_per_turn, "
        "supply, morale, arrears, autonomy, status "
        "FROM armies WHERE owner_power='ming' "
        "ORDER BY arrears DESC, maintenance_per_turn DESC, id"
    ).fetchall()
    out: List[Dict[str, object]] = []
    for row in rows:
        arrears = int(row["arrears"] or 0)
        morale = int(row["morale"] or 0)
        if arrears > 0:
            pay_status = "欠饷"
        elif morale < 35:
            pay_status = "军心危"
        else:
            pay_status = "足饷"
        out.append({
            "id": str(row["id"]),
            "name": str(row["name"]),
            "station": str(row["station"] or ""),
            "theater": str(row["theater"] or ""),
            "commander": str(row["commander"] or ""),
            "controller": str(row["controller"] or ""),
            "monthly_pay": int(row["maintenance_per_turn"] or 0),
            "arrears": arrears,
            "supply": int(row["supply"] or 0),
            "morale": morale,
            "autonomy": int(row["autonomy"] or 0),
            "status": str(row["status"] or ""),
            "pay_status": pay_status,
        })
    return out


def _ledger_movements(db: GameDB, *, limit: int = 80) -> List[Dict[str, object]]:
    rows = db.conn.execute(
        "SELECT id, turn, year, period, account, delta, balance_after, category, reason, "
        "purpose, target_kind, target_id, created_at "
        "FROM economy_ledger ORDER BY id DESC LIMIT ?",
        (max(1, min(200, int(limit))),),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "turn": int(row["turn"]),
            "year": int(row["year"]),
            "period": int(row["period"]),
            "account": str(row["account"]),
            "delta": int(row["delta"]),
            "balance_after": int(row["balance_after"]),
            "category": str(row["category"] or ""),
            "reason": str(row["reason"] or ""),
            "purpose": str(row["purpose"] or ""),
            "target_kind": str(row["target_kind"] or ""),
            "target_id": str(row["target_id"] or ""),
            "created_at": str(row["created_at"] or ""),
        }
        for row in rows
    ]


def _ledger_summary(movements: List[Dict[str, object]], *, limit: int = 12) -> Dict[str, object]:
    actual_movements = [row for row in movements if str(row.get("category") or "") != "期初"]
    recent = actual_movements[:max(1, int(limit))]
    income_total = sum(max(0, int(row.get("delta") or 0)) for row in recent)
    expense_total = sum(max(0, -int(row.get("delta") or 0)) for row in recent)
    by_account: Dict[str, Dict[str, int]] = {
        "国库": {"income": 0, "expense": 0, "net": 0},
        "内库": {"income": 0, "expense": 0, "net": 0},
    }
    for row in recent:
        account = str(row.get("account") or "")
        delta = int(row.get("delta") or 0)
        if account not in by_account:
            by_account[account] = {"income": 0, "expense": 0, "net": 0}
        if delta >= 0:
            by_account[account]["income"] += delta
        else:
            by_account[account]["expense"] += -delta
        by_account[account]["net"] += delta
    largest_expenses = sorted(
        [row for row in recent if int(row.get("delta") or 0) < 0],
        key=lambda row: abs(int(row.get("delta") or 0)),
        reverse=True,
    )[:5]
    largest_incomes = sorted(
        [row for row in recent if int(row.get("delta") or 0) > 0],
        key=lambda row: int(row.get("delta") or 0),
        reverse=True,
    )[:5]
    return {
        "window": f"最近{len(recent)}笔" if recent else "暂无近期",
        "recent_count": len(recent),
        "income_total": income_total,
        "expense_total": expense_total,
        "net": income_total - expense_total,
        "by_account": by_account,
        "largest_incomes": largest_incomes,
        "largest_expenses": largest_expenses,
    }


def _account_cards(
    net_by_account: Dict[str, Dict[str, int]],
    revenue_rows: List[Dict[str, object]],
    expense_rows: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    cards: List[Dict[str, object]] = []
    for account in ("国库", "内库"):
        net = net_by_account.get(account, {})
        incomes = [row for row in revenue_rows if row.get("account") == account]
        expenses = [row for row in expense_rows if row.get("account") == account]
        monthly_net = int(net.get("net") or 0)
        if monthly_net > 0:
            headline = f"{account}本月自然盈余 {_amount_text(monthly_net)}"
        elif monthly_net < 0:
            headline = f"{account}本月自然亏空 {_amount_text(monthly_net)}"
        else:
            headline = f"{account}本月收支相抵"
        cards.append({
            "account": account,
            "balance": int(net.get("balance") or 0),
            "income_total": int(net.get("income_total") or 0),
            "expense_total": int(net.get("expense_total") or 0),
            "net": monthly_net,
            "projected_balance": int(net.get("projected_balance") or 0),
            "cash_gap_next_month": int(net.get("cash_gap_next_month") or 0),
            "top_revenue": sorted(incomes, key=lambda row: int(row.get("amount") or 0), reverse=True)[:4],
            "top_expense": sorted(expenses, key=lambda row: int(row.get("amount") or 0), reverse=True)[:4],
            "headline": headline,
        })
    return cards


def _shortage_explainers(
    province_tax_rows: List[Dict[str, object]],
    army_rows: List[Dict[str, object]],
    net_by_account: Dict[str, Dict[str, int]],
) -> List[Dict[str, object]]:
    factors: List[Dict[str, object]] = []
    weak_tax = sorted(
        province_tax_rows,
        key=lambda row: (float(row.get("efficiency") or 0), -int(row.get("tax_base") or 0)),
    )[:4]
    for row in weak_tax:
        if float(row.get("efficiency") or 0) >= 0.35:
            continue
        factors.append({
            "kind": "province_efficiency",
            "label": f"{row.get('name')}到账率低",
            "detail": (
                f"到账率{float(row.get('efficiency') or 0):.2f}，"
                f"士绅{row.get('gentry_resistance')}，腐败{row.get('corruption')}，动乱{row.get('unrest')}"
            ),
            "region_id": row.get("region_id"),
        })
    arrears_total = sum(int(row.get("arrears") or 0) for row in army_rows)
    if arrears_total > 0:
        factors.append({
            "kind": "army_arrears",
            "label": f"军饷欠发 {arrears_total} 万两",
            "detail": "欠饷会压低士气，并在月结后继续累积到财政约束面板。",
        })
    gk_gap = int(net_by_account.get("国库", {}).get("operating_gap") or 0)
    if gk_gap > 0:
        factors.append({
            "kind": "treasury_gap",
            "label": f"国库月度缺口 {gk_gap} 万两",
            "detail": "若无加税、裁支、内帑拨补或一次性进账，下月仍会亏空。",
        })
    return factors


def _money_questions(
    net_by_account: Dict[str, Dict[str, int]],
    revenue_rows: List[Dict[str, object]],
    expense_rows: List[Dict[str, object]],
    ledger_summary: Dict[str, object],
    arrears_total: int,
) -> List[Dict[str, object]]:
    guo_revenue = [row for row in revenue_rows if row.get("account") == "国库"]
    nei_revenue = [row for row in revenue_rows if row.get("account") == "内库"]
    guo_expense = [row for row in expense_rows if row.get("account") == "国库"]
    nei_expense = [row for row in expense_rows if row.get("account") == "内库"]
    gk = net_by_account.get("国库", {})
    nk = net_by_account.get("内库", {})
    ledger_net = int(ledger_summary.get("net") or 0)
    largest_expenses = ledger_summary.get("largest_expenses") or []
    largest_incomes = ledger_summary.get("largest_incomes") or []
    latest_spend = _join_top_amounts([
        {"name": row.get("reason") or row.get("category") or row.get("account"), "amount": abs(int(row.get("delta") or 0))}
        for row in largest_expenses  # type: ignore[union-attr]
    ], limit=2)
    latest_income = _join_top_amounts([
        {"name": row.get("reason") or row.get("category") or row.get("account"), "amount": int(row.get("delta") or 0)}
        for row in largest_incomes  # type: ignore[union-attr]
    ], limit=2)

    balance_lines = [
        f"国库月净 {_amount_text(int(gk.get('net') or 0))}，内库月净 {_amount_text(int(nk.get('net') or 0))}。",
        f"{ledger_summary.get('window')}流水净变动 {_amount_text(ledger_net)}，收入 {int(ledger_summary.get('income_total') or 0)}万两，支出 {int(ledger_summary.get('expense_total') or 0)}万两。",
    ]
    if latest_spend != "无":
        balance_lines.append(f"最近大额支出：{latest_spend}。")
    if latest_income != "无":
        balance_lines.append(f"最近大额收入：{latest_income}。")
    if arrears_total > 0:
        balance_lines.append(f"另有军饷欠发 {arrears_total}万两，没有从余额里扣完，而是挂到各军欠饷。")

    return [
        {
            "id": "make_money",
            "title": "怎么赚钱",
            "answer": (
                f"国库靠省份税源，每月约 {int(gk.get('income_total') or 0)}万两："
                f"{_join_top_amounts(guo_revenue, limit=4)}。"
                f"内库靠皇庄、织造、矿税等，每月约 {int(nk.get('income_total') or 0)}万两："
                f"{_join_top_amounts(nei_revenue, limit=4)}。"
            ),
            "lines": [
                "省份税不是固定到账，腐败、士绅阻力、动乱会压低到账率。",
                "皇庄、织造、矿税走内库，和国库税收分账。",
            ],
        },
        {
            "id": "spend_money",
            "title": "钱花在哪",
            "answer": (
                f"国库月支 {int(gk.get('expense_total') or 0)}万两，主要是"
                f"{_join_top_amounts(guo_expense, limit=4)}。"
                f"内库月支 {int(nk.get('expense_total') or 0)}万两，主要是"
                f"{_join_top_amounts(nei_expense, limit=4)}。"
            ),
            "lines": [
                "军饷是逐军结算：钱不够时形成欠饷，不会在账面假装发足。",
                "宗室、官俸、宫廷和恩赏属于月度常例开支。",
            ],
        },
        {
            "id": "balance_change",
            "title": "为什么余额变了",
            "answer": "月预算解释下月自然收支，经济流水解释余额刚刚为什么增减。",
            "lines": balance_lines,
        },
    ]


def fiscal_center_payload(db: GameDB, state: GameState) -> Dict[str, Any]:
    """Return the fiscal-center payload used by API and policy-center surfaces."""

    budget = compute_budget_lines(db, state)
    _guo, _nei, province_tax_rows = calc_province_fiscal(state, db)
    army_rows = _army_pay_rows(db)
    net = _net_by_account(budget, state)
    revenue_rows = _revenue_family_rows(budget, province_tax_rows)
    expense_rows = _expense_family_rows(budget)
    ledger_movements = _ledger_movements(db)
    ledger_summary = _ledger_summary(ledger_movements)
    arrears_total = sum(int(row.get("arrears") or 0) for row in army_rows)
    province_dynamic_total = sum(int(row.get("province_total") or 0) for row in province_tax_rows)
    return {
        "turn": _turn_payload(state),
        "unit": "万两/月",
        "revenue_sources": _budget_source_rows(budget, "income"),
        "expense_sources": _budget_source_rows(budget, "expense"),
        "revenue_family_rows": revenue_rows,
        "expense_family_rows": expense_rows,
        "province_tax_rows": province_tax_rows,
        "army_pay_rows": army_rows,
        "net_by_account": net,
        "account_cards": _account_cards(net, revenue_rows, expense_rows),
        "ledger_movements": ledger_movements,
        "ledger_summary": ledger_summary,
        "policy_modifiers": db.legacy_modifiers(state),
        "player_model": {
            "monthly_budget": "compute_budget_lines 是下月自然收支预告，统一口径为月度万两。",
            "ledger": "economy_ledger 是余额变动流水，月结税收、俸禄、军饷和一次性拨款都会在这里落账。",
            "accounts": "国库管朝廷公开财政；内库管皇帝私帑，二者收入和支出分开解释。",
            "arrears": "军饷不足时，缺口进入 armies.arrears；这解释了为什么钱没扣完但军队仍在欠饷。",
        },
        "totals": {
            "province_dynamic_tax": province_dynamic_total,
            "army_arrears": arrears_total,
            "ming_army_monthly_pay": sum(int(row.get("monthly_pay") or 0) for row in army_rows),
            "operating_gap": sum(int(item.get("operating_gap") or 0) for item in net.values()),
            "cash_gap_next_month": sum(int(item.get("cash_gap_next_month") or 0) for item in net.values()),
        },
        "explainers": _shortage_explainers(province_tax_rows, army_rows, net),
        "money_questions": _money_questions(net, revenue_rows, expense_rows, ledger_summary, arrears_total),
    }
