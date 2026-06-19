// 移动端五标签 App 骨架：顶部状态条 + tab 视图 + 底部导航。
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { formatApiError } from "../api/client";
import { GameDataProvider, useGame } from "./GameData";
import { PersonProvider } from "./Person";
import { Menu } from "./Menu";
import { Guide, guideSeen, markGuideSeen } from "./Guide";
import { authStatus, exitToMenu, login, menuStatus, register, resolveDecision } from "./api";
import type { AudienceLead, Decision, DecisionTestimony, ImpactEffect, Tab } from "./api";
import { HomeView } from "./views/HomeView";
import { DeskView } from "./views/DeskView";
import { AudienceView } from "./views/AudienceView";
import { EdictsView } from "./views/EdictsView";
import { PolicyCenterView } from "./views/RealmView";

const PERIOD_CN = ["", "正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊"];

function TopBar({ go, onHelp }: { go: (t: Tab) => void; onHelp: () => void }) {
  const { time, desk, advance } = useGame();
  const [busy, setBusy] = useState(false);
  const year = time?.year ?? 1627;
  const month = time?.month ?? 10;
  const day = time?.day_in_month ?? 1;
  const shi = desk?.shi ?? 55;
  const renshi = desk?.renshi_willingness ?? 60;
  const att = desk?.attention_left ?? 0;
  const attMax = desk?.attention_per_day ?? 12;

  const onAdvance = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await advance(5, true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <header className="m-topbar">
      <div className="m-topbar-row">
        <button className="m-reign" onClick={() => go("home")} aria-label="回御前">
          <span className="m-reign-era">崇祯{year - 1626 <= 1 ? "元" : year - 1626}年</span>
          <span className="m-reign-date">{PERIOD_CN[month] || month}月·第{day}日</span>
        </button>
        <button className="m-advance" onClick={onAdvance} disabled={busy}>
          {busy ? "推进中…" : "推时日 ›"}
        </button>
      </div>
      <div className="m-gauges" onClick={onHelp} role="button" aria-label="国势释义">
        <Gauge label="君威" value={shi} pct={shi} tone={shi < 25 ? "danger" : shi < 45 ? "warn" : "ok"} />
        <Gauge label="任事" value={renshi} pct={renshi} tone={renshi <= 25 ? "danger" : renshi <= 45 ? "warn" : "ok"} />
        <Gauge label="精力" value={att} max={attMax} pct={(att / Math.max(1, attMax)) * 100} noRise
               tone={att <= 1 ? "danger" : att <= 3 ? "warn" : "ok"} />
      </div>
    </header>
  );
}

function Gauge({ label, value, max, pct, tone, noRise }: { label: string; value: number; max?: number; pct: number; tone: string; noRise?: boolean }) {
  // 数值上涨→金色脉冲：每一次正向变化都被即时看见（高频正反馈）。精力日耗日补，不脉冲。
  const prev = useRef(value);
  const [rise, setRise] = useState(false);
  useEffect(() => {
    if (!noRise && value > prev.current) {
      setRise(true);
      const t = setTimeout(() => setRise(false), 1100);
      prev.current = value;
      return () => clearTimeout(t);
    }
    prev.current = value;
  }, [value, noRise]);
  return (
    <div className={`m-gauge m-gauge-${tone}${rise ? " is-rise" : ""}`}>
      <div className="m-gauge-top">
        <span className="m-gauge-label">{label}</span>
        <span className="m-gauge-value">
          {value}
          {max != null && <span className="m-gauge-max">/{max}</span>}
        </span>
      </div>
      <div className="m-gauge-track"><span className="m-gauge-fill" style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} /></div>
    </div>
  );
}

const TABS: Array<{ key: Tab; label: string; glyph: string }> = [
  { key: "home", label: "御前", glyph: "御" },
  { key: "desk", label: "御案", glyph: "案" },
  { key: "audience", label: "召对", glyph: "召" },
  { key: "edicts", label: "诏旨", glyph: "诏" },
  { key: "policy", label: "国策", glyph: "策" },
];

