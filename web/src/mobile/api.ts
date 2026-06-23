// 移动端数据层：薄封装现有 api/client + payloads。后端契约不变。
import { api, streamJsonSse } from "../api/client";
import { decodeMapNodes, decodeOrganizationPayload, normalizeGameState } from "../api/payloads";

// ── 类型（贴后端形状，按需扩展，宽松处用 any）───────────────────────────────
export type Tab = "home" | "desk" | "audience" | "edicts" | "policy";

export type TimeStatus = {
  current_day: number;
  day_in_month: number;
  days_in_month: number;
  year: number;
  month: number;
  turn: number;
  at_month_end: boolean;
  await_decree: boolean;
};

export type Memorial = {
  id: number;
  author: string;
  org: string;
  kind: string; // 请旨|请款|弹章|告变|荐人|陈情|复命...
  urgency: number; // 1常 2要 3急
  summary: string;
  full_text: string;
  piaoni: string;
  piaoni_author: string;
  arrived_day: number;
  shelved_days: number;
  status: string;
  ref_kind: string;
  ref_id: string;
  days_to_expire: number;
  policy_doctrine?: {
    id?: string;
    name?: string;
    axis?: string;
    issue_id?: number;
    bar_value?: number;
    state_label?: string;
    reform_ready?: boolean;
    establishment_blocked?: boolean;
    reform_hint?: string;
    active_conflicts?: Array<{ id?: string; name?: string; axis?: string }>;
    direction?: "support" | "oppose" | string;
    direction_label?: string;
    author_stance?: { stance?: string; score?: number; reasons?: string[] };
  };
  action_effects?: Record<string, Array<{ kind?: string; label: string; tone?: "good" | "bad" | "neutral" | string }>>;
};

export type DeskPayload = {
  pending: Memorial[];
  recent_decided: Memorial[];
  backlog: number;
  attention_left: number;
  attention_per_day: number;
  shi: number;
  renshi_willingness: number;
  eunuch_power: number;
  daipihong: boolean;
  daipihong_keeper: string | null;
  daipihong_keeper_upright: boolean;
  trap_hint: string;
};

export type DirectiveLifecycle = {
  id: number;
  text: string;
  status: string; // in_transit|executing|stalled|done|aborted
  category: string;
  progress: number;
  assignee: string;
  start_day: number;
  eta_day: number;
  resistance: number;
  blocker_clue?: { kind?: string; name?: string; label?: string; detail?: string; source_minister?: string; day?: number };
  blocker_action?: {
    action?: string;
    label?: string;
    day?: number;
    progress_delta?: number;
    resistance_delta?: number;
  };
  followup_action?: {
    kind?: string;
    minister?: string;
    day?: number;
  };
  followup_history?: Array<{
    kind?: string;
    minister?: string;
    day?: number;
  }>;
  policy_doctrine?: {
    summary?: string;
    risk_tags?: string[];
    exception_mode?: string;
    exception_label?: string;
    temporary_exception?: boolean;
    establishment_blocked?: boolean;
    establishment_blockers?: Array<{ id?: string; name?: string; axis?: string }>;
    primary?: { id?: string; name?: string; axis?: string; status?: string };
    conflicts?: Array<{ id?: string; name?: string; axis?: string }>;
    execution_gate?: {
      level?: string;
      establishment_blocked?: boolean;
      exception_mode?: string;
      temporary_exception?: boolean;
      resistance_delta?: number;
      check_risk_delta?: Record<string, number>;
      notes?: string[];
    };
  };
  statecraft_preflight?: {
    domains?: string[];
    score?: number;
    status?: string;
    tone?: string;
    summary?: string;
    capacity_rows?: Array<{
      domain?: string;
      label?: string;
      score?: number;
      status?: string;
      tone?: string;
      effect?: string;
      institutions?: any[];
    }>;
    bottlenecks?: Array<{
      kind?: string;
      title?: string;
      detail?: string;
      tone?: string;
    }>;
  };
  reported_rate: number;
  anomaly: string;
  settle_note: string;
  outcome_status?: string;
  outcome_summary?: Array<{ kind: string; label: string; tone: "good" | "bad" | "neutral" | string }>;
  intervention_options?: Array<{
    action: string;
    label: string;
    tone?: string;
    disabled?: boolean;
    disabled_reason?: string;
    effects?: Array<{ kind?: string; label: string; tone?: "good" | "bad" | "neutral" | string }>;
  }>;
};

