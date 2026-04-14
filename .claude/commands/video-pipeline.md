---
description: 热点视频自动生成流水线。当用户想生成 GitHub热点、AI资讯、科技新闻视频，或提到自动上传B站、定时视频生成时触发。支持 setup/article/convert/run/status/schedule/upload 子命令。
argument-hint: setup | article github|ai|daily | convert <file> | run github|ai|daily | status | schedule | upload <path>
allowed-tools: Agent(*), Bash(python3:*), Bash(pip*), Bash(apt*), Bash(ffmpeg*), Bash(ffprobe*), Bash(biliup*), Bash(sqlite3*), Read(*), Write(*), Edit(*), Glob(*), Grep(*)
---

# 热点视频自动生成流水线

帮助用户自动生成热点资讯视频并上传 B 站。**推荐工作流：先生成图文文章审核，再转成视频。**

## 流水线根目录
`/root/video-pipeline/` — 所有代码、配置、输出、日志均在此目录。

## 子命令路由

根据 `$ARGUMENTS` 执行对应操作：
- `setup` → 执行【Setup 流程】
- `article github|ai|daily` → **【推荐】** 生成图文文章，供用户审核
- `convert <file>` → 将审核后的图文文章转成视频
- `run github|ai|daily` → 一键跑完全流程（article + convert，中途有确认点）
- `status` → 查看最近运行记录
- `schedule` → 配置定时任务
- `upload <path>` → 上传视频到 B 站
- 无参数 → 列出子命令，询问用户

---

## 推荐工作流

```
/video-pipeline article github   ← Step1: 生成图文文章
                                    ↓ 用户审核、修改内容
/video-pipeline convert <file>   ← Step2: 转成视频
                                    ↓ 用户预览视频
/video-pipeline upload <path>    ← Step3: 上传 B 站
```

---

## Article 流程（生成图文文章）

### 步骤

**A1 — 抓取内容**（启动 video-scraper 子 Agent）
- `github`：GitHub Trending weekly Top 10 + Hacker News RSS Top 5
- `ai`：HuggingFace Blog RSS + VentureBeat AI RSS + arxiv cs.AI 最新 5 篇
- `daily`：36氪 RSS + 少数派 RSS + Hacker News Top 10

**A2 — 过滤去重**
- SQLite url hash 去重
- 过滤 > 7 天旧内容，有效内容 ≥ 3 条

**A3 — 生成结构化图文文章**（启动 video-script-writer 子 Agent）

为每条新闻生成 **5 个要点卡片**，格式：
```json
{"emoji": "📈", "title": "要点标题（10字内）", "body": "详细说明（50-80字）"}
```

> **评分对齐要求（直接影响 /video-score 得分，必须满足）：**
> - 每条摘要 blockquote 严格控制在 **80-170 字**（B2 满分条件）
> - 每条新闻必须有且仅有 **5 个要点卡片**（B3 满分条件）
> - **零禁用词**：惊艳/颠覆/革命性/史诗级/遥遥领先/大家好/感谢阅读（B4 扣分项）
> - 每条新闻正文第一句必须含来源归因开头（B4/C4 得分项）
> - 这些要求不是建议，是评分硬指标，生成时自我检查，不达标则重写

**A4 — 保存图文文章**

保存为 `/root/video-pipeline/output/<date>/<type>/article.md`

### 文章格式规范（橘鸦Juya AI早报风格，严格遵守）

#### 整体结构
```
文章标题（frontmatter）
→ H1 日期标题
→ H2 概览（分类目录）
→ 分割线
→ [每段新闻，结构见下]
→ 分割线
→ 结尾固定两行
```

#### 文章标题格式
```
关键事件；关键事件【AI 早报 YYYY-MM-DD】
```
示例：`Anthropic 发布 Claude Mythos；智谱开源 GLM-5.1【AI 早报 2026-04-08】`

#### 每段新闻的完整构成（每段只说一件事）

