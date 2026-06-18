import { useEffect, useState } from "react";
import { useGame } from "../GameData";
import { ChatPane } from "../ChatPane";
import { Portrait } from "../Portrait";
import { loadEunuch, loadEunuchCandidates, loadPlaystyleBrief, loadSummonHints, replaceEunuch } from "../api";
import type { AudienceLead, ChatContext, PlaystyleBriefCard, PublicCharacter, Suggestion, SummonHint, Tab } from "../api";
import { usePerson } from "../personCtx";
import { audienceLeadFromBrief, briefUrgency, closurePromptForAudience, shortName } from "./HomeView";

// 召对·人治之门：皇帝只直接对话随侍太监；要见大臣，命随侍传召 → 大臣趋入奏对 → 奏对完成，由随侍收尾。
// 随侍是必经的门与顾问（进言荐人、过滤外朝），不可绕过直挑大臣。
export function AudienceView({
  audience,
  onAudienceChange,
  onAudienceComplete,
  audienceNotice,
  audienceLead,
  go,
}: {
  audience: string;
  onAudienceChange: (name: string, lead?: AudienceLead | null) => void;
  onAudienceComplete: (name: string) => void;
  audienceNotice?: string;
  audienceLead?: AudienceLead | null;
  go?: (tab: Tab) => void;
}) {
  const { state, refresh, worldVersion } = useGame();
  const openPerson = usePerson();
  const [eunuch, setEunuch] = useState<PublicCharacter | null | undefined>(undefined);
  const [sheet, setSheet] = useState<"" | "summon" | "replace" | "hooks">("");
  const [dossierOpen, setDossierOpen] = useState(false);
  const [candidates, setCandidates] = useState<Array<{ name: string; office: string; is_eunuch: boolean }>>([]);
  const [leads, setLeads] = useState<PlaystyleBriefCard[]>([]);
  const [summonHints, setSummonHints] = useState<Record<string, SummonHint>>({});

  useEffect(() => {
    loadEunuch().then((r) => setEunuch(r.eunuch)).catch(() => setEunuch(null));
  }, []);
  useEffect(() => {
    if (audience) return;
    loadPlaystyleBrief(3, "")
      .then((r) => setLeads((r.cards || []).filter((card) => card.actor).slice(0, 3)))
      .catch(() => setLeads([]));
  }, [audience, worldVersion]);
  useEffect(() => {
    setDossierOpen(false);
    setSheet("");
  }, [audience]);

  const ministers: PublicCharacter[] = (state?.ministers || []).filter(
    (m: any) => m.status === "active" && m.name !== eunuch?.name,
  );
  const summonMinisters = [...ministers].sort((a, b) => (
    summonPriority(b, state?.conversation_goals, leads, summonHints)
    - summonPriority(a, state?.conversation_goals, leads, summonHints)
    || String(a.name || "").localeCompare(String(b.name || ""), "zh-Hans-CN")
  ));

  const openSummon = async () => {
    setSheet("summon");
    try {
      setSummonHints((await loadSummonHints()).hints || {});
    } catch {
      setSummonHints({});
    }
  };
  const openReplace = async () => {
    setSheet("replace");
    if (!candidates.length) {
      try { setCandidates((await loadEunuchCandidates()).candidates || []); } catch { /* ignore */ }
    }
  };
  const doReplace = async (name: string) => {
    try {
      const r = await replaceEunuch(name);
      setEunuch(r.eunuch); setSheet("");
    } catch { /* ignore */ }
  };
  const leadFor = (card: PlaystyleBriefCard) =>
    audienceLeadFromBrief(card, String(card.actor || ""), String(card.target || ""));

  if (eunuch === undefined) return <div className="m-loading">正召随侍…</div>;

  // ── 奏对模式：与被传召的大臣（明确的趋入→奏对→退下）──
  if (audience) {
    const activeLead = audienceLead && (!audienceLead.actor || audienceLead.actor === audience) ? audienceLead : null;
    const isAttendantAudience = !!eunuch?.name && audience === eunuch.name;
    const audienceGoals = openAudienceGoals(state?.conversation_goals, audience);
    const goalSuggestion = audienceGoals[0] ? suggestionFromGoal(audienceGoals[0], audience) : null;
    const leadSuggestions = [
      ...(activeLead?.prompts || []),
      ...(goalSuggestion ? [goalSuggestion] : []),
    ];
    const hasDossier = Boolean(activeLead || audienceGoals.length > 0);
    const dossierTone = activeLead?.tone || audienceGoalsTone(audienceGoals);
    const dossierMeta = [activeLead?.meta, audienceGoals.length ? `${audienceGoals.length} 桩旧约` : ""].filter(Boolean).join(" · ");
    const dossierTitle = activeLead?.title || audienceGoals[0]?.title || audienceGoals[0]?.goal_type || "可展开查阅";
    const dossierCount = (activeLead ? 1 : 0) + audienceGoals.length;
    const localIntro = compactAudienceOpening(activeLead, audienceGoals, audience);
    return (
      <div className="m-audience-full">
        <div className="m-audience-bar">
          <div className="m-audience-who">
            <Portrait name={audience} size={40} />
            <div className="m-audience-id">
              <span className="m-audience-name">{audience}</span>
              <span className="m-audience-role">
                {isAttendantAudience ? "御前随侍" : "奉召觐见"}
                <em>{isAttendantAudience ? "差使复命" : "奏对中"}</em>
              </span>
            </div>
          </div>
          <div className="m-audience-acts">
            {hasDossier && (
              <button
                className={`m-mini m-audience-dossier-act tone-${dossierTone}`}
                onClick={() => setDossierOpen(true)}
                aria-label={`打开奏对情报卷宗：${dossierMeta || dossierTitle}`}
              >
                案牍{dossierCount}
              </button>
            )}
            <button className="m-mini" onClick={() => openPerson(audience)} aria-label={`查看${audience}档案`}>查档</button>
            {isAttendantAudience ? (
              <button className="m-mini m-mini-complete" onClick={() => onAudienceChange("")}>回侍</button>
            ) : (
              <button className="m-mini m-mini-complete" onClick={() => onAudienceComplete(audience)}>奏毕</button>
            )}
          </div>
        </div>
        <ChatPane
          key={isAttendantAudience ? `eunuch-lead:${audience}:${activeLead?.ref_id || ""}` : audience}
          name={audience}
          speakerLabel={isAttendantAudience ? `${audience}·随侍` : audience}
          onSummon={(next) => onAudienceChange(next)}
          onGo={go}
          onWorldChanged={refresh}
          onOpenPerson={openPerson}
          arrivalNote={localIntro}
          leadSuggestions={leadSuggestions}
          suggestionLimit={4}
          compact
          chatContext={activeLead ? chatContextFromLead(activeLead) : undefined}
        />
        {hasDossier && dossierOpen && (
          <AudienceDossierSheet
            tone={dossierTone}
            activeLead={activeLead}
            audienceGoals={audienceGoals}
            audience={audience}
            openPerson={openPerson}
            onAudienceChange={onAudienceChange}
            onClose={() => setDossierOpen(false)}
          />
        )}
      </div>
    );
  }

  // ── 随侍模式：与太监对话（枢纽）──
  if (eunuch === null) {
    return (
      <div className="m-view m-audience">
        <section className="m-card m-card-hero">
          <h2 className="m-card-title">召对·人治</h2>
          <p className="m-hint">朝中无随侍太监可用。可于「天下·官制」拣选内臣充任随侍，方能传召大臣。</p>
        </section>
      </div>
    );
  }

  return (
    <div className="m-audience-full">
      <div className="m-audience-bar">
        <div className="m-audience-who">
          <Portrait name={eunuch.name} size={40} />
          <div className="m-audience-id">
            <span className="m-audience-name">{eunuch.name}</span>
            <span className="m-audience-role">御前随侍</span>
          </div>
        </div>
        <div className="m-audience-acts">
          {leads.length > 0 && (
            <button className="m-mini m-audience-context-act" onClick={() => setSheet("hooks")}>
              递话{leads.length}
            </button>
          )}
          <button className="m-mini" onClick={openSummon}>传召</button>
          <button className="m-mini" onClick={openReplace}>换侍</button>
        </div>
      </div>
      {audienceNotice && <div className="m-audience-return">{audienceNotice}</div>}
      <ChatPane
        key={`eunuch:${eunuch.name}`}
        name={eunuch.name}
        speakerLabel={`${eunuch.name}·随侍`}
        onSummon={(next) => onAudienceChange(next)}
        onGo={go}
        onWorldChanged={refresh}
        onOpenPerson={openPerson}
      />

      {sheet && (
        <div className="m-sheet-backdrop" onClick={() => setSheet("")}>
          <div className="m-sheet" onClick={(e) => e.stopPropagation()}>
            <div className="m-sheet-head">
              <h3>{sheet === "summon" ? `命${eunuch.name}传召大臣觐见` : sheet === "replace" ? "改命随侍太监" : "随侍递话"}</h3>
              <button className="m-mini" onClick={() => setSheet("")}>关</button>
            </div>
            <div className="m-sheet-body">
              {sheet === "summon" && (
                <>
                  <p className="m-hint" style={{ margin: "0 4px 8px" }}>随侍领命传召，大臣须臾趋入御前。</p>
                  {summonMinisters.map((m) => {
                    const summonLead = leadFromSummonRow(m.name, state?.conversation_goals, leads);
                    const tags = summonRowTags(m, state?.conversation_goals, leads, summonHints);
                    return (
                      <button key={m.name} className="m-sheet-row m-sheet-row-face" onClick={() => { onAudienceChange(m.name, summonLead); setSheet(""); }}>
                        <Portrait name={m.name} size={36} interactive={false} />
                        <div className="m-sheet-row-id">
                          <span className="m-row-name">{m.name}</span>
                          <span className="m-row-sub">{[m.office, m.faction].filter(Boolean).join(" · ")}</span>
                          {tags.length > 0 && (
                            <span className="m-row-tags">
                              {tags.map((tag) => (
                                <span key={tag.label} className={`m-row-tag tone-${tag.tone || "neutral"}`}>{tag.label}</span>
                              ))}
                            </span>
                          )}
                        </div>
                      </button>
                    );
                  })}
                </>
              )}
              {sheet === "hooks" && (
                <AudienceHookList
                  leads={leads}
                  onSelect={(card) => {
                    const actor = String(card.actor || "").trim();
                    if (!actor) return;
                    onAudienceChange(actor, leadFor(card));
                    setSheet("");
                  }}
                />
              )}
              {sheet === "replace" &&
                candidates.map((c) => (
                  <button key={c.name} className="m-sheet-row" onClick={() => doReplace(c.name)}>
                    <span className="m-row-name">{c.name}</span>
                    <span className="m-row-sub">{c.office}{c.is_eunuch ? "" : "（非宦官）"}</span>
                  </button>
                ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function AudienceHookList({
  leads,
  onSelect,
}: {
  leads: PlaystyleBriefCard[];
  onSelect: (card: PlaystyleBriefCard) => void;
}) {
  return (
    <div className="m-audience-hooks-list m-audience-hooks-sheet">
      {leads.map((card, i) => {
        const urgency = briefUrgency(card.urgency);
        return (
          <div key={`${card.kind}-${card.ref_id || i}`} className={`m-audience-hook tone-${card.tone || "info"}`}>
            <button className="m-audience-hook-main" onClick={() => onSelect(card)}>
              <Portrait name={String(card.actor || "")} size={30} interactive={false} />
              <span className="m-audience-hook-body">
                <span className="m-audience-hook-title">{card.title}</span>
                <span className="m-audience-hook-detail">{card.detail}</span>
                {card.stakes?.length ? (
                  <span className="m-audience-hook-stakes" aria-label="候见两难">
                    {card.stakes.slice(0, 3).map((it, idx) => (
                      <span key={`${it.label}-${idx}`} className={`m-stake-chip tone-${it.tone || "neutral"}`}>
                        {it.label}
                      </span>
                    ))}
                  </span>
                ) : null}
              </span>
              {urgency && <span className={`m-audience-hook-rank level-${urgency.level}`}>{urgency.label}{urgency.score}</span>}
            </button>
            <button className="m-audience-hook-act" onClick={() => onSelect(card)}>
              召{shortName(card.actor)}
            </button>
          </div>
        );
      })}
    </div>
  );
}

function AudienceDossierSheet({
  tone,
  activeLead,
  audienceGoals,
  audience,
  openPerson,
  onAudienceChange,
  onClose,
}: {
  tone: string;
  activeLead: AudienceLead | null;
  audienceGoals: any[];
  audience: string;
  openPerson: (name: string) => void;
  onAudienceChange: (name: string, lead?: AudienceLead | null) => void;
  onClose: () => void;
}) {
  return (
    <div className="m-sheet-backdrop" onClick={onClose}>
      <div className={`m-sheet m-audience-context-sheet tone-${tone}`} onClick={(e) => e.stopPropagation()}>
        <div className="m-sheet-head">
          <h3>奏对情报</h3>
          <button className="m-mini" onClick={onClose}>收</button>
        </div>
        <div className="m-sheet-body m-audience-context-body">
          <AudienceDossierBody
            activeLead={activeLead}
            audienceGoals={audienceGoals}
            audience={audience}
            openPerson={openPerson}
            onAudienceChange={onAudienceChange}
            onClose={onClose}
          />
        </div>
      </div>
    </div>
  );
}

function AudienceDossierBody({
  activeLead,
  audienceGoals,
  audience,
  openPerson,
  onAudienceChange,
  onClose,
}: {
  activeLead: AudienceLead | null;
  audienceGoals: any[];
  audience: string;
  openPerson: (name: string) => void;
  onAudienceChange: (name: string, lead?: AudienceLead | null) => void;
  onClose: () => void;
}) {
  const visibleAudienceGoals = audienceGoals.slice(0, 3);
  return (
    <div className="m-audience-dossier-body">
      {activeLead && (
        <section className="m-audience-dossier-section">
          <div className="m-audience-dossier-label">本次召对</div>
          <div className="m-audience-dossier-title">{activeLead.title}</div>
          <div className="m-audience-dossier-detail">{activeLead.detail}</div>
          <AudienceDealBox lead={activeLead} />
          {activeLead.stakes?.length ? (
            <div className="m-audience-lead-stakes" aria-label="本次召对两难">
              {activeLead.stakes.slice(0, 3).map((it, idx) => (
                <span key={`${it.label}-${idx}`} className={`m-stake-chip tone-${it.tone || "neutral"}`}>
                  {it.label}
                </span>
              ))}
            </div>
          ) : null}
          {activeLead.target && (
            <div className="m-lead-actions">
              <button className="m-lead-link" onClick={() => { onClose(); openPerson(activeLead.target!); }}>查{activeLead.target}</button>
              {activeLead.target !== audience && (
                <button
                  className="m-lead-link primary"
                  onClick={() => {
                    onClose();
                    onAudienceChange(activeLead.target!, counterpartLeadFromActive(activeLead, audience));
                  }}
                >
                  传{shortName(activeLead.target)}对质
                </button>
              )}
            </div>
          )}
        </section>
      )}
      {audienceGoals.length > 0 && (
        <section className="m-audience-dossier-section">
          <div className="m-audience-dossier-label">旧约在案 · {audienceGoals.length} 桩未竟</div>
          <div className="m-audience-goals-list">
            {visibleAudienceGoals.map((goal, i) => (
              <div key={`${goal.id || i}-${goal.title || goal.target_text || ""}`} className={`m-audience-goal tone-${goal.status || "active"}`}>
                <span className="m-audience-goal-title">{goal.title || goal.goal_type || "未竟奏对"}</span>
                <span className="m-audience-goal-meta">
                  {[goal.status_label, goal.condition_label, goal.due_label, goal.progress_label].filter(Boolean).join(" · ")}
                </span>
                {(goal.blocker_summary || goal.pending_summary || goal.public_hint) && (
                  <span className="m-audience-goal-note">{goal.blocker_summary || goal.pending_summary || goal.public_hint}</span>
                )}
              </div>
            ))}
            {audienceGoals.length > visibleAudienceGoals.length && (
              <div className="m-audience-goals-more">
                另有 {audienceGoals.length - visibleAudienceGoals.length} 桩未竟，查此人可看完整档案
              </div>
            )}
          </div>
        </section>
      )}
    </div>
  );
}

function AudienceDealBox({ lead }: { lead: AudienceLead }) {
  const items = [
    ["所求", lead.ask || lead.motive || ""],
    ["可逼", lead.exchange || lead.gain || ""],
    ["代价", lead.cost || ""],
    ["拒之", lead.refusal || ""],
  ].filter(([, value]) => String(value || "").trim());
  if (!items.length) return null;
  return (
    <div className="m-audience-deal" aria-label="御前交易条件">
      {items.map(([label, value]) => (
        <span key={label}>
          <b>{label}</b>{value}
        </span>
      ))}
    </div>
  );
}

function compactAudienceOpening(activeLead: AudienceLead | null, audienceGoals: any[], audience: string): string {
  if (!activeLead) return "";
  const title = String(activeLead.title || activeLead.detail || "本次奏对").trim();
  const meta = String(activeLead.meta || "").trim();
  const goalCount = audienceGoals.length;
  const pressure = goalCount ? `另有${goalCount}桩旧约压在案头` : "";
  const suffix = [meta, pressure].filter(Boolean).join("；");
  return `${audience}趋入殿中。案由：「${title}」${suffix ? `（${suffix}）` : ""}。`;
}

function chatContextFromLead(lead: AudienceLead): ChatContext {
  return {
    kind: lead.kind,
    actor: lead.actor,
    target: lead.target,
    ref_kind: lead.ref_kind,
    ref_id: lead.ref_id,
    title: lead.title,
    meta: lead.meta,
  };
}

function openAudienceGoals(rawGoals: unknown, audience: string): any[] {
  const name = String(audience || "").trim();
  if (!name || !Array.isArray(rawGoals)) return [];
  const hidden = new Set(["fulfilled", "abandoned"]);
  return rawGoals
    .filter((goal: any) => String(goal?.minister_name || "").trim() === name)
    .filter((goal: any) => !hidden.has(String(goal?.status || "").trim()))
    .slice(0, 4);
}

function audienceGoalsTone(goals: any[]): string {
  if (goals.some((goal) => ["blocked", "expired"].includes(String(goal?.status || "")))) return "danger";
  if (goals.some((goal) => String(goal?.status || "") === "waiting_conditions")) return "warn";
  return "info";
}

function suggestionFromGoal(goal: any, audience: string): Suggestion {
  const title = String(goal?.title || goal?.target_text || "未竟奏对").trim();
  const pending = String(goal?.pending_summary || "").trim();
  const blocker = String(goal?.blocker_summary || "").trim();
  const due = String(goal?.due_label || "").trim();
  const status = String(goal?.status_label || goal?.status || "未定").trim();
  const pressure = [status, due, blocker || pending].filter(Boolean).join("；");
  return {
    label: blocker ? "问阻力" : "追旧约",
    text: `${audience}，朕记得你旧约「${title}」尚未了结。${pressure ? `眼下是：${pressure}。` : ""}你当面说清楚：已办成什么、还缺什么证据、几日能回奏，若再误事谁担责？`,
    prefix: true,
  };
}

function audienceLeadFromGoal(goal: any, actor: string): AudienceLead {
  const title = String(goal?.title || goal?.target_text || "未竟奏对").trim();
  const blocker = String(goal?.blocker_summary || "").trim();
  const pending = String(goal?.pending_summary || "").trim();
  const due = String(goal?.due_label || "").trim();
  const status = String(goal?.status_label || goal?.status || "未定").trim();
  const condition = String(goal?.condition_label || "").trim();
  const detailBits = [status, condition, due, blocker || pending || String(goal?.public_hint || "").trim()].filter(Boolean);
  const rawStatus = String(goal?.status || "").trim();
  const severe = Boolean(blocker) || ["blocked", "expired"].includes(rawStatus);
  return {
    kind: "monthly_followup",
    title: `追旧约：${title}`,
    detail: detailBits.length ? `这桩旧约仍未了结：${detailBits.join("；")}。` : "这桩旧约仍未了结，须当面问清。",
    tone: severe ? "danger" : rawStatus === "waiting_conditions" ? "warn" : "info",
    actor,
    target: "",
    meta: blocker ? "旧约受阻" : rawStatus === "expired" ? "旧约失期" : "旧约",
    ref_kind: "conversation_goal",
    ref_id: String(goal?.id || ""),
    opening: `${actor}奉召入殿，知道陛下今日不是泛问国事，而是追问旧约「${title}」。此番须把已办、未办、待证和担责说清。`,
    prompts: [
      suggestionFromGoal(goal, actor),
      closurePromptForAudience("monthly_followup", actor, ""),
    ],
    stakes: [
      { kind: "gain", label: "旧约闭环", tone: "good" },
      { kind: "cost", label: blocker ? "阻力未清" : "再拖成怨", tone: "bad" },
      { kind: "ask", label: "限期证据", tone: "neutral" },
    ],
  };
}

function leadFromSummonRow(name: string, rawGoals: unknown, leads: PlaystyleBriefCard[]): AudienceLead | null {
  const clean = String(name || "").trim();
  if (!clean) return null;
  const lead = leads.find((card) => String(card.actor || "").trim() === clean);
  if (lead) return audienceLeadFromBrief(lead, clean, String(lead.target || ""));
  const goal = openAudienceGoals(rawGoals, clean)[0];
  if (goal) return audienceLeadFromGoal(goal, clean);
  return null;
}

function summonPriority(
  minister: PublicCharacter,
  rawGoals: unknown,
  leads: PlaystyleBriefCard[],
  hints: Record<string, SummonHint>,
): number {
  const name = String(minister?.name || "").trim();
  const goals = openAudienceGoals(rawGoals, name);
  const lead = leads.find((card) => String(card.actor || "").trim() === name);
  let score = 0;
  if (lead) score += 100 + Number(lead.urgency || 0);
  if (goals.some((goal) => ["blocked", "expired"].includes(String(goal?.status || "")))) score += 80;
  score += goals.length * 12;
  score += Number(hints[name]?.pressure_score || 0);
  return score;
}

function summonRowTags(
  minister: PublicCharacter,
  rawGoals: unknown,
  leads: PlaystyleBriefCard[],
  hints: Record<string, SummonHint>,
): Array<{ label: string; tone?: string }> {
  const clean = String(minister?.name || "").trim();
  const tags: Array<{ label: string; tone?: string }> = [];
  const lead = leads.find((card) => String(card.actor || "").trim() === clean);
  if (lead) {
    tags.push({
      label: lead.meta ? `候旨·${lead.meta}` : "候旨",
      tone: lead.tone === "danger" ? "bad" : lead.tone === "warn" ? "warn" : "neutral",
    });
  }
  const goals = openAudienceGoals(rawGoals, clean);
  if (goals.length) {
    const blocked = goals.some((goal) => String(goal?.status || "") === "blocked");
    const expired = goals.some((goal) => String(goal?.status || "") === "expired");
    const waiting = goals.some((goal) => String(goal?.status || "") === "waiting_conditions");
    tags.push({
      label: expired ? "旧约失期" : blocked ? "旧约受阻" : waiting ? "待证旧约" : `旧约${goals.length}`,
      tone: expired || blocked ? "bad" : waiting ? "warn" : "neutral",
    });
  }
  tags.push(...(hints[clean]?.tags || []));
  return tags.slice(0, 5);
}

function counterpartLeadFromActive(lead: AudienceLead, currentActor: string): AudienceLead {
  const next = String(lead.target || "").trim();
  const current = String(currentActor || lead.actor || "").trim();
  return {
    ...lead,
    actor: next,
    target: current,
    title: lead.title.startsWith("对质：") ? lead.title : `对质：${lead.title}`,
    detail: current
      ? `方才已听${current}一面之词。此番传${next}入殿，仍围绕同一桩事，问其证据、代价和愿担的责。`
      : lead.detail,
    meta: lead.meta ? `对质/${lead.meta}` : "对质",
    opening: counterpartOpening(lead.kind, next, current),
    prompts: counterpartPrompts(lead.kind, next, current),
  };
}

function counterpartOpening(kind: string, next: string, previous: string): string {
  const who = next || "此人";
  const other = previous || "前一人";
  if (kind === "rivalry") {
    return `${who}奉召趋入，已知${other}方才在御前陈情；此番不敢只喊冤，须把旧怨、证据和愿退让的边界说清。`;
  }
  if (kind === "patronage") {
    return `${who}趋入叩首，知道陛下正在查这条举荐人情链；此番要说清自己能办什么、受谁牵引、若误事谁该担责。`;
  }
  if (kind === "legacy") {
    return `${who}入殿承问旧政余波；方才已听过另一方说法，此番须把受益、受损、钱粮缺口和善后代价讲明。`;
  }
  if (kind === "petition") {
    return `${who}趋入时先看了${other}一眼，知道陛下要听两面之词；此番须说明自己是否掣肘、是否愿给台阶或证据。`;
  }
  if (kind === "relationship") {
    return `${who}奉召入殿，知道陛下问的是自己同${other}这层人情；此番须说清护持、担保或旧怨的真实边界。`;
  }
  return `${who}奉召续入。陛下方才已听${other}一面之词，此番要听另一面：证据、代价、可退让处，都须说清。`;
}

function counterpartPrompts(kind: string, next: string, previous: string) {
  const who = next || "你";
  const other = previous || "前一人";
  if (kind === "rivalry") {
    return [
      { label: "听另一面", text: `朕已听过${other}之言。你如今当面说：旧怨从何而起，哪一句是假，哪一件可证？`, prefix: true },
      { label: "问退让", text: `若朕令你与${other}各退一步、共办一差，你肯让哪一步，又要什么边界？`, prefix: true },
      { label: "防借刀", text: `你若只是借朕的手压${other}，也要说清代价。朝中谁会因此得利，谁会反扑？`, prefix: true },
      closurePromptForAudience(kind, who, other),
    ];
  }
  if (kind === "patronage") {
    return [
      { label: "查担保", text: `朕正在查你和${other}的举荐人情。若用错人，谁担责？若用得其人，何差可验？`, prefix: true },
      { label: "问避嫌", text: `${who}，你今日说清楚：自己是朝廷的人，还是${other}的人？如何避嫌、如何交账？`, prefix: true },
      { label: "定试差", text: `若朕给一件试差，几日内见成效？办坏了，举主、新人，各担什么责？`, prefix: true },
      closurePromptForAudience(kind, who, other),
    ];
  }
  if (kind === "legacy") {
    return [
      { label: "问谁得利", text: `旧政余波不是一句民怨。你指出谁得利、谁受损、谁在中间吞没或加派。`, prefix: true },
      { label: "问缺口", text: `若要善后，钱粮缺口由谁补？若不善后，地方怨气会落到谁头上？`, prefix: true },
      { label: "定证据", text: `朕不要空泛议论。几日内能拿出账册、人证或地方实情？`, prefix: true },
      closurePromptForAudience(kind, who, other),
    ];
  }
  return [
    { label: "听另一面", text: `朕已听过${other}一面之词。你照实说，哪里是真，哪里是假？`, prefix: true },
    { label: "问代价", text: `若朕采信你这一面，会得罪谁、补什么缺口、留下什么后患？`, prefix: true },
    { label: "定可验证据", text: `不要空口。你能拿出什么证据、差使或期限，证明自己不是推脱？`, prefix: true },
    closurePromptForAudience(kind, who, other),
  ];
}
