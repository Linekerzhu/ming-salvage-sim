"""随侍太监枢纽（人治之门）：皇帝唯一可随时直奏之人，百官经其传召觐见。L7。

随侍太监是一个**可替换的职位**，但必须由 `sex=eunuch` 的在朝阉人担任（KV 记在任者）。
其本身是一个有血有肉的 LLM 角色（复用大臣 agent 链路），只是被注入「随侍」角色简报：
为皇帝过滤/转呈百官奏报、可奉旨传召大臣(summon_minister 工具)，且有自己的立场私心。

不另起对话栈：与太监对话走现有 session.chat / /api/ministers/{name}/chat；
本模块只管「在任太监是谁、怎么换、给谁注入随侍简报」。无在任太监则优雅降级。
"""

from __future__ import annotations

from typing import List, Optional

from ming_sim.db import GameDB
from ming_sim.identity import character_is_eunuch, requires_eunuch_identity

KV_ATTENDING_EUNUCH = "upgrade.attending_eunuch"

# 默认随侍优先人选（崇祯贴身、史上殉国之忠宦）。
_PREFERRED = "王承恩"


def is_eunuch_like(office: str, office_type: str) -> bool:
    return requires_eunuch_identity(office, office_type)


def _active_char(db: GameDB, name: str):
    if not name:
        return None
    return db.conn.execute(
        "SELECT name, office, office_type, faction, sex, power_id FROM characters "
        "WHERE name=? AND status='active'", (name,)
    ).fetchone()


def _row_is_eunuch(row) -> bool:
    if row is None:
        return False
    return character_is_eunuch(
        row,
        sex=row["sex"] if "sex" in row.keys() else "",
        office=row["office"] if "office" in row.keys() else "",
        office_type=row["office_type"] if "office_type" in row.keys() else "",
        faction=row["faction"] if "faction" in row.keys() else "",
        allow_legacy_text_fallback=True,
    )


def list_candidates(db: GameDB) -> List[dict]:
    """可任随侍者：必须是在朝大明阉人。"""
    rows = db.conn.execute(
        "SELECT name, office, office_type, faction, sex FROM characters "
        "WHERE status='active' AND power_id='ming' AND office_type!='后宫' ORDER BY name"
    ).fetchall()
    candidates = []
    for r in rows:
        if not _row_is_eunuch(r):
            continue
        item = {"name": str(r["name"]), "office": str(r["office"] or ""),
                "sex": str(r["sex"] or "unknown"), "is_eunuch": True}
        candidates.append(item)
    return candidates


def get_attending_eunuch(db: GameDB) -> Optional[str]:
    """在任随侍太监姓名。KV 已设且在朝则用之；否则默认挑（王承恩→任一在朝宦官→None）。"""
    raw = (db.kv_get(KV_ATTENDING_EUNUCH) or "").strip()
    if raw:
        row = _active_char(db, raw)
        if row is not None and _row_is_eunuch(row):
            return raw
        db.kv_set(KV_ATTENDING_EUNUCH, "")
    # 默认：王承恩
    preferred = _active_char(db, _PREFERRED)
    if preferred is not None and _row_is_eunuch(preferred):
        db.kv_set(KV_ATTENDING_EUNUCH, _PREFERRED)
        return _PREFERRED
    # 任一在朝宦官
    for c in list_candidates(db):
        if c["is_eunuch"]:
            db.kv_set(KV_ATTENDING_EUNUCH, c["name"])
            return c["name"]
    return None  # 优雅降级：无随侍太监，召对回退直选官员


def set_attending_eunuch(db: GameDB, name: str) -> dict:
    """换随侍太监。校验在朝且必须为阉人身份。"""
    name = (name or "").strip()
    row = _active_char(db, name)
    if row is None:
        return {"ok": False, "message": "此人不在朝，无法充任随侍。"}
    if not _row_is_eunuch(row):
        return {"ok": False, "message": "随侍太监须由阉人担任；请先补内侍或完成净身身份转换。"}
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
        "第一人称硬规矩：只用「奴婢」；请罪或求恩时可用「奴才」「小人」；不得自称「臣」「微臣」「愚臣」。"
        "你不是内阁大学士；外朝军国大政多以“奴婢听闻”“须问该部/内阁”“奴婢可替陛下传问或催办”回应。"
        "除非陛下问传旨、查访、内库、宫禁或司礼监事务，不要主动铺陈宰辅式政策全案。"
        "说话像贴身内侍：短、低、近，先报所闻，再说奴婢可传问、催办或密查。对话用浅近文言，简练得体。"
    )