```markdown
## 新闻标题（主体+动作+结果，20-35字） #N

> **摘要**（80-170字，2-3句）：[主体] + [动作/事件] + [核心数据] + [当前状态/影响]。
> 关键公司名、数字**加粗**，产品名/技术术语用`code`格式。此段用作视频口播主文案。

近日，[正文第1句：时间/来源状语开头，必须有明确主语]。[背景概况，60-130字]

[正文第2段：技术细节/核心功能，60-130字，每段一个主题]

[正文第3段：社区讨论/使用场景/延伸影响，30-100字。可引用社区反应："社区认为……"/"开发者反馈……"]

[正文最后段：可用性说明，必须有。已开放/定价/下载地址/上线平台，30-80字]

![内容素材](图片URL或截图路径，视频帧等，无图则省略此行)
<!-- media_score: N 分（见素材评分规则） -->

```
https://原始信息来源URL（每段结尾放，裸链接，不超链接）
```

- 📈 **要点标题（≤10字）**: 要点说明（50-80字，用于视频卡片展示）
- 💡 **要点标题（≤10字）**: 要点说明（50-80字）
- ⚡ **要点标题（≤10字）**: 要点说明（50-80字）
- 🔧 **要点标题（≤10字）**: 要点说明（50-80字）
- 📊 **要点标题（≤10字）**: 要点说明（50-80字）

---
```

#### 概览区格式（文章开头目录）
```markdown
## 概览

### 模型发布
- 新闻标题完整文字 #1
- 新闻标题完整文字 #2

### 开发生态
- 新闻标题完整文字 #3

---
```

#### 结尾固定两行（每篇必须）
```markdown
提示：内容由AI辅助创作，可能存在幻觉和错误。
作者Bunny，视频版在同名哔哩哔哩。欢迎点赞、关注、分享。
```

#### 完整 article.md 骨架
```markdown
---
title: "关键事件；关键事件【AI 早报 YYYY-MM-DD】"
type: github|ai|daily
episode: 1
date: 2026-04-08
channel_color: "#2DA44E"
tab_label: "开发生态"
tags: ["AI", "GitHub", "开源"]
---

# AI 早报 YYYY-MM-DD

## 概览
### 模型发布
- 新闻标题 #1
### 开发生态
- 新闻标题 #2

---

## 新闻标题 #1
> 摘要...

近日，...（正文段落）

...（补充细节：社区讨论/使用场景）

目前已开放...（可用性）

![截图](url)

` ` `
https://source-url
` ` `

- 📈 **要点**: 说明
- 💡 **要点**: 说明
- ⚡ **要点**: 说明
- 🔧 **要点**: 说明
- 📊 **要点**: 说明

---

## 新闻标题 #2
...（同上结构）

---

提示：内容由AI辅助创作，可能存在幻觉和错误。
作者Bunny，视频版在同名哔哩哔哩。欢迎点赞、关注、分享。
```

### 写作规范细则

**文体：** 新闻通讯社电报风格，第三人称，绝不出现"你/我/我们认为"

**禁止词：** 惊艳、颠覆、革命性、史诗级、遥遥领先、大家好、感谢阅读

**来源归因（开头必须）：** `"近日，"` / `"据媒体报道，"` / `"官方称，"` / `"社区发现，"` / `"消息称，"`

**关键词高亮：**
- 产品名/功能名用 `code` 格式：`Claude Code`、`bash tool`、`claude -p`
- 公司名/关键数字用 **加粗**：**Anthropic**、**93.9%**、**300万**

**数字：** 全部阿拉伯数字，禁止转汉字（`7540亿` 不写"七千五百四十亿"）

**英文术语保留：** `CLI`、`API`、`TTS`、`LLM`、`MCP`、`Token`、`eGPU`、`BYOK`

**正文段落顺序：** 背景概况 → 技术细节 → 社区讨论/使用场景 → 可用性（最后段必须有）

**分类（概览区 H3，自由命名）：**
根据本期内容自由拟定分类名，2-6字，准确概括该组新闻的共同主题。
参考分类（不限于此）：`要闻` / `模型发布` / `开发生态` / `产品应用` / `技术与洞察` / `行业动态` / `前瞻与传闻` / `安全与隐私` / `硬件与芯片` / `创业融资` / `开源社区` / `政策监管` 等，也可自创。
同一期内分类数量建议 2-4 个，避免每条新闻单独一类。

