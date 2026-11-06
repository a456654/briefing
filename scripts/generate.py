#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日简报生成器（深版 v5）
抓取 RSS → 调用 DeepSeek 生成简报 → 输出静态网页 → 可选微信推送（Server酱）

环境变量：
  DEEPSEEK_API_KEY   必填
  SERVERCHAN_KEY     选填，不填则不推送
"""

from __future__ import annotations

import datetime
import os
import pathlib
import re
import smtplib
import sys
import xml.etree.ElementTree as ET
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import markdown as mdlib
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ARCHIVE = DOCS / "archive"

TZ = datetime.timezone(datetime.timedelta(hours=8))
NOW = datetime.datetime.now(TZ)
DATE = NOW.strftime("%Y-%m-%d")
STAMP = NOW.strftime("%Y-%m-%d %H:%M")
WEEKDAY = ["一", "二", "三", "四", "五", "六", "日"][NOW.weekday()]
IS_SUNDAY = WEEKDAY == "日"

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "").strip()

# 新闻源。某一源挂了不影响整体，会在日志里标 [跳过]。
FEEDS = [
    ("BBC 中文", "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"),
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("纽约时报中文网", "https://cn.nytimes.com/rss/"),
    ("半岛电视台", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("卫报 World", "https://www.theguardian.com/world/rss"),
    ("NPR World", "https://feeds.npr.org/1004/rss.xml"),
    ("Google News 中文", "https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("Google News World",
     "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en"),
]

MAX_PER_FEED = 10
MAX_TOTAL = 80
TIMEOUT = 25
UA = {"User-Agent": "Mozilla/5.0 (compatible; daily-briefing/2.0)"}

SYSTEM_PROMPT = """你是一名严谨的新闻简报编辑，服务对象是一名中国大学生。
你的最高准则不是"客观"（那不可能），而是"可核查"。

【输出结构】严格按以下顺序用 Markdown 输出，不要加任何前言后语：

# 每日简报 · {date}

## 一句话

## 今日主线
### 发生了什么
### 为什么重要
### 接下来看什么

## 国内

## 国际

## 数据与日程

## 两个判断
### 判断一 · 往回看（因果）
### 判断二 · 往前看（预测）

## 纠偏

## 本期未覆盖

【条数与篇幅】每天固定 15 条，不多不少。
- 主线 1 条，350–450 字
- 国内 5 条，每条 80–150 字（事实 55–120 字 + → 一句）
- 国际 6 条，每条 80–150 字（事实 55–120 字 + → 一句）
- 数据与日程 3 条，每条一句数据 + 一句 →
- 两个判断各 150–200 字
- 正文合计 2300–2700 字

【排版铁律 · 最高优先级】
读者在手机微信里读：屏幕窄、注意力短。三条硬要求——能数清、能分开、能分清谁是事实谁是判断。

一、编号
- 国内、国际、数据与日程三栏的每一条，必须以「**① 标题**」开头。
- 编号字符固定用 ①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮，**全篇连续编号，跨栏目不重置**（国内 ①–⑤，国际接着 ⑥–⑪），这样读者能数清今天一共几条。
- 标题 10–18 字，概括这条是什么。

二、分隔
- **每两条之间必须有一条 `---`。** 不要靠空行凑，要靠线分开。
- 栏目之间用 `##` 标题 + `---`。

三、三层视觉，不许混
1. **事实**：普通正文段落，纯黑，不加引用块、不加引号、不加灰字。一条事实最多 3 行。
2. **后果**：单独一行，写成 `> ▍→ 一句话后果`。用引用块，表示"这是我的机械推断，可被证伪"。
3. **判断**：整段放进引用块，并以 `> ▍推断：` 开头。判断绝不能混进事实段落里。

四、其他
- 数字、日期、关键主体一律加粗。
- 一句话栏目：第一行加粗给结论，第二行补一句背景。
- 主线固定三节：`### 发生了什么`（只写事实）、`### 为什么重要`（推断，用 `> ▍推断：`）、`### 接下来看什么`（列表）。

正确示范：

## 国内

**① 结婚登记数跌至 18 年最低**

2026 年三季度数据。这是出生人口的先行指标，比出生人口拐点早 1–2 年。来源：〔[路透](URL)〕

