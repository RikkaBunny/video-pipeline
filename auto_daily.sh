#!/bin/bash
# auto_daily.sh — 每日自动生成视频并上传B站
# 用法: auto_daily.sh [ai|github]  (不传参则按日期奇偶轮换)
set -uo pipefail

# 确保 cron 环境下也能找到所有命令
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export HOME="${HOME:-/root}"

cd /root/video-pipeline
DATE=$(TZ=Asia/Shanghai date +%Y-%m-%d)
DAY_NUM=$(TZ=Asia/Shanghai date +%j)
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/${DATE}.log"

exec > >(tee -a "$LOG") 2>&1
echo "============================================"
echo "  auto_daily.sh  |  $DATE  |  $(date -u)"
echo "============================================"

# 1. 确定类型
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
FULL_OUT="/root/video-pipeline/$OUT_DIR"
mkdir -p "$FULL_OUT/media"

# 2. 用 claude -p 生成文章（分步，更可靠）
echo ""
echo "[STEP 1/4] 生成图文文章..."

claude -p "在 /root/video-pipeline 目录下，为 $TYPE 类型生成今日($DATE)的AI早报图文文章。

具体步骤：
1. 用Python feedparser抓取 config/sources.yaml 中 $TYPE 的RSS源，获取最近7天的新闻(每个源最多5条)
2. 用sqlite3检查 pipeline/dedup.db 去重，排除已处理的URL
3. 选取5-8条最有价值的新闻
4. 生成 $FULL_OUT/article.md，格式要求：
   - YAML frontmatter: title(关键事件;关键事件【早报 $DATE】), type=$TYPE, date=$DATE
   - H1日期标题, H2概览(2-4分类), 每条H2新闻(blockquote摘要80-170字+正文+来源URL代码块+恰好5个emoji要点卡片)
   - 结尾: 提示：内容由AI辅助创作，可能存在幻觉和错误。 和 作者Bunny，视频版在同名哔哩哔哩。欢迎点赞、关注、分享。
   - 零禁用词(惊艳/颠覆/革命性/史诗级/遥遥领先), 正文首句来源归因, 产品名\`code\`, 公司名**加粗**
5. 为每条新闻运行: python3 pipeline/collect_media.py --url <URL> --out $FULL_OUT/media --num <N>
6. 在article.md对应位置插入 ![](media/NN_media_og.png) 和 <!-- media_score: 3 -->
7. 将URL注册到 pipeline/dedup.db (type=$TYPE)

不要询问确认，直接执行所有步骤。" \
    --max-turns 40 \
    2>&1 | tee /tmp/vp_article_out.txt

# 查找生成的 article.md
ARTICLE_PATH="$FULL_OUT/article.md"
if [ ! -f "$ARTICLE_PATH" ]; then
    # 尝试查找其他位置
    FOUND=$(find /root/video-pipeline/output/$DATE -name "article.md" -type f 2>/dev/null | head -1)
    if [ -n "$FOUND" ]; then
        ARTICLE_PATH="$FOUND"
    fi
fi

if [ ! -f "$ARTICLE_PATH" ]; then
    echo "[ERROR] 文章生成失败，未找到 article.md"
    echo "[DEBUG] 查找路径: $FULL_OUT/article.md"
    echo "[DEBUG] output目录内容:"
    find /root/video-pipeline/output/$DATE -type f 2>/dev/null || echo "  (空)"
    exit 1
fi
echo "[OK] 文章: $ARTICLE_PATH"

# 3. 转换为视频（手绘风格）
echo ""
echo "[STEP 2/4] 转换视频（手绘风格）..."
python3 convert_sketch.py "$ARTICLE_PATH" 2>&1

VIDEO_DIR="$(dirname "$ARTICLE_PATH")"
VIDEO_PATH="$VIDEO_DIR/video_sketch.mp4"

if [ ! -f "$VIDEO_PATH" ]; then
    echo "[ERROR] 视频生成失败，未找到 $VIDEO_PATH"
    exit 1
fi
VIDEO_SIZE=$(du -h "$VIDEO_PATH" | cut -f1)
echo "[OK] 视频: $VIDEO_PATH ($VIDEO_SIZE)"

# 4. 提取标题和标签
echo ""
echo "[STEP 3/4] 准备上传信息..."
TITLE=$(grep '^title:' "$ARTICLE_PATH" | head -1 | sed 's/title:[[:space:]]*"\{0,1\}\(.*\)"\{0,1\}/\1/' | tr -d '"')
if [ ${#TITLE} -gt 80 ]; then
    TITLE="${TITLE:0:77}..."
fi

if [ "$TYPE" = "ai" ]; then
    TAGS="AI,科技,LLM,早报"
else
    TAGS="GitHub,开源,科技,开发者"
fi
echo "  标题: $TITLE"
echo "  标签: $TAGS"

# 5. 上传B站
echo ""
echo "[STEP 4/4] 上传B站..."
biliup renew 2>&1 | head -2 || echo "[WARN] biliup renew 失败，继续尝试上传"

COVER=$(ls "$VIDEO_DIR"/media/01_media_og.png 2>/dev/null || echo "")
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
echo "  文章: $ARTICLE_PATH"
echo "  视频: $VIDEO_PATH"
echo "  日志: $LOG"
echo "============================================"
