import { useEffect, useState } from "react";
import { useGame } from "../GameData";
import { Portrait } from "../Portrait";
import { loadEunuch, loadPlaystyleBrief } from "../api";
import type { AudienceLead, PlaystyleBriefCard, PublicCharacter, Suggestion, Tab, TickEvent } from "../api";
import { usePerson } from "../personCtx";
import { OutcomeSummary } from "./EdictsView";

const INFORMATIONAL_KINDS = ["复命", "捷报"];
const BRIEF_TABS: Tab[] = ["home", "desk", "audience", "edicts", "realm"];
type BriefBucket = { kind: string; label: string; shown: number; total: number; hidden: number; rank_level?: string; rank_label?: string; rank_count?: number };

export function HomeView({ go, summon }: { go: (t: Tab) => void; summon: (name: string, lead?: AudienceLead) => void }) {
  const { state, desk, lifecycle, recentEvents, zhongxing, worldVersion } = useGame();
  const openPerson = usePerson();
  const [eunuch, setEunuch] = useState<PublicCharacter | null>(null);
  const [briefCards, setBriefCards] = useState<PlaystyleBriefCard[]>([]);
  const [briefLead, setBriefLead] = useState<PlaystyleBriefCard | null>(null);
  const [briefCount, setBriefCount] = useState({ shown: 0, total: 0, hidden: 0 });
  const [briefBuckets, setBriefBuckets] = useState<BriefBucket[]>([]);
  const [briefRanks, setBriefRanks] = useState<Array<{ level: string; label: string; count: number }>>([]);
  const [briefLimit, setBriefLimit] = useState(5);
  const [briefKind, setBriefKind] = useState("");
  useEffect(() => {
    loadEunuch().then((r) => setEunuch(r.eunuch)).catch(() => setEunuch(null));
  }, []);
  useEffect(() => {
    loadPlaystyleBrief(briefLimit, briefKind)
      .then((r) => {
        const cards = r.cards || [];
        const buckets = (r.buckets || []).filter((b) => Number(b.total || 0) > 0);
        const ranks = (r.ranks || []).filter((rank) => Number(rank.count || 0) > 0);
        setBriefCards(cards);
        setBriefLead(r.lead || cards[0] || null);
        setBriefCount({
          shown: Number(r.shown ?? cards.length),
          total: Number(r.total ?? cards.length),
          hidden: Number(r.hidden ?? 0),
        });
        setBriefBuckets(buckets);
        setBriefRanks(ranks);
        if (briefKind && !buckets.some((b) => b.kind === briefKind)) setBriefKind("");
      })
      .catch(() => {
        setBriefCards([]);
        setBriefLead(null);
        setBriefCount({ shown: 0, total: 0, hidden: 0 });
        setBriefBuckets([]);
        setBriefRanks([]);
      });
  }, [worldVersion, briefLimit, briefKind]);
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
  const activeBriefBucket = briefBuckets.find((b) => b.kind === briefKind);
  const allBriefTotal = briefBuckets.reduce((sum, b) => sum + Number(b.total || 0), 0);
  const leadUrgency = briefLead ? briefUrgency(briefLead.urgency) : null;
  const leadContract = briefLead ? briefContract(briefLead) : "";
  const chooseBriefKind = (kind: string) => {
    setBriefKind((current) => (current === kind ? "" : kind));
    setBriefLimit(5);
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
          <header className="m-brief-header">
            <h2 className="m-card-title m-brief-titlebar">
              朝局风向{activeBriefBucket ? ` · ${activeBriefBucket.label}` : ""}
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
            {briefRanks.length > 0 && (
              <div className="m-brief-ranks" aria-label="朝局危急分布">
                {briefRanks.map((rank) => (
                  <span key={rank.level} className={`m-brief-rank level-${rank.level}`}>
                    {rank.label}<b>{rank.count}</b>
                  </span>
                ))}
              </div>
            )}
            {briefLead && (
              <button type="button" className="m-brief-lead" onClick={() => openBrief(briefLead)}>
                <span className="m-brief-lead-main">
                  <span className="m-brief-lead-kicker">首要</span>
                  {leadUrgency && (
                    <span className={`m-brief-lead-rank level-${leadUrgency.level}`}>{leadUrgency.label}{leadUrgency.score}</span>
                  )}
                  <b>{briefLead.title}</b>
                  <em>{briefLead.cta || "处置"}›</em>
                </span>
                {briefLead.effects?.length ? (
                  <span className="m-brief-lead-effects" aria-label="首要影响">
                    {briefLead.effects.slice(0, 2).map((it, idx) => (
                      <span key={`${it.label}-${idx}`} className={`m-effect-chip tone-${it.tone || "neutral"}`}>
                        {it.label}
                      </span>
                    ))}
                  </span>
                ) : null}
                {leadContract && <span className="m-brief-contract">{leadContract}</span>}
                {briefLead.stakes?.length ? (
                  <span className="m-brief-lead-stakes" aria-label="首要两难">
                    {briefLead.stakes.slice(0, 3).map((it, idx) => (
                      <span key={`${it.label}-${idx}`} className={`m-stake-chip tone-${it.tone || "neutral"}`}>
                        {it.label}
                      </span>
                    ))}
                  </span>
                ) : null}
              </button>
            )}
            {briefBuckets.length > 0 && (
              <div className="m-brief-buckets" aria-label="朝局系统构成">
                <button
                  type="button"
                  className={`m-brief-bucket ${!briefKind ? "is-active" : ""}`}
                  aria-pressed={!briefKind}
                  onClick={() => chooseBriefKind("")}
                >
                  全局<b>{allBriefTotal}</b>
                </button>
                {briefBuckets.map((bucket) => (
                  <button
                    key={bucket.kind}
                    type="button"
                    className={`m-brief-bucket ${bucket.hidden > 0 ? "has-hidden" : ""} ${briefKind === bucket.kind ? "is-active" : ""}`}
                    aria-pressed={briefKind === bucket.kind}
                    onClick={() => chooseBriefKind(bucket.kind)}
                  >
                    {bucket.label}
                    {bucket.rank_label && Number(bucket.rank_count || 0) > 0 && (
                      <span className={`m-brief-bucket-rank level-${bucket.rank_level || "info"}`}>
                        {bucket.rank_label}{bucket.rank_count}
                      </span>
                    )}
                    <b>{bucket.hidden > 0 ? `${bucket.shown}/${bucket.total}` : bucket.total}</b>
                  </button>
                ))}
              </div>
            )}
          </header>
          <ul className="m-brief-list">
            {briefCards.map((card, i) => {
              const urgency = briefUrgency(card.urgency);
              const contract = briefContract(card);
              return (
                <li key={`${card.kind}-${card.ref_id || i}`} className={`m-brief-card tone-${card.tone || "info"}`}>
                  <button className="m-brief-main" onClick={() => openBrief(card)}>
                    {card.actor ? <Portrait name={card.actor} size={34} /> : <span className="m-brief-mark">{kindMark(card.kind)}</span>}
                    <span className="m-brief-body">
                      <span className="m-brief-head">
                        <span className="m-brief-title">{card.title}</span>
                        {urgency && (
                          <span className={`m-brief-urgency level-${urgency.level}`} title={`危急度 ${urgency.score}`}>
                            {urgency.label}{urgency.score}
                          </span>
                        )}
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
                      {contract && <span className="m-brief-contract">{contract}</span>}
                      {card.stakes?.length ? (
                        <span className="m-brief-stakes" aria-label="奏对两难">
                          {card.stakes.slice(0, 3).map((it, idx) => (
                            <span key={`${it.label}-${idx}`} className={`m-stake-chip tone-${it.tone || "neutral"}`}>
                              {it.label}
                            </span>
                          ))}
                        </span>
                      ) : null}
                    </span>
                  </button>
                  <div className="m-brief-actions">
                    {card.kind === "decision" ? (
                      <>
                        <button className="m-brief-action primary" onClick={() => go("desk")}>去裁断›</button>
                        {canSummon(card.actor, activeMinisters) && (
                          <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.actor!, card.target || "")}>召当事人</button>
                        )}
                        {canSummon(card.target, activeMinisters) && (
                          <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.target!, card.actor || "")}>召牵涉人</button>
                        )}
                        {card.actor && <button className="m-brief-action" onClick={() => inspect(card.actor)}>查{shortName(card.actor)}</button>}
                        {card.target && <button className="m-brief-action" onClick={() => inspect(card.target)}>查{shortName(card.target)}</button>}
                      </>
                    ) : card.kind === "rivalry" && card.actor && card.target ? (
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
                    ) : card.kind === "directive_followup" ? (
                      <>
                        {canSummon(card.actor, activeMinisters) && (
                          <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.actor!, "")}>召复盘</button>
                        )}
                        <button className="m-brief-action primary" onClick={() => go("edicts")}>看诏旨›</button>
                        {card.actor && <button className="m-brief-action" onClick={() => inspect(card.actor)}>查主办</button>}
                      </>
                    ) : card.kind === "monthly_followup" ? (
                      <>
                        {canSummon(card.actor, activeMinisters) && (
                          <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.actor!, "")}>召请安</button>
                        )}
                        {card.actor && <button className="m-brief-action" onClick={() => inspect(card.actor)}>查此人</button>}
                      </>
                    ) : card.kind === "patronage" && card.actor && card.target ? (
                      <>
                        <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.actor!, card.target)}>召举主</button>
                        <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.target!, card.actor)}>召新人</button>
                        <button className="m-brief-action" onClick={() => inspect(card.actor)}>查举主</button>
                        <button className="m-brief-action" onClick={() => inspect(card.target)}>查新人</button>
                      </>
                    ) : card.kind === "relationship" && card.actor && card.target ? (
                      <>
                        <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.actor!, card.target)}>召担保人</button>
                        <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.target!, card.actor)}>召关系人</button>
                        <button className="m-brief-action" onClick={() => inspect(card.actor)}>查{shortName(card.actor)}</button>
                        <button className="m-brief-action" onClick={() => inspect(card.target)}>查{shortName(card.target)}</button>
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
                    ) : card.kind === "legacy" && card.actor ? (
                      <>
                        {canSummon(card.actor, activeMinisters) && (
                          <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.actor!, card.target || "")}>召{shortName(card.actor)}</button>
                        )}
                        {canSummon(card.target, activeMinisters) && (
                          <button className="m-brief-action primary" onClick={() => summonFromBrief(card, card.target!, card.actor)}>召{shortName(card.target)}</button>
                        )}
                        <button className="m-brief-action" onClick={() => go("realm")}>看天下</button>
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
              <FeedItem key={i} e={e} openPerson={openPerson} go={go} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

