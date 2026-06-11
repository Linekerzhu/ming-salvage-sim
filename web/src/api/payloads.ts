export type EncodedRow<T = Record<string, any>> = Partial<T> | unknown[];

export type CharacterIndexResponse<T = Record<string, any>> = {
  character_fields?: string[];
  characters?: EncodedRow<T>[];
};

export type MapResponse<T = Record<string, any>> = {
  node_fields?: string[];
  region_fields?: string[];
  army_fields?: string[];
  building_fields?: string[];
  nodes?: EncodedRow<T>[];
};

export type OrganizationWirePayload = Record<string, any> & {
  org_person_fields?: string[];
  org_slot_fields?: string[];
  org_institution_fields?: string[];
  institutions?: EncodedRow[];
  unassigned?: EncodedRow[];
};

export type MonthlyFollowupResponse<T = Record<string, any>> = {
  turn?: number;
  followup_fields?: string[];
  followup_defaults?: Partial<T>;
  followups?: EncodedRow<T>[];
};

export type GameStateWire = Record<string, any> & {
  minister_fields?: string[];
  region_fields?: string[];
  army_fields?: string[];
  power_fields?: string[];
  issue_fields?: string[];
  legacy_fields?: string[];
  ministers?: EncodedRow[];
  consorts?: EncodedRow[];
  regions?: EncodedRow[];
  armies?: EncodedRow[];
  powers?: EncodedRow[];
  issues?: EncodedRow[];
  legacies?: EncodedRow[];
};

export type PayloadDecodeOptions = {
  gameStartYear?: number;
  powerLabels?: Record<string, string>;
  statusLabels?: Record<string, string>;
  careerStateForStatus?: (status: string, statusLabel: string) => string;
};

const statusLabelFor = (status: string, explicit: unknown, options: PayloadDecodeOptions) => {
  return String(explicit || options.statusLabels?.[status] || status);
};

const careerStateFor = (status: string, statusLabel: string, explicit: unknown, options: PayloadDecodeOptions) => {
  return String(explicit || options.careerStateForStatus?.(status, statusLabel) || statusLabel);
};

export const decodeRows = <T extends Record<string, any>>(
  rows: EncodedRow<T>[] | undefined,
  fields: string[] | undefined,
): T[] => {
  const fieldList = fields || [];
  const fieldCount = fieldList.length;
  return (rows || []).map((row) => {
    if (!Array.isArray(row)) {
      return { ...row } as T;
    }
    const decoded: Record<string, unknown> = {};
    for (let index = 0; index < fieldCount; index += 1) {
      decoded[fieldList[index]] = row[index];
    }
    return decoded as T;
  });
};

export const decodeMinisterRows = <T extends Record<string, any> = Record<string, any>>(
  rows: EncodedRow<T>[] | undefined,
  fields: string[] | undefined,
  options: PayloadDecodeOptions = {},
): T[] => {
  return decodeRows<Record<string, any>>(rows, fields).map((decoded) => {
    const birthYear = Number(decoded.birth_year || 0);
    const startAge = Number(decoded.start_age || (birthYear ? (options.gameStartYear || 1627) - birthYear : 0));
    const powerId = String(decoded.power_id || "ming");
    const powerName = options.powerLabels?.[powerId] || powerId;
    const status = String(decoded.status || "active");
    const statusLabel = statusLabelFor(status, decoded.status_label, options);
    return {
      ...decoded,
      status,
      status_label: statusLabel,
      status_reason: String(decoded.status_reason || ""),
      career_state: careerStateFor(status, statusLabel, decoded.career_state, options),
      summary: String(decoded.summary || [powerName, decoded.faction, decoded.office_type, statusLabel].filter(Boolean).join(" · ")),
      start_age: startAge,
      age_label: String(decoded.age_label || (startAge ? `开局${startAge}岁` : "")),
      power_id: powerId,
      portrait_id: String(decoded.portrait_id || ""),
      favorite: !!decoded.favorite,
      skills: Array.isArray(decoded.skills) ? decoded.skills : [],
    } as unknown as T;
  });
};

