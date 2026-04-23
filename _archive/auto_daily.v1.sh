#!/bin/bash
# auto_daily.sh — 每日双期自动化流程（系统 cron 版）
# 挂载方式: 0 23 * * * /root/video-pipeline/auto_daily.sh >> /root/video-pipeline/logs/cron-shell.log 2>&1
#
# 本脚本调用 claude CLI 执行完整的双期流程（ai + github），
# 包含 A5/C7 门禁、FORCED_PASS、自动上传等逻辑，全程无需用户确认。
#
# 告警：在 /root/video-pipeline/.env 里设置以下任一变量即可启用失败告警（可选）：
#   VP_ALERT_BARK_URL=https://api.day.app/<你的 key>/
#   VP_ALERT_WEBHOOK=https://...   # 通用 POST，body={"title":..,"text":..}
# 未设置时静默跳过，不影响主流程。

set -uo pipefail

export PATH="/root/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export HOME="/root"

cd /root/video-pipeline

# 可选 .env：用于 VP_ALERT_* 等敏感变量
if [[ -f /root/video-pipeline/.env ]]; then
    set -a
    # shellcheck disable=SC1091
    source /root/video-pipeline/.env
    set +a
fi

DATE=$(TZ=Asia/Shanghai date +%Y-%m-%d)
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

# 看门狗超时（秒）：ai + github 双期 + 两次 upload 的经验上限 ~30min，给 90min 余量
TIMEOUT_SECONDS="${VP_RUN_TIMEOUT:-5400}"

# ── 轻量日志轮转（宿主无 logrotate）─────────────────
# 规则：.log 超过 20MB → 重命名为 .log.YYYYMMDD_HHMMSS → gzip；28 天前的 .gz 清掉
rotate_logs() {
    local dir="$1"
    [[ -d "$dir" ]] || return 0
    find "$dir" -maxdepth 1 -type f -name "*.log" -size +20M -print0 2>/dev/null \
        | while IFS= read -r -d '' f; do
            local ts
            ts=$(date +%Y%m%d_%H%M%S)
            mv "$f" "${f}.${ts}" && : > "$f"
            gzip -f "${f}.${ts}" 2>/dev/null || true
            echo "[ROTATE] $f → ${f}.${ts}.gz"
          done
    find "$dir" -maxdepth 1 -type f -name "*.log.*.gz" -mtime +28 -delete 2>/dev/null || true
}
rotate_logs "$LOG_DIR"
rotate_logs "/root/video-pipeline"

echo "============================================"
echo "  auto_daily.sh  |  $DATE  |  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  timeout=${TIMEOUT_SECONDS}s"
echo "============================================"

# 检查 claude CLI
if ! command -v claude &>/dev/null; then
    echo "[FATAL] claude CLI 未找到，退出"
    exit 1
fi

send_alert() {
    local title="$1"
    local body="$2"

    if [[ -n "${VP_ALERT_BARK_URL:-}" ]]; then
        # Bark: URL 末尾应带 /，后接 title/body
        local encoded_title encoded_body
        encoded_title=$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$title")
        encoded_body=$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$body")
        curl -fsS --max-time 10 \
            "${VP_ALERT_BARK_URL%/}/${encoded_title}/${encoded_body}?group=video-pipeline&isArchive=1" \
            >/dev/null 2>&1 \
            && echo "[ALERT] Bark 已发送" \
            || echo "[ALERT] Bark 发送失败（忽略）"
    fi

    if [[ -n "${VP_ALERT_WEBHOOK:-}" ]]; then
        curl -fsS --max-time 10 \
            -H "Content-Type: application/json" \
            -X POST "$VP_ALERT_WEBHOOK" \
            -d "$(python3 -c 'import json,sys;print(json.dumps({"title":sys.argv[1],"text":sys.argv[2]}))' "$title" "$body")" \
            >/dev/null 2>&1 \
            && echo "[ALERT] Webhook 已发送" \
            || echo "[ALERT] Webhook 发送失败（忽略）"
    fi

    if [[ -z "${VP_ALERT_BARK_URL:-}" && -z "${VP_ALERT_WEBHOOK:-}" ]]; then
        echo "[ALERT] 未配置告警通道，跳过（title=$title）"
    fi
}

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
timeout --signal=TERM --kill-after=60s "${TIMEOUT_SECONDS}s" \
    claude --max-turns 120 -p "$PROMPT" 2>&1
