#!/bin/bash
# auto_daily_v2.sh — 每日双期自动化流程 · v2 专用版
#
# v1 归档：/root/video-pipeline/_archive/auto_daily.v1.sh
#
# 挂载方式:
#   0 23 * * * /root/video-pipeline/auto_daily_v2.sh >> /root/video-pipeline/logs/cron-v2.log 2>&1
#
# 本脚本调用 claude CLI 执行 v2 双期流程（ai + github），走：
#   video-scraper → video-script-writer（产 article.md + visual_beats.json）
#     → /video-score A5 门禁 → convert_hyperframes.py → /video-score C7 门禁
#     → biliup 上传
#
# 告警：在 .env 设 VP_ALERT_BARK_URL 或 VP_ALERT_WEBHOOK 启用，未设静默跳过。
#
# v1 差异：
#   - 引用 v2 slash 命令（命令名没变，内容已替换）
#   - 移除 /tmp/vp_sketch_v2_* 清理（v2 不产这种 tmp 目录）
#   - 日志文件名：cron-v2.log（与 v1 cron-shell.log 物理分离，方便对比）
#   - run-log 验收点新增 visual_beats.json 存在性检查

set -uo pipefail

export PATH="/root/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export HOME="/root"

cd /root/video-pipeline

# .env：VP_ALERT_* / VP_RUN_TIMEOUT 等敏感配置
if [[ -f /root/video-pipeline/.env ]]; then
    set -a
    # shellcheck disable=SC1091
    source /root/video-pipeline/.env
    set +a
fi

DATE=$(TZ=Asia/Shanghai date +%Y-%m-%d)
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

# 看门狗：v2 draft 画质一期 ~15 min，双期 + 上传 ~40-50 min，给 90 min 余量
TIMEOUT_SECONDS="${VP_RUN_TIMEOUT:-5400}"

# ── 轻量日志轮转 ───────────────────────────────────────
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
echo "  auto_daily_v2.sh  |  $DATE  |  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  pipeline=v2 · HyperFrames · 云扬 · 10 类 B-roll"
echo "  timeout=${TIMEOUT_SECONDS}s"
echo "============================================"

# 检查 claude CLI
if ! command -v claude &>/dev/null; then
    echo "[FATAL] claude CLI 未找到，退出"
    exit 1
fi

# 检查 v2 核心文件
for f in /root/video-pipeline/convert_hyperframes.py \
         /root/video-pipeline/.claude/commands/video-pipeline.md \
         /root/.claude/agents/video-script-writer.md \
         /root/video-pipeline/hyperframes/hyperframes.json; do
    if [[ ! -f "$f" ]]; then
        echo "[FATAL] v2 核心文件缺失: $f"
        exit 1
    fi
done
echo "[OK] v2 核心文件全部就位"

send_alert() {
    local title="$1"; local body="$2"
    if [[ -n "${VP_ALERT_BARK_URL:-}" ]]; then
        local et eb
        et=$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$title")
        eb=$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$body")
        curl -fsS --max-time 10 \
            "${VP_ALERT_BARK_URL%/}/${et}/${eb}?group=video-pipeline-v2&isArchive=1" \
            >/dev/null 2>&1 && echo "[ALERT] Bark 已发送" || echo "[ALERT] Bark 发送失败（忽略）"
    fi
    if [[ -n "${VP_ALERT_WEBHOOK:-}" ]]; then
        curl -fsS --max-time 10 -H "Content-Type: application/json" \
            -X POST "$VP_ALERT_WEBHOOK" \
            -d "$(python3 -c 'import json,sys;print(json.dumps({"title":sys.argv[1],"text":sys.argv[2]}))' "$title" "$body")" \
            >/dev/null 2>&1 && echo "[ALERT] Webhook 已发送" || echo "[ALERT] Webhook 发送失败（忽略）"
    fi
    if [[ -z "${VP_ALERT_BARK_URL:-}" && -z "${VP_ALERT_WEBHOOK:-}" ]]; then
        echo "[ALERT] 未配置告警通道，跳过（title=$title）"
    fi
}