// 朝报一条：活的宫廷事件不再是白流走的灰字——人物事件带头像、点头像识其人，
// 旨意/局势事件可一键直达对应模块。让「世界自己的落子」也可被回应（非只可读）。
function FeedItem({ e, openPerson, go }: { e: TickEvent; openPerson: (n: string) => void; go: (t: Tab) => void }) {
  const isChar = e.ref_kind === "character" && !!e.ref_id;
  const route: Tab | null =
    e.ref_kind === "directive" ? "edicts" : e.ref_kind === "issue" ? "realm" : null;
  const clickable = isChar || route != null;
  const onClick = () => {
    if (isChar) openPerson(e.ref_id);
    else if (route) go(route);
  };
  return (
    <li
      className={`m-feed-item lv-${e.level}${clickable ? " is-clickable" : ""}`}
      onClick={clickable ? onClick : undefined}
      role={clickable ? "button" : undefined}
    >
      {isChar && <Portrait name={e.ref_id} size={26} interactive={false} />}
      <span className="m-feed-body">
        <span className="m-feed-title">
          {feedMark(e.kind) && <i className="m-feed-mark">{feedMark(e.kind)}</i>}
          {e.title}
        </span>
        {e.detail && <span className="m-feed-detail">{e.detail}</span>}
      </span>
      {clickable && <span className="m-feed-go">{isChar ? "识此人" : route === "edicts" ? "看诏旨" : "看天下"} ›</span>}
    </li>
  );
}

