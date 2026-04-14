#!/usr/bin/env python3
"""
collect_media.py — 智能媒体素材采集器

用法：
  python3 collect_media.py --url <URL> --out <输出目录> --num <编号> [--type github|article|twitter|research|hn]

返回 JSON：
  {"path": "/abs/path/to/file", "score": 6, "desc": "README 演示 GIF", "type": "gif"}

策略（按优先级）：
  GitHub repo → README GIF(6) → README 视频/YouTube(9) → README 首图(3) → opengraph(3)
  Twitter/X   → Playwright 截图推文(3)
  YouTube     → yt-dlp 下载最低画质(9) 或封面图(3)
  Research    → 页面首图 → Playwright 截图(3)
  文章/博客   → OG image(3) → 首张内容图(3) → Playwright 截图(3)
"""

import re, sys, os, json, subprocess, argparse, hashlib, time
import urllib.request, urllib.parse, urllib.error
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ── HTTP 工具 ─────────────────────────────────────────────────────────────────

def http_get(url, timeout=15, headers=None, max_retries=3):
    h = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
         "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            # 4xx 客户端错误不重试
            if 400 <= e.code < 500:
                return None
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
        except (urllib.error.URLError, OSError):
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
        except Exception:
            return None
    return None

def download_to(url, path, min_bytes=5000, timeout=20, max_retries=3):
    """下载文件到 path，返回 True/False，并验证大小。带指数退避重试。"""
    for attempt in range(max_retries):
        data = http_get(url, timeout=timeout, max_retries=1)
        if data and len(data) >= min_bytes:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            return True
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)
    return False

def fetch_html(url):
    data = http_get(url)
    return data.decode("utf-8", errors="ignore") if data else ""

# ── 解析工具 ──────────────────────────────────────────────────────────────────

def extract_og_image(html):
    """兼容 property= 和 name= 两种写法，以及 content 前置/后置。"""
    patterns = [
        r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\'](https?://[^"\'?]+[^"\']*)["\']',
        r'<meta[^>]+content=["\'](https?://[^"\'?]+[^"\']*)["\'][^>]+(?:property|name)=["\']og:image["\']',
        r'og:image.*?content=["\'](https?://[^"\'?]+)',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.I | re.DOTALL)
        if m:
            url = m.group(1).split("?")[0]  # 去掉 query string
            if url.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                return url
            return m.group(1)  # 保留原始（含参数的 CDN 链接）
    return None

def extract_first_img(html, base_url=""):
    """提取 HTML 中第一张有意义的 <img>（宽高暗示 > 200px 或 URL 含 cover/banner/hero/preview）"""
    imgs = re.findall(r'<img[^>]+src=["\'](https?://[^"\']+\.(?:png|jpg|jpeg|webp|gif))["\']', html, re.I)
    priority_kw = ['cover', 'banner', 'hero', 'preview', 'thumbnail', 'feature', 'og', 'social']
    for img in imgs:
        if any(kw in img.lower() for kw in priority_kw):
            return img
    return imgs[0] if imgs else None

def is_github_repo_url(url):
    m = re.match(r'https?://github\.com/([^/]+)/([^/?#]+)', url)
    return (m.group(1), m.group(2)) if m else None

def is_twitter_url(url):
    return bool(re.match(r'https?://(twitter|x)\.com/', url))

def is_youtube_url(url):
    return bool(re.match(r'https?://(www\.)?(youtube\.com/watch|youtu\.be/)', url))

def is_arxiv_url(url):
    return bool(re.match(r'https?://arxiv\.org/abs/', url))

# ── GitHub 策略 ───────────────────────────────────────────────────────────────

GITHUB_API = "https://api.github.com"

def github_api_get(path):
    data = http_get(f"{GITHUB_API}{path}", headers={"Accept": "application/vnd.github.v3+json"})
    if data:
        try:
            return json.loads(data)
        except Exception:
            pass
    return None

