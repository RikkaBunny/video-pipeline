# 热点视频自动生成流水线 — 架构文档

## 一、系统概览

```
┌─────────────────────────────────────────────────────────────────┐
│                        调度层 (Scheduler)                         │
│              APScheduler — 每日/每周 cron 触发                     │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                     Pipeline 编排器 (Orchestrator)                │
│         状态机 + 断点续跑 + 重试 + 质量门禁                          │
└──┬──────────┬───────────┬──────────┬───────────┬────────────────┘
   │          │           │          │            │
   ▼          ▼           ▼          ▼            ▼
[Step1]    [Step2]    [Step3]    [Step4]      [Step5]
抓取        过滤       生成脚本    合成视频      上传发布
Scraper    Filter    LLM Script  TTS+FFmpeg   Biliup
```

## 二、内容频道规划

| 频道 | 来源 | 更新周期 | 时长 |
|------|------|---------|------|
| GitHub 热点周报 | GitHub Trending + RSS | 每周一 | 7-8 分钟 |
| AI 资讯周报 | HuggingFace/arxiv/VentureBeat RSS | 每周三 | 5-6 分钟 |
| 科技日报 | 36氪/少数派/Hacker News RSS | 每日 | 3-4 分钟 |

## 三、视频风格规范（参考 IT咖啡馆）

- **封面**：渐变色背景 + 期数大字 + 相关 logo/图标（Pillow 生成）
- **片头**：3秒频道 logo 动画
- **内容段**：每条热点 = 标题卡(2s) + 内容配图(15-30s) + 字幕
- **片尾**：3秒关注引导
- **分辨率**：1920x1080，H.264 + AAC

## 四、技术栈选型

### 4.1 各步骤选型

```
Step 1 抓取:
  feedparser          # RSS 聚合主力（GitHub Trending/AI 新闻/科技资讯）
  httpx + asyncio     # 异步 HTTP，并发抓取
  BeautifulSoup4      # GitHub Trending 页面解析
  newspaper4k         # 新闻正文提取

Step 2 过滤/去重:
  SQLite              # 存储已处理内容的 hash，防止重复
  simhash             # 标题相似度去重
  pydantic            # 数据结构校验

Step 3 LLM 生成脚本:
  instructor          # 结构化输出 + 自动重试（最关键）
  pydantic            # 强类型脚本模型，防幻觉
  Claude API (claude -p)  # 利用已有账号，无需额外付费

Step 4 合成视频:
  edge-tts            # 中文 TTS（免费，晓晓/云希音色）
  openai-whisper      # TTS 音频 → 精准 SRT 字幕时间轴
  ffmpeg-python       # 视频合成核心
  Pillow              # 封面/标题卡片生成
  Pexels API          # 免费高清背景图/视频素材

Step 5 上传:
  biliup              # B 站投稿 CLI（稳定，社区活跃）
  social-auto-upload  # 备选，Playwright 模拟上传
```

### 4.2 质量三关门禁

```
关 1 — 抓取后: 时效过滤(24h) + SimHash 去重 + 内容充实度(>200字)
关 2 — 脚本后: Pydantic 校验 + 长度检查 + 禁止幻觉词 + Claude 自评分
关 3 — 视频后: FFprobe 检查时长/分辨率/音频/文件大小
```

## 五、目录结构

```
/root/video-pipeline/
├── config/
│   ├── settings.yaml          # 平台密钥、TTS声音、视频规格
│   └── sources.yaml           # RSS源列表、GitHub配置
├── pipeline/
│   ├── orchestrator.py        # 流水线编排，状态机
│   ├── scraper.py             # Step 1: 内容抓取
│   ├── filter.py              # Step 2: 去重+质量过滤
│   ├── script_generator.py    # Step 3: LLM 生成脚本
│   ├── tts.py                 # Step 4a: TTS 配音 + 字幕
│   ├── video_builder.py       # Step 4b: FFmpeg 合成
│   └── uploader.py            # Step 5: 上传 B 站
├── models/
│   └── schemas.py             # Pydantic 数据模型
├── utils/
│   ├── retry.py               # tenacity 重试装饰器
│   ├── db.py                  # SQLite 去重数据库
│   └── notifier.py            # 失败告警（Telegram/微信）
├── assets/
│   ├── fonts/                 # 字体文件（中文）
│   ├── music/                 # 背景音乐
│   └── templates/             # 封面模板
├── output/                    # 生成的视频输出
├── logs/                      # 运行日志
├── scheduler.py               # 定时任务入口
├── main.py                    # 手动触发入口
├── requirements.txt
└── docker-compose.yml
```

## 六、流水线状态机

每个 Pipeline Run 的状态持久化到 SQLite，支持断点续跑：

```
PENDING → SCRAPING → FILTERING → SCRIPTING → VALIDATING
       → TTS → VIDEO_BUILD → VIDEO_VALIDATE → UPLOADING → DONE
                                                         ↓ FAILED
```

## 七、参考项目

- [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) — 最完整的关键词→视频流水线
- [youtube-shorts-pipeline](https://github.com/rushindrasinha/youtube-shorts-pipeline) — 架构最清晰
- [NarratoAI](https://github.com/linyqh/NarratoAI) — 新闻→解说剪辑
- [social-auto-upload](https://github.com/dreammis/social-auto-upload) — 多平台上传
- [biliup](https://github.com/biliup/biliup) — B 站投稿

## 八、实施路径

### 阶段一：验证核心链路（优先）
1. 搭好目录结构和 Pydantic 模型
2. RSS 抓取 + SQLite 去重
3. Claude 生成脚本（用 `claude -p` 调用）
4. edge-tts 配音 + FFmpeg 基础合成
5. 人工验收第一个视频

### 阶段二：质量提升
1. Whisper 精准字幕对齐
2. Pexels 背景素材自动匹配
3. Pillow 封面模板
4. 三关质量门禁完整实现

### 阶段三：自动化
1. biliup 自动上传
2. APScheduler 定时任务
3. 失败告警通知
4. 监控看板
