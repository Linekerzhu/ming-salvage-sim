"""净身恶趣味 · E2a：净身「宝」的处置、奴性表达、还阳传说、全尸执念。

净身入宫不只是身份转换——明代内廷的阴郁肌理：
  · 净身割下之「宝」（势）须有处置：自赎保存（kept，盛于匣中供奉，望来世全尸、还阳做完整人）、
    被官府收没（forfeit，强阉之奇辱）、或客死遗失（lost，终身憾）。
  · 升司礼监要职须「验宝」——无宝者为同侪所轻、迁转受沮。
  · 老宦官多信「还阳」之说——攒宝礼佛、望来世；亦有「年老阳气还生」的流言。
  · 强阉夺宝者奴性多扭曲（谄媚卑顺而心结阴鸷），自宫求进／自愿入者多恭谨守分——
    **奴性的表达随净身情形分野**，喂入与其对话的角色简报。
  · 殁时无全尸（forfeit/lost）是终身大憾，党羽哀恸更深。

本模块：eunuch_lore 表（每个净身者一行）+ 净身时登记（接 convert_character_to_eunuch）+
奴性/宝况注入对话简报（接 context 身份叠加层）+ 还阳传言月度 tick（挂 rollover）+
殁时全尸执念涟漪（接 lifespan.mortality_tick）。无净身记录则优雅降级。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from ming_sim.db import GameDB
from ming_sim.models import GameState, period_label

# 宝（势）之处置
BAO_KEPT = "kept"        # 自赎保存，盛匣供奉
BAO_FORFEIT = "forfeit"  # 被官府收没（强阉之辱）
BAO_LOST = "lost"        # 客死遗失，无凭

_AGED_FOR_REINCARNATION = 58   # 信「还阳」之说的高龄门槛

_DETAIL_COLUMNS = {
    "castration_method": "TEXT NOT NULL DEFAULT ''",
    "knife_tool": "TEXT NOT NULL DEFAULT ''",
    "anesthesia": "TEXT NOT NULL DEFAULT ''",
    "procedure_note": "TEXT NOT NULL DEFAULT ''",
    "bao_size": "TEXT NOT NULL DEFAULT ''",
    "bao_shape": "TEXT NOT NULL DEFAULT ''",
    "bao_texture": "TEXT NOT NULL DEFAULT ''",
    "bao_weight": "TEXT NOT NULL DEFAULT ''",
    "bao_preservation": "TEXT NOT NULL DEFAULT ''",
    "bao_container": "TEXT NOT NULL DEFAULT ''",
    "bao_ritual": "TEXT NOT NULL DEFAULT ''",
    "aftereffect": "TEXT NOT NULL DEFAULT ''",
    "urinary_aftereffect": "TEXT NOT NULL DEFAULT ''",
    "voice_body_change": "TEXT NOT NULL DEFAULT ''",
    "trauma_response": "TEXT NOT NULL DEFAULT ''",
    "private_fixation": "TEXT NOT NULL DEFAULT ''",
    "psychosexual_state": "TEXT NOT NULL DEFAULT ''",
}

_METHOD_FORCED = (
    "刑房急净",
    "奉旨宫刑",
    "番役按案粗净",
    "净军房夜割",
)
_METHOD_VOLUNTARY = (
    "熟匠细净",
    "内书堂老匠净身",
    "净军房温酒净身",
    "入宫前自请一刀",
)
_KNIFE_FORCED = (
    "刑房薄刃",
    "番役快刀",
    "旧军刀磨刃",
    "铜柄宫刀",
)
_KNIFE_VOLUNTARY = (
    "银柄小净刀",
    "老匠熟刀",
    "檀柄细刀",
    "内书堂净刀",
)
_ANESTHESIA_FORCED = (
    "无麻，冷汗硬熬",
    "烈酒灌口，算作麻醉",
    "蒙眼塞布，痛醒两回",
    "半碗麻沸散，药劲不足",
)
_ANESTHESIA_VOLUNTARY = (
    "温酒压惊",
    "麻沸散浅麻",
    "香汤熏鼻",
    "老匠按脉候气",
)
_FLOW_FORCED = (
    "奉旨押入净军房，验名封案，事毕即换内廷号衣",
    "刑房立案，番役守门，宝由官库收签",
    "夜半急办，不许亲旧送别，醒后即学叩首复命",
    "先杖后净，以辱折气，案尾钤上宫禁奴籍",
)
_FLOW_VOLUNTARY = (
    "自具甘结，老匠验身，事毕宝匣自赎",
    "内书堂引保，净后休养七日，再学传话礼",
    "先沐浴焚香，再请老匠细净，宝匣交本人收执",
    "入宫前签身契，净后改名，随堂听差",
)
_BAO_SIZE = (
    "小如雀卵",
    "中平常制",
    "偏沉粗大",
    "干瘪寒缩",
)
_BAO_SHAPE = (
    "圆缩成团",
    "细长偏皱",
    "一大一小",
    "瘪坠不匀",
)
_BAO_TEXTURE = (
    "韧而发暗",
    "干皱如旧枣",
    "油封后发硬",
    "石灰封后发白",
)
_BAO_WEIGHT = (
    "约一两二钱",
    "约二两",
    "约二两八钱",
    "轻得几乎无声",
)
_PRESERVE_KEPT = (
    "石灰封燥",
    "油炸封蜡",
    "香料腌藏",
    "盐灰同封",
)
_PRESERVE_FORFEIT = (
    "官库石灰封存",
    "刑房油炸验讫",
    "粗盐灰封入案袋",
    "香料压味后封签入库",
)
_PRESERVE_LOST = (
    "客途中失匣",
    "兵荒中散失",
    "旧仆卷匣而逃",
    "潮湿霉坏无凭",
)
_CONTAINER_KEPT = (
    "杉木宝匣",
    "黑漆楠木匣",
    "锡胆小木匣",
    "黄杨木描金匣",
)
_CONTAINER_FORFEIT = (
    "官库粗木匣",
    "白签灰瓮",
    "铁皮锁匣",
    "旧案匣",
)
_CONTAINER_LOST = (
    "破锦囊",
    "无名灰瓮",
    "失签木匣",
    "旧布包",
)
_RITUAL_KEPT = (
    "初一焚香供奉，望来世全尸",
    "夜半验匣，钥匙贴身",
    "小佛龛后暗藏宝匣",
    "临睡默念还阳旧愿",
)
_RITUAL_FORFEIT = (
    "暗记官库封签，终身惦念",
    "逢刑房文牍便失神",
    "私下打听宝匣下落",
    "忌听同辈谈全尸",
)
_AFTER_FORCED = (
    "阴雨旧创牵痛，夜半盗汗",
    "久立腰腹发冷，闻铜器心悸",
    "声线忽尖，怒时反笑",
    "逢刑房气味便面色煞白",
)
_AFTER_VOLUNTARY = (
    "畏寒怕湿，行步收敛",
    "久坐腰酸，闻药香便心定",
    "晨起嗓音发紧，午后易倦",
    "逢雨旧创发痒，越发谨慎",
)
_URINE_FORCED = (
    "漏尿，夜间须垫旧布",
    "尿线细弱，冬日易尿闭",
    "石淋反复，痛时额汗如豆",
    "小解灼痛，常备热砖暖腹",
)
_URINE_VOLUNTARY = (
    "小解不畅，遇寒更甚",
    "偶有漏尿，羞于近侍闻见",
    "结石隐患，久站后腹胀",
    "夜尿频仍，常以香囊遮味",
)
_VOICE_BODY_FORCED = (
    "嗓音尖薄，怒时破声；肩背微缩",
    "身形发软，步幅变碎；面色常青白",
    "腰腹畏寒，久跪后起身踉跄",
    "体态越发拘谨，见刑具便夹肩",
)
_VOICE_BODY_VOLUNTARY = (
    "嗓音渐细，笑时收声；体态谨饬",
    "腰腹怕冷，衣带束得极紧",
    "步子轻碎，常低首避视",
    "面色柔白，举止带内廷规矩",
)
_TRAUMA_FORCED = (
    "幻肢痛与噩梦并发，闻刀磨声即失态",
    "PTSD：被人按肩会骤然僵住",
    "梦回净房，醒后反复摸索不存在的旧物",
    "对封匣、验身、点名格外敏感",
)
_TRAUMA_VOLUNTARY = (
    "偶有幻肢痛，常以焚香压念",
    "怕冷与羞耻记忆交缠，夜间少眠",
    "旧创发作时沉默寡言，不肯示弱",
    "对宝匣过度在意，钥匙离身便心慌",
)
_FIX_FORCED = (
    "洁净癖与控物欲并重",
    "恋香压惊，厌恶血腥旧味",
    "偏爱掌管钥匙与封匣",
    "受罚仪式癖，越被明令越心定",
    "束带安定癖，衣带不紧便惊惶",
    "受辱后格外贪恋权柄分寸",
)
_FIX_VOLUNTARY = (
    "宝匣供奉癖",
    "恋香与数珠癖",
    "洁净癖，衣褶不齐便不安",
    "服从仪式癖，听见传旨声便心跳",
    "束缚安定癖，睡前必紧束腰带",
    "钥匙收藏癖",
)
_PSYCHOSEX_FORCED = (
    "性无能自知，转以权柄、服从与封匣仪式代偿",
    "贤者模式式空心麻木，欲念退潮后只剩畏冷与厌烦",
    "受罚束缚依恋，越被规训越心定",
    "畸恋式权力代偿，羞辱与掌控混作一团",
)
_PSYCHOSEX_VOLUNTARY = (
    "性欲淡薄，转以宝匣供奉和近侍秩序安神",
    "贤者模式式冷淡，事后只觉身空心静",
    "服从依恋，听见传旨声便生出安定感",
    "禁欲洁癖，厌肉欲而恋香、钥匙与规矩",
)


def _pick(options: tuple[str, ...], name: str, salt: str) -> str:
    if not options:
        return ""
    seed = sum(ord(ch) for ch in f"{name}:{salt}")
    return options[seed % len(options)]


def _default_detail(name: str, *, forced: bool, bao_status: str) -> Dict[str, str]:
    lost = bao_status == BAO_LOST
    if lost:
        preservation = _pick(_PRESERVE_LOST, name, "preserve-lost")
        container = _pick(_CONTAINER_LOST, name, "container-lost")
        ritual = "临终仍问旧匣下落，不得其凭"
    elif bao_status == BAO_FORFEIT:
        preservation = _pick(_PRESERVE_FORFEIT, name, "preserve-forfeit")
        container = _pick(_CONTAINER_FORFEIT, name, "container-forfeit")
        ritual = _pick(_RITUAL_FORFEIT, name, "ritual-forfeit")
    else:
        preservation = _pick(_PRESERVE_KEPT, name, "preserve-kept")
        container = _pick(_CONTAINER_KEPT, name, "container-kept")
        ritual = _pick(_RITUAL_KEPT, name, "ritual-kept")
    return {
        "castration_method": _pick(_METHOD_FORCED if forced else _METHOD_VOLUNTARY, name, "method"),
        "knife_tool": _pick(_KNIFE_FORCED if forced else _KNIFE_VOLUNTARY, name, "knife"),
        "anesthesia": _pick(_ANESTHESIA_FORCED if forced else _ANESTHESIA_VOLUNTARY, name, "anesthesia"),
        "procedure_note": _pick(_FLOW_FORCED if forced else _FLOW_VOLUNTARY, name, "flow"),
        "bao_size": _pick(_BAO_SIZE, name, "bao-size"),
        "bao_shape": _pick(_BAO_SHAPE, name, "bao-shape"),
        "bao_texture": _pick(_BAO_TEXTURE, name, "bao-texture"),
        "bao_weight": _pick(_BAO_WEIGHT, name, "bao-weight"),
        "bao_preservation": preservation,
        "bao_container": container,
        "bao_ritual": ritual,
        "aftereffect": _pick(_AFTER_FORCED if forced else _AFTER_VOLUNTARY, name, "after"),
        "urinary_aftereffect": _pick(_URINE_FORCED if forced else _URINE_VOLUNTARY, name, "urine"),
        "voice_body_change": _pick(_VOICE_BODY_FORCED if forced else _VOICE_BODY_VOLUNTARY, name, "voice-body"),
        "trauma_response": _pick(_TRAUMA_FORCED if forced else _TRAUMA_VOLUNTARY, name, "trauma"),
        "private_fixation": _pick(_FIX_FORCED if forced else _FIX_VOLUNTARY, name, "fix"),
        "psychosexual_state": _pick(_PSYCHOSEX_FORCED if forced else _PSYCHOSEX_VOLUNTARY, name, "psychosexual"),
    }


def _detail_insert_sql(base_columns: tuple[str, ...]) -> str:
    columns = list(base_columns) + list(_DETAIL_COLUMNS.keys())
    placeholders = ",".join(["?"] * len(columns))
    return f"INSERT INTO eunuch_lore({','.join(columns)}) VALUES ({placeholders})"


def _detail_values(details: Dict[str, str]) -> List[str]:
    return [str(details.get(column) or "") for column in _DETAIL_COLUMNS]


def _detail_update_sql() -> str:
    return ", ".join(f"{column}=?" for column in _DETAIL_COLUMNS)


def _is_adult_for_lore(db: GameDB, name: str) -> bool:
    """Adult gate for lore fields that should not be exposed for young palace boys."""
    try:
        row = db.conn.execute(
            """
            SELECT c.birth_year AS birth_year, gs.year AS year
            FROM characters c
            LEFT JOIN game_state gs ON gs.id=1
            WHERE c.name=?
            """,
            ((name or "").strip(),),
        ).fetchone()
    except Exception:
        row = None
    if row is None:
        return True
    birth_year = int(row["birth_year"] or 0)
    year = int(row["year"] or 0)
    if birth_year <= 0 or year <= 0:
        return True
    return year - birth_year >= 18


def _clamp_0_100(value: int) -> int:
    return max(0, min(100, int(value)))


def ensure_schema(db: GameDB) -> None:
    db.conn.execute(
        """CREATE TABLE IF NOT EXISTS eunuch_lore (
            name TEXT PRIMARY KEY,
            bao_status TEXT NOT NULL DEFAULT 'kept',
            forced INTEGER NOT NULL DEFAULT 0,
            servility INTEGER NOT NULL DEFAULT 45,
            castration_day INTEGER NOT NULL DEFAULT 0,
            reincarnation INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT ''
        )""")
    cols = {str(row["name"]) for row in db.conn.execute("PRAGMA table_info(eunuch_lore)").fetchall()}
    for column, ddl in _DETAIL_COLUMNS.items():
        if column not in cols:
            db.conn.execute(f"ALTER TABLE eunuch_lore ADD COLUMN {column} {ddl}")
    db.conn.commit()


def seed_eunuch_lore(db: GameDB) -> None:
    """幂等：为开局已在朝的内廷／司礼监宦官补默认净身记录（多系少年自宫入仕，宝存恭谨）。"""
    ensure_schema(db)
    from ming_sim.eunuch import is_eunuch_like
    rows = db.conn.execute(
        "SELECT name, office, office_type, integrity, loyalty, status_reason FROM characters "
        "WHERE status='active' AND power_id='ming'").fetchall()
    for r in rows:
        name = str(r["name"])
        if not is_eunuch_like(str(r["office"] or ""), str(r["office_type"] or "")):
            continue
        if db.conn.execute("SELECT 1 FROM eunuch_lore WHERE name=?", (name,)).fetchone():
            continue
        reason = str(r["status_reason"] or "")
        forced = 1 if ("强旨" in reason or "心结" in reason) else 0
        integ = int(r["integrity"] or 50)
        loy = int(r["loyalty"] or 50)
        # 奴性：自愿入仕的少年宦官多恭谨自安；忠厚者奴性平、阴鸷弄权者奴性低（不甘为奴）。
        servility = 45 + max(0, 55 - integ) // 4 - max(0, loy - 60) // 5
        servility = max(15, min(85, servility + (25 if forced else 0)))
        bao = BAO_FORFEIT if forced else BAO_KEPT
        details = _default_detail(name, forced=bool(forced), bao_status=bao)
        note = (
            "少年净身入仕，宝匣自藏"
            if not forced
            else "奉强旨净身，宝为官没"
        )
        db.conn.execute(
            _detail_insert_sql(("name", "bao_status", "forced", "servility", "castration_day", "note")),
            (name, bao, forced, int(servility), 0, note, *_detail_values(details)),
        )
    db.conn.commit()


def record_castration(db: GameDB, name: str, *, forced: bool, day: int, detail_text: str = "") -> Dict[str, object]:
    """净身时登记「宝」之处置与奴性（接 convert_character_to_eunuch）。
    强阉＝宝被官府收没、奴性扭曲（谄而心结深）；自愿＝宝可自赎保存、奴性恭谨。"""
    ensure_schema(db)
    name = (name or "").strip()
    if not name:
        return {}
    bao = BAO_FORFEIT if forced else BAO_KEPT
    servility = 78 if forced else 46
    details = _default_detail(name, forced=bool(forced), bao_status=bao)
    if not _is_adult_for_lore(db, name):
        details["psychosexual_state"] = ""
    note = (
        "奉强旨净身，宝为官没——奇辱深结"
        if forced
        else "奏对同意后净身，宝匣自藏供奉"
    )
    note = (
        f"{note}；{details['castration_method']}，{details['knife_tool']}，{details['anesthesia']}；"
        f"{details['bao_size']}，{details['bao_shape']}，{details['bao_texture']}，{details['bao_weight']}，{details['bao_preservation']}，"
        f"{details['bao_container']}；后遗：{details['aftereffect']}；尿路：{details['urinary_aftereffect']}；"
        f"体声：{details['voice_body_change']}；惊创：{details['trauma_response']}；"
        f"隐癖：{details['private_fixation']}；癖性：{details['psychosexual_state']}"
    )
    db.conn.execute(
        "INSERT INTO eunuch_lore(name, bao_status, forced, servility, castration_day, reincarnation, note) "
        "VALUES (?,?,?,?,?,0,?) "
        "ON CONFLICT(name) DO UPDATE SET bao_status=excluded.bao_status, forced=excluded.forced, "
        "servility=excluded.servility, castration_day=excluded.castration_day, note=excluded.note",
        (name, bao, 1 if forced else 0, int(servility), int(day), note))
    db.conn.execute(
        f"UPDATE eunuch_lore SET {_detail_update_sql()} WHERE name=?",
        (*_detail_values(details), name),
    )
    db.conn.commit()
    result: Dict[str, object] = {"name": name, "bao_status": bao, "forced": bool(forced), "servility": servility, **details}
    if str(detail_text or "").strip():
        updated = update_lore_from_text(db, name, detail_text, day=day)
        if isinstance(updated, dict) and isinstance(updated.get("castration"), dict):
            refreshed = get_lore(db, name) or {}
            result.update(refreshed)
            result["scheme_applied"] = updated.get("updated", {})
    return result


def get_lore(db: GameDB, name: str) -> Optional[Dict[str, object]]:
    ensure_schema(db)
    row = db.conn.execute(
        "SELECT name, bao_status, forced, servility, castration_day, reincarnation, note, "
        f"{', '.join(_DETAIL_COLUMNS.keys())} "
        "FROM eunuch_lore WHERE name=?", ((name or "").strip(),)).fetchone()
    if row is None:
        return None
    forced = bool(row["forced"])
    bao = str(row["bao_status"])
    details = _default_detail(str(row["name"]), forced=forced, bao_status=bao)
    for key in _DETAIL_COLUMNS:
        value = str(row[key] or "").strip()
        if value:
            details[key] = value
    return {"name": str(row["name"]), "bao_status": bao,
            "forced": forced, "servility": int(row["servility"]),
            "castration_day": int(row["castration_day"]), "reincarnation": bool(row["reincarnation"]),
            "note": str(row["note"] or ""), **details}


_BAO_LABEL = {BAO_KEPT: "宝匣自藏（望来世全尸）", BAO_FORFEIT: "宝为官没（强阉之辱）",
              BAO_LOST: "宝已遗失（客死无凭）"}


def _voice_profile_from_lore(db: GameDB, name: str, lore: Dict[str, object]) -> Dict[str, object]:
    """Stable eunuch voice/stage profile derived from stats and castration lore."""

    try:
        row = db.conn.execute(
            "SELECT ability, wisdom, courage, style, office, office_type FROM characters WHERE name=?",
            ((name or "").strip(),),
        ).fetchone()
    except Exception:
        row = None
    ability = int(row["ability"] or 50) if row else 50
    wisdom = int(row["wisdom"] or 50) if row and "wisdom" in row.keys() else 50
    courage = int(row["courage"] or 50) if row else 50
    style = str(row["style"] or "") if row else ""
    office = str(row["office"] or "") if row else ""
    office_type = str(row["office_type"] or "") if row else ""
    servility = int(lore.get("servility") or 45)
    forced = bool(lore.get("forced"))
    bao = str(lore.get("bao_status") or "")
    condition_blob = " ".join(
        str(lore.get(key) or "")
        for key in (
            "urinary_aftereffect",
            "voice_body_change",
            "trauma_response",
            "private_fixation",
            "bao_ritual",
        )
    )
    low_culture = bool(
        ability <= 48
        or wisdom <= 45
        or re.search(r"小火者|生徒|识字不多|出身寒微|粗|怯|逃荒", f"{style} {office}")
    )
    clerkly = bool(wisdom >= 70 or ability >= 70 or re.search(r"文书|内书堂|识字|司礼监", f"{style} {office} {office_type}"))
    if low_culture:
        register = "低文化内侍"
        speech_rule = (
            "短句、土话、宫里切口；话可以糙，但不要现代脏话。只从殿门、监房、名册、跑腿见闻说起，"
            "遇到朝政大题要说“奴婢只听得这点风声”，不要替内阁筹划全套国策。"
        )
        pet_phrases = ["奴婢晓得", "不敢瞒陛下", "那档子事", "小的听来的"]
        allowed_moves = [
            "用值房、廊下、门上、净房、封签这些近身见闻答话",
            "承认自己不懂外朝大局，只报听来的风声和跑腿见闻",
            "句子短，先请罪，再说人名、地点、谁吩咐、谁递话",
        ]
        forbidden_moves = [
            "不要讲内阁大学士式长篇财政/边防总策",
            "不要用经筵、策论、空泛忠君爱民套话撑满回答",
            "不要突然全知全能点评所有派系真实动机",
        ]
        slang = ["门上递话", "值房听来的", "净房旧例", "封签没对上", "那档子腌臜事"]
    elif clerkly:
        register = "识字文书内臣"
        speech_rule = (
            "会说名册、封签、账页、钥匙、值房规矩；判断仍从内廷文书和传旨差使出发，"
            "不要装外朝通儒。"
        )
        pet_phrases = ["奴婢按册回", "封签上写得明白", "值房里有旧例", "容奴婢查一查档"]
        allowed_moves = [
            "从名册、封签、账页、钥匙、值房旧例推断",
            "能核对谁经手、谁押签、哪份档案缺页",
            "可给出内廷执行建议，但不替六部包办国策",
        ]
        forbidden_moves = [
            "不要写成外朝清流奏疏口吻",
            "不要越过内廷文书证据直接断大案全貌",
        ]
        slang = ["按册回话", "封签旧例", "钥匙在谁手里", "账页缺口", "值房押签"]
    else:
        register = "谨慎近侍"
        speech_rule = "先复命，再说风险；话留半寸，不抢皇帝决断。"
        pet_phrases = ["奴婢领会", "这事还得陛下定夺", "奴婢只敢照实回", "容奴婢递个话"]
        allowed_moves = [
            "先复命，再补一条风险或传闻",
            "把判断压低成“奴婢看着像”，留皇帝裁断",
        ]
        forbidden_moves = [
            "不要抢皇帝决断",
            "不要把传闻说成铁案",
        ]
        slang = ["容奴婢递话", "门外风声", "值房规矩", "不敢替陛下断"]
    if courage >= 72:
        register += " · 急性子"
        speech_rule += " 性急时可先抢半句，随即叩首收住；短促、冒进、怕误事。"
        pet_phrases.append("奴婢先说一句")
        allowed_moves.append("急时先冒出半句实话，再立刻叩首收住")
        slang.append("奴婢急着回一句")
    elif courage <= 42:
        register += " · 胆怯"
        speech_rule += " 胆怯时吞字、停顿、先请罪再答实话。"
        pet_phrases.append("奴婢该死")
        allowed_moves.append("胆怯时吞字、停顿、先请罪，再说最小的一段实话")
        slang.append("奴婢该死")
    if forced or bao == BAO_FORFEIT:
        register += " · 强阉心结"
        speech_rule += " 被问到净房、封签、宝匣时表面更卑顺，底下有怨气。"
    elif servility >= 65:
        register += " · 奴性重"
        speech_rule += " 请恩时更谄、更爱揣摩上意，但仍不能越职讲朝政大局。"
    stage_cues: List[str] = []
    if re.search(r"漏尿|尿闭|石淋|小解|夜尿", condition_blob):
        stage_cues.append("久站时夹腰缩步，嗫嚅请退半步")
    if re.search(r"嗓音|尖薄|破声|体态|肩背|步子", condition_blob):
        stage_cues.append("说急了嗓音发尖，肩背微缩")
    if re.search(r"幻肢|PTSD|噩梦|刀声|净房|按肩", condition_blob, flags=re.IGNORECASE):
        stage_cues.append("听见刀、净房、验身旧话会短暂失神")
    if re.search(r"宝匣|钥匙|供奉|封签", condition_blob) or bao in {BAO_KEPT, BAO_FORFEIT}:
        stage_cues.append("偶尔摸袖中钥匙或避谈宝匣")
    if not stage_cues:
        stage_cues.append("垂手贴身侍立，先看皇帝脸色再回话")
    return {
        "register": register,
        "speech_rule": speech_rule,
        "pet_phrases": list(dict.fromkeys(pet_phrases))[:5],
        "allowed_moves": list(dict.fromkeys(allowed_moves))[:5],
        "forbidden_moves": list(dict.fromkeys(forbidden_moves))[:5],
        "slang": list(dict.fromkeys(slang))[:6],
        "stage_cues": list(dict.fromkeys(stage_cues))[:5],
    }


def eunuch_voice_profile(db: GameDB, name: str) -> Optional[Dict[str, object]]:
    lore = get_lore(db, name)
    if lore is None:
        return None
    return _voice_profile_from_lore(db, name, lore)


def castration_scheme_profile(lore: Dict[str, object]) -> Dict[str, object]:
    """Gameplay profile for the chosen castration/bao handling scheme.

    This is intentionally deterministic and data-only: UI, dialogue, dispatch
    risk, and care costs can all read the same profile without inventing lore.
    """

    raw = " ".join(
        str(lore.get(key) or "")
        for key in (
            "note",
            "castration_method",
            "knife_tool",
            "anesthesia",
            "procedure_note",
            "bao_preservation",
            "bao_container",
            "bao_ritual",
            "aftereffect",
            "urinary_aftereffect",
            "voice_body_change",
            "trauma_response",
        )
    )
    explicit = "奏对维护净身档案" in raw or "御前方案" in raw
    brutality = 48 if bool(lore.get("forced")) else 28
    trauma_risk = 30
    surgery_risk = 30
    bao_security = 45
    care_cost_delta = 0
    effects: List[str] = []

    def bump(label: str, *, brutal: int = 0, trauma: int = 0, surgery: int = 0, bao: int = 0, care: int = 0) -> None:
        nonlocal brutality, trauma_risk, surgery_risk, bao_security, care_cost_delta
        brutality += int(brutal)
        trauma_risk += int(trauma)
        surgery_risk += int(surgery)
        bao_security += int(bao)
        care_cost_delta += int(care)
        if label and label not in effects:
            effects.append(label)

    if re.search(r"净军房|刑房|宫刑|番役|押入|夜半|杖", raw):
        bump("刑房急办：震慑强，怨望与惊创风险上升", brutal=14, trauma=14, surgery=8, care=1)
    if re.search(r"无麻|冷汗硬熬|痛醒", raw):
        bump("无麻硬熬：短期压服，尿路与惊创后患加重", brutal=16, trauma=16, surgery=10, care=2)
    elif re.search(r"麻沸散|香汤|熟匠|老匠|细净|沐浴|焚香", raw):
        bump("细净安置：创伤较轻，后续调养省力", brutal=-8, trauma=-8, surgery=-8, care=-1)
    elif re.search(r"烈酒|蒙眼|塞布", raw):
        bump("粗麻遮痛：能撑流程，醒后旧创更难安", brutal=5, trauma=6, surgery=4)
    if re.search(r"铜柄宫刀|番役快刀|刑房薄刃|旧军刀", raw):
        bump("刀具粗硬：失仪与旧创风险升高", brutal=5, trauma=4, surgery=5)
    elif re.search(r"银柄小净刀|檀柄细刀", raw):
        bump("刀具细净：伤口较稳，体声失仪略缓", brutal=-3, trauma=-2, surgery=-4)
    if re.search(r"漏尿|尿闭|石淋|灼痛|夜尿|尿频", raw):
        bump("尿路后患：久候、远行、夜值差遣风险升高", surgery=12, care=1)
    if re.search(r"幻肢|PTSD|噩梦|梦回|磨刀|按肩|净房旧话", raw, flags=re.IGNORECASE):
        bump("惊创未平：刑房、封签、逼问场景容易失神", trauma=14, care=1)
    if re.search(r"嗓音|尖薄|体态|肩背|步子|腰腹|久跪", raw):
        bump("体声异变：乔装问话与公开传旨更容易露怯", surgery=5)
    if re.search(r"油炸|石灰|盐灰|香料|封蜡|封签|官库", raw):
        bump("宝案封存：线索清楚，但封签会牵动心结", bao=18, trauma=4)
    if re.search(r"楠木|黄杨|锡胆|杉木|描金|宝匣|钥匙|供奉|佛龛", raw):
        bump("宝匣安置：可供后续验宝、安抚或追查", bao=18, trauma=-2)
    if str(lore.get("bao_status") or "") == BAO_FORFEIT:
        bump("宝为官没：服从来自恐惧，长线怨气更重", brutal=8, trauma=8, care=1)
    elif str(lore.get("bao_status") or "") == BAO_KEPT:
        bump("宝可自藏：全尸执念有安放处，内廷规矩较稳", brutal=-4, trauma=-4, bao=12, care=-1)

    brutality = _clamp_0_100(brutality)
    trauma_risk = _clamp_0_100(trauma_risk)
    surgery_risk = _clamp_0_100(surgery_risk)
    bao_security = _clamp_0_100(bao_security)
    risk_score = _clamp_0_100((brutality * 3 + trauma_risk * 3 + surgery_risk * 2 + (100 - bao_security)) // 9)
    if risk_score >= 72:
        tier = "酷烈高危"
    elif risk_score >= 55:
        tier = "粗急伤身"
    elif risk_score >= 38:
        tier = "可控旧例"
    else:
        tier = "细净安置"
    stat_delta = {
        "emp_trust": max(-4, min(2, -max(0, brutality - 58) // 14 + max(0, 42 - trauma_risk) // 18)),
        "grievance": max(-2, min(8, max(0, brutality - 45) // 8 + max(0, trauma_risk - 55) // 12)),
        "ability": -1 if surgery_risk >= 62 else 0,
        "wisdom": 1 if bao_security >= 72 and trauma_risk < 60 else 0,
        "charm": -1 if surgery_risk >= 52 or trauma_risk >= 64 else 0,
        "luck": -1 if risk_score >= 72 else 0,
    }
    care_cost_delta = max(-1, min(4, int(care_cost_delta)))
    return {
        "tier": tier,
        "explicit": bool(explicit),
        "risk_score": risk_score,
        "brutality": brutality,
        "trauma_risk": trauma_risk,
        "surgery_risk": surgery_risk,
        "bao_security": bao_security,
        "care_cost_delta": care_cost_delta if explicit else 0,
        "stat_delta": stat_delta if explicit else {},
        "effects": effects[:6],
    }


def public_lore_payload(db: GameDB, name: str) -> Optional[Dict[str, object]]:
    """人物档案/前端公开用：把净身旧档折成稳定 payload。"""
    lore = get_lore(db, name)
    if lore is None:
        return None
    adult = _is_adult_for_lore(db, name)
    payload = dict(lore)
    payload["bao_label"] = _BAO_LABEL.get(str(lore["bao_status"]), "")
    payload["method_label"] = str(lore.get("castration_method") or "")
    payload["knife_label"] = str(lore.get("knife_tool") or "")
    payload["anesthesia_label"] = str(lore.get("anesthesia") or "")
    payload["procedure_label"] = str(lore.get("procedure_note") or "")
    payload["bao_size_label"] = str(lore.get("bao_size") or "")
    payload["bao_shape_label"] = str(lore.get("bao_shape") or "")
    payload["bao_texture_label"] = str(lore.get("bao_texture") or "")
    payload["bao_weight_label"] = str(lore.get("bao_weight") or "")
    payload["preservation_label"] = str(lore.get("bao_preservation") or "")
    payload["container_label"] = str(lore.get("bao_container") or "")
    payload["ritual_label"] = str(lore.get("bao_ritual") or "")
    payload["aftereffect_label"] = str(lore.get("aftereffect") or "")
    payload["urine_label"] = str(lore.get("urinary_aftereffect") or "")
    payload["voice_body_label"] = str(lore.get("voice_body_change") or "")
    payload["trauma_label"] = str(lore.get("trauma_response") or "")
    fixation = str(lore.get("private_fixation") or "")
    if not adult and re.search(r"受罚|束缚|羞辱|调教|畸恋|情欲|肉欲|性", fixation):
        fixation = ""
    payload["fixation_label"] = fixation
    payload["psychosexual_label"] = str(lore.get("psychosexual_state") or "") if adult else ""
    payload["detail_line"] = " · ".join(
        part for part in (
            payload["method_label"],
            payload["knife_label"],
            payload["anesthesia_label"],
            payload["bao_size_label"],
            payload["bao_shape_label"],
            payload["bao_texture_label"],
            payload["bao_weight_label"],
            payload["preservation_label"],
            payload["container_label"],
        ) if part
    )
    payload["condition_line"] = "；".join(
        part for part in (
            f"后遗：{payload['aftereffect_label']}" if payload["aftereffect_label"] else "",
            f"尿路：{payload['urine_label']}" if payload["urine_label"] else "",
            f"体声：{payload['voice_body_label']}" if payload["voice_body_label"] else "",
            f"惊创：{payload['trauma_label']}" if payload["trauma_label"] else "",
            f"隐癖：{payload['fixation_label']}" if payload["fixation_label"] else "",
            f"癖性：{payload['psychosexual_label']}" if payload["psychosexual_label"] else "",
            str(payload["ritual_label"] or ""),
        ) if part
    )
    payload["procedure_line"] = str(payload["procedure_label"] or "")
    payload["voice_profile"] = _voice_profile_from_lore(db, name, lore)
    payload["scheme_profile"] = castration_scheme_profile(lore)
    return payload


def _set_if_match(updates: Dict[str, str], text: str, key: str, patterns: List[tuple[str, str]]) -> None:
    if key in updates:
        return
    for pattern, value in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            updates[key] = value
            return


def update_lore_from_text(db: GameDB, name: str, text: str, *, day: int = 0) -> Dict[str, object]:
    """把奏对/剧情文本里明确提到的净身细节吸收到既有宦官档案。

    这是轻量确定性规则，不凭空创造 lore：只有已存在净身记录的人物才会被维护。
    """
    lore = get_lore(db, name)
    if lore is None:
        return {}
    raw = str(text or "").strip()
    if not raw:
        return {}
    updates: Dict[str, str] = {}
    _set_if_match(updates, raw, "castration_method", [
        (r"净军房|净军", "净军房夜割"),
        (r"内书堂|老匠|熟匠", "内书堂老匠净身"),
        (r"自请|自宫|自愿", "入宫前自请一刀"),
        (r"刑房|宫刑|强旨", "奉旨宫刑"),
    ])
    _set_if_match(updates, raw, "knife_tool", [
        (r"银柄|银刀", "银柄小净刀"),
        (r"铜柄|铜刀", "铜柄宫刀"),
        (r"檀柄|檀木", "檀柄细刀"),
        (r"番役|快刀", "番役快刀"),
        (r"薄刃|刑房刀", "刑房薄刃"),
    ])
    _set_if_match(updates, raw, "anesthesia", [
        (r"无麻|不用麻|没麻", "无麻，冷汗硬熬"),
        (r"烈酒|灌酒", "烈酒灌口，算作麻醉"),
        (r"麻沸散|麻药", "麻沸散浅麻"),
        (r"蒙眼|塞布", "蒙眼塞布，痛醒两回"),
    ])
    _set_if_match(updates, raw, "procedure_note", [
        (r"押入|封案|换.*号衣", "奉旨押入净军房，验名封案，事毕即换内廷号衣"),
        (r"验名|官库|收签", "刑房立案，番役守门，宝由官库收签"),
        (r"沐浴|焚香|宝匣交", "先沐浴焚香，再请老匠细净，宝匣交本人收执"),
        (r"身契|改名|随堂", "入宫前签身契，净后改名，随堂听差"),
    ])
    _set_if_match(updates, raw, "bao_size", [
        (r"小如雀卵|很小|偏小", "小如雀卵"),
        (r"粗大|偏大|很大", "偏沉粗大"),
        (r"干瘪|寒缩", "干瘪寒缩"),
        (r"中平|平常", "中平常制"),
    ])
    _set_if_match(updates, raw, "bao_shape", [
        (r"一大一小", "一大一小"),
        (r"细长|偏皱", "细长偏皱"),
        (r"瘪坠|不匀", "瘪坠不匀"),
        (r"圆缩|成团", "圆缩成团"),
    ])
    _set_if_match(updates, raw, "bao_texture", [
        (r"旧枣|干皱", "干皱如旧枣"),
        (r"油封|发硬", "油封后发硬"),
        (r"石灰|发白", "石灰封后发白"),
        (r"韧|发暗", "韧而发暗"),
    ])
    _set_if_match(updates, raw, "bao_weight", [
        (r"一两|一两二钱", "约一两二钱"),
        (r"二两八|三两", "约二两八钱"),
        (r"二两", "约二两"),
        (r"轻|无声", "轻得几乎无声"),
    ])
    _set_if_match(updates, raw, "bao_preservation", [
        (r"油炸", "油炸封蜡"),
        (r"石灰", "石灰封燥"),
        (r"香料|香灰|香丸", "香料腌藏"),
        (r"盐灰|粗盐", "盐灰同封"),
        (r"官库", "官库石灰封存"),
    ])
    _set_if_match(updates, raw, "bao_container", [
        (r"楠木", "黑漆楠木匣"),
        (r"杉木", "杉木宝匣"),
        (r"锡胆|锡匣", "锡胆小木匣"),
        (r"黄杨|描金", "黄杨木描金匣"),
        (r"铁皮|锁匣", "铁皮锁匣"),
        (r"灰瓮", "白签灰瓮"),
    ])
    _set_if_match(updates, raw, "bao_ritual", [
        (r"初一|焚香|供奉", "初一焚香供奉，望来世全尸"),
        (r"验匣|钥匙贴身", "夜半验匣，钥匙贴身"),
        (r"佛龛|暗藏", "小佛龛后暗藏宝匣"),
        (r"还阳|来世", "临睡默念还阳旧愿"),
    ])
    _set_if_match(updates, raw, "aftereffect", [
        (r"阴雨|旧创|牵痛", "阴雨旧创牵痛，夜半盗汗"),
        (r"畏寒|腰腹.*冷", "久立腰腹发冷，闻铜器心悸"),
        (r"盗汗|夜半", "阴雨旧创牵痛，夜半盗汗"),
    ])
    _set_if_match(updates, raw, "urinary_aftereffect", [
        (r"漏尿.{0,8}尿闭|尿闭.{0,8}漏尿", "漏尿兼尿闭，夜间须垫旧布，冬日易闭"),
        (r"漏尿", "漏尿，夜间须垫旧布"),
        (r"尿闭|尿线|小便不通", "尿线细弱，冬日易尿闭"),
        (r"结石|石淋", "石淋反复，痛时额汗如豆"),
        (r"灼痛|小解痛", "小解灼痛，常备热砖暖腹"),
        (r"夜尿|尿频", "夜尿频仍，常以香囊遮味"),
    ])
    _set_if_match(updates, raw, "voice_body_change", [
        (r"嗓音|尖嗓|破声", "嗓音尖薄，怒时破声；肩背微缩"),
        (r"体态|肩背|夹肩", "体态越发拘谨，见刑具便夹肩"),
        (r"步子|步幅|轻碎", "步子轻碎，常低首避视"),
        (r"腰腹|久跪|踉跄", "腰腹畏寒，久跪后起身踉跄"),
    ])
    _set_if_match(updates, raw, "trauma_response", [
        (r"幻肢|幻痛", "幻肢痛与噩梦并发，闻刀磨声即失态"),
        (r"PTSD|噩梦|梦回", "梦回净房，醒后反复摸索不存在的旧物"),
        (r"按肩|僵住", "PTSD：被人按肩会骤然僵住"),
        (r"磨刀|刀声", "幻肢痛与噩梦并发，闻刀磨声即失态"),
    ])
    private_patterns = [
        (r"洁净|爱干净", "洁净癖，衣褶不齐便不安"),
        (r"钥匙|封匣|掌管.*匣", "偏爱掌管钥匙与封匣"),
        (r"恋香|香味", "恋香压惊，厌恶血腥旧味"),
    ]
    if _is_adult_for_lore(db, name):
        private_patterns.extend([
            (r"受罚|规训|羞辱", "受罚仪式癖，越被明令越心定"),
            (r"束缚|束带|捆|缚", "束带安定癖，衣带不紧便惊惶"),
        ])
    _set_if_match(updates, raw, "private_fixation", private_patterns)
    if _is_adult_for_lore(db, name):
        _set_if_match(updates, raw, "psychosexual_state", [
            (r"贤者模式", "贤者模式式空心麻木，欲念退潮后只剩畏冷与厌烦"),
            (r"性无能|不能人道|无能", "性无能自知，转以权柄、服从与封匣仪式代偿"),
            (r"变态|畸恋", "畸恋式权力代偿，羞辱与掌控混作一团"),
            (r"BDSM|受罚|束缚|羞辱|调教", "受罚束缚依恋，越被规训越心定"),
            (r"禁欲|冷淡|无欲", "性欲淡薄，转以宝匣供奉和近侍秩序安神"),
        ])
    changed = {key: value for key, value in updates.items() if str(lore.get(key) or "") != value}
    if not changed:
        return {}
    note = str(lore.get("note") or "")
    changed_summary = "；".join(f"{key}={value}" for key, value in changed.items())
    addition = f"奏对维护净身档案（第{int(day or 0)}日）：{changed_summary}"
    note = f"{note}；{addition}" if note else addition
    assignments = [f"{key}=?" for key in changed]
    values: List[object] = list(changed.values())
    assignments.append("note=?")
    values.append(note)
    values.append(name)
    db.conn.execute(
        f"UPDATE eunuch_lore SET {', '.join(assignments)} WHERE name=?",
        values,
    )
    db.conn.commit()
    return {"name": name, "updated": changed, "castration": public_lore_payload(db, name)}


def servility_brief(db: GameDB, name: str) -> str:
    """注入与净身者对话的角色简报：奴性表达 + 宝况心结，随净身情形分野。无记录则空。"""
    lore = get_lore(db, name)
    if lore is None:
        return ""
    serv = int(lore["servility"])
    forced = bool(lore["forced"])
    bao = str(lore["bao_status"])
    parts: List[str] = ["【净身·心相】"]
    if forced or bao == BAO_FORFEIT:
        parts.append("你是被强旨净身入宫的——这是奇辱。表面卑顺谄媚、口称奴婢叩首不迭，"
                     "内里却结着深重心结，逢迎之中带几分阴鸷与不甘；对昔日羞辱你的外朝，怨而隐忍。")
    elif serv >= 60:
        parts.append("你自净身入宫，安于为奴：恭顺谄媚、揣摩上意唯恐不及，以伺候得陛下欢心为荣，"
                     "言必称奴婢、奴才，事事先意承旨。")
    else:
        parts.append("你净身入宫多年，奴性已化为分寸：恭谨守礼而不谄佞，知所进退；"
                     "口称奴婢，却存一段读书人或老内臣的体面。")
    if bao == BAO_KEPT:
        parts.append("你私藏「宝」于匣中供奉，礼佛攒福，望来世做个全尸完整之人——此念你深藏不露。")
    elif bao == BAO_FORFEIT:
        parts.append("你的「宝」被官府收没，每念及死后不得全尸，便如鲠在喉，是你最深的隐痛。")
    details = public_lore_payload(db, name)
    if details:
        parts.append(
            f"净身旧档：{details.get('detail_line') or '旧档不全'}；"
            f"{details.get('condition_line') or '后遗未详'}。"
        )
    profile = eunuch_voice_profile(db, name) or {}
    voice_rules: List[str] = []
    register = str(profile.get("register") or "").strip()
    speech_rule = str(profile.get("speech_rule") or "").strip()
    pet_phrases = [str(item).strip() for item in (profile.get("pet_phrases") or []) if str(item).strip()]
    allowed_moves = [str(item).strip() for item in (profile.get("allowed_moves") or []) if str(item).strip()]
    forbidden_moves = [str(item).strip() for item in (profile.get("forbidden_moves") or []) if str(item).strip()]
    slang = [str(item).strip() for item in (profile.get("slang") or []) if str(item).strip()]
    if register or speech_rule:
        phrase_text = f"；常用口头禅：{'、'.join(pet_phrases[:4])}" if pet_phrases else ""
        voice_rules.append(f"{register}：{speech_rule}{phrase_text}")
    if allowed_moves:
        voice_rules.append("可用说法：" + "；".join(allowed_moves[:4]))
    if slang:
        voice_rules.append("宫里切口/粗口径：" + "、".join(slang[:6]))
    if forbidden_moves:
        voice_rules.append("禁用话术：" + "；".join(forbidden_moves[:4]))
    stage_bits = [str(item).strip() for item in (profile.get("stage_cues") or []) if str(item).strip()]
    if voice_rules:
        parts.append("【口吻差异】" + "；".join(voice_rules))
    if stage_bits:
        parts.append("【动作神态】动作/神态要短，不要塞满对白正文；可用极短括注表现：" + "；".join(dict.fromkeys(stage_bits[:4])))
    return "".join(parts)


def lore_overlay(db: GameDB, name: str) -> Dict[str, str]:
    """供 context 身份叠加层取用：把奴性与宝况折成 biography/risk 增补片段。"""
    brief = servility_brief(db, name)
    if not brief:
        return {}
    return {"servility": brief}


def reincarnation_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初：老宦官的「还阳」传言与攒宝礼佛之念（迷信 flavor，至多一桩/月）。挂 rollover。"""
    from ming_sim.timeflow import LEVEL_BLUE
    from ming_sim.lifespan import _age  # 复用年龄算法
    ensure_schema(db)
    year = int(getattr(state, "year", 0) or 0)
    rows = db.conn.execute(
        "SELECT l.name, l.bao_status, l.reincarnation, c.office, c.birth_year "
        "FROM eunuch_lore l JOIN characters c ON c.name=l.name "
        "WHERE c.status='active' AND c.power_id='ming'").fetchall()
    # 确定性挑选（不用随机）：取尚未起过还阳传言、年最高的一位老宦官。
    aged = []
    for r in rows:
        age = _age(int(r["birth_year"] or 0), year) if year else 0
        if age >= _AGED_FOR_REINCARNATION and not int(r["reincarnation"]):
            aged.append((age, str(r["name"]), str(r["office"] or ""), str(r["bao_status"])))
    if not aged:
        return []
    # 仅在「月之朔逢特定节律」起一桩，避免月月刷屏：用 day 的整除性当确定性闸门。
    if (int(day) // 30) % 4 != 0:
        return []
    aged.sort(reverse=True)
    age, name, office, bao = aged[0]
    db.conn.execute("UPDATE eunuch_lore SET reincarnation=1 WHERE name=?", (name,))
    db.conn.commit()
    if bao == BAO_KEPT:
        detail = (f"老内臣{office}{name}（{age}岁）于佛前供奉宝匣、日诵往生，"
                  "私望来世得全尸、还阳做个完整之人。宫中老监闻之，多有戚戚。")
    else:
        detail = (f"宫中传：老内臣{office}{name}（{age}岁）年高阳气复生、「还阳」之兆——"
                  "无稽之谈，然老监无宝者闻之，竟有羡叹焚香求之者。")
    return [{"level": LEVEL_BLUE, "kind": "eunuch_reincarnation",
             "title": f"内廷异闻：{name}与「还阳」",
             "detail": detail, "ref_kind": "character", "ref_id": name, "day": day}]


def _complication_options(lore: Dict[str, object], *, adult: bool) -> List[str]:
    options: List[str] = []
    if str(lore.get("urinary_aftereffect") or "").strip():
        options.append("urinary")
    if str(lore.get("trauma_response") or "").strip():
        options.append("trauma")
    if str(lore.get("voice_body_change") or "").strip():
        options.append("body")
    if str(lore.get("bao_ritual") or "").strip() or str(lore.get("bao_status") or "") in {BAO_FORFEIT, BAO_LOST}:
        options.append("bao")
    fixation = str(lore.get("private_fixation") or "")
    if fixation and (adult or not re.search(r"受罚|束缚|羞辱|调教|畸恋|情欲|肉欲|性", fixation)):
        options.append("fixation")
    if adult and str(lore.get("psychosexual_state") or "").strip():
        options.append("psychosexual")
    return options


_COMPLICATION_CARE_TRAITS = {
    "urinary": {"旧患调养", "御前调养"},
    "trauma": {"惊创抚慰", "御前调养"},
    "body": {"仪态修整", "御前调养"},
    "bao": {"宝匣安置", "御前调养"},
    "fixation": {"心癖安顿", "御前调养"},
    "psychosexual": {"心相安顿", "御前调养"},
}


def _complication_mitigated(kind: str, traits: set[str]) -> bool:
    care_traits = _COMPLICATION_CARE_TRAITS.get(str(kind or ""), {"御前调养"})
    return bool(traits.intersection(care_traits))


def _apply_complication_effect(db: GameDB, name: str, kind: str, lore: Dict[str, object]) -> Dict[str, object]:
    row = db.conn.execute(
        "SELECT emp_trust, grievance, ability, wisdom, charm, luck FROM characters WHERE name=?",
        (name,),
    ).fetchone()
    if row is None:
        return {}
    delta = {"emp_trust": 0, "grievance": 0, "ability": 0, "wisdom": 0, "charm": 0, "luck": 0}
    if kind == "urinary":
        delta.update({"grievance": 2, "ability": -1, "charm": -1})
    elif kind == "trauma":
        delta.update({"emp_trust": -1, "grievance": 3, "wisdom": -1})
    elif kind == "body":
        delta.update({"grievance": 1, "charm": -1})
    elif kind == "bao":
        if str(lore.get("bao_status") or "") == BAO_KEPT:
            delta.update({"emp_trust": 1, "grievance": -1, "wisdom": 1})
        else:
            delta.update({"emp_trust": -1, "grievance": 2})
    elif kind == "fixation":
        delta.update({"emp_trust": 1, "grievance": -1})
    elif kind == "psychosexual":
        delta.update({"grievance": 1, "charm": -1})
    scheme = castration_scheme_profile(lore)
    if bool(scheme.get("explicit")) and int(scheme.get("risk_score") or 0) >= 62:
        traits = _trait_names(db, name)
        mitigated = _complication_mitigated(kind, traits)
        risk_score = int(scheme.get("risk_score") or 0)
        pressure = 1 + (1 if risk_score >= 78 else 0) + (1 if risk_score >= 90 else 0)
        if mitigated:
            pressure = max(0, pressure - 1)
        if pressure:
            if kind == "urinary":
                delta["grievance"] += pressure
                delta["ability"] -= 1 if pressure >= 2 else 0
            elif kind == "trauma":
                delta["grievance"] += pressure + 1
                delta["emp_trust"] -= 1
                delta["wisdom"] -= 1 if pressure >= 2 else 0
            elif kind == "body":
                delta["grievance"] += pressure
                delta["charm"] -= 1
            elif kind == "bao":
                delta["grievance"] += pressure if str(lore.get("bao_status") or "") != BAO_KEPT else 0
                delta["emp_trust"] -= 1 if str(lore.get("bao_status") or "") == BAO_FORFEIT and pressure >= 2 else 0
            elif kind == "psychosexual":
                delta["grievance"] += pressure
                delta["charm"] -= 1 if pressure >= 2 else 0
            else:
                delta["grievance"] += pressure
    before = {key: int(row[key] or (55 if key == "emp_trust" else 20 if key == "grievance" else 50)) for key in delta}
    after = {key: _clamp_0_100(before[key] + delta[key]) for key in delta}
    db.conn.execute(
        """
        UPDATE characters
        SET emp_trust=?, grievance=?, ability=?, wisdom=?, charm=?, luck=?
        WHERE name=?
        """,
        (after["emp_trust"], after["grievance"], after["ability"], after["wisdom"], after["charm"], after["luck"], name),
    )
    return {
        "before": before,
        "after": after,
        "delta": {key: after[key] - before[key] for key in delta if after[key] != before[key]},
    }


def _complication_text(kind: str, name: str, lore: Dict[str, object]) -> Dict[str, str]:
    if kind == "urinary":
        symptom = str(lore.get("urinary_aftereffect") or "小解不畅")
        return {
            "title": f"内廷旧患：{name}尿路发作",
            "detail": f"{name}当值时旧患忽发，{symptom}，复命慢了半刻。若久置不管，近侍差遣会越发吃力。",
            "stage": "夹腿缩腰，袖口压着下腹，回话比平日更短。",
            "process": symptom,
        }
    if kind == "trauma":
        symptom = str(lore.get("trauma_response") or "旧梦惊醒")
        return {
            "title": f"净房旧梦：{name}一时失神",
            "detail": f"宫中器具相碰，{name}{symptom}。小内侍们看在眼里，私下不敢多问。",
            "stage": "闻声一僵，眼神空了片刻，随即叩首请罪。",
            "process": symptom,
        }
    if kind == "body":
        symptom = str(lore.get("voice_body_change") or "体态有异")
        return {
            "title": f"内廷失仪：{name}体声露怯",
            "detail": f"{name}奏事急了些，{symptom}，被旁人记下一笔笑柄。",
            "stage": "嗓音一尖，肩背微缩，马上把话咽回去。",
            "process": symptom,
        }
    if kind == "bao":
        ritual = str(lore.get("bao_ritual") or "")
        if str(lore.get("bao_status") or "") == BAO_KEPT:
            detail = f"{name}夜里验看宝匣，{ritual or '默念全尸旧愿'}，心神稍定。"
        elif str(lore.get("bao_status") or "") == BAO_FORFEIT:
            detail = f"{name}听见官库封签，想起宝为官没之辱，半日不肯多话。"
        else:
            detail = f"{name}又问旧匣下落，因宝已无凭，神色颓然。"
        return {
            "title": f"宝匣心结：{name}",
            "detail": detail,
            "stage": "手指在袖中摸索钥匙或封签，旋即垂手。",
            "process": ritual or str(lore.get("bao_label") or ""),
        }
    if kind == "psychosexual":
        symptom = str(lore.get("psychosexual_state") or "癖性心结")
        return {
            "title": f"隐癖扰心：{name}",
            "detail": f"{name}近来心相更偏，{symptom}。这不是大案，却会改变他近侍时的胆怯、逢迎与怨气。",
            "stage": "听见规训二字，先低头，随后又急急称奴婢该死。",
            "process": symptom,
        }
    fixation = str(lore.get("private_fixation") or "心结发作")
    return {
        "title": f"内廷怪癖：{name}",
        "detail": f"{name}{fixation}，借此压住旧创惊悸。近侍们渐渐知道他这点毛病。",
        "stage": "反复抚平衣褶或摸索封匣钥匙，像在给自己定神。",
        "process": fixation,
    }


def _ensure_complication_goal(
    db: GameDB,
    state: GameState,
    name: str,
    kind: str,
    text: Dict[str, str],
    effect: Dict[str, object],
) -> int:
    """Turn a castration complication into a trackable audience request."""

    clean_name = str(name or "").strip()
    if not clean_name or not hasattr(db, "create_conversation_goal"):
        return 0
    existing = db.conn.execute(
        """
        SELECT id FROM conversation_goals
        WHERE minister_name=?
          AND action_kind='eunuch_care'
          AND status IN ('active','waiting_conditions','blocked')
        ORDER BY id DESC LIMIT 1
        """,
        (clean_name,),
    ).fetchone()
    if existing is not None:
        goal_id = int(existing["id"] or 0)
        db.add_conversation_goal_event(
            state,
            goal_id,
            "complication_flare",
            status="waiting_conditions",
            score_delta=4,
            score_after=0,
            summary=str(text.get("title") or "净身旧患复发")[:180],
            payload={
                "source": "eunuch_complication",
                "complication": kind,
                "effect": effect.get("delta") if isinstance(effect, dict) else {},
                "stage_direction": str(text.get("stage") or ""),
            },
            commit=False,
        )
        return goal_id
    label_map = {
        "urinary": "尿路调养",
        "trauma": "惊创抚慰",
        "body": "体声修整",
        "bao": "宝匣安置",
        "fixation": "心癖安顿",
        "psychosexual": "心相安顿",
    }
    label = label_map.get(kind, "内廷调养")
    title = f"{label}求助：{clean_name}"
    target_text = (
        f"{clean_name}因「{text.get('title') or '净身旧患'}」主动候见。"
        "召对时应让他说清旧患、宝匣或差遣风险，再决定动内库调养、验宝安置，"
        "或明示仍要强派办差并承担误事风险。"
    )
    risk_tags = ["净身旧患", label]
    delta = effect.get("delta") if isinstance(effect, dict) else {}
    if isinstance(delta, dict) and delta:
        risk_tags.append("属性波动")
    conditions = [
        {"description": f"召见{clean_name}，听其亲口说明{label}所求。", "status": "pending"},
        {"description": "选择调养/宝匣安置/验宝查案，或明示暂不理会、仍照常派差。", "status": "pending"},
    ]
    blockers = [
        "内库小耗与司礼监旧档会留下痕迹。",
        "若久置不理，旧患还会继续扰动差遣、信任与怨望。",
    ]
    return db.create_conversation_goal(
        state,
        minister_name=clean_name,
        action_kind="eunuch_care",
        title=title,
        target_text=target_text,
        status="waiting_conditions",
        condition_status="pending",
        threshold=70,
        score=35,
        conditions=conditions,
        blockers=blockers,
        expires_turn=int(getattr(state, "turn", 0) or 0) + 2,
        last_delta={
            "source": "eunuch_complication",
            "complication": kind,
            "public_hint": target_text,
            "risk_tags": risk_tags,
            "stage_direction": str(text.get("stage") or ""),
            "court_decision": {"action": "eunuch_care", "mode": kind},
        },
    )


def castration_complication_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """日 tick：净身后遗症/宝匣心结低频发作，真实扰动人物状态。

    这是游戏性结算，不是纯展示：每次触发会改变信任、怨望或人物属性，并写入事件记忆。
    """
    from ming_sim.timeflow import LEVEL_BLUE
    ensure_schema(db)
    day = int(day or 0)
    regular_window = day % 6 == 3
    scheme_surge_window = day % 6 == 1
    if day <= 0 or not (regular_window or scheme_surge_window):
        return []
    rows = db.conn.execute(
        """
        SELECT l.name
        FROM eunuch_lore l
        JOIN characters c ON c.name=l.name
        WHERE c.status='active' AND c.power_id='ming'
        ORDER BY l.name
        """
    ).fetchall()
    candidates: List[tuple[int, str, str, Dict[str, object], Dict[str, object], bool]] = []
    for row in rows:
        name = str(row["name"] or "")
        lore = get_lore(db, name) or {}
        adult = _is_adult_for_lore(db, name)
        options = _complication_options(lore, adult=adult)
        if not options:
            continue
        scheme = castration_scheme_profile(lore)
        scheme_surge = False
        if regular_window:
            candidate_options = options
        else:
            risk_score = int(scheme.get("risk_score") or 0)
            if not bool(scheme.get("explicit")) or risk_score < 72:
                continue
            traits = _trait_names(db, name)
            candidate_options = [kind for kind in options if not _complication_mitigated(kind, traits)]
            if not candidate_options:
                continue
            scheme_surge = True
        seed = sum(ord(ch) for ch in name) + day * 17
        kind = candidate_options[seed % len(candidate_options)]
        priority = (seed % 997) + (300 if scheme_surge else 0) + max(0, int(scheme.get("risk_score") or 0) - 72)
        candidates.append((priority, name, kind, lore, scheme, scheme_surge))
    candidates.sort(reverse=True)
    for _, name, kind, lore, scheme, scheme_surge in candidates:
        source_id = f"{day}:{name}:{kind}:{'scheme' if scheme_surge else 'regular'}"
        exists = db.conn.execute(
            """
            SELECT 1 FROM event_memories
            WHERE subject_type='character' AND subject_id=? AND event_type='eunuch_complication'
              AND source_kind='timeflow' AND source_id=?
            """,
            (name, source_id),
        ).fetchone()
        if exists is not None:
            continue
        effect = _apply_complication_effect(db, name, kind, lore)
        text = _complication_text(kind, name, lore)
        scheme_tags: List[str] = []
        if bool(scheme.get("explicit")):
            tier = str(scheme.get("tier") or "").strip()
            if tier:
                scheme_tags.append(tier)
            if scheme_surge:
                text["detail"] = (
                    f"{text['detail']}（净身方案压着旧患，未调养便提前发作；"
                    f"方案画像：{tier or '高危'}，风险{int(scheme.get('risk_score') or 0)}。）"
                )
            elif int(scheme.get("risk_score") or 0) >= 62:
                text["detail"] = f"{text['detail']}（净身方案旧患留痕明显。）"
        goal_id = _ensure_complication_goal(db, state, name, kind, text, effect)
        outcome_bits = []
        for key, label in (
            ("emp_trust", "信任"),
            ("grievance", "怨望"),
            ("ability", "才干"),
            ("wisdom", "机敏"),
            ("charm", "仪表"),
            ("luck", "运势"),
        ):
            delta = int((effect.get("delta") or {}).get(key) or 0) if isinstance(effect, dict) else 0
            if delta:
                outcome_bits.append(f"{label}{delta:+d}")
        if scheme_surge:
            outcome_bits.append("方案压迫")
        outcome = "，".join(outcome_bits) or "只留内廷传闻"
        tags = ["净身", "旧患", "宝匣", kind, *scheme_tags]
        if scheme_surge:
            tags.append("方案压迫")
        db.upsert_event_memory(
            state,
            "character",
            name,
            "eunuch_complication",
            text["title"],
            cause="净身旧患/宝匣心结发作",
            process=text["process"],
            outcome=outcome,
            sentiment="negative" if kind in {"urinary", "trauma", "body", "psychosexual"} else "mixed",
            importance=4 if scheme_surge else 3,
            tags=tags,
            source_kind="timeflow",
            source_id=source_id,
        )
        db.record_log(state, f"【净身旧患】{text['title']}：{outcome}。")
        db.conn.commit()
        return [{
            "level": LEVEL_BLUE,
            "kind": "eunuch_complication",
            "complication": kind,
            "title": text["title"],
            "detail": text["detail"],
            "stage_direction": text["stage"],
            "effect": outcome,
            "goal_id": goal_id,
            "scheme_surge": scheme_surge,
            "scheme_profile": scheme if bool(scheme.get("explicit")) else {},
            "ref_kind": "character",
            "ref_id": name,
            "day": day,
        }]
    return []


def _bao_instability_score(lore: Dict[str, object], traits: set[str]) -> int:
    bao = str(lore.get("bao_status") or "")
    preservation = str(lore.get("bao_preservation") or "")
    container = str(lore.get("bao_container") or "")
    ritual = str(lore.get("bao_ritual") or "")
    note = str(lore.get("note") or "")
    score = 0
    if bao == BAO_FORFEIT:
        score += 38
    elif bao == BAO_LOST:
        score += 44
    elif bao == BAO_KEPT:
        score += 8
    if re.search(r"官库|封签|刑房|案袋|灰瓮|旧案", f"{preservation} {container} {ritual} {note}"):
        score += 14
    if re.search(r"粗木|白签|灰瓮|铁皮|旧案匣|破锦|无名|失签|旧布", container):
        score += 12
    if re.search(r"失匣|散失|遗失|霉坏|无凭|卷匣而逃", f"{preservation} {container} {ritual} {note}"):
        score += 18
    if re.search(r"打听|下落|忌听|失神|终身惦念|问旧匣", ritual):
        score += 10
    if re.search(r"黑漆楠木|黄杨|锡胆|杉木|描金|钥匙贴身|佛龛|供奉", f"{container} {ritual}"):
        score -= 8
    scheme = castration_scheme_profile(lore)
    if bool(scheme.get("explicit")) and int(scheme.get("risk_score") or 0) >= 72:
        score += 6
    if "宝案钳制" in traits:
        score += 22
    if "御赐宝匣" in traits:
        score -= 80
    elif traits.intersection({"宝匣安置", "御前调养"}):
        score -= 60
    return max(0, min(100, int(score)))


def _apply_bao_instability_effect(db: GameDB, name: str, lore: Dict[str, object], score: int) -> Dict[str, object]:
    row = db.conn.execute(
        "SELECT emp_trust, grievance, ability, wisdom, charm, luck FROM characters WHERE name=?",
        (name,),
    ).fetchone()
    if row is None:
        return {}
    bao = str(lore.get("bao_status") or "")
    delta = {"emp_trust": 0, "grievance": 0, "ability": 0, "wisdom": 0, "charm": 0, "luck": 0}
    if bao == BAO_FORFEIT:
        delta.update({"emp_trust": -1, "grievance": 3, "wisdom": -1 if score >= 58 else 0})
    elif bao == BAO_LOST:
        delta.update({"emp_trust": -1, "grievance": 2, "luck": -1})
    else:
        delta.update({"grievance": 1, "wisdom": 1 if score < 36 else 0})
    if score >= 70:
        delta["grievance"] += 1
        delta["luck"] -= 1
    before = {key: int(row[key] or (55 if key == "emp_trust" else 20 if key == "grievance" else 50)) for key in delta}
    after = {key: _clamp_0_100(before[key] + delta[key]) for key in delta}
    db.conn.execute(
        """
        UPDATE characters
        SET emp_trust=?, grievance=?, ability=?, wisdom=?, charm=?, luck=?
        WHERE name=?
        """,
        (after["emp_trust"], after["grievance"], after["ability"], after["wisdom"], after["charm"], after["luck"], name),
    )
    return {
        "before": before,
        "after": after,
        "delta": {key: after[key] - before[key] for key in delta if after[key] != before[key]},
    }


def _bao_instability_text(name: str, lore: Dict[str, object], score: int) -> Dict[str, str]:
    bao = str(lore.get("bao_status") or "")
    preservation = str(lore.get("bao_preservation") or "")
    container = str(lore.get("bao_container") or "")
    ritual = str(lore.get("bao_ritual") or "")
    if bao == BAO_FORFEIT:
        return {
            "title": f"宝案风声：{name}官库封签走漏",
            "detail": (
                f"{name}听闻官库有人翻看旧封签，想起宝为官没之辱。"
                f"旧案所记：{preservation or '封存未详'}，{container or '匣器未详'}。"
                "若不查验安置，内廷会把这桩羞辱当作拿捏他的把柄。"
            ),
            "stage": "手指在袖中一僵，听见封签二字便低头不语。",
            "process": f"官库封签扰动；风险{score}",
        }
    if bao == BAO_LOST:
        return {
            "title": f"宝匣无凭：{name}旧匣下落成疑",
            "detail": (
                f"{name}又听见有人说起旧匣下落，{preservation or '遗失无凭'}。"
                "真伪未明，却足以搅动他的全尸执念。"
            ),
            "stage": "他下意识摸袖中空处，神色一下黯了。",
            "process": f"旧匣线索扰动；风险{score}",
        }
    return {
        "title": f"宝匣失安：{name}夜验旧匣",
        "detail": (
            f"{name}夜里验看宝匣，{ritual or '默念全尸旧愿'}；"
            f"{preservation or '保存未详'}，{container or '匣器未详'}。"
            "宝匣尚在，心神稍定，但越在意，越怕人知晓。"
        ),
        "stage": "他摸了摸袖中钥匙，像把一口乱气按回去。",
        "process": f"宝匣供奉扰动；风险{score}",
    }


def bao_instability_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """低频宝匣/官库封签事件：让宝的处置成为长期风险源，可用宝匣安置压住。"""
    from ming_sim.timeflow import LEVEL_BLUE
    ensure_schema(db)
    day = int(day or 0)
    if day <= 0 or day % 10 != 6:
        return []
    rows = db.conn.execute(
        """
        SELECT l.name
        FROM eunuch_lore l
        JOIN characters c ON c.name=l.name
        WHERE c.status='active' AND c.power_id='ming'
        ORDER BY l.name
        """
    ).fetchall()
    candidates: List[tuple[int, int, str, Dict[str, object]]] = []
    for row in rows:
        name = str(row["name"] or "")
        lore = get_lore(db, name) or {}
        score = _bao_instability_score(lore, _trait_names(db, name))
        if score < 24:
            continue
        seed = sum(ord(ch) for ch in f"{name}:bao") + day * 23
        candidates.append((score * 10 + seed % 997, score, name, lore))
    candidates.sort(reverse=True)
    for _, score, name, lore in candidates:
        source_id = f"{day}:{name}:bao:{score}"
        exists = db.conn.execute(
            """
            SELECT 1 FROM event_memories
            WHERE subject_type='character' AND subject_id=? AND event_type='eunuch_bao_instability'
              AND source_kind='timeflow' AND source_id=?
            """,
            (name, source_id),
        ).fetchone()
        if exists is not None:
            continue
        text = _bao_instability_text(name, lore, score)
        effect = _apply_bao_instability_effect(db, name, lore, score)
        goal_id = _ensure_complication_goal(db, state, name, "bao", text, effect)
        outcome_bits = []
        for key, label in (
            ("emp_trust", "信任"),
            ("grievance", "怨望"),
            ("ability", "才干"),
            ("wisdom", "机敏"),
            ("charm", "仪表"),
            ("luck", "运势"),
        ):
            delta = int((effect.get("delta") or {}).get(key) or 0) if isinstance(effect, dict) else 0
            if delta:
                outcome_bits.append(f"{label}{delta:+d}")
        outcome = "，".join(outcome_bits) or "只留宝案风声"
        db.upsert_event_memory(
            state,
            "character",
            name,
            "eunuch_bao_instability",
            text["title"],
            cause="宝匣/官库封签失安",
            process=text["process"],
            outcome=outcome,
            sentiment="negative" if str(lore.get("bao_status") or "") != BAO_KEPT else "mixed",
            importance=4 if score >= 56 else 3,
            tags=["净身", "宝匣", "封签", str(lore.get("bao_status") or "")],
            source_kind="timeflow",
            source_id=source_id,
        )
        db.record_log(state, f"【宝匣失安】{text['title']}：{outcome}。")
        db.conn.commit()
        return [{
            "level": LEVEL_BLUE,
            "kind": "eunuch_bao_instability",
            "title": text["title"],
            "detail": text["detail"],
            "stage_direction": text["stage"],
            "effect": outcome,
            "goal_id": goal_id,
            "bao_risk": score,
            "ref_kind": "character",
            "ref_id": name,
            "day": day,
        }]
    return []


def _secret_order_task_domains(order: Dict[str, object]) -> List[str]:
    text = " ".join(
        str(part or "")
        for part in (
            order.get("title"),
            order.get("content"),
            " ".join(str(tag) for tag in (order.get("tags") or [])),
            order.get("result"),
            order.get("sim_note"),
        )
    )
    domains = ["investigation"]
    if re.search(r"内廷|司礼监|东厂|宝匣|封签|官库|净房|净军", text):
        domains.append("inner")
    if re.search(r"边镇|军|营|辽东|山西|陕西|兵|饷", text):
        domains.append("military")
    if re.search(r"地方|州|县|府|乡绅|粮长|民|田|税|盐", text):
        domains.append("local")
    return list(dict.fromkeys(domains))


def _secret_order_task_text(order: Dict[str, object]) -> str:
    return "\n".join(
        part
        for part in (
            str(order.get("title") or "").strip(),
            str(order.get("content") or "").strip(),
            " ".join(str(tag) for tag in (order.get("tags") or []) if str(tag).strip()),
            str(order.get("result") or "").strip(),
            str(order.get("sim_note") or "").strip(),
        )
        if part
    )


def _secret_order_has_old_wound_line(order: Dict[str, object], state: GameState) -> bool:
    stamp = f"〔{period_label(state.year, state.period)}〕[旧患拖累]"
    return any(str(line).startswith(stamp) for line in str(order.get("sim_note") or "").split("\n"))


def _secret_order_has_protective_strategy(order: Dict[str, object]) -> bool:
    text = str(order.get("sim_note") or "")
    if re.search(r"照旧强派|不许退缩|限期硬查|硬查硬办|强派", text):
        return False
    return bool(re.search(r"旧患差遣|副手|分班|轮值|绕开刑房|绕开净房|外围文书|先调养再派|先调养", text))


def _append_secret_order_old_wound_line(
    db: GameDB,
    state: GameState,
    order_id: int,
    note: str,
    *,
    delay_due: bool,
) -> int:
    row = db.conn.execute(
        "SELECT sim_note, due_turn FROM secret_orders WHERE id=? AND status='active'",
        (int(order_id),),
    ).fetchone()
    if row is None:
        return 0
    stamp = f"〔{period_label(state.year, state.period)}〕[旧患拖累]"
    lines = [line for line in str(row["sim_note"] or "").split("\n") if line.strip()]
    if any(line.startswith(stamp) for line in lines):
        return int(row["due_turn"] or 0)
    lines.append(f"{stamp} {str(note or '').strip()[:300]}")
    due_before = int(row["due_turn"] or 0)
    due_after = due_before
    if delay_due and due_before > 0:
        due_after = max(due_before + 1, int(getattr(state, "turn", 0) or 0) + 1)
    db.conn.execute(
        """
        UPDATE secret_orders
        SET sim_note=?, due_turn=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        ("\n".join(lines), due_after, int(order_id)),
    )
    db.conn.commit()
    return due_after


def secret_order_old_wound_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """进行中密令的净身旧患回流。

    当宦官带着漏尿、惊创、宝匣心结去办久候、刑房、封签、内廷查验类密令时，
    旧患会写进密令进度线，重者顺延期限。玩家若已经用副手轮值、绕开触发或先调养
    处理过，事件不会再以同样方式拖慢。
    """

    from ming_sim.timeflow import LEVEL_BLUE, LEVEL_YELLOW

    ensure_schema(db)
    day = int(day or 0)
    if day <= 0 or day % 7 != 4:
        return []
    orders = db.list_secret_orders(status="active")
    candidates: List[tuple[int, Dict[str, object], Dict[str, object], Dict[str, object], List[str]]] = []
    for order in orders:
        name = str(order.get("minister_name") or "").strip()
        lore = get_lore(db, name)
        if not name or lore is None:
            continue
        row = db.conn.execute(
            "SELECT status, power_id FROM characters WHERE name=?",
            (name,),
        ).fetchone()
        if row is None or str(row["status"] or "") != "active" or str(row["power_id"] or "ming") != "ming":
            continue
        if _secret_order_has_old_wound_line(order, state) or _secret_order_has_protective_strategy(order):
            continue
        task = _secret_order_task_text(order)
        domains = _secret_order_task_domains(order)
        risk = assignment_risk_profile(db, name, task, domains=domains)
        if not risk:
            continue
        score_delta = int(risk.get("score_delta") or 0)
        if score_delta > -6:
            continue
        severity = abs(score_delta) * 10 + int(order.get("importance") or 0) * 3
        if int(order.get("due_turn") or 0) and int(order.get("due_turn") or 0) <= int(getattr(state, "turn", 0) or 0) + 1:
            severity += 18
        candidates.append((severity, order, lore, risk, domains))
    candidates.sort(key=lambda item: item[0], reverse=True)
    for _, order, lore, risk, domains in candidates:
        order_id = int(order.get("id") or 0)
        name = str(order.get("minister_name") or "").strip()
        score_delta = int(risk.get("score_delta") or 0)
        source_id = f"{day}:{order_id}:old_wound"
        exists = db.conn.execute(
            """
            SELECT 1 FROM event_memories
            WHERE subject_type='secret_order' AND subject_id=? AND event_type='eunuch_secret_order_old_wound'
              AND source_kind='timeflow' AND source_id=?
            """,
            (str(order_id), source_id),
        ).fetchone()
        if exists is not None:
            continue
        risks = [str(item) for item in (risk.get("risks") or []) if str(item).strip()]
        stages = [str(item) for item in (risk.get("stage_cues") or []) if str(item).strip()]
        primary_risk = risks[0] if risks else str(risk.get("note") or "净身旧患牵动密令")
        stage = stages[0] if stages else "退到廊下定了定神，才敢继续回话。"
        delay_due = score_delta <= -9
        due_before = int(order.get("due_turn") or 0)
        note = (
            f"{name}办「{str(order.get('title') or '密令')}」时旧患发作：{primary_risk}"
            + ("；限期顺延一月。" if delay_due and due_before else "；进度线留下风险。")
        )
        due_after = _append_secret_order_old_wound_line(db, state, order_id, note, delay_due=delay_due)
        mode = _primary_care_mode_for_task(risk)
        text = {
            "title": f"密令旧患：{name}差事受阻",
            "detail": (
                f"{name}承办密令「{str(order.get('title') or '')}」时，净身旧患牵动执行。"
                f"{primary_risk} 若不调养或改派副手，此类密差会继续拖慢。"
            ),
            "stage": stage,
            "process": primary_risk,
        }
        goal_id = _ensure_complication_goal(db, state, name, mode, text, {"delta": {}})
        outcome_bits = [f"密令修正{score_delta}"]
        if delay_due and due_before:
            outcome_bits.append(f"期限{due_before}->{due_after}")
        outcome = "，".join(outcome_bits)
        db.upsert_event_memory(
            state,
            "secret_order",
            str(order_id),
            "eunuch_secret_order_old_wound",
            text["title"],
            cause=f"{name}净身旧患牵动密令",
            process=primary_risk,
            outcome=outcome,
            sentiment="negative",
            importance=4 if delay_due else 3,
            tags=["净身", "密令", "旧患", *domains[:3]],
            source_kind="timeflow",
            source_id=source_id,
        )
        db.record_log(state, f"【密令旧患】{text['title']}：{outcome}。")
        db.conn.commit()
        return [{
            "level": LEVEL_YELLOW if delay_due and due_before else LEVEL_BLUE,
            "kind": "eunuch_secret_order_old_wound",
            "title": text["title"],
            "detail": text["detail"],
            "stage_direction": stage,
            "effect": outcome,
            "order_id": order_id,
            "goal_id": goal_id,
            "risk": risk,
            "ref_kind": "secret_order",
            "ref_id": str(order_id),
            "day": day,
        }]
    return []


_CARE_MODE_ALIASES = {
    "urinary": "urinary",
    "尿路": "urinary",
    "漏尿": "urinary",
    "尿闭": "urinary",
    "石淋": "urinary",
    "小解": "urinary",
    "trauma": "trauma",
    "惊创": "trauma",
    "幻肢": "trauma",
    "噩梦": "trauma",
    "ptsd": "trauma",
    "体声": "body",
    "body": "body",
    "嗓音": "body",
    "体态": "body",
    "仪态": "body",
    "bao": "bao",
    "宝": "bao",
    "宝匣": "bao",
    "验宝": "bao",
    "官库": "bao",
    "全尸": "bao",
    "fixation": "fixation",
    "隐癖": "fixation",
    "怪癖": "fixation",
    "洁净": "fixation",
    "调养": "general",
    "医治": "general",
    "general": "general",
}


def normalize_care_mode(mode: str, hint: str = "") -> str:
    raw = f"{mode or ''} {hint or ''}".strip().lower()
    for key, value in _CARE_MODE_ALIASES.items():
        if key.lower() in raw:
            return value
    if re.search(r"尿|漏|石淋|小解", raw):
        return "urinary"
    if re.search(r"幻肢|噩梦|PTSD|ptsd|惊|刀声|按肩|压惊", raw):
        return "trauma"
    if re.search(r"嗓|体态|仪态|肩背|步子", raw):
        return "body"
    if re.search(r"宝|匣|钥匙|官库|全尸|供奉|封签", raw):
        return "bao"
    if re.search(r"洁净|衣褶|香囊|规训|束带", raw):
        return "fixation"
    return "general"


def _care_plan(mode: str, lore: Dict[str, object], *, adult: bool) -> Dict[str, object]:
    mode = normalize_care_mode(mode)
    if mode == "urinary":
        return {
            "mode": mode,
            "label": "尿路调养",
            "cost": 3,
            "trait": "旧患调养",
            "delta": {"emp_trust": 2, "grievance": -6, "ability": 1, "charm": 1},
            "process": str(lore.get("urinary_aftereffect") or "遣太医以热砖、汤药调理小解旧患"),
            "stage": "垂手夹腰叩谢，回话仍短，却明显松了一口气。",
        }
    if mode == "trauma":
        return {
            "mode": mode,
            "label": "惊创抚慰",
            "cost": 2,
            "trait": "惊创抚慰",
            "delta": {"emp_trust": 3, "grievance": -5, "wisdom": 1},
            "process": str(lore.get("trauma_response") or "命熟内侍避开刀声净房旧话，以香汤压惊"),
            "stage": "听见不再追问净房二字，肩背慢慢放低。",
        }
    if mode == "body":
        return {
            "mode": mode,
            "label": "体声修整",
            "cost": 2,
            "trait": "仪态修整",
            "delta": {"emp_trust": 1, "grievance": -3, "charm": 1},
            "process": str(lore.get("voice_body_change") or "令内书堂教其收声、缓步、少久跪"),
            "stage": "先尖声应是，旋即压低嗓子，谨慎退半步。",
        }
    if mode == "bao":
        kept = str(lore.get("bao_status") or "") == BAO_KEPT
        return {
            "mode": mode,
            "label": "宝匣安置" if kept else "宝案查验",
            "cost": 2,
            "trait": "宝匣安置",
            "delta": {"emp_trust": 2 if kept else 1, "grievance": -5 if kept else -3, "wisdom": 1},
            "process": str(lore.get("bao_ritual") or ("赐香料修匣、重封钥匙" if kept else "命官库查封签、补录宝案去处")),
            "stage": "手在袖中摸了摸钥匙或封签，额头伏得更低。",
        }
    if mode == "fixation":
        fixation = str(lore.get("private_fixation") or "以规矩压住旧创心悸")
        if not adult and re.search(r"受罚|束缚|羞辱|调教|畸恋|情欲|肉欲|性", fixation):
            fixation = "以洁净衣褶、香囊压住旧创心悸"
        return {
            "mode": mode,
            "label": "心癖安顿",
            "cost": 1,
            "trait": "心癖安顿",
            "delta": {"emp_trust": 1, "grievance": -3},
            "process": fixation,
            "stage": "反复抚平衣褶，像把一口乱气也一并按住。",
        }
    return {
        "mode": "general",
        "label": "内廷调养",
        "cost": 4,
        "trait": "御前调养",
        "delta": {"emp_trust": 2, "grievance": -4, "ability": 1},
        "process": "命太医看旧创，内库支药，司礼监记一笔调养档",
        "stage": "叩首称谢，口里仍说奴婢不敢劳动陛下。",
    }


_BAO_LEVERAGE_ALIASES = {
    "return": "return",
    "赐还": "return",
    "归还": "return",
    "发还": "return",
    "交还": "return",
    "还给": "return",
    "自藏": "return",
    "自己收": "return",
    "钥匙给": "return",
    "control": "control",
    "钳制": "control",
    "拿捏": "control",
    "把柄": "control",
    "官库封存": "control",
    "押在官库": "control",
    "收着他的宝": "control",
    "不赐还": "control",
    "封签拿住": "control",
    "以宝制": "control",
}


def normalize_bao_leverage_mode(mode: str, hint: str = "") -> str:
    raw = f"{mode or ''} {hint or ''}".strip()
    if re.search(r"钳制|拿捏|把柄|官库封存|押在官库|不赐还|封签拿住|以宝制|收着.{0,6}宝", raw):
        return "control"
    for key, value in _BAO_LEVERAGE_ALIASES.items():
        if key and key in raw:
            return value
    if re.search(r"赐还|归还|发还|交还|还给|自藏|自己收|钥匙给|还他全尸|全尸", raw):
        return "return"
    return "return"


def _bao_leverage_note(existing: str, addition: str) -> str:
    parts = [str(existing or "").strip(), str(addition or "").strip()]
    text = "；".join(part for part in parts if part)
    return text[:320]


def apply_bao_leverage(
    db: GameDB,
    state: GameState,
    name: str,
    *,
    mode: str = "return",
    note: str = "",
    source: str = "dialogue",
) -> Dict[str, object]:
    """对白驱动宝匣筹码：赐还收心，或官库封存拿捏。

    这是策略结算，不是护理：同一只宝匣既可换忠心，也可作把柄。
    """
    ensure_schema(db)
    clean_name = str(name or "").strip()
    if not clean_name:
        return {"ok": False, "reason": "未点明宝匣所系之人。"}
    lore = get_lore(db, clean_name)
    if lore is None:
        return {"ok": False, "reason": f"{clean_name}没有净身旧档。"}
    row = db.conn.execute(
        "SELECT emp_trust, grievance, ability, wisdom, charm, luck FROM characters WHERE name=? AND status='active'",
        (clean_name,),
    ).fetchone()
    if row is None:
        return {"ok": False, "reason": f"{clean_name}不在可处置名册中。"}
    resolved_mode = normalize_bao_leverage_mode(mode, note)
    source_id = f"{int(state.turn)}:{clean_name}:{resolved_mode}:{source}"
    existed = db.conn.execute(
        """
        SELECT 1 FROM event_memories
        WHERE subject_type='character' AND subject_id=? AND event_type='eunuch_bao_leverage'
          AND source_kind=? AND source_id=?
        """,
        (clean_name, source, source_id),
    ).fetchone()
    if existed is not None:
        return {"ok": False, "reason": f"{clean_name}本回合宝案已处置。", "mode": resolved_mode}
    try:
        day = int(getattr(state, "turn", 0) or 0) * 30
    except Exception:
        day = 0
    lore_update: Dict[str, str] = {}
    if str(note or "").strip():
        updated = update_lore_from_text(db, clean_name, note, day=day)
        if isinstance(updated, dict) and isinstance(updated.get("updated"), dict):
            lore_update = {str(k): str(v) for k, v in updated["updated"].items()}
        lore = get_lore(db, clean_name) or lore
    before = {
        "emp_trust": int(row["emp_trust"] or 55),
        "grievance": int(row["grievance"] or 20),
        "ability": int(row["ability"] or 50),
        "wisdom": int(row["wisdom"] or 50),
        "charm": int(row["charm"] or 50),
        "luck": int(row["luck"] or 50),
    }
    if resolved_mode == "control":
        label = "宝案钳制"
        trait = "宝案钳制"
        bao_status = BAO_FORFEIT
        delta = {"emp_trust": -2, "grievance": 8, "wisdom": -1, "luck": -1}
        preservation = str(lore.get("bao_preservation") or "").strip()
        container = str(lore.get("bao_container") or "").strip()
        if not preservation or not re.search(r"官库|石灰|封存|油炸|香料", preservation):
            preservation = "官库石灰封存"
            lore_update.setdefault("bao_preservation", preservation)
        if not container or not re.search(r"铁皮|灰瓮|官库|白签", container):
            container = "铁皮锁匣"
            lore_update.setdefault("bao_container", container)
        ritual = "官库封签作御前把柄，终身惦念"
        item_id = f"官库宝案把柄：{clean_name}"
        title = f"宝案钳制：{clean_name}"
        cause = "御前以宝案作内廷把柄"
        stage = "听见官库封签，他喉头一紧，叩首更低。"
        outcome_label = "短期威慑"
        sentiment = "negative"
        process = f"{container}；{preservation}；{note}".strip("；")[:160]
        db.conn.execute("DELETE FROM character_traits WHERE name=? AND trait IN ('御赐宝匣','宝匣安置')", (clean_name,))
    else:
        label = "赐还宝匣"
        trait = "御赐宝匣"
        bao_status = BAO_KEPT
        delta = {"emp_trust": 5, "grievance": -9, "wisdom": 1, "luck": 1}
        preservation = str(lore.get("bao_preservation") or "").strip()
        container = str(lore.get("bao_container") or "").strip()
        if not preservation or re.search(r"官库|石灰|封存|灰瓮|白签", preservation):
            preservation = "香料腌藏"
            lore_update.setdefault("bao_preservation", preservation)
        if not container or re.search(r"灰瓮|旧案|白签|粗木|铁皮", container):
            container = "锡胆小木匣"
            lore_update.setdefault("bao_container", container)
        ritual = "御前赐还，钥匙贴身，望来世全尸"
        item_id = f"御赐宝匣：{clean_name}"
        title = f"赐还宝匣：{clean_name}"
        cause = "御前以宝匣收心降怨"
        stage = "他伏地捧匣，指节发颤，半晌才敢谢恩。"
        outcome_label = "收心降怨"
        sentiment = "positive"
        process = f"{container}；{preservation}；{note}".strip("；")[:160]
        db.conn.execute("DELETE FROM character_traits WHERE name=? AND trait='宝案钳制'", (clean_name,))
        db.conn.execute(
            "INSERT OR IGNORE INTO character_traits (name, trait, valence) VALUES (?,?,?)",
            (clean_name, "宝匣安置", 1),
        )
    after = dict(before)
    for key, value in delta.items():
        after[key] = _clamp_0_100(after[key] + int(value or 0))
    db.conn.execute(
        """
        UPDATE characters
        SET emp_trust=?, grievance=?, ability=?, wisdom=?, charm=?, luck=?
        WHERE name=?
        """,
        (after["emp_trust"], after["grievance"], after["ability"], after["wisdom"], after["charm"], after["luck"], clean_name),
    )
    db.conn.execute(
        """
        UPDATE eunuch_lore
        SET bao_status=?, bao_preservation=?, bao_container=?, bao_ritual=?, note=?
        WHERE name=?
        """,
        (
            bao_status,
            preservation,
            container,
            ritual,
            _bao_leverage_note(str(lore.get("note") or ""), note),
            clean_name,
        ),
    )
    lore_update["bao_status"] = bao_status
    lore_update["bao_preservation"] = preservation
    lore_update["bao_container"] = container
    lore_update["bao_ritual"] = ritual
    db.conn.execute(
        "INSERT OR IGNORE INTO character_traits (name, trait, valence) VALUES (?,?,?)",
        (clean_name, trait, 1 if resolved_mode == "return" else -1),
    )
    exists = db.conn.execute("SELECT 1 FROM player_inventory WHERE item_id=?", (item_id,)).fetchone()
    items_added: List[str] = []
    if exists is None:
        db.grant_player_item(item_id, state)
        items_added.append(item_id)
    outcome_bits = [
        f"{label_name}{after[key] - before[key]:+d}"
        for key, label_name in (
            ("emp_trust", "信任"),
            ("grievance", "怨望"),
            ("wisdom", "机敏"),
            ("luck", "运势"),
        )
        if after[key] != before[key]
    ]
    outcome_bits.append(outcome_label)
    outcome = "，".join(outcome_bits)
    db.upsert_event_memory(
        state,
        "character",
        clean_name,
        "eunuch_bao_leverage",
        title,
        cause=cause,
        process=process,
        outcome=outcome,
        sentiment=sentiment,
        importance=4,
        tags=["净身", "宝匣", "筹码", resolved_mode, label],
        source_kind=source,
        source_id=source_id,
    )
    db.record_log(state, f"【宝匣筹码】{title}：{outcome}。")
    db.conn.commit()
    return {
        "ok": True,
        "name": clean_name,
        "mode": resolved_mode,
        "label": label,
        "trait": trait,
        "stage_direction": stage,
        "outcome": outcome,
        "delta": {key: after[key] - before[key] for key in before if after[key] != before[key]},
        "lore_update": lore_update,
        "items_added": items_added,
        "process": process,
        "leverage_note": outcome_label,
    }


def apply_eunuch_care(
    db: GameDB,
    state: GameState,
    name: str,
    *,
    mode: str = "general",
    note: str = "",
    source: str = "dialogue",
) -> Dict[str, object]:
    """对白驱动照料净身旧患：花内库小钱，缓解怨望/能力损伤并写入记忆。"""
    ensure_schema(db)
    clean_name = str(name or "").strip()
    if not clean_name:
        return {"ok": False, "reason": "未点明照料对象。"}
    lore = get_lore(db, clean_name)
    if lore is None:
        return {"ok": False, "reason": f"{clean_name}没有净身旧档。"}
    row = db.conn.execute(
        "SELECT emp_trust, grievance, ability, wisdom, charm, luck, birth_year FROM characters WHERE name=? AND status='active'",
        (clean_name,),
    ).fetchone()
    if row is None:
        return {"ok": False, "reason": f"{clean_name}不在可照料名册中。"}
    adult = _is_adult_for_lore(db, clean_name)
    requested_mode = normalize_care_mode(mode, note)
    source_id = f"{int(state.turn)}:{clean_name}:{requested_mode}:{source}"
    existed = db.conn.execute(
        """
        SELECT 1 FROM event_memories
        WHERE subject_type='character' AND subject_id=? AND event_type='eunuch_care'
          AND source_kind=? AND source_id=?
        """,
        (clean_name, source, source_id),
    ).fetchone()
    if existed is not None:
        return {"ok": False, "reason": f"{clean_name}本回合已照料过这项旧患。", "mode": requested_mode}
    lore_update: Dict[str, str] = {}
    if requested_mode == "bao" and str(note or "").strip():
        try:
            from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int
            day = kv_int(db, KV_CURRENT_DAY, int(getattr(state, "turn", 0) or 0) * 30)
        except Exception:
            day = int(getattr(state, "turn", 0) or 0) * 30
        updated = update_lore_from_text(db, clean_name, note, day=day)
        if isinstance(updated, dict) and isinstance(updated.get("updated"), dict):
            lore_update = {str(k): str(v) for k, v in updated["updated"].items()}
            lore = get_lore(db, clean_name) or lore
    plan = _care_plan(requested_mode, lore, adult=adult)
    scheme = castration_scheme_profile(lore)
    mode = str(plan["mode"])

    before = {
        "emp_trust": int(row["emp_trust"] or 55),
        "grievance": int(row["grievance"] or 20),
        "ability": int(row["ability"] or 50),
        "wisdom": int(row["wisdom"] or 50),
        "charm": int(row["charm"] or 50),
        "luck": int(row["luck"] or 50),
    }
    delta = dict(plan.get("delta") or {})
    after = dict(before)
    for key, value in delta.items():
        if key in after:
            after[key] = _clamp_0_100(after[key] + int(value or 0))
    db.conn.execute(
        """
        UPDATE characters
        SET emp_trust=?, grievance=?, ability=?, wisdom=?, charm=?, luck=?
        WHERE name=?
        """,
        (after["emp_trust"], after["grievance"], after["ability"], after["wisdom"], after["charm"], after["luck"], clean_name),
    )
    trait = str(plan.get("trait") or "").strip()
    if trait:
        db.conn.execute(
            "INSERT OR IGNORE INTO character_traits (name, trait, valence) VALUES (?,?,?)",
            (clean_name, trait, 1),
        )
    base_cost = max(0, int(plan.get("cost") or 0))
    scheme_cost = int(scheme.get("care_cost_delta") or 0)
    if mode not in {"urinary", "trauma", "body", "general"}:
        scheme_cost = min(scheme_cost, 1)
    cost = max(0, base_cost + scheme_cost)
    paid = 0
    if cost:
        paid = abs(db.record_issue_economy_move(
            state,
            "内库",
            -cost,
            "内廷调养",
            f"{clean_name}{plan.get('label') or '旧患调养'}",
            purpose="maintenance",
            target_kind="character",
            target_id=clean_name,
            apply_legacy=False,
        ))
    else:
        db.save_state(state)
    outcome_bits = [
        f"{label}{after[key] - before[key]:+d}"
        for key, label in (
            ("emp_trust", "信任"),
            ("grievance", "怨望"),
            ("ability", "才干"),
            ("wisdom", "机敏"),
            ("charm", "仪表"),
            ("luck", "运势"),
        )
        if after[key] != before[key]
    ]
    if paid:
        outcome_bits.append(f"内库-{paid}")
    if scheme_cost:
        outcome_bits.append(f"方案调养{scheme_cost:+d}")
    if lore_update:
        label_map = {
            "bao_preservation": "宝存",
            "bao_container": "宝匣",
            "bao_ritual": "仪式",
            "bao_texture": "宝况",
            "bao_weight": "宝重",
            "bao_shape": "宝形",
        }
        updated_labels = [label_map.get(key, key) for key in lore_update]
        outcome_bits.append(f"宝档更新：{'、'.join(updated_labels[:4])}")
    items_added: List[str] = []
    if mode == "bao":
        item_id = f"宝案安置：{clean_name}"
        exists = db.conn.execute(
            "SELECT 1 FROM player_inventory WHERE item_id=?",
            (item_id,),
        ).fetchone()
        if exists is None:
            db.grant_player_item(item_id, state)
            items_added.append(item_id)
    outcome = "，".join(outcome_bits) or "照料入档"
    title = f"{plan.get('label') or '内廷调养'}：{clean_name}"
    process = "；".join(part for part in (str(plan.get("process") or ""), str(note or "").strip()) if part)[:160]
    db.upsert_event_memory(
        state,
        "character",
        clean_name,
        "eunuch_care",
        title,
        cause="御前照料净身旧患",
        process=process,
        outcome=outcome,
        sentiment="positive" if int(after["grievance"]) <= int(before["grievance"]) else "mixed",
        importance=4,
        tags=["净身", "调养", mode, str(plan.get("label") or "")],
        source_kind=source,
        source_id=source_id,
    )
    fulfilled_goal_id = 0
    goal_row = db.conn.execute(
        """
        SELECT id FROM conversation_goals
        WHERE minister_name=?
          AND action_kind='eunuch_care'
          AND status IN ('active','waiting_conditions','blocked')
        ORDER BY id DESC LIMIT 1
        """,
        (clean_name,),
    ).fetchone()
    if goal_row is not None:
        fulfilled_goal_id = int(goal_row["id"] or 0)
        if fulfilled_goal_id:
            db.update_conversation_goal(
                fulfilled_goal_id,
                state=state,
                event_kind="eunuch_care_fulfilled",
                event_summary=f"{clean_name}{plan.get('label') or '内廷调养'}已奉旨处置：{outcome}",
                status="fulfilled",
                score=100,
                condition_status="satisfied",
                last_delta_json={
                    "source": "eunuch_care",
                    "mode": mode,
                    "public_hint": f"{clean_name}{plan.get('label') or '内廷调养'}已奉旨处置。",
                    "outcome": outcome,
                },
            )
    db.record_log(state, f"【内廷调养】{title}：{outcome}。")
    db.conn.commit()
    return {
        "ok": True,
        "name": clean_name,
        "mode": mode,
        "label": str(plan.get("label") or "内廷调养"),
        "cost": paid,
        "trait": trait,
        "stage_direction": str(plan.get("stage") or ""),
        "process": process,
        "outcome": outcome,
        "delta": {key: after[key] - before[key] for key in before if after[key] != before[key]},
        "scheme_profile": scheme,
        "goal_id": fulfilled_goal_id,
        "lore_update": lore_update,
        "items_added": items_added,
    }


def _trait_names(db: GameDB, name: str) -> set[str]:
    try:
        rows = db.conn.execute(
            "SELECT trait FROM character_traits WHERE name=?",
            ((name or "").strip(),),
        ).fetchall()
    except Exception:
        return set()
    return {str(row["trait"] or "").strip() for row in rows if str(row["trait"] or "").strip()}


_DISPATCH_STRATEGY_ALIASES = {
    "relay": "relay",
    "分班": "relay",
    "换班": "relay",
    "副手": "relay",
    "轮值": "relay",
    "avoid_trigger": "avoid_trigger",
    "avoid": "avoid_trigger",
    "避触发": "avoid_trigger",
    "避刑房": "avoid_trigger",
    "绕行": "avoid_trigger",
    "care_first": "care_first",
    "care": "care_first",
    "先调养": "care_first",
    "调养后再派": "care_first",
    "force": "force",
    "强派": "force",
    "硬派": "force",
    "照旧": "force",
}


def normalize_dispatch_strategy(strategy: str) -> str:
    raw = str(strategy or "").strip().lower()
    for key, value in _DISPATCH_STRATEGY_ALIASES.items():
        if key.lower() in raw:
            return value
    if re.search(r"副手|换班|分班|轮值|接力", raw):
        return "relay"
    if re.search(r"避|绕|外围|不入.*(刑房|净房)", raw):
        return "avoid_trigger"
    if re.search(r"调养|太医|先治", raw):
        return "care_first"
    if re.search(r"强|硬|照旧|限期", raw):
        return "force"
    return "relay"


def _dispatch_strategy_marks(text: str) -> Dict[str, bool]:
    raw = str(text or "")
    return {
        "relay": bool(re.search(r"副手|换班|分班|轮值|接力|替手", raw)),
        "avoid_trigger": bool(re.search(r"避开|绕开|外围|不入.*(刑房|净房)|先走.*文书|避.*(刀声|封签|净房|刑房)", raw)),
        "care_first": bool(re.search(r"先调养|调养后再派|太医.*再派|先治.*再查", raw)),
        "force": bool(re.search(r"强派|硬派|照旧硬查|照旧派|不许退|硬查|限期照办", raw)),
    }


def _dispatch_strategy_options(
    lore: Dict[str, object],
    *,
    raw: str,
    domain_set: set[str],
    risks: Sequence[str],
    score_delta: int,
) -> List[Dict[str, object]]:
    if not risks and score_delta >= 0:
        return []
    options: List[Dict[str, object]] = []
    has_urine = bool(str(lore.get("urinary_aftereffect") or "").strip())
    has_trigger = bool(
        str(lore.get("trauma_response") or "").strip()
        or str(lore.get("voice_body_change") or "").strip()
        or str(lore.get("bao_ritual") or "").strip()
        or str(lore.get("bao_status") or "") in {BAO_FORFEIT, BAO_LOST}
    )
    scheme = castration_scheme_profile(lore)
    if has_urine and (
        re.search(r"久候|夜守|盯梢|远行|出京|巡|缉拿|路上", raw)
        or domain_set.intersection({"investigation", "military", "local", "inner"})
    ):
        options.append({
            "key": "relay",
            "label": "分班副手",
            "score_relief": 4,
            "cost": 1,
            "cost_account": "内库",
            "tradeoff": "少误时，但多一层耳目，风声略宽。",
            "prompt": "准其带副手轮值，尿闭漏尿时换班，不许硬撑坏事。",
        })
    if has_trigger and (
        re.search(r"刑房|净房|刀|血|拷|审|拿问|封签|宝匣|乔装|传旨", raw)
        or domain_set.intersection({"investigation", "inner"})
    ):
        options.append({
            "key": "avoid_trigger",
            "label": "避开触发",
            "score_relief": 3,
            "cost": 0,
            "cost_account": "",
            "tradeoff": "旧患少发，但查办绕远，进展会慢。",
            "prompt": "令其先走外围文书和线人，不入刑房净房，不当面碰封签刀声。",
        })
    if score_delta <= -4 or int(scheme.get("care_cost_delta") or 0) > 0:
        options.append({
            "key": "care_first",
            "label": "先调养再派",
            "score_relief": 6,
            "cost": max(2, 4 + int(scheme.get("care_cost_delta") or 0)),
            "cost_account": "内库",
            "tradeoff": "最稳，耗内库，也会让差事慢半拍。",
            "prompt": "先动太医和内库压住旧患，再令其接差。",
        })
    options.append({
        "key": "force",
        "label": "照旧强派",
        "score_relief": -3,
        "cost": 0,
        "cost_account": "",
        "tradeoff": "最快最密，怨望、失手和旧患爆发风险加重。",
        "prompt": "不许退缩，照旧限期硬查硬办。",
    })
    dedup: Dict[str, Dict[str, object]] = {}
    for option in options:
        dedup[str(option["key"])] = option
    return list(dedup.values())


def assignment_risk_profile(
    db: GameDB,
    name: str,
    task_text: str = "",
    *,
    domains: Optional[Sequence[str]] = None,
) -> Dict[str, object]:
    """Translate eunuch lore into concrete dispatch/secret-order risk.

    This keeps the castration/bao details playable: the same old wound can be a
    bad fit for long surveillance, prison work, confiscated-bao inquiries, or a
    public-facing errand. Dialogue care traits soften the corresponding risk.
    """

    clean_name = str(name or "").strip()
    lore = get_lore(db, clean_name)
    if not clean_name or lore is None:
        return {}
    raw = str(task_text or "")
    domain_set = {str(domain or "").strip() for domain in (domains or []) if str(domain or "").strip()}
    traits = _trait_names(db, clean_name)
    score_delta = 0
    notes: List[str] = []
    risks: List[str] = []
    mitigations: List[str] = []
    stage_cues: List[str] = []
    strategy_marks = _dispatch_strategy_marks(raw)
    strategy_score_adjustment = 0

    def add(
        delta: int,
        note: str,
        risk: str = "",
        *,
        care_trait: str = "",
        mitigation: str = "",
        stage: str = "",
    ) -> None:
        nonlocal score_delta
        adjusted = int(delta)
        care_traits = {item.strip() for item in str(care_trait or "").split("|") if item.strip()}
        if adjusted < 0 and care_traits and traits.intersection(care_traits):
            relief = min(abs(adjusted), 4)
            adjusted += relief
            if mitigation and mitigation not in mitigations:
                mitigations.append(mitigation)
            note = f"{note}已调养缓和"
        if adjusted == 0 and delta < 0:
            return
        score_delta += adjusted
        if note and note not in notes:
            notes.append(note)
        if adjusted < 0 and risk and risk not in risks:
            risks.append(risk)
        if stage and stage not in stage_cues:
            stage_cues.append(stage)

    urine = str(lore.get("urinary_aftereffect") or "").strip()
    if urine:
        hard_wait = bool(
            re.search(r"久候|夜守|盯梢|跟踪|远行|出京|巡|缉拿|路上|边镇|陕西|山西|辽东", raw)
            or domain_set.intersection({"investigation", "military", "local"})
        )
        add(
            -5 if hard_wait else -2,
            "尿路旧患牵制久候远行",
            f"尿路旧患：{urine}，久候盯梢、远行或连夜差遣易误时。",
            care_trait="旧患调养",
            mitigation="尿路旧患已有御前调养，久候误事风险下降。",
            stage="久候会夹腰缩步，需给副手或准其换班。",
        )

    trauma = str(lore.get("trauma_response") or "").strip()
    if trauma:
        prison_pressure = bool(
            re.search(r"刑房|净房|净军|刀|血|拷|审|拿问|下狱|诏狱|廷杖|抄家|封签", raw)
            or "investigation" in domain_set
        )
        add(
            -6 if prison_pressure else -2,
            "惊创旧梦影响高压查办",
            f"惊创未平：{trauma}，刑房、拿问、封签或净房线索会触发失神。",
            care_trait="惊创抚慰",
            mitigation="惊创已受抚慰，刑房旧梦触发概率下降。",
            stage="听见刑房、刀声、净房旧话会短暂失神。",
        )

    body = str(lore.get("voice_body_change") or "").strip()
    if body and (
        re.search(r"乔装|潜入|外出|传旨|密会|面见|跑腿|见人|口供", raw)
        or domain_set.intersection({"investigation", "local", "inner"})
    ):
        add(
            -3,
            "体声异变影响乔装接触",
            f"体声异变：{body}，乔装、问话或公开传旨时容易露怯。",
            care_trait="仪态修整",
            mitigation="体声仪态已修整，露怯风险下降。",
            stage="话急时嗓音发尖、肩背微缩。",
        )

    bao_status = str(lore.get("bao_status") or "")
    ritual = str(lore.get("bao_ritual") or "").strip()
    scheme = castration_scheme_profile(lore)
    if scheme.get("explicit") and int(scheme.get("risk_score") or 0) >= 62 and (
        re.search(r"久候|夜守|盯梢|刑房|净房|刀|血|拷|审|拿问|下狱|诏狱|抄家|远行|出京|传旨|乔装|密会", raw)
        or domain_set.intersection({"investigation", "military", "local", "inner"})
    ):
        add(
            -3 if int(scheme.get("risk_score") or 0) < 76 else -5,
            f"净身方案{scheme.get('tier') or '高危'}，旧患底子不稳",
            f"净身方案画像：{scheme.get('tier')}，酷烈{scheme.get('brutality')}、创伤{scheme.get('trauma_risk')}、伤身{scheme.get('surgery_risk')}。",
            care_trait="御前调养|旧患调养|惊创抚慰|仪态修整",
            mitigation="相关旧患已有御前调养，方案后患被压住一部分。",
            stage="一遇久候或刑房差事，会先摸封签、夹肩定神。",
        )
    bao_touch = bool(re.search(r"宝|宝匣|封签|官库|旧案|净房|司礼监|内廷|验身|验宝", raw) or "inner" in domain_set)
    if bao_touch:
        if bao_status in {BAO_FORFEIT, BAO_LOST}:
            label = "宝为官没" if bao_status == BAO_FORFEIT else "宝已遗失"
            add(
                -4,
                "宝案心结牵动内廷查验",
                f"宝案心结：{label}，遇官库封签、验宝或净房旧案容易怨气上涌。",
                care_trait="宝匣安置|御赐宝匣",
                mitigation="宝案已奉旨查验安置，封签刺激稍缓。",
                stage="听见封签宝匣会摸袖中钥匙或避开视线。",
            )
        elif ritual:
            add(
                2,
                "宝匣自藏使其在内廷规矩里较能定神",
                stage="遇内廷封匣旧例会先摸钥匙定神。",
            )
        if "宝案钳制" in traits:
            add(
                -3,
                "宝案钳制使其惧而不安",
                "宝案钳制：官库封签是把柄，遇封签宝匣差事容易失神或怨气反扑。",
                stage="听见官库封签便喉头一紧，叩首更低。",
            )

    fixation = str(lore.get("private_fixation") or "").strip()
    if fixation and re.search(r"钥匙|封匣|账册|库|规矩|搜查|翻检|洁净|衣物", raw):
        add(
            -2,
            "心癖会让差事偏执走样",
            f"心癖牵动：{fixation}，翻检、封匣或库房差事可能过度偏执。",
            care_trait="心癖安顿",
            mitigation="心癖已有安顿，偏执走样风险下降。",
            stage="反复抚平衣褶或摸索钥匙给自己定神。",
        )

    if strategy_marks["relay"] and any("尿路旧患" in risk for risk in risks):
        strategy_score_adjustment += 4
        if "已设副手分班换班，久候漏尿误事风险下降。" not in mitigations:
            mitigations.append("已设副手分班换班，久候漏尿误事风险下降。")
        if "准其夹腰退半步，由副手接力轮值。" not in stage_cues:
            stage_cues.append("准其夹腰退半步，由副手接力轮值。")
    if strategy_marks["avoid_trigger"] and any(("惊创" in risk or "宝案心结" in risk or "体声异变" in risk) for risk in risks):
        strategy_score_adjustment += 3
        if "已令其绕开刑房净房、封签刀声，改走外围文书。" not in mitigations:
            mitigations.append("已令其绕开刑房净房、封签刀声，改走外围文书。")
    if strategy_marks["care_first"]:
        if "已奉旨先调养再派差，旧患风险按调养结果继续折减。" not in mitigations:
            mitigations.append("已奉旨先调养再派差，旧患风险按调养结果继续折减。")
    if strategy_marks["force"] and risks:
        strategy_score_adjustment -= 3
        force_risk = "奉旨照旧强派，短期保密和速度上去，怨望与失手风险也被推高。"
        if force_risk not in risks:
            risks.append(force_risk)

    score_delta = max(-18, min(4, int(score_delta)))
    if strategy_score_adjustment:
        score_delta = max(-18, min(4, int(score_delta) + strategy_score_adjustment))
    if not notes and not risks and not mitigations:
        return {}
    sign = "+" if score_delta > 0 else ""
    dispatch_strategies = _dispatch_strategy_options(
        lore,
        raw=raw,
        domain_set=domain_set,
        risks=risks,
        score_delta=score_delta,
    )
    return {
        "name": clean_name,
        "score_delta": score_delta,
        "note": f"净身旧患修正{sign}{score_delta}（{'、'.join(notes[:4])}）" if notes else "",
        "risks": risks[:6],
        "mitigations": mitigations[:4],
        "stage_cues": stage_cues[:4],
        "condition_line": str((public_lore_payload(db, clean_name) or {}).get("condition_line") or "")[:240],
        "dispatch_strategies": dispatch_strategies[:4],
    }


def _primary_care_mode_for_task(risk: Dict[str, object]) -> str:
    risks = " ".join(str(item) for item in (risk.get("risks") or []))
    if "尿路" in risks or "漏尿" in risks or "尿闭" in risks:
        return "urinary"
    if "惊创" in risks or "幻肢" in risks or "刑房" in risks:
        return "trauma"
    if "体声" in risks or "嗓音" in risks:
        return "body"
    if "宝案" in risks or "宝匣" in risks or "封签" in risks:
        return "bao"
    return "general"


def apply_eunuch_dispatch_strategy(
    db: GameDB,
    state: GameState,
    name: str,
    task_text: str,
    strategy: str,
    *,
    order_id: int = 0,
    domains: Optional[Sequence[str]] = None,
    note: str = "",
    source: str = "dialogue",
) -> Dict[str, object]:
    """对白驱动的宦官差遣策略：把旧患从风险提示变成可结算取舍。"""
    ensure_schema(db)
    clean_name = str(name or "").strip()
    if not clean_name:
        return {"ok": False, "reason": "未点明承办宦官。"}
    lore = get_lore(db, clean_name)
    if lore is None:
        return {"ok": False, "reason": f"{clean_name}没有净身旧档。"}
    task = str(task_text or "").strip()
    mode = normalize_dispatch_strategy(strategy)
    risk_before = assignment_risk_profile(db, clean_name, task, domains=domains)
    if not risk_before:
        return {"ok": False, "reason": f"{clean_name}此差未触发净身旧患风险。"}
    source_id = f"{int(getattr(state, 'turn', 0) or 0)}:{clean_name}:{mode}:{int(order_id or 0)}:{source}"
    existed = db.conn.execute(
        """
        SELECT 1 FROM event_memories
        WHERE subject_type='character' AND subject_id=? AND event_type='eunuch_dispatch_strategy'
          AND source_kind=? AND source_id=?
        """,
        (clean_name, source, source_id),
    ).fetchone()
    if existed is not None:
        return {"ok": False, "reason": f"{clean_name}本回合已安排过这项差遣策略。", "strategy": mode}
    row = db.conn.execute(
        "SELECT emp_trust, grievance, ability, wisdom, charm, luck FROM characters WHERE name=? AND status='active'",
        (clean_name,),
    ).fetchone()
    if row is None:
        return {"ok": False, "reason": f"{clean_name}不在可差遣名册中。"}

    if mode == "care_first":
        care_mode = _primary_care_mode_for_task(risk_before)
        care = apply_eunuch_care(
            db,
            state,
            clean_name,
            mode=care_mode,
            note=note or f"先调养再派：{task[:80]}",
            source=f"{source}_dispatch",
        )
        if not care.get("ok"):
            return {"ok": False, "reason": str(care.get("reason") or "调养未成。"), "strategy": mode}
        strategy_note = (
            f"[旧患差遣] 奉旨先调养再派，{clean_name}{care.get('label')}后再接此差；"
            "先治旧患，不许硬撑误事。"
        )
        paid = int(care.get("cost") or 0)
        delta = care.get("delta") if isinstance(care.get("delta"), dict) else {}
        stage = str(care.get("stage_direction") or "")
    else:
        before = {
            "emp_trust": int(row["emp_trust"] or 55),
            "grievance": int(row["grievance"] or 20),
            "ability": int(row["ability"] or 50),
            "wisdom": int(row["wisdom"] or 50),
            "charm": int(row["charm"] or 50),
            "luck": int(row["luck"] or 50),
        }
        if mode == "relay":
            plan_delta = {"emp_trust": 1, "grievance": -1}
            cost = 1
            strategy_note = (
                f"[旧患差遣] 奉旨给{clean_name}副手换班、分班轮值，"
                "尿闭漏尿时准其退半步，由替手接力；多一层耳目，风声略宽。"
            )
            stage = "夹腰退半步时不再硬撑，身后一名小火者接过灯牌。"
        elif mode == "avoid_trigger":
            plan_delta = {"emp_trust": 1}
            cost = 0
            strategy_note = (
                f"[旧患差遣] 奉旨令{clean_name}避开刑房净房、封签刀声，"
                "先走外围文书与线人；查办绕远，进展慢半拍。"
            )
            stage = "听见刑房二字先一僵，得旨绕行后才低声称谢。"
        else:
            mode = "force"
            plan_delta = {"emp_trust": -1, "grievance": 3}
            if int(risk_before.get("score_delta") or 0) <= -8:
                plan_delta["ability"] = -1
            cost = 0
            strategy_note = (
                f"[旧患差遣] 奉旨强派{clean_name}照旧硬查，不许因净身旧患退缩；"
                "保密与速度优先，怨望和失手风险加重。"
            )
            stage = "他伏地称奴婢不敢，起身时肩背却缩得更紧。"
        after = dict(before)
        for key, value in plan_delta.items():
            if key in after:
                after[key] = _clamp_0_100(after[key] + int(value or 0))
        db.conn.execute(
            """
            UPDATE characters
            SET emp_trust=?, grievance=?, ability=?, wisdom=?, charm=?, luck=?
            WHERE name=?
            """,
            (after["emp_trust"], after["grievance"], after["ability"], after["wisdom"], after["charm"], after["luck"], clean_name),
        )
        paid = 0
        if cost:
            paid = abs(db.record_issue_economy_move(
                state,
                "内库",
                -cost,
                "内廷差遣",
                f"{clean_name}旧患差遣分班副手",
                purpose="maintenance",
                target_kind="character",
                target_id=clean_name,
                apply_legacy=False,
            ))
        else:
            db.save_state(state)
        delta = {key: after[key] - before[key] for key in before if after[key] != before[key]}

    if note:
        strategy_note = f"{strategy_note} 朱批备注：{str(note).strip()[:100]}"
    if order_id:
        try:
            db.update_secret_order_sim_note(int(order_id), strategy_note, year=state.year, period=state.period)
        except Exception:
            pass
    risk_after = assignment_risk_profile(db, clean_name, f"{task}\n{strategy_note}", domains=domains)
    outcome_bits = []
    for key, label in (
        ("emp_trust", "信任"),
        ("grievance", "怨望"),
        ("ability", "才干"),
        ("wisdom", "机敏"),
        ("charm", "仪表"),
        ("luck", "运势"),
    ):
        value = int((delta or {}).get(key) or 0) if isinstance(delta, dict) else 0
        if value:
            outcome_bits.append(f"{label}{value:+d}")
    if paid:
        outcome_bits.append(f"内库-{paid}")
    before_score = int(risk_before.get("score_delta") or 0)
    after_score = int((risk_after or {}).get("score_delta") or 0)
    if before_score != after_score:
        outcome_bits.append(f"旧患风险{before_score:+d}->{after_score:+d}")
    outcome = "，".join(outcome_bits) or "差遣策略入档"
    db.upsert_event_memory(
        state,
        "character",
        clean_name,
        "eunuch_dispatch_strategy",
        f"旧患差遣：{clean_name}",
        cause="御前按净身旧患调整密令差遣",
        process=strategy_note,
        outcome=outcome,
        sentiment="negative" if mode == "force" else "mixed",
        importance=4 if mode in {"care_first", "force"} else 3,
        tags=["净身", "差遣", "旧患", mode],
        source_kind=source,
        source_id=source_id,
    )
    db.record_log(state, f"【旧患差遣】{clean_name}：{outcome}。")
    db.conn.commit()
    return {
        "ok": True,
        "name": clean_name,
        "strategy": mode,
        "order_id": int(order_id or 0),
        "cost": paid,
        "stage_direction": stage,
        "process": strategy_note,
        "outcome": outcome,
        "delta": delta,
        "risk_before": risk_before,
        "risk_after": risk_after,
    }


# ── 民间募新宦官（E2d）：灾年民困，良家子自宫求进、卖身入宫充内侍 ──
_RECRUIT_SURNAMES = "王李张刘陈杨赵黄周吴徐孙马朱"
_RECRUIT_GIVEN = ("进忠", "永贞", "国泰", "得禄", "承恩", "守义", "小顺", "三才", "应元",
                  "化淳", "良辅", "时敏", "本忠", "永寿", "尽忠", "存仁")
_RECRUIT_OFFICES = ("内官监小火者", "司礼监随堂", "净军", "御马监勇士营", "尚膳监差使")
_RECRUIT_UNREST_GATE = 48      # 流民／民困到此程度，自宫求进者渐多
_RECRUIT_CAP = 8               # 自动募入的上限（免灌爆名册）


def _auto_recruited_count(db: GameDB) -> int:
    ensure_schema(db)
    return int(db.conn.execute(
        "SELECT COUNT(*) c FROM eunuch_lore WHERE note LIKE '%自宫求进%'").fetchone()["c"])


def recruit_tick(db: GameDB, state: GameState, day: int) -> List[Dict[str, object]]:
    """月初：灾年民困（流民多）→ 良家子自宫求进、卖身入宫充内侍，为内廷添新血。
    生成低品宦官 NPC 入册（非净身现成大臣）。挂 rollover。无灾或已满额则空转。"""
    from ming_sim.timeflow import LEVEL_BLUE
    from ming_sim.models import Character
    ensure_schema(db)
    if _auto_recruited_count(db) >= _RECRUIT_CAP:
        return []
    row = db.conn.execute("SELECT AVG(unrest) a FROM regions").fetchone()
    avg_unrest = float(row["a"] or 0)
    if avg_unrest < _RECRUIT_UNREST_GATE:
        return []
    # 确定性节律：每两月一审，民困越深越易来人（用 day 整除当闸门，避免月月新人）。
    if (int(day) // 30) % 2 != 0:
        return []
    existing = {str(r["name"]) for r in db.conn.execute("SELECT name FROM characters").fetchall()}
    idx = (int(day) // 60)
    name = ""
    for off in range(len(_RECRUIT_GIVEN)):
        cand = _RECRUIT_SURNAMES[(idx + off) % len(_RECRUIT_SURNAMES)] + _RECRUIT_GIVEN[(idx + off) % len(_RECRUIT_GIVEN)]
        if cand not in existing:
            name = cand
            break
    if not name:
        return []
    office = _RECRUIT_OFFICES[idx % len(_RECRUIT_OFFICES)]
    db.add_character(state, Character(
        name=name, office=office, office_type="内官监", faction="内廷", aliases=[],
        personal_skills=["洒扫", "传话"], loyalty=58, ability=38, integrity=50, courage=40,
        style="自宫求进、卑微谨愿的小内侍，凡事唯恐失欢", power_id="ming", status="active",
        summary=f"灾年贫家子，自宫求进入宫，充{office}。", rank_category="eunuch"),
        source="民间自宫求进入宫")
    # 自宫求进者奴性偏重（求生卑微），宝多自藏（望来世）。
    record_castration(db, name, forced=False, day=day)
    db.conn.execute("UPDATE eunuch_lore SET servility=66, note=? WHERE name=?",
                    ("灾年自宫求进入宫，宝匣自藏", name))
    db.conn.commit()
    return [{"level": LEVEL_BLUE, "kind": "eunuch_recruit",
             "title": f"自宫求进：{name}入内廷",
             "detail": f"岁饥民困，贫家子{name}自宫求进、卖身入宫，充{office}。"
                       "外朝叹曰：净身求活者日多，亦世道之凄惶。内廷由是益众。",
             "ref_kind": "character", "ref_id": name, "day": day}]


def burial_lament_on_death(db: GameDB, state: GameState, name: str, day: int) -> Optional[str]:
    """殁时全尸执念（接 lifespan.mortality_tick）：净身者无宝（forfeit/lost）→ 不得全尸之憾，
    党羽哀恸更深（额外 grievance）；有宝（kept）→ 凑得全尸、稍慰。返回讣告增补语或 None。"""
    lore = get_lore(db, name)
    if lore is None:
        return None
    bao = str(lore["bao_status"])
    try:
        from ming_sim import court
        allies = court.allies_of(db, name, limit=4)
    except Exception:
        allies = []
    if bao == BAO_KEPT:
        return "宝匣得以同棺，凑成全尸下葬，老监谓其「圆满去得」，稍慰生平之缺。"
    # 无宝：不得全尸，党羽哀恸更深
    for a in allies:
        try:
            from ming_sim import court
            court._adjust_char(db, a["name"], grievance=+2)
        except Exception:
            pass
    if bao == BAO_FORFEIT:
        return "宝为官没、终不得全尸——生前奇辱、身后犹缺，党羽哀其不圆，怨气深结。"
    return "客死无凭、宝已遗失，不得全尸而葬，宫人嗟叹。"
