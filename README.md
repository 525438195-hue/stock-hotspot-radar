# A股热点情报筛选系统 MVP

这是一个最小可运行版本，用模拟数据跑通“热点输入 -> 去重 -> 题材分类 -> 来源验证 -> 风险标记 -> 透明评分 -> 报告输出”的完整流程。

系统只输出适合人工复核的情报整理，不生成交易指令，不连接真实网站，也不做自动交易。

## 当前版本说明

- 版本名：v0.3.5 自动联网读取 + 搜索质量过滤 + Streamlit 部署准备版
- 功能：模拟数据输入、手动 CSV 数据输入、Tavily 自动联网读取、搜索质量过滤、热点评分、风险标记、中文报告输出、个股观察池生成、Streamlit 看板
- 数据源状态：自动模式会尽力读取 RSS 新闻、A股板块行情和公开公告源；任何数据源失败都不会让流程崩溃，会记录告警并回退到手动 CSV

## 项目结构

```text
stock-hotspot-radar/
├─ config/
│  ├─ sources.yaml
│  ├─ scoring_rules.yaml
├─ data/
│  ├─ sample_events.json
│  ├─ sample_announcements.json
│  ├─ sample_market.json
│  ├─ manual_news.csv
│  ├─ manual_announcements.csv
│  ├─ manual_market.csv
│  ├─ cache/
├─ src/
│  ├─ fetchers/
│  ├─ main.py
│  ├─ models.py
│  ├─ dedupe.py
│  ├─ classify_topics.py
│  ├─ verify_sources.py
│  ├─ risk_filter.py
│  ├─ score_events.py
│  ├─ generate_report.py
│  ├─ scheduler.py
├─ app.py
├─ outputs/
│  ├─ report_state.json
├─ tests/
│  ├─ test_score_events.py
│  ├─ test_generate_report.py
│  ├─ test_modes_and_manual_input.py
│  ├─ test_risk_filter.py
├─ AGENTS.md
├─ requirements.txt
└─ README.md
```

## 快速运行

进入项目目录：

```bash
cd stock-hotspot-radar
```

安装依赖：

```bash
python -m pip install -r requirements.txt
```

运行模拟数据模式：

```bash
python src/main.py --mode sample
```

运行手动 CSV 数据模式：

```bash
python src/main.py --mode manual
```

运行自动联网模式：

```bash
python src/main.py --mode auto
```

启动本地网页看板：

```bash
streamlit run app.py
```

启动定时器：

```bash
python src/scheduler.py
```

运行测试：

```bash
pytest
```

如果 `pytest` 命令不可用，也可以运行：

```bash
python -m pytest
```

## 输出文件

运行 `python src/main.py --mode sample`、`python src/main.py --mode manual` 或 `python src/main.py --mode auto` 后都会生成：

```text
outputs/daily_report.md
outputs/watchlist.csv
outputs/risk_flags.csv
outputs/report_state.json
outputs/stock_candidates.csv
outputs/search_results_raw.csv
outputs/search_results_deduped.csv
outputs/news_summary.csv
```

三个输出文件都在 `outputs/` 目录下。

### daily_report.md

`daily_report.md` 是面向人工复核的中文日报，当前固定输出四个栏目：

```text
一、高可信热点
二、未证实传闻
三、风险公告
四、明日观察池
```

日报不会输出交易建议，只提供题材、来源、验证状态、风险点、明日观察条件和放弃条件。

### watchlist.csv

`watchlist.csv` 是热点观察池明细，使用中文表头，字段含义如下：

```text
事件编号：热点事件的唯一编号
题材：系统识别出的热点题材
热点标题：原始热点标题
验证状态：已确认、部分确认、未证实传闻、被否定、旧闻、仅有市场反应
来源：信息来源名称
来源类型：政策文件、公司公告、交易所公告、财经媒体、行业媒体、社媒情绪、行情数据、未知来源
发布时间：信息发布时间，格式为 YYYY-MM-DD HH:MM
原始链接：模拟数据中的来源链接
可信度分数：按 `config/scoring_rules.yaml` 计算出的 0-100 分
风险标签：系统识别出的中文风险标签
评分原因：基础分、加分、扣分和最终验证状态的中文解释
重复次数：去重前合并的相似线索数量
对应板块：匹配到的行情板块
板块涨幅：对应板块涨幅
涨停数量：对应板块涨停数量
成交额：对应板块成交额
放量幅度：对应板块成交额变化幅度
```

