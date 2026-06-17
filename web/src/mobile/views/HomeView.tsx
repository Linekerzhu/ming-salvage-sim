import { useEffect, useState } from "react";
import { useGame } from "../GameData";
import { Portrait } from "../Portrait";
import { loadEunuch, loadPlaystyleBrief } from "../api";
import type { PlaystyleBriefCard, PublicCharacter, Tab } from "../api";
import { OutcomeSummary } from "./EdictsView";

const INFORMATIONAL_KINDS = ["复命", "捷报"];
const BRIEF_TABS: Tab[] = ["home", "desk", "audience", "edicts", "realm"];

export function HomeView({ go }: { go: (t: Tab) => void }) {
  const { desk, lifecycle, recentEvents, zhongxing, worldVersion } = useGame();
  const [eunuch, setEunuch] = useState<PublicCharacter | null>(null);
  const [briefCards, setBriefCards] = useState<PlaystyleBriefCard[]>([]);
  useEffect(() => {
    loadEunuch().then((r) => setEunuch(r.eunuch)).catch(() => setEunuch(null));
  }, []);
  useEffect(() => {
    loadPlaystyleBrief(5).then((r) => setBriefCards(r.cards || [])).catch(() => setBriefCards([]));
  }, [worldVersion]);
  const replies = (desk?.pending || []).filter((m) => INFORMATIONAL_KINDS.includes(m.kind));
  const drowning = (desk?.pending || []).filter((m) => m.days_to_expire > 0 && m.days_to_expire <= 7).length;
  const fuming = lifecycle.filter((d) => d.status === "stalled" || (d.anomaly && d.anomaly !== "")).length;
  const live = lifecycle.filter((d) => ["in_transit", "executing"].includes(d.status)).length;
  const directiveById: Record<string, any> = {};
  for (const d of lifecycle || []) directiveById[String(d.id)] = d;

  const tasks: Array<{ urgent: boolean; text: string; cta: string; to: Tab }> = [];
  if (drowning > 0) tasks.push({ urgent: true, text: `${drowning} 封奏疏将淹没`, cta: "即刻批红", to: "desk" });
  if (fuming > 0) tasks.push({ urgent: true, text: `${fuming} 道旨意生变`, cta: "查看处置", to: "edicts" });
  if ((desk?.backlog || 0) > 0) tasks.push({ urgent: false, text: `御案待批 ${desk?.backlog} 封`, cta: "批红", to: "desk" });
  if (live > 0) tasks.push({ urgent: false, text: `在办旨意 ${live} 道`, cta: "看进度", to: "edicts" });
  if (tasks.length === 0) tasks.push({ urgent: false, text: "朝局暂安，可召大臣问对", cta: "召对", to: "audience" });

  return (
    <div className="m-view m-home">
      {eunuch && (
        <section className="m-card m-attend">
          <Portrait name={eunuch.name} size={48} />
          <div className="m-attend-id">
            <span className="m-attend-name">{eunuch.name}</span>
            <span className="m-attend-role">御前随侍 · 候陛下问对</span>
          </div>
          <button className="m-chip" onClick={() => go("audience")}>问随侍 ›</button>
        </section>
      )}

      <section className="m-card m-card-hero">
        <h2 className="m-card-title">今日要务</h2>
        <ul className="m-tasklist">
          {tasks.map((t, i) => (
            <li key={i} className={`m-task ${t.urgent ? "is-urgent" : ""}`}>
              <span className="m-task-text">{t.text}</span>
              <button className="m-chip" onClick={() => go(t.to)}>{t.cta}</button>
            </li>
          ))}
        </ul>
      </section>

      {briefCards.length > 0 && (
        <section className="m-card m-brief">
          <h2 className="m-card-title">朝局风向</h2>
          <ul className="m-brief-list">
            {briefCards.map((card, i) => {
              const to = BRIEF_TABS.includes(card.tab) ? card.tab : "audience";
              return (
                <li key={`${card.kind}-${card.ref_id || i}`} className={`m-brief-card tone-${card.tone || "info"}`}>
                  <button className="m-brief-main" onClick={() => go(to)}>
                    {card.actor ? <Portrait name={card.actor} size={34} /> : <span className="m-brief-mark">{kindMark(card.kind)}</span>}
                    <span className="m-brief-body">
                      <span className="m-brief-head">
                        <span className="m-brief-title">{card.title}</span>
                        {card.meta && <span className="m-brief-meta">{card.meta}</span>}
                      </span>
                      <span className="m-brief-detail">{card.detail}</span>
                    </span>
                  </button>
                  <button className="m-chip m-brief-cta" onClick={() => go(to)}>{card.cta || "处置"} ›</button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {zhongxing?.stage && (zhongxing.goals?.length ?? 0) > 0 && (
        <section className="m-card m-stage">
          <h2 className="m-card-title">
            本章方略 · {zhongxing.stage.title.replace(/（.*$/, "")}
            <span className="m-stage-prog">{zhongxing.goals.filter((g) => g.done).length}/{zhongxing.goals.length}</span>
          </h2>
          <ul className="m-goallist">
            {zhongxing.goals.map((g) => (
              <li key={g.id} className={`m-goal ${g.done ? "is-done" : ""}`}>
                <span className="m-goal-mark">{g.done ? "✓" : "○"}</span>
                <div className="m-goal-body">
                  <span className="m-goal-title">{g.title}</span>
                  {!g.done && g.hint && <span className="m-goal-hint">{g.hint}</span>}
                </div>
              </li>
            ))}
          </ul>
          <button className="m-chip" onClick={() => go("realm")}>看中兴气象 ›</button>
        </section>
      )}

      {replies.length > 0 && (
        <section className="m-card m-card-replies">
          <h2 className="m-card-title">诏书复命 · {replies.length}</h2>
          <ul className="m-replies">
            {replies.slice(0, 6).map((m) => (
              <li key={m.id} className="m-reply" onClick={() => go("desk")}>
                <span className="m-reply-by">{m.author || "—"}复命</span>
                {m.ref_kind === "directive" && directiveById[String(m.ref_id)]?.outcome_summary?.length > 0 && (
                  <OutcomeSummary items={directiveById[String(m.ref_id)].outcome_summary} compact />
                )}
                <p className="m-reply-text">{m.full_text || m.summary}</p>
              </li>
            ))}
          </ul>
          <button className="m-chip" onClick={() => go("desk")}>御案阅复命 ›</button>
        </section>
      )}

      <section className="m-card">
        <h2 className="m-card-title">近日朝报</h2>
        {recentEvents.length === 0 ? (
          <p className="m-empty">推动时日，诸事方有回音。</p>
        ) : (
          <ul className="m-feed">
            {recentEvents.slice(0, 12).map((e, i) => (
              <li key={i} className={`m-feed-item lv-${e.level}`}>
                <span className="m-feed-title">{e.title}</span>
                {e.detail && <span className="m-feed-detail">{e.detail}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function kindMark(kind: string): string {
  if (kind === "decision") return "裁";
  if (kind === "army") return "军";
  if (kind === "faction") return "党";
  if (kind === "hook") return "柄";
  if (kind === "rivalry") return "怨";
  return "机";
}
