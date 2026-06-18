import type { ChatMention } from "./api";

const BLOCKED_MENTION_TERMS = new Set([
  "朝廷", "内廷", "外朝", "宫中", "宫里", "厂卫",
  "内阁", "司礼", "司礼监", "东厂", "锦衣卫", "北镇抚司", "南镇抚司", "镇抚司",
  "吏部", "户部", "礼部", "兵部", "刑部", "工部", "都察院", "翰林院", "詹事府",
  "大理寺", "太常寺", "光禄寺", "内官监", "御马监", "内书堂", "文书房", "南镇抚司",
  "南户部", "南京户部", "南京兵部", "南京礼部", "南京吏部", "南京工部", "南京刑部",
  "首辅", "次辅", "阁老", "前首辅", "原首辅", "大学士", "尚书", "侍郎",
  "掌印", "秉笔", "掌印太监", "秉笔太监", "都指挥使", "督师", "经略", "总督", "巡抚",
  "提督", "少司马", "本兵", "都督", "指挥", "百户", "千户", "内官", "内侍", "太监",
  "知府", "知县", "御史", "郎中", "主事", "监军", "总兵", "副将", "游击", "把总",
  "司礼监掌印", "司礼监秉笔", "司礼监文书房", "锦衣卫千户", "锦衣卫百户", "南镇抚司试百户",
]);
const ORG_MENTION_TOKENS = [
  "司礼", "司礼监", "东厂", "锦衣卫", "镇抚司", "内阁", "都察院", "翰林院", "詹事府",
  "大理寺", "太常寺", "光禄寺", "内官监", "御马监", "内书堂", "文书房", "南京",
];
const ORG_MENTION_SUFFIXES = ["监", "部", "院", "寺", "厂", "卫", "司", "府", "衙", "局", "营", "镇", "房", "堂"];
const SURNAME_TITLE_SUFFIXES = [
  "首辅", "次辅", "阁老", "大学士", "尚书", "侍郎", "掌印", "秉笔",
  "厂臣", "督师", "经略", "总督", "巡抚", "提督", "少司马", "本兵",
  "都督", "指挥", "百户", "千户", "公公", "伴伴",
  "太监", "内侍", "知府", "知县", "御史", "郎中", "主事", "监军",
];
const TITLE_ONLY_SUFFIXES = SURNAME_TITLE_SUFFIXES.filter((suffix) => suffix !== "公公" && suffix !== "伴伴");

export type MentionTerm = { name: string; term: string };

export function isBlockedMentionName(name: string) {
  if (BLOCKED_MENTION_TERMS.has(name)) return true;
  if (ORG_MENTION_TOKENS.some((token) => name.includes(token))) return true;
  if (name.length >= 2 && name.length <= 8 && ORG_MENTION_SUFFIXES.some((suffix) => name.endsWith(suffix))) return true;
  return false;
}

function isSurnameTitleAlias(term: string, name: string) {
  return Boolean(name && term.startsWith(name.slice(0, 1)) && term.length <= 4 && SURNAME_TITLE_SUFFIXES.some((suffix) => term.endsWith(suffix)));
}

export function isBlockedMentionTerm(term: string, name: string) {
  if (BLOCKED_MENTION_TERMS.has(term)) return true;
  if (term.length >= 2 && term.length <= 4 && TITLE_ONLY_SUFFIXES.some((suffix) => term.endsWith(suffix)) && !isSurnameTitleAlias(term, name)) return true;
  if (ORG_MENTION_TOKENS.some((token) => term.includes(token)) && !isSurnameTitleAlias(term, name)) return true;
  if (term.length >= 2 && term.length <= 8 && ORG_MENTION_SUFFIXES.some((suffix) => term.endsWith(suffix)) && !isSurnameTitleAlias(term, name)) return true;
  return false;
}

export function mentionTerms(mentions?: ChatMention[]): MentionTerm[] {
  const seen = new Set<string>();
  const terms: MentionTerm[] = [];
  for (const mention of mentions || []) {
    if (mention?.has_profile === false) continue;
    if (mention?.kind && mention.kind !== "character") continue;
    const name = String(mention?.name || "").trim();
    if (!name) continue;
    if (isBlockedMentionName(name)) continue;
    for (const rawTerm of [name, ...((mention.terms || []) as string[])]) {
      const term = String(rawTerm || "").trim();
      if (term.length < 2) continue;
      if (isBlockedMentionTerm(term, name)) continue;
      const key = `${name}:${term}`;
      if (seen.has(key)) continue;
      seen.add(key);
      terms.push({ name, term });
    }
  }
  terms.sort((a, b) => b.term.length - a.term.length);
  return terms;
}