**A4.5 — 素材采集（智能多策略，自动选最优）**

为每条新闻采集最高质量、与内容匹配的素材。**使用 `/root/video-pipeline/pipeline/collect_media.py` 脚本自动执行**，严禁人工伪造素材。

**素材评分规则：**

| 素材类型 | 分值 | 示例 |
|---------|------|------|
| 视频（mp4/YouTube） | **9分** | README 演示视频、YouTube 演示 |
| 动态图（GIF） | **6分** | README 演示 GIF、操作演示 |
| 高信息量截图/预览图 | **3分** | 文章 OG 封面、GitHub 预览图、Playwright 截图 |
| 无素材 | **0分** | 省略 `![]()` 行 |
| 虚假/不相关素材 | **-10分** | 严禁使用 |

**采集命令（每条新闻执行一次）：**
```bash
python3 /root/video-pipeline/pipeline/collect_media.py \
  --url <新闻来源URL> \
  --out <output_dir>/media \
  --num <N>
# 返回 JSON: {"path": "...", "score": N, "desc": "...", "type": "gif|image|video"}
```

**各内容类型策略（脚本自动按优先级尝试）：**

| 内容类型 | 优先策略 |
|---------|---------|
| **GitHub repo** | README GIF(6) → README YouTube/mp4 视频(9) → README 首图(3) → opengraph(3) |
| **YouTube** | yt-dlp 下载最低画质视频(9) → 封面图(3) |
| **Twitter/X** | Playwright 截图推文(3)（需调用 MCP playwright） |
| **学术论文(arXiv)** | HTML版论文首图(3) → OG图(3) |
| **文章/博客/HN** | OG封面图(3) → 首张内容图(3) → Playwright截图(3) |

**Playwright 补充截图（脚本未能自动获取时）：**
- 使用 `mcp__playwright__browser_navigate` + `mcp__playwright__browser_take_screenshot` 截图
- 截图前滚动页面，确保显示有效内容（不是 loading 状态或空白）
- 截图保存至 `<output_dir>/media/<N>_media_pw.png`

**素材质量验证（必须执行，防止低分）：**
1. 检查文件大小 ≥ 5KB（脚本已内置）
2. 检查图片尺寸 ≥ 150×100px（脚本已内置）
3. 人工判断：素材内容是否与当条新闻直接相关？若不相关 → 重新采集或换 Playwright 截图
4. **严禁**使用与新闻无关的图片（哪怕质量高），宁可降级用 Playwright 截图

**素材兜底规则：**
- 每条新闻素材分 **≥ 3 分**（`collect_media.py` 失败时必须用 Playwright 截图补充）
- 全文素材总分 **≥ 新闻数 × 3**

**A5 — Evaluator 门禁（调用 `/video-score`，article mode）**

> Generator ≠ Evaluator。A4.8 内联自评已废弃，统一由独立 evaluator 门禁放行。

**门禁参数：**
- `min_score_to_convert = 60`
- `max_revision_rounds = 3`
- 3 轮后仍未达标 → 取历史最高分版本强制放行（FORCED_PASS），写入 run-log

**执行流程：**

```
best_score = 0
best_article_path = article_path

for revision_round in 0..3:
    调用 /video-score <article_path> --json
    读取 evaluation.json → score, grade, integrity_fail, revise_hints

    if integrity_fail:
        终止，写 run-log: ABORTED integrity_fail
        停止

    if score > best_score:
        best_score = score
        cp article.md → article_best.md  # 保留最高分版本

    if score >= 60:
        → PASS，展示评分，进 Convert
        break

    if revision_round < 3:
        读 revise_hints，筛 high/mid 优先级
        由 AI 判断修订策略（见 /video-score 修订策略表）
        执行修订，覆盖写 article.md
    else:
        cp article_best.md → article.md  # 回滚最高分版本
        → FORCED_PASS，展示评分 + ⚠️ 提示，进 Convert
        写 run-log: forced_pass, best_score=XX
```

