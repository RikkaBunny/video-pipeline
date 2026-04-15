---
description: 对 video-pipeline 生成的图文文章或视频进行多维度质量评分，输出详细评分报告和修订建议。
argument-hint: <article.md路径> | <video.mp4路径> | latest [--json]
allowed-tools: Read(*), Glob(*), Grep(*), Bash(ffprobe:*), Bash(python3:*), Bash(curl:*), Bash(sqlite3:*), mcp__playwright__browser_navigate(*), mcp__playwright__browser_evaluate(*), mcp__playwright__browser_take_screenshot(*)
---

# video-pipeline 质量评分系统（Evaluator）

**article mode** 和 **video mode** 使用独立评分体系，不共用分母。

## 使用方式

- `/video-score latest` — 自动找最近一次生成的 article.md 评分
- `/video-score /path/to/article.md` — article mode
- `/video-score /path/to/video.mp4` — video mode（需同目录有 article.md）
- `/video-score <path> --json` — 额外写出 `evaluation.json`

---

# ARTICLE MODE（图文文章评分）

触发条件：目标文件为 `article.md`。

## 维度总览（满分 100）

| 维度 | 满分 | 核心问题 |
|------|------|---------|
| A 素材可用性 | 35 | 每条新闻的素材是否存在、相关、有质量 |
| B 内容质量 | 35 | 内容真实、无重复、TTS适用、卡片完整 |
| C 结构完整性 | 20 | convert 解析所依赖的所有格式字段 |
| D 叙事钩子 | 10 | 标题强度、首句hook、主题一致 |

门禁阈值：`min_score_to_convert = 60`
修订上限：`max_revision_rounds = 3`，3轮后取历史最高分版本强制放行（标记 FORCED_PASS）

---

## A 素材可用性（35分）

### 评分表

| 素材类型 | 原始分 | 判断标准 |
|---------|--------|---------|
| 🎬 视频（mp4/YouTube演示） | 6 | `![...](...mp4)` 或 YouTube 链接 |
| 🌀 动态图（GIF） | 5 | URL 含 `.gif` 或文件为 GIF |
| 🖼️ 相关截图/OG图 | 3 | 文件存在、尺寸≥150×100px、内容与新闻相关 |
| ➖ 无素材 | 0 | 缺少 `![]()` 行 |
| ❌ 虚假/不相关 | -8 | 文件不存在、与新闻无关、单色占位图 |

### 标准化公式

```
A_norm = round(A_raw / (n_items × 5) × 35, 1)
```

分母以 GIF（5分）为基准，反映新闻类文章的实际上限，不以视频为满分基准。

### 验证规则
- 本地路径：检查文件存在性 + 文件大小 ≥ 4KB
- 图片尺寸：用 python3 PIL 验证 ≥ 150×100px
- 内容相关性：alt 文字或文件名与新闻标题有实质关联，人工判断
- 单色检测：三通道 stddev < 6 → 判定为占位图 → -8分

---

## B 内容质量（35分）

### B1 来源核实——全量逐条核查（8分）

**核查所有条目**，不再抽样。

**核查流程（每条新闻执行）：**

```bash
# Step 1: URL 可访问性
curl -sIL --max-time 8 <source_url> | head -1
# HTTP 2xx/3xx → pass；4xx/5xx/timeout → fail

# Step 2: 标题内容匹配（Twitter 来源跳过此步）
curl -sL --max-time 10 <source_url> | python3 -c "
import sys, re
html = sys.stdin.read()
m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
print(m.group(1)[:200] if m else '')
"
# 取页面 title，与新闻标题做关键词重合度检测
```

**Twitter 来源处理**：
- 判断条件：source_url 域名为 `x.com` 或 `twitter.com`
- 只验证 URL 可访问，跳过标题匹配
- 不计入标题匹配失败统计

**评分计算：**
```
每条通过双检（可访问 + 标题匹配）→ +1 基础分
每条 404/timeout → -1（倒扣）
每条标题不匹配（非Twitter） → -1（倒扣）
B1 = max(0, 通过条数/n_items × 8 - 倒扣合计)
```

**INTEGRITY_FAIL 触发条件**：
- 超过 2 条标题完全不匹配（非Twitter）→ 整篇标记 `INTEGRITY_FAIL`
- 终止当前评分，不进入修订循环
- 写入 run-log，标记 `ABORTED: integrity_fail`

### B2 摘要TTS质量（9分）

- 每条 blockquote 摘要 80-170 字：+1/条（上限 n_items，不 cap 5）
- 全文摘要总字数 ≥ 400：+2
- 无明显口水词（"总的来说"/"值得注意的是"/"不得不说"）：+1

### B3 卡片完整性（8分）

- 每条新闻恰好有 5 个要点卡片（`- emoji **标题**: 正文`）：+1/条（上限 n_items）
- 抽查 3 条：每个卡片正文 ≥ 50 字：全对 +3 / 1处不足 +1 / 2处以上不足 +0

