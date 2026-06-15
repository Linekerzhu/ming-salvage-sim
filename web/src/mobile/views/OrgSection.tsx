import { useEffect, useState } from "react";
import { loadOrganizations } from "../api";
import type { OrgInstitution } from "../api";
import { Section } from "./Section";

// 官制·衙门：衙门(管事) → 席位(下属) → 在任/空缺。
// 「信任官僚 vs 微操」的舞台——空缺多则体系运转不灵，皇帝须取舍是否亲自补位。
function InstitutionCard({ inst }: { inst: OrgInstitution }) {
  const [open, setOpen] = useState(false);
  const held = Number(inst.holder_count || 0);
  const vac = Number(inst.vacancy_count || 0);
  const readiness = Number(inst.readiness || 0);
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
        </div>
      ))}
    </div>
  );
}

export function OrgSection() {
  const [insts, setInsts] = useState<OrgInstitution[] | null>(null);
  useEffect(() => {
    loadOrganizations().then((r) => setInsts(r.institutions)).catch(() => setInsts([]));
  }, []);
  if (insts === null) return <p className="m-empty m-card">官制载入中…</p>;
  if (insts.length === 0) return null;
  const totalVac = insts.reduce((a, i) => a + Number(i.vacancy_count || 0), 0);
  return (
    <Section title="官制·衙门" count={insts.length} tag={totalVac > 0 ? `空缺 ${totalVac}` : ""} defaultOpen={false}>
      <p className="m-hint" style={{ margin: "0 2px 8px" }}>衙门管事、席位是其下属；空缺越多体系越不灵，是托付还是亲补，在陛下。</p>
      {insts.map((inst, i) => <InstitutionCard key={i} inst={inst} />)}
    </Section>
  );
}