export type TickEvent = {
  level: "red" | "yellow" | "blue";
  kind: string;
  title: string;
  detail: string;
  ref_kind: string;
  ref_id: string;
  day: number;
};

export type AdvanceResult = {
  advanced: number;
  stopped_by: string;
  reports: Array<{ day: number; events: TickEvent[] }>;
  status: TimeStatus;
  outcomes_applied?: Array<{ directive_id: number; applied: Record<string, unknown> }>;
};

// GameState 顶层字段（turn 是对象）。其余列表由 normalizeGameState 解码。
export type GameState = Record<string, any> & {
  turn?: { year: number; period: number; turn: number; phase: string };
  metrics?: Record<string, number>;
  regions?: any[];
  armies?: any[];
  issues?: any[];
  directives?: any[];
  treasury?: any;
  budget?: any;
  ministers?: any[];
  consorts?: any[];
  victory_status?: any;
  ending?: any;
  pending_count?: number;
};

// ── 菜单 / 开局 ────────────────────────────────────────────────────────────
export type MenuStatus = {
  has_api_key: boolean;
  has_running_game: boolean;
  has_main_db: boolean;
  saves?: Array<{ name?: string; label?: string; year?: number; period?: number; turn?: number }>;
  current_campaign?: string;
};
export type AuthStatus = {
  auth_enabled: boolean;
  authenticated: boolean;
  username: string;
  is_admin: boolean;
};
export const authStatus = () => api<AuthStatus>("/api/auth/me");
export const login = (username: string, password: string) =>
  api<AuthStatus>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
export const register = (username: string, password: string, invite_code: string) =>
  api<AuthStatus>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password, invite_code }),
  });
export const menuStatus = () => api<MenuStatus>("/api/menu/status");
export const newGame = () => api<{ state: any }>("/api/menu/new_game", { method: "POST", body: "{}" });
export const continueGame = () => api<{ state: any }>("/api/menu/continue", { method: "POST", body: "{}" });
export const loadSave = (name: string) =>
  api<{ state: any }>(`/api/menu/load_save/${encodeURIComponent(name)}`, { method: "POST", body: "{}" });
export const exitToMenu = () => api("/api/menu/exit_to_menu", { method: "POST", body: "{}" });

// ── 加载 ──────────────────────────────────────────────────────────────────
export const loadGameState = async (): Promise<GameState> => {
  const raw = await api<Record<string, any>>("/api/game/state");
  // 后端把 state 包在 web_payload_response 里；兼容直返与 {state}/{data} 包装。
  const wire = (raw.state || raw.data || raw) as Record<string, any>;
  return normalizeGameState<GameState>(wire);
};

export const loadTime = async (): Promise<TimeStatus> => {
  const r = await api<{ time: TimeStatus }>("/api/time");
  return r.time;
};

export const loadDesk = (): Promise<DeskPayload> => api<DeskPayload>("/api/desk");

export type FiscalCenterPayload = Record<string, any> & {
  unit?: string;
  revenue_sources?: any[];
  expense_sources?: any[];
  revenue_family_rows?: any[];
  expense_family_rows?: any[];
  province_tax_rows?: any[];
  army_pay_rows?: any[];
  net_by_account?: Record<string, any>;
  account_cards?: any[];
  ledger_movements?: any[];
  ledger_summary?: Record<string, any>;
  totals?: Record<string, number>;
  explainers?: any[];
  money_questions?: any[];
  player_model?: Record<string, string>;
};

