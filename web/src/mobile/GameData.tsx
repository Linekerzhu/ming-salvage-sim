// 全局数据上下文：集中加载 state/time/desk/lifecycle，统一 refresh 与推进时日。
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  advanceTime,
  loadDesk,
  loadGameState,
  loadDecision,
  loadLifecycle,
  loadTime,
  loadZhongxing,
} from "./api";
import type {
  AdvanceResult,
  Decision,
  DeskPayload,
  DirectiveLifecycle,
  GameState,
  TickEvent,
  TimeStatus,
  ZhongxingPayload,
} from "./api";

export type GameData = {
  state: GameState | null;
  time: TimeStatus | null;
  desk: DeskPayload | null;
  lifecycle: DirectiveLifecycle[];
  recentEvents: TickEvent[]; // 最近推进/复命产生的事件（御前朝报）
  zhongxing: ZhongxingPayload | null; // 中兴气象 + 阶段诏题
  milestone: { key: number; title: string } | null; // 诏题达成成就（一过性庆祝）
  decision: Decision | null; // 待裁断的抉择事件（CK3 化 P2）
  loading: boolean;
  error: string;
  worldVersion: number;
  metricFlash: { key: number; deltas: Record<string, number> } | null;
  refresh: () => Promise<GameState | null>;
  advance: (days: number, stopOnYellow?: boolean) => Promise<AdvanceResult | null>;
};

const Ctx = createContext<GameData | null>(null);

export const useGame = (): GameData => {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useGame 必须在 GameDataProvider 内使用");
  return ctx;
};

export function GameDataProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<GameState | null>(null);
  const [time, setTime] = useState<TimeStatus | null>(null);
  const [desk, setDesk] = useState<DeskPayload | null>(null);
  const [lifecycle, setLifecycle] = useState<DirectiveLifecycle[]>([]);
  const [recentEvents, setRecentEvents] = useState<TickEvent[]>([]);
  const [zhongxing, setZhongxing] = useState<ZhongxingPayload | null>(null);
  const [milestone, setMilestone] = useState<{ key: number; title: string } | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const doneGoalsRef = useRef<Set<string>>(new Set());
  const milestoneSeeded = useRef(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [worldVersion, setWorldVersion] = useState(0);
  const [metricFlash, setMetricFlash] = useState<{ key: number; deltas: Record<string, number> } | null>(null);
  const inflight = useRef(false);
  const metricsRef = useRef<Record<string, number>>({});
  // 君威/任事 来自 desk（非 state.metrics），单独留存以便推进后比对、上飘高光。
  const swayRef = useRef<Record<string, number>>({});

  const refresh = useCallback(async (): Promise<GameState | null> => {
    if (inflight.current) return null;
    inflight.current = true;
    try {
      const [s, t, d, lc, zx, dec] = await Promise.all([
        loadGameState().catch(() => null),
        loadTime().catch(() => null),
        loadDesk().catch(() => null),
        loadLifecycle().catch(() => [] as DirectiveLifecycle[]),
        loadZhongxing().catch(() => null),
        loadDecision().catch(() => ({ decision: null })),
      ]);
      setDecision(dec?.decision ?? null);
      if (s) { setState(s); metricsRef.current = (s.metrics || {}) as Record<string, number>; }
      if (t) setTime(t);
      if (d) {
        setDesk(d);
        swayRef.current = { 君威: Number((d as any).shi ?? 0), 任事: Number((d as any).renshi_willingness ?? 0) };
      }
      setLifecycle(lc);
      if (zx) {
        setZhongxing(zx);
        // 诏题达成检测：首刷只播种已完成集合（不庆祝旧成就），之后新达成→成就庆祝。
        const nowDone = (zx.goals || []).filter((g) => g.done);
        if (!milestoneSeeded.current) {
          doneGoalsRef.current = new Set(nowDone.map((g) => g.id));
          milestoneSeeded.current = true;
        } else {
          const fresh = nowDone.find((g) => !doneGoalsRef.current.has(g.id));
          if (fresh) setMilestone({ key: Date.now(), title: fresh.title });
          doneGoalsRef.current = new Set(nowDone.map((g) => g.id));
        }
      }
      setError("");
      setWorldVersion((v) => v + 1);
      return s;
    } catch (e: any) {
      setError(String(e?.message || e || "加载失败"));
      return null;
    } finally {
      inflight.current = false;
      setLoading(false);
    }
  }, []);

  const FLASH_KEYS = ["国库", "内库", "民心", "皇威"];
  const advance = useCallback(
    async (days: number, stopOnYellow = true): Promise<AdvanceResult | null> => {
      try {
        const before = { ...metricsRef.current };
        const beforeSway = { ...swayRef.current };
        const result = await advanceTime(days, stopOnYellow);
        const evs = (result.reports || []).flatMap((r) => r.events || []);
        if (evs.length) setRecentEvents((prev) => [...evs, ...prev].slice(0, 40));
        const s = await refresh();
        const after = (s?.metrics || metricsRef.current) as Record<string, number>;
        const deltas: Record<string, number> = {};
        for (const k of FLASH_KEYS) {
          const dv = Number(after[k] || 0) - Number(before[k] || 0);
          if (dv) deltas[k] = dv;
        }
        // 君威/任事 翻盘瞬间（破崇祯陷阱、政令复行）同样上飘，让"赢的一手"可见。
        for (const k of ["君威", "任事"]) {
          const dv = Number(swayRef.current[k] || 0) - Number(beforeSway[k] || 0);
          if (dv) deltas[k] = dv;
        }
        if (Object.keys(deltas).length) setMetricFlash({ key: Date.now(), deltas });
        return result;
      } catch (e: any) {
        setError(String(e?.message || e || "推进失败"));
        return null;
      }
    },
    [refresh],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value: GameData = {
    state,
    time,
    desk,
    lifecycle,
    recentEvents,
    zhongxing,
    milestone,
    decision,
    loading,
    error,
    worldVersion,
    metricFlash,
    refresh,
    advance,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
