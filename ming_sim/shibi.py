"""史笔系统（S11）：后世史官立传与季度史评预览。L6。

支柱 P7「悲剧值得玩」：评价的不是输赢，而是你以什么方式、为了什么而失败。
- 终局立传 generate_biography()：以清修《明史·本纪》体例为玩家作传，
  素材 = 全程章节记忆 + 关键信念变量（势/任事意愿）轨迹 + 结局。
- 季度史评 quarterly_review()：起居注片段式的中途预览，让玩家感知
  「史书会怎么写我」；risk_aversion 高企时点破「上多疑，臣下莫敢任事」。
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from agno.agent import Agent

from ming_sim.db import GameDB
from ming_sim.llm_config import for_role as _llm_for_role
from ming_sim.llm_model import create_chat_model
from ming_sim.models import GameState, LLMConfig
from ming_sim.pipeline_registry import llm_output_token_budget
from ming_sim.token_stats import tlog
from ming_sim.upgrade_schema import (
    KV_RISK_AVERSION,
    KV_SHI,
    RISK_AVERSION_DEFAULT,
    SHI_DEFAULT,
    kv_int,
)

SHIBI_BIOGRAPHY_PROMPT = """你是清修《明史》的史官，正为崇祯皇帝撰写本纪赞语与小传。
你收到的是这位皇帝在位期间的逐月起居注章节（chapters）、信念轨迹（beliefs：势=朝廷威令是否行得通；任事意愿=臣下敢不敢担责）与结局（ending）。

要求：
1. 以《明史·本纪》的文体作传：先简述其政之大端（用人、理财、驭军、抚民各择其要），再下「赞曰」。
2. 笔调必须由其实际作为决定，不得套话。判断维度举例：
   - 屡惩办事失败之臣而臣下莫敢任事者，书「上多疑而好杀，柄臣救过不暇，无敢任事者」；
   - 为败将忠臣担责开脱、朝局渐稳者，书「知人善任，推诚待下」；
   - 事必躬亲而政益丛脞者，书「宵衣旰食，而经画乖方」；
   - 亡国而有恤民之实者，仍当书其哀荣，「君非亡国之君」之论可采可驳。
3. 评价要落在具体事上（引 chapters 中的实事，按年月），不许空泛。
4. 若有 mingpi_of_the_fallen（身故要员的命批诗），可择一二化入传文作谶语或挽笔（如「时人有诗谶曰…」），不必全用。
5. 篇幅 400-700 字。文言或浅近文言。不要 markdown 标题，不要列表。
6. 直接输出传文，不要任何解释或前言。"""

QUARTERLY_REVIEW_PROMPT = """你是崇祯朝的起居注日讲官，每季度私下录一段「史臣曰」式的短评（这段话将来会进史书，皇帝偶尔能窥见只言片语）。
你收到近三个月的章节记忆（chapters）与信念变量（beliefs：势、任事意愿、其变动原因摘录）。

