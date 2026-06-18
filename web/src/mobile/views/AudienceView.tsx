import { useEffect, useState } from "react";
import { useGame } from "../GameData";
import { ChatPane } from "../ChatPane";
import { Portrait } from "../Portrait";
import { loadEunuch, loadEunuchCandidates, loadPlaystyleBrief, replaceEunuch } from "../api";
import type { AudienceLead, ChatContext, PlaystyleBriefCard, PublicCharacter } from "../api";
import { usePerson } from "../personCtx";
import { audienceLeadFromBrief, briefUrgency, shortName } from "./HomeView";

// 召对·人治之门：皇帝只直接对话随侍太监；要见大臣，命随侍传召 → 大臣趋入奏对 → 奏对完成，由随侍收尾。
// 随侍是必经的门与顾问（进言荐人、过滤外朝），不可绕过直挑大臣。
export function AudienceView({
  audience,
  onAudienceChange,
  onAudienceComplete,
  audienceNotice,
  audienceLead,
}: {
  audience: string;
  onAudienceChange: (name: string, lead?: AudienceLead | null) => void;
  onAudienceComplete: (name: string) => void;
  audienceNotice?: string;
  audienceLead?: AudienceLead | null;
}) {
  const { state, refresh, worldVersion } = useGame();
  const openPerson = usePerson();
  const [eunuch, setEunuch] = useState<PublicCharacter | null | undefined>(undefined);
  const [sheet, setSheet] = useState<"" | "summon" | "replace">("");
  const [candidates, setCandidates] = useState<Array<{ name: string; office: string; is_eunuch: boolean }>>([]);
  const [leads, setLeads] = useState<PlaystyleBriefCard[]>([]);

  useEffect(() => {
    loadEunuch().then((r) => setEunuch(r.eunuch)).catch(() => setEunuch(null));
  }, []);
  useEffect(() => {
    if (audience) return;
    loadPlaystyleBrief(3, "")
      .then((r) => setLeads((r.cards || []).filter((card) => card.actor).slice(0, 3)))
      .catch(() => setLeads([]));
  }, [audience, worldVersion]);

  const ministers: PublicCharacter[] = (state?.ministers || []).filter(
    (m: any) => m.status === "active" && m.name !== eunuch?.name,
  );

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
    return (
      <div className="m-audience-full">
        <div className="m-audience-bar">
          <div className="m-audience-who">
            <Portrait name={audience} size={40} />
            <div className="m-audience-id">
              <span className="m-audience-name">{audience}</span>
              <span className="m-audience-role">{isAttendantAudience ? "御前随侍" : "奉召觐见"}</span>
            </div>
          </div>
          <div className="m-audience-acts">
            <button className="m-mini" onClick={() => openPerson(audience)}>查此人</button>
            {isAttendantAudience ? (
              <button className="m-mini m-mini-complete" onClick={() => onAudienceChange("")}>回随侍 ›</button>
            ) : (
              <button className="m-mini m-mini-complete" onClick={() => onAudienceComplete(audience)}>奏对完成 ›</button>
            )}
          </div>
        </div>
        <div className="m-arrival">
          {isAttendantAudience
            ? `（${audience}正在御前随侍，本次按差使复命追问。）`
            : `（${audience} 奉召趋入，正在御前奏对。奏对完成后，由随侍送其告退。）`}
        </div>
        {activeLead && (
          <div className={`m-audience-lead tone-${activeLead.tone || "info"}`}>
            <div className="m-audience-lead-head">
              <span className="m-audience-lead-kicker">本次召对</span>
              {activeLead.meta && <span className="m-audience-lead-meta">{activeLead.meta}</span>}
            </div>
            <div className="m-audience-lead-title">{activeLead.title}</div>
            <div className="m-audience-lead-detail">{activeLead.detail}</div>
            {activeLead.target && (
              <button className="m-lead-link" onClick={() => openPerson(activeLead.target!)}>查{activeLead.target}</button>
            )}
          </div>
        )}
        <ChatPane
          key={isAttendantAudience ? `eunuch-lead:${audience}:${activeLead?.ref_id || ""}` : audience}
          name={audience}
          speakerLabel={isAttendantAudience ? `${audience}·随侍` : audience}
          onWorldChanged={refresh}
          onOpenPerson={openPerson}
          localMessages={activeLead?.opening ? [{ role: "minister", content: activeLead.opening }] : undefined}
          leadSuggestions={activeLead?.prompts || []}
          chatContext={activeLead ? chatContextFromLead(activeLead) : undefined}
        />
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
          <button className="m-mini" onClick={() => setSheet("summon")}>命其传召</button>
          <button className="m-mini" onClick={openReplace}>换随侍</button>
        </div>
      </div>
      {audienceNotice && <div className="m-audience-return">{audienceNotice}</div>}
      {leads.length > 0 && (
        <section className="m-audience-hooks" aria-label="今日候见">
          <div className="m-audience-hooks-head">
            <span>随侍递话</span>
            <small>宫门外有人候旨</small>
          </div>
          <div className="m-audience-hooks-list">
            {leads.map((card, i) => {
              const urgency = briefUrgency(card.urgency);
              return (
                <div key={`${card.kind}-${card.ref_id || i}`} className={`m-audience-hook tone-${card.tone || "info"}`}>
                  <button
                    className="m-audience-hook-main"
                    onClick={() => {
                      const actor = String(card.actor || "").trim();
                      if (!actor) return;
                      onAudienceChange(actor, leadFor(card));
                    }}
                  >
                    <Portrait name={String(card.actor || "")} size={30} interactive={false} />
                    <span className="m-audience-hook-body">
                      <span className="m-audience-hook-title">{card.title}</span>
                      <span className="m-audience-hook-detail">{card.detail}</span>
                    </span>
                    {urgency && <span className={`m-audience-hook-rank level-${urgency.level}`}>{urgency.label}{urgency.score}</span>}
                  </button>
                  <button
                    className="m-audience-hook-act"
                    onClick={() => {
                      const actor = String(card.actor || "").trim();
                      if (!actor) return;
                      onAudienceChange(actor, leadFor(card));
                    }}
                  >
                    召{shortName(card.actor)}
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      )}
      <ChatPane
        key={`eunuch:${eunuch.name}`}
        name={eunuch.name}
        speakerLabel={`${eunuch.name}·随侍`}
        onSummon={(next) => onAudienceChange(next)}
        onWorldChanged={refresh}
        onOpenPerson={openPerson}
      />

      {sheet && (
        <div className="m-sheet-backdrop" onClick={() => setSheet("")}>
          <div className="m-sheet" onClick={(e) => e.stopPropagation()}>
            <div className="m-sheet-head">
              <h3>{sheet === "summon" ? `命${eunuch.name}传召大臣觐见` : "改命随侍太监"}</h3>
              <button className="m-mini" onClick={() => setSheet("")}>关</button>
            </div>
            <div className="m-sheet-body">
              {sheet === "summon" && (
                <>
                  <p className="m-hint" style={{ margin: "0 4px 8px" }}>随侍领命传召，大臣须臾趋入御前。</p>
                  {ministers.map((m) => (
                    <button key={m.name} className="m-sheet-row m-sheet-row-face" onClick={() => { onAudienceChange(m.name); setSheet(""); }}>
                      <Portrait name={m.name} size={36} interactive={false} />
                      <div className="m-sheet-row-id">
                        <span className="m-row-name">{m.name}</span>
                        <span className="m-row-sub">{[m.office, m.faction].filter(Boolean).join(" · ")}</span>
                      </div>
                    </button>
                  ))}
                </>
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