export const decodeIssueRows = <T extends Record<string, any> = Record<string, any>>(
  rows: EncodedRow<T>[] | undefined,
  fields: string[] | undefined,
): T[] => {
  return decodeRows<Record<string, any>>(rows, fields).map((decoded) => ({
    ...decoded,
    kind: decoded.kind || "situation",
    bar_value: Number(decoded.bar_value || 0),
    severity: Number(decoded.severity || 0),
    inertia: Number(decoded.inertia || 0),
    tags: Array.isArray(decoded.tags) ? decoded.tags : [],
    effect_on_resolve: decoded.effect_on_resolve || {},
    effect_on_fail: decoded.effect_on_fail || {},
  } as unknown as T));
};

export const decodeLegacyRows = <T extends Record<string, any> = Record<string, any>>(
  rows: EncodedRow<T>[] | undefined,
  fields: string[] | undefined,
): T[] => {
  return decodeRows<Record<string, any>>(rows, fields).map((decoded) => ({
    ...decoded,
    modifiers: decoded.modifiers || {},
    remaining_months: Number(decoded.remaining_months ?? -1),
  } as unknown as T));
};

export const decodeCharacterIndexRows = <T extends Record<string, any> = Record<string, any>>(
  rows: EncodedRow<T>[] | undefined,
  fields: string[] | undefined,
  options: PayloadDecodeOptions = {},
): T[] => {
  return decodeRows<Record<string, any>>(rows, fields).map((decoded) => {
    const powerId = String(decoded.power_id || "ming");
    const powerName = String(decoded.power_name || options.powerLabels?.[powerId] || powerId || "大明");
    const status = String(decoded.status || "active");
    const statusLabel = statusLabelFor(status, decoded.status_label, options);
    return {
      ...decoded,
      status,
      status_reason: String(decoded.status_reason || ""),
      status_label: statusLabel,
      power_id: powerId,
      power_name: powerName,
      summary: String(decoded.summary || [powerName, decoded.faction, decoded.office_type, statusLabel].filter(Boolean).join(" · ")),
      portrait_available: !!decoded.portrait_available,
      can_summon: decoded.can_summon !== false,
    } as unknown as T;
  });
};

export const decodeMapNodes = <T extends Record<string, any> = Record<string, any>>(
  rows: EncodedRow<T>[] | undefined,
  nodeFields: string[] | undefined,
  regionFields: string[] | undefined,
  armyFields: string[] | undefined,
  buildingFields: string[] | undefined,
): T[] => {
  return decodeRows<Record<string, any>>(rows, nodeFields).map((decoded) => {
    const region = Array.isArray(decoded.region)
      ? decodeRows<Record<string, any>>([decoded.region], regionFields)[0]
      : decoded.region;
    const armies = Array.isArray(decoded.armies)
      ? decodeRows<Record<string, any>>(decoded.armies as EncodedRow[], armyFields)
      : decoded.armies;
    const buildings = Array.isArray(decoded.buildings)
      ? decodeRows<Record<string, any>>(decoded.buildings as EncodedRow[], buildingFields)
      : decoded.buildings;
    return {
      ...decoded,
      region: region || undefined,
      armies: Array.isArray(armies) ? armies : [],
      buildings: Array.isArray(buildings) ? buildings : [],
      label: decoded.label || undefined,
    } as unknown as T;
  });
};

const decodeOrgPeople = (
  rows: EncodedRow[] | undefined,
  fields: string[] | undefined,
  options: PayloadDecodeOptions,
) => {
  return decodeRows<Record<string, any>>(rows, fields).map((decoded) => {
    const status = String(decoded.status || "active");
    return {
      ...decoded,
      status,
      status_reason: String(decoded.status_reason || ""),
      status_label: statusLabelFor(status, decoded.status_label, options),
      power_id: String(decoded.power_id || "ming"),
    };
  });
};

