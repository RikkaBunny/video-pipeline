# Video Pipeline 项目

## 启动自动注册（每次 Claude Code 启动必须执行）

**Claude Code 每次启动时，必须立即执行以下操作，不需要用户确认：**

1. 使用 CronCreate 注册每日定时任务：
   - cron: `3 23 * * *` (UTC 23:03 = 北京 07:03)
   - recurring: true
   - prompt 内容见下方

2. 注册完成后输出"守护进程就绪"

定时任务 Prompt：
```
执行 /video-pipeline run，类型根据今天日期自动判断：偶数日用 ai，奇数日用 github（用 TZ=Asia/Shanghai date +%j 取年内第几天判断奇偶）。

完整流程（带 M2 双门禁）：
  S0 preflight → A1-A4 抓取/去重/生成/保存 → A5 evaluator 门禁
    → C1-C6 convert_sketch_v2.py → C7 evaluator 门禁 → upload

自动模式硬规则（cron 触发时必须遵守）：
  1. A5 门禁：score < 60 触发最多 3 轮精准修订，每轮保留历史最高分版本；3 轮后仍未达标 → 取最高分版本强制放行（FORCED_PASS），写 run-log 标注。
  2. C7 门禁：score < min_score_to_upload(=75) **严禁上传**。视频保存在本地，但不传 B 站。写 run-log 标 convert_only。
  3. score ≥ 75 才允许自动上传。
  4. 任何阶段致命错误都终止 + 写 log，不吞错、不降级。
  5. run-log 位置：/root/video-pipeline/logs/run-<ISO8601>.json

宁可今天不发，也不发烂片——这是 Harness Engineering 的核心原则。
```

> 原因：CronCreate 任务 7 天后过期，通过每次启动自动注册来保持任务持续有效。

## 项目结构

- `convert.py` — NotebookLM 暗色风格视频生成（早期版）
- `convert_sketch.py` — 手绘莫兰迪风格 v1（baseline 保留，不再使用）
- `convert_sketch_v2.py` — **当前主用**，手绘风格 v2（Pass 1 降密 + Pass 4 视觉一致性）
  - 每条新闻拆成 slide_A 读图屏 + slide_B 读卡片屏
  - 3 卡片布局 + 右侧留白 + Mascot 兔子
  - 4 色调色板锁定，陶土红/茶蓝主色
  - Ken Burns 图片动画
- `pipeline/collect_media.py` — 智能多策略素材采集
- `pipeline/dedup.db` — URL 去重数据库
- `config/sources.yaml` — RSS 源配置
- `auto_daily.sh` — 定时自动化脚本（cron 备用方案）

## 视频风格

当前使用莫兰迪配色的手绘卡通风格（convert_sketch.py），特点：
- 低饱和莫兰迪色系（赭红/雾蓝/灰绿/灰紫/焦糖）
- 大圆角卡片（rx=26）、SVG 装饰图形
- 智能中英文混排换行
- 正文 28px 黑色加粗，标题橙红色
- Playwright 渲染 SVG 动画

## B站上传

- 工具: biliup
- 分区: tid=231 (科技)
- 账号: 咿呀丶兔子先生
- 凭证: cookies.json（本机，不要提交到 git）