// 朝报事件徽标（与 brief 卡的 kindMark 不同：未知则空，不强贴「机」）。
function feedMark(kind: string): string {
  const map: Record<string, string> = {
    harem_move: "宫", duishi: "对", court_move: "党", court_back: "恩",
    defection: "叛", mortality: "薨", succession: "继", eunuch_power: "阉",
    changwei: "厂", autonomy: "镇", directive_anomaly: "异", directive_aborted: "罢",
    threshold: "危", decision: "裁", frame: "构", smear: "诬",
    castration: "刑", recruit: "募", reincarnation: "异",
  };
  return map[kind] || "";
}

function kindMark(kind: string): string {
  if (kind === "decision") return "裁";
  if (kind === "army") return "军";
  if (kind === "faction") return "党";
  if (kind === "hook") return "柄";
  if (kind === "rivalry") return "怨";
  if (kind === "directive_blocker") return "阻";
  if (kind === "directive_followup") return "复";
  if (kind === "monthly_followup") return "候";
  if (kind === "bargain") return "账";
  if (kind === "patronage") return "荐";
  if (kind === "relationship") return "情";
  if (kind === "trap") return "任";
  if (kind === "trap_remedy") return "担";
  if (kind === "petition") return "求";
  if (kind === "favor") return "恩";
  if (kind === "legacy") return "政";
  return "机";
}

function briefContract(card: PlaystyleBriefCard): string {
  const motive = String(card.motive || "").trim();
  const gain = String(card.gain || "").trim();
  const cost = String(card.cost || "").trim();
  const parts = [];
  if (motive) parts.push(`来意：${motive}`);
  if (gain) parts.push(`可得：${gain}`);
  if (cost) parts.push(`代价：${cost}`);
  return parts.join(" · ");
}

function stakeLabel(card: PlaystyleBriefCard, kind: string): string {
  const item = (card.stakes || []).find((stake) => String(stake.kind || "") === kind);
  return String(item?.label || "").trim();
}