> ▍→ 托育与地产需求会先感到冷。

---

**② 澳门首宗国安案落判，12 年半**

前立法会议员获刑，为澳门首次适用国安法。

> ▍→ 量刑口径会被后续案件当作标尺。

错误示范（禁止）：多条挤成一段；没有编号；判断和事实混在同一段；正文用灰色。

【硬性规则】
1. 只能使用我提供的条目作为事实来源。严禁引入你记忆中的新闻，严禁编造事件、数字、人名、机构名。
2. 每条事实后面必须带来源链接，用 Markdown 链接格式，写成〔[来源名](URL)〕；链接只能从我在条目里给出的 URL 中选。
3. 每条开头标注可信度，四选一，紧跟在列表符号后：
   【已证实】两家以上独立信源，或有一级文件／统计数据
   【官方口径】只有当事方声明，无独立核实
   【单一来源】仅一家媒体报道
   【待核实】目前只有转述或传闻
   默认用【单一来源】；只有当同一事件在多个不同来源中出现时，才可用【已证实】。
4. 凡属你的推断、解释、因果分析，一律以"（判断）"开头。读者必须能只看事实、跳过判断。

【「→」句：不是评论，是准入门槛】
国内、国际、数据与日程的每一条后面，另起一行写一句「→ …」。
- 只准一句，不超过 25 字。
- 只回答一个问题：这会改变谁的选择？谁多了筹码、谁多付了成本、哪一步被卡住。
- 禁止句式：很重要、值得关注、影响深远、或将持续、意义重大、释放信号、显示……加剧、引发……担忧。
- 必须有具体传导路径或具体观察指标，让人能反驳。
- 写不出 → 的条目，说明它只是热闹，直接删掉，不要放进 15 条里凑数。
- 示例：「→ 油价风险溢价回来了，中国是最大原油进口国，首当其冲。」

【两个判断的写法】
- 两条判断的**整段正文都必须放进引用块**，每段以 `> ▍推断：` 开头。
  这样读者一眼就知道"这一段全是我的判断，不是新闻"。
- 段内第一句加粗，作为该判断的结论句，后面再展开。
- 判断一·往回看：解释今天主线背后的机制。为什么是现在？触发条件是什么？各方真正在算什么账？
- 判断二·往前看：必须可证伪，严格写成这种句式——
  "如果 X 成立，那么在【具体日期】之前，应该看到【具体现象】。"
  不接受"值得关注""或将持续""影响深远"这类无法检验的废话。
- 两条判断必须分工，不能都写同一件事。
- 写不出可证伪的预测，就承认这条没想清楚，宁可不写。

判断栏正确示范：

### 判断一 · 往回看（因果）

> ▍推断：**AI 的约束不在算法，在许可。** 一座数据中心要过三关——电网接入、土地环评、地方补贴，全在州和县一级。

### 判断二 · 往前看（预测）

> ▍推断：**如果监管是真的，2027 年 3 月 31 日前应看到某个州出台数据中心限电新规。** 若到期只有听证会而无新规，说明这轮抛售是情绪而非定价。

【防膨胀】
深度只加在两个判断里。→ 每句不超过 25 字，不许展开成段，不许夹叙夹议。
深度版最大的失败模式是膨胀成三千字社论，必须避免。

【其他规则】
5. 不凑数。条目确实不够，就在对应栏目写"今日无可报之事"，并说明为什么不够。
6. 无法确认的直接写"未核实"，并说明卡在哪一步（未读原文／信源匿名／口径不明）。
7. 禁用煽情词：震惊、重磅、炸裂、刷屏、史诗级、彻底。用平实叙述。
8. 国内部分只写硬信息：政策文件名 + 发布机构 + 生效时间、数据 + 统计口径、人事、司法。
   政策、数据、人事、司法各最多一条；第五个位置留给当月最实用或最该盯的一条。
9. "本期未覆盖"栏：列出你判断重要、但条目信息不足因而不敢写的题材。

【今天是星期{weekday}】
{sunday_branch}"""

SUNDAY_BRANCH = """今天是周日。在"## 本期未覆盖"之后，追加两栏：

## 本周对账

- 上周两个判断：分别中了没有？中在哪、错在哪一步？
- 如果无法核对上周内容，就写"本周为首次运行，无往期可对账"，不要编造。

