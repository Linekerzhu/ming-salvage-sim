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
import type { AudienceLead, DirectiveLifecycle, InterventionEffect, Suggestion } from "../api";
import { usePerson } from "../personCtx";

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

type SummonAudience = (name: string, lead?: AudienceLead) => void;
type InterventionOption = NonNullable<DirectiveLifecycle["intervention_options"]>[number];

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

function InterventionEffects({ items }: { items: InterventionEffect[] }) {
  if (!items.length) return null;
  return (
    <div className="m-outcome-strip is-compact m-intervention-effects" aria-label="处置影响">
      <span className="m-outcome-head">处置</span>
      {items.map((it, i) => (
        <span key={`${it.label}-${i}`} className={`m-outcome-chip tone-${it.tone || "neutral"}`}>
          {it.label}
        </span>
      ))}
    </div>
  );
}

function EffectPreview({ option }: { option?: InterventionOption }) {
  const items = option?.effects || [];
  if (!items.length) return null;
  return (
    <span className="m-effect-preview" aria-label={`${option?.label || "处置"}预期影响`}>
      {items.slice(0, 3).map((it, i) => (
        <span key={`${it.label}-${i}`} className={`m-effect-chip tone-${it.tone || "neutral"}`}>{it.label}</span>
      ))}
    </span>
  );
}

function PolicyDoctrineStrip({ data }: { data?: DirectiveLifecycle["policy_doctrine"] }) {
  const primary = data?.primary || {};
  const name = String(primary.name || "").trim();
  if (!name) return null;
  const status = primary.status === "orthodox" ? "正统" : "待议";
  const conflicts = Array.isArray(data?.conflicts) ? data!.conflicts! : [];
  const riskTags = Array.isArray(data?.risk_tags) ? data!.risk_tags! : [];
  const gate = data?.execution_gate || {};
  const temporaryException = !!data?.temporary_exception || !!gate.temporary_exception;
  const establishmentBlocked = (!!data?.establishment_blocked || !!gate.establishment_blocked) && !temporaryException;
  const resistanceDelta = Number(gate.resistance_delta || 0);
  const riskDelta = gate.check_risk_delta || {};
  const blockDelta = Number(riskDelta.block || 0);
  const delayDelta = Number(riskDelta.delay || 0);
  return (
    <div className="m-outcome-strip is-compact" aria-label="国策路线">
      <span className="m-outcome-head">国策</span>
      <span className={`m-outcome-chip tone-${conflicts.length ? "bad" : primary.status === "orthodox" ? "good" : "neutral"}`}>
        {name} · {status}
      </span>
      {temporaryException && <span className="m-outcome-chip tone-warn">权宜变通</span>}
      {establishmentBlocked && <span className="m-outcome-chip tone-bad">不可定策</span>}
      {conflicts.slice(0, 2).map((it, idx) => (
        <span key={`${it.name}-${idx}`} className="m-outcome-chip tone-bad">冲突：{it.name}</span>
      ))}
      {!conflicts.length && riskTags.slice(0, 2).map((tag, idx) => (
        <span key={`${tag}-${idx}`} className="m-outcome-chip tone-neutral">{tag}</span>
      ))}
      {resistanceDelta > 0 && <span className="m-outcome-chip tone-bad">阻力 +{resistanceDelta}</span>}
      {blockDelta > 0 && <span className="m-outcome-chip tone-bad">封驳 +{blockDelta}</span>}
      {!blockDelta && delayDelta > 0 && <span className="m-outcome-chip tone-warn">拖延 +{delayDelta}</span>}
    </div>
  );
}

