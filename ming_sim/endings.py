"""结局判定与总评（从 decree.resolve_directives 提取，供月末结算与即时复命共用）。L7。

变集中反馈为即时反馈后，结局不再只在月末判：每条诏书到期 drain 落 delta 后、
以及每日 tick 末都可能触发结局。本模块是唯一判定入口，幂等（state.ended 闸门）。
"""

from __future__ import annotations

import json
from typing import Callable, Dict, Optional

from agno.db.sqlite import SqliteDb

from ming_sim.agents import create_ending_summary_agent, run_agent_text
from ming_sim.context import (
    ENDING_ONGOING,
    ENDING_TIMEOUT,
    victory_status,
)
from ming_sim.db import GameDB
from ming_sim.memories import build_timeline
from ming_sim.models import GameState, LLMConfig
from ming_sim.token_stats import tlog

# 20 年自动结算：开局 1627.10（turn=1），每回合 +1 月。满 240 回合（1647.09）仍未分胜负则强制收尾。
TIMEOUT_TURN = 240


def _noop(kind: str, data: str) -> None:
    pass


def evaluate_and_finalize(
    db: GameDB,
    state: GameState,
    llm_config: LLMConfig,
    agno_db: SqliteDb,
    *,
    applied: Optional[Dict[str, object]] = None,
    emit: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, object]:
    """结局判定：叙事型（退位/自尽，applied 已带）→ 数值型（京畿失守）→ 到期型（20 年/240 回合）。
    已 ended 的存档不重判、不重生总评（省 token、不反复弹页）。

    返回 {"ended": bool, "outcome": dict|None, "ending_text": str}。
    ended 时已置 state.ended / state.ending_status（调用方负责 save_state）。"""
    _emit = emit or _noop
    if state.ended:
        return {"ended": False, "outcome": None, "ending_text": ""}

    applied = applied or {}
    outcome = applied.get("victory_status") or victory_status(db, state)
    if (
        isinstance(outcome, dict)
        and outcome.get("status") == ENDING_ONGOING
        and state.turn >= TIMEOUT_TURN
    ):
        outcome = {
            "status": ENDING_TIMEOUT,
            "summary": "崇祯在位二十载，朝局至此尘埃落定，是中兴、是苟延、还是衰亡，自有史评。",
        }

    ended = isinstance(outcome, dict) and outcome.get("status") != ENDING_ONGOING
    ending_text = ""
    if ended:
        db.record_log(state, f"结局判定：{outcome.get('summary', '')}")
        ending_text = generate_ending_summary(db, state, llm_config, agno_db, outcome, _emit)
        state.ended = True
        state.ending_status = str(outcome.get("status") or "")
    return {"ended": ended, "outcome": outcome if ended else None, "ending_text": ending_text}


def generate_ending_summary(
    db: GameDB,
    state: GameState,
    llm_config: LLMConfig,
    agno_db: SqliteDb,
    outcome: Dict[str, object],
    _emit: Callable[[str, str], None],
) -> str:
    """国史编纂官读全部章节记忆生成结局总评，落库 ending_summary（含逐回合时间线）。
    LLM 失败时用章节拼保底总评。返回总评正文（也已落库）。"""
    chapters = db.list_chapter_memories(upto_turn=state.turn)
    timeline = build_timeline(db, upto_turn=state.turn)
    summary_text = ""
    try:
        _emit("stage", "国史编纂结局总评")
        ending_agent = create_ending_summary_agent(llm_config, agno_db)
        payload = {
            "ending": {"status": outcome.get("status"), "summary": outcome.get("summary")},
            "chapters": chapters,
            "final_state": {
                "year": state.year, "period": state.period, "turn": state.turn,
                "metrics": dict(state.metrics),
            },
        }
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=False)
        tlog(f"[ending-summary/INPUT] chapters={len(chapters)} ({len(payload_json)}字)")
        summary_text = run_agent_text(ending_agent, payload_json, tag="ending-summary").strip()
        tlog(f"[ending-summary/OUTPUT] ({len(summary_text)}字)")
    except Exception as exc:
        tlog(f"[ending-summary] LLM 失败，走保底：{exc}")

    if not summary_text:
        bits = [str(outcome.get("summary") or "")]
        for c in chapters[-6:]:
            body = (c.get("body") or "").strip()
            if body:
                bits.append(f"{c['year']}年{c['period']}月：{body}")
        summary_text = "\n".join(b for b in bits if b)

    # 结局光谱（S12）：按中兴指数+任事意愿归档基调，喂史笔定笔调。
    spectrum: Dict[str, str] = {}
    try:
        from ming_sim.zhongxing import spectrum_label
        spectrum = spectrum_label(db, state, str(outcome.get("status") or ""))
        db.kv_set("upgrade.ending_spectrum", json.dumps(spectrum, ensure_ascii=False))
        summary_text = f"【结局归档：{spectrum['label']}】（中兴指数 {spectrum['zhongxing']}）\n" + summary_text
        outcome["spectrum"] = spectrum
    except Exception as exc:
        tlog(f"[spectrum] 归档跳过：{exc}")

    # 史笔立传（S11）：明史馆史官按实际作为定笔调，为这位崇祯作传。
    try:
        from ming_sim.shibi import generate_biography
        _emit("stage", "明史馆立传")
        biography = generate_biography(db, state, llm_config, outcome)
        if biography:
            summary_text = summary_text + "\n\n【明史·本纪】\n" + biography
    except Exception as exc:
        tlog(f"[shibi] 立传跳过：{exc}")

    try:
        db.save_ending_summary(
            state, str(outcome.get("status") or ""), summary_text, timeline,
        )
    except Exception as exc:
        tlog(f"[ending-summary] 落库失败：{exc}")
    return summary_text