## 本周一件事

1500–2500 字。从本周条目里挑一件最值得想清楚的事，讲清它的机制、争议和你的判断。
这一栏可以长，但仍须遵守"（判断）"标注规则。"""

NORMAL_BRANCH = "今天不是周日，不要输出「本周对账」和「本周一件事」两栏。"


# ---------------------------------------------------------------- 抓取


def log(msg: str) -> None:
    print(msg, flush=True)


def strip_tags(s: str | None) -> str:
    return re.sub(r"<[^>]+>", " ", s or "").strip()


def normalize(title: str) -> str:
    return re.sub(r"\W+", "", title).lower()[:24]


def fetch_feed(name: str, url: str) -> list[dict]:
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers=UA)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:  # 单源失败不影响整体
        log(f"[跳过] {name}: {exc}")
        return []

    out = []
    for item in root.iter("item"):
        title = strip_tags(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        desc = strip_tags(item.findtext("description"))[:260]
        if title and link.startswith("http"):
            out.append({"source": name, "title": title, "link": link, "desc": desc})
    log(f"[OK]   {name}: {len(out)} 条")
    return out[:MAX_PER_FEED]


def collect() -> list[dict]:
    seen: set[str] = set()
    items: list[dict] = []
    for name, url in FEEDS:
        for it in fetch_feed(name, url):
            key = normalize(it["title"])
            if key and key not in seen:
                seen.add(key)
                items.append(it)
    return items[:MAX_TOTAL]


# ---------------------------------------------------------------- 生成


def build_user_prompt(items: list[dict]) -> str:
    lines = []
    for i, it in enumerate(items, 1):
        lines.append(
            f"[{i}] 来源：{it['source']}\n"
            f"    标题：{it['title']}\n"
            f"    摘要：{it['desc'] or '（无）'}\n"
            f"    链接：{it['link']}"
        )
    body = "\n".join(lines)
    return (
        f"今天是 {DATE}（北京时间，星期{WEEKDAY}）。\n"
        f"以下是过去 24 小时抓到的原始条目，共 {len(items)} 条，每条带编号、来源和链接：\n\n"
        f"{body}\n\n"
        f"请严格按规范生成今天的简报。"
    )


def call_deepseek(items: list[dict]) -> str:
    if not DEEPSEEK_KEY:
        sys.exit("错误：环境变量 DEEPSEEK_API_KEY 未设置")

    system = SYSTEM_PROMPT.format(
        date=DATE,
        weekday=WEEKDAY,
        sunday_branch=SUNDAY_BRANCH if IS_SUNDAY else NORMAL_BRANCH,
    )

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": build_user_prompt(items)},
        ],
        "temperature": 0.3,
        "max_tokens": 6000,
        "stream": False,
    }

    last_err = None
    for attempt in range(1, 4):
        try:
            resp = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=300,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
            text = resp.json()["choices"][0]["message"]["content"].strip()
            break
        except Exception as exc:
            last_err = exc
            log(f"[重试 {attempt}/3] {exc}")
    else:
        sys.exit(f"DeepSeek 调用失败：{last_err}")

    text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


# ---------------------------------------------------------------- 渲染

PAGE_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{title}</title>
<style>
  :root {{
    --fg:#000000; --fg-soft:#141414; --bg:#ffffff; --muted:#5c5c60;
    --line:#dcdcdf; --line-soft:#ededf0; --accent:#b3352b; --pill:rgba(120,120,128,.10);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --fg:#e9e9eb; --fg-soft:#c6c6ca; --bg:#111112; --muted:#8e8e93;
      --line:#26262a; --line-soft:#1d1d20; --accent:#e37a6e; --pill:rgba(150,150,160,.14);
    }}
  }}
  * {{ box-sizing: border-box; }}
  html {{ -webkit-text-size-adjust: 100%; }}
  body {{
    margin: 0 auto; padding: 26px 20px 76px; max-width: 720px;
    background: var(--bg); color: var(--fg);
    font: 16.5px/1.8 -apple-system, BlinkMacSystemFont, "PingFang SC",
          "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  }}
  @media (max-width: 480px) {{
    body {{ font-size: 16px; padding: 22px 16px 64px; }}
  }}
  h1 {{
    font-size: 1.5em; font-weight: 700; line-height: 1.32;
    letter-spacing: -.015em; margin: 0 0 .12em;
  }}
  h2 {{
    position: relative;
    font-size: .95em; font-weight: 650; letter-spacing: .1em; color: var(--fg-soft);
    margin: 2.8em 0 1.05em; padding: 0 0 .5em .8em;
    border-bottom: 1px solid var(--line);
  }}
  h2::before {{
    content: ""; position: absolute; left: 0; top: .16em; bottom: .62em;
    width: 3px; border-radius: 2px; background: var(--accent); opacity: .55;
  }}
  h3 {{ font-size: 1.02em; font-weight: 650; margin: 1.7em 0 .55em; }}
  p {{ margin: 1.05em 0; }}
  li {{ margin: .55em 0; }}
  strong {{ font-weight: 650; }}
  a {{
    color: var(--accent); text-decoration: none;
    border-bottom: 1px solid rgba(128,128,128,.34);
  }}
  a:hover {{ border-bottom-color: var(--accent); }}
  blockquote {{
    margin: 1.2em 0; padding: .7em 1em; color: var(--fg-soft);
    border-left: 3px solid var(--line); font-size: .95em;
  }}
  /* 「> ▍→ 后果句」：黑色正文 + 红色竖线，从事实里跳出来 */
  blockquote.soq {{
    margin: .85em 0 .2em; padding: .3em 0 .3em .95em;
    border-left: 3px solid var(--accent);
    color: var(--fg); font-size: .96em; line-height: 1.75; font-weight: 500;
  }}
  blockquote.soq p {{ margin: .15em 0; }}
  /* 「> ▍推断」：整段推断，用不同底色再区分一层 */
  blockquote.soi {{
    margin: .9em 0; padding: .85em 1em;
    border-left: 3px solid #6b6b70; background: rgba(120,120,128,.07);
    color: var(--fg); font-size: .96em; line-height: 1.78;
  }}
  blockquote.soi p {{ margin: .3em 0; }}
  hr {{ border: 0; border-top: 1px solid var(--line); margin: 2.5em 0; }}
  code {{
    background: var(--pill); padding: .12em .4em;
    border-radius: 4px; font-size: .88em;
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: .9em; margin: 1.2em 0; }}
  th, td {{ border: 1px solid var(--line); padding: .5em .65em; text-align: left; vertical-align: top; }}

  /* 顶部时间戳 */
  .stamp {{
    color: var(--muted); font-size: .78em; letter-spacing: .03em;
    margin: 0 0 1.5em; display: flex; align-items: center; gap: .5em;
  }}
  .stamp::before {{
    content: ""; width: 6px; height: 6px; border-radius: 50%;
    background: var(--accent); flex: none;
  }}

  /* 可信度标签 */
  .tag {{
    display: inline-block; font-size: .75em; font-weight: 500; line-height: 1.6;
    padding: .06em .5em; border-radius: 4px; margin-right: .45em;
    vertical-align: .1em; white-space: nowrap;
    background: var(--pill); color: var(--muted);
  }}
  .tag.ev  {{ color: #2f7d4f; background: rgba(47,125,79,.13); }}
  .tag.off {{ color: #3a6ea5; background: rgba(58,110,165,.13); }}
  .tag.chk {{ color: #96660f; background: rgba(150,102,15,.15); }}
  @media (prefers-color-scheme: dark) {{
    .tag.ev  {{ color: #79d3a2; background: rgba(121,211,162,.15); }}
    .tag.off {{ color: #8dbdee; background: rgba(141,189,238,.15); }}
    .tag.chk {{ color: #e2b562; background: rgba(226,181,98,.16); }}
  }}

  /* 「→」后果句 */
  .so {{
    display: block; margin: .55em 0 .15em; padding: .1em 0 .1em .8em;
    border-left: 2px solid var(--accent);
    color: var(--fg-soft); font-size: .95em; line-height: 1.7;
  }}

  /* 新闻列表：去符号，用细分隔线 */
  ul.news {{ list-style: none; padding: 0; margin: 0; }}
  ul.news > li {{
    margin: 0; padding: 1.1em 0;
    border-top: 1px solid var(--line-soft);
  }}
  ul.news > li:first-child {{ border-top: none; padding-top: .15em; }}
  ul.news > li > p {{ margin: 0; }}

  /* 来源 */
  .src {{
    display: block; margin-top: .55em;
    font-size: .76em; color: var(--muted); letter-spacing: .03em;
  }}
  .src a {{
    color: var(--muted); border-bottom: 1px dotted rgba(128,128,128,.45);
  }}
  .src a:hover {{ color: var(--accent); border-bottom-color: var(--accent); }}

  /* 归档 */
  nav.archive {{
    margin-top: 3.4em; padding-top: 1.4em;
    border-top: 1px solid var(--line);
  }}
  nav.archive h2 {{ margin: 0 0 .9em; border: none; padding: 0; }}
  .arcrow {{
    display: flex; align-items: baseline; gap: 1em;
    padding: .55em 0; border-bottom: 1px solid var(--line-soft);
  }}
  .arcrow:last-child {{ border-bottom: none; }}
  .ym {{ color: var(--muted); font-size: .8em; white-space: nowrap; min-width: 6.6em; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: .4em; }}
  .chips a {{
    display: inline-block; min-width: 2em; text-align: center;
    padding: .06em .35em; border-radius: 5px;
    font-size: .86em; font-variant-numeric: tabular-nums;
    color: var(--fg-soft); background: var(--pill); border-bottom: none;
  }}
  .chips a:hover {{ color: #fff; background: var(--accent); }}
  .empty {{ color: var(--muted); font-size: .86em; }}

  nav.back {{ margin-top: 3.4em; font-size: .9em; }}
</style>
</head>
<body>
<p class="stamp">{stamp} 更新 · 北京时间</p>
{body}
</body>
</html>
"""

