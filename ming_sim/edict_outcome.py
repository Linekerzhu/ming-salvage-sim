"""即时复命（变集中反馈为即时）：诏书到期，当场把结果报给皇帝。L7。

旧模型：诏书效果攒到月末由 simulator 写邸报、extractor 抽 delta 一把结算（集中反馈）。
新模型：每道旨意各自到期（lifecycle done）时，由后台 worker 跑一次「单诏复命」——
  1) narrator：以主办官口吻写复命奏报（按 reported 口径，账实分离 P3：报喜不报忧）；
  2) extractor：估这道旨意「政策本身」按真实执行率(actual%)折算的数值后果，暂存 outcome_delta；
结果以复命奏报落御案，数值由主线程 session.drain_pending_outcomes 落库（worker 无 GameState）。

worker 侧自包含：只用 llm_config + GameDB 连接，不依赖 agno_db / content / registry /
simulator_payload。LLM 不可用或失败 → 模板复命 + 空 delta（游戏不停摆，数值不乱动）。
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from ming_sim.models import LLMConfig
from ming_sim.token_stats import tlog

# 单诏复命：一次 LLM 调用同时产「奏报正文」与「数值 delta」，省一半调用且叙事与数值自洽。
EDICT_OUTCOME_PROMPT = """你是大明的结算判官，正为一道刚办结的旨意做复命与定数。
你会收到：旨意原文、主办官姓名官职、主办官话语身份合约、真实执行率 actual%、奏报执行率 reported%、
涉及地区、当前国势数值、与本旨相关的在办局势(issues)清单。

输出严格 JSON，两部分：
1. "memorial"：以主办官口吻写 60-160 字复命奏报正文（浅近文言）。必须遵守 speaker_contract：
   内廷/太监只能自称「奴婢/奴才」，不得写「臣谨奏」「微臣」；外朝官员才用「臣」。
   这是**奏报口径(reported)**——若 reported 高于 actual，要把折扣粉饰过去（报喜不报忧），不得透露真实数字。
2. "delta"：这道旨意「政策本身」带来的数值后果，**按真实执行率 actual% 折算**（actual 越低后果越小）。
   只填确有因果的字段，其余省略或留空。各字段含义：
   - "metric_delta": {"民心"|"皇威": 整数}  幅度克制，单项一般 -5..+5。
   - "economy_moves": [{"account":"国库"|"内库","delta":整数(正入负支,单位万两不要乘),"category":"简短","reason":"简短","purpose":"补饷|其它(可省略)"}]
     若旨意明写"合计X万两"这类硬数，负向拨款合计必须与原文一致；涉及辽饷/京营/欠饷/军饷的国库支出可标 purpose="补饷"。
   - "region_delta": {地区id: {"unrest": 整数}}  仅当本旨直接影响该地动乱。
   - "issue_advances": [{"issue_id": 整数, "delta_bar": -50..50 推进量, "reason":"简短", "stage_text":"简短新态势", "narrative":"简短"}]
     仅推进给定清单里的 issue；delta_bar 为本旨带来的局势条变化，正数代表向解决推进。
   - "office_changes": [{"name":"被任命者","new_office":"所授官职","reason":"简短"}]  仅当本旨**任命/起复/调任**某人时填；
     name **必须是旨意原文中明确点到的人名**，不得新造人名；new_office 为所授官职（如"兵部尚书""蓟辽督师"）。
   - "character_status_changes": [{"name":"被处置者","status":"dismissed|imprisoned|exiled|retired|dead|castrated","reason":"简短"}]
     仅当本旨**罢免/革职/下狱/流放/勒令致仕/处死/处宫刑**某在朝大臣时填；name 须是旨意原文点到的在朝者，不得新造人名。
     对照：罢免/革职=dismissed，下狱/逮问=imprisoned，流放/充军=exiled，致仕/乞休=retired，处死/弃市/赐死=dead，**处宫刑/腐刑/发净军/没入内廷为奴=castrated**。
   不要凭空造 issue_id / 人名；不要超出给定地区/issue 范围。无数值后果时 "delta" 给 {}。
