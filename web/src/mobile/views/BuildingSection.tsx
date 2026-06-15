import { useEffect, useState } from "react";
import { loadBuildings } from "../api";
import type { Building } from "../api";
import { Section } from "./Section";

// 天下·建筑：城防/仓廪/工坊等。condition 完好度严重度上色（<30红/<55橙），示产出。
const CAT_ICON: Record<string, string> = { 军事: "城", 经济: "仓", 民生: "坊", 内廷: "宫" };

function condClass(c: number): string {
  if (c < 30) return "danger";
  if (c < 55) return "warn";
  return "";
}

export function BuildingSection() {
  const [bs, setBs] = useState<Building[] | null>(null);
  useEffect(() => {
    loadBuildings().then(setBs).catch(() => setBs([]));
  }, []);
  if (bs === null) return <p className="m-empty m-card">建筑载入中…</p>;
  if (bs.length === 0) return null;
  // 完好度低者置前（一眼见危）
  const sorted = [...bs].sort((a, b) => Number(a.condition ?? 100) - Number(b.condition ?? 100));
  const ailing = sorted.filter((b) => Number(b.condition ?? 100) < 55).length;
  return (
    <Section title="建筑" count={bs.length} tag={ailing > 0 ? `失修 ${ailing}` : ""} defaultOpen={false}>
      <ul className="m-rowlist">
        {sorted.slice(0, 24).map((b) => {
          const cond = Number(b.condition ?? 0);
          const out = b.output_metric && Number(b.output_amount) > 0
            ? `${b.output_metric}+${Math.round(Number(b.output_amount) * cond / 100)}` : "";
          return (
            <li key={b.id || b.name} className="m-row">
              <span className="m-row-glyph">{CAT_ICON[String(b.category)] || "建"}</span>
              <span className="m-row-name">{b.name}</span>
              {out && <span className="m-row-sub">{out}</span>}
              <span className={`m-row-tag ${condClass(cond)}`}>完好 {cond}</span>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}