**展示格式（PASS/FORCED_PASS 均输出）：**

```
✅ 图文文章已生成（evaluator 通过）
路径：/root/video-pipeline/output/<date>/<type>/article.md
共 X 条新闻 | 修订轮次：X/3 | 最高分版本：XX 分

┌─────────────────┬───────┬─────┬──────────────────────┐
│ 维度            │ 得分  │ 满分│ 备注                 │
├─────────────────┼───────┼─────┼──────────────────────┤
│ A 素材可用性    │ XX/35 │  35 │ X图 X GIF            │
│ B 内容质量      │ XX/35 │  35 │ 核实X/X条·去重通过   │
│ C 结构完整性    │ XX/20 │  20 │                      │
│ D 叙事钩子      │ XX/10 │  10 │                      │
├─────────────────┼───────┼─────┼──────────────────────┤
│ 综合得分        │ XX    │ 100 │ ⭐⭐⭐ B 级           │
└─────────────────┴───────┴─────┴──────────────────────┘

门禁：✅ convert (≥60)

下一步：/video-pipeline convert <article路径>
```

评级对照：S≥90 / A≥75 / B≥60 / C≥45 / D<45

---

## Convert 流程（图文转视频）

接收一个 article.md 路径，执行 `python3 /root/video-pipeline/convert.py`（脚本内已硬编码 ARTICLE 路径，需在运行前更新）。

**C1 — 读取并验证图文文章**
- 解析 frontmatter（title, type, episode, date, channel_color, tab_label）
- 提取每条新闻：H2标题、blockquote摘要（口播正文）、5个要点卡片、media 图片（最多2张）
- 动态提取 Tab 栏标签（从概览区 H3 分类 + 开场/结尾 固定项）

**C2 — TTS 配音 + ASS 字幕**
- edge-tts 声音：`zh-CN-XiaoxiaoNeural`（晓晓），rate=+5%
- 开场固定句：`"欢迎收看《文章标题》，本期带来 X 条热门资讯。"`
- 口播内容：每条新闻 blockquote 摘要（去除 `摘要：` 前缀及 Markdown 格式）
- 结尾固定：`"感谢收听，明天见。"`
- 字幕：ASS 格式，PlayResX=1920/PlayResY=1080，字号 72px，固定底部单行，逐句同步
- 保存到 `/tmp/vp_build3/audio/` 和 `/tmp/vp_build3/subs/`

**C2.5 — 素材解析**
- 解析每条新闻的 `![](...)` 图片（最多2张）
- 远程 URL 自动下载到 `/tmp/vp_build3/media/`

**C3 — 生成 Slides（Pillow，NotebookLM 深色风格，1920×1080）**

**背景**：深色 `#0A0A14`

**顶部动态 Tab 栏（高52px）**：
- 标签从文章分类动态提取，固定含 开场 / 结尾
- 激活标签：青色 `#0DCFB4` 背景 + 白字，非激活：深灰底
- 字号 26px

**标题区（Tab栏下方，高108px）**：
- 自动换行，字号从 58→50→44→38px 逐档尝试，确保 ≤2行不截断
- 颜色白色，左对齐，左边距52px

**卡片区（标题下方至底部），3+2 布局**：
- 圆角16px，深色卡片背景 `#161628`，顶部彩色条（青/紫/琥珀/玫/绿 循环）
- 卡片内：emoji 图标(52px, NotoColorEmoji) + 标题(28px, 频道主色) + 正文(26px, 灰色, 行高34px)
- Emoji 单独用 NotoColorEmoji.ttf @ 109px 渲染后缩小，确保彩色显示

**开场 Slide**：同心圆渐变背景 + 日期标题 + 分类卡片（与正片相同的 draw_card 样式）

**结尾 Slide**：居中 `感谢收听 · 明日见`，72px

**字体文件**：
- 中文：`/root/video-pipeline/assets/fonts/NotoSansSC-Regular.ttf`
- Emoji：`/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf`（仅在 PIL 中使用）
- ASS 字幕字体：`Noto Sans CJK SC`（系统字体，ffmpeg libass 调用）

