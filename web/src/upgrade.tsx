/**
 * 升级总案（docs/upgrade-master-plan.md）半即时层前端：
 * - TimeBar：日历时钟与推进控件（S1）+ 势/任事意愿/注意力仪表（S5/S6）
 * - DeskDrawer：御案奏疏批红（S4）
 * - WatchDrawer：司天台——预警仪表/中兴指数/阶段诏题/在办旨意/文书互证/史笔（S2/S3/S9/S10/S11/S12）
 * 自包含组件，main.tsx 只做挂载；样式见 styles.css 末尾「upgrade-*」段。
 */

import React from "react";
import { Clock, Eye, FastForward, Hourglass, Play, Scroll, X } from "lucide-react";
import { api, formatApiError } from "./api/client";

// ── 类型 ─────────────────────────────────────────────────────────────────────

type TimeStatus = {
  current_day: number;
  day_in_month: number;
  days_in_month: number;
  year: number;
  month: number;
  turn: number;
  at_month_end: boolean;
  await_decree: boolean;
};

type TickEvent = {
  level: "red" | "yellow" | "blue";
  kind: string;
  title: string;
  detail: string;
  ref_kind: string;
  ref_id: string;
  day: number;
};

type AdvanceResult = {
  advanced: number;
  stopped_by: string;
  reports: Array<{ day: number; events: TickEvent[] }>;
  status: TimeStatus;
};

type Memorial = {
  id: number;
  author: string;
  org: string;
  kind: string;
  urgency: number;
  summary: string;
  full_text: string;
  piaoni: string;
  piaoni_author: string;
  arrived_day: number;
  shelved_days: number;
  status: string;
  ref_kind: string;
  ref_id: string;
};

type DeskPayload = {
  pending: Memorial[];
  recent_decided: Memorial[];
  backlog: number;
  attention_left: number;
  attention_per_day: number;
  shi: number;
  renshi_willingness: number;
  trap_hint: string;
};

type Gauge = {
  field: string;
  label: string;
  value: number;
  danger_at: number;
  direction: string;
  status: "safe" | "warn" | "danger";
};

type ThresholdItem = {
  rule_id: string;
  object_id: string;
  title: string;
  status: "safe" | "warn" | "danger";
  in_cooldown: boolean;
  gauges: Gauge[];
};

type LifecycleDirective = {
  id: number;
  text: string;
  status: string;
  category: string;
  progress: number;
  assignee: string;
  start_day: number;
  eta_day: number;
  resistance: number;
  reported_rate: number;
  anomaly: string;
  settle_note: string;
};

type ZhongxingPayload = {
  current: { total: number; parts: Record<string, number> };
  history: Array<{ turn: number; total: number }>;
  stage: { id: string; title: string; brief: string } | null;
  goals: Array<{ id: string; title: string; hint: string; done: boolean }>;
};

type FoundationCandidate = {
  name: string;
  age: number | null;
  rank: string;
  status: string;
  honor: string;
  identity: string;
  native_place: string;
  ability100: number;
  five_axes: Record<string, number>;
  traits: string;
};

type Contradiction = {
  id: number;
  entity_kind: string;
  entity_id: string;
  field: string;
  reported_value: number;
  author_org: string;
  author: string;
  reported_day: number;
  note: string;
};

const LIFECYCLE_LABEL: Record<string, string> = {
  in_transit: "在途",
  executing: "执行中",
  stalled: "封驳搁置",
  done: "已办结",
  aborted: "已收回",
};

// ── TimeBar ──────────────────────────────────────────────────────────────────

