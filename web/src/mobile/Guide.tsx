// 御前导览 / 国势释义：首次亲政自动弹一次（localStorage 门控），之后可点顶部国势重看。
// 讲清「你是谁 + 人治→天下治怎么玩 + 君威/任事/精力是什么」，消除反直觉。

const SEEN_KEY = "ming.m.guide.seen.v1";

export const guideSeen = (): boolean => {
  try { return localStorage.getItem(SEEN_KEY) === "1"; } catch { return true; }
};
export const markGuideSeen = (): void => {
  try { localStorage.setItem(SEEN_KEY, "1"); } catch { /* ignore */ }
};

const LOOP: Array<{ k: string; t: string; d: string }> = [
  { k: "召", t: "召对 · 人治之门", d: "你只与身边的随侍太监直接说话。要见大臣，命随侍传召——大臣趋入奏对，事毕命其退下。随侍是你的耳目与顾问，也有私心。" },
  { k: "诏", t: "诏旨 · 下旨于人", d: "让大臣拟旨，你核定、拟诏颁布。旨意交人去办，各自到期复命。你不可能事事亲为——须在信任官僚与亲自微操间取舍（在办可催办/加拨/换人/收回）。" },
  { k: "案", t: "御案 · 批阅奏章", d: "百官奏疏陆续送达。看内阁票拟（建议处置）、写朱批交办；发部议＝按你的批语生成旨意去落实。精力有限，留中积压有代价。" },
  { k: "下", t: "天下 · 治理成果", d: "省份/军队/经济/局势/建筑/官制随你的人治而变。这是「天下治」的去向。" },
];

const VITALS: Array<{ n: string; d: string }> = [
  { n: "君威（势）", d: "你的号令之力（0–100）。高则诏令通行、可乾纲独断；低则政令不出午门。" },
  { n: "任事意愿", d: "百官敢不敢任事的集体心气（0–100）。问责越狠它越低→人人请旨观望、御案壅塞；为忠臣买单可回暖。" },
  { n: "精力", d: "每日处置力（每日 12）。批奏、召见大臣各耗精力；与随侍说话免费。竭则就寝（推时日）回血。" },
];

export function Guide({ onClose, onExit }: { onClose: () => void; onExit?: () => void }) {
  return (
    <div className="m-guide-backdrop" onClick={onClose}>
      <div className="m-guide" onClick={(e) => e.stopPropagation()}>
        <div className="m-guide-scroll">
          <h2 className="m-guide-title">亲政 · 以人治天下</h2>
          <p className="m-guide-lead">崇祯元年，内忧外患。你不能事必躬亲——治国，就是与这一个个有血有肉的人打交道：和他们说话、托付他们办事、在信任与微操之间权衡。</p>

          <h3 className="m-guide-h">一回合怎么玩</h3>
          <ul className="m-guide-loop">
            {LOOP.map((s) => (
              <li key={s.k} className="m-guide-step">
                <span className="m-guide-glyph">{s.k}</span>
                <div><b>{s.t}</b><p>{s.d}</p></div>
              </li>
            ))}
          </ul>

          <h3 className="m-guide-h">顶上三道国势</h3>
          <ul className="m-guide-vitals">
            {VITALS.map((v) => (
              <li key={v.n}><b>{v.n}</b>：{v.d}</li>
            ))}
          </ul>

          <p className="m-guide-foot">闭环：召对问对·拟旨 → 颁诏 → 推时日 → 到期复命，天下随之回应。</p>
          {onExit && <button className="m-guide-exit" onClick={onExit}>退出到菜单（换局/重开）</button>}
        </div>
        <button className="m-guide-cta" onClick={onClose}>朕已悉，亲政</button>
      </div>
    </div>
  );
}
