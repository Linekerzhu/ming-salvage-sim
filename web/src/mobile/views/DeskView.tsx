import { useEffect, useState } from "react";
import { useGame } from "../GameData";
import { Portrait } from "../Portrait";
import { decideMemorial, setDaipihong, loadEunuchCandidates } from "../api";
import type { Memorial, MemorialAction } from "../api";
import { OutcomeSummary } from "./EdictsView";

const ACTIONS: Array<{ key: MemorialAction; label: string; hint: string }> = [
  { key: "approve", label: "照准", hint: "依奏施行" },
  { key: "refer", label: "发部议", hint: "按批语交内阁/该部落实" },
  { key: "deny", label: "驳回", hint: "掷还不准" },
  { key: "shelve", label: "留中", hint: "暂压（久压淹没）" },
];

const INFORMATIONAL_KINDS = ["复命", "捷报"];

// 诏书去套语，取实质一段作「原旨」摘要，让复命与陛下的决策形成因果对照。
function decreeGist(text: string): string {
  let t = String(text || "").replace(/^[\s\S]*?诏曰[：:]\s*/u, "").replace(/^奉天承运[^\n]*\n+/u, "");
  t = t.replace(/\n+/g, " ").trim();
  return t.length > 64 ? t.slice(0, 64) + "…" : t;
}

function textPreview(text: string, limit = 86): string {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  return clean.length > limit ? clean.slice(0, limit) + "…" : clean;
}

function ActionPreview({ items }: { items?: Array<{ label: string; tone?: string }> }) {
  if (!items || items.length === 0) return null;
  return (
    <span className="m-effect-preview" aria-label="批红预期影响">
      {items.slice(0, 3).map((it, i) => (
        <span key={`${it.label}-${i}`} className={`m-effect-chip tone-${it.tone || "neutral"}`}>{it.label}</span>
      ))}
    </span>
  );
}

function PolicyDoctrineMemo({ data }: { data?: Memorial["policy_doctrine"] }) {
  const name = String(data?.name || "").trim();
  if (!name) return null;
  const opposesRoute = data?.direction === "oppose";
  const direction = opposesRoute ? "反对" : "推动";
  const stance = String(data?.author_stance?.stance || "");
  const stateLabel = String(data?.state_label || "").trim();
  const reformReady = !!data?.reform_ready;
  const blocked = !!data?.establishment_blocked;
  const reformAction = reformReady ? (opposesRoute ? "准此疏阻改弦" : "准此疏可改弦") : "";
  const stanceLabel = stance === "support" ? "本人倾向支持"
    : stance === "oppose" ? "本人倾向反对"
      : "本人立场可变";
  const tone = opposesRoute ? "bad" : "good";
  const stanceTone = stance === "oppose" ? "bad" : stance === "support" ? "good" : "neutral";
  const stateTone = reformReady ? (opposesRoute ? "warn" : "good") : blocked ? "bad" : "neutral";
  return (
    <div className="m-outcome-strip is-compact" aria-label="奏疏国策路线">
      <span className="m-outcome-head">路线</span>
      <span className={`m-outcome-chip tone-${tone}`}>{direction}：{name}</span>
      {data?.bar_value != null && <span className="m-outcome-chip tone-neutral">争议 {data.bar_value}/100</span>}
      {stateLabel && <span className={`m-outcome-chip tone-${stateTone}`}>{stateLabel}</span>}
      {reformAction && <span className={`m-outcome-chip tone-${opposesRoute ? "warn" : "good"}`}>{reformAction}</span>}
      <span className={`m-outcome-chip tone-${stanceTone}`}>{stanceLabel}</span>
    </div>
  );
}