export function TimeBar({
  settling,
  onWorldChanged,
}: {
  settling: boolean;
  onWorldChanged: () => void;
}) {
  const [status, setStatus] = React.useState<TimeStatus | null>(null);
  const [beliefs, setBeliefs] = React.useState<{ shi: number; renshi: number } | null>(null);
  const [attention, setAttention] = React.useState<number | null>(null);
  const [events, setEvents] = React.useState<TickEvent[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  const refresh = React.useCallback(async () => {
    try {
      const [t, b, d] = await Promise.all([
        api<{ time: TimeStatus }>("/api/time"),
        api<{ shi: number; renshi: number }>("/api/beliefs"),
        api<DeskPayload>("/api/desk"),
      ]);
      setStatus(t.time);
      setBeliefs({ shi: b.shi, renshi: b.renshi });
      setAttention(d.attention_left);
    } catch (err) {
      setError(formatApiError(err, "时辰失准"));
    }
  }, []);

  React.useEffect(() => {
    refresh();
  }, [refresh]);

  // 月末结算结束（settling true→false）后必须重取时钟：
  // 否则时钟条停留在「月终待颁诏」直到手动刷新页面
  const prevSettling = React.useRef(settling);
  React.useEffect(() => {
    if (prevSettling.current && !settling) refresh();
    prevSettling.current = settling;
  }, [settling, refresh]);

  const advance = async (days: number) => {
    if (busy || settling) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<AdvanceResult>("/api/time/advance", {
        method: "POST",
        body: JSON.stringify({ days, stop_on_yellow: true }),
      });
      setStatus(result.status);
      const newEvents = result.reports.flatMap((r) => r.events);
      if (newEvents.length) setEvents((prev) => [...newEvents.reverse(), ...prev].slice(0, 12));
      await refresh();
      onWorldChanged();
    } catch (err) {
      setError(formatApiError(err, "推进失败"));
    } finally {
      setBusy(false);
    }
  };

  if (!status) return null;
  const pct = Math.round((status.day_in_month / status.days_in_month) * 100);
  return (
    <div className="upgrade-timebar" aria-label="半即时时钟">
      <span className="upgrade-timebar-date">
        <Clock size={13} />
        {status.year}年{status.month}月·第{status.day_in_month}日
      </span>
      <span className="upgrade-timebar-track" title={`本月第 ${status.day_in_month}/${status.days_in_month} 日`}>
        <span className="upgrade-timebar-fill" style={{ width: `${pct}%` }} />
      </span>
      {status.await_decree ? (
        <span className="upgrade-timebar-monthend">月终——请颁诏或无诏退朝以入新月</span>
      ) : (
        <span className="upgrade-timebar-controls">
          <button disabled={busy || settling} onClick={() => advance(1)} title="推进一日">
            <Play size={12} />1日
          </button>
          <button disabled={busy || settling} onClick={() => advance(5)} title="推进五日（遇事即停）">
            <FastForward size={12} />5日
          </button>
          <button disabled={busy || settling} onClick={() => advance(30)} title="推进至下一事件或月末">
            <Hourglass size={12} />至有事
          </button>
        </span>
      )}
      {beliefs && (
        <span className="upgrade-timebar-pills">
          <span className="upgrade-pill" title="势：百官对『抗命会被惩罚、服从是大流』的信念。诏令落地+，朝令夕改/弹章留中-">
            势 <b>{beliefs.shi}</b>
          </span>
          <span
            className={`upgrade-pill${beliefs.renshi <= 40 ? " danger" : ""}`}
            title="任事意愿：百官敢不敢担责。严惩失败者-，为忠臣买单+。过低则事事请旨、御案被淹（崇祯陷阱）"
          >
            任事 <b>{beliefs.renshi}</b>
          </span>
          {attention !== null && (
            <span className="upgrade-pill" title="今日批红余力：细读2、批答1、留中免。明日寅时刷新">
              精力 <b>{attention}</b>
            </span>
          )}
        </span>
      )}
      {error && <span className="upgrade-timebar-error">{error}</span>}
      {events.length > 0 && (
        <div className="upgrade-event-ticker">
          {events.slice(0, 4).map((ev, i) => (
            <div key={`${ev.day}-${i}`} className={`upgrade-event upgrade-event-${ev.level}`}>
              <b>第{ev.day % 30 === 0 ? 30 : ev.day % 30}日</b> {ev.title}
              {ev.detail ? <span className="upgrade-event-detail">{ev.detail}</span> : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── 轻量抽屉骨架（复用 right-drawer 样式）────────────────────────────────────

function UpgradeDrawer({
  open,
  onClose,
  title,
  icon,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <>
      {open && <button className="drawer-scrim" aria-label="收起" onClick={onClose} />}
      <aside className={`right-drawer upgrade-drawer ${open ? "open" : ""}`} aria-hidden={!open}>
        <div className="right-drawer-brand">
          <div className="panel-title">
            {icon}
            <span>{title}</span>
          </div>
          <button className="icon-button" aria-label="收起" onClick={onClose}>
            <X size={16} />
          </button>
        </div>
        <div className="right-drawer-body">{open ? children : null}</div>
      </aside>
    </>
  );
}

// ── DeskDrawer 御案 ──────────────────────────────────────────────────────────

const URGENCY_LABEL: Record<number, string> = { 1: "常", 2: "要", 3: "急" };

export function DeskDrawer({
  open,
  onClose,
  onWorldChanged,
}: {
  open: boolean;
  onClose: () => void;
  onWorldChanged: () => void;
}) {
  const [desk, setDesk] = React.useState<DeskPayload | null>(null);
  const [expanded, setExpanded] = React.useState<number>(0);
  const [message, setMessage] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const refresh = React.useCallback(async () => {
    try {
      setDesk(await api<DeskPayload>("/api/desk"));
    } catch (err) {
      setMessage(formatApiError(err, "御案取阅失败"));
    }
  }, []);

  React.useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);

  const decide = async (mid: number, action: string) => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await api<{ message: string }>(`/api/desk/${mid}/decide`, {
        method: "POST",
        body: JSON.stringify({ action }),
      });
      setMessage(result.message);
      await refresh();
      onWorldChanged();
    } catch (err) {
      setMessage(formatApiError(err, "批红失败"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <UpgradeDrawer open={open} onClose={onClose} title="御案·批红" icon={<Scroll size={15} />}>
      {desk && (
        <>
          <div className="upgrade-desk-head">
            <span>
              待批 <b>{desk.backlog}</b> 封 · 今日精力 <b>{desk.attention_left}</b>/{desk.attention_per_day}
            </span>
            <span>
              势 <b>{desk.shi}</b> · 任事意愿 <b>{desk.renshi_willingness}</b>
            </span>
          </div>
          {desk.trap_hint && <div className="upgrade-trap-hint">{desk.trap_hint}</div>}
          {message && <div className="upgrade-desk-message">{message}</div>}
          {desk.pending.length === 0 && <p className="upgrade-empty">御案清净，并无待批章奏。</p>}
          {desk.pending.map((m) => (
            <div key={m.id} className={`upgrade-memorial urgency-${m.urgency}`}>
              <button
                className="upgrade-memorial-head"
                onClick={() => setExpanded(expanded === m.id ? 0 : m.id)}
              >
                <span className="upgrade-memorial-urgency">{URGENCY_LABEL[m.urgency] || "常"}</span>
                <span className="upgrade-memorial-kind">{m.kind}</span>
                <span className="upgrade-memorial-summary">{m.summary}</span>
                <span className="upgrade-memorial-author">
                  {m.org}
                  {m.author}
                  {m.shelved_days > 0 ? ` · 留中${m.shelved_days}日` : ""}
                </span>
              </button>
              {expanded === m.id && (
                <div className="upgrade-memorial-body">
                  <p className="upgrade-memorial-text">{m.full_text}</p>
                  {m.piaoni && (
                    <p className="upgrade-memorial-piaoni">
                      【票拟·{m.piaoni_author || "内阁"}】{m.piaoni}
                      <span className="upgrade-piaoni-note">（票拟出自有立场的人之手）</span>
                    </p>
                  )}
                  <div className="upgrade-memorial-actions">
                    <button disabled={busy} onClick={() => decide(m.id, "approve")} title="耗精力1">
                      照准
                    </button>
                    <button disabled={busy} onClick={() => decide(m.id, "deny")} title="耗精力1；驳直言会塞言路">
                      驳回
                    </button>
                    <button disabled={busy} onClick={() => decide(m.id, "shelve")} title="不耗精力；积压有后果">
                      留中
                    </button>
                    <button disabled={busy} onClick={() => decide(m.id, "refer")} title="耗精力1；生成旨意草案随诏下达">
                      发部议
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
          {desk.recent_decided.length > 0 && (
            <details className="upgrade-decided">
              <summary>近批章奏（{desk.recent_decided.length}）</summary>
              {desk.recent_decided.map((m) => (
                <div key={m.id} className="upgrade-decided-row">
                  <span>{m.summary}</span>
                  <span className="upgrade-decided-status">{
                    { approved: "已照准", denied: "已驳回", referred: "已发部议" }[m.status] || m.status
                  }</span>
                </div>
              ))}
            </details>
          )}
        </>
      )}
    </UpgradeDrawer>
  );
}

// ── WatchDrawer 司天台 ───────────────────────────────────────────────────────

export function WatchDrawer({
  open,
  onClose,
  onWorldChanged,
}: {
  open: boolean;
  onClose: () => void;
  onWorldChanged: () => void;
}) {
  const [board, setBoard] = React.useState<ThresholdItem[]>([]);
  const [zx, setZx] = React.useState<ZhongxingPayload | null>(null);
  const [directives, setDirectives] = React.useState<LifecycleDirective[]>([]);
  const [contradictions, setContradictions] = React.useState<Contradiction[]>([]);
  const [reviews, setReviews] = React.useState<Array<{ year: number; period: number; text: string }>>([]);
  const [pool, setPool] = React.useState<FoundationCandidate[]>([]);
  const [poolAvailable, setPoolAvailable] = React.useState(false);
  const [message, setMessage] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const refresh = React.useCallback(async () => {
    try {
      const [t, z, d, c, s, f] = await Promise.all([
        api<{ board: ThresholdItem[] }>("/api/thresholds"),
        api<ZhongxingPayload>("/api/zhongxing"),
        api<{ directives: LifecycleDirective[] }>("/api/directives/lifecycle"),
        api<{ items: Contradiction[] }>("/api/veil/contradictions"),
        api<{ reviews: Array<{ year: number; period: number; text: string }> }>("/api/shibi"),
        api<{ available: boolean; candidates: FoundationCandidate[] }>("/api/foundation/candidates?limit=8"),
      ]);
      setBoard(t.board.filter((b) => b.status !== "safe").slice(0, 12));
      setZx(z);
      setDirectives(d.directives);
      setContradictions(c.items.slice(0, 8));
      setReviews(s.reviews.slice(-3).reverse());
      setPoolAvailable(f.available);
      setPool(f.candidates);
    } catch (err) {
      setMessage(formatApiError(err, "司天台失灵"));
    }
  }, []);

  const recruit = async (name: string) => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await api<{ message: string }>("/api/foundation/recruit", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      setMessage(result.message);
      await refresh();
      onWorldChanged();
    } catch (err) {
      setMessage(formatApiError(err, "征辟失败"));
    } finally {
      setBusy(false);
    }
  };

  React.useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);

  const intervene = async (id: number, action: string) => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await api<{ message: string }>(`/api/directives/${id}/intervene`, {
        method: "POST",
        body: JSON.stringify({ action, fund: action === "fund" ? 10 : 0 }),
      });
      setMessage(result.message);
      await refresh();
      onWorldChanged();
    } catch (err) {
      setMessage(formatApiError(err, "处置失败"));
    } finally {
      setBusy(false);
    }
  };

  const investigate = async (targetKind: string, targetId: string, line: string) => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await api<{ message: string }>("/api/veil/investigate", {
        method: "POST",
        body: JSON.stringify({ line, target_kind: targetKind, target_id: targetId }),
      });
      setMessage(result.message);
    } catch (err) {
      setMessage(formatApiError(err, "密查派遣失败"));
    } finally {
      setBusy(false);
    }
  };

  const live = directives.filter((d) => ["in_transit", "executing", "stalled"].includes(d.status));
  const recent = directives.filter((d) => ["done", "aborted"].includes(d.status)).slice(0, 5);

  return (
    <UpgradeDrawer open={open} onClose={onClose} title="司天台·朝局观测" icon={<Eye size={15} />}>
      {message && <div className="upgrade-desk-message">{message}</div>}

      {zx && (
        <section className="upgrade-section">
          <h4>中兴指数 <b className="upgrade-zx-total">{zx.current.total}</b></h4>
          <div className="upgrade-zx-parts">
            {Object.entries(zx.current.parts).map(([k, v]) => (
              <span key={k} className="upgrade-zx-part">
                {k} <b>{v}</b>
              </span>
            ))}
          </div>
          {zx.stage && (
            <div className="upgrade-stage">
              <p className="upgrade-stage-title">【阶段诏题】{zx.stage.title}</p>
              <p className="upgrade-stage-brief">{zx.stage.brief}</p>
              {zx.goals.map((g) => (
                <div key={g.id} className={`upgrade-goal${g.done ? " done" : ""}`}>
                  <span>{g.done ? "☑" : "☐"} {g.title}</span>
                  {!g.done && g.hint && <span className="upgrade-goal-hint">{g.hint}</span>}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      <section className="upgrade-section">
        <h4>预警仪表 <span className="upgrade-section-note">（呈报口径——账若做假，仪表同样骗你）</span></h4>
        {board.length === 0 && <p className="upgrade-empty">诸项尚在安全线内。</p>}
        {board.map((item) => (
          <div key={`${item.rule_id}:${item.object_id}`} className={`upgrade-watch upgrade-watch-${item.status}`}>
            <span className="upgrade-watch-title">{item.title}</span>
            {item.gauges.map((g) => (
              <span key={g.field} className={`upgrade-gauge upgrade-gauge-${g.status}`}>
                {g.label} {g.value}/{g.danger_at}
              </span>
            ))}
          </div>
        ))}
      </section>

      <section className="upgrade-section">
        <h4>在办旨意（{live.length}）</h4>
        {live.length === 0 && <p className="upgrade-empty">并无在办之旨。</p>}
        {live.map((d) => (
          <div key={d.id} className={`upgrade-directive status-${d.status}`}>
            <div className="upgrade-directive-head">
              <span className="upgrade-directive-status">{LIFECYCLE_LABEL[d.status] || d.status}</span>
              <span className="upgrade-directive-text">{d.text.slice(0, 40)}</span>
            </div>
            <div className="upgrade-directive-meta">
              主办 {d.assignee || "未定"} · {d.category} · 阻力 {d.resistance}
            </div>
            <div className="upgrade-progress-track">
              <span className="upgrade-progress-fill" style={{ width: `${d.progress}%` }} />
            </div>
            <div className="upgrade-directive-actions">
              <button disabled={busy} onClick={() => intervene(d.id, "cuiban")} title="加速但主办怨气+、百官观望+">
                催办
              </button>
              <button disabled={busy} onClick={() => intervene(d.id, "fund")} title="拨10万两疏通，阻力-">
                加拨
              </button>
              <button disabled={busy} onClick={() => intervene(d.id, "ducai")} title="势>70解锁：绕部议强推，任事意愿-">
                独断
              </button>
              <button disabled={busy} onClick={() => intervene(d.id, "abort")} title="朝令夕改，势-3">
                收回
              </button>
              <button
                disabled={busy}
                onClick={() => investigate("directive", String(d.id), "changwei")}
                title="厂卫核查办差实情（奏报可能粉饰）"
              >
                密核
              </button>
            </div>
          </div>
        ))}
        {recent.length > 0 && (
          <details className="upgrade-decided">
            <summary>近毕旨意（{recent.length}）</summary>
            {recent.map((d) => (
              <div key={d.id} className="upgrade-decided-row">
                <span>{d.text.slice(0, 36)}</span>
                <span className="upgrade-decided-status">
                  {LIFECYCLE_LABEL[d.status]} · 奏报{Math.round(d.reported_rate / 10)}成
                </span>
              </div>
            ))}
          </details>
        )}
      </section>

      <section className="upgrade-section">
        <h4>文书互证 <span className="upgrade-section-note">（每个数字都有作者）</span></h4>
        {contradictions.length === 0 && <p className="upgrade-empty">册籍诸数，暂无可疑之处。</p>}
        {contradictions.map((c) => (
          <div key={c.id} className="upgrade-ledger-row">
            <span className="upgrade-ledger-note">
              {c.note}：<b>{c.reported_value}</b>（{c.author_org}造）
            </span>
            <span className="upgrade-ledger-actions">
              <button
                disabled={busy}
                onClick={() => investigate(c.entity_kind, c.entity_id, "changwei")}
                title="厂卫盘验：快而准，但累积民怨物议，且可能被反侦"
              >
                厂卫
              </button>
              <button
                disabled={busy}
                onClick={() => investigate(c.entity_kind, c.entity_id, "kedao")}
                title="科道巡按：无政治成本，但慢且带派系滤镜"
              >
                科道
              </button>
            </span>
          </div>
        ))}
      </section>

      {poolAvailable && pool.length > 0 && (
        <section className="upgrade-section">
          <h4>遗贤在野 <span className="upgrade-section-note">（国朝人事档案·征辟耗精力2）</span></h4>
          {pool.map((c) => (
            <div key={c.name} className="upgrade-ledger-row">
              <span className="upgrade-ledger-note">
                <b>{c.name}</b>
                {c.age ? `（${c.age}岁）` : ""} {c.native_place} · {c.rank || "白身"} · {c.status}
                {c.traits ? <span className="upgrade-goal-hint">{c.traits}</span> : null}
              </span>
              <span className="upgrade-ledger-actions">
                <button disabled={busy} onClick={() => recruit(c.name)} title="征辟入朝听用">
                  征辟
                </button>
              </span>
            </div>
          ))}
        </section>
      )}

      {reviews.length > 0 && (
        <section className="upgrade-section">
          <h4>起居注·史臣曰 <span className="upgrade-section-note">（史书会怎么写你）</span></h4>
          {reviews.map((r, i) => (
            <blockquote key={i} className="upgrade-shibi">
              <span className="upgrade-shibi-date">{r.year}年{r.period}月</span>
              {r.text}
            </blockquote>
          ))}
        </section>
      )}
    </UpgradeDrawer>
  );
}