TAG_CLASS = {"已证实": "ev", "官方口径": "off", "单一来源": "sig", "待核实": "chk"}


def style_tags(text: str) -> str:
    """把【已证实】这类标记渲染成小标签。"""
    for label, cls in TAG_CLASS.items():
        text = text.replace(f"【{label}】", f'<span class="tag {cls}">{label}</span>')
    return text


BRACKET_LINK = re.compile(r'〔\s*<a href="([^"]+)">([^<]+)</a>\s*〕')


def _move_sources(block: str, closer: str) -> str:
    """把块内的〔[来源](url)〕摘出来，统一挂到块末尾。"""
    links = BRACKET_LINK.findall(block)
    if not links:
        return block
    block = BRACKET_LINK.sub("", block)
    srcs = " · ".join(f'<a href="{u}">{t}</a>' for u, t in links)
    pos = block.rfind(closer)
    if pos == -1:
        return block
    return block[:pos] + f'<span class="src">来源：{srcs}</span>' + block[pos:]


def tidy_sources(text: str) -> str:
    """列表条目与正文段落里的来源链接，都收进一行小灰字。"""
    text = re.sub(
        r"<li>.*?</li>", lambda m: _move_sources(m.group(0), "</li>"), text, flags=re.S
    )
    text = re.sub(
        r"<p>.*?</p>", lambda m: _move_sources(m.group(0), "</p>"), text, flags=re.S
    )
    return text


