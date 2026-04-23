---
description: 热点视频自动生成流水线 v2 — 用 HyperFrames + 云扬男声 + Whisper 字幕对齐。Apple 极简风视觉，与 v1 (convert_sketch_v2) 并存，输出 video_hyperframes.mp4。
argument-hint: setup | article github|ai|daily | convert <file> | run github|ai|daily | status | schedule | upload <path>
allowed-tools: Agent(*), Bash(python3:*), Bash(pip*), Bash(apt*), Bash(ffmpeg*), Bash(ffprobe*), Bash(npx*), Bash(whisper-cpp*), Bash(biliup*), Bash(sqlite3*), Read(*), Write(*), Edit(*), Glob(*), Grep(*)
---

# 热点视频自动生成流水线 · v2 (HyperFrames)

帮助用户自动生成热点资讯视频并上传 B 站。**v2 区别于 v1：**
- 视觉引擎：**HyperFrames**（HTML 合成）替代 SVG + Playwright
- TTS：edge-tts `zh-CN-YunyangNeural` (云扬，专业新闻男声)，备用 `YunjianNeural` (云健，激情)
- 字幕：whisper-cpp medium 模型 char-level 强制对齐
- 视觉：Apple 冷白 `#FBFBFD` + Inter 900 / Noto Sans CJK SC，珊瑚红 `#E45A45` 强调
- 输出文件：**`video_hyperframes.mp4`**（与 v1 的 `video_sketch_v2.mp4` 并存）

## 流水线根目录
`/root/video-pipeline/` — 所有代码/配置/输出/日志均在此目录。

## 子命令路由

根据 `$ARGUMENTS` 执行对应操作：
- `setup` → 执行【Setup v2 流程】（装 whisper.cpp + Node 22 + HyperFrames）
- `article github|ai|daily` → 复用 v1 的 article 生成（与 v1 共享 article.md 格式）
- `convert <file>` → 将审核后的 article.md 转成 v2 视频
- `run github|ai|daily` → 一键跑完全流程（article + convert v2）
- `status` → 查看最近运行记录
- `schedule` → 配置定时任务
- `upload <path>` → 上传视频到 B 站（与 v1 共用）
- 无参数 → 列出子命令，询问用户

---

## Article 流程（与 v1 完全一致）

**直接复用** `/video-pipeline article` 的 A1-A5 步骤。article.md 输出位置不变：
`/root/video-pipeline/output/<date>/<type>/article.md`

**v2 兼容的 frontmatter 扩展**（可选）：
```yaml
tts_voice: yunyang   # 默认；改 yunjian 用激情男声
```

不写则默认 yunyang。其他 frontmatter 字段与 v1 完全相同。

### 步骤

**A1 — 抓取内容**（启动 video-scraper 子 Agent）
- `github`：GitHub Trending weekly Top 10 + Hacker News RSS Top 5
- `ai`：HuggingFace Blog RSS + VentureBeat AI RSS + arxiv cs.AI 最新 5 篇
- `daily`：36氪 RSS + 少数派 RSS + Hacker News Top 10

**A2 — 过滤去重** SQLite url hash，过滤 >7 天旧内容，有效 ≥3 条

**A3 — 生成结构化图文文章 + B-roll 编排**（启动 video-script-writer 子 Agent）

video-script-writer agent **同时产出两个文件**（两个都必须写盘）：
1. `article.md` — 口播稿 + 5 张要点卡片
2. `visual_beats.json` — 每条新闻 6-8 个异构 B-roll beat

**visual_beats.json 硬约束**（agent 必须遵守）：
- 每条新闻 6-8 个 beat，同条内类型不重复
- 10 类 beat：`logo-hero` / `wordmark` / `metric-cards` / `codeblock` / `tools-cascade` / `mockup(slack|discord|imessage)` / `glyphs` / `stat-hero` / `timeline` / `image-hero`
- 排序：logo-hero 或 image-hero 开场 → wordmark/stat-hero 紧随 → 中段 2-4 个内容型 → glyphs/image-hero 收尾
- 每 beat `weight` ∈ [0.08, 0.25]，总和 ≈ 1.0
- 详细 schema 见 `video-script-writer` agent 的 system prompt

**article.md 写作要求**（直接影响 /video-score 得分，必须满足）：
- 每条摘要 blockquote **80-170 字**（B2）
- 每条新闻 **5 张要点卡片**（B3）
- **零禁用词**：惊艳/颠覆/革命性/史诗级/遥遥领先/大家好/感谢阅读（B4）
- 正文第一句必须含来源归因（"近日，"/"据媒体报道，"/"官方称，"）
- 每段 60-130 字，每段只说一件事
- 分类（概览区 H3）自由命名，2-6 字，2-4 个分类
- 详细格式规范见 `_archive/video-pipeline.v1.md` 第 71-217 行

**A4 — 保存**
- `/root/video-pipeline/output/<date>/<type>/article.md`
- `/root/video-pipeline/output/<date>/<type>/visual_beats.json`

