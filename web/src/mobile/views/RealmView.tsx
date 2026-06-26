import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useGame } from "../GameData";
import { OrgSection } from "./OrgSection";
import { BuildingSection } from "./BuildingSection";
import { Section } from "./Section";
import { Portrait } from "../Portrait";
import { frontierSupervisor, loadFiscalCenter, loadPolicyCenter, loadStatecraftCenter, privyRelief } from "../api";

// 军镇行：欠饷/士气/离心 + 监军太监（E4：天子耳目钳制割据，代价是掣肘军务）。
function ArmyRow({ a }: { a: any }) {
  const { refresh } = useGame();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const sup = a.supervisor || "";
  const autonomy = Number(a.autonomy || 0);
  async function act(recall: boolean) {
    if (busy) return;
    setBusy(true); setMsg("");
    try {
      const r = await frontierSupervisor(String(a.id), recall ? { recall: true } : {});
      setMsg(r.message || "");
      await refresh();
    } catch (e: any) { setMsg(e?.message || "未成。"); }
    finally { setBusy(false); }
  }
  return (
    <li className="m-row m-army-row">
      <div className="m-army-line">
        <span className="m-row-name">{a.name}</span>
        <span className={`m-row-tag ${Number(a.arrears) > 0 ? "danger" : ""}`}>
          {Number(a.arrears) > 0 ? `欠饷 ${a.arrears}` : "足饷"}
        </span>
        <span className="m-row-tag">士气 {a.morale ?? "—"}</span>
        {autonomy >= 45 && <span className={`m-row-tag ${autonomy >= 72 ? "danger" : "warn"}`}>自专 {autonomy}</span>}
      </div>
      <div className="m-army-sup">
        {sup ? (
          <>
            <span className="m-sup-tag"><Portrait name={sup} size={18} interactive={false} />监军 {sup}</span>
            <button className="m-mini-btn" disabled={busy} onClick={() => act(true)}>撤监军</button>
          </>
        ) : (
          <button className="m-mini-btn" disabled={busy} onClick={() => act(false)}>遣监军钳制</button>
        )}
      </div>
      {msg && <p className="m-army-msg">{msg}</p>}
    </li>
  );
}

// 内帑助饷：发私帑（内库）补太仓、清边军欠饷——崇祯朝最揪心的道德抉择，
// 也是堆积内库的去处。发帑则军心民心一振、君父示天下以诚，然私帑日削、再无急变之储。
function PrivyReliefBar({ neiKu, owing }: { neiKu: number; owing: number }) {
  const { refresh } = useGame();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  if (neiKu <= 0) return null;
  async function act() {
    if (busy) return;
    setBusy(true); setMsg("");
    try {
      const r = await privyRelief();  // 省略额度＝按欠饷总额（或 50 万两）
      setMsg(r.message || "");
      await refresh();
    } catch (e: any) { setMsg(e?.message || "未成。"); }
    finally { setBusy(false); }
  }
  return (
    <div className="m-privy">
      <button className="m-privy-btn" disabled={busy} onClick={act}>
        发内帑助饷{owing ? "（清边军欠饷）" : ""}
      </button>
      <span className="m-privy-hint">私帑 {neiKu} 万两{owing ? ` · ${owing} 镇欠饷待解` : " · 可拨补太仓"}</span>
      {msg && <p className="m-privy-msg">{msg}</p>}
    </div>
  );
}

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

function Money({ value, signed = false }: { value: any; signed?: boolean }) {
  const n = Number(value || 0);
  const prefix = signed && n > 0 ? "+" : "";
  return <>{prefix}{n} 万两</>;
}

