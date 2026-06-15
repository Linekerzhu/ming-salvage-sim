import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useGame } from "../GameData";
import { OrgSection } from "./OrgSection";
import { BuildingSection } from "./BuildingSection";
import { Section } from "./Section";

// 舆图含 mapPaths(115KB)，按需懒加载，缩小首屏包。
const RealmMap = lazy(() => import("./RealmMap").then((m) => ({ default: m.RealmMap })));

function Metric({ label, value, suffix }: { label: string; value: any; suffix?: string }) {
  return (
    <div className="m-metric">
      <span className="m-metric-label">{label}</span>
      <span className="m-metric-value">{value ?? "—"}{suffix}</span>
    </div>
  );
}

// 中兴气象：天下治的总仪表——大字指数 + 折线趋势 + 五分项 + 本章诏题清单。长期爽点的去向。
function RevivalCard() {
  const { zhongxing } = useGame();
  if (!zhongxing) return null;
  const total = Number(zhongxing.current?.total ?? 0);
  const parts = zhongxing.current?.parts || {};
  const hist = (zhongxing.history || []).slice(-24).map((h) => Number(h.total));
  const prev = hist.length >= 2 ? hist[hist.length - 2] : total;
  const trend = total - prev;
  const band = total >= 65 ? "中兴在望" : total >= 45 ? "守成持平" : total >= 30 ? "勉力支撑" : "积重难返";
  const tone = total >= 65 ? "ok" : total >= 45 ? "mild" : total >= 30 ? "warn" : "danger";
  const goals = zhongxing.goals || [];
  const doneN = goals.filter((g) => g.done).length;
  // 折线（sparkline）：把最近指数映射到 0..1。
  const lo = Math.min(...hist, total), hi = Math.max(...hist, total, lo + 1);
  const pts = hist.length >= 2
    ? hist.map((v, i) => `${(i / (hist.length - 1)) * 100},${28 - ((v - lo) / (hi - lo)) * 26}`).join(" ")
    : "";

  return (
    <section className={`m-card m-revival rv-${tone}`}>
      <h2 className="m-card-title">中兴气象 <span className="m-revival-band">{band}</span></h2>
      <div className="m-revival-head">
        <div className="m-revival-dial">
          <span className="m-revival-total">{total}</span>
          <span className="m-revival-cap">中兴指数</span>
          {trend !== 0 && (
            <span className={`m-revival-trend ${trend > 0 ? "up" : "down"}`}>
              {trend > 0 ? "▲" : "▼"}{Math.abs(trend)}
            </span>
          )}
        </div>
        {pts && (
          <svg className="m-revival-spark" viewBox="0 0 100 28" preserveAspectRatio="none" aria-label="中兴趋势">
            <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
          </svg>
        )}
      </div>
      <div className="m-revival-parts">
        {Object.entries(parts).map(([k, v]) => (
          <div key={k} className="m-revival-part">
            <span className="m-revival-part-label">{k}</span>
            <div className="m-revival-part-track">
              <span className="m-revival-part-fill" style={{ width: `${Math.max(0, Math.min(100, Number(v)))}%` }} />
            </div>
            <span className="m-revival-part-val">{Number(v)}</span>
          </div>
        ))}
      </div>
      {zhongxing.stage && (
        <div className="m-revival-stage">
          <div className="m-revival-stage-head">
            <span className="m-revival-stage-title">{zhongxing.stage.title}</span>
            <span className="m-revival-stage-prog">{doneN}/{goals.length} 方略告成</span>
          </div>
          {zhongxing.stage.brief && <p className="m-revival-stage-brief">{zhongxing.stage.brief}</p>}
        </div>
      )}
    </section>
  );
}

