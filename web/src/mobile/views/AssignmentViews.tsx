import { useEffect, useRef, useState, useCallback } from "react";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";
import {
  loadAssignmentDashboard,
  loadAssignmentFocus,
  loadMinisterMerit,
  loadMeritOverview,
  loadPetitions,
  grantReward,
  applyPunishment,
  grantPetition,
  rejectPetition,
  issueAssignment,
  transformInvestigation,
} from "../api";
import type {
  AssignmentCard,
  AssignmentDashboard,
  AssignmentView,
  MeritLedger,
  Petition,
} from "../api";
import { useGame } from "../GameData";
import { MilestoneProgress } from "./MilestoneProgress";

gsap.registerPlugin(useGSAP);

const STATUS_CN: Record<string, string> = {
  in_transit: "送达中", executing: "承办中", stalled: "封驳/候议", done: "已复命", aborted: "已收回",
};
const CAN_TRANSFORM = new Set(["audit_purge", "secret_investigation"]);

/** 统一入场动画：scope 内 .m-enter 错峰浮入（遵循 gsap-react skill）。 */
function useEnter<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useGSAP(
    () => {
      gsap.from(".m-enter", { y: 14, opacity: 0, duration: 0.4, stagger: 0.05, ease: "power2.out" });
    },
    { scope: ref }
  );
  return ref;
}

// ════════════ 差使大厅 ════════════

export function AssignmentHallView() {
  const scopeRef = useEnter<HTMLDivElement>();
  const [view, setView] = useState<AssignmentView>("by_official");
  const [data, setData] = useState<AssignmentDashboard | null>(null);
  const [focus, setFocus] = useState<{ kind: string; items: any[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [composerOpen, setComposerOpen] = useState(false);
  const [mode, setMode] = useState<"table" | "chain">("table");

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      if (focus) {
        const r: any = await loadAssignmentFocus(focus.kind as any);
        setFocus({ kind: focus.kind, items: (r as any).items || [] });
      } else {
        setData(await loadAssignmentDashboard(view));
      }
    } catch { /* ignore */ } finally { setBusy(false); }
  }, [view, focus]);

  useEffect(() => { refresh(); }, [view, focus]);

  // 所有卡片的 id→title 映射（用于依赖链显示）+ 流转图输入
  const cardMap = new Map<number, AssignmentCard>();
  if (data) data.groups.forEach((g) => g.items.forEach((c) => cardMap.set(c.id, c)));
  if (focus) focus.items.forEach((c: any) => cardMap.set(c.id, c));
  const liveForDeps = Array.from(cardMap.values()).filter((c) =>
    ["in_transit", "executing", "stalled"].includes(c.status));
  const allCards = Array.from(cardMap.values());

  return (
    <div className="m-hall" ref={scopeRef}>
      <button className="m-hall-compose-toggle" onClick={() => setComposerOpen((v) => !v)}>
        {composerOpen ? "收起下达" : "＋ 下达差使"}
      </button>
      {composerOpen && <AssignmentComposer liveForDeps={liveForDeps} onDone={refresh} />}

      <div className="m-hall-mode">
        <button className={mode === "table" ? "active" : ""} onClick={() => setMode("table")}>名册</button>
        <button className={mode === "chain" ? "active" : ""} onClick={() => setMode("chain")}>流转图</button>
      </div>

      {mode === "chain" ? (
        <DependencyFlow cards={allCards} />
      ) : (
      <>
      <div className="m-hall-tabs">
        {(["by_official", "by_region", "by_category", "by_status"] as AssignmentView[]).map((v) => (
          <button key={v} className={view === v && !focus ? "active" : ""} onClick={() => { setFocus(null); setView(v); }}>
            {{ by_official: "按官员", by_region: "按地区", by_category: "按类别", by_status: "按状态" }[v]}
          </button>
        ))}
        <button className={focus?.kind === "needs_action" ? "active warn" : ""} onClick={() => setFocus({ kind: "needs_action", items: [] })}>待处置</button>
      </div>

      {busy && <p className="m-hall-loading">查阅差使…</p>}

      {focus ? (
        <div className="m-hall-focus">
          <h4>待处置队列（{focus.items.length}）</h4>
          {focus.items.length === 0 && <p className="m-empty">御前清净，暂无待办。</p>}
          {focus.items.map((c: any) => <HallCard key={c.uid || c.id} c={c} cardMap={cardMap} onChanged={refresh} />)}
        </div>
      ) : data && (
        <>
          <div className="m-hall-summary">
            共 {data.total} 件 · 承办中 {data.summary.executing || 0} · 封驳/候议 {data.summary.stalled || 0}
            {data.summary.done_unfollowed > 0 && <span className="warn"> · 待追问 {data.summary.done_unfollowed}</span>}
          </div>
          {data.groups.map((g) => (
            <div key={g.key} className="m-hall-group m-enter">
              <div className="m-hall-group-h">
                <span>{g.assignee || g.key}</span>
                {g.office && <span className="m-hall-office">{g.office}</span>}
                <span className={`m-hall-count ${g.overloaded ? "warn" : ""}`}>{g.active_count} 在办{g.overloaded ? " · 超载" : ""}</span>
              </div>
              {g.items.map((c) => <HallCard key={c.uid || c.id} c={c} cardMap={cardMap} onChanged={refresh} />)}
            </div>
          ))}
        </>
      )}
      </>
      )}
    </div>
  );
}