### risk_flags.csv

`risk_flags.csv` 是风险清单，使用中文表头，集中列出热点和公告触发的风险标记，字段含义如下：

```text
编号：热点事件编号或公告编号
类型：热点或公告
风险类型：公司否认、减持风险、监管风险、高位追涨风险、旧闻新炒等
严重程度：低、中、高
原因：触发风险标签的原因
来源：风险信息来源名称
来源类型：风险信息来源类型
发布时间：风险信息发布时间，格式为 YYYY-MM-DD HH:MM
原始链接：模拟数据中的来源链接
可信度分数：相关热点或公告来源对应的可信度分数
```

### report_state.json

`report_state.json` 是网页看板读取的状态文件，包含：

```text
last_update_time：最后更新时间
raw_count：原始热点数量
deduped_count：去重后热点数量
high_confidence_count：高可信热点数量
risk_announcement_count：风险公告数量
risk_count：风险标签数量
source_success_rate：数据源成功率
source_status：每个数据源的成功/失败状态
warnings：自动联网和 fallback 过程中产生的告警
coverage_report：多源搜索覆盖统计，包括搜索词数量、成功/失败/跳过数据源、独立域名数量和重要告警
```

### stock_candidates.csv

`stock_candidates.csv` 是网页首页使用的个股观察池，字段含义如下：

```text
候选类型：个股、题材或占位
股票名称：从行情数据、公告、搜索结果或文本中识别出的股票名称
股票代码：如能识别则填入，不能识别时留空
所属题材：候选对应的热点题材
题材阶段：启动、扩散、分歧、退潮等观察阶段
题材强度分：按板块涨幅、涨停数量、成交额变化等规则计算
可信度分数：沿用透明评分模型的 0-100 分
市场信号：板块涨幅、涨停数量、成交额等市场反应摘要
信息来源：合并后的新闻、公告、行情或手动来源
验证状态：已确认、部分确认、未证实传闻、被否定、旧闻、仅有市场反应
风险标签：公司否认、减持风险、监管风险、高位追涨风险等
观察建议：优先跟踪、只看核心、等待回踩、暂不参与、直接排除
观察条件：后续人工复核需要观察的条件
放弃条件：题材或候选需要剔除的条件
备注：补充说明
```

### search_results_raw.csv / search_results_deduped.csv

`search_results_raw.csv` 保存 Tavily、RSS 等联网搜索的原始候选结果；`search_results_deduped.csv` 保存去重和质量过滤后的搜索结果。主要字段包括：

```text
标题：搜索结果标题
摘要：搜索结果摘要
来源：搜索结果来源名称
来源类型：搜索 API、财经媒体等
原始链接：搜索结果 URL
域名：从 URL 解析出的独立域名
查询词：触发该结果的搜索词
抓取时间：系统抓取时间
是否来自Tavily：是否由 Tavily 返回
是否来自RSS：是否由 RSS 返回
是否保留：是否通过 A股相关性过滤
过滤原因：保留、低相关候选或剔除原因
是否简体化：是否经过繁简转换
A股相关性分数：按白名单域名、交易相关词、股票代码等规则计算
```

### news_summary.csv

`news_summary.csv` 是给网页和后续大模型辅助分析预留的轻量新闻摘要文件，来自 `search_results_deduped.csv`，便于快速查看本次联网检索的重点候选信息。

## 评分规则

评分规则在 `config/scoring_rules.yaml` 中透明配置，使用 100 分制：