export type PolicyCenterPayload = Record<string, any> & {
  route_summary?: { orthodox?: number; contested?: number; latent?: number };
  routes?: any[];
  orthodox?: any[];
  contested?: any[];
  latent?: any[];
  strategic_snapshot?: Record<string, any>;
  workstreams?: Record<string, any[]>;
  inner_court_tools?: Record<string, any>;
};

export type StatecraftCenterPayload = Record<string, any> & {
  model?: Record<string, string>;
  topbar?: any[];
  economy_lanes?: any[];
  capacity_rows?: any[];
  bureaucracy_lanes?: any[];
  directive_queue_rows?: any[];
  building_capacity_rows?: any[];
  bureaucracy_rows?: any[];
  bottlenecks?: any[];
  source_links?: Record<string, string>;
};

const unwrapPanel = <T,>(raw: any): T => (raw?.data || raw?.payload || raw) as T;
export const loadFiscalCenter = async (): Promise<FiscalCenterPayload> =>
  unwrapPanel<FiscalCenterPayload>(await api<any>("/api/fiscal_center"));
export const loadPolicyCenter = async (): Promise<PolicyCenterPayload> =>
  unwrapPanel<PolicyCenterPayload>(await api<any>("/api/policy_center"));
export const loadStatecraftCenter = async (): Promise<StatecraftCenterPayload> =>
  unwrapPanel<StatecraftCenterPayload>(await api<any>("/api/statecraft_center"));

// 司礼监代批红（宦官恶趣味 E1）：开＝御案积压由内廷廓清（省精力），代价＝权阉之势涨、阉党自固。
export type DaipihongStatus = { on: boolean; eunuch_power: number; keeper: string | null; keeper_upright: boolean };
export const loadDaipihong = (): Promise<DaipihongStatus> =>
  api<DaipihongStatus>("/api/eunuch/daipihong");
export const setDaipihong = (
  on: boolean,
  keeper?: string,
): Promise<{ on: boolean; message: string; eunuch_power: number; keeper: string | null; keeper_upright: boolean }> =>
  api("/api/eunuch/daipihong", { method: "POST", body: JSON.stringify(keeper ? { on, keeper } : { on }) });

// 中兴气象（趋势仪表，非胜利条件）+ 当前阶段诏题（朔日刷新）。
export type ZhongxingPayload = {
  current: { total: number; parts: Record<string, number> };
  history: Array<{ turn: number; year: number; period: number; total: number; parts: Record<string, number> }>;
  stage: { id: string; title: string; brief: string } | null;
  goals: Array<{ id: string; title: string; hint: string; done: boolean; done_turn: number | null }>;
};
export const loadZhongxing = (): Promise<ZhongxingPayload> => api<ZhongxingPayload>("/api/zhongxing");