def fetch_readme_content(owner, repo):
    """获取 README 原始文本，尝试 main/master 分支。"""
    for branch in ("main", "master", "HEAD"):
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
        data = http_get(url)
        if data:
            return data.decode("utf-8", errors="ignore")
    # 尝试 API
    info = github_api_get(f"/repos/{owner}/{repo}/readme")
    if info and info.get("download_url"):
        data = http_get(info["download_url"])
        if data:
            return data.decode("utf-8", errors="ignore")
    return ""

def resolve_readme_url(raw_url, owner, repo, branch="main"):
    """将 README 中的相对路径转换为绝对 URL。"""
    if raw_url.startswith("http"):
        return raw_url
    if raw_url.startswith("/"):
        return f"https://github.com/{owner}/{repo}/blob/{branch}{raw_url}?raw=true"
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{raw_url}"

def collect_github(owner, repo, out_dir, num):
    readme = fetch_readme_content(owner, repo)
    if not readme:
        return None

    # 1. 查找演示 GIF（优先级最高，6分）
    gif_urls = re.findall(r'!\[[^\]]*\]\(((?:https?://[^\)]+|[^\)]+)\.gif[^\)]*)\)', readme, re.I)
    gif_urls += re.findall(r'src=["\']((?:https?://[^\'"]+|[^\'"]+)\.gif)["\']', readme, re.I)
    for branch in ("main", "master"):
        for raw in gif_urls[:5]:
            url = resolve_readme_url(raw.split("?")[0], owner, repo, branch)
            # 跳过 badges（小图标）
            if any(kw in url.lower() for kw in ["badge", "shield", "travis", "circle", "codecov", "npm"]):
                continue
            out = str(Path(out_dir) / f"{num:02d}_media.gif")
            if download_to(url, out, min_bytes=10000):
                print(f"  [collect] GIF found: {url[:80]}", file=sys.stderr)
                return {"path": out, "score": 6, "desc": f"README 演示 GIF ({owner}/{repo})", "type": "gif"}

    # 2. 查找 YouTube 视频（9分）
    yt_ids = re.findall(r'(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{11})', readme)
    if yt_ids:
        vid_id = yt_ids[0]
        # 先尝试下载封面图（3分）作为保底
        thumb_url = f"https://img.youtube.com/vi/{vid_id}/maxresdefault.jpg"
        out_thumb = str(Path(out_dir) / f"{num:02d}_media_yt_thumb.jpg")
        if download_to(thumb_url, out_thumb, min_bytes=5000):
            # 再尝试用 yt-dlp 下载最低画质视频（9分）
            out_vid = str(Path(out_dir) / f"{num:02d}_media_yt.mp4")
            r = subprocess.run(
                ["yt-dlp", "-f", "worstvideo[ext=mp4]+worstaudio/worst[ext=mp4]/worst",
                 "--max-filesize", "30m", "-o", out_vid,
                 f"https://www.youtube.com/watch?v={vid_id}"],
                capture_output=True, timeout=60
            )
            if r.returncode == 0 and Path(out_vid).exists() and Path(out_vid).stat().st_size > 10000:
                print(f"  [collect] YouTube video: {vid_id}", file=sys.stderr)
                return {"path": out_vid, "score": 9, "desc": f"README YouTube 演示视频 ({owner}/{repo})", "type": "video"}
            print(f"  [collect] YouTube thumbnail: {vid_id}", file=sys.stderr)
            return {"path": out_thumb, "score": 3, "desc": f"YouTube 封面图 ({owner}/{repo})", "type": "image"}

    # 3. 查找 README 中 mp4 直链（9分）
    mp4_urls = re.findall(r'https?://[^\s\'"<>]+\.mp4', readme, re.I)
    for url in mp4_urls[:3]:
        if any(kw in url.lower() for kw in ["badge", "icon"]):
            continue
        out = str(Path(out_dir) / f"{num:02d}_media.mp4")
        if download_to(url, out, min_bytes=20000, timeout=30):
            print(f"  [collect] mp4: {url[:80]}", file=sys.stderr)
            return {"path": out, "score": 9, "desc": f"README 演示视频 ({owner}/{repo})", "type": "video"}

    # 4. 查找 README 中高质量静态图（非 badge）（3分）
    img_urls = re.findall(r'!\[[^\]]*\]\((https?://[^\)]+\.(?:png|jpg|jpeg|webp)(?:\?[^\)]*)?)\)', readme, re.I)
    img_urls += re.findall(r'src=["\'](https?://[^\'"]+\.(?:png|jpg|jpeg|webp))["\']', readme, re.I)
    for raw in img_urls[:8]:
        url = raw.split(" ")[0]  # 去掉 title
        if any(kw in url.lower() for kw in ["badge", "shield", "travis", "circle", "codecov", "npm", "icon"]):
            continue
        out = str(Path(out_dir) / f"{num:02d}_media_readme.png")
        if download_to(url, out, min_bytes=5000):
            print(f"  [collect] README img: {url[:80]}", file=sys.stderr)
            return {"path": out, "score": 3, "desc": f"README 内嵌图片 ({owner}/{repo})", "type": "image"}

    # 5. Fallback: opengraph 社交预览图（3分）
    og_url = f"https://opengraph.github.com/repo/{owner}/{repo}"
    out = str(Path(out_dir) / f"{num:02d}_media_og.png")
    if download_to(og_url, out, min_bytes=5000):
        print(f"  [collect] opengraph: {owner}/{repo}", file=sys.stderr)
        return {"path": out, "score": 3, "desc": f"GitHub 社交预览图 ({owner}/{repo})", "type": "image"}

    return None

