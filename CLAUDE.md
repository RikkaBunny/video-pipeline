# Video Pipeline 项目

## 启动自动注册（每次 Claude Code 启动必须执行）

**Claude Code 每次启动时，必须立即执行以下操作，不需要用户确认：**

1. 使用 CronCreate 注册每日定时任务：
   - cron: `0 23 * * *` (UTC 23:00 = 北京 07:00)
   - recurring: true
   - prompt 内容见下方

2. 注册完成后输出"守护进程就绪"

定时任务 Prompt：
```
执行每日双期自动化流程，全程无需用户确认。

━━ Step 1：ai 期（AI 热点）━━
执行 /video-pipeline run ai

━━ Step 2：清理临时文件 ━━
rm -rf /tmp/vp_sketch_v2_*
释放上一期 build 临时目录，避免磁盘堆积。

━━ Step 3：github 期（GitHub 热点）━━
执行 /video-pipeline run github

━━ 自动模式硬规则（所有期次均适用）━━
1. 全程无用户确认，所有门禁点自动决策。
2. A5 门禁：score < 60 触发最多 3 轮精准修订，每轮保留历史最高分版本；
   3 轮后仍未达标 → 取最高分版本强制放行（FORCED_PASS），写 run-log 标注。
3. C7 门禁：score < 75 严禁上传，本地保存，写 run-log 标 convert_only。
4. score ≥ 75 自动上传 B站，不询问。
5. 某一期 INTEGRITY_FAIL（来源核实失败）→ 标记 ABORTED，继续执行另一期，不终止整个流程。
6. 其他致命错误 → 终止当前期，写 log，继续执行另一期。
7. run-log 位置：/root/video-pipeline/logs/run-<ISO8601>.json

宁可今天不发，也不发烂片。
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