// 朝局风向：零 LLM 的玩法雷达，把暗线局势转成首页可点击钩子。
export type PlaystyleBriefCard = {
  kind:
    | "decision"
    | "agenda"
    | "rivalry"
    | "army"
    | "faction"
    | "hook"
    | "directive_blocker"
    | "directive_followup"
    | "monthly_followup"
    | "bargain"
    | "patronage"
    | "trap"
    | "trap_remedy"
    | "petition"
    | "legacy"
    | string;
  title: string;
  detail: string;
  urgency: number;
  tone: "danger" | "warn" | "info" | string;
  cta: string;
  tab: Tab | "realm" | string;
  actor?: string;
  target?: string;
  meta?: string;
  motive?: string;
  gain?: string;
  cost?: string;
  ask?: string;
  exchange?: string;
  refusal?: string;
  ref_kind?: string;
  ref_id?: string;
  effects?: Array<{ kind?: string; label: string; tone?: "good" | "bad" | "neutral" | string }>;
  stakes?: Array<{ kind?: string; label: string; tone?: "good" | "bad" | "neutral" | string }>;
};
export type PlaystyleBriefPayload = {
  cards: PlaystyleBriefCard[];
  lead?: PlaystyleBriefCard | null;
  limit: number;
  filter?: string;
  shown?: number;
  total?: number;
  hidden?: number;
  buckets?: Array<{
    kind: string;
    label: string;
    shown: number;
    total: number;
    hidden: number;
    top_urgency?: number;
    rank_level?: "danger" | "warn" | "info" | string;
    rank_label?: string;
    rank_count?: number;
  }>;
  ranks?: Array<{ level: "danger" | "warn" | "info" | string; label: string; count: number }>;
};
export type AudienceLead = {
  kind: string;
  title: string;
  detail: string;
  tone?: string;
  actor?: string;
  target?: string;
  meta?: string;
  motive?: string;
  gain?: string;
  cost?: string;
  ask?: string;
  exchange?: string;
  refusal?: string;
  ref_kind?: string;
  ref_id?: string;
  opening?: string;
  prompts?: Suggestion[];
  stakes?: Array<{ kind?: string; label: string; tone?: "good" | "bad" | "neutral" | string }>;
};
export type ChatContext = {
  kind?: string;
  actor?: string;
  target?: string;
  ref_kind?: string;
  ref_id?: string;
  title?: string;
  meta?: string;
  motive?: string;
  gain?: string;
  cost?: string;
  ask?: string;
  exchange?: string;
  refusal?: string;
};
export const loadPlaystyleBrief = (limit = 5, kind = ""): Promise<PlaystyleBriefPayload> => {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (kind) qs.set("kind", kind);
  return api<PlaystyleBriefPayload>(`/api/playstyle/brief?${qs.toString()}`);
};
export type SummonHintTag = { label: string; tone?: "good" | "bad" | "warn" | "neutral" | string };
export type SummonHint = { tags?: SummonHintTag[]; pressure_score?: number; lead?: PlaystyleBriefCard };
export const loadSummonHints = (): Promise<{ hints: Record<string, SummonHint> }> =>
  api<{ hints: Record<string, SummonHint> }>("/api/audience/summon_hints");

// 活的宫廷：某官员的私心 + 党羽 + 政敌（双向好感网络）。
export type CourtTie = { name: string; opinion: number; basis: string; strength_label?: string; play_hint?: string };
export type CourtTrait = { key: string; valence: number; desc: string };
export type CourtCastration = {
  bao_status: string;
  bao_label: string;
  forced: boolean;
  servility: number;
  castration_day?: number;
  reincarnation?: boolean;
  note?: string;
  method_label?: string;
  knife_label?: string;
  anesthesia_label?: string;
  procedure_label?: string;
  bao_size_label?: string;
  bao_shape_label?: string;
  bao_texture_label?: string;
  bao_weight_label?: string;
  preservation_label?: string;
  container_label?: string;
  ritual_label?: string;
  aftereffect_label?: string;
  urine_label?: string;
  voice_body_label?: string;
  trauma_label?: string;
  fixation_label?: string;
  psychosexual_label?: string;
  detail_line?: string;
  condition_line?: string;
  procedure_line?: string;
  voice_profile?: {
    register?: string;
    speech_rule?: string;
    pet_phrases?: string[];
    allowed_moves?: string[];
    forbidden_moves?: string[];
    slang?: string[];
    stage_cues?: string[];
  };
  scheme_profile?: {
    tier?: string;
    explicit?: boolean;
    risk_score?: number;
    brutality?: number;
    trauma_risk?: number;
    surgery_risk?: number;
    bao_security?: number;
    care_cost_delta?: number;
    effects?: string[];
  };
};
export type CourtSecret = { kind: string; label: string; detail: string; severity: number; used: boolean };
export type CourtFavorMemory = {
  turn: number;
  year: number;
  period: number;
  title: string;
  cause: string;
  process: string;
  outcome: string;
  sentiment: string;
  importance: number;
  tags?: string[];
};
export type ImpactEffect = { kind?: string; label: string; tone?: "good" | "bad" | "neutral" | string };
export type CourtPayload = {
  traits: CourtTrait[];
  agenda: {
    kind: string;
    title: string;
    target: string;
    intensity: number;
    status: string;
    bargain?: { ask?: string; exchange?: string; cost?: string; refusal?: string; risk_label?: string; cost_label?: string };
  } | null;
  favor_memories?: CourtFavorMemory[];
  allies: CourtTie[];
  rivals: CourtTie[];
  duishi?: string;
  castration?: CourtCastration | null;
  secret?: CourtSecret | null;
  back_previews?: Partial<Record<CourtBackKind, ImpactEffect[]>>;
  intrigue_previews?: Partial<Record<IntriguePreviewKind, ImpactEffect[]>>;
};
export const loadCourt = (name: string): Promise<CourtPayload> =>
  api<CourtPayload>(`/api/court/${encodeURIComponent(name)}`);

