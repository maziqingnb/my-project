# 足球水位分析系统

基于 **临场水位变动 + 历史战绩** 的足球分析工具，自动同步赛程 / 阵容 / 赔率，
对 **五大联赛（英超、西甲、意甲、德甲、法甲）+ 欧冠** 的 **亚盘（让球）** 和 **大小球**
给出方向推荐，并**将杯赛数据与联赛数据分开计算**。

> ⚠️ 本工具仅用于数据分析与学习，不构成任何投注建议。足球存在偶然性，**没有任何模型能保证盈利**，请理性对待、量力而行。

## 功能

| 功能 | 说明 |
|------|------|
| 赛程同步 | 自动拉取未来 7 天五大联赛 + 欧冠赛程 |
| 阵容抓取 | 临近开赛时抓取首发阵容与阵型 |
| 赔率监测 | 定时抓取亚盘 / 大小球赔率，积累**水位历史**（初盘 → 临场） |
| 皇冠盘抓取 | 内置 `CrownScraper` 适配器，可切换皇冠盘数据源，支持中文队名映射 |
| 微信推送 | 分析完成后自动推到**企业微信群机器人 / Server酱** |
| 推荐引擎 | 结合临场水位变动 + 历史战绩，输出亚盘 / 大小球方向与信心等级 |
| 同场去重 | 同一场比赛若同时有亚盘与大小球候选，只保留**推荐系数（概率）最高**的一条 |
| 赛果结算 | 自动匹配最终比分，判定「中 / 没中 / 走盘 / 半赢 / 半输」 |
| 命中率统计 | 看板展示整体 / 分盘口 / 分联赛的**累计命中率** |
| 杯赛 / 联赛分离 | 欧冠按杯赛口径、五大联赛按联赛口径**独立统计**近况与交手 |
| 看板 | GitHub Pages 展示推荐结果、推荐系数、赛果中没中与命中率 |

## 架构

```
the-odds-api（赔率）  API-Football（赛程/结果/阵容）
        │                       │
        └──────┬────────────────┘
               ▼
      GitHub Actions 定时任务（每 6 小时）
               │  同步 -> 存 JSON -> 分析 -> 渲染
               ▼
        data/（水位历史、结果）  docs/reports/（推荐结果）
               │
               ▼
        GitHub Pages 看板（docs/index.html）
```

## 目录结构

```
├── config/config.yaml        # 联赛配置、盘口权重、阈值、数据源选择
├── src/
│   ├── adapters/             # 数据源适配器（the-odds-api / crown / API-Football / mock）
│   ├── sync/                 # 赛程、赔率、阵容同步
│   ├── analysis/             # 特征 + 推荐引擎
│   ├── output/               # 渲染 JSON / Markdown 报告
│   ├── notify/               # 微信推送（企业微信机器人 / Server酱）
│   ├── storage.py            # JSON 文件存储
│   ├── models.py             # 统一数据模型
│   └── main.py               # 命令行入口
├── samples/crown_sample.html # 皇冠盘样例页（离线验证抓取解析用）
├── docs/                     # GitHub Pages 看板
├── data/                     # 数据（由 Actions 提交回仓库）
└── .github/workflows/        # 定时任务 + CI
```

## 快速开始（本地）

需要 Python 3.9+。

```bash
pip install -r requirements.txt

# 1) 演示模式：无需任何 key，用内置样例数据跑通全流程
python src/main.py analyze --demo
# 输出 data/reports/report.md 和 docs/reports/latest.json

# 2) 真实数据（免 key，默认球探网 win007）
python src/main.py sync --all
python src/main.py analyze
```

> 默认数据源是球探网（`sources.odds: win007`、`sources.fixtures: win007`），**无需任何 API key**。
> 也可切回 the-odds-api / API-Football（需 key，见下）。

## 获取 API Key（都是注册即用、有免费档）

1. **the-odds-api**（赔率/水位）https://the-odds-api.com
   - 免费档 500 次/月，提供 `spreads`（亚盘）和 `totals`（大小球）盘口。
2. **API-Football**（赛程/结果/阵容）https://www.api-sports.io
   - 免费档 100 次/天，提供赛程、结果、首发。

设置环境变量（本地）：

```bash
export THE_ODDS_API_KEY="你的key"
export API_FOOTBALL_KEY="你的key"
```

## 部署到 GitHub

1. 新建仓库，把本目录推上去（或直接 `gh repo create`）。
2. 在仓库 **Settings → Secrets and variables → Actions** 添加：
   - `THE_ODDS_API_KEY`
   - `API_FOOTBALL_KEY`
3. 推送后，`.github/workflows/analyze.yml` 会每 6 小时自动同步+分析并提交数据；
   没配 key 时会自动回退到演示数据，保证流程可用。
4. 在 **Settings → Pages** 里把 Source 设为 `main` 分支的 `/docs` 目录，即可访问看板。

## 推荐逻辑（透明、可调）

**信号**（每场比较初盘 vs 临场快照）：

- 亚盘：盘口升/降（让球数变化）+ 主队水位降水/升水
- 大小球：盘口升/降 + 大球水位降水/升水

**历史**（近 5 场，**联赛/杯赛分开口径**）：

- 近期状态（胜率、得失球）、历史交手、主客场

**打分**：各信号按权重加权（见 `config/config.yaml` 的 `analysis.weights`），
合成 0–100 分，分数越远离 50 方向越明确：

| 分数 | 信心 |
|------|------|
| ≥ 70 | 强推 |
| ≥ 55 | 可关注 |
| < 55 | 观望（不输出） |

**同场二选一**：同一场比赛若亚盘、大小球都出了推荐，只保留分数（推荐系数）更高的那个，
保证每场比赛最多一条推荐。