def mark_news_lists(text: str) -> str:
    """给国内/国际/数据三个栏目的列表加上 news 类，去掉圆点。"""
    for section in ("国内", "国际", "数据与日程"):
        text = re.sub(
            rf"(<h2>{section}</h2>\s*)<ul>",
            r'\1<ul class="news">',
            text,
        )
    return text


def build_archive_nav(dates: list[str]) -> str:
    if not dates:
        return '<nav class="archive"><h2>往期归档</h2><p class="empty">还没有往期，明天开始积累。</p></nav>'

    groups: dict[str, list[str]] = {}
    for d in dates:
        groups.setdefault(d[:7], []).append(d)

    rows = []
    for ym in sorted(groups, reverse=True):
        year, month = ym.split("-")
        chips = "".join(
            f'<a href="archive/{d}.html">{int(d[8:10])}</a>' for d in groups[ym]
        )
        rows.append(
            f'<div class="arcrow"><span class="ym">{year} 年 {int(month)} 月</span>'
            f'<span class="chips">{chips}</span></div>'
        )
    return '<nav class="archive"><h2>往期归档</h2>' + "".join(rows) + "</nav>"


def render_html(
    markdown_text: str,
    dates: list[str] | None,
    archive: bool,
    for_email: bool = False,
) -> str:
    body = mdlib.markdown(
        markdown_text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )

    # 「→」独立成块（正文里裸箭头的老格式）
    body = re.sub(
        r"^\s*→\s*(.+?)(?=</li>|</p>|<br\s*/?>|$)",
        r'<span class="so">→ \1</span>',
        body,
        flags=re.M,
    )
    # 引用块形式的后果句：「> ▍→ …」标上 soq
    body = re.sub(
        r"<blockquote>\s*(<p>\s*(?:▍)?\s*→)",
        r'<blockquote class="soq">\1',
        body,
    )
    # 引用块形式的推断：「> ▍推断：…」标上 soi
    body = re.sub(
        r"<blockquote>\s*(<p>\s*(?:▍)?\s*推断)",
        r'<blockquote class="soi">\1',
        body,
    )
    body = style_tags(body)
    body = tidy_sources(body)
    body = mark_news_lists(body)

    if for_email:
        nav = '<p class="stamp" style="margin-top:2em">—— 完 ——</p>'
    elif archive:
        nav = '<nav class="back"><a href="../index.html">← 返回最新一期</a></nav>'
    else:
        nav = build_archive_nav(dates or [])

    return PAGE_TEMPLATE.format(
        title=f"每日简报 · {DATE}",
        stamp=STAMP,
        body=body + nav,
    )


