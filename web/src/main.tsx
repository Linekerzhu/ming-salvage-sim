// 移动端优先入口（全面重做）。旧桌面版保留在 git 历史。
import { createRoot } from "react-dom/client";
import { App } from "./mobile/App";
import "./mobile.css";

const el = document.getElementById("root");
if (el) {
  createRoot(el).render(<App />);
}