# v2 专用 PROMPT：强调 visual_beats.json 双产出
PROMPT='执行每日双期自动化流程（v2 HyperFrames 管线），全程无需用户确认。

━━ Step 1：ai 期（AI 热点）━━
执行 /video-pipeline run ai

━━ Step 2：github 期（GitHub 热点）━━
执行 /video-pipeline run github

━━ v2 关键产物 ━━
每期完成时应有以下文件：
  output/<date>/<type>/article.md          （video-script-writer 产）
  output/<date>/<type>/visual_beats.json   （video-script-writer 产，6-8 beat × N 条新闻）
  output/<date>/<type>/video_hyperframes.mp4 （convert_hyperframes.py 产）

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

echo "[INFO] 启动 claude CLI..."
timeout --signal=TERM --kill-after=60s "${TIMEOUT_SECONDS}s" \
    claude --max-turns 120 -p "$PROMPT" 2>&1
EXIT_CODE=$?

echo ""
echo "============================================"
echo "  [claude exit] code=$EXIT_CODE  |  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "============================================"

TIMED_OUT=0
if [[ $EXIT_CODE -eq 124 || $EXIT_CODE -eq 137 ]]; then
    TIMED_OUT=1
    echo "[WARN] 看门狗触发，claude 被终止（>${TIMEOUT_SECONDS}s）"
fi

# run-log 验收（v2 新增 visual_beats.json 检查）
VERIFY_OUTPUT=$(
    DATE="$DATE" TIMED_OUT="$TIMED_OUT" EXIT_CODE="$EXIT_CODE" \
    python3 <<'PY'
import json, os, pathlib, datetime
from zoneinfo import ZoneInfo

log_dir = pathlib.Path("/root/video-pipeline/logs")
output_root = pathlib.Path("/root/video-pipeline/output")
today = os.environ["DATE"]
timed_out = os.environ["TIMED_OUT"] == "1"
exit_code = int(os.environ["EXIT_CODE"])
tz = ZoneInfo("Asia/Shanghai")

def mtime_day(p):
    return datetime.datetime.fromtimestamp(p.stat().st_mtime, tz).strftime("%Y-%m-%d")

candidates = sorted(log_dir.glob("run-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
run_log = next((p for p in candidates if mtime_day(p) == today), None)

report = {
    "run_log": str(run_log) if run_log else None,
    "timed_out": timed_out,
    "claude_exit": exit_code,
    "episodes": [],
    "v2_artifacts": {},  # 新增：每期的 v2 产物对账
    "failures": [],
    "ok": False,
}

# v2 产物对账
today_dir = output_root / today
for ep_type in ("ai", "github"):
    ep_dir = today_dir / ep_type
    artifacts = {
        "article_md": (ep_dir / "article.md").exists(),
        "visual_beats_json": (ep_dir / "visual_beats.json").exists(),
        "video_mp4": (ep_dir / "video_hyperframes.mp4").exists(),
    }
    report["v2_artifacts"][ep_type] = artifacts
    # visual_beats.json 缺失 = 降级到 4-beat fallback，算弱警告而不是致命
    if not artifacts["visual_beats_json"] and artifacts["article_md"]:
        report["failures"].append(f"{ep_type}: visual_beats.json 缺失（走 4-beat fallback，视觉降级）")

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
    TITLE="video-pipeline-v2 FAILED $DATE"
    BODY=$(python3 -c '
import json, sys
r = json.loads(sys.argv[1])
lines = []
lines.append(f"episodes: {r[\"episodes\"]}")
lines.append(f"v2_artifacts: {r[\"v2_artifacts\"]}")
lines.append(f"failures: {r[\"failures\"]}")
lines.append(f"run_log: {r[\"run_log\"]}")
print(" | ".join(lines))
' "$VERIFY_OUTPUT")
    send_alert "$TITLE" "$BODY"
    [[ $EXIT_CODE -eq 0 ]] && EXIT_CODE=2
fi

echo "============================================"
echo "  [DONE] exit=$EXIT_CODE  |  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "============================================"
exit $EXIT_CODE