/** P2 下达器：统一下达差使，可选期限 + 依赖（P2.1），下达后回显领旨与超载预警（P2.2）。 */
function AssignmentComposer({ liveForDeps, onDone }: { liveForDeps: AssignmentCard[]; onDone: () => void }) {
  const { state } = useGame();
  const ministers = (state?.ministers || []).filter((m: any) => m.status === "active");
  const [kind, setKind] = useState("edict");
  const [text, setText] = useState("");
  const [actor, setActor] = useState("");
  const [deadline, setDeadline] = useState(0);
  const [deps, setDeps] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);

  const submit = async () => {
    if (!text.trim()) return;
    setBusy(true); setResult(null);
    try {
      const r = await issueAssignment({
        kind, text: text.trim(), actor: actor || undefined,
        deadline_days: deadline || undefined,
        depends_on: deps.length ? deps : undefined,
      });
      setResult(r);
      setText(""); setDeps([]); setDeadline(0);
      onDone();
    } catch { /* ignore */ } finally { setBusy(false); }
  };

  return (
    <div className="m-compose">
      <div className="m-compose-row">
        <label>类别</label>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="edict">颁诏（公开旨意）</option>
          <option value="audience_commission">召对交办</option>
          <option value="posting">常驻差使</option>
        </select>
        <label>主办</label>
        <select value={actor} onChange={(e) => setActor(e.target.value)}>
          <option value="">（自动选派）</option>
          {ministers.map((m: any) => <option key={m.name} value={m.name}>{m.name}（{m.office}）</option>)}
        </select>
        <label>限期（日，0=不限）</label>
        <input type="number" min={0} value={deadline} onChange={(e) => setDeadline(Math.max(0, +e.target.value))} />
      </div>
      <textarea placeholder="旨意正文，如：着户部十日内清查辽饷亏空" value={text}
        onChange={(e) => setText(e.target.value)} rows={2} />
      {liveForDeps.length > 0 && (
        <div className="m-compose-deps">
          <label>须待这些差使办成后开展（P2.1 依赖）：</label>
          <div className="m-compose-deps-list">
            {liveForDeps.map((c) => (
              <label key={c.uid} className={deps.includes(c.id) ? "checked" : ""}>
                <input type="checkbox" checked={deps.includes(c.id)}
                  onChange={(e) => setDeps((d) => e.target.checked ? [...d, c.id] : d.filter((x) => x !== c.id))} />
                {c.entry_label}·{c.text.slice(0, 14)}
              </label>
            ))}
          </div>
        </div>
      )}
      <button className="m-compose-submit primary" disabled={busy || !text.trim()} onClick={submit}>
        {busy ? "下达中…" : "颁出"}
      </button>
      {result && (
        <div className={`m-compose-result ${result.overload_warning ? "warn" : ""}`}>
          <p>已授{result.entry_label}（{result.assignee || "待派"}）{result.eta_day ? `· 约 ${result.eta_day} 日见分晓` : ""}。</p>
          {result.acceptance?.narrative && <p className="m-accept">{result.acceptance.narrative}</p>}
          {result.overload_warning && <p className="warn">⚠ {result.overload_warning}</p>}
        </div>
      )}
    </div>
  );
}