### B4 禁用词（4分）

基准 4，全文检测以下词汇每次 -1（扣完为止）：
`惊艳` `颠覆` `革命性` `史诗级` `遥遥领先` `大家好` `感谢阅读` `我们认为` `你认为` `总的来说` `不得不说`

### B5 去重检查（6分）

检查每条新闻是否与近 7 天已发布内容重复。

**Step 1 — URL 精确匹配（dedup.db）**
```bash
sqlite3 /root/video-pipeline/pipeline/dedup.db \
  "SELECT url, date FROM published
   WHERE url_hash = '<hash>'
   AND date >= date('now', '-7 days');"
```
命中 → 该条「硬重复」→ -2分/条

**Step 2 — 标题关键词相似度（近 7 天输出目录）**
```python
# 提取近7天所有 output/*/article.md 的新闻标题
# 对当前每条新闻标题提取关键词（去停用词后长度≥2的词）
overlap = len(keywords_current & keywords_past) / max(len(keywords_current), 1)
if overlap > 0.6: 疑似重复
```
疑似重复 → 在 revise_hints 中注明（不扣分，仅提示；由 AI 判断是否为新进展）

**评分计算：**
```
B5 = max(0, 6 - 硬重复条数 × 2)
```

---

## C 结构完整性（20分）

### C1 Frontmatter 完整性（5分）

必须包含：`title` `type` `date` `channel_color` `tab_label` `tags`
每缺一项 -1分（episode 缺失 -0.5）

### C2 标题格式（3分）

- 符合 `关键事件；关键事件【XX YYYY-MM-DD】` 格式：+2
- 主体部分（【】前）≤ 20 字：+1

### C3 文档结构（8分）

- H1 日期标题存在：+1
- `## 概览` 区块存在，含 ≥2 个 H3 分类：+1
- 每条新闻末尾来源 URL 在 ``` 代码块内（全对 +3 / 每缺一条 -0.5）：最高 +3
- 结尾固定两行（提示/作者）：+1
- ≥ 80% 的新闻末段有可用性说明（上线地址/定价/下载入口）：+2

### C4 写作规范（4分）

- 来源归因开头（`近日，`/`据XX报道，`/`官方称，`等）覆盖率 ≥ 80%：+2
- 全篇使用阿拉伯数字（抽查 5 处）：+2

---

## D 叙事钩子（10分）

### D1 标题钩子强度（4分）

对 frontmatter.title 主体部分评分：
- 含具体数字（如 "1000万"/"93.9%"）：+1
- 含对比/冲突（如 "开源vs闭源"/"首次超越"/"逆向破解"）：+2
- 含悬念/疑问（如 "为什么"/"将如何"/"疑遭"）：+1

### D2 首条新闻首句 hook（3分）

首条新闻 blockquote 的第一句话：
- 含具体数字 **且** 有强反差/动作：+3
- 含数字但无明显反差：+2
- 仅背景铺垫，无数字无反差：+1
- 纯描述，无亮点：+0

### D3 主题一致性（3分）

- > 80% 条目与 frontmatter.title 或分类主题契合：+3
- 60-80%：+2
- < 60%：+0

---

## 修订策略（AI 自主判断）

| 失分模式 | 建议策略 |
|---------|---------|
| A < 15，素材缺失/无效 | 重跑 collect_media.py，定向补采 |
| B1 有倒扣（URL失效或标题不匹配） | 定向替换问题条目的来源，重写该条内容 |
| B5 有硬重复 | 删除重复条目，从抓取池补充新内容 |
| B2/B3 失分（字数/卡片问题） | 定向修写失分条目 |
| C 失分（格式问题） | 直接 Edit 修复，不调 agent |
| D 失分（标题/首句弱） | 定向修改 frontmatter.title 和首条 blockquote |
| 总分 < 40 或失分分散在 ≥ 3 个维度 | 整篇重新生成 |
| 上轮修订后分数下降 | 回滚到历史最高分版本，再做定向修订 |

修订完成后必须重新完整评分，不允许跳过任何维度。

---

## 输出格式（article mode）

```
╔══════════════════════════════════════╗
║   video-pipeline 图文质量评分报告    ║
╚══════════════════════════════════════╝

文件：/root/video-pipeline/output/2026-04-14/ai/article.md
日期：2026-04-14 | 新闻数：7 | 修订轮次：1/3

┌─────────────────┬───────┬─────┬──────────────────────────┐
│ 维度            │ 得分  │ 满分│ 备注                     │
├─────────────────┼───────┼─────┼──────────────────────────┤
│ A 素材可用性    │ 21.0  │  35 │ 7×OG图3分，分母n×5       │
│ B 内容质量      │ 28.0  │  35 │ B1全量核查/B5无重复       │
│ C 结构完整性    │ 18.0  │  20 │ 格式齐全                 │
│ D 叙事钩子      │  8.0  │  10 │ 标题含数字+对比          │
├─────────────────┼───────┼─────┼──────────────────────────┤
│ 综合得分        │ 75.0  │ 100 │ ⭐⭐⭐⭐ A 级             │
└─────────────────┴───────┴─────┴──────────────────────────┘

