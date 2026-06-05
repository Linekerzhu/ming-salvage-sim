import React from "react";
import { createRoot } from "react-dom/client";
import {
  Check,
  Crown,
  Edit3,
  Landmark,
  Loader2,
  Lock,
  LogOut,
  ChevronLeft,
  ChevronRight,
  MapPinned,
  Menu,
  MessageSquare,
  Power,
  RotateCcw,
  Save,
  Send,
  Settings,
  ScrollText,
  Shield,
  Star,
  Paintbrush,
  Target,
  Trash2,
  Swords,
  Upload,
  X,
  Pencil,
  Eraser,
  Move,
  ZoomIn,
  ZoomOut,
  Scroll,
} from "lucide-react";
import { EXTERNAL_PATH_GROUPS, MAP_VIEW_BOX, REGION_PATH_GROUPS } from "./mapPaths";
import "./styles.css";

type Metrics = Record<string, number>;

type Region = {
  id: string;
  name: string;
  kind: string;
  population: number;
  public_support: number;
  unrest: number;
  natural_disaster: string;
  human_disaster: string;
  registered_land: number;
  hidden_land: number;
  tax_per_turn: number;
  grain_security: number;
  gentry_resistance: number;
  military_pressure: number;
  status: string;
  controlled_by?: string;
};

type Army = {
  id: string;
  name: string;
  station: string;
  theater: string;
  commander: string;
  controller: string;
  troop_type: string;
  manpower: number;
  maintenance_per_turn: number;
  supply: number;
  morale: number;
  training: number;
  equipment: number;
  arrears: number;
  mobility: number;
  loyalty: number;
  status: string;
  owner_power?: string;
};

type Power = {
  id: string;
  name: string;
  kind: string;
  leader: string;
  stance: string;
  leverage: number;
  satisfaction: number;
  military_strength: number;
  cohesion: number;
  supply: number;
  agenda: string;
  status: string;
  last_action: string;
};

type Building = {
  id: string;
  region_id: string;
  name: string;
  category: string;
  level: number;
  condition: number;
  maintenance: number;
  risk: number;
  output_metric: string;
  output_amount: number;
  status: string;
  origin: string;
};

type OrgSlot = {
  title: string;
  office_type: string;
  count: number;
  holders: Minister[];
  filled_count?: number;
  vacancies: number;
  overflow_count?: number;
  open_pool?: boolean;
  match_hint?: string;
};

type Institution = {
  id: string;
  name: string;
  category: string;
  mandate: string;
  custom?: boolean;
  readiness?: number;
  coverage?: number;
  holder_quality?: number;
  execution_summary?: string;
  execution_risks?: string[];
  slots: OrgSlot[];
  vacancy_count: number;
  holder_count?: number;
};

type OrganizationPayload = {
  institutions: Institution[];
  vacancy_count: number;
  custom_count: number;
  assigned_count?: number;
  unassigned?: Minister[];
  court_readiness?: number;
  risk_count?: number;
  execution_summary?: string;
  overloaded_holders?: Array<{ name: string; slot_count: number }>;
};

type TiangangDimension = {
  id?: string;
  symbol: string;
  name: string;
  type: string;
  band?: {
    tone?: "left" | "center" | "right";
    left: number;
    width: number;
  };
  poles?: {
    left: string;
    right: string;
  };
};

type TiangangGroup = {
  name: string;
  dimensions: TiangangDimension[];
};

type TiangangProfile = {
  archetype: string;
  hidden: boolean;
  derived?: boolean;
  groups: TiangangGroup[];
};

type XinpanConcern = {
  dim_id: string;
  symbol: string;
  name: string;
  npc_value?: number;
  weight?: number;
  perceived_player_value?: number;
  reason?: string;
};

type XinpanAbility = {
  dim_id: string;
  symbol: string;
  name: string;
  band?: string;
};

type XinpanTrajectoryPoint = {
  turn: number;
  dao_he: number;
  shi_he: number;
  fear?: number;
  hatred?: number;
  trust_coeff?: number;
  has_delta?: boolean;
  dao_delta?: number;
  shi_delta?: number;
  fear_delta?: number;
  hatred_delta?: number;
  trust_delta?: number;
  quadrant?: string;
  event?: string;
  source_kind?: string;
};

type XinpanProfile = {
  quadrant: "股肱" | "权附" | "道隐" | "离心" | string;
  dao_he: number;
  shi_he: number;
  fear: number;
  trust_coeff: number;
  hatred: number;
  patience_threshold?: number;
  dao_cutoff?: number;
  shi_cutoff?: number;
  core_concerns?: XinpanConcern[];
  top_abilities?: XinpanAbility[];
  behavior_hint?: string;
  warnings?: string[];
  trajectory?: XinpanTrajectoryPoint[];
  updated_turn?: number;
};

type NetworkRelation = {
  target: string;
  type: string;
  note?: string;
  confidence?: string;
  office?: string;
  office_type?: string;
  faction?: string;
  status?: string;
};

type NetworkRecommendation = {
  name: string;
  office?: string;
  office_type?: string;
  faction?: string;
  status?: string;
  confidence?: string;
  evidence?: string[];
};

type NetworkProfile = {
  biography?: string;
  ability_logic?: string;
  growth_arc?: Record<string, string>;
  relations?: NetworkRelation[];
  recommendations?: NetworkRecommendation[];
  derived?: boolean;
};

type StanceEvidenceDriver = {
  kind: string;
  text: string;
};

type StanceEvidence = {
  drivers?: StanceEvidenceDriver[];
  source?: string;
};

type StanceNote = {
  id: number;
  topic: string;
  stance: "support" | "oppose" | "caution" | "neutral";
  confidence: number;
  summary: string;
  conditions: string;
  related_issue_id: number;
  evidence?: StanceEvidence;
  risk_tags_list?: string[];
  execution_hint?: string;
  handshake_status?: "sealed" | "conditional" | "blocked" | "none";
  psychological_score?: number;
  psychological?: {
    threshold?: number;
    verbal_only?: boolean;
    tasks?: string[];
    blockers?: string[];
    action_kind?: string;
  };
  agreement_id?: number;
};

type AgreementTask = {
  id: number;
  description: string;
  task_kind?: string;
  status: "pending" | "done" | "failed";
  evidence?: string;
  last_checked_turn?: number;
};

type Agreement = {
  id: number;
  minister_name: string;
  topic: string;
  core_topic?: string;
  target_text?: string;
  action_kind: string;
  promise_type?: string;
  stakes?: string;
  status: "sealed" | "pending" | "blocked" | "fulfilled" | "failed";
  condition_status?: "pending" | "satisfied" | "failed";
  target_status?: "pending_conditions" | "achieved" | "failed" | "blocked";
  handshake_status: "sealed" | "conditional" | "blocked" | "none";
  handshake_label?: string;
  psychological_score: number;
  threshold: number;
  verbal_only?: boolean;
  due_turn?: number;
  fulfillment_score?: number;
  fulfillment_evidence?: string;
  target_evidence?: string;
  execution_consequence?: string;
  auto_review?: Record<string, unknown>;
  llm_review?: Record<string, unknown>;
  political_effect?: Record<string, unknown>;
  conditions?: string;
  summary?: string;
  tasks?: AgreementTask[];
};

type CausalNote = {
  kind: string;
  tone?: "good" | "warn" | "bad" | "neutral";
  title: string;
  summary?: string;
  drivers?: string[];
  risks?: string[];
  execution_hint?: string;
};

type MapNode = {
  id: string;
  kind: "region" | "theater" | "external";
  x: number;
  y: number;
  label?: string;
  risk: number;
  region?: Region;
  armies: Army[];
  buildings?: Building[];
  power?: Power;
};

type RegionPathRenderItem = {
  id: string;
  name: string;
  controlledBy: string;
  unrest: number;
  risk: number;
  labelX: number;
  labelY: number;
  paths: Array<{ id: string; d: string }>;
};

type ExternalPathRenderItem = {
  id: string;
  name: string;
  powerId: string;
  labelX: number;
  labelY: number;
  paths: Array<{ id: string; d: string }>;
};

type SvgLabelPosition = {
  svgX: number;
  svgY: number;
};

type Minister = {
  id?: string;
  name: string;
  office: string;  // 去职者已清空，可能为空串
  office_type: string;
  faction: string;
  status: string;  // active/dismissed/imprisoned/exiled/retired/dead/offstage
  status_reason?: string;
  status_label: string;  // 中文：在朝/已罢黜/下狱/流放/致仕…
  career_state?: string;
  summary: string;
  birth_year?: number;
  start_age?: number;
  age_label?: string;
  favorite: boolean;
  portrait_id?: string;  // 空/undefined=无专属，前端 fallback 到池
  portrait_available?: boolean;
  portrait_status?: "ready" | "pending" | "error" | "missing" | string;
  portrait_error?: string;
  portrait_dna_seed?: string;
  portrait_wardrobe_key?: string;
  power_id?: string;     // 大明=ming, 后金=houjin, 流寇=bandits 等
  network_profile?: NetworkProfile;
  xinpan_profile?: XinpanProfile;
  tiangang_profile?: TiangangProfile;
  stance_notes?: StanceNote[];
  skills: Array<{ id: string; name: string; sources: string[]; description: string }>;
};

type CharacterIndexEntry = {
  name: string;
  office: string;
  office_type: string;
  faction: string;
  status: string;
  status_reason?: string;
  status_label: string;
  power_id: string;
  power_name: string;
  summary: string;
  birth_year?: number;
  start_age?: number;
  age_label?: string;
  portrait_available?: boolean;
  portrait_status?: "ready" | "pending" | "error" | "missing" | string;
  portrait_error?: string;
  portrait_dna_seed?: string;
  portrait_wardrobe_key?: string;
  can_summon?: boolean;
  xinpan_quadrant?: string;
};

type EventItem = {
  id: string;
  title: string;
  kind: string;
  summary: string;
  urgency: number;
  severity: number;
  credibility: number;
  interests: string[];
  audiences: string[];
};

type Directive = {
  id: number;
  event_id: string;
  event_title: string;
  actor: string;
  skill_id: string;
  skill_name: string;
  text: string;
  source: string;
  status: string; // pending（待核定大臣拟旨）| draft（颁诏候选）
  notes: string;
  authority: string;
};

type Issue = {
  id: number;
  kind: "situation" | "initiative";
  title: string;
  bar_value: number;
  bar_good_meaning: string;
  bar_bad_meaning: string;
  phase: string;
  stage_text: string;
  severity: number;
  tags: string[];
  inertia: number;
  resolve_condition: string;
  fail_condition: string;
  ongoing_text: string;
  effect_on_resolve: Record<string, number>;
  effect_on_fail: Record<string, number>;
};

type LegacyEffect = {
  国库?: number;
  内库?: number;
  民心?: number;
  皇威?: number;
  regions?: Record<string, Record<string, number>>;
  armies?: Record<string, Record<string, number>>;
};

type Legacy = {
  id: number;
  name: string;
  narrative_hint: string;
  modifiers: LegacyEffect;
  effect_text: string;
  remaining_months: number;  // -1 = 永久
  clear_condition: string;
};

type ClosedIssue = {
  id: number;
  kind: "situation" | "initiative";
  title: string;
  status: "resolved" | "failed" | "dropped";
  bar_value: number;
  bar_good_meaning: string;
  bar_bad_meaning: string;
  closed_turn: number;
  stage_text: string;
  effect: any;
};

type BudgetItem = {
  name: string;
  amount: number;
  note: string;
};

type BudgetMovement = {
  delta: number;
  balance_after: number;
  category: string;
  reason: string;
};

type BudgetAccount = {
  balance: number;
  income: BudgetItem[];
  expense: BudgetItem[];
  income_total: number;
  expense_total: number;
  net: number;
  movements: BudgetMovement[];
  movements_total: number;
};

type Budget = Record<"国库" | "内库", BudgetAccount>;

type AdventureLog = {
  turn: number; year: number; period: number;
  adventure_id: string; title: string; choice: string;
  success: boolean; narrative: string;
  items_found: string[]; metrics_change: Record<string, number>;
};
type PlayerItem = {
  id: string; name: string; category: string;
  rarity: string; quantity: number; equipped: boolean;
};
type GameState = {
  turn: { year: number; period: number; turn: number };
  metrics: Metrics;
  previous_summary: string;
  treasury: string;
  issues: Issue[];
  legacies: Legacy[];
  closed_this_turn: ClosedIssue[];
  budget: Budget;
  region_warning: string;
  army_warning: string;
  power_warning: string;
  powers: Power[];
  victory_status: { status: string; summary: string };
  ending: EndingPayload | null;
  events: EventItem[];
  regions: Region[];
  armies: Army[];
  map_nodes: MapNode[];
  organizations: OrganizationPayload;
  character_index?: CharacterIndexEntry[];
  ministers: Minister[];
  consorts: Minister[];
  directives: Directive[];
  agreements?: Agreement[];
  pending_count: number;
  last_decree: string;
  last_report: string;
  adventures: AdventureLog[];
  items: PlayerItem[];
};

type EndingTimelineItem = {
  turn: number; year: number; period: number;
  decree_brief: string; effect_brief: string; chapter: string;
};
type EndingPayload = {
  status: string; label: string; summary: string; timeline: EndingTimelineItem[];
};
type ChatMessage = { role: "user" | "minister"; content: string };
type ChatDisplayMessage = ChatMessage & { pending?: boolean };
type Suggestion = { label: string; text: string; prefix?: boolean };
type ModalName = "none" | "state" | "chat" | "edict" | "report" | "extraction" | "history" | "menu" | "secret_orders" | "ending" | "long_goals" | "adventure";
type DrawerName = "" | "court" | "harem" | "army" | "region" | "building" | "economy" | "appointment" | "organization";
type SaveEntry = MenuSave & { current?: boolean };
type LLMConfigInfo = {
  base_url: string;
  model: string;
  max_tokens: number;
  timeout_seconds: number;
  thinking_level: string;
  advanced_model: string;
  advanced_base_url: string;
  has_advanced_api_key: boolean;
  advanced_thinking_level: string;
  has_api_key: boolean;
  persisted: {
    base_url: string;
    model: string;
    has_api_key: boolean;
    max_tokens: number;
    timeout_seconds: number;
    thinking_level: string;
    advanced_model: string;
    advanced_base_url: string;
    has_advanced_api_key: boolean;
    advanced_thinking_level: string;
  };
};
type SecretOrder = {
  id: number;
  turn_issued: number;
  due_turn: number;
  year_issued: number;
  period_issued: number;
  minister_name: string;
  title: string;
  content: string;
  tags: string[];
  importance: number;
  status: "active" | "pending_review" | "done" | "failed" | "cancelled";
  result: string;
  sim_note: string;
  turn_closed: number | null;
};

type ProposedDirective = {
  id: number;
  text: string;
  status: string;
  source?: string;
  actor?: string;
  notes: string;
};
type ChatResponse = {
  minister_profile?: Minister;
  answer: string;
  history: ChatMessage[];
  suggestions: Suggestion[];
  directives: Directive[];
  pending_count?: number;
  can_undo_last_chat?: boolean;
  court_action?: string;
  next_minister?: string;
  appointed_minister?: string;
  registered_minister?: string;
  displaced_minister?: string;
  displaced_effect?: {
    summary?: string;
    reaction_summary?: string;
    old_office?: string;
    old_office_type?: string;
    old_faction?: string;
    xinpan?: Record<string, number>;
  };
  proposed_directive?: ProposedDirective | null;
  secret_order_id?: number;
  secret_order_assignee?: string;
  secret_order_effect?: {
    summary?: string;
    risk_label?: string;
    risk_score?: number;
    xinpan?: Record<string, number>;
  };
};

type ChatEffectChip = {
  label: string;
  value: string;
  tone?: "good" | "bad" | "warn" | "neutral";
};

type ChatEffectNotice = {
  id: string;
  kind: "appointment" | "secret" | "registry";
  title: string;
  source: string;
  summary: string;
  detail?: string;
  chips?: ChatEffectChip[];
  tone?: "danger" | "warn" | "neutral";
};

type ChatUndoResponse = {
  minister_profile?: Minister;
  history: ChatMessage[];
  suggestions: Suggestion[];
  directives: Directive[];
  pending_count: number;
  secret_orders: SecretOrder[];
  can_undo_last_chat: boolean;
};

const signedEffect = (value: number, digits = 1) => {
  const rounded = Number(value.toFixed(digits));
  return `${rounded > 0 ? "+" : ""}${rounded}`;
};

const xinpanEffectChips = (xinpan?: Record<string, number>): ChatEffectChip[] => {
  if (!xinpan) return [];
  const chips: ChatEffectChip[] = [];
  const shi = Number(xinpan.shi_delta || 0);
  const fear = Number(xinpan.fear_delta || 0);
  const hatred = Number(xinpan.hatred_delta || 0);
  const trust = Number(xinpan.trust_multiplier || 1);
  if (Math.abs(shi) >= 0.05) {
    chips.push({ label: "势合", value: signedEffect(shi), tone: shi > 0 ? "good" : "bad" });
  }
  if (Math.abs(fear) >= 0.05) {
    chips.push({ label: "畏惧", value: signedEffect(fear), tone: fear > 0 ? "warn" : "good" });
  }
  if (Math.abs(hatred) >= 0.05) {
    chips.push({ label: "仇恨", value: signedEffect(hatred), tone: hatred > 0 ? "bad" : "good" });
  }
  if (Math.abs(trust - 1) >= 0.004) {
    chips.push({ label: "信言", value: `x${Number(trust.toFixed(3))}`, tone: trust >= 1 ? "good" : "bad" });
  }
  return chips;
};

const secretOrderUrgency = (order: SecretOrder, currentTurn: number) => {
  const total = Number(order.due_turn || 0) ? Number(order.due_turn || 0) - Number(order.turn_issued || 0) : 0;
  const remaining = Number(order.due_turn || 0) ? Number(order.due_turn || 0) - Number(currentTurn || order.turn_issued || 0) : 0;
  if (["done", "failed", "cancelled"].includes(order.status)) {
    const closedLabel = order.status === "done" ? "已完成" : order.status === "failed" ? "已失败" : "已撤销";
    return { total, remaining, text: closedLabel, tone: "neutral" };
  }
  const text = order.due_turn
    ? remaining <= 0
      ? "已到限"
      : remaining === 1
        ? "剩1月"
        : `剩${remaining}月`
    : "无硬限";
  const tone = order.due_turn && remaining <= 1 ? "danger" : order.due_turn && remaining <= 3 ? "warn" : "neutral";
  return { total, remaining, text, tone };
};

const secretOrderRisk = (order: SecretOrder, currentTurn: number) => {
  const context = `${order.title || ""} ${order.content || ""} ${(order.tags || []).join(" ")}`;
  let score = 0;
  const urgency = secretOrderUrgency(order, currentTurn);
  const closed = ["done", "failed", "cancelled"].includes(order.status);
  if (!closed) {
    if (order.due_turn && urgency.remaining <= 1) score += 2;
    else if (order.due_turn) score += 1;
  } else if (order.due_turn) {
    score += 1;
  }
  if (/刺杀|赐死|诛|抄家|下狱|廷杖/.test(context)) score += 3;
  if (/东厂|锦衣卫|厂卫|密查|暗查|线人|取证|盯梢/.test(context)) score += 1;
  if (/辽东|边镇|军饷|清丈|盐课|东林|阉党|后金|流寇/.test(context)) score += 1;
  const bounded = Math.max(0, Math.min(5, score));
  const label = ["常密", "限密", "险密", "危密", "危密", "死密"][bounded] || "常密";
  const tone = bounded >= 4 ? "danger" : bounded >= 2 ? "warn" : "neutral";
  return { score: bounded, label, tone };
};

type ApiErrorDetail = {
  code?: string;
  message?: string;
  provider_message?: string;
  status_code?: number | null;
};

class ApiRequestError extends Error {
  detail: ApiErrorDetail;

  constructor(detail: ApiErrorDetail, fallback: string) {
    const message = detail.message || fallback;
    super(detail.code ? `[${detail.code}] ${message}` : message);
    this.name = "ApiRequestError";
    this.detail = detail;
  }
}

const normalizeApiError = (error: any, fallback: string): ApiErrorDetail => {
  const detail = error?.detail ?? error;
  if (detail && typeof detail === "object") {
    return {
      code: detail.code,
      message: detail.message || detail.detail || fallback,
      provider_message: detail.provider_message,
      status_code: detail.status_code,
    };
  }
  return { message: String(detail || fallback) };
};

const formatApiError = (error: any, fallback: string) => {
  const detail = error instanceof ApiRequestError ? error.detail : normalizeApiError(error, fallback);
  return detail.code ? `[${detail.code}] ${detail.message || fallback}` : detail.message || fallback;
};

const api = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiRequestError(normalizeApiError(error, response.statusText), response.statusText);
  }
  return response.json();
};

const parseSseMessage = (raw: string): { event: string; data: string } | null => {
  const lines = raw.split(/\r?\n/);
  let event = "message";
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (!dataLines.length) return null;
  return { event, data: dataLines.join("\n") };
};

