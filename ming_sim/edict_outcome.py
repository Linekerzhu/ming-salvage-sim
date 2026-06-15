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
from typing import Dict, List, Optional

from ming_sim.models import LLMConfig
from ming_sim.token_stats import tlog

# 单诏复命：一次 LLM 调用同时产「奏报正文」与「数值 delta」，省一半调用且叙事与数值自洽。
EDICT_OUTCOME_PROMPT = """你是大明的结算判官，正为一道刚办结的旨意做复命与定数。
你会收到：旨意原文、主办官姓名官职、真实执行率 actual%、奏报执行率 reported%、
涉及地区、当前国势数值、与本旨相关的在办局势(issues)清单。

输出严格 JSON，两部分：
1. "memorial"：以主办官口吻写 60-160 字复命奏报正文（浅近文言）。这是**奏报口径(reported)**——
   若 reported 高于 actual，要把折扣粉饰过去（报喜不报忧），不得透露真实数字。
2. "delta"：这道旨意「政策本身」带来的数值后果，**按真实执行率 actual% 折算**（actual 越低后果越小）。
   只填确有因果的字段，其余省略或留空。各字段含义：
   - "metric_delta": {"民心"|"皇威": 整数}  幅度克制，单项一般 -5..+5。
   - "economy_moves": [{"account":"国库"|"内库","delta":整数(正入负支,单位万两不要乘),"category":"简短","reason":"简短"}]
   - "region_delta": {地区id: {"unrest": 整数}}  仅当本旨直接影响该地动乱。
   - "issue_advances": [{"issue_id": 整数, "delta": 0-100 推进量, "reason":"简短"}]  仅推进给定清单里的 issue。
   - "office_changes": [{"name":"被任命者","new_office":"所授官职","reason":"简短"}]  仅当本旨**任命/起复/调任**某人时填；
     name **必须是旨意原文中明确点到的人名**，不得新造人名；new_office 为所授官职（如"兵部尚书""蓟辽督师"）。
   - "character_status_changes": [{"name":"被处置者","status":"dismissed|imprisoned|exiled|retired|dead","reason":"简短"}]
     仅当本旨**罢免/革职/下狱/流放/勒令致仕/处死**某在朝大臣时填；name 须是旨意原文点到的在朝者，不得新造人名。
     对照：罢免/革职=dismissed，下狱/逮问=imprisoned，流放/充军=exiled，致仕/乞休=retired，处死/弃市/赐死=dead。
   不要凭空造 issue_id / 人名；不要超出给定地区/issue 范围。无数值后果时 "delta" 给 {}。
直接输出 JSON，不要解释、不要 markdown 代码块。"""

_FALLBACK_MEMORIAL = "臣谨奏：前奉谕旨，今已遵办完竣，地方安堵，伏乞圣鉴。"


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
    if assignee:
        orow = db.conn.execute(
            "SELECT office FROM characters WHERE name=?", (assignee,)).fetchone()
        if orow is not None:
            office = str(orow["office"] or "")
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
    return {
        "directive": str(row["text"] or "")[:300],
        "assignee": assignee,
        "office": office,
        "actual": int(row["integrity_actual"]),
        "reported": int(row["integrity_reported"]),
        "region_id": region_id,
        "region_name": region_name,
        "region_unrest": region_unrest,
        "current_metrics": metrics,
        "related_issues": related_issues,
        "allowed_issue_ids": [it["issue_id"] for it in related_issues],
    }


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
    memorial = str(data.get("memorial") or "").strip()
    delta = data.get("delta")
    if not isinstance(delta, dict):
        delta = {}
    delta = _scope_delta(delta, context)
    return memorial, delta


def _scope_delta(delta: Dict[str, object], context: Dict[str, object]) -> Dict[str, object]:
    """护栏：issue_advances 只许命中白名单 issue_id；office_changes 的 name 必须在诏书原文出现
    （防 LLM 乱编人名导致 apply 建出幽灵角色）。幅度由 apply_score_extraction 侧再 clamp。"""
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
    _VALID_STATUS = {"dismissed", "imprisoned", "exiled", "retired", "dead"}
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
        memorial = _FALLBACK_MEMORIAL
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