# ── YouTube 独立策略 ──────────────────────────────────────────────────────────

def collect_youtube(url, out_dir, num):
    vid_id_m = re.search(r'(?:v=|youtu\.be/)([\w-]{11})', url)
    if not vid_id_m:
        return None
    vid_id = vid_id_m.group(1)

    out_vid = str(Path(out_dir) / f"{num:02d}_media_yt.mp4")
    r = subprocess.run(
        ["yt-dlp", "-f", "worstvideo[ext=mp4]+worstaudio/worst[ext=mp4]/worst",
         "--max-filesize", "30m", "-o", out_vid, url],
        capture_output=True, timeout=90
    )
    if r.returncode == 0 and Path(out_vid).exists():
        return {"path": out_vid, "score": 9, "desc": "YouTube 演示视频", "type": "video"}

    # Fallback: thumbnail
    thumb_url = f"https://img.youtube.com/vi/{vid_id}/maxresdefault.jpg"
    out = str(Path(out_dir) / f"{num:02d}_media_yt_thumb.jpg")
    if download_to(thumb_url, out, min_bytes=5000):
        return {"path": out, "score": 3, "desc": "YouTube 封面图", "type": "image"}
    return None

# ── 通用文章/博客策略 ─────────────────────────────────────────────────────────

def collect_article(url, out_dir, num):
    html = fetch_html(url)
    if not html:
        return None

    # 1. OG image
    og = extract_og_image(html)
    if og:
        out = str(Path(out_dir) / f"{num:02d}_media_og.png")
        if download_to(og, out, min_bytes=5000):
            print(f"  [collect] OG image: {og[:80]}", file=sys.stderr)
            return {"path": out, "score": 3, "desc": f"文章 OG 封面图", "type": "image"}

    # 2. 首张内容图
    first_img = extract_first_img(html, url)
    if first_img:
        ext = Path(urllib.parse.urlparse(first_img).path).suffix.lower() or ".jpg"
        out = str(Path(out_dir) / f"{num:02d}_media_img{ext}")
        if download_to(first_img, out, min_bytes=5000):
            print(f"  [collect] first img: {first_img[:80]}", file=sys.stderr)
            return {"path": out, "score": 3, "desc": "文章内嵌首图", "type": "image"}

    return None

# ── arXiv 策略 ────────────────────────────────────────────────────────────────

