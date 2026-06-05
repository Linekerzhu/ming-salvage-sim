# NPC 画像生成规格

## 标准尺寸

- 立绘比例：固定 `2:3` 竖版全身站像，最小宽度 `512px`；发布前如要精修可生成 `1024 x 1536 px` 后再压缩。
- 构图：必须 head-to-toe 全身完整可见，包含冠帽、宽袖、双手、袍角、靴子；不得裁头、裁手、裁腰、裁膝、裁脚。
- DNA 参考板比例：`3:4`，2x2 四视图头模版式。
- 输出格式：优先透明 `PNG`；手动上传兼容 `WebP/JPEG`。
- 文件大小：单张建议不超过 `2MB`，上限 `8MB`。
- 命名规则：官员/外部/江湖人物用 `minister_姓名.png`；后宫人物用 `consort_姓名.png`
- 放置目录：`web/public/portraits/`
- 游戏内生成：`portrait_assets.image_blob` 存入当前 SQLite 存档，`characters.portrait_id` 指向 `generated:<asset_id>`。

## 新版生成链路

1. 每名人物先生成 DNA 参考板：严格 2x2，左上正面、右上右三分之二、左下左三分之二、右下侧面；所有格子的头骨、五官、耳形、颌线必须一致。汉人男性以束发/明式发际参考、无胡须作为面部识别锚；女性按发髻参考；不强调服装。
2. 初始发布图按人物开局状态生成固定立绘，不在游戏启动时反复生成；生成立绘时必须把该人物 DNA 图作为第一参考图，再叠加官服/太监服/锦衣卫/后宫样板图。
3. 游戏内新增人物、净身入宫、选妃升格、吏部铨补/名册补档、月末官职/身份变化会写入 `portrait_assets(status='pending')`，前端显示“画师绘制中”。
4. DNA 资产只按人物和存档生成一次；立绘资产按人物 + 当前官职/身份/服制签名生成，升迁或换身份时可重绘。
5. 302.ai nano-banana-2 同步返回图片后，图片 BLOB 写回同一存档；加载该存档即可继续使用。
6. 品级与服制由 `ming_sim/ranks.py` + `ming_sim/portraits.py` 统一判断：文官/武官 1-9 品、太监高/中/低/底层、锦衣卫、武将、后宫、江湖/社会人物、外部势力、未知官位自动设计。
7. 太监身份强制无胡须；官员成熟男性允许短须/髭须；净身转换后重绘会去除原胡须。

## 品级与服制规则

`content/characters.json` 已为全部人物补充：

- `rank_grade`：明制视觉品级，`1-9` 为外朝文武官品级，`0` 表示无明制品级。
- `rank_label`：用于画像和校对的人物品级/位分说明。
- `rank_category`：`civil`、`military`、`eunuch`、`harem`、`foreign:*`、`unranked` 等。

画像生成时以“当前官职”实时推断品级；静态字段用于开局归档与人工校对。玩家原创官位、江湖人物、外臣、内廷太监、后宫妃嫔不能硬套外朝品级，按身份服制生成。

明制官服按游戏视觉规则近似：

| 层级 | 颜色/制式 | 文官补子 | 武官补子 | 用途 |
|---|---|---|---|---|
| 1-4 品 | 绯红/赤红圆领补服，乌纱帽，高阶玉带/金玉带 | 一品仙鹤、二品锦鸡、三品孔雀、四品云雁 | 一二品狮子、三四品虎豹 | 阁臣、尚书、都御史、总督、督师、经略、高阶武臣 |
| 5-7 品 | 青蓝圆领补服，乌纱帽，素金/铜质束带 | 五品白鹇、六品鹭鸶、七品鸂鶒或低阶禽鸟 | 五品熊罴、六七品彪 | 郎中、主事、给事中、御史、编修、知县、千户等 |
| 8-9 品 | 绿青圆领补服，布靴/素靴，简素束带 | 八品黄鹂、九品鹌鹑 | 八品犀牛、九品海马 | 低阶杂职和需要低品视觉感的人物 |
| 太监/内廷 | 不使用外朝文武补子；按掌印/秉笔/监军/随堂/小火者分层 | 云纹、蟒纹、暗纹 | 无 | 司礼监、东厂、内廷执行链 |
| 后宫 | 凤冠霞帔或宫装，不穿外朝官服 | 凤、花卉、云纹 | 无 | 皇后、贵妃、妃嫔 |
| 外臣/江湖/在野 | 不穿明制品官补服 | 无 | 无 | 后金、蒙古、朝鲜、流寇、士绅、商人、僧道、侠客等 |

