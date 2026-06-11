import { existsSync, readdirSync, rmSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(scriptDir, "../dist");
const publicPortraitDir = path.resolve(scriptDir, "../public/portraits");
const distPortraitDir = path.join(distDir, "portraits");

const removable = [
  "ming-ui-editor.html",
  "ming-ui-mockup.html",
  "ming-ui-preview.html",
  "portraits/_dna",
  "steam_assets",
  "ui-cutouts",
  "ui-reference-11236.jpg",
  "最新ui.jpg",
  "地图.webp",
  "bg_chat.png",
  "bg_chat.wm.png",
  "bg_court.png",
  "bg_court.wm.png",
  "bg_edict.png",
  "bg_edict.wm.png",
  "bg_harem.png",
  "bg_loading.png",
  "bg_loading.wm.png",
  "bg_node.png",
  "bg_node.original.png",
  "bg_state.png",
  "bg_state.wm.png",
  "icon_seal.png",
  "icon_seal.original.png",
  "icon_ming_emblem.png",
  "icon_scroll.png",
  "image.png",
  "ui/exact/案板.png",
  "ui/exact/zoushu.png",
  "ui/exact/mingxi.png",
  "ui/exact/miling.png",
  "ui/exact/lishi.png",
  "ui/exact/yiwen.png",
  "ui/exact/nizhao.png",
];

function bytesFor(target) {
  if (!existsSync(target)) return 0;
  const stats = statSync(target);
  if (stats.isDirectory()) {
    return readdirSync(target).reduce((total, entry) => total + bytesFor(path.join(target, entry)), 0);
  }
  return stats.size;
}

let removedBytes = 0;
let removedCount = 0;
for (const item of removable) {
  const target = path.join(distDir, item);
  if (!existsSync(target)) continue;
  removedBytes += bytesFor(target);
  removedCount += 1;
  rmSync(target, { recursive: true, force: true });
}

if (existsSync(publicPortraitDir) && existsSync(distPortraitDir)) {
  const publicPortraits = new Set(
    readdirSync(publicPortraitDir, { withFileTypes: true })
      .filter((entry) => entry.isFile())
      .map((entry) => entry.name),
  );

  for (const entry of readdirSync(distPortraitDir, { withFileTypes: true })) {
    if (!entry.isFile() || publicPortraits.has(entry.name)) continue;
    const target = path.join(distPortraitDir, entry.name);
    removedBytes += bytesFor(target);
    removedCount += 1;
    rmSync(target, { force: true });
  }
}

if (removedCount) {
  const mib = (removedBytes / 1024 / 1024).toFixed(1);
  console.log(`[prune-dist-assets] removed ${removedCount} runtime-unused assets (${mib} MiB)`);
}