// 宫斗阴谋：令东厂侦缉 / 凭把柄挟制。
export type IntriguePreviewKind =
  | "investigate"
  | "fabricate"
  | "discord"
  | "coerce_submit"
  | "coerce_serve"
  | "coerce_retire";
export type InvestigateResult = { ok: boolean; found: boolean; chief?: string; message: string;
  secret?: { kind: string; detail: string; severity: number }; already?: boolean };
export const intrigueInvestigate = (name: string): Promise<InvestigateResult> =>
  api("/api/intrigue/investigate", { method: "POST", body: JSON.stringify({ name }) });
export const intrigueCoerce = (name: string, mode: string): Promise<{ ok: boolean; mode?: string; message: string }> =>
  api("/api/intrigue/coerce", { method: "POST", body: JSON.stringify({ name, mode }) });
export const intrigueFabricate = (name: string): Promise<{ ok: boolean; success?: boolean; imprisoned?: boolean; message: string }> =>
  api("/api/intrigue/fabricate", { method: "POST", body: JSON.stringify({ name }) });
export const intrigueDiscord = (a: string, b: string): Promise<{ ok: boolean; success?: boolean; message: string }> =>
  api("/api/intrigue/discord", { method: "POST", body: JSON.stringify({ a, b }) });
// 监军太监：遣（省略 eunuch 即默认东厂提督）/ 撤。
export const frontierSupervisor = (army_id: string, opts?: { eunuch?: string; recall?: boolean }): Promise<{ ok: boolean; message: string }> =>
  api("/api/frontier/supervisor", { method: "POST", body: JSON.stringify({ army_id, ...(opts || {}) }) });

// 内帑助饷：发内库（私帑）补太仓、清边军欠饷。崇祯朝的道德抉择。
export type PrivyReliefStatus = { nei_ku: number; guo_ku: number; arrears_total: number; suggested: number };
export const privyReliefStatus = (): Promise<PrivyReliefStatus> => api("/api/treasury/privy_relief");
export const privyRelief = (amount?: number): Promise<{ ok: boolean; moved?: number; arrears_cleared?: number; message: string; nei_ku?: number; guo_ku?: number; effects?: ImpactEffect[] }> =>
  api("/api/treasury/privy_relief", { method: "POST", body: JSON.stringify(amount != null ? { amount } : {}) });

// 选秀→册封：待册封秀女（candidate）一键降诏立为妃嫔（active）。
export type ConsortCandidate = { name: string; office: string; summary: string; style: string };
export const consortCandidates = (): Promise<{ candidates: ConsortCandidate[] }> => api("/api/consorts/candidates");
export const selectConsort = (name: string): Promise<{ selected?: PublicCharacter }> =>
  api(`/api/consorts/${encodeURIComponent(name)}/select`, { method: "POST" });

// 抉择事件（CK3 化 P2）：朝局张力弹出的"请陛下裁断"。
export type DecisionChoice = { key: string; label: string; hint: string; effects?: ImpactEffect[] };
export type DecisionTestimony = {
  minister: string;
  role?: string;
  target?: string;
  ask?: string;
  summary?: string;
  stance?: string;
  turn?: number;
  day?: number;
};
export type Decision = {
  id: string;
  title: string;
  narrative: string;
  choices: DecisionChoice[];
  testimonies?: DecisionTestimony[];
};
export const loadDecision = (): Promise<{ decision: Decision | null }> =>
  api<{ decision: Decision | null }>("/api/decision");
