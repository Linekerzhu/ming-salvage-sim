import { useState } from "react";
import { useGame } from "../GameData";
import { Portrait } from "../Portrait";
import { decideMemorial } from "../api";
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
              {/* 内阁票拟＝有立场的处置建议，是裁断的主要依据 */}
              {m.piaoni && (
                <div className="m-piaoni">
                  <span className="m-piaoni-by">{m.piaoni_author || "内阁"}票拟（建议处置）</span>
                  {m.piaoni}
                </div>
              )}
              {m.full_text && <p className="m-memorial-text muted">原疏：{m.full_text}</p>}
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
              <button className="m-btn" disabled={busy} onClick={() => act("ack")}>已阅（免精力）</button>
            ) : ACTIONS.map((a) => (
              <button key={a.key} className="m-btn" disabled={busy} onClick={() => act(a.key)} title={a.hint}>
                {a.label}
              </button>
            ))}
          </div>
          {!info && <p className="m-mem-tip">发部议＝按你的批语生成旨意，颁诏后交内阁/司礼监落实，到期复命。</p>}
        </div>
      )}
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
