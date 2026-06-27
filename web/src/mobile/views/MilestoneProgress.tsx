import { useRef } from "react";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";
import type { DirectiveLifecycle } from "../api";

gsap.registerPlugin(useGSAP);

type Milestone = NonNullable<DirectiveLifecycle["milestones"]>[number];

/**
 * 进度条 + 多阶段里程碑（P1.4）。
 * 用 GSAP 平滑动画进度填充宽度，里程碑标记错峰浮入；已达成里程碑脉冲高亮。
 * 遵循 gsap-react skill：useGSAP + scope + refs，自动 cleanup。
 */
export function MilestoneProgress({
  progress,
  milestones,
  overdue,
}: {
  progress: number;
  milestones?: Milestone[];
  overdue?: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const fillRef = useRef<HTMLSpanElement>(null);
  const pct = Math.max(0, Math.min(100, Number(progress || 0)));
  const ms = (milestones || []).filter((m) => m.threshold < 100); // 终段(复命/100)不在轨上标点

  useGSAP(
    () => {
      // 进度填充宽度平滑过渡（GSAP 优于 CSS transition：可叠加 ease 与错峰）
      if (fillRef.current) {
        gsap.to(fillRef.current, {
          width: `${pct}%`,
          duration: 0.7,
          ease: "power2.out",
        });
      }
      // 里程碑标记错峰浮入
      gsap.from(".m-ms-marker", {
        scale: 0,
        opacity: 0,
        stagger: 0.06,
        duration: 0.35,
        ease: "back.out(2)",
      });
      // 已达成里程碑脉冲（金色光圈放大消散，可见）
      gsap.fromTo(
        ".m-ms-marker.is-done .m-ms-dot",
        { scale: 1, boxShadow: "0 0 0 0px rgba(212,175,55,0.65)" },
        { scale: 1.25, boxShadow: "0 0 0 10px rgba(212,175,55,0)", duration: 0.7, ease: "power2.out", stagger: 0.06, transformOrigin: "center" }
      );
    },
    { scope: containerRef, dependencies: [pct, ms.length] }
  );

  return (
    <div className={`m-prog-track m-prog-ms ${overdue ? "is-overdue" : ""}`} ref={containerRef}>
      <span className="m-prog-fill" ref={fillRef} style={{ width: `${pct}%` }} />
      {ms.map((m) => {
        const done = m.status === "done";
        return (
          <span
            key={m.key}
            className={`m-ms-marker ${done ? "is-done" : ""}`}
            style={{ left: `${m.threshold}%` }}
            title={`${m.label}（${m.threshold}%）${done ? "·已达成" : ""}`}
          >
            <span className="m-ms-dot" />
            <span className="m-ms-label">{m.label}</span>
          </span>
        );
      })}
    </div>
  );
}