门禁：✅ convert (≥60)

💡 修订建议（不阻塞，可参考）：
- [LOW] items[2].summary — 疑似与 2026-04-12 期重复（重合度 65%）

下一步：/video-pipeline convert <article路径>
```

---

## --json 输出契约

加 `--json` 时，写出 `<article_dir>/evaluation.json`：

```json
{
  "target": "/root/video-pipeline/output/2026-04-14/ai/article.md",
  "mode": "article",
  "timestamp": "2026-04-14T07:10:00+08:00",
  "revision_round": 1,
  "score": 75.0,
  "grade": "A",
  "best_score_so_far": 75.0,
  "dims": {
    "A": {"score": 21.0, "max": 35, "details": []},
    "B": {"score": 28.0, "max": 35, "details": []},
    "C": {"score": 18.0, "max": 20, "details": []},
    "D": {"score": 8.0,  "max": 10, "details": []}
  },
  "integrity_fail": false,
  "revise_hints": [
    {
      "priority": "high|mid|low",
      "loc": "items[N].field",
      "problem": "观察到的问题",
      "fix": "可执行的修复动作"
    }
  ],
  "warnings": []
}
```

`best_score_so_far` 跨修订轮次持久化：每轮评分后更新，3轮结束取最高分版本进 convert。

---

---

# VIDEO MODE（视频评分）

触发条件：目标文件为 `*.mp4`，同目录读取 `article.md`。

## 维度总览（满分 100）

| 维度 | 满分 | 说明 |
|------|------|------|
| A 素材质量 | 35 | 复用 article mode A 维度得分 |
| B 内容质量 | 25 | 复用 article mode B 维度，标准化到 25 |
| C 格式规范 | 10 | 复用 article mode C 维度，标准化到 10 |
| D 视频质量 | 10 | ffprobe 指标 |
| E 叙事连贯 | 10 | 主题/过渡/分类/呼应 |
| F 开场钩子 | 10 | TTS 开场文案 + 首条钩子 + 视觉 |

门禁阈值：`min_score_to_upload = 60`

### D 视频质量（10分，ffprobe）

| 指标 | 满分 | 标准 |
|------|------|------|
| 分辨率 | 3 | ≥1920×1080: 3 / ≥1280×720: 2 / 更低: 0 |
| 时长合理 | 3 | 60-900秒: 3 / 30-60或>900: 1 / <30: 0 |
| 有音频流 | 2 | AAC 音频流存在 |
| 文件大小 | 2 | 1MB-500MB: 2 / 边界: 1 |

### E 叙事连贯（10分）

- E1 主题一致（3）：>80% 条目契合标题主题 +3 / 60-80% +2 / <60% +0
- E2 过渡串联（3）：相邻条目间有递进/对比/因果关系 ≥3处 +3 / 1-2处 +2 / 0处 +0
- E3 概览分类准确（2）：H3 分类与实际内容匹配，0误分类 +2 / 1-2条 +1 / ≥3条 +0
- E4 首尾呼应（2）：结尾两行 +1 / 开篇主题在末尾有 callback +1

### F 开场钩子（10分）

- F1 TTS 开场文案强度（4）：含具体数字 +1 / 含冲突对比 +2 / 含悬念疑问 +1
- F2 首条新闻钩子句（3）：含数字或强反差 +3 / 仅背景 +1 / 纯描述 +0
- F3 开场 slide 视觉（3）：标题 ≤20字 +1 / 含主题关键词 +1 / 首屏无大段空白 +1

---

## 执行流程（通用）

**S1 — 定位文件**
- `latest`：`/root/video-pipeline/output/` 下最新日期目录的 article.md
- 指定路径：直接读取
- `.mp4` 路径 → video mode，同目录读 article.md

**S2 — 解析 article.md**
- frontmatter、H2标题、blockquote摘要、要点卡片、素材行、来源URL
- 若存在 `episode-spec.json` → 读取 theme/narrative_arc 辅助 E 维度

**S3 — 执行评分**
- Article mode：A → B（含B1全量核查、B5去重） → C → D
- Video mode：A/B/C 复用或重评 → D(ffprobe) → E → F

**S4 — 输出报告 + 可选写 evaluation.json**

---

## 评级对照（100分制）

| 总分 | 评级 | 建议 |
|------|------|------|
| 90-100 | ⭐⭐⭐⭐⭐ S | 直接发布 |
| 75-89  | ⭐⭐⭐⭐ A  | 小幅优化后发布 |
| 60-74  | ⭐⭐⭐ B   | 需优化后发布 |
| 45-59  | ⭐⭐ C    | 建议重新生成 |
| < 45   | ⭐ D     | 重新运行全流程 |

---

## 当前用户指令
$ARGUMENTS