def collect_arxiv(url, out_dir, num):
    arxiv_id_m = re.search(r'arxiv\.org/abs/([0-9.v]+)', url)
    if not arxiv_id_m:
        return None
    arxiv_id = arxiv_id_m.group(1)

    # 1. 尝试 HTML 版本，提取第一张论文 figure 图
    html_url = f"https://arxiv.org/html/{arxiv_id}"
    html2 = fetch_html(html_url)
    if html2:
        fig_imgs = re.findall(r'<img[^>]+src=["\'](/html/[^"\']+\.(?:png|jpg))["\']', html2)
        for fi in fig_imgs[:5]:
            fig_url = f"https://arxiv.org{fi}"
            out = str(Path(out_dir) / f"{num:02d}_media_fig.png")
            if download_to(fig_url, out, min_bytes=3000):
                print(f"  [collect] arxiv figure: {fig_url[:80]}", file=sys.stderr)
                return {"path": out, "score": 3, "desc": "论文配图（arXiv HTML）", "type": "image"}

    # 2. 抓取 abs 页 OG 图（semantic scholar 或 arxiv 有时提供）
    html = fetch_html(url)
    if html:
        og = extract_og_image(html)
        if og:
            out = str(Path(out_dir) / f"{num:02d}_media_og.png")
            if download_to(og, out, min_bytes=5000):
                return {"path": out, "score": 3, "desc": "arXiv OG 图", "type": "image"}

    # 3. Semantic Scholar 搜索封面图
    ss_url = f"https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}?fields=openAccessPdf,tldr"
    ss_data = http_get(ss_url)
    if ss_data:
        try:
            ss_json = json.loads(ss_data)
            pdf = ss_json.get("openAccessPdf", {}) or {}
            if pdf.get("url"):
                # 有 PDF 但不下载，改为取截图占位 → 交给 Playwright
                pass
        except Exception:
            pass

    return None

# ── Twitter/X 策略（需调用方用 Playwright 截图，此处返回标记）────────────────

def collect_twitter(url, out_dir, num):
    """返回一个特殊标记，告知调用方需要用 Playwright 截图推文。"""
    # 尝试 nitter 镜像（无需登录）
    nitter_url = url.replace("twitter.com", "nitter.net").replace("x.com", "nitter.net")
    html = fetch_html(nitter_url)
    if html:
        og = extract_og_image(html)
        if og and "avatar" not in og.lower():
            out = str(Path(out_dir) / f"{num:02d}_media_tweet.png")
            if download_to(og, out, min_bytes=5000):
                return {"path": out, "score": 3, "desc": "推文 OG 图", "type": "image"}

    # 标记需要 Playwright（调用方处理）
    return {"path": None, "score": 0, "desc": "需 Playwright 截图推文", "type": "playwright_needed",
            "playwright_url": url}

# ── 验证素材质量 ──────────────────────────────────────────────────────────────

def normalize_image(path):
    """把图片规范化为 RGB PNG（透明背景压白）。返回 (ok, reason)。
    避免渲染端把 RGBA 透明像素当成黑色 → 视频里出现纯黑色块。
    同时拒绝近乎单色的图（透明 logo flatten 后还是单色的 / 真黑图 / 真白图也不行）。
    """
    try:
        from PIL import Image, ImageStat
    except Exception as e:
        return False, f"PIL unavailable: {e}"
    p = Path(path)
    try:
        img = Image.open(str(p))
        img.load()
    except Exception as e:
        return False, f"open failed: {e}"
    # 含 alpha → 白底 flatten
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    # 单色检测：三通道 stddev 全 < 6 视为近乎纯色
    stat = ImageStat.Stat(img)
    if max(stat.stddev) < 6:
        return False, f"near-monochrome stddev={[round(x,1) for x in stat.stddev]} mean={[round(x,1) for x in stat.mean]}"
    img.save(str(p), "PNG", optimize=True)
    return True, "ok"


