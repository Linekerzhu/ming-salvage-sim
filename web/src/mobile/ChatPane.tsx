import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { loadChat, streamChat } from "./api";
import { Portrait } from "./Portrait";
import type { ChatContext, ChatMention, ChatMessage, ChatResponse, Suggestion } from "./api";

const EMPTY_LOCAL_MESSAGES: ChatMessage[] = [];
const BLOCKED_MENTION_TERMS = new Set([
  "朝廷", "内廷", "外朝", "宫中", "宫里", "厂卫",
  "内阁", "司礼", "司礼监", "东厂", "锦衣卫", "北镇抚司", "南镇抚司", "镇抚司",
  "吏部", "户部", "礼部", "兵部", "刑部", "工部", "都察院", "翰林院", "詹事府",
  "大理寺", "太常寺", "光禄寺", "内官监", "御马监", "内书堂", "文书房", "南镇抚司",
  "南户部", "南京户部", "南京兵部", "南京礼部", "南京吏部", "南京工部", "南京刑部",
  "首辅", "次辅", "阁老", "前首辅", "原首辅", "大学士", "尚书", "侍郎",
  "掌印", "秉笔", "掌印太监", "秉笔太监", "都指挥使", "督师", "经略", "总督", "巡抚",
  "提督", "少司马", "本兵", "都督", "指挥", "百户", "千户", "内官", "内侍", "太监",
  "司礼监掌印", "司礼监秉笔", "锦衣卫千户", "锦衣卫百户", "南镇抚司试百户",
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
];
const TITLE_ONLY_SUFFIXES = SURNAME_TITLE_SUFFIXES.filter((suffix) => suffix !== "公公" && suffix !== "伴伴");

function cleanDisplayText(raw: string): string {
  return String(raw || "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^\s*#{1,4}\s+/gm, "")
    .replace(/^\s*[-*]\s+/gm, "· ")
    .trim();
}

function mentionTerms(mentions?: ChatMention[]): Array<{ name: string; term: string }> {
  const seen = new Set<string>();
  const terms: Array<{ name: string; term: string }> = [];
  for (const mention of mentions || []) {
    if (mention?.has_profile === false) continue;
    if (mention?.kind && mention.kind !== "character") continue;
    const name = String(mention?.name || "").trim();
    if (!name) continue;
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

function isSurnameTitleAlias(term: string, name: string) {
  return Boolean(name && term.startsWith(name.slice(0, 1)) && term.length <= 4 && SURNAME_TITLE_SUFFIXES.some((suffix) => term.endsWith(suffix)));
}

function isBlockedMentionTerm(term: string, name: string) {
  if (BLOCKED_MENTION_TERMS.has(term)) return true;
  if (term.length >= 2 && term.length <= 4 && TITLE_ONLY_SUFFIXES.some((suffix) => term.endsWith(suffix)) && !isSurnameTitleAlias(term, name)) return true;
  if (ORG_MENTION_TOKENS.some((token) => term.includes(token)) && !isSurnameTitleAlias(term, name)) return true;
  if (term.length >= 2 && term.length <= 8 && ORG_MENTION_SUFFIXES.some((suffix) => term.endsWith(suffix)) && !isSurnameTitleAlias(term, name)) return true;
  return false;
}

function renderMentionedText(
  text: string,
  mentions: ChatMention[] | undefined,
  onOpenPerson?: (name: string) => void,
) {
  const terms = mentionTerms(mentions);
  if (!terms.length || !onOpenPerson) return text;
  const out: Array<string | ReactNode> = [];
  let pos = 0;
  let key = 0;
  while (pos < text.length) {
    let best: { name: string; term: string; index: number } | null = null;
    for (const item of terms) {
      const index = text.indexOf(item.term, pos);
      if (index < 0) continue;
      if (!best || index < best.index || (index === best.index && item.term.length > best.term.length)) {
        best = { ...item, index };
      }
    }
    if (!best) {
      out.push(text.slice(pos));
      break;
    }
    if (best.index > pos) out.push(text.slice(pos, best.index));
    out.push(
      <button
        key={`mention-${key++}`}
        type="button"
        className="m-chat-mention"
        onClick={() => onOpenPerson(best!.name)}
        title={`查看${best.name}档案`}
      >
        {best.term}
      </button>,
    );
    pos = best.index + best.term.length;
  }
  return out;
}

// 复用于「随侍太监」与「被传召大臣」的对话气泡 UI。
export function ChatPane({
  name,
  speakerLabel,
  onSummon,
  onWorldChanged,
  onOpenPerson,
  localMessages = EMPTY_LOCAL_MESSAGES,
  leadSuggestions = [],
  chatContext,
}: {
  name: string;
  speakerLabel: string;
  onSummon?: (next: string) => void;
  onWorldChanged?: () => void;
  onOpenPerson?: (name: string) => void;
  localMessages?: ChatMessage[];
  leadSuggestions?: Suggestion[];
  chatContext?: ChatContext;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [win, setWin] = useState<{ glyph: string; title: string; sub: string } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!win) return;
    const t = setTimeout(() => setWin(null), 4200);
    return () => clearTimeout(t);
  }, [win]);

  useEffect(() => {
    let alive = true;
    setMessages([]);
    setSuggestions([]);
    setStreaming("");
    setNotice("");
    setWin(null);
    loadChat(name)
      .then((r) => {
        if (!alive) return;
        setMessages(r.history || []);
        setSuggestions(r.suggestions || []);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [name]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, localMessages, streaming, busy]);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(120, Math.max(44, el.scrollHeight))}px`;
  }, [input]);

  const send = async (text: string) => {
    const msg = text.trim();
    if (!msg || busy) return;
    setBusy(true);
    setInput("");
    setMessages((m) => [...m, { role: "user", content: msg }]);
    setStreaming("");
    try {
      const resp = await streamChat<ChatResponse>(name, msg, (d) => setStreaming((s) => s + d), chatContext);
      setStreaming("");
      if (resp.history) setMessages(resp.history);
      setSuggestions(resp.suggestions || []);
      // 驾驭高光：让"说动了人"这一刻被看见、被庆祝。
      const goal: any = resp.dialogue_goal || {};
      const committed = goal.committed || ["committed", "fulfilled", "达成", "promised"].includes(String(goal.status || ""));
      if (resp.directive_effect?.message) {
        setWin({ glyph: "督", title: resp.directive_effect.title || "旨意有动", sub: String(resp.directive_effect.message).slice(0, 42) });
      } else if (resp.dialogue_effect?.message) {
        setWin({ glyph: resp.recruited_minister ? "擢" : "调", title: resp.dialogue_effect.title || "奏对有动", sub: String(resp.dialogue_effect.message).slice(0, 42) });
      } else if (resp.recruited_minister || resp.appointed_minister || resp.registered_minister) {
        setWin({ glyph: "擢", title: "得人", sub: `${resp.recruited_minister || resp.appointed_minister || resp.registered_minister} 入朝听用` });
      } else if (resp.proposed_directive?.text) {
        setWin({ glyph: "旨", title: `${name}俯首拟旨`, sub: String(resp.proposed_directive.text).slice(0, 22) + "…（往「诏旨」核定颁布）" });
      } else if (resp.secret_order_id) {
        setWin({ glyph: "谍", title: "密令已下", sub: `${name}领密命而去` });
      } else if (committed) {
        setWin({ glyph: "诺", title: `${name}俯首允诺`, sub: String(goal.title || "已为陛下所动") });
      } else {
        setWin(null);
      }
      onWorldChanged?.();
      if (resp.next_minister && onSummon) {
        setNotice(`已传召 ${resp.next_minister} 觐见。`);
        onSummon(resp.next_minister);
      }
    } catch (e: any) {
      setNotice(String(e?.message || e || "对话失败"));
      setStreaming("");
    } finally {
      setBusy(false);
    }
  };

  const onSuggestion = (s: Suggestion) => {
    if (s.prefix) setInput(s.text);
    else void send(s.text);
  };
  const visibleSuggestions = [
    ...leadSuggestions,
    ...suggestions.filter((s) => !leadSuggestions.some((lead) => lead.label === s.label || lead.text === s.text)),
  ].slice(0, 6);
  const transientMessages = messages.length === 0 ? localMessages : EMPTY_LOCAL_MESSAGES;

  return (
    <div className="m-chat">
      <div className="m-chat-scroll" ref={scrollRef}>
        {messages.length + transientMessages.length === 0 && !streaming && <p className="m-empty">尚未开问。</p>}
        {[...messages, ...transientMessages].map((m, i) => (
          m.role === "user" ? (
            <div key={i} className="m-bubble is-emperor">
              <span className="m-bubble-who">朕</span>
              <p className="m-bubble-text">{renderMentionedText(m.content, m.mentions, onOpenPerson)}</p>
            </div>
          ) : (
            <div key={i} className="m-bubble-row">
              <Portrait name={name} size={32} />
              <div className="m-bubble is-other">
                <span className="m-bubble-who">{speakerLabel}</span>
                <p className="m-bubble-text">{renderMentionedText(cleanDisplayText(m.content), m.mentions, onOpenPerson)}</p>
              </div>
            </div>
          )
        ))}
        {streaming && (
          <div className="m-bubble-row">
            <Portrait name={name} size={32} />
            <div className="m-bubble is-other">
              <span className="m-bubble-who">{speakerLabel}</span>
              <p className="m-bubble-text">{cleanDisplayText(streaming)}<span className="m-caret">▍</span></p>
            </div>
          </div>
        )}
        {busy && !streaming && (
          <div className="m-bubble-row">
            <Portrait name={name} size={32} />
            <div className="m-bubble is-other m-typing">
              <span className="m-bubble-who">{speakerLabel}</span>
              <span className="m-dots" aria-label="正在思量"><i /><i /><i /></span>
            </div>
          </div>
        )}
      </div>

      {win && (
        <div className="m-win" key={`${win.title}-${win.sub}`}>
          <span className="m-win-seal">{win.glyph}</span>
          <div className="m-win-txt"><span className="m-win-title">{win.title}</span><span className="m-win-sub">{win.sub}</span></div>
        </div>
      )}
      {notice && <div className="m-chat-notice">{notice}</div>}

      {visibleSuggestions.length > 0 && (
        <div className="m-suggestions">
          {visibleSuggestions.map((s, i) => (
            <button key={i} className="m-sugg" disabled={busy} onClick={() => onSuggestion(s)}>
              {s.label}
            </button>
          ))}
        </div>
      )}

      <div className="m-chat-input">
        <textarea
          ref={inputRef}
          value={input}
          rows={1}
          placeholder={`与${speakerLabel}说…`}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(input);
            }
          }}
        />
        <button className="m-send" disabled={busy || !input.trim()} onClick={() => void send(input)}>
          {busy ? "…" : "奏"}
        </button>
      </div>
    </div>
  );
}