function DirectiveCard({ d, today, onActed, ministers, activeMinisters, summon }: {
  d: DirectiveLifecycle; today: number; onActed: () => void; ministers: any[]; activeMinisters: Set<string>; summon?: SummonAudience;
}) {
  const openPerson = usePerson();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [effects, setEffects] = useState<InterventionEffect[]>([]);
  const [open, setOpen] = useState(false);
  const [picking, setPicking] = useState(false);
  const eta = d.eta_day - today;
  const live = ["in_transit", "executing", "stalled"].includes(d.status);
  const tone = d.status === "stalled" ? "t-urgent" : d.status === "done" ? "t-info"
    : d.status === "executing" ? "t-warn" : d.status === "aborted" ? "t-calm" : "t-calm";
  const overdue = live && d.eta_day > 0 && eta < 0;
  const anomalyLabel = directiveAnomalyLabel(d.anomaly);
  const assignee = String(d.assignee || "").trim();
  const canReachAssignee = !!assignee && activeMinisters.has(assignee);
  const canFollowupAssignee = live || d.status === "done";
  const canSummonAssignee = canFollowupAssignee && canReachAssignee && !!summon;
  const clue = d.blocker_clue || {};
  const clueName = String(clue.name || clue.label || "").trim();
  const canSummonClue = live && clue.kind === "person" && clueName && activeMinisters.has(clueName) && !!summon;
  const optionsByAction = new Map((d.intervention_options || []).map((opt) => [opt.action, opt]));
  const blockerAction = blockerActionLabel(d.blocker_action);
  const followupActions = followupActionLabels(d);

  const act = async (action: string, extra?: Record<string, unknown>) => {
    if (busy) return;
    setBusy(true);
    setEffects([]);
    try {
      const r = await interveneDirective(d.id, action, extra || {});
      setMsg(r.message || "");
      setEffects(r.effects || []);
      onActed();
    } catch (e: any) {
      setMsg(String(e?.message || e || "处置失败"));
      setEffects([]);
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
      <PolicyDoctrineStrip data={d.policy_doctrine} />
      <div className="m-directive-foot">
        <span>主办 {d.assignee || "—"}</span>
        {live && <span className="m-prog-pct">{d.progress}%</span>}
      </div>
      {assignee && (
        <div className="m-directive-acts" aria-label="主办官动作">
          {canSummonAssignee && (
            <button className="m-directive-act primary" onClick={() => summon!(assignee, directiveAudienceLead(d, today))}>
              {d.status === "done" ? "召复盘" : "召主办"}
            </button>
          )}
          {canReachAssignee && (
            <button className="m-directive-act" onClick={() => openPerson(assignee)}>
              查主办
            </button>
          )}
          {!canReachAssignee && <span className="m-directive-muted">主办暂不可召</span>}
        </div>
      )}
      {clueName && (
        <div className="m-blocker-clue">
          <div className="m-blocker-copy">
            <span className="m-blocker-k">阻力线索</span>
            <span className="m-blocker-v">{clueName}{clue.detail ? ` · ${clue.detail}` : ""}</span>
            {blockerAction && <span className="m-blocker-done">{blockerAction}</span>}
          </div>
          {live && (
            <>
              <button className="m-directive-act has-preview" disabled={busy || !!optionsByAction.get("bargain_blocker")?.disabled} onClick={() => act("bargain_blocker")}>
                <span>协调阻力</span>
                <EffectPreview option={optionsByAction.get("bargain_blocker")} />
              </button>
              <button className="m-directive-act danger has-preview" disabled={busy || !!optionsByAction.get("pressure_blocker")?.disabled} onClick={() => act("pressure_blocker")}>
                <span>申饬阻力</span>
                <EffectPreview option={optionsByAction.get("pressure_blocker")} />
              </button>
            </>
          )}
          {canSummonClue && (
            <button className="m-directive-act primary" onClick={() => summon!(clueName, blockerAudienceLead(d, clueName))}>
              召问阻力
            </button>
          )}
          {clue.kind === "person" && clueName && (
            <button className="m-directive-act" onClick={() => openPerson(clueName)}>
              查此人
            </button>
          )}
        </div>
      )}
      {live && (
        <div className="m-prog-track"><span className="m-prog-fill" style={{ width: `${d.progress}%` }} /></div>
      )}
      {followupActions.length > 0 && (
        <div className="m-followup-action" aria-label="复命追问状态">
          <span className="m-followup-k">追问</span>
          {followupActions.slice(0, 4).map((it, idx) => (
            <span key={`${it.label}-${idx}`} className={`m-followup-v tone-${it.tone}`}>{it.label}</span>
          ))}
          {d.followup_action?.day ? <span className="m-followup-day">最近第 {d.followup_action.day} 日</span> : null}
        </div>
      )}
      <OutcomeSummary items={d.outcome_summary} />
      {d.settle_note && <p className="m-settle">{d.settle_note}</p>}
      <InterventionEffects items={effects} />
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
                  <button
                    key={a.key}
                    className="m-btn has-preview"
                    disabled={busy || !!optionsByAction.get(a.key)?.disabled}
                    title={optionsByAction.get(a.key)?.disabled_reason || ""}
                    onClick={() => act(a.key, a.extra)}
                  >
                    <span>{a.label}</span>
                    <EffectPreview option={optionsByAction.get(a.key)} />
                  </button>
                ))}
                <button className="m-btn has-preview" disabled={busy} onClick={() => setPicking((v) => !v)}>
                  <span>{picking ? "取消换人" : "换人"}</span>
                  {!picking && <EffectPreview option={optionsByAction.get("reassign")} />}
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