def validate_media(result):
    """验证素材：文件存在、大小合理、尺寸够大、规范化为 RGB、不是纯色。"""
    if not result or not result.get("path"):
        return False
    p = Path(result["path"])
    if not p.exists():
        return False
    size = p.stat().st_size
    if size < 4000:
        print(f"  [validate] FAIL too small: {size}B", file=sys.stderr)
        return False
    # 图片：尺寸 + 规范化（透明 flatten）+ 单色检测
    if result.get("type") == "image":
        try:
            from PIL import Image
            img = Image.open(str(p))
            w, h = img.size
            if w < 150 or h < 100:
                print(f"  [validate] FAIL too tiny: {w}x{h}", file=sys.stderr)
                return False
        except Exception:
            return True  # 非图片格式（gif/mp4 允许）
        ok, why = normalize_image(p)
        if not ok:
            print(f"  [validate] FAIL {why}: {p.name}", file=sys.stderr)
            return False
    return True


def check_dir(media_dir):
    """目录级体检：扫描 <dir> 下所有 *_media_*.{png,jpg,jpeg,webp}。
    对每张图执行 normalize_image，输出报告。返回坏图数量。
    供 skill 在素材采集后做最终门禁。"""
    d = Path(media_dir)
    if not d.exists():
        print(f"[check] dir not found: {d}", file=sys.stderr)
        return 1
    bad = 0
    files = sorted([p for p in d.iterdir()
                    if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
                    and "_media_" in p.name])
    for p in files:
        size = p.stat().st_size
        if size < 4000:
            print(f"  ✗ {p.name}  too small {size}B")
            bad += 1
            continue
        ok, why = normalize_image(p)
        if ok:
            print(f"  ✓ {p.name}")
        else:
            print(f"  ✗ {p.name}  {why}")
            bad += 1
    print(f"[check] {len(files)-bad}/{len(files)} pass, {bad} bad")
    return bad

# ── 主入口 ────────────────────────────────────────────────────────────────────

def collect(url, out_dir, num, hint_type=None):
    """
    根据 URL 自动选择最佳策略采集素材。
    hint_type: 'github'|'article'|'twitter'|'youtube'|'research'|None（自动检测）
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    results = []

    # 自动检测类型
    gh = is_github_repo_url(url)
    if gh or hint_type == "github":
        owner, repo = gh if gh else (None, None)
        if owner:
            r = collect_github(owner, repo, out_dir, num)
            if r and validate_media(r):
                results.append(r)

    if is_youtube_url(url) or hint_type == "youtube":
        r = collect_youtube(url, out_dir, num)
        if r and validate_media(r):
            results.append(r)

    if is_twitter_url(url) or hint_type == "twitter":
        r = collect_twitter(url, out_dir, num)
        if r:
            results.append(r)

    if is_arxiv_url(url) or hint_type == "research":
        r = collect_arxiv(url, out_dir, num)
        if r and validate_media(r):
            results.append(r)

    # 通用文章兜底
    if not results or hint_type == "article":
        r = collect_article(url, out_dir, num)
        if r and validate_media(r):
            results.append(r)

    if not results:
        return {"path": None, "score": 0, "desc": "未找到可用素材", "type": None}

    # 取最高分
    best = max(results, key=lambda x: x.get("score", 0))
    return best


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=False)
    parser.add_argument("--out", required=False, help="输出目录")
    parser.add_argument("--num", type=int, default=1, help="新闻编号（用于文件名）")
    parser.add_argument("--type", dest="hint_type", default=None,
                        choices=["github", "article", "twitter", "youtube", "research"])
    parser.add_argument("--check-dir", dest="check_dir_path", default=None,
                        help="只对该目录内已有素材做体检（透明 flatten + 单色检测），坏图返回非零退出码")
    args = parser.parse_args()

    if args.check_dir_path:
        bad = check_dir(args.check_dir_path)
        sys.exit(1 if bad else 0)

    if not args.url or not args.out:
        parser.error("--url and --out are required (unless --check-dir)")
    result = collect(args.url, args.out, args.num, args.hint_type)
    print(json.dumps(result, ensure_ascii=False))
