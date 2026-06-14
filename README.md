<p align="center">
  <img src="https://brightdata.com/favicon.ico" width="48" alt="Bright Data">
</p>

<h1 align="center">海外 KOL 网红数据采集与分析 Pipeline</h1>
<p align="center">
  <strong>Bright Data × Python — 批量采集 Instagram + TikTok 博主数据，自建 KOL 筛选评分引擎</strong>
</p>

<p align="center">
  <a href="#-为什么需要这个项目">为什么</a> ·
  <a href="#-架构设计">架构</a> ·
  <a href="#-快速开始">快速开始</a> ·
  <a href="#-使用教程">教程</a> ·
  <a href="#-kol-评分模型">评分模型</a> ·
  <a href="#-成本分析">成本分析</a>
</p>

---

## 为什么需要这个项目

> 花大钱找的网红带货翻车，事后才发现数据注水——如果有真实数据早就发现了。

**出海品牌找海外 KOL 的三大痛点：**

1. **国内工具不覆盖海外平台** — 飞瓜、卡思、新榜做国内很强，但 Instagram、TikTok 海外版、YouTube 的博主数据要么没有，要么字段少得可怜。
2. **海外工具贵且不灵活** — HypeAuditor $99-$399/月，数据月度更新，细分筛选能力弱，没有中文支持。
3. **定制化组合条件无人满足** — "粉丝在东南亚、互动率>5%、近30天发过美妆内容"——没有任何现成工具能直接给你。

