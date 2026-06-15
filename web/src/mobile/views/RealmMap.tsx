import { MAP_VIEW_BOX, REGION_PATH_GROUPS, EXTERNAL_PATH_GROUPS, UNMAPPED_PLAYABLE_PATHS } from "../../mapPaths";

// 天下舆图：各省按动荡度上色（一眼见哪里乱），可点选。外藩/未治区中性色。
function unrestFill(u?: number): string {
  if (u == null) return "#cdb88f";          // 中性（无数据省）
  if (u >= 60) return "#9e3429";            // 朱红·糜烂
  if (u >= 45) return "#c2682f";            // 橙·动荡
  if (u >= 30) return "#c9a34a";            // 金·微澜
  return "#4a8060";                          // 青·安靖
}

export function RealmMap({ regions, onPick, selectedId, improvedIds }: { regions: any[]; onPick?: (id: string, r: any) => void; selectedId?: string; improvedIds?: string[] }) {
  const byId: Record<string, any> = {};
  for (const r of regions) byId[String(r.id)] = r;
  const improved = new Set(improvedIds || []);
  return (
    <svg className="m-map" viewBox={MAP_VIEW_BOX} preserveAspectRatio="xMidYMid meet" role="img" aria-label="天下舆图">
      {UNMAPPED_PLAYABLE_PATHS.map((p) => <path key={`u-${p.id}`} d={p.d} className="m-map-unmapped" />)}
      {EXTERNAL_PATH_GROUPS.flatMap((g) => g.paths.map((p) => (
        <path key={`e-${p.id}`} d={p.d} className="m-map-ext" />
      )))}
      {REGION_PATH_GROUPS.flatMap((g) => {
        const r = byId[g.regionId];
        const fill = unrestFill(r ? Number(r.unrest) : undefined);
        const sel = selectedId && g.regionId === selectedId;
        const imp = improved.has(g.regionId);
        return g.paths.map((p) => (
          <path
            key={`r-${p.id}`}
            d={p.d}
            className={`m-map-region${sel ? " is-sel" : ""}${imp ? " is-improved" : ""}`}
            fill={fill}
            onClick={r && onPick ? () => onPick(g.regionId, r) : undefined}
          />
        ));
      })}
    </svg>
  );
}
