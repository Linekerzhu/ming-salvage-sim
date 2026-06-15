import { useEffect, useState } from "react";
import { useGame } from "../GameData";
import { Portrait } from "../Portrait";
import { loadEunuch } from "../api";
import type { PublicCharacter, Tab } from "../api";

const INFORMATIONAL_KINDS = ["复命", "捷报"];

export function HomeView({ go }: { go: (t: Tab) => void }) {
  const { desk, lifecycle, recentEvents, zhongxing } = useGame();
  const [eunuch, setEunuch] = useState<PublicCharacter | null>(null);
  useEffect(() => {
    loadEunuch().then((r) => setEunuch(r.eunuch)).catch(() => setEunuch(null));
  }, []);
  const replies = (desk?.pending || []).filter((m) => INFORMATIONAL_KINDS.includes(m.kind));
  const drowning = (desk?.pending || []).filter((m) => m.days_to_expire > 0 && m.days_to_expire <= 7).length;
  const fuming = lifecycle.filter((d) => d.status === "stalled" || (d.anomaly && d.anomaly !== "")).length;
  const live = lifecycle.filter((d) => ["in_transit", "executing"].includes(d.status)).length;

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
