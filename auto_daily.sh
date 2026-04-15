#!/bin/bash
# auto_daily.sh — 每日双期自动化流程（系统 cron 版）
# 挂载方式: 0 23 * * * /root/video-pipeline/auto_daily.sh >> /root/video-pipeline/logs/cron-shell.log 2>&1
#
# 本脚本调用 claude CLI 执行完整的双期流程（ai + github），
# 包含 A5/C7 门禁、FORCED_PASS、自动上传等逻辑，全程无需用户确认。

set -uo pipefail

export PATH="/root/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export HOME="/root"

cd /root/video-pipeline

DATE=$(TZ=Asia/Shanghai date +%Y-%m-%d)
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

echo "============================================"
echo "  auto_daily.sh  |  $DATE  |  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "============================================"

# 检查 claude CLI
if ! command -v claude &>/dev/null; then
    echo "[FATAL] claude CLI 未找到，退出"
    exit 1
fi

PROMPT='执行每日双期自动化流程，全程无需用户确认。

━━ Step 1：ai 期（AI 热点）━━
执行 /video-pipeline run ai

━━ Step 2：清理临时文件 ━━
删除 /tmp/vp_sketch_v2_* 目录，释放上一期 build 临时文件，避免磁盘堆积。

━━ Step 3：github 期（GitHub 热点）━━
执行 /video-pipeline run github

━━ 自动模式硬规则（所有期次均适用）━━
1. 全程无用户确认，所有门禁点自动决策。
2. A5 门禁：score < 60 触发最多 3 轮精准修订，每轮保留历史最高分版本；
   3 轮后仍未达标 → 取最高分版本强制放行（FORCED_PASS），写 run-log 标注。
3. C7 门禁：score < 60 严禁上传，本地保存，写 run-log 标 convert_only。
4. score >= 60 自动上传 B站，不询问。
5. 某一期 INTEGRITY_FAIL（来源核实失败）→ 标记 ABORTED，继续执行另一期，不终止整个流程。
6. 其他致命错误 → 终止当前期，写 log，继续执行另一期。
7. run-log 位置：/root/video-pipeline/logs/run-<ISO8601>.json

宁可今天不发，也不发烂片。'

echo "[INFO] 启动 claude CLI（settings.json: defaultMode=dontAsk）..."
claude --max-turns 120 \
       -p "$PROMPT" \
       2>&1

EXIT_CODE=$?
echo ""
echo "============================================"
echo "  [DONE] claude 退出码: $EXIT_CODE  |  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "============================================"
exit $EXIT_CODE
