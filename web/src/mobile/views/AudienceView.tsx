import { useEffect, useState } from "react";
import { useGame } from "../GameData";
import { ChatPane } from "../ChatPane";
import { Portrait } from "../Portrait";
import { loadEunuch, loadEunuchCandidates, replaceEunuch } from "../api";
import type { PublicCharacter } from "../api";

// 召对·人治之门：皇帝只直接对话随侍太监；要见大臣，命随侍传召 → 大臣趋入奏对 → 命其退下。
// 随侍是必经的门与顾问（进言荐人、过滤外朝），不可绕过直挑大臣。
export function AudienceView() {
  const { state, refresh } = useGame();
  const [eunuch, setEunuch] = useState<PublicCharacter | null | undefined>(undefined);
  const [audience, setAudience] = useState<string>(""); // "" = 与随侍；否则=正在奏对的大臣名
  const [sheet, setSheet] = useState<"" | "summon" | "replace">("");
  const [candidates, setCandidates] = useState<Array<{ name: string; office: string; is_eunuch: boolean }>>([]);

  useEffect(() => {
    loadEunuch().then((r) => setEunuch(r.eunuch)).catch(() => setEunuch(null));
  }, []);

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

  if (eunuch === undefined) return <div className="m-loading">正召随侍…</div>;

  // ── 奏对模式：与被传召的大臣（明确的趋入→奏对→退下）──
  if (audience) {
    return (
      <div className="m-audience-full">
        <div className="m-audience-bar">
          <div className="m-audience-who">
            <Portrait name={audience} size={40} />
            <div className="m-audience-id">
              <span className="m-audience-name">{audience}</span>
              <span className="m-audience-role">奉召觐见</span>
            </div>
          </div>
          <button className="m-mini m-mini-dismiss" onClick={() => setAudience("")}>命其退下 ›</button>
        </div>
        <div className="m-arrival">（{audience} 奉召趋入，叩见陛下。事毕，命其退下。）</div>
        <ChatPane key={audience} name={audience} speakerLabel={audience} onWorldChanged={refresh} />
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
      <ChatPane
        key={`eunuch:${eunuch.name}`}
        name={eunuch.name}
        speakerLabel={`${eunuch.name}·随侍`}
        onSummon={(next) => setAudience(next)}
        onWorldChanged={refresh}
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
                    <button key={m.name} className="m-sheet-row m-sheet-row-face" onClick={() => { setAudience(m.name); setSheet(""); }}>
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