function HallCard({ c, cardMap, onChanged }: { c: any; cardMap?: Map<number, AssignmentCard>; onChanged?: () => void }) {
  const live = ["in_transit", "executing", "stalled"].includes(c.status);
  const [busy, setBusy] = useState(false);
  const depTitles = (c.depends_on || []).map((id: number) => {
    const dep = cardMap?.get(id);
    return dep ? dep.text.slice(0, 12) : `#${id}`;
  });
  const canTransform = c.status === "done" && c.source_table !== "secret_orders" && CAN_TRANSFORM.has(c.category);

  const transform = async () => {
    setBusy(true);
    try { await transformInvestigation(c.id); onChanged?.(); } catch { /* ignore */ } finally { setBusy(false); }
  };

  return (
    <div className={`m-hall-card m-enter ${c.overdue ? "is-overdue" : ""}`}>
      <div className="m-hall-card-h">
        <span className="m-hall-kind">{c.entry_label}</span>
        <span className="m-hall-status">{STATUS_CN[c.status] || c.status}</span>
        {c.overdue && <span className="m-hall-badge warn">逾期</span>}
        {c.deps_blocked && <span className="m-hall-badge warn">待前置</span>}
        {c.acceptance?.label && <span className="m-hall-badge">{c.acceptance.label}</span>}
      </div>
      <p className="m-hall-text">{c.text}</p>
      {c.assignee && <p className="m-hall-assignee">主办：{c.assignee}</p>}
      {c.deps_blocked && depTitles.length > 0 && (
        <p className="m-hall-deps">待前置：{depTitles.join("、")}</p>
      )}
      {live && <MilestoneProgress progress={c.progress} milestones={c.milestones} overdue={c.overdue} />}
      {c.settle_note && <p className="m-hall-settle">{c.settle_note}</p>}
      {canTransform && (
        <button className="m-hall-transform" disabled={busy} onClick={transform}>
          {busy ? "转化中…" : "据查转弹劾 ›"}
        </button>
      )}
    </div>
  );
}

// ════════════ 功过册 ════════════