function BottomNav({ active, go }: { active: Tab; go: (t: Tab) => void }) {
  const { desk, lifecycle } = useGame();
  const live = lifecycle.filter((d) => ["in_transit", "executing", "stalled"].includes(d.status)).length;
  const badge: Partial<Record<Tab, number>> = {
    desk: desk?.backlog || 0,
    edicts: live,
  };
  return (
    <nav className="m-bottomnav" role="tablist">
      {TABS.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={active === t.key}
          className={`m-tab ${active === t.key ? "is-active" : ""}`}
          onClick={() => go(t.key)}
        >
          <span className="m-tab-glyph">{t.glyph}</span>
          <span className="m-tab-label">{t.label}</span>
          {!!badge[t.key] && <span className="m-tab-badge">{badge[t.key]! > 99 ? "99+" : badge[t.key]}</span>}
        </button>
      ))}
    </nav>
  );
}

function MetricFlash() {
  const { metricFlash } = useGame();
  const [show, setShow] = useState(false);
  useEffect(() => {
    if (!metricFlash) return;
    setShow(true);
    const t = setTimeout(() => setShow(false), 3600);
    return () => clearTimeout(t);
  }, [metricFlash?.key]);
  if (!metricFlash || !show) return null;
  const entries = Object.entries(metricFlash.deltas);
  if (!entries.length) return null;
  // 净收益分（势/任事/民心权重更高）——足够大则升格为「捷报」大字高潮卡。
  const W: Record<string, number> = { 君威: 3, 任事: 2.5, 民心: 1.5, 皇威: 3, 国库: 0.4, 内库: 0.4 };
  const score = entries.reduce((s, [k, v]) => s + (W[k] ?? 1) * v, 0);
  const triumph = score >= 9 && entries.some(([, v]) => v > 0);
  return (
    <div className={`m-flash${triumph ? " m-flash-triumph" : ""}`} key={metricFlash.key}>
      {triumph && <span className="m-flash-hail">捷报</span>}
      {entries.map(([k, v]) => (
        <span key={k} className={`m-flash-item ${v > 0 ? "up" : "down"}`}>{k} {v > 0 ? "▲" : "▼"}{Math.abs(v)}</span>
      ))}
    </div>
  );
}

// 诏题达成：本章方略告成时，朱金大字成就横幅自天而降，给一记响亮的正反馈。
function MilestoneToast() {
  const { milestone } = useGame();
  const [show, setShow] = useState(false);
  useEffect(() => {
    if (!milestone) return;
    setShow(true);
    const t = setTimeout(() => setShow(false), 5000);
    return () => clearTimeout(t);
  }, [milestone?.key]);
  if (!milestone || !show) return null;
  return (
    <div className="m-milestone" key={milestone.key}>
      <span className="m-milestone-seal">诏题<br />告成</span>
      <div className="m-milestone-txt">
        <span className="m-milestone-kicker">本章方略 · 达成</span>
        <span className="m-milestone-title">{milestone.title}</span>
        <span className="m-milestone-sub">君威已彰，史官记之（势 ＋）</span>
      </div>
    </div>
  );
}

// 抉择事件（CK3 化 P2）：朝局张力弹出"请陛下裁断"，玩家落子→后果即时回写朝局。
function DecisionEffectChips({ items }: { items?: ImpactEffect[] }) {
  const shown = (items || []).filter((item) => item.label).slice(0, 6);
  if (!shown.length) return null;
  return (
    <span className="m-effect-preview m-decision-effects" aria-label="预期影响">
      {shown.map((it, i) => (
        <span key={`${it.label}-${i}`} className={`m-effect-chip tone-${it.tone || "neutral"}`}>{it.label}</span>
      ))}
    </span>
  );
}

