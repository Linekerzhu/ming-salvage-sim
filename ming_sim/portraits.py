"""Portrait generation pipeline helpers.

The game has two portrait classes:
- static release portraits in ``web/public/portraits``;
- save-bound generated portraits stored in SQLite ``portrait_assets`` rows.

This module owns deterministic character DNA, wardrobe classification, prompt
assembly, and the 302.ai nano-banana-2 text-to-image call shape.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from ming_sim.models import Character, GameState
from ming_sim.ranks import official_rank_for, rank_prompt_fragment


GENERATED_PORTRAIT_PREFIX = "generated:"
NANO_BANANA_DEFAULT_BASE_URL = "https://api.302.ai"
NANO_BANANA_DEFAULT_ENDPOINT = "/ws/api/v3/google/nano-banana-2/text-to-image"
NANO_BANANA_DEFAULT_EDIT_ENDPOINT = "/ws/api/v3/google/nano-banana-2/edit"
NANO_BANANA_DEFAULT_ORIGINAL_ENDPOINT = "/google/v1/models/gemini-3.1-flash-image-preview:generateContent?response_format=b64_json"
NANO_BANANA_MODEL = "nano-banana-2"
PORTRAIT_ASPECT_RATIO = "2:3"
DNA_SHEET_ASPECT_RATIO = "3:4"
REFERENCE_ROOT = Path(os.environ.get("MING_PORTRAIT_REFERENCE_DIR") or (Path.home() / "Downloads"))


@dataclass(frozen=True)
class PortraitSpec:
    dna_seed: str
    asset_id: str
    dna_asset_id: str
    wardrobe_key: str
    wardrobe_label: str
    prompt: str
    dna_prompt: str
    must_be_clean_shaven: bool
    reference_images: tuple[str, ...] = ()
    dna_reference_images: tuple[str, ...] = ()


WARDROBE_REFERENCE_IMAGES: Dict[str, tuple[str, ...]] = {
    "civil_high": ("高级文官.jpg",),
    "civil_mid": ("中级文官.jpg",),
    "civil_low": ("低级文官.jpg",),
    "eunuch_high": ("顶级太监.png", "高级太监.png"),
    "eunuch_mid": ("中级太监.jpg",),
    "eunuch_low": ("低级太监.jpg",),
    "eunuch_servant": ("底层太监.png",),
    "jinyiwei_high": ("高级锦衣卫.png",),
    "jinyiwei_mid": ("高级锦衣卫.png",),
    "jinyiwei_low": ("低级锦衣卫.png",),
    "military_high": ("高级武将.png",),
    "military_mid": ("高级武将.png",),
    "military_low": ("高级武将.png",),
    "consort_empress": ("皇后妃嫔.png", "皇后妃嫔2.png"),
    "consort_noble": ("皇后妃嫔2.png", "皇后妃嫔.png"),
    "commoner": (),
}


def _existing_reference_paths(names: tuple[str, ...]) -> tuple[str, ...]:
    paths = []
    for name in names:
        path = REFERENCE_ROOT / name
        if path.exists():
            paths.append(str(path))
    return tuple(paths)


def reference_images_for_wardrobe(wardrobe_key: str) -> tuple[str, ...]:
    return _existing_reference_paths(WARDROBE_REFERENCE_IMAGES.get(wardrobe_key, ()))


def dna_reference_images() -> tuple[str, ...]:
    # DNA sheets are intentionally generated from text only. A shared visual
    # angle reference makes the model borrow too much facial structure and
    # collapses character variety.
    return ()


def stable_dna_seed(character: Character) -> str:
    raw = "|".join([
        character.name,
        str(character.birth_year or 0),
        character.faction or "",
        character.summary or "",
        ",".join(character.aliases or []),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def is_eunuch_identity(character: Character) -> bool:
    text = f"{character.office or ''} {character.office_type or ''} {character.faction or ''}"
    if re.search(r"民籍|百姓|布衣|还民|脱籍", text):
        return False
    return bool(re.search(r"太监|宦官|内官|司礼监|东厂|内廷|秉笔|掌印|随堂", text))


def is_former_eunuch_commoner(character: Character) -> bool:
    text = f"{character.office or ''} {character.office_type or ''} {character.faction or ''} {character.style or ''} {character.summary or ''}"
    return bool(re.search(r"民籍|百姓|布衣|还民|脱籍", text) and re.search(r"太监|宦官|内官|司礼监|东厂|内廷|宫禁", text))


def _office_rank_tier(office: str, office_type: str) -> str:
    text = f"{office or ''} {office_type or ''}"
    if re.search(r"首辅|次辅|大学士|尚书|总督|督师|经略|司礼监掌印|提督东厂|都督", text):
        return "high"
    if re.search(r"侍郎|都御史|巡抚|总兵|副将|锦衣卫都指挥使|秉笔|太监", text):
        return "mid"
    if re.search(r"郎中|员外郎|主事|御史|编修|检讨|知县|千户|百户|随堂|小火者", text):
        return "low"
    return "common"


def _tier_from_grade(grade: int, fallback: str = "common") -> str:
    if grade <= 0:
        return fallback
    if grade <= 3:
        return "high"
    if grade <= 6:
        return "mid"
    return "low"


def wardrobe_for(character: Character) -> tuple[str, str]:
    office = character.office or ""
    office_type = character.office_type or ""
    rank = official_rank_for(
        office,
        office_type,
        power_id=getattr(character, "power_id", "ming") or "ming",
        faction=character.faction or "",
    )
    tier = _tier_from_grade(rank.grade, _office_rank_tier(office, office_type))
    text = f"{office} {office_type} {character.faction or ''}"
    if office_type == "后宫":
        if re.search(r"皇后|中宫", text):
            return ("consort_empress", "皇后礼服：红金凤冠霞帔，深蓝里襟，珠翠步摇")
        return ("consort_noble", "后宫妃嫔礼服：绣金霞帔、珠翠发冠、红蓝或金色宫装")
    if re.search(r"锦衣卫|北镇抚司|镇抚司", text):
        rank_text = rank_prompt_fragment(office, office_type, getattr(character, "power_id", "ming"), character.faction)
        return (f"jinyiwei_{tier}", f"{'高级' if tier in {'high','mid'} else '低级'}锦衣卫飞鱼服，绣纹束带，佩绣春刀。{rank_text}")
    if re.search(r"总兵|副将|参将|游击|守备|督师|经略|边镇|军|将|伯", text):
        rank_text = rank_prompt_fragment(office, office_type, getattr(character, "power_id", "ming"), character.faction)
        return (f"military_{tier}", f"{'高级' if tier in {'high','mid'} else '低级'}武将服制：可在品官袍外叠札甲或鱼鳞甲，披风与佩刀。{rank_text}")
    if re.search(r"民籍|百姓|布衣|还民|脱籍", text):
        return ("commoner", "民籍百姓布衣：粗麻或旧棉布交领短袍，素色头巾，布带，草鞋或旧布靴，不得再穿内廷冠服")
    if is_eunuch_identity(character):
        if tier == "high":
            return ("eunuch_high", "高级太监服：赭红或白金绣蟒袍，黑纱内廷冠，可有披风")
        if tier == "mid":
            return ("eunuch_mid", "中级太监服：深蓝或青绿圆领袍，内廷小冠，绣云纹")
        if tier == "low":
            return ("eunuch_low", "低级太监服：暗绿或灰青圆领袍，布带，朴素内廷帽")
        return ("eunuch_servant", "底层太监服：旧灰绿短袍，布带，朴素小帽")
    if office_type in {"内阁", "吏部", "户部", "礼部", "兵部", "刑部", "工部", "都察院", "翰林院", "地方"}:
        rank_text = rank_prompt_fragment(office, office_type, getattr(character, "power_id", "ming"), character.faction)
        if tier == "high":
            return ("civil_high", f"高级文官官服，威严但不帝王化。{rank_text}")
        if tier == "mid":
            return ("civil_mid", f"中级文官官服，端正实务。{rank_text}")
        return ("civil_low", f"低级文官官服，简素清峻。{rank_text}")
    if re.search(r"江湖|山庄|少林|武当|龙虎|商人|传教士|琴师|隐士|游侠|侠女|道长|真人|医|药", text):
        return ("society_custom", "江湖/社会人物服饰：按身份自动生成，保持晚明时代质感")
    rank_text = rank_prompt_fragment(office, office_type, getattr(character, "power_id", "ming"), character.faction)
    return ("auto_custom", f"未说明官位服饰：AI按官位、地域、社会身份自动设计晚明服装。{rank_text}")


def _pose_for(character: Character) -> str:
    text = f"{character.office or ''} {character.office_type or ''} {character.faction or ''} {character.style or ''} {character.summary or ''}"
    seed = stable_dna_seed(character)
    if character.office_type == "后宫":
        pose = _seed_pick(seed, "portrait_pose", (
            "one hand lightly lifting the outer sleeve while the other rests before the waist",
            "both hands folded low before the robe, shoulders square and ceremonial",
            "slight three-quarter court turn, one hand touching a sash tassel",
            "hands crossed before wide sleeves with fingers visible, chin lowered in restrained palace composure",
            "one sleeve drawn inward while the other hand remains fully visible as if pausing before counsel",
        ))
        expression = _seed_pick(seed, "portrait_expression", (
            "serene but watchful expression", "gentle authority", "reserved courtly sorrow",
            "quietly persuasive gaze", "dignified self-command",
        ))
    elif re.search(r"民籍|百姓|布衣|还民|脱籍", text):
        pose = _seed_pick(seed, "portrait_pose", (
            "standing with one hand holding a folded cloth bundle and the other visible at the belt",
            "slight three-quarter stance, one hand adjusting a plain headscarf, the other holding a small travel bundle",
            "humble upright stance with both hands visible before a coarse cloth robe",
            "one foot set forward as if leaving the palace gate, sleeves simple and hands visible",
            "plain commoner stance with a small ledger or cloth pouch held low",
        ))
        expression = _seed_pick(seed, "portrait_expression", (
            "relieved but cautious expression", "guarded new-freedom gaze", "humble wary calm",
            "quietly grateful but self-protective eyes", "nervous hope held under restraint",
        ))
    elif re.search(r"太监|宦官|内官|司礼监|东厂|内廷|秉笔|掌印|随堂", text):
        pose = _seed_pick(seed, "portrait_pose", (
            "one hand emerging from the sleeve and the other holding a folded imperial order",
            "both hands visible at sleeve openings, slight bow from the waist",
            "standing close and narrow, one sleeve lifted as if receiving an order, fingers visible",
            "three-quarter stance with a sealed document held low",
            "one hand at the belt, the other hand visible beside the sleeve, alert palace posture",
            "quick half-step forward, one hand clutching a palace tally and the other sleeve swept back",
            "head slightly tilted as if listening for an order, both hands visible outside the sleeves",
            "low deferential bow with robe hem flaring asymmetrically around both boots",
            "one hand presenting a folded yellow decree cloth while the other steadies the belt",
            "nervous alert stance, shoulders narrow, one foot pulled back as if ready to withdraw",
        ))
        expression = _seed_pick(seed, "portrait_expression", (
            "obedient but watchful expression", "quick and deferential gaze", "smooth guarded smile",
            "silent calculating calm", "dutiful inner-court focus", "bright anxious eyes",
            "a sly palace-trained glance", "strained eagerness under formal obedience",
        ))
    elif re.search(r"女将|女侠|红娘子|刀|武艺", text):
        pose = _seed_pick(seed, "portrait_pose", (
            "one hand resting on a sheathed sword, feet planted apart",
            "turning slightly as if about to step forward, one sleeve swept back",
            "left hand on belt and right hand holding a short weapon downward",
            "upright martial stance, cloak or sash falling behind the legs",
            "alert ready stance with one boot forward and both hands visible",
        ))
        expression = _seed_pick(seed, "portrait_expression", (
            "fearless direct gaze", "cool half-smile", "unyielding heroine composure",
            "field-hardened calm", "bright combative focus",
        ))
    elif re.search(r"果敢|刚|勇|悍|武|死战|冲锋|总兵|副将|边镇|将军|锦衣卫", text):
        pose = _seed_pick(seed, "portrait_pose", (
            "one hand resting on a sheathed weapon or belt, stern forward stance",
            "feet braced wide, one hand gripping the sword hilt and the other holding a helmet or sleeve",
            "three-quarter command stance, one arm lowered and the other at the waist",
            "cloak or robe pulled back to reveal boots, both hands visible",
            "slight forward lean as if issuing battlefield orders",
        ))
        expression = _seed_pick(seed, "portrait_expression", (
            "stern battlefield focus", "hard impatient stare", "weathered command calm",
            "anger held under discipline", "brave but tired eyes",
        ))
    elif re.search(r"圆滑|权谋|机变|阴|谨慎|老成|自保|党争", text):
        pose = _seed_pick(seed, "portrait_pose", (
            "both hands visible at sleeve openings, slight three-quarter turn, calculating restrained posture",
            "one hand extended from the sleeve as if testing the room, the other hand fully visible",
            "standing with folded document held against the chest",
            "narrow stance, sleeve edges gathered carefully before the waist",
            "one shoulder angled away while the face watches forward",
        ))
        expression = _seed_pick(seed, "portrait_expression", (
            "calculating restrained gaze", "polite guarded smile", "cautious official calm",
            "watchful eyes under lowered brows", "self-protective composure",
        ))
    elif re.search(r"清|正|廉|理学|弹劾|气节|忠贞|守正", text):
        pose = _seed_pick(seed, "portrait_pose", (
            "upright austere stance, memorial tablet or folded document in hand",
            "both hands visible holding a memorial at chest height",
            "straight-backed stance with sleeves hanging cleanly and feet together",
            "one hand holding a scroll downward, the other hand visible at the sleeve opening",
            "formal remonstrance posture, one sleeve lifted as if about to speak",
        ))
        expression = _seed_pick(seed, "portrait_expression", (
            "austere moral severity", "clear-eyed remonstrance", "stern loyal grief",
            "unbending scholar calm", "quiet righteous anger",
        ))
    elif re.search(r"新科|科举|进士|庶吉士|主事|给事中|翰林|奏对|文书", text):
        pose = _seed_pick(seed, "portrait_pose", (
            "one hand raising a fresh examination scroll while the other shows ink-stained fingers",
            "half-step court entrance posture, sleeve lifted too eagerly, both boots visible",
            "one hand clutching a folded memorial to the chest, the other open in nervous argument",
            "slight over-formal bow with the robe hem pulled unevenly around both feet",
            "young official stance, one foot forward and one sleeve flaring as if answering a question",
            "hands visible around a thin book bundle, shoulders tense with new ambition",
        ))
        expression = _seed_pick(seed, "portrait_expression", (
            "eager scholarly intensity", "nervous new-official brightness", "earnest but untested gaze",
            "ambition held behind ritual restraint", "quick-thinking exam-hall alertness",
        ))
    elif re.search(r"待铨|举贤|在野|地方|游历|乡绅|塾师|幕客|入京", text):
        pose = _seed_pick(seed, "portrait_pose", (
            "one hand holding a recommendation letter, the other gripping a travel bundle",
            "road-worn arrival stance with one boot forward and sleeves uneven from travel",
            "three-quarter wary stance, one hand near a plain cloth pouch and the other fully visible",
            "standing as if just summoned from the road, shoulders turned and robe hem dusty",
            "one hand presenting a local account book while the other steadies a belt tassel",
            "measured outsider posture, head inclined but feet planted apart",
        ))
        expression = _seed_pick(seed, "portrait_expression", (
            "wary outsider focus", "practical local confidence", "humble but sharp-eyed composure",
            "watching the capital before committing", "weathered intelligence under restraint",
        ))
    else:
        pose = _seed_pick(seed, "portrait_pose", (
            "formal standing pose with subtle asymmetry, one sleeve lifted as if about to speak",
            "one hand holding a folded paper and the other resting by the belt",
            "three-quarter stance with robe hem visible around both boots",
            "hands loosely folded before the waist, one foot slightly forward",
            "standing in a measured court pose, sleeves balanced and both hands visible",
            "one hand cutting outward in restrained debate, the other visible beside a hanging sleeve",
            "slightly theatrical court stance with shoulders angled and robe folds sweeping sideways",
            "leaning forward a little as if about to answer, both hands clear of the sleeves",
        ))
        expression = _seed_pick(seed, "portrait_expression", (
            "reserved official gaze", "thoughtful restrained expression", "calm practical focus",
            "mildly worried eyes", "quiet confidence", "restless political curiosity",
            "dry skeptical half-smile", "sudden alertness",
        ))
    return f"{pose}; expression: {expression}; keep both complete hands with fingers visible and both feet visible and unobscured"


def _age_for_prompt(character: Character, state: Optional[GameState]) -> int:
    if character.birth_year and state is not None:
        return max(16, int(state.year) - int(character.birth_year))
    return 38


def _gender_for(character: Character) -> str:
    text = f"{character.name} {character.office} {character.office_type} {character.faction} {character.summary} {' '.join(character.personal_skills or [])}"
    if character.office_type == "后宫" or re.search(r"皇后|贵妃|妃|嫔|贵人|乳母|夫人|女侠|侠女|女将|红娘子|女子|女性", text):
        return "female"
    return "male"


def _origin_hint_for(character: Character) -> str:
    text = f"{character.name} {character.office} {character.location} {character.summary} {' '.join(character.personal_skills or [])}"
    patterns = [
        (r"后金|八旗|满洲|贝勒|汗|辽沈", "Jurchen / Manchu banner background: broader cheekbones possible, martial northern bearing, not Ming scholar styling"),
        (r"蒙古|察哈尔|喀尔喀|漠南|草原", "Mongolian steppe background: strong cheekbones, outdoor wind and sun, horseback nomad bearing"),
        (r"朝鲜", "Joseon Korean court background: East Asian but distinct Korean courtly facial styling, restrained expression"),
        (r"苗疆|蛊师", "southwestern Miao frontier background: sharper local ethnic styling, humid mountain climate, mysterious gaze"),
        (r"西洋|传教士|耶稣会|德国", "European Jesuit background: non-Han European facial structure, high nose bridge, deep-set eyes, tonsure or period missionary hair if visible"),
        (r"江南|常熟|松江|上海|南京|应天|秦淮|南直隶", "Jiangnan / Lower Yangtze cultural background: refined scholar bearing, humid southern climate, softer skin texture unless hardship says otherwise"),
        (r"蒲州|山西|大同|宣大", "Shanxi / northern frontier background: dry northern climate, leaner cheeks, wind-cut skin texture"),
        (r"陕西|陕北|延绥|凤阳|饥民|流寇", "northwest famine-and-frontier background: dust-weathered complexion, gaunt or hungry tension around cheeks and mouth"),
        (r"辽东|关宁|宁远|锦州|山海关|东江|关外", "Liaodong / cold frontier background: weathered face, firmer cheekbones, cold wind marks, soldierly hardness when appropriate"),
        (r"福建|海商|东海|水师|南洋|马尼拉", "maritime southeast background: sun-browned skin, sea wind texture, alert merchant or sailor eyes when appropriate"),
    ]
    for pattern, hint in patterns:
        if re.search(pattern, text):
            return hint
    return "late Ming Han Chinese background unless the character biography clearly says otherwise; use life experience and personality to individualize the face"


def _seed_pick(seed: str, key: str, choices: tuple[str, ...]) -> str:
    if not choices:
        return ""
    raw = hashlib.sha256(f"{seed}|{key}".encode("utf-8")).digest()
    return choices[int.from_bytes(raw[:4], "big") % len(choices)]


def _visual_variation_for(character: Character, dna_seed: str, wardrobe_key: str) -> str:
    """Deterministic art-direction entropy so generated portraits do not collapse."""
    role_text = f"{character.office or ''} {character.office_type or ''} {character.faction or ''} {character.style or ''} {character.summary or ''}"
    body = _seed_pick(dna_seed, "body_silhouette", (
        "tall narrow silhouette with long sleeves",
        "stockier grounded body with broad shoulders",
        "slight stoop from age or caution",
        "upright lean frame with visible neck and shoulders",
        "compact tense posture with asymmetrical sleeve fall",
        "weathered practical build, not heroic idealization",
    ))
    camera = _seed_pick(dna_seed, "camera_angle", (
        "front-facing with only a subtle three-quarter turn",
        "three-quarter left stance",
        "three-quarter right stance",
        "slightly lower court-portrait viewpoint",
        "slightly higher document-portrait viewpoint",
    ))
    prop_pool = (
        "folded memorial paper", "plain cloth pouch", "old travel bundle",
        "wooden tally slip", "unsealed letter", "simple belt tassel",
        "empty hands with clearly different sleeve rhythm",
    )
    if wardrobe_key.startswith("military") or wardrobe_key.startswith("jinyiwei") or re.search(r"武|将|兵|卫|刀", role_text):
        prop_pool = prop_pool + ("sheathed weapon held low", "helmet tucked under one arm", "command baton held at belt")
    elif wardrobe_key.startswith("eunuch"):
        prop_pool = prop_pool + ("small sealed imperial order", "palace tally", "folded yellow decree cloth")
    elif wardrobe_key == "commoner":
        prop_pool = ("plain cloth bundle", "coarse cloth pouch", "wooden travel staff held low", "folded old robe in one hand", "empty hands with humble open palms")
    accent = _seed_pick(dna_seed, "color_accent", (
        "one muted cinnabar accent", "one blue-green accent", "one old-gold belt accent",
        "ink-black dominant folds", "weathered grey-brown undertone",
        "desaturated russet lining", "deep indigo shadow accent",
    ))
    imperfection = _seed_pick(dna_seed, "portrait_imperfection", (
        "slightly uneven robe hem", "one sleeve folded higher than the other",
        "asymmetrical shoulder line", "subtle dust on boots",
        "small crease across the sash", "one visible personal mark echoed from the DNA sheet",
    ))
    return (
        "Internal randomized art-director brief generated from the identity seed: "
        f"{body}; {camera}; hand prop: {_seed_pick(dna_seed, 'hand_prop', prop_pool)}; "
        f"palette variation: {accent}; silhouette memory detail: {imperfection}. "
        "Do not reuse the same pose, sleeve shape, facial template, body build or hand gesture as another portrait."
    )


def _ability_face_marks(character: Character) -> str:
    marks: list[str] = []
    text = f"{character.office} {character.office_type} {character.faction} {character.style} {character.summary} {' '.join(character.personal_skills or [])}"
    if character.courage >= 78 or re.search(r"勇|悍|冲阵|死战|将|总兵|边镇|锦衣卫", text):
        marks.append("battle-or-duty tension: firmer jaw, intense eyes, possible small healed scar")
    if character.integrity >= 78 or re.search(r"清|廉|正|理学|气节|忠贞|守正", text):
        marks.append("austere moral severity: cleaner brow line, restrained mouth, less indulgent softness")
    if character.integrity <= 42 or re.search(r"权谋|贪|阴|狠|毒|自保|反复", text):
        marks.append("morally ambiguous edge: asymmetric smile, guarded eyes, subtle under-eye shadow")
    if character.ability >= 80 or character.wisdom >= 80:
        marks.append("intellectual sharpness: focused eyes, defined brow ridge, alert facial tension")
    if character.charm >= 75:
        marks.append("social magnetism: memorable eyes or mouth, expressive but not generic beauty")
    if is_eunuch_identity(character):
        marks.append("inner-court eunuch identity: clean shaven cheeks with no beard shadow, watchful palace-trained eyes, refined but not identical face")
    if character.office_type == "后宫":
        marks.append("court woman identity: feminine face, hairline suited to Ming palace styling, no masculine beard traits")
    return "; ".join(marks[:5]) or "balanced official face; avoid default average features by emphasizing the deterministic facial parameters"


def _personality_face_hint(character: Character) -> str:
    text = f"{character.style} {character.summary} {' '.join(character.personal_skills or [])}"
    hints: list[str] = []
    if re.search(r"老成|持重|谨慎|缜密|守正|沉稳", text):
        hints.append("restrained, observant expression; forehead and eye-corner lines if mature")
    if re.search(r"果敢|刚|勇|悍|冲锋|敢任|忠勇", text):
        hints.append("direct stare, square or tense jaw, heroic but not beautified")
    if re.search(r"圆滑|权谋|机变|深沉|自保|党争", text):
        hints.append("calculating gaze, slightly guarded eyelids, mouth corners that can read as political caution")
    if re.search(r"清|廉|理学|气节|文采|诗|翰林|士林", text):
        hints.append("scholarly austerity, refined but individualized features, ink-and-paper temperament")
    if re.search(r"阴|毒|莫测|冷峻|狠|厂卫", text):
        hints.append("sharper cheekbones or narrow eyes, colder expression, one memorable severe feature")
    if re.search(r"温婉|娇|妩|端庄|母仪|宠妃", text):
        hints.append("feminine courtly composure, distinct face shape and eyes, avoid same doll-like beauty")
    return "; ".join(hints[:4]) or "personality must be visible through face shape and gaze, not through costume"


def _dna_face_design(character: Character, state: Optional[GameState], dna_seed: str) -> str:
    gender = _gender_for(character)
    age = _age_for_prompt(character, state)
    role_text = f"{character.name} {character.office} {character.office_type} {character.faction} {character.summary} {' '.join(character.personal_skills or [])}"
    if age >= 66:
        age_marks = "elderly: deep nasolabial folds, forehead lines, thinner cheeks, age spots or sagging eyelids allowed"
    elif age >= 52:
        age_marks = "late middle age: visible forehead and eye-corner lines, heavier eyelids, mature facial weight"
    elif age >= 36:
        age_marks = "mature adult: defined cheeks and jaw, subtle eye lines, not youthful"
    elif age >= 24:
        age_marks = "young adult: smoother skin but still individualized bone structure"
    else:
        age_marks = "youthful: softer cheeks and less facial weathering"

    if gender == "female":
        if re.search(r"女将|女侠|红娘子|武|刀|马", role_text):
            face_shapes = (
                "athletic angular female face", "lean warrior face with high cheekbones",
                "sun-browned oval face with firm jaw", "sharp-eyed heroine face",
                "long resilient face", "wide-cheeked frontier heroine face",
            )
            female_identity_line = "female martial identity: not delicate court glamour, visible outdoor toughness and trained alertness"
        elif re.search(r"皇后|中宫", role_text):
            face_shapes = (
                "broad dignified empress face", "oval matriarchal court face with strong chin",
                "long solemn face with high forehead", "rounder authoritative noble face",
                "wide cheekbones with restrained mouth",
            )
            female_identity_line = "empress identity: maternal authority and court discipline, beautiful only if the seed says so, never a generic idol face"
        elif re.search(r"贵妃|宠妃|妃|嫔|贵人", role_text):
            face_shapes = (
                "heart-shaped court beauty face with one memorable flaw", "long elegant face with proud chin",
                "round youthful palace face with distinct eyes", "slender angular favored-consort face",
                "small sharp face with knowing mouth corners", "soft oval face with asymmetric eyelids",
            )
            female_identity_line = "favored consort identity: charisma and court survival, seductive period expression without modern glamour-template features"
        else:
            face_shapes = (
                "oval face with high cheekbones", "rounder courtly face with distinct chin",
                "long elegant face", "heart-shaped face", "slender angular face",
                "wide-cheeked practical female face",
            )
            female_identity_line = "female identity: individual biography first, beauty secondary, avoid the same doll-like court face"
        beard_line = "female face; no beard, no moustache, no masculine beard shadow"
        eye_choices = (
            "long narrow phoenix eyes", "round youthful eyes with guarded focus", "sleepy heavy-lidded court eyes",
            "sharp upturned warrior eyes", "slightly uneven eyelids", "large sorrowful eyes",
            "small calculating eyes beneath soft brows", "bright almond eyes with one asymmetry",
        )
        brow_choices = (
            "thin willow brows", "straight firm brows", "soft arched brows", "slightly uneven brows",
            "short tense brows", "low cautious brows", "fine high palace brows",
        )
        nose_choices = (
            "delicate high nose bridge", "short rounded nose", "straight narrow nose",
            "slightly broad nose with natural realism", "small nose with sharp bridge", "subtly crooked nose",
        )
        mouth_choices = (
            "small restrained mouth", "thin proud lips", "fuller lips with stern set",
            "downturned mouth corners", "faint asymmetric smile line", "wide firm mouth",
            "soft mouth with guarded corners",
        )
        skin_choices = (
            "pale palace-kept complexion", "warm southern complexion", "sun-browned outdoor skin",
            "sallow tired court complexion", "smooth youthful skin with distinct bone structure",
            "natural pockmarked or freckled skin texture", "ruddy northern complexion",
        )
    else:
        if re.search(r"流寇|驿卒|饥民|边兵|乱|曹操|闯王", role_text):
            face_shapes = ("gaunt rebel face", "dust-weathered narrow face", "hard hungry face with hollow cheeks", "rough frontier face", "sharp jawed outlaw face")
        elif re.search(r"总兵|副将|游击|参将|守备|边镇|将军|锦衣卫|武|刀客|骑射|护军|固山", role_text):
            face_shapes = ("square jaw and broad chin", "lean weathered frontier face", "high cheekbones and hard jaw", "scar-ready soldier face", "broad cheeked martial face")
        elif re.search(r"太监|内官|司礼监|东厂|内廷", role_text):
            face_shapes = ("smooth narrow palace face", "high cheekbones with narrow chin", "rounder eunuch face with watchful eyes", "fine-boned inner-court face", "long guarded face")
        elif re.search(r"后金|八旗|满洲|贝勒|蒙古|察哈尔|汗|草原", role_text):
            face_shapes = ("broad northern face with strong cheekbones", "square steppe-warrior face", "wide cheekbones and firm jaw", "rugged banner noble face", "lean cold-frontier face")
        elif re.search(r"朝鲜", role_text):
            face_shapes = ("restrained court scholar face", "oval Joseon official face", "narrow refined face", "soft but worried court face")
        elif re.search(r"西洋|传教士|耶稣会|德国", role_text):
            face_shapes = ("long European face", "high-nosed Jesuit face", "deep-set eyed European scholar face", "narrow ascetic missionary face")
        elif re.search(r"翰林|礼部|内阁|尚书|侍郎|御史|给事中|编修|诗|士林|理学|文", role_text):
            face_shapes = ("long narrow scholar face", "broad forehead and fine jaw", "austere official face", "thin cheeked literati face", "rounder heavy official face")
        else:
            face_shapes = ("long narrow official face", "square jaw and broad chin", "gaunt cheeked face", "rounder heavy official face", "high cheekbones with narrow chin", "broad forehead and firm jaw", "lean weathered face")
        beard_line = "no beard and no moustache on DNA sheet, but keep masculine skull and hairline if male"
        female_identity_line = ""
        eye_choices = (
            "narrow observant eyes", "large tired eyes", "deep-set calculating eyes", "sharp upturned eyes",
            "soft but watchful eyes", "heavy-lidded eyes", "bright restless eyes",
        )
        brow_choices = (
            "straight thick brows", "thin arched brows", "broken uneven brows", "heavy downward brows",
            "clean scholar brows", "short tense brows",
        )
        nose_choices = (
            "high straight nose bridge", "broad nose with rounded tip", "hooked or aquiline nose", "short blunt nose",
            "narrow long nose", "slightly crooked nose",
        )
        mouth_choices = (
            "thin compressed lips", "wide firm mouth", "small restrained mouth", "downturned mouth corners",
            "faint asymmetric smile line", "fuller lips with stern set",
        )
        skin_choices = (
            "pale indoor scholar complexion", "yellow-brown outdoor complexion", "ruddy northern complexion",
            "sallow tired complexion", "sun-darkened skin", "smooth palace-kept skin", "pockmarked or rough skin texture",
        )
    if age < 32:
        mark_choices = (
            "small mole near one cheek", "subtle scar near eyebrow", "uneven left-right eyelids",
            "slight cheek hollow", "one ear more protruding", "distinct widow's peak hairline",
            "no obvious mark, but very distinct skull silhouette",
        )
    elif age < 52:
        mark_choices = (
            "small mole near one cheek", "subtle scar near eyebrow", "uneven left-right eyelids", "noticeable under-eye bags",
            "slight cheek hollow", "one ear more protruding", "distinct widow's peak hairline",
            "no obvious mark, but very distinct skull silhouette",
        )
    else:
        mark_choices = (
            "small mole near one cheek", "subtle scar near eyebrow", "uneven left-right eyelids", "noticeable under-eye bags",
            "deep nasolabial folds", "slight cheek hollow", "one ear more protruding", "distinct widow's peak hairline",
            "age spot near temple", "no obvious mark, but very distinct skull silhouette",
        )
    if gender == "female":
        mark_choices = mark_choices + (
            "small beauty mark below one eye", "mole near one mouth corner", "one cheek dimple",
            "slightly asymmetric face width", "distinct rounded forehead", "sharp cupid-bow mouth",
        )
    features = {
        "face_shape": _seed_pick(dna_seed, "face_shape", face_shapes),
        "eyes": _seed_pick(dna_seed, "eyes", eye_choices),
        "brows": _seed_pick(dna_seed, "brows", brow_choices),
        "nose": _seed_pick(dna_seed, "nose", nose_choices),
        "mouth": _seed_pick(dna_seed, "mouth", mouth_choices),
        "skin": _seed_pick(dna_seed, "skin", skin_choices),
        "mark": _seed_pick(dna_seed, "mark", mark_choices),
    }
    return (
        f"DNA identity parameters: gender={gender}; apparent age={age}; {age_marks}. "
        f"Origin/life imprint: {_origin_hint_for(character)}. "
        f"Personality face logic: {_personality_face_hint(character)}. "
        f"Experience/stat face marks: {_ability_face_marks(character)}. "
        f"{female_identity_line + '. ' if female_identity_line else ''}"
        f"Deterministic facial design from seed {dna_seed}: {features['face_shape']}; {features['eyes']}; "
        f"{features['brows']}; {features['nose']}; {features['mouth']}; {features['skin']}; signature memory point: {features['mark']}. "
        f"{beard_line}. The face must be memorable and recognizably different from every other character, not a generic average Han face."
    )


def build_portrait_spec(character: Character, state: Optional[GameState], campaign_id: str = "") -> PortraitSpec:
    dna_seed = stable_dna_seed(character)
    wardrobe_key, wardrobe_label = wardrobe_for(character)
    must_clean = is_eunuch_identity(character) or is_former_eunuch_commoner(character)
    age_hint = ""
    dna_age_hint = ""
    age = _age_for_prompt(character, state)
    gender = _gender_for(character)
    if character.birth_year and state is not None:
        age_hint = f" apparent age about {age},"
        dna_age_hint = f" apparent age about {age},"
    if gender == "female":
        beard_rule = "female face, absolutely no moustache or beard, no masculine beard shadow"
    elif must_clean:
        beard_rule = "clean shaven, absolutely no moustache, no beard, smooth face from former or current eunuch identity"
    elif age < 28:
        beard_rule = "youthful male face, no beard or at most very faint early moustache, no mature official beard"
    else:
        beard_rule = "historically plausible Ming official facial hair, moustache and short beard if mature"
    dna_prompt = (
        f"Create a facial identity reference sheet for {character.name}. "
        f"Internal invisible identity code: {dna_seed}; use it only to choose facial features, never write the code in the image. "
        f"Biography and identity source: office/status={character.office or character.office_type}; faction={character.faction}; "
        f"personality={character.style}; traits={', '.join(character.personal_skills or [])}; biography={character.summary}. "
        f"{_dna_face_design(character, state, dna_seed)} "
        "Create one clean 3:4 image containing a 2x2 head-angle model sheet: "
        "top left strict front view, top right three-quarter right view, bottom left three-quarter left view, "
        "bottom right strict side profile. Same head, same skull shape, same eyes, nose, mouth, ears and jaw in all four panels. "
        "Unified visual style: realistic painterly late-Ming game concept art, anatomical head model sheet, subdued oil-paint texture, "
        "plain light background with thin panel dividers; not anime, not manga, not 3D render. "
        "Do not use or request any visual reference image for this DNA sheet; build the four-angle layout from this text specification only. "
        "Do not imitate a shared template face, celebrity face, mannequin face, product photo, or generic AI-beauty face. "
        "Absolutely no title, no labels, no captions, no angle names, no character name, no seed text, no letters, no numbers, no watermark. "
        f"Neutral expression,{dna_age_hint} bare neck and shoulders or plain collar only, no costume focus, no official hat, no text, no watermark. "
        "Use the character's inferred gender, culture, origin and life experience for the face. "
        "For Ming Han male identity use hair tied into a simple Ming-style topknot or tight hairline reference, not a Qing queue. "
        "For women use a simple late-Ming hairline reference without ornate palace costume. No beard and no moustache on the DNA sheet."
    )
    refs = reference_images_for_wardrobe(wardrobe_key)
    variation = _visual_variation_for(character, dna_seed, wardrobe_key)
    ref_hint = (
        "Use the provided clothing reference image(s) for robe color, collar, sleeve width, hat silhouette, belt and textile construction; "
        "do not copy mannequin faces, studio background, labels, numbers, borders or product-photo layout. "
        if refs else ""
    )
    if wardrobe_key.startswith("civil"):
        wardrobe_guard = (
            "Civil official wardrobe guard: absolutely no armor, no military helmet, no sword, no weapon, no battlefield cloak; "
            "wear a Ming civil round-collar official robe, winged black official hat, rank patch and court boots. "
        )
    elif wardrobe_key.startswith("eunuch"):
        wardrobe_guard = (
            "Inner-court eunuch wardrobe guard: no beard, no moustache, no military armor unless the office explicitly says commander; "
            "use palace eunuch hat, robe, belt and restrained palace posture. "
        )
    elif wardrobe_key.startswith("consort"):
        wardrobe_guard = (
            "Palace woman wardrobe guard: no armor, no weapon, no official male hat, no beard; use court hair ornaments, phoenix crown or palace robe as appropriate. "
        )
    elif wardrobe_key.startswith("military") or wardrobe_key.startswith("jinyiwei"):
        wardrobe_guard = "Military/security wardrobe guard: armor, weapon, boots and command posture are allowed, but keep the full figure readable. "
    else:
        wardrobe_guard = "Identity wardrobe guard: clothing must follow the stated office, faction, culture and social role, not a generic warrior. "
    prompt = (
        "Late Ming dynasty political strategy game character portrait, dark Romance of the Three Kingdoms style oil painting, "
        "STRICT 2:3 vertical full-body standing cutout, head-to-toe entire figure visible including hat, sleeves, hands, robe hem and boots, "
        "transparent alpha background if the image model supports it; if alpha is unsupported, render on a perfectly flat solid chroma-key magenta (#ff00ff) background for local transparent cutout, no white or paper background, no backdrop, no frame, no text, no watermark, "
        "do not crop at head, hands, waist, knees or feet. "
        "Both complete hands with fingers and both complete feet/boots must be fully visible, not hidden by sleeves, robe hem, cloak or frame. "
        "Never draw a checkerboard transparency pattern, floor plane, cast shadow, contact shadow, gradient, texture or scenery. "
        f"Character: {character.name}.{age_hint} Office/status: {character.office or character.office_type}. "
        f"Faction: {character.faction}. Personality: {character.style}. Biography clue: {character.summary}. "
        f"DNA seed: {dna_seed}; keep the same face identity as the DNA reference. "
        f"Face rule: {beard_rule}. Wardrobe: {wardrobe_label}. "
        f"{wardrobe_guard}"
        f"{ref_hint}"
        f"{variation} "
        f"Pose: {_pose_for(character)}. "
        "Historically grounded late Ming fabric texture, heavy shadows, muted cinnabar, ink black, old gold, blue-green robe colors, "
        "painterly realism, sharp silhouette, centered full figure with safe transparent margins around crown, sleeves and feet, minimum width 512 pixels."
    )
    signature = "|".join([
        "portrait-v4-diverse-chroma-cutout",
        campaign_id,
        character.name,
        dna_seed,
        wardrobe_key,
        wardrobe_label,
        character.office or "",
        character.office_type or "",
        "clean" if must_clean else "beard-ok",
    ])
    asset_id = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:24]
    dna_asset_id = hashlib.sha1(f"dna-v2-distinct-face|{campaign_id}|{character.name}|{dna_seed}|dna-sheet".encode("utf-8")).hexdigest()[:24]
    return PortraitSpec(
        dna_seed=dna_seed,
        asset_id=asset_id,
        dna_asset_id=dna_asset_id,
        wardrobe_key=wardrobe_key,
        wardrobe_label=wardrobe_label,
        prompt=prompt,
        dna_prompt=dna_prompt,
        must_be_clean_shaven=must_clean,
        reference_images=refs,
        dna_reference_images=dna_reference_images(),
    )


def _find_base64(value: Any) -> Optional[str]:
    if isinstance(value, str):
        if value.startswith("data:image"):
            return value.split(",", 1)[1]
        if len(value) > 200 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", value):
            return value
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_base64(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("b64_json", "base64", "image_base64", "image", "data"):
            found = _find_base64(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _find_base64(item)
            if found:
                return found
    return None


def _find_url(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, list):
        for item in value:
            found = _find_url(item)
            if found:
                return found
    if isinstance(value, dict):
        for item in value.values():
            found = _find_url(item)
            if found:
                return found
    return None


def _provider_base_url() -> str:
    return os.environ.get("NANO_BANANA_BASE_URL", NANO_BANANA_DEFAULT_BASE_URL).rstrip("/")


def _rewrite_302_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if not host:
        return url
    base_host = urllib.parse.urlparse(_provider_base_url()).netloc
    if host == "api.302.ai" and base_host:
        return urllib.parse.urlunparse(parsed._replace(netloc=base_host))
    if host == "file.302.ai":
        override = os.environ.get("NANO_BANANA_FILE_BASE_URL", "").strip().rstrip("/")
        if override:
            override_parsed = urllib.parse.urlparse(override if "://" in override else f"https://{override}")
            if override_parsed.netloc:
                return urllib.parse.urlunparse(
                    parsed._replace(
                        scheme=override_parsed.scheme or parsed.scheme,
                        netloc=override_parsed.netloc,
                    )
                )
        if base_host.lower().endswith("302ai.cn"):
            return urllib.parse.urlunparse(parsed._replace(netloc="file.302ai.cn"))
    return url


def _payload_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {}


def _payload_status(payload: Dict[str, Any]) -> str:
    data = _payload_data(payload)
    value = (data.get("status") or payload.get("status")) if isinstance(payload, dict) else ""
    return str(value or "").strip().lower()


def _payload_error_detail(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return str(payload)[:300]
    data = _payload_data(payload)
    for container in (data, payload):
        for key in ("error", "message", "msg", "detail"):
            value = container.get(key)
            if value:
                return str(value)[:500]
    return str(payload)[:500]


def _payload_result_url(payload: Dict[str, Any]) -> Optional[str]:
    data = _payload_data(payload)
    for container in (data, payload):
        urls = container.get("urls") if isinstance(container, dict) else None
        if isinstance(urls, dict):
            for key in ("get", "result"):
                url = urls.get(key)
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    return url
    return None


def _is_302_result_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    return host in {"api.302.ai", "api.302ai.cn"} and "/predictions/" in path and path.endswith("/result")


def _looks_like_image_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if _is_302_result_url(url):
        return False
    if host.startswith("file.302"):
        return True
    return path.endswith((".png", ".jpg", ".jpeg", ".webp"))


def _find_image_url(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value if _looks_like_image_url(value) else None
    if isinstance(value, list):
        for item in value:
            found = _find_image_url(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("outputs", "output", "images", "image_urls", "image_url", "url", "result", "results"):
            found = _find_image_url(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _find_image_url(item)
            if found:
                return found
    return None


def _download_image_url(url: str, timeout_s: int) -> bytes:
    rewritten_url = _rewrite_302_url(url)
    img_req = urllib.request.Request(
        rewritten_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://302.ai/",
        },
    )
    with urllib.request.urlopen(img_req, timeout=timeout_s) as img_resp:
        return img_resp.read()


def _read_302_result(result_url: str, key: str, timeout_s: int) -> Dict[str, Any]:
    req = urllib.request.Request(
        _rewrite_302_url(result_url),
        method="GET",
        headers={
            "Authorization": f"Bearer {key}",
            "User-Agent": "Ming-Salvage-Sim/portrait-pipeline",
        },
    )
    return _read_json_response(req, timeout_s)


def _poll_302_result(result_url: str, key: str, timeout_s: int) -> Dict[str, Any]:
    deadline = time.monotonic() + max(1, timeout_s)
    interval = max(0.5, float(os.environ.get("NANO_BANANA_POLL_INTERVAL_SECONDS", "3") or 3))
    last_payload: Dict[str, Any] = {}
    terminal_ok = {"succeeded", "success", "completed", "complete", "ready", "finished"}
    terminal_bad = {"failed", "failure", "error", "cancelled", "canceled"}
    while time.monotonic() < deadline:
        remaining = max(1, min(30, int(deadline - time.monotonic())))
        last_payload = _read_302_result(result_url, key, remaining)
        status = _payload_status(last_payload)
        if _find_base64(last_payload) or _find_image_url(last_payload):
            return last_payload
        if status in terminal_ok:
            return last_payload
        if status in terminal_bad:
            raise RuntimeError(f"nano banana 任务失败：{_payload_error_detail(last_payload)}")
        time.sleep(min(interval, max(0.1, deadline - time.monotonic())))
    raise RuntimeError(f"nano banana 任务超时：{_payload_error_detail(last_payload)}")


def _image_bytes_from_provider_payload(payload: Dict[str, Any], *, key: str, timeout_s: int, allow_poll: bool = True) -> bytes:
    b64 = _find_base64(payload)
    if b64:
        return base64.b64decode(re.sub(r"\s+", "", b64))
    url = _find_image_url(payload)
    if url:
        return _download_image_url(url, timeout_s)
    result_url = _payload_result_url(payload)
    if allow_poll and result_url:
        result_payload = _poll_302_result(result_url, key, timeout_s)
        return _image_bytes_from_provider_payload(result_payload, key=key, timeout_s=timeout_s, allow_poll=False)
    raise RuntimeError(f"nano banana 未返回图片数据：{str(payload)[:500]}")


def detect_image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _reference_image_inline(path: str, *, max_width: int = 960) -> Dict[str, str]:
    """Read a local reference image as Gemini inline_data.

    The provider receives the image; the project never writes a copy into the
    repo. Large local references are resized in memory so requests stay sane.
    """
    if path.startswith("data:image"):
        header, payload = path.split(",", 1)
        mime_match = re.search(r"data:([^;]+);base64", header)
        return {"mime_type": mime_match.group(1) if mime_match else "image/png", "data": payload}
    if path.startswith(("http://", "https://")):
        req = urllib.request.Request(path, headers={"User-Agent": "Ming-Salvage-Sim/portrait-pipeline"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
        mime = detect_image_mime(raw)
        return {"mime_type": mime, "data": base64.b64encode(raw).decode("ascii")}
    raw = Path(path).read_bytes()
    try:
        from PIL import Image

        image = Image.open(BytesIO(raw)).convert("RGB")
        if image.width > max_width:
            ratio = max_width / max(1, image.width)
            image = image.resize((max_width, max(1, int(image.height * ratio))), Image.Resampling.LANCZOS)
        out = BytesIO()
        image.save(out, format="JPEG", quality=88, optimize=True)
        raw = out.getvalue()
        mime = "image/jpeg"
    except Exception:
        mime = detect_image_mime(raw)
    return {"mime_type": mime, "data": base64.b64encode(raw).decode("ascii")}


def image_data_url(data: bytes, mime_type: Optional[str] = None) -> str:
    mime = mime_type or detect_image_mime(data)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _reference_image_data_url(path: str, *, max_width: int = 960) -> str:
    item = _reference_image_inline(path, max_width=max_width)
    return f"data:{item['mime_type']};base64,{item['data']}"


def _read_json_response(req: urllib.request.Request, timeout_s: int) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        detail = f"HTTP {exc.code} {exc.reason}"
        if body:
            detail += f": {body[:500]}"
        raise RuntimeError(detail) from exc


def _nano_banana_generate_with_references(
    prompt: str,
    reference_images: tuple[str, ...],
    *,
    timeout_s: int,
    aspect_ratio: str,
) -> bytes:
    key = os.environ.get("NANO_BANANA_API_KEY", "").strip() or os.environ.get("OPENAI_IMAGE_KEY", "").strip()
    if not key:
        raise RuntimeError("缺 NANO_BANANA_API_KEY 环境变量。")
    base_url = _provider_base_url()
    endpoint = os.environ.get("NANO_BANANA_ORIGINAL_ENDPOINT", NANO_BANANA_DEFAULT_ORIGINAL_ENDPOINT)
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    parts: list[Dict[str, Any]] = [{"text": prompt}]
    for ref in reference_images:
        try:
            parts.append({"inline_data": _reference_image_inline(ref)})
        except Exception as exc:
            raise RuntimeError(f"读取参考图失败：{ref}：{exc}") from exc
    body = json.dumps({
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": aspect_ratio},
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}{endpoint}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "Ming-Salvage-Sim/portrait-pipeline",
            "X-Client-Request-Id": hashlib.md5(("ref|" + prompt).encode("utf-8")).hexdigest(),
        },
    )
    try:
        payload = _read_json_response(req, timeout_s)
    except Exception as original_error:
        edit_endpoint = os.environ.get("NANO_BANANA_EDIT_ENDPOINT", NANO_BANANA_DEFAULT_EDIT_ENDPOINT)
        if not edit_endpoint.startswith("/"):
            edit_endpoint = "/" + edit_endpoint
        images = []
        for ref in reference_images:
            try:
                images.append(_reference_image_data_url(ref))
            except Exception as exc:
                raise RuntimeError(f"读取参考图失败：{ref}：{exc}") from exc
        edit_body = json.dumps({
            "prompt": prompt,
            "images": images,
            "aspect_ratio": aspect_ratio,
            "output_format": "png",
            "resolution": os.environ.get("NANO_BANANA_RESOLUTION", "1k"),
            "enable_base64_output": True,
            "enable_sync_mode": True,
        }).encode("utf-8")
        edit_req = urllib.request.Request(
            f"{base_url}{edit_endpoint}",
            data=edit_body,
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "Ming-Salvage-Sim/portrait-pipeline",
                "X-Client-Request-Id": hashlib.md5(("edit|" + prompt).encode("utf-8")).hexdigest(),
            },
        )
        try:
            payload = _read_json_response(edit_req, timeout_s)
        except Exception as edit_error:
            raise RuntimeError(f"参考图生成失败：original={original_error}; edit={edit_error}") from edit_error
    try:
        return _image_bytes_from_provider_payload(payload, key=key, timeout_s=timeout_s)
    except Exception as exc:
        raise RuntimeError(f"nano banana 参考图生成未返回图片数据：{exc}") from exc


def _parse_aspect_ratio(value: Optional[str]) -> Optional[tuple[int, int]]:
    if not value:
        return None
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", value)
    if not match:
        return None
    return max(1, int(match.group(1))), max(1, int(match.group(2)))


def _opaque_bbox_with_margin(image: Any, *, threshold: int = 18, margin_ratio: float = 0.055) -> Optional[tuple[int, int, int, int]]:
    try:
        alpha = image.getchannel("A")
        mask = alpha.point(lambda value: 255 if int(value) > threshold else 0)
        bbox = mask.getbbox()
        if not bbox:
            return None
        left, top, right, bottom = bbox
        width, height = image.size
        margin = max(8, int(max(right - left, bottom - top) * margin_ratio))
        return (
            max(0, left - margin),
            max(0, top - margin),
            min(width, right + margin),
            min(height, bottom + margin),
        )
    except Exception:
        return None


def _opaque_bbox(image: Any, *, threshold: int = 18) -> Optional[tuple[int, int, int, int]]:
    try:
        alpha = image.getchannel("A")
        mask = alpha.point(lambda value: 255 if int(value) > threshold else 0)
        return mask.getbbox()
    except Exception:
        return None


def _rgb_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((int(a[i]) - int(b[i])) ** 2 for i in range(3)) ** 0.5


def _median_rgb(samples: list[tuple[int, int, int, int]]) -> tuple[int, int, int]:
    if not samples:
        return (0, 0, 0)
    channels = []
    for idx in range(3):
        values = sorted(int(px[idx]) for px in samples)
        channels.append(values[len(values) // 2])
    return (channels[0], channels[1], channels[2])


def _perimeter_samples(image: Any, bbox: Optional[tuple[int, int, int, int]] = None) -> list[tuple[int, int, int, int]]:
    pixels = image.load()
    width, height = image.size
    if bbox is None:
        left, top, right, bottom = 0, 0, width, height
    else:
        left, top, right, bottom = bbox
        left = max(0, min(width - 1, left))
        top = max(0, min(height - 1, top))
        right = max(left + 1, min(width, right))
        bottom = max(top + 1, min(height, bottom))
    step = max(1, min(right - left, bottom - top) // 80)
    coords: list[tuple[int, int]] = []
    for x in range(left, right, step):
        coords.append((x, top))
        coords.append((x, bottom - 1))
    for y in range(top, bottom, step):
        coords.append((left, y))
        coords.append((right - 1, y))
    seen: set[tuple[int, int]] = set()
    samples: list[tuple[int, int, int, int]] = []
    for x, y in coords:
        if (x, y) in seen:
            continue
        seen.add((x, y))
        px = pixels[x, y]
        if px[3] >= 8:
            samples.append(px)
    return samples


def _background_profile(samples: list[tuple[int, int, int, int]]) -> tuple[tuple[int, int, int], bool, bool, bool, int, float]:
    bg = _median_rgb(samples)
    spread = max(bg) - min(bg)
    luminance = (bg[0] + bg[1] + bg[2]) / 3
    bg_is_white = min(bg) > 218 and spread < 45
    bg_is_light_neutral = luminance > 184 and min(bg) > 155 and spread < 82
    bg_is_chroma = bg[0] > 150 and bg[2] > 150 and bg[1] < 145 and abs(bg[0] - bg[2]) < 120
    threshold = 96 if bg_is_chroma else 88 if bg_is_light_neutral else 52
    if os.environ.get("PORTRAIT_BG_FLOOD_THRESHOLD"):
        try:
            threshold = max(8, int(os.environ["PORTRAIT_BG_FLOOD_THRESHOLD"]))
        except ValueError:
            pass
    close_count = 0
    for r, g, b, _a in samples:
        if _is_background_rgb((r, g, b), bg, bg_is_white, bg_is_light_neutral, bg_is_chroma, threshold):
            close_count += 1
    consistency = close_count / max(1, len(samples))
    return bg, bg_is_white, bg_is_light_neutral, bg_is_chroma, threshold, consistency


def _is_background_rgb(
    rgb: tuple[int, int, int],
    bg: tuple[int, int, int],
    bg_is_white: bool,
    bg_is_light_neutral: bool,
    bg_is_chroma: bool,
    threshold: int,
) -> bool:
    if _rgb_distance(rgb, bg) < threshold:
        return True
    spread = max(rgb) - min(rgb)
    if bg_is_white and min(rgb) > 216 and spread < 62:
        return True
    if bg_is_light_neutral and (sum(rgb) / 3) > 184 and min(rgb) > 150 and spread < 92:
        return True
    if bg_is_chroma and rgb[0] > 120 and rgb[2] > 120 and min(rgb[0], rgb[2]) - rgb[1] > 42:
        return True
    return False


def _flood_background_from_seeds(
    image: Any,
    seeds: Iterator[tuple[int, int]],
    bg: tuple[int, int, int],
    bg_is_white: bool,
    bg_is_light_neutral: bool,
    bg_is_chroma: bool,
    threshold: int,
) -> int:
    pixels = image.load()
    width, height = image.size
    removed = 0
    seen: set[tuple[int, int]] = set()
    queue: deque[tuple[int, int]] = deque(seeds)
    while queue:
        x, y = queue.popleft()
        if (x, y) in seen or x < 0 or y < 0 or x >= width or y >= height:
            continue
        seen.add((x, y))
        r, g, b, a = pixels[x, y]
        if a < 8 or _is_background_rgb((r, g, b), bg, bg_is_white, bg_is_light_neutral, bg_is_chroma, threshold):
            if a >= 8:
                removed += 1
            pixels[x, y] = (r, g, b, 0)
            queue.append((x + 1, y))
            queue.append((x - 1, y))
            queue.append((x, y + 1))
            queue.append((x, y - 1))
    return removed


def _edge_seeds(width: int, height: int) -> Iterator[tuple[int, int]]:
    for x in range(width):
        yield (x, 0)
        yield (x, height - 1)
    for y in range(height):
        yield (0, y)
        yield (width - 1, y)


def _bbox_perimeter_seeds(bbox: tuple[int, int, int, int]) -> Iterator[tuple[int, int]]:
    left, top, right, bottom = bbox
    for x in range(left, right):
        yield (x, top)
        yield (x, bottom - 1)
    for y in range(top, bottom):
        yield (left, y)
        yield (right - 1, y)


def _cutout_connected_background(image: Any) -> tuple[tuple[int, int, int], bool, bool, int]:
    width, height = image.size
    samples = _perimeter_samples(image)
    if samples:
        bg, bg_is_white, bg_is_light_neutral, bg_is_chroma, threshold, _consistency = _background_profile(samples)
    else:
        bg, bg_is_white, bg_is_light_neutral, bg_is_chroma, threshold = (0, 0, 0), False, False, False, 52
    removed = _flood_background_from_seeds(
        image,
        _edge_seeds(width, height),
        bg,
        bg_is_white,
        bg_is_light_neutral,
        bg_is_chroma,
        threshold,
    )

    # If the provider returns a white/paper/chroma mat inside an already
    # transparent canvas, the outer edge has no opaque background to sample.
    # Sample the current opaque bbox perimeter and flood that inset mat too.
    bbox = _opaque_bbox(image)
    if bbox is not None:
        inset_samples = _perimeter_samples(image, bbox)
        if inset_samples:
            inset_bg, inset_white, inset_light, inset_chroma, inset_threshold, consistency = _background_profile(inset_samples)
            blank_like = inset_white or inset_light or inset_chroma
            if blank_like and consistency > 0.36:
                removed += _flood_background_from_seeds(
                    image,
                    _bbox_perimeter_seeds(bbox),
                    inset_bg,
                    inset_white,
                    inset_light,
                    inset_chroma,
                    inset_threshold,
                )
                bg, bg_is_white, bg_is_chroma = inset_bg, inset_white, inset_chroma
    return bg, bg_is_chroma, bg_is_white, removed


def normalize_portrait_png(
    data: bytes,
    *,
    target_width: int = 512,
    target_aspect_ratio: Optional[str] = None,
    cutout_background: bool = True,
    use_rembg: bool = True,
) -> bytes:
    """Convert provider output to compact PNG, optional cutout, and fixed canvas.

    nano-banana may return JPEG even when ``output_format=png`` is requested.
    This post-process keeps save files small and gives the UI a transparent-ish
    cutout when the model returns a simple studio/background field. When an
    aspect ratio is provided, the image is padded, not cropped.
    """
    try:
        from PIL import Image
    except Exception:
        return data
    try:
        if cutout_background and use_rembg and os.environ.get("PORTRAIT_DISABLE_REMBG", "").strip() not in {"1", "true", "TRUE"}:
            try:
                from rembg import remove  # type: ignore

                data = remove(data)
            except Exception:
                pass
        image = Image.open(BytesIO(data)).convert("RGBA")
        if image.width != target_width:
            ratio = target_width / max(1, image.width)
            image = image.resize((target_width, max(1, int(image.height * ratio))), Image.Resampling.LANCZOS)
        if cutout_background:
            bg, bg_is_chroma, bg_is_white, removed_chroma_spill = _cutout_connected_background(image)
            pixels = image.load()
            width, height = image.size
            if bg_is_chroma:
                alpha_snapshot = image.getchannel("A")

                def touches_transparent(px: int, py: int) -> bool:
                    for nx in (px - 1, px, px + 1):
                        for ny in (py - 1, py, py + 1):
                            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                                return True
                            if alpha_snapshot.getpixel((nx, ny)) < 12:
                                return True
                    return False

                for y in range(height):
                    for x in range(width):
                        r, g, b, a = pixels[x, y]
                        if a < 8:
                            continue
                        dist = sum((int((r, g, b)[i]) - int(bg[i])) ** 2 for i in range(3)) ** 0.5
                        chroma_spill = r > 120 and b > 120 and min(r, b) - g > 55
                        if dist < 92 or (chroma_spill and touches_transparent(x, y)):
                            pixels[x, y] = (r, g, b, 0)
                            removed_chroma_spill += 1
            alpha_snapshot = image.getchannel("A")

            def edge_touches_transparent(px: int, py: int) -> bool:
                for nx in (px - 1, px, px + 1):
                    for ny in (py - 1, py, py + 1):
                        if nx < 0 or ny < 0 or nx >= width or ny >= height:
                            return True
                        if alpha_snapshot.getpixel((nx, ny)) < 12:
                            return True
                return False

            for y in range(height):
                for x in range(width):
                    r, g, b, a = pixels[x, y]
                    if a < 8:
                        continue
                    magenta_edge = r > 105 and b > 105 and min(r, b) - g > 38 and abs(r - b) < 90
                    white_edge = bg_is_white and min(r, g, b) > 216 and (max(r, g, b) - min(r, g, b)) < 58
                    if (magenta_edge or white_edge) and (a < 245 or edge_touches_transparent(x, y)):
                        pixels[x, y] = (r, g, b, 0)
                        removed_chroma_spill += 1
            default_contract = "1" if bg_is_chroma or bg_is_white or removed_chroma_spill > 25 else "0"
            edge_contract = int(os.environ.get("PORTRAIT_ALPHA_EDGE_CONTRACT", default_contract) or default_contract)
            if edge_contract > 0:
                try:
                    from PIL import ImageFilter

                    alpha = image.getchannel("A")
                    for _ in range(edge_contract):
                        alpha = alpha.filter(ImageFilter.MinFilter(3))
                    image.putalpha(alpha)
                    pixels = image.load()
                except Exception:
                    pass
            feather_radius = float(os.environ.get("PORTRAIT_ALPHA_FEATHER_RADIUS", "0.85") or 0.85)
            if feather_radius > 0:
                try:
                    from PIL import ImageChops, ImageFilter

                    alpha = image.getchannel("A")
                    blurred = alpha.filter(ImageFilter.GaussianBlur(radius=feather_radius))
                    # Use only the inward side of the blur so transparent
                    # background pixels do not grow a pale/checkerboard halo.
                    image.putalpha(ImageChops.darker(alpha, blurred))
                except Exception:
                    pass
            bbox = _opaque_bbox_with_margin(image)
            if bbox is not None:
                crop_w = bbox[2] - bbox[0]
                crop_h = bbox[3] - bbox[1]
                if crop_w > 12 and crop_h > 12 and (crop_w < image.width * 0.96 or crop_h < image.height * 0.96):
                    image = image.crop(bbox)
        aspect = _parse_aspect_ratio(target_aspect_ratio)
        if aspect is not None:
            aspect_w, aspect_h = aspect
            out_w = int(target_width)
            out_h = max(1, int(round(out_w * aspect_h / aspect_w)))
            scale = min(out_w / max(1, image.width), out_h / max(1, image.height))
            new_w = max(1, int(round(image.width * scale)))
            new_h = max(1, int(round(image.height * scale)))
            if image.size != (new_w, new_h):
                image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
            x = (out_w - new_w) // 2
            y = (out_h - new_h) // 2 if not cutout_background else out_h - new_h
            canvas.alpha_composite(image, (x, max(0, y)))
            image = canvas
        out = BytesIO()
        image.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:
        return data


def nano_banana_generate_png(
    prompt: str,
    *,
    timeout: Optional[int] = None,
    aspect_ratio: Optional[str] = None,
    reference_images: tuple[str, ...] = (),
) -> bytes:
    key = os.environ.get("NANO_BANANA_API_KEY", "").strip() or os.environ.get("OPENAI_IMAGE_KEY", "").strip()
    if not key:
        raise RuntimeError("缺 NANO_BANANA_API_KEY 环境变量。")
    base_url = _provider_base_url()
    endpoint = os.environ.get("NANO_BANANA_TEXT_ENDPOINT", NANO_BANANA_DEFAULT_ENDPOINT)
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    timeout_s = int(timeout or os.environ.get("NANO_BANANA_TIMEOUT_SECONDS", "180") or 180)
    ratio = aspect_ratio or os.environ.get("NANO_BANANA_ASPECT_RATIO", PORTRAIT_ASPECT_RATIO)
    refs = tuple(str(ref) for ref in reference_images if str(ref).strip())
    if refs:
        return _nano_banana_generate_with_references(
            prompt,
            refs,
            timeout_s=timeout_s,
            aspect_ratio=ratio,
        )
    body = json.dumps({
        "prompt": prompt,
        "aspect_ratio": ratio,
        "output_format": "png",
        "resolution": os.environ.get("NANO_BANANA_RESOLUTION", "1k"),
        "enable_base64_output": True,
        "enable_sync_mode": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}{endpoint}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "Ming-Salvage-Sim/portrait-pipeline",
            "X-Client-Request-Id": hashlib.md5(prompt.encode("utf-8")).hexdigest(),
        },
    )
    payload = _read_json_response(req, timeout_s)
    return _image_bytes_from_provider_payload(payload, key=key, timeout_s=timeout_s)
