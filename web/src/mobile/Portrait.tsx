import { useState } from "react";
import { usePerson } from "./personCtx";

// 人物头像（"有血有肉的真人"）：/portraits/minister_<名>.png，404 回退姓名首字圆牌。
// interactive 时点按打开人物详情卡（默认开；嵌在按钮内的场景传 false 避免双重触发）。
export function Portrait({ name, size = 40, interactive = true }: { name: string; size?: number; interactive?: boolean }) {
  const [err, setErr] = useState(false);
  const openPerson = usePerson();
  const px = `${size}px`;
  const onClick = interactive && name
    ? (e: React.MouseEvent) => { e.stopPropagation(); openPerson(name); }
    : undefined;
  const cls = `m-portrait${interactive && name ? " is-tappable" : ""}`;
  if (!name || err) {
    return (
      <span className={`${cls} m-portrait-fb`} style={{ width: px, height: px, fontSize: `${size * 0.42}px` }} onClick={onClick}>
        {name ? name[0] : "—"}
      </span>
    );
  }
  return (
    <img
      className={cls}
      src={`/portraits/minister_${encodeURIComponent(name)}.png`}
      style={{ width: px, height: px }}
      alt={name}
      loading="lazy"
      onError={() => setErr(true)}
      onClick={onClick}
    />
  );
}
