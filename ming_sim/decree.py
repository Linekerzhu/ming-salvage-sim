"""诏书生成与回合结算：拟诏、推演落库、无诏推进。L7。

纯逻辑（无 input()）；resolve_directives 的 print 是诊断输出，非交互。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Callable, Dict, List, Optional

from agno.db.sqlite import SqliteDb

from ming_sim.agents import (
    _dump_llm_messages,
    create_agreement_reviewer_agent,
    create_chapter_memory_agent,
    create_decree_writer_agent,
    create_ending_summary_agent,
    create_json_sanitizer_agent,
    create_score_extractor_module_agent,
    create_season_simulator_agent,
    parse_agent_json,
    run_agent_text,
)
from ming_sim.constants import ECONOMY_ACCOUNTS, TURN_UNIT
from ming_sim.causality import build_turn_causal_notes
from ming_sim.context import ENDING_LABELS, ENDING_ONGOING, ENDING_TIMEOUT, victory_status
from ming_sim.db import GameDB
from ming_sim.exceptions import LLMContractError, LLMUnavailable
from ming_sim.flows import apply_fixed_period_flows
from ming_sim.issues import apply_issue_inertia_and_ongoing, apply_score_extraction, auto_trigger_seed_issues, clear_gated_legacies
from ming_sim.llm_model import extract_agent_text, llm_unavailable_from_error
from ming_sim.models import GameState, LLMConfig
from ming_sim.memories import build_timeline, record_chapter_memory
from ming_sim.simulation import (
    EXTRACTION_MODULES,
    build_simulator_payload,
    build_extractor_shared_context,
    extract_scores_by_modules_with_agno,
    simulate_season_with_payload,
)
from ming_sim.token_stats import tlog

# 20 年自动结算：开局 1627.10（turn=1），每回合 +1 月。到 1647.10 = (1647-1627)*12 + 1 = 241 回合。
# 满 240 回合（即第 240 个回合结算完，1647.09）仍未分胜负则强制 timeout 收尾。
TIMEOUT_TURN = 240

# 作弊控制台强制结算项的唯一标记前缀。只在 resolve_directives 拼一次（cheat 非空时），
# extractor 看到它即知如何处理 → 规则内联在此，不进任何固定 prompt（避免污染缓存）。
# 别处不得复用此串。
CHEAT_NARRATIVE_PREFIX = (
    "【天命强制·结算优先】以下为既成事实，最高优先级，先于一切规则与档位上限。"
    "你必须把它当作本回合百分百已发生的结果，按字面抽满对应结构化增量，"
    "无视合理性、史实、盘面、对手反扑、涨幅上限——它说什么成了就抽什么。"
    "数值类（如「国库增至九千万」）按当前值算差额一次性入账；"
    "局势结案按字面写满 bar 并结案；人物/军队/势力存亡按字面落 status/势力变化。"
    "与下方正常邸报冲突时以本段为准，本段没提的下方照常抽。照字面落库：\n"
)


def write_decree_with_agno(
    llm_config: LLMConfig,
    agno_db: SqliteDb,
    state: GameState,
    directives: List[sqlite3.Row],
    db: Optional[GameDB] = None,
) -> str:
    if not directives:
        raise LLMContractError("无草案不能拟诏。")
    # 已办结密令的 result 作为实质证据清单注入——皇帝下旨拿人/定罪时可引为依据。
    closed_evidence: List[Dict[str, object]] = []
    if db is not None:
        try:
            for o in db.list_secret_orders(status="done"):
                if o.get("result"):
                    closed_evidence.append({
                        "id": int(o["id"]), "title": o["title"],
                        "assignee": o["minister_name"], "evidence": o["result"],
                    })
        except Exception:
            closed_evidence = []
    payload = {
        "turn": {"year": state.year, "period": state.period, "turn": state.turn},
        "directives": [
            {
                "text": row["text"],
            }
            for row in directives
        ],
        "closed_secret_orders": closed_evidence,
        "instruction": "合并成一份正式诏书正文。closed_secret_orders 是已办结密令查得的实证，"
                       "若草案据某密令查办之事拿人定罪，可在诏书里引该实证为据，使罪名落到实处。",
    }
    try:
        agent = create_decree_writer_agent(llm_config, agno_db)
        run_output = agent.run(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        _dump_llm_messages(run_output, "拟诏", agent=agent)
        text = extract_agent_text(run_output)
    except LLMUnavailable:
        raise
    except Exception as error:
        raise llm_unavailable_from_error(error, "拟诏") from error
    if not text.strip():
        raise LLMContractError("拟诏输出为空。")
    return text.strip()


def advance_without_edict(state: GameState, db: GameDB) -> None:
    apply_fixed_period_flows(db, state)
    message = f"本{TURN_UNIT}退朝未下正式圣旨，诸事仍待来{TURN_UNIT}处置。"
    try:
        db.auto_review_negotiation_agreements(
            state,
            narrative=message,
            phase="postresolve",
        )
    except Exception as exc:
        tlog(f"[agreement] 退朝履约审计失败，跳过：{exc}")
    db.record_log(state, message)
    print("\n" + message)
    state.next_period()
    db.save_state(state)


def _review_agreements_with_llm(
    *,
    state: GameState,
    db: GameDB,
    agno_db: SqliteDb,
    llm_config: LLMConfig,
    phase: str,
    decree_text: str = "",
    narrative: str = "",
    directives: Optional[List[sqlite3.Row]] = None,
    applied: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    agreements = [
        item
        for item in db.negotiation_agreement_ledger(state, limit=80)
        if str(item.get("status") or "") in {"pending", "sealed"}
    ]
    if not agreements:
        return {"reviews": []}
    payload = {
        "turn": {"year": state.year, "period": state.period, "turn": state.turn},
        "phase": phase,
        "agreements": agreements,
        "evidence_context": db._agreement_review_context(
            decree_text=decree_text,
            narrative=narrative,
            directives=directives,
            applied=applied,
        ),
        "instruction": (
            "逐条判断 tasks 条件是否满足；只有全部条件满足，target_text 标的才可达成。"
            "若证据不足，保持 pending，不要替皇帝补事实。"
        ),
    }
    agent = create_agreement_reviewer_agent(llm_config, agno_db)
    raw = run_agent_text(agent, json.dumps(payload, ensure_ascii=False, sort_keys=False), tag=f"agreement-review/{phase}")
    data = parse_agent_json(raw, f"奏对履约审计/{phase}")
    reviews = data.get("reviews")
    if not isinstance(reviews, list):
        return {"reviews": []}
    return {"reviews": [item for item in reviews if isinstance(item, dict)]}


def resolve_directives(
    state: GameState,
    db: GameDB,
    agno_db: SqliteDb,
    llm_config: LLMConfig,
    directives: List[sqlite3.Row],
    decree_text: str,
    deaths_this_turn: Optional[List[Dict[str, str]]] = None,
    debuts_this_turn: Optional[List[Dict[str, str]]] = None,
    on_event: Optional[Callable[[str, str], None]] = None,
    content=None,
    registry=None,
    cheat_directive: str = "",
) -> str:
    """on_event(kind, data): 推演过程实时回调。
    kind ∈ {stage, thinking, text}；stage 携带阶段名，thinking/text 携带增量片段。

    cheat_directive: 作弊控制台（Ctrl+~）下的强制结算指令。非空时拼到当期邸报最前面
    一起喂给 extractor，按字面当既成事实落库。唯一入口——只此一处写入标记前缀（见
    CHEAT_NARRATIVE_PREFIX），别处不得复用。
    """
    def _emit(kind: str, data: str) -> None:
        if on_event:
            on_event(kind, data)

    if not directives:
        advance_without_edict(state, db)
        return f"本{TURN_UNIT}未颁正式诏书。"

    before_turn = state.turn

    # 草案内容已由拟诏合并进 decree_text，simulator 只读 decree_text，不再单传逐条草案。

    # 1) 固定月度财政 tick（田赋/辽饷/军饷等，在 LLM 推演前落账）
    tlog("结算 1/4 固定月度财政 tick")
    _emit("stage", "固定月度财政入账")
    fixed_flows = apply_fixed_period_flows(db, state)

    # 1.6) 程序硬触发：标了 auto_trigger 的 seed 情势，gate 达标即由程序直接立项，
    #      绕过 LLM 因果判定。放在 simulator 前，使硬立的 issue 当回合即进盘面被邸报叙述。
    auto_triggered = auto_trigger_seed_issues(state, db)
    if auto_triggered:
        tlog(f"[AUTO-TRIGGER] 本回合程序硬立项 {len(auto_triggered)} 条：{[t.get('title') for t in auto_triggered]}")

    # 1.8) 历史脉络：取近几回合章节记忆注入推演（章节记忆取代旧的关键词原子检索）。
    relevant_memories: List[Dict] = []
    secret_orders_for_sim: list = []  # try 外初始化：检索失败也不能让后续 NameError
    try:
        _emit("stage", "回顾近来朝局")
        # state.turn 此刻仍是本回合（尚未 next_period），章节记忆存的是 turn-1 及更早的已结算回合。
        relevant_memories = db.list_chapter_memories(upto_turn=state.turn, recent=6)
        tlog(f"[memory/chapters] inject={len(relevant_memories)} upto_turn={state.turn}")
    except Exception as exc:
        tlog(f"[memory/chapters] 失败，跳过：{exc}")

    # 密令期限：到期 active 自动转 pending_review，保证本月核议一锤定音。
    try:
        due_orders = db.auto_submit_due_secret_orders(state)
        if due_orders:
            tlog(f"[secret_order] 到期送核议 {due_orders}")
    except Exception as exc:
        tlog(f"[secret_order] 到期送核议失败，跳过：{exc}")

    # 密令注入推演：active + pending_review 都要进（pending_review 需推演本月核议判 done/failed）
    try:
        active_orders = (
            db.list_secret_orders(status="active")
            + db.list_secret_orders(status="pending_review")
        )[:20]
        for o in active_orders:
            secret_orders_for_sim.append({
                "id": int(o["id"]),
                "minister_name": o["minister_name"],
                "title": o["title"],
                "content": o["content"][:120],
                "status": o["status"],
                "turn_issued": o.get("turn_issued") or 0,
                "due_turn": o.get("due_turn") or 0,
                "progress": o.get("result") or "",      # 承办人聊天里存的当前进展
                "sim_note": o.get("sim_note") or "",     # 上轮推演写的副作用
            })
        n_active = sum(1 for o in active_orders if o["status"] == "active")
        n_pending = sum(1 for o in active_orders if o["status"] == "pending_review")
        tlog(f"[secret_order] 注入推演 active={n_active} pending_review={n_pending}"
             + (f" titles={[o['title'] for o in active_orders]}" if active_orders else ""))
    except Exception as exc:
        tlog(f"[secret_order] 注入失败，跳过：{exc}")

    # 2) 推演 agent: 写邸报
    tlog("结算 2/4 推演 agent（月末邸报）")
    _emit("stage", "推演月末邸报")
    previous_narrative = db.previous_turn_summary(state) or ""
    llm_reviews: Dict[str, object] = {"reviews": []}
    try:
        llm_reviews = _review_agreements_with_llm(
            state=state,
            db=db,
            agno_db=agno_db,
            llm_config=llm_config,
            phase="preresolve",
            decree_text=decree_text,
            directives=directives,
        )
    except Exception as exc:
        tlog(f"[agreement] 颁诏前 LLM 审计失败，改用规则兜底：{exc}")
    try:
        reviewed = db.auto_review_negotiation_agreements(
            state,
            decree_text=decree_text,
            directives=directives,
            llm_reviews=llm_reviews,
            phase="preresolve",
        )
        if reviewed:
            tlog(f"[agreement] 颁诏前自动审计 {len(reviewed)} 条")
    except Exception as exc:
        tlog(f"[agreement] 颁诏前规则审计失败，跳过：{exc}")
    simulator_payload = build_simulator_payload(
        state, db, decree_text, previous_narrative,
        fixed_flows=fixed_flows,
        deaths_this_turn=deaths_this_turn,
        debuts_this_turn=debuts_this_turn,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders_for_sim,
        directives=directives,
    )
    simulator = create_season_simulator_agent(
        llm_config, agno_db, state=state, db=db, simulator_payload=simulator_payload
    )
    try:
        narrative, simulator_payload = simulate_season_with_payload(
            simulator, state, db, decree_text, previous_narrative,
            fixed_flows=fixed_flows,
            deaths_this_turn=deaths_this_turn,
            debuts_this_turn=debuts_this_turn,
            relevant_memories=relevant_memories,
            secret_orders=secret_orders_for_sim,
            simulator_payload=simulator_payload,
            on_thinking=lambda c: _emit("thinking", c),
            on_text=lambda c: _emit("text", c),
        )
    except Exception as exc:
        print(f"[WARN] 推演 agent 失败：{exc}；本{TURN_UNIT}用简化邸报兜底，跳过 LLM 结算。")
        narrative = (
            f"奉天承运皇帝诏曰：本{TURN_UNIT}推演 agent 被服务方拦截，无完整邸报。"
            f"已颁诏书：\n{decree_text}\n"
            f"固定收支已落账，事项 inertia 自然漂移；本{TURN_UNIT}无新立 issue。"
        )
        # 跳过 extractor，避免连锁失败
        db.record_log(state, narrative[:1200])
        db.save_turn_report(state, narrative)
        db.save_turn_extraction(
            state, decree_text=decree_text, narrative=narrative,
            extractor_output=f"[推演 agent 失败] {exc}；本回合跳过 extractor。",
        )
        llm_reviews = {"reviews": []}
        try:
            llm_reviews = _review_agreements_with_llm(
                state=state,
                db=db,
                agno_db=agno_db,
                llm_config=llm_config,
                phase="postresolve",
                decree_text=decree_text,
                narrative=narrative,
                directives=directives,
            )
        except Exception as review_exc:
            tlog(f"[agreement] 失败兜底 LLM 审计失败，改用规则兜底：{review_exc}")
        try:
            db.auto_review_negotiation_agreements(
                state,
                decree_text=decree_text,
                narrative=narrative,
                directives=directives,
                llm_reviews=llm_reviews,
                phase="postresolve",
            )
        except Exception as review_exc:
            tlog(f"[agreement] 失败兜底规则审计失败，跳过：{review_exc}")
        apply_issue_inertia_and_ongoing(db, state, touched_ids=set())
        for name in clear_gated_legacies(db, state):
            db.record_log(state, f"帝国修正消除：{name}")
        db.mark_directives_issued(state)
        state.next_period()
        db.save_state(state)
        return f"\n本{TURN_UNIT}颁布诏书：\n" + decree_text + "\n\n" + narrative

    # 2.5) 作弊强制项：拼到邸报最前面一起喂 extractor（唯一入口）。
    #      落库前文/turn_report 仍用原始 narrative，effective 版只进 extractor 与留痕。
    cheat = (cheat_directive or "").strip()
    if cheat:
        effective_narrative = CHEAT_NARRATIVE_PREFIX + cheat + "\n\n" + narrative
        tlog(f"[CHEAT] 强制结算项注入 extractor（{len(cheat)}字）：{cheat[:200]}")
    else:
        effective_narrative = narrative

    # 3) 结算 agent: 读邸报抽 JSON
    tlog("结算 3/4 结算 agent（抽 JSON）")
    _emit("stage", "数值推演结算")
    extractor_shared_context = build_extractor_shared_context(
        db, state, effective_narrative, decree_text,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders_for_sim,
    )
    sanitizer = create_json_sanitizer_agent(llm_config, agno_db)
    extractor_input = ""
    extractor_output = ""
    try:
        tlog("结算 3/4 抽取（模块 module）")
        extractors = {
            module: create_score_extractor_module_agent(
                llm_config,
                agno_db,
                module,
                simulator_payload=simulator_payload,
                supplemental_context=extractor_shared_context,
            )
            for module in EXTRACTION_MODULES
        }
        extracted, extractor_output, extractor_input = extract_scores_by_modules_with_agno(
            extractors, db, state, effective_narrative, decree_text=decree_text, sanitizer=sanitizer,
            relevant_memories=relevant_memories,
            secret_orders=secret_orders_for_sim,
        )
    except Exception as exc:
        print(f"[WARN] 结算抽取失败：{exc}；本{TURN_UNIT}数值不变。")
        extracted = {}
        extractor_output = f"[抽取失败] {exc}"

    tlog("结算 4/4 落库 + inertia/ongoing")
    _emit("stage", "落库与事项推进")
    applied = apply_score_extraction(db, state, extracted, content=content, registry=registry)
    llm_reviews = {"reviews": []}
    try:
        llm_reviews = _review_agreements_with_llm(
            state=state,
            db=db,
            agno_db=agno_db,
            llm_config=llm_config,
            phase="postresolve",
            decree_text=decree_text,
            narrative=effective_narrative,
            directives=directives,
            applied=applied,
        )
    except Exception as exc:
        tlog(f"[agreement] 月末 LLM 审计失败，改用规则兜底：{exc}")
    try:
        reviewed = db.auto_review_negotiation_agreements(
            state,
            decree_text=decree_text,
            narrative=effective_narrative,
            directives=directives,
            applied=applied,
            llm_reviews=llm_reviews,
            phase="postresolve",
        )
        if reviewed:
            applied["agreements"] = reviewed
            tlog(f"[agreement] 月末自动审计 {len(reviewed)} 条")
    except Exception as exc:
        tlog(f"[agreement] 月末规则审计失败，跳过：{exc}")
    try:
        applied["xinpan"] = db.apply_turn_xinpan_update(state, decree_text, effective_narrative, applied)
    except Exception as exc:
        tlog(f"[xinpan] 月末更新失败，跳过：{exc}")
    causal_notes = build_turn_causal_notes(db, state, decree_text, applied)

    # 4) 把 narrative 与诏书写入 turn_logs 作下月前文
    db.record_log(state, narrative[:1200])
    db.save_turn_report(state, narrative)
    # 推演链原始输入/输出留痕，事后可追「该立的 issue 为何没立」。
    db.save_turn_extraction(
        state,
        decree_text=decree_text,
        narrative=effective_narrative,  # 留痕含作弊段，便于事后追「为何这么落库」
        extractor_input=extractor_input,
        extractor_output=extractor_output,
        causal_notes=causal_notes,
    )

    # 5) 章节记忆：LLM 把本回合诏书+邸报+落库效果浓缩成一段叙事章节，落 event_memories
    #    （chapter_summary）。失败有保底拼接，不抛断。
    _emit("stage", "记起居注")
    try:
        chapter_agent = create_chapter_memory_agent(llm_config, agno_db)
        record_chapter_memory(chapter_agent, db, state, decree_text, narrative, applied)
    except Exception as exc:
        tlog(f"[chapter-memory] 跳过：{exc}")

    # 6) 天命异闻：低频暗线事件，服务政略主循环。
    _emit("stage", "异闻入档")
    adventure_narrative = ""
    try:
        from ming_sim.adventure_engine import AdventureEngine, format_adventure_narrative
        from ming_sim.dice import DiceRoller
        from ming_sim.paths import bundled_path

        adventure_records = getattr(content, "adventures", []) if content is not None else []
        if adventure_records:
            adv_engine = AdventureEngine.from_records(adventure_records)
        else:
            adv_path = bundled_path("content", "adventures.json")
            adv_engine = AdventureEngine.from_json(str(adv_path))
        seen_rows = db.conn.execute("SELECT DISTINCT adventure_id FROM adventure_log").fetchall()
        adv_engine.remember_triggered([str(row[0]) for row in seen_rows])
        roller = DiceRoller(seed=state.turn * 1000 + state.year * 12 + state.period)
        emperor_profile = db.get_player_profile()

        def _profile_int(key: str, fallback: int) -> int:
            try:
                return int(emperor_profile.get(key, fallback))
            except (TypeError, ValueError):
                return fallback

        emperor_name = str(emperor_profile.get("name") or "崇祯")
        char_abilities = {
            "force": _profile_int("force", 45),
            "wisdom": _profile_int("wisdom", 76),
            "charm": _profile_int("charm", 62),
            "luck": _profile_int("luck", 55),
            "cultivation": _profile_int("cultivation", 0),
        }
        triggered = adv_engine.get_available_adventures(
            turn=state.turn,
            metrics=dict(state.metrics),
            region_id="beizhili",
            character_luck=char_abilities["luck"],
            roller=roller,
            limit=2,
        )
        if triggered:
            adv = triggered[0]
            choice_index = adv_engine.choose_choice_index(
                adv.id,
                char_abilities,
                metrics=dict(state.metrics),
            )
            result = adv_engine.resolve_choice(adv.id, choice_index, char_abilities, roller=roller)
            adventure_narrative = format_adventure_narrative(result, emperor_name)
            db.conn.execute(
                """
                INSERT INTO adventure_log
                (turn, year, period, adventure_id, title, chosen_index, choice_text, success, narrative, effects, item_reward)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.turn, state.year, state.period,
                    result.adventure_id, result.title, result.chosen_index,
                    result.choice_text,
                    1 if result.success else 0, result.narrative,
                    json.dumps(result.effects, ensure_ascii=False),
                    result.item_reward,
                ),
            )
            if result.item_reward:
                db.grant_player_item(result.item_reward, state)
            for eff_key, eff_val in (result.effects or {}).items():
                if eff_key in char_abilities or eff_key in {"hp", "max_hp", "exp", "level"}:
                    db.apply_player_profile_delta(state, eff_key, int(eff_val))
                elif eff_key in ECONOMY_ACCOUNTS:
                    db.record_issue_economy_move(
                        state,
                        eff_key,
                        int(eff_val),
                        "天命异闻",
                        result.title,
                    )
                elif eff_key in state.metrics:
                    state.metrics[eff_key] = max(0, state.metrics[eff_key] + int(eff_val))
            state.clamp()
            db.conn.commit()
            tlog(f"[adventure] 触发异闻：{adv.title}，成功={result.success}")
    except Exception as exc:
        tlog(f"[adventure] 异闻触发失败，跳过：{exc}")

    # 6.5) 落 inertia + ongoing (未被本月 issue_advances 触动的)
    touched_ids = set()
    for adv in applied.get("issue_summary", {}).get("advances", []) or []:
        touched_ids.add(int(adv.get("issue_id") or 0))
    apply_issue_inertia_and_ongoing(db, state, touched_ids=touched_ids)

    # 7) 开局负面帝国修正：本月若达成消除条件即清除（程序判定，不靠 LLM/时长）
    cleared = clear_gated_legacies(db, state)
    for name in cleared:
        db.record_log(state, f"帝国修正消除：{name}")

    # 8) 结局判定：叙事型（退位/自尽，applied 已带）→ 数值型（京畿失守）→ 到期型（20 年/240 回合）。
    #    state.turn 此刻仍是刚结算完的本回合（next_period 之前）。
    #    结局只触发一次：已 ended 的存档继续推进时不重判、不重生总评（省 token、不反复弹页）。
    outcome = None
    ended = False
    ending_text = ""
    if not state.ended:
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
        if ended:
            db.record_log(state, f"结局判定：{outcome.get('summary', '')}")
            # 章节记忆（含本回合）已落库，国史编纂官读全程生成结局总评。
            ending_text = _generate_ending_summary(db, state, llm_config, agno_db, outcome, _emit)
            state.ended = True
            state.ending_status = str(outcome.get("status") or "")

    db.mark_directives_issued(state)
    state.next_period()
    db.save_state(state)
    assert state.turn == before_turn + 1

    ending = ""
    if ended:
        label = ENDING_LABELS.get(str(outcome.get("status")), "结局")
        ending = f"\n\n【结局·{label}】{outcome.get('summary', '')}"
        if ending_text:
            ending += "\n\n" + ending_text
    adventure_section = f"\n\n{adventure_narrative}" if adventure_narrative else ""
    full_report = f"\n本{TURN_UNIT}颁布诏书：\n" + decree_text + "\n\n" + narrative + adventure_section + ending
    return full_report


def _generate_ending_summary(
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

    try:
        db.save_ending_summary(
            state, str(outcome.get("status") or ""), summary_text, timeline,
        )
    except Exception as exc:
        tlog(f"[ending-summary] 落库失败：{exc}")
    return summary_text
