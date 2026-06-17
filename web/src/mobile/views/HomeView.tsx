import { useEffect, useState } from "react";
import { useGame } from "../GameData";
import { Portrait } from "../Portrait";
import { loadEunuch, loadPlaystyleBrief } from "../api";
import type { AudienceLead, PlaystyleBriefCard, PublicCharacter, Suggestion, Tab } from "../api";
import { OutcomeSummary } from "./EdictsView";

const INFORMATIONAL_KINDS = ["复命", "捷报"];
const BRIEF_TABS: Tab[] = ["home", "desk", "audience", "edicts", "realm"];

export function HomeView({ go, summon }: { go: (t: Tab) => void; summon: (name: string, lead?: AudienceLead) => void }) {
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
  const openBrief = (card: PlaystyleBriefCard) => {
    const to = BRIEF_TABS.includes(card.tab) ? card.tab : "audience";
    if (to === "audience" && card.actor) {
      summon(card.actor, audienceLeadFromBrief(card));
      return;
    }
    go(to);
  };

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
              return (
                <li key={`${card.kind}-${card.ref_id || i}`} className={`m-brief-card tone-${card.tone || "info"}`}>
                  <button className="m-brief-main" onClick={() => openBrief(card)}>
                    {card.actor ? <Portrait name={card.actor} size={34} /> : <span className="m-brief-mark">{kindMark(card.kind)}</span>}
                    <span className="m-brief-body">
                      <span className="m-brief-head">
                        <span className="m-brief-title">{card.title}</span>
                        {card.meta && <span className="m-brief-meta">{card.meta}</span>}
                      </span>
                      <span className="m-brief-detail">{card.detail}</span>
                    </span>
                  </button>
                  <button className="m-chip m-brief-cta" onClick={() => openBrief(card)}>{card.cta || "处置"} ›</button>
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

function audienceLeadFromBrief(card: PlaystyleBriefCard): AudienceLead {
  return {
    kind: card.kind,
    title: card.title,
    detail: card.detail,
    tone: card.tone,
    actor: card.actor,
    target: card.target,
    meta: card.meta,
    ref_kind: card.ref_kind,
    ref_id: card.ref_id,
    prompts: briefPrompts(card),
  };
}

function briefPrompts(card: PlaystyleBriefCard): Suggestion[] {
  const actor = card.actor || "你";
  const target = card.target || "他人";
  const topic = briefTopic(card.title);
  if (card.kind === "hook") {
    return [
      { label: "试探把柄", text: `朕听到一些关于你的风闻。你若还愿替朕任事，今日就把话说明白。`, prefix: true },
      { label: "换取效忠", text: `此事朕可以暂不发作，但你须给朕一个可验的交代。你能办成什么？`, prefix: true },
    ];
  }
  if (card.kind === "rivalry") {
    return [
      { label: "追问旧怨", text: `朕闻你与${target}嫌隙已深。今日召你，是要听实话：此怨从何而起？`, prefix: true },
      { label: "逼其表态", text: `若朕令你暂收锋芒，同${target}共办一事，你肯不肯？条件是什么？`, prefix: true },
    ];
  }
  if (card.kind === "agenda") {
    return [
      { label: "追问私心", text: `朕闻你近来有「${topic}」之势。你自己说，是为国任事，还是另有所图？`, prefix: true },
      { label: "令其交账", text: `若朕现在用你办事，你准备如何避嫌、如何交账？`, prefix: true },
      { label: "问党援钱粮", text: `此事牵动谁的党援和钱粮？把实话说清楚。`, prefix: true },
    ];
  }
  return [
    { label: "问根由", text: `朕今日召${actor}来，正为这桩风向。你先把根由、风险、可用之处说清楚。`, prefix: true },
  ];
}

function briefTopic(title: string): string {
  const text = String(title || "").trim();
  const parts = text.split("：");
  return (parts[parts.length - 1] || text || "此事").trim();
}
