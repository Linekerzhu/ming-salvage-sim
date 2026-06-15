// 开局菜单：新游戏 / 继续 / 读档。移动版入口（无运行游戏时先到这）。
import { useEffect, useState } from "react";
import { formatApiError } from "../api/client";
import { continueGame, loadSave, menuStatus, newGame } from "./api";
import type { MenuStatus } from "./api";

export function Menu({ onEnter }: { onEnter: () => void }) {
  const [status, setStatus] = useState<MenuStatus | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    menuStatus().then(setStatus).catch((e) => setErr(formatApiError(e, "无法读取菜单状态")));
  }, []);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(label);
    setErr("");
    try {
      await fn();
      onEnter();
    } catch (e: any) {
      setErr(formatApiError(e, "开局失败"));
      setBusy("");
    }
  };

  const saves = status?.saves || [];
  const noKey = status != null && !status.has_api_key;

  return (
    <div className="m-menu">
      <div className="m-menu-inner">
        <div className="m-menu-crest">明</div>
        <h1 className="m-menu-title">大明王朝 · 崇祯</h1>
        <p className="m-menu-sub">力挽天倾 · 以人治天下</p>

        {err && <div className="m-error">{err}</div>}
        {noKey && <div className="m-trap-hint">未配置 LLM（OPENAI_API_KEY）——新游戏需 LLM 推演开局。</div>}

        {busy === "new" ? (
          <div className="m-menu-loading">正在推演开局，请稍候…<span className="m-dots"><i /><i /><i /></span></div>
        ) : (
          <div className="m-menu-acts">
            <button className="m-menu-btn primary" disabled={!!busy} onClick={() => run("new", newGame)}>
              新游戏 · 崇祯元年
            </button>
            {status?.has_main_db && (
              <button className="m-menu-btn" disabled={!!busy} onClick={() => run("cont", continueGame)}>
                继续上次
              </button>
            )}
            {saves.length > 0 && (
              <div className="m-menu-saves">
                <span className="m-menu-saves-h">读档</span>
                {saves.slice(0, 8).map((sv) => (
                  <button key={sv.name} className="m-menu-save" disabled={!!busy}
                    onClick={() => run("load", () => loadSave(String(sv.name)))}>
                    {sv.label || sv.name}{sv.year ? ` · ${sv.year}年${sv.period || ""}月` : ""}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {status == null && !err && <p className="m-menu-loading">载入中…</p>}
      </div>
    </div>
  );
}
