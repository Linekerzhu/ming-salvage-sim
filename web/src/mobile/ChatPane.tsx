import { useEffect, useRef, useState } from "react";
import { loadChat, streamChat } from "./api";
import { Portrait } from "./Portrait";
import type { ChatContext, ChatMention, ChatMessage, ChatResponse, Suggestion, Tab } from "./api";
import { mentionSegments } from "./mentionLinks";

const EMPTY_LOCAL_MESSAGES: ChatMessage[] = [];

type NextStep = {
  label: string;
  hint: string;
  tab?: Tab;
  person?: string;
  profile?: string;
};

function cleanDisplayText(raw: string): string {
  return String(raw || "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^\s*#{1,4}\s+/gm, "")
    .replace(/^\s*[-*]\s+/gm, "· ")
    .trim();
}

function renderMentionedText(
  text: string,
  mentions: ChatMention[] | undefined,
  onOpenPerson?: (name: string) => void,
) {
  if (!onOpenPerson) return text;
  let key = 0;
  return mentionSegments(text, mentions).map((segment) => (
    segment.name ? (
      <button
        key={`mention-${key++}`}
        type="button"
        className="m-chat-mention"
        onClick={() => onOpenPerson(segment.name!)}
        title={`查看${segment.name}档案`}
      >
        {segment.text}
      </button>
    ) : segment.text
  ));
}

// 复用于「随侍太监」与「被传召大臣」的对话气泡 UI。
export function ChatPane({
  name,
  speakerLabel,
  onSummon,
  onGo,
  onWorldChanged,
  onOpenPerson,
  localMessages = EMPTY_LOCAL_MESSAGES,
  leadSuggestions = [],
  suggestionLimit = 6,
  compact = false,
  chatContext,
  arrivalNote = "",
}: {
  name: string;
  speakerLabel: string;
  onSummon?: (next: string) => void;
  onGo?: (tab: Tab) => void;
  onWorldChanged?: () => void;
  onOpenPerson?: (name: string) => void;
  localMessages?: ChatMessage[];
  leadSuggestions?: Suggestion[];
  suggestionLimit?: number;
  compact?: boolean;
  chatContext?: ChatContext;
  arrivalNote?: string;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [win, setWin] = useState<{ glyph: string; title: string; sub: string } | null>(null);
  const [nextStep, setNextStep] = useState<NextStep | null>(null);
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
    setNextStep(null);
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
    setNotice("");
    setNextStep(null);
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
      const minister = String(resp.recruited_minister || resp.appointed_minister || resp.registered_minister || "").trim();
      if (minister && onSummon) {
        setNextStep({ label: "召来验看", hint: `${minister} 已入名册`, person: minister });
      } else if (resp.proposed_directive?.text && onGo) {
        setNextStep({ label: "去诏旨核定", hint: "已有拟旨待批", tab: "edicts" });
      } else if (resp.secret_order_id && onGo) {
        setNextStep({ label: "看密令进度", hint: "密令已入诏旨账", tab: "edicts" });
      } else if (resp.directive_effect?.message && onGo) {
        setNextStep({ label: "看在办诏旨", hint: "旨意执行有变化", tab: "edicts" });
      } else if (goal?.id) {
        const owner = String(goal.minister_name || goal.owner || goal.actor || name).trim();
        const title = String(goal.title || "御前承诺").slice(0, 18);
        if (owner && onOpenPerson) {
          setNextStep({ label: "查履约档", hint: `已入履约账本：${title}`, profile: owner });
        } else if (owner && onSummon) {
          setNextStep({ label: "召履约人", hint: `已入履约账本：${title}`, person: owner });
        }
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
  const onNextStep = () => {
    if (!nextStep) return;
    if (nextStep.person && onSummon) {
      setNotice(`已传召 ${nextStep.person} 觐见。`);
      onSummon(nextStep.person);
      setNextStep(null);
      return;
    }
    if (nextStep.profile && onOpenPerson) {
      onOpenPerson(nextStep.profile);
      setNextStep(null);
      return;
    }
    if (nextStep.tab && onGo) {
      onGo(nextStep.tab);
      setNextStep(null);
    }
  };
  const visibleSuggestions = [
    ...leadSuggestions,
    ...suggestions.filter((s) => !leadSuggestions.some((lead) => lead.label === s.label || lead.text === s.text)),
  ].slice(0, Math.max(0, suggestionLimit));
  const transientMessages = messages.length === 0 ? localMessages : EMPTY_LOCAL_MESSAGES;
  const showArrival = Boolean(arrivalNote && messages.length === 0);

  return (
    <div className={`m-chat${compact ? " is-compact" : ""}`}>
      {showArrival && <div className="m-arrival">{arrivalNote}</div>}
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
      {nextStep && (
        <div className="m-next-step">
          <span>{nextStep.hint}</span>
          <button type="button" onClick={onNextStep}>{nextStep.label}</button>
        </div>
      )}
      {notice && <div className="m-chat-notice">{notice}</div>}

      {visibleSuggestions.length > 0 && (
        <details className="m-suggestions-panel">
          <summary className="m-suggestions-summary">
            <span>问法</span>
            <small>{visibleSuggestions.length} 条</small>
            <b>{visibleSuggestions.slice(0, 3).map((s) => s.label).join(" · ")}</b>
            <span className="m-disclosure-caret">卷</span>
          </summary>
          <div className="m-suggestions">
            {visibleSuggestions.map((s, i) => (
              <button key={i} className="m-sugg" disabled={busy} onClick={() => onSuggestion(s)}>
                {s.label}
              </button>
            ))}
          </div>
        </details>
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
