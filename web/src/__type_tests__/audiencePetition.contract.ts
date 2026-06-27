// Type-level contract test for AudiencePetitionList grant/reject round-trip.
//
// AudiencePetitionList lives inside AudienceView.tsx and reads Petition[] from
// loadPetitions("available", npc), then calls grantPetition/rejectPetition on
// each card. This file exercises the same public API surface to verify:
//
// 1. loadPetitions(status, npc) returns the expected shape
// 2. grantPetition(id, draft, actor) returns { ok, id, assignment_kind, entry_label }
// 3. rejectPetition(id, reason) returns { ok, petition_id, status }
// 4. Petition.id / .title / .proposer_name / .draft_directive fields exist
//
// This file is type-only (never executed); if the AudienceView grant/reject
// integration drifts away from these contracts, `tsc --noEmit` will fail.
//
// Run via: `cd web && ./node_modules/.bin/tsc --noEmit`

import {
  grantPetition,
  loadPetitions,
  rejectPetition,
  type Petition,
} from "../mobile/api";

// 1. loadPetitions 至少要返回 status 字符串 + items 数组。
const _checkLoadPetitions = async (npc: string) => {
  const res = await loadPetitions("available", npc);
  const items: Petition[] = res.items;
  // 显式访问每条 petition 必备字段；字段缺失则编译失败。
  items.forEach((p) => {
    const _id: number = p.id;
    const _key: string = p.petition_key;
    const _title: string = p.title;
    const _status: string = p.status;
    const _proposer: string = p.proposer_name;
    const _draft: string = p.draft_directive;
    void _id; void _key; void _title; void _status; void _proposer; void _draft;
  });
  // npc 为空字符串时仍合法（不按 NPC 过滤）。
  const _all = await loadPetitions();
  void _all;
};

// 2. grantPetition 必须返回 assignment_kind + entry_label（AudiencePetitionList 准按钮依赖）。
const _checkGrantPetition = async (id: number) => {
  const r = await grantPetition(id, "", "");
  const _ok: boolean = r.ok;
  const _id: number = r.id;
  const _kind: string = r.assignment_kind;
  const _label: string = r.entry_label;
  void _ok; void _id; void _kind; void _label;
};

// 3. rejectPetition 必须返回 status="rejected"。
const _checkRejectPetition = async (id: number) => {
  const r = await rejectPetition(id, "");
  const _ok: boolean = r.ok;
  const _pid: number = r.petition_id;
  const _status: string = r.status;  // 应为 "rejected"
  void _ok; void _pid; void _status;
};

// 让上述函数被引用，防止未使用告警（type-only 文件，无运行时）。
export const __typeCheckContract: unknown = [_checkLoadPetitions, _checkGrantPetition, _checkRejectPetition];