function DoctrineIssueStrip({ doctrine }: { doctrine: any }) {
  if (!doctrine?.id) return null;
  const factions = Array.isArray(doctrine.factions) ? doctrine.factions.slice(0, 3) : [];
  const figures = Array.isArray(doctrine.figures) ? doctrine.figures.slice(0, 2) : [];
  const conflicts = Array.isArray(doctrine.active_conflicts) ? doctrine.active_conflicts : [];
  const stateLabel = String(doctrine.state_label || "").trim();
  const reformReady = !!doctrine.reform_ready;
  const reformHint = String(doctrine.reform_hint || "").trim();
  const toneFor = (stance: string) => stance === "support" ? "good" : stance === "oppose" ? "bad" : "neutral";
  const stanceWord = (stance: string) => stance === "support" ? "赞" : stance === "oppose" ? "阻" : "观望";
  return (
    <div className="m-outcome-strip is-compact m-issue-doctrine" aria-label="国策路线争议">
      <span className="m-outcome-chip">路线 {doctrine.name || doctrine.id}</span>
      {doctrine.axis && <span className="m-outcome-chip">轴 {doctrine.axis}</span>}
      {doctrine.bar_value != null && <span className="m-outcome-chip">正统 {Number(doctrine.bar_value)}/100</span>}
      {stateLabel && <span className={`m-outcome-chip tone-${reformReady ? "good" : doctrine.establishment_blocked ? "bad" : "neutral"}`}>{stateLabel}</span>}
      {factions.map((f: any) => {
        const stance = String(f.stance || "neutral");
        const label = stance === "support"
          ? `${f.faction} 支${Number(f.support || 0)}`
          : stance === "oppose"
            ? `${f.faction} 反${Number(f.oppose || 0)}`
            : `${f.faction} 观望`;
        return <span key={`${f.faction}-${stance}`} className={`m-outcome-chip tone-${toneFor(stance)}`}>{label}</span>;
      })}
      {figures.map((p: any) => (
        <span key={`${p.name}-${p.stance}`} className={`m-outcome-chip tone-${toneFor(String(p.stance || "neutral"))}`}>
          {p.name}{stanceWord(String(p.stance || "neutral"))}
        </span>
      ))}
      {conflicts.length > 0 && (
        <span className="m-outcome-chip tone-bad">冲突 {conflicts.map((c: any) => c.name || c.id).join("、")}</span>
      )}
      {reformHint && (
        <span className={`m-outcome-chip tone-${reformReady ? "good" : "warn"}`}>{reformReady ? "准路线疏可改弦" : "须推至待定策"}</span>
      )}
    </div>
  );
}

function RouteBoard({ center }: { center: any }) {
  const orthodox = Array.isArray(center?.orthodox) ? center.orthodox : [];
  const contested = Array.isArray(center?.contested) ? center.contested : [];
  const latent = Array.isArray(center?.latent) ? center.latent : [];
  const conflicts = [...orthodox, ...contested]
    .flatMap((route: any) => route.active_conflicts || route.conflicts || [])
    .filter((item: any) => item?.id || item?.name)
    .slice(0, 4);
  return (
    <section className="m-card">
      <h2 className="m-card-title">国策中枢</h2>
      <div className="m-metrics-grid">
        <Metric label="基本国策" value={orthodox.length} />
        <Metric label="路线争议" value={contested.length} />
        <Metric label="潜势路线" value={latent.length} />
        <Metric label="路线冲突" value={conflicts.length} />
      </div>
      <div className="m-outcome-strip is-compact" aria-label="现行基本国策">
        {orthodox.length === 0 ? (
          <span className="m-outcome-chip">尚无定策</span>
        ) : orthodox.slice(0, 5).map((route: any) => (
          <span key={route.id} className="m-outcome-chip tone-good">
            {route.name || route.id}{route.axis ? ` · ${route.axis}` : ""}
          </span>
        ))}
      </div>
      {contested.length > 0 && (
        <ul className="m-rowlist">
          {contested.slice(0, 4).map((route: any) => (
            <li key={route.id} className="m-row">
              <span className="m-row-name">{route.name || route.id}</span>
              <span className="m-row-tag warn">正统 {Number(route.bar_value || 0)}/100</span>
              {!!route.establishment_blocked && <span className="m-row-tag danger">冲突</span>}
              <DoctrineIssueStrip doctrine={route} />
            </li>
          ))}
        </ul>
      )}
      {conflicts.length > 0 && (
        <p className="m-empty">冲突路线：{conflicts.map((item: any) => item.name || item.id).join("、")}</p>
      )}
    </section>
  );
}

