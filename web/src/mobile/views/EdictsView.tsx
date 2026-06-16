import { useState } from "react";
import { useGame } from "../GameData";
import {
  confirmDirective,
  deleteDirective,
  interveneDirective,
  issueDecreeStream,
  patchDecree,
  rejectDirective,
  writeDecree,
} from "../api";
import type { DirectiveLifecycle } from "../api";

const STATUS_CN: Record<string, string> = {
  in_transit: "送达中", executing: "承办中", stalled: "封驳停摆", done: "已复命", aborted: "已收回",
};

// 在办旨意的干预（微操）——与「信任官僚体系、静待复命」相对。各有代价。
const INTERVENE: Array<{ key: string; label: string; extra?: Record<string, unknown> }> = [
  { key: "cuiban", label: "催办" },
  { key: "fund", label: "加拨", extra: { fund: 10 } },
  { key: "ducai", label: "独断" },
  { key: "abort", label: "收回" },
];

export function OutcomeSummary({ items, compact = false }: { items?: DirectiveLifecycle["outcome_summary"]; compact?: boolean }) {
  if (!items?.length) return null;
  return (
    <div className={`m-outcome-strip${compact ? " is-compact" : ""}`} aria-label="复命结果摘要">
      {!compact && <span className="m-outcome-head">结果</span>}
      {items.map((it, i) => (
        <span key={`${it.label}-${i}`} className={`m-outcome-chip tone-${it.tone || "neutral"}`}>
          {it.label}
        </span>
      ))}
    </div>
  );
}