const streamChat = async (
  ministerName: string,
  message: string,
  onDelta: (delta: string) => void,
): Promise<ChatResponse> => {
  const response = await fetch(`/api/ministers/${encodeURIComponent(ministerName)}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiRequestError(normalizeApiError(error, response.statusText), response.statusText);
  }
  if (!response.body) {
    throw new Error("浏览器不支持流式回复。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const messages = buffer.split("\n\n");
    buffer = messages.pop() || "";

    for (const messageBlock of messages) {
      const parsed = parseSseMessage(messageBlock);
      if (!parsed) continue;
      const payload = JSON.parse(parsed.data);
      if (parsed.event === "delta") {
        onDelta(String(payload.content || ""));
      } else if (parsed.event === "done") {
        return payload as ChatResponse;
      } else if (parsed.event === "error") {
        throw new ApiRequestError(normalizeApiError(payload, "流式回复失败。"), "流式回复失败。");
      }
    }

    if (done) break;
  }

  throw new Error("流式回复中断，未收到完成事件。");
};

const scoreTone = (value: number, inverse = false) => {
  const danger = inverse ? value >= 65 : value <= 38;
  const warn = inverse ? value >= 45 : value <= 52;
  if (danger) return "danger";
  if (warn) return "warn";
  return "good";
};

const formatMoney = (value: number) => `${value}万两`;

const formatSignedMoney = (value: number) => `${value > 0 ? "+" : ""}${formatMoney(value)}`;

const monthlyAmount = (value: number) => Math.max(0, Math.round(value / 3));

const issueTone = (value: number) => {
  if (value <= 28) return "danger";
  if (value <= 58) return "warn";
  return "good";
};

const signedNumber = (value: number) => `${value > 0 ? "+" : ""}${value}`;

const numericEffectValue = (value: any): number | null => {
  if (typeof value === "number") return value;
  if (typeof value === "string" && /^-?\d+$/.test(value.trim())) return Number(value);
  return null;
};

const appendScopedEffect = (
  parts: string[],
  block: any,
  labelEntity: (id: any) => string,
) => {
  if (!block || typeof block !== "object" || Array.isArray(block)) return;
  for (const [entity, fields] of Object.entries(block)) {
    if (!fields || typeof fields !== "object" || Array.isArray(fields)) continue;
    for (const [field, raw] of Object.entries(fields)) {
      const n = numericEffectValue(raw);
      if (!n) continue;
      parts.push(`${labelEntity(entity)}·${cnField(field)}${signedNumber(n)}`);
    }
  }
};

const formatEffectSummary = (effect: any) => {
  if (!effect || typeof effect !== "object") return "无直接数值影响";
  const parts: string[] = [];

  const metrics = effect.metrics || {};
  for (const [k, v] of Object.entries(metrics)) {
    const n = Number(v);
    if (!n) continue;
    parts.push(`${k}${signedNumber(n)}`);
  }

  const econ = Array.isArray(effect.economy) ? effect.economy : [];
  for (const e of econ) {
    const n = Number(e?.delta);
    if (!n) continue;
    parts.push(`${e.account || "钱粮"}${signedNumber(n)}万`);
  }

  const factions = effect.factions || {};
  for (const [k, v] of Object.entries(factions)) {
    if (v && typeof v === "object") {
      const sub: string[] = [];
      for (const [kk, vv] of Object.entries(v as any)) {
        const n = Number(vv);
        if (!n) continue;
        sub.push(`${SAT_LEV_CN[kk] || cnField(kk)}${signedNumber(n)}`);
      }
      if (sub.length) parts.push(`${k}（${sub.join("、")}）`);
    } else {
      const n = Number(v);
      if (n) parts.push(`${k}${signedNumber(n)}`);
    }
  }

  appendScopedEffect(parts, effect.classes, labelClass);
  appendScopedEffect(parts, effect.regions, labelRegion);
  appendScopedEffect(parts, effect.armies, labelArmy);
  appendScopedEffect(parts, effect.powers, labelPower);

  if (effect.legacy && typeof effect.legacy === "object") {
    const legacyName = String(effect.legacy.name || "帝国修正");
    const duration = effect.legacy.duration ? `，${effect.legacy.duration}` : "";
    const modifiers = formatLegacyEffect(effect.legacy.modifiers || {});
    parts.push(`帝国修正：${legacyName}${duration}${modifiers ? `（${modifiers}）` : ""}`);
  }

  for (const [key, value] of Object.entries(effect)) {
    if (["metrics", "economy", "factions", "classes", "regions", "armies", "powers", "legacy", "buildings"].includes(key)) continue;
    const n = numericEffectValue(value);
    if (n) parts.push(`${cnField(key)}${signedNumber(n)}`);
  }

  return parts.length ? parts.join("、") : "无直接数值影响";
};

const formatIssueEffect = formatEffectSummary;
const formatClosedEffect = formatEffectSummary;

const splitReportItems = (text: string, prefix: string) => {
  const cleaned = text.replace(prefix, "").trim();
  const totalMatch = cleaned.match(/(两京十三省账面[月]税合计[^。]+|建档兵力合计[^。]+)。?$/);
  const itemsPart = totalMatch ? cleaned.slice(0, totalMatch.index).replace(/。$/, "") : cleaned.replace(/。$/, "");
  return {
    items: itemsPart.split("；").map((item) => item.replace(/^。+|。+$/g, "").trim()).filter(Boolean),
    tail: totalMatch?.[1] || "",
  };
};

// 邸报详明里 extractor 常输出英文 id（region_id/army_id/power_id）或编号。
// 这里建一份 id→中文名 的全局映射，每次拉 state 时刷新，供 ExtractionView 各 block 翻译。
const labelMaps = {
  region: new Map<string, string>(),
  army: new Map<string, string>(),
  power: new Map<string, string>(),
  issue: new Map<number, string>(),
};

const POWER_ID_CN: Record<string, string> = {
  ming: "大明",
  houjin: "后金",
  mongol: "蒙古",
  korea: "朝鲜",
  bandits: "流寇",
  dutch: "荷兰东印度公司",
  japan: "日本",
};

function refreshLabelMaps(state: GameState) {
  labelMaps.region.clear();
  labelMaps.army.clear();
  labelMaps.power.clear();
  labelMaps.issue.clear();
  for (const r of state.regions || []) labelMaps.region.set(r.id, r.name);
  for (const a of state.armies || []) labelMaps.army.set(a.id, a.name);
  for (const p of state.powers || []) labelMaps.power.set(p.id, p.name);
  for (const it of state.issues || []) labelMaps.issue.set(it.id, it.title);
  for (const it of state.closed_this_turn || []) labelMaps.issue.set(it.id, it.title);
}

// 把 id 翻成中文名；查不到（如本月新增/已离场）就回退原值，至少不空。
const labelRegion = (id: any) => labelMaps.region.get(String(id)) || String(id ?? "");
const labelArmy = (id: any) => labelMaps.army.get(String(id)) || String(id ?? "");
const labelPower = (id: any) => labelMaps.power.get(String(id)) || POWER_ID_CN[String(id)] || String(id ?? "");
const labelIssue = (id: any) => {
  const t = labelMaps.issue.get(Number(id));
  return t ? `#${id} ${t}` : `#${id}`;
};

// extractor 偶尔吐出的英文枚举值，统一翻中文。
const EN_VALUE_CN: Record<string, string> = {
  ...POWER_ID_CN,
  appoint: "新进朝堂", promote: "升迁", transfer: "调任", demote: "贬", reinstate: "起复",
  resolved: "已了", failed: "崩坏", dropped: "撤销",
  situation: "时局", initiative: "举措", crisis: "危机", reform: "改革", decree: "诏令",
  done: "办结", pending: "在办", pending_review: "待核议", active: "进行中",
  draft: "草案", rejected: "已驳回", cancelled: "已取消",
};
const cnValue = (v: any) => (v == null ? "" : (EN_VALUE_CN[String(v)] || String(v)));

// extractor 吐的是英文字段名（region/army/class/power 的列名），这里统一翻中文。
// 查不到的回退原值，至少不空。
const EN_FIELD_CN: Record<string, string> = {
  // 地区
  public_support: "民心", unrest: "动乱", grain_security: "粮食安全",
  gentry_resistance: "士绅阻力", military_pressure: "边防压力", corruption: "腐败度",
  population: "人口", registered_land: "在册田亩", hidden_land: "隐田",
  tax_per_turn: "月税", natural_disaster: "天灾", human_disaster: "人祸",
  status: "状态", controlled_by: "控制者", 控制: "控制者", kind: "类型",
  // 军队
  supply: "补给", morale: "士气", training: "操练", equipment: "军械",
  arrears: "欠饷", mobility: "机动", loyalty: "忠诚", manpower: "兵力",
  maintenance_quarter: "月饷", maintenance_per_turn: "月饷",
  station: "驻地", commander: "统帅", controller: "主管", troop_type: "兵种", owner_power: "归属",
  // 势力
  cohesion: "凝聚", 威望: "威望", leverage: "威望", 实力: "实力",
  military_strength: "实力", 经济: "经济",
  // 阶级
  satisfaction: "满意度",
};
const cnField = (k: string) => EN_FIELD_CN[k] || k;

const fiscalKeyLabel = (key: any): string => {
  const raw = String(key ?? "");
  const match = raw.match(/^(.+)_(base|rate)$/);
  if (!match) return cnField(raw);
  return `${match[1]}${match[2] === "base" ? "基数" : "系数"}`;
};

const briefTreasury = (state: GameState) => [
  `固定预算：国库月净${formatSignedMoney(state.budget["国库"].net)}，内库月净${formatSignedMoney(state.budget["内库"].net)}。`,
  `账面余银：国库${formatMoney(state.budget["国库"].balance)}，内库${formatMoney(state.budget["内库"].balance)}。`,
];

const briefRegionWarnings = (text: string) => {
  const { items, tail } = splitReportItems(text, "地区警讯：");
  return [...items.slice(0, 3), tail].filter(Boolean);
};

const briefArmyWarnings = (text: string) => {
  const { items, tail } = splitReportItems(text, "军队警讯：");
  return [...items.slice(0, 3), tail].filter(Boolean);
};

type EdictReadinessItem = {
  tone: "danger" | "warn" | "good";
  title: string;
  body: string;
};

const decreeActionText = (directives: Directive[]) =>
  directives.map((item) => `${item.text} ${item.notes || ""}`).join("\n");

const buildEdictReadiness = (state: GameState, draftDirectives: Directive[], pendingDirectives: Directive[]): EdictReadinessItem[] => {
  const items: EdictReadinessItem[] = [];
  const guoku = state.budget["国库"];
  const neiku = state.budget["内库"];
  const actionText = decreeActionText(draftDirectives);
  const hasMoneyAction = /银|饷|粮|赈|拨|库|税|清丈|盐|商|抄没|追缴|借|厘/.test(actionText);
  const hasMilitaryAction = /辽|关宁|山海|蓟|宣|大同|兵|军|饷|练|镇|边|后金|建州|锦州|宁远/.test(actionText);
  const activeIssues = [...(state.issues || [])]
    .filter((issue) => issue.kind === "situation")
    .sort((a, b) => (a.bar_value + Math.min(0, a.inertia) * 2) - (b.bar_value + Math.min(0, b.inertia) * 2));

  if (pendingDirectives.length) {
    items.push({
      tone: "danger",
      title: "尚有拟旨未核",
      body: `${pendingDirectives.length} 道大臣拟旨需先准/驳，否则不能颁诏。`,
    });
  }

  if (!draftDirectives.length) {
    items.push({
      tone: "danger",
      title: "本月无可颁指令",
      body: "网页端不可空过。至少新增一道指令，或召见大臣形成草案。",
    });
  }

  if (guoku.balance + guoku.net <= 80 || guoku.net < -40) {
    items.push({
      tone: guoku.balance + guoku.net <= 0 ? "danger" : "warn",
      title: "国库承压",
      body: `国库当前${formatMoney(guoku.balance)}，定额月净${formatSignedMoney(guoku.net)}。若诏令还要用银，最好明示来源。`,
    });
  }

  if (neiku.balance + neiku.net <= 60 || neiku.net < -18) {
    items.push({
      tone: "warn",
      title: "内库余地有限",
      body: `内库当前${formatMoney(neiku.balance)}，定额月净${formatSignedMoney(neiku.net)}。连续挪用会削弱宫廷与内廷线。`,
    });
  }

  const urgent = activeIssues.find((issue) => issue.bar_value <= 35 || issue.inertia < 0);
  if (urgent) {
    items.push({
      tone: urgent.bar_value <= 25 ? "danger" : "warn",
      title: `局势逼近：${urgent.title}`,
      body: `${urgent.stage_text || "仍在发酵"}；${urgent.inertia < 0 ? `自然恶化${urgent.inertia}/月` : "本月需继续压住惯性"}。`,
    });
  }

  if (draftDirectives.length && !hasMoneyAction && (guoku.net < 0 || activeIssues.some((issue) => /亏空|赈|饷|仓|税|银|粮/.test(issue.title + issue.tags.join(""))))) {
    items.push({
      tone: "warn",
      title: "缺少钱粮口径",
      body: "草案没有明显的拨银、税源、清账或赈济安排，结算时容易被判为执行条件不足。",
    });
  }

  if (draftDirectives.length && activeIssues.some((issue) => /辽|边|后金|军|饷|兵/.test(issue.title + issue.tags.join(""))) && !hasMilitaryAction) {
    items.push({
      tone: "warn",
      title: "边军线未被触及",
      body: "当前有边防或军务压力，但草案没有明显军饷、整军、换将或战备动作。",
    });
  }

  if (draftDirectives.length && hasMoneyAction) {
    items.push({
      tone: "good",
      title: "执行条件较清楚",
      body: "草案已出现钱粮/税源/拨付口径，月末推演更容易把圣旨落到具体账目。",
    });
  }

  if (draftDirectives.length && items.every((item) => item.tone === "good")) {
    items.push({
      tone: "good",
      title: "可颁",
      body: "未见阻断项。仍建议在正式诏书中写清承办人、银粮来源和时限。",
    });
  }

  return items.slice(0, 5);
};


const getMapIntelStyle = (node: MapNode): React.CSSProperties => {
  const left = Math.min(82, Math.max(18, node.x));
  const horizontal = node.x > 66 ? "-100%" : node.x < 34 ? "0" : "-50%";
  const style: React.CSSProperties = {
    left: `${left}%`,
    transform: `translateX(${horizontal})`,
  };
  if (node.y > 50) {
    style.bottom = "12px";
    style.top = "auto";
  } else {
    style.top = "12px";
    style.bottom = "auto";
  }
  return style;
};

type AppView = "menu" | "game";

type MenuSave = {
  name: string;
  size: number;
  mtime: number;
  campaign_id?: string;
  kind?: "auto" | "manual";
  label?: string;
  year?: number;
  period?: number;
  turn?: number;
  tag?: string;
};

type MenuCampaign = {
  campaign_id: string;
  kind: "auto" | "manual";
  current: boolean;
  saves: MenuSave[];
  latest_mtime: number;
};

type MenuStatus = {
  has_api_key: boolean;
  has_running_game: boolean;
  has_main_db: boolean;
  saves: MenuSave[];
  campaigns?: MenuCampaign[];
  current_campaign?: string;
  llm: {
    base_url: string;
    model: string;
    has_api_key: boolean;
    max_tokens: number;
    timeout_seconds: number;
    thinking_level: string;
    advanced_model: string;
    advanced_base_url: string;
    has_advanced_api_key: boolean;
    advanced_thinking_level: string;
  };
};

function App() {
  const [appView, setAppView] = React.useState<AppView>("menu");
  const [menuStatus, setMenuStatus] = React.useState<MenuStatus | null>(null);
  const [state, setState] = React.useState<GameState | null>(null);
  const [selectedNodeId, setSelectedNodeId] = React.useState<string>("");
  const [mapIntelOpen, setMapIntelOpen] = React.useState(false);
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [haremDrawerOpen, setHaremDrawerOpen] = React.useState(false);
  const [armyDrawerOpen, setArmyDrawerOpen] = React.useState(false);
  const [regionDrawerOpen, setRegionDrawerOpen] = React.useState(false);
  const [buildingDrawerOpen, setBuildingDrawerOpen] = React.useState(false);
  const [economyDrawerOpen, setEconomyDrawerOpen] = React.useState(false);
  const [appointmentDrawerOpen, setAppointmentDrawerOpen] = React.useState(false);
  const [organizationDrawerOpen, setOrganizationDrawerOpen] = React.useState(false);
  const [selectedRegionId, setSelectedRegionId] = React.useState<string>("");
  const [selectedArmyId, setSelectedArmyId] = React.useState<string>("");
  const [ministerGroup, setMinisterGroup] = React.useState("在职");
  const [haremGroup, setHaremGroup] = React.useState("全部");
  const [selectedMinister, setSelectedMinister] = React.useState<string>("");
  const [temporaryActiveMinister, setTemporaryActiveMinister] = React.useState<Minister | null>(null);
  const [activeModal, setActiveModal] = React.useState<ModalName>("none");
  const [chat, setChat] = React.useState<ChatMessage[]>([]);
  const [suggestions, setSuggestions] = React.useState<Suggestion[]>([]);
  const [pendingUserMessage, setPendingUserMessage] = React.useState("");
  const [streamingMinisterMessage, setStreamingMinisterMessage] = React.useState("");
  const [chatNotice, setChatNotice] = React.useState("");
  const [chatEffectNotices, setChatEffectNotices] = React.useState<ChatEffectNotice[]>([]);
  const [canUndoLastChat, setCanUndoLastChat] = React.useState(false);
  const [composerHint, setComposerHint] = React.useState("");
  const [input, setInput] = React.useState("");
  const [directiveText, setDirectiveText] = React.useState("");
  const [editingDirectiveId, setEditingDirectiveId] = React.useState<number | null>(null);
  const [editingDirectiveText, setEditingDirectiveText] = React.useState("");
  const [decree, setDecree] = React.useState("");
  const [report, setReport] = React.useState("");
  const [gazetteReport, setGazetteReport] = React.useState("");
  const [busy, setBusy] = React.useState("");
  const [error, setError] = React.useState("");
  const [settleStage, setSettleStage] = React.useState("");
  const [settleThinking, setSettleThinking] = React.useState("");
  const [settleNarrative, setSettleNarrative] = React.useState("");
  const [closedShown, setClosedShown] = React.useState<number>(() => {
    const raw = sessionStorage.getItem("closedShownTurn");
    return raw ? Number(raw) : -1;
  });
  const [closedModal, setClosedModal] = React.useState<ClosedIssue[]>([]);
  const [gazetteShown, setGazetteShown] = React.useState<number>(-1);
  // 结局页本次加载是否已被玩家关掉（关掉后让位邸报，刷新复位重弹）。
  const [endingDismissed, setEndingDismissed] = React.useState(false);
  const [secretOrders, setSecretOrders] = React.useState<SecretOrder[]>([]);
  const [secretOrderShown, setSecretOrderShown] = React.useState<number>(-1);
  // 作弊控制台（Ctrl+~）：cheatDirective 暂存强制结算项，下次颁诏随结算一次性穿入。
  const [cheatOpen, setCheatOpen] = React.useState(false);
  const [cheatDirective, setCheatDirective] = React.useState("");

  const activeDrawer: DrawerName =
    drawerOpen ? "court" :
    haremDrawerOpen ? "harem" :
    armyDrawerOpen ? "army" :
    regionDrawerOpen ? "region" :
    buildingDrawerOpen ? "building" :
    economyDrawerOpen ? "economy" :
    appointmentDrawerOpen ? "appointment" :
    organizationDrawerOpen ? "organization" : "";

  const setActiveDrawer = React.useCallback((drawer: DrawerName) => {
    setDrawerOpen(drawer === "court");
    setHaremDrawerOpen(drawer === "harem");
    setArmyDrawerOpen(drawer === "army");
    setRegionDrawerOpen(drawer === "region");
    setBuildingDrawerOpen(drawer === "building");
    setEconomyDrawerOpen(drawer === "economy");
    setAppointmentDrawerOpen(drawer === "appointment");
    setOrganizationDrawerOpen(drawer === "organization");
  }, []);

  const toggleDrawer = React.useCallback((drawer: Exclude<DrawerName, "">) => {
    setActiveDrawer(activeDrawer === drawer ? "" : drawer);
  }, [activeDrawer, setActiveDrawer]);

  const loadState = React.useCallback(async () => {
    const data = await api<GameState>("/api/game/state");
    refreshLabelMaps(data);
    setState(data);
    setSelectedNodeId((current) => current || data.map_nodes[0]?.id || "");
    setDecree(data.last_decree || "");
    setReport(data.last_report || "");
  }, [selectedMinister]);

  const loadMinisterChat = React.useCallback(async (ministerName: string) => {
    const data = await api<{ minister: Minister; history: ChatMessage[]; suggestions: Suggestion[]; can_undo_last_chat: boolean }>(`/api/ministers/${encodeURIComponent(ministerName)}/chat`);
    setTemporaryActiveMinister(data.minister);
    setChat(data.history);
    setSuggestions(data.suggestions);
    setCanUndoLastChat(!!data.can_undo_last_chat);
  }, []);

  React.useEffect(() => {
    if (!state) return;
    const pending = [...(state.ministers || []), ...(state.consorts || [])]
      .filter((m) => m.portrait_status === "pending" && m.portrait_id?.startsWith("generated:"));
    if (!pending.length) return;
    let cancelled = false;
    const poll = async () => {
      let changed = false;
      for (const minister of pending) {
        try {
          const data = await api<{ status: string; portrait_id?: string }>(`/api/portraits/${encodeURIComponent(minister.name)}/status`);
          if (data.status && data.status !== "pending") {
            const key = data.portrait_id || minister.portrait_id || "";
            if (key) _portraitBust[key] = Date.now();
            changed = true;
          }
        } catch {
          // 状态轮询是旁路，失败时等下轮或玩家手动刷新。
        }
      }
      if (changed && !cancelled) {
        await loadState();
      }
    };
    const timer = window.setInterval(() => { void poll(); }, 5000);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [state, loadState]);

  const uploadPortrait = React.useCallback(async (ministerName: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const resp = await fetch(`/api/consorts/${encodeURIComponent(ministerName)}/portrait`, {
      method: "POST",
      body: form,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    await loadState();  // 重新拉 state，新 portrait_id 流回卡片
  }, [loadState]);

  const generatePortrait = React.useCallback(async (ministerName: string) => {
    const data = await api<{ job: { portrait_id: string; status: string }; character?: Minister | null }>(
      `/api/portraits/${encodeURIComponent(ministerName)}/generate`,
      { method: "POST" },
    );
    if (data.job?.portrait_id) {
      _portraitBust[data.job.portrait_id] = Date.now();
    }
    await loadState();
  }, [loadState]);

  const refreshMenuStatus = React.useCallback(async () => {
    const s = await api<MenuStatus>("/api/menu/status");
    setMenuStatus(s);
    return s;
  }, []);

  React.useEffect(() => {
    refreshMenuStatus()
      .then((s) => {
        if (s.has_running_game) {
          setAppView("game");
          loadState().catch((err) => setError(err.message));
        }
      })
      .catch((err) => setError(err.message));
  }, [refreshMenuStatus, loadState]);

  const enterGameAfterMenu = React.useCallback(async () => {
    setAppView("game");
    await loadState();
  }, [loadState]);

  const exitToMenu = React.useCallback(async () => {
    await fetch("/api/menu/exit_to_menu", { method: "POST" });
    setState(null);
    setAppView("menu");
    await refreshMenuStatus();
  }, [refreshMenuStatus]);

  React.useEffect(() => {
    if (!state) return;
    const closed = state.closed_this_turn || [];
    const currentTurn = state.turn.turn;
    if (closed.length && currentTurn !== closedShown) {
      setClosedModal(closed);
      setClosedShown(currentTurn);
      sessionStorage.setItem("closedShownTurn", String(currentTurn));
    }
  }, [state, closedShown]);

  // 新回合进入时拉取全部密令，有 active 密令则弹密令进度弹窗（邸报关闭后显示）
  React.useEffect(() => {
    if (!state) return;
    const currentTurn = state.turn.turn;
    if (currentTurn === secretOrderShown) return;
    api<{ orders: SecretOrder[] }>("/api/secret_orders")
      .then(({ orders }) => {
        setSecretOrders(orders);
        if (orders.some(o => o.status === "active" || o.status === "pending_review")) {
          // 延迟 400ms，避免与邸报弹窗争抢
          setTimeout(() => setActiveModal("secret_orders"), 400);
        }
        setSecretOrderShown(currentTurn);
      })
      .catch(() => {/* 失败静默 */});
  }, [state?.turn.turn]);

  // 结局已触发：每次进页面/刷新都自动弹结局结算页。玩家点关闭后（endingDismissed）
  // 本次加载让位给盘面/邸报，可继续看局；刷新即复位重弹。
  React.useEffect(() => {
    if (!state || !state.ending) return;
    if (endingDismissed) return;
    setActiveModal("ending");
  }, [state, endingDismissed]);

  // 每次进入页面/换回合都弹上回合邸报。不持久化记录——刷新即重新弹。
  // 同一加载周期内同一回合不重复弹（gazetteShown 用 React state，刷新后回到 -1）。
  React.useEffect(() => {
    if (!state) return;
    // 结局页未关掉时让位给它；玩家关掉后（endingDismissed）邸报照常。
    if (state.ending && !endingDismissed) return;
    const currentTurn = state.turn.turn;
    const summary = (state.previous_summary || "").trim();
    if (!summary) return;
    if (summary.startsWith("登基伊始")) return;
    if (currentTurn === gazetteShown) return;
    setGazetteReport(summary);
    setActiveModal("report");
    setGazetteShown(currentTurn);
  }, [state, gazetteShown, endingDismissed]);

  React.useEffect(() => {
    if (!selectedMinister) {
      setChat([]);
      setSuggestions([]);
      setPendingUserMessage("");
      setStreamingMinisterMessage("");
      setChatNotice("");
      setChatEffectNotices([]);
      setCanUndoLastChat(false);
      setComposerHint("");
      return;
    }
    setChat([]);
    setSuggestions([]);
    setPendingUserMessage("");
    setStreamingMinisterMessage("");
    setChatEffectNotices([]);
    setCanUndoLastChat(false);
    setComposerHint("");
    loadMinisterChat(selectedMinister).catch((err) => setError(err.message));
  }, [selectedMinister, loadMinisterChat]);

  // 全局 ESC：按 z-index 优先级，最前面的弹窗先关
  React.useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (activeModal === "chat" || activeModal === "edict" || activeModal === "state" || activeModal === "history" || activeModal === "report" || activeModal === "secret_orders" || activeModal === "long_goals" || activeModal === "adventure") {
        // 召对/诏书等全屏弹窗最优先
        setActiveModal("none");
      } else if (activeDrawer) {
        setActiveDrawer("");
      } else if (mapIntelOpen) {
        setMapIntelOpen(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [
    activeModal,
    activeDrawer,
    setActiveDrawer,
    mapIntelOpen,
  ]);

  // 作弊控制台：Ctrl+~（或 Ctrl+`）切换显隐。强制结算唯一入口。
  React.useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.ctrlKey && (event.key === "~" || event.key === "`")) {
        event.preventDefault();
        setCheatOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  if (appView === "menu") {
    return (
      <MenuPage
        status={menuStatus}
        onRefresh={refreshMenuStatus}
        onEnterGame={enterGameAfterMenu}
        error={error}
        setError={setError}
      />
    );
  }

  if (!state) {
    return (
      <div className="loading-screen">
        <div className="loading-panel">
          <Crown size={28} />
          <p>正在启封奏牍与山河舆图...</p>
        </div>
      </div>
    );
  }

  const powerById = new Map((state.powers || []).map((power) => [power.id, power]));
  const mapNodes = state.map_nodes.map((node) => {
    const powerId = node.region?.controlled_by;
    return powerId ? { ...node, power: powerById.get(powerId) } : node;
  });
  const selectedNode = mapNodes.find((node) => node.id === selectedNodeId) || mapNodes[0];
  const ministers = filterMinisters(state.ministers, ministerGroup);
  const consorts = filterConsorts(state.consorts || [], haremGroup);
  const allCharacters = [...state.ministers, ...(state.consorts || [])];
  const activeMinister = selectedMinister
    ? (temporaryActiveMinister?.name === selectedMinister ? temporaryActiveMinister : null)
      || allCharacters.find((m) => m.name === selectedMinister)
    : null;
  const mapIntelStyle = selectedNode ? getMapIntelStyle(selectedNode) : undefined;

  const openChat = (minister: Minister, prefill = "") => {
    if (minister.status && minister.status !== "active") {
      setError(`${minister.name}${minister.status_label}${minister.status_reason ? "（" + minister.status_reason + "）" : ""}，无法召见。`);
      return;
    }
    const switchingMinister = selectedMinister !== minister.name;
    if (switchingMinister) {
      setChat([]);
      setSuggestions([]);
      setTemporaryActiveMinister(null);
      setCanUndoLastChat(false);
    }
    setSelectedMinister(minister.name);
    setActiveModal("chat");
    setError("");
    setInput(prefill);
    setComposerHint(prefill ? "已为净身劝说预置奏对，请斟酌后发送" : "");
    setChatNotice("");
    setChatEffectNotices([]);
    setCanUndoLastChat(false);
    setPendingUserMessage("");
    setStreamingMinisterMessage("");
    loadMinisterChat(minister.name).catch((err) => setError(err.message));
  };

  const selectMapNode = (nodeId: string) => {
    setSelectedNodeId(nodeId);
    setMapIntelOpen(true);
  };

  const sendChat = async (text = input) => {
    if (busy) return;
    if (!activeMinister) return;
    const message = text.trim();
    if (!message) {
      setComposerHint("请先问话或点一个奏对题目");
      return;
    }

    const fromComposer = text === input;
    setPendingUserMessage(message);
    setStreamingMinisterMessage("");
    setBusy("大臣思索中");
    setError("");
    setComposerHint("");
    setChatNotice("");
    setChatEffectNotices([]);
    if (fromComposer) {
      setInput("");
    }
    try {
      const data = await streamChat(activeMinister.name, message, (delta) => {
        setStreamingMinisterMessage((current) => current + delta);
      });
      setPendingUserMessage("");
      setStreamingMinisterMessage("");
      if (data.minister_profile) {
        setTemporaryActiveMinister(data.minister_profile);
      }
      setChat(data.history);
      setSuggestions(data.suggestions);
      setCanUndoLastChat(!!data.can_undo_last_chat);
      setState((current) => (current ? { ...current, directives: data.directives, pending_count: data.pending_count ?? current.pending_count } : current));
      await loadState();
      if (!data.next_minister) {
        await loadMinisterChat(activeMinister.name);
      }
      // 刷新密令列表（含历史，大臣可能调了 issue_secret_order tool）
      api<{ orders: SecretOrder[] }>("/api/secret_orders")
        .then(({ orders }) => setSecretOrders(orders))
        .catch(() => {});
      const notices: string[] = [];
      const effectNotices: ChatEffectNotice[] = [];
      if (data.appointed_minister) {
        notices.push(`吏部已铨补${data.appointed_minister}入朝，名册已更新。`);
      }
      if (data.displaced_minister) {
        effectNotices.push({
          id: `displaced-${data.displaced_minister}`,
          kind: "appointment",
          title: `腾缺去职：${data.displaced_minister}`,
          source: "吏部铨选",
          summary: data.displaced_effect?.summary || `${data.displaced_minister}已因腾缺去任。`,
          detail: data.displaced_effect?.reaction_summary ? `朝局余波：${data.displaced_effect.reaction_summary}` : "",
          chips: xinpanEffectChips(data.displaced_effect?.xinpan),
          tone: "danger",
        });
      }
      if (data.registered_minister) {
        const summoned = data.next_minister === data.registered_minister ? "，并已传入殿" : "";
        notices.push(`已将${data.registered_minister}补入名册${summoned}。`);
      }
      if (data.secret_order_id) {
        const riskScore = Number(data.secret_order_effect?.risk_score || 0);
        effectNotices.push({
          id: `secret-${data.secret_order_id}`,
          kind: "secret",
          title: `密令交付 #${data.secret_order_id}`,
          source: data.secret_order_effect?.risk_label || "密令",
          summary: data.secret_order_effect?.summary || `密令已秘密交付${data.secret_order_assignee || activeMinister.name}。`,
          chips: xinpanEffectChips(data.secret_order_effect?.xinpan),
          tone: riskScore >= 3 ? "danger" : riskScore >= 2 ? "warn" : "neutral",
        });
      }
      if (data.proposed_directive) {
        notices.push(`${data.proposed_directive.actor || activeMinister.name}已拟旨一道，待陛下在「诏书草案」核定（准/驳）。`);
      }
      if (data.next_minister) {
        setChat([]);
        setSuggestions([]);
        setStreamingMinisterMessage("");
        setCanUndoLastChat(false);
        setSelectedMinister(data.next_minister);
        setActiveModal("chat");
        if (data.next_minister !== data.registered_minister) {
          notices.push(`已传${data.next_minister}入殿。`);
        }
        loadMinisterChat(data.next_minister).catch((err) => setError(err.message));
      }
      if (data.court_action === "dismiss") {
        setPendingUserMessage("");
        notices.push(`${activeMinister.name}已退下。请从左侧召见下一位大臣。`);
      }
      if (notices.length) {
        setChatNotice(notices.join("\n"));
      }
      setChatEffectNotices(effectNotices);
    } catch (err) {
      if (fromComposer) {
        setInput(message);
      }
      setPendingUserMessage("");
      setStreamingMinisterMessage("");
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const undoLastChat = async () => {
    if (busy || !activeMinister || !canUndoLastChat) return;
    const ok = window.confirm("将撤回最近一轮召对及其政务影响，是否继续？");
    if (!ok) return;
    setBusy("撤回召对");
    setError("");
    setChatNotice("");
    setChatEffectNotices([]);
    setComposerHint("");
    setPendingUserMessage("");
    setStreamingMinisterMessage("");
    try {
      const data = await api<ChatUndoResponse>(`/api/ministers/${encodeURIComponent(activeMinister.name)}/chat/undo`, {
        method: "POST",
      });
      if (data.minister_profile) {
        setTemporaryActiveMinister(data.minister_profile);
      }
      setChat(data.history);
      setSuggestions(data.suggestions);
      setCanUndoLastChat(!!data.can_undo_last_chat);
      setSecretOrders(data.secret_orders || []);
      setState((current) => (current ? { ...current, directives: data.directives, pending_count: data.pending_count } : current));
      await loadState();
      await loadMinisterChat(activeMinister.name);
      setChatNotice("已撤回最近一轮召对。");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const createDirective = async () => {
    if (!directiveText.trim()) return;
    setBusy("登记诏书草案");
    setError("");
    try {
      const data = await api<{ directives: Directive[] }>("/api/directives", {
        method: "POST",
        body: JSON.stringify({
          text: directiveText.trim(),
        }),
      });
      setDirectiveText("");
      setState((current) => (current ? { ...current, directives: data.directives } : current));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const toggleFavorite = async (minister: Minister) => {
    setBusy(minister.favorite ? "移出收藏" : "加入收藏");
    setError("");
    try {
      const data = await api<{ favorites: string[] }>(`/api/favorites/${encodeURIComponent(minister.name)}`, {
        method: minister.favorite ? "DELETE" : "POST",
      });
      setTemporaryActiveMinister((current) => (
        current?.name === minister.name
          ? { ...current, favorite: data.favorites.includes(minister.name) }
          : current
      ));
      await loadState();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const startEditDirective = (directive: Directive) => {
    setEditingDirectiveId(directive.id);
    setEditingDirectiveText(directive.text);
  };

  const cancelEditDirective = () => {
    setEditingDirectiveId(null);
    setEditingDirectiveText("");
  };

  const saveDirective = async (directive: Directive) => {
    if (!editingDirectiveText.trim()) return;
    setBusy("修改草案");
    setError("");
    try {
      const data = await api<{ directives: Directive[] }>(`/api/directives/${directive.id}`, {
        method: "PATCH",
        body: JSON.stringify({ text: editingDirectiveText.trim() }),
      });
      setState((current) => (current ? { ...current, directives: data.directives } : current));
      cancelEditDirective();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const deleteDirective = async (directiveId: number) => {
    setBusy("删除草案");
    setError("");
    try {
      const data = await api<{ directives: Directive[] }>(`/api/directives/${directiveId}`, { method: "DELETE" });
      setState((current) => (current ? { ...current, directives: data.directives } : current));
      if (editingDirectiveId === directiveId) {
        cancelEditDirective();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const confirmDirective = async (directiveId: number) => {
    setBusy("核定大臣拟旨");
    setError("");
    try {
      const data = await api<{ directives: Directive[]; pending_count: number }>(`/api/directives/${directiveId}/confirm`, { method: "POST" });
      setState((current) => (current ? { ...current, directives: data.directives, pending_count: data.pending_count } : current));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const rejectDirective = async (directiveId: number) => {
    setBusy("驳回大臣拟旨");
    setError("");
    try {
      const data = await api<{ directives: Directive[]; pending_count: number }>(`/api/directives/${directiveId}/reject`, { method: "POST" });
      setState((current) => (current ? { ...current, directives: data.directives, pending_count: data.pending_count } : current));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const writeDecree = async () => {
    setBusy("拟写正式诏书");
    setError("");
    try {
      const data = await api<{ decree: string }>("/api/decree/write", { method: "POST" });
      setDecree(data.decree);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const saveDecree = async (text: string) => {
    setBusy("存改诏书");
    setError("");
    try {
      const data = await api<{ decree: string }>("/api/decree", {
        method: "PATCH",
        body: JSON.stringify({ decree: text }),
      });
      setDecree(data.decree);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const issueDecree = async () => {
    setBusy("月末结算");
    setSettleStage("");
    setSettleThinking("");
    setSettleNarrative("");
    setError("");
    try {
      // 作弊强制结算项随颁诏一次性穿入；发出即清空，绝不跨回合。
      const cheatPayload = cheatDirective.trim();
      const response = await fetch("/api/decree/issue/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cheat: cheatPayload }),
      });
      if (cheatPayload) {
        setCheatDirective("");
      }
      if (!response.ok || !response.body) {
        throw new Error(`颁诏失败：HTTP ${response.status}`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let done = false;
      let failed = "";
      while (!done) {
        const { value, done: streamDone } = await reader.read();
        if (streamDone) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE 事件以空行分隔
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";
        for (const block of blocks) {
          let evName = "";
          let dataRaw = "";
          for (const line of block.split("\n")) {
            if (line.startsWith("event: ")) evName = line.slice(7).trim();
            else if (line.startsWith("data: ")) dataRaw += line.slice(6);
          }
          if (!evName || !dataRaw) continue;
          let data: { content?: string; message?: string } = {};
          try { data = JSON.parse(dataRaw); } catch { continue; }
          if (evName === "stage") {
            setSettleStage(data.content || "");
          } else if (evName === "thinking") {
            setSettleThinking((prev) => prev + (data.content || ""));
          } else if (evName === "text") {
            setSettleNarrative((prev) => prev + (data.content || ""));
          } else if (evName === "error") {
            failed = data.message || "颁诏失败。";
            done = true;
          } else if (evName === "done") {
            done = true;
          }
        }
      }
      if (failed) {
        setError(failed);
        setBusy("");
        return;
      }
      // 结算完成：强制整页刷新，草案/对话/局势/closed 弹窗全部按新 state 重新初始化
      window.location.reload();
      return;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy("");
    }
  };

  const addCustomInstitution = async (payload: { name: string; category: string; mandate: string; slots: string[] }) => {
    setBusy("增设机构");
    setError("");
    try {
      const data = await api<{ message: string; organizations: OrganizationPayload }>("/api/organizations/custom", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await loadState();
      return data.message;
    } finally {
      setBusy("");
    }
  };

  const runRecruitment = async (action: "exam" | "eunuch" | "recommend") => {
    setBusy("遴选人才");
    setError("");
    try {
      const data = await api<{ message: string; minister?: Minister }>(`/api/recruitment/${action}`, { method: "POST" });
      await loadState();
      return data.message;
    } finally {
      setBusy("");
    }
  };

  const castrateMinister = async (name: string, force = false) => {
    setBusy("净身入宫");
    setError("");
    try {
      const data = await api<{ message: string; minister: Minister }>("/api/recruitment/castrate", {
        method: "POST",
        body: JSON.stringify({ name, force }),
      });
      await loadState();
      return data.message;
    } finally {
      setBusy("");
    }
  };

  const emancipateMinister = async (name: string, force = false) => {
    setBusy("奴籍转民籍");
    setError("");
    try {
      const data = await api<{ message: string; minister: Minister }>("/api/recruitment/emancipate", {
        method: "POST",
        body: JSON.stringify({ name, force }),
      });
      await loadState();
      return data.message;
    } finally {
      setBusy("");
    }
  };

  const runConsortAction = async (name: string, action: "stabilize" | "treasury" | "appease" | "recommend") => {
    setBusy("后宫行事");
    setError("");
    try {
      const data = await api<{ message: string }>(`/api/consorts/${encodeURIComponent(name)}/action`, {
        method: "POST",
        body: JSON.stringify({ action }),
      });
      await loadState();
      return data.message;
    } finally {
      setBusy("");
    }
  };

  const settling = busy === "月末结算";
  const guardClose = (fn: () => void) => () => {
    if (settling) return;
    fn();
  };

  return (
    <main className="game-shell">
      <GrandMap nodes={mapNodes} selectedId={mapIntelOpen ? selectedNode?.id || "" : ""} onSelect={selectMapNode} />
      <TopStatusBar
        state={state}
        onOpenState={() => setActiveModal("state")}
        onOpenMenu={() => setActiveModal("menu")}
      />
      <RightNavBar
        onToggleCourt={() => toggleDrawer("court")}
        onToggleHarem={() => toggleDrawer("harem")}
        onToggleArmy={() => toggleDrawer("army")}
        onToggleRegion={() => toggleDrawer("region")}
        onToggleBuilding={() => toggleDrawer("building")}
        onToggleEconomy={() => toggleDrawer("economy")}
        onToggleAppointment={() => toggleDrawer("appointment")}
        onToggleOrganization={() => toggleDrawer("organization")}
        onOpenLongGoals={() => setActiveModal("long_goals")}
        activeDrawer={activeDrawer}
      />
      <BottomCommandBar
        eventsCount={state.events.length}
        directivesCount={state.directives.length}
        secretOrdersCount={secretOrders.filter((o) => o.status === "active" || o.status === "pending_review").length}
        adventureCount={(state.adventures || []).length}
        onOpenMemorials={() => setActiveModal("state")}
        onOpenEdict={() => setActiveModal("edict")}
        onOpenExtraction={() => setActiveModal("extraction")}
        onOpenHistory={() => setActiveModal("history")}
        onOpenSecretOrders={() => setActiveModal("secret_orders")}
        onOpenAdventure={() => setActiveModal("adventure")}
      />

      <CourtDrawer
        state={state}
        ministers={ministers}
        ministerGroup={ministerGroup}
        selectedMinister={selectedMinister}
        open={drawerOpen}
        onGroupChange={setMinisterGroup}
        onClose={guardClose(() => setActiveDrawer(""))}
        onOpenChat={openChat}
        onUploadPortrait={uploadPortrait}
        onGeneratePortrait={generatePortrait}
      />

      <HaremDrawer
        consorts={consorts}
        haremGroup={haremGroup}
        selectedMinister={selectedMinister}
        open={haremDrawerOpen}
        onGroupChange={setHaremGroup}
        onClose={guardClose(() => setActiveDrawer(""))}
        onOpenChat={openChat}
        onUploadPortrait={uploadPortrait}
        onGeneratePortrait={generatePortrait}
        onAction={runConsortAction}
      />

      <ArmyDrawer
        armies={state.armies}
        open={armyDrawerOpen}
        selectedArmyId={selectedArmyId}
        onSelectArmy={setSelectedArmyId}
        onClose={guardClose(() => setActiveDrawer(""))}
      />

      <RegionDrawer
        regions={state.regions}
        open={regionDrawerOpen}
        selectedRegionId={selectedRegionId}
        onSelectRegion={setSelectedRegionId}
        onClose={guardClose(() => setActiveDrawer(""))}
      />

      <BuildingDrawer
        regions={state.regions}
        mapNodes={mapNodes}
        open={buildingDrawerOpen}
        onClose={guardClose(() => setActiveDrawer(""))}
      />

      <EconomyDrawer
        state={state}
        open={economyDrawerOpen}
        onClose={guardClose(() => setActiveDrawer(""))}
      />

      <AppointmentDrawer
        ministers={state.ministers}
        characterIndex={state.character_index || []}
        agreements={state.agreements || []}
        open={appointmentDrawerOpen}
        onOpenChat={openChat}
        onRecruit={runRecruitment}
        onCastrate={castrateMinister}
        onEmancipate={emancipateMinister}
        onClose={guardClose(() => setActiveDrawer(""))}
      />

      <OrganizationDrawer
        organizations={state.organizations}
        open={organizationDrawerOpen}
        onAddCustom={addCustomInstitution}
        onOpenChat={openChat}
        onClose={guardClose(() => setActiveDrawer(""))}
      />

      <SituationPanel
        issues={state.issues}
        closedIssues={state.closed_this_turn || []}
        hasLegacies={(state.legacies || []).length > 0}
      />

      {mapIntelOpen && selectedNode ? (
        <MapIntelPanel node={selectedNode} style={mapIntelStyle} onClose={() => setMapIntelOpen(false)} />
      ) : null}

      {activeModal === "state" ? (
        <FullscreenModal title="国势与奏报" subtitle={`${state.turn.year} 年 ${state.turn.period} 月`} bgClass="modal-bg-state" onClose={guardClose(() => setActiveModal("none"))}>
          <StateModal state={state} />
        </FullscreenModal>
      ) : null}

      {activeModal === "long_goals" ? (
        <LongGoalsModal onClose={guardClose(() => setActiveModal("none"))} />
      ) : null}

      {activeModal === "chat" && activeMinister ? (
        <FullscreenModal title={`召对：${activeMinister.name}`} subtitle={activeMinister.office} bgClass="modal-bg-chat" onClose={guardClose(() => setActiveModal("none"))}>
          <ChatModal
            minister={activeMinister}
            portraitPrefix={(state.consorts || []).some((c) => c.name === activeMinister.name) ? "consort_" : "minister_"}
            chat={chat}
            suggestions={suggestions}
            pendingUserMessage={pendingUserMessage}
            streamingMinisterMessage={streamingMinisterMessage}
            chatNotice={chatNotice}
            chatEffectNotices={chatEffectNotices}
            canUndoLastChat={canUndoLastChat}
            composerHint={composerHint}
            input={input}
            busy={busy}
            error={error}
            secretOrders={secretOrders.filter((o) => o.minister_name === activeMinister.name && (o.status === "active" || o.status === "pending_review"))}
            onInput={setInput}
            onSend={sendChat}
            onUndo={undoLastChat}
            onHint={setComposerHint}
            onFavorite={() => toggleFavorite(activeMinister)}
            onOpenEdict={() => setActiveModal("edict")}
            onClose={guardClose(() => setActiveModal("none"))}
          />
        </FullscreenModal>
      ) : null}

      {activeModal === "edict" ? (
        <FullscreenModal title="诏书草案" subtitle="本月指令、拟诏与颁布" bgClass="modal-bg-edict" onClose={guardClose(() => setActiveModal("none"))}>
          <EdictModal
            state={state}
            directiveText={directiveText}
            editingDirectiveId={editingDirectiveId}
            editingDirectiveText={editingDirectiveText}
            decree={decree}
            report={report}
            busy={busy}
            error={error}
            onDirectiveTextChange={setDirectiveText}
            onEditingTextChange={setEditingDirectiveText}
            onCreateDirective={createDirective}
            onStartEdit={startEditDirective}
            onCancelEdit={cancelEditDirective}
            onSaveDirective={saveDirective}
            onDeleteDirective={deleteDirective}
            onWriteDecree={writeDecree}
            onSaveDecree={saveDecree}
            onIssueDecree={issueDecree}
            onConfirmDirective={confirmDirective}
            onRejectDirective={rejectDirective}
          />
        </FullscreenModal>
      ) : null}

      {activeModal === "report" && (gazetteReport || report) ? (
        <ReportModal report={gazetteReport || report} onClose={guardClose(() => setActiveModal("none"))} />
      ) : null}

      {activeModal === "ending" && state.ending ? (
        <EndingModal ending={state.ending} onClose={() => { setEndingDismissed(true); setActiveModal("none"); }} />
      ) : null}

      {activeModal === "extraction" ? (
        <ExtractionModal onClose={guardClose(() => setActiveModal("none"))} />
      ) : null}

      {activeModal === "history" ? (
        <HistoryModal onClose={guardClose(() => setActiveModal("none"))} />
      ) : null}

      {activeModal === "menu" ? (
        <GameMenuModal
          onClose={guardClose(() => setActiveModal("none"))}
          onAfterLoad={() => {
            setActiveModal("none");
            window.location.reload();
          }}
          onExitToMenu={async () => {
            await exitToMenu();
            setActiveModal("none");
          }}
        />
      ) : null}

      {closedModal.length ? (
        <ClosedIssuesModal items={closedModal} onClose={() => setClosedModal([])} />
      ) : null}

      {activeModal === "secret_orders" ? (
        <SecretOrdersModal
          orders={secretOrders}
          currentTurn={state.turn.turn}
          onClose={() => setActiveModal("none")}
          onOpenMinister={(name) => {
            setActiveModal("chat");
            setSelectedMinister(name);
          }}
        />
      ) : null}

      {activeModal === "adventure" ? (
        <AdventureLogModal state={state} onClose={guardClose(() => setActiveModal("none"))} />
      ) : null}

      {settling ? (
        <SettlementLock
          stage={settleStage}
          thinking={settleThinking}
          narrative={settleNarrative}
        />
      ) : null}

      {cheatOpen ? (
        <CheatConsole
          directive={cheatDirective}
          onCommit={setCheatDirective}
          onClose={() => setCheatOpen(false)}
        />
      ) : null}
    </main>
  );
}

// 作弊控制台：terminal UI。强制结算唯一入口（Ctrl+~ 唤出）。输入的指令暂存于
// cheatDirective，下次颁诏时随结算穿入 extractor 当既成事实落库。
function CheatConsole({
  directive,
  onCommit,
  onClose,
}: {
  directive: string;
  onCommit: (text: string) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = React.useState("");
  const [history, setHistory] = React.useState<string[]>([]);
  const inputRef = React.useRef<HTMLTextAreaElement>(null);
  const bodyRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    inputRef.current?.focus();
  }, []);
  React.useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [history]);

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    onCommit(text);
    setHistory((h) => [...h, `> ${text}`, "  已挂载强制结算项，下次颁诏随结算生效（一次性）。"]);
    setDraft("");
  };

  const clearMounted = () => {
    onCommit("");
    setHistory((h) => [...h, "  已清空强制结算项。"]);
  };

  return (
    <div className="cheat-console" role="dialog" aria-label="天命控制台" onClick={onClose}>
      <div className="cheat-console-window" onClick={(e) => e.stopPropagation()}>
        <div className="cheat-console-titlebar">
          <span>tianming@ming-salvage:~$ 天命控制台</span>
          <button className="cheat-console-x" onClick={onClose} aria-label="关闭">×</button>
        </div>
        <div className="cheat-console-body" ref={bodyRef}>
          <div className="cheat-console-line cheat-console-dim">
            强制结算控制台。输入的指令将在下次颁诏时作为「既成事实」穿入结算，无视合理性与史实。
          </div>
          <div className="cheat-console-line cheat-console-dim">
            Enter 提交 · Shift+Enter 换行 · Ctrl+~ 关闭
          </div>
          {directive ? (
            <div className="cheat-console-line cheat-console-armed">
              ● 当前已挂载：{directive}
            </div>
          ) : (
            <div className="cheat-console-line cheat-console-dim">○ 当前无挂载项</div>
          )}
          {history.map((line, i) => (
            <div className="cheat-console-line" key={i}>{line}</div>
          ))}
        </div>
        <div className="cheat-console-prompt">
          <span className="cheat-console-caret">&gt;</span>
          <textarea
            ref={inputRef}
            className="cheat-console-input"
            value={draft}
            rows={1}
            placeholder="例：国库增至九千万两，后金军覆灭，皇太极暴毙"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
        </div>
        <div className="cheat-console-actions">
          <button className="cheat-console-btn" onClick={submit}>挂载</button>
          <button className="cheat-console-btn cheat-console-btn-ghost" onClick={clearMounted}>清空挂载</button>
        </div>
      </div>
    </div>
  );
}

function SettlementLock({
  stage,
  thinking,
  narrative,
}: {
  stage: string;
  thinking: string;
  narrative: string;
}) {
  const thinkRef = React.useRef<HTMLDivElement>(null);
  const narrRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    const block = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
    };
    window.addEventListener("keydown", block, true);
    return () => window.removeEventListener("keydown", block, true);
  }, []);
  // 流式内容到达时自动滚到底
  React.useEffect(() => {
    if (thinkRef.current) thinkRef.current.scrollTop = thinkRef.current.scrollHeight;
  }, [thinking]);
  React.useEffect(() => {
    if (narrRef.current) narrRef.current.scrollTop = narrRef.current.scrollHeight;
  }, [narrative]);
  return (
    <div className="settlement-lock" role="alertdialog" aria-modal="true" aria-label="月末结算">
      <div className="settlement-lock-card">
        <Loader2 className="settlement-spin" size={28} />
        <h2>月末结算中</h2>
        <p>{stage === "数值推演结算" ? "档房核账中，钱粮、地方、军务落账，请稍候。" : stage ? `当前：${stage}` : "朝廷推演钱粮、地方、军务，请勿操作。"}</p>
        {thinking && (
          <div className="settlement-stream-block">
            <div className="settlement-stream-label">邸报房推敲</div>
            <div className="settlement-stream-text settlement-thinking" ref={thinkRef}>
              {thinking}
            </div>
          </div>
        )}
        {narrative && (
          <div className="settlement-stream-block">
            <div className="settlement-stream-label">月末奏章</div>
            <div className="settlement-stream-text settlement-narrative" ref={narrRef}>
              {narrative}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function MinisterPortrait({ primary, fallback, name }: { primary: string; fallback?: string; name: string }) {
  // 两级 fallback：primary（专属）→ fallback（pool 预设）→ 占位符
  const [stage, setStage] = React.useState<"primary" | "fallback" | "placeholder">(
    fallback ? "primary" : (primary ? "primary" : "placeholder")
  );
  React.useEffect(() => {
    setStage(primary ? "primary" : fallback ? "fallback" : "placeholder");
  }, [primary, fallback, name]);
  const src = stage === "primary" ? primary : stage === "fallback" ? (fallback ?? "") : "";
  if (stage === "placeholder") {
    return <div className="minister-card-portrait-placeholder">臣</div>;
  }
  return (
    <img
      className="minister-card-portrait"
      src={src}
      alt={name}
      onError={() => {
        if (stage === "primary" && fallback) setStage("fallback");
        else setStage("placeholder");
      }}
    />
  );
}

function PortraitMissingBadge({ minister }: { minister: Minister }) {
  if (minister.portrait_status === "pending") return <span className="portrait-missing-badge pending">画师绘制中</span>;
  if (minister.portrait_status === "error") return <span className="portrait-missing-badge error">重绘失败</span>;
  if (minister.portrait_available !== false) return null;
  return <span className="portrait-missing-badge">缺图</span>;
}

// 朝班两条透视线（百分比锚点，由用户拖定）
// 左列：韩爌(外) → 黄立极(内)；右列：张瑞图(外) → 施凤来(内)
const LEFT_ANCHOR  = { near: { px: 0.077, py: 0.532 }, far: { px: 0.377, py: 0.066 } };
const RIGHT_ANCHOR = { near: { px: 0.862, py: 0.532 }, far: { px: 0.558, py: 0.045 } };

// 每列槽位数
const COURT_SLOTS_PER_ROW = 10;

// 生成两列所有槽位坐标（百分比）
function courtSlots(): { px: number; py: number; side: "left" | "right"; slot: number }[] {
  const slots = [];
  for (let i = 0; i < COURT_SLOTS_PER_ROW; i++) {
    const t = i / (COURT_SLOTS_PER_ROW - 1);
    slots.push({
      px: LEFT_ANCHOR.near.px + t * (LEFT_ANCHOR.far.px - LEFT_ANCHOR.near.px),
      py: LEFT_ANCHOR.near.py + t * (LEFT_ANCHOR.far.py - LEFT_ANCHOR.near.py),
      side: "left" as const, slot: i,
    });
    slots.push({
      px: RIGHT_ANCHOR.near.px + t * (RIGHT_ANCHOR.far.px - RIGHT_ANCHOR.near.px),
      py: RIGHT_ANCHOR.near.py + t * (RIGHT_ANCHOR.far.py - RIGHT_ANCHOR.near.py),
      side: "right" as const, slot: i,
    });
  }
  return slots;
}

// 找最近槽位（已被占用的跳过，但允许同名覆盖）
function snapToSlot(px: number, py: number, occupied: Set<string>, selfKey: string): { px: number; py: number } {
  const slots = courtSlots();
  let best = null as { px: number; py: number } | null;
  let bestDist = Infinity;
  for (const s of slots) {
    const key = `${s.side}:${s.slot}`;
    if (occupied.has(key) && key !== selfKey) continue;
    const d = Math.hypot(s.px - px, s.py - py);
    if (d < bestDist) { bestDist = d; best = s; }
  }
  return best ?? { px, py };
}

// 默认坐标：从 near 开始，每人占一格，紧挨着排不留空
const COURT_SLOT_STEP = 1 / (COURT_SLOTS_PER_ROW - 1);  // 相邻槽间距（百分比t）

function defaultCourtPct(index: number, total: number): { px: number; py: number } {
  const leftCount = Math.ceil(total / 2);
  const isLeft = index < leftCount;
  const posInRow = isLeft ? index : index - leftCount;
  const anchor = isLeft ? LEFT_ANCHOR : RIGHT_ANCHOR;
  const t = posInRow * COURT_SLOT_STEP;  // 从槽0开始连续，不跳格
  return {
    px: anchor.near.px + t * (anchor.far.px - anchor.near.px),
    py: anchor.near.py + t * (anchor.far.py - anchor.near.py),
  };
}

// 坐标存百分比（0-1），持久化到服务端 db（按存档隔离）
async function loadCourtPos(): Promise<Record<string, { px: number; py: number }>> {
  try {
    const r = await fetch("/api/court_layout");
    if (!r.ok) return {};
    const d = await r.json();
    return JSON.parse(d.layout || "{}");
  } catch { return {}; }
}
function saveCourtPos(pos: Record<string, { px: number; py: number }>) {
  fetch("/api/court_layout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ layout: JSON.stringify(pos) }),
  }).catch(() => {});
}

function MinisterCardList({
  list,
  portraitPrefix,
  selectedMinister,
  emptyNote,
  onOpenChat,
  onUploadPortrait,
  onGeneratePortrait,
  courtMode = false,
}: {
  list: Minister[];
  portraitPrefix: string;
  selectedMinister: string;
  emptyNote: string;
  onOpenChat: (minister: Minister) => void;
  onUploadPortrait?: (ministerName: string, file: File) => Promise<void>;
  onGeneratePortrait?: (ministerName: string) => Promise<void>;
  courtMode?: boolean;
}) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [positions, setPositions] = React.useState<Record<string, { px: number; py: number }>>({});
  const savedPosRef = React.useRef<Record<string, { px: number; py: number }> | null>(null);
  const dragging = React.useRef<{ name: string; startMX: number; startMY: number; startPX: number; startPY: number } | null>(null);
  const didDrag = React.useRef(false);

  // 固定职位 → 固定槽位（由 office 文字推导：office 逗号分项里命中即占该槽）
  const FIXED_SLOTS: { role: string; side: "left" | "right"; slot: number }[] = [
    { role: "首辅",    side: "left",  slot: 0 },
    { role: "次辅",    side: "right", slot: 0 },
    { role: "吏部尚书", side: "left",  slot: 1 },
    { role: "户部尚书", side: "right", slot: 1 },
    { role: "礼部尚书", side: "left",  slot: 2 },
    { role: "兵部尚书", side: "right", slot: 2 },
    { role: "刑部尚书", side: "left",  slot: 3 },
    { role: "工部尚书", side: "right", slot: 3 },
  ];

  // 从 office 字符串推导固定席位：逗号切分，任一分项精确等于某固定职名即占该槽。
  // 南京XX尚书是留都缺，不占京职槽——精确匹配自然排除（分项是「南京兵部尚书」≠「兵部尚书」）。
  function roleFromOffice(office: string): string {
    const parts = (office || "").split(",").map((s) => s.trim());
    const fs = FIXED_SLOTS.find((f) => parts.includes(f.role));
    return fs ? fs.role : "";
  }

  function fixedSlotFor(role: string): { px: number; py: number } | null {
    if (!role) return null;
    const allSlots = courtSlots();
    const fs = FIXED_SLOTS.find((f) => f.role === role);
    if (!fs) return null;
    const s = allSlots.find((sl) => sl.side === fs.side && sl.slot === fs.slot);
    return s ? { px: s.px, py: s.py } : null;
  }

  // 拖动覆盖坐标只加载一次。list 变化只重排，不重 fetch。
  const listKey = list.map((m) => m.name).join("|");
  React.useEffect(() => {
    let cancelled = false;
    const arrange = (saved: Record<string, { px: number; py: number }>) => {
      if (cancelled) return;
      const allSlots = courtSlots();
      const next: Record<string, { px: number; py: number }> = {};
      const usedSlots = new Set<string>();

      list.forEach((m) => {
        const role = roleFromOffice(m.office || "");
        const fixed = fixedSlotFor(role);
        if (fixed) {
          next[m.name] = fixed;
          const fs = FIXED_SLOTS.find((f) => f.role === role);
          if (fs) usedSlots.add(`${fs.side}:${fs.slot}`);
        }
      });

      list.forEach((m) => {
        if (next[m.name]) return;
        if (saved[m.name]) {
          const cur = saved[m.name];
          let best = allSlots.find((s) => !usedSlots.has(`${s.side}:${s.slot}`)) ?? allSlots[0];
          let bestD = Infinity;
          for (const s of allSlots) {
            if (usedSlots.has(`${s.side}:${s.slot}`)) continue;
            const d = Math.hypot(s.px - cur.px, s.py - cur.py);
            if (d < bestD) { bestD = d; best = s; }
          }
          usedSlots.add(`${best.side}:${best.slot}`);
          next[m.name] = { px: best.px, py: best.py };
        } else {
          const slot = allSlots.find((s) => !usedSlots.has(`${s.side}:${s.slot}`));
          if (slot) {
            usedSlots.add(`${slot.side}:${slot.slot}`);
            next[m.name] = { px: slot.px, py: slot.py };
          } else {
            next[m.name] = { px: 0.5, py: 0.532 };
          }
        }
      });
      setPositions(next);
    };

    if (savedPosRef.current !== null) {
      arrange(savedPosRef.current);
    } else {
      loadCourtPos().then((saved) => {
        savedPosRef.current = saved;
        arrange(saved);
      });
    }
    return () => { cancelled = true; };
  }, [listKey]);

  const onMouseDown = (e: React.MouseEvent, name: string) => {
    if ((e.target as HTMLElement).closest(".portrait-upload-btn, .portrait-generate-btn")) return;
    e.preventDefault();
    const pos = positions[name] || { px: 0.5, py: 0.8 };
    dragging.current = { name, startMX: e.clientX, startMY: e.clientY, startPX: pos.px, startPY: pos.py };
    didDrag.current = false;

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const dx = ev.clientX - dragging.current.startMX;
      const dy = ev.clientY - dragging.current.startMY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) didDrag.current = true;
      const el = containerRef.current;
      if (!el) return;
      const { width, height } = el.getBoundingClientRect();
      // 拖动增量转百分比
      const npx = Math.max(0, Math.min(1, dragging.current.startPX + dx / width));
      const npy = Math.max(0, Math.min(1, dragging.current.startPY + dy / height));
      setPositions((prev) => {
        const next = { ...prev, [dragging.current!.name]: { px: npx, py: npy } };
        savedPosRef.current = next;
        saveCourtPos(next);
        return next;
      });
    };
    const onUp = () => {
      if (dragging.current && didDrag.current) {
        // 松手时吸附到最近槽位
        const dragName = dragging.current.name;
        setPositions((prev) => {
          const cur = prev[dragName];
          if (!cur) return prev;
          // 已占槽位（其他大臣）
          const occupied = new Set<string>();
          // 找吸附目标
          const snapped = snapToSlot(cur.px, cur.py, occupied, "");
          const next = { ...prev, [dragName]: snapped };
          savedPosRef.current = next;
          saveCourtPos(next);
          return next;
        });
      }
      dragging.current = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  if (!list.length) return <div className={courtMode ? "minister-list minister-list-court" : "minister-list"}><div className="empty-note">{emptyNote}</div></div>;

  // 非朝班模式（全部tab）：普通网格
  if (!courtMode) {
    return (
      <div className="minister-list">
        {list.map((minister) => {
          const { primary: dedicated, fallback: poolFallback } = portraitSources(minister, portraitPrefix);
          const ousted = minister.status !== "active";
          return (
            <div key={minister.name}
              role="button"
              tabIndex={0}
              className={`minister-card ${selectedMinister === minister.name ? "selected" : ""} ${ousted ? "ousted" : ""}`}
              onClick={() => onOpenChat(minister)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onOpenChat(minister);
                }
              }}
            >
              <div className="minister-card-portrait-wrap">
                <MinisterPortrait primary={dedicated} fallback={poolFallback} name={minister.name} />
                <PortraitMissingBadge minister={minister} />
                {onUploadPortrait && <PortraitUploadButton ministerName={minister.name} onUpload={onUploadPortrait} />}
                {onGeneratePortrait && <PortraitGenerateButton minister={minister} onGenerate={onGeneratePortrait} />}
              </div>
              <div className="minister-card-info">
                <div className="minister-card-top">
                  <span className="minister-name">{minister.name}</span>
                  {ousted && <span className={`minister-status status-${minister.status}`}>{minister.status_label}</span>}
                  {minister.office && <span className="minister-office">{minister.office}</span>}
                </div>
                <span className="minister-bio">{minister.summary}</span>
              </div>
              {minister.favorite && <Star className="favorite-mark" size={13} />}
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div className="minister-list minister-list-court" ref={containerRef}>
      {list.map((minister) => {
        const { primary: dedicated, fallback: poolFallback } = portraitSources(minister, portraitPrefix);
        const ousted = minister.status !== "active";
        const pct = positions[minister.name];
        // 透视缩放：py=0最远最小，py=1最近最大
        const perspScale = pct ? 0.38 + 0.62 * pct.py : 1;
        // 卡片宽用 vh 单位（CSS），这里只控制 scale
        return (
          <div
            key={minister.name}
            role="button"
            tabIndex={0}
            className={`minister-card ${selectedMinister === minister.name ? "selected" : ""} ${ousted ? "ousted" : ""}`}
            style={pct ? {
              position: "absolute",
              left: `${pct.px * 100}%`,
              top: `${pct.py * 100}%`,
              cursor: "grab",
              transform: `scale(${perspScale.toFixed(3)})`,
              transformOrigin: "bottom center",
              zIndex: Math.round(pct.py * 1000),
            } : { visibility: "hidden" }}
            onMouseDown={(e) => onMouseDown(e, minister.name)}
            onClick={(e) => { if (didDrag.current) { e.preventDefault(); return; } onOpenChat(minister); }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onOpenChat(minister);
              }
            }}
          >
            <div className="minister-card-portrait-wrap">
              <MinisterPortrait primary={dedicated} fallback={poolFallback} name={minister.name} />
              <PortraitMissingBadge minister={minister} />
              {onUploadPortrait && (
                <PortraitUploadButton ministerName={minister.name} onUpload={onUploadPortrait} />
              )}
              {onGeneratePortrait && <PortraitGenerateButton minister={minister} onGenerate={onGeneratePortrait} />}
            </div>
            <div className="minister-card-info">
              <div className="minister-card-top">
                <span className="minister-name">{minister.name}</span>
                {ousted && <span className={`minister-status status-${minister.status}`}>{minister.status_label}</span>}
                {minister.office && <span className="minister-office">{minister.office}</span>}
              </div>
              <span className="minister-bio">{minister.summary}</span>
            </div>
            {minister.favorite && <Star className="favorite-mark" size={13} />}
          </div>
        );
      })}
    </div>
  );
}

// 自定义立绘文件名固定（一人一图），故按 portrait_id 之外另用上传时间戳刷缓存。
const _portraitBust: Record<string, number> = {};
function cacheBust(key: string): number {
  if (!_portraitBust[key]) _portraitBust[key] = Date.now();
  return _portraitBust[key];
}

function portraitSources(minister: Minister, portraitPrefix: string) {
  const portraitId = minister.portrait_id || "";
  if (portraitId.startsWith("generated:")) {
    const assetId = portraitId.slice("generated:".length);
    return {
      primary: `/portraits/generated/${encodeURIComponent(assetId)}.png?t=${cacheBust(portraitId)}`,
      fallback: `/portraits/${portraitPrefix}${minister.id ?? minister.name}.png`,
    };
  }
  if (portraitId.startsWith("custom:")) {
    return {
      primary: `/portraits/custom/${encodeURIComponent(minister.name)}?t=${cacheBust(portraitId)}`,
      fallback: undefined,
    };
  }
  return {
    primary: `/portraits/${portraitPrefix}${minister.id ?? minister.name}.png`,
    fallback: portraitId ? `/portraits/${portraitId}.png` : undefined,
  };
}

function PortraitUploadButton({
  ministerName,
  onUpload,
}: {
  ministerName: string;
  onUpload: (ministerName: string, file: File) => Promise<void>;
}) {
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [busy, setBusy] = React.useState(false);
  return (
    <>
      <button
        type="button"
        className="portrait-upload-btn"
        title="上传立绘"
        disabled={busy}
        onClick={(e) => {
          e.stopPropagation();  // 别触发卡片的召见
          inputRef.current?.click();
        }}
      >
        <Upload size={13} />
      </button>
      <input
        ref={inputRef}
        type="file"
        aria-label={`上传${ministerName}立绘文件`}
        tabIndex={-1}
        accept="image/png,image/jpeg,image/webp"
        style={{ display: "none" }}
        onClick={(e) => e.stopPropagation()}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";  // 允许重选同一文件
          if (!file) return;
          setBusy(true);
          try {
            // 立即刷该人物缓存键，loadState 回来后新图不被旧缓存挡住。
            _portraitBust[`custom:${ministerName}`] = Date.now();
            await onUpload(ministerName, file);
          } catch (err) {
            window.alert(`上传失败：${(err as Error).message}`);
          } finally {
            setBusy(false);
          }
        }}
      />
    </>
  );
}

function PortraitGenerateButton({
  minister,
  onGenerate,
}: {
  minister: Minister;
  onGenerate: (ministerName: string) => Promise<void>;
}) {
  const [busy, setBusy] = React.useState(false);
  const pending = minister.portrait_status === "pending";
  return (
    <button
      type="button"
      className="portrait-generate-btn"
      aria-label={pending ? `${minister.name}立绘绘制中` : `为${minister.name}重绘立绘`}
      title={pending ? "画师绘制中" : "画师重绘"}
      disabled={busy || pending}
      onClick={async (e) => {
        e.stopPropagation();
        setBusy(true);
        try {
          await onGenerate(minister.name);
        } catch (err) {
          window.alert(`重绘失败：${(err as Error).message}`);
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy || pending ? <Loader2 size={13} className="spin-icon" /> : <Paintbrush size={13} />}
    </button>
  );
}

function RightNavBar({
  onToggleCourt,
  onToggleHarem,
  onToggleArmy,
  onToggleRegion,
  onToggleBuilding,
  onToggleEconomy,
  onToggleAppointment,
  onToggleOrganization,
  onOpenLongGoals,
  activeDrawer,
}: {
  onToggleCourt: () => void;
  onToggleHarem: () => void;
  onToggleArmy: () => void;
  onToggleRegion: () => void;
  onToggleBuilding: () => void;
  onToggleEconomy: () => void;
  onToggleAppointment: () => void;
  onToggleOrganization: () => void;
  onOpenLongGoals: () => void;
  activeDrawer: DrawerName;
}) {
  const items = [
    { key: "court", label: "政", short: "朝堂", title: "朝堂·召见大臣", onClick: onToggleCourt },
    { key: "harem", label: "内", short: "后宫", title: "后宫", onClick: onToggleHarem },
    { key: "army", label: "兵", short: "军队", title: "军队列表", onClick: onToggleArmy },
    { key: "region", label: "省", short: "省份", title: "省份列表", onClick: onToggleRegion },
    { key: "building", label: "工", short: "建筑", title: "建筑列表", onClick: onToggleBuilding },
    { key: "economy", label: "户", short: "经济", title: "经济面板", onClick: onToggleEconomy },
    { key: "appointment", label: "吏", short: "吏部", title: "吏部考核", onClick: onToggleAppointment },
    { key: "organization", label: "制", short: "组织", title: "组织架构", onClick: onToggleOrganization },
  ];
  return (
    <nav className="right-nav-bar" aria-label="六部入口">
      {items.map((item) => (
        <button
          key={item.key}
          className={`right-nav-btn${activeDrawer === item.key ? " active" : ""}`}
          title={item.title}
          aria-label={item.title}
          aria-expanded={activeDrawer === item.key}
          onClick={item.onClick}
        >
          <span className="right-nav-glyph">{item.label}</span>
          <span className="right-nav-label">{item.short}</span>
        </button>
      ))}
      <button
        className="right-nav-btn right-nav-btn-goal"
        title="长期目标"
        aria-label="大明长期目标"
        onClick={onOpenLongGoals}
      >
        <span className="right-nav-glyph">目</span>
        <span className="right-nav-label">目标</span>
      </button>
    </nav>
  );
}

function useDrawerFocus(open: boolean) {
  const drawerRef = React.useRef<HTMLElement | null>(null);
  const closeRef = React.useRef<HTMLButtonElement | null>(null);
  const previousFocusRef = React.useRef<HTMLElement | null>(null);

  React.useEffect(() => {
    const drawer = drawerRef.current;
    if (!drawer) return;

    if (!open) {
      drawer.setAttribute("inert", "");
      return;
    }

    drawer.removeAttribute("inert");
    const activeElement = document.activeElement;
    previousFocusRef.current = activeElement instanceof HTMLElement ? activeElement : null;

    const focusTimer = window.setTimeout(() => {
      const current = document.activeElement;
      if (current instanceof HTMLElement && drawer.contains(current)) return;
      closeRef.current?.focus();
    }, 0);

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        drawer.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => (
        element.getAttribute("aria-hidden") !== "true"
        && (element.offsetWidth > 0 || element.offsetHeight > 0 || element.getClientRects().length > 0)
      ));

      if (!focusable.length) {
        event.preventDefault();
        closeRef.current?.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      if (event.shiftKey && (!(active instanceof HTMLElement) || active === first || !drawer.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", handleKeyDown);
      const previousFocus = previousFocusRef.current;
      if (previousFocus && document.contains(previousFocus)) {
        window.setTimeout(() => previousFocus.focus(), 0);
      }
    };
  }, [open]);

  return { drawerRef, closeRef };
}

function RightDrawer({
  open,
  onClose,
  title,
  icon,
  children,
  extraClass,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  extraClass?: string;
}) {
  const titleId = React.useId();
  const { drawerRef, closeRef } = useDrawerFocus(open);

  return (
    <>
      {open && <button className="drawer-scrim" aria-label="收起" onClick={onClose} />}
      <aside
        ref={drawerRef}
        className={`right-drawer ${extraClass || ""} ${open ? "open" : ""}`}
        role="dialog"
        aria-modal={open ? true : undefined}
        aria-labelledby={titleId}
        aria-hidden={!open}
        inert={!open ? true : undefined}
      >
        <div className="right-drawer-brand">
          <div className="panel-title">
            {icon}
            <span id={titleId}>{title}</span>
          </div>
          <button ref={closeRef} className="icon-button" aria-label="收起" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="right-drawer-body">
          {children}
        </div>
      </aside>
    </>
  );
}

function ArmyDrawer({
  armies,
  open,
  selectedArmyId,
  onSelectArmy,
  onClose,
}: {
  armies: Army[];
  open: boolean;
  selectedArmyId: string;
  onSelectArmy: (id: string) => void;
  onClose: () => void;
}) {
  const [q, setQ] = React.useState("");
  const mingArmies = armies.filter((a) => (a.owner_power || "ming") === "ming");
  const filtered = q ? mingArmies.filter((a) => a.name.includes(q) || a.station.includes(q) || a.commander.includes(q)) : mingArmies;
  const selected = mingArmies.find((a) => a.id === selectedArmyId) || null;
  const arrearsTone = (army: Army) => {
    const maint = army.maintenance_per_turn || 1;
    const months = army.arrears / maint;
    if (months >= 3) return "danger";
    if (months >= 1) return "warn";
    return "";
  };
  return (
    <RightDrawer open={open} onClose={onClose} title="军队" icon={<Swords size={17} />} extraClass="right-drawer-army">
      <div className="right-drawer-search">
        <input className="right-drawer-search-input" aria-label="搜索番号、驻地或统帅" placeholder="搜索番号/驻地/统帅…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="right-drawer-list">
        {filtered.map((army) => (
          <button
            key={army.id}
            className={`right-drawer-row${selectedArmyId === army.id ? " selected" : ""} ${arrearsTone(army)}`}
            onClick={() => onSelectArmy(army.id === selectedArmyId ? "" : army.id)}
          >
            <span className="right-drawer-row-name">{army.name}</span>
            <span className="right-drawer-row-meta">
              {army.manpower}兵 · {army.station}
            </span>
          </button>
        ))}
        {!filtered.length && <div className="empty-note">{q ? "无匹配结果。" : "暂无大明军队记录。"}</div>}
      </div>
      {selected && (
        <div className="right-drawer-detail">
          <div className="right-drawer-detail-title">
            {selected.name}
            <button className="right-drawer-detail-close" onClick={() => onSelectArmy("")} aria-label="关闭详情"><X size={14} /></button>
          </div>
          <table className="intel-table">
            <tbody>
              <tr><th>驻地</th><td>{selected.station}</td><th>战区</th><td>{selected.theater}</td></tr>
              <tr><th>统帅</th><td>{selected.commander || "—"}</td><th>兵种</th><td>{selected.troop_type}</td></tr>
              <tr><th>兵力</th><td>{selected.manpower}</td><th>月饷</th><td>{selected.maintenance_per_turn}万</td></tr>
              <tr><th>士气</th><td>{selected.morale}</td><th>操练</th><td>{selected.training}</td></tr>
              <tr><th>军械</th><td>{selected.equipment}</td><th>补给</th><td>{selected.supply}</td></tr>
              <tr><th>机动</th><td>{selected.mobility}</td><th>忠诚</th><td>{selected.loyalty}</td></tr>
              <tr><th>欠饷</th><td colSpan={3}>
                {selected.arrears > 0
                  ? `${selected.arrears}万两（≈${(selected.arrears / (selected.maintenance_per_turn || 1)).toFixed(1)}月）`
                  : "无欠饷"}
              </td></tr>
              <tr><th>状态</th><td colSpan={3}>{selected.status}</td></tr>
            </tbody>
          </table>
        </div>
      )}
    </RightDrawer>
  );
}

function RegionDrawer({
  regions,
  open,
  selectedRegionId,
  onSelectRegion,
  onClose,
}: {
  regions: Region[];
  open: boolean;
  selectedRegionId: string;
  onSelectRegion: (id: string) => void;
  onClose: () => void;
}) {
  const [q, setQ] = React.useState("");
  const mingRegions = regions.filter((r) => (r.controlled_by || "ming") === "ming");
  const filtered = q ? mingRegions.filter((r) => r.name.includes(q)) : mingRegions;
  const selected = mingRegions.find((r) => r.id === selectedRegionId) || null;
  const regionTone = (r: Region) => {
    if (r.unrest >= 70) return "danger";
    if (r.unrest >= 45) return "warn";
    return "";
  };
  return (
    <RightDrawer open={open} onClose={onClose} title="省份" icon={<MapPinned size={17} />} extraClass="right-drawer-region">
      <div className="right-drawer-search">
        <input className="right-drawer-search-input" aria-label="搜索省份名" placeholder="搜索省份名…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="right-drawer-list">
        {filtered.map((r) => (
          <button
            key={r.id}
            className={`right-drawer-row${selectedRegionId === r.id ? " selected" : ""} ${regionTone(r)}`}
            onClick={() => onSelectRegion(r.id === selectedRegionId ? "" : r.id)}
          >
            <span className="right-drawer-row-name">{r.name}</span>
            <span className="right-drawer-row-meta">
              动乱{r.unrest} · 月税{r.tax_per_turn}万
            </span>
          </button>
        ))}
        {!filtered.length && <div className="empty-note">{q ? "无匹配结果。" : "暂无大明省份记录。"}</div>}
      </div>
      {selected && (
        <div className="right-drawer-detail">
          <div className="right-drawer-detail-title">
            {selected.name}
            <button className="right-drawer-detail-close" onClick={() => onSelectRegion("")} aria-label="关闭详情"><X size={14} /></button>
          </div>
          <table className="intel-table">
            <tbody>
              <tr><th>人口</th><td>{selected.population}万</td><th>田亩</th><td>{selected.registered_land}万亩</td></tr>
              <tr><th>民心</th><td>{selected.public_support}</td><th>动乱</th><td>{selected.unrest}</td></tr>
              <tr><th>粮食</th><td>{selected.grain_security}</td><th>月税</th><td>{selected.tax_per_turn}万</td></tr>
              <tr><th>士绅阻力</th><td>{selected.gentry_resistance}</td><th>边防压力</th><td>{selected.military_pressure}</td></tr>
              <tr><th>天灾</th><td colSpan={3}>{selected.natural_disaster}</td></tr>
              <tr><th>人祸</th><td colSpan={3}>{selected.human_disaster}</td></tr>
              <tr><th>状况</th><td colSpan={3}>{selected.status}</td></tr>
            </tbody>
          </table>
        </div>
      )}
    </RightDrawer>
  );
}

function BuildingDrawer({
  regions,
  mapNodes,
  open,
  onClose,
}: {
  regions: Region[];
  mapNodes: MapNode[];
  open: boolean;
  onClose: () => void;
}) {
  const allBuildings: (Building & { regionName: string })[] = [];
  for (const node of mapNodes) {
    if (!node.buildings) continue;
    const regionName = node.region?.name || node.label || node.id;
    for (const b of node.buildings) {
      allBuildings.push({ ...b, regionName });
    }
  }
  const [filterRegion, setFilterRegion] = React.useState("");
  const [q, setQ] = React.useState("");
  const regionNames = Array.from(new Set(allBuildings.map((b) => b.regionName)));
  const filtered = allBuildings
    .filter((b) => !filterRegion || b.regionName === filterRegion)
    .filter((b) => !q || b.name.includes(q) || b.category.includes(q));
  return (
    <RightDrawer open={open} onClose={onClose} title="建筑" icon={<Landmark size={17} />} extraClass="right-drawer-building">
      <div className="right-drawer-search">
        <input className="right-drawer-search-input" aria-label="搜索建筑名或类别" placeholder="搜索建筑名/类别…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="right-drawer-filter">
        <select
          value={filterRegion}
          onChange={(e) => setFilterRegion(e.target.value)}
          className="right-drawer-select"
        >
          <option value="">全部省份</option>
          {regionNames.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </div>
      <div className="right-drawer-list">
        {filtered.map((b) => (
          <div key={b.id} className="right-drawer-row right-drawer-row-building">
            <span className="right-drawer-row-name">{b.name}</span>
            <span className="right-drawer-row-meta">{b.regionName} · {b.category} Lv{b.level}</span>
            <span className="right-drawer-row-sub">
              完好{b.condition} · 维护{b.maintenance}万/月
              {b.output_metric ? ` · ${b.output_metric}+${b.output_amount}` : ""}
            </span>
          </div>
        ))}
        {!filtered.length && <div className="empty-note">{q || filterRegion ? "无匹配结果。" : "暂无建筑记录。"}</div>}
      </div>
    </RightDrawer>
  );
}

function EconomyDrawer({
  state,
  open,
  onClose,
}: {
  state: GameState;
  open: boolean;
  onClose: () => void;
}) {
  const [tab, setTab] = React.useState<"国库" | "内库">("国库");
  const [q, setQ] = React.useState("");
  const budget = state.budget[tab];
  const matchItem = (name: string) => !q || name.includes(q);
  return (
    <RightDrawer open={open} onClose={onClose} title="经济" icon={<ScrollText size={17} />} extraClass="right-drawer-economy">
      <div className="segmented right-drawer-segmented">
        {(["国库", "内库"] as const).map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <div className="right-drawer-search">
        <input className="right-drawer-search-input" aria-label="搜索收支项" placeholder="搜索收支项…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="right-drawer-economy-summary">
        <span>余额 <b>{formatMoney(budget.balance)}</b></span>
        <span className={budget.net >= 0 ? "income" : "expense"}>
          月净 <b>{formatSignedMoney(budget.net)}</b>
        </span>
      </div>
      <div className="right-drawer-list">
        <div className="right-drawer-section-title">固定收入</div>
        {budget.income.filter((item) => matchItem(item.name)).map((item) => (
          <div key={`in-${item.name}`} className="right-drawer-budget-row">
            <span>{item.name}</span>
            <b className="income">+{formatMoney(item.amount)}</b>
          </div>
        ))}
        <div className="right-drawer-section-title">固定支出</div>
        {budget.expense.filter((item) => matchItem(item.name)).map((item) => (
          <div key={`ex-${item.name}`} className="right-drawer-budget-row">
            <span>{item.name}</span>
            <b className="expense">-{formatMoney(item.amount)}</b>
          </div>
        ))}
        {budget.movements.filter((m) => matchItem(m.category || m.reason)).length > 0 && (
          <>
            <div className="right-drawer-section-title">本月一次性入账</div>
            {budget.movements.filter((m) => matchItem(m.category || m.reason)).map((m, i) => (
              <div key={`mv-${i}`} className="right-drawer-budget-row">
                <span>{m.category || m.reason}</span>
                <b className={m.delta >= 0 ? "income" : "expense"}>{formatSignedMoney(m.delta)}</b>
              </div>
            ))}
          </>
        )}
      </div>
    </RightDrawer>
  );
}

function AppointmentDrawer({
  ministers,
  characterIndex,
  agreements,
  open,
  onOpenChat,
  onRecruit,
  onCastrate,
  onEmancipate,
  onClose,
}: {
  ministers: Minister[];
  characterIndex: CharacterIndexEntry[];
  agreements: Agreement[];
  open: boolean;
  onOpenChat: (minister: Minister, prefill?: string) => void;
  onRecruit: (action: "exam" | "eunuch" | "recommend") => Promise<string>;
  onCastrate: (name: string, force?: boolean) => Promise<string>;
  onEmancipate: (name: string, force?: boolean) => Promise<string>;
  onClose: () => void;
}) {
  const [q, setQ] = React.useState("");
  const [notice, setNotice] = React.useState("");
  const [actionBusy, setActionBusy] = React.useState("");
  const [selectedName, setSelectedName] = React.useState("");
  const [scope, setScope] = React.useState("全部");
  const [detail, setDetail] = React.useState<Minister | null>(null);
  const [detailCache, setDetailCache] = React.useState<Record<string, { signature: string; detail: Minister }>>({});
  const [loadingDetail, setLoadingDetail] = React.useState("");
  const [detailError, setDetailError] = React.useState("");
  const offices = ["内阁", "吏部", "户部", "礼部", "兵部", "刑部", "工部"];
  const bureauScopes = ["全部", "在职", "内阁六部", "边镇厂卫", "待铨外缘", "在野", "缺图"];
  const mingMinisters = ministers.filter((m) => (m.power_id || "ming") === "ming");
  const activeMinisters = mingMinisters.filter((m) => m.status === "active");
  const hiddenCount = mingMinisters.filter((m) => m.status === "offstage").length;
  const fieldCount = mingMinisters.filter((m) => ["dismissed", "exiled", "retired"].includes(m.status)).length;
  const imprisonedCount = mingMinisters.filter((m) => m.status === "imprisoned").length;
  const totalArchive = characterIndex.length || ministers.length;
  const archiveMing = characterIndex.filter((m) => (m.power_id || "ming") === "ming");
  const archiveExternal = characterIndex.filter((m) => (m.power_id || "ming") !== "ming").length;
  const archiveHarem = characterIndex.filter((m) => m.office_type === "后宫").length;
  const archiveManaged = archiveMing.filter((m) => m.office_type !== "后宫").length || mingMinisters.length;
  const filterHit = (m: Minister) => !q || m.name.includes(q) || (m.office || "").includes(q) || (m.office_type || "").includes(q) || (m.faction || "").includes(q) || (m.age_label || "").includes(q);
  const scopedHit = (m: Minister) => {
    if (scope === "在职") return m.status === "active";
    if (scope === "内阁六部") return offices.some((office) => (m.office_type || "").includes(office));
    if (scope === "边镇厂卫") return /边镇|锦衣卫|东厂|司礼监|兵部/.test(`${m.office_type}${m.office}`);
    if (scope === "待铨外缘") return /待铨|外臣|地方|未仕|翰林/.test(`${m.office_type}${m.office}`);
    if (scope === "在野") return ["dismissed", "exiled", "retired", "offstage"].includes(m.status);
    if (scope === "缺图") return m.portrait_available === false;
    return true;
  };
  const filteredMinisters = mingMinisters.filter((m) => filterHit(m) && scopedHit(m));
  React.useEffect(() => {
    if (selectedName && mingMinisters.some((m) => m.name === selectedName)) return;
    const preferred = activeMinisters[0]?.name || mingMinisters[0]?.name || "";
    setSelectedName(preferred);
  }, [selectedName, mingMinisters, activeMinisters]);
  React.useEffect(() => {
    if (!filteredMinisters.length) return;
    if (selectedName && filteredMinisters.some((m) => m.name === selectedName)) return;
    setSelectedName(filteredMinisters[0].name);
  }, [selectedName, filteredMinisters]);
  const selectedOfficer = mingMinisters.find((m) => m.name === selectedName) || filteredMinisters[0] || null;
  const selectedSignature = selectedOfficer
    ? [selectedOfficer.office, selectedOfficer.office_type, selectedOfficer.faction, selectedOfficer.status, selectedOfficer.power_id, selectedOfficer.portrait_available ? "1" : "0"].join("|")
    : "";
  React.useEffect(() => {
    if (!selectedOfficer) {
      setDetail(null);
      setLoadingDetail("");
      return;
    }
    const cached = detailCache[selectedOfficer.name];
    if (cached && cached.signature === selectedSignature) {
      setDetail(cached.detail);
      setLoadingDetail("");
      setDetailError("");
      return;
    }
    let cancelled = false;
    setLoadingDetail(selectedOfficer.name);
    setDetailError("");
    api<{ character: Minister }>(`/api/characters/${encodeURIComponent(selectedOfficer.name)}`)
      .then((data) => {
        if (cancelled) return;
        setDetail(data.character);
        setDetailCache((current) => ({
          ...current,
          [selectedOfficer.name]: { signature: selectedSignature, detail: data.character },
        }));
      })
      .catch((err) => {
        if (cancelled) return;
        setDetail(null);
        setDetailError(formatApiError(err, "调阅考核档失败"));
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail("");
      });
    return () => {
      cancelled = true;
    };
  }, [selectedOfficer?.name, selectedSignature, detailCache]);
  const identityText = (m?: Minister | null) => `${m?.office || ""} ${m?.office_type || ""} ${m?.faction || ""}`;
  const isEunuchIdentity = (m?: Minister | null) => /司礼监|东厂|太监|宦官|内官|内廷|秉笔|掌印|随堂/.test(identityText(m));
  const isCommonerIdentity = (m?: Minister | null) => /民籍|百姓|布衣|还民|脱籍/.test(identityText(m));
  const isCivilOrMilitaryOfficial = (m?: Minister | null) => {
    const text = identityText(m);
    if (/民籍|百姓|布衣|江湖|商人|隐士|传教士|后宫|流寇|后金|蒙古|朝鲜/.test(text)) return false;
    return /内阁|吏部|户部|礼部|兵部|刑部|工部|都察院|翰林|地方|边镇|锦衣卫|待铨|官|将|督|抚|御史|尚书|侍郎|郎中|主事|总兵|千户|百户/.test(text);
  };
  const selectedDetail = detail?.name === selectedOfficer?.name ? detail : null;
  const canUseIdentityAction = !!selectedOfficer && selectedOfficer.status === "active" && (selectedOfficer.power_id || "ming") === "ming" && selectedOfficer.office_type !== "后宫";
  const canCastrateSelected = canUseIdentityAction && isCivilOrMilitaryOfficial(selectedOfficer) && !isEunuchIdentity(selectedOfficer) && !isCommonerIdentity(selectedOfficer);
  const canEmancipateSelected = canUseIdentityAction && isEunuchIdentity(selectedOfficer);
  const selectedCastrate = canCastrateSelected ? (selectedDetail || selectedOfficer) : null;
  const selectedEmancipate = canEmancipateSelected ? (selectedDetail || selectedOfficer) : null;
  const castrationNote = selectedCastrate?.stance_notes?.find((note) => (
    /净身|入宫|内廷|司礼监|太监|宦官/.test(`${note.topic}${note.summary}${note.conditions}`)
  ));
  const castrationAgreement = agreements.find((agreement) => (
    agreement.minister_name === (selectedCastrate?.name || "")
    && agreement.action_kind === "castration"
    && ["sealed", "fulfilled", "pending", "blocked", "failed"].includes(agreement.status)
  ));
  const hasCastrationConsent = (
    castrationNote?.handshake_status === "sealed"
    || castrationAgreement?.target_status === "achieved"
    || (!castrationAgreement?.target_status && ["sealed", "fulfilled"].includes(castrationAgreement?.status || ""))
  );
  const castrationBlocked = castrationNote?.handshake_status === "blocked" || ["blocked", "failed"].includes(castrationAgreement?.target_status || castrationAgreement?.status || "");
  const castrationConditional = castrationNote?.handshake_status === "conditional" || castrationAgreement?.target_status === "pending_conditions" || castrationAgreement?.status === "pending";
  const castrationTasks = castrationAgreement?.tasks?.filter((task) => task.status !== "done") || [];
  const emancipationNote = selectedEmancipate?.stance_notes?.find((note) => (
    /奴籍|民籍|脱籍|还民|转为民|转民籍|出宫为民|归为百姓|赐还为民/.test(`${note.topic}${note.summary}${note.conditions}`)
  ));
  const emancipationAgreement = agreements.find((agreement) => (
    agreement.minister_name === (selectedEmancipate?.name || "")
    && agreement.action_kind === "emancipation"
    && ["sealed", "fulfilled", "pending", "blocked", "failed"].includes(agreement.status)
  ));
  const hasEmancipationConsent = (
    emancipationNote?.handshake_status === "sealed"
    || emancipationAgreement?.target_status === "achieved"
    || (!emancipationAgreement?.target_status && ["sealed", "fulfilled"].includes(emancipationAgreement?.status || ""))
  );
  const emancipationBlocked = emancipationNote?.handshake_status === "blocked" || ["blocked", "failed"].includes(emancipationAgreement?.target_status || emancipationAgreement?.status || "");
  const emancipationConditional = emancipationNote?.handshake_status === "conditional" || emancipationAgreement?.target_status === "pending_conditions" || emancipationAgreement?.status === "pending";
  const emancipationTasks = emancipationAgreement?.tasks?.filter((task) => task.status !== "done") || [];
  const selectedAgreements = agreements.filter((agreement) => (
    agreement.minister_name === (selectedOfficer?.name || "")
    && ["pending", "sealed", "fulfilled", "blocked", "failed"].includes(agreement.status)
  )).slice(0, 5);
  const castrationPrompt = selectedCastrate
    ? `朕欲问卿一件极重之事：若令卿净身入宫，转入司礼监，为朕近侍家奴，专司密奏、传旨与催办，以制衡外朝诸派，卿是否自愿？若愿，须明言愿入内廷；若有条件，也请当面奏明。`
    : "";
  const emancipationPrompt = selectedEmancipate
    ? `朕念汝久在内廷，身属奴籍，不得自择去留。若今日赐汝脱离内廷奴籍，转为民籍百姓，着布衣头巾，出宫自立，汝是否自愿？若愿，须明言愿脱籍还民；若有条件，也请当面奏明。`
    : "";
  const runRecruit = async (action: "exam" | "eunuch" | "recommend") => {
    if (actionBusy) return;
    setActionBusy(action);
    setNotice("");
    try {
      setNotice(await onRecruit(action));
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setActionBusy("");
    }
  };
  const runPersuadeCastration = () => {
    if (!selectedCastrate || actionBusy) return;
    onClose();
    onOpenChat(selectedCastrate, castrationPrompt);
  };
  const runCastrate = async (force = false) => {
    if (!selectedCastrate || actionBusy) return;
    if (force) {
      const ok = window.confirm(`确定强旨令${selectedCastrate.name}净身入内廷？这会绕过本人同意，外朝会视为重罚与奇辱。`);
      if (!ok) return;
    }
    setActionBusy("castrate");
    setNotice("");
    try {
      setNotice(await onCastrate(selectedCastrate.name, force));
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setActionBusy("");
    }
  };
  const runPersuadeEmancipation = () => {
    if (!selectedEmancipate || actionBusy) return;
    onClose();
    onOpenChat(selectedEmancipate, emancipationPrompt);
  };
  const runEmancipate = async (force = false) => {
    if (!selectedEmancipate || actionBusy) return;
    if (force) {
      const ok = window.confirm(`确定下旨令${selectedEmancipate.name}脱离内廷奴籍、转为民籍百姓？`);
      if (!ok) return;
    }
    setActionBusy("emancipate");
    setNotice("");
    try {
      setNotice(await onEmancipate(selectedEmancipate.name, force));
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setActionBusy("");
    }
  };
  const ageText = (item?: Pick<Minister, "age_label" | "start_age"> | null) => item?.age_label || (item?.start_age ? `开局${item.start_age}岁` : "开局年龄未详");
  const detailCharacter = selectedDetail || selectedOfficer;
  const recentStanceCount = detailCharacter?.stance_notes?.length || 0;
  const pendingAgreementCount = selectedAgreements.filter((agreement) => agreement.target_status === "pending_conditions" || agreement.status === "pending").length;
  const readyAgreementCount = selectedAgreements.filter((agreement) => agreement.target_status === "achieved" || (!agreement.target_status && ["sealed", "fulfilled"].includes(agreement.status))).length;
  const agreementStatusLabel: Record<Agreement["status"], string> = {
    sealed: "已成约",
    pending: "待审计",
    blocked: "未说服",
    fulfilled: "已兑现",
    failed: "已失信",
  };
  const taskStatusLabel: Record<AgreementTask["status"], string> = {
    pending: "待证",
    done: "已证",
    failed: "失信",
  };
  const conditionStatusLabel: Record<string, string> = {
    pending: "条件待证",
    satisfied: "条件已足",
    failed: "条件失败",
  };
  const targetStatusLabel: Record<string, string> = {
    pending_conditions: "标的未成",
    achieved: "标的达成",
    failed: "标的失败",
    blocked: "未说服",
  };
  const canChatSelected = !!selectedOfficer && selectedOfficer.status === "active" && (selectedOfficer.power_id || "ming") === "ming";
  return (
    <RightDrawer open={open} onClose={onClose} title="吏部考核" icon={<Star size={17} />} extraClass="right-drawer-appointment">
      <div className="bureau-audit-shell">
        <section className="bureau-summary">
          <div className="bureau-summary-title">
            <span>铨选簿</span>
            <b>{archiveManaged} / {totalArchive}</b>
          </div>
          <div className="bureau-count-strip">
            <span>吏部管辖 <b>{archiveManaged}</b></span>
            <span>在职 <b>{activeMinisters.length}</b></span>
            <span>未登场 <b>{hiddenCount}</b></span>
            <span>在野 <b>{fieldCount}</b></span>
            <span>下狱 <b>{imprisonedCount}</b></span>
            <span>外部 <b>{archiveExternal}</b></span>
            <span>后宫 <b>{archiveHarem}</b></span>
          </div>
          <p>总录含大明、后金、流寇、蒙古、朝鲜与后宫；本台只处理大明非后宫人物，所以开局看到 77 名不是丢人，是吏部管理口径。</p>
        </section>

        <section className="bureau-recruit-row">
          <button onClick={() => runRecruit("exam")} disabled={!!actionBusy}>科举取士</button>
          <button onClick={() => runRecruit("recommend")} disabled={!!actionBusy}>举贤发现</button>
          <button onClick={() => runRecruit("eunuch")} disabled={!!actionBusy}>内廷募入</button>
          {notice && <div className="drawer-action-notice">{notice}</div>}
        </section>

        <section className="bureau-main-grid">
          <div className="bureau-roster-panel">
            <div className="right-drawer-search">
              <input className="right-drawer-search-input" aria-label="检索姓名、职位、派系或年龄" placeholder="检索姓名/职位/派系/年龄…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <div className="bureau-scope-tabs">
              {bureauScopes.map((item) => (
                <button key={item} className={scope === item ? "active" : ""} onClick={() => setScope(item)}>
                  {item}
                </button>
              ))}
            </div>
            <div className="bureau-roster-count">当前筛出 {filteredMinisters.length} 人</div>
            <div className="bureau-roster-list">
              {filteredMinisters.map((m) => (
                <button
                  key={m.name}
                  className={`bureau-roster-row status-${m.status} ${selectedOfficer?.name === m.name ? "selected" : ""}`}
                  onClick={() => setSelectedName(m.name)}
                >
                  <span>
                    <b>{m.name}</b>
                    <i>{m.faction} · {m.office_type}</i>
                  </span>
                  <em>{m.office || m.status_label}</em>
                  <small>{ageText(m)} · {m.status_label}</small>
                </button>
              ))}
              {!filteredMinisters.length ? <div className="empty-note">{q ? "无匹配官员。" : "此类暂无官员。"}</div> : null}
            </div>
          </div>

          <div className="bureau-detail-panel">
            {selectedOfficer ? (
              <>
                <header className="bureau-person-head">
                  <div>
                    <span>{selectedOfficer.summary}</span>
                    <h3>{selectedOfficer.name}</h3>
                  </div>
                  <b className={`minister-status status-${selectedOfficer.status}`}>{selectedOfficer.status_label}</b>
                </header>

                <div className="bureau-person-lines">
                  <span>{selectedOfficer.office || selectedOfficer.office_type}</span>
                  <span>{ageText(detailCharacter)}</span>
                  <span>{selectedOfficer.portrait_available === false ? "立绘缺失" : "立绘可用"}</span>
                </div>

                <div className="bureau-eval-grid">
                  <div>
                    <small>任事状态</small>
                    <b>{selectedOfficer.status_label}</b>
                    <span>{selectedOfficer.status_reason || "无特别案由"}</span>
                  </div>
                  <div>
                    <small>奏对记录</small>
                    <b>{recentStanceCount}</b>
                    <span>{recentStanceCount ? "本回合已有立场证据" : "尚未形成考核证据"}</span>
                  </div>
                  <div>
                    <small>履约协议</small>
                    <b>{readyAgreementCount}/{selectedAgreements.length}</b>
                    <span>{pendingAgreementCount ? `${pendingAgreementCount} 项待履约` : "无待办条件"}</span>
                  </div>
                  <div>
                    <small>名册范围</small>
                    <b>{archiveManaged}</b>
                    <span>大明非后宫官僚池</span>
                  </div>
                </div>

                <div className="bureau-command-row">
                  <button onClick={() => selectedOfficer && onOpenChat(selectedDetail || selectedOfficer)} disabled={!canChatSelected}>召见考问</button>
                  <button onClick={() => setScope("缺图")}>查缺图</button>
                  <button onClick={() => setScope("待铨外缘")}>查待铨</button>
                </div>

                {selectedAgreements.length ? (
                  <div className="agreement-mini-list">
                    <b>履约清单</b>
                    {selectedAgreements.map((agreement) => (
                      <article key={agreement.id} className={`agreement-mini status-${agreement.status}`}>
                        <span>{targetStatusLabel[agreement.target_status || ""] || agreementStatusLabel[agreement.status]} · {agreement.core_topic || agreement.topic}</span>
                        <small>
                          {conditionStatusLabel[agreement.condition_status || ""] || agreement.promise_type || agreement.handshake_label || agreement.handshake_status}
                          {agreement.stakes ? ` · ${agreement.stakes}` : ""}
                          {typeof agreement.fulfillment_score === "number" ? ` · 审计${agreement.fulfillment_score}` : ""}
                        </small>
                        {agreement.target_text ? <p className="agreement-target">标的：{agreement.target_text}</p> : null}
                        {agreement.fulfillment_evidence ? <p className="agreement-evidence">{agreement.fulfillment_evidence}</p> : null}
                        {agreement.target_evidence ? <p className="agreement-evidence">标的裁断：{agreement.target_evidence}</p> : null}
                        {agreement.tasks?.length ? (
                          <div>
                            {agreement.tasks.map((task) => (
                              <p key={task.id}>
                                <i className={`task-${task.status}`}>{taskStatusLabel[task.status]}</i>
                                <em>{task.description}</em>
                                {task.evidence ? <small>{task.evidence}</small> : null}
                              </p>
                            ))}
                          </div>
                        ) : null}
                        {agreement.execution_consequence ? <p className="agreement-consequence">{agreement.execution_consequence}</p> : null}
                      </article>
                    ))}
                  </div>
                ) : null}

                <StanceNotes notes={detailCharacter?.stance_notes} />

                {loadingDetail ? <div className="empty-note">正在调阅{loadingDetail}考核档...</div> : null}
                {detailError ? <div className="empty-note">{detailError}</div> : null}
                {selectedDetail?.network_profile ? <NetworkProfileBlock profile={selectedDetail.network_profile} /> : null}
                {selectedDetail?.xinpan_profile ? <XinpanProfileBlock profile={selectedDetail.xinpan_profile} /> : null}
                {selectedDetail?.tiangang_profile ? <TiangangSpectrum profile={selectedDetail.tiangang_profile} /> : null}

                <details className="bureau-special-action">
                  <summary>特殊身份转换</summary>
                  <div className="identity-action-list">
                    <section className={`identity-action-card ${selectedCastrate ? "" : "disabled"}`} aria-disabled={!selectedCastrate}>
                      <div className="drawer-action-head">
                        <b>净身入内廷</b>
                        <span>当前：{selectedOfficer.name}</span>
                      </div>
                      <small>适用于在朝文官、武官；转入司礼监太监身份。</small>
                      <div className="identity-consequence-row" aria-label="净身入内廷后果预估">
                        <span><b>自愿</b>势合+16 · 信言↑</span>
                        <span className="danger"><b>强旨</b>势合-48 · 畏惧+24 · 仇恨+52起</span>
                      </div>
                      {selectedCastrate ? (
                        <>
                          <div className={`castration-consent ${hasCastrationConsent ? "ready" : castrationBlocked ? "blocked" : castrationConditional ? "conditional" : ""}`}>
                            <b>{hasCastrationConsent ? "握手成功" : castrationConditional ? "附条件未闭环" : castrationBlocked ? "本人未同意" : "尚未奏对"}</b>
                            <span>
                              {hasCastrationConsent
                                ? "可按自愿入内廷办理。"
                                : castrationConditional
                                  ? `需先履约：${castrationTasks.map((task) => task.description).join("；") || castrationNote?.conditions || "条件未明"}`
                                  : "先劝说，心理量表握手成功后才可自愿转换；否则只能强旨。"}
                            </span>
                          </div>
                          <div className="castration-actions">
                            <button onClick={runPersuadeCastration} disabled={!!actionBusy}>劝说奏对</button>
                            <button onClick={() => runCastrate(false)} disabled={!hasCastrationConsent || !!actionBusy}>自愿入内廷</button>
                            <button className="danger" onClick={() => runCastrate(true)} disabled={!!actionBusy}>强旨净身</button>
                          </div>
                        </>
                      ) : (
                        <div className="identity-disabled-note">当前人物不适用净身入内廷。</div>
                      )}
                    </section>

                    <section className={`identity-action-card ${selectedEmancipate ? "" : "disabled"}`} aria-disabled={!selectedEmancipate}>
                      <div className="drawer-action-head">
                        <b>奴籍转民籍</b>
                        <span>当前：{selectedOfficer.name}</span>
                      </div>
                      <small>适用于太监/内廷奴籍；转出后立绘改为百姓布衣与头巾。</small>
                      <div className="identity-consequence-row" aria-label="奴籍转民籍后果预估">
                        <span><b>自愿</b>势合+6 · 畏惧-2</span>
                        <span className="danger"><b>下旨</b>势合-36 · 畏惧+10 · 仇恨+58起</span>
                      </div>
                      {selectedEmancipate ? (
                        <>
                          <div className={`castration-consent ${hasEmancipationConsent ? "ready" : emancipationBlocked ? "blocked" : emancipationConditional ? "conditional" : ""}`}>
                            <b>{hasEmancipationConsent ? "握手成功" : emancipationConditional ? "附条件未闭环" : emancipationBlocked ? "本人未同意" : "尚未奏对"}</b>
                            <span>
                              {hasEmancipationConsent
                                ? "可按自愿脱籍还民办理。"
                                : emancipationConditional
                                  ? `需先履约：${emancipationTasks.map((task) => task.description).join("；") || emancipationNote?.conditions || "条件未明"}`
                                  : "先劝导，心理量表握手成功后才可自愿转民籍；否则只能下旨。"}
                            </span>
                          </div>
                          <div className="castration-actions">
                            <button onClick={runPersuadeEmancipation} disabled={!!actionBusy}>劝导奏对</button>
                            <button onClick={() => runEmancipate(false)} disabled={!hasEmancipationConsent || !!actionBusy}>自愿转民籍</button>
                            <button className="danger" onClick={() => runEmancipate(true)} disabled={!!actionBusy}>直接下旨</button>
                          </div>
                        </>
                      ) : (
                        <div className="identity-disabled-note">当前人物不是太监/内廷奴籍。</div>
                      )}
                    </section>
                  </div>
                </details>
              </>
            ) : (
              <div className="empty-note">选择一名官员调阅考核簿。</div>
            )}
          </div>
        </section>
      </div>
    </RightDrawer>
  );
}

function OrganizationDrawer({
  organizations,
  open,
  onAddCustom,
  onOpenChat,
  onClose,
}: {
  organizations: OrganizationPayload;
  open: boolean;
  onAddCustom: (payload: { name: string; category: string; mandate: string; slots: string[] }) => Promise<string>;
  onOpenChat: (minister: Minister) => void;
  onClose: () => void;
}) {
  const [q, setQ] = React.useState("");
  const [selectedId, setSelectedId] = React.useState("");
  const [selectedCategory, setSelectedCategory] = React.useState("全部");
  const [viewMode, setViewMode] = React.useState<"institutions" | "vacancies" | "unassigned">("institutions");
  const [name, setName] = React.useState("海关");
  const [mandate, setMandate] = React.useState("稽查海贸、抽分商税、兼察沿海走私。");
  const [slotsText, setSlotsText] = React.useState("海关提举\n海关副使\n海关巡检");
  const [notice, setNotice] = React.useState("");
  const [adding, setAdding] = React.useState(false);
  const institutions = organizations?.institutions || [];
  const unassigned = organizations?.unassigned || [];
  const query = q.trim();
  const categories = React.useMemo(
    () => ["全部", ...Array.from(new Set(institutions.map((item) => item.category).filter(Boolean)))],
    [institutions],
  );
  const categoryStats = React.useMemo(() => categories.map((category) => {
    const rows = category === "全部" ? institutions : institutions.filter((item) => item.category === category);
    return {
      category,
      count: rows.length,
      vacancies: rows.reduce((sum, item) => sum + item.vacancy_count, 0),
    };
  }), [categories, institutions]);
  const filtered = React.useMemo(() => institutions.filter((item) => {
    const categoryHit = selectedCategory === "全部" || item.category === selectedCategory;
    const slotHit = item.slots.some((slot) => (
      slot.title.includes(query)
      || (slot.match_hint || "").includes(query)
      || slot.holders.some((holder) => (
        holder.name.includes(query)
        || (holder.office || "").includes(query)
        || (holder.office_type || "").includes(query)
        || (holder.faction || "").includes(query)
      ))
    ));
    const queryHit = !query || item.name.includes(query) || item.category.includes(query) || item.mandate.includes(query) || slotHit;
    return categoryHit && queryHit;
  }), [institutions, query, selectedCategory]);
  const unassignedRows = React.useMemo(() => unassigned.filter((m) => (
    !query || m.name.includes(query) || (m.office || "").includes(query) || (m.office_type || "").includes(query) || (m.faction || "").includes(query)
  )), [query, unassigned]);
  const vacancyGroups = React.useMemo(() => institutions.map((item) => {
    const slots = item.slots.filter((slot) => {
      if (!slot.vacancies) return false;
      if (!query) return true;
      return (
        item.name.includes(query)
        || item.category.includes(query)
        || item.mandate.includes(query)
        || slot.title.includes(query)
        || (slot.match_hint || "").includes(query)
      );
    });
    return { item, slots };
  }).filter((group) => {
    const categoryHit = selectedCategory === "全部" || group.item.category === selectedCategory;
    return categoryHit && group.slots.length > 0;
  }), [institutions, query, selectedCategory]);
  const vacancySlotCount = vacancyGroups.reduce(
    (sum, group) => sum + group.slots.reduce((slotSum, slot) => slotSum + slot.vacancies, 0),
    0,
  );
  React.useEffect(() => {
    const candidates = filtered.length ? filtered : institutions;
    if (!candidates.length) {
      if (selectedId) setSelectedId("");
      return;
    }
    if (!selectedId || !candidates.some((item) => item.id === selectedId)) {
      setSelectedId(candidates[0].id);
    }
  }, [filtered, institutions, selectedId]);
  const selected = filtered.find((item) => item.id === selectedId) || filtered[0];
  const selectedSlots = selected?.slots || [];
  const selectedHolderCount = selectedSlots.reduce((sum, slot) => sum + (slot.filled_count ?? Math.min(slot.holders.length, slot.count)), 0);
  const selectedCapacity = selectedSlots.reduce((sum, slot) => sum + (slot.open_pool ? (slot.filled_count ?? slot.holders.length) : slot.count), 0);
  const selectedVacancies = selectedSlots.reduce((sum, slot) => sum + slot.vacancies, 0);
  const addCustom = async () => {
    if (adding) return;
    setAdding(true);
    setNotice("");
    try {
      const message = await onAddCustom({
        name,
        category: "非常规",
        mandate,
        slots: slotsText.split(/\r?\n|、|,/).map((item) => item.trim()).filter(Boolean),
      });
      setNotice(message);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setAdding(false);
    }
  };
  return (
    <RightDrawer open={open} onClose={onClose} title="组织架构" icon={<Landmark size={17} />} extraClass="right-drawer-organization">
      <div className="org-command-strip">
        <div>
          <span>执行力</span>
          <b>{organizations?.court_readiness ?? 0}</b>
        </div>
        <div>
          <span>机构</span>
          <b>{institutions.length}</b>
        </div>
        <div>
          <span>空缺</span>
          <b>{organizations?.vacancy_count || 0}</b>
        </div>
        <div>
          <span>非常规</span>
          <b>{organizations?.custom_count || 0}</b>
        </div>
        <div>
          <span>未归位</span>
          <b>{unassigned.length}</b>
        </div>
      </div>
      {organizations?.execution_summary ? (
        <div className="org-execution-summary">
          <span>班子审计</span>
          <p>{organizations.execution_summary}</p>
        </div>
      ) : null}
      <div className="org-category-tabs" aria-label="机构类别">
        {categoryStats.map((item) => (
          <button
            key={item.category}
            className={selectedCategory === item.category ? "active" : ""}
            onClick={() => setSelectedCategory(item.category)}
          >
            <span>{item.category}</span>
            <small>{item.count} 所 · 空 {item.vacancies}</small>
          </button>
        ))}
      </div>
      <div className="org-view-tabs" aria-label="组织图视图">
        <button className={viewMode === "institutions" ? "active" : ""} onClick={() => setViewMode("institutions")}>
          <span>机构</span>
          <small>{filtered.length} 所</small>
        </button>
        <button className={viewMode === "vacancies" ? "active" : ""} onClick={() => setViewMode("vacancies")}>
          <span>缺员</span>
          <small>{vacancySlotCount} 席</small>
        </button>
        <button className={viewMode === "unassigned" ? "active" : ""} onClick={() => setViewMode("unassigned")}>
          <span>未归位</span>
          <small>{unassignedRows.length} 人</small>
        </button>
      </div>
      <div className="org-toolbar">
        <input className="right-drawer-search-input" aria-label="搜索机构、席位、人名或派系" placeholder="搜索机构、席位、人名或派系…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {viewMode === "institutions" && (
        <div className="org-layout">
          <aside className="org-index">
            <div className="org-index-head">
              <b>机构目录</b>
              <span>{filtered.length} 所</span>
            </div>
            <div className="org-index-list">
              {filtered.map((item) => (
                <button
                  key={item.id}
                  className={`org-index-row${selected?.id === item.id ? " selected" : ""} ${item.vacancy_count || (item.readiness ?? 100) < 55 ? "warn" : ""}`}
                  onClick={() => setSelectedId(item.id)}
                >
                  <span>{item.name}</span>
                  <small>{item.category}</small>
                  <b>{item.readiness != null ? `${item.readiness}` : item.vacancy_count ? `空 ${item.vacancy_count}` : `${item.holder_count || 0} 人`}</b>
                </button>
              ))}
              {!filtered.length && <div className="empty-note">无匹配机构。</div>}
            </div>
          </aside>
          {selected ? (
            <section className="org-ledger">
              <header className="org-ledger-head">
                <div>
                  <span>{selected.category}</span>
                  <h2>{selected.name}</h2>
                </div>
                <div className={`org-vacancy-seal ${selectedVacancies ? "warn" : ""}`}>
                  <b>{selectedHolderCount}/{selectedCapacity}</b>
                  <small>{selectedVacancies ? `空缺 ${selectedVacancies}` : "额满"}</small>
                </div>
              </header>
              <p className="org-mandate">{selected.mandate}</p>
              <div className="org-readiness-panel">
                <div>
                  <span>执行力</span>
                  <b>{selected.readiness ?? 0}</b>
                </div>
                <div>
                  <span>席位覆盖</span>
                  <b>{selected.coverage ?? 0}</b>
                </div>
                <div>
                  <span>在任质量</span>
                  <b>{selected.holder_quality ?? 0}</b>
                </div>
              </div>
              {selected.execution_summary ? <p className="org-execution-note">{selected.execution_summary}</p> : null}
              {selected.execution_risks?.length ? (
                <div className="org-risk-list">
                  {selected.execution_risks.slice(0, 4).map((risk) => <span key={risk}>{risk}</span>)}
                </div>
              ) : null}
              <div className="org-slot-grid">
                {selected.slots.map((slot) => (
                  <div key={`${selected.id}-${slot.title}`} className={`org-slot ${slot.vacancies ? "vacant" : ""} ${slot.open_pool ? "open-pool" : ""}`}>
                    <div className="org-slot-head">
                      <b>{slot.title}</b>
                      <span>
                        {slot.open_pool
                          ? `${slot.holders.length} 人`
                          : `${slot.filled_count ?? Math.min(slot.holders.length, slot.count)}/${slot.count}`}
                      </span>
                    </div>
                    {slot.match_hint ? <em className="org-slot-hint">{slot.match_hint}</em> : null}
                    {slot.holders.length ? (
                      <div className="org-holder-list">
                        {slot.holders.map((holder) => (
                          <button key={holder.name} onClick={() => onOpenChat(holder)}>
                            <span>{holder.name}</span>
                            <small>{holder.office || holder.faction}</small>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <small>空缺</small>
                    )}
                    {slot.overflow_count ? <small>同类超额 {slot.overflow_count} 人，已列出供调任裁撤。</small> : null}
                  </div>
                ))}
              </div>
            </section>
          ) : (
            <section className="org-ledger empty-note">未选择机构。</section>
          )}
        </div>
      )}
      {viewMode === "vacancies" && (
        <section className="org-ledger org-vacancy-board">
          <header className="org-ledger-head">
            <div>
              <span>{selectedCategory}</span>
              <h2>缺员清单</h2>
            </div>
            <div className={`org-vacancy-seal ${vacancySlotCount ? "warn" : ""}`}>
              <b>{vacancySlotCount}</b>
              <small>空席</small>
            </div>
          </header>
          <div className="org-vacancy-list">
            {vacancyGroups.map(({ item, slots }) => (
              <article key={item.id} className="org-vacancy-card">
                <div className="org-vacancy-card-head">
                  <div>
                    <span>{item.category}</span>
                    <b>{item.name}</b>
                  </div>
                  <button onClick={() => { setSelectedId(item.id); setViewMode("institutions"); }}>查看</button>
                </div>
                <div className="org-vacancy-slot-list">
                  {slots.map((slot) => (
                    <div key={`${item.id}-${slot.title}`} className="org-vacancy-slot">
                      <b>{slot.title}</b>
                      <span>缺 {slot.vacancies}</span>
                      {slot.match_hint ? <small>{slot.match_hint}</small> : null}
                    </div>
                  ))}
                </div>
              </article>
            ))}
            {!vacancyGroups.length && <div className="empty-note">无匹配空缺。</div>}
          </div>
        </section>
      )}
      {viewMode === "unassigned" && (
        <section className="org-ledger org-unassigned-ledger">
          <header className="org-ledger-head">
            <div>
              <span>{selectedCategory}</span>
              <h2>未归位人物</h2>
            </div>
            <div className={`org-vacancy-seal ${unassignedRows.length ? "warn" : ""}`}>
              <b>{unassignedRows.length}</b>
              <small>人物</small>
            </div>
          </header>
          <div className="org-unassigned-list">
            {unassignedRows.map((holder) => (
              <button key={holder.name} className="org-unassigned-row" onClick={() => onOpenChat(holder)}>
                <span>{holder.name}</span>
                <small>{holder.office || holder.office_type || "无实任"}</small>
                <i>{holder.faction}</i>
              </button>
            ))}
            {!unassignedRows.length && <div className="empty-note">无匹配人物。</div>}
          </div>
        </section>
      )}
      <details className="org-custom-panel">
        <summary>增设非常规机构</summary>
        <div className="org-custom-form">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="机构名" />
          <textarea value={mandate} onChange={(e) => setMandate(e.target.value)} rows={2} placeholder="权责" />
          <textarea value={slotsText} onChange={(e) => setSlotsText(e.target.value)} rows={3} placeholder="每行一个席位" />
          <button className="drawer-wide-action" onClick={addCustom} disabled={adding || !name.trim()}>
            增设机构
          </button>
          {notice && <div className="drawer-action-notice">{notice}</div>}
        </div>
      </details>
    </RightDrawer>
  );
}

function CourtDrawer({
  state: _state,
  ministers,
  ministerGroup,
  selectedMinister,
  open,
  onGroupChange,
  onClose,
  onOpenChat,
  onUploadPortrait,
  onGeneratePortrait,
}: {
  state: GameState;
  ministers: Minister[];
  ministerGroup: string;
  selectedMinister: string;
  open: boolean;
  onGroupChange: (group: string) => void;
  onClose: () => void;
  onOpenChat: (minister: Minister) => void;
  onUploadPortrait: (ministerName: string, file: File) => Promise<void>;
  onGeneratePortrait: (ministerName: string) => Promise<void>;
}) {
  const [q, setQ] = React.useState("");
  const titleId = React.useId();
  const { drawerRef, closeRef } = useDrawerFocus(open);
  const filtered = q
    ? ministers.filter((m) => m.name.includes(q) || (m.office || "").includes(q) || (m.office_type || "").includes(q) || (m.faction || "").includes(q))
    : ministers;
  const missingPortraits = ministers.filter((m) => m.portrait_available === false).length;
  const activeCount = _state.ministers.filter((m) => (m.power_id || "ming") === "ming" && m.status === "active").length;
  const groups = ["在职", "内阁+六部", "边镇厂卫", "江湖外缘", "收藏", "全部", "人物志"];
  const characterByName = new Map([..._state.ministers, ...(_state.consorts || [])].map((item) => [item.name, item]));
  const archiveRows = _state.character_index || [];
  const archiveFilteredCount = archiveRows.filter((m) => {
    if (!q.trim()) return true;
    return m.name.includes(q) || m.office.includes(q) || m.office_type.includes(q) || m.faction.includes(q) || m.power_name.includes(q);
  }).length;
  return (
    <>
      {open && <button className="drawer-scrim" aria-label="收起" onClick={onClose} />}
      <aside
        ref={drawerRef}
        className={`court-drawer ${open ? "open" : ""}`}
        role="dialog"
        aria-modal={open ? true : undefined}
        aria-labelledby={titleId}
        aria-hidden={!open}
        inert={!open ? true : undefined}
      >
        <div className="drawer-brand">
          <div className="panel-title">
            <Landmark size={17} />
            <span id={titleId}>朝堂</span>
          </div>
          <button ref={closeRef} className="icon-button" aria-label="收起" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="segmented">
          {groups.map((group) => (
            <button
              className={ministerGroup === group ? "active" : ""}
              key={group}
              onClick={() => onGroupChange(group)}
            >
              {group}
            </button>
          ))}
        </div>
        <div className="court-drawer-meta">
          <span>当前 {ministerGroup === "人物志" ? archiveFilteredCount : filtered.length} 人</span>
          <span>在职 {activeCount} 人</span>
          {missingPortraits > 0 && <span className="warn">缺图 {missingPortraits}</span>}
        </div>
        <div className="right-drawer-search court-search">
          <input className="right-drawer-search-input" aria-label="搜索姓名、职位或派系" placeholder="搜索姓名/职位/派系…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {ministerGroup === "人物志" ? (
          <CharacterArchive
            rows={archiveRows}
            query={q}
            characterByName={characterByName}
            onOpenChat={onOpenChat}
          />
        ) : (
          <MinisterCardList
            list={filtered}
            portraitPrefix="minister_"
            selectedMinister={selectedMinister}
            emptyNote={q ? "无匹配大臣。" : "此栏暂无可召见大臣。"}
            onOpenChat={onOpenChat}
            courtMode={ministerGroup === "内阁+六部"}
            onUploadPortrait={onUploadPortrait}
            onGeneratePortrait={onGeneratePortrait}
          />
        )}
      </aside>
    </>
  );
}

function CharacterArchive({
  rows,
  query,
  characterByName,
  onOpenChat,
}: {
  rows: CharacterIndexEntry[];
  query: string;
  characterByName: Map<string, Minister>;
  onOpenChat: (minister: Minister) => void;
}) {
  const [scope, setScope] = React.useState("全部");
  const [selectedName, setSelectedName] = React.useState(rows[0]?.name || "");
  const [detail, setDetail] = React.useState<Minister | null>(null);
  const [loadingName, setLoadingName] = React.useState("");
  const [detailError, setDetailError] = React.useState("");
  const [detailCache, setDetailCache] = React.useState<Record<string, { signature: string; detail: Minister }>>({});
  const scopes = ["全部", "大明", "外部", "后宫", "尚未登场", "缺图"];
  const filtered = rows.filter((row) => {
    const q = query.trim();
    const queryHit = !q || row.name.includes(q) || row.office.includes(q) || row.office_type.includes(q) || row.faction.includes(q) || row.power_name.includes(q);
    if (!queryHit) return false;
    if (scope === "大明") return (row.power_id || "ming") === "ming" && row.office_type !== "后宫";
    if (scope === "外部") return (row.power_id || "ming") !== "ming";
    if (scope === "后宫") return row.office_type === "后宫";
    if (scope === "尚未登场") return row.status === "offstage";
    if (scope === "缺图") return row.portrait_available === false;
    return true;
  });
  React.useEffect(() => {
    if (selectedName && filtered.some((row) => row.name === selectedName)) return;
    setSelectedName(filtered[0]?.name || "");
  }, [selectedName, filtered, rows]);
  const selectedRow = rows.find((row) => row.name === selectedName);
  const selectedSignature = selectedRow
    ? [selectedRow.office, selectedRow.office_type, selectedRow.faction, selectedRow.status, selectedRow.power_id, selectedRow.portrait_available ? "1" : "0"].join("|")
    : "";
  React.useEffect(() => {
    if (!selectedName) {
      setDetail(null);
      setLoadingName("");
      return;
    }
    const cached = detailCache[selectedName];
    if (cached && cached.signature === selectedSignature) {
      setDetail(cached.detail);
      setLoadingName("");
      setDetailError("");
      return;
    }
    let cancelled = false;
    setLoadingName(selectedName);
    setDetailError("");
    api<{ character: Minister }>(`/api/characters/${encodeURIComponent(selectedName)}`)
      .then((data) => {
        if (cancelled) return;
        setDetail(data.character);
        setDetailCache((current) => ({
          ...current,
          [selectedName]: { signature: selectedSignature, detail: data.character },
        }));
      })
      .catch((err) => {
        if (cancelled) return;
        setDetail(null);
        setDetailError(formatApiError(err, "读取人物档案失败"));
      })
      .finally(() => {
        if (!cancelled) setLoadingName("");
      });
    return () => {
      cancelled = true;
    };
  }, [selectedName, selectedSignature, detailCache]);
  const active = rows.filter((row) => row.status === "active").length;
  const offstage = rows.filter((row) => row.status === "offstage").length;
  const external = rows.filter((row) => (row.power_id || "ming") !== "ming").length;
  const missing = rows.filter((row) => row.portrait_available === false).length;
  const openArchiveChat = (row: CharacterIndexEntry, known?: Minister) => {
    if (!row.can_summon) return;
    if (known) {
      onOpenChat(known);
      return;
    }
    if (detail?.name === row.name) {
      onOpenChat(detail);
      return;
    }
    setLoadingName(row.name);
    setDetailError("");
    api<{ character: Minister }>(`/api/characters/${encodeURIComponent(row.name)}`)
      .then((data) => {
        setDetail(data.character);
        setDetailCache((current) => ({
          ...current,
          [row.name]: { signature: [row.office, row.office_type, row.faction, row.status, row.power_id, row.portrait_available ? "1" : "0"].join("|"), detail: data.character },
        }));
        onOpenChat(data.character);
      })
      .catch((err) => setDetailError(formatApiError(err, "读取人物档案失败")))
      .finally(() => setLoadingName(""));
  };
  return (
    <section className="character-archive">
      <div className="character-archive-stats">
        <span>总录 <b>{rows.length}</b></span>
        <span>在场 <b>{active}</b></span>
        <span>未登场 <b>{offstage}</b></span>
        <span>外部 <b>{external}</b></span>
        <span className={missing ? "warn" : ""}>缺图 <b>{missing}</b></span>
      </div>
      <div className="character-archive-tabs">
        {scopes.map((item) => (
          <button key={item} className={scope === item ? "active" : ""} onClick={() => setScope(item)}>
            {item}
          </button>
        ))}
      </div>
      <div className="character-archive-detail">
        {detail ? (
          <>
            <header>
              <div>
                <span>{detail.summary}</span>
                <h3>{detail.name}</h3>
              </div>
              {detail.status !== "active" ? <b>{detail.status_label}</b> : null}
              {detail.status === "active" && detail.power_id === "ming" ? (
                <button onClick={() => onOpenChat(detail)}>召见</button>
              ) : null}
            </header>
            {detail.office ? <p>{detail.office}</p> : null}
            <NetworkProfileBlock profile={detail.network_profile} />
            <XinpanProfileBlock profile={detail.xinpan_profile} />
            <TiangangSpectrum profile={detail.tiangang_profile} />
          </>
        ) : loadingName ? (
          <div className="empty-note">正在调阅{loadingName}档案...</div>
        ) : detailError ? (
          <div className="empty-note">{detailError}</div>
        ) : (
          <div className="empty-note">选择一名人物查看小传、人脉与天罡谱尺。</div>
        )}
      </div>
      <div className="character-archive-list">
        {filtered.map((row) => {
          const minister = characterByName.get(row.name);
          const callable = !!row.can_summon;
          return (
            <article
              className={`character-archive-row status-${row.status} ${callable ? "callable" : ""} ${selectedName === row.name ? "selected" : ""}`}
              key={row.name}
              role="button"
              tabIndex={0}
              onClick={() => setSelectedName(row.name)}
              onKeyDown={(event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                setSelectedName(row.name);
              }}
            >
              <div>
                <b>{row.name}</b>
                <span>{row.power_name} · {row.faction} · {row.office_type}</span>
              </div>
              <small>{row.office || row.status_label}</small>
              <em>{row.status_label}{row.portrait_available === false ? " · 缺立绘" : ""}</em>
              {callable ? (
                <button onClick={(event) => { event.stopPropagation(); openArchiveChat(row, minister); }}>
                  召见
                </button>
              ) : null}
            </article>
          );
        })}
        {!filtered.length ? <div className="empty-note">人物志无匹配记录。</div> : null}
      </div>
    </section>
  );
}

function HaremDrawer({
  consorts,
  haremGroup,
  selectedMinister,
  open,
  onGroupChange,
  onClose,
  onOpenChat,
  onUploadPortrait,
  onGeneratePortrait,
  onAction,
}: {
  consorts: Minister[];
  haremGroup: string;
  selectedMinister: string;
  open: boolean;
  onGroupChange: (group: string) => void;
  onClose: () => void;
  onOpenChat: (minister: Minister) => void;
  onUploadPortrait: (ministerName: string, file: File) => Promise<void>;
  onGeneratePortrait: (ministerName: string) => Promise<void>;
  onAction: (name: string, action: "stabilize" | "treasury" | "appease" | "recommend") => Promise<string>;
}) {
  const [q, setQ] = React.useState("");
  const [actionName, setActionName] = React.useState("");
  const [notice, setNotice] = React.useState("");
  const [actionBusy, setActionBusy] = React.useState("");
  const titleId = React.useId();
  const { drawerRef, closeRef } = useDrawerFocus(open);
  const filtered = q ? consorts.filter((c) => c.name.includes(q)) : consorts;
  const activeConsorts = consorts.filter((c) => c.status === "active");
  React.useEffect(() => {
    if (actionName && activeConsorts.some((c) => c.name === actionName)) return;
    const preferred = activeConsorts.find((c) => c.name === selectedMinister)?.name || activeConsorts[0]?.name || "";
    setActionName(preferred);
  }, [actionName, activeConsorts, selectedMinister]);
  const runAction = async (action: "stabilize" | "treasury" | "appease" | "recommend") => {
    if (!actionName || actionBusy) return;
    setActionBusy(action);
    setNotice("");
    try {
      setNotice(await onAction(actionName, action));
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setActionBusy("");
    }
  };
  return (
    <>
      {open && <button className="drawer-scrim" aria-label="收起" onClick={onClose} />}
      <aside
        ref={drawerRef}
        className={`court-drawer harem-drawer overlay-panel ${open ? "open" : ""}`}
        role="dialog"
        aria-modal={open ? true : undefined}
        aria-labelledby={titleId}
        aria-hidden={!open}
        inert={!open ? true : undefined}
      >
        <div className="drawer-brand">
          <div className="panel-title">
            <Crown size={17} />
            <span id={titleId}>后宫</span>
          </div>
          <button ref={closeRef} className="icon-button" aria-label="收起" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="segmented">
          {["全部", "收藏"].map((group) => (
            <button
              className={haremGroup === group ? "active" : ""}
              key={group}
              onClick={() => onGroupChange(group)}
            >
              {group}
            </button>
          ))}
        </div>
        <div className="right-drawer-search court-search">
          <input className="right-drawer-search-input" aria-label="搜索后宫姓名" placeholder="搜索姓名…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="drawer-action-panel harem-action-panel">
          <div className="drawer-action-head">
            <b>宫务</b>
            <select value={actionName} onChange={(e) => setActionName(e.target.value)}>
              {activeConsorts.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
            </select>
          </div>
          <div className="drawer-action-grid">
            <button onClick={() => runAction("stabilize")} disabled={!actionName || !!actionBusy}>协理六宫</button>
            <button onClick={() => runAction("treasury")} disabled={!actionName || !!actionBusy}>盘点内库</button>
            <button onClick={() => runAction("appease")} disabled={!actionName || !!actionBusy}>安抚内廷</button>
            <button onClick={() => runAction("recommend")} disabled={!actionName || !!actionBusy}>举荐宫人</button>
          </div>
          {notice && <div className="drawer-action-notice">{notice}</div>}
        </div>
        <MinisterCardList
          list={filtered}
          portraitPrefix="consort_"
          selectedMinister={selectedMinister}
          emptyNote={q ? "无匹配结果。" : "后宫暂无可召见之人。"}
          onOpenChat={onOpenChat}
          onUploadPortrait={onUploadPortrait}
          onGeneratePortrait={onGeneratePortrait}
        />
      </aside>
    </>
  );
}

function TopStatusBar({
  state,
  onOpenState,
  onOpenMenu,
}: {
  state: GameState;
  onOpenState: () => void;
  onOpenMenu: () => void;
}) {
  const scoreKeys = ["民心", "皇威"];
  return (
    <>
    <header className="status-bar" aria-label="国势状态栏">
      <button className="status-emblem" onClick={onOpenState}>
        <img src="/icon_ming_emblem.png" alt="大明" className="emblem-art" />
        <span>{state.turn.year} 年 {state.turn.period} 月</span>
      </button>
      <div className="status-metrics">
        <BudgetHover accountName="国库" budget={state.budget["国库"]} />
        <BudgetHover accountName="内库" budget={state.budget["内库"]} />
        {scoreKeys.map((key) => (
          <span className={`status-pill ${scoreTone(state.metrics[key], false)}`} key={key}>
            {key} <b>{state.metrics[key]}</b>
          </span>
        ))}
      </div>
      <button className="status-menu" onClick={onOpenMenu} aria-label="游戏菜单">
        <Menu size={16} />
        <span>菜单</span>
      </button>
    </header>
    <LegacyBar legacies={state.legacies} />
    </>
  );
}

const LONG_GOAL_POSTERS = [
  { src: "/long_goal_ming.jpg", alt: "长期目标：让大明再续二百年" },
  { src: "/long_goal_tech.jpg", alt: "长期目标：科技树与文明延续" },
  { src: "/long_goal_modernity.jpg", alt: "长期目标：从王朝危机到现代文明" },
];

function LongGoalsModal({ onClose }: { onClose: () => void }) {
  const [index, setIndex] = React.useState(0);
  const goPrev = React.useCallback(() => {
    setIndex((current) => (current + LONG_GOAL_POSTERS.length - 1) % LONG_GOAL_POSTERS.length);
  }, []);
  const goNext = React.useCallback(() => {
    setIndex((current) => (current + 1) % LONG_GOAL_POSTERS.length);
  }, []);

  React.useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "ArrowLeft") goPrev();
      if (event.key === "ArrowRight") goNext();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [goPrev, goNext]);

  const poster = LONG_GOAL_POSTERS[index];
  return (
    <section className="long-goal-layer" role="dialog" aria-modal="true" aria-label="大明长期目标">
      <div className="long-goal-scrim" onClick={onClose} />
      <button className="long-goal-close" aria-label="关闭弹窗" onClick={onClose}>
        <X size={30} />
      </button>
      <button className="long-goal-nav long-goal-nav-prev" aria-label="上一张长期目标图" onClick={goPrev}>
        <ChevronLeft size={34} />
      </button>
      <figure className="long-goal-poster">
        <img src={poster.src} alt={poster.alt} />
      </figure>
      <button className="long-goal-nav long-goal-nav-next" aria-label="下一张长期目标图" onClick={goNext}>
        <ChevronRight size={34} />
      </button>
      <div className="long-goal-dots" aria-label="长期目标图切换">
        {LONG_GOAL_POSTERS.map((item, itemIndex) => (
          <button
            key={item.src}
            className={itemIndex === index ? "active" : ""}
            aria-label={`切换到第 ${itemIndex + 1} 张长期目标图`}
            onClick={() => setIndex(itemIndex)}
          />
        ))}
      </div>
    </section>
  );
}

const LEGACY_FIELD_LABELS: Record<string, string> = {
  public_support: "民心", unrest: "动乱", gentry_resistance: "士绅阻力", military_pressure: "边防压力",
  tax_per_turn: "月税", grain_security: "粮食", corruption: "腐败度",
  morale: "士气", training: "训练", loyalty: "忠诚", supply: "补给", equipment: "装备",
  arrears: "欠饷", mobility: "机动",
};

function pctStr(v: number): string {
  return `${v > 0 ? "+" : ""}${v}%`;
}

// modifiers = {国库?:pct, 内库?:pct, regions?:{rid:{field:pct}}, armies?:{aid:{field:pct}}}
function formatLegacyEffect(eff: LegacyEffect): string {
  const parts: string[] = [];
  for (const acc of ["国库", "内库", "民心", "皇威"] as const) {
    const v = eff[acc];
    if (typeof v === "number") parts.push(`${acc}${pctStr(v)}`);
  }
  for (const scope of ["regions", "armies"] as const) {
    const block = eff[scope];
    if (!block || typeof block !== "object") continue;
    for (const [entity, fields] of Object.entries(block)) {
      for (const [field, pct] of Object.entries(fields)) {
        const entityLabel = scope === "regions" ? labelRegion(entity) : labelArmy(entity);
        const label = LEGACY_FIELD_LABELS[field] || cnField(field);
        parts.push(`${entityLabel}·${label}${pctStr(pct as number)}`);
      }
    }
  }
  return parts.join("、");
}

function LegacyBar({ legacies }: { legacies: Legacy[] }) {
  const [open, setOpen] = React.useState(false);
  const titleId = React.useId();
  const dialogId = React.useId();
  const triggerRef = React.useRef<HTMLButtonElement | null>(null);
  const closeRef = React.useRef<HTMLButtonElement | null>(null);
  const closeModal = React.useCallback(() => {
    setOpen(false);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  }, []);
  React.useEffect(() => {
    if (!open) return;
    window.setTimeout(() => closeRef.current?.focus(), 0);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeModal();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [closeModal, open]);
  if (!legacies || legacies.length === 0) return null;
  return (
    <>
      <button
        ref={triggerRef}
        className="legacy-bar"
        aria-label="现行帝国修正"
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={open ? dialogId : undefined}
        onClick={() => setOpen(true)}
      >
        <span className="legacy-bar-label">帝国修正</span>
        <span className="legacy-bar-count">{legacies.length}</span>
      </button>
      {open && (
        <div className="legacy-modal-backdrop">
          <button type="button" className="legacy-modal-scrim" aria-label="关闭帝国修正" onClick={closeModal} />
          <div
            id={dialogId}
            className="legacy-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
          >
            <div className="legacy-modal-head">
              <h3 id={titleId}>现行帝国修正</h3>
              <button ref={closeRef} className="legacy-modal-close" onClick={closeModal} aria-label="关闭">×</button>
            </div>
            <ul className="legacy-list">
              {legacies.map((lg) => (
                <li key={lg.id} className="legacy-item">
                  <div className="legacy-item-top">
                    <b>{lg.name}</b>
                    <span className="legacy-item-meta">
                      <span className="legacy-item-dur">{lg.remaining_months < 0 ? "永久" : `余 ${lg.remaining_months} 月`}</span>
                    </span>
                  </div>
                  <p className="legacy-item-eff">{lg.effect_text || formatLegacyEffect(lg.modifiers)}</p>
                  {lg.clear_condition && <p className="legacy-item-clear">消除条件：{lg.clear_condition}</p>}
                  {lg.narrative_hint && <p className="legacy-item-hint">{lg.narrative_hint}</p>}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </>
  );
}

function BudgetHover({ accountName, budget }: { accountName: "国库" | "内库"; budget: BudgetAccount }) {
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef<HTMLSpanElement | null>(null);
  const popoverId = React.useId();
  React.useEffect(() => {
    if (!open) return;
    const handleOutsidePress = (event: Event) => {
      if (!rootRef.current || !(event.target instanceof Node)) return;
      if (!rootRef.current.contains(event.target)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", handleOutsidePress, true);
    document.addEventListener("mousedown", handleOutsidePress, true);
    document.addEventListener("touchstart", handleOutsidePress, true);
    document.addEventListener("click", handleOutsidePress, true);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handleOutsidePress, true);
      document.removeEventListener("mousedown", handleOutsidePress, true);
      document.removeEventListener("touchstart", handleOutsidePress, true);
      document.removeEventListener("click", handleOutsidePress, true);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);
  return (
    <span
      ref={rootRef}
      className={`budget-hover ${open ? "open" : ""}`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <button
        className="status-money budget-trigger"
        type="button"
        aria-label={`查看${accountName}固定收支`}
        aria-expanded={open}
        aria-controls={popoverId}
        onClick={() => setOpen((current) => !current)}
      >
        <span>{accountName} <b>{formatMoney(budget.balance)}</b></span>
        <small className={budget.net >= 0 ? "income" : "expense"}>月 {formatSignedMoney(budget.net)}</small>
      </button>
      <span className="budget-popover" id={popoverId} role="tooltip">
        <span className="budget-popover-head">
          <b>{accountName}月度定额</b>
          <span className="budget-summary">
            <span><small>入</small><strong className="income">{formatMoney(budget.income_total)}</strong></span>
            <span><small>出</small><strong className="expense">{formatMoney(budget.expense_total)}</strong></span>
            <span><small>净</small><strong className={budget.net >= 0 ? "income" : "expense"}>{formatSignedMoney(budget.net)}</strong></span>
          </span>
        </span>
        <BudgetList title="固定收入" items={budget.income} />
        <BudgetList title="固定支出" items={budget.expense} expense />
        <BudgetMovementsList movements={budget.movements} total={budget.movements_total} />
      </span>
    </span>
  );
}

function BudgetMovementsList({ movements, total }: { movements: BudgetMovement[]; total: number }) {
  if (!movements.length) {
    return (
      <span className="budget-list">
        <span className="budget-list-title">本月一次性入账（上月末结算）</span>
        <span className="budget-row"><span><b>暂无</b><small>上月末未结算入出</small></span></span>
      </span>
    );
  }
  return (
    <span className="budget-list">
      <span className="budget-list-title">
        本月一次性入账（上月末结算）
        <small className={total >= 0 ? "income" : "expense"}>　合计 {formatSignedMoney(total)}</small>
      </span>
      {movements.map((m, idx) => {
        const sign = m.delta >= 0 ? "+" : "-";
        const cls = m.delta >= 0 ? "income" : "expense";
        return (
          <span className="budget-row" key={`mv-${idx}`}>
            <span>
              <b>{m.category || "—"}</b>
              <small>{m.reason}</small>
            </span>
            <strong className={cls}>{sign}{formatMoney(Math.abs(m.delta))}</strong>
          </span>
        );
      })}
    </span>
  );
}

function BudgetList({ title, items, expense = false }: { title: string; items: BudgetItem[]; expense?: boolean }) {
  return (
    <span className="budget-list">
      <span className="budget-list-title">{title}</span>
      {items.map((item) => (
        <span className="budget-row" key={`${title}-${item.name}`}>
          <span>
            <b>{item.name}</b>
            <small>{item.note}</small>
          </span>
          <strong className={expense ? "expense" : "income"}>{expense ? "-" : "+"}{formatMoney(item.amount)}</strong>
        </span>
      ))}
    </span>
  );
}

function BottomCommandBar({
  eventsCount,
  directivesCount,
  secretOrdersCount,
  adventureCount,
  onOpenMemorials,
  onOpenEdict,
  onOpenExtraction,
  onOpenHistory,
  onOpenSecretOrders,
  onOpenAdventure,
}: {
  eventsCount: number;
  directivesCount: number;
  secretOrdersCount: number;
  adventureCount: number;
  onOpenMemorials: () => void;
  onOpenEdict: () => void;
  onOpenExtraction: () => void;
  onOpenHistory: () => void;
  onOpenSecretOrders: () => void;
  onOpenAdventure: () => void;
}) {
  return (
    <div className="ui-stage">
      {/* 案板 + 图标 + 玉玺一体 */}
      <div className="anban-wrap">
        {/* 图标行：底部贴基准线向上生长 */}
        <nav className="bottom-command-bar" aria-label="朝政辅助操作">
          <button className="command-icon" onClick={onOpenMemorials} aria-label={`奏疏 ${eventsCount} 件待览`}>
            <img src="/ui/exact/zoushu.png" alt="" className="command-art" />
            {eventsCount ? <span className="command-badge">{eventsCount}</span> : null}
          </button>
          <button className="command-icon" onClick={onOpenExtraction} aria-label="邸报详明">
            <img src="/ui/exact/mingxi.png" alt="" className="command-art" />
          </button>
          <button className="command-icon" onClick={onOpenSecretOrders} aria-label={`密令 ${secretOrdersCount} 条进行中`}>
            <img src="/ui/exact/miling.png" alt="" className="command-art command-art-secret" />
            {secretOrdersCount ? <span className="command-badge command-badge-secret">{secretOrdersCount}</span> : null}
          </button>
          <button className="command-icon" onClick={onOpenHistory} aria-label="历代奏报">
            <img src="/ui/exact/lishi.png" alt="" className="command-art" />
          </button>
          <button className="command-icon" onClick={onOpenAdventure} aria-label={`天命异闻 ${adventureCount} 条记录`}>
            <Scroll size={24} />
            {adventureCount ? <span className="command-badge command-badge-adventure">{adventureCount}</span> : null}
          </button>
          <button className="edict-turn-button" onClick={onOpenEdict} aria-label={`诏书草案 ${directivesCount} 道待发`}>
            <span className="edict-turn-art">
              <img src="/ui/exact/nizhao.png" alt="" />
              {directivesCount ? <span className="command-badge edict-turn-badge">{directivesCount}</span> : null}
            </span>
          </button>
        </nav>
        {/* 文字行：贴在 bar 下方 */}
        <div className="bottom-caption-bar">
          <span className="command-caption"><b>奏疏</b><small>{eventsCount} 件待览</small></span>
          <span className="command-caption"><b>邸报详明</b><small>数项加减/账目明细</small></span>
          <span className="command-caption"><b>密令</b><small>{secretOrdersCount ? `${secretOrdersCount} 条进行中` : "暂无密令"}</small></span>
          <span className="command-caption"><b>史册</b><small>历代奏报/诏书</small></span>
          <span className="command-caption"><b>异闻</b><small>{adventureCount ? `${adventureCount} 条` : "暂无异闻"}</small></span>
          <span className="command-caption"><b>拟诏</b><small>{directivesCount ? `${directivesCount} 道` : "本回合"}</small></span>
        </div>
      </div>
    </div>
  );
}

function FullscreenModal({
  title,
  subtitle,
  bgClass,
  onClose,
  children,
  headerExtra,
}: {
  title: string;
  subtitle: string;
  bgClass?: string;
  onClose: () => void;
  children: React.ReactNode;
  headerExtra?: React.ReactNode;
}) {
  const titleId = React.useId();
  const subtitleId = React.useId();
  const modalRef = React.useRef<HTMLDivElement | null>(null);
  const closeRef = React.useRef<HTMLButtonElement | null>(null);
  const previousFocusRef = React.useRef<HTMLElement | null>(null);
  const onCloseRef = React.useRef(onClose);

  React.useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  const closeModal = React.useCallback(() => {
    onCloseRef.current();
  }, []);

  React.useEffect(() => {
    const activeElement = document.activeElement;
    previousFocusRef.current = activeElement instanceof HTMLElement ? activeElement : null;

    const focusTimer = window.setTimeout(() => {
      const modal = modalRef.current;
      const current = document.activeElement;
      if (modal && current instanceof HTMLElement && modal.contains(current)) return;
      closeRef.current?.focus();
    }, 0);

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeModal();
        return;
      }
      if (event.key !== "Tab") return;

      const modal = modalRef.current;
      if (!modal) return;
      const focusable = Array.from(
        modal.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => (
        element.getAttribute("aria-hidden") !== "true"
        && (element.offsetWidth > 0 || element.offsetHeight > 0 || element.getClientRects().length > 0)
      ));

      if (!focusable.length) {
        event.preventDefault();
        closeRef.current?.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      if (event.shiftKey && (!(active instanceof HTMLElement) || active === first || !modal.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", handleKeyDown);
      const previousFocus = previousFocusRef.current;
      if (previousFocus && document.contains(previousFocus)) {
        window.setTimeout(() => previousFocus.focus(), 0);
      }
    };
  }, [closeModal]);

  return (
    <section className="fullscreen-layer" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={subtitleId}>
      <div className="fullscreen-scrim" aria-hidden="true" onClick={closeModal} />
      <div className={`fullscreen-modal ${bgClass || ""}`} ref={modalRef}>
        <header className="modal-header">
          <div className="modal-title">
            <div>
              <h1 id={titleId}>{title}</h1>
              <span id={subtitleId}>{subtitle}</span>
            </div>
          </div>
          <div className="modal-header-actions">
            {headerExtra}
            <button ref={closeRef} className="icon-button" aria-label="关闭弹窗" onClick={closeModal}>
              <X size={18} />
            </button>
          </div>
        </header>
        {children}
      </div>
    </section>
  );
}

type ExtractionData = {
  turn: number;
  year: number;
  period: number;
  exists: boolean;
  extractor_output?: any;
  causal_notes?: CausalNote[];
};

function ReportModal({ report, onClose }: { report: string; onClose: () => void }) {
  return (
    <FullscreenModal title="月末奏疏" subtitle="推演结果" bgClass="modal-bg-state" onClose={onClose}>
      <article className="state-document report-document modal-scroll">
        <div className="document-section report-section">
          <pre className="memorial-text">{report}</pre>
        </div>
      </article>
    </FullscreenModal>
  );
}

function EndingModal({ ending, onClose }: { ending: EndingPayload; onClose: () => void }) {
  const lastTimeline = ending.timeline?.[ending.timeline.length - 1];
  const endingDate = lastTimeline ? `${lastTimeline.year}年${lastTimeline.period}月` : "终局";
  const timelineCount = ending.timeline?.length ?? 0;

  return (
    <FullscreenModal
      title="终章定论"
      subtitle="崇祯一朝，盖棺论定"
      bgClass="modal-bg-state modal-bg-ending"
      onClose={onClose}
    >
      <article className="state-document ending-document modal-scroll">
        <div className="ending-hero">
          <div className="ending-seal" aria-hidden="true">
            <Crown size={34} />
          </div>
          <div className="ending-hero-copy">
            <p>大明国史馆录</p>
            <h2>{ending.label}</h2>
            <span>{endingDate} · 第 {timelineCount || 1} 卷</span>
          </div>
        </div>

        <section className="ending-verdict-card" aria-label="结局总评">
          <div className="ending-section-kicker">
            <ScrollText size={17} />
            <span>国史编纂官总评</span>
          </div>
          <pre className="ending-summary-text">{ending.summary || "（无总评）"}</pre>
        </section>

        {ending.timeline && ending.timeline.length > 0 && (
          <section className="ending-chronicle" aria-label="逐月历程">
            <div className="ending-section-kicker">
              <Landmark size={17} />
              <span>崇祯一朝逐月历程</span>
            </div>
            <ol className="ending-timeline">
              {ending.timeline.map((it) => (
                <li key={it.turn} className="ending-timeline-item">
                  <div className="ending-timeline-date">
                    <b>{it.year}</b>
                    <span>{it.period}月</span>
                  </div>
                  <div className="ending-timeline-body">
                    {it.chapter ? (
                      <p className="ending-timeline-chapter">{it.chapter}</p>
                    ) : null}
                    {it.decree_brief ? (
                      <p className="ending-timeline-decree">诏：{it.decree_brief}</p>
                    ) : null}
                    {it.effect_brief ? (
                      <p className="ending-timeline-effect">效：{it.effect_brief}</p>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          </section>
        )}
      </article>
    </FullscreenModal>
  );
}

function SecretOrdersModal({
  orders,
  currentTurn,
  onClose,
  onOpenMinister,
}: {
  orders: SecretOrder[];
  currentTurn: number;
  onClose: () => void;
  onOpenMinister: (name: string) => void;
}) {
  const [tab, setTab] = React.useState<"active" | "pending_review" | "done" | "failed" | "all">("active");
  const [selectedOrder, setSelectedOrder] = React.useState<SecretOrder | null>(null);
  const statusLabel: Record<string, string> = {
    active: "进行中",
    pending_review: "待核议",
    done: "已完成",
    failed: "已失败",
    cancelled: "已撤销",
  };
  const statusCls: Record<string, string> = {
    active: "so-active",
    pending_review: "so-pending",
    done: "so-done",
    failed: "so-failed",
    cancelled: "so-cancelled",
  };
  const tabs: { key: typeof tab; label: string }[] = [
    { key: "active",         label: `进行中 (${orders.filter(o => o.status === "active").length})` },
    { key: "pending_review", label: `待核议 (${orders.filter(o => o.status === "pending_review").length})` },
    { key: "done",           label: `已完成 (${orders.filter(o => o.status === "done").length})` },
    { key: "failed",         label: `已失败 (${orders.filter(o => o.status === "failed").length})` },
    { key: "all",            label: `全部 (${orders.length})` },
  ];
  const visible = tab === "all" ? orders : orders.filter(o => o.status === tab);
  return (
    <FullscreenModal title="密令进度" subtitle={`共 ${orders.length} 条密令记录`} bgClass="modal-bg-edict" onClose={onClose}>
      <article className="state-document modal-scroll">
        <div className="so-tabs">
          {tabs.map(t => (
            <button key={t.key} className={`so-tab${tab === t.key ? " so-tab-active" : ""}`} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
        <div className="secret-orders-list">
          {visible.length === 0 && <p className="so-empty">暂无此类密令。</p>}
          {visible.map((o) => {
            const urgency = secretOrderUrgency(o, currentTurn);
            const risk = secretOrderRisk(o, currentTurn);
            return (
              <article
                key={o.id}
                className={`secret-order-card ${statusCls[o.status] || ""} risk-${risk.tone}`}
              >
                <div className="so-header">
                  <span className="so-title"><Lock size={13} />{o.title}</span>
                  <span className={`so-status ${statusCls[o.status] || ""}`}>{statusLabel[o.status] || o.status}</span>
                </div>
                <div className="so-meta">第 {o.year_issued} 年 {o.period_issued} 月下令 · 承办：{o.minister_name}</div>
                <div className="so-signal-row">
                  <i className={`tone-${risk.tone}`}>{risk.label}</i>
                  <i className={`tone-${urgency.tone}`}>{urgency.text}</i>
                  {typeof o.importance === "number" ? <i>重{o.importance}</i> : null}
                </div>
                {o.tags?.length ? (
                  <div className="so-tag-row">
                    {o.tags.slice(0, 5).map((tag) => <small key={tag}>{tag}</small>)}
                  </div>
                ) : null}
                {o.content ? <p className="so-card-preview">{o.content}</p> : null}
                <div className="so-card-actions">
                  <button
                    type="button"
                    className="secondary-action so-detail-open"
                    onClick={() => setSelectedOrder(o)}
                  >
                    查看详情
                  </button>
                  {o.status === "active" && (
                    <button
                      className="secondary-action so-goto"
                      onClick={() => {
                        onClose();
                        onOpenMinister(o.minister_name);
                      }}
                    >
                      <MessageSquare size={13} />
                      召见 {o.minister_name}
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </article>
      {selectedOrder ? (
        <SecretOrderDetailDialog
          order={selectedOrder}
          currentTurn={currentTurn}
          statusLabel={statusLabel}
          statusCls={statusCls}
          onClose={() => setSelectedOrder(null)}
          onOpenMinister={(name) => {
            setSelectedOrder(null);
            onClose();
            onOpenMinister(name);
          }}
        />
      ) : null}
    </FullscreenModal>
  );
}

function SecretOrderDetailDialog({
  order,
  currentTurn,
  statusLabel,
  statusCls,
  onClose,
  onOpenMinister,
}: {
  order: SecretOrder;
  currentTurn: number;
  statusLabel: Record<string, string>;
  statusCls: Record<string, string>;
  onClose: () => void;
  onOpenMinister: (name: string) => void;
}) {
  const deadlineText = order.due_turn
    ? `第 ${order.due_turn} 回合核议${order.due_turn <= order.turn_issued ? "" : `（限 ${order.due_turn - order.turn_issued} 个月）`}`
    : "无硬期限";
  const urgency = secretOrderUrgency(order, currentTurn);
  const risk = secretOrderRisk(order, currentTurn);
  const detailRows = [
    ["编号", `#${order.id}`],
    ["承办", order.minister_name],
    ["下令", `第 ${order.year_issued} 年 ${order.period_issued} 月 · 回合 ${order.turn_issued}`],
    ["期限", deadlineText],
    ["重要", String(order.importance || 0)],
    ["标签", order.tags?.length ? order.tags.join("、") : "无"],
  ];
  return (
    <div className="so-detail-layer" role="dialog" aria-modal="true" aria-label={`密令详情：${order.title}`}>
      <div className="so-detail-scrim" onClick={onClose} />
      <section className="so-detail-dialog">
        <header className="so-detail-header">
          <div>
            <span className={`so-status ${statusCls[order.status] || ""}`}>{statusLabel[order.status] || order.status}</span>
            <h2>{order.title}</h2>
            <div className="so-detail-signals">
              <i className={`tone-${risk.tone}`}>{risk.label}</i>
              <i className={`tone-${urgency.tone}`}>{urgency.text}</i>
            </div>
          </div>
          <button className="icon-button" aria-label="关闭密令详情" onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <div className="so-detail-body">
          <dl className="so-detail-grid">
            {detailRows.map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          <SecretOrderDetailBlock title="密令正文" text={order.content || "未记正文。"} />
          {order.sim_note ? <SecretOrderDetailBlock title="月度动向" text={order.sim_note} tone="green" /> : null}
          {order.result ? (
            <SecretOrderDetailBlock title={order.status === "active" ? "承办回报" : "执行结果"} text={order.result} tone="green" />
          ) : null}
        </div>
        <footer className="so-detail-actions">
          {order.status === "active" ? (
            <button className="secondary-action" onClick={() => onOpenMinister(order.minister_name)}>
              <MessageSquare size={15} />
              召见 {order.minister_name}
            </button>
          ) : null}
          <button className="secondary-action" onClick={onClose}>返回列表</button>
        </footer>
      </section>
    </div>
  );
}

function SecretOrderDetailBlock({ title, text, tone = "default" }: { title: string; text: string; tone?: "default" | "green" }) {
  return (
    <section className={`so-detail-block so-detail-block-${tone}`}>
      <h3>{title}</h3>
      <p>{text}</p>
    </section>
  );
}

function ClosedIssuesModal({ items, onClose }: { items: ClosedIssue[]; onClose: () => void }) {
  const resolved = items.filter((i) => i.status === "resolved");
  const failed = items.filter((i) => i.status === "failed");
  const dropped = items.filter((i) => i.status === "dropped");
  return (
    <FullscreenModal title="局势了结" subtitle={`本月共 ${items.length} 条局势了结`} bgClass="modal-bg-state" onClose={onClose}>
      <article className="state-document modal-scroll">
        {resolved.length ? <ClosedGroup title="已结案" items={resolved} cls="resolved" /> : null}
        {failed.length ? <ClosedGroup title="已崩坏" items={failed} cls="failed" /> : null}
        {dropped.length ? <ClosedGroup title="已撤旨" items={dropped} cls="dropped" /> : null}
      </article>
    </FullscreenModal>
  );
}

function ClosedGroup({ title, items, cls }: { title: string; items: ClosedIssue[]; cls: string }) {
  return (
    <div className="document-section">
      <h3 className={`closed-group-title ${cls}`}>{title}</h3>
      <ul className="closed-list">
        {items.map((it) => (
          <li key={it.id} className={`closed-card ${cls}`}>
            <div className="closed-card-head">
              <b>#{it.id} {it.title}</b>
              <span>{cls === "resolved" ? it.bar_good_meaning : it.bar_bad_meaning}</span>
            </div>
            {it.stage_text ? <p className="closed-card-stage">{it.stage_text}</p> : null}
            <div className="closed-card-effect">{formatClosedEffect(it.effect)}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ExtractionModal({ onClose }: { onClose: () => void }) {
  const [extraction, setExtraction] = React.useState<ExtractionData | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const resp = await fetch("/api/turn_extraction");
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (alive) setExtraction(data);
      } catch (e: any) {
        if (alive) setError(e?.message || "加载失败");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  return (
    <FullscreenModal title="邸报详明" subtitle="数项加减/账目明细" bgClass="modal-bg-state" onClose={onClose}>
      <article className="state-document modal-scroll">
        <ExtractionView data={extraction} loading={loading} error={error} />
      </article>
    </FullscreenModal>
  );
}

type HistoryTurnItem = {
  turn: number;
  year: number;
  period: number;
  has_report: boolean;
  has_extraction: boolean;
  has_directive: boolean;
};

type HistoryDirective = {
  id: number;
  turn: number;
  year: number;
  period: number;
  event_id: string;
  event_title: string;
  actor: string;
  skill_id: string;
  text: string;
  source: string;
  status: string;
  notes: string;
  created_at: string;
  updated_at: string;
};

type HistoryDetail = {
  turn: number;
  exists: boolean;
  year: number;
  period: number;
  report: string;
  decree_text: string;
  directives: HistoryDirective[];
  extraction: ExtractionData | null;
};

function HistoryModal({ onClose }: { onClose: () => void }) {
  const [turns, setTurns] = React.useState<HistoryTurnItem[]>([]);
  const [listLoading, setListLoading] = React.useState(true);
  const [listError, setListError] = React.useState("");
  const [selectedTurn, setSelectedTurn] = React.useState<number | null>(null);
  const [detail, setDetail] = React.useState<HistoryDetail | null>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [detailError, setDetailError] = React.useState("");

  React.useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const resp = await fetch("/api/history/turns");
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (!alive) return;
        const list: HistoryTurnItem[] = data.turns || [];
        setTurns(list);
        if (list.length) setSelectedTurn(list[list.length - 1].turn);
      } catch (e: any) {
        if (alive) setListError(e?.message || "加载失败");
      } finally {
        if (alive) setListLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  React.useEffect(() => {
    if (selectedTurn == null) return;
    let alive = true;
    setDetailLoading(true);
    setDetailError("");
    (async () => {
      try {
        const resp = await fetch(`/api/history/turn/${selectedTurn}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (alive) setDetail(data);
      } catch (e: any) {
        if (alive) setDetailError(e?.message || "加载失败");
      } finally {
        if (alive) setDetailLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [selectedTurn]);

  const subtitle = turns.length ? `共 ${turns.length} 月存档` : "尚无存档";

  return (
    <FullscreenModal title="史册：历代奏报与诏书" subtitle={subtitle} bgClass="modal-bg-state" onClose={onClose}>
      <div className="history-modal-body">
        <aside className="history-turn-list">
          {listLoading ? <p className="long-copy">加载中…</p> : null}
          {listError ? <p className="long-copy">加载失败：{listError}</p> : null}
          {!listLoading && !listError && turns.length === 0 ? (
            <p className="long-copy">尚无存档回合。</p>
          ) : null}
          <ul>
            {turns.slice().reverse().map((t) => {
              const active = t.turn === selectedTurn;
              const tags: string[] = [];
              if (t.has_report) tags.push("奏报");
              if (t.has_directive) tags.push("诏");
              if (t.has_extraction) tags.push("册");
              return (
                <li key={t.turn}>
                  <button
                    className={`history-turn-item ${active ? "active" : ""}`}
                    onClick={() => setSelectedTurn(t.turn)}
                  >
                    <b>{t.year} 年 {t.period} 月</b>
                    <small>第 {t.turn} 回合 · {tags.join(" / ") || "—"}</small>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>
        <article className="history-detail modal-scroll">
          <HistoryDetailView
            loading={detailLoading}
            error={detailError}
            detail={detail}
            selectedTurn={selectedTurn}
          />
        </article>
      </div>
    </FullscreenModal>
  );
}

type GameMenuTab = "save" | "load" | "llm" | "reset" | "exit_menu" | "shutdown";

const GAME_MENU_TABS: Array<{ id: GameMenuTab; label: string }> = [
  { id: "save", label: "保存存档" },
  { id: "load", label: "加载存档" },
  { id: "llm", label: "LLM 配置" },
  { id: "reset", label: "重开新局" },
  { id: "exit_menu", label: "回到主菜单" },
  { id: "shutdown", label: "退出游戏" },
];

function GameMenuTabIcon({ tab }: { tab: GameMenuTab }) {
  if (tab === "save") return <Save size={14} />;
  if (tab === "load") return <Upload size={14} />;
  if (tab === "llm") return <Settings size={14} />;
  if (tab === "reset") return <RotateCcw size={14} />;
  if (tab === "exit_menu") return <LogOut size={14} />;
  return <Power size={14} />;
}

function GameMenuModal({
  onClose,
  onAfterLoad,
  onExitToMenu,
}: {
  onClose: () => void;
  onAfterLoad: () => void;
  onExitToMenu: () => void;
}) {
  const [tab, setTab] = React.useState<GameMenuTab>("save");
  const titleId = React.useId();
  const tabsId = React.useId();
  const panelId = React.useId();
  const modalRef = React.useRef<HTMLDivElement | null>(null);
  const firstTabRef = React.useRef<HTMLButtonElement | null>(null);
  const closeRef = React.useRef<HTMLButtonElement | null>(null);
  const previousFocusRef = React.useRef<HTMLElement | null>(null);
  const onCloseRef = React.useRef(onClose);

  React.useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  const closeModal = React.useCallback(() => {
    onCloseRef.current();
  }, []);

  React.useEffect(() => {
    const activeElement = document.activeElement;
    previousFocusRef.current = activeElement instanceof HTMLElement ? activeElement : null;

    const focusTimer = window.setTimeout(() => {
      const modal = modalRef.current;
      const current = document.activeElement;
      if (modal && current instanceof HTMLElement && modal.contains(current)) return;
      (firstTabRef.current || closeRef.current)?.focus();
    }, 0);

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeModal();
        return;
      }
      if (event.key !== "Tab") return;

      const modal = modalRef.current;
      if (!modal) return;
      const focusable = Array.from(
        modal.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => (
        element.getAttribute("aria-hidden") !== "true"
        && (element.offsetWidth > 0 || element.offsetHeight > 0 || element.getClientRects().length > 0)
      ));

      if (!focusable.length) {
        event.preventDefault();
        closeRef.current?.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (!(active instanceof HTMLElement) || active === first || !modal.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKey);

    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKey);
      const previousFocus = previousFocusRef.current;
      if (previousFocus && document.contains(previousFocus)) {
        window.setTimeout(() => previousFocus.focus(), 0);
      }
    };
  }, [closeModal]);

  const focusTab = React.useCallback((nextTab: GameMenuTab) => {
    window.setTimeout(() => {
      document.getElementById(`${tabsId}-${nextTab}`)?.focus();
    }, 0);
  }, [tabsId]);

  const selectTab = React.useCallback((nextTab: GameMenuTab) => {
    setTab(nextTab);
    focusTab(nextTab);
  }, [focusTab]);

  const handleTabKeyDown = React.useCallback((event: React.KeyboardEvent<HTMLButtonElement>, currentTab: GameMenuTab) => {
    if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const currentIndex = GAME_MENU_TABS.findIndex((item) => item.id === currentTab);
    const lastIndex = GAME_MENU_TABS.length - 1;
    let nextIndex = currentIndex;
    if (event.key === "ArrowUp" || event.key === "ArrowLeft") nextIndex = currentIndex <= 0 ? lastIndex : currentIndex - 1;
    if (event.key === "ArrowDown" || event.key === "ArrowRight") nextIndex = currentIndex >= lastIndex ? 0 : currentIndex + 1;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = lastIndex;
    selectTab(GAME_MENU_TABS[nextIndex].id);
  }, [selectTab]);

  return (
    <section className="center-layer" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div className="center-scrim" aria-hidden="true" onClick={closeModal} />
      <div className="center-modal" ref={modalRef}>
        <header className="center-modal-header">
          <h1 id={titleId}>游戏菜单</h1>
          <button ref={closeRef} className="icon-button" aria-label="关闭弹窗" onClick={closeModal}>
            <X size={18} />
          </button>
        </header>
        <div className="game-menu">
          <nav className="game-menu-tabs" aria-label="游戏菜单分类">
            {GAME_MENU_TABS.map((item, index) => {
              const active = tab === item.id;
              return (
                <button
                  key={item.id}
                  id={`${tabsId}-${item.id}`}
                  ref={index === 0 ? firstTabRef : undefined}
                  className={active ? "active" : ""}
                  aria-current={active ? "page" : undefined}
                  aria-controls={active ? panelId : undefined}
                  onClick={() => selectTab(item.id)}
                  onKeyDown={(event) => handleTabKeyDown(event, item.id)}
                >
                  <GameMenuTabIcon tab={item.id} /> {item.label}
                </button>
              );
            })}
          </nav>
          <div className="game-menu-body" id={panelId} aria-labelledby={`${tabsId}-${tab}`}>
            {tab === "save" ? <SaveTab /> : null}
            {tab === "load" ? <LoadTab onAfterLoad={onAfterLoad} /> : null}
            {tab === "llm" ? <LLMConfigTab /> : null}
            {tab === "reset" ? <ResetTab onAfterReset={onAfterLoad} /> : null}
            {tab === "exit_menu" ? <ExitToMenuTab onExit={onExitToMenu} /> : null}
            {tab === "shutdown" ? <ShutdownTab /> : null}
          </div>
        </div>
      </div>
    </section>
  );
}

function SaveTab() {
  const [name, setName] = React.useState("");
  const [saves, setSaves] = React.useState<SaveEntry[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");
  const [err, setErr] = React.useState("");

  const refresh = React.useCallback(async () => {
    try {
      const data = await api<{ saves: SaveEntry[] }>("/api/saves");
      setSaves(data.saves);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  React.useEffect(() => {
    refresh();
  }, [refresh]);

  const onSave = async () => {
    if (!name.trim()) {
      setErr("请填存档名。");
      return;
    }
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      await api<{ save: { name: string }; saves: SaveEntry[] }>("/api/saves", {
        method: "POST",
        body: JSON.stringify({ name: name.trim() }),
      });
      setMsg(`已保存为 ${name.trim()}.db`);
      setName("");
      await refresh();
    } catch (e) {
      const detail = e instanceof ApiRequestError ? e.detail : null;
      setErr(detail ? `code: ${detail.code || "unknown"}\nmessage: ${detail.message || (e instanceof Error ? e.message : String(e))}` : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="menu-section">
      <h3>保存当前局</h3>
      <p className="menu-hint">将当前 DB 热备到 data/saves/&lt;名字&gt;.db。同名直接覆盖。</p>
      <div className="menu-row">
        <input
          className="menu-input"
          placeholder="存档名（字母/数字/._-）"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={busy}
        />
        <button className="menu-btn primary" onClick={onSave} disabled={busy}>
          {busy ? <Loader2 size={14} className="spin" /> : <Save size={14} />} 保存
        </button>
      </div>
      {msg ? <div className="menu-success">{msg}</div> : null}
      {err ? <div className="menu-error">{err}</div> : null}
      <h4>现有存档</h4>
      <SavesList saves={saves} onRefresh={refresh} />
    </section>
  );
}

function LoadTab({ onAfterLoad }: { onAfterLoad: () => void }) {
  const [saves, setSaves] = React.useState<SaveEntry[]>([]);
  const [busy, setBusy] = React.useState("");
  const [err, setErr] = React.useState("");
  const refresh = React.useCallback(async () => {
    try {
      const data = await api<{ saves: SaveEntry[] }>("/api/saves");
      setSaves(data.saves);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);
  React.useEffect(() => {
    refresh();
  }, [refresh]);

  const onLoad = async (n: string) => {
    if (!window.confirm(`确定加载 ${n}.db？当前未保存进度会丢失。`)) return;
    setBusy(n);
    setErr("");
    try {
      await api(`/api/saves/${encodeURIComponent(n)}/load`, { method: "POST" });
      onAfterLoad();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy("");
    }
  };

  return (
    <section className="menu-section">
      <h3>加载存档</h3>
      <p className="menu-hint">选一份覆盖回主 DB。加载后页面会自动重新载入。</p>
      {err ? <div className="menu-error">{err}</div> : null}
      <SavesList saves={saves} onRefresh={refresh} action={onLoad} busy={busy} />
    </section>
  );
}

function ResetTab({ onAfterReset }: { onAfterReset: () => void }) {
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");
  const [confirmText, setConfirmText] = React.useState("");

  const canReset = confirmText.trim() === "重开";

  const onReset = async () => {
    if (!canReset) return;
    if (!window.confirm("确定重开新局？当前局所有数据将被永久清空（存档目录不动）。")) return;
    setBusy(true);
    setErr("");
    try {
      await api("/api/game/reset", { method: "POST" });
      onAfterReset();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <section className="menu-section">
      <h3>重开新局</h3>
      <p className="menu-hint">
        清空主 DB（聊天记录、回合奏报、局势、ledger 全清），重置到天启七年十二月开局。
        <b>不可撤销</b>。要保留当前局，先到「保存存档」存一份。
      </p>
      <p className="menu-hint">输入「重开」二字以解锁按钮：</p>
      <div className="menu-row">
        <input
          className="menu-input"
          placeholder="输入：重开"
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          disabled={busy}
        />
        <button className="menu-btn danger" onClick={onReset} disabled={!canReset || busy}>
          {busy ? <Loader2 size={14} className="spin" /> : <RotateCcw size={14} />} 重开新局
        </button>
      </div>
      {err ? <div className="menu-error">{err}</div> : null}
    </section>
  );
}

function ExitToMenuTab({ onExit }: { onExit: () => void | Promise<void> }) {
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");
  const onClick = async () => {
    if (!window.confirm("回到主菜单？当前对局会关闭（DB 仍保留，可从「继续上局」回到此处）。")) return;
    setBusy(true);
    setErr("");
    try {
      await onExit();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };
  return (
    <section className="menu-section">
      <h3>回到主菜单</h3>
      <p className="menu-hint">
        关闭当前游戏会话，回到主菜单。数据库与存档不变；可从主菜单「继续上局」或「加载存档」回到游戏。
      </p>
      <div className="menu-row">
        <button className="menu-btn primary" onClick={onClick} disabled={busy}>
          {busy ? <Loader2 size={14} className="spin" /> : <LogOut size={14} />} 回到主菜单
        </button>
      </div>
      {err ? <div className="menu-error">{err}</div> : null}
    </section>
  );
}

function ShutdownTab() {
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");
  const onClick = async () => {
    if (!window.confirm("退出整个游戏？前后端进程都会关闭，未保存的进度会丢失。")) return;
    setBusy(true);
    setErr("");
    try {
      await fetch("/api/menu/shutdown", { method: "POST" });
      // server 已发 SIGTERM 给自己；前端尝试关页面（浏览器可能拦截），否则提示用户。
      setTimeout(() => {
        try { window.close(); } catch { /* noop */ }
      }, 400);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };
  return (
    <section className="menu-section">
      <h3>退出游戏</h3>
      <p className="menu-hint">
        终止服务进程并尝试关闭浏览器页面。<b>未保存的进度会丢失</b>。要保留当前局，先到「保存存档」。
      </p>
      <div className="menu-row">
        <button className="menu-btn danger" onClick={onClick} disabled={busy}>
          {busy ? <Loader2 size={14} className="spin" /> : <Power size={14} />} 退出游戏
        </button>
      </div>
      {err ? <div className="menu-error">{err}</div> : null}
    </section>
  );
}

function SavesList({
  saves,
  onRefresh,
  action,
  busy,
}: {
  saves: SaveEntry[];
  onRefresh: () => void;
  action?: (name: string) => void;
  busy?: string;
}) {
  const [delErr, setDelErr] = React.useState("");
  const onDelete = async (n: string) => {
    if (!window.confirm(`删除 ${n}.db？`)) return;
    try {
      await api(`/api/saves/${encodeURIComponent(n)}`, { method: "DELETE" });
      onRefresh();
    } catch (e) {
      setDelErr(e instanceof Error ? e.message : String(e));
    }
  };
  if (!saves.length) return <div className="menu-empty">尚无存档。</div>;
  return (
    <ul className="saves-list">
      {delErr ? <div className="menu-error">{delErr}</div> : null}
      {saves.map((s) => {
        const isAuto = s.kind === "auto";
        const badge = isAuto
          ? s.current
            ? "本局自动档"
            : `战局 ${s.campaign_id?.slice(0, 6) || "未知"}`
          : "手动存档";
        const displayName = s.label || s.name;
        return (
          <li key={s.name} className="saves-row">
            <div className="saves-name">
              <div className="saves-title-line">
                <b>{displayName}</b>
                <span className={`saves-badge ${isAuto ? "auto" : "manual"} ${s.current ? "current" : ""}`}>{badge}</span>
              </div>
              <small>
                {s.label ? `${s.name}.db · ` : ""}
                {new Date(s.mtime * 1000).toLocaleString()} · {(s.size / 1024).toFixed(1)} KB
              </small>
            </div>
            <div className="saves-actions">
              {action ? (
                <button className="menu-btn primary" disabled={busy === s.name} onClick={() => action(s.name)}>
                  {busy === s.name ? <Loader2 size={14} className="spin" /> : <Upload size={14} />} 加载
                </button>
              ) : null}
              <button className="menu-btn danger" onClick={() => onDelete(s.name)}>
                <Trash2 size={14} /> 删
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function LLMConfigTab() {
  const [info, setInfo] = React.useState<LLMConfigInfo | null>(null);
  const [baseUrl, setBaseUrl] = React.useState("");
  const [model, setModel] = React.useState("");
  const [advancedModel, setAdvancedModel] = React.useState("");
  const [advancedBaseUrl, setAdvancedBaseUrl] = React.useState("");
  const [advancedApiKey, setAdvancedApiKey] = React.useState("");
  const [advancedThinkingLevel, setAdvancedThinkingLevel] = React.useState("");
  const [apiKey, setApiKey] = React.useState("");
  const [maxTokens, setMaxTokens] = React.useState("8000");
  const [timeoutSeconds, setTimeoutSeconds] = React.useState("180");
  const [thinkingLevel, setThinkingLevel] = React.useState("");
  const [show, setShow] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");
  const [err, setErr] = React.useState("");

  React.useEffect(() => {
    api<LLMConfigInfo>("/api/llm/config")
      .then((data) => {
        setInfo(data);
        setBaseUrl(data.base_url);
        setModel(data.model);
        setAdvancedModel(data.advanced_model || "");
        setAdvancedBaseUrl(data.advanced_base_url || "");
        setAdvancedThinkingLevel(data.advanced_thinking_level || "");
        setMaxTokens(String(data.max_tokens || 8000));
        setTimeoutSeconds(String(data.timeout_seconds || 180));
        setThinkingLevel(data.thinking_level || "");
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  const onSave = async () => {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const data = await api<LLMConfigInfo>("/api/llm/config", {
        method: "POST",
        body: JSON.stringify({
          base_url: baseUrl,
          model,
          api_key: apiKey,
          max_tokens: parseInt(maxTokens) || 8000,
          timeout_seconds: parseFloat(timeoutSeconds) || 180,
          thinking_level: thinkingLevel.trim(),
          advanced_model: advancedModel,
          advanced_base_url: advancedBaseUrl,
          advanced_api_key: advancedApiKey.trim() ? advancedApiKey : "__keep__",
          advanced_thinking_level: advancedThinkingLevel.trim(),
        }),
      });
      setInfo((cur) => (cur ? { ...cur, ...data } : null));
      setApiKey("");
      setAdvancedApiKey("");
      setMsg("已生效并写入 data/runtime_llm.json。");
    } catch (e) {
      const detail = e instanceof ApiRequestError ? e.detail : null;
      setErr(detail ? `code: ${detail.code || "unknown"}\nmessage: ${detail.message || (e instanceof Error ? e.message : String(e))}` : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="menu-section">
      <h3>LLM 配置</h3>
      <p className="menu-hint">
        立即生效并写入 <code>data/runtime_llm.json</code>，重启进程后自动加载。api_key 留空保留当前。
      </p>
      <label className="menu-field">
        <span>Base URL</span>
        <input
          className="menu-input"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://api.openai.com/v1"
        />
      </label>
      <label className="menu-field">
        <span>Model</span>
        <input
          className="menu-input"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="gpt-4o-mini"
        />
      </label>
      <label className="menu-field">
        <span>Thinking Level <small className="menu-hint">（空=默认，请填写你的模型支持的值。）</small></span>
        <input
          className="menu-input"
          value={thinkingLevel}
          onChange={(e) => setThinkingLevel(e.target.value)}
          placeholder="默认"
        />
      </label>
      <label className="menu-field">
        <span>Advanced Model <small className="menu-hint">（推演 + 打分专用，空=与 Model 一致）</small></span>
        <input
          className="menu-input"
          value={advancedModel}
          onChange={(e) => setAdvancedModel(e.target.value)}
          placeholder="deepseek-reasoner / gpt-5（留空 fallback）"
        />
      </label>
      <label className="menu-field">
        <span>Advanced Base URL <small className="menu-hint">（advanced 专用网关，空=与 Base URL 一致）</small></span>
        <input
          className="menu-input"
          value={advancedBaseUrl}
          onChange={(e) => setAdvancedBaseUrl(e.target.value)}
          placeholder="https://other-gateway/v1（留空复用主 Base URL）"
        />
      </label>
      <label className="menu-field">
        <span>
          Advanced API Key{" "}
          {info?.has_advanced_api_key ? (
            <small className="ok">（当前已设置）</small>
          ) : (
            <small className="menu-hint">（空=复用主 API Key）</small>
          )}
        </span>
        <input
          className="menu-input"
          type={show ? "text" : "password"}
          value={advancedApiKey}
          onChange={(e) => setAdvancedApiKey(e.target.value)}
          placeholder="留空=复用主 API Key / 保留当前"
        />
      </label>
      <label className="menu-field">
        <span>Advanced Thinking Level <small className="menu-hint">（空=默认，请填写你的模型支持的值。）</small></span>
        <input
          className="menu-input"
          value={advancedThinkingLevel}
          onChange={(e) => setAdvancedThinkingLevel(e.target.value)}
          placeholder="默认"
        />
      </label>
      <label className="menu-field">
        <span>Max Tokens</span>
        <input
          className="menu-input"
          type="number"
          min={256}
          max={65536}
          value={maxTokens}
          onChange={(e) => setMaxTokens(e.target.value)}
          placeholder="8000"
        />
      </label>
      <label className="menu-field">
        <span>Timeout Seconds</span>
        <input
          className="menu-input"
          type="number"
          min={10}
          max={900}
          value={timeoutSeconds}
          onChange={(e) => setTimeoutSeconds(e.target.value)}
          placeholder="180"
        />
      </label>
      <label className="menu-field">
        <span>
          API Key{" "}
          {info?.has_api_key ? <small className="ok">（当前已设置）</small> : <small className="warn">（未设置）</small>}
        </span>
        <div className="menu-row">
          <input
            className="menu-input"
            type={show ? "text" : "password"}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={info?.has_api_key ? "留空保留当前" : "请输入"}
            autoComplete="off"
          />
          <button className="menu-btn" type="button" onClick={() => setShow((v) => !v)}>
            {show ? "隐" : "显"}
          </button>
        </div>
      </label>
      <div className="menu-row">
        <button className="menu-btn primary" onClick={onSave} disabled={busy}>
          {busy ? <Loader2 size={14} className="spin" /> : <Check size={14} />} 保存并应用
        </button>
      </div>
      {msg ? <div className="menu-success">{msg}</div> : null}
      {err ? <div className="menu-error">{err}</div> : null}
    </section>
  );
}

function HistoryDetailView({
  loading,
  error,
  detail,
  selectedTurn,
}: {
  loading: boolean;
  error: string;
  detail: HistoryDetail | null;
  selectedTurn: number | null;
}) {
  if (selectedTurn == null) return <div className="document-section"><p className="long-copy">请从左侧择月。</p></div>;
  if (loading) return <div className="document-section"><p className="long-copy">加载中…</p></div>;
  if (error) return <div className="document-section"><p className="long-copy">加载失败：{error}</p></div>;
  if (!detail || !detail.exists) return <div className="document-section"><p className="long-copy">该回合无存档。</p></div>;

  return (
    <>
      {detail.decree_text ? (
        <section className="document-section">
          <h3 className="extraction-section-title">本月诏书</h3>
          <pre className="memorial-text">{detail.decree_text}</pre>
        </section>
      ) : null}

      {detail.directives.length ? (
        <section className="document-section">
          <h3 className="extraction-section-title">已颁草案（{detail.directives.length} 道）</h3>
          <ul className="history-directive-list">
            {detail.directives.map((d) => (
              <li key={d.id} className="history-directive-item">
                <div className="history-directive-head">
                  <b>#{d.id}</b>
                  {d.event_title ? <span>事项：{d.event_title}</span> : null}
                  {d.actor ? <span>主官：{d.actor}</span> : null}
                  {d.skill_id ? <span>技能：{d.skill_id}</span> : null}
                  <span className="history-directive-source">{d.source}</span>
                </div>
                <pre className="memorial-text">{d.text}</pre>
                {d.notes ? <div className="history-directive-notes">备注：{d.notes}</div> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {detail.report ? (
        <section className="document-section">
          <h3 className="extraction-section-title">月末邸报奏报</h3>
          <pre className="memorial-text">{detail.report}</pre>
        </section>
      ) : null}

      {detail.extraction && detail.extraction.exists ? (
        <section className="document-section">
          <h3 className="extraction-section-title">邸报详明（extractor 解析）</h3>
          <ExtractionView data={detail.extraction} loading={false} error="" />
        </section>
      ) : null}
    </>
  );
}

function ExtractionView({ data, loading, error }: { data: ExtractionData | null; loading: boolean; error: string }) {
  if (loading) return <div className="document-section"><p className="long-copy">加载中…</p></div>;
  if (error) return <div className="document-section"><p className="long-copy">加载失败：{error}</p></div>;
  if (!data || !data.exists) return <div className="document-section"><p className="long-copy">该回合无 extractor 数据。</p></div>;
  const rawOut = data.extractor_output;
  if (!rawOut || typeof rawOut !== "object") {
    return (
      <div className="document-section">
        <CausalNotesBlock notes={data.causal_notes} />
        <pre className="memorial-text">{String(rawOut ?? "")}</pre>
      </div>
    );
  }
  // modular 结构：数据在 merged（已合并扁平），顶层只有 mode/modules/merged/raw。老存档是扁平对象，直接用。
  const out = (rawOut as any).mode === "modular" && (rawOut as any).merged && typeof (rawOut as any).merged === "object"
    ? (rawOut as any).merged
    : rawOut;
  return (
    <div className="document-section extraction-view">
      <CausalNotesBlock notes={data.causal_notes} />
      <ExtractionSection title="国势变化">
        <MetricDeltaBlock data={pickField(out, "国势变化", "metric_delta")} />
      </ExtractionSection>
      <ExtractionSection title="钱粮收支">
        <EconomyBlock data={pickField(out, "钱粮收支", "economy_moves")} />
      </ExtractionSection>
      <ExtractionSection title="派系变化">
        <FactionBlock data={pickField(out, "派系变化", "faction_delta")} />
      </ExtractionSection>
      <ExtractionSection title="阶级变化">
        <ClassDeltaBlock data={pickField(out, "阶级变化", "class_delta")} />
      </ExtractionSection>
      <ExtractionSection title="官职任免">
        <OfficeChangesBlock data={pickField(out, "人事变更", "office_changes")} />
      </ExtractionSection>
      <ExtractionSection title="去职变更">
        <StatusChangesBlock data={pickField(out, "人物状态变化", "character_status_changes")} />
      </ExtractionSection>
      <ExtractionSection title="人物易主">
        <PowerChangesBlock data={pickField(out, "人物易主", "character_power_changes")} />
      </ExtractionSection>
      <ExtractionSection title="后宫纳妃">
        <AppointmentsBlock data={pickField(out, "后宫册封", "appointments")} />
      </ExtractionSection>
      <ExtractionSection title="局势推进">
        <IssueAdvancesBlock data={pickField(out, "局势推进", "issue_advances")} />
      </ExtractionSection>
      <ExtractionSection title="新立局势">
        <NewIssuesBlock data={pickField(out, "新立局势", "new_issues")} />
      </ExtractionSection>
      <ExtractionSection title="结案 / 失败">
        <CloseIssuesBlock data={pickField(out, "结案局势", "close_issues")} />
      </ExtractionSection>
      <ExtractionSection title="撤旨">
        <CancelsBlock data={pickField(out, "撤销局势", "cancels")} />
      </ExtractionSection>
      <ExtractionSection title="地区变化">
        <EntityDeltaBlock data={pickField(out, "地区变化", "region_delta")} labelFn={labelRegion} />
      </ExtractionSection>
      <ExtractionSection title="军队变化">
        <EntityDeltaBlock data={pickField(out, "军队变化", "army_delta")} labelFn={labelArmy} />
      </ExtractionSection>
      <ExtractionSection title="新建军队">
        <NewArmiesBlock data={pickField(out, "新建军队", "new_armies")} />
      </ExtractionSection>
      <ExtractionSection title="势力变化">
        <EntityDeltaBlock data={pickField(out, "势力变化", "power_updates")} labelFn={labelPower} />
      </ExtractionSection>
      <ExtractionSection title="财政系数">
        <FiscalBlock data={pickField(out, "财政制度变化", "fiscal_changes")} />
      </ExtractionSection>
      <ExtractionSection title="外交关系">
        <DiplomacyBlock data={pickField(out, "外交关系", "world_advance") ?? pickField(out, "外交", "world_advance") ?? pickField(out, "外交态度", "world_advance") ?? pickField(out, "四方动向", "world_advance")} />
      </ExtractionSection>
      <ExtractionSection title="密令副作用">
        <SecretSideBlock data={pickField(out, "密令副作用", "secret_order_updates")} />
      </ExtractionSection>
      <ExtractionSection title="密令核议">
        <SecretCloseBlock data={pickField(out, "密令结案", "secret_order_closes")} />
      </ExtractionSection>
    </div>
  );
}

function pickField(obj: any, cn: string, en: string): any {
  if (!obj || typeof obj !== "object") return undefined;
  return obj[cn] ?? obj[en];
}

function pickItem(obj: any, cn: string, en: string): any {
  if (!obj || typeof obj !== "object") return undefined;
  return obj[cn] ?? obj[en];
}

function ExtractionSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="extraction-section">
      <h3 className="extraction-section-title">{title}</h3>
      <div className="extraction-section-body">{children}</div>
    </section>
  );
}

function fmtDelta(n: any): string {
  // 缺失/非数（extractor 偶尔不带 delta_bar）按 0 处理，避免渲染出字面 "undefined"
  const num = Number(n);
  if (!Number.isFinite(num)) return "0";
  if (num > 0) return `+${num}`;
  return String(num);
}

function isEmptyData(d: any): boolean {
  if (d == null) return true;
  if (Array.isArray(d)) return d.length === 0;
  if (typeof d === "object") return Object.keys(d).length === 0;
  return false;
}

function MetricDeltaBlock({ data }: { data: any }) {
  if (isEmptyData(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-kv">
      {Object.entries(data).map(([k, v]) => (
        <li key={k}><span>{k}</span><b className={Number(v) >= 0 ? "good" : "bad"}>{fmtDelta(v)}</b></li>
      ))}
    </ul>
  );
}

function EconomyBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((item: any, i: number) => (
        <li key={i}>
          <b className={Number(pickItem(item, "增量", "delta")) >= 0 ? "good" : "bad"}>
            {pickItem(item, "账户", "account") || "?"} {fmtDelta(pickItem(item, "增量", "delta"))} 万
          </b>
          <span>{pickItem(item, "分类", "category") || ""}{pickItem(item, "原因", "reason") ? ` — ${pickItem(item, "原因", "reason")}` : ""}</span>
        </li>
      ))}
    </ul>
  );
}

function FactionBlock({ data }: { data: any }) {
  if (isEmptyData(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-kv">
      {Object.entries(data).map(([k, v]: [string, any]) => {
        if (v && typeof v === "object") {
          return (
            <li key={k}>
              <span>{k}</span>
              <b>{Object.entries(v).map(([kk, vv]) => `${SAT_LEV_CN[kk] || cnField(kk)}${fmtDelta(vv)}`).join("  ")}</b>
            </li>
          );
        }
        return <li key={k}><span>{k}</span><b className={Number(v) >= 0 ? "good" : "bad"}>{fmtDelta(v)}</b></li>;
      })}
    </ul>
  );
}

function IssueAdvancesBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b className={Number(pickItem(it, "进度增量", "delta_bar")) >= 0 ? "good" : "bad"}>
            {labelIssue(pickItem(it, "局势编号", "issue_id"))} 进度 {fmtDelta(pickItem(it, "进度增量", "delta_bar"))}
            {pickItem(it, "惯性增量", "inertia_delta") ? `，惯性 ${fmtDelta(pickItem(it, "惯性增量", "inertia_delta"))}` : ""}
          </b>
          {pickItem(it, "阶段", "stage_text") ? <span>{pickItem(it, "阶段", "stage_text")}</span> : null}
          {pickItem(it, "叙述", "narrative") ? <span className="extraction-narr">{pickItem(it, "叙述", "narrative")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function NewIssuesBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b>{pickItem(it, "标题", "title") || pickItem(it, "编号", "id") || "新事项"}（{cnValue(pickItem(it, "类型", "kind") || pickItem(it, "来源类型", "origin_kind") || "")}）</b>
          {pickItem(it, "阶段", "stage_text") ? <span>{pickItem(it, "阶段", "stage_text")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function CloseIssuesBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b className={pickItem(it, "原因", "reason") === "resolved" ? "good" : "bad"}>
            {labelIssue(pickItem(it, "局势编号", "issue_id"))} {pickItem(it, "原因", "reason") === "resolved" ? "结案" : "失败"}
          </b>
          {pickItem(it, "叙述", "narrative") ? <span>{pickItem(it, "叙述", "narrative")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function CancelsBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b>{labelIssue(pickItem(it, "局势编号", "issue_id"))} 撤旨</b>
          {pickItem(it, "叙述", "narrative") ? <span>{pickItem(it, "叙述", "narrative")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function OfficeChangesBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b className={pickItem(it, "rejected", "rejected") ? "bad" : "good"}>
            {pickItem(it, "姓名", "name")} → {pickItem(it, "新官职", "new_office")}
            {pickItem(it, "新官署类别", "new_office_type") ? `（${pickItem(it, "新官署类别", "new_office_type")}）` : ""}
            {pickItem(it, "rejected", "rejected") ? "（未落地）" : pickItem(it, "kind", "kind") === "appoint" ? "（新进朝堂）" : ""}
          </b>
          {pickItem(it, "displaced", "displaced") ? <span>顶替 {pickItem(it, "displaced", "displaced")} 去职</span> : null}
          {pickItem(it, "原因", "reason") ? <span>{pickItem(it, "原因", "reason")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function StatusChangesBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  const label: Record<string, string> = {
    dismissed: "罢黜", imprisoned: "下狱", exiled: "流放",
    retired: "致仕", dead: "身故", offstage: "去位",
  };
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b className={pickItem(it, "rejected", "rejected") ? "bad" : ""}>
            {pickItem(it, "姓名", "name")} {label[pickItem(it, "状态", "status")] || cnValue(pickItem(it, "状态", "status"))}
            {pickItem(it, "rejected", "rejected") ? "（未落地）" : ""}
          </b>
          {pickItem(it, "原因", "reason") ? <span>{pickItem(it, "原因", "reason")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function AppointmentsBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b className={pickItem(it, "rejected", "rejected") ? "bad" : "good"}>
            {pickItem(it, "姓名", "name")} 册封 {pickItem(it, "位号", "office")}
            {pickItem(it, "rejected", "rejected") ? "（未落地）" : ""}
          </b>
          {pickItem(it, "原因", "reason") ? <span>{pickItem(it, "原因", "reason")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function FiscalBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b className={Number(pickItem(it, "增量", "delta")) >= 0 ? "good" : "bad"}>
            {fiscalKeyLabel(pickItem(it, "键", "key"))} {fmtDelta(pickItem(it, "增量", "delta"))}
          </b>
          {pickItem(it, "原因", "reason") ? <span>{pickItem(it, "原因", "reason")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

// 一个字段值渲染成可读串：数字带正负号，文字直接显示（英文枚举翻中文）。
function fmtFieldVal(v: any): { text: string; tone: string } {
  if (typeof v === "number") return { text: fmtDelta(v), tone: v >= 0 ? "good" : "bad" };
  const n = Number(v);
  if (v !== "" && v != null && Number.isFinite(n) && String(v).trim() !== "" && !isNaN(n) && /^-?\d+$/.test(String(v).trim())) {
    return { text: fmtDelta(n), tone: n >= 0 ? "good" : "bad" };
  }
  return { text: cnValue(v), tone: "" };
}

// 地区/军队/势力变化：外层 key=实体 id（翻中文名），内层=字段→增量/新值。
function EntityDeltaBlock({ data, labelFn }: { data: any; labelFn: (id: any) => string }) {
  if (isEmptyData(data) || typeof data !== "object" || Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {Object.entries(data).map(([id, fields]: [string, any]) => (
        <li key={id}>
          <b>{labelFn(id)}</b>
          {fields && typeof fields === "object" && !Array.isArray(fields) ? (
            <span className="extraction-fieldline">
              {Object.entries(fields).map(([fk, fv]) => {
                const { text, tone } = fmtFieldVal(fv);
                return <em key={fk} className={tone}>{cnField(fk)} {text}</em>;
              })}
            </span>
          ) : (
            <span>{cnValue(fields)}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

// 外交关系：key=势力 id（翻中文名），value=态度字符串。
function DiplomacyBlock({ data }: { data: any }) {
  if (isEmptyData(data) || typeof data !== "object" || Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-kv">
      {Object.entries(data).map(([id, stance]: [string, any]) => (
        <li key={id}><span>{labelPower(id)}</span><b>{cnValue(stance)}</b></li>
      ))}
    </ul>
  );
}

// 阶级变化：key=阶级名 或 阶级@region_id；region 后缀翻中文名。value={满意,影响力} 增量。
const SAT_LEV_CN: Record<string, string> = { satisfaction: "满意", leverage: "影响力", 满意: "满意", 影响力: "影响力" };
function labelClass(key: string): string {
  const at = key.indexOf("@");
  if (at < 0) return key;
  return `${key.slice(0, at)}（${labelRegion(key.slice(at + 1))}）`;
}
function ClassDeltaBlock({ data }: { data: any }) {
  if (isEmptyData(data) || typeof data !== "object" || Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-kv">
      {Object.entries(data).map(([k, v]: [string, any]) => {
        if (v && typeof v === "object") {
          return (
            <li key={k}>
              <span>{labelClass(k)}</span>
              <b>{Object.entries(v).map(([kk, vv]) => `${SAT_LEV_CN[kk] || cnField(kk)}${fmtDelta(vv)}`).join("  ")}</b>
            </li>
          );
        }
        return <li key={k}><span>{labelClass(k)}</span><b className={Number(v) >= 0 ? "good" : "bad"}>{fmtDelta(v)}</b></li>;
      })}
    </ul>
  );
}

// 人物易主：姓名 → 新势力（翻中文名）。
function PowerChangesBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b>{pickItem(it, "姓名", "name")} → {labelPower(pickItem(it, "new_power", "new_power"))}</b>
          {pickItem(it, "reason", "reason") || pickItem(it, "原因", "reason") ? <span>{pickItem(it, "reason", "reason") || pickItem(it, "原因", "reason")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

// 密令副作用：active 密令的推演副作用。
function SecretSideBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b>密令 #{pickItem(it, "密令编号", "order_id")}</b>
          <span>{pickItem(it, "推演备注", "sim_note") || ""}</span>
        </li>
      ))}
    </ul>
  );
}

// 密令核议：pending_review 密令结案判定。
function SecretCloseBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => {
        const st = pickItem(it, "状态", "status");
        return (
          <li key={i}>
            <b className={st === "done" ? "good" : "bad"}>
              密令 #{pickItem(it, "密令编号", "order_id")} {st === "done" ? "办结" : "失败"}
            </b>
            <span>{pickItem(it, "结果", "result") || ""}</span>
          </li>
        );
      })}
    </ul>
  );
}

function NewArmiesBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((item: any, i: number) => {
        const name = pickItem(item, "名称", "name") || pickItem(item, "编号", "id") || "?";
        const owner = labelPower(pickItem(item, "归属", "owner_power")) || "?";
        const manpower = pickItem(item, "人数", "manpower");
        const station = pickItem(item, "驻扎地", "station") || "";
        const commander = pickItem(item, "统将", "commander") || "";
        return (
          <li key={i}>
            <b>{name}</b>（归属：{owner}）{manpower ? ` · ${manpower}人` : ""}
            <span>{station}{commander ? ` · ${commander}` : ""}</span>
          </li>
        );
      })}
    </ul>
  );
}

function PreviousSummary({ summary }: { summary: string }) {
  if (!summary) {
    return <p className="long-copy">登基伊始，尚无上月回奏。</p>;
  }
  const lines = summary.split("\n").map((line) => line.trim()).filter(Boolean);
  const rows = lines
    .map((line) => {
      const idx = line.indexOf("：");
      if (idx <= 0) return null;
      return { label: line.slice(0, idx), value: line.slice(idx + 1) };
    })
    .filter((row): row is { label: string; value: string } => !!row && !!row.value);

  if (!rows.length) {
    return <p className="long-copy">{summary}</p>;
  }

  return (
    <table className="summary-table">
      <tbody>
        {rows.map((row) => (
          <tr key={row.label}>
            <th>{row.label}</th>
            <td>{row.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StateModal({ state }: { state: GameState }) {
  const report = state.last_report || state.previous_summary;
  const emptyBudget: BudgetAccount = {
    balance: 0,
    income: [],
    expense: [],
    income_total: 0,
    expense_total: 0,
    net: 0,
    movements: [],
    movements_total: 0,
  };
  const treasuryBudget = state.budget?.["国库"] || emptyBudget;
  const privateBudget = state.budget?.["内库"] || emptyBudget;
  const peopleScore = state.metrics?.["民心"] ?? 0;
  const authorityScore = state.metrics?.["皇威"] ?? 0;
  const activeIssues = (state.issues || [])
    .filter((issue) => issue.kind === "situation" || issue.kind === "initiative")
    .sort((a, b) => a.bar_value - b.bar_value);
  const urgentIssue = activeIssues[0] || null;
  const draftCount = (state.directives || []).filter((directive) => directive.status !== "pending").length;
  const pendingCount = (state.directives || []).filter((directive) => directive.status === "pending").length;
  return (
    <article className="state-document modal-scroll">
      <section className="document-section state-brief-section" aria-label="当前国势摘要">
        <div className="state-brief-head">
          <span>御前摘要</span>
          <b>{state.turn.year} 年 {state.turn.period} 月</b>
        </div>
        <div className="state-brief-grid">
          <div className={`state-brief-card ${treasuryBudget.net >= 0 ? "good" : "danger"}`}>
            <span>国库</span>
            <b>{formatMoney(treasuryBudget.balance)}</b>
            <small>月净 {formatSignedMoney(treasuryBudget.net)}</small>
          </div>
          <div className={`state-brief-card ${privateBudget.net >= 0 ? "good" : "warn"}`}>
            <span>内库</span>
            <b>{formatMoney(privateBudget.balance)}</b>
            <small>月净 {formatSignedMoney(privateBudget.net)}</small>
          </div>
          <div className={`state-brief-card ${scoreTone(peopleScore, false)}`}>
            <span>民心</span>
            <b>{peopleScore}</b>
            <small>低则民变、抗粮加剧</small>
          </div>
          <div className={`state-brief-card ${scoreTone(authorityScore, false)}`}>
            <span>皇威</span>
            <b>{authorityScore}</b>
            <small>低则诏令摩擦增大</small>
          </div>
        </div>
        <div className="state-brief-ledger">
          <span>待阅奏疏 <b>{state.events?.length ?? 0}</b></span>
          <span>草案 <b>{draftCount}</b></span>
          <span>待核指令 <b>{pendingCount}</b></span>
          <span>本月结案 <b>{state.closed_this_turn?.length ?? 0}</b></span>
        </div>
        {urgentIssue ? (
          <div className={`state-brief-issue ${issueTone(urgentIssue.bar_value)}`}>
            <span>最紧局势</span>
            <b>{urgentIssue.title} · {urgentIssue.bar_value}/100</b>
            <small>{urgentIssue.stage_text || urgentIssue.ongoing_text || urgentIssue.fail_condition}</small>
          </div>
        ) : (
          <div className="state-brief-issue good">
            <span>局势</span>
            <b>暂无进行中局势</b>
            <small>本月没有需要持续推进的危机条。</small>
          </div>
        )}
      </section>
      <section className="document-section state-report-section">
        <h2>上月奏报</h2>
        {report
          ? <pre className="memorial-text">{report}</pre>
          : <div className="empty-note">尚无上月奏报。</div>}
      </section>
    </article>
  );
}

function AdventureLogModal({ state, onClose }: { state: GameState; onClose: () => void }) {
  const adventures = state.adventures || [];
  const items = state.items || [];
  const rarityClass = (r: string) => {
    switch (r) {
      case "legendary": return "rarity-legendary";
      case "epic": return "rarity-epic";
      case "rare": return "rarity-rare";
      default: return "rarity-common";
    }
  };
  return (
    <FullscreenModal title="天命异闻" subtitle="月末秘奏、异象与处置验算" bgClass="modal-bg-state" onClose={onClose}>
      <div className="modal-scroll">
        {adventures.length === 0 && items.length === 0 ? (
          <div className="empty-note">暂无异闻。月末结算时，秘奏、谣言、天象或地方暗线可能低频入档。</div>
        ) : null}
        {adventures.length > 0 && (
          <section className="document-section">
            <h2>异闻记录</h2>
            <div className="adventure-list">
              {adventures.map((adv, idx) => (
                <div className={`adventure-card ${adv.success ? "success" : "fail"}`} key={`${adv.adventure_id}-${idx}`}>
                  <div className="adventure-head">
                    <span className="adventure-title">{adv.title}</span>
                    <span className="adventure-turn">{adv.year}年{adv.period}月</span>
                  </div>
                  <div className="adventure-choice">处置：{adv.choice}</div>
                  <div className="adventure-narrative">{adv.narrative}</div>
                  {adv.items_found.length > 0 && (
                    <div className="adventure-loot">
                      获得：{adv.items_found.join("、")}
                    </div>
                  )}
                  {adv.success ? (
                    <span className="adventure-badge success">得力</span>
                  ) : (
                    <span className="adventure-badge fail">失措</span>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}
        {items.length > 0 && (
          <section className="document-section">
            <h2>档内物证</h2>
            <div className="item-grid">
              {items.map((item) => (
                <div className={`item-card ${rarityClass(item.rarity)} ${item.equipped ? "equipped" : ""}`} key={item.id}>
                  <div className="item-name">{item.name}</div>
                  <div className="item-meta">
                    <span className={`item-rarity ${rarityClass(item.rarity)}`}>{item.rarity}</span>
                    <span className="item-category">{item.category}</span>
                    {item.equipped && <span className="item-equipped">已装备</span>}
                  </div>
                  <div className="item-quantity">×{item.quantity}</div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </FullscreenModal>
  );
}

function BriefReport({ title, items }: { title: string; items: string[] }) {
  return (
    <article>
      <h2>{title}</h2>
      <ul className="brief-list">
        {items.map((item) => <li key={`${title}-${item}`}>{item}</li>)}
      </ul>
    </article>
  );
}

function SituationPanel({
  issues,
  closedIssues,
  hasLegacies,
}: {
  issues: Issue[];
  closedIssues: ClosedIssue[];
  hasLegacies: boolean;
}) {
  const active = issues.filter((issue) => issue.kind === "situation" || issue.kind === "initiative");
  const [collapsed, setCollapsed] = React.useState(() =>
    typeof window !== "undefined" && window.matchMedia("(max-width: 820px)").matches
  );
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const media = window.matchMedia("(max-width: 820px)");
    const syncCollapsedToViewport = () => setCollapsed(media.matches);
    syncCollapsedToViewport();
    media.addEventListener("change", syncCollapsedToViewport);
    return () => media.removeEventListener("change", syncCollapsedToViewport);
  }, []);
  if (!active.length && !closedIssues.length) return null;
  const bySeq = (a: Issue, b: Issue) => {
    if (a.kind !== b.kind) return a.kind === "initiative" ? -1 : 1;
    return a.id - b.id;
  };
  // 长期局势＝贯穿一朝的大计（甲申国亡前不结案），靠 fail_condition 文案判定，纯前端分组。
  const isLongTerm = (issue: Issue) => /甲申|贯穿一朝|倾国之大计/.test(issue.fail_condition || "");
  const longTerm = active.filter(isLongTerm).sort(bySeq);
  const nearTerm = active.filter((i) => !isLongTerm(i)).sort(bySeq);
  return (
    <aside className={`situation-panel ${collapsed ? "collapsed" : ""} ${hasLegacies ? "with-legacies" : ""}`} aria-label="局势进度">
      <div className="situation-panel-title">
        <span>局势进度</span>
        <button
          type="button"
          className="situation-toggle"
          aria-label={collapsed ? "展开局势" : "收起局势"}
          aria-expanded={!collapsed}
          onClick={() => setCollapsed((c) => !c)}
        >{collapsed ? "+" : "−"}</button>
      </div>
      {!collapsed && closedIssues.length ? (
        <div className="situation-closed-list">
          {closedIssues.map((ci) => (
            <div className={`situation-closed-row ${ci.status}`} key={`closed-${ci.id}`} tabIndex={0}>
              <div className="situation-closed-head">
                <span className="situation-closed-badge">{ci.status === "resolved" ? "已结案" : ci.status === "failed" ? "已崩坏" : "已撤"}</span>
                <span className="situation-closed-name">{ci.title}</span>
              </div>
              <div className="situation-closed-effect">{formatClosedEffect(ci.effect)}</div>
            </div>
          ))}
        </div>
      ) : null}
      {!collapsed && (longTerm.length ? (
        <div className="situation-group">
          <div className="situation-group-title">长期局势</div>
          <div className="situation-list">
            {longTerm.map((issue) => <SituationRow key={issue.id} issue={issue} />)}
          </div>
        </div>
      ) : null)}
      {!collapsed && (nearTerm.length ? (
        <div className="situation-group">
          <div className="situation-group-title">近期局势</div>
          <div className="situation-list">
            {nearTerm.map((issue) => <SituationRow key={issue.id} issue={issue} />)}
          </div>
        </div>
      ) : null)}
    </aside>
  );
}

function SituationRow({ issue }: { issue: Issue }) {
  return (
    <div className={`situation-row ${issueTone(issue.bar_value)}`} tabIndex={0}>
      <div className="situation-row-head">
        <span className="situation-name">{issue.title}</span>
        <b>{issue.bar_value}</b>
      </div>
      <div className="situation-bar">
        <i style={{ width: `${Math.max(0, Math.min(100, issue.bar_value))}%` }} />
      </div>
      <div className="situation-tip" role="tooltip">
        <div className="situation-tip-head">#{issue.id} {issue.title}</div>
        <div className="situation-tip-row"><span>阶段</span><b>{issue.phase}</b></div>
        <div className="situation-tip-row"><span>进度</span><b>{issue.bar_value} / 100</b></div>
        <div className="situation-tip-row">
          <span>月度推进</span>
          <b>{issue.inertia > 0 ? `+${issue.inertia}` : issue.inertia}/月</b>
        </div>
        <div className="situation-tip-row">
          <span>当前影响</span>
          <b>{issue.ongoing_text || "无"}</b>
        </div>
        <p className="situation-tip-stage">{issue.stage_text}</p>
        <div className="situation-tip-outcome good">
          <div className="situation-tip-outcome-head">达成（{issue.bar_good_meaning}）</div>
          {issue.resolve_condition && <p>{issue.resolve_condition}</p>}
          <div className="situation-tip-effect">{formatIssueEffect(issue.effect_on_resolve)}</div>
        </div>
        <div className="situation-tip-outcome bad">
          <div className="situation-tip-outcome-head">失败（{issue.bar_bad_meaning}）</div>
          {issue.fail_condition && <p>{issue.fail_condition}</p>}
          <div className="situation-tip-effect">{formatIssueEffect(issue.effect_on_fail)}</div>
        </div>
        {issue.tags.length ? (
          <div className="situation-tip-tags">
            {issue.tags.map((tag) => <small key={tag}>{tag}</small>)}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function IssueGroup({ title, issues }: { title: string; issues: Issue[] }) {
  if (!issues.length) return null;
  return (
    <div className="issue-group">
      <h3>{title}</h3>
      <div className="issue-list">
        {issues.map((issue) => (
          <article className={`issue-line ${issueTone(issue.bar_value)}`} key={issue.id}>
            <div className="issue-head">
              <b>#{issue.id} {issue.title}</b>
              <span>{issue.phase} · {issue.bar_value}</span>
            </div>
            <div className="issue-progress" aria-label={`${issue.title}进度 ${issue.bar_value}`}>
              <span>{issue.bar_bad_meaning}</span>
              <div>
                <i style={{ width: `${Math.max(0, Math.min(100, issue.bar_value))}%` }} />
              </div>
              <span>{issue.bar_good_meaning}</span>
            </div>
            <p>{issue.stage_text}</p>
            {issue.tags.length ? (
              <div className="issue-tags">
                {issue.tags.map((tag) => <small key={tag}>{tag}</small>)}
              </div>
            ) : null}
          </article>
        ))}
      </div>
    </div>
  );
}

const xinpanPlanePoint = (daoRaw: number, shiRaw: number) => {
  const dao = Math.max(-100, Math.min(100, Number(daoRaw || 0)));
  const shi = Math.max(-100, Math.min(100, Number(shiRaw || 0)));
  return {
    x: Math.max(0, Math.min(100, 50 + shi / 2)),
    y: Math.max(0, Math.min(100, 50 - dao / 2)),
  };
};

const xinpanQuadrantReading: Record<string, { title: string; summary: string }> = {
  股肱: { title: "同道同利", summary: "价值与利益都站在皇权一侧，适合托付重任与预警。" },
  权附: { title: "逐势依附", summary: "利益暂附，忠诚多半来自局势和赏罚，需持续给出可兑现好处。" },
  道隐: { title: "同道失势", summary: "价值可谈但利益受损，长期亏待会把清议拖成离心。" },
  离心: { title: "异心积怨", summary: "价值和利益都远离皇权，畏惧只能压住表面，不能消除反抗动机。" },
};

const xinpanSourceLabel: Record<string, string> = {
  turn: "月末",
  chat: "奏对",
  agreement: "履约",
  identity_conversion: "身份",
  appointment_displacement: "腾缺",
  secret_order: "密令",
  current: "当前",
};

const xinpanConcernReason = (reason?: string) => {
  const text = String(reason || "").trim();
  if (!text) return "核心关切";
  if (text.includes("极值")) return "立场很硬";
  if (text.includes("偏向")) return "有明显偏向";
  if (text.includes("身份") || text.includes("派系")) return "身份/派系牵动";
  return text;
};

function XinpanProfileBlock({ profile }: { profile?: XinpanProfile }) {
  if (!profile || !profile.quadrant) return null;
  const dao = Math.max(-100, Math.min(100, Number(profile.dao_he || 0)));
  const shi = Math.max(-100, Math.min(100, Number(profile.shi_he || 0)));
  const fear = Math.round(Number(profile.fear || 0));
  const hatred = Math.round(Number(profile.hatred || 0));
  const trust = Number(profile.trust_coeff || 0);
  const currentPoint = xinpanPlanePoint(dao, shi);
  const daoCutoff = Math.max(-100, Math.min(100, Number(profile.dao_cutoff ?? 15)));
  const shiCutoff = Math.max(-100, Math.min(100, Number(profile.shi_cutoff ?? 15)));
  const thresholdPoint = xinpanPlanePoint(daoCutoff, shiCutoff);
  const planeStyle = {
    "--xinpan-cutoff-x": `${thresholdPoint.x}%`,
    "--xinpan-cutoff-y": `${thresholdPoint.y}%`,
  } as React.CSSProperties;
  const pointStyle = {
    left: `${currentPoint.x}%`,
    top: `${currentPoint.y}%`,
  };
  const thresholdXStyle = { top: `${thresholdPoint.y}%` };
  const thresholdYStyle = { left: `${thresholdPoint.x}%` };
  const quadrantClass = String(profile.quadrant || "").replace(/[^\w\u4e00-\u9fff-]/g, "");
  const concerns = profile.core_concerns || [];
  const abilities = profile.top_abilities || [];
  const warnings = profile.warnings || [];
  const trajectory = (profile.trajectory || [])
    .map((point) => ({
      ...point,
      dao_he: Math.max(-100, Math.min(100, Number(point.dao_he || 0))),
      shi_he: Math.max(-100, Math.min(100, Number(point.shi_he || 0))),
    }))
    .filter((point) => Number.isFinite(point.dao_he) && Number.isFinite(point.shi_he));
  const trailPoints = trajectory.map((point) => xinpanPlanePoint(point.dao_he, point.shi_he));
  const trailPath = trailPoints.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
  const hasTrail = trailPoints.length > 1;
  const reading = xinpanQuadrantReading[String(profile.quadrant)] || { title: "心迹未定", summary: "需继续观察奏对、履约与人事处置后的变化。" };
  const recentEvents = trajectory
    .filter((point) => point.event && !["当前", "初始点"].includes(point.event))
    .slice(-4)
    .reverse();
  const positiveTone = (value: number) => (value > 8 ? "good" : value < -8 ? "bad" : "neutral");
  const pressureTone = (value: number) => (value >= 70 ? "bad" : value >= 40 ? "warn" : "neutral");
  const trustTone = trust >= 0.9 ? "good" : trust <= 0.62 ? "bad" : "neutral";
  const deltaText = (value?: number, digits = 1) => {
    const numeric = Number(value || 0);
    const rounded = Number(numeric.toFixed(digits));
    return `${rounded > 0 ? "+" : ""}${rounded}`;
  };
  const deltaTone = (value?: number, positiveIsGood = true) => {
    const numeric = Number(value || 0);
    if (Math.abs(numeric) < 0.05) return "neutral";
    const good = positiveIsGood ? numeric > 0 : numeric < 0;
    return good ? "good" : "bad";
  };
  return (
    <div className={`xinpan-profile quadrant-${quadrantClass}`}>
      <div className="xinpan-profile-head">
        <Shield size={13} />
        <span>心盘</span>
        <b>{profile.quadrant}</b>
      </div>
      <div className="xinpan-judgement">
        <strong>{reading.title}</strong>
        <span>{reading.summary}</span>
      </div>
      <div className="xinpan-grid">
        <div className="xinpan-plane" style={planeStyle} aria-label={`心盘：红点为真实位置，道合${dao}，势合${shi}；虚线为象限分界，道合${daoCutoff}，势合${shiCutoff}`}>
          <i className="threshold-x" style={thresholdXStyle} />
          <i className="threshold-y" style={thresholdYStyle} />
          <span className="axis-label axis-label-dao">道合↑</span>
          <span className="axis-label axis-label-shi">势合→</span>
          <span className="threshold-label">象限分界</span>
          <span className="quad q1">股肱</span>
          <span className="quad q2">道隐</span>
          <span className="quad q3">离心</span>
          <span className="quad q4">权附</span>
          <svg className="xinpan-trail" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            {hasTrail ? <polyline points={trailPath} /> : null}
            {trailPoints.slice(0, -1).map((point, index) => (
              <circle key={`${index}-${point.x}-${point.y}`} cx={point.x} cy={point.y} r={2.3} />
            ))}
          </svg>
          <b className="xinpan-point" style={pointStyle} title={`真实位置：道合${dao}，势合${shi}`} />
          <span className="xinpan-plane-note">{hasTrail ? `${trailPoints.length}点轨迹 · 红点为真实位置` : "红点为真实位置"}</span>
        </div>
        <div className="xinpan-metrics">
          <span className={positiveTone(dao)}><b>道合</b>{dao > 0 ? "+" : ""}{dao}</span>
          <span className={positiveTone(shi)}><b>势合</b>{shi > 0 ? "+" : ""}{shi}</span>
          <span className={pressureTone(fear)}><b>畏惧</b>{fear}</span>
          <span className={trustTone}><b>信言</b>{trust.toFixed(2)}</span>
          <span className={pressureTone(hatred)}><b>仇恨</b>{hatred}</span>
        </div>
      </div>
      {profile.behavior_hint ? <p className="xinpan-behavior">{profile.behavior_hint}</p> : null}
      {recentEvents.length ? (
        <div className="xinpan-ledger">
          <b>近期心证</b>
          {recentEvents.map((point, index) => (
            <span key={`${point.turn}-${point.event}-${index}`}>
              <i>{point.turn ? `第${point.turn}回合` : "本局"}</i>
              <em>{xinpanSourceLabel[point.source_kind || ""] || point.source_kind || "记录"}</em>
              <strong>{point.event}</strong>
              {point.has_delta ? (
                <small className="xinpan-delta-line">
                  <b className={deltaTone(point.dao_delta)}>道{deltaText(point.dao_delta)}</b>
                  <b className={deltaTone(point.shi_delta)}>势{deltaText(point.shi_delta)}</b>
                  <b className={deltaTone(point.fear_delta, false)}>惧{deltaText(point.fear_delta)}</b>
                  <b className={deltaTone(point.hatred_delta, false)}>恨{deltaText(point.hatred_delta)}</b>
                  <b className={deltaTone(point.trust_delta)}>信{deltaText(point.trust_delta, 3)}</b>
                </small>
              ) : null}
              <small className="xinpan-landing-line">事后落点 道{point.dao_he > 0 ? "+" : ""}{point.dao_he} · 势{point.shi_he > 0 ? "+" : ""}{point.shi_he} · {point.quadrant || "未定"}</small>
            </span>
          ))}
        </div>
      ) : null}
      {concerns.length ? (
        <div className="xinpan-insight-group">
          <b className="xinpan-insight-title">他最在意</b>
          <div className="xinpan-concerns">
            {concerns.slice(0, 5).map((concern) => (
              <span key={concern.dim_id || `${concern.symbol}-${concern.name}`} title={xinpanConcernReason(concern.reason)}>
                <b>{concern.symbol}{concern.name}</b>
                <small>{xinpanConcernReason(concern.reason)}</small>
              </span>
            ))}
          </div>
        </div>
      ) : null}
      {abilities.length ? (
        <div className="xinpan-insight-group">
          <b className="xinpan-insight-title">可用强项</b>
          <div className="xinpan-abilities">
            {abilities.slice(0, 5).map((ability) => (
              <i key={ability.dim_id || `${ability.symbol}-${ability.name}`}>
                {ability.symbol}{ability.name}{ability.band ? ` · ${ability.band}` : ""}
              </i>
            ))}
          </div>
        </div>
      ) : null}
      {warnings.length ? (
        <div className="xinpan-warnings">
          {warnings.slice(0, 3).map((warning) => <span key={warning}>{warning}</span>)}
        </div>
      ) : null}
    </div>
  );
}

function TiangangSpectrum({ profile }: { profile?: TiangangProfile }) {
  const groups = profile?.groups || [];
  if (!groups.length) return null;
  return (
    <div className="tiangang-spectrum">
      <div className="tiangang-spectrum-head">
        <Target size={13} />
        <span>天罡谱尺</span>
        {profile?.archetype && <b>{profile.archetype}</b>}
      </div>
      <div className="tiangang-spectrum-groups">
        {groups.map((group) => (
          <section className="tiangang-spectrum-group" key={group.name}>
            <h3>{group.name}</h3>
            <div className="tiangang-spectrum-list">
              {group.dimensions.map((dim) => {
                const left = dim.poles?.left || "一端";
                const right = dim.poles?.right || "另一端";
                const band = dim.band || { left: 28, width: 44, tone: "center" as const };
                const bandLeft = Math.max(0, Math.min(100, Number(band.left) || 0));
                const bandWidth = Math.max(16, Math.min(100 - bandLeft, Number(band.width) || 36));
                return (
                  <div className={`tiangang-spectrum-row type-${dim.type || "mixed"}`} key={`${group.name}-${dim.symbol}-${dim.name}`}>
                    <div className="tiangang-spectrum-name">
                      <span>{dim.symbol}</span>
                      <b>{dim.name}</b>
                    </div>
                    <div className="tiangang-spectrum-scale" aria-label={`${dim.name}：谱尺显影`}>
                      <i
                        className={`tiangang-spectrum-band tone-${band.tone || "center"}`}
                        style={{ left: `${bandLeft}%`, width: `${bandWidth}%` }}
                      />
                    </div>
                    <div className="tiangang-spectrum-labels">
                      <small>{left}</small>
                      <small>{right}</small>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function StanceNotes({ notes }: { notes?: StanceNote[] }) {
  const rows = notes || [];
  if (!rows.length) return null;
  const label: Record<StanceNote["stance"], string> = {
    support: "支持",
    oppose: "反对",
    caution: "附条件",
    neutral: "未定",
  };
  const handshakeLabel: Record<string, string> = {
    sealed: "握手成功",
    conditional: "附条件",
    blocked: "未说服",
    none: "未成约",
  };
  return (
    <div className="stance-notes">
      <div className="stance-notes-head">
        <Check size={13} />
        <span>本月奏对立场</span>
      </div>
      {rows.map((note) => (
        <div className={`stance-note ${note.stance}`} key={note.id}>
          <b>{label[note.stance] || note.stance}</b>
          <span>{note.topic}</span>
          {note.handshake_status ? (
            <div className={`stance-handshake ${note.handshake_status}`}>
              <strong>{handshakeLabel[note.handshake_status] || "未成约"}</strong>
              <em>{Number(note.psychological_score || 0)}/{Number(note.psychological?.threshold || 0)}</em>
              {note.psychological?.verbal_only ? <small>口头承诺已足</small> : null}
            </div>
          ) : null}
          {note.conditions && <small>{note.conditions}</small>}
          {note.psychological?.tasks?.length ? (
            <ul className="stance-tasks">
              {note.psychological.tasks.slice(0, 4).map((task, index) => <li key={`${note.id}-task-${index}`}>{task}</li>)}
            </ul>
          ) : null}
          {note.evidence?.drivers?.length ? (
            <div className="stance-evidence">
              {note.evidence.drivers.slice(0, 4).map((driver, index) => (
                <span key={`${note.id}-driver-${index}`}>
                  <b>{driver.kind}</b>{driver.text}
                </span>
              ))}
            </div>
          ) : null}
          {note.risk_tags_list?.length ? (
            <div className="stance-risk-tags">
              {note.risk_tags_list.slice(0, 6).map((risk) => <i key={risk}>{risk}</i>)}
            </div>
          ) : null}
          {note.execution_hint ? <small>{note.execution_hint}</small> : null}
        </div>
      ))}
    </div>
  );
}

function NetworkProfileBlock({ profile }: { profile?: NetworkProfile }) {
  const relations = profile?.relations || [];
  const recommendations = profile?.recommendations || [];
  const growth = profile?.growth_arc || {};
  const hasGrowth = Boolean(growth.seed || growth.rise || growth.risk);
  if (!profile || (!profile.biography && !relations.length && !hasGrowth && !recommendations.length)) {
    return null;
  }
  return (
    <div className="network-profile">
      <div className="network-profile-head">
        <Landmark size={13} />
        <span>人物网络</span>
        {profile.derived ? <b>局中推定</b> : null}
      </div>
      {profile.biography ? <p className="network-biography">{profile.biography}</p> : null}
      {relations.length ? (
        <div className="network-relations">
          {relations.slice(0, 8).map((relation) => (
            <article className={`network-relation ${relation.confidence === "high" ? "strong" : "weak"}`} key={`${relation.type}-${relation.target}`}>
              <div>
                <b>{relation.target}</b>
                <small>{relation.type}</small>
              </div>
              <span>{[relation.faction, relation.office_type].filter(Boolean).join(" · ")}</span>
              {relation.note ? <p>{relation.note}</p> : null}
            </article>
          ))}
        </div>
      ) : null}
      {hasGrowth ? (
        <div className="network-growth">
          {growth.seed ? <span><b>起</b>{growth.seed}</span> : null}
          {growth.rise ? <span><b>升</b>{growth.rise}</span> : null}
          {growth.risk ? <span><b>险</b>{growth.risk}</span> : null}
        </div>
      ) : null}
      {profile.ability_logic ? (
        <details className="network-ability">
          <summary>能力构成</summary>
          <p>{profile.ability_logic}</p>
        </details>
      ) : null}
      {recommendations.length ? (
        <div className="network-recommendations">
          <b>可联络</b>
          <div>
            {recommendations.slice(0, 5).map((item) => (
              <span key={item.name} title={(item.evidence || []).join("；")}>
                {item.name}<small>{item.faction || item.office_type || "中立"}</small>
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function CausalNotesBlock({ notes }: { notes?: CausalNote[] }) {
  const rows = (notes || []).filter((note) => note && (note.title || note.summary));
  if (!rows.length) return null;
  return (
    <section className="causal-notes">
      <div className="causal-notes-head">
        <ScrollText size={14} />
        <span>成因札记</span>
      </div>
      <div className="causal-notes-list">
        {rows.map((note, index) => (
          <article className={`causal-note tone-${note.tone || "neutral"}`} key={`${note.kind}-${index}`}>
            <b>{note.title}</b>
            {note.summary ? <p>{note.summary}</p> : null}
            {note.drivers?.length ? (
              <ul>
                {note.drivers.slice(0, 4).map((driver, i) => <li key={i}>{driver}</li>)}
              </ul>
            ) : null}
            {note.risks?.length ? (
              <div className="stance-risk-tags">
                {note.risks.slice(0, 6).map((risk) => <i key={risk}>{risk}</i>)}
              </div>
            ) : null}
            {note.execution_hint ? <small>{note.execution_hint}</small> : null}
          </article>
        ))}
      </div>
    </section>
  );
}

function ChatEffectLedger({ notices }: { notices: ChatEffectNotice[] }) {
  if (!notices.length) return null;
  return (
    <div className="chat-effect-ledger" aria-label="奏对留痕">
      <b>奏对留痕</b>
      {notices.map((notice) => (
        <article className={`chat-effect-note tone-${notice.tone || "neutral"} kind-${notice.kind}`} key={notice.id}>
          <div>
            <span>{notice.source}</span>
            <strong>{notice.title}</strong>
          </div>
          <p>{notice.summary}</p>
          {notice.detail ? <small>{notice.detail}</small> : null}
          {notice.chips?.length ? (
            <div className="chat-effect-chips">
              {notice.chips.map((chip) => (
                <i className={`tone-${chip.tone || "neutral"}`} key={`${notice.id}-${chip.label}`}>
                  <b>{chip.label}</b>{chip.value}
                </i>
              ))}
            </div>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function ChatModal({
  minister,
  portraitPrefix,
  chat,
  suggestions,
  pendingUserMessage,
  streamingMinisterMessage,
  chatNotice,
  chatEffectNotices,
  canUndoLastChat,
  composerHint,
  input,
  busy,
  error,
  secretOrders,
  onInput,
  onSend,
  onUndo,
  onHint,
  onFavorite,
  onOpenEdict,
  onClose,
}: {
  minister: Minister;
  portraitPrefix: string;
  chat: ChatMessage[];
  suggestions: Suggestion[];
  pendingUserMessage: string;
  streamingMinisterMessage: string;
  chatNotice: string;
  chatEffectNotices: ChatEffectNotice[];
  canUndoLastChat: boolean;
  composerHint: string;
  input: string;
  busy: string;
  error: string;
  secretOrders: SecretOrder[];
  onInput: (value: string) => void;
  onSend: (text?: string) => void;
  onUndo: () => void;
  onHint: (value: string) => void;
  onFavorite: () => void;
  onOpenEdict: () => void;
  onClose: () => void;
}) {
  const { primary: portraitPrimary, fallback: portraitFallback } = portraitSources(minister, portraitPrefix);
  const chatLogRef = React.useRef<HTMLDivElement | null>(null);
  const inputRef = React.useRef<HTMLTextAreaElement | null>(null);
  const displayMessages: ChatDisplayMessage[] = [...chat];

  if (pendingUserMessage) {
    displayMessages.push({ role: "user", content: pendingUserMessage, pending: true });
  }
  if (streamingMinisterMessage) {
    displayMessages.push({ role: "minister", content: streamingMinisterMessage, pending: true });
  }

  React.useEffect(() => {
    inputRef.current?.focus();
  }, [minister.name]);

  React.useEffect(() => {
    const node = chatLogRef.current;
    if (node) {
      node.scrollTop = node.scrollHeight;
    }
  }, [minister.name, chat, pendingUserMessage, streamingMinisterMessage, chatNotice, chatEffectNotices, busy, error]);

  const handleSend = () => {
    onSend(input);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    onSend(input);
  };

  const sendSuggestion = (suggestion: Suggestion) => {
    if (suggestion.prefix) {
      // 填前缀到输入框，不直接发送，光标跟到末尾
      onInput(suggestion.text);
      setTimeout(() => inputRef.current?.focus(), 0);
    } else {
      onSend(suggestion.text);
    }
  };

  return (
    <div className="chat-full-grid">
      <aside className="modal-pane minister-side">
        <div className="chat-character-stage">
          <div className="chat-portrait-wrap">
            <MinisterPortrait primary={portraitPrimary} fallback={portraitFallback} name={minister.name} />
          </div>
        </div>
        <div className="chat-intel-stack">
          <div className="chat-identity-plate">
            <div className="minister-profile">
              <div>
                <h2>{minister.name}</h2>
                <p>
                  {minister.status !== "active" && (
                    <span className={`minister-status status-${minister.status}`}>{minister.status_label}</span>
                  )}
                  {minister.office && <span className="profile-office">{minister.office}</span>}
                </p>
              </div>
              <button className="icon-button" aria-label="收藏大臣" onClick={onFavorite}>
                <Star size={16} fill={minister.favorite ? "currentColor" : "none"} />
              </button>
            </div>
            <p className="profile-copy">{minister.summary}</p>
          </div>
          <details className="chat-intel-details" open>
            <summary>人物情报</summary>
            <div className="chat-intel-detail-body">
              <NetworkProfileBlock profile={minister.network_profile} />
              <StanceNotes notes={minister.stance_notes} />
              <XinpanProfileBlock profile={minister.xinpan_profile} />
              <TiangangSpectrum profile={minister.tiangang_profile} />
            </div>
          </details>
          <button className="secondary-action" onClick={onOpenEdict}>
            <ScrollText size={15} />
            转入诏书草案
          </button>
          {secretOrders.length > 0 && (
            <div className="chat-secret-orders">
              <div className="secret-orders-label"><Lock size={12} />密令</div>
              {secretOrders.map((o) => (
                <div key={o.id} className="secret-order-item">
                  <div className="secret-order-title">{o.title}</div>
                  <div className="secret-order-meta">第 {o.year_issued} 年 {o.period_issued} 月下令</div>
                  {o.content && <div className="secret-order-content">{o.content}</div>}
                  {o.sim_note && <div className="secret-order-content"><b>月度动向：</b>{o.sim_note}</div>}
                  {o.result && <div className="secret-order-content"><b>承办回报：</b>{o.result}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>

      <section className="modal-pane chat-main">
        <div className="chat-log" ref={chatLogRef}>
          {!displayMessages.length && !busy && !chatEffectNotices.length && !chatNotice && !error ? (
            <div className="chat-empty-state">
              <b>尚未开问</b>
              <span>可先问钱粮、军情、地方阻力，或用下方「拟旨」「下密令」起草行动。</span>
            </div>
          ) : null}
          {displayMessages.map((message, index) => (
            <div className={`chat-message ${message.role} ${message.pending ? "pending" : ""}`} key={`${message.role}-${index}-${message.content}`}>
              <span>{message.role === "user" ? "朕" : minister.name}</span>
              <p>{message.content}</p>
            </div>
          ))}
          {busy && !streamingMinisterMessage && (
            <div className="chat-message minister thinking">
              <span>{minister.name}</span>
              <p><Loader2 size={14} />大臣思索中...</p>
            </div>
          )}
          <ChatEffectLedger notices={chatEffectNotices} />
          {chatNotice && <div className="chat-system-note">{chatNotice}</div>}
          {error && <div className="chat-system-note danger" role="alert">{error}</div>}
        </div>
        <div className="chat-composer">
          <div className="hitl-bar">
            {suggestions.map((suggestion) => (
              <button
                key={`${suggestion.label}-${suggestion.text}`}
                onClick={() => sendSuggestion(suggestion)}
                disabled={!!busy}
                title={suggestion.prefix ? `填入前缀：${suggestion.text}` : suggestion.text}
                className={suggestion.prefix ? "hitl-prefix" : ""}
              >
                {suggestion.label}
              </button>
            ))}
          </div>
          <label className="chat-input">
            <span>问话</span>
            <textarea
              ref={inputRef}
              value={input}
              onChange={(event) => {
                onInput(event.target.value);
                if (composerHint) onHint("");
              }}
              onKeyDown={handleKeyDown}
              placeholder="问大臣军情、钱粮、地方，或要求他拟旨... Enter 发送，Shift+Enter 换行"
            />
          </label>
          <div className="composer-actions">
            <button className={`primary-action ${!input.trim() ? "is-empty" : ""}`} onClick={handleSend} disabled={!!busy}>
              <Send size={15} />
              发送
            </button>
            <button className="secondary-action composer-undo" onClick={onUndo} disabled={!!busy || !canUndoLastChat}>
              <RotateCcw size={15} />
              撤回本轮
            </button>
            <button className="secondary-action composer-exit" onClick={onClose}>
              <X size={15} />
              退出召对
            </button>
            {composerHint && <div className="composer-hint">{composerHint}</div>}
          </div>
        </div>
      </section>
    </div>
  );
}

function EdictModal({
  state,
  directiveText,
  editingDirectiveId,
  editingDirectiveText,
  decree,
  report,
  busy,
  error,
  onDirectiveTextChange,
  onEditingTextChange,
  onCreateDirective,
  onStartEdit,
  onCancelEdit,
  onSaveDirective,
  onDeleteDirective,
  onWriteDecree,
  onSaveDecree,
  onIssueDecree,
  onConfirmDirective,
  onRejectDirective,
}: {
  state: GameState;
  directiveText: string;
  editingDirectiveId: number | null;
  editingDirectiveText: string;
  decree: string;
  report: string;
  busy: string;
  error: string;
  onDirectiveTextChange: (value: string) => void;
  onEditingTextChange: (value: string) => void;
  onCreateDirective: () => void;
  onStartEdit: (directive: Directive) => void;
  onCancelEdit: () => void;
  onSaveDirective: (directive: Directive) => void;
  onDeleteDirective: (directiveId: number) => void;
  onWriteDecree: () => void;
  onSaveDecree: (text: string) => void;
  onIssueDecree: () => void;
  onConfirmDirective: (directiveId: number) => void;
  onRejectDirective: (directiveId: number) => void;
}) {
  const pendingDirectives = state.directives.filter((d) => d.status === "pending");
  const draftDirectives = state.directives.filter((d) => d.status !== "pending");
  const hasPending = pendingDirectives.length > 0;
  const readinessItems = buildEdictReadiness(state, draftDirectives, pendingDirectives);
  const [decreeDraft, setDecreeDraft] = React.useState(decree);
  React.useEffect(() => {
    setDecreeDraft(decree);
  }, [decree]);
  return (
    <div className="edict-full-grid">
      <section className="modal-pane directive-pane">
        <h2>本月指令</h2>
        {hasPending && (
          <div className="pending-directives" role="region" aria-label="待核定大臣拟旨">
            <h3>⚠ 大臣拟旨待核定（{pendingDirectives.length}）</h3>
            {pendingDirectives.map((directive) => (
              <div className="directive-item pending" key={directive.id}>
                <div className="directive-head">
                  <b>#{directive.id}</b>
                  <span>{directive.source}</span>
                </div>
                <p>{directive.text}</p>
                {directive.notes ? <small>{directive.notes}</small> : null}
                <div className="directive-tools">
                  <button onClick={() => onConfirmDirective(directive.id)} disabled={!!busy}><Check size={14} />准</button>
                  <button onClick={() => onRejectDirective(directive.id)} disabled={!!busy}><X size={14} />驳</button>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="directive-list">
          {draftDirectives.map((directive) => (
            <div className="directive-item" key={directive.id}>
              <div className="directive-head">
                <b>#{directive.id}</b>
                <span>{directive.source}</span>
              </div>
              {editingDirectiveId === directive.id ? (
                <div className="directive-edit">
                  <textarea value={editingDirectiveText} onChange={(event) => onEditingTextChange(event.target.value)} />
                  <div>
                    <button className="icon-button" onClick={() => onSaveDirective(directive)} aria-label="保存草案"><Check size={15} /></button>
                    <button className="icon-button" onClick={onCancelEdit} aria-label="取消修改"><X size={15} /></button>
                  </div>
                </div>
              ) : (
                <>
                  <p>{directive.text}</p>
                  {directive.notes ? <small>{directive.notes}</small> : null}
                  <div className="directive-tools">
                    <button onClick={() => onStartEdit(directive)}><Edit3 size={14} />改</button>
                    <button onClick={() => onDeleteDirective(directive.id)}><Trash2 size={14} />删</button>
                  </div>
                </>
              )}
            </div>
          ))}
          {!draftDirectives.length && !hasPending && <div className="empty-note">本月不可空过。请先召见大臣，或在右侧新增一道指令。</div>}
        </div>
      </section>

      <section className="modal-pane edict-compose">
        <h2>新增指令</h2>
        <textarea
          value={directiveText}
          onChange={(event) => onDirectiveTextChange(event.target.value)}
          placeholder="例如：命毕自严核拨关宁、山海关、蓟镇辽饷一百五十二万两..."
        />
        <div className="edict-actions">
          <button onClick={onCreateDirective} disabled={!!busy || !directiveText.trim()}>新增草案</button>
          <button onClick={onWriteDecree} disabled={!!busy || !draftDirectives.length || hasPending}>生成诏书</button>
          <button className="primary-action" onClick={onIssueDecree} disabled={!!busy || !draftDirectives.length || hasPending}>颁布诏书</button>
        </div>
        {hasPending && <small className="pending-hint">尚有 {pendingDirectives.length} 道大臣拟旨待核定（准/驳），核定后方可颁诏。</small>}
      </section>

      <section className="modal-pane settlement-box">
        <h2>诏书与奏章</h2>
        <EdictReadinessPanel items={readinessItems} />
        {busy && <div className="busy-line"><Loader2 size={15} />{busy}...</div>}
        {error && <div className="error-line" role="alert">{error}</div>}
        {decree && !report ? (
          <div className="decree-edit">
            <label>诏书正文（可改，颁布前以此为准）</label>
            <textarea
              value={decreeDraft}
              onChange={(event) => setDecreeDraft(event.target.value)}
            />
            <div className="decree-edit-tools">
              <button
                onClick={() => onSaveDecree(decreeDraft)}
                disabled={!!busy || !decreeDraft.trim() || decreeDraft === decree}
              >
                <Check size={14} />存改
              </button>
              <button
                onClick={() => setDecreeDraft(decree)}
                disabled={!!busy || decreeDraft === decree}
              >
                <X size={14} />还原
              </button>
            </div>
          </div>
        ) : decree || report ? (
          <pre>{`${decree || ""}${report ? `\n\n${report}` : ""}`}</pre>
        ) : (
          <div className="empty-note">生成诏书后，正式诏文会在此显示；可手动改定，颁布后会显示月末总结奏章。</div>
        )}
      </section>
    </div>
  );
}

function EdictReadinessPanel({ items }: { items: EdictReadinessItem[] }) {
  if (!items.length) return null;
  return (
    <div className="edict-readiness" aria-label="御前核验">
      <div className="edict-readiness-head">
        <Shield size={15} />
        <span>御前核验</span>
      </div>
      {items.map((item) => (
        <div className={`edict-readiness-item ${item.tone}`} key={`${item.title}-${item.body}`}>
          <b>{item.title}</b>
          <span>{item.body}</span>
        </div>
      ))}
    </div>
  );
}

// 官职品级权重，数字越小品级越高（排越前）
function officeRank(office: string): number {
  if (/首辅/.test(office)) return 1;
  if (/次辅/.test(office)) return 2;
  if (/大学士/.test(office)) return 3;
  if (/尚书/.test(office)) return 4;
  if (/侍郎/.test(office)) return 5;
  if (/都御史|巡抚|总督/.test(office)) return 6;
  if (/郎中/.test(office)) return 8;
  return 9;
}

function filterMinisters(ministers: Minister[], group: string) {
  const courtMinisters = ministers.filter((m) => (m.power_id || "ming") === "ming");
  const byOfficeRank = (a: Minister, b: Minister) => officeRank(a.office || "") - officeRank(b.office || "");
  if (group === "内阁+六部" || group === "内阁" || group === "六部") {
    return courtMinisters
      .filter((m) =>
        (m.office_type === "内阁" || ["吏部", "户部", "礼部", "兵部", "刑部", "工部"].includes(m.office_type))
        && m.status === "active"
        && !!(m.office || "").trim()
        && !/前|罢|致仕/.test(m.office || "")  // 无实职不排朝班
      )
      .sort(byOfficeRank);
  }
  if (group === "边镇厂卫") {
    return courtMinisters
      .filter((m) => m.status === "active" && ["边镇", "司礼监", "锦衣卫", "东厂", "都察院", "地方"].includes(m.office_type))
      .sort(byOfficeRank);
  }
  if (group === "江湖外缘") {
    return courtMinisters
      .filter((m) => {
        const identity = `${m.office || ""} ${m.office_type || ""}`;
        return m.status === "active" && (
          ["待铨", "未仕"].includes(m.office_type)
          || m.faction === "西学"
          || /江湖|山庄|少林|武当|龙虎山|教主|商人|隐士|琴师|刀客|蛊师|传教士|华侨|游侠|侠女|真人|道长|药王/.test(identity)
        );
      })
      .sort((a, b) => (a.office_type || "").localeCompare(b.office_type || "", "zh-Hans-CN") || a.name.localeCompare(b.name, "zh-Hans-CN"));
  }
  if (group === "在职") return courtMinisters.filter((m) => m.status === "active").sort(byOfficeRank);
  if (group === "收藏") return courtMinisters.filter((minister) => minister.favorite);
  return courtMinisters;
}

function filterConsorts(consorts: Minister[], group: string) {
  const mingConsorts = consorts.filter((c) => (c.power_id || "ming") === "ming");
  if (group === "收藏") return mingConsorts.filter((c) => c.favorite);
  return mingConsorts;
}

const MING_MAP_COLOR = "#4f8a57";
const UNREST_MAP_COLOR = "#b83a31";
const EXTERNAL_MAP_COLOR = "#5f6366";
const DEFAULT_MAP_COLOR = EXTERNAL_MAP_COLOR;
const UNREST_DANGER_THRESHOLD = 60;
const MING_MAP_OPACITY = 0.2;
const EXTERNAL_MAP_OPACITY = 0.3;

const MAP_DISPLAY_POWER_OVERRIDES: Record<string, string> = {
  // 崇祯元年辽西只剩山海关外宁锦前线，不能按关内省份红色处理。
  liaodong: "ming_frontier",
};

const THEATER_ONLY_REGION_IDS = new Set(["liaodong"]);
const THEATER_COORD_STORAGE_KEY = "ming-map-theater-coords";
const MAP_PENCIL_STORAGE_KEY = "ming-map-pencil-line";
const MAP_TERRAIN_STORAGE_KEY = "ming-map-terrain-transform-v3";

type TerrainTransform = { x: number; y: number; width: number; height: number };

const DEFAULT_TERRAIN_TRANSFORM: TerrainTransform = {
  x: 840.22,
  y: 83.48,
  width: 276,
  height: 206,
};

function getRegionMapColor(region: RegionPathRenderItem) {
  if (region.controlledBy !== "ming") return EXTERNAL_MAP_COLOR;
  if (region.unrest > UNREST_DANGER_THRESHOLD) return UNREST_MAP_COLOR;
  return MING_MAP_COLOR;
}

function getRegionMapOpacity(region: RegionPathRenderItem) {
  return region.controlledBy === "ming" ? MING_MAP_OPACITY : EXTERNAL_MAP_OPACITY;
}

function GrandMap({ nodes, selectedId, onSelect }: { nodes: MapNode[]; selectedId: string; onSelect: (id: string) => void }) {
  const viewportRef = React.useRef<HTMLDivElement | null>(null);
  const mapTileRef = React.useRef<HTMLDivElement | null>(null);
  const svgRef = React.useRef<SVGSVGElement | null>(null);
  const didCenterRef = React.useRef(false);
  const viewBoxParts = React.useMemo(() => MAP_VIEW_BOX.split(/\s+/).map(Number), []);
  const defaultTerrainTransform = DEFAULT_TERRAIN_TRANSFORM;

  // 坐标取点工具：URL 加 ?coords=1 开启。点地图打印 x/y% 与 SVG viewBox 坐标。
  const coordPick = typeof window !== "undefined" && new URLSearchParams(window.location.search).has("coords");
  const [pick, setPick] = React.useState<{ x: number; y: number; svgX: number; svgY: number; label?: string } | null>(null);
  const [draggedTheaters, setDraggedTheaters] = React.useState<Record<string, { x: number; y: number }>>(() => {
    if (typeof window === "undefined") return {};
    try {
      const raw = window.localStorage.getItem(THEATER_COORD_STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw) as Record<string, { x: number; y: number }>;
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  });
  const [pencilMode, setPencilMode] = React.useState(false);
  const [terrainMode, setTerrainMode] = React.useState(coordPick);
  const [terrainTransform, setTerrainTransform] = React.useState<TerrainTransform>(() => {
    if (typeof window === "undefined") return defaultTerrainTransform;
    try {
      const raw = window.localStorage.getItem(MAP_TERRAIN_STORAGE_KEY);
      if (!raw) return defaultTerrainTransform;
      const parsed = JSON.parse(raw) as TerrainTransform;
      if (
        parsed &&
        Number.isFinite(parsed.x) &&
        Number.isFinite(parsed.y) &&
        Number.isFinite(parsed.width) &&
        Number.isFinite(parsed.height) &&
        parsed.width > 0 &&
        parsed.height > 0
      ) {
        return parsed;
      }
    } catch {}
    return defaultTerrainTransform;
  });
  const [pencilLine, setPencilLine] = React.useState<Array<{ x: number; y: number; svgX: number; svgY: number }>>(() => {
    if (typeof window === "undefined") return [];
    try {
      const raw = window.localStorage.getItem(MAP_PENCIL_STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw) as Array<{ x: number; y: number; svgX: number; svgY: number }>;
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  });
  const [mapZoom, setMapZoom] = React.useState(1);
  const [svgLabelPositions, setSvgLabelPositions] = React.useState<Record<string, SvgLabelPosition>>({});
  const dragRef = React.useRef<{ id: string; pointerId: number; moved: boolean } | null>(null);
  const pencilDragRef = React.useRef<{ pointerId: number } | null>(null);
  const terrainDragRef = React.useRef<{ pointerId: number; startSvgX: number; startSvgY: number; start: TerrainTransform } | null>(null);
  const svgCoordFromPct = React.useCallback((x: number, y: number) => ({
    svgX: +(viewBoxParts[0] + (x / 100) * viewBoxParts[2]).toFixed(2),
    svgY: +(viewBoxParts[1] + (y / 100) * viewBoxParts[3]).toFixed(2),
  }), [viewBoxParts]);
  const pickFromClient = React.useCallback((clientX: number, clientY: number, label?: string) => {
    const rect = mapTileRef.current?.getBoundingClientRect();
    if (!rect) return null;
    const x = +(((clientX - rect.left) / rect.width) * 100).toFixed(2);
    const y = +(((clientY - rect.top) / rect.height) * 100).toFixed(2);
    const clampedX = Math.min(100, Math.max(0, x));
    const clampedY = Math.min(100, Math.max(0, y));
    const svg = svgCoordFromPct(clampedX, clampedY);
    return { x: clampedX, y: clampedY, ...svg, label };
  }, [svgCoordFromPct]);
  const saveDraggedTheater = React.useCallback((id: string, pos: { x: number; y: number }) => {
    setDraggedTheaters((current) => {
      const next = { ...current, [id]: pos };
      try {
        window.localStorage.setItem(THEATER_COORD_STORAGE_KEY, JSON.stringify(next));
      } catch {}
      return next;
    });
  }, []);
  const saveTerrainTransform = React.useCallback((transform: TerrainTransform) => {
    setTerrainTransform(transform);
    try {
      window.localStorage.setItem(MAP_TERRAIN_STORAGE_KEY, JSON.stringify(transform));
    } catch {}
  }, []);
  const resizeTerrain = React.useCallback((factor: number) => {
    setTerrainTransform((current) => {
      const nextWidth = +(current.width * factor).toFixed(2);
      const nextHeight = +(current.height * factor).toFixed(2);
      const centerX = current.x + current.width / 2;
      const centerY = current.y + current.height / 2;
      const next = {
        x: +(centerX - nextWidth / 2).toFixed(2),
        y: +(centerY - nextHeight / 2).toFixed(2),
        width: nextWidth,
        height: nextHeight,
      };
      try {
        window.localStorage.setItem(MAP_TERRAIN_STORAGE_KEY, JSON.stringify(next));
      } catch {}
      return next;
    });
  }, []);
  const savePencilLine = React.useCallback((line: Array<{ x: number; y: number; svgX: number; svgY: number }>) => {
    setPencilLine(line);
    try {
      window.localStorage.setItem(MAP_PENCIL_STORAGE_KEY, JSON.stringify(line));
    } catch {}
  }, []);
  const addPencilPoint = React.useCallback((point: { x: number; y: number; svgX: number; svgY: number }) => {
    setPencilLine((current) => {
      const last = current[current.length - 1];
      if (last) {
        const dx = point.svgX - last.svgX;
        const dy = point.svgY - last.svgY;
        if (Math.hypot(dx, dy) < 1.2) return current;
      }
      const next = [...current, point];
      try {
        window.localStorage.setItem(MAP_PENCIL_STORAGE_KEY, JSON.stringify(next));
      } catch {}
      return next;
    });
  }, []);
  const onPickClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!coordPick || pencilMode) return;
    const next = pickFromClient(e.clientX, e.clientY);
    if (!next) return;
    setPick(next);
    console.log(`map pct: (${next.x}, ${next.y}) svg: (${next.svgX}, ${next.svgY})`);
  };
  const onPencilPointerDown = (ev: React.PointerEvent<HTMLDivElement>) => {
    if (!coordPick || !pencilMode) return;
    ev.preventDefault();
    ev.stopPropagation();
    const next = pickFromClient(ev.clientX, ev.clientY, "铅笔");
    if (!next) return;
    pencilDragRef.current = { pointerId: ev.pointerId };
    ev.currentTarget.setPointerCapture(ev.pointerId);
    const point = { x: next.x, y: next.y, svgX: next.svgX, svgY: next.svgY };
    savePencilLine([point]);
    setPick(next);
  };
  const onPencilPointerMove = (ev: React.PointerEvent<HTMLDivElement>) => {
    const drag = pencilDragRef.current;
    if (!coordPick || !pencilMode || !drag || drag.pointerId !== ev.pointerId) return;
    ev.preventDefault();
    ev.stopPropagation();
    const next = pickFromClient(ev.clientX, ev.clientY, "铅笔");
    if (!next) return;
    addPencilPoint({ x: next.x, y: next.y, svgX: next.svgX, svgY: next.svgY });
    setPick(next);
  };
  const onPencilPointerUp = (ev: React.PointerEvent<HTMLDivElement>) => {
    const drag = pencilDragRef.current;
    if (!coordPick || !pencilMode || !drag || drag.pointerId !== ev.pointerId) return;
    ev.preventDefault();
    ev.stopPropagation();
    try { ev.currentTarget.releasePointerCapture(ev.pointerId); } catch {}
    pencilDragRef.current = null;
    console.log(`pencil svg line: ${JSON.stringify(pencilLine.map((point) => [point.svgX, point.svgY]))}`);
  };
  const onTerrainPointerDown = (ev: React.PointerEvent<SVGImageElement>) => {
    if (!coordPick || !terrainMode) return;
    ev.preventDefault();
    ev.stopPropagation();
    const next = pickFromClient(ev.clientX, ev.clientY, "底图");
    if (!next) return;
    terrainDragRef.current = {
      pointerId: ev.pointerId,
      startSvgX: next.svgX,
      startSvgY: next.svgY,
      start: terrainTransform,
    };
    ev.currentTarget.setPointerCapture(ev.pointerId);
    setPick(next);
  };
  const onTerrainPointerMove = (ev: React.PointerEvent<SVGImageElement>) => {
    const drag = terrainDragRef.current;
    if (!coordPick || !terrainMode || !drag || drag.pointerId !== ev.pointerId) return;
    ev.preventDefault();
    ev.stopPropagation();
    const next = pickFromClient(ev.clientX, ev.clientY, "底图");
    if (!next) return;
    saveTerrainTransform({
      ...drag.start,
      x: +(drag.start.x + next.svgX - drag.startSvgX).toFixed(2),
      y: +(drag.start.y + next.svgY - drag.startSvgY).toFixed(2),
    });
    setPick(next);
  };
  const onTerrainPointerUp = (ev: React.PointerEvent<SVGImageElement>) => {
    const drag = terrainDragRef.current;
    if (!coordPick || !terrainMode || !drag || drag.pointerId !== ev.pointerId) return;
    ev.preventDefault();
    ev.stopPropagation();
    try { ev.currentTarget.releasePointerCapture(ev.pointerId); } catch {}
    terrainDragRef.current = null;
  };
  const onTheaterPointerDown = (node: MapNode) => (ev: React.PointerEvent<HTMLButtonElement>) => {
    if (!coordPick || node.kind !== "theater") return;
    ev.preventDefault();
    ev.stopPropagation();
    dragRef.current = { id: node.id, pointerId: ev.pointerId, moved: false };
    ev.currentTarget.setPointerCapture(ev.pointerId);
    const next = pickFromClient(ev.clientX, ev.clientY, node.label || node.id);
    if (next) {
      saveDraggedTheater(node.id, { x: next.x, y: next.y });
      setPick(next);
    }
  };
  const onTheaterPointerMove = (node: MapNode) => (ev: React.PointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current;
    if (!coordPick || !drag || drag.id !== node.id || drag.pointerId !== ev.pointerId) return;
    ev.preventDefault();
    ev.stopPropagation();
    drag.moved = true;
    const next = pickFromClient(ev.clientX, ev.clientY, node.label || node.id);
    if (!next) return;
    saveDraggedTheater(node.id, { x: next.x, y: next.y });
    setPick(next);
  };
  const onTheaterPointerUp = (node: MapNode) => (ev: React.PointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current;
    if (!coordPick || !drag || drag.id !== node.id || drag.pointerId !== ev.pointerId) return;
    ev.preventDefault();
    ev.stopPropagation();
    try { ev.currentTarget.releasePointerCapture(ev.pointerId); } catch {}
    const next = pickFromClient(ev.clientX, ev.clientY, node.label || node.id);
    if (next) {
      saveDraggedTheater(node.id, { x: next.x, y: next.y });
      setPick(next);
      console.log(`${node.id}: pct=(${next.x}, ${next.y}) svg=(${next.svgX}, ${next.svgY})`);
    }
    dragRef.current = null;
  };
  const changeMapZoom = React.useCallback((delta: number) => {
    setMapZoom((current) => Math.min(2.6, Math.max(0.8, +(current + delta).toFixed(2))));
  }, []);
  const nodeById = React.useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const regionPathItems = React.useMemo<RegionPathRenderItem[]>(
    () => REGION_PATH_GROUPS.filter((group) => !THEATER_ONLY_REGION_IDS.has(group.regionId)).map((group) => {
      const node = nodeById.get(group.regionId);
      return {
        id: group.regionId,
        name: node?.region?.name || group.regionId,
        controlledBy: MAP_DISPLAY_POWER_OVERRIDES[group.regionId] || String(node?.region?.controlled_by || "ming"),
        unrest: node?.region?.unrest || 0,
        risk: node?.risk || 0,
        labelX: node?.x ?? 50,
        labelY: node?.y ?? 50,
        paths: group.paths,
      };
    }),
    [nodeById],
  );
  const externalPathItems = React.useMemo<ExternalPathRenderItem[]>(
    () => {
      return EXTERNAL_PATH_GROUPS.filter((group) => group.paths.length > 0).map((group) => {
        const node = nodeById.get(group.id);
        return {
          ...group,
          labelX: node?.x ?? 50,
          labelY: node?.y ?? 50,
        };
      });
    },
    [nodeById],
  );

  React.useLayoutEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const next: Record<string, SvgLabelPosition> = {};
    const pathsByRegion = new Map<string, SVGGraphicsElement[]>();
    svg.querySelectorAll<SVGGraphicsElement>("path[data-region-id]").forEach((path) => {
      const id = path.getAttribute("data-region-id");
      if (!id) return;
      const current = pathsByRegion.get(id) || [];
      current.push(path);
      pathsByRegion.set(id, current);
    });
    for (const [id, paths] of pathsByRegion.entries()) {
      let minX = Number.POSITIVE_INFINITY;
      let minY = Number.POSITIVE_INFINITY;
      let maxX = Number.NEGATIVE_INFINITY;
      let maxY = Number.NEGATIVE_INFINITY;
      for (const path of paths) {
        const box = path.getBBox();
        if (!Number.isFinite(box.x) || !Number.isFinite(box.y) || box.width <= 0 || box.height <= 0) continue;
        minX = Math.min(minX, box.x);
        minY = Math.min(minY, box.y);
        maxX = Math.max(maxX, box.x + box.width);
        maxY = Math.max(maxY, box.y + box.height);
      }
      if (Number.isFinite(minX) && Number.isFinite(minY) && Number.isFinite(maxX) && Number.isFinite(maxY)) {
        next[id] = {
          svgX: +((minX + maxX) / 2).toFixed(2),
          svgY: +((minY + maxY) / 2).toFixed(2),
        };
      }
    }
    setSvgLabelPositions(next);
  }, [regionPathItems, externalPathItems]);

  React.useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || didCenterRef.current) return;
    const board = viewport.querySelector<HTMLElement>(".map-tile");
    if (!board) return;
    didCenterRef.current = true;
    const mingCenterX = 0.58;
    const mingCenterY = 0.42;
    viewport.scrollLeft = Math.max(0, board.offsetLeft + board.clientWidth * mingCenterX - viewport.clientWidth / 2);
    viewport.scrollTop = Math.max(0, board.offsetTop + board.clientHeight * mingCenterY - viewport.clientHeight / 2);
  }, []);

  return (
    <section
      ref={viewportRef}
      className="grand-map"
      aria-label="大明地图"
    >
      {coordPick ? (
        <div className="coord-toolbox">
          <button
            className={`coord-tool-button ${pencilMode ? "active" : ""}`}
            onClick={(ev) => {
              ev.stopPropagation();
              setPencilMode((current) => {
                const next = !current;
                if (next) setTerrainMode(false);
                return next;
              });
            }}
            aria-label="铅笔工具"
            title="铅笔工具"
          >
            <Pencil size={16} />
            <span>{pencilMode ? "铅笔开启" : "铅笔"}</span>
          </button>
          <button
            className="coord-tool-button"
            onClick={(ev) => {
              ev.stopPropagation();
              savePencilLine([]);
              console.log("pencil line cleared");
            }}
            aria-label="清除铅笔线"
            title="清除铅笔线"
          >
            <Eraser size={16} />
          </button>
          <button
            className={`coord-tool-button ${terrainMode ? "active" : ""}`}
            onClick={(ev) => {
              ev.stopPropagation();
              setTerrainMode((current) => {
                const next = !current;
                if (next) setPencilMode(false);
                return next;
              });
            }}
            aria-label="拖动底图"
            title="拖动底图"
          >
            <Move size={16} />
            <span>{terrainMode ? "底图开启" : "底图"}</span>
          </button>
          <button
            className="coord-tool-button icon-only"
            onClick={(ev) => {
              ev.stopPropagation();
              resizeTerrain(0.96);
            }}
            aria-label="缩小底图"
            title="缩小底图"
          >
            <ZoomOut size={16} />
          </button>
          <button
            className="coord-tool-button icon-only"
            onClick={(ev) => {
              ev.stopPropagation();
              resizeTerrain(1.04);
            }}
            aria-label="放大底图"
            title="放大底图"
          >
            <ZoomIn size={16} />
          </button>
          <button
            className="coord-tool-button icon-only"
            onClick={(ev) => {
              ev.stopPropagation();
              saveTerrainTransform(defaultTerrainTransform);
            }}
            aria-label="重置底图"
            title="重置底图"
          >
            <RotateCcw size={15} />
          </button>
          <button
            className="coord-tool-button icon-only"
            onClick={(ev) => {
              ev.stopPropagation();
              changeMapZoom(-0.15);
            }}
            aria-label="缩小地图"
            title="缩小地图"
          >
            <ZoomOut size={16} />
          </button>
          <span className="coord-zoom-readout">{Math.round(mapZoom * 100)}%</span>
          <button
            className="coord-tool-button icon-only"
            onClick={(ev) => {
              ev.stopPropagation();
              changeMapZoom(0.15);
            }}
            aria-label="放大地图"
            title="放大地图"
          >
            <ZoomIn size={16} />
          </button>
          <button
            className="coord-tool-button icon-only"
            onClick={(ev) => {
              ev.stopPropagation();
              setMapZoom(1);
            }}
            aria-label="重置缩放"
            title="重置缩放"
          >
            <RotateCcw size={15} />
          </button>
        </div>
      ) : null}
      <div
        className={`map-strip ${pencilMode ? "pencil-mode" : ""} ${terrainMode ? "terrain-mode" : ""}`}
        style={coordPick ? {
          width: `${1900 * mapZoom + 320}px`,
          height: `${(1900 * mapZoom * 206) / 276 + 240}px`,
        } : undefined}
        onClick={onPickClick}
        onPointerDown={onPencilPointerDown}
        onPointerMove={onPencilPointerMove}
        onPointerUp={onPencilPointerUp}
        onPointerCancel={onPencilPointerUp}
      >
        <div
          className="map-tile"
          ref={mapTileRef}
          style={coordPick ? { width: `${1900 * mapZoom}px` } : undefined}
        >
            <svg
              ref={svgRef}
              className="province-map-layer"
              viewBox={MAP_VIEW_BOX}
              preserveAspectRatio="xMinYMin meet"
            >
              <image
                className={`map-terrain-image ${coordPick && terrainMode ? "draggable" : ""}`}
                href="/ming-1627-terrain-map.png"
                x={terrainTransform.x}
                y={terrainTransform.y}
                width={terrainTransform.width}
                height={terrainTransform.height}
                preserveAspectRatio="xMidYMid slice"
                onPointerDown={onTerrainPointerDown}
                onPointerMove={onTerrainPointerMove}
                onPointerUp={onTerrainPointerUp}
                onPointerCancel={onTerrainPointerUp}
              />
              {externalPathItems.map((group, groupIndex) => {
                const selected = selectedId === group.id;
                const fill = EXTERNAL_MAP_COLOR;
                return (
                  <g
                    key={`external:${group.id}:${groupIndex}:paths`}
                    className={`province-external power-${group.powerId} ${selected ? "selected" : ""}`}
                    data-external-id={group.id}
                    style={{ "--province-fill": fill } as React.CSSProperties}
                    role="button"
                    tabIndex={0}
                    aria-label={`查看${group.name}`}
                    aria-pressed={selected}
                    onClick={(ev) => {
                      ev.stopPropagation();
                      ev.currentTarget.blur();
                      onSelect(group.id);
                    }}
                    onKeyDown={(ev) => {
                      if (ev.key === "Enter" || ev.key === " ") {
                        ev.preventDefault();
                        onSelect(group.id);
                      }
                    }}
                  >
                    {group.paths.map((path) => (
                      <path
                        key={`external:${group.id}:${groupIndex}:${path.id}`}
                        data-map-path-id={path.id}
                        data-region-id={group.id}
                        fill={fill}
                        fillOpacity={EXTERNAL_MAP_OPACITY}
                        d={path.d}
                      >
                        <title>{group.name}</title>
                      </path>
                    ))}
                  </g>
                );
              })}
              {regionPathItems.map((region, regionIndex) => {
                const selected = selectedId === region.id;
                const fill = getRegionMapColor(region);
                return (
                  <g
                    key={`region:${region.id}:${regionIndex}:paths`}
                    data-region-id={region.id}
                    className={`province-region power-${region.controlledBy} ${selected ? "selected" : ""} ${region.controlledBy === "ming" && region.unrest > UNREST_DANGER_THRESHOLD ? "danger" : ""}`}
                    style={{ "--province-fill": fill } as React.CSSProperties}
                    role="button"
                    tabIndex={0}
                    aria-label={`查看${region.name}`}
                    aria-pressed={selected}
                    onClick={(ev) => {
                      ev.stopPropagation();
                      ev.currentTarget.blur();
                      onSelect(region.id);
                    }}
                    onKeyDown={(ev) => {
                      if (ev.key === "Enter" || ev.key === " ") {
                        ev.preventDefault();
                        onSelect(region.id);
                      }
                    }}
                  >
                    {region.paths.map((path) => (
                      <path
                        key={`region:${region.id}:${regionIndex}:${path.id}`}
                        data-map-path-id={path.id}
                        data-region-id={region.id}
                        fill={fill}
                        fillOpacity={getRegionMapOpacity(region)}
                        d={path.d}
                      >
                        <title>{region.name}</title>
                      </path>
                    ))}
                  </g>
                );
              })}
              {pencilLine.length > 1 ? (
                <polyline
                  className="coord-pencil-line"
                  points={pencilLine.map((point) => `${point.svgX},${point.svgY}`).join(" ")}
                />
              ) : null}
              <g className="map-label-layer" aria-hidden="true">
                {externalPathItems.map((group, groupIndex) => {
                  const pos = svgLabelPositions[group.id] || svgCoordFromPct(group.labelX, group.labelY);
                  return (
                    <text
                      key={`external:${group.id}:${groupIndex}:label`}
                      className="map-region-label external"
                      x={pos.svgX}
                      y={pos.svgY}
                    >
                      {group.name.split(" / ")[0]}
                    </text>
                  );
                })}
                {regionPathItems.map((region, regionIndex) => {
                  const pos = svgLabelPositions[region.id] || svgCoordFromPct(region.labelX, region.labelY);
                  return (
                    <text
                      key={`region:${region.id}:${regionIndex}:label`}
                      className="map-region-label"
                      x={pos.svgX}
                      y={pos.svgY}
                    >
                      {region.name.split(" / ")[0]}
                    </text>
                  );
                })}
              </g>
            </svg>
            {nodes.filter((node) => node.kind === "theater").map((node) => {
              const selected = selectedId === node.id;
              const danger = node.risk > 175;
              const override = draggedTheaters[node.id];
              const nodeX = override?.x ?? node.x;
              const nodeY = override?.y ?? node.y;
              return (
                <button
                  key={node.id}
                  className={`map-node ${node.kind} ${coordPick ? "draggable" : ""} ${selected ? "selected" : ""} ${danger ? "danger" : ""}`}
                  style={{ left: `${nodeX}%`, top: `${nodeY}%` }}
                  data-node-id={node.id}
                  onPointerDown={onTheaterPointerDown(node)}
                  onPointerMove={onTheaterPointerMove(node)}
                  onPointerUp={onTheaterPointerUp(node)}
                  onPointerCancel={onTheaterPointerUp(node)}
                  onClick={(ev) => {
                    ev.stopPropagation();
                    if (coordPick) return;
                    onSelect(node.id);
                  }}
                  aria-label={`查看${node.region?.name || node.label}`}
                  aria-pressed={selected}
                  tabIndex={0}
                >
                  {node.kind === "theater" ? <Shield size={16} /> : <MapPinned size={15} />}
                  <span>{node.region?.name.split(" / ")[0] || node.label}</span>
                </button>
              );
            })}
        </div>
      </div>
      {coordPick && pick ? (
        <div className="coord-pick-readout">
          {pick.label ? `${pick.label} ` : ""}pct: ({pick.x}, {pick.y}) &nbsp; svg: ({pick.svgX}, {pick.svgY})
        </div>
      ) : null}
    </section>
  );
}

function MapIntelPanel({ node, style, onClose }: { node: MapNode; style?: React.CSSProperties; onClose: () => void }) {
  const titleId = React.useId();
  const closeRef = React.useRef<HTMLButtonElement | null>(null);
  const previousFocusRef = React.useRef<((HTMLElement | SVGElement) & { focus: () => void }) | null>(null);
  const onCloseRef = React.useRef(onClose);

  React.useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  const closePanel = React.useCallback(() => {
    onCloseRef.current();
  }, []);

  React.useEffect(() => {
    const activeElement = document.activeElement;
    previousFocusRef.current = activeElement && typeof (activeElement as { focus?: unknown }).focus === "function"
      ? activeElement as (HTMLElement | SVGElement) & { focus: () => void }
      : null;

    const focusTimer = window.setTimeout(() => {
      closeRef.current?.focus();
    }, 0);

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closePanel();
    };

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", handleKeyDown);
      const previousFocus = previousFocusRef.current;
      if (previousFocus && document.contains(previousFocus)) {
        window.setTimeout(() => previousFocus.focus(), 0);
      }
    };
  }, [closePanel]);

  return (
    <section
      className="map-intel-panel overlay-panel"
      style={style}
      role="dialog"
      aria-labelledby={titleId}
    >
      <button ref={closeRef} className="icon-button panel-close" aria-label="关闭地区详情" onClick={closePanel}>
        <X size={16} />
      </button>
      <NodeIntel node={node} titleId={titleId} />
    </section>
  );
}

function NodeIntel({ node, titleId }: { node: MapNode; titleId?: string }) {
  const region = node.region;
  const power = node.power;
  if (node.kind === "external") {
    return (
      <>
        <div className="panel-title">
          <MapPinned size={14} />
          <span id={titleId}>{region?.name || node.label}</span>
        </div>
        <table className="intel-table">
          <tbody>
            <tr><th>归属</th><td colSpan={3}>{labelPower(region?.controlled_by || power?.id || "")}</td></tr>
          </tbody>
        </table>
        <div className="empty-note">非大明辖治，详情不可见。</div>
      </>
    );
  }
  return (
    <>
      <div className="panel-title">
        {node.kind === "theater" ? <Shield size={14} /> : <MapPinned size={14} />}
        <span id={titleId}>{region?.name || node.label}</span>
      </div>
      {region ? (
        <table className="intel-table">
          <tbody>
            <tr><th>人口</th><td>{region.population}万</td><th>田亩</th><td>{region.registered_land}万亩</td></tr>
            <tr><th>民心</th><td>{region.public_support}</td><th>动乱</th><td>{region.unrest}</td></tr>
            <tr><th>粮食</th><td>{region.grain_security}</td><th>月税</th><td>{monthlyAmount(region.tax_per_turn)}万/月</td></tr>
            <tr><th>归属</th><td>{labelPower(region.controlled_by || "ming")}</td><th>类型</th><td>{region.kind}</td></tr>
            <tr><th>天灾</th><td colSpan={3}>{region.natural_disaster}</td></tr>
            <tr><th>人祸</th><td colSpan={3}>{region.human_disaster}</td></tr>
            <tr><th>状况</th><td colSpan={3}>{region.status}</td></tr>
          </tbody>
        </table>
      ) : null}
      {power && power.id !== "ming" ? (
        <>
          <div className="garrison-title">势力归属</div>
          <table className="intel-table">
            <tbody>
              <tr><th>势力</th><td>{power.name}</td><th>首领</th><td>{power.leader}</td></tr>
              <tr><th>立场</th><td>{power.stance}</td><th>类型</th><td>{power.kind}</td></tr>
              <tr><th>军力</th><td>{power.military_strength}</td><th>凝聚</th><td>{power.cohesion}</td></tr>
              <tr><th>影响</th><td>{power.leverage}</td><th>补给</th><td>{power.supply}</td></tr>
              <tr><th>诉求</th><td colSpan={3}>{power.agenda}</td></tr>
              <tr><th>近况</th><td colSpan={3}>{power.last_action}</td></tr>
            </tbody>
          </table>
        </>
      ) : null}
      <div className="garrison-title">驻军</div>
      {node.armies.length ? (
        <table className="intel-table">
          <thead>
            <tr><th>番号</th><th>兵种</th><th>兵</th><th>饷</th><th>士气</th><th>欠饷</th></tr>
          </thead>
          <tbody>
            {node.armies.map((army) => {
              const maint = army.maintenance_per_turn || 0;
              const arr = army.arrears || 0;
              const months = maint > 0 && arr > 0 ? (arr / maint) : 0;
              const arrText = arr > 0
                ? (months > 0 ? `${arr}万两（≈${months.toFixed(1)}月）` : `${arr}万两`)
                : '—';
              return (
                <tr key={army.id}>
                  <td>{army.name}</td>
                  <td>{army.troop_type}</td>
                  <td>{army.manpower}</td>
                  <td>{monthlyAmount(maint)}</td>
                  <td>{army.morale}</td>
                  <td>{arrText}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : <div className="empty-note">本地未记录常驻军。</div>}
      {region ? (
        <>
          <div className="garrison-title">建筑</div>
          {node.buildings && node.buildings.length ? (
            <table className="intel-table">
              <thead>
                <tr><th>名称</th><th>类别</th><th>等级</th><th>完好</th><th>维护</th><th>产出</th></tr>
              </thead>
              <tbody>
                {node.buildings.map((b) => (
                  <tr key={b.id}>
                    <td>{b.name}</td>
                    <td>{b.category}</td>
                    <td>{b.level}</td>
                    <td>{b.condition}</td>
                    <td>{b.maintenance}万/月</td>
                    <td>{b.output_metric ? `${b.output_metric}+${b.output_amount}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div className="empty-note">本地未记录建筑。</div>}
        </>
      ) : null}
    </>
  );
}

function Info({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className={`info-cell ${tone || ""}`}>
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

function MenuPage({
  status,
  onRefresh,
  onEnterGame,
  error,
  setError,
}: {
  status: MenuStatus | null;
  onRefresh: () => Promise<MenuStatus>;
  onEnterGame: () => Promise<void>;
  error: string;
  setError: (msg: string) => void;
}) {
  const [busy, setBusy] = React.useState<string>("");
  const [showApiForm, setShowApiForm] = React.useState(false);
  const [showSaveList, setShowSaveList] = React.useState(false);

  const guard = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError("");
    try {
      await fn();
    } catch (err: any) {
      setError(err?.message || String(err));
    } finally {
      setBusy("");
    }
  };

  const onNewGame = () =>
    guard("新游戏中...", async () => {
      if (status?.has_main_db && !window.confirm("将覆盖当前主进度，是否继续？建议先在游戏中保存为存档。")) return;
      await api("/api/menu/new_game", { method: "POST" });
      await onEnterGame();
    });

  const onContinue = () =>
    guard("载入上次进度...", async () => {
      await api("/api/menu/continue", { method: "POST" });
      await onEnterGame();
    });

  const onLoadSave = (name: string) =>
    guard(`载入「${name}」...`, async () => {
      await api(`/api/menu/load_save/${encodeURIComponent(name)}`, { method: "POST" });
      await onEnterGame();
    });

  const hasKey = !!status?.has_api_key;
  const hasMainDb = !!status?.has_main_db;
  const saves = status?.saves || [];
  const campaigns = status?.campaigns || [];

  return (
    <div className="menu-screen">
      <div className="menu-panel">
        <h1 className="menu-title">明末力挽狂澜</h1>
        <p className="menu-subtitle">崇祯元年正月 · 召大臣议天下事</p>

        {!hasKey && (
          <div className="menu-notice">尚未配置 API 接口。请先「设置 API」。</div>
        )}
        {error && <div className="menu-error">{error}</div>}

        <div className="menu-buttons">
          <button className="menu-btn primary" disabled={!hasKey || !!busy} onClick={onNewGame}>
            开始新游戏
          </button>
          <button className="menu-btn" disabled={!hasKey || !hasMainDb || !!busy} onClick={onContinue} title={hasMainDb ? "" : "无上次进度"}>
            继续
          </button>
          <button className="menu-btn" disabled={!hasKey || !!busy || !saves.length} onClick={() => setShowSaveList(true)} title={saves.length ? "" : "暂无存档"}>
            加载存档 {saves.length ? `(${saves.length})` : ""}
          </button>
          <button className="menu-btn" disabled={!!busy} onClick={() => setShowApiForm(true)}>
            设置 API {hasKey ? "" : "（必需）"}
          </button>
        </div>

        {busy && <div className="menu-busy">{busy}</div>}
        {hasKey && status?.llm && (
          <div className="menu-llm-info">
            当前接口：{status.llm.base_url} · {status.llm.model}
          </div>
        )}
      </div>

      {showApiForm && (
        <ApiSettingsModal
          initial={status?.llm}
          onClose={() => setShowApiForm(false)}
          onSaved={async () => {
            setShowApiForm(false);
            await onRefresh();
          }}
        />
      )}

      {showSaveList && (
        <SaveListModal
          campaigns={campaigns}
          onClose={() => setShowSaveList(false)}
          onLoad={async (name) => {
            setShowSaveList(false);
            await onLoadSave(name);
          }}
          onDelete={async (name) => {
            await api(`/api/menu/saves/${encodeURIComponent(name)}`, { method: "DELETE" });
            await onRefresh();
          }}
        />
      )}
    </div>
  );
}

function ApiSettingsModal({
  initial,
  onClose,
  onSaved,
}: {
  initial?: {
    base_url: string;
    model: string;
    has_api_key: boolean;
    max_tokens?: number;
    timeout_seconds?: number;
    thinking_level?: string;
    advanced_model?: string;
    advanced_base_url?: string;
    has_advanced_api_key?: boolean;
    advanced_thinking_level?: string;
  };
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [baseUrl, setBaseUrl] = React.useState(initial?.base_url || "https://api.deepseek.com");
  const [model, setModel] = React.useState(initial?.model || "deepseek-chat");
  const [advancedModel, setAdvancedModel] = React.useState(initial?.advanced_model || "");
  const [advancedBaseUrl, setAdvancedBaseUrl] = React.useState(initial?.advanced_base_url || "");
  const [advancedApiKey, setAdvancedApiKey] = React.useState("");
  const [advancedThinkingLevel, setAdvancedThinkingLevel] = React.useState(initial?.advanced_thinking_level || "");
  const [apiKey, setApiKey] = React.useState("");
  const [maxTokens, setMaxTokens] = React.useState(String(initial?.max_tokens || 8000));
  const [timeoutSeconds, setTimeoutSeconds] = React.useState(String(initial?.timeout_seconds || 180));
  const [thinkingLevel, setThinkingLevel] = React.useState(initial?.thinking_level || "");
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");

  const onSave = async () => {
    setBusy(true);
    setErr("");
    try {
      const response = await fetch("/api/menu/llm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_url: baseUrl.trim(),
          model: model.trim(),
          api_key: apiKey.trim(),
          max_tokens: parseInt(maxTokens) || 8000,
          timeout_seconds: parseFloat(timeoutSeconds) || 180,
          thinking_level: thinkingLevel.trim(),
          advanced_model: advancedModel.trim(),
          advanced_base_url: advancedBaseUrl.trim(),
          advanced_api_key: advancedApiKey.trim(),
          advanced_thinking_level: advancedThinkingLevel.trim(),
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({ detail: response.statusText }));
        const detail = normalizeApiError(payload, response.statusText);
        setErr(`code: ${detail.code || "unknown"}\nmessage: ${detail.message || response.statusText}`);
        return;
      }
      await onSaved();
    } catch (e: any) {
      setErr(`code: request_failed\nmessage: ${e?.message || String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="menu-modal-bg" onClick={onClose}>
      <div className="menu-modal" onClick={(e) => e.stopPropagation()}>
        <h2>设置 API</h2>
        <p className="menu-hint">推荐 DeepSeek（中文好、价格便宜）。配置写入本地，不上传。</p>
        <div className="menu-modal-fields">
          <label>
            Base URL
            <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.deepseek.com" />
          </label>
          <label>
            Model
            <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="deepseek-chat" />
          </label>
          <label>
            Thinking Level <small className="menu-hint">（空=默认，请填写你的模型支持的值。）</small>
            <input value={thinkingLevel} onChange={(e) => setThinkingLevel(e.target.value)} placeholder="默认" />
          </label>
          <label>
            Advanced Model <small className="menu-hint">（推演 + 打分专用；留空 fallback）</small>
            <input value={advancedModel} onChange={(e) => setAdvancedModel(e.target.value)} placeholder="deepseek-reasoner / gpt-5" />
          </label>
          <label>
            Advanced Base URL <small className="menu-hint">（advanced 专用网关；留空复用主 Base URL）</small>
            <input value={advancedBaseUrl} onChange={(e) => setAdvancedBaseUrl(e.target.value)} placeholder="https://other-gateway/v1" />
          </label>
          <label>
            Advanced API Key{" "}
            <small className="menu-hint">{initial?.has_advanced_api_key ? "(已配置；留空保留)" : "(留空=复用主 API Key)"}</small>
            <input type="password" value={advancedApiKey} onChange={(e) => setAdvancedApiKey(e.target.value)} placeholder={initial?.has_advanced_api_key ? "(已配置；如需更换请重新填写)" : "留空=复用主 Key"} />
          </label>
          <label>
            Advanced Thinking Level <small className="menu-hint">（空=默认，请填写你的模型支持的值。）</small>
            <input value={advancedThinkingLevel} onChange={(e) => setAdvancedThinkingLevel(e.target.value)} placeholder="默认" />
          </label>
          <label>
            Max Tokens
            <input type="number" min={256} max={65536} value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} placeholder="8000" />
          </label>
          <label>
            Timeout Seconds
            <input type="number" min={10} max={900} value={timeoutSeconds} onChange={(e) => setTimeoutSeconds(e.target.value)} placeholder="180" />
          </label>
          <label>
            API Key
            <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={initial?.has_api_key ? "(已配置；如需更换请重新填写)" : "sk-..."} />
          </label>
          {err && <div className="menu-error">{err}</div>}
        </div>
        <div className="menu-modal-actions">
          <button onClick={onClose} disabled={busy}>取消</button>
          <button className="primary" onClick={onSave} disabled={busy || !baseUrl.trim() || !model.trim() || (!apiKey.trim() && !initial?.has_api_key)}>
            {busy ? "保存中..." : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

function SaveListModal({
  campaigns,
  onClose,
  onLoad,
  onDelete,
}: {
  campaigns: MenuCampaign[];
  onClose: () => void;
  onLoad: (name: string) => Promise<void>;
  onDelete: (name: string) => Promise<void>;
}) {
  const hasAny = campaigns.some((c) => c.saves.length);
  const [delBusy, setDelBusy] = React.useState("");
  const [delErr, setDelErr] = React.useState("");
  const handleDelete = async (name: string, label?: string) => {
    if (!window.confirm(`删除存档「${label || name}」？此操作不可撤销。`)) return;
    setDelBusy(name);
    setDelErr("");
    try {
      await onDelete(name);
    } catch (e) {
      setDelErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDelBusy("");
    }
  };
  return (
    <div className="menu-modal-bg" onClick={onClose}>
      <div className="menu-modal" onClick={(e) => e.stopPropagation()}>
        <h2>加载存档</h2>
        <div className="menu-modal-scroll">
          {delErr ? <div className="menu-error">{delErr}</div> : null}
          {hasAny ? (
            <div className="menu-campaign-list">
              {campaigns.map((c) => (
                <div key={c.campaign_id || "__manual__"} className="menu-campaign">
                  <div className="menu-campaign-head">
                    <span>{c.kind === "manual" ? "手动存档" : `战局 ${c.campaign_id.slice(0, 6)}`}</span>
                    {c.current ? <span className="menu-campaign-badge">本局</span> : null}
                  </div>
                  <ul className="menu-save-list">
                    {c.saves.map((s) => (
                      <li key={s.name} className="menu-save-row">
                        <button className="menu-save-load" onClick={() => onLoad(s.name)}>
                          <span className="save-name">{s.label || s.name}</span>
                          <span className="save-meta">{new Date(s.mtime * 1000).toLocaleString("zh-CN")}</span>
                        </button>
                        <button
                          className="menu-save-del"
                          title="删除存档"
                          disabled={delBusy === s.name}
                          onClick={() => handleDelete(s.name, s.label)}
                        >
                          {delBusy === s.name ? <Loader2 size={14} className="spin" /> : <Trash2 size={14} />}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : (
            <p className="menu-empty">暂无存档。</p>
          )}
        </div>
        <div className="menu-modal-actions">
          <button onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