**A4.5 — 素材采集**（`pipeline/collect_media.py` 自动选最优）
```bash
python3 /root/video-pipeline/pipeline/collect_media.py \
  --url <新闻来源URL> --out <output_dir>/media --num <N>
```
素材分 ≥ 3 分，失败用 Playwright 补截图。详见 `_archive/video-pipeline.v1.md` A4.5。

**A5 — Evaluator 门禁**（调用 `/video-score`）
- `min_score_to_convert = 60` · `max_revision_rounds = 3`
- 3 轮仍未达标 → 取历史最高分版本强制放行（FORCED_PASS）
- integrity_fail → ABORTED

---

## Convert 流程（v2 替换 v1 convert）

接收一个 article.md 路径，调用：
```bash
python3 /root/video-pipeline/convert_hyperframes.py <article.md> [--quality draft|standard|high]
```

**默认使用 `--quality draft`（生产 cron 同样用 draft）**，一期约 14-18 分钟。
`standard`（60-90 分钟）只在手动触发、时间允许时使用，cron 禁止。

**Pipeline 内部步骤：**

**C1 — 解析 article.md**
- frontmatter（title, date, type, episode, channel_color, tab_label, tts_voice）
- 概览 H3 → category 反查每条新闻
- 每条新闻：H2 标题 #N、blockquote（口播）、5 个要点 bullets、media path、source URL

**C2 — 自动 logo 检测**
- 扫描 title + blockquote 匹配 brand 名（OpenAI/Anthropic/Google/阿里/字节...）
- 命中则从 simple-icons CDN (`cdn.jsdelivr.net/npm/simple-icons`) 下载 SVG 缓存到 `assets/logos/`
- 未命中则用文章 media 图作 hero

**C3 — TTS 生成（edge-tts 云扬）**
- 三段：intro / news_NN / outro
- intro 文案：`AI 早报，{date} 年 {month} 月 {day} 日，本期带来 N 条热门资讯。`
- news 文案：`第N条。{blockquote}`
- outro 文案：`感谢收听，明日见。`
- 各段保存为 mp3，再 `ffmpeg concat → full.wav`
- 主播默认 `zh-CN-YunyangNeural` 云扬；frontmatter `tts_voice: yunjian` 切换到云健

**C4 — Whisper 字幕对齐**
- `whisper-cpp -m /opt/whisper.cpp/models/ggml-medium.bin -l zh -ml 1 -oj` → JSON
- 解析 char-level 时间戳
- 强制对齐：用 Whisper 时间 + 原始脚本文字（避免识别错误）
- 自动按标点切字幕，最短 1.5s（短的合并到下一条）

**C5 — Compose HTML（v4 视觉锁定版）**
- 单 `index.html`，全部 inline CSS + GSAP timeline
- Apple 冷白 `#FBFBFD` 底，单道顶部白光，2.5% 噪点
- 每条新闻 4 beats（独立 track，软交叉淡入淡出）：
  1. **logo** (~20% 时长)：company logo / media 大图 / 大编号兜底
  2. **title** (~18% 时长)：大标题 + source platform badge
  3. **bullets** (~50% 时长)：5 要点卡片 stagger 揭示
  4. **closing** (~12% 时长)：「下一条 →」预告 + source URL
- intro：大日期 + sections 列表 + 「小兔播报」
- outro：「感谢收听 · 明日见」+ Bunny B 站
- 顶部 chrome 移除（最大化主体），仅底部 12px 极次要签名条
- eyebrow 在每条新闻期间显示 `№ NN  CATEGORY` 在左上 60px
- 字幕：白底胶囊 36px Inter 600，bottom-100px 居中

**C6 — Lint** : `npx hyperframes lint` — 必须 0 errors，否则中止。

**C7 — 渲染** : `npx hyperframes render -q <quality> -w 2 -o <article_dir>/video_hyperframes.mp4`

**C8 — 报告 + ffprobe 验证**

---

## Setup v2 流程
确保以下都装好：
1. `node --version` ≥ v22
2. `ffmpeg --version` 任意版本
3. `whisper-cpp` 在 PATH（`ln -sf /opt/whisper.cpp/build/bin/whisper-cli /usr/local/bin/whisper-cpp`）
4. `/opt/whisper.cpp/models/ggml-medium.bin` 存在（约 1.5GB）
5. python: `edge-tts`, `kokoro-onnx`, `soundfile`
6. `cd /root/video-pipeline/hyperframes && npx hyperframes doctor` 全绿
7. 字体：`/root/video-pipeline/hyperframes/assets/fonts/NotoSansSC-Regular.ttf`

---

## v1 vs v2 切换

**当前默认仍是 v1**（`/video-pipeline`）。
v2 处于并行验证阶段，cron 暂未切换。

**手动切换 cron 到 v2：** 编辑 `/root/video-pipeline/auto_daily.sh`，把 `/video-pipeline run ai` 改为 `/video-pipeline-v2 run ai`。

---

## 当前用户指令
$ARGUMENTS
