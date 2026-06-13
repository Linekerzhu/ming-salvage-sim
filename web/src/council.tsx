/**
 * 中枢·今日朝政 —— 核心循环的引导枢纽。
 *
 * 设计意图（针对「玩法/爽点未被前置 + 功能模块无提示」）：
 * - 把核心循环（批奏疏→召大臣→下圣旨→推时日→阅邸报）从散落的导航/弹窗里拎出来，
 *   做成一个常驻的「现在该做什么」面板，新玩家不读文档也能上手。
 * - 头条＝阶段诏题（内置教程式北极星目标）；其下＝由实时局势驱动的优先待办，
 *   每条可一键直达对应模块——把「爽点入口」（密查揭穿账实、处置生变旨意、月终颁诏）显性化。
 *
 * 自包含组件：自取数据，靠 refreshKey 变化重取；路由靠 App 传入的 onOpen* 回调。
 */

import React from "react";
import { Landmark, ScrollText, Eye, Gavel, Users, ChevronRight, ChevronDown, X } from "lucide-react";
import { api } from "./api/client";

const FIRSTRUN_KEY = "ming.firstRunGuide.seen";

/**
 * 首次亲政导览：一次性浮层，讲清「你是谁 + 一回合怎么玩 + 爽点在哪」。
 * 自管 localStorage，已看过则不渲染。直接挂在 game-shell，自门控。
 */
export function FirstRunGuide() {
  const [seen, setSeen] = React.useState<boolean>(() => {
    try {
      return localStorage.getItem(FIRSTRUN_KEY) === "1";
    } catch {
      return true; // 读不到则不打扰
    }
  });
  if (seen) return null;
  const dismiss = () => {
    try {
      localStorage.setItem(FIRSTRUN_KEY, "1");
    } catch {
      /* ignore */
    }
    setSeen(true);
  };
  const steps = [
    { icon: <ScrollText size={18} />, t: "批奏疏", d: "御案上奏疏纷至：请款、弹章、荐人、告变。准、驳、留中、发部议——每一笔批红都是表态，久压不批则奏疏淹没、君威受损。" },
    { icon: <Users size={18} />, t: "召大臣", d: "朝堂召见大臣对谈定策、托付密令、查访风闻。言语即落子：你说的每句话都在改变朝局——这是本作的核心。" },
    { icon: <Gavel size={18} />, t: "下圣旨", d: "拟诏颁旨。诏书不会即刻成真：经送达、承办、旬检定，会被拖延、截留、封驳——迟滞的官僚体系本身就是博弈对象。" },
    { icon: <Eye size={18} />, t: "推时日·阅邸报", d: "推进时日，静观事态；月末邸报结算天下之变。账实未必相符（密查可揭穿瞒报），昔日之策亦会埋下祸根（裁驿→流寇）。史笔终将评判你。" },
  ];
  return (
    <div className="firstrun-scrim" role="dialog" aria-modal="true" aria-label="亲政导览">
      <div className="firstrun-card">
        <button className="firstrun-close" onClick={dismiss} aria-label="关闭导览"><X size={18} /></button>
        <h2 className="firstrun-title">崇祯元年·初登大宝</h2>
        <p className="firstrun-lead">
          你是刚刚即位的崇祯皇帝。内忧外患，积重难返。
          每月为一回合，照此节奏理政——右上「中枢·今日朝政」会随时提示你<b>现在该做什么</b>。
        </p>
        <div className="firstrun-steps">
          {steps.map((s, i) => (
            <div key={s.t} className="firstrun-step">
              <span className="firstrun-step-no">{i + 1}</span>
              <span className="firstrun-step-icon">{s.icon}</span>
              <span className="firstrun-step-body">
                <b>{s.t}</b>
                <span>{s.d}</span>
              </span>
            </div>
          ))}
        </div>
        <p className="firstrun-foot">皇权是均衡之术，悲剧亦值得一玩。诸事不必尽善，但求无愧史笔。</p>
        <button className="firstrun-start" onClick={dismiss}>始亲政</button>
      </div>
    </div>
  );
}

