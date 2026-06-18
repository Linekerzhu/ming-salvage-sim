// 人物详情卡：点任意头像 → 看此人是谁（身份/性格小传/擅长），知道在跟谁打交道。
// 才/忠/廉等隐藏数值不直显（账实分离·印象系统）——呈现性格与擅长即可。
import { useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Portrait } from "./Portrait";
import type { PersonFocus, PersonOpen } from "./personCtx";
import { PersonCtx } from "./personCtx";
import { loadCharacter, loadCourt, intrigueInvestigate, intrigueCoerce, intrigueFabricate, intrigueDiscord, courtBack } from "./api";
import type { CourtBackKind, CourtPayload, ImpactEffect, IntriguePreviewKind } from "./api";
import { useGame } from "./GameData";

const AGENDA_CN: Record<string, string> = {
  climb: "进取", protect: "护党", revenge: "夙怨", enrich: "自肥", survive: "自保", entrench: "自重",
};

type ImpactTag = { label: string; tone?: "good" | "warn" | "bad" | "info" };

function servilityTone(value?: number): string {
  const v = Number(value ?? 0);
  if (v >= 75) return "卑顺深重";
  if (v >= 60) return "恭谨畏慎";
  if (v >= 42) return "守分自持";
  return "外顺内拗";
}

function summarizeImpacts(
  beforeC: Record<string, any> | null,
  beforeCourt: CourtPayload | null,
  nextC: Record<string, any> | null,
  nextCourt: CourtPayload | null,
): ImpactTag[] {
  const out: ImpactTag[] = [];
  const add = (label: string, tone: ImpactTag["tone"] = "info") => {
    if (label && !out.some((tag) => tag.label === label)) out.push({ label, tone });
  };
  if (beforeC && nextC) {
    if (beforeC.status_label !== nextC.status_label) {
      add(`状态 ${beforeC.status_label || beforeC.status || "旧"}→${nextC.status_label || nextC.status || "新"}`, nextC.status === "active" ? "info" : "bad");
    }
    if (beforeC.office !== nextC.office) add(`官职 ${shortText(beforeC.office)}→${shortText(nextC.office)}`, "warn");
    if (beforeC.faction !== nextC.faction) add(`派系 ${beforeC.faction || "无"}→${nextC.faction || "无"}`, nextC.faction === "皇党" ? "good" : "warn");
  }
  if (beforeCourt?.secret && nextCourt?.secret && !beforeCourt.secret.used && nextCourt.secret.used) add("把柄已用", "warn");
  if (!beforeCourt?.secret && nextCourt?.secret) add("把柄在手", "good");
  if (beforeCourt && nextCourt && tieSignature(beforeCourt) !== tieSignature(nextCourt)) add("关系已扰动", "warn");
  if (!out.length) add("盘面已刷新", "info");
  return out.slice(0, 4);
}

function impactTagsFromEffects(items?: Array<{ label: string; tone?: string }>): ImpactTag[] {
  return (items || [])
    .filter((item) => item.label)
    .slice(0, 5)
    .map((item) => ({
      label: item.label,
      tone: item.tone === "good" || item.tone === "bad" || item.tone === "warn" ? item.tone : "info",
    }));
}

function EffectChips({ items, limit = 6 }: { items?: ImpactEffect[]; limit?: number }) {
  const shown = (items || []).filter((item) => item.label).slice(0, limit);
  if (!shown.length) return null;
  return (
    <span className="m-effect-preview" aria-label="预期影响">
      {shown.map((it, i) => (
        <span key={`${it.label}-${i}`} className={`m-effect-chip tone-${it.tone || "neutral"}`}>{it.label}</span>
      ))}
    </span>
  );
}

function tieSignature(court: CourtPayload): string {
  const allies = (court.allies || []).map((t) => `${t.name}:${t.opinion}:${t.basis}`).join("|");
  const rivals = (court.rivals || []).map((t) => `${t.name}:${t.opinion}:${t.basis}`).join("|");
  return `${allies}::${rivals}`;
}

function shortText(value: unknown): string {
  const text = String(value || "无");
  return text.length > 8 ? `${text.slice(0, 8)}…` : text;
}