def write_outputs(text: str) -> None:
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    (ARCHIVE / f"{DATE}.md").write_text(text, encoding="utf-8")
    (ARCHIVE / f"{DATE}.html").write_text(
        render_html(text, None, archive=True), encoding="utf-8"
    )

    dates = sorted((p.stem for p in ARCHIVE.glob("*.md")), reverse=True)
    (DOCS / "index.html").write_text(
        render_html(text, dates, archive=False), encoding="utf-8"
    )
    log(f"[写入] docs/index.html 与 archive/{DATE}.html（共 {len(dates)} 期）")


def push_email(text: str) -> None:
    """邮件推送。默认走 QQ 邮箱，465 端口 SSL。"""
    host = os.environ.get("SMTP_HOST", "smtp.qq.com").strip() or "smtp.qq.com"
    user = os.environ.get("SMTP_USER", "").strip()
    pwd = os.environ.get("SMTP_PASS", "").strip()
    to = os.environ.get("MAIL_TO", "").strip() or user

    if not (user and pwd):
        log("[邮件] 未配置 SMTP_USER / SMTP_PASS，跳过")
        return

    try:
        port = int(os.environ.get("SMTP_PORT", "465") or 465)
    except ValueError:
        port = 465

    html = render_html(text, None, archive=True, for_email=True)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(f"每日简报 {DATE}", "utf-8")
    msg["From"] = formataddr((str(Header("每日简报", "utf-8")), user))
    msg["To"] = to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=45)
        else:
            server = smtplib.SMTP(host, port, timeout=45)
            server.starttls()
        server.login(user, pwd)
        server.sendmail(user, [to], msg.as_string())
        server.quit()
        log(f"[邮件] 已发送到 {to}")
    except Exception as exc:
        log(f"[邮件失败] {exc}")


def push_serverchan(text: str) -> None:
    if not SERVERCHAN_KEY:
        log("[推送] 未配置 SERVERCHAN_KEY，跳过")
        return
    try:
        resp = requests.post(
            f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send",
            data={"title": f"每日简报 {DATE}"[:32], "desp": text[:30000]},
            timeout=40,
        )
        log(f"[推送] Server酱 HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        log(f"[推送失败] {exc}")


def main() -> None:
    log(f"=== 每日简报 {DATE} 星期{WEEKDAY} ===")
    items = collect()
    log(f"共抓取 {len(items)} 条去重条目")

    if len(items) < 8:
        sys.exit("抓到的条目太少（<8），可能所有源都失效了，请检查 FEEDS")

    text = call_deepseek(items)
    if len(text) < 500:
        sys.exit(f"生成内容过短（{len(text)} 字），疑似失败，未写入")

    write_outputs(text)
    push_serverchan(text)
    push_email(text)
    log(f"完成，正文 {len(text)} 字")


if __name__ == "__main__":
    main()