type Stage = { id: string; title: string; brief: string } | null;
type Goal = { id: string; title: string; hint: string; done: boolean };
type Zhongxing = {
  current: { total: number; parts: Record<string, number> };
  stage: Stage;
  goals: Goal[];
};
type DeskLite = {
  backlog: number;
  attention_left: number;
  attention_per_day: number;
  pending: Array<{ days_to_expire: number; kind: string }>;
};
type DirLite = { status: string; anomaly: string };
type TimeLite = { await_decree: boolean; year: number; month: number; day_in_month: number };

export type CouncilNav = {
  onOpenDesk: () => void;
  onOpenWatch: () => void;
  onOpenEdict: () => void;
  onOpenCourt: () => void;
};

type Action = {
  key: string;
  urgent: boolean;
  icon: React.ReactNode;
  label: string;
  detail: string;
  cta: string;
  onClick: () => void;
};

const HUB_COLLAPSE_KEY = "ming.councilHub.collapsed";

export function CouncilHub({ refreshKey, nav }: { refreshKey: number; nav: CouncilNav }) {
  const [zx, setZx] = React.useState<Zhongxing | null>(null);
  const [desk, setDesk] = React.useState<DeskLite | null>(null);
  const [dirs, setDirs] = React.useState<DirLite[]>([]);
  const [contradictions, setContradictions] = React.useState(0);
  const [time, setTime] = React.useState<TimeLite | null>(null);
  const [collapsed, setCollapsed] = React.useState<boolean>(() => {
    try {
      return localStorage.getItem(HUB_COLLAPSE_KEY) === "1";
    } catch {
      return false;
    }
  });

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [z, d, dl, c, t] = await Promise.all([
          api<Zhongxing>("/api/zhongxing"),
          api<DeskLite>("/api/desk"),
          api<{ directives: DirLite[] }>("/api/directives/lifecycle"),
          api<{ items: unknown[] }>("/api/veil/contradictions"),
          api<{ time: TimeLite }>("/api/time"),
        ]);
        if (cancelled) return;
        setZx(z);
        setDesk(d);
        setDirs(dl.directives || []);
        setContradictions((c.items || []).length);
        setTime(t.time);
      } catch {
        /* 中枢取数失败：保持上次视图，不打断玩家 */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const toggleCollapsed = () => {
    setCollapsed((v) => {
      const next = !v;
      try {
        localStorage.setItem(HUB_COLLAPSE_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  const live = dirs.filter((d) => ["in_transit", "executing", "stalled"].includes(d.status));
  const anomalies = dirs.filter((d) => d.status === "stalled" || (d.anomaly && d.anomaly !== "")).length;
  const drowning = (desk?.pending || []).filter((m) => m.days_to_expire > 0 && m.days_to_expire <= 7).length;

  const actions: Action[] = [];
  if (time?.await_decree) {
    actions.push({
      key: "decree", urgent: true, icon: <Gavel size={15} />,
      label: "月终待颁诏", detail: "颁诏定本月大计，或无诏退朝以入新月",
      cta: "拟旨颁诏", onClick: nav.onOpenEdict,
    });
  }
  if (drowning > 0) {
    actions.push({
      key: "drown", urgent: true, icon: <ScrollText size={15} />,
      label: `${drowning} 封奏疏将淹没`, detail: "再不批红便石沉大海——弹章淹没折损君威、令臣下寒心",
      cta: "即刻批红", onClick: nav.onOpenDesk,
    });
  }
  if (anomalies > 0) {
    actions.push({
      key: "anomaly", urgent: true, icon: <Eye size={15} />,
      label: `${anomalies} 道旨意生变`, detail: "封驳顶撞/迁延拖办/实情有变——可催办、加拨、独断或密核",
      cta: "司天台处置", onClick: nav.onOpenWatch,
    });
  }
  if (desk && desk.backlog > 0 && drowning === 0) {
    actions.push({
      key: "desk", urgent: false, icon: <ScrollText size={15} />,
      label: `御案待批 ${desk.backlog} 封`, detail: `今日精力 ${desk.attention_left}/${desk.attention_per_day}——留中不耗精力，但久压有后果`,
      cta: "批红", onClick: nav.onOpenDesk,
    });
  }
  if (contradictions > 0) {
    actions.push({
      key: "veil", urgent: false, icon: <Eye size={15} />,
      label: `${contradictions} 处账实可疑`, detail: "奏报数字未必是真——遣厂卫或科道密查，揭穿瞒报截留",
      cta: "查册究问", onClick: nav.onOpenWatch,
    });
  }
  if (live.length > 0 && anomalies === 0) {
    actions.push({
      key: "live", urgent: false, icon: <Eye size={15} />,
      label: `在办旨意 ${live.length} 道`, detail: "看进度与阻力，必要时催办/加拨/独断",
      cta: "司天台", onClick: nav.onOpenWatch,
    });
  }
  if (actions.length === 0) {
    actions.push({
      key: "idle", urgent: false, icon: <Users size={15} />,
      label: "朝政暂清", detail: "可召见大臣议事定策，或推进时日，静观天下之变",
      cta: "召见大臣", onClick: nav.onOpenCourt,
    });
  }

  const incompleteGoals = (zx?.goals || []).filter((g) => !g.done).slice(0, 3);

  return (
    <section className={`council-hub${collapsed ? " collapsed" : ""}`} aria-label="中枢·今日朝政">
      <header className="council-hub-head">
        <span className="council-hub-title">
          <Landmark size={15} /> 中枢·今日朝政
        </span>
        {time && (
          <span className="council-hub-date">
            {time.year}年{time.month}月·第{time.day_in_month}日
          </span>
        )}
        <button
          className="council-hub-collapse"
          onClick={toggleCollapsed}
          aria-label={collapsed ? "展开中枢" : "收起中枢"}
          title={collapsed ? "展开中枢" : "收起中枢"}
        >
          {collapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}
        </button>
      </header>

      {!collapsed && (
        <>
          {zx?.stage && (
            <button className="council-hub-objective" onClick={nav.onOpenWatch} title="查看阶段诏题与中兴指数">
              <span className="council-hub-obj-tag">当前要务</span>
              <span className="council-hub-obj-title">{zx.stage.title}</span>
              <span className="council-hub-obj-brief">{zx.stage.brief}</span>
              {incompleteGoals.length > 0 && (
                <span className="council-hub-goals">
                  {incompleteGoals.map((g) => (
                    <span key={g.id} className="council-hub-goal">☐ {g.title}</span>
                  ))}
                </span>
              )}
            </button>
          )}

          <div className="council-hub-actions">
            {actions.map((a) => (
              <button
                key={a.key}
                className={`council-action${a.urgent ? " urgent" : ""}`}
                onClick={a.onClick}
              >
                <span className="council-action-icon">{a.icon}</span>
                <span className="council-action-main">
                  <span className="council-action-label">{a.label}</span>
                  <span className="council-action-detail">{a.detail}</span>
                </span>
                <span className="council-action-cta">{a.cta} <ChevronRight size={12} /></span>
              </button>
            ))}
          </div>

          <footer className="council-hub-loop" title="一回合（一月）的核心循环">
            <span>批奏疏</span><ChevronRight size={11} />
            <span>召大臣</span><ChevronRight size={11} />
            <span>下圣旨</span><ChevronRight size={11} />
            <span>推时日</span><ChevronRight size={11} />
            <span>阅邸报</span>
          </footer>
        </>
      )}
    </section>
  );
}
