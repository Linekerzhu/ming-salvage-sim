// 移动端数据层：薄封装现有 api/client + payloads。后端契约不变。
import { api, streamJsonSse } from "../api/client";
import { decodeMapNodes, decodeOrganizationPayload, normalizeGameState } from "../api/payloads";

// ── 类型（贴后端形状，按需扩展，宽松处用 any）───────────────────────────────
export type Tab = "home" | "desk" | "audience" | "edicts" | "realm";

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
  kind: "decision" | "agenda" | "rivalry" | "army" | "faction" | "hook" | string;
  title: string;
  detail: string;
  urgency: number;
  tone: "danger" | "warn" | "info" | string;
  cta: string;
  tab: Tab;
  actor?: string;
  target?: string;
  meta?: string;
  ref_kind?: string;
  ref_id?: string;
};
export type AudienceLead = {
  kind: string;
  title: string;
  detail: string;
  tone?: string;
  actor?: string;
  target?: string;
  meta?: string;
  ref_kind?: string;
  ref_id?: string;
  prompts?: Suggestion[];
};
export type ChatContext = {
  kind?: string;
  actor?: string;
  target?: string;
  ref_kind?: string;
  ref_id?: string;
  title?: string;
  meta?: string;
};
export const loadPlaystyleBrief = (limit = 5): Promise<{ cards: PlaystyleBriefCard[]; limit: number }> =>
  api<{ cards: PlaystyleBriefCard[]; limit: number }>(`/api/playstyle/brief?limit=${encodeURIComponent(String(limit))}`);

// 活的宫廷：某官员的私心 + 党羽 + 政敌（双向好感网络）。
export type CourtTie = { name: string; opinion: number; basis: string };
export type CourtTrait = { key: string; valence: number; desc: string };
export type CourtCastration = { bao_status: string; bao_label: string; forced: boolean; servility: number };
export type CourtSecret = { kind: string; label: string; detail: string; severity: number; used: boolean };
export type CourtPayload = {
  traits: CourtTrait[];
  agenda: { kind: string; title: string; target: string; intensity: number; status: string } | null;
  allies: CourtTie[];
  rivals: CourtTie[];
  duishi?: string;
  castration?: CourtCastration | null;
  secret?: CourtSecret | null;
};
export const loadCourt = (name: string): Promise<CourtPayload> =>
  api<CourtPayload>(`/api/court/${encodeURIComponent(name)}`);

// 宫斗阴谋：令东厂侦缉 / 凭把柄挟制。
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

// 抉择事件（CK3 化 P2）：朝局张力弹出的"请陛下裁断"。
export type DecisionChoice = { key: string; label: string; hint: string };
export type Decision = { id: string; title: string; narrative: string; choices: DecisionChoice[] };
export const loadDecision = (): Promise<{ decision: Decision | null }> =>
  api<{ decision: Decision | null }>("/api/decision");
export const resolveDecision = (choice: string) =>
  api<{ title: string; choice: string; effect: string }>("/api/decision/resolve", {
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
export type ChatMessage = { role: string; content: string };
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