function directiveAnomalyLabel(raw?: string): string {
  try {
    const k = (JSON.parse(raw || "{}") || {}).kind;
    return { block: "封驳抗命", delay: "迟滞拖延", surprise: "实情有变" }[k as string] || "";
  } catch {
    return "";
  }
}

function directiveAudienceLead(d: DirectiveLifecycle, today: number): AudienceLead {
  const statusLabel = STATUS_CN[d.status] || d.status;
  const progress = Math.max(0, Math.min(100, Number(d.progress || 0)));
  const anomaly = directiveAnomalyLabel(d.anomaly);
  const eta = Number(d.eta_day || 0) - today;
  const etaText = Number(d.eta_day || 0) > 0 ? (eta < 0 ? `逾期 ${-eta} 日` : `余 ${eta} 日`) : "未定期";
  const done = d.status === "done";
  const followup = followupActionLabel(d.followup_action);
  return {
    kind: "directive",
    title: done ? "复命后追问" : d.status === "stalled" ? "旨意停摆，召主办问罪" : "追问在办旨意",
    detail: done
      ? `${statusLabel} · 奏报 ${Math.max(0, Math.min(100, Number(d.reported_rate || 0)))}% · ${shortDirectiveText(d.text, 54)}`
      : `${statusLabel} · ${progress}% · ${shortDirectiveText(d.text, 54)}`,
    tone: done ? "info" : d.status === "stalled" ? "danger" : "warn",
    actor: d.assignee,
    meta: done ? followup?.label || "复命" : anomaly || etaText,
    ref_kind: "directive",
    ref_id: String(d.id),
    prompts: directivePrompts(d, anomaly),
  };
}

function directivePrompts(d: DirectiveLifecycle, anomaly: string): Suggestion[] {
  const text = shortDirectiveText(d.text, 34);
  if (d.status === "done") {
    return [
      { label: "问水分", text: `朕看过「${text}」的复命。真实成效如何，奏报里有没有水分？`, prefix: true },
      { label: "奖有功", text: `此事办得有功，朕要明赏。你说清谁最该记功，后续还需给什么名分？`, prefix: true },
      { label: "续下一手", text: `此事复命之后，下一步还缺什么，交给谁续办？`, prefix: true },
    ];
  }
  const prompts: Suggestion[] = [
    { label: "问进度", text: `朕交你承办的「${text}」，眼下到底办到几分？把实数奏来。`, prefix: true },
    { label: "问阻力", text: `此旨阻力在何处？是谁不肯配合，是钱粮、人手，还是你自己畏难？`, prefix: true },
  ];
  if (d.status === "stalled" || anomaly) {
    prompts.push({ label: "责停滞", text: `此旨已经${anomaly || "停滞"}，朕今日要听实话：你该担什么责，又要朕如何裁断？`, prefix: true });
  } else {
    prompts.push({ label: "限复命", text: `朕再给你一个期限。几日之内能有可验回音？若不能，你荐谁接办？`, prefix: true });
  }
  return prompts;
}

