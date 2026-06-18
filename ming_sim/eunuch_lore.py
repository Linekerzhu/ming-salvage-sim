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
from typing import Dict, List, Optional

from ming_sim.db import GameDB
from ming_sim.models import GameState

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


def record_castration(db: GameDB, name: str, *, forced: bool, day: int) -> Dict[str, object]:
    """净身时登记「宝」之处置与奴性（接 convert_character_to_eunuch）。
    强阉＝宝被官府收没、奴性扭曲（谄而心结深）；自愿＝宝可自赎保存、奴性恭谨。"""
    ensure_schema(db)
    name = (name or "").strip()
    if not name:
        return {}
    bao = BAO_FORFEIT if forced else BAO_KEPT
    servility = 78 if forced else 46
    details = _default_detail(name, forced=bool(forced), bao_status=bao)
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
    return {"name": name, "bao_status": bao, "forced": bool(forced), "servility": servility, **details}


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


def public_lore_payload(db: GameDB, name: str) -> Optional[Dict[str, object]]:
    """人物档案/前端公开用：把净身旧档折成稳定 payload。"""
    lore = get_lore(db, name)
    if lore is None:
        return None
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
    payload["fixation_label"] = str(lore.get("private_fixation") or "")
    payload["psychosexual_label"] = str(lore.get("psychosexual_state") or "")
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
        (r"刑房|宫刑|强旨", "奉旨宫刑"),
        (r"净军房|净军", "净军房夜割"),
        (r"内书堂|老匠|熟匠", "内书堂老匠净身"),
        (r"自请|自宫|自愿", "入宫前自请一刀"),
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
    _set_if_match(updates, raw, "private_fixation", [
        (r"洁净|爱干净", "洁净癖，衣褶不齐便不安"),
        (r"钥匙|封匣|掌管.*匣", "偏爱掌管钥匙与封匣"),
        (r"受罚|规训|羞辱", "受罚仪式癖，越被明令越心定"),
        (r"束缚|束带|捆|缚", "束带安定癖，衣带不紧便惊惶"),
        (r"恋香|香味", "恋香压惊，厌恶血腥旧味"),
    ])
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