function DirectiveCard({ d, today, onActed, ministers }: {
  d: DirectiveLifecycle; today: number; onActed: () => void; ministers: any[];
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState(false);
  const [picking, setPicking] = useState(false);
  const eta = d.eta_day - today;
  const live = ["in_transit", "executing", "stalled"].includes(d.status);
  const tone = d.status === "stalled" ? "t-urgent" : d.status === "done" ? "t-info"
    : d.status === "executing" ? "t-warn" : d.status === "aborted" ? "t-calm" : "t-calm";
  const overdue = live && d.eta_day > 0 && eta < 0;
  let anomalyLabel = "";
  try {
    const k = (JSON.parse(d.anomaly || "{}") || {}).kind;
    anomalyLabel = { block: "封驳抗命", delay: "迟滞拖延", surprise: "实情有变" }[k as string] || "";
  } catch { /* ignore */ }

  const act = async (action: string, extra?: Record<string, unknown>) => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await interveneDirective(d.id, action, extra || {});
      setMsg(r.message || "");
      onActed();
    } catch (e: any) {
      setMsg(String(e?.message || e || "处置失败"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`m-card m-directive ${tone}`}>
      <div className="m-directive-head">
        <span className={`m-status s-${d.status}`}>{STATUS_CN[d.status] || d.status}</span>
        {anomalyLabel && <span className="m-anomaly">⚠ {anomalyLabel}</span>}
        {live && d.eta_day > 0 && (
          <span className={`m-eta ${overdue ? "is-overdue" : ""}`}>{overdue ? `逾期 ${-eta} 日` : `约 ${eta} 日见分晓`}</span>
        )}
      </div>
      <p className="m-directive-text">{d.text}</p>
      <div className="m-directive-foot">
        <span>主办 {d.assignee || "—"}</span>
        {live && <span className="m-prog-pct">{d.progress}%</span>}
      </div>
      {live && (
        <div className="m-prog-track"><span className="m-prog-fill" style={{ width: `${d.progress}%` }} /></div>
      )}
      <OutcomeSummary items={d.outcome_summary} />
      {d.settle_note && <p className="m-settle">{d.settle_note}</p>}
      {msg && <p className="m-intervene-msg">{msg}</p>}
      {live && (
        <>
          <button className="m-intervene-toggle" onClick={() => setOpen((v) => !v)}>
            {open ? "收起干预" : "干预此旨 ›"}
          </button>
          {open && (
            <>
              <div className="m-actions m-actions-wrap">
                {INTERVENE.map((a) => (
                  <button key={a.key} className="m-btn" disabled={busy} onClick={() => act(a.key, a.extra)}>
                    {a.label}
                  </button>
                ))}
                <button className="m-btn" disabled={busy} onClick={() => setPicking((v) => !v)}>
                  {picking ? "取消换人" : "换人"}
                </button>
              </div>
              {picking && (
                <div className="m-reassign">
                  <p className="m-hint">改派能臣接办（交接折损进度，原主办怨望）：</p>
                  <div className="m-reassign-list">
                    {ministers
                      .filter((m) => m.name !== d.assignee)
                      .slice(0, 30)
                      .map((m) => (
                        <button
                          key={m.name}
                          className="m-reassign-row"
                          disabled={busy}
                          onClick={() => {
                            setPicking(false);
                            void act("reassign", { new_assignee: m.name });
                          }}
                        >
                          <span className="m-row-name">{m.name}</span>
                          <span className="m-row-sub">{[m.office, m.faction].filter(Boolean).join(" · ")}</span>
                        </button>
                      ))}
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

function Composer({ onDone }: { onDone: () => void }) {
  const [stage, setStage] = useState<"idle" | "drafting" | "editing" | "issuing">("idle");
  const [decree, setDecree] = useState("");
  const [report, setReport] = useState("");
  const [err, setErr] = useState("");

  const draft = async () => {
    setStage("drafting");
    setErr("");
    try {
      const r = await writeDecree();
      setDecree(r.decree || "");
      setStage("editing");
    } catch (e: any) {
      setErr(String(e?.message || e || "拟诏失败"));
      setStage("idle");
    }
  };

  const issue = async () => {
    setStage("issuing");
    setErr("");
    setReport("");
    try {
      if (decree.trim()) await patchDecree(decree.trim());
      const r = await issueDecreeStream((d) => setReport((s) => s + d));
      setReport(r.report || "诏书已颁。");
      onDone();
      setStage("idle");
      setDecree("");
    } catch (e: any) {
      setErr(String(e?.message || e || "颁诏失败"));
      setStage("editing");
    }
  };

  return (
    <div className="m-composer">
      {err && <p className="m-intervene-msg">{err}</p>}
      {stage === "idle" && (
        <button className="m-chip m-chip-wide" onClick={draft}>拟诏颁布 ›</button>
      )}
      {stage === "drafting" && <p className="m-hint">内阁拟诏中…</p>}
      {(stage === "editing" || stage === "issuing") && (
        <>
          <textarea className="m-decree-text" value={decree} rows={6}
            onChange={(e) => setDecree(e.target.value)} placeholder="诏书正文…" />
          <button className="m-chip m-chip-wide" disabled={stage === "issuing"} onClick={issue}>
            {stage === "issuing" ? "颁诏中…" : "颁布诏书"}
          </button>
        </>
      )}
      {report && <pre className="m-report">{report}</pre>}
    </div>
  );
}

export function EdictsView() {
  const { lifecycle, time, state, refresh } = useGame();
  const today = time?.current_day ?? 0;
  const ministers = (state?.ministers || []).filter((m: any) => m.status === "active");
  const live = lifecycle.filter((d) => ["in_transit", "executing", "stalled"].includes(d.status));
  const done = lifecycle.filter((d) => d.status === "done").slice(0, 12);
  const drafts = (state?.directives || []).filter((x: any) => x.status === "draft" || x.status === "pending");
  const pending = drafts.filter((x: any) => x.status === "pending");

  const decide = async (id: number, ok: boolean) => {
    try {
      await (ok ? confirmDirective(id) : rejectDirective(id));
      await refresh();
    } catch { /* ignore */ }
  };
  const removeDraft = async (id: number) => {
    try { await deleteDirective(id); await refresh(); } catch { /* ignore */ }
  };

  return (
    <div className="m-view m-edicts">
      <section className="m-card m-card-hero">
        <h2 className="m-card-title">草案待颁（{drafts.length}）</h2>
        {drafts.length === 0 ? (
          <p className="m-hint">召大臣议事拟旨，或自拟旨意，再拟诏颁布——下旨于人，是为人治。</p>
        ) : (
          <>
            {drafts.map((x: any) => (
              <div key={x.id} className="m-draft">
                <p className="m-draft-line">〔{x.actor || x.source || "拟"}〕{x.text}</p>
                {x.status === "pending" ? (
                  <div className="m-actions">
                    <button className="m-btn" onClick={() => decide(x.id, true)}>准</button>
                    <button className="m-btn" onClick={() => decide(x.id, false)}>驳</button>
                  </div>
                ) : (
                  <button className="m-mini" onClick={() => removeDraft(x.id)}>删去</button>
                )}
              </div>
            ))}
            {pending.length > 0 && <p className="m-hint">尚有大臣拟旨待核定（准/驳）方可颁诏。</p>}
            {pending.length === 0 && <Composer onDone={refresh} />}
          </>
        )}
      </section>

      <h2 className="m-section-title">在办旨意（{live.length}）</h2>
      {live.length === 0 ? (
        <p className="m-empty m-card">并无在办旨意。</p>
      ) : (
        live.map((d) => <DirectiveCard key={d.id} d={d} today={today} onActed={refresh} ministers={ministers} />)
      )}

      {done.length > 0 && (
        <>
          <h2 className="m-section-title">已复命</h2>
          {done.map((d) => <DirectiveCard key={d.id} d={d} today={today} onActed={refresh} ministers={ministers} />)}
        </>
      )}
    </div>
  );
}