- 基础分按来源类型计算。
- 多源验证、官方确认、成交额确认、板块强度确认、政策连续性按规则加分。
- 社媒单一来源、旧闻新炒、标题正文不符、公司否认、减持公告、监管警示、高位追逐风险按规则扣分。
- 最终分数限制在 `0-100`。

输出文件中的验证状态显示为中文，对应关系如下：

```text
已确认
部分确认
未证实传闻
被否定
旧闻
仅有市场反应
```

## 配置说明

当前 `.yaml` 配置文件采用 JSON 兼容写法。程序会优先尝试使用 `PyYAML` 解析；如果环境中没有 `PyYAML`，会自动回退到 Python 标准库 `json` 解析。

`data/cache/` 是 v0.3 预留的本地缓存目录，用于后续保存联网抓取的中间结果。

## v0.2 手动 CSV 输入

v0.2 不爬网站，也不自动接入外部数据源。你可以把真实财经新闻、公司公告和行情数据手动填入以下文件，然后运行：

```bash
python src/main.py --mode manual
```

手动新闻文件：`data/manual_news.csv`

```text
标题
正文
来源
来源类型
发布时间
原始链接
相关关键词
```

手动公告文件：`data/manual_announcements.csv`

```text
公司名称
股票代码
公告标题
公告正文
公告类型
发布时间
原始链接
```

手动行情文件：`data/manual_market.csv`

```text
板块名称
板块涨幅
涨停数量
成交额
放量幅度
领涨股票
领涨股票涨幅
```

如果三个手动 CSV 文件只有表头或没有任何有效数据，程序不会报错，会生成空日报，并在 `daily_report.md` 中提示“暂无手动导入数据”。

手动 CSV 建议使用 Excel/WPS 另存为 UTF-8 CSV。项目写出的 `watchlist.csv` 和 `risk_flags.csv` 使用 `utf-8-sig` 编码，方便 Excel/WPS 打开中文。

## v0.3 自动联网读取

手动生成自动报告：

```bash
python src/main.py --mode auto
```

auto 模式会：

```text
多源搜索候选 + RSS 新闻 -> 去重 -> 分类 -> 验证 -> 风险标记 -> 评分 -> 中文报告
公告源失败 -> 回退 manual_announcements.csv
行情源失败 -> 回退 manual_market.csv
新闻源失败或为空 -> 回退 manual_news.csv
```

自动模式不会模拟登录，不会绕过网站风控，不会执行任何交易。失败信息会写入 `outputs/report_state.json` 的 `warnings` 和 `source_status`。

### 多源搜索覆盖

v0.3 会根据 `config/sources.yaml` 中的 `search.base_keywords` 自动生成搜索矩阵，例如“AI算力 政策”“低空经济 公告”“机器人 监管函”等，并扩展同义词线索。系统优先尝试 Tavily Search API、Google Programmable Search JSON API、NewsAPI 和 RSS 新闻源；`config/sources.yaml` 配置环境变量名，实际 API key 写入 `.env`。未配置 API key 的搜索源会被记录为“跳过”，不会导致程序报错。

搜索结果只作为候选信息，不能直接当事实依据。`daily_report.md` 会新增“数据覆盖范围”一节，显示今日搜索关键词数量、成功数据源、失败数据源、跳过数据源、独立域名数量、官方源核验情况和重要警告。

请注意：本报告不代表全网穷尽搜索，仅代表已配置数据源范围内的自动检索和交叉验证结果。

### API Key 配置

首次使用搜索 API 前，复制示例文件：

```bash
copy .env.example .env
```

然后在 `.env` 里填写自己的 API Key：

```text
TAVILY_API_KEY=
DEEPSEEK_API_KEY=
QWEN_API_KEY=
OPENAI_API_KEY=
GOOGLE_API_KEY=
GOOGLE_CSE_ID=
NEWSAPI_KEY=
```

本地运行时项目使用 `python-dotenv` 读取 `.env`；部署到 Streamlit Community Cloud 时，可以在 Secrets 中填写同名 Key。读取优先级为：环境变量 > Streamlit Secrets > `.env`。

