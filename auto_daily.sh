#!/bin/bash
# auto_daily.sh — 每日自动生成视频并上传B站
# 用法: auto_daily.sh [ai|github]  (不传参则按日期奇偶轮换)
set -euo pipefail

cd /root/video-pipeline
DATE=$(TZ=Asia/Shanghai date +%Y-%m-%d)
DAY_NUM=$(TZ=Asia/Shanghai date +%j)  # 年内第几天
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/${DATE}.log"

exec > >(tee -a "$LOG") 2>&1
echo "============================================"
echo "  auto_daily.sh  |  $DATE  |  $(date -u)"
echo "============================================"

# 1. 确定类型：参数 > 按日期奇偶轮换
if [ -n "${1:-}" ]; then
    TYPE="$1"
else
    if (( DAY_NUM % 2 == 0 )); then
        TYPE="ai"
    else
        TYPE="github"
    fi
fi
echo "[INFO] 今日类型: $TYPE"

OUT_DIR="output/$DATE/$TYPE"
mkdir -p "$OUT_DIR/media"

# 2. 用 claude 生成图文文章
echo ""
echo "[STEP 1/4] 生成图文文章..."
claude -p "你是视频流水线自动化助手。执行以下任务，不要询问确认，直接完成：

1. 作为 video-scraper：抓取 $TYPE 类型的RSS源（参考 /root/video-pipeline/config/sources.yaml），获取最近7天内的新闻，返回JSON列表
2. 去重：检查 /root/video-pipeline/pipeline/dedup.db 排除已处理的URL
3. 作为 video-script-writer：用抓取的内容生成 article.md，严格遵循橘鸦Juya AI早报格式规范：
   - frontmatter（title用分号分隔关键事件+【AI/GitHub 早报 $DATE】, type=$TYPE, episode自增, date=$DATE）
   - 概览区2-4个分类
   - 每条新闻：H2标题#N + blockquote摘要(80-170字) + 正文段落(来源归因开头) + 来源URL(代码块) + 恰好5个要点卡片(emoji)
   - 结尾固定两行
   - 零禁用词，产品名code格式，公司名数字加粗
4. 保存到 /root/video-pipeline/$OUT_DIR/article.md
5. 为每条新闻执行: python3 /root/video-pipeline/pipeline/collect_media.py --url <URL> --out /root/video-pipeline/$OUT_DIR/media --num <N>
6. 在article.md中插入 ![](media/NN_media_og.png) 和 <!-- media_score: 3 -->
7. 将所有URL注册到 dedup.db（type=$TYPE）

完成后只输出：ARTICLE_DONE|<article.md的完整路径>" --max-turns 30 2>&1 | tee /tmp/vp_article_out.txt

ARTICLE_PATH=$(grep "ARTICLE_DONE|" /tmp/vp_article_out.txt | tail -1 | cut -d'|' -f2 | tr -d '[:space:]')

if [ -z "$ARTICLE_PATH" ] || [ ! -f "$ARTICLE_PATH" ]; then
    ARTICLE_PATH="/root/video-pipeline/$OUT_DIR/article.md"
fi

if [ ! -f "$ARTICLE_PATH" ]; then
    echo "[ERROR] 文章生成失败，未找到 $ARTICLE_PATH"
    exit 1
fi
echo "[OK] 文章: $ARTICLE_PATH"

# 3. 转换为视频（手绘风格）
echo ""
echo "[STEP 2/4] 转换视频（手绘风格）..."
python3 convert_sketch.py "$ARTICLE_PATH"
VIDEO_PATH="$(dirname "$ARTICLE_PATH")/video_sketch.mp4"

if [ ! -f "$VIDEO_PATH" ]; then
    echo "[ERROR] 视频生成失败"
    exit 1
fi
echo "[OK] 视频: $VIDEO_PATH"

# 4. 提取标题和标签
echo ""
echo "[STEP 3/4] 准备上传信息..."
TITLE=$(grep '^title:' "$ARTICLE_PATH" | head -1 | sed 's/title:\s*"\?\(.*\)"\?/\1/' | tr -d '"')
if [ ${#TITLE} -gt 80 ]; then
    TITLE="${TITLE:0:77}..."
fi

if [ "$TYPE" = "ai" ]; then
    TAGS="AI,科技,LLM,早报"
else
    TAGS="GitHub,开源,科技,开发者"
fi

DESC=$(head -5 "$ARTICLE_PATH" | grep -oP 'tags: \[.*?\]' | tr -d '[]"' || echo "AI,科技")
echo "  标题: $TITLE"
echo "  标签: $TAGS"

# 5. 上传B站
echo ""
echo "[STEP 4/4] 上传B站..."
biliup renew 2>&1 | head -2

# 提取封面
COVER=$(ls "$OUT_DIR"/media/01_media_og.png 2>/dev/null || echo "")
COVER_ARG=""
if [ -n "$COVER" ]; then
    COVER_ARG="--cover $COVER"
fi

biliup upload "$VIDEO_PATH" \
    --title "$TITLE" \
    --desc "内容由AI辅助创作，可能存在幻觉和错误。作者Bunny，欢迎点赞关注分享。" \
    --tag "$TAGS" \
    --tid 231 \
    $COVER_ARG \
    2>&1

echo ""
echo "============================================"
echo "  [DONE] $DATE $TYPE 完成"
echo "  视频: $VIDEO_PATH"
echo "  日志: $LOG"
echo "============================================"