export const resolveDecision = (choice: string) =>
  api<{ title: string; choice: string; effect: string; effects?: ImpactEffect[] }>("/api/decision/resolve", {
    method: "POST", body: JSON.stringify({ choice }),
  });

export const loadLifecycle = async (): Promise<DirectiveLifecycle[]> => {
  const r = await api<{ directives: DirectiveLifecycle[] }>("/api/directives/lifecycle");
  return r.directives || [];
};

// ── 动作 ──────────────────────────────────────────────────────────────────
export const advanceTime = (days: number, stopOnYellow = true): Promise<AdvanceResult> =>
  api<AdvanceResult>("/api/time/advance", {
    method: "POST",
    body: JSON.stringify({ days, stop_on_yellow: stopOnYellow }),
  });

export type MemorialAction = "approve" | "deny" | "shelve" | "refer" | "ack";
export const decideMemorial = (id: number, action: MemorialAction, note = ""): Promise<{ message: string; desk?: DeskPayload }> =>
  api(`/api/desk/${id}/decide`, { method: "POST", body: JSON.stringify({ action, note }) });

export type CourtBackKind = "shoulder" | "comfort" | "reuse";
export const courtBack = (name: string, kind: CourtBackKind, cost = 0): Promise<{
  message: string;
  effects?: ImpactEffect[];
}> =>
  api("/api/court/back", { method: "POST", body: JSON.stringify({ name, kind, cost }) });

// 旨意草案 CRUD + 核定
export const createDirective = (text: string) =>
  api<{ directives: any[] }>("/api/directives", { method: "POST", body: JSON.stringify({ text }) });
export const patchDirective = (id: number, text: string) =>
  api<{ directives: any[] }>(`/api/directives/${id}`, { method: "PATCH", body: JSON.stringify({ text }) });
export const deleteDirective = (id: number) =>
  api<{ directives: any[] }>(`/api/directives/${id}`, { method: "DELETE" });
export const confirmDirective = (id: number) =>
  api<{ directives: any[]; pending_count: number }>(`/api/directives/${id}/confirm`, { method: "POST" });
export const rejectDirective = (id: number) =>
  api<{ directives: any[]; pending_count: number }>(`/api/directives/${id}/reject`, { method: "POST" });
export type InterventionEffect = { kind?: string; label: string; tone?: "good" | "bad" | "neutral" | string };
export const interveneDirective = (id: number, action: string, extra: Record<string, unknown> = {}) =>
  api<{ message: string; effects?: InterventionEffect[]; directives: DirectiveLifecycle[] }>(`/api/directives/${id}/intervene`, {
    method: "POST",
    body: JSON.stringify({ action, ...extra }),
  });

// 拟诏 / 颁诏
export const writeDecree = () => api<{ decree: string }>("/api/decree/write", { method: "POST" });
export const patchDecree = (decree: string) =>
  api<{ decree: string }>("/api/decree", { method: "PATCH", body: JSON.stringify({ decree }) });
export const issueDecreeStream = (onDelta: (d: string) => void) =>
  streamJsonSse<{ decree: string; report: string; state?: any }>(
    "/api/decree/issue/stream", {}, onDelta);

export { streamChat } from "../api/client";

// ── 召对 / 随侍太监 ────────────────────────────────────────────────────────
export type ChatMention = {
  kind?: "character" | string;
  name: string;
  terms?: string[];
  has_profile?: boolean;
  office?: string;
};
export type ChatMessage = { role: string; content: string; day?: number; mentions?: ChatMention[]; stage_directions?: string[] };
export type Suggestion = { label: string; text: string; prefix?: boolean };
export type PublicCharacter = Record<string, any> & { name: string; office?: string; portrait_id?: string };