如果没有 `.env`，或者某个 API Key 留空，程序仍然可以运行，并会在数据源状态中显示“跳过：未配置 API Key”。如果没有 `TAVILY_API_KEY`，网页会提示“当前未配置 Tavily API Key，无法联网搜索。”

### 信息源配置系统

v0.3 的信息源由 `config/sources.yaml` 统一管理。以后新增或停用来源时，优先改配置文件和 `.env`，不需要改主流程代码。

```text
search_sources：Tavily、Google CSE、NewsAPI 等搜索 API，API key 从 .env 的环境变量名读取
rss_sources：财经媒体 RSS 源，按 enabled、url、priority 控制是否启用
official_sources：巨潮资讯、上交所、深交所等官方公告源，目前为 placeholder，不模拟登录、不绕风控
market_sources：行情源，目前支持 akshare，失败后回退 manual_market.csv
social_sources：社媒情绪源，目前只支持 data/manual_rumors.csv 手动传闻输入
```

`priority` 数字越小，可信优先级越高：官方公告源为 1，财经媒体和行情源为 2，搜索 API 为 3，社媒情绪为 5。社媒情绪只能作为情绪信号，不能作为事实依据，也不会进入高可信热点。

## 本地网页看板

启动看板：

```bash
streamlit run app.py
```

看板标题为“A股热点个股雷达”，首页第一屏直接展示“今日重点观察股”，展示：

```text
更新时间
今日观察股数量
高可信热点数量
风险股票数量
数据源成功率
今日重点观察股
题材热度排行
题材观察
风险股票 / 风险公告
暂不参与 / 直接排除
未证实传闻与社媒小道消息
原始日报
```

看板支持按题材、可信度分数、验证状态、风险标签筛选，也支持只看“优先跟踪”和隐藏“暂不参与 / 直接排除”。页面每 5 分钟自动刷新显示结果，但不会因为页面刷新而重复请求 Tavily。

打开网页时，程序会先检查 `outputs/report_state.json`：

```text
如果状态文件不存在，自动执行一次 auto 模式
如果 last_update_time 不是今天，自动执行一次 auto 模式
如果 last_update_time 是今天，直接读取缓存
点击“立即刷新今日数据”时，才强制重新联网刷新
```

看板按钮：

```text
立即刷新今日数据：执行 python src/main.py --mode auto，并重新生成观察池
重新生成观察池：基于当前输出文件重新生成 outputs/stock_candidates.csv
导出观察池：下载 outputs/stock_candidates.csv
查看原始日报：展开 outputs/daily_report.md
```

## Streamlit Community Cloud 部署

部署步骤：

1. 上传项目到 GitHub。
2. 登录 Streamlit Community Cloud。
3. 选择 GitHub 仓库。
4. Main file path 填 `app.py`。
5. 在 Secrets 中填写：

```toml
TAVILY_API_KEY = "xxx"
```

6. Deploy。
7. 打开生成的网址即可使用。

云端运行时仍然会生成：

```text
outputs/daily_report.md
outputs/stock_candidates.csv
outputs/search_results_raw.csv
outputs/search_results_deduped.csv
outputs/report_state.json
outputs/news_summary.csv
```

注意事项：

- 不要把真实 Tavily API Key 写进代码。
- 不要把 `.env` 上传到 GitHub。
- 本地使用 `.env`，云端使用 Streamlit Secrets。
- 如果没有配置 `TAVILY_API_KEY`，网页不会崩溃，会提示“当前未配置 Tavily API Key，无法联网搜索。”

## 定时任务

启动定时器：

```bash
python src/scheduler.py
```

默认定时：

```text
08:40 盘前报告
15:30 收盘报告
21:30 公告风险扫描
```

每次定时执行：

```bash
python src/main.py --mode auto
```

## 当前限制

- 支持模拟数据、手动 CSV 数据和自动联网读取。
- 不模拟登录，不绕过网站风控。
- 不输出买入、卖出、满仓、梭哈、必涨等交易化建议。
- 不做自动交易。
