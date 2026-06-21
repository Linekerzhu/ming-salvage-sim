import { useCallback, useEffect, useState } from "react";
import { fillOrganizationVacancy, loadOrganizations } from "../api";
import type { OrgInstitution, OrgSlot } from "../api";
import { useGame } from "../GameData";
import { Section } from "./Section";

// 官制·衙门：衙门(管事) → 席位(下属) → 在任/空缺。
// 「信任官僚 vs 微操」的舞台——空缺多则体系运转不灵，皇帝须取舍是否亲自补位。
function InstitutionCard({
  inst,
  busyKey,
  onFill,
}: {
  inst: OrgInstitution;
  busyKey: string;
  onFill: (inst: OrgInstitution, slot: OrgSlot, method: string) => void;
}) {
  const [open, setOpen] = useState(Number(inst.vacancy_count || 0) > 0);
  const held = Number(inst.holder_count || 0);
  const vac = Number(inst.vacancy_count || 0);
  const readiness = Number(inst.readiness || 0);
  const slotMethods = (slot: OrgSlot): Array<[string, string]> => {
    const text = `${slot.title || ""} ${slot.office_type || ""}`;
    const inner = /司礼监|东厂|太监|宦官|内廷|内官|小火者/.test(text);
    if (inner) return [["auto", "补内侍"], ["restore", "起复旧内臣"]];
    return [["auto", "补一人"], ["exam", "开科取士"], ["recommend", "举贤入京"], ["restore", "起复旧臣"]];
  };
  useEffect(() => {
    if (vac > 0) setOpen(true);
  }, [vac]);
  return (
    <div className="m-card m-inst">
      <button className="m-inst-head" onClick={() => setOpen((v) => !v)}>
        <span className="m-inst-name">{inst.name}</span>
        <span className="m-inst-counts">
          <span className="m-row-tag">在任 {held}</span>
          {vac > 0 && <span className="m-row-tag danger">缺 {vac}</span>}
        </span>
      </button>
      <div className="m-readiness"><span className="m-readiness-bar" style={{ width: `${Math.max(0, Math.min(100, readiness))}%` }} /></div>
      {inst.execution_summary && <p className="m-inst-summary">{inst.execution_summary}</p>}
      {open && (inst.slots || []).map((s, i) => (
        <div key={i} className="m-slot">
          <div className="m-slot-title">{s.title || "席"}{Number(s.vacancies) > 0 && <span className="m-row-tag danger">缺{s.vacancies}</span>}</div>
          {(s.holders || []).length > 0 ? (
            <div className="m-slot-holders">{(s.holders || []).map((h: any) => h.name).filter(Boolean).join("、")}</div>
          ) : (
            <div className="m-slot-empty">虚位以待{s.match_hint ? `（${s.match_hint}）` : ""}</div>
          )}
          {Number(s.vacancies || 0) > 0 && !s.open_pool && (
            <div className="m-slot-actions" aria-label={`${s.title || "席"}补缺方式`}>
              {slotMethods(s).map(([method, label]) => {
                const key = `${inst.id || inst.name || ""}::${s.title || ""}`;
                const busy = busyKey.startsWith(`${key}::`);
                return (
                  <button
                    key={method}
                    type="button"
                    className={`m-slot-action ${method === "auto" ? "primary" : ""}`}
                    disabled={busy}
                    onClick={() => onFill(inst, s, method)}
                  >
                    {busyKey === `${key}::${method}` ? "补缺中…" : label}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function OrgSection() {
  const { refresh, worldVersion } = useGame();
  const [insts, setInsts] = useState<OrgInstitution[] | null>(null);
  const [busyKey, setBusyKey] = useState("");
  const [msg, setMsg] = useState("");
  const reload = useCallback(() => {
    loadOrganizations().then((r) => setInsts(r.institutions)).catch(() => setInsts([]));
  }, []);
  useEffect(() => {
    reload();
  }, [reload, worldVersion]);
  const handleFill = async (inst: OrgInstitution, slot: OrgSlot, method: string) => {
    const institutionId = String(inst.id || inst.name || "");
    const slotTitle = String(slot.title || "");
    const key = `${institutionId}::${slotTitle}`;
    setBusyKey(`${key}::${method}`);
    setMsg("");
    try {
      const result = await fillOrganizationVacancy(institutionId, slotTitle, method);
      if (result.organizations?.institutions?.length) {
        setInsts(result.organizations.institutions);
      } else {
        reload();
      }
      setMsg(result.message || "已补缺，组织图已更新。");
      await refresh();
    } catch (e: any) {
      setMsg(String(e?.message || e || "补缺失败"));
    } finally {
      setBusyKey("");
    }
  };
  if (insts === null) return <p className="m-empty m-card">官制载入中…</p>;
  if (insts.length === 0) return null;
  const totalVac = insts.reduce((a, i) => a + Number(i.vacancy_count || 0), 0);
  return (
    <Section title="官制·衙门" count={insts.length} tag={totalVac > 0 ? `空缺 ${totalVac}` : ""} defaultOpen={false}>
      <p className="m-hint" style={{ margin: "0 2px 8px" }}>衙门管事、席位是其下属；空缺会拖慢旨意与财政执行。点具体空位即可补官。</p>
      {msg && <p className="m-org-msg">{msg}</p>}
      {insts.map((inst, i) => <InstitutionCard key={i} inst={inst} busyKey={busyKey} onFill={handleFill} />)}
    </Section>
  );
}