function StatecraftBoard({ center }: { center: any }) {
  const topbar = Array.isArray(center?.topbar) ? center.topbar : [];
  const lanes = Array.isArray(center?.economy_lanes) ? center.economy_lanes : [];
  const capacities = Array.isArray(center?.capacity_rows) ? center.capacity_rows : [];
  const bureaucracyLanes = Array.isArray(center?.bureaucracy_lanes) ? center.bureaucracy_lanes : [];
  const queueRows = Array.isArray(center?.directive_queue_rows) ? center.directive_queue_rows : [];
  const bottlenecks = Array.isArray(center?.bottlenecks) ? center.bottlenecks : [];
  const shownTop = topbar.slice(0, 5);
  const coreCapacities = capacities
    .filter((row: any) => ["fiscal", "military", "construction", "local", "personnel", "procedure", "inner", "investigation"].includes(String(row.domain || "")))
    .slice(0, 8);
  const coreBureaucracyLanes = bureaucracyLanes
    .filter((row: any) => ["fiscal", "military", "construction", "local", "personnel", "procedure", "inner", "investigation"].includes(String(row.domain || "")))
    .slice(0, 8);
  const tagClass = (tone: any) => tone === "danger" ? "danger" : tone === "warn" ? "warn" : "";
  return (
    <section className="m-card">
      <h2 className="m-card-title">国家机器</h2>
      <div className="m-metrics-grid">
        {shownTop.map((item: any) => (
          <Metric
            key={item.key || item.label}
            label={item.label || item.key}
            value={Number(item.value || 0)}
            suffix={item.unit ? ` ${item.unit}` : ""}
          />
        ))}
      </div>
      {center?.model?.principle && <p className="m-empty">{center.model.principle}</p>}
      <div className="m-policy-subpanel">
        <h3>四条账 <span>{lanes.length}</span></h3>
        <ul className="m-rowlist">
          {lanes.map((line: any) => (
            <li key={line.id || line.label} className="m-row">
              <span className="m-row-name">{line.label}</span>
              <span className={`m-row-tag ${Number(line.value || 0) < 0 ? "danger" : ""}`}>
                <Money value={line.value} signed />{line.unit ? `/${String(line.unit).replace("万两/", "")}` : ""}
              </span>
              <span className="m-row-sub">{line.note}</span>
              {line.capacity_domain && (
                <span className="m-row-sub">关联产能：{line.capacity_domain} {Number(line.capacity_score || 0)}</span>
              )}
            </li>
          ))}
        </ul>
      </div>
      <div className="m-policy-subpanel">
        <h3>旨意生产线 <span>{queueRows.length}</span></h3>
        {queueRows.length === 0 ? (
          <p className="m-empty">暂无在办旨意占用国家机器。</p>
        ) : (
          <ul className="m-rowlist">
            {queueRows.slice(0, 6).map((item: any) => (
              <li key={`queue-${item.id}`} className="m-row">
                <span className="m-row-name">{item.title || item.text}</span>
                <span className={`m-row-tag ${tagClass(item.tone)}`}>{item.status_label || item.status} {Number(item.progress || 0)}%</span>
                <span className="m-row-sub">
                  {item.assignee ? `${item.assignee} · ` : ""}{(item.domains || []).join(" / ")} · 产能 {Number(item.capacity_score || 0)}
                  {Number(item.remaining_days || 0) > 0 ? ` · 约${item.remaining_days}日` : ""}
                </span>
                {(item.constrained_by || []).slice(0, 2).map((line: any, idx: number) => (
                  <span key={`queue-${item.id}-c-${idx}`} className="m-row-sub">卡点：{line}</span>
                ))}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="m-policy-subpanel">
        <h3>部门产能 <span>{coreCapacities.length}</span></h3>
        <ul className="m-rowlist">
          {coreCapacities.map((row: any) => (
            <li key={row.domain || row.label} className="m-row">
              <span className="m-row-name">{row.label}</span>
              <span className={`m-row-tag ${tagClass(row.tone)}`}>{row.status} {Number(row.score || 0)}</span>
              <span className="m-row-sub">{row.effect}</span>
              {(row.institutions || []).slice(0, 2).map((inst: any) => (
                <span key={`${row.domain}-${inst.id || inst.name}`} className="m-row-sub">
                  {inst.name} 执行力 {Number(inst.readiness || 0)}{Number(inst.vacancy_count || 0) > 0 ? ` · 空缺 ${inst.vacancy_count}` : ""}
                </span>
              ))}
            </li>
          ))}
        </ul>
      </div>
      <div className="m-policy-subpanel">
        <h3>官僚泳道 <span>{coreBureaucracyLanes.length}</span></h3>
        <ul className="m-rowlist">
          {coreBureaucracyLanes.map((row: any) => (
            <li key={`lane-${row.domain || row.label}`} className="m-row">
              <span className="m-row-name">{row.label}</span>
              <span className={`m-row-tag ${tagClass(row.tone)}`}>{row.load_status} · {Number(row.active_count || 0)} 线</span>
              <span className="m-row-sub">{row.effect}</span>
              {(row.active_directives || []).slice(0, 3).map((item: any) => (
                <span key={`lane-${row.domain}-${item.id}`} className="m-row-sub">
                  {item.title} · {item.status_label} {Number(item.progress || 0)}%
                </span>
              ))}
              {(row.weak_institutions || []).slice(0, 2).map((inst: any) => (
                <span key={`lane-${row.domain}-${inst.id || inst.name}`} className="m-row-sub">
                  薄弱：{inst.name} 执行力 {Number(inst.readiness || 0)}{Number(inst.vacancy_count || 0) > 0 ? ` · 空缺 ${inst.vacancy_count}` : ""}
                </span>
              ))}
            </li>
          ))}
        </ul>
      </div>
      <div className="m-policy-subpanel">
        <h3>瓶颈警报 <span>{bottlenecks.length}</span></h3>
        {bottlenecks.length === 0 ? (
          <p className="m-empty">暂无国家机器级瓶颈。</p>
        ) : (
          <ul className="m-rowlist">
            {bottlenecks.map((item: any) => (
              <li key={item.kind || item.title} className="m-row">
                <span className="m-row-name">{item.title}</span>
                <span className={`m-row-tag ${tagClass(item.tone)}`}>{item.tone === "danger" ? "危" : item.tone === "warn" ? "警" : "记"}</span>
                <span className="m-row-sub">{item.detail}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function FiscalBoard({ fiscal }: { fiscal: any }) {
  const accounts = fiscal?.net_by_account || {};
  const guo = accounts["国库"] || {};
  const nei = accounts["内库"] || {};
  const revenue = Array.isArray(fiscal?.revenue_sources) ? fiscal.revenue_sources : [];
  const expense = Array.isArray(fiscal?.expense_sources) ? fiscal.expense_sources : [];
  const revenueFamilies = Array.isArray(fiscal?.revenue_family_rows) && fiscal.revenue_family_rows.length
    ? fiscal.revenue_family_rows
    : revenue;
  const expenseFamilies = Array.isArray(fiscal?.expense_family_rows) && fiscal.expense_family_rows.length
    ? fiscal.expense_family_rows
    : expense;
  const provinces = Array.isArray(fiscal?.province_tax_rows) ? fiscal.province_tax_rows : [];
  const explainers = Array.isArray(fiscal?.explainers) ? fiscal.explainers : [];
  const questions = Array.isArray(fiscal?.money_questions) ? fiscal.money_questions : [];
  const ledger = Array.isArray(fiscal?.ledger_movements)
    ? fiscal.ledger_movements.filter((line: any) => String(line.category || "") !== "期初").slice(0, 6)
    : [];
  const ledgerSummary = fiscal?.ledger_summary || {};
  const provinceTop = [...provinces].sort((a: any, b: any) => Number(b.province_total || 0) - Number(a.province_total || 0)).slice(0, 4);
  return (
    <section className="m-card">
      <h2 className="m-card-title">财政账簿</h2>
      <div className="m-metrics-grid">
        <Metric label="国库月入" value={Number(guo.income_total || 0)} suffix=" 万两" />
        <Metric label="国库月支" value={Number(guo.expense_total || 0)} suffix=" 万两" />
        <Metric label="国库月净" value={Number(guo.net || 0)} suffix=" 万两" />
        <Metric label="下月缺口" value={Number((fiscal?.totals || {}).cash_gap_next_month || guo.cash_gap_next_month || 0)} suffix=" 万两" />
        <Metric label="内库月入" value={Number(nei.income_total || 0)} suffix=" 万两" />
        <Metric label="内库月净" value={Number(nei.net || 0)} suffix=" 万两" />
      </div>
      <div className="m-policy-subpanel">
        <h3>财政三问 <span>{questions.length || 3}</span></h3>
        <ul className="m-rowlist">
          {questions.length > 0 ? questions.map((q: any) => (
            <li key={q.id || q.title} className="m-row">
              <span className="m-row-name">{q.title}</span>
              <span className="m-row-sub">{q.answer}</span>
              {(Array.isArray(q.lines) ? q.lines : []).slice(0, 4).map((line: any, idx: number) => (
                <span key={`${q.id || q.title}-${idx}`} className="m-row-sub">{line}</span>
              ))}
            </li>
          )) : (
            <li className="m-row">
              <span className="m-row-name">怎么赚钱、钱花在哪、余额为什么变</span>
              <span className="m-row-sub">财政中枢正在整理账簿。</span>
            </li>
          )}
        </ul>
      </div>
      <div className="m-policy-subpanel">
        <h3>税源拆账 <span>{revenueFamilies.length}</span></h3>
        <ul className="m-rowlist">
          {revenueFamilies.map((line: any, idx: number) => (
            <li key={`${line.account}-${line.name}-${idx}`} className="m-row">
              <span className="m-row-name">{line.account} · {line.name}</span>
              <span className="m-row-tag"><Money value={line.amount} /></span>
              {line.note && <span className="m-row-sub">{line.note}</span>}
              {line.base_amount != null && Number(line.base_amount) !== Number(line.amount || 0) && (
                <span className="m-row-sub">账面基数 {Number(line.base_amount)} 万两，实际到账 {Number(line.amount || 0)} 万两。</span>
              )}
            </li>
          ))}
        </ul>
      </div>
      <div className="m-policy-subpanel">
        <h3>支出账 <span>{expenseFamilies.length}</span></h3>
        <ul className="m-rowlist">
          {expenseFamilies.map((line: any, idx: number) => (
            <li key={`${line.account}-${line.name}-${idx}`} className="m-row">
              <span className="m-row-name">{line.account} · {line.name}</span>
              <span className="m-row-tag danger"><Money value={line.amount} /></span>
              {(line.why || line.note) && <span className="m-row-sub">{line.why || line.note}</span>}
            </li>
          ))}
        </ul>
      </div>
      <div className="m-policy-subpanel">
        <h3>余额流水 <span>{ledger.length}</span></h3>
        <ul className="m-rowlist">
          {ledger.length > 0 && (
            <li className="m-row">
              <span className="m-row-name">{ledgerSummary.window || "近期流水"}</span>
              <span className={`m-row-tag ${Number(ledgerSummary.net || 0) < 0 ? "danger" : ""}`}>
                <Money value={ledgerSummary.net} signed />
              </span>
              <span className="m-row-sub">
                收入 {Number(ledgerSummary.income_total || 0)} 万两 · 支出 {Number(ledgerSummary.expense_total || 0)} 万两
              </span>
            </li>
          )}
          {ledger.map((line: any) => (
            <li key={`ledger-${line.id}`} className="m-row">
              <span className="m-row-name">{line.account} · {line.reason || line.category}</span>
              <span className={`m-row-tag ${Number(line.delta || 0) < 0 ? "danger" : ""}`}>
                <Money value={line.delta} signed />
              </span>
              <span className="m-row-sub">余 {Number(line.balance_after || 0)} 万两 · {line.year}年{line.period}月</span>
            </li>
          ))}
          {ledger.length === 0 && (
            <li className="m-row">
              <span className="m-row-name">暂无近期流水</span>
              <span className="m-row-sub">月结、拨款、抄没、赈济、补饷都会在这里留下余额变化。</span>
            </li>
          )}
        </ul>
      </div>
      <div className="m-policy-subpanel">
        <h3>为什么钱少了 <span>{explainers.length || provinceTop.length}</span></h3>
        <ul className="m-rowlist">
          {explainers.map((item: any, idx: number) => (
            <li key={`${item.kind}-${idx}`} className="m-row">
              <span className="m-row-name">{item.label}</span>
              {item.detail && <span className="m-row-sub">{item.detail}</span>}
            </li>
          ))}
          {provinceTop.map((row: any) => (
            <li key={`tax-${row.region_id}`} className="m-row">
              <span className="m-row-name">{row.name}</span>
              <span className="m-row-tag">到账 {Number(row.efficiency || 0).toFixed(2)}</span>
              <span className="m-row-sub">
                田赋{row.田赋} · 辽饷{row.辽饷} · 盐税{row.盐税} · 商税{row.商税}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function ConstraintBoard({ center, fiscal, regions, armies }: { center: any; fiscal: any; regions: any[]; armies: any[] }) {
  const territory = center?.strategic_snapshot?.territory || {};
  const army = center?.strategic_snapshot?.army || {};
  const restless = Number(territory.restless_regions ?? regions.filter((r) => Number(r.unrest) >= 35).length);
  const danger = Number(territory.danger_regions ?? regions.filter((r) => Number(r.unrest) >= 60).length);
  const arrears = Number(army.arrears_total ?? (fiscal?.totals || {}).army_arrears ?? armies.reduce((sum, a) => sum + Number(a.arrears || 0), 0));
  const owing = Number(army.owing_armies ?? armies.filter((a) => Number(a.arrears) > 0).length);
  return (
    <section className="m-card">
      <h2 className="m-card-title">疆域与军队约束</h2>
      <div className="m-metrics-grid">
        <Metric label="动荡省份" value={restless} />
        <Metric label="糜烂省份" value={danger} />
        <Metric label="欠饷军镇" value={owing} />
        <Metric label="欠饷缺口" value={arrears} suffix=" 万两" />
      </div>
      <div className="m-outcome-strip is-compact" aria-label="国策约束">
        {Number((fiscal?.totals || {}).operating_gap || 0) > 0 && (
          <span className="m-outcome-chip tone-bad">国库月缺 {Number((fiscal?.totals || {}).operating_gap)} 万两</span>
        )}
        {restless > 0 && <span className="m-outcome-chip tone-warn">{restless} 省动乱牵制国策</span>}
        {arrears > 0 && <span className="m-outcome-chip tone-bad">军饷欠发拖累边防</span>}
        {center?.inner_court_tools?.route_status && (
          <span className="m-outcome-chip">内廷工具：{center.inner_court_tools.route_status}</span>
        )}
      </div>
    </section>
  );
}

function WorkstreamBoard({ center }: { center: any }) {
  const work = center?.workstreams || {};
  const memorials = Array.isArray(work.memorials) ? work.memorials : [];
  const directives = Array.isArray(work.directives) ? work.directives : [];
  const agreements = Array.isArray(work.agreements) ? work.agreements : [];
  const total = memorials.length + directives.length + agreements.length;
  return (
    <Section title="奏疏、旨意与履约证据" count={total} defaultOpen={total > 0 && total <= 8}>
      {total === 0 ? (
        <p className="m-empty">暂无国策相关奏疏、在办旨意或履约承诺。</p>
      ) : (
        <ul className="m-rowlist">
          {memorials.slice(0, 4).map((m: any) => (
            <li key={`m-${m.id}`} className="m-row">
              <span className="m-row-name">奏疏 · {m.summary || m.issue_title}</span>
              <span className="m-row-tag">{m.kind}</span>
              <span className="m-row-sub">{m.author}{m.status ? ` · ${m.status}` : ""}</span>
            </li>
          ))}
          {directives.slice(0, 4).map((d: any) => (
            <li key={`d-${d.id}`} className="m-row">
              <span className="m-row-name">旨意 · {d.doctrine_name}</span>
              <span className="m-row-tag">{d.status}</span>
              <span className="m-row-sub">{d.text}</span>
            </li>
          ))}
          {agreements.slice(0, 4).map((a: any) => (
            <li key={`a-${a.id}`} className="m-row">
              <span className="m-row-name">履约 · {a.core_topic || a.topic}</span>
              <span className="m-row-tag">{a.status_label || a.status}</span>
              <span className="m-row-sub">{a.minister_name}{a.evidence ? ` · ${a.evidence}` : ""}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
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

export function PolicyCenterView() {
  const { state, worldVersion } = useGame();
  const [picked, setPicked] = useState<any>(null);
  const [policyCenter, setPolicyCenter] = useState<any>(null);
  const [fiscalCenter, setFiscalCenter] = useState<any>(null);
  const [statecraftCenter, setStatecraftCenter] = useState<any>(null);
  const metrics = state?.metrics || {};
  const regions: any[] = state?.regions || [];
  const armies: any[] = state?.armies || [];
  const issues: any[] = state?.issues || [];
  const restless = regions.filter((r) => Number(r.unrest) >= 35).length;
  const owing = armies.filter((a) => Number(a.arrears) > 0).length;
  useEffect(() => {
    let alive = true;
    Promise.all([loadPolicyCenter(), loadFiscalCenter(), loadStatecraftCenter()])
      .then(([policy, fiscal, statecraft]) => {
        if (!alive) return;
        setPolicyCenter(policy);
        setFiscalCenter(fiscal);
        setStatecraftCenter(statecraft);
      })
      .catch(() => {
        if (!alive) return;
        setPolicyCenter(null);
        setFiscalCenter(null);
        setStatecraftCenter(null);
      });
    return () => { alive = false; };
  }, [worldVersion]);

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
    <div className="m-view m-realm m-policy">
      <RouteBoard center={policyCenter} />
      <StatecraftBoard center={statecraftCenter} />
      <FiscalBoard fiscal={fiscalCenter} />
      <ConstraintBoard center={policyCenter} fiscal={fiscalCenter} regions={regions} armies={armies} />
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
        <PrivyReliefBar neiKu={Number(metrics["内库"]) || 0} owing={owing} />
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
          {armies.map((a: any) => <ArmyRow key={a.id || a.name} a={a} />)}
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
                <DoctrineIssueStrip doctrine={it.policy_doctrine} />
              </li>
            ))}
          </ul>
        )}
      </Section>

      <WorkstreamBoard center={policyCenter} />

      <BuildingSection />
      <OrgSection />
    </div>
  );
}

export const RealmView = PolicyCenterView;
