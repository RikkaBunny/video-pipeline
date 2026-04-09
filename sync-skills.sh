#!/bin/bash
# sync-skills.sh — 从 GitHub 同步最新 skill 到 Claude Code
set -e

cd "$(dirname "$0")"

echo "🔄 Pulling latest from GitHub..."
git pull origin main

echo "📋 Syncing skills to ~/.claude/commands/..."
cp .claude/commands/*.md ~/.claude/commands/

echo ""
echo "✅ Skills synced:"
for f in .claude/commands/*.md; do
  name=$(basename "$f")
  echo "   ~/.claude/commands/$name"
done