function MemorialCard({ m, issue, directive, onActed }: { m: Memorial; issue?: any; directive?: any; onActed: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const info = INFORMATIONAL_KINDS.includes(m.kind);
  const drowning = !info && m.days_to_expire > 0 && m.days_to_expire <= 7;
  const urgent = !info && (m.urgency >= 3 || drowning || ["弹章", "告变", "密揭"].includes(m.kind));
  const warn = !info && !urgent && m.urgency === 2;
  const tone = info ? "t-info" : urgent ? "t-urgent" : warn ? "t-warn" : "t-calm";
  const urgencyTag = info ? "" : m.urgency >= 3 ? "急" : m.urgency === 2 ? "要" : "常";

  const act = async (a: MemorialAction) => {
    if (busy) return;
    setBusy(true);
    try {
      await decideMemorial(m.id, a, note.trim());
      setNote("");
      onActed();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`m-card m-memorial ${tone}`}>
      <button className="m-memorial-head" onClick={() => setOpen((v) => !v)}>
        <div className="m-memorial-meta">
          {urgencyTag && <span className={`m-urg u-${m.urgency >= 3 ? "hi" : m.urgency === 2 ? "mid" : "lo"}`}>{urgencyTag}</span>}
          <span className={`m-kind k-${m.kind}`}>{m.kind}</span>
          {m.author && <Portrait name={m.author} size={22} />}
          <span className="m-memorial-author">{m.author || "—"}{m.org ? `·${m.org}` : ""}</span>
          {drowning && <span className="m-badge-drown">⚠ {m.days_to_expire}日淹没</span>}
        </div>
        <p className="m-memorial-summary">{m.summary}</p>
        <PolicyDoctrineMemo data={m.policy_doctrine} />
        {!info && m.full_text && <p className="m-memorial-excerpt">{textPreview(m.full_text)}</p>}
        {info && directive?.outcome_summary?.length > 0 && <OutcomeSummary items={directive.outcome_summary} compact />}
      </button>
      {open && (
        <div className="m-memorial-body">
          {/* 关联局势的实情（让陛下据实裁断，而非空壳正文） */}
          {issue && (
            <div className="m-mem-issue">
              <span className="m-mem-issue-head">所关之事 · {issue.title}
                {issue.severity != null && <span className={`m-row-tag ${Number(issue.severity) >= 70 ? "danger" : Number(issue.severity) >= 45 ? "warn" : ""}`}>险 {issue.severity}</span>}
              </span>
              {issue.stage_text && <p className="m-mem-issue-text">{issue.stage_text}</p>}
            </div>
          )}
          {info ? (
            <>
              {/* 因果对照：陛下原旨 → 此番复命结果，让"我的决策起了作用"可见。 */}
              {directive && (
                <div className="m-mem-cause">
                  <span className="m-mem-cause-head">陛下原旨{directive.assignee ? ` · 交${directive.assignee}` : ""}</span>
                  <p className="m-mem-cause-text">{decreeGist(directive.text)}</p>
                  <span className="m-mem-cause-arrow">奉旨复命 ↓</span>
                </div>
              )}
              {directive?.outcome_summary?.length > 0 && <OutcomeSummary items={directive.outcome_summary} />}
              {m.full_text && <p className="m-memorial-text">{m.full_text}</p>}
            </>
          ) : (
            <>
              {m.full_text && (
                <div className="m-zoushu">
                  <span className="m-zoushu-head">奏疏原文 · {m.author || "佚名"}</span>
                  <p className="m-memorial-text">{m.full_text}</p>
                </div>
              )}
              {/* 内阁票拟＝有立场的处置建议，是裁断的重要参考，但不能盖过原疏 */}
              {m.piaoni && (
                <div className="m-piaoni">
                  <span className="m-piaoni-by">{m.piaoni_author || "内阁"}票拟（建议处置）</span>
                  {m.piaoni}
                </div>
              )}
              <textarea
                className="m-batch"
                value={note}
                rows={2}
                placeholder="朱批批语（可空）——发部议时作上谕交办，照准/驳回时附旨"
                onChange={(e) => setNote(e.target.value)}
              />
            </>
          )}
          <div className="m-actions m-actions-wrap">
            {info ? (
              <button className="m-btn has-preview" disabled={busy} onClick={() => act("ack")}>
                <span>已阅</span>
                <ActionPreview items={m.action_effects?.ack} />
              </button>
            ) : ACTIONS.map((a) => {
              const effects = m.action_effects?.[a.key] || [];
              return (
                <button key={a.key} className={`m-btn ${effects.length ? "has-preview" : ""}`} disabled={busy} onClick={() => act(a.key)} title={a.hint}>
                  <span>{a.label}</span>
                  <ActionPreview items={effects} />
                </button>
              );
            })}
          </div>
          {!info && <p className="m-mem-tip">发部议＝按你的批语生成旨意，颁诏后交内阁/司礼监落实，到期复命。</p>}
        </div>
      )}
    </div>
  );
}

// 司礼监代批红（E1）：御案壅塞时，可委内臣代行批红廓清积压（省精力）。
// **善恶取决于委任者**——忠谨守分者据实拟行、弹章呈御览、权阉只微升；
// 惯于弄权者留中劾阉、阉党自固、权阉日炽。委谁，由陛下定。
function DaipihongBar() {
  const { desk, refresh } = useGame();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [picking, setPicking] = useState(false);
  const [candidates, setCandidates] = useState<Array<{ name: string; office: string; is_eunuch: boolean }>>([]);
  const on = !!desk?.daipihong;
  const power = desk?.eunuch_power ?? 0;
  const keeper = desk?.daipihong_keeper || null;
  const upright = !!desk?.daipihong_keeper_upright;
  const tone = power >= 75 ? "danger" : power >= 50 ? "warn" : "ok";

  useEffect(() => {
    if (!picking || candidates.length) return;
    loadEunuchCandidates()
      .then((r) => setCandidates((r.candidates || []).filter((c) => c.is_eunuch)))
      .catch(() => {});
  }, [picking, candidates.length]);

  async function toggle() {
    if (busy) return;
    setBusy(true);
    try {
      const r = await setDaipihong(!on);
      setMsg(r.message || "");
      await refresh();
    } catch (e: any) {
      setMsg(e?.message || "操作未成。");
    } finally {
      setBusy(false);
    }
  }

  async function commit(name: string) {
    if (busy) return;
    setBusy(true);
    setPicking(false);
    try {
      // 委任并（重新）开启代批红，令新委任者即时生效。
      const r = await setDaipihong(true, name);
      setMsg(r.message || "");
      await refresh();
    } catch (e: any) {
      setMsg(e?.message || "委任未成。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`m-daipihong ${on ? "is-on" : ""} ${on && upright ? "is-upright" : ""}`}>
      <div className="m-daipihong-row">
        <span className="m-daipihong-label">
          司礼监代批红
          <span className={`m-daipihong-state t-${on ? "warn" : "calm"}`}>{on ? "在行" : "未开"}</span>
        </span>
        <button className="m-daipihong-btn" onClick={toggle} disabled={busy}>
          {on ? "收回批红权" : "命内廷代批"}
        </button>
      </div>
      {keeper && (
        <div className="m-daipihong-keeper">
          <span className="m-daipihong-keeper-label">委任：</span>
          <Portrait name={keeper} size={22} />
          <b>{keeper}</b>
          <span className={`m-daipihong-disp t-${upright ? "ok" : "warn"}`}>
            {upright ? "忠谨守分" : "须警惕弄权"}
          </span>
          <button className="m-daipihong-pick" onClick={() => setPicking((v) => !v)} disabled={busy}>
            {picking ? "取消" : "换委任者"}
          </button>
        </div>
      )}
      {picking && (
        <div className="m-daipihong-cands">
          {candidates.length === 0 ? (
            <p className="m-hint" style={{ margin: "4px 0" }}>宫中并无可委之内臣。</p>
          ) : (
            candidates.map((c) => (
              <button key={c.name} className="m-daipihong-cand" onClick={() => commit(c.name)} disabled={busy}>
                <Portrait name={c.name} size={24} interactive={false} />
                <span className="m-daipihong-cand-name">{c.name}</span>
                <span className="m-daipihong-cand-office">{c.office}</span>
              </button>
            ))
          )}
        </div>
      )}
      <div className="m-daipihong-meter">
        <span className="m-daipihong-meter-label">权阉之势</span>
        <span className="m-daipihong-track"><span className={`m-daipihong-fill t-${tone}`} style={{ width: `${power}%` }} /></span>
        <b className={`t-${tone}`}>{power}</b>
      </div>
      <p className="m-daipihong-note">
        {on
          ? upright
            ? "委忠谨内臣代廓积压：寻常奏章据票拟据实拟行、弹章仍呈御览，权柄未旁落、言路不壅——然代行批红终非亲裁。"
            : "此委任者惯于弄权：积压虽廓，然劾阉之疏被留中销折、阉党借势自固，权阉日盛——养虎须慎。"
          : "御案壅塞时，可命内臣代批红廓清积压（省精力）。委忠谨者可期据实拟行；委权阉则恐养虎——委谁全在陛下。"}
      </p>
      {msg && <p className="m-daipihong-msg">{msg}</p>}
    </div>
  );
}

export function DeskView() {
  const { desk, state, lifecycle, refresh } = useGame();
  const pending = desk?.pending || [];
  const issueById: Record<string, any> = {};
  for (const it of (state?.issues || [])) issueById[String(it.id)] = it;
  const directiveById: Record<string, any> = {};
  for (const d of (lifecycle || [])) directiveById[String(d.id)] = d;
  // 复命置顶（结果通知），其余后端已按急/淹没排序。
  const sorted = [...pending].sort((a, b) => (b.kind === "复命" ? 1 : 0) - (a.kind === "复命" ? 1 : 0));

  return (
    <div className="m-view m-desk">
      <div className="m-desk-status">
        <span>御案待批 <b>{desk?.backlog ?? 0}</b></span>
        <span>今日精力 <b>{desk?.attention_left ?? 0}</b>/{desk?.attention_per_day ?? 12}</span>
      </div>
      <DaipihongBar />
      {desk?.trap_hint && <div className="m-trap-hint">{desk.trap_hint}</div>}
      {sorted.length === 0 ? (
        <div className="m-empty m-card">
          <p style={{ margin: 0 }}>御案清明，并无待批章奏。</p>
          <p className="m-hint" style={{ marginTop: 6 }}>推时日，百官奏章自当陆续送达；亦可往「召对」问对、下旨。</p>
        </div>
      ) : (
        sorted.map((m) => (
          <MemorialCard
            key={m.id}
            m={m}
            issue={m.ref_kind === "issue" ? issueById[String(m.ref_id)] : undefined}
            directive={m.ref_kind === "directive" ? directiveById[String(m.ref_id)] : undefined}
            onActed={refresh}
          />
        ))
      )}
    </div>
  );
}
