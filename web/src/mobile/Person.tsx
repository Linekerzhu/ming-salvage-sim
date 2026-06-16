// 人物详情卡：点任意头像 → 看此人是谁（身份/性格小传/擅长），知道在跟谁打交道。
// 才/忠/廉等隐藏数值不直显（账实分离·印象系统）——呈现性格与擅长即可。
import { useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Portrait } from "./Portrait";
import { PersonCtx } from "./personCtx";
import { loadCharacter, loadCourt, intrigueInvestigate, intrigueCoerce } from "./api";
import type { CourtPayload } from "./api";

const AGENDA_CN: Record<string, string> = {
  climb: "进取", protect: "护党", revenge: "夙怨", enrich: "自肥", survive: "自保", entrench: "自重",
};

function PersonSheet({ name, onClose }: { name: string; onClose: () => void }) {
  const [c, setC] = useState<Record<string, any> | null>(null);
  const [court, setCourt] = useState<CourtPayload | null>(null);
  const [err, setErr] = useState(false);
  const [intrigueMsg, setIntrigueMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const openPerson = useContext(PersonCtx);
  useEffect(() => {
    setC(null); setErr(false); setCourt(null); setIntrigueMsg(""); setBusy(false);
    loadCharacter(name).then((r) => setC(r.character)).catch(() => setErr(true));
    loadCourt(name).then(setCourt).catch(() => setCourt(null));
  }, [name]);
  const isMing = !c || (c.power_id ? c.power_id === "ming" : true);
  const isSelf = name === "崇祯" || c?.office_type === "君主";
  async function probe() {
    if (busy) return;
    setBusy(true); setIntrigueMsg("");
    try {
      const r = await intrigueInvestigate(name);
      setIntrigueMsg(r.message || "");
      await loadCourt(name).then(setCourt).catch(() => {});
    } catch (e: any) { setIntrigueMsg(e?.message || "侦缉未成。"); }
    finally { setBusy(false); }
  }
  async function coerce(mode: string) {
    if (busy) return;
    setBusy(true); setIntrigueMsg("");
    try {
      const r = await intrigueCoerce(name, mode);
      setIntrigueMsg(r.message || "");
      await loadCourt(name).then(setCourt).catch(() => {});
    } catch (e: any) { setIntrigueMsg(e?.message || "挟制未成。"); }
    finally { setBusy(false); }
  }
  const skills: string[] = (c?.personal_skills || c?.skills || []).map((x: any) => typeof x === "string" ? x : x?.name).filter(Boolean);
  return (
    <div className="m-sheet-backdrop" onClick={onClose}>
      <div className="m-sheet m-person" onClick={(e) => e.stopPropagation()}>
        <div className="m-person-hd">
          <Portrait name={name} size={64} interactive={false} />
          <div className="m-person-id">
            <span className="m-person-name">{name}</span>
            <span className="m-person-sub">{c ? [c.office || c.office_type, c.faction].filter(Boolean).join(" · ") : "…"}</span>
            <span className="m-person-sub2">{c ? [c.status_label, c.age_label].filter(Boolean).join(" · ") : ""}</span>
          </div>
          <button className="m-mini" onClick={onClose}>关</button>
        </div>
        {err && <p className="m-empty">查无此人详档。</p>}
        {c?.style && (
          <div className="m-person-block">
            <span className="m-person-h">性情·擅短</span>
            <p className="m-person-style">{c.style}</p>
          </div>
        )}
        {skills.length > 0 && (
          <div className="m-person-block">
            <span className="m-person-h">擅长</span>
            <div className="m-person-skills">{skills.slice(0, 12).map((sk, i) => <span key={i} className="m-skill">{sk}</span>)}</div>
          </div>
        )}
        {(court?.traits?.length ?? 0) > 0 && (
          <div className="m-person-block">
            <span className="m-person-h">性格</span>
            <div className="m-person-traits">
              {court!.traits.map((t) => (
                <span key={t.key} className={`m-trait ${t.valence > 0 ? "tv-good" : t.valence < 0 ? "tv-bad" : "tv-neu"}`} title={t.desc}>
                  {t.key}
                </span>
              ))}
            </div>
          </div>
        )}
        {court?.agenda?.title && (
          <div className="m-person-block">
            <span className="m-person-h">私心 <span className="m-agenda-tag">{AGENDA_CN[court.agenda.kind] || ""}</span></span>
            <p className="m-person-agenda">{court.agenda.title}</p>
          </div>
        )}
        {((court?.allies?.length ?? 0) > 0 || (court?.rivals?.length ?? 0) > 0) && (
          <div className="m-person-block">
            <span className="m-person-h">党羽 · 政敌</span>
            <div className="m-ties">
              {court!.allies.map((t) => (
                <button key={`a-${t.name}`} className="m-tie is-ally" onClick={() => openPerson(t.name)}>
                  <Portrait name={t.name} size={26} interactive={false} />
                  <span className="m-tie-name">{t.name}</span>
                  <span className="m-tie-basis">{t.basis}</span>
                </button>
              ))}
              {court!.rivals.map((t) => (
                <button key={`r-${t.name}`} className="m-tie is-rival" onClick={() => openPerson(t.name)}>
                  <Portrait name={t.name} size={26} interactive={false} />
                  <span className="m-tie-name">{t.name}</span>
                  <span className="m-tie-basis">{t.basis}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        {(court?.castration || court?.duishi) && (
          <div className="m-person-block">
            <span className="m-person-h">内廷 · 净身</span>
            {court?.castration && (
              <p className="m-person-castration">
                <span className={court.castration.forced ? "tv-bad" : "tv-good"}>
                  {court.castration.forced ? "强旨净身" : "自愿净身"}
                </span>
                {court.castration.bao_label && <span className="m-bao">· {court.castration.bao_label}</span>}
                <span className="m-serv">· 奴性 {court.castration.servility}</span>
              </p>
            )}
            {court?.duishi && (
              <p className="m-person-duishi">
                对食：
                <button className="m-tie-inline" onClick={() => openPerson(court.duishi!)}>
                  <Portrait name={court.duishi} size={22} interactive={false} />
                  <span className="m-tie-name">{court.duishi}</span>
                </button>
              </p>
            )}
          </div>
        )}
        {isMing && !isSelf && (
          <div className="m-person-block">
            <span className="m-person-h">把柄 · 厂卫</span>
            {court?.secret ? (
              <>
                <p className="m-person-secret">
                  <span className={`m-secret-tag ${court.secret.used ? "is-used" : ""}`}>{court.secret.label}</span>
                  <span className="m-secret-detail">{court.secret.detail}</span>
                </p>
                <div className="m-intrigue-acts">
                  <button className="m-intrigue-btn" disabled={busy} onClick={() => coerce("submit")}>胁其输诚</button>
                  <button className="m-intrigue-btn" disabled={busy} onClick={() => coerce("serve")}>胁迫听用</button>
                  <button className="m-intrigue-btn is-danger" disabled={busy} onClick={() => coerce("retire")}>逼令致仕</button>
                </div>
              </>
            ) : (
              <div className="m-intrigue-acts">
                <button className="m-intrigue-btn" disabled={busy} onClick={probe}>令东厂密查</button>
                <span className="m-hint" style={{ alignSelf: "center" }}>厂卫侦缉，发其阴私为把柄</span>
              </div>
            )}
            {intrigueMsg && <p className="m-intrigue-msg">{intrigueMsg}</p>}
          </div>
        )}
        {Array.isArray(c?.conversation_goals) && c.conversation_goals.length > 0 && (
          <div className="m-person-block">
            <span className="m-person-h">与朕奏对</span>
            {c.conversation_goals.slice(0, 4).map((g: any, i: number) => (
              <p key={i} className="m-person-goal">{g.title || g.goal_type}{g.progress != null ? ` · ${g.progress}%` : ""}</p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function PersonProvider({ children }: { children: ReactNode }) {
  const [who, setWho] = useState<string>("");
  return (
    <PersonCtx.Provider value={setWho}>
      {children}
      {who && <PersonSheet name={who} onClose={() => setWho("")} />}
    </PersonCtx.Provider>
  );
}
