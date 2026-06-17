import { useEffect, useRef, useState } from "react";
import { loadChat, streamChat } from "./api";
import { Portrait } from "./Portrait";
import type { ChatContext, ChatMessage, ChatResponse, Suggestion } from "./api";

const EMPTY_LOCAL_MESSAGES: ChatMessage[] = [];

function cleanDisplayText(raw: string): string {
  return String(raw || "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^\s*#{1,4}\s+/gm, "")
    .replace(/^\s*[-*]\s+/gm, "· ")
    .trim();
}

// 复用于「随侍太监」与「被传召大臣」的对话气泡 UI。
export function ChatPane({
  name,
  speakerLabel,
  onSummon,
  onWorldChanged,
  localMessages = EMPTY_LOCAL_MESSAGES,
  leadSuggestions = [],
  chatContext,
}: {
  name: string;
  speakerLabel: string;
  onSummon?: (next: string) => void;
  onWorldChanged?: () => void;
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
      } else if (resp.appointed_minister || resp.registered_minister) {
        setWin({ glyph: "擢", title: "得人", sub: `${resp.appointed_minister || resp.registered_minister} 入朝听用` });
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

  return (
    <div className="m-chat">
      <div className="m-chat-scroll" ref={scrollRef}>
        {messages.length + localMessages.length === 0 && !streaming && <p className="m-empty">尚未开问。</p>}
        {[...messages, ...localMessages].map((m, i) => (
          m.role === "user" ? (
            <div key={i} className="m-bubble is-emperor">
              <span className="m-bubble-who">朕</span>
              <p className="m-bubble-text">{m.content}</p>
            </div>
          ) : (
            <div key={i} className="m-bubble-row">
              <Portrait name={name} size={32} />
              <div className="m-bubble is-other">
                <span className="m-bubble-who">{speakerLabel}</span>
                <p className="m-bubble-text">{cleanDisplayText(m.content)}</p>
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