export function MeritLedgerView() {
  const scopeRef = useEnter<HTMLDivElement>();
  const [list, setList] = useState<MeritLedger[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<MeritLedger | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try { const r = await loadMeritOverview(); setList(r.items || []); } catch { /* ignore */ }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const openMinister = async (name: string) => {
    if (open === name) { setOpen(null); setDetail(null); return; }
    setOpen(name); setDetail(await loadMinisterMerit(name));
  };

  const act = async (minister: string, fn: typeof grantReward, tier: string) => {
    setBusy(true);
    try { await fn(minister, tier); if (open) setDetail(await loadMinisterMerit(open)); await refresh(); }
    catch { /* ignore */ } finally { setBusy(false); }
  };

  return (
    <div className="m-merit" ref={scopeRef}>
      <h4>办差功过册（按功过分排序）</h4>
      {list.length === 0 && <p className="m-empty">尚无已结差使。</p>}
      {list.map((m) => (
        <div key={m.assignee} className="m-merit-row m-enter">
          <button className="m-merit-head" onClick={() => openMinister(m.assignee)}>
            <span className="m-merit-name">{m.assignee}</span>
            <span className="m-merit-score">功过 {m.merit_score}</span>
            <span className="m-merit-mini">成{m.totals.succeeded} 半{m.totals.partial} 败{m.totals.failed} 截{m.totals.skim}</span>
          </button>
          {open === m.assignee && detail && (
            <div className="m-merit-detail">
              <p>均实 {detail.avg_integrity}% · 逾期 {detail.totals.overdue} 旬 · 历奖 {detail.reward_count} 历罚 {detail.punish_count}</p>
              {detail.recent.slice(0, 5).map((r) => (
                <div key={r.directive_id} className="m-merit-rec">
                  <span className={`m-grade g-${r.grade}`}>{({ succeeded: "成", partial: "半", failed: "败" } as any)[r.grade]}</span>
                  <span>{r.text}</span>
                  {r.skim && <span className="warn">·截留</span>}
                  {r.overdue_deca > 0 && <span className="warn">·逾期{r.overdue_deca}旬</span>}
                </div>
              ))}
              <div className="m-merit-acts">
                <span>奖：</span>
                {(["merit_mark", "raise", "promote"] as const).map((t) => (
                  <button key={t} disabled={busy} onClick={() => act(m.assignee, grantReward, t)}>
                    {{ merit_mark: "记功", raise: "加俸", promote: "超擢" }[t]}
                  </button>
                ))}
                <span>罚：</span>
                {(["reprimand", "fine", "demote"] as const).map((t) => (
                  <button key={t} disabled={busy} className="danger" onClick={() => act(m.assignee, applyPunishment, t)}>
                    {{ reprimand: "申饬", fine: "罚俸", demote: "降黜" }[t]}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ════════════ 奏请 ════════════

export function PetitionsView() {
  const scopeRef = useEnter<HTMLDivElement>();
  const [items, setItems] = useState<Petition[]>([]);
  const [busy, setBusy] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try { const r = await loadPetitions("available"); setItems(r.items || []); } catch { /* ignore */ }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const grant = async (p: Petition) => {
    setBusy(p.id);
    try { await grantPetition(p.id, p.draft_directive); await refresh(); } catch { /* ignore */ } finally { setBusy(null); }
  };
  const reject = async (p: Petition) => {
    setBusy(p.id);
    try { await rejectPetition(p.id); await refresh(); } catch { /* ignore */ } finally { setBusy(null); }
  };

  return (
    <div className="m-petitions" ref={scopeRef}>
      <h4>臣工奏请（待御批）</h4>
      {items.length === 0 && <p className="m-empty">御案无奏请。臣工或有陈情，月杪或再上疏。</p>}
      {items.map((p) => (
        <div key={p.id} className="m-petition-card m-enter">
          <div className="m-petition-h">
            <span className="m-petition-title">{p.title}</span>
            <span className="m-petition-from">{p.proposer_name || p.proposer_office || "臣工"} 奏</span>
          </div>
          {p.description && <p className="m-petition-desc">{p.description}</p>}
          <p className="m-petition-draft">拟旨：{p.draft_directive}</p>
          <div className="m-petition-acts">
            <button disabled={busy === p.id} className="primary" onClick={() => grant(p)}>准（转差使）</button>
            <button disabled={busy === p.id} onClick={() => reject(p)}>驳回</button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ════════════ P2.1 差使流转图（依赖拓扑可视化）════════════

/** 拓扑分层（Kahn）：把参与依赖关系的差使按"须待层级"分层。孤立差使不计入。 */
function computeDependencyLayers(cards: AssignmentCard[]): AssignmentCard[][] {
  const byId = new Map(cards.map((c) => [c.id, c]));
  const involved = new Set<number>();
  for (const c of cards) {
    for (const d of c.depends_on || []) {
      if (byId.has(d)) { involved.add(d); involved.add(c.id); }
    }
  }
  if (involved.size === 0) return [];
  const placed = new Set<number>();
  const layers: AssignmentCard[][] = [];
  while (placed.size < involved.size) {
    const layer: AssignmentCard[] = [];
    for (const id of involved) {
      if (placed.has(id)) continue;
      const deps = (byId.get(id)!.depends_on || []).filter((d) => byId.has(d));
      if (deps.every((d) => placed.has(d))) layer.push(byId.get(id)!);
    }
    if (layer.length === 0) break; // 环保护
    layer.forEach((c) => placed.add(c.id));
    layers.push(layer);
  }
  return layers;
}

type EdgeGeom = { key: string; d: string; pending: boolean };

/** 差使流转图：分层案牌 + 墨线贝塞尔连线 + 朱砂/金印。GSAP 按层瀑布入场、连线描画。 */
export function DependencyFlow({ cards }: { cards: AssignmentCard[] }) {
  const scopeRef = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef<Map<number, HTMLElement>>(new Map());
  const layers = computeDependencyLayers(cards);
  const [edges, setEdges] = useState<EdgeGeom[]>([]);
  const [size, setSize] = useState({ w: 0, h: 0 });

  const measure = useCallback(() => {
    const cb = scopeRef.current?.getBoundingClientRect();
    if (!cb) return;
    setSize({ w: cb.width, h: cb.height });
    const byId = new Map(cards.map((c) => [c.id, c]));
    const geoms: EdgeGeom[] = [];
    for (const c of cards) {
      for (const d of c.depends_on || []) {
        const f = nodeRefs.current.get(d), t = nodeRefs.current.get(c.id);
        if (!f || !t) continue;
        const fb = f.getBoundingClientRect(), tb = t.getBoundingClientRect();
        const x1 = fb.left - cb.left + fb.width / 2, y1 = fb.bottom - cb.top;
        const x2 = tb.left - cb.left + tb.width / 2, y2 = tb.top - cb.top;
        const my = (y1 + y2) / 2;
        const depCard = byId.get(d);
        const pending = !!depCard && depCard.status !== "done";
        geoms.push({
          key: `${d}-${c.id}`,
          d: `M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`,
          pending,
        });
      }
    }
    setEdges(geoms);
  }, [cards]);

  useGSAP(
    () => {
      measure();
      gsap.from(".m-dep-layer", { opacity: 0, y: 22, duration: 0.5, ease: "power3.out", stagger: 0.14 });
      gsap.from(".m-dep-edge", {
        strokeDashoffset: 1, duration: 0.6, ease: "power2.out", stagger: 0.05, delay: 0.3,
      });
    },
    { scope: scopeRef, dependencies: [cards] }
  );

  useEffect(() => {
    const onResize = () => measure();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [measure]);

  if (layers.length === 0) {
    return (
      <div className="m-dep-empty">
        <p className="m-dep-empty-title">尚无关联差使</p>
        <p>下达时勾选「须待前置」，可令一道旨意等另一道办成后再开展——<br/>彻查→惩办→追赃，自成差使链。</p>
      </div>
    );
  }

  return (
    <div className="m-dep-flow" ref={scopeRef}>
      <svg className="m-dep-edges" width={size.w} height={size.h} aria-hidden>
        {edges.map((e) => (
          <path
            key={e.key} className={`m-dep-edge ${e.pending ? "pending" : "done"}`}
            d={e.d} pathLength={1} fill="none"
            strokeDasharray={1} strokeDashoffset={0}
          />
        ))}
      </svg>
      {layers.map((layer, li) => (
        <div className="m-dep-layer" key={li}>
          {layer.map((c) => {
            const done = c.status === "done";
            const blocked = !!c.deps_blocked;
            return (
              <div
                key={c.uid || c.id}
                ref={(el) => { if (el) nodeRefs.current.set(c.id, el); else nodeRefs.current.delete(c.id); }}
                className={`m-dep-node s-${c.status} ${done ? "is-done" : ""} ${blocked ? "is-blocked" : ""}`}
              >
                <span className="m-dep-seal">{done ? "结" : blocked ? "待" : "办"}</span>
                <span className="m-dep-kind">{c.entry_label}</span>
                <span className="m-dep-text">{c.text.slice(0, 16)}</span>
                <span className="m-dep-who">{c.assignee || "待派"}</span>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
