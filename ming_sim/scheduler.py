"""异步 LLM 结算队列（S1.4）。L7。

半即时下 LLM 延迟必须与游戏时间解耦：规则层在 tick 中把需要文采的节点
（旨意异常陈情、办结奏报、票拟、派系出招……）enqueue 进 llm_jobs 表，
后台 worker 线程逐个消费；LLM 不可用/失败时落模板兜底文本，游戏不因此停摆。
结果以「奏报送达」形式回到御案——LLM 延迟被驿传时间合理化。

worker 用独立 GameDB 连接（不与请求线程共享 sqlite3.Connection）。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable, Dict, Optional

from agno.agent import Agent

from ming_sim.llm_model import create_chat_model
from ming_sim.models import LLMConfig
from ming_sim.pipeline_registry import llm_output_token_budget
from ming_sim.token_stats import tlog

ANOMALY_TEXT_PROMPT = """你是明末的承办官员，正就一道执行受阻的旨意向皇帝上一份简短奏疏（陈情/自辩/封驳理由）。
你会收到：旨意原文、异常类型（delay 拖延 / block 封驳 / surprise 实情有变）、主办官员姓名与官职。
要求：以该官员第一人称口吻写 80-150 字的奏疏正文（文言或浅近文言），给出听上去成理的缘由——可以是实情，也可以半真半假地推诿，但要符合异常类型。直接输出正文，不要解释。"""

SETTLE_NOTE_PROMPT = """你是明末的承办官员，旨意已办结，向皇帝上一份简短的复命奏报。
你会收到：旨意原文、主办官员姓名官职、真实完成率（actual）与奏报完成率（reported）。
要求：以该官员口吻写 60-120 字复命正文。注意：你写的是**奏报口径**（reported）——若 reported 高于 actual，要把折扣粉饰过去（言辞圆滑、报喜不报忧），不得透露真实数字。直接输出正文。"""


def enqueue_job(db, kind: str, payload: Dict[str, object]) -> int:
    cur = db.conn.execute(
        "INSERT INTO llm_jobs (kind, payload) VALUES (?, ?)",
        (kind, json.dumps(payload, ensure_ascii=False)),
    )
    db.conn.commit()
    return int(cur.lastrowid)


def _one_shot_agent(llm_config: LLMConfig, *, agent_id: str, prompt: str, minimum: int) -> Agent:
    return Agent(
        name=agent_id,
        id=agent_id,
        model=create_chat_model(
            llm_config, temperature=0.7, top_p=0.9,
            max_tokens=llm_output_token_budget(f"llm.{agent_id}", llm_config.max_tokens, minimum=minimum),
            enable_thinking=False,
        ),
        instructions=[prompt],
        add_history_to_context=False,
        markdown=False,
    )


def _run_text(llm_config: Optional[LLMConfig], *, agent_id: str, prompt: str,
              payload: Dict[str, object], minimum: int = 400) -> str:
    if llm_config is None:
        return ""
    from ming_sim.agents import run_agent_text
    agent = _one_shot_agent(llm_config, agent_id=agent_id, prompt=prompt, minimum=minimum)
    return run_agent_text(
        agent, json.dumps(payload, ensure_ascii=False, sort_keys=False), tag=agent_id
    ).strip()


# ── job handlers ─────────────────────────────────────────────────────────────

def _handle_anomaly_text(db, llm_config: Optional[LLMConfig], payload: Dict[str, object]) -> str:
    did = int(payload.get("directive_id") or 0)
    row = db.conn.execute(
        "SELECT text, assignee, anomaly FROM turn_directives WHERE id=?", (did,)
    ).fetchone()
    if row is None:
        return ""
    try:
        anomaly = json.loads(row["anomaly"] or "{}")
    except ValueError:
        anomaly = {}
    kind = str(payload.get("anomaly_kind") or anomaly.get("kind") or "delay")
    text = ""
    try:
        text = _run_text(llm_config, agent_id="anomaly-memorial", prompt=ANOMALY_TEXT_PROMPT,
                         payload={"directive": str(row["text"])[:200],
                                  "anomaly_kind": kind,
                                  "assignee": str(row["assignee"] or "")})
    except Exception as exc:
        tlog(f"[scheduler] anomaly_text LLM 失败，走模板：{exc}")
    if not text:
        fallback = {
            "delay": "臣奉旨办理，奈钱粮未齐、吏胥推诿，恳乞圣慈宽限时日，容臣督催。",
            "block": "该旨事体未协，科道封还。臣不敢擅行，恭候圣裁。",
            "surprise": "臣抵任查勘，地方情形与原议不符，须更张办法，谨奏闻。",
        }
        text = fallback.get(kind, fallback["delay"])
    if anomaly:
        anomaly["text"] = text
        db.conn.execute("UPDATE turn_directives SET anomaly=? WHERE id=?",
                        (json.dumps(anomaly, ensure_ascii=False), did))
        db.conn.commit()
    # 陈情/封驳以奏疏形式送达御案（异常节点的玩家入口）
    try:
        from ming_sim.memorials import create_memorial
        from ming_sim.upgrade_schema import KV_CURRENT_DAY, kv_int
        day = kv_int(db, KV_CURRENT_DAY, 0)
        assignee = str(row["assignee"] or "")
        org_row = db.conn.execute(
            "SELECT office FROM characters WHERE name=?", (assignee,)).fetchone()
        create_memorial(
            db, None, day=day, author_name=assignee,
            org=str(org_row["office"]) if org_row else "",
            kind="陈情" if kind != "block" else "弹章",
            urgency=2 if kind != "block" else 3,
            summary=f"为旨意〔{str(row['text'])[:20]}〕{ {'delay': '乞展限', 'block': '封驳抗辩', 'surprise': '奏报实情有变'}.get(kind, '陈情') }",
            full_text=text, ref_kind="directive", ref_id=str(did))
    except Exception as exc:
        tlog(f"[scheduler] 陈情疏入御案失败：{exc}")
    return text


def _handle_settle_note(db, llm_config: Optional[LLMConfig], payload: Dict[str, object]) -> str:
    did = int(payload.get("directive_id") or 0)
    row = db.conn.execute(
        "SELECT text, assignee, integrity_actual, integrity_reported FROM turn_directives WHERE id=?",
        (did,),
    ).fetchone()
    if row is None:
        return ""
    text = ""
    try:
        text = _run_text(llm_config, agent_id="settle-memorial", prompt=SETTLE_NOTE_PROMPT,
                         payload={"directive": str(row["text"])[:200],
                                  "assignee": str(row["assignee"] or ""),
                                  "actual": int(row["integrity_actual"]),
                                  "reported": int(row["integrity_reported"])})
    except Exception as exc:
        tlog(f"[scheduler] settle_note LLM 失败，走模板：{exc}")
    if not text:
        text = "臣谨奏：前奉谕旨，今已遵办完竣，地方安堵，伏乞圣鉴。"
    db.conn.execute("UPDATE turn_directives SET settle_note=? WHERE id=?", (text, did))
    db.conn.commit()
    return text


_HANDLERS: Dict[str, Callable] = {
    "anomaly_text": _handle_anomaly_text,
    "settle_note": _handle_settle_note,
}


def register_handler(kind: str, fn: Callable) -> None:
    """后续里程碑（票拟/派系出招/密查报告）注册自己的 handler。"""
    _HANDLERS[kind] = fn


def _load_extra_handlers() -> None:
    """惰性加载会自注册 handler 的模块（worker 线程可能先于主流程消费到这些任务）。"""
    for module in ("ming_sim.memorials", "ming_sim.theater", "ming_sim.veil",
                   "ming_sim.edict_outcome"):
        try:
            __import__(module)
        except Exception:
            continue


def process_pending(db, llm_config: Optional[LLMConfig], limit: int = 3) -> int:
    """消费至多 limit 个待办 job。返回处理数。同步调用（worker 与测试共用）。"""
    rows = db.conn.execute(
        "SELECT id, kind, payload FROM llm_jobs WHERE status='pending' ORDER BY id LIMIT ?",
        (int(limit),),
    ).fetchall()
    done = 0
    for row in rows:
        job_id, kind = int(row["id"]), str(row["kind"])
        db.conn.execute(
            "UPDATE llm_jobs SET status='running', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (job_id,))
        db.conn.commit()
        handler = _HANDLERS.get(kind)
        if handler is None:
            _load_extra_handlers()
            handler = _HANDLERS.get(kind)
        try:
            payload = json.loads(row["payload"] or "{}")
        except ValueError:
            payload = {}
        try:
            if handler is None:
                db.conn.execute(
                    "UPDATE llm_jobs SET status='failed', result=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (f"无 handler：{kind}", job_id))
                db.conn.commit()
                continue
            result = handler(db, llm_config, payload)
            db.conn.execute(
                "UPDATE llm_jobs SET status='done', result=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(result or "")[:2000], job_id))
            done += 1
        except Exception as exc:
            tlog(f"[scheduler] job#{job_id} {kind} 失败：{exc}")
            db.conn.execute(
                "UPDATE llm_jobs SET status='failed', result=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(exc)[:500], job_id))
        db.conn.commit()
    return done


# ── 后台 worker ──────────────────────────────────────────────────────────────

_WORKERS: Dict[str, "QueueWorker"] = {}
_WORKERS_LOCK = threading.Lock()


class QueueWorker:
    def __init__(self, db_path: str, llm_config: Optional[LLMConfig], interval: float = 4.0):
        self.db_path = db_path
        self.llm_config = llm_config
        self.interval = interval
        self._db = None  # worker 专用连接，stop() 时必须关闭（Windows 换档依赖文件句柄释放）
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"llm-queue:{db_path[-24:]}")

    def start(self) -> None:
        self._thread.start()

    def stop(self, join_timeout: float = 6.0) -> None:
        """停 worker 并释放其 sqlite 连接。换档/新开局前必须调用，
        否则 Windows 上 os.remove/os.replace 主库会因句柄被占而失败，
        POSIX 上 worker 会绑死在已 unlink 的旧 inode 上静默失效。"""
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=join_timeout)
        db = self._db
        self._db = None
        if db is not None:
            try:
                db.conn.close()
            except Exception:
                pass

    def _loop(self) -> None:
        from ming_sim.db import GameDB
        try:
            self._db = GameDB(self.db_path)  # worker 专用连接
        except Exception as exc:
            tlog(f"[scheduler] worker 连接失败：{exc}")
            return
        while not self._stop.is_set():
            try:
                if process_pending(self._db, self.llm_config, limit=3) == 0:
                    self._stop.wait(self.interval)
            except Exception as exc:
                tlog(f"[scheduler] worker 异常：{exc}")
                self._stop.wait(self.interval)


def ensure_worker(db_path: str, llm_config: Optional[LLMConfig]) -> None:
    """每个存档库一个常驻 worker（daemon），重复调用幂等（含配置热更新）。"""
    with _WORKERS_LOCK:
        worker = _WORKERS.get(db_path)
        if worker is not None and worker._thread.is_alive():
            worker.llm_config = llm_config  # 配置热更新
            return
        worker = QueueWorker(db_path, llm_config)
        _WORKERS[db_path] = worker
        worker.start()


def stop_worker(db_path: str) -> None:
    """停掉并摘除某存档库的 worker（换档/新开局/退到菜单前调用）。幂等。"""
    with _WORKERS_LOCK:
        worker = _WORKERS.pop(db_path, None)
    if worker is not None:
        worker.stop()