**赛果结算与命中率**：每次分析会把推荐与最终比分比对（亚盘用让球线、大小球用总进球线，
1/4 盘口按亚洲盘拆两条腿结算），判定「中 / 没中 / 走盘 / 半赢 / 半输」。
看板的命中率口径：**命中 = 中 + 半赢，已结算 = 中 + 半赢 + 半输 + 没中（走盘不计入）**。
历史推荐沉淀在 `data/reports/history.json`，赛果陆续到位后自动补结算、累计命中率。

## 皇冠 / 球探网抓取（已内置适配器）

皇冠盘没有公开稳定的官方零售站，通常经比分网、皇冠代理页或数据商 API 获取。内置两个抓取适配器，都实现 `OddsClient` 接口：

| 数据源 | 适配器 | 说明 | 启用 |
|--------|--------|------|------|
| 皇冠代理页 / 比分网 HTML | `crown_scraper.py` | 普通 HTML 表格 | `sources.odds: crown` |
| 球探网移动版 m.ewin007.com | `win007.py` | **已实测可用**，抓 `Schedule.htm`（服务端渲染） | `sources.odds: win007` |

**球探网（已实测）**：`https://m.ewin007.com/Schedule.htm?date=YYYY-MM-DD` 内嵌两个 JS 变量，
`scheduleDataStr` 每场含 `主水 / 让球盘口 / 客水 / 大小球盘口`。win007 联赛 ID：

| 联赛 | win007_id |
|------|-----------|
| 英超 | 36 |
| 西甲 | 31 |
| 德甲 | 8 |
| 意甲 | 34 |
| 法甲 | 11 |
| 欧冠 | 待欧冠比赛日确认（球探网当天无欧冠场次） |

**启用步骤：**

1. `config/config.yaml` 设 `sources.odds: win007`（地址已默认填好）。
2. 用 `team_name_map` 把球探网**中文队名**映射到 API-Football 英文队名，否则近况/交手按队名匹配不上。
   （当前只放了 6 个示例，实际要按你关注的联赛补齐 ~100 支球队。）
3. 若让球方向反了，改 `scrapers.win007.handicap_sign`（默认 `-1`，含义见注释）。

**已实现（免 key 全链路）**：

- 赔率：`Schedule.htm` 拿亚盘即时水位 + `Analy/Analysis/{id}.htm` 拿**大小球水位和初盘**，
  每场产出「初盘 vs 临场」两个快照，亚盘 + 大小球的水位变动都能算。
- 赛程/结果：`Schedule.htm?date=` 按过去日期拉完场比分（用于近况/交手）。

**已知限制（如实说明）：**

- 阵容需分析页另一部分数据，未接入（`fetch_lineup` 返回 None）；
- 队名中英混杂，需 `team_name_map` 映射；未映射时水位/盘口信号不受影响，仅近况/交手匹配不全；
- 欧冠的 win007 联赛 ID 待确认（欧冠比赛日抓一次即可，见下方排查命令）。

**离线试跑**：

```bash
WIN007_URL=samples/win007_sample.html python src/main.py sync --odds    # 球探网离线样例
```

**找欧冠 ID**（欧冠比赛日执行，把输出的 ID 填到 `config.yaml` 的 `ucl.win007_id`）：

```bash
python -c "
import requests, re
from datetime import datetime
html = requests.get(f'https://m.ewin007.com/Schedule.htm?date={datetime.now().strftime(\"%Y-%m-%d\")}', headers={'User-Agent':'Mozilla/5.0'}).text
sc = re.search(r'var sclassDataStr = \"(.*?)\";', html, re.S)
for seg in sc.group(1).split('!'):
    p = seg.split('^')
    if p and any(k in p[0] for k in ['欧冠','冠军','欧洲']):
        print(p[0], 'id=', p[1])
"

> ⚠️ 抓取类数据源需自行处理反爬、频率限制与站点改版维护，且可能违反目标站点服务条款；
> 更稳定合规的做法是接付费皇冠数据 API（接口同样套 `OddsClient`，分析层零改动）。

## 微信推送

> 微信**不开放"凭微信号给个人发消息"的接口**，所以不能直接按微信号推送。
> 要通过下面任一渠道拿到一个 key/地址，才能推到你自己的微信。

| 渠道 | 环境变量 | 获取方式 |
|------|----------|----------|
| **Server酱**（推荐，个人微信最快） | `SERVERCHAN_KEY` | 用微信扫码登录 https://sct.ftqq.com → 取 SendKey |
| 企业微信群机器人 | `WECOM_WEBHOOK` | 建群 → 添加群机器人 → 复制 webhook 地址 |

**Server酱 三步收到微信：**

1. 微信扫码登录 https://sct.ftqq.com，绑定你的微信；
2. 复制页面上的 `SendKey`（形如 `SCTxxxxxxxxxxxxx`）；
3. 把 key 设进环境变量 / GitHub Secrets：

```bash
export SERVERCHAN_KEY="SCTxxxxxxxxxxxxx"
python src/main.py analyze --demo
python src/main.py notify      # 报告就发到你微信了
```

GitHub Actions 已自动在分析后执行 `notify`，只需在仓库 Secrets 添加
`SERVERCHAN_KEY`（或 `WECOM_WEBHOOK`，二选一即可）。未配置时自动跳过，不影响流程。

看板还内置了**水位走势 sparkline**：每条推荐右侧会画出初盘→临场的水位变化线（亚盘画主/客队水位、大小球画大/小球水位）。

## 免责声明

本项目仅供学习与技术研究，不保证数据的实时性与准确性，不构成任何投注或投资建议。
博彩有风险，请遵守当地法律法规，理性参与。
