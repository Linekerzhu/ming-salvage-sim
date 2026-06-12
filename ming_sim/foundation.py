"""NPC 数据基座适配层：接 ~/Documents/大明王朝1628/master_data.sqlite。L3。

基座内容（581 NPC，与游戏 263 人 100% 重叠 + 318 增量）：
- 韬/治/识/略/望 五维能力（5-19）+ 才总（46-80）
- 八轴人格（心术：序/义/世/霸；气性：燥/豪/机/执，-2..+2）+ 人格简报（已生成文本）
- 53 种特质（擅/痼/绝艺，带行为后果文案）
- 明史体小传、明批（命批诗）、v_npc_opening_llm_context.full_prompt 现成 agent 上下文

接入原则：
1. 基座只读（uri mode=ro），游戏运行态绝不写回——基座是「国朝人事档案」，
   游戏内变化（忠诚漂移、怨气、死亡）仍走游戏自己的 characters 表。
2. 找不到基座文件时一切函数静默降级（返回 None/空），游戏完全可玩。
3. full_prompt/人格简报按人静态 → 注入大臣 agent 的 system 静态段，前缀缓存友好。

路径解析：env MING_FOUNDATION_DB → <仓库>/data/foundation.sqlite → ~/Documents/大明王朝1628/master_data.sqlite
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional

from ming_sim.token_stats import tlog

_CONN: Optional[sqlite3.Connection] = None
_CONN_FAILED = False
_LOCK = threading.RLock()
_PROFILE_CACHE: Dict[str, Optional[Dict[str, object]]] = {}


def _db_path() -> Optional[str]:
    candidates = [
        os.environ.get("MING_FOUNDATION_DB", ""),
        str(Path(__file__).resolve().parent.parent / "data" / "foundation.sqlite"),
        # 冻结（PyInstaller）发行版：bundle 内部不可写，普通用户放这里
        str(Path.home() / ".ming_sim" / "foundation.sqlite"),
        str(Path.home() / "Documents" / "大明王朝1628" / "master_data.sqlite"),
    ]
    for cand in candidates:
        if cand and Path(cand).is_file():
            return cand
    return None


def _conn() -> Optional[sqlite3.Connection]:
    global _CONN, _CONN_FAILED
    with _LOCK:
        if _CONN is not None:
            return _CONN
        if _CONN_FAILED:
            return None
        path = _db_path()
        if path is None:
            _CONN_FAILED = True
            tlog("[foundation] 未找到 NPC 数据基座（master_data.sqlite），相关增强降级关闭")
            return None
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM npc_base LIMIT 1")
            _CONN = conn
            tlog(f"[foundation] NPC 数据基座已挂载（只读）：{path}")
            return _CONN
        except sqlite3.Error as exc:
            _CONN_FAILED = True
            tlog(f"[foundation] 基座打开失败，降级关闭：{exc}")
            return None


def available() -> bool:
    return _conn() is not None


def reset_for_tests() -> None:
    global _CONN, _CONN_FAILED
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
        _CONN = None
        _CONN_FAILED = False
        _PROFILE_CACHE.clear()


# ── 档案读取 ─────────────────────────────────────────────────────────────────

def profile(name: str) -> Optional[Dict[str, object]]:
    """单人完整档案（缓存）。无基座/无此人 → None。
    查询全程持 _LOCK：共享只读连接的跨线程安全不依赖 sqlite3 编译期 threadsafety。"""
    if name in _PROFILE_CACHE:
        return _PROFILE_CACHE[name]
    conn = _conn()
    if conn is None:
        return None
    with _LOCK:
        return _profile_locked(conn, name)


def _profile_locked(conn: sqlite3.Connection, name: str) -> Optional[Dict[str, object]]:
    row = conn.execute(
        "SELECT * FROM v_npc_runtime_profile WHERE npc_name=?", (name,)
    ).fetchone()
    if row is None:
        _PROFILE_CACHE[name] = None
        return None
    npc_id = int(row["npc_id"])
    ability = conn.execute(
        "SELECT js_tao, js_zhi, js_shi, js_lue, js_wang, cai_zong, cai_ding "
        "FROM npc_ability WHERE npc_id=?", (npc_id,)
    ).fetchone()
    persona = conn.execute(
        "SELECT * FROM v_npc_persona_runtime WHERE npc_id=?", (npc_id,)
    ).fetchone()
    traits = conn.execute(
        "SELECT trait_code, trait_name, kind, consequence FROM v_npc_trait_runtime WHERE npc_id=?",
        (npc_id,),
    ).fetchall()
    ctx = conn.execute(
        "SELECT full_prompt FROM v_npc_opening_llm_context WHERE npc_id=?", (npc_id,)
    ).fetchone()
    brief = conn.execute(
        "SELECT * FROM npc_persona_brief WHERE npc_id=?", (npc_id,)
    ).fetchone()
    brief_text = ""
    if brief is not None:
        for key in ("system_prompt", "narrative", "dialogue_style"):
            if key in brief.keys() and brief[key] and len(str(brief[key])) > 40:
                brief_text = str(brief[key])
                break
    result: Dict[str, object] = {
        "npc_id": npc_id,
        "name": str(row["npc_name"]),
        "age_in_1628": row["age_in_1628"],
        "birth_year": row["birth_year"],
        "death_year": row["death_year"],
        "existence": str(row["existence_label"] or ""),
        "identity": str(row["identity_label"] or ""),
        "power": str(row["power_label"] or ""),
        "service_status": str(row["service_status_label"] or ""),
        "rank": str(row["rank_label"] or ""),
        "slot": str(row["current_slot_name"] or ""),
        "honor": str(row["honor_label"] or ""),
        "native_place": str(row["native_place_name"] or ""),
        "biography": str(row["biography"] or ""),
        "mingpi": str(row["mingpi"] or ""),
        "appearance_prompt": str(row["外貌prompt"] or "") if "外貌prompt" in row.keys() else "",
        "ability": dict(ability) if ability else {},
        "persona": dict(persona) if persona else {},
        "persona_brief": brief_text,
        "traits": [dict(t) for t in traits],
        "full_prompt": str(ctx["full_prompt"]) if ctx else "",
    }
    _PROFILE_CACHE[name] = result
    return result


def ability100(name: str) -> Optional[int]:
    """才总 46-80 → 游戏 0-100 口径（≈15-100）。"""
    p = profile(name)
    if not p or not p.get("ability"):
        return None
    cai = int(p["ability"].get("cai_zong") or 0)
    if cai <= 0:
        return None
    return max(10, min(100, round((cai - 40) * 2.5)))


def mingpi(name: str) -> str:
    p = profile(name)
    return str(p.get("mingpi") or "") if p else ""


# ── 大臣 agent 注入块（S8 + 人格深化）────────────────────────────────────────

def agent_block(name: str) -> str:
    """注入大臣 agent system 的静态人格档案块。基座 full_prompt 已含
    身份/能力/八轴/人格简报/特质/职权，外加明史小传摘要——这是扮演的「骨血」。"""
    p = profile(name)
    if not p:
        return ""
    parts = ["【国朝人事档案（你的底色——所有言行须从此生长，不得自相矛盾）】"]
    full = str(p.get("full_prompt") or "").strip()
    if full:
        parts.append(full)
    bio = str(p.get("biography") or "").strip()
    if bio:
        parts.append(f"生平（明史体，你对自己过往的记忆）：{bio[:400]}")
    parts.append(
        "扮演要求：人格简报中的「标志性行为」必须演出来；「立场刚性」里写明的裂缝是你可被说动之处；"
        "对白风格须贯彻。痼疾不是缺点说明书而是你真实的行为模式——它会害你也不自知。"
    )
    return "\n".join(parts)


# ── 特质 → 指令检定修正（S2 深接）────────────────────────────────────────────

# 擅/绝艺：对口类别加成（score+，工期×factor）
_TRAIT_CATEGORY_BONUS: Dict[str, Dict[str, object]] = {
    "S012": {"cats": ["military_ops"], "score": 10, "exec_factor": 0.85},          # 知兵
    "S013": {"cats": ["military_ops"], "score": 6, "exec_factor": 0.92},           # 百战余生
    "S018": {"cats": ["military_ops", "diplomacy"], "score": 8, "exec_factor": 0.9},  # 久历边事
    "S014": {"cats": ["military_ops", "relief"], "score": 6, "exec_factor": 1.0},  # 定海神针
    "S015": {"cats": ["fiscal_allocation", "relief", "tax_reform"], "score": 10, "exec_factor": 0.85},  # 精于钱谷
    "S019": {"cats": ["tax_reform", "audit_purge"], "score": 10, "exec_factor": 0.85},  # 清丈能手
    "S008": {"cats": ["audit_purge", "personnel"], "score": 10, "exec_factor": 0.9},    # 铁面无私
    "S010": {"cats": ["audit_purge", "secret_investigation"], "score": 8, "exec_factor": 0.9},  # 耳目遍布
    "S011": {"cats": ["secret_investigation", "ritual_signal"], "score": 6, "exec_factor": 0.9},  # 内廷通达
    "S009": {"cats": ["diplomacy", "relief"], "score": 10, "exec_factor": 0.88},   # 舌灿莲花
    "S017": {"cats": ["personnel"], "score": 12, "exec_factor": 0.85},             # 知人善任
    "S016": {"cats": ["ritual_signal"], "score": 8, "exec_factor": 0.85},          # 妙笔生花
    "S007": {"cats": ["*"], "score": 4, "exec_factor": 1.0},                       # 神机妙算
    "J005": {"cats": ["*"], "score": 8, "exec_factor": 0.95, "anomaly": {"skim": -20, "delay": -15}},  # 考成法
    "J006": {"cats": ["*"], "score": 0, "exec_factor": 0.85},                      # 焚膏继晷
    "J007": {"cats": ["*"], "score": 4, "exec_factor": 1.0, "anomaly": {"block": -10}},  # 揽责担当
}

# 痼/绝艺（负面/双刃）：异常权重偏置与减分
_TRAIT_FLAW_EFFECTS: Dict[str, Dict[str, object]] = {
    "G015": {"anomaly": {"skim": +30}},                       # 贪墨成性
    "J010": {"anomaly": {"skim": +25}},                       # 阳奉阴违（绝艺，但对皇帝是暗亏）
    "G017": {"anomaly": {"skim": +10}},                       # 沽名钓誉（粉饰）
    "G010": {"anomaly": {"delay": +20}, "score": -4},         # 优柔寡断
    "G012": {"anomaly": {"delay": +10}, "score": -4},         # 猜忌多疑
    "G009": {"anomaly": {"block": +10, "surprise": +8}},      # 刚愎自用
    "G011": {"anomaly": {"surprise": +15}},                   # 好大喜功
    "J009": {"anomaly": {"block": +10}},                      # 直言不讳（敢顶）
    "G016": {"resistance": +10},                              # 结党营私
    "G007": {"resistance": +8},                               # 门户之见
    "G013": {"score": -4},                                    # 暴戾恣睢
    "G002": {"score_cats": {"diplomacy": -8, "personnel": -6}},   # 不通人情
    "G004": {"score_cats": {"diplomacy": -8, "ritual_signal": -4}},  # 拙于言辞
    "G003": {"score_cats": {"ritual_signal": -10, "audit_purge": -8}},  # 目不识丁
}


def directive_modifiers(name: str, category_id: str) -> Dict[str, object]:
    """主办官员的基座特质 → 该类指令的检定修正。
    返回 {score, exec_factor, anomaly_bias{kind:±}, resistance, notes[]}；查无此人返回中性值。"""
    neutral = {"score": 0, "exec_factor": 1.0, "anomaly_bias": {}, "resistance": 0, "notes": []}
    p = profile(name)
    if not p:
        return neutral
    score, factor, resistance = 0, 1.0, 0
    anomaly: Dict[str, int] = {}
    notes: List[str] = []
    for t in p.get("traits") or []:
        code = str(t.get("trait_code") or "")
        tname = str(t.get("trait_name") or "")
        bonus = _TRAIT_CATEGORY_BONUS.get(code)
        if bonus is not None:
            cats = bonus.get("cats") or []
            if "*" in cats or category_id in cats:
                score += int(bonus.get("score") or 0)
                factor *= float(bonus.get("exec_factor") or 1.0)
                for k, v in (bonus.get("anomaly") or {}).items():
                    anomaly[k] = anomaly.get(k, 0) + int(v)
                notes.append(f"擅[{tname}]")
        flaw = _TRAIT_FLAW_EFFECTS.get(code)
        if flaw is not None:
            score += int(flaw.get("score") or 0)
            score += int((flaw.get("score_cats") or {}).get(category_id) or 0)
            resistance += int(flaw.get("resistance") or 0)
            for k, v in (flaw.get("anomaly") or {}).items():
                anomaly[k] = anomaly.get(k, 0) + int(v)
            notes.append(f"痼[{tname}]")
    # 气性执（磐石）：封驳后更难收场；气性机（深机）：截留更隐蔽——留作后续细化
    return {"score": score, "exec_factor": round(factor, 3),
            "anomaly_bias": anomaly, "resistance": resistance, "notes": notes}


# ── 印象档案风闻（S3 深接）───────────────────────────────────────────────────

def evaluation_rumors(name: str, *, familiarity: int, intel_boost: int) -> List[Dict[str, str]]:
    """veil 印象档案的风闻条目：擅=朝野共知；痼/绝艺=须熟识（召对≥20）或密查后方见。"""
    p = profile(name)
    if not p:
        return []
    out: List[Dict[str, str]] = []
    deep_known = familiarity >= 20 or intel_boost > 0
    hidden_skipped = False
    traits = list(p.get("traits") or [])
    # 擅（公开）在前；痼/绝艺（隐藏面）须熟识或密查
    for t in traits:
        if str(t.get("kind") or "") == "擅":
            out.append({"kind": "朝野共知",
                        "text": f"{t.get('trait_name')}——{str(t.get('consequence') or '')[:60]}"})
    for t in traits:
        kind = str(t.get("kind") or "")
        if kind == "擅":
            continue
        if deep_known:
            label = "深知其病" if kind == "痼" else "深知其能"
            out.append({"kind": label,
                        "text": f"{t.get('trait_name')}——{str(t.get('consequence') or '')[:60]}"})
        else:
            hidden_skipped = True
    if hidden_skipped:
        out.append({"kind": "讳莫如深", "text": "此人尚有未显之性，须多召对或遣人访查。"})
    return out[:6]


# ── 人才池（S7 闭环：荐人/征辟 → 入朝）──────────────────────────────────────

_EXCLUDED_FROM_POOL = {"朱由检"}  # 皇帝本人在基座中为赋闲宗室，须排除


def candidates(exclude_names: set, *, limit: int = 20, keyword: str = "") -> List[Dict[str, object]]:
    """在世、大明、赋闲/在野、不在朝中名册的人才。返回带能力/特质摘要的清单。"""
    conn = _conn()
    if conn is None:
        return []
    with _LOCK:
        rows = conn.execute(
            """SELECT p.npc_name, p.age_in_1628, p.rank_label, p.service_status_label,
                      p.honor_label, p.identity_label, p.native_place_name,
                      a.cai_zong, a.js_tao, a.js_zhi, a.js_shi, a.js_lue, a.js_wang
               FROM v_npc_runtime_profile p
               JOIN npc_ability a ON a.npc_id = p.npc_id
               WHERE p.existence_label='在世' AND p.power_label='大明'
                 AND p.service_status_label IN ('赋闲','在野')
               ORDER BY a.cai_zong DESC"""
        ).fetchall()
    out: List[Dict[str, object]] = []
    for row in rows:
        nm = str(row["npc_name"])
        if nm in exclude_names or nm in _EXCLUDED_FROM_POOL:
            continue
        if keyword and keyword not in nm:
            continue
        p = profile(nm)
        traits_digest = "、".join(
            f"{t['trait_name']}" for t in (p.get("traits") or [])[:3]
        ) if p else ""
        out.append({
            "name": nm,
            "age": row["age_in_1628"],
            "rank": str(row["rank_label"] or ""),
            "status": str(row["service_status_label"] or ""),
            "honor": str(row["honor_label"] or ""),
            "identity": str(row["identity_label"] or ""),
            "native_place": str(row["native_place_name"] or ""),
            "ability100": max(10, min(100, round((int(row["cai_zong"]) - 40) * 2.5))),
            "five_axes": {"韬": row["js_tao"], "治": row["js_zhi"], "识": row["js_shi"],
                          "略": row["js_lue"], "望": row["js_wang"]},
            "traits": traits_digest,
        })
        if len(out) >= limit:
            break
    return out


def random_candidate(exclude_names: set, rng) -> Optional[Dict[str, object]]:
    pool = candidates(exclude_names, limit=60)
    return rng.choice(pool) if pool else None


def build_game_character(name: str, current_year: int):
    """基座档案 → 游戏 Character（起复/征辟入朝用）。
    映射口径：ability=才总折百；loyalty=世轴（-2 以身许国高）；integrity=义轴（-2 杀身成仁高）；
    courage=豪轴；wisdom=识；style=人格简报对白风格行；summary=明史小传摘。"""
    from ming_sim.models import Character
    p = profile(name)
    if not p:
        return None
    persona = p.get("persona") or {}
    ability = p.get("ability") or {}

    def _axis(key: str) -> int:
        try:
            return int(persona.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    cai = int(ability.get("cai_zong") or 60)
    shi_axis = _axis("xinshu_shi")    # -2 以身许国 … +2 遁世绝俗
    yi_axis = _axis("xinshu_yi")      # -2 杀身成仁 … +2 唯利是趋
    hao_axis = _axis("qixing_hao")    # -2 龟缩 … +2 孤注
    style_bits = []
    for key in ("xinshu_xu_label", "qixing_zhi_label", "qixing_ji_label"):
        val = str(persona.get(key) or "").replace("(枢)", "").replace("(常)", "")
        if val:
            style_bits.append(val)
    traits = p.get("traits") or []
    trait_names = [str(t.get("trait_name") or "") for t in traits]
    faction = "东林党" if "东林清望" in trait_names else "无"
    birth_year = int(p.get("birth_year") or 0)
    return Character(
        name=name,
        office="赋闲听用",
        office_type="待铨",
        faction=faction,
        aliases=[],
        personal_skills=[t for t in trait_names if t][:5],
        loyalty=max(20, min(95, 55 - shi_axis * 14)),
        ability=max(10, min(100, round((cai - 40) * 2.5))),
        integrity=max(15, min(95, 55 - yi_axis * 16)),
        courage=max(15, min(95, 52 + hao_axis * 15)),
        style="、".join(style_bits) or "见档案",
        power_id="ming",
        location=str(p.get("native_place") or ""),
        birth_year=birth_year,
        status="active",
        summary=(str(p.get("biography") or "")[:120] or f"{name}，起复入朝。"),
        wisdom=max(20, min(95, int(ability.get("js_shi") or 10) * 5)),
        force=max(20, min(90, int(ability.get("js_lue") or 10) * 4)),
        charm=max(20, min(90, int(ability.get("js_wang") or 10) * 5)),
        luck=50,
    )