**本项目的解法：** 用 [Bright Data](https://brightdata.com) 的 Web Scraper API 直接采集 Instagram/TikTok 原始数据，Python 清洗 + 自定义加权评分，产出可复用的 KOL 筛选报告。

### 国内工具 vs 自建方案对比

| 工具 | 月费 | 覆盖平台 | 数据时效 | 核心局限 |
|------|------|----------|----------|----------|
| 飞瓜数据 | ¥999-3,999/月 | 抖音/快手/B站/小红书 | 较新 | 不覆盖 Instagram/TikTok 海外版 |
| 新榜 | ¥1,500-5,000/月 | 微信/微博/抖音 | 较新 | 海外博主数据几乎没有 |
| 卡思数据 | ¥2,000+/月 | 抖音/快手/B站 | 较新 | 仅限国内平台 |
| 蝉妈妈 | ¥299-1,999/月 | 抖音/TikTok 部分 | 较新 | TikTok 海外数据不全 |
| HypeAuditor | $99-399/月 | IG/TT/YouTube | 月度更新 | 价格高，细分弱 |
| **自建 (Bright Data)** | **按用量付费** | **海外社媒全平台** | **实时** | 需要初始配置时间 |

---

## 架构设计

```
Bright Data Web Scraper API
    │
    ├── Instagram Collector ──→ instagram_profile_scraper.py
    │                                │
    ├── TikTok Collector ──────→ tiktok_creator_scraper.py
    │                                │
    └────────────────────────────────┘
                    │
                    ▼
            kol_scoring_model.py
           (合并 + 标准化 + 评分)
                    │
                    ▼
            export_to_sheets.py
        (CSV / Excel / Google Sheets)
```

**数据流转：** 原始 JSON → 标准化 Schema → 多维度归一化 → 加权总分 → 排名导出

---

## 快速开始

### 前置条件

- Python 3.9+
- [Bright Data 账号](https://brightdata.com)（免费注册送 $5 试用额度）
- Bright Data API Token + Instagram/TikTok Collector Zone

### 安装

```bash
git clone https://github.com/your-org/kol-scoring-pipeline.git
cd kol-scoring-pipeline
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，填入你的 Bright Data API Token 和 Zone 名称：

```env
BRIGHTDATA_API_TOKEN=your_api_token_here
BRIGHTDATA_ZONE_INSTAGRAM=instagram_profiles
BRIGHTDATA_ZONE_TIKTOK=tiktok_profiles
```

### 三步走

```bash
# Step 1 — 采集 Instagram 博主数据
python scripts/instagram_profile_scraper.py beauty_by_emma techwithjason fitness_with_luna

# Step 2 — 采集 TikTok 创作者数据
python scripts/tiktok_creator_scraper.py --usernames beauty_by_emma techwithjason --hashtag beauty

# Step 3 — 评分 + 导出
python scripts/kol_scoring_model.py -i data/instagram_profiles.json -t data/tiktok_creators.json -o data/kol_scores.csv
python scripts/export_to_sheets.py -i data/kol_scores.csv -f excel
```

---

## 项目结构

```
.
├── scripts/
│   ├── instagram_profile_scraper.py   # Instagram 博主数据采集
│   ├── tiktok_creator_scraper.py      # TikTok 创作者数据采集
│   ├── kol_scoring_model.py           # KOL 评分与排名引擎
│   └── export_to_sheets.py            # 导出 Google Sheets / Excel / CSV
├── data/
│   ├── sample_instagram_profiles.json # 示例 Instagram 数据
│   ├── sample_tiktok_creators.json    # 示例 TikTok 数据
│   ├── kol_scores_sample.csv          # 示例评分输出
│   └── kol_scoring_template.csv       # 评分权重模板
├── .env.example                       # 环境变量模板
├── requirements.txt                   # Python 依赖
└── README.md
```

---

## KOL 评分模型

### 六大评分维度

| 维度 | 默认权重 | 说明 |
|------|----------|------|
| **Engagement Rate** (互动率) | 30% | 点赞+评论+分享 / 粉丝数，衡量内容质量 |
| **Follower Growth** (涨粉速度) | 20% | 基于近期视频播放量趋势估算 |
| **Followers** (粉丝规模) | 15% | 对数归一化，避免头部通吃 |
| **Commerce Potential** (带货潜力) | 10% | 是否有 TikTok Shop + 商品数量 |
| **Content Frequency** (更新频率) | 10% | 每周发帖数，衡量活跃度 |
| **Avg Views** (平均播放量) | 15% | TikTok 视频平均播放量 |

**权重可自定义** — 编辑 `.env` 中的 `KOL_WEIGHT_*` 变量即可调整评分侧重。

### 输出字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `rank` | int | 综合排名 (1=最佳) |
| `kol_score` | float | 加权总分 (0-100) |
| `score_engagement` | float | 互动率得分 |
| `score_growth` | float | 涨粉速度得分 |
| `score_followers` | float | 粉丝规模得分 (对数归一化) |
| `score_commerce` | float | 带货潜力得分 |
| `score_content_freq` | float | 更新频率得分 |
| `score_views` | float | 平均播放量得分 |
| `total_followers` | int | Instagram + TikTok 合计粉丝 |

---

## 成本分析

### Bright Data 按量付费 vs 传统订阅

| 方案 | 月费 | 每千条博主 | 灵活度 | 适合场景 |
|------|------|-----------|--------|----------|
| HypeAuditor | $99-399 | N/A (固定订阅) | 低 | 预算充裕的成熟团队 |
| 飞瓜数据 | ¥999-3,999 | N/A | 低 | 国内抖音/快手投放 |
| **Bright Data 自建** | **$0.001/条起** | **~$1.00/千条** | **高** | **出海团队，按需采集** |

> Bright Data 免费额度：每月 5,000 credits（~$7.50 价值），无需信用卡。足够日常小规模 KOL 筛选使用。

---

## 注册与链接

- 🚀 [Bright Data 注册（含 $5 试用额度）](https://brightdata.com)
- 📦 [Bright Data MCP Server — 用 AI Agent 直接爬取](https://brightdata.com/products/mcp)
- 🛠 [GitHub: brightdata-mcp](https://github.com/brightdata/brightdata-mcp)
- 📚 [API 文档](https://docs.brightdata.com/api-reference/web-scraper)

---

## 截图

### Instagram 数据采集输出
![Instagram Data](outputs/screenshots/instagram_data_output.png)

### KOL 评分排名输出
![KOL Scoring](outputs/screenshots/kol_scoring_output.png)

---

## License

MIT © 2026
