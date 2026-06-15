"""随侍太监枢纽（人治之门）：皇帝唯一可随时直奏之人，百官经其传召觐见。L7。

随侍太监是一个**可替换的职位**——和所有官员一样可换人（KV 记在任者）。
其本身是一个有血有肉的 LLM 角色（复用大臣 agent 链路），只是被注入「随侍」角色简报：
为皇帝过滤/转呈百官奏报、可奉旨传召大臣(summon_minister 工具)，且有自己的立场私心。

不另起对话栈：与太监对话走现有 session.chat / /api/ministers/{name}/chat；
本模块只管「在任太监是谁、怎么换、给谁注入随侍简报」。无在任太监则优雅降级。
"""

from __future__ import annotations

from typing import List, Optional

from ming_sim.db import GameDB

KV_ATTENDING_EUNUCH = "upgrade.attending_eunuch"

# 宦官身份关键词（职/衙门）——与 portraits.is_eunuch_identity 同源，落到 office/office_type。
_EUNUCH_KW = ("太监", "宦官", "内官", "司礼监", "东厂", "内廷", "秉笔", "掌印", "随堂",
              "御马监", "御用监", "尚膳监", "内官监", "御前")
# 默认随侍优先人选（崇祯贴身、史上殉国之忠宦）。
_PREFERRED = "王承恩"


def is_eunuch_like(office: str, office_type: str) -> bool:
    blob = f"{office or ''}{office_type or ''}"
    return any(kw in blob for kw in _EUNUCH_KW)


def _active_char(db: GameDB, name: str):
    if not name:
        return None
    return db.conn.execute(
        "SELECT name, office, office_type, power_id FROM characters "
        "WHERE name=? AND status='active'", (name,)
    ).fetchone()


def list_candidates(db: GameDB) -> List[dict]:
    """可任随侍者：在朝大明宦官优先；用户「人皆可换」，故也允许其余在朝官员，但宦官置顶。"""
    rows = db.conn.execute(
        "SELECT name, office, office_type FROM characters "
        "WHERE status='active' AND power_id='ming' AND office_type!='后宫' ORDER BY name"
    ).fetchall()
    eunuchs, others = [], []
    for r in rows:
        item = {"name": str(r["name"]), "office": str(r["office"] or ""),
                "is_eunuch": is_eunuch_like(str(r["office"] or ""), str(r["office_type"] or ""))}
        (eunuchs if item["is_eunuch"] else others).append(item)
    return eunuchs + others


def get_attending_eunuch(db: GameDB) -> Optional[str]:
    """在任随侍太监姓名。KV 已设且在朝则用之；否则默认挑（王承恩→任一在朝宦官→None）。"""
    raw = (db.kv_get(KV_ATTENDING_EUNUCH) or "").strip()
    if raw and _active_char(db, raw) is not None:
        return raw
    # 默认：王承恩
    if _active_char(db, _PREFERRED) is not None:
        db.kv_set(KV_ATTENDING_EUNUCH, _PREFERRED)
        return _PREFERRED
    # 任一在朝宦官
    for c in list_candidates(db):
        if c["is_eunuch"]:
            db.kv_set(KV_ATTENDING_EUNUCH, c["name"])
            return c["name"]
    return None  # 优雅降级：无随侍太监，召对回退直选官员


def set_attending_eunuch(db: GameDB, name: str) -> dict:
    """换随侍太监（人皆可换）。校验在朝。"""
    name = (name or "").strip()
    row = _active_char(db, name)
    if row is None:
        return {"ok": False, "message": "此人不在朝，无法充任随侍。"}
    db.kv_set(KV_ATTENDING_EUNUCH, name)
    return {"ok": True, "message": f"已命{name}充任御前随侍。", "name": name,
            "office": str(row["office"] or "")}


def eunuch_role_brief(name: str, office: str = "") -> str:
    """注入到与在任太监对话的 user message 头部——令其以「随侍」身份应对。"""
    post = office or "御前随侍"
    return (
        f"【随侍太监·身份】你是{name}（{post}），皇帝身边的随侍太监，是陛下唯一可随时直接召问之人。"
        "你的本分：替陛下过滤、转呈百官奏报与外朝动静；陛下若要见某位大臣，你当奉旨传召（用 summon_minister 工具召其觐见）。"
        "你熟知宫府人事、可为陛下进言举荐，但你也有自己的立场与私心——进言可带倾向、可隐讳，却须合乎一个内臣的身份与分寸，不可僭越妄言朝政决断。"
        "对话用浅近文言，简练得体。"
    )