直接输出 JSON，不要解释、不要 markdown 代码块。"""

_FALLBACK_MINISTER_MEMORIAL = "臣谨奏：前奉谕旨，今已遵办完竣，地方安堵，伏乞圣鉴。"
_FALLBACK_EUNUCH_MEMORIAL = "奴婢谨奏：前奉谕旨，今已遵办完竣，诸事已有回报，伏乞圣鉴。"
_EUNUCH_ROLE_RE = re.compile(r"太监|宦官|内官|司礼监|东厂|内廷|秉笔|掌印|随堂|御马监|御用监|尚膳监|内官监|御前")


_CN_NUM = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}
_MONEY_NUM = r"([零〇一二两三四五六七八九十百千\d]{1,8})"


def _cn_number_to_int(raw: str) -> int:
    text = str(raw or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    if text == "廿":
        return 20
    if text == "卅":
        return 30
    total = 0
    number = 0
    for ch in text:
        if ch.isdigit():
            number = number * 10 + int(ch)
        elif ch in _CN_NUM:
            number = _CN_NUM[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            total += (number or 1) * unit
            number = 0
    return total + number


def _normalize_issue_advances(delta: Dict[str, object]) -> None:
    advances = delta.get("issue_advances")
    if not isinstance(advances, list):
        return
    normalized: List[Dict[str, object]] = []
    for item in advances:
        if not isinstance(item, dict):
            continue
        copy = dict(item)
        if "delta_bar" not in copy and "delta" in copy:
            copy["delta_bar"] = copy.get("delta")
        normalized.append(copy)
    delta["issue_advances"] = normalized


def _explicit_money_plan(text: str) -> Optional[Dict[str, int]]:
    """Return hard-money instruction totals from decree text when it is explicit enough.

    This deliberately handles only clear "合计X万两" style decrees. Broader fiscal
    interpretation remains with the model; hard totals should not drift in JSON.
    """
    decree = str(text or "")
    if not any(k in decree for k in ("合计", "共计")):
        return None
    if not any(k in decree for k in ("拨", "支", "发", "赈", "饷", "给")):
        return None
    match = re.search(rf"(?:合计|共计)\s*{_MONEY_NUM}\s*万两", decree)
    if not match:
        return None
    total = _cn_number_to_int(match.group(1))
    if total <= 0:
        return None
    inner = 0
    inner_match = re.search(rf"内库(?:借支|动支|支|拨|出)?\s*{_MONEY_NUM}\s*万两", decree)
    if inner_match:
        inner = _cn_number_to_int(inner_match.group(1))
    inner = max(0, min(total, inner))
    return {"total": total, "inner": inner, "treasury": max(0, total - inner)}


def _normalize_explicit_economy_moves(delta: Dict[str, object], context: Dict[str, object]) -> None:
    plan = _explicit_money_plan(str(context.get("directive") or ""))
    if not plan:
        return
    decree = str(context.get("directive") or "")
    category = "军饷" if any(k in decree for k in ("辽饷", "京营", "欠饷", "军饷")) else "拨款"
    reason = re.sub(r"\s+", "", decree)[:80] or "奉旨拨款"
    kept: List[Dict[str, object]] = []
    for move in delta.get("economy_moves") or []:
        if not isinstance(move, dict):
            continue
        try:
            move_delta = int(move.get("delta") or 0)
        except (TypeError, ValueError):
            continue
        # Positive income/side effects may coexist with a hard spending plan.
        if move_delta > 0:
            kept.append(dict(move))
    moves: List[Dict[str, object]] = []
    if plan["treasury"] > 0:
        treasury_move: Dict[str, object] = {
            "account": "国库",
            "delta": -int(plan["treasury"]),
            "category": category,
            "reason": reason,
        }
        if category == "军饷":
            treasury_move["purpose"] = "补饷"
        moves.append(treasury_move)
    if plan["inner"] > 0:
        moves.append({
            "account": "内库",
            "delta": -int(plan["inner"]),
            "category": "借支",
            "reason": reason,
        })
    delta["economy_moves"] = kept + moves


def _is_eunuch_assignee(context: Dict[str, object]) -> bool:
    identity = " ".join(
        str(context.get(key) or "")
        for key in ("assignee", "office", "office_type", "faction")
    )
    return bool(_EUNUCH_ROLE_RE.search(identity))


def _speaker_contract(context: Dict[str, object]) -> str:
    assignee = str(context.get("assignee") or "主办官").strip() or "主办官"
    office = str(context.get("office") or "").strip()
    label = f"{assignee}（{office}）" if office else assignee
    if _is_eunuch_assignee(context):
        return (
            f"{label}是内廷/太监主办。复命第一人称必须用「奴婢」或「奴才」；"
            "不得用「臣」「微臣」「愚臣」。口气应像近侍回奏：短、低、近，"
            "先说奉旨已办到哪一步，再说可继续传问、催办或复查。"
        )
    return (
        f"{label}是外朝/军镇/地方主办。复命第一人称用「臣」「微臣」；"
        "不得用「奴婢」「奴才」。"
    )


def _fallback_memorial(context: Dict[str, object]) -> str:
    return _FALLBACK_EUNUCH_MEMORIAL if _is_eunuch_assignee(context) else _FALLBACK_MINISTER_MEMORIAL


def _normalize_memorial_voice(memorial: str, context: Dict[str, object]) -> str:
    """Keep persisted outcome memory aligned with the assignee's current speech role."""
    text = re.sub(r"\s+", "", str(memorial or "").strip())
    if not text:
        return ""
    if _is_eunuch_assignee(context):
        text = text.replace("微臣", "奴婢").replace("愚臣", "奴婢")
        text = re.sub(r"^臣(?=谨奏|奉旨|遵旨|已|今|不敢|闻|请|当|愿|即|所|查|抵|承)", "奴婢", text)
        text = re.sub(r"^臣([一-龥]{1,4})(?=谨奏|叩奏|奏)", r"奴婢\1", text)
        replacements = {
            "，臣谨": "，奴婢谨",
            "。臣谨": "。奴婢谨",
            "，臣已": "，奴婢已",
            "。臣已": "。奴婢已",
            "，臣奉": "，奴婢奉",
            "。臣奉": "。奴婢奉",
            "，臣不敢": "，奴婢不敢",
            "。臣不敢": "。奴婢不敢",
            "，臣请": "，奴婢请",
            "。臣请": "。奴婢请",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
    else:
        text = text.replace("奴婢谨奏", "臣谨奏").replace("奴才谨奏", "臣谨奏")
    return text


def _directive_context(db, did: int) -> Optional[Dict[str, object]]:
    row = db.conn.execute(
        "SELECT text, category, assignee, integrity_actual, integrity_reported, chain "
        "FROM turn_directives WHERE id=?", (did,)
    ).fetchone()
    if row is None:
        return None
    try:
        meta = json.loads(row["chain"] or "{}")
    except ValueError:
        meta = {}
    region_id = str(meta.get("region_id") or "")
    region_name, region_unrest = "", None
    if region_id:
        rr = db.conn.execute(
            "SELECT name, unrest FROM regions WHERE id=?", (region_id,)).fetchone()
        if rr is not None:
            region_name = str(rr["name"])
            region_unrest = int(rr["unrest"])
    assignee = str(row["assignee"] or "")
    office = ""
    office_type = ""
    faction = ""
    if assignee:
        orow = db.conn.execute(
            "SELECT office, office_type, faction FROM characters WHERE name=?", (assignee,)).fetchone()
        if orow is not None:
            office = str(orow["office"] or "")
            office_type = str(orow["office_type"] or "")
            faction = str(orow["faction"] or "")
    # 与本旨相关的在办局势：同地区优先，给 extractor 一个可推进的 issue_id 白名单
    issue_rows = db.conn.execute(
        "SELECT id, title FROM issues WHERE status='active' ORDER BY "
        "CASE WHEN region_hint=? THEN 0 ELSE 1 END, id DESC LIMIT 8",
        (region_id,),
    ).fetchall()
    related_issues = [{"issue_id": int(r["id"]), "title": str(r["title"])} for r in issue_rows]
    # 当前国势（只读，给 LLM 把握 delta 幅度，不写库）
    metrics: Dict[str, int] = {}
    try:
        st = db.load_state()
        metrics = {k: int(v) for k, v in st.metrics.items()}
    except Exception:
        metrics = {}
    context = {
        "directive": str(row["text"] or "")[:300],
        "assignee": assignee,
        "office": office,
        "office_type": office_type,
        "faction": faction,
        "speaker_contract": "",
        "actual": int(row["integrity_actual"]),
        "reported": int(row["integrity_reported"]),
        "region_id": region_id,
        "region_name": region_name,
        "region_unrest": region_unrest,
        "current_metrics": metrics,
        "related_issues": related_issues,
        "allowed_issue_ids": [it["issue_id"] for it in related_issues],
    }
    context["speaker_contract"] = _speaker_contract(context)
    return context


def _run_outcome_llm(llm_config: Optional[LLMConfig], context: Dict[str, object]) -> tuple[str, Dict[str, object]]:
    """返回 (复命正文, delta dict)。LLM 不可用/失败/解析失败 → 模板正文 + 空 delta。"""
    if llm_config is None:
        return "", {}
    from ming_sim.scheduler import _one_shot_agent
    from ming_sim.agents import run_agent_text, parse_agent_json
    try:
        agent = _one_shot_agent(llm_config, agent_id="edict-outcome",
                                prompt=EDICT_OUTCOME_PROMPT, minimum=500)
        raw = run_agent_text(
            agent, json.dumps(context, ensure_ascii=False, sort_keys=False), tag="edict-outcome")
        data = parse_agent_json(raw, "即时复命")
    except Exception as exc:
        tlog(f"[edict_outcome] LLM/解析失败，走模板兜底：{exc}")
        return "", {}
    memorial = _normalize_memorial_voice(str(data.get("memorial") or "").strip(), context)
    delta = data.get("delta")
    if not isinstance(delta, dict):
        delta = {}
    delta = _scope_delta(delta, context)
    return memorial, delta


def _scope_delta(delta: Dict[str, object], context: Dict[str, object]) -> Dict[str, object]:
    """护栏：issue_advances 只许命中白名单 issue_id；office_changes 的 name 必须在诏书原文出现
    （防 LLM 乱编人名导致 apply 建出幽灵角色）。幅度由 apply_score_extraction 侧再 clamp。"""
    _normalize_issue_advances(delta)
    _normalize_explicit_economy_moves(delta, context)
    allowed = set(int(i) for i in (context.get("allowed_issue_ids") or []))
    advances = delta.get("issue_advances")
    if isinstance(advances, list) and allowed:
        delta["issue_advances"] = [
            a for a in advances
            if isinstance(a, dict) and int(a.get("issue_id") or 0) in allowed
        ]
    elif advances is not None and not allowed:
        delta["issue_advances"] = []
    # office_changes / character_status_changes：仅保留名字在诏书原文里出现的项
    # （任命/处置的人须是旨意明确点到的，防 LLM 乱编人名建幽灵角色或误伤）。
    text = str(context.get("directive") or "")
    ocs = delta.get("office_changes")
    if isinstance(ocs, list):
        delta["office_changes"] = [
            o for o in ocs
            if isinstance(o, dict) and str(o.get("name") or "").strip()
            and str(o.get("name")).strip() in text and str(o.get("new_office") or "").strip()
        ]
    _VALID_STATUS = {"dismissed", "imprisoned", "exiled", "retired", "dead", "castrated"}
    cscs = delta.get("character_status_changes")
    if isinstance(cscs, list):
        delta["character_status_changes"] = [
            s for s in cscs
            if isinstance(s, dict) and str(s.get("name") or "").strip()
            and str(s.get("name")).strip() in text
            and str(s.get("status") or "").strip().lower() in _VALID_STATUS
        ]
    return delta


def handle_edict_outcome(db, llm_config: Optional[LLMConfig], payload: Dict[str, object]) -> str:
    """worker handler：跑单诏复命，暂存 delta + 落复命奏报到御案。幂等（outcome_status 闸门）。"""
    did = int(payload.get("directive_id") or 0)
    if did <= 0:
        return ""
    # 幂等：已抽取/已落库的不重复（worker 可能重试）
    guard = db.conn.execute(
        "SELECT outcome_status FROM turn_directives WHERE id=?", (did,)).fetchone()
    if guard is None or str(guard["outcome_status"] or ""):
        return ""
    context = _directive_context(db, did)
    if context is None:
        return ""
    memorial, delta = _run_outcome_llm(llm_config, context)
    if not memorial:
        memorial = _fallback_memorial(context)
    else:
        memorial = _normalize_memorial_voice(memorial, context)
    # 暂存 delta，置 extracted（CAS 防并发重复）
    db.conn.execute(
        "UPDATE turn_directives SET outcome_delta=?, outcome_status='extracted', settle_note=? "
        "WHERE id=? AND outcome_status=''",
        (json.dumps(delta, ensure_ascii=False), memorial[:2000], did),
    )
    db.conn.commit()
    # 复命奏报落御案（即时反馈：到期了，结果在你案头）
    try:
        from ming_sim.memorials import create_memorial
        from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int
        day = kv_int(db, KV_CURRENT_DAY, 0)
        create_memorial(
            db, None, day=day, author_name=str(context.get("assignee") or ""),
            org=str(context.get("office") or ""),
            kind="复命", urgency=2,
            summary=f"复命：{str(context.get('directive') or '')[:20]}",
            full_text=memorial, ref_kind="directive", ref_id=str(did))
    except Exception as exc:
        tlog(f"[edict_outcome] 复命奏报入御案失败：{exc}")
    return memorial


try:
    from ming_sim.scheduler import register_handler as _register_handler
    _register_handler("edict_outcome", handle_edict_outcome)
except Exception:
    pass
