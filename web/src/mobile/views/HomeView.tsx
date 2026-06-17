import { useEffect, useState } from "react";
import { useGame } from "../GameData";
import { Portrait } from "../Portrait";
import { loadEunuch, loadPlaystyleBrief } from "../api";
import type { AudienceLead, PlaystyleBriefCard, PublicCharacter, Suggestion, Tab } from "../api";
import { usePerson } from "../personCtx";
import { OutcomeSummary } from "./EdictsView";

const INFORMATIONAL_KINDS = ["复命", "捷报"];
const BRIEF_TABS: Tab[] = ["home", "desk", "audience", "edicts", "realm"];

export function HomeView({ go, summon }: { go: (t: Tab) => void; summon: (name: string, lead?: AudienceLead) => void }) {
  const { state, desk, lifecycle, recentEvents, zhongxing, worldVersion } = useGame();
  const openPerson = usePerson();
  const [eunuch, setEunuch] = useState<PublicCharacter | null>(null);
  const [briefCards, setBriefCards] = useState<PlaystyleBriefCard[]>([]);
  const [briefCount, setBriefCount] = useState({ shown: 0, total: 0, hidden: 0 });
  const [briefBuckets, setBriefBuckets] = useState<Array<{ kind: string; label: string; shown: number; total: number; hidden: number }>>([]);
  const [briefLimit, setBriefLimit] = useState(5);
  useEffect(() => {
    loadEunuch().then((r) => setEunuch(r.eunuch)).catch(() => setEunuch(null));
  }, []);
  useEffect(() => {
    loadPlaystyleBrief(briefLimit)
      .then((r) => {
        const cards = r.cards || [];
        setBriefCards(cards);
        setBriefCount({
          shown: Number(r.shown ?? cards.length),
          total: Number(r.total ?? cards.length),
          hidden: Number(r.hidden ?? 0),
        });
        setBriefBuckets((r.buckets || []).filter((b) => Number(b.total || 0) > 0));
      })
      .catch(() => {
        setBriefCards([]);
        setBriefCount({ shown: 0, total: 0, hidden: 0 });
        setBriefBuckets([]);
      });
  }, [worldVersion, briefLimit]);
  const replies = (desk?.pending || []).filter((m) => INFORMATIONAL_KINDS.includes(m.kind));
  const drowning = (desk?.pending || []).filter((m) => m.days_to_expire > 0 && m.days_to_expire <= 7).length;
  const fuming = lifecycle.filter((d) => d.status === "stalled" || (d.anomaly && d.anomaly !== "")).length;
  const live = lifecycle.filter((d) => ["in_transit", "executing"].includes(d.status)).length;
  const activeMinisters = new Set(
    ((state?.ministers || []) as PublicCharacter[])
      .filter((m) => m.status === "active" && m.name)
      .map((m) => String(m.name)),
  );
  const directiveById: Record<string, any> = {};
  for (const d of lifecycle || []) directiveById[String(d.id)] = d;

  const tasks: Array<{ urgent: boolean; text: string; cta: string; to: Tab }> = [];
  if (drowning > 0) tasks.push({ urgent: true, text: `${drowning} 封奏疏将淹没`, cta: "即刻批红", to: "desk" });
  if (fuming > 0) tasks.push({ urgent: true, text: `${fuming} 道旨意生变`, cta: "查看处置", to: "edicts" });
  if ((desk?.backlog || 0) > 0) tasks.push({ urgent: false, text: `御案待批 ${desk?.backlog} 封`, cta: "批红", to: "desk" });
  if (live > 0) tasks.push({ urgent: false, text: `在办旨意 ${live} 道`, cta: "看进度", to: "edicts" });
  if (tasks.length === 0) tasks.push({ urgent: false, text: "朝局暂安，可召大臣问对", cta: "召对", to: "audience" });
  const openBrief = (card: PlaystyleBriefCard) => {
    if (card.kind === "trap_remedy" && card.actor) {
      inspect(card.actor, "back");
      return;
    }
    const to = BRIEF_TABS.includes(card.tab) ? card.tab : "audience";
    if (to === "audience" && card.actor) {
      summonFromBrief(card, card.actor, card.target || "");
      return;
    }
    go(to);
  };
  const summonFromBrief = (card: PlaystyleBriefCard, actor: string, target = "") => {
    const name = String(actor || "").trim();
    if (!name) return;
    summon(name, audienceLeadFromBrief(card, name, target));
  };
  const inspect = (name?: string, focus?: "intrigue" | "back") => {
    const who = String(name || "").trim();
    if (who) openPerson(focus ? { name: who, focus } : who);
  };
  const canExpandBrief = briefLimit < 8 && briefCount.hidden > 0;
  const canCollapseBrief = briefLimit > 5 && briefCount.total > 5;

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
          <header className="m-brief-header">
            <h2 className="m-card-title m-brief-titlebar">
              朝局风向
              {briefCount.hidden > 0 && (
                <span className="m-brief-count">{briefCount.shown}/{briefCount.total}</span>
              )}
              {(canExpandBrief || canCollapseBrief) && (
                <button
                  type="button"
                  className="m-brief-toggle"
                  onClick={() => setBriefLimit((v) => (v > 5 ? 5 : 8))}
                >
                  {briefLimit > 5 ? "收起" : "展开"}
                </button>
              )}
            </h2>
            {briefBuckets.length > 0 && (
              <div className="m-brief-buckets" aria-label="朝局系统构成">
                {briefBuckets.map((bucket) => (
                  <span key={bucket.kind} className={`m-brief-bucket ${bucket.hidden > 0 ? "has-hidden" : ""}`}>
                    {bucket.label}
                    <b>{bucket.hidden > 0 ? `${bucket.shown}/${bucket.total}` : bucket.total}</b>
                  </span>
                ))}
              </div>
            )}
          </header>
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
                      {card.effects?.length ? (
                        <span className="m-brief-effects" aria-label="预期影响">
                          {card.effects.slice(0, 6).map((it, idx) => (
                            <span key={`${it.label}-${idx}`} className={`m-effect-chip tone-${it.tone || "neutral"}`}>
                              {it.label}
                            </span>
                          ))}
                        </span>
                      ) : null}
                    </span>
                  </button>
                  <div className="m-brief-actions">
                    {card.kind === "rivalry" && card.actor && card.target ? (
                      <>
                        <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.actor!, card.target)}>召{shortName(card.actor)}</button>
                        <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.target!, card.actor)}>召{shortName(card.target)}</button>
                        <button className="m-brief-action" onClick={() => inspect(card.actor)}>查{shortName(card.actor)}</button>
                        <button className="m-brief-action" onClick={() => inspect(card.target)}>查{shortName(card.target)}</button>
                      </>
                    ) : card.kind === "directive_blocker" ? (
                      <>
                        <button className="m-brief-action primary" onClick={() => go("edicts")}>处置旨意›</button>
                        {canSummon(card.actor, activeMinisters) && (
                          <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.actor!, card.target || "")}>召问阻力</button>
                        )}
                        {card.actor && <button className="m-brief-action" onClick={() => inspect(card.actor)}>查{shortName(card.actor)}</button>}
                        {card.target && <button className="m-brief-action" onClick={() => inspect(card.target)}>查主办</button>}
                      </>
                    ) : card.kind === "trap_remedy" && card.actor ? (
                      <>
                        <button className="m-brief-action primary" onClick={() => inspect(card.actor, "back")}>去买单›</button>
                        {canSummon(card.actor, activeMinisters) && (
                          <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.actor!, "")}>召来问对</button>
                        )}
                        <button className="m-brief-action" onClick={() => go("desk")}>看御案</button>
                      </>
                    ) : card.kind === "hook" && card.actor ? (
                      <>
                        <button className="m-brief-action primary" onClick={() => inspect(card.actor, "intrigue")}>用把柄</button>
                        {canSummon(card.actor, activeMinisters) && (
                          <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.actor!, "")}>召试探</button>
                        )}
                        <button className="m-brief-action" onClick={() => inspect(card.actor)}>查此人</button>
                      </>
                    ) : card.tab === "audience" && card.actor ? (
                      <>
                        <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.actor!, card.target || "")}>{card.cta || "召来问对"}</button>
                        <button className="m-brief-action" onClick={() => inspect(card.actor)}>查此人</button>
                        {card.target && <button className="m-brief-action" onClick={() => inspect(card.target)}>查{shortName(card.target)}</button>}
                      </>
                    ) : card.kind === "army" ? (
                      <>
                        <button className="m-brief-action primary" onClick={() => go("realm")}>看天下›</button>
                        {canSummon(card.actor, activeMinisters) && (
                          <>
                            <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.actor!, "")}>召主帅</button>
                            <button className="m-brief-action" onClick={() => inspect(card.actor)}>查主帅</button>
                          </>
                        )}
                      </>
                    ) : card.kind === "faction" ? (
                      <>
                        <button className="m-brief-action primary" onClick={() => go("desk")}>看御案›</button>
                        {canSummon(card.actor, activeMinisters) && (
                          <>
                            <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.actor!, "")}>召代表</button>
                            <button className="m-brief-action" onClick={() => inspect(card.actor)}>查代表</button>
                          </>
                        )}
                      </>
                    ) : (
                      <>
                        <button className="m-brief-action primary" onClick={() => openBrief(card)}>{card.cta || "处置"} ›</button>
                        {card.actor && <button className="m-brief-action" onClick={() => inspect(card.actor)}>查{shortName(card.actor)}</button>}
                      </>
                    )}
                  </div>
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
  if (kind === "directive_blocker") return "阻";
  if (kind === "trap") return "任";
  if (kind === "trap_remedy") return "担";
  return "机";
}