export type ChatResponse = Record<string, any> & {
  history?: ChatMessage[];
  suggestions?: Suggestion[];
  answer?: string;
  next_minister?: string;
  court_action?: string;
  proposed_directive?: { id: number; text: string };
  directive_effect?: { title?: string; message?: string; kind?: string; progress_delta?: number; resistance_delta?: number };
  dialogue_effect?: { title?: string; message?: string; effects?: ImpactEffect[]; stage_direction?: string };
  dialogue_goal?: Record<string, any>;
  recruited_minister?: string;
  appointed_minister?: string;
  registered_minister?: string;
  minister_profile?: PublicCharacter;
};

export const loadEunuch = () =>
  api<{ eunuch: PublicCharacter | null; brief?: string }>("/api/eunuch");
export const loadEunuchCandidates = () =>
  api<{ candidates: Array<{ name: string; office: string; is_eunuch: boolean }> }>("/api/eunuch/candidates");
export const replaceEunuch = (name: string) =>
  api<{ message: string; eunuch: PublicCharacter }>("/api/eunuch/replace", {
    method: "POST",
    body: JSON.stringify({ name }),
  });

// 官制·组织：衙门 / 席位 / 在任 / 空缺——可视化「官员管事·管下属·空缺」。
export type OrgSlot = Record<string, any> & {
  title?: string; holders?: any[]; vacancies?: number; filled_count?: number; count?: number; match_hint?: string;
};
export type OrgInstitution = Record<string, any> & {
  name?: string; slots?: OrgSlot[]; holder_count?: number; vacancy_count?: number;
  readiness?: number; coverage?: number; execution_summary?: string;
};
export const loadOrganizations = async (): Promise<{ institutions: OrgInstitution[]; unassigned: any[] }> => {
  const raw = await api<Record<string, any>>("/api/organizations");
  const decoded = decodeOrganizationPayload<Record<string, any>>(raw.data || raw.state || raw) || {};
  return { institutions: (decoded.institutions as OrgInstitution[]) || [], unassigned: (decoded.unassigned as any[]) || [] };
};
export const fillOrganizationVacancy = async (
  institution_id: string,
  slot_title: string,
  method = "auto",
): Promise<{ message?: string; organizations?: { institutions?: OrgInstitution[]; unassigned?: any[] }; state?: GameState }> => {
  const raw = await api<Record<string, any>>("/api/organizations/fill_vacancy", {
    method: "POST",
    body: JSON.stringify({ institution_id, slot_title, method }),
  });
  const wireOrgs = raw.organizations || raw.data?.organizations || raw.state?.organizations || {};
  const organizations = decodeOrganizationPayload<Record<string, any>>(wireOrgs) || {};
  return {
    ...raw,
    message: String(raw.message || raw.data?.message || ""),
    organizations: {
      institutions: (organizations.institutions as OrgInstitution[]) || [],
      unassigned: (organizations.unassigned as any[]) || [],
    },
  };
};

// 建筑（城防/仓廪/工坊）：来自 /api/map 的节点嵌套，展平为列表。condition 严重度上色。
export type Building = Record<string, any> & {
  id?: string; name?: string; category?: string; condition?: number;
  output_metric?: string; output_amount?: number; region_id?: string; risk?: number;
};
export const loadBuildings = async (): Promise<Building[]> => {
  const d = await api<Record<string, any>>("/api/map");
  const nodes = decodeMapNodes<Record<string, any>>(
    d.nodes, d.node_fields, d.region_fields, d.army_fields, d.building_fields);
  const out: Building[] = [];
  for (const n of nodes) for (const b of (n.buildings || [])) out.push(b as Building);
  return out;
};

export const loadCharacter = (name: string) =>
  api<{ character: PublicCharacter & Record<string, any> }>(`/api/characters/${encodeURIComponent(name)}`);

export const loadChat = (name: string) =>
  api<{ minister?: PublicCharacter; history: ChatMessage[]; suggestions: Suggestion[]; can_undo_last_chat?: boolean }>(
    `/api/ministers/${encodeURIComponent(name)}/chat`,
  );