function DecisionTestimonyPanel({ items }: { items?: DecisionTestimony[] }) {
  const shown = (items || []).filter((item) => item.minister && item.summary).slice(-3);
  if (!shown.length) return null;
  return (
    <section className="m-decision-testimony" aria-label="裁断前问询">
      <div className="m-decision-testimony-title">已问入案</div>
      {shown.map((item, i) => (
        <div key={`${item.minister}-${i}`} className="m-decision-testimony-row">
          <div className="m-decision-testimony-meta">
            <span className="m-decision-testimony-name">{item.minister}</span>
            <span>{item.role || "问询人"}</span>
            {item.stance && <span>{item.stance}</span>}
          </div>
          <p>{item.summary}</p>
        </div>
      ))}
    </section>
  );
}

function DecisionModal() {
  const { decision, refresh, desk } = useGame();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ choice: string; effect: string; effects?: ImpactEffect[] } | null>(null);
  // 本地快照：落子后 refresh 会把 decision 置空，须保留以展示"已断"后果，玩家确认再消。
  const [active, setActive] = useState<Decision | null>(null);
  const hasUnreadOutcome = (desk?.pending || []).some((m: any) => m.kind === "复命" || m.kind === "捷报");
  useEffect(() => {
    if (!desk || hasUnreadOutcome) {
      setActive(null);
      setDone(null);
      return;
    }
    if (decision && !hasUnreadOutcome) { setActive(decision); setDone(null); }
  }, [desk, decision?.id, hasUnreadOutcome]);
  if (!active || !desk || hasUnreadOutcome) return null;
  const pick = async (key: string) => {
    if (busy || done) return;
    setBusy(true);
    try {
      const r = await resolveDecision(key);
      setDone({ choice: r.choice, effect: r.effect, effects: r.effects });
      await refresh();
    } finally { setBusy(false); }
  };
  return (
    <div className="m-decision-backdrop">
      <div className="m-decision">
        <div className="m-decision-seal">裁<br />断</div>
        <h2 className="m-decision-title">{active.title}</h2>
        <p className="m-decision-narr">{active.narrative}</p>
        <DecisionTestimonyPanel items={active.testimonies} />
        {done ? (
          <div className="m-decision-done">
            <p className="m-decision-chosen">已断：{done.choice}</p>
            <p className="m-decision-effect">{done.effect}</p>
            <DecisionEffectChips items={done.effects} />
            <button className="m-decision-ack" onClick={() => { setActive(null); setDone(null); }}>朝局已动，继续</button>
          </div>
        ) : (
          <div className="m-decision-choices">
            {active.choices.map((c) => (
              <button key={c.key} className="m-decision-choice" disabled={busy} onClick={() => pick(c.key)}>
                <span className="m-decision-label">{c.label}</span>
                {c.hint && <span className="m-decision-hint">{c.hint}</span>}
                <DecisionEffectChips items={c.effects} />
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Shell({ onExitMenu }: { onExitMenu: () => void }) {
  const [tab, setTab] = useState<Tab>("home");
  const [guideOpen, setGuideOpen] = useState(false);
  const [audienceName, setAudienceName] = useState("");
  const [audienceLead, setAudienceLead] = useState<AudienceLead | null>(null);
  const [audienceNotice, setAudienceNotice] = useState("");
  const { loading, error } = useGame();
  const summonAudience = (minister: string, lead?: AudienceLead) => {
    const name = minister.trim();
    if (!name) return;
    setAudienceNotice("");
    setAudienceName(name);
    setAudienceLead(lead || null);
    setTab("audience");
  };
  const completeAudience = (minister: string) => {
    const name = minister.trim();
    if (!name) return;
    setAudienceNotice(`奴婢回陛下，${name}大人已经告退，此番奏对已毕。`);
    setAudienceName("");
    setAudienceLead(null);
  };
  useEffect(() => {
    if (!guideSeen()) setGuideOpen(true);
  }, []);
  return (
    <PersonProvider onSummon={summonAudience}>
      <div className="m-app">
        <TopBar go={setTab} onHelp={() => setGuideOpen(true)} />
        <MetricFlash />
        <MilestoneToast />
        <main className="m-content" key={tab}>
          {error && <div className="m-error">{error}</div>}
          {loading && <div className="m-loading">正在召集朝局…</div>}
          {!loading && tab === "home" && <HomeView go={setTab} summon={summonAudience} />}
          {!loading && tab === "desk" && <DeskView />}
          {!loading && tab === "audience" && (
            <AudienceView
              audience={audienceName}
              onAudienceChange={(name, lead) => { setAudienceName(name); setAudienceLead(lead || null); if (name) setAudienceNotice(""); }}
              onAudienceComplete={completeAudience}
              audienceNotice={audienceNotice}
              audienceLead={audienceLead}
              go={setTab}
            />
          )}
          {!loading && tab === "edicts" && <EdictsView summon={summonAudience} />}
          {!loading && tab === "policy" && <PolicyCenterView />}
        </main>
        <BottomNav active={tab} go={setTab} />
        <DecisionModal />
        {guideOpen && (
          <Guide
            onClose={() => { markGuideSeen(); setGuideOpen(false); }}
            onExit={async () => { try { await exitToMenu(); } catch { /* ignore */ } onExitMenu(); }}
          />
        )}
      </div>
    </PersonProvider>
  );
}

function AuthScreen({ onAuthed }: { onAuthed: () => Promise<void> }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setErr("");
    try {
      if (mode === "login") {
        await login(username, password);
      } else {
        await register(username, password, inviteCode);
      }
      await onAuthed();
    } catch (e: any) {
      setErr(formatApiError(e, mode === "login" ? "登录失败" : "注册失败"));
      setBusy(false);
    }
  };

  return (
    <div className="m-menu m-auth">
      <form className="m-menu-inner m-auth-panel" onSubmit={submit}>
        <div className="m-menu-crest">明</div>
        <h1 className="m-menu-title">奉诏入朝</h1>
        <p className="m-menu-sub">各立案牍 · 各守进度</p>

        <div className="m-auth-switch" role="tablist" aria-label="登录方式">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "login"}
            className={mode === "login" ? "is-active" : ""}
            onClick={() => { setMode("login"); setErr(""); }}
          >
            登录
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "register"}
            className={mode === "register" ? "is-active" : ""}
            onClick={() => { setMode("register"); setErr(""); }}
          >
            注册
          </button>
        </div>

        {err && <div className="m-error">{err}</div>}

        <label className="m-auth-field">
          <span>用户名</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
        </label>
        <label className="m-auth-field">
          <span>密码</span>
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            type="password"
            required
          />
        </label>
        {mode === "register" && (
          <label className="m-auth-field">
            <span>邀请码</span>
            <input
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value)}
              autoComplete="one-time-code"
              required
            />
          </label>
        )}
        <button className="m-menu-btn primary" type="submit" disabled={busy}>
          {busy ? "验符中…" : mode === "login" ? "入宫" : "注册并入宫"}
        </button>
      </form>
    </div>
  );
}

export function App() {
  const [phase, setPhase] = useState<"boot" | "auth" | "menu" | "game">("boot");

  const enterAuthedArea = async () => {
    try {
      const s = await menuStatus();
      setPhase(s.has_running_game ? "game" : "menu");
    } catch (e: any) {
      if (String(formatApiError(e, "")).includes("auth_required")) {
        setPhase("auth");
        return;
      }
      setPhase("menu");
    }
  };

  useEffect(() => {
    authStatus()
      .then((me) => {
        if (me.auth_enabled && !me.authenticated) {
          setPhase("auth");
          return;
        }
        void enterAuthedArea();
      })
      .catch(() => setPhase("auth"));
  }, []);

  if (phase === "boot") return <div className="m-boot">正召集朝局…</div>;
  if (phase === "auth") return <AuthScreen onAuthed={enterAuthedArea} />;
  if (phase === "menu") return <Menu onEnter={() => setPhase("game")} />;
  return (
    <GameDataProvider>
      <Shell onExitMenu={() => setPhase("menu")} />
    </GameDataProvider>
  );
}
