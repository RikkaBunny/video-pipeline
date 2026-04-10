#!/bin/bash
# start_daemon.sh — 在 tmux 中启动 Claude Code 常驻进程
# Claude Code 启动后会读取 CLAUDE.md，自动注册每日定时任务
# 用法: bash start_daemon.sh

set -euo pipefail
SESSION="claude-daemon"

# 如果已有会话，先关闭
tmux kill-session -t "$SESSION" 2>/dev/null || true

echo "启动 Claude Code 常驻会话..."
tmux new-session -d -s "$SESSION" -c /root/video-pipeline \
    "claude --allowedTools 'Bash,Read,Write,Edit,Glob,Grep,Agent,Skill,CronCreate' \
     -p '读取 CLAUDE.md 并执行其中的启动指令。' \
     --max-turns 5; \
     claude"

echo "✅ 已启动 tmux 会话: $SESSION"
echo "   查看: tmux attach -t $SESSION"
echo "   Claude Code 会在后台空闲时自动执行定时任务"