function PersonSheet({ name, focus, onClose }: { name: string; focus?: PersonFocus; onClose: () => void }) {
  const [c, setC] = useState<Record<string, any> | null>(null);
  const [court, setCourt] = useState<CourtPayload | null>(null);
  const [err, setErr] = useState(false);
  const [intrigueMsg, setIntrigueMsg] = useState("");
  const [impactTags, setImpactTags] = useState<ImpactTag[]>([]);
  const [busy, setBusy] = useState(false);
  const intrigueRef = useRef<HTMLDivElement | null>(null);
  const backRef = useRef<HTMLDivElement | null>(null);
  const openPerson = useContext(PersonCtx);
  const { refresh } = useGame();
  useEffect(() => {
    setC(null); setErr(false); setCourt(null); setIntrigueMsg(""); setImpactTags([]); setBusy(false);
    loadCharacter(name).then((r) => setC(r.character)).catch(() => setErr(true));
    loadCourt(name).then(setCourt).catch(() => setCourt(null));
  }, [name]);
  useEffect(() => {
    if (focus !== "intrigue" || !c) return;
    const t = window.setTimeout(() => {
      intrigueRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    }, 80);
    return () => window.clearTimeout(t);
  }, [focus, c, court, name]);
  useEffect(() => {
    if (focus !== "back" || !c) return;
    const t = window.setTimeout(() => {
      backRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    }, 80);
    return () => window.clearTimeout(t);
  }, [focus, c, name]);
  const isMing = !c || (c.power_id ? c.power_id === "ming" : true);
  const isSelf = name === "崇祯" || c?.office_type === "君主";
  async function reloadAfterAction(beforeC: Record<string, any> | null, beforeCourt: CourtPayload | null) {
    const [nextCharacter, nextCourt] = await Promise.all([
      loadCharacter(name).then((r) => { setErr(false); return r.character; }).catch(() => { setErr(true); return null; }),
      loadCourt(name).catch(() => null),
      refresh().catch(() => null),
    ]);
    setC(nextCharacter);
    setCourt(nextCourt);
    setImpactTags(summarizeImpacts(beforeC, beforeCourt, nextCharacter, nextCourt));
  }
  async function probe() {
    if (busy) return;
    const beforeC = c;
    const beforeCourt = court;
    setBusy(true); setIntrigueMsg("");
    try {
      const r = await intrigueInvestigate(name);
      setIntrigueMsg(r.message || "");
      await reloadAfterAction(beforeC, beforeCourt);
    } catch (e: any) { setIntrigueMsg(e?.message || "侦缉未成。"); }
    finally { setBusy(false); }
  }
  async function coerce(mode: string) {
    if (busy) return;
    const beforeC = c;
    const beforeCourt = court;
    setBusy(true); setIntrigueMsg("");
    try {
      const r = await intrigueCoerce(name, mode);
      setIntrigueMsg(r.message || "");
      await reloadAfterAction(beforeC, beforeCourt);
    } catch (e: any) { setIntrigueMsg(e?.message || "挟制未成。"); }
    finally { setBusy(false); }
  }
  async function fabricate() {
    if (busy) return;
    const beforeC = c;
    const beforeCourt = court;
    setBusy(true); setIntrigueMsg("");
    try {
      const r = await intrigueFabricate(name);
      setIntrigueMsg(r.message || "");
      await reloadAfterAction(beforeC, beforeCourt);
    } catch (e: any) { setIntrigueMsg(e?.message || "构陷未成。"); }
    finally { setBusy(false); }
  }
  async function discord() {
    const ally = court?.allies?.[0]?.name;
    if (busy || !ally) return;
    const beforeC = c;
    const beforeCourt = court;
    setBusy(true); setIntrigueMsg("");
    try {
      const r = await intrigueDiscord(name, ally);
      setIntrigueMsg(r.message || "");
      await reloadAfterAction(beforeC, beforeCourt);
    } catch (e: any) { setIntrigueMsg(e?.message || "离间未成。"); }
    finally { setBusy(false); }
  }
  async function back(kind: CourtBackKind) {
    if (busy) return;
    const beforeC = c;
    const beforeCourt = court;
    setBusy(true); setIntrigueMsg("");
    try {
      const r = await courtBack(name, kind);
      setIntrigueMsg(r.message || "");
      await reloadAfterAction(beforeC, beforeCourt);
      const tags = impactTagsFromEffects(r.effects);
      if (tags.length) setImpactTags(tags);
    } catch (e: any) { setIntrigueMsg(e?.message || "买单未成。"); }
    finally { setBusy(false); }
  }
  const skills: string[] = (c?.personal_skills || c?.skills || []).map((x: any) => typeof x === "string" ? x : x?.name).filter(Boolean);
  const canBack = !!c && isMing && !isSelf && ["active", "imprisoned", "dismissed"].includes(String(c.status || ""));
  const canReuse = !!c && ["imprisoned", "dismissed"].includes(String(c.status || ""));
  const backPreview = (kind: CourtBackKind, fallback: ImpactEffect[]) => {
    const items = court?.back_previews?.[kind];
    return items?.length ? items : fallback;
  };
  const intriguePreview = (kind: IntriguePreviewKind, fallback: ImpactEffect[]) => {
    const items = court?.intrigue_previews?.[kind];
    return items?.length ? items : fallback;
  };
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
        {intrigueMsg && (
          <div className="m-person-outcome m-intrigue-msg">
            <span className="m-person-outcome-kicker">事已行</span>
            <span className="m-person-outcome-text">{intrigueMsg}</span>
            {impactTags.length > 0 && (
              <span className="m-impact-tags">
                {impactTags.map((tag, i) => <span key={i} className={`m-impact-tag tone-${tag.tone || "info"}`}>{tag.label}</span>)}
              </span>
            )}
          </div>
        )}
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
            {court.agenda.bargain && (
              <div className="m-agenda-bargain" aria-label="私心交易画像">
                {court.agenda.bargain.ask && <span><b>所求</b>{court.agenda.bargain.ask}</span>}
                {court.agenda.bargain.exchange && <span><b>可压</b>{court.agenda.bargain.exchange}</span>}
                {court.agenda.bargain.cost && <span><b>许之</b>{court.agenda.bargain.cost}</span>}
                {court.agenda.bargain.refusal && <span><b>拒之</b>{court.agenda.bargain.refusal}</span>}
              </div>
            )}
          </div>
        )}
        {(court?.favor_memories?.length ?? 0) > 0 && (
          <div className="m-person-block">
            <span className="m-person-h">旧恩</span>
            <div className="m-favor-memories">
              {court!.favor_memories!.slice(0, 3).map((m, i) => (
                <p key={`${m.turn}-${i}`} className="m-person-favor">
                  <span className="m-favor-title">{m.title || "旧恩未报"}</span>
                  <span className="m-favor-text">{m.outcome || m.cause}</span>
                </p>
              ))}
            </div>
          </div>
        )}
        {canBack && (
          <div className="m-person-block" ref={backRef}>
            <span className="m-person-h">任事杠杆</span>
            <p className="m-hint" style={{ marginBottom: 8 }}>为失败或失意之臣买单：短期折势或惹议，长期回暖百官任事意愿。</p>
            <div className="m-intrigue-acts">
              <button className="m-intrigue-btn has-preview" disabled={busy} onClick={() => back("shoulder")}>
                <span>公开担责</span>
                <EffectChips items={backPreview("shoulder", [
                  { label: "任事 +8", tone: "good" },
                  { label: "势 -4", tone: "bad" },
                ])} />
              </button>
              <button className="m-intrigue-btn has-preview" disabled={busy} onClick={() => back("comfort")}>
                <span>抚恤褒奖</span>
                <EffectChips items={backPreview("comfort", [
                  { label: "任事 +5", tone: "good" },
                ])} />
              </button>
              {canReuse && (
                <button className="m-intrigue-btn has-preview" disabled={busy} onClick={() => back("reuse")}>
                  <span>败后复用</span>
                  <EffectChips items={backPreview("reuse", [
                    { label: "任事 +10", tone: "good" },
                    { label: "势 -2", tone: "bad" },
                  ])} />
                </button>
              )}
            </div>
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
            <span className="m-person-h">内廷旧事</span>
            {court?.castration && (
              <p className="m-person-castration">
                <span className={court.castration.forced ? "tv-bad" : "tv-good"}>
                  {court.castration.forced ? "强旨净身" : "自愿净身"}
                </span>
                {court.castration.bao_label && <span className="m-bao">· {court.castration.bao_label}</span>}
                <span className="m-serv">· 心相：{servilityTone(court.castration.servility)}</span>
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
          <div className="m-person-block" ref={intrigueRef}>
            <span className="m-person-h">把柄 · 厂卫</span>
            {court?.secret ? (
              <>
                <p className="m-person-secret">
                  <span className={`m-secret-tag ${court.secret.used ? "is-used" : ""}`}>{court.secret.label}</span>
                  <span className="m-secret-detail">{court.secret.detail}</span>
                </p>
                <div className="m-intrigue-acts">
                  <button className="m-intrigue-btn has-preview" disabled={busy} onClick={() => coerce("submit")}>
                    <span>胁其输诚</span>
                    <EffectChips items={intriguePreview("coerce_submit", [
                      { label: "归附皇党", tone: "good" },
                      { label: "怨气 +10", tone: "bad" },
                      { label: "把柄消耗", tone: "bad" },
                    ])} />
                  </button>
                  <button className="m-intrigue-btn has-preview" disabled={busy} onClick={() => coerce("serve")}>
                    <span>胁迫听用</span>
                    <EffectChips items={intriguePreview("coerce_serve", [
                      { label: "畏罪听用", tone: "good" },
                      { label: "怨气 +8", tone: "bad" },
                      { label: "把柄消耗", tone: "bad" },
                    ])} />
                  </button>
                  <button className="m-intrigue-btn is-danger has-preview" disabled={busy} onClick={() => coerce("retire")}>
                    <span>逼令致仕</span>
                    <EffectChips items={intriguePreview("coerce_retire", [
                      { label: "致仕去职", tone: "good" },
                      { label: "党羽怨怒", tone: "bad" },
                      { label: "把柄消耗", tone: "bad" },
                    ])} />
                  </button>
                </div>
              </>
            ) : (
              <div className="m-intrigue-acts">
                <button className="m-intrigue-btn has-preview" disabled={busy} onClick={probe}>
                  <span>令东厂密查</span>
                  <EffectChips items={intriguePreview("investigate", [
                    { label: "可能得把柄", tone: "good" },
                    { label: "不保证有实", tone: "neutral" },
                  ])} />
                </button>
                <span className="m-hint" style={{ alignSelf: "center" }}>厂卫侦缉，发其阴私为把柄</span>
              </div>
            )}
            <div className="m-intrigue-acts">
              <button className="m-intrigue-btn is-danger has-preview" disabled={busy} onClick={fabricate}>
                <span>罗织构陷·下诏狱</span>
                <EffectChips items={intriguePreview("fabricate", [
                  { label: "若成：下诏狱", tone: "bad" },
                  { label: "若败：势 -3", tone: "bad" },
                ])} />
              </button>
              {(court?.allies?.length ?? 0) > 0 && (
                <button className="m-intrigue-btn has-preview" disabled={busy} onClick={discord}>
                  <span>离间其腹心（{court!.allies[0].name}）</span>
                  <EffectChips items={intriguePreview("discord", [
                    { label: "关系骤跌", tone: "good" },
                    { label: "双方怨气 +3", tone: "bad" },
                    { label: "忠正可识破", tone: "bad" },
                  ])} />
                </button>
              )}
            </div>
            <span className="m-hint">构陷凭空罗织：清誉高者难陷，陷之易暴露反噬。</span>
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
  const [who, setWho] = useState<{ name: string; focus?: PersonFocus } | null>(null);
  const openPerson: PersonOpen = (target) => {
    const name = (typeof target === "string" ? target : target.name).trim();
    if (!name) return;
    setWho({ name, focus: typeof target === "string" ? undefined : target.focus });
  };
  return (
    <PersonCtx.Provider value={openPerson}>
      {children}
      {who && <PersonSheet name={who.name} focus={who.focus} onClose={() => setWho(null)} />}
    </PersonCtx.Provider>
  );
}