function audienceLeadFromBrief(card: PlaystyleBriefCard, actor = card.actor || "", target = card.target || ""): AudienceLead {
  return {
    kind: card.kind,
    title: card.title,
    detail: card.detail,
    tone: card.tone,
    actor,
    target,
    meta: card.meta,
    ref_kind: card.ref_kind,
    ref_id: card.ref_id,
    prompts: briefPrompts(card, actor, target),
  };
}

function briefPrompts(card: PlaystyleBriefCard, actor = card.actor || "你", target = card.target || "他人"): Suggestion[] {
  const speaker = actor || "你";
  const counterpart = target || "他人";
  const topic = briefTopic(card.title);
  if (card.kind === "hook") {
    return [
      { label: "试探把柄", text: `朕听到一些关于你的风闻。你若还愿替朕任事，今日就把话说明白。`, prefix: true },
      { label: "换取效忠", text: `此事朕可以暂不发作，但你须给朕一个可验的交代。你能办成什么？`, prefix: true },
    ];
  }
  if (card.kind === "rivalry") {
    return [
      { label: "追问旧怨", text: `朕闻你与${counterpart}嫌隙已深。今日召你，是要听实话：此怨从何而起？`, prefix: true },
      { label: "逼其表态", text: `若朕令你暂收锋芒，同${counterpart}共办一事，你肯不肯？条件是什么？`, prefix: true },
    ];
  }
  if (card.kind === "directive_blocker") {
    return [
      { label: "问掣肘", text: `朕闻${counterpart || "主办官"}承办此旨时受你牵制。你当面说清楚：是何缘故？`, prefix: true },
      { label: "令其配合", text: `此旨是朕亲下，你若有异议当奏明，不得暗中掣肘。你能如何配合办成？`, prefix: true },
      { label: "查其私心", text: `你阻此旨，是为公议，还是另有所图？把你背后的党援、人情和钱粮说清楚。`, prefix: true },
    ];
  }
  if (card.kind === "agenda") {
    return [
      { label: "追问私心", text: `朕闻你近来有「${topic}」之势。你自己说，是为国任事，还是另有所图？`, prefix: true },
      { label: "令其交账", text: `若朕现在用你办事，你准备如何避嫌、如何交账？`, prefix: true },
      { label: "问党援钱粮", text: `此事牵动谁的党援和钱粮？把实话说清楚。`, prefix: true },
    ];
  }
  if (card.kind === "trap_remedy") {
    return [
      { label: "问旧案", text: `朕今日问你旧事：当日办坏，是才力不逮、钱粮掣肘，还是有人借题问罪？`, prefix: true },
      { label: "试复用", text: `若朕替你担一点罪、再给你一件差遣，你敢不敢重新任事？你要什么条件？`, prefix: true },
    ];
  }
  if (card.kind === "army") {
    return [
      { label: "追欠饷", text: `朕闻你所领军镇欠饷压心。你据实奏来：欠从何来，兵心还能稳多久？`, prefix: true },
      { label: "问自专", text: `边镇离心，往往始于主帅自专。你今日当面说清楚：军中听朝廷，还是只听你？`, prefix: true },
      { label: "议制衡", text: `若朕遣监军、调饷、换将三策并举，你以为哪一策先行，哪一策最易激变？`, prefix: true },
    ];
  }
  if (card.kind === "faction") {
    const faction = String(card.ref_id || card.title || "本派").replace(/势大.*$|敌意.*$|怨气.*$/g, "") || "本派";
    return [
      { label: "问派内", text: `朕今日召你，是要听${faction}的实话：眼下谁能办事，谁在借势要价？`, prefix: true },
      { label: "许以差遣", text: `若朕借${faction}办一件急务，你们要什么名分，又能给朕什么可验的成效？`, prefix: true },
      { label: "立规矩", text: `${faction}可以任事，但不可挟势。你回去告诉众人：朕给差遣，也会查账。`, prefix: true },
    ];
  }
  return [
    { label: "问根由", text: `朕今日召${speaker}来，正为这桩风向。你先把根由、风险、可用之处说清楚。`, prefix: true },
  ];
}

function briefTopic(title: string): string {
  const text = String(title || "").trim();
  const parts = text.split("：");
  return (parts[parts.length - 1] || text || "此事").trim();
}

function shortName(name?: string): string {
  const text = String(name || "").trim();
  return text.length > 3 ? `${text.slice(0, 3)}…` : text;
}

function canSummon(name: string | undefined, activeMinisters: Set<string>): boolean {
  const text = String(name || "").trim();
  return !!text && activeMinisters.has(text);
}