const decodeOrgSlots = (
  rows: EncodedRow[] | undefined,
  slotFields: string[] | undefined,
  personFields: string[] | undefined,
  options: PayloadDecodeOptions,
) => {
  return decodeRows<Record<string, any>>(rows, slotFields).map((decoded) => ({
    ...decoded,
    count: Number(decoded.count || 0),
    holders: decodeOrgPeople(decoded.holders, personFields, options),
    filled_count: Number(decoded.filled_count || 0),
    vacancies: Number(decoded.vacancies || 0),
    overflow_count: Number(decoded.overflow_count || 0),
    open_pool: !!decoded.open_pool,
    match_hint: String(decoded.match_hint || ""),
  }));
};

export const decodeOrganizationPayload = <T extends Record<string, any> = Record<string, any>>(
  payload: OrganizationWirePayload | T | null | undefined,
  options: PayloadDecodeOptions = {},
): T | null => {
  if (!payload) return null;
  const wire = payload as OrganizationWirePayload;
  const institutions = decodeRows<Record<string, any>>(wire.institutions, wire.org_institution_fields).map((decoded) => ({
    ...decoded,
    custom: !!decoded.custom,
    readiness: Number(decoded.readiness || 0),
    coverage: Number(decoded.coverage || 0),
    holder_quality: Number(decoded.holder_quality || 0),
    execution_summary: String(decoded.execution_summary || ""),
    execution_risks: Array.isArray(decoded.execution_risks) ? decoded.execution_risks : [],
    slots: decodeOrgSlots(decoded.slots, wire.org_slot_fields, wire.org_person_fields, options),
    vacancy_count: Number(decoded.vacancy_count || 0),
    holder_count: Number(decoded.holder_count || 0),
  }));
  return {
    ...wire,
    institutions,
    unassigned: decodeOrgPeople(wire.unassigned, wire.org_person_fields, options),
  } as unknown as T;
};

export const decodeMonthlyFollowups = <T extends Record<string, any> = Record<string, any>>(
  rows: EncodedRow<T>[] | undefined,
  fields: string[] | undefined,
  defaults: Partial<T> | undefined,
): T[] => {
  const shared = (defaults || {}) as Record<string, any>;
  return decodeRows<Record<string, any>>(rows, fields).map((decoded) => ({
    ...shared,
    ...decoded,
    minister_name: String(decoded.minister_name || shared.minister_name || ""),
    priority: Number(decoded.priority || shared.priority || 0),
    reason_types: Array.isArray(decoded.reason_types) ? decoded.reason_types : (shared.reason_types || []),
    memory_hooks: Array.isArray(decoded.memory_hooks) ? decoded.memory_hooks : (shared.memory_hooks || []),
    risk_tags: Array.isArray(decoded.risk_tags) ? decoded.risk_tags : (shared.risk_tags || []),
    title: String(decoded.title || shared.title || ""),
    summary: String(decoded.summary || shared.summary || decoded.title || shared.title || ""),
    suggested_opening: String(decoded.suggested_opening || shared.suggested_opening || ""),
    preferred_stance: String(decoded.preferred_stance || shared.preferred_stance || "neutral"),
    truth_mode: String(decoded.truth_mode || shared.truth_mode || ""),
    personality_cue: String(decoded.personality_cue || shared.personality_cue || ""),
  } as unknown as T));
};

export const normalizeGameState = <T extends Record<string, any> = Record<string, any>>(
  data: GameStateWire,
  options: PayloadDecodeOptions = {},
): T => {
  const {
    minister_fields: ministerFields,
    region_fields: regionFields,
    army_fields: armyFields,
    power_fields: powerFields,
    issue_fields: issueFields,
    legacy_fields: legacyFields,
    ministers,
    consorts,
    regions,
    armies,
    powers,
    issues,
    legacies,
    ...rest
  } = data;
  return {
    ...rest,
    regions: decodeRows<Record<string, any>>(regions, regionFields),
    armies: decodeRows<Record<string, any>>(armies, armyFields),
    powers: decodeRows<Record<string, any>>(powers, powerFields),
    issues: decodeIssueRows(issues, issueFields),
    legacies: decodeLegacyRows(legacies, legacyFields),
    ministers: decodeMinisterRows(ministers, ministerFields, options),
    consorts: decodeMinisterRows(consorts, ministerFields, options),
  } as unknown as T;
};
