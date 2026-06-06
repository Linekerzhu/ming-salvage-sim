"""Shared dialogue-goal state machine for summons.

Conversation goals are the psychological handshake layer. They sit between raw
chat and the agreement ledger: a goal may be active or waiting on conditions;
only sealed goals become negotiation agreements.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ming_sim.assets import strip_json_fence
from ming_sim.context import (
    _clean_obsidian_text,
    _effective_tiangang_entry,
    npc_dialogue_behavior_profile,
    npc_network_recommendations,
    npc_tiangang_behavior_brief,
)
from ming_sim.models import Character, GameState, LLMConfig
from ming_sim.negotiation import (
    CASTRATION_CONTEXT_RE,
    HANDSHAKE_BLOCKED,
    HANDSHAKE_CONDITIONAL,
    HANDSHAKE_NONE,
    HANDSHAKE_SEALED,
    MONEY_RESOURCE_RE,
    NegotiationResult,
    commitment_required,
    evaluate_negotiation,
    extract_negotiation_tasks,
    handshake_label,
    promise_type_from_terms,
    stakes_from_terms,
    target_text_from_terms,
)


GOAL_ACTIVE = "active"
GOAL_WAITING = "waiting_conditions"
GOAL_SEALED = "sealed"
GOAL_BLOCKED = "blocked"
GOAL_ABANDONED = "abandoned"
GOAL_EXPIRED = "expired"

INSTANT_AGREEMENT_ACTIONS = {"castration", "emancipation", "personnel"}

POLITICAL_CHAT_RE = re.compile(
    r"政|旨|诏|办|查|任|罢|饷|银|税|粮|兵|厂卫|内廷|司礼监|太监|宦官|"
    r"净身|入宫|清流|东林|阉党|密令|举荐|铨选|调任|下狱|抄家|支持|协办|承办|背书"
)


@dataclass
class GoalDetection:
    action_kind: str = ""
    title: str = ""
    target_text: str = ""
    confidence: int = 0
    source: str = "none"
    abandon: bool = False
    switches_goal: bool = False
    reason: str = ""

    @property
    def has_goal(self) -> bool:
        return bool(self.action_kind and self.title and not self.abandon)


@dataclass
class PreparedDialogue:
    prefix: str = ""
    detection: GoalDetection = field(default_factory=GoalDetection)
    active_goal: Optional[Dict[str, object]] = None
    preview_goal: Optional[Dict[str, object]] = None


def _compact(text: str, limit: int = 120) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    return cleaned[:limit]


def _tg_value(name: str, dim_id: str, default: int = 3) -> int:
    entry = _effective_tiangang_entry(name)
    values = entry.get("values") if isinstance(entry.get("values"), dict) else {}
    try:
        return max(1, min(5, int(values.get(dim_id, default) or default)))
    except (TypeError, ValueError, AttributeError):
        return default


def _topic_label(text: str, fallback: str = "本次奏对事项") -> str:
    text = text or ""
    for label, pattern in (
        ("吏部任职", r"吏部"),
        ("户部任职", r"户部"),
        ("兵部任职", r"兵部"),
        ("礼部任职", r"礼部"),
        ("刑部任职", r"刑部"),
        ("工部任职", r"工部"),
        ("内阁任事", r"内阁"),
        ("辽东军务", r"辽东|关宁|山海关|宁锦"),
        ("钱粮筹措", MONEY_RESOURCE_RE),
        ("厂卫密查", r"厂卫|东厂|锦衣卫|密查|暗查|取证|线人"),
        ("人事举荐", r"举荐|保荐|保举|荐"),
        ("内廷身份转换", CASTRATION_CONTEXT_RE),
        ("奴籍转民籍", r"奴籍|民籍|脱籍|还民|出宫为民"),
    ):
        if re.search(pattern, text):
            return label
    return _compact(text, 48) or fallback


def _is_personnel_acceptance(text: str) -> bool:
    return bool(
        re.search(
            r"(愿|可愿|愿否|是否愿|是否可|欲令|朕欲|命卿|着卿|调卿|授卿|任卿|起用卿|请卿).{0,24}"
            r"(任|去|赴|入|到|做|担任|出任|接任|掌|管|补|调往|调任|做官|任事|官)",
            text,
        )
        or re.search(r"(吏部|户部|礼部|兵部|刑部|工部|内阁|都察院|巡抚|总督|尚书|侍郎).{0,18}(任|做官|任事|补|调|掌)", text)
    )


def _rule_detect_goal(user_text: str, active_goal: Optional[Dict[str, object]] = None) -> GoalDetection:
    text = str(user_text or "").strip()
    if not text:
        return GoalDetection()
    active_kind = str((active_goal or {}).get("action_kind") or "")
    active_title = str((active_goal or {}).get("title") or "")
    if active_goal and re.search(r"放弃|作罢|不谈了|暂且不谈|暂缓|撤回|算了|先不办", text):
        return GoalDetection(
            action_kind=active_kind,
            title=active_title,
            target_text=str(active_goal.get("target_text") or active_title),
            confidence=92,
            source="rule",
            abandon=True,
            reason="玩家主动放弃或暂缓当前目的。",
        )

    personnel_acceptance = _is_personnel_acceptance(text)
    if personnel_acceptance:
        label = _topic_label(text, "人事任职")
        return GoalDetection(
            action_kind="personnel",
            title=f"劝其接受{label}",
            target_text=f"本人接受或支持人事安排：{label}",
            confidence=92,
            source="rule",
        )

    if re.search(r"奴籍|民籍|脱籍|还民|转为民|转民籍|出宫为民|归为百姓|赐还为民", text):
        return GoalDetection(
            action_kind="emancipation",
            title="劝其自愿奴籍转民籍",
            target_text="本人同意奴籍转民籍并接受身份转换",
            confidence=94,
            source="rule",
        )

    if re.search(CASTRATION_CONTEXT_RE, text):
        return GoalDetection(
            action_kind="castration",
            title="劝其自愿净身入内廷",
            target_text="本人同意净身入内廷并接受内廷身份转换",
            confidence=96,
            source="rule",
        )

    if re.search(r"密令|秘密任务|暗查|密查|盯梢|取证|密旨|线人|耳目", text):
        label = _topic_label(text, "密查取证")
        return GoalDetection(
            action_kind="secret_order",
            title=f"劝其密办：{label}",
            target_text=f"本人同意密办/取证：{label}",
            confidence=88,
            source="rule",
        )

    if re.search(r"举荐|保荐|保举|作保|背书|代奏|联络|调停|转圜|斡旋|游说|探口风|保密|守口|不泄", text):
        label = _topic_label(text, "奏对协力")
        return GoalDetection(
            action_kind="court_commitment",
            title=f"劝其协力：{label}",
            target_text=f"本人同意履行奏对约定：{label}",
            confidence=82,
            source="rule",
        )

    if re.search(r"支持|赞成|协办|承办|推行|办理|办成|担待|配合|赞画", text) and re.search(r"政|新政|清丈|商税|辽饷|军|兵|粮|银|厂卫|廷议|言路|地方|赈", text):
        label = _topic_label(text, "政务推行")
        return GoalDetection(
            action_kind="policy",
            title=f"劝其支持协办：{label}",
            target_text=f"本人同意支持、背书或协办政策：{label}",
            confidence=76,
            source="rule",
        )

    return GoalDetection()


def _intentish(text: str) -> bool:
    return bool(re.search(r"是否|可愿|愿否|愿不愿|朕欲|命卿|着卿|请卿|让你|令你|支持|协办|承办|背书|密查|举荐|任|调", text or ""))


def _llm_detect_goal(user_text: str, active_goal: Optional[Dict[str, object]], llm_config: Optional[LLMConfig], agno_db: object) -> GoalDetection:
    if llm_config is None or agno_db is None or not _intentish(user_text):
        return GoalDetection()
    try:
        from agno.agent import Agent

        from ming_sim.agents import run_agent_text
        from ming_sim.llm_model import create_chat_model

        active = {
            "action_kind": (active_goal or {}).get("action_kind") or "",
            "title": (active_goal or {}).get("title") or "",
            "target_text": (active_goal or {}).get("target_text") or "",
        }
        agent = Agent(
            name="奏对目的识别",
            id="dialogue-goal-detector",
            session_id="dialogue-goal-detector",
            db=agno_db,
            model=create_chat_model(llm_config, temperature=0, max_tokens=500, force_json_output=True),
            instructions=[
                "你只做中文历史策略游戏的奏对目的识别，必须输出 JSON。",
                "允许 action_kind: personnel, secret_order, policy, court_commitment, castration, emancipation, none。",
                "净身只有明确净身/去势/自愿入内廷身份转换才判 castration；普通任官或提到司礼监背景不得误判。",
                "普通咨询、寒暄、问现状输出 none。",
                "JSON字段: action_kind,title,target_text,confidence,abandon,reason。",
            ],
            markdown=False,
        )
        payload = {"user_text": user_text, "active_goal": active}
        raw = run_agent_text(agent, json.dumps(payload, ensure_ascii=False), tag="dialogue-goal-detect")
        data = json.loads(strip_json_fence(raw))
        if not isinstance(data, dict):
            return GoalDetection()
        action_kind = str(data.get("action_kind") or "").strip()
        if action_kind == "none":
            action_kind = ""
        if action_kind not in {"personnel", "secret_order", "policy", "court_commitment", "castration", "emancipation", ""}:
            action_kind = ""
        try:
            confidence = max(0, min(100, int(data.get("confidence") or 0)))
        except (TypeError, ValueError):
            confidence = 0
        if not action_kind or confidence < 70:
            return GoalDetection()
        return GoalDetection(
            action_kind=action_kind,
            title=_compact(str(data.get("title") or _topic_label(user_text)), 120),
            target_text=_compact(str(data.get("target_text") or data.get("title") or user_text), 240),
            confidence=confidence,
            source="llm",
            abandon=bool(data.get("abandon")),
            reason=str(data.get("reason") or "").strip()[:180],
        )
    except Exception:
        return GoalDetection()


def _threshold_for_goal(character: Character, action_kind: str, user_text: str, xinpan_profile: Dict[str, object], behavior_profile: Dict[str, object]) -> int:
    threshold = commitment_required(action_kind)
    preferred = str(behavior_profile.get("preferred_stance") or "")
    if preferred == "support":
        threshold -= 5
    elif preferred == "caution":
        threshold += 2
    elif preferred == "oppose":
        threshold += 8

    quadrant = str((xinpan_profile or {}).get("quadrant") or "")
    try:
        shi_he = float((xinpan_profile or {}).get("shi_he") or 0)
        dao_he = float((xinpan_profile or {}).get("dao_he") or 0)
        hatred = float((xinpan_profile or {}).get("hatred") or 0)
        trust = float((xinpan_profile or {}).get("trust_coeff") or 1.0)
    except (TypeError, ValueError):
        shi_he = dao_he = hatred = 0.0
        trust = 1.0
    if quadrant == "股肱":
        threshold -= 5
    elif quadrant == "离心":
        threshold += 8
    threshold -= round(max(-4.0, min(4.0, shi_he / 20.0)))
    threshold -= round(max(-3.0, min(3.0, dao_he / 25.0)))
    threshold += round(min(10.0, hatred / 10.0))
    if trust < 0.7:
        threshold += 4

    if action_kind == "castration":
        d03 = _tg_value(character.name, "d03")
        d10 = _tg_value(character.name, "d10")
        threshold += 8 if min(d03, d10) <= 2 else (-4 if max(d03, d10) >= 4 else 0)
    elif action_kind == "secret_order":
        d04 = _tg_value(character.name, "d04")
        d12 = _tg_value(character.name, "d12")
        threshold += 5 if min(d04, d12) <= 2 else (-4 if max(d04, d12) >= 4 else 0)
    elif action_kind == "personnel":
        threshold += 3 if _tg_value(character.name, "d07") >= 4 else 0
        threshold -= 3 if max(_tg_value(character.name, "d21"), _tg_value(character.name, "d34")) >= 4 else 0
    elif action_kind == "policy" and re.search(r"清丈|商税|新政|改革|变法", user_text or ""):
        threshold += 3 if _tg_value(character.name, "d05") >= 4 else -2

    return max(45, min(96, int(threshold)))


def detect_conversation_goal(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    *,
    active_goal: Optional[Dict[str, object]] = None,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
) -> GoalDetection:
    detection = _rule_detect_goal(user_text, active_goal)
    if not detection.has_goal and not detection.abandon:
        detection = _llm_detect_goal(user_text, active_goal, llm_config, agno_db)

    if active_goal and detection.has_goal:
        old_kind = str(active_goal.get("action_kind") or "")
        old_target = str(active_goal.get("target_text") or active_goal.get("title") or "")
        new_target = detection.target_text or detection.title
        detection.switches_goal = bool(old_kind and (old_kind != detection.action_kind or (new_target and old_target and new_target[:24] != old_target[:24])))
    return detection


def prepare_dialogue_context(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    *,
    llm_config: Optional[LLMConfig] = None,
    agno_db: object = None,
    persistent: bool = True,
) -> PreparedDialogue:
    if not persistent:
        return PreparedDialogue()
    active = db.get_active_conversation_goal(character.name)
    detection = detect_conversation_goal(
        db,
        state,
        character,
        user_text,
        active_goal=active,
        llm_config=llm_config,
        agno_db=agno_db,
    )

    preview: Optional[Dict[str, object]] = active
    mode = "续接"
    if detection.abandon and active:
        mode = "放弃"
    elif detection.has_goal:
        mode = "改立" if detection.switches_goal and active else "拟立"
        preview = {
            "action_kind": detection.action_kind,
            "title": detection.title,
            "target_text": detection.target_text,
            "status": GOAL_ACTIVE,
            "score": 0,
            "threshold": commitment_required(detection.action_kind),
            "condition_status": "none",
            "conditions": [],
        }

    if not preview and not detection.abandon:
        return PreparedDialogue(detection=detection, active_goal=active, preview_goal=None)

    lines = ["【召对目的提示（隐藏机制；可转化为局内拟旨感，不要复述机制名）】"]
    if detection.abandon and active:
        lines.append(f"- 玩家可能要放弃当前目的：{active.get('title') or active.get('target_text')}")
        lines.append("- 若玩家确是放弃，作出符合身份的反应；不要继续假装已经成约。")
    elif preview:
        score = int(preview.get("score") or 0)
        threshold = int(preview.get("threshold") or commitment_required(str(preview.get("action_kind") or "general")))
        conditions = preview.get("conditions") if isinstance(preview.get("conditions"), list) else []
        lines.append(f"- 本轮{mode}目的：{preview.get('title') or preview.get('target_text')}。")
        lines.append(f"- 心理进度：{score}% / 目标阈值 {threshold}。")
        if conditions:
            pending = "；".join(str(item.get("description") or "") for item in conditions if isinstance(item, dict) and item.get("status") != "done")
            if pending:
                lines.append(f"- 已开条件待证：{pending}。若玩家已明确满足，可承认条件闭环。")
        lines.append("- 围绕该目的回应：真愿意就明说承诺；需要条件就列 1-3 条可履约条件；不能接受就明确拒绝或保留。")
    return PreparedDialogue(prefix="\n".join(lines), detection=detection, active_goal=active, preview_goal=preview)


def related_issue_for_chat(db: Any, text: str) -> int:
    combined = text or ""
    try:
        rows = db.list_active_issues()
    except Exception:
        rows = []
    for row in rows:
        title = str(row["title"] or "")
        try:
            tags = json.loads(str(row["tags"] or "[]"))
        except Exception:
            tags = []
        needles = [title, *[str(tag) for tag in tags]]
        if any(needle and needle[:12] in combined for needle in needles):
            return int(row["id"])
    return 0


def extract_conditions(answer: str) -> str:
    clauses = re.split(r"[。！？\n；;]", answer or "")
    condition_keywords = (
        r"但|只是|须|需|若|恐|难|除非|条件|银|钱|饷|粮|人手|名分|明旨|圣旨|"
        r"廷议|成例|期限|掣肘|反噬|阻|保全|家眷|家小|族人|官|赏|安置|"
        r"体面|不辱|抚恤|遮护|边界|职掌|程序"
    )
    picked = [clause.strip() for clause in clauses if clause.strip() and re.search(condition_keywords, clause)]
    return "；".join(picked[:3])


def infer_chat_stance(answer: str) -> tuple[str, int, Dict[str, bool]]:
    hard_oppose = bool(re.search(r"万不可|断不可|绝不可|不可行|不可为|臣不敢从|臣不敢奉诏|不能从|不能奉行|难以奉行|不愿奉行|恕难奉行|请陛下收回成命", answer or ""))
    soft_oppose = bool(re.search(r"不宜|不当|请陛下三思|恐不可|不可急|不可骤|不可轻", answer or ""))
    support = bool(re.search(r"臣愿|奴才愿|小的愿|愿为陛下|臣领旨|遵旨|可行|可以|臣以为可|宜行|应当|当奉旨|愿领|敢不奉行", answer or ""))
    caution = bool(re.search(r"但|只是|须|需|若|恐|难|银|人手|名分|期限|掣肘|反噬|阻", answer or ""))
    if hard_oppose:
        stance, confidence = "oppose", 5
    elif support and caution:
        stance, confidence = "caution", 4
    elif soft_oppose and caution:
        stance, confidence = "caution", 4
    elif soft_oppose:
        stance, confidence = "oppose", 4
    elif support:
        stance, confidence = "support", 4
    elif caution:
        stance, confidence = "caution", 3
    else:
        stance, confidence = "neutral", 2
    return stance, confidence, {
        "hard_oppose": hard_oppose,
        "soft_oppose": soft_oppose,
        "support": support,
        "caution": caution,
    }


def adjust_stance_by_persona(
    db: Any,
    state: GameState,
    minister_name: str,
    combined: str,
    answer: str,
    stance: str,
    confidence: int,
    signals: Dict[str, bool],
) -> tuple[str, int, Dict[str, object], Dict[str, object]]:
    try:
        xinpan_profile = db.get_xinpan_profile(minister_name, state)
    except Exception:
        xinpan_profile = {}
    behavior = npc_dialogue_behavior_profile(
        minister_name,
        xinpan_profile=xinpan_profile if isinstance(xinpan_profile, dict) else {},
        text=combined,
    )
    preferred = str(behavior.get("preferred_stance") or "neutral")
    try:
        margin = int(behavior.get("margin") or 0)
    except (TypeError, ValueError):
        margin = 0
    try:
        fear = float((xinpan_profile or {}).get("fear") or 0)
    except (TypeError, ValueError):
        fear = 0.0
    try:
        hatred = float((xinpan_profile or {}).get("hatred") or 0)
    except (TypeError, ValueError):
        hatred = 0.0
    explicit_commitment = bool(re.search(r"臣愿|奴才愿|小的愿|愿为陛下|臣领旨|遵旨|愿领|愿奉旨|敢不奉行|臣当奉行|臣愿担此", answer or ""))
    adjusted = stance
    hard_oppose = signals.get("hard_oppose", False)
    support = signals.get("support", False)
    caution = signals.get("caution", False)
    soft_oppose = signals.get("soft_oppose", False)
    if not hard_oppose:
        if preferred == "support":
            if stance == "neutral" and margin >= 4:
                adjusted = "support"
            elif stance == "caution" and margin >= 6 and support and not caution:
                adjusted = "support"
        elif preferred == "caution":
            if stance == "support" and margin >= 3 and not explicit_commitment:
                adjusted = "caution"
            elif stance == "neutral" and margin >= 3:
                adjusted = "caution"
        elif preferred == "oppose":
            if stance == "support" and (margin >= 4 or hatred >= 50) and not explicit_commitment:
                adjusted = "caution"
            elif stance == "support" and margin >= 7:
                adjusted = "caution"
            elif stance in {"neutral", "caution"} and margin >= 4 and not support:
                adjusted = "oppose" if fear < 70 and not soft_oppose else "caution"
            elif stance == "neutral" and hatred >= 80:
                adjusted = "oppose" if fear < 70 else "caution"
        if preferred == "oppose" and adjusted == "oppose" and fear >= 70 and not hard_oppose:
            adjusted = "caution"
    if adjusted != stance:
        confidence = max(confidence, 4)
        behavior["stance_adjustment"] = f"{stance}->{adjusted}"
    elif preferred == stance and margin >= 4:
        confidence = min(5, confidence + 1)
    return adjusted, max(1, min(5, int(confidence or 3))), behavior, xinpan_profile if isinstance(xinpan_profile, dict) else {}


def stance_evidence_from_chat(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    answer: str,
    stance: str,
    conditions: str,
    behavior_profile: Optional[Dict[str, object]] = None,
) -> tuple[Dict[str, object], List[str], str]:
    combined = f"{user_text}\n{answer}"
    drivers: List[Dict[str, str]] = []
    risks: List[str] = []

    def add_driver(kind: str, text: str) -> None:
        text = re.sub(r"\s+", " ", (text or "").strip())
        if not text:
            return
        item = {"kind": kind[:12], "text": text[:96]}
        if item not in drivers:
            drivers.append(item)

    add_driver("身份", f"{character.office_type} / {character.faction}，当前官职：{character.office or '无实职'}")
    if character.loyalty >= 78:
        add_driver("忠诚", "皇命优先，通常愿先确认圣意再办。")
    elif character.loyalty <= 48:
        add_driver("自保", "忠诚摇摆，容易先衡量个人退路与派系风向。")
    if character.ability >= 72 or character.wisdom >= 72:
        add_driver("能力", "能较快抓住手续、钱粮或执行瓶颈。")
    elif character.ability <= 48:
        add_driver("能力", "承办复杂政务时容易依赖他人或拖成文书往返。")
    if character.integrity >= 72:
        add_driver("清议", "重账目清楚与名分干净。")
    elif character.integrity <= 45:
        add_driver("手段", "更容易把灰色操作视为可用工具。")
    if character.courage >= 72:
        add_driver("胆略", "敢担责，遇阻不易立刻退缩。")
    elif character.courage <= 45:
        add_driver("胆略", "畏祸趋避，遇高压差事容易求保全。")

    tiangang = npc_tiangang_behavior_brief(character.name)
    for line in tiangang.splitlines():
        clean = line.strip().lstrip("-").strip()
        if clean.startswith("原型：") or clean.startswith("政治底色："):
            add_driver("天罡", clean)
        if len([d for d in drivers if d["kind"] == "天罡"]) >= 2:
            break

    try:
        xinpan = db.get_xinpan_profile(character.name, state)
    except Exception:
        xinpan = {}
    if xinpan:
        quadrant = str(xinpan.get("quadrant") or "")
        behavior_hint = str(xinpan.get("behavior_hint") or "")
        add_driver("心盘", f"{quadrant}：{behavior_hint}")
        if xinpan.get("warnings"):
            risks.append("心盘")

    if behavior_profile:
        preferred = str(behavior_profile.get("preferred_stance") or "")
        preferred_label = {"support": "偏支持", "caution": "偏附条件", "oppose": "偏反对", "neutral": "偏审慎"}.get(preferred, preferred)
        reasons = "；".join(str(item).strip() for item in (behavior_profile.get("reasons") or [])[:3] if str(item).strip())
        adjustment = str(behavior_profile.get("stance_adjustment") or "")
        note = preferred_label + (f"；{reasons}" if reasons else "")
        if adjustment:
            note += f"；规则修正{adjustment}"
        add_driver("行为档案", note)
        for tag in behavior_profile.get("risk_tags") or []:
            clean_tag = str(tag).strip()
            if clean_tag and clean_tag not in risks:
                risks.append(clean_tag)

    for rec in npc_network_recommendations(character.name, db=db, limit=24):
        target = str(rec.get("name") or "")
        if target and target in combined:
            evidence = "；".join(_clean_obsidian_text(x) for x in (rec.get("evidence") or [])[:2])
            add_driver("人脉", f"{target}（{evidence or '人脉可牵动'}）")

    for label, pattern in (
        ("银两", r"银|钱|饷|国库|内库|经费|财用|亏空"),
        ("人手", r"人手|胥吏|差役|官员|属官|书吏|匠"),
        ("名分", r"名分|祖制|成例|法度|体统|章程|会审|廷议"),
        ("期限", r"期限|时日|急|缓|月内|旬日|仓促"),
        ("派系", r"清流|东林|阉党|内廷|厂卫|士林|公论|弹劾|朋党"),
        ("地方", r"地方|士绅|豪右|民变|灾|粮|州县|巡抚|布政"),
        ("军务", r"兵|军|边镇|辽东|关宁|总兵|营伍|补给"),
        ("保密", r"密|泄|风声|耳目|线索|查访|锦衣卫|东厂"),
    ):
        if re.search(pattern, combined) and label not in risks:
            risks.append(label)
    if conditions:
        add_driver("条件", conditions)

    hint_by_stance = {
        "support": "若诏书交给其本人或本衙门，月末推演应降低其个人拖延；仍须检验钱粮、人手、名分和外部阻力。",
        "caution": "只有满足其条件时才算做通；未满足则应表现为折损、补奏、拖延或转求程序。",
        "oppose": "若强推，阻力应来自其能影响的官署、同僚、程序或舆论，不应写成空泛群臣不满。",
        "neutral": "尚未形成承诺，只能作为一般意见，不能当成执行背书。",
    }
    return {"drivers": drivers[:8], "source": "chat"}, risks[:8], hint_by_stance.get(stance, hint_by_stance["neutral"])


def _goal_conditions_from_text(conditions: str, action_kind: str) -> List[Dict[str, object]]:
    tasks = extract_negotiation_tasks(conditions, action_kind)
    return [{"description": task, "status": "pending", "evidence": ""} for task in tasks]


def _conditions_satisfied_by_text(conditions: List[Dict[str, object]], text: str) -> tuple[bool, List[Dict[str, object]], str]:
    if not conditions:
        return False, conditions, ""
    context = text or ""
    updated: List[Dict[str, object]] = []
    all_done = True
    evidence_parts: List[str] = []
    for raw in conditions:
        item = dict(raw)
        desc = str(item.get("description") or "")
        status = str(item.get("status") or "pending")
        if status == "done":
            updated.append(item)
            evidence_parts.append(str(item.get("evidence") or desc))
            continue
        relevant = any(term in context and term in desc for term in ("明旨", "圣旨", "廷议", "银", "钱", "饷", "粮", "人手", "胥吏", "保全", "家眷", "官", "边界", "期限", "密旨", "密令"))
        done = relevant and re.search(r"准|许|给|拨|发|添|派|授|任|明旨|圣旨|照办|朕已|即刻|下旨|拟旨|会同|保全|安置", context)
        if done:
            item["status"] = "done"
            item["evidence"] = f"奏对中明确满足：{desc[:80]}"
            evidence_parts.append(str(item["evidence"]))
        else:
            all_done = False
        updated.append(item)
    return all_done, updated, "；".join(evidence_parts[:3])


def _progress_score(previous: int, negotiation: NegotiationResult, stance: str) -> int:
    base = int(previous or 0)
    instant = int(negotiation.psychological_score or 0)
    if stance == "support":
        delta = 12 + round(max(0, instant - 55) / 4)
    elif stance == "caution":
        delta = 6 + round(max(0, instant - 55) / 8)
    elif stance == "oppose":
        delta = -18
    else:
        delta = -4 if instant < 45 else 4
    if negotiation.explicit_commitment:
        delta += 12
    if negotiation.tasks:
        delta = max(delta, 8)
    if negotiation.handshake_status == HANDSHAKE_BLOCKED:
        delta = min(delta, -16)
    return max(0, min(99, base + delta))


def _agreement_tasks_for_goal(goal: Dict[str, object]) -> List[str]:
    action_kind = str(goal.get("action_kind") or "general")
    target = str(goal.get("target_text") or goal.get("title") or "本次奏对标的")
    if action_kind in INSTANT_AGREEMENT_ACTIONS:
        return []
    if action_kind == "secret_order":
        return [f"完成密办/取证并回报：{target}"[:180]]
    if action_kind == "policy":
        return [f"在后续执行中实际协办或背书：{target}"[:180]]
    if action_kind == "court_commitment":
        return [f"实际履行奏对约定：{target}"[:180]]
    return []


def _create_agreement_for_goal(
    db: Any,
    state: GameState,
    goal: Dict[str, object],
    *,
    stance_id: int = 0,
    summary: str = "",
    conditions: str = "",
) -> int:
    existing = int(goal.get("agreement_id") or 0)
    if existing:
        return existing
    action_kind = str(goal.get("action_kind") or "general")
    tasks = _agreement_tasks_for_goal(goal)
    status = "sealed" if not tasks else "pending"
    score = int(goal.get("score") or 100)
    threshold = int(goal.get("threshold") or commitment_required(action_kind))
    topic = str(goal.get("title") or goal.get("target_text") or "本次奏对目的")
    agreement_id = db.create_negotiation_agreement(
        state,
        minister_name=str(goal.get("minister_name") or ""),
        topic=topic,
        action_kind=action_kind,
        status=status,
        stance_id=stance_id,
        goal_id=int(goal.get("id") or 0),
        handshake_status=HANDSHAKE_SEALED,
        psychological_score=score,
        threshold=threshold,
        verbal_only=not tasks,
        core_topic=topic,
        target_text=str(goal.get("target_text") or topic),
        promise_type=promise_type_from_terms(action_kind, conditions, tasks),
        stakes=stakes_from_terms(action_kind, conditions, f"{topic}\n{goal.get('target_text') or ''}"),
        due_turn=int(state.turn) + (1 if tasks else 0),
        conditions=conditions,
        summary=summary or f"奏对目的已握手：{topic}",
        tasks=tasks,
    )
    db.bind_conversation_goal_agreement(int(goal.get("id") or 0), agreement_id)
    return agreement_id


def record_dialogue_effects(
    db: Any,
    state: GameState,
    character: Character,
    user_text: str,
    answer: str,
    prepared: Optional[PreparedDialogue] = None,
    *,
    source_chat_turn_id: int = 0,
    persistent: bool = True,
) -> Dict[str, object]:
    if not persistent:
        return {}
    prepared = prepared or prepare_dialogue_context(db, state, character, user_text, persistent=True)
    detection = prepared.detection
    active_goal = prepared.active_goal or db.get_active_conversation_goal(character.name)
    combined = f"{user_text}\n{answer}"

    if detection.abandon and active_goal:
        updated = db.abandon_conversation_goal(
            state,
            int(active_goal["id"]),
            reason=detection.reason or "玩家主动放弃当前奏对目的。",
            source_chat_turn_id=source_chat_turn_id,
        )
        try:
            db.apply_chat_xinpan_update(
                state,
                character.name,
                user_text,
                answer,
                stance="neutral",
                handshake_status=HANDSHAKE_NONE,
                psychological_score=int(updated.get("score") or 0),
                source_chat_turn_id=source_chat_turn_id,
                goal_context={"event": "abandoned", "status": GOAL_ABANDONED, "action_kind": updated.get("action_kind") or ""},
            )
        except Exception:
            pass
        return {"goal": updated, "event": "abandoned"}

    if not detection.has_goal and active_goal is None and not POLITICAL_CHAT_RE.search(combined):
        return {}

    if active_goal and detection.has_goal and detection.switches_goal:
        db.update_conversation_goal(
            int(active_goal["id"]),
            state=state,
            event_kind="switched",
            event_summary=f"玩家转入新目的：{detection.title}",
            source_chat_turn_id=source_chat_turn_id,
            status=GOAL_ABANDONED,
            abandoned_reason=f"转入新目的：{detection.title}"[:180],
            last_delta_json={"new_goal": detection.title},
        )
        try:
            db.apply_chat_xinpan_update(
                state,
                character.name,
                user_text,
                answer,
                stance="neutral",
                handshake_status=HANDSHAKE_NONE,
                psychological_score=int(active_goal.get("score") or 0),
                source_chat_turn_id=source_chat_turn_id,
                goal_context={"event": "switched", "status": GOAL_ABANDONED, "action_kind": active_goal.get("action_kind") or ""},
            )
        except Exception:
            pass
        active_goal = None

    stance, confidence, signals = infer_chat_stance(answer)
    behavior_text = f"{(active_goal or {}).get('title') or detection.title}\n{combined}"
    stance, confidence, behavior_profile, xinpan_profile = adjust_stance_by_persona(
        db, state, character.name, behavior_text, answer, stance, confidence, signals
    )
    conditions = extract_conditions(answer)
    related_issue_id = related_issue_for_chat(db, combined)
    related_issue_title = ""
    if related_issue_id:
        try:
            issue_row = db.conn.execute("SELECT title FROM issues WHERE id=?", (related_issue_id,)).fetchone()
            related_issue_title = str(issue_row["title"] or "") if issue_row else ""
        except Exception:
            related_issue_title = ""

    goal: Optional[Dict[str, object]] = active_goal
    if detection.has_goal and goal is None:
        threshold = _threshold_for_goal(character, detection.action_kind, user_text, xinpan_profile, behavior_profile)
        goal_id = db.create_conversation_goal(
            state,
            minister_name=character.name,
            action_kind=detection.action_kind,
            title=detection.title,
            target_text=detection.target_text,
            threshold=threshold,
            score=0,
            status=GOAL_ACTIVE,
            condition_status="none",
            related_issue_id=related_issue_id,
            source_chat_turn_id=source_chat_turn_id,
            expires_turn=int(state.turn) + 1,
            last_delta={"detected_by": detection.source, "confidence": detection.confidence},
        )
        goal = db.get_conversation_goal(goal_id)

    goal_for_eval = goal or {
        "action_kind": detection.action_kind or "general",
        "title": detection.title or _topic_label(user_text),
        "target_text": detection.target_text or target_text_from_terms(detection.action_kind or "general", _topic_label(user_text), stance, answer),
    }
    threshold = int((goal or {}).get("threshold") or _threshold_for_goal(character, str(goal_for_eval.get("action_kind") or "general"), user_text, xinpan_profile, behavior_profile))
    negotiation = evaluate_negotiation(
        character,
        user_text,
        answer,
        stance,
        conditions,
        related_issue_title,
        goal=goal_for_eval,
        action_kind=str(goal_for_eval.get("action_kind") or "general"),
        threshold_override=threshold,
        xinpan_profile=xinpan_profile,
        behavior_profile=behavior_profile,
    )

    old_score = int((goal or {}).get("score") or 0)
    old_conditions = [item for item in ((goal or {}).get("conditions") or []) if isinstance(item, dict)]
    satisfied_by_text, satisfied_conditions, condition_evidence = _conditions_satisfied_by_text(old_conditions, user_text)
    condition_items = _goal_conditions_from_text(conditions, negotiation.action_kind) if conditions else old_conditions
    blockers = list(negotiation.blockers)
    pressure = bool(re.search(r"强旨|不许推辞|必须奉行|若不从|抗旨|严办", user_text or ""))

    event = "progress"
    next_status = GOAL_ACTIVE
    condition_status = str((goal or {}).get("condition_status") or "none")
    next_score = _progress_score(old_score, negotiation, stance)
    handshake_status = HANDSHAKE_NONE
    if satisfied_by_text:
        condition_items = satisfied_conditions
        next_status = GOAL_SEALED
        condition_status = "satisfied"
        next_score = 100
        handshake_status = HANDSHAKE_SEALED
        event = "conditions_satisfied"
        blockers = []
    elif negotiation.handshake_status == HANDSHAKE_SEALED:
        next_status = GOAL_SEALED
        condition_status = "satisfied"
        next_score = 100
        handshake_status = HANDSHAKE_SEALED
        event = "sealed"
    elif negotiation.handshake_status == HANDSHAKE_CONDITIONAL or condition_items:
        next_status = GOAL_WAITING
        condition_status = "pending"
        next_score = max(old_score, min(99, max(next_score, threshold - 8)))
        handshake_status = HANDSHAKE_CONDITIONAL
        event = "waiting_conditions"
    elif negotiation.handshake_status == HANDSHAKE_BLOCKED:
        next_status = GOAL_BLOCKED
        condition_status = "failed" if old_conditions else "none"
        next_score = max(0, min(old_score, negotiation.psychological_score))
        handshake_status = HANDSHAKE_BLOCKED
        event = "blocked"
    elif goal and next_score >= threshold and stance == "support" and not blockers:
        next_status = GOAL_SEALED
        condition_status = "satisfied"
        next_score = 100
        handshake_status = HANDSHAKE_SEALED
        event = "sealed"

    if goal:
        delta_payload = {
            "event": event,
            "stance": stance,
            "score_before": old_score,
            "score_after": next_score,
            "threshold": threshold,
            "handshake_status": handshake_status,
            "conditions": condition_items,
            "blockers": blockers,
            "pressure": pressure,
            "negotiation": negotiation.factors,
        }
        db.update_conversation_goal(
            int(goal["id"]),
            state=state,
            event_kind=event,
            event_summary=f"{handshake_label(handshake_status)}：{goal.get('title') or negotiation.core_topic}",
            source_chat_turn_id=source_chat_turn_id,
            status=next_status,
            score=next_score,
            threshold=threshold,
            condition_status=condition_status,
            conditions_json=condition_items,
            blockers_json=blockers,
            related_issue_id=related_issue_id or int(goal.get("related_issue_id") or 0),
            last_delta_json=delta_payload,
            expires_turn=int(state.turn) + (3 if next_status == GOAL_WAITING else 1),
        )
        goal = db.get_conversation_goal(int(goal["id"])) or goal

    topic = negotiation.core_topic or str((goal or {}).get("title") or _compact(user_text, 80) or "本次奏对事项")
    summary = {
        "support": f"{character.name}已表示愿意支持/承办此事。",
        "oppose": f"{character.name}明确反对或不愿奉行此事。",
        "caution": f"{character.name}附条件赞成或有重大保留。",
        "neutral": f"{character.name}未给出明确承诺，只作一般分析。",
    }[stance]
    if conditions:
        summary += f" 条件/顾虑：{conditions}"
    summary += f" 奏对目的：{handshake_label(handshake_status)}，{next_score}/{threshold}。"
    evidence, risk_tags, execution_hint = stance_evidence_from_chat(
        db, state, character, user_text, answer, stance, conditions, behavior_profile=behavior_profile
    )
    if handshake_status == HANDSHAKE_SEALED:
        execution_hint = "奏对目的已握手；是否成为执行背书以履约账本 target_status 为准。"
    elif handshake_status == HANDSHAKE_CONDITIONAL:
        execution_hint = "目的附条件待证；条件未闭环前不得当作自愿配合。"
    elif handshake_status == HANDSHAKE_BLOCKED:
        execution_hint = "目的未握手；若强推，应按强旨/政治高压处理，而不是自愿配合。"

    psychological = {
        "goal_id": int((goal or {}).get("id") or 0),
        "action_kind": negotiation.action_kind,
        "handshake_status": handshake_status,
        "score": next_score,
        "threshold": threshold,
        "verbal_only": handshake_status == HANDSHAKE_SEALED and not condition_items,
        "explicit_commitment": negotiation.explicit_commitment,
        "core_topic": topic,
        "target_text": str((goal or {}).get("target_text") or negotiation.target_text),
        "promise_type": negotiation.promise_type,
        "stakes": negotiation.stakes,
        "due_turns": negotiation.due_turns,
        "tasks": [str(item.get("description") or "") for item in condition_items if isinstance(item, dict)],
        "blockers": blockers,
        "factors": negotiation.factors,
        "persona_behavior": behavior_profile,
    }
    stance_id = db.record_minister_stance(
        state,
        character.name,
        topic=topic,
        stance=stance,
        confidence=confidence,
        summary=summary,
        conditions=conditions,
        related_issue_id=related_issue_id,
        source_chat_turn_id=source_chat_turn_id,
        user_message=user_text,
        minister_answer=answer,
        evidence=evidence,
        risk_tags=risk_tags,
        execution_hint=execution_hint,
        handshake_status=handshake_status,
        psychological_score=next_score,
        psychological=psychological,
        goal_id=int((goal or {}).get("id") or 0),
    )

    try:
        db.apply_chat_xinpan_update(
            state,
            character.name,
            user_text,
            answer,
            stance=stance,
            handshake_status=handshake_status,
            psychological_score=next_score,
            source_chat_turn_id=source_chat_turn_id,
            goal_context={
                "event": event,
                "status": next_status,
                "action_kind": negotiation.action_kind,
                "goal_id": int((goal or {}).get("id") or 0),
                "pressure": pressure,
            },
        )
    except Exception:
        pass

    agreement_id = 0
    if goal and next_status == GOAL_SEALED:
        agreement_id = _create_agreement_for_goal(
            db,
            state,
            goal,
            stance_id=stance_id,
            summary=summary,
            conditions=condition_evidence or conditions,
        )
        db.update_minister_stance_agreement(stance_id, agreement_id)
        db.add_conversation_goal_event(
            state,
            int(goal["id"]),
            "agreement_created",
            status=GOAL_SEALED,
            score_delta=0,
            score_after=100,
            summary=f"已进入履约账本 #{agreement_id}",
            payload={"agreement_id": agreement_id},
            source_chat_turn_id=source_chat_turn_id,
        )
    return {
        "goal": goal,
        "stance_id": stance_id,
        "agreement_id": agreement_id,
        "event": event,
        "handshake_status": handshake_status,
        "score": next_score,
    }


def _task_done_by_context(description: str, context: str) -> tuple[str, str]:
    if not description or not context:
        return "pending", ""
    combined = f"{description}\n{context}"
    contradiction = re.search(
        r"未准|驳回|搁置|不予|不许|未拨|无银可拨|未给|未设|未议|未下|未见|"
        r"食言|失信|背约|不兑现|作罢|强旨|强行|勒令",
        combined,
    )
    if contradiction:
        return "failed", "诏书或邸报出现未兑现/强推/驳回等相反证据。"
    relevant = any(term in description and term in context for term in ("明旨", "圣旨", "廷议", "银", "钱", "饷", "粮", "人手", "保全", "家眷", "安置", "官", "边界", "期限", "密旨", "密令", "会审", "章程"))
    if relevant and re.search(r"准|照办|奉旨|已办|成议|允行|照准|如议|明旨|圣旨|廷议|会审|拨|发|支|给|添|派|授|补|任|擢|调|保全|安置|抚恤|密旨|密令", combined):
        return "done", f"发现与「{description[:48]}」相符的条件兑现证据。"
    return "pending", ""


def review_conversation_goals(
    db: Any,
    state: GameState,
    *,
    decree_text: str = "",
    narrative: str = "",
    directives: Optional[List[Any]] = None,
    applied: Optional[Dict[str, object]] = None,
    phase: str = "preresolve",
    limit: int = 80,
) -> List[Dict[str, object]]:
    context = db._agreement_review_context(
        decree_text=decree_text,
        narrative=narrative,
        directives=directives,
        applied=applied,
    )
    goals = db.list_conversation_goals(statuses=[GOAL_ACTIVE, GOAL_WAITING], limit=limit)
    reviewed: List[Dict[str, object]] = []
    for goal in goals:
        status = str(goal.get("status") or "")
        conditions = [dict(item) for item in (goal.get("conditions") or []) if isinstance(item, dict)]
        expires_turn = int(goal.get("expires_turn") or 0)
        if status == GOAL_ACTIVE and not conditions and phase == "postresolve":
            db.update_conversation_goal(
                int(goal["id"]),
                state=state,
                event_kind="expired",
                event_summary="本回合未继续推进，奏对目的过期。",
                status=GOAL_EXPIRED,
                last_delta_json={"phase": phase},
            )
            reviewed.append(db.get_conversation_goal(int(goal["id"])) or goal)
            continue
        if status == GOAL_WAITING and expires_turn and int(state.turn) > expires_turn:
            db.update_conversation_goal(
                int(goal["id"]),
                state=state,
                event_kind="expired",
                event_summary="附条件目的逾期未见闭环，已过期。",
                status=GOAL_EXPIRED,
                last_delta_json={"phase": phase},
            )
            reviewed.append(db.get_conversation_goal(int(goal["id"])) or goal)
            continue
        if not conditions or not context:
            continue
        changed = False
        for item in conditions:
            if str(item.get("status") or "pending") != "pending":
                continue
            next_status, evidence = _task_done_by_context(str(item.get("description") or ""), context)
            if next_status != "pending":
                item["status"] = next_status
                item["evidence"] = evidence[:240]
                changed = True
        statuses = [str(item.get("status") or "pending") for item in conditions]
        if any(item == "failed" for item in statuses):
            db.update_conversation_goal(
                int(goal["id"]),
                state=state,
                event_kind="condition_failed",
                event_summary="目的条件被官方文本否定。",
                status=GOAL_BLOCKED,
                condition_status="failed",
                conditions_json=conditions,
                last_delta_json={"conditions": conditions, "phase": phase},
            )
            reviewed.append(db.get_conversation_goal(int(goal["id"])) or goal)
        elif statuses and all(item == "done" for item in statuses):
            db.update_conversation_goal(
                int(goal["id"]),
                state=state,
                event_kind="conditions_satisfied",
                event_summary="目的条件已由诏书/草案/邸报证实，握手达成。",
                status=GOAL_SEALED,
                score=100,
                condition_status="satisfied",
                conditions_json=conditions,
                last_delta_json={"conditions": conditions, "phase": phase},
            )
            sealed = db.get_conversation_goal(int(goal["id"])) or goal
            stances = db.list_minister_stances(turn=state.turn, minister_name=str(goal.get("minister_name") or ""), limit=8)
            stance_id = 0
            for stance in stances:
                if int(stance.get("goal_id") or 0) == int(goal["id"]):
                    stance_id = int(stance.get("id") or 0)
                    break
            agreement_id = _create_agreement_for_goal(
                db,
                state,
                sealed,
                stance_id=stance_id,
                summary=f"条件审计闭环：{sealed.get('title') or sealed.get('target_text')}",
                conditions="；".join(str(item.get("evidence") or item.get("description") or "") for item in conditions),
            )
            if stance_id:
                db.update_minister_stance_agreement(stance_id, agreement_id)
            reviewed.append(db.get_conversation_goal(int(goal["id"])) or sealed)
        elif changed:
            db.update_conversation_goal(
                int(goal["id"]),
                state=state,
                event_kind="condition_reviewed",
                event_summary="目的条件审计更新，尚未全部闭环。",
                conditions_json=conditions,
                last_delta_json={"conditions": conditions, "phase": phase},
            )
            reviewed.append(db.get_conversation_goal(int(goal["id"])) or goal)
    return reviewed
