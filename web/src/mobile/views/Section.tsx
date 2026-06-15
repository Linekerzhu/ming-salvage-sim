import { useState } from "react";
import type { ReactNode } from "react";

// 可折叠分节（天下长列表收纳）：点标题展开/收起，缓解长滚动。
export function Section({
  title, count, tag, defaultOpen = true, children,
}: { title: string; count?: number; tag?: string; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="m-sec">
      <button className="m-sec-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="m-sec-caret">{open ? "▾" : "▸"}</span>
        <span className="m-sec-title">{title}</span>
        {count != null && <span className="m-sec-count">{count}</span>}
        {tag && <span className="m-sec-tag">{tag}</span>}
      </button>
      {open && <div className="m-sec-body">{children}</div>}
    </section>
  );
}
