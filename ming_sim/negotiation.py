"""Negotiation scoring for summons.

This module turns a minister chat into a hidden psychological scale. It is
deliberately heuristic: the LLM provides in-character language, while this code
guards game mechanics from treating any polite sentence as a binding agreement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

from ming_sim.models import Character


HANDSHAKE_SEALED = "sealed"
HANDSHAKE_CONDITIONAL = "conditional"
HANDSHAKE_BLOCKED = "blocked"
HANDSHAKE_NONE = "none"

MONEY_RESOURCE_RE = (
    r"户部|太仓|国库|内库|亏空|钱粮|钱银|钱款|钱财|财用|度支|经费|"
    r"缺钱|没钱|用钱|拨钱|筹钱|给钱|银|饷|粮|税|盐|商税|清丈"
)
CASTRATION_CORE_RE = r"净身|去势|阉割|受阉|自阉|阉了|阉掉|阉去|阉为|阉作"
INNER_IDENTITY_CONVERSION_RE = (
    r"(愿|自愿|是否|可愿|愿否|愿不愿|令卿|若令卿|命卿|着卿|令其|命其|"
    r"让你|让他|让她|让其|朕欲|欲令).{0,28}"
    r"(入内廷|入宫|司礼监|太监|宦官|内臣|近侍)"
)
CASTRATION_CONTEXT_RE = rf"{CASTRATION_CORE_RE}|{INNER_IDENTITY_CONVERSION_RE}"


@dataclass(frozen=True)
class NegotiationResult:
    action_kind: str
    handshake_status: str
    psychological_score: int
    threshold: int
    verbal_only: bool
    explicit_commitment: bool
    core_topic: str = ""
    target_text: str = ""
    promise_type: str = ""
    stakes: str = ""
    due_turns: int = 1
    tasks: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    factors: Dict[str, int | str | bool] = field(default_factory=dict)

    @property
    def successful(self) -> bool:
        return self.handshake_status == HANDSHAKE_SEALED


def action_kind_from_text(text: str) -> str:
    if re.search(r"奴籍|民籍|脱籍|还民|转为民|转民籍|出宫为民|归为百姓|赐还为民", text):
        return "emancipation"
    if re.search(CASTRATION_CONTEXT_RE, text):
        return "castration"
    if re.search(r"密令|秘密任务|暗查|密查|盯梢|取证|密旨", text):
        return "secret_order"
    if re.search(
        r"劝|说服|游说|调停|转圜|斡旋|背书|代奏|联络|试探|探口风|保密|守口|不泄|承办|协办|办成|包揽|担待",
        text,
    ):
        return "court_commitment"
    if re.search(
        r"任命|任免|授官|授职|授衔|补缺|补任|擢升|擢用|调任|调补|调往|铨选|铨叙|"
        r"举荐|保荐|起用|罢官|罢黜|去职|下狱|流放|致仕|升官|官职|官缺|缺分|"
        r"人选|备选|起复|堂官|尚书|侍郎|职掌|品级",
        text,
    ):
        return "personnel"
    if re.search(rf"{MONEY_RESOURCE_RE}|军|兵|厂卫|清流|东林|阉党|抄家|查账|新政", text):
        return "policy"
    return "general"


def commitment_required(action_kind: str) -> int:
    if action_kind == "castration":
        return 86
    if action_kind == "emancipation":
        return 78
    if action_kind in {"secret_order", "personnel"}:
        return 72
    if action_kind == "court_commitment":
        return 68
    if action_kind == "policy":
        return 66
    return 62


def _compact_clause(text: str, limit: int = 96) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(r"^(朕|我|你|卿|问|请|拟|密令|旨意|着|命)[:：，,\s]*", "", cleaned)
    return cleaned[:limit]


def core_topic_from_chat(
    user_text: str,
    answer: str,
    action_kind: str,
    related_issue_title: str = "",
) -> str:
    """Extract the political issue at stake, not the conversational surface.

    The simulator needs a durable agenda item: "辽饷筹措" is useful, while
    "朕问你怎么看" is not. This remains deterministic so the agreement ledger
    works even when no extra LLM call is available.
    """
    if related_issue_title:
        return _compact_clause(related_issue_title, 80)

    combined = f"{user_text}\n{answer}"
    issue_patterns = [
        ("辽东军饷与边防", r"辽东|关宁|宁锦|山海关|蓟辽|辽饷|建州|后金"),
        ("陕西赈灾与流寇", r"陕西|陕北|流寇|饥民|赈|驿卒|高迎祥|李自成"),
        (
            "人事任免与官缺",
            r"任命|任免|授官|授职|授衔|补缺|补任|擢升|擢用|调任|调补|调往|铨选|铨叙|"
            r"举荐|保荐|起用|罢官|罢黜|去职|下狱|流放|致仕|升官|官职|官缺|缺分|"
            r"人选|备选|起复|堂官|尚书|侍郎|职掌|品级",
        ),
        ("阉党清理与厂卫", r"魏忠贤|阉党|厂卫|东厂|锦衣卫|司礼监|客氏|崔呈秀"),
        ("东林清流与廷议", r"东林|清流|廷议|言官|都察院|科道|士林|公论"),
        ("户部亏空与钱粮", MONEY_RESOURCE_RE),
        ("密查取证与内线", r"密令|密旨|暗查|密查|盯梢|取证|线人|耳目"),
        ("内廷身份转换", rf"{CASTRATION_CONTEXT_RE}|奴籍|民籍|脱籍|还民"),
        ("军务调度与将帅", r"军|兵|营|总兵|督师|调防|换帅|招抚|练兵|火器"),
    ]
    for label, pattern in issue_patterns:
        if re.search(pattern, combined):
            return label

    first = _compact_clause(user_text, 80)
    if first and not re.fullmatch(r"(怎么看|如何|可否|是否|卿以为如何|何如)[？?。!！\s]*", first):
        return first
    return {
        "castration": "内廷身份转换",
        "emancipation": "奴籍转民籍",
        "secret_order": "密查取证",
        "personnel": "人事任免",
        "policy": "政务推行",
    }.get(action_kind, "本次奏对事项")


def promise_type_from_terms(action_kind: str, conditions: str, tasks: List[str]) -> str:
    text = f"{conditions}\n{'；'.join(tasks)}"
    if action_kind in {"castration", "emancipation"}:
        return "身份身家承诺"
    if re.search(r"密|泄|风声|耳目|取证|暗查", text) or action_kind == "secret_order":
        return "密办承诺"
    if re.search(r"任|授|补|擢|调|罢|官|名分|廷议|会审|章程", text) or action_kind == "personnel":
        return "名分程序承诺"
    if re.search(rf"{MONEY_RESOURCE_RE}|赏|抚恤|安置", text):
        return "资源兑现承诺"
    if action_kind == "policy":
        return "政务协办承诺"
    if action_kind == "court_commitment":
        return "通用奏对承诺"
    return "口头协力承诺"


def target_text_from_terms(action_kind: str, core_topic: str, stance: str, answer: str) -> str:
    topic = core_topic or "本次奏对事项"
    if action_kind == "castration":
        return "本人同意净身入内廷并接受内廷身份转换"
    if action_kind == "emancipation":
        return "本人同意奴籍转民籍并接受身份转换"
    if action_kind == "secret_order":
        return f"本人同意密办/取证：{topic}"
    if action_kind == "personnel":
        if re.search(r"举荐|保荐|荐", answer):
            return f"本人同意为人事安排举荐或背书：{topic}"
        return f"本人同意支持或承办人事安排：{topic}"
    if action_kind == "policy":
        return f"本人同意支持、背书或协办政策：{topic}"
    if action_kind == "court_commitment":
        return f"本人同意履行奏对约定：{topic}"
    if stance == "support":
        return f"本人同意支持：{topic}"
    if stance == "caution":
        return f"本人附条件同意：{topic}"
    return f"本次奏对标的：{topic}"


def stakes_from_terms(action_kind: str, conditions: str, combined: str) -> str:
    text = f"{conditions}\n{combined}"
    stakes: List[str] = []
    if action_kind in {"castration", "emancipation"} or re.search(r"身|辱|名节|奴籍|民籍|家眷|族", text):
        stakes.append("身家名节")
    if re.search(MONEY_RESOURCE_RE, text):
        stakes.append("钱粮资源")
    if re.search(r"名分|廷议|成例|祖制|章程|法度", text):
        stakes.append("制度名分")
    if re.search(r"东林|清流|阉党|士林|厂卫|弹劾|朋党", text):
        stakes.append("派系声望")
    if re.search(r"密|泄|风声|灭门|杀身|取证|锦衣卫|东厂", text):
        stakes.append("保密风险")
    if re.search(r"军|兵|辽东|关宁|边镇|流寇", text):
        stakes.append("军国成败")
    return "、".join(stakes[:4]) or "一般政务"


def classify_task_kind(description: str) -> str:
    text = description or ""
    if re.search(rf"{MONEY_RESOURCE_RE}|赏|抚恤", text):
        return "resource"
    if re.search(r"人手|胥吏|差役|属官|书吏|匠|兵|校尉", text):
        return "staff"
    if re.search(r"名分|成例|法度|祖制|章程|明旨|圣旨|廷议|会审|部议|程序", text):
        return "legitimacy"
    if re.search(r"保全|家眷|家小|族人|安置|不辱|体面|遮护|免罪", text):
        return "protection"
    if re.search(r"官|任|授|补|擢|升|调|职掌|边界", text):
        return "office"
    if re.search(r"期限|时日|月内|旬日|限|缓|急", text):
        return "deadline"
    if re.search(r"保密|密|泄|风声|耳目|线索|取证|暗查", text):
        return "secrecy"
    return "general"


def extract_negotiation_tasks(conditions: str, action_kind: str = "general") -> List[str]:
    tasks: List[str] = []
    for raw in re.split(r"[；;。！？\n]", conditions or ""):
        clause = raw.strip(" ，,、")
        if not clause:
            continue
        if re.search(rf"{MONEY_RESOURCE_RE}|人手|名分|成例|明旨|圣旨|廷议|保全|家眷|赏|官|期限|东林|清流|厂卫|内廷|遮护|不辱|体面|安置|抚恤", clause):
            tasks.append(clause[:120])
    if action_kind in {"castration", "emancipation"} and not tasks and re.search(r"须|需|若|但|只是|除非", conditions or ""):
        tasks.append((conditions or "").strip()[:120])
    deduped: List[str] = []
    for task in tasks:
        if task and task not in deduped:
            deduped.append(task)
    return deduped[:5]


def evaluate_negotiation(
    character: Character | None,
    user_text: str,
    answer: str,
    stance: str,
    conditions: str,
    related_issue_title: str = "",
    *,
    goal: Optional[Mapping[str, object]] = None,
    action_kind: str = "",
    threshold_override: int = 0,
    xinpan_profile: Optional[Mapping[str, object]] = None,
    behavior_profile: Optional[Mapping[str, object]] = None,
) -> NegotiationResult:
    combined = f"{user_text}\n{answer}"
    if goal is not None:
        action_kind = str(goal.get("action_kind") or action_kind or "general").strip()
    action_kind = str(action_kind or "").strip() or action_kind_from_text(combined)
    tasks = extract_negotiation_tasks(conditions, action_kind)
    threshold = max(0, min(100, int(threshold_override or 0))) or commitment_required(action_kind)
    if goal is not None:
        core_topic = str(goal.get("title") or goal.get("core_topic") or "").strip()
        target_text = str(goal.get("target_text") or "").strip()
    else:
        core_topic = ""
        target_text = ""
    if not core_topic:
        core_topic = core_topic_from_chat(user_text, answer, action_kind, related_issue_title)
    if not target_text:
        target_text = target_text_from_terms(action_kind, core_topic, stance, answer)
    promise_type = promise_type_from_terms(action_kind, conditions, tasks)
    stakes = stakes_from_terms(action_kind, conditions, combined)

    loyalty = int(getattr(character, "loyalty", 50) if character is not None else 50)
    courage = int(getattr(character, "courage", 50) if character is not None else 50)
    integrity = int(getattr(character, "integrity", 50) if character is not None else 50)
    ability = int(getattr(character, "ability", 50) if character is not None else 50)

    score = 34
    score += round((loyalty - 50) * 0.35)
    score += round((courage - 50) * 0.18)
    score += round((ability - 50) * 0.10)
    score -= round(max(0, integrity - 65) * 0.10)

    xinpan_profile = xinpan_profile or {}
    behavior_profile = behavior_profile or {}
    quadrant = str(xinpan_profile.get("quadrant") or "")
    try:
        dao_he = float(xinpan_profile.get("dao_he") or 0)
        shi_he = float(xinpan_profile.get("shi_he") or 0)
        fear = float(xinpan_profile.get("fear") or 0)
        hatred = float(xinpan_profile.get("hatred") or 0)
        trust = float(xinpan_profile.get("trust_coeff") or 1.0)
    except (TypeError, ValueError):
        dao_he = shi_he = fear = hatred = 0.0
        trust = 1.0
    score += round(max(-8.0, min(8.0, dao_he / 12.0)))
    score += round(max(-10.0, min(10.0, shi_he / 10.0)))
    score -= round(min(18.0, hatred / 6.0))
    if trust < 0.75:
        score -= round((0.75 - trust) * 18)
    if quadrant == "股肱":
        score += 8
    elif quadrant == "权附":
        score += 2
    elif quadrant == "离心":
        score -= 12
    if fear >= 70:
        # Fear can mute open defiance, but it is not consent.
        score += 3 if stance != "support" else 0

    preferred = str(behavior_profile.get("preferred_stance") or "")
    if preferred == "support":
        score += 8
    elif preferred == "caution":
        score -= 2
    elif preferred == "oppose":
        score -= 12

    explicit_commitment = bool(re.search(r"臣愿|奴才愿|小的愿|愿为陛下|臣领旨|遵旨|愿领|愿奉旨|愿听圣裁|敢不奉行|臣当奉行|臣愿担此", answer))
    explicit_castration = bool(re.search(r"臣愿净身|愿净身|自愿净身|愿入内廷|愿入宫禁|愿为内臣|愿作内臣|愿受此身", answer))
    explicit_emancipation = bool(re.search(r"愿脱籍|愿转民籍|愿还民|愿还为民|愿出宫为民|愿归民籍|愿作百姓|愿为百姓|谢恩还民", answer))
    hard_oppose = bool(re.search(r"万不可|断不可|绝不可|臣不敢从|臣不敢奉诏|不能从|不能奉行|难以奉行|不愿奉行|恕难奉行|收回成命", answer))
    soft_oppose = bool(re.search(r"不宜|不当|请陛下三思|恐不可|不可急|不可骤|不可轻", answer))
    self_sacrifice = bool(re.search(r"死|身家|性命|族|辱|名节|清议|后路|身后", combined))

    if stance == "support":
        score += 20
    elif stance == "caution":
        score += 4
    elif stance == "oppose":
        score -= 34
    else:
        score -= 8
    if explicit_commitment:
        score += 12
    if soft_oppose:
        score -= 14
    if hard_oppose:
        score -= 40
    if tasks:
        score -= 16
    if action_kind == "castration":
        score -= 24
        if explicit_castration:
            score += 32
        elif re.search(r"入内廷|入宫|司礼监|内臣", answer):
            score += 8
    if action_kind == "emancipation":
        score -= 10
        if explicit_emancipation:
            score += 24
        elif re.search(r"脱籍|还民|民籍|出宫|百姓", answer):
            score += 8
    if action_kind == "secret_order" and re.search(r"风声|泄|反噬|杀身|灭门", combined):
        score -= 8
    if self_sacrifice and not explicit_commitment:
        score -= 8

    score = max(0, min(100, int(score)))
    blockers: List[str] = []
    if hard_oppose or stance == "oppose":
        blockers.append("明确拒绝")
    if action_kind == "castration" and not explicit_castration:
        blockers.append("净身未明确自愿")
    if action_kind == "emancipation" and not explicit_emancipation:
        blockers.append("转民籍未明确自愿")
    score_gap = threshold - score
    tentative_path = bool(tasks and (explicit_commitment or explicit_castration or explicit_emancipation or stance == "caution"))
    if score_gap > 0 and not tasks:
        blockers.append(f"心理量表未过线（{score}/{threshold}）")
    elif tasks and score_gap > 24 and not tentative_path:
        blockers.append(f"心理量表离握手过远（{score}/{threshold}）")

    if blockers:
        handshake = HANDSHAKE_BLOCKED if stance == "oppose" or hard_oppose or action_kind in {"castration", "emancipation"} else HANDSHAKE_NONE
    elif tasks:
        handshake = HANDSHAKE_CONDITIONAL
    elif score >= threshold and (explicit_commitment or explicit_castration or explicit_emancipation):
        handshake = HANDSHAKE_SEALED
    else:
        handshake = HANDSHAKE_NONE

    verbal_only = bool(handshake == HANDSHAKE_SEALED and not tasks)
    return NegotiationResult(
        action_kind=action_kind,
        handshake_status=handshake,
        psychological_score=score,
        threshold=threshold,
        verbal_only=verbal_only,
        explicit_commitment=explicit_commitment or explicit_castration or explicit_emancipation,
        core_topic=core_topic,
        target_text=target_text,
        promise_type=promise_type,
        stakes=stakes,
        due_turns=1 if tasks else 0,
        tasks=tasks,
        blockers=blockers,
        factors={
            "loyalty": loyalty,
            "courage": courage,
            "integrity": integrity,
            "ability": ability,
            "stance": stance,
            "action_kind": action_kind,
            "xinpan_quadrant": quadrant,
            "xinpan_dao_he": round(dao_he, 1),
            "xinpan_shi_he": round(shi_he, 1),
            "xinpan_fear": round(fear, 1),
            "xinpan_hatred": round(hatred, 1),
            "xinpan_trust": round(trust, 2),
            "behavior_preferred": preferred,
            "has_conditions": bool(tasks),
            "explicit_commitment": explicit_commitment,
            "explicit_castration": explicit_castration,
            "explicit_emancipation": explicit_emancipation,
        },
    )


def handshake_label(status: str) -> str:
    return {
        HANDSHAKE_SEALED: "握手成功",
        HANDSHAKE_CONDITIONAL: "附条件",
        HANDSHAKE_BLOCKED: "未说服",
        HANDSHAKE_NONE: "未成约",
    }.get(status, "未成约")