function blockerAudienceLead(d: DirectiveLifecycle, blocker: string): AudienceLead {
  const assignee = d.assignee || "主办";
  const text = shortDirectiveText(d.text, 34);
  return {
    kind: "directive_blocker",
    title: "追问旨意阻力",
    detail: `${assignee}称此旨受${blocker}牵制：${text}`,
    tone: "danger",
    actor: blocker,
    target: assignee,
    meta: "阻力线索",
    ref_kind: "directive",
    ref_id: String(d.id),
    prompts: [
      { label: "问掣肘", text: `朕闻${assignee}承办「${text}」时受你牵制。你当面说清楚：是何缘故？`, prefix: true },
      { label: "令其配合", text: `此旨是朕亲下，你若有异议当奏明，不得暗中掣肘。你能如何配合${assignee}办成？`, prefix: true },
      { label: "查其私心", text: `你阻此旨，是为公议，还是另有所图？把你背后的党援、人情和钱粮说清楚。`, prefix: true },
    ],
  };
}

function blockerActionLabel(raw?: DirectiveLifecycle["blocker_action"]): string {
  if (!raw?.action || !raw?.label) return "";
  const verb = raw.action === "pressure_blocker" ? "已申饬"
    : raw.action === "bargain_blocker" ? "已协调" : "已处置";
  const day = Number(raw.day || 0) > 0 ? ` · 第${raw.day}日` : "";
  return `${verb}${raw.label}${day}`;
}

function followupActionLabel(raw?: DirectiveLifecycle["followup_action"]): { label: string; tone: string } | null {
  const kind = String(raw?.kind || "");
  if (!kind) return null;
  if (kind === "rewarded") return { label: "已奖叙", tone: "good" };
  if (kind === "accounted") return { label: "功过已核", tone: "good" };
  if (kind === "followup_evasive") return { label: "避责未清", tone: "bad" };
  if (kind === "next_step") return { label: "已有续办", tone: "good" };
  return { label: "已点过", tone: "neutral" };
}

function followupActionLabels(d: DirectiveLifecycle): Array<{ label: string; tone: string }> {
  const history = Array.isArray(d.followup_history) ? d.followup_history : [];
  const labels = history
    .map((item) => followupActionLabel(item))
    .filter((item): item is { label: string; tone: string } => !!item);
  if (labels.length) return labels;
  const last = followupActionLabel(d.followup_action);
  return last ? [last] : [];
}

function shortDirectiveText(text: string, limit: number): string {
  const clean = String(text || "此旨").replace(/\s+/g, " ").trim();
  return clean.length > limit ? `${clean.slice(0, limit)}…` : clean;
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

export function EdictsView({ summon }: { summon?: SummonAudience }) {
  const { lifecycle, time, state, refresh } = useGame();
  const today = time?.current_day ?? 0;
  const ministers = (state?.ministers || []).filter((m: any) => m.status === "active");
  const activeMinisters = new Set(ministers.map((m: any) => String(m.name || "")).filter(Boolean));
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
                <PolicyDoctrineStrip data={x.policy_doctrine} />
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
        live.map((d) => (
          <DirectiveCard
            key={d.id}
            d={d}
            today={today}
            onActed={refresh}
            ministers={ministers}
            activeMinisters={activeMinisters}
            summon={summon}
          />
        ))
      )}

      {done.length > 0 && (
        <>
          <h2 className="m-section-title">已复命</h2>
          {done.map((d) => (
            <DirectiveCard
              key={d.id}
              d={d}
              today={today}
              onActed={refresh}
              ministers={ministers}
              activeMinisters={activeMinisters}
              summon={summon}
            />
          ))}
        </>
      )}
    </div>
  );
}