要求：
1. 一段 60-120 字的文言短评，点出本季度皇帝为政的最显眼倾向及其端倪后果。
2. 信念变量是重要线索：任事意愿低迷（risk_aversion 高）当点破「上多疑，臣下莫敢任事」之类；势衰当点破「诏令不出都门」。
3. 委婉但不阿谀——这是写给后世看的，不是写给皇帝看的。
4. 直接输出短评正文，不要解释。"""


def _create_agent(llm_config: LLMConfig, *, name: str, agent_id: str, prompt: str,
                  budget_key: str, minimum: int) -> Agent:
    cfg = _llm_for_role(llm_config, "simulator")  # 史笔走 advanced 档（若已配置）
    return Agent(
        name=name,
        id=agent_id,
        model=create_chat_model(
            cfg,
            temperature=0.7,
            top_p=0.9,
            max_tokens=llm_output_token_budget(budget_key, cfg.max_tokens, minimum=minimum),
            enable_thinking=True,
        ),
        instructions=[prompt],
        add_history_to_context=False,
        markdown=False,
    )


def _belief_trajectory(db: GameDB, *, limit: int = 60) -> Dict[str, object]:
    rows = db.conn.execute(
        "SELECT day, key, old_value, new_value, reason FROM belief_logs ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return {
        "current": {
            "势": kv_int(db, KV_SHI, SHI_DEFAULT),
            "任事意愿": 100 - kv_int(db, KV_RISK_AVERSION, RISK_AVERSION_DEFAULT),
        },
        "changes": [
            {"day": int(r["day"]), "key": str(r["key"]),
             "from": int(r["old_value"]), "to": int(r["new_value"]),
             "reason": str(r["reason"])}
            for r in reversed(rows)
        ],
    }


def generate_biography(db: GameDB, state: GameState, llm_config: LLMConfig,
                       outcome: Dict[str, object]) -> str:
    """终局立传。失败返回空串（调用方自行决定是否保底）。"""
    chapters = db.list_chapter_memories(upto_turn=state.turn)
    # NPC 数据基座命批：本朝身故要员的命批诗，供史官化用为传中谶语/挽笔
    mingpi_notes: List[Dict[str, str]] = []
    try:
        from ming_sim.foundation import mingpi as foundation_mingpi
        dead = db.conn.execute(
            "SELECT name, status_reason FROM characters WHERE status='dead' "
            "ORDER BY status_changed_turn DESC LIMIT 8"
        ).fetchall()
        for row in dead:
            verse = foundation_mingpi(str(row["name"]))
            if verse:
                mingpi_notes.append({"name": str(row["name"]), "fate": str(row["status_reason"] or ""),
                                     "mingpi": verse})
    except Exception:
        mingpi_notes = []
    payload = {
        "ending": {"status": outcome.get("status"), "summary": outcome.get("summary")},
        "reign_span": {"from": "1627年10月", "to": f"{state.year}年{state.period}月"},
        "final_metrics": dict(state.metrics),
        "beliefs": _belief_trajectory(db),
        "chapters": chapters,
        "mingpi_of_the_fallen": mingpi_notes,
    }
    try:
        agent = _create_agent(
            llm_config, name="明史馆史官", agent_id="shibi-biography",
            prompt=SHIBI_BIOGRAPHY_PROMPT, budget_key="llm.shibi_biography", minimum=1600,
        )
        from ming_sim.agents import run_agent_text
        text = run_agent_text(
            agent, json.dumps(payload, ensure_ascii=False, sort_keys=False), tag="shibi-biography"
        ).strip()
        if text:
            db.kv_set("upgrade.shibi_biography", text)
        return text
    except Exception as exc:
        tlog(f"[shibi] 立传失败，跳过：{exc}")
        return ""


def quarterly_review(db: GameDB, state: GameState, llm_config: LLMConfig) -> str:
    """季度史评预览（每 3 个月由调用方触发）。落 kv 供前端取；失败返回空串。"""
    chapters = db.list_chapter_memories(upto_turn=state.turn, recent=3)
    payload = {
        "turn": {"year": state.year, "period": state.period},
        "beliefs": _belief_trajectory(db, limit=20),
        "chapters": chapters,
    }
    try:
        agent = _create_agent(
            llm_config, name="起居注日讲官", agent_id="shibi-quarterly",
            prompt=QUARTERLY_REVIEW_PROMPT, budget_key="llm.shibi_quarterly", minimum=400,
        )
        from ming_sim.agents import run_agent_text
        text = run_agent_text(
            agent, json.dumps(payload, ensure_ascii=False, sort_keys=False), tag="shibi-quarterly"
        ).strip()
        if text:
            reviews = list_quarterly_reviews(db)
            reviews.append({"year": state.year, "period": state.period, "text": text})
            db.kv_set("upgrade.shibi_reviews", json.dumps(reviews[-40:], ensure_ascii=False))
        return text
    except Exception as exc:
        tlog(f"[shibi] 季评失败，跳过：{exc}")
        return ""


def list_quarterly_reviews(db: GameDB) -> List[Dict[str, object]]:
    try:
        data = json.loads(db.kv_get("upgrade.shibi_reviews") or "[]")
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def get_biography(db: GameDB) -> str:
    return db.kv_get("upgrade.shibi_biography") or ""
