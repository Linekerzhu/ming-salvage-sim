import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";

const require = createRequire("/tmp/codex-playwright/package.json");
const { chromium } = require("playwright");

const outDir = path.resolve("artifacts/latest-8ddb937-playtest");
const base = "http://127.0.0.1:8011";
const chromePath = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const notes = [];

async function shot(page, name) {
  const file = path.join(outDir, name);
  await page.screenshot({ path: file, fullPage: true });
  notes.push(`screenshot ${name}`);
}

async function visibleText(page, pattern, timeout = 15000) {
  await page.getByText(pattern).first().waitFor({ state: "visible", timeout });
}

async function clickFirst(page, pattern, timeout = 15000) {
  const loc = page.getByText(pattern).first();
  await loc.waitFor({ state: "visible", timeout });
  await loc.click();
}

async function dismissOverlays(page) {
  const guide = page.getByText("朕已悉，亲政").first();
  if (await guide.isVisible().catch(() => false)) {
    await guide.click();
    await page.waitForTimeout(500);
  }
  const decision = page.getByText("朝局已动，继续").first();
  if (await decision.isVisible().catch(() => false)) {
    await decision.click();
    await page.waitForTimeout(500);
  }
  const sheetClose = page.locator(".m-sheet button").filter({ hasText: "关" }).last();
  if (await sheetClose.isVisible().catch(() => false)) {
    await sheetClose.click();
    await page.waitForTimeout(500);
  }
}

async function goTab(page, name) {
  await dismissOverlays(page);
  const tab = page.getByRole("tab", { name: new RegExp(name) }).first();
  await tab.waitFor({ state: "visible", timeout: 20000 });
  await tab.click();
  await page.waitForTimeout(700);
  await dismissOverlays(page);
}

await fs.mkdir(outDir, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  executablePath: chromePath,
  args: ["--no-sandbox"],
});
const context = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
  locale: "zh-CN",
});
const page = await context.newPage();
page.setDefaultTimeout(20000);
page.on("console", (msg) => {
  if (msg.type() === "error") notes.push(`console-error ${msg.text()}`);
});
page.on("pageerror", (err) => notes.push(`page-error ${err.message}`));

await page.goto(base, { waitUntil: "networkidle" });
await page.evaluate(() => localStorage.setItem("ming.m.guide.seen.v1", "1"));
await page.reload({ waitUntil: "networkidle" });
await shot(page, "01-menu.png");

if (await page.getByText("新游戏").first().isVisible().catch(() => false)) {
  await clickFirst(page, "新游戏");
}
await visibleText(page, /推时日|御案|召对/, 180000);
await dismissOverlays(page);
await shot(page, "02-home.png");

await goTab(page, "御案");
await visibleText(page, "司礼监代批红");
await shot(page, "03-desk-daipihong-off.png");
const enableDaipihong = page.getByRole("button", { name: "命内廷代批" }).first();
if (await enableDaipihong.isVisible().catch(() => false)) {
  await enableDaipihong.click();
  await page.waitForTimeout(1000);
}
await shot(page, "04-desk-daipihong-on.png");

await clickFirst(page, "换委任者");
await page.waitForTimeout(800);
await shot(page, "05-daipihong-keeper-picker.png");
const wang = page.getByText("王承恩").first();
if (await wang.isVisible().catch(() => false)) {
  await wang.click();
  await page.waitForTimeout(1000);
  await shot(page, "06-daipihong-wang-chengen.png");
}

await goTab(page, "召对");
await visibleText(page, /御前随侍|命其传召/);
await shot(page, "07-audience-eunuch.png");
await clickFirst(page, "命其传召");
await page.waitForTimeout(800);
await shot(page, "08-summon-sheet.png");
const firstMinister = page.locator(".m-sheet-row-face").first();
await firstMinister.waitFor({ state: "visible", timeout: 20000 });
const summoned = (await firstMinister.locator(".m-row-name").textContent())?.trim() || "";
await firstMinister.click();
await visibleText(page, /奉召觐见/);
await shot(page, "09-minister-audience.png");
await clickFirst(page, "奏对完成");
await visibleText(page, /已经告退|御前随侍/);
await shot(page, "10-audience-complete-back-to-eunuch.png");
notes.push(`summoned ${summoned}`);

await goTab(page, "御案");
await visibleText(page, "司礼监代批红");
const portrait = page.locator(".m-daipihong-keeper img, .m-daipihong-keeper .m-portrait, .m-daipihong-keeper button").first();
if (await portrait.count()) {
  await portrait.click({ force: true }).catch(() => {});
  await page.waitForTimeout(800);
}
if (!(await page.getByText("内廷旧事").first().isVisible().catch(() => false))) {
  await goTab(page, "召对");
  const face = page.locator(".m-audience-who .m-portrait, .m-audience-who img").first();
  await face.click({ force: true }).catch(() => {});
  await page.waitForTimeout(800);
}
await shot(page, "11-person-card.png");

await goTab(page, "天下");
await visibleText(page, "天下舆图");
const content = page.locator(".m-content").first();
await content.evaluate((el) => { el.scrollTop = el.scrollHeight; });
await page.waitForTimeout(800);
await shot(page, "12-realm-bottom.png");
const armyHead = page.locator(".m-sec-head").filter({ hasText: "军队" }).first();
if (await armyHead.isVisible().catch(() => false)) {
  await armyHead.click();
  await page.waitForTimeout(600);
}
const supervisor = page.getByText("遣监军钳制").first();
if (await supervisor.isVisible().catch(() => false)) {
  await supervisor.click();
  await page.waitForTimeout(1000);
  await shot(page, "13-realm-supervisor.png");
}

const summary = {
  url: base,
  model: await fetch(`${base}/api/menu/status`).then((r) => r.json()).then((j) => j.llm?.model || ""),
  viewport: "390x844 mobile",
  notes,
  bodyTextSample: (await page.locator("body").innerText()).slice(0, 2000),
};
await fs.writeFile(path.join(outDir, "playtest-summary.json"), JSON.stringify(summary, null, 2), "utf8");
await browser.close();