参考图路径（本地，不提交到仓库）：

- 高/中/低文官：`reference://高级文官.jpg`、`reference://中级文官.jpg`、`reference://低级文官.jpg`
- 太监分层：`reference://顶级太监.png`、`reference://高级太监.png`、`reference://中级太监.jpg`、`reference://低级太监.jpg`、`reference://底层太监.png`
- 锦衣卫/武将：`reference://高级锦衣卫.png`、`reference://低级锦衣卫.png`、`reference://高级武将.png`
- 后宫：`reference://皇后妃嫔.png`、`reference://皇后妃嫔2.png`

## 本地 nano banana 配置

真实 key 只写 `.env`，不要提交到仓库：

```bash
NANO_BANANA_API_KEY=your_302_ai_key_here
NANO_BANANA_BASE_URL=https://api.302.ai
NANO_BANANA_TEXT_ENDPOINT=/ws/api/v3/google/nano-banana-2/text-to-image
NANO_BANANA_ASPECT_RATIO=2:3
NANO_BANANA_RESOLUTION=1k
NANO_BANANA_TIMEOUT_SECONDS=180
MING_PORTRAIT_REFERENCE_DIR=~/Downloads
```

> `MING_PORTRAIT_REFERENCE_DIR` 用来解析 manifest 里的 `reference://...` 服装样板；仓库内 DNA 图会以相对路径记录。真实 key 和本机路径只写 `.env`，不要提交。

## 构图要求

- 竖版全身站像，完整人物剪影优先于细节填满画面。
- 人物居中，头顶、两侧、脚底留透明安全边距，避免前端缩放时切掉冠帽、宽袖和靴子。
- 新版游戏内图要求透明背景 PNG；静态发布图也按透明 cutout 处理，前端统一复用同一张立绘。
- 不要生成文字、水印、签名、边框 UI、现代摄影棚元素。

## 风格基准

- 晚明历史策略游戏画像。
- 写实但带轻微工笔/国画质感。
- 色彩以绯红、墨黑、旧金、竹青、绢纸色为主。
- NPC 应有品级/身份差异：阁臣沉稳，言官清峻，边将甲胄，厂卫凌厉，太监内廷感更强；后宫主位可更华贵但不现代化；后金、蒙古、朝鲜、流寇与江湖外缘人物可更异质，但仍保持同一时代质感。

## 批量生成命令

运行：

```bash
python3 scripts/export_portrait_prompts.py
```

会按当前前端缺图判定生成：

- `docs/portrait_batch_prompts.md`
- `content/portrait_generation_manifest.json`

再按需生成图片：

```bash
python3 scripts/gen_portraits.py --kind dna --limit 2       # 先试两张 DNA 四视图
python3 scripts/gen_portraits.py --kind dna --workers 2     # 第一步：先生成全员 DNA
python3 scripts/gen_portraits.py --kind portrait --limit 2  # 第二步：再试两张立绘，引用 DNA + 服装样板
python3 scripts/gen_portraits.py --kind portrait --workers 2 # 第三步：批量跑初始立绘
python3 scripts/gen_portraits.py --kind both --replace      # 发布前重绘并替换旧半身/旧服制图
```

脚本会自动读取 `.env`，立绘输出到 `web/public/portraits/`，DNA 图输出到 `web/public/portraits/_dna/`。