export function RealmView() {
  const { state, worldVersion } = useGame();
  const [picked, setPicked] = useState<any>(null);
  const metrics = state?.metrics || {};
  const regions: any[] = state?.regions || [];
  const armies: any[] = state?.armies || [];
  const issues: any[] = state?.issues || [];
  const restless = regions.filter((r) => Number(r.unrest) >= 35).length;
  const owing = armies.filter((a) => Number(a.arrears) > 0).length;

  // 舆图转青：动乱较上次明显回落的省份，一过性绿色脉冲——让"治理见效"可见。
  const prevUnrest = useRef<Record<string, number>>({});
  const [improved, setImproved] = useState<string[]>([]);
  useEffect(() => {
    const now: Record<string, number> = {};
    const dropped: string[] = [];
    for (const r of regions) {
      const id = String(r.id);
      const u = Number(r.unrest);
      now[id] = u;
      const was = prevUnrest.current[id];
      if (was != null && was - u >= 5) dropped.push(id);
    }
    prevUnrest.current = now;
    if (dropped.length) {
      setImproved(dropped);
      const t = setTimeout(() => setImproved([]), 2600);
      return () => clearTimeout(t);
    }
  }, [worldVersion]);

  return (
    <div className="m-view m-realm">
      <RevivalCard />
      <section className="m-card m-mapcard">
        <h2 className="m-card-title">天下舆图</h2>
        <Suspense fallback={<div className="m-map-loading">舆图载入中…</div>}>
          <RealmMap regions={regions} selectedId={picked?.id != null ? String(picked.id) : undefined} improvedIds={improved} onPick={(_id, r) => setPicked(r)} />
        </Suspense>
        <div className="m-map-legend">
          <span><i className="lg lg-calm" />安靖</span>
          <span><i className="lg lg-mild" />微澜</span>
          <span><i className="lg lg-warn" />动荡</span>
          <span><i className="lg lg-danger" />糜烂</span>
        </div>
        <p className="m-map-cap">
          {picked ? `${picked.name} · 动乱 ${picked.unrest ?? "—"}${picked.public_support != null ? ` · 民望 ${picked.public_support}` : ""}` : "点选舆图各省查看；色越赤则其地越乱。"}
        </p>
      </section>

      <section className="m-card">
        <h2 className="m-card-title">国势</h2>
        <div className="m-metrics-grid">
          <Metric label="国库" value={metrics["国库"]} suffix=" 万两" />
          <Metric label="内库" value={metrics["内库"]} suffix=" 万两" />
          <Metric label="民心" value={metrics["民心"]} />
          <Metric label="皇威" value={metrics["皇威"]} />
        </div>
      </section>

      <Section title="省份" count={regions.length} tag={restless ? `${restless} 处动荡` : ""} defaultOpen={false}>
        <ul className="m-rowlist">
          {regions.map((r: any) => (
            <li key={r.id || r.name} className="m-row">
              <span className="m-row-name">{r.name}</span>
              <span className={`m-row-tag ${Number(r.unrest) >= 60 ? "danger" : Number(r.unrest) >= 35 ? "warn" : ""}`}>
                动乱 {r.unrest ?? "—"}
              </span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="军队" count={armies.length} tag={owing ? `${owing} 镇欠饷` : ""} defaultOpen={false}>
        <ul className="m-rowlist">
          {armies.map((a: any) => (
            <li key={a.id || a.name} className="m-row">
              <span className="m-row-name">{a.name}</span>
              <span className={`m-row-tag ${Number(a.arrears) > 0 ? "danger" : ""}`}>
                {Number(a.arrears) > 0 ? `欠饷 ${a.arrears}` : "足饷"}
              </span>
              <span className="m-row-tag">士气 {a.morale ?? "—"}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="在办局势" count={issues.length} defaultOpen={issues.length > 0 && issues.length <= 8}>
        {issues.length === 0 ? (
          <p className="m-empty">天下无大事。</p>
        ) : (
          <ul className="m-rowlist">
            {issues.map((it: any) => (
              <li key={it.id} className="m-row">
                <span className="m-row-name">{it.title}</span>
                {it.severity != null && (
                  <span className={`m-row-tag ${Number(it.severity) >= 70 ? "danger" : Number(it.severity) >= 45 ? "warn" : ""}`}>
                    险 {it.severity}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <BuildingSection />
      <OrgSection />
    </div>
  );
}