function shortClause(value: string, max = 26): string {
  const text = String(value || "").replace(/\s+/g, "").trim();
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

export function briefUrgency(value?: number): { label: string; level: "danger" | "warn" | "info"; score: number } | null {
  const raw = Number(value ?? 0);
  if (!Number.isFinite(raw)) return null;
  const score = Math.max(0, Math.min(100, Math.round(raw)));
  if (score >= 90) return { label: "危", level: "danger", score };
  if (score >= 78) return { label: "急", level: "warn", score };
  if (score >= 65) return { label: "要", level: "info", score };
  return null;
}

export function audienceLeadFromBrief(card: PlaystyleBriefCard, actor = card.actor || "", target = card.target || ""): AudienceLead {
  const followup = card.kind === "directive_followup";
  return {
    kind: card.kind,
    title: followup ? "复命后追问" : card.title,
    detail: card.detail,
    tone: card.tone,
    actor,
    target,
    meta: card.meta,
    motive: card.motive,
    gain: card.gain,
    cost: card.cost,
    ask: card.ask,
    exchange: card.exchange,
    refusal: card.refusal,
    ref_kind: card.ref_kind,
    ref_id: card.ref_id,
    opening: audienceOpeningFromBrief(card, actor, target),
    prompts: withClosurePrompt(withBargainPrompt(briefPrompts(card, actor, target), card, actor, target), card.kind, actor, target),
    stakes: card.stakes || [],
  };
}

function withBargainPrompt(prompts: Suggestion[], card: PlaystyleBriefCard, actor: string, target: string): Suggestion[] {
  const prompt = bargainPromptFromCard(card, actor, target);
  if (!prompt || prompts.some((item) => ["问所求", "问交易"].includes(item.label))) return prompts;
  return [prompt, ...prompts];
}

function bargainPromptFromCard(card: PlaystyleBriefCard, actor = "你", target = "此事"): Suggestion | null {
  const ask = shortClause(card.ask || stakeLabel(card, "ask") || card.motive || "");
  const exchange = shortClause(card.exchange || card.gain || "");
  const cost = shortClause(card.cost || "");
  const refusal = shortClause(card.refusal || "");
  if (!ask && !exchange && !cost && !refusal) return null;
  const who = actor || "你";
  const other = target || "此事";
  const terms = [
    ask ? `你所求「${ask}」` : "",
    exchange ? `愿用什么换「${exchange}」` : "",
    cost ? `朕若应允要付「${cost}」这类代价` : "",
    refusal ? `若朕不允是否会「${refusal}」` : "",
  ].filter(Boolean).join("；");
  return {
    label: "问所求",
    text: `${who}，今日不是泛泛问策。围绕${other}，先把御前交易说清：${terms}。朕要听实话，不要只报忠心。`,
    prefix: true,
  };
}

function withClosurePrompt(prompts: Suggestion[], kind: string, actor: string, target: string): Suggestion[] {
  if (prompts.some((item) => item.label === "定下一手")) return prompts;
  return [...prompts, closurePromptForAudience(kind, actor, target)];
}

export function closurePromptForAudience(kind: string, actor = "你", target = "此事"): Suggestion {
  const who = actor || "你";
  const other = target || "此事";
  if (kind === "rivalry") {
    return {
      label: "定下一手",
      text: `朕已听够两面之词。现在定下一手：你与${other}谁承办、几日回奏、各退哪一步、办坏了谁担责？`,
      prefix: true,
    };
  }
  if (kind === "patronage") {
    return {
      label: "定下一手",
      text: `荐人不是空话。现在定下一手：${who}与${other}谁领试差、几日见证据、举主和新人各担什么责？`,
      prefix: true,
    };
  }
  if (kind === "relationship") {
    return {
      label: "定下一手",
      text: `人情不是空账。现在定下一手：${who}替${other}担什么保、共办什么差、几日交证据，若坏事谁连坐？`,
      prefix: true,
    };
  }
  if (kind === "legacy") {
    return {
      label: "定下一手",
      text: `旧政善后不能空谈。现在定下一手：谁承办清查、几日给账册、钱粮缺口由谁补、办坏了谁担责？`,
      prefix: true,
    };
  }
  if (kind === "bargain") {
    return {
      label: "定下一手",
      text: `旧账今日要清。现在定下一手：是兑现、索证、改限还是明拒？由${who}几日交什么证据，办坏了谁担责？`,
      prefix: true,
    };
  }
  if (kind === "army") {
    return {
      label: "定下一手",
      text: `军镇之事现在定下一手：兵册、欠饷、监军、换防四项，先办哪一项，几日回奏，谁担责？`,
      prefix: true,
    };
  }
  if (kind === "faction") {
    return {
      label: "定下一手",
      text: `朕可以借力，也可以压势。现在定下一手：本派交什么人、办什么差、几日见效、若挟势谁担责？`,
      prefix: true,
    };
  }
  return {
    label: "定下一手",
    text: `朕已听明白。现在定下一手：由谁承办、几日回奏、拿什么证据、办坏了谁担责？你当面给朕一个可落账的说法。`,
    prefix: true,
  };
}

function audienceOpeningFromBrief(card: PlaystyleBriefCard, actor = card.actor || "此人", target = card.target || ""): string {
  const speaker = actor || "此人";
  const counterpart = target || "那人";
  const topic = briefTopic(card.title);
  const withDeal = (line: string) => withBargainOpening(line, card);
  if (card.kind === "decision") {
    return withDeal(`${speaker}趋入御前，知道这不是寻常问安，而是有一桩请陛下裁断的事压在案上。若陛下先问人、不急下判，愿把自己的利害、证据和怕处说清。`);
  }
  if (card.kind === "petition") {
    return withDeal(`${speaker}趋入叩首，先低声说明：今日不是空谈国事，而是带着一桩难处求陛下裁断；若陛下肯听，愿拿差使和证据来换。`);
  }
  if (card.kind === "rivalry") {
    return withDeal(`${speaker}入殿后神色紧绷：与${counterpart}的嫌隙，外间多有添油加醋。此番愿说实情，但也要陛下给一句边界。`);
  }
  if (card.kind === "army") {
    return withDeal(`${speaker}跪奏军情：军中欠饷与人心不敢粉饰。若只问罪不问饷，兵心难稳；若只给饷不立规矩，边镇也难服朝廷。`);
  }
  if (card.kind === "faction") {
    return withDeal(`${speaker}入殿便称本派人心浮动：既有人可用，也有人借势要价。陛下若要借力，须先定名分与规矩。`);
  }
  if (card.kind === "legacy") {
    const fiscalSide = actor && actor === card.actor;
    return withDeal(fiscalSide
      ? `${speaker}趋入便谈旧政余波：钱粮缺口是真，民怨也是真；若要蠲缓，先要说清由谁补这个窟窿。`
      : `${speaker}入殿叩首，先指向地方承受：这项旧政在账上或许有利，在地方却已成怨。请陛下先问受损者，再问谁从中得利。`);
  }
  if (card.kind === "favor") {
    return withDeal(`${speaker}伏地称谢旧恩，表示受过陛下护持，不敢只记在心里；今日若陛下有难差，愿先听条件。`);
  }
  if (card.kind === "bargain") {
    return withDeal(`${speaker}趋入时先提起前番御前旧账：不敢说陛下忘了，只求今日把兑现、补证或拒请边界当面讲清。`);
  }
  if (card.kind === "patronage") {
    return withDeal(target
      ? `${speaker}入殿先谈举荐：举荐不是一纸名帖。此番愿当面说清${counterpart}可用何处，也愿担该担的风险。`
      : `${speaker}趋入候旨，愿受陛下试用，但也请陛下明察自己从何处来、受谁举荐。`);
  }
  if (card.kind === "relationship") {
    return withDeal(`${speaker}趋入后先替${counterpart}留了半句余地：这层人情不是白纸黑字的官箴，却牵着同党、故旧和担保。今日若陛下要问，愿说可担哪一步，也要说清边界。`);
  }
  if (card.kind === "agenda") {
    return withDeal(agendaOpening(card, speaker, topic, counterpart));
  }
  if (card.kind === "hook") {
    return withDeal(`${speaker}入殿时明显收敛，知道陛下今日不是寻常问策；若有风闻落到自己身上，愿听陛下发问。`);
  }
  if (card.kind === "trap_remedy") {
    return withDeal(`${speaker}跪奏旧事：当日办坏，不敢一味喊冤。陛下若肯再问，愿把当日卡点和今日补救一并说清。`);
  }
  if (card.kind === "monthly_followup") {
    const meta = String(card.meta || "");
    if (meta.includes("失期")) {
      return withDeal(`${speaker}趋入时先俯首请罪：旧约已经过限，今日不是求宽一句话，而是要把误在哪里、谁担责、还缺什么条件说明白。`);
    }
    if (meta.includes("受阻")) {
      return withDeal(`${speaker}入殿便称旧约卡住：不是全无进展，也不是已经办成；阻力、人情、钱粮或名分今日须请陛下裁断。`);
    }
    if (meta.includes("待条件")) {
      return withDeal(`${speaker}趋入复奏，先呈待证条件：旧约差一步才能闭环，今日请陛下定是给资源、改期限，还是责其自证。`);
    }
    return withDeal(`${speaker}趋入复奏，本该主动请安回话，不敢等陛下追问；旧约办到哪一步，今日照实奏来。`);
  }
  if (card.kind === "directive_blocker") {
    return withDeal(`${speaker}入殿便称并非有意梗旨，只是此旨牵动人情、钱粮或名分。陛下若问，愿当面说清。`);
  }
  if (card.kind === "directive_followup") {
    return withDeal(`${speaker}捧着复命入殿：旨意办到几分，奏报里哪些是真功、哪些只是口径，今日不敢含糊。`);
  }
  return withDeal(`${speaker}入殿候问，知道陛下召见不是闲谈。此事利害，请陛下发问，愿照实奏来。`);
}

function withBargainOpening(line: string, card: PlaystyleBriefCard): string {
  const ask = shortClause(card.ask || stakeLabel(card, "ask") || card.motive || "", 30);
  const exchange = shortClause(card.exchange || card.gain || "", 34);
  const cost = shortClause(card.cost || "", 22);
  const refusal = shortClause(card.refusal || "", 30);
  const pieces = [
    ask ? `所求「${ask}」` : "",
    exchange ? `可逼其「${exchange}」` : "",
    cost ? `应下代价「${cost}」` : "",
    refusal ? `拒之恐「${refusal}」` : "",
  ].filter(Boolean);
  if (!pieces.length) return line;
  return `${line}\n御前交易：${pieces.slice(0, 4).join("；")}。`;
}

function agendaCue(card: PlaystyleBriefCard): string {
  const effectLabels = (card.effects || []).map((it) => String(it.label || "")).join(" ");
  return `${card.title || ""} ${card.detail || ""} ${card.meta || ""} ${effectLabels}`;
}

function agendaOpening(card: PlaystyleBriefCard, speaker: string, topic: string, counterpart: string): string {
  const cue = agendaCue(card);
  if (cue.includes("自肥") || cue.includes("查账") || cue.includes("请托") || cue.includes("肥缺")) {
    return `${speaker}被召入殿，先不敢谈功劳，只说账目与请托多是外间讹传；若陛下要查，愿拿一件差使自证清白。`;
  }
  if (cue.includes("护党") || cue.includes("植党") || cue.includes("门生") || cue.includes("故旧")) {
    return `${speaker}趋入先替同党分辩：门生故旧未必都是私援。只是官缺、人情和担保三样，今日恐怕都绕不开。`;
  }
  if (cue.includes("拥兵") || cue.includes("军镇") || cue.includes("兵册") || cue.includes("欠饷")) {
    return `${speaker}跪奏军中事：兵册、欠饷与亲信将领都可交代，但若陛下压得太急，军心未必受得住。`;
  }
  if (cue.includes("避祸") || cue.includes("旧案") || cue.includes("洗白")) {
    return `${speaker}伏地请罪，称旧案还有可辩之处；若陛下给一条活路，愿拿新功、同谋或把柄来换。`;
  }
  if (cue.includes("复仇") || cue.includes("弹劾") || cue.includes("政敌")) {
    return `${speaker}入殿便压着火气，说弹劾${counterpart}是为公义，不是私怨；但证据要如何呈、刀要落到哪里，须听陛下裁断。`;
  }
  if (cue.includes("进取") || cue.includes("求名位") || cue.includes("大差")) {
    return `${speaker}被召入殿，先请陛下给一件能见真章的差使；只是功成之后的名位和避嫌规矩，也须今日说清。`;
  }
  return `${speaker}被召入殿，先行自辩：外间说近来有「${topic}」之图，不敢全认，也不敢全辩，请陛下问。`;
}

function agendaPrompts(card: PlaystyleBriefCard, topic: string, counterpart: string): Suggestion[] {
  const cue = agendaCue(card);
  if (cue.includes("自肥") || cue.includes("查账") || cue.includes("请托") || cue.includes("肥缺")) {
    return [
      { label: "查账自证", text: `朕闻你近来有「${topic}」之势。今日先不问忠心，先问账目：请托、出入、经手人，哪一项能立刻交出来？`, prefix: true },
      { label: "限期补亏", text: `若朕暂不深究，你拿几日、多少证据、哪笔亏空来换？空口清白，朕不收。`, prefix: true },
      { label: "断肥缺", text: `你若还想任事，就先把肥缺和人情线切开。哪些人替你关说，哪些人该一并查？`, prefix: true },
    ];
  }
  if (cue.includes("护党") || cue.includes("植党") || cue.includes("门生") || cue.includes("故旧")) {
    return [
      { label: "问担保", text: `你护门生故旧，朕可以听。但你愿拿什么官声、差使和期限替他们连坐担保？`, prefix: true },
      { label: "命共办", text: `若朕令你与敌派共办一事，既验才干也验私心，你肯不肯？条件是什么？`, prefix: true },
      { label: "断官缺", text: `官缺不是党中私物。你今日说清楚：哪些人可用，哪些只是借你的门路求官？`, prefix: true },
    ];
  }
  if (cue.includes("拥兵") || cue.includes("军镇") || cue.includes("兵册") || cue.includes("欠饷")) {
    return [
      { label: "索兵册", text: `朕今日要看兵册、饷册和亲信将领名单。哪些听朝廷，哪些只听你？`, prefix: true },
      { label: "遣监军", text: `若朕遣监军、换亲信、补欠饷三策并行，哪一策最稳，哪一策会激变？`, prefix: true },
      { label: "限期交权", text: `军心可安，但兵权不可私握。你几日内能交出什么权柄，换朕暂不动你？`, prefix: true },
    ];
  }
  if (cue.includes("避祸") || cue.includes("旧案") || cue.includes("洗白")) {
    return [
      { label: "旧案换功", text: `旧案朕可以再听一次，但你须拿新功来换。几日内能办成什么，谁替你担保？`, prefix: true },
      { label: "交出同谋", text: `要台阶，就交把柄。旧案里谁同谋、谁受益、谁如今还能查？`, prefix: true },
      { label: "不给空赦", text: `朕不会白赦旧过。若不给你台阶，你会转投谁、牵连谁？照实说。`, prefix: true },
    ];
  }
  if (cue.includes("复仇") || cue.includes("弹劾") || cue.includes("政敌")) {
    return [
      { label: "先要证据", text: `你要劾${counterpart}，先把证据、人证、私怨三样分开。哪一样能经得住廷议？`, prefix: true },
      { label: "压成共办", text: `若朕不准你立刻动刀，而令你同${counterpart}共办一差，你肯不肯？你怕什么？`, prefix: true },
      { label: "问借刀价", text: `朕若准你这一刀，朝中谁会得利，谁会反扑？你拿什么替朕压住后患？`, prefix: true },
    ];
  }
  if (cue.includes("进取") || cue.includes("求名位") || cue.includes("大差")) {
    return [
      { label: "许难差", text: `你求用，朕可给难差，不先给名位。你要几日办成，拿什么证据回奏？`, prefix: true },
      { label: "功后再迁", text: `名位可谈，但须功成之后。若办不成，你愿降什么责，断哪些私援？`, prefix: true },
      { label: "查党援", text: `你声望渐起，朕要知道是谁在推你。党援、人情、门路，今日说清楚。`, prefix: true },
    ];
  }
  return [
    { label: "追问私心", text: `朕闻你近来有「${topic}」之势。你自己说，是为国任事，还是另有所图？`, prefix: true },
    { label: "令其交账", text: `若朕现在用你办事，你准备如何避嫌、如何交账？`, prefix: true },
    { label: "问党援钱粮", text: `此事牵动谁的党援和钱粮？把实话说清楚。`, prefix: true },
  ];
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
  if (card.kind === "petition") {
    return [
      { label: "准其护持", text: `朕可以暂护你一程，但不是白给。你先说清楚：朕应你会得罪谁，你能拿什么差使来换？`, prefix: true },
      { label: "逼其交账", text: `要朕给台阶，就先交账：人证、账目、把柄或可验差使，你今日能拿出哪一样？`, prefix: true },
      { label: "暂不护持", text: `朕今日未必应你。若朕暂不护持，你还愿不愿替朝廷办事？你的底线是什么？`, prefix: true },
    ];
  }
  if (card.kind === "decision") {
    return [
      { label: "先听己见", text: `朕还未裁断。你先说：这桩事若按你的说法办，谁得利、谁受损、谁来担责？`, prefix: true },
      { label: "问证据", text: `不要只讲情理。你能拿出什么人证、账册、旧约或差使成效，证明朕该信你？`, prefix: true },
      { label: "问反噬", text: `若朕照你所请裁断，朝中谁会反扑，几日内会出什么后患？你拿什么替朕压住？`, prefix: true },
    ];
  }
  if (card.kind === "favor") {
    return [
      { label: "点明旧恩", text: `朕昔日曾为你留任事余地。今日召你，是要听你如何还这笔旧恩。`, prefix: true },
      { label: "换难差", text: `旧恩不是空话。你若还愿任事，就领一件可验难差，几日内给朕回奏？`, prefix: true },
      { label: "防其要赏", text: `你若借天恩替同党求情，也须先说清代价、担保和可查的成效。`, prefix: true },
    ];
  }
  if (card.kind === "bargain") {
    return [
      { label: "追旧账", text: `前番御前这笔旧账，今日你自己说清楚：朕当时许了什么、留了什么条件、拒了什么边界？`, prefix: true },
      { label: "索实证", text: `不要只拿旧话要价。你今日能拿出什么账册、人证、担保或差使成效，证明这笔账该清？`, prefix: true },
      { label: "定去留", text: `朕可以兑现，也可以改限或明拒。你先说：哪一种结果你能承受，哪一种会让你转入拖延？`, prefix: true },
    ];
  }
  if (card.kind === "directive_blocker") {
    return [
      { label: "问掣肘", text: `朕闻${counterpart || "主办官"}承办此旨时受你牵制。你当面说清楚：是何缘故？`, prefix: true },
      { label: "令其配合", text: `此旨是朕亲下，你若有异议当奏明，不得暗中掣肘。你能如何配合办成？`, prefix: true },
      { label: "查其私心", text: `你阻此旨，是为公议，还是另有所图？把你背后的党援、人情和钱粮说清楚。`, prefix: true },
    ];
  }
  if (card.kind === "directive_followup") {
    return [
      { label: "问成效", text: `朕看了你的复命。此旨究竟办成到哪一步，哪里是真功，哪里只是奏报口径？`, prefix: true },
      { label: "论赏罚", text: `这桩差事既已复命，朕要定赏罚。你自己说：该奖谁，该问谁，下一步还缺什么？`, prefix: true },
      { label: "续下一手", text: `若朕要顺着这桩成果再下一道旨，你以为应接着办什么，交给谁办，限期几日？`, prefix: true },
    ];
  }
  if (card.kind === "monthly_followup") {
    return monthlyFollowupPrompts(card);
  }
  if (card.kind === "patronage") {
    const summonedSponsor = actor && actor === card.actor;
    return [
      summonedSponsor
        ? { label: "问担保", text: `你既举荐${counterpart}入朝，就当面说清楚：你愿拿什么名节、差事或期限替他担保？`, prefix: true }
        : { label: "验新人", text: `你由${counterpart}举荐入朝。今日不问荐书，只问你自己能办什么、短板在哪里、几日能见证据？`, prefix: true },
      summonedSponsor
        ? { label: "验新人", text: `${counterpart}究竟有什么可用之处，短板在哪里？若朕给他一件试差，几日能见证据？`, prefix: true }
        : { label: "问避嫌", text: `你与${counterpart}有人情牵连。若朕用你办事，你如何避嫌、如何交账、如何不只替举主说话？`, prefix: true },
      { label: "防植党", text: `荐人可以，但不可借荐人植党。你和${counterpart}的关系、派系、人情账，今日要说清楚。`, prefix: true },
    ];
  }
  if (card.kind === "relationship") {
    return [
      { label: "问担保", text: `朕知道你与${counterpart}有一层人情。今日说清楚：你肯替他担保到哪一步，拿什么名节或差使作保？`, prefix: true },
      { label: "命共办", text: `若朕令你与${counterpart}共办一件可验小差，既用人情也防植党，你肯不肯？条件是什么？`, prefix: true },
      { label: "问避嫌", text: `人情可用，也可坏政。你如何避嫌、如何交账，若${counterpart}误事你愿担什么责？`, prefix: true },
    ];
  }
  if (card.kind === "agenda") {
    return agendaPrompts(card, topic, counterpart);
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
  if (card.kind === "legacy") {
    return [
      { label: "问利害", text: `这项旧政仍在拖动朝局。朕要听实话：它如今利在哪里，害在哪里，谁因它得利，谁因它受损？`, prefix: true },
      { label: "查浮收", text: `朕不只听民怨二字。你指出这项旧政里谁加派、谁吞没、谁借题肥私，几日内能给证据？`, prefix: true },
      { label: "议蠲缓", text: `若朕准蠲缓或改税源，国库缺口、边饷缺口由谁补？你拿一个有期限、有代价的善后方案来。`, prefix: true },
    ];
  }
  return [
    { label: "问根由", text: `朕今日召${speaker}来，正为这桩风向。你先把根由、风险、可用之处说清楚。`, prefix: true },
  ];
}

function monthlyFollowupPrompts(card: PlaystyleBriefCard): Suggestion[] {
  const meta = String(card.meta || "");
  const title = briefTopic(card.title);
  if (meta.includes("失期")) {
    return [
      { label: "问失期", text: `旧约已过限。先说清楚：是你误事、旁人掣肘，还是朕当初给的条件不足？`, prefix: true },
      { label: "最后期限", text: `朕可给最后一次期限，但要写明几日、何证、谁担保。你今日拿什么换这个期限？`, prefix: true },
      { label: "当面追责", text: `若失期要问罪，先从哪里问起？你、同办之人、还是背后掣肘的人？`, prefix: true },
    ];
  }
  if (meta.includes("受阻")) {
    return [
      { label: "问阻力", text: `你说旧约受阻。阻力究竟是钱粮、人手、名分、政敌，还是你自己不愿担责？`, prefix: true },
      { label: "给资源价", text: `若朕给你资源或明旨，你拿什么可验成效来换？几日内回奏？`, prefix: true },
      { label: "逼其担责", text: `朕可以裁断，但不能替你白担。若再办不成，你愿担什么责？`, prefix: true },
    ];
  }
  if (meta.includes("待条件")) {
    return [
      { label: "核条件", text: `旧约待条件闭环。哪些条件已经成了，哪些还差证据？逐条奏来。`, prefix: true },
      { label: "改条件", text: `若原条件太虚，今日重定：何证、何人、何限，少一样都不算履约。`, prefix: true },
      { label: "防空口", text: `朕不要空口忠勤。你今日能拿出哪一件实证，证明不是拖延？`, prefix: true },
    ];
  }
  if (meta.includes("密令")) {
    return [
      { label: "先复密令", text: `先不求新恩。${title}办到哪一步？哪些是亲见证据，哪些只是风闻？`, prefix: true },
      { label: "补证再议", text: `若证据不足，朕不给你空名分。你还缺谁的人证、哪处账目、几日能补齐？`, prefix: true },
      { label: "问失手价", text: `密令若再拖下去，会惊动谁、牵连谁？你要朕保你，先说失手的代价。`, prefix: true },
    ];
  }
  if (meta.includes("举主")) {
    return [
      { label: "问担保", text: `你今日候见，若是为荐人旧账而来，就说明白：你愿拿什么名节、差使或期限作保？`, prefix: true },
      { label: "令共办", text: `荐人不能只写在名帖上。你同他共办一件可验小差，几日内交账？`, prefix: true },
      { label: "防植党", text: `若此事只是替门生故旧求路，朕不会轻许。你如何证明不是借荐人植党？`, prefix: true },
    ];
  }
  if (meta.includes("旧政")) {
    return [
      { label: "先报清查", text: `旧政清查先说证据：谁加派，谁吞没，谁受益，谁受损？`, prefix: true },
      { label: "限期善后", text: `若朕给你名分清查，你几日内能拿出账目、人证和善后方案？`, prefix: true },
      { label: "问反噬", text: `这桩旧政牵动钱粮和党争。若朕动它，谁会反噬，缺口由谁补？`, prefix: true },
    ];
  }
  if (meta.includes("旧约") || meta.includes("到期")) {
    return [
      { label: "清旧约", text: `你本月候见，先把旧约说清楚：已办成什么，没办成什么，谁在拖你？`, prefix: true },
      { label: "重定限期", text: `朕可再给期限，但要换条件。几日、何证、何人担保？`, prefix: true },
      { label: "问失约罪", text: `若这回仍无实据，朕该问谁的罪？你自己先说轻重。`, prefix: true },
    ];
  }
  return [
    { label: "先听复命", text: `朕准你请安。先说清楚：这是复命、求资源，还是要朕给一道明旨？`, prefix: true },
    { label: "问卡点", text: `你这桩事卡在钱粮、人手、名分，还是有人暗中掣肘？照实奏来。`, prefix: true },
    { label: "设回奏期", text: `朕可以给边界和名分，但要有期限、有证据。你几日内能拿什么来回奏？`, prefix: true },
  ];
}

function briefTopic(title: string): string {
  const text = String(title || "").trim();
  const parts = text.split("：");
  return (parts[parts.length - 1] || text || "此事").trim();
}

export function shortName(name?: string): string {
  const text = String(name || "").trim();
  return text.length > 3 ? `${text.slice(0, 3)}…` : text;
}

function canSummon(name: string | undefined, activeMinisters: Set<string>): boolean {
  const text = String(name || "").trim();
  return !!text && activeMinisters.has(text);
}