**C4 — FFmpeg 逐段编码（字幕烧录 + 素材 Overlay）**
- 每段独立编码：`-loop 1` 静态幻灯片 + TTS 音频 + ASS 字幕（`ass=` filter）
- 素材 Overlay（翻书效果，每段口播期间内联展示）：
  - **图片**（1张）：RGBA 面板居中叠加，fade=alpha=1 渐入+向上滑动，持续后渐出+向下滑动
  - **图片**（2张）：并排显示于同一 RGBA 面板（左右各占一半，垂直居中对齐）
  - **GIF**：stream_loop 循环，yuva420p 透明混合，同款渐入渐出
  - **视频**：同 GIF 处理，时长取自然时长与可用窗口的较小值
  - **Overlay 时长自适应**（`calc_overlay_dur`）：图片=段时长×40%（3.5–8s），GIF=×55%（4–14s）
- 所有段编码完后 `-f concat` 拼接
- 输出：H.264 CRF=23，AAC 128k 44100Hz，`-movflags +faststart`
- 输出路径：article.md 同目录下的 `video_notebooklm.mp4`

**C5 — ffprobe 质量验证**
- 时长 / 分辨率（1920×1080）/ 有音频 / 文件大小

**C6 — 输出报告（含视频质量评分）**

用 ffprobe 结果计算 D 维度得分（满6分）：
- 分辨率 ≥1920×1080: 2分 / ≥1280×720: 1分
- 时长 60-900秒: 2分 / 30-60秒或>900秒: 1分
- 有 AAC 音频流: 1分
- 文件大小 1MB-500MB: 1分

综合估分 = 文章阶段 B+C+A 得分 + D 视频得分（满86分）

输出格式：
```
✅ 视频生成完成
路径：/root/video-pipeline/output/<date>/<type>/video.mp4
时长：X 分 XX 秒 | 大小：XX MB | 热点数：X 条

┌─────────────────────────────────────────────┐
│          最终质量评分                         │
├──────────────┬───────┬───────────────────────┤
│ A 素材质量   │ XX/40 │ 已在文章阶段确认        │
│ B 内容质量   │ XX/30 │                        │
│ C 格式规范   │ XX/10 │                        │
│ D 视频质量   │  X/6  │ 分辨率✅ 时长✅ 音频✅   │
├──────────────┼───────┼───────────────────────┤
│ 综合总分     │ XX/86 │ ⭐⭐⭐⭐ A级            │
└──────────────┴───────┴───────────────────────┘

是否现在上传到 B 站？(yes/no)
```

评级对照（86分制）：S≥77 / A≥65 / B≥52 / C≥34 / D<34

---

## Run 流程（一键全流程）

依次执行 Article + Convert，在 A5（展示图文后）等待用户确认再继续。

---

## Setup 流程

1. 安装：`ffmpeg`（apt）、`python3-pip`
2. 安装 Python 包：`feedparser httpx pillow edge-tts ffmpeg-python tenacity pydantic instructor newspaper4k`
3. 安装 biliup：`pip install biliup`
4. 初始化 SQLite 去重数据库（`/root/video-pipeline/pipeline/dedup.db`）
5. 创建配置文件：`settings.yaml`、`sources.yaml`
6. 确认字体：`/root/video-pipeline/assets/fonts/NotoSansSC-Regular.ttf`
7. 输出 Setup 完成报告

## Status 流程
查询 logs/ 和 dedup.db：最近 5 次运行 + 总内容数 + 输出目录空间

## Schedule 流程
管理 cron 定时任务，保存到 `/root/video-pipeline/config/schedule.yaml`

## Upload 流程
1. 检查 biliup 登录（未登录提示 `! biliup login`）
2. 读取同目录 article.md 提取标题和标签
3. `biliup upload <path> --title "..." --tag "AI,科技,GitHub"`
4. 报告上传结果

## 错误处理
每步失败记录到 `/root/video-pipeline/logs/<date>.log`，给出原因和修复建议。

## 当前用户指令
$ARGUMENTS