EXIT_CODE=$?

echo ""
echo "============================================"
echo "  [claude exit] code=$EXIT_CODE  |  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "============================================"

# 超时退出码 124（TERM）/137（KILL）
TIMED_OUT=0
if [[ $EXIT_CODE -eq 124 || $EXIT_CODE -eq 137 ]]; then
    TIMED_OUT=1
    echo "[WARN] 看门狗触发，claude 被终止（>${TIMEOUT_SECONDS}s）"
fi

# 解析今日 run-log，判断双期上传状况
VERIFY_OUTPUT=$(
    DATE="$DATE" TIMED_OUT="$TIMED_OUT" EXIT_CODE="$EXIT_CODE" \
    python3 <<'PY'
import json, os, pathlib, datetime
from zoneinfo import ZoneInfo

log_dir = pathlib.Path("/root/video-pipeline/logs")
today = os.environ["DATE"]  # Asia/Shanghai 日期
timed_out = os.environ["TIMED_OUT"] == "1"
exit_code = int(os.environ["EXIT_CODE"])
tz = ZoneInfo("Asia/Shanghai")

def mtime_day(p):
    return datetime.datetime.fromtimestamp(p.stat().st_mtime, tz).strftime("%Y-%m-%d")

# run-*.json 的文件名是 UTC 时间戳，cron 触发点正好跨日；
# 用 mtime 在 Asia/Shanghai 下的日期做匹配更稳。
candidates = sorted(log_dir.glob("run-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
run_log = next((p for p in candidates if mtime_day(p) == today), None)

report = {
    "run_log": str(run_log) if run_log else None,
    "timed_out": timed_out,
    "claude_exit": exit_code,
    "episodes": [],
    "failures": [],
    "ok": False,
}

if run_log is None:
    report["failures"].append("今日无 run-log 产出")
else:
    try:
        data = json.loads(run_log.read_text(encoding="utf-8"))
    except Exception as e:
        report["failures"].append(f"run-log 解析失败: {e}")
    else:
        episodes = data.get("episodes", [])
        for ep in episodes:
            t = ep.get("type", "?")
            action = ep.get("final_action", "unknown")
            score = ep.get("final_score")
            report["episodes"].append({"type": t, "action": action, "score": score})
            if action != "uploaded":
                report["failures"].append(f"{t} 未上传（action={action}, score={score}）")
        if not episodes:
            report["failures"].append("run-log 中 episodes 为空")

if timed_out:
    report["failures"].append(f"看门狗超时（claude_exit={exit_code}）")
elif exit_code != 0:
    report["failures"].append(f"claude 非零退出（exit={exit_code}）")

report["ok"] = (not report["failures"])
print(json.dumps(report, ensure_ascii=False))
PY
)

echo "[VERIFY] $VERIFY_OUTPUT"

VERIFY_OK=$(python3 -c 'import json,sys;print("1" if json.loads(sys.argv[1])["ok"] else "0")' "$VERIFY_OUTPUT")

if [[ "$VERIFY_OK" != "1" ]]; then
    TITLE="video-pipeline FAILED $DATE"
    BODY=$(python3 -c '
import json, sys
r = json.loads(sys.argv[1])
lines = []
lines.append(f"episodes: {r[\"episodes\"]}")
lines.append(f"failures: {r[\"failures\"]}")
lines.append(f"run_log: {r[\"run_log\"]}")
print(" | ".join(lines))
' "$VERIFY_OUTPUT")
    send_alert "$TITLE" "$BODY"
    # 保持非零退出，便于 cron 邮件/监控识别
    [[ $EXIT_CODE -eq 0 ]] && EXIT_CODE=2
fi

echo "============================================"
echo "  [DONE] exit=$EXIT_CODE  |  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "============================================"
exit $EXIT_CODE
