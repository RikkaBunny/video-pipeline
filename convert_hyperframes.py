#!/usr/bin/env python3
"""
convert_hyperframes.py — v2 视觉管线（10 类 B-roll 组件库）

输入  : article.md + visual_beats.json（可选）
输出  : <article 同目录>/video_hyperframes.mp4

Pipeline:
  1) parse_article        → Episode { news[] }
  2) load_visual_beats    → visual_beats.json（缺失则退化 4-beat 模板）
  3) detect_logos         → simple-icons 拉 company + tool SVG
  4) gen_tts              → edge-tts (云扬) intro / news_NN / outro
  5) whisper_align        → whisper-cpp medium char-level
  6) allocate beats       → weight 归一 + MIN/MAX clamp
  7) render 10 beat types → logo-hero / wordmark / metric-cards / codeblock /
                            tools-cascade / mockup / glyphs / stat-hero /
                            timeline / image-hero
  8) HyperFrames lint + render → mp4

视觉规范：Apple 冷白 #FBFBFD / Inter 900 / coral #E45A45 / 小兔播报
CSS 与 v4_reference.html 保持像素级一致。
"""

from __future__ import annotations
import re, sys, json, asyncio, subprocess, shutil, hashlib
from pathlib import Path
from dataclasses import dataclass, field
from urllib.request import urlretrieve

# ── Configuration ─────────────────────────────────────────────────────────
PROJECT       = Path("/root/video-pipeline/hyperframes")
WHISPER_BIN   = "whisper-cpp"
WHISPER_MODEL = "/opt/whisper.cpp/models/ggml-medium.bin"
LOGO_CDN      = "https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/{slug}.svg"
TTS_VOICE_DEFAULT = "zh-CN-YunyangNeural"
TTS_VOICE_ALT     = "zh-CN-YunjianNeural"

MIN_BEAT_DUR = 1.4   # 太短观众看不清
MAX_BEAT_DUR = 4.6   # 太长节奏走神

# Map common names (English / Chinese) → simple-icons slug
COMPANY_LOGOS: dict[str, str | None] = {
    # AI labs
    "OpenAI": "openai", "ChatGPT": "openai", "Codex": "openai", "Sora": "openai",
    "Anthropic": "anthropic", "Claude": "anthropic",
    "Google": "google", "Gemini": "google", "TPU": "google",
    "DeepMind": "googledeepmind",
    "Apple": "apple",
    "Microsoft": "microsoft", "Copilot": "githubcopilot",
    "Meta": "meta", "Llama": "meta",
    "Amazon": "amazon", "AWS": "amazonaws",
    "Mistral": "mistralai",
    "Perplexity": "perplexity",
    "xAI": "x", "Grok": "x",
    # 中国厂
    "阿里": "alibabacloud", "阿里云": "alibabacloud", "Qwen": "alibabacloud",
    "腾讯": "tencentqq",
    "字节": "tiktok", "字节跳动": "tiktok",
    "百度": "baidu",
    "小米": "xiaomi",
    "华为": "huawei",
    # 工具
    "Slack": "slack",
    "GitHub": "github",
    "Linear": "linear",
    "Notion": "notion",
    "HuggingFace": "huggingface", "Hugging Face": "huggingface",
    "Vercel": "vercel",
    "Cloudflare": "cloudflare",
    "Stripe": "stripe",
    "Discord": "discord",
    # 硬件
    "Nvidia": "nvidia", "AMD": "amd", "Intel": "intel",
    "Tesla": "tesla",
}

# tools-cascade 的 label → simple-icons slug 别名表
# 当 agent 只给 label 没给 slug 时做兜底映射
SLUG_ALIASES: dict[str, str] = {
    # 消费类 App
    "spotify": "spotify", "audible": "audible", "ubereats": "ubereats",
    "uber": "uber", "ubereat": "ubereats", "turbotax": "turbotax",
    "netflix": "netflix", "youtube": "youtube",
    # 开发工具
    "github": "github", "gitlab": "gitlab", "vscode": "vscodium",
    "npm": "npm", "pnpm": "pnpm", "python": "python", "node": "nodedotjs",
    "nodejs": "nodedotjs", "node.js": "nodedotjs",
    "rust": "rust", "go": "go", "typescript": "typescript",
    "docker": "docker", "kubernetes": "kubernetes", "k8s": "kubernetes",
    # 模型 / 推理
    "ollama": "ollama", "huggingface": "huggingface", "vllm": "",  # vLLM 无 icon
    "llama.cpp": "", "llamacpp": "", "openai": "openai",
    "anthropic": "anthropic", "claude": "anthropic",
    "mistral": "mistralai", "cohere": "",
    # 协作
    "slack": "slack", "discord": "discord", "notion": "notion",
    "linear": "linear", "figma": "figma", "jira": "jira",
    "trello": "trello", "airtable": "airtable", "zapier": "zapier",
    # Google / Microsoft / Apple 生态
    "googledocs": "googledocs", "docs": "googledocs",
    "gmail": "gmail", "email": "gmail",
    "googlecalendar": "googlecalendar", "calendar": "googlecalendar",
    "microsoftword": "microsoftword", "word": "microsoftword",
    "microsoftexcel": "microsoftexcel", "excel": "microsoftexcel",
    # 云
    "aws": "amazonaws", "amazonaws": "amazonaws",
    "gcp": "googlecloud", "googlecloud": "googlecloud",
    "azure": "microsoftazure", "microsoftazure": "microsoftazure",
    "cloudflare": "cloudflare", "vercel": "vercel",
    # 数据库
    "postgresql": "postgresql", "postgres": "postgresql",
    "mysql": "mysql", "redis": "redis", "mongodb": "mongodb",
    # 硬件
    "nvidia": "nvidia", "amd": "amd", "intel": "intel",
    "tpu": "google", "apple": "apple",
}

# Platform → Source badge dot 色 (for title beat)
SOURCE_DOT_CLASS = {
    "github": "gh", "x": "x", "arxiv": "arxiv", "hf": "hf",
    "openai": "openai", "anthropic": "anthropic", "blog": "blog",
    "youtube": "youtube",
}
SOURCE_LABEL = {
    "github": "github.com", "x": "x.com", "arxiv": "arxiv.org",
    "hf": "huggingface.co", "youtube": "youtube.com",
    "openai": "openai.com/blog", "anthropic": "anthropic.com",
    "blog": "web",
}

# ╔════════════════════════════════════════════════════════════════════════╗
# ║  1. PARSER                                                              ║
# ╚════════════════════════════════════════════════════════════════════════╝

@dataclass
class Bullet:
    emoji: str
    title: str
    body: str

@dataclass
class NewsItem:
    n: int
    title_md: str
    title_clean: str
    category: str
    blockquote: str
    bullets: list[Bullet]
    media_path: Path | None
    media_is_video: bool
    source_url: str
    source_platform: str
    company_slug: str | None = None
    company_name: str | None = None

@dataclass
class Episode:
    title: str
    date: str
    type_: str
    episode: int
    voice: str
    sections: list[str]
    news: list[NewsItem]
    article_dir: Path

_MD_INLINE = re.compile(r"`([^`]+)`|\*\*([^*]+)\*\*|\*([^*]+)\*")
def strip_md_inline(s: str) -> str:
    return _MD_INLINE.sub(lambda m: next(g for g in m.groups() if g), s).strip()

def detect_source_platform(url: str) -> str:
    u = url.lower()
    if "github.com/" in u: return "github"
    if "x.com/" in u or "twitter.com/" in u: return "x"
    if "arxiv.org" in u: return "arxiv"
    if "huggingface.co" in u: return "hf"
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "openai.com" in u: return "openai"
    if "anthropic.com" in u: return "anthropic"
    return "blog"

def detect_company(title: str, blockquote: str) -> tuple[str | None, str | None]:
    text = title + " " + blockquote
    for name in sorted(COMPANY_LOGOS.keys(), key=lambda x: -len(x)):
        if name in text:
            return name, COMPANY_LOGOS[name]
    return None, None

def parse_article(path: Path) -> Episode:
    text = path.read_text(encoding="utf-8")
    article_dir = path.parent

    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not fm_match:
        raise ValueError("no frontmatter")
    fm_text = fm_match.group(1)
    body = text[fm_match.end():]

    def fm_field(key, default=""):
        m = re.search(rf'^{key}\s*:\s*"?([^"\n]+?)"?\s*$', fm_text, re.MULTILINE)
        return m.group(1).strip() if m else default

    title = fm_field("title")
    date = fm_field("date")
    type_ = fm_field("type", "ai")
    try:    episode = int(fm_field("episode", "1"))
    except: episode = 1
    voice = fm_field("tts_voice", "yunyang")
    if voice not in ("yunyang", "yunjian"): voice = "yunyang"

    overview_section_for: dict[int, str] = {}
    sections: list[str] = []
    overview_match = re.search(r"^##\s+概览\s*\n(.+?)(?=^---|\Z)", body, re.MULTILINE | re.DOTALL)
    if overview_match:
        cur_section = ""
        for line in overview_match.group(1).splitlines():
            line = line.strip()
            if line.startswith("### "):
                cur_section = line[4:].strip()
                if cur_section not in sections: sections.append(cur_section)
            else:
                m = re.search(r"#(\d+)\s*$", line)
                if m and cur_section:
                    overview_section_for[int(m.group(1))] = cur_section

    news_blocks = re.findall(
        r"^##\s+([^\n]+?)\s+#(\d+)\s*\n(.*?)(?=^##\s+|\Z)",
        body, re.MULTILINE | re.DOTALL,
    )

    news: list[NewsItem] = []
    for title_md, n_str, block in news_blocks:
        n = int(n_str)
        title_md = title_md.strip()
        bq_match = re.search(r"^>\s*(.+?)(?=\n\n|\Z)", block, re.MULTILINE | re.DOTALL)
        bq_raw = bq_match.group(1) if bq_match else ""
        bq_clean = strip_md_inline(re.sub(r"\s+", " ", bq_raw)).strip().lstrip("**摘要**:").lstrip("摘要:").strip()

        bullets = []
        for em, ttl, body_text in re.findall(
            r"^-\s+([^\s]+)\s+\*\*([^*]+)\*\*\s*[:：]\s*(.+?)$",
            block, re.MULTILINE,
        ):
            bullets.append(Bullet(em, strip_md_inline(ttl), strip_md_inline(body_text)))

        media_path: Path | None = None
        media_is_video = False
        media_match = re.search(r"!\[[^\]]*\]\(([^)]+)\)", block)
        if media_match:
            mp = media_match.group(1)
            if not mp.startswith(("http://", "https://")):
                p = (article_dir / mp).resolve()
                if p.exists():
                    media_path = p
                    media_is_video = mp.lower().endswith((".mp4", ".webm", ".mov", ".gif"))

        url_match = re.search(r"```\s*\n(https?://[^\n]+)\s*\n```", block)
        source_url = url_match.group(1).strip() if url_match else ""
        platform = detect_source_platform(source_url)

        company_name, company_slug = detect_company(title_md, bq_clean)

        news.append(NewsItem(
            n=n, title_md=title_md, title_clean=strip_md_inline(title_md),
            category=overview_section_for.get(n, ""), blockquote=bq_clean,
            bullets=bullets, media_path=media_path, media_is_video=media_is_video,
            source_url=source_url, source_platform=platform,
            company_slug=company_slug, company_name=company_name,
        ))

    return Episode(title=title, date=date, type_=type_, episode=episode, voice=voice,
                   sections=sections, news=news, article_dir=article_dir)

# ╔════════════════════════════════════════════════════════════════════════╗
# ║  2. LOGO FETCHER (cached, shared for companies + tools)                 ║
# ╚════════════════════════════════════════════════════════════════════════╝

LOGO_CACHE = PROJECT / "assets" / "logos"

def fetch_logo(slug: str) -> Path | None:
    if not slug: return None
    LOGO_CACHE.mkdir(parents=True, exist_ok=True)
    target = LOGO_CACHE / f"{slug}.svg"
    if target.exists() and target.stat().st_size > 100:
        return target
    try:
        urlretrieve(LOGO_CDN.format(slug=slug), target)
        if target.stat().st_size < 100:
            target.unlink(missing_ok=True)
            return None
        return target
    except Exception:
        return None

def read_logo_svg(slug: str | None, inline_color: str | None = None) -> str:
    """读 SVG 去外层 fill，可选注入 inline color on root <svg>."""
    if not slug: return ""
    p = LOGO_CACHE / f"{slug}.svg"
    if not p.exists(): return ""
    svg = p.read_text()
    svg = re.sub(r' fill="[^"]*"', "", svg)
    if inline_color:
        svg = re.sub(r"<svg\b", f'<svg fill="{inline_color}"', svg, count=1)
    return svg

# ╔════════════════════════════════════════════════════════════════════════╗
# ║  3. TTS GENERATION                                                      ║
# ╚════════════════════════════════════════════════════════════════════════╝

import edge_tts

VOICE_MAP = {"yunyang": TTS_VOICE_DEFAULT, "yunjian": TTS_VOICE_ALT}

def intro_script(episode: Episode) -> str:
    return f"AI 早报，{episode.date.replace('-', ' 年 ', 1).replace('-', ' 月 ')} 日，本期带来 {len(episode.news)} 条热门资讯。"

def news_script(item: NewsItem, idx: int) -> str:
    pos = ["第一", "第二", "第三", "第四", "第五", "第六", "第七", "第八", "第九", "第十"][idx]
    return f"{pos}条。{item.blockquote}"

def outro_script(_: Episode) -> str:
    return "感谢收听，明日见。"

async def gen_tts(episode: Episode, audio_dir: Path) -> dict[str, dict]:
    audio_dir.mkdir(parents=True, exist_ok=True)
    voice = VOICE_MAP[episode.voice]

    segments = [("intro", intro_script(episode))]
    for i, n in enumerate(episode.news):
        segments.append((f"news_{n.n:02d}", news_script(n, i)))
    segments.append(("outro", outro_script(episode)))

    cache_key_path = audio_dir / "_segments_cache.json"
    cache_key = {sid: hashlib.md5((voice + "|" + text).encode()).hexdigest()
                 for sid, text in segments}
    prev = {}
    if cache_key_path.exists():
        try: prev = json.loads(cache_key_path.read_text())
        except: prev = {}

    async def gen(sid: str, text: str):
        out = audio_dir / f"{sid}.mp3"
        if out.exists() and out.stat().st_size > 1024 and prev.get(sid) == cache_key[sid]:
            return sid, out, text
        comm = edge_tts.Communicate(text, voice, rate="+0%", pitch="+0Hz")
        await comm.save(str(out))
        return sid, out, text

    results = await asyncio.gather(*[gen(s, t) for s, t in segments])
    cache_key_path.write_text(json.dumps(cache_key))

    info: dict[str, dict] = {}
    for sid, path, text in results:
        dur_str = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)], text=True).strip()
        info[sid] = {"file": path, "duration": float(dur_str), "text": text}

    concat_lines = "\n".join(f"file '{info[s]['file']}'" for s, _ in segments)
    concat_path = audio_dir / "_concat.txt"
    concat_path.write_text(concat_lines)
    full_wav = audio_dir / "full.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path),
         "-ar", "48000", "-ac", "2", str(full_wav)],
        capture_output=True, check=True)
    info["full"] = {
        "file": full_wav,
        "duration": sum(info[s]["duration"] for s, _ in segments),
        "text": "",
    }
    return info

# ╔════════════════════════════════════════════════════════════════════════╗
# ║  4. WHISPER FORCED ALIGNMENT                                            ║
# ╚════════════════════════════════════════════════════════════════════════╝

def whisper_chars(audio_path: Path) -> list[tuple[str, float, float]]:
    base = audio_path.parent / (audio_path.stem + "_w16k")
    wav16 = base.with_name(base.name + ".wav")
    json_out = base.with_name(base.name + ".json")
    json_arg = str(base)

    if not (wav16.exists() and wav16.stat().st_size > 1024):
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(audio_path), "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", str(wav16)],
            capture_output=True, check=True)

    if not (json_out.exists() and json_out.stat().st_size > 1024):
        subprocess.run(
            [WHISPER_BIN, "-m", WHISPER_MODEL, "-l", "zh", "-ml", "1",
             "-oj", "-of", json_arg, str(wav16)],
            capture_output=True, check=True)

    j = json.loads(json_out.read_text())
    chars = []
    for seg in j.get("transcription", []):
        text = seg.get("text", "")
        o = seg.get("offsets", {})
        a, b = o.get("from", 0)/1000.0, o.get("to", 0)/1000.0
        if not text: continue
        per = (b - a) / max(len(text), 1)
        for i, c in enumerate(text):
            chars.append((c, a + i*per, a + (i+1)*per))
    return chars

_PUNC = re.compile(r"[\s,，。、·.\-_\[\]?？!！:：;；()（）]")

def find_anchor_time(chars: list[tuple[str, float, float]],
                     anchor: str, after: float = 0.0) -> float | None:
    target = _PUNC.sub("", anchor)
    if not target: return None
    buf, ts = "", []
    for c, s, _ in chars:
        if s < after - 0.05: continue
        cs = _PUNC.sub("", c)
        if not cs: continue
        buf += cs; ts.append(s)
        if len(buf) > len(target) + 8:
            buf, ts = buf[-(len(target)+8):], ts[-(len(target)+8):]
        i = buf.find(target)
        if i >= 0:
            return ts[i]
    return None

# ╔════════════════════════════════════════════════════════════════════════╗
# ║  5. CAPTION SPLIT                                                       ║
# ╚════════════════════════════════════════════════════════════════════════╝

@dataclass
class Caption:
    start: float
    end: float
    text: str

def split_caption_text(text: str, max_chars: int = 26) -> list[str]:
    # 支持多种中文分隔符：，。、·；;，以及英文逗号/句号
    segs = re.split(r"([，。、·；;,.])", text)
    chunks, cur = [], ""
    for s in segs:
        if not s: continue
        if len(cur) + len(s) <= max_chars:
            cur += s
        else:
            if cur: chunks.append(cur)
            cur = s
    if cur: chunks.append(cur)
    return [c.strip("，。、·；;, ") for c in chunks if c.strip("，。、·；;, ")]

def plan_captions(item: NewsItem, scene_start: float, scene_dur: float,
                  audio_chars: list[tuple[str, float, float]]) -> list[Caption]:
    """为一条新闻切字幕。
    策略：① 先按字符数比例分配时间作 baseline；② 在 baseline 附近用 whisper 锚点精修；
    ③ end 设到下一条 start；④ 只合并 < 0.6s 的短句到前一条（避免过度合并）。
    """
    end = scene_start + scene_dur
    cap_segs = split_caption_text(item.blockquote, 26)
    if not cap_segs:
        return []

    # ① 按字符数比例分配 baseline 时间（跳过引导 "第N条" 占 ~0.5s）
    lead_in = 0.5
    avail = max(scene_dur - lead_in - 0.2, 1.0)
    total_chars = sum(len(s) for s in cap_segs) or 1
    baseline: list[float] = []
    t_acc = scene_start + lead_in
    for seg in cap_segs:
        baseline.append(t_acc)
        t_acc += len(seg) / total_chars * avail

    # ② 用 whisper anchor 精修，偏离 baseline 超过 2s 则回退到 baseline
    captions: list[Caption] = []
    cursor = scene_start + lead_in
    for i, seg in enumerate(cap_segs):
        anchor = _PUNC.sub("", seg[:8])
        t_anchor = find_anchor_time(audio_chars, anchor, cursor) if anchor else None
        t_base = baseline[i]
        if t_anchor is None or abs(t_anchor - t_base) > 2.0:
            t = t_base
        else:
            t = t_anchor
        # 保证单调递增
        if captions and t <= captions[-1].start + 0.1:
            t = captions[-1].start + 0.1
        captions.append(Caption(start=t, end=t, text=seg))
        cursor = t + 0.3

    # ③ end 设到下一条 start - 小 gap
    for i, c in enumerate(captions):
        nxt = captions[i+1].start if i+1 < len(captions) else end
        c.end = max(c.start + 0.3, nxt - 0.02)

    # ④ 只合并 < 0.6s 的（阻止把正常 1-3s 的字幕也合掉）
    merged: list[Caption] = []
    for c in captions:
        if c.end - c.start < 0.6 and merged and (c.end - merged[-1].start) < 5:
            merged[-1].text = merged[-1].text + " · " + c.text
            merged[-1].end = c.end
        else:
            merged.append(c)
    return merged

# ╔════════════════════════════════════════════════════════════════════════╗
# ║  6. VISUAL BEATS — data model + allocation                              ║
# ╚════════════════════════════════════════════════════════════════════════╝

@dataclass
class RenderedBeat:
    news_index: int
    type_: str
    start: float
    duration: float
    track_index: int
    html: str
    animate_kind: str = ""   # extra hint for GSAP (e.g. "stagger-cards")

def load_visual_beats(article_dir: Path) -> dict | None:
    """读 visual_beats.json。做 schema 适配兼容 LLM schema drift：
    - agent 可能输出 news/items、news_id/index、payload/data 两套键，全部映射到规范名
    - episode 可能是 string 或 int，统一成 int（取不到默认 1）
    """
    p = article_dir / "visual_beats.json"
    if not p.exists(): return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

    # 规范化顶层
    items_raw = raw.get("items") or raw.get("news") or raw.get("episodes") or []
    if not items_raw:
        return None

    # 规范化 episode（字符串→整数或 1）
    ep_raw = raw.get("episode", 1)
    try:
        episode = int(ep_raw) if isinstance(ep_raw, (int, float)) else int(str(ep_raw).split("-")[-1])
    except Exception:
        episode = 1

    normalized = {
        "version": 1,
        "episode": episode,
        "type": raw.get("type", ""),
        "items": [],
    }
    for it in items_raw:
        idx = it.get("index") or it.get("news_id") or it.get("id") or (len(normalized["items"]) + 1)
        beats_raw = it.get("beats", [])
        beats_norm = []
        for b in beats_raw:
            btype = b.get("type", "")
            weight = b.get("weight", 1.0 / max(len(beats_raw), 1))
            # Schema drift 兼容：data / payload / content / props / attributes
            data = (b.get("data") or b.get("payload") or b.get("content")
                    or b.get("props") or b.get("attributes") or {})
            # 如果 data 还是空但 b 本身含识别字段，把 b 除了 meta 键之外的当 data
            if not data:
                meta_keys = {"type", "weight", "id", "index", "order"}
                data = {k: v for k, v in b.items() if k not in meta_keys}
            beats_norm.append({"type": btype, "weight": weight, "data": data})
        normalized["items"].append({
            "index": int(idx) if str(idx).isdigit() else len(normalized["items"]) + 1,
            "eyebrow_en": it.get("eyebrow_en", ""),
            "beats": beats_norm,
        })
    return normalized

def allocate_beat_times(weights: list[float], total: float,
                        min_dur: float = MIN_BEAT_DUR, max_dur: float = MAX_BEAT_DUR
                        ) -> list[tuple[float, float]]:
    """把一条新闻的总时长按 weight 分配到每个 beat，clamp 到 [min,max] 后把差额
    在未 clamp 的 peers 间重分。返回 [(start_offset, duration), ...]"""
    if not weights: return []
    n = len(weights)
    if total <= 0:
        return [(0.0, 0.0)] * n
    # 防止 min*n > total（新闻时长太短）
    eff_min = min(min_dur, total / n * 0.9)
    eff_max = max(max_dur, total / n * 1.1)
    sum_w = sum(weights) or 1.0
    raw = [total * w / sum_w for w in weights]
    locked = [None] * n
    changed = True
    while changed:
        changed = False
        free_idx = [i for i in range(n) if locked[i] is None]
        if not free_idx: break
        free_total = total - sum(v for v in locked if v is not None)
        free_w = sum(weights[i] for i in free_idx) or 1.0
        for i in free_idx:
            raw[i] = free_total * weights[i] / free_w
        for i in free_idx:
            if raw[i] < eff_min:
                locked[i] = eff_min; changed = True; break
            if raw[i] > eff_max:
                locked[i] = eff_max; changed = True; break
    final = [locked[i] if locked[i] is not None else raw[i] for i in range(n)]
    # 计算 offsets
    out, cum = [], 0.0
    for d in final:
        out.append((cum, d))
        cum += d
    # 归一化误差
    if cum > 0 and abs(cum - total) > 0.01:
        # 把误差补到最后一个
        last_off, last_d = out[-1]
        out[-1] = (last_off, last_d + (total - cum))
    return out

# ╔════════════════════════════════════════════════════════════════════════╗
# ║  7. BEAT RENDERERS (10 kinds)                                           ║
# ╚════════════════════════════════════════════════════════════════════════╝

def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def wrap_broll(inner: str, start: float, dur: float, track: int, tag_id: str = "") -> str:
    id_attr = f' id="{tag_id}"' if tag_id else ""
    return (f'<div class="broll clip"{id_attr} '
            f'data-start="{start:.2f}" data-duration="{dur:.2f}" '
            f'data-track-index="{track}">{inner}</div>')

# ── 1. logo-hero ──────────────────────────────────────────────────────────
def render_logo_hero(data: dict, item: NewsItem) -> tuple[str, str]:
    # 兼容多种字段名：company/brand/name，slug 可能 agent 直接给
    company = data.get("company") or data.get("brand") or data.get("name") or ""
    direct_slug = data.get("slug", "")
    slug = direct_slug or COMPANY_LOGOS.get(company) or item.company_slug
    # 直接 slug 先拉一下缓存（agent 给的 slug 可能还没下载）
    if direct_slug:
        fetch_logo(direct_slug)
    # HyperFrames 不可靠地继承 CSS fill 到 inline SVG，inline 写 fill 强制 #1D1D1F
    svg = read_logo_svg(slug, inline_color="#1D1D1F") if slug else ""
    if svg:
        return f'<div class="b-logo">{svg}</div>', "fade-scale"
    # fallback: media image / number
    if item.media_path and not item.media_is_video:
        rel = f"./assets/media/{item.media_path.name}"
        return f'<div class="b-logo image"><img src="{rel}" alt=""/></div>', "fade"
    return (f'<div style="font-size:360px;font-weight:900;color:#1D1D1F;'
            f'letter-spacing:-12px;">{item.n:02d}</div>', "fade")

# ── 2. wordmark ───────────────────────────────────────────────────────────
def render_wordmark(data: dict) -> tuple[str, str]:
    # 兼容 text 单字段（agent 有时给单行字符串）+ lines 数组
    lines = data.get("lines")
    if not lines:
        text = data.get("text") or data.get("word") or "WORDMARK"
        # 单行字符串转数组
        lines = [text] if isinstance(text, str) else list(text)
    accent = data.get("accent_line", -1)
    spans = []
    for i, line in enumerate(lines):
        cls = ' class="accent"' if i == accent else ""
        spans.append(f'<span{cls}>{html_escape(str(line))}</span>')
    return f'<div class="b-wordmark">{"<br>".join(spans)}</div>', "fade-up"

# ── 3. metric-cards ───────────────────────────────────────────────────────
def render_metric_cards(data: dict) -> tuple[str, str]:
    cards = data.get("cards", [])
    cols = int(data.get("columns", len(cards) or 4))
    cells = []
    for c in cards:
        cells.append(
            f'<div class="b-tier">'
            f'<div class="ico">{html_escape(c.get("emoji",""))}</div>'
            f'<div class="nm">{html_escape(c.get("name",""))}</div>'
            f'<div class="sub">{html_escape(c.get("sub",""))}</div>'
            f'</div>'
        )
    inner = (f'<div class="b-tier-grid" style="grid-template-columns:repeat({cols},1fr);">'
             f'{"".join(cells)}</div>')
    return inner, "stagger-cards"

# ── 4. codeblock ──────────────────────────────────────────────────────────
def render_codeblock(data: dict) -> tuple[str, str]:
    lines = data.get("lines", [])
    running = data.get("running", "")
    run_span = f'<span class="running">{html_escape(running)}</span>' if running else ""
    body = "<br>".join(lines)
    return (f'<div class="b-codeblk">{body}{run_span}</div>', "fade-up")

# ── 5. tools-cascade ──────────────────────────────────────────────────────
def render_tools_cascade(data: dict) -> tuple[str, str]:
    tools = data.get("tools", [])
    cells = []
    for t in tools:
        slug = t.get("slug", "")
        label = t.get("label", slug)
        # slug 为空时，用 label 转常见别名试一次（agent 有时给空 slug）
        if not slug:
            slug = SLUG_ALIASES.get(label.lower().replace(" ", ""), "")
        # 只下载，不检测失败情况下再 fallback
        if slug:
            fetch_logo(slug)  # 幂等，如已有缓存直接用
        svg = read_logo_svg(slug, inline_color="#1D1D1F") if slug else ""
        if svg:
            icon = f'<div class="badge">{svg}</div>'
        else:
            # fallback: 首字母
            initial = (label[:1] or "?").upper()
            icon = f'<div class="badge fallback">{html_escape(initial)}</div>'
        cells.append(f'<div class="b-tool">{icon}<div class="lbl">{html_escape(label)}</div></div>')
    return f'<div class="b-tools-row">{"".join(cells)}</div>', "stagger-tools"

# ── 6. mockup ─────────────────────────────────────────────────────────────
def render_mockup(data: dict) -> tuple[str, str]:
    platform = data.get("platform", "slack")
    avatar_bg = data.get("avatar_bg", "#1D1D1F")
    who = html_escape(data.get("who", ""))
    when = html_escape(data.get("when", ""))
    body_html = data.get("body_html", "")  # 允许原样 inline HTML（信任 LLM）
    actions = data.get("actions", [])
    btns = []
    for a in actions:
        variant = a.get("variant", "no")
        btns.append(f'<button class="btn {variant}">{html_escape(a.get("label",""))}</button>')
    mark = {"slack": "S", "discord": "D", "imessage": "iM"}.get(
        platform, platform[:1].upper() or "?"
    )
    inner = (
        f'<div class="b-mockup b-mockup-{platform}">'
        f'<div class="head">'
        f'<div class="avatar" style="background:{avatar_bg};">{mark}</div>'
        f'<div class="who">{who}</div>'
        f'<div class="when">{when}</div>'
        f'</div>'
        f'<div class="body">{body_html}</div>'
        f'<div class="actions">{"".join(btns)}</div>'
        f'</div>'
    )
    return inner, "fade-up"

# ── 7. glyphs ─────────────────────────────────────────────────────────────
def render_glyphs(data: dict) -> tuple[str, str]:
    items = data.get("items", [])
    cells = []
    for it in items:
        cells.append(
            f'<div class="b-glyph">'
            f'<div class="ring">{html_escape(it.get("emoji",""))}</div>'
            f'<div class="lbl">{html_escape(it.get("label",""))}</div>'
            f'</div>'
        )
    return f'<div class="b-glyphs-row">{"".join(cells)}</div>', "stagger-glyphs"

# ── 8. stat-hero ──────────────────────────────────────────────────────────
def render_stat_hero(data: dict) -> tuple[str, str]:
    value = html_escape(data.get("value", ""))
    unit = html_escape(data.get("unit", ""))
    caption = html_escape(data.get("caption", ""))
    unit_span = f'<span class="unit">{unit}</span>' if unit else ""
    return (
        f'<div class="b-stat">'
        f'<div class="value">{value}{unit_span}</div>'
        f'<div class="caption">{caption}</div>'
        f'</div>',
        "fade-up"
    )

# ── 9. timeline ───────────────────────────────────────────────────────────
def render_timeline(data: dict) -> tuple[str, str]:
    steps = data.get("steps", [])
    cells = []
    for i, s in enumerate(steps):
        cells.append(
            f'<div class="b-step">'
            f'<div class="num">{html_escape(s.get("num", f"{i+1:02d}"))}</div>'
            f'<div class="lbl">{html_escape(s.get("label",""))}</div>'
            f'</div>'
        )
    sep = '<div class="b-step-sep">→</div>'
    inner = f'<div class="b-timeline-row">{sep.join(cells)}</div>'
    return inner, "stagger-steps"

# ── 10. image-hero ────────────────────────────────────────────────────────
def render_image_hero(data: dict, item: NewsItem) -> tuple[str, str]:
    filename = data.get("filename", "")
    if filename:
        rel = f"./assets/media/{filename}"
    elif item.media_path and not item.media_is_video:
        rel = f"./assets/media/{item.media_path.name}"
    elif item.media_path and item.media_is_video:
        rel = f"./assets/media/{item.media_path.name}"
        return (f'<div class="b-image"><video src="{rel}" muted autoplay loop></video></div>',
                "fade")
    else:
        return render_logo_hero({"company": item.company_name or ""}, item)
    if rel.lower().endswith((".mp4", ".webm", ".mov")):
        return (f'<div class="b-image"><video src="{rel}" muted autoplay loop></video></div>',
                "fade")
    return f'<div class="b-image"><img src="{rel}" alt=""/></div>', "fade"

RENDERERS = {
    "logo-hero":      lambda d, it: render_logo_hero(d, it),
    "wordmark":       lambda d, it: render_wordmark(d),
    "metric-cards":   lambda d, it: render_metric_cards(d),
    "codeblock":      lambda d, it: render_codeblock(d),
    "tools-cascade":  lambda d, it: render_tools_cascade(d),
    "mockup":         lambda d, it: render_mockup(d),
    "glyphs":         lambda d, it: render_glyphs(d),
    "stat-hero":      lambda d, it: render_stat_hero(d),
    "timeline":       lambda d, it: render_timeline(d),
    "image-hero":     lambda d, it: render_image_hero(d, it),
}

# ╔════════════════════════════════════════════════════════════════════════╗
# ║  8. CSS (extends v4_reference)                                          ║
# ╚════════════════════════════════════════════════════════════════════════╝

CSS = r"""
@font-face {
  font-family: "NotoSC";
  src: url("./assets/fonts/NotoSansSC-Regular.ttf") format("truetype");
  font-weight: 100 900;
}
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@200;400;700;900&family=JetBrains+Mono:wght@400;600&display=swap');

* { margin: 0; padding: 0; box-sizing: border-box; }
html, body {
  width: 1920px; height: 1080px; overflow: hidden;
  background: #FBFBFD;
  font-family: "Inter", "NotoSC", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  color: #1D1D1F;
}

.bg {
  position: absolute; inset: 0;
  background:
    radial-gradient(ellipse 90% 70% at 50% 0%, #FFFFFF 0%, transparent 60%),
    linear-gradient(180deg, #FBFBFD 0%, #F5F5F7 100%);
}
.bg-noise {
  position: absolute; inset: 0; pointer-events: none; opacity: 0.025;
  mix-blend-mode: multiply;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");
}

.eyebrow {
  position: absolute; top: 60px; left: 80px;
  font-size: 18px; letter-spacing: 12px; color: #86868B; font-weight: 600;
  text-transform: uppercase;
}
.eyebrow .num { color: #D04835; font-weight: 900; margin-right: 24px; letter-spacing: 6px; }

.stage {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  padding: 120px 160px 200px;
}
.headline {
  font-size: 168px; font-weight: 900; line-height: 1.04;
  letter-spacing: -3px; color: #1D1D1F;
  text-align: center; max-width: 1700px;
}
.headline .em {
  background: linear-gradient(180deg, #E45A45 0%, #B83020 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.lede {
  font-size: 38px; line-height: 1.5; color: #6E6E73; font-weight: 400;
  letter-spacing: 0.3px; text-align: center; max-width: 1400px;
  margin-top: 44px;
}

.broll {
  position: absolute; inset: 160px 100px 180px 100px;
  display: flex; align-items: center; justify-content: center;
}

/* ══ 1. logo-hero ═════════════════════════════════════════════ */
.b-logo { width: 420px; height: 420px; opacity: 0.95; }
.b-logo svg { width: 100%; height: 100%; fill: #1D1D1F; }
.b-logo.image {
  width: auto; height: 600px; border-radius: 24px;
  box-shadow: 0 30px 80px -20px rgba(0,0,0,0.18);
  overflow: hidden;
}
.b-logo.image img, .b-logo.image video {
  height: 100%; width: auto; max-width: 1500px; object-fit: cover; display: block;
}

/* ══ 2. wordmark ═════════════════════════════════════════════ */
.b-wordmark {
  font-size: 208px; font-weight: 900; line-height: 1; letter-spacing: -4px;
  text-align: center; color: #1D1D1F;
}
.b-wordmark span {
  display: inline-block;
  background: linear-gradient(180deg, #1D1D1F 0%, #4A3F35 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.b-wordmark .accent {
  background: linear-gradient(180deg, #E45A45 0%, #B83020 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}

/* ══ 3. metric-cards ═════════════════════════════════════════ */
.b-tier-grid {
  display: grid; gap: 36px;
  width: 100%; max-width: 1720px;
}
.b-tier {
  background: #FFFFFF;
  border: 1px solid rgba(0,0,0,0.06);
  border-radius: 28px; padding: 52px 36px; text-align: center;
  box-shadow: 0 20px 56px -16px rgba(0,0,0,0.08);
}
.b-tier .ico { font-size: 64px; margin-bottom: 22px; line-height: 1; }
.b-tier .nm { font-size: 36px; font-weight: 800; color: #1D1D1F; letter-spacing: -0.5px; margin-bottom: 8px; }
.b-tier .sub { font-size: 16px; color: #8E8E93; letter-spacing: 2px; font-weight: 600; text-transform: uppercase; }

/* ══ 4. codeblock ════════════════════════════════════════════ */
.b-codeblk {
  width: 100%; max-width: 1620px;
  background: #1D1D1F; border-radius: 22px;
  padding: 68px 60px 52px;
  font-family: "JetBrains Mono", monospace;
  color: #E8DDC9; font-size: 28px; line-height: 1.7;
  box-shadow: 0 28px 70px -16px rgba(0,0,0,0.25);
  position: relative;
}
.b-codeblk::before {
  content: "● ● ●"; position: absolute; top: 18px; left: 22px;
  font-size: 12px; color: #4A3F35; letter-spacing: 4px;
}
.b-codeblk .key { color: #FF8A6B; }
.b-codeblk .str { color: #A0E6B8; }
.b-codeblk .com { color: #6B5B4D; font-style: italic; }
.b-codeblk .punc { color: #8A7A66; }
.b-codeblk .var { color: #FFD89E; }
.b-codeblk .running {
  display: inline-block; padding: 4px 14px; margin-left: 12px; margin-top: 12px;
  background: rgba(255,138,107,0.18); color: #FF8A6B; border-radius: 6px;
  font-size: 18px; letter-spacing: 1px;
}
.b-codeblk .running::before { content: "● "; animation: pulse 1.4s infinite; }
@keyframes pulse { 50% { opacity: .35; } }

/* ══ 5. tools-cascade ════════════════════════════════════════ */
.b-tools-row {
  display: flex; gap: 80px; align-items: center; justify-content: center;
  flex-wrap: wrap;
}
.b-tool {
  display: flex; flex-direction: column; align-items: center; gap: 24px;
}
.b-tool .badge {
  width: 200px; height: 200px; background: #FFFFFF;
  border-radius: 46px; box-shadow: 0 22px 50px -14px rgba(0,0,0,0.12);
  display: flex; align-items: center; justify-content: center;
  border: 1px solid rgba(0,0,0,0.05);
}
.b-tool .badge svg { width: 110px; height: 110px; fill: #1D1D1F; }
.b-tool .badge.fallback {
  font-size: 90px; font-weight: 900; color: #1D1D1F; letter-spacing: -2px;
}
.b-tool .lbl { font-size: 28px; font-weight: 700; color: #1D1D1F; letter-spacing: 0.5px; }

/* ══ 6. mockup (slack / discord / imessage) ══════════════════ */
.b-mockup {
  width: 900px; background: #FFFFFF; border-radius: 22px;
  padding: 38px 44px; box-shadow: 0 30px 70px -16px rgba(0,0,0,0.14);
  border: 1px solid rgba(0,0,0,0.05);
}
.b-mockup .head {
  display: flex; align-items: center; gap: 18px; margin-bottom: 22px;
}
.b-mockup .avatar {
  width: 60px; height: 60px; border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 30px; font-weight: 900; letter-spacing: -0.5px;
}
.b-mockup-imessage .avatar { border-radius: 50%; }
.b-mockup-discord .avatar  { border-radius: 16px; }
.b-mockup .who { font-weight: 800; color: #1D1D1F; font-size: 28px; }
.b-mockup .when { color: #86868B; font-size: 16px; margin-left: auto; font-variant-numeric: tabular-nums; }
.b-mockup .body { font-size: 24px; color: #1D1D1F; line-height: 1.5; margin-bottom: 24px; }
.b-mockup .body code {
  background: #F0EDED; padding: 3px 10px; border-radius: 5px;
  font-family: "JetBrains Mono", monospace; color: #B83020; font-size: 0.92em;
}
.b-mockup .actions { display: flex; gap: 14px; flex-wrap: wrap; }
.b-mockup .btn {
  padding: 14px 28px; border-radius: 10px; font-weight: 700; font-size: 19px;
  letter-spacing: 0.5px; cursor: pointer; border: none;
}
.b-mockup .btn.ok { background: #007A5A; color: #fff; }
.b-mockup-discord .btn.ok { background: #5865F2; }
.b-mockup-imessage .btn.ok { background: #34C759; }
.b-mockup .btn.no { background: #F0EDED; color: #4A3F35; }

/* ══ 7. glyphs ═══════════════════════════════════════════════ */
.b-glyphs-row {
  display: flex; gap: 110px; align-items: center; justify-content: center;
  flex-wrap: wrap;
}
.b-glyph {
  display: flex; flex-direction: column; align-items: center; gap: 24px;
}
.b-glyph .ring {
  width: 260px; height: 260px;
  background: linear-gradient(135deg, #FFE5D9 0%, #FFCAB8 100%);
  border-radius: 50%; display: flex; align-items: center; justify-content: center;
  font-size: 116px; box-shadow: 0 28px 60px -12px rgba(208,72,53,0.22);
}
.b-glyph .lbl {
  font-size: 34px; font-weight: 800; color: #1D1D1F; letter-spacing: -0.3px;
  text-align: center;
}

/* ══ 8. stat-hero ════════════════════════════════════════════ */
.b-stat {
  display: flex; flex-direction: column; align-items: center; gap: 32px;
  text-align: center;
}
.b-stat .value {
  font-size: 360px; font-weight: 900; line-height: 0.95; letter-spacing: -12px;
  background: linear-gradient(180deg, #1D1D1F 0%, #4A3F35 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.b-stat .value .unit {
  display: inline-block;
  font-size: 0.28em; font-weight: 700; letter-spacing: 4px;
  margin-left: 28px; vertical-align: 0.55em;
  color: #E45A45;
  -webkit-text-fill-color: #E45A45;
  text-transform: uppercase;
}
.b-stat .caption {
  font-size: 36px; color: #6E6E73; font-weight: 500; letter-spacing: 0.5px;
  max-width: 1400px;
}

/* ══ 9. timeline ═════════════════════════════════════════════ */
.b-timeline-row {
  display: flex; gap: 28px; align-items: stretch; justify-content: center;
  flex-wrap: wrap;
}
.b-step {
  background: #FFFFFF;
  border: 1px solid rgba(0,0,0,0.06);
  border-radius: 22px; padding: 36px 40px;
  min-width: 280px; max-width: 340px;
  display: flex; flex-direction: column; gap: 14px;
  box-shadow: 0 18px 46px -14px rgba(0,0,0,0.08);
}
.b-step .num {
  font-family: "JetBrains Mono", monospace;
  font-size: 22px; font-weight: 700; letter-spacing: 4px; color: #E45A45;
}
.b-step .lbl {
  font-size: 30px; font-weight: 800; color: #1D1D1F; letter-spacing: -0.3px;
  line-height: 1.25;
}
.b-step-sep {
  display: flex; align-items: center;
  color: #D04835; font-size: 56px; font-weight: 900; padding: 0 4px;
}

/* ══ 10. image-hero ══════════════════════════════════════════ */
.b-image {
  width: auto; max-width: 1500px; height: 600px;
  border-radius: 24px; overflow: hidden;
  box-shadow: 0 30px 80px -20px rgba(0,0,0,0.18);
}
.b-image img, .b-image video {
  height: 100%; width: auto; max-width: 1500px; object-fit: cover; display: block;
}

/* ══ title / source badge (fallback beat) ════════════════════ */
.b-title {
  font-size: 88px; font-weight: 900; line-height: 1.12;
  letter-spacing: -2px; color: #1D1D1F;
  text-align: center; max-width: 1700px;
}
.b-title em {
  background: linear-gradient(180deg, #E45A45 0%, #B83020 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  font-style: normal;
}
.b-title.smaller { font-size: 64px; }
.b-source {
  display: inline-flex; align-items: center; gap: 14px;
  padding: 14px 28px; background: #FFFFFF;
  border: 1px solid rgba(0,0,0,0.06); border-radius: 100px;
  font-size: 22px; color: #1D1D1F; font-weight: 600;
  box-shadow: 0 12px 32px -10px rgba(0,0,0,0.08);
  margin-top: 24px;
}
.b-source .dot { width: 18px; height: 18px; border-radius: 4px; background: #1D1D1F; }
.b-source.gh    .dot { background: #181717; }
.b-source.x     .dot { background: #000000; }
.b-source.arxiv .dot { background: #B31B1B; }
.b-source.hf    .dot { background: #FFD21E; }
.b-source.openai .dot { background: #10A37F; }
.b-source.anthropic .dot { background: #C96442; }
.b-source.youtube .dot { background: #FF0000; }

/* bullets (legacy fallback beat) */
.b-bullets {
  width: 100%; max-width: 1500px;
  display: flex; flex-direction: column; gap: 18px;
}
.b-bullet {
  background: #FFFFFF;
  border: 1px solid rgba(0,0,0,0.05);
  border-radius: 22px; padding: 26px 32px;
  display: grid; grid-template-columns: 64px 1fr; gap: 24px;
  align-items: start;
  box-shadow: 0 16px 40px -14px rgba(0,0,0,0.06);
}
.b-bullet .e { font-size: 48px; line-height: 1; }
.b-bullet .t { font-size: 28px; font-weight: 800; color: #1D1D1F;
              letter-spacing: -0.3px; margin-bottom: 6px; }
.b-bullet .d { font-size: 20px; color: #424245; line-height: 1.5; font-weight: 400; }

/* closing (legacy fallback beat) */
.b-closing {
  display: flex; flex-direction: column; align-items: center; gap: 28px;
  text-align: center;
}
.b-closing .next-label { font-size: 18px; letter-spacing: 8px; color: #86868B; font-weight: 700; }
.b-closing .next-text {
  font-size: 44px; font-weight: 800; color: #1D1D1F; max-width: 1500px;
  line-height: 1.25; letter-spacing: -0.5px;
}
.b-closing .src {
  margin-top: 12px; font-size: 18px; color: #86868B; letter-spacing: 1px;
  font-family: "JetBrains Mono", monospace;
}

/* outro */
.outro-stage {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 36px;
}
.outro-msg {
  font-size: 124px; font-weight: 900; letter-spacing: -2px;
  color: #1D1D1F;
}
.outro-msg .dot { color: #D04835; }
.outro-tag { font-size: 24px; letter-spacing: 8px; color: #86868B; }
.outro-tag .brand { color: #D04835; font-weight: 700; }

/* caption */
.cap {
  position: absolute; bottom: 100px; left: 50%; transform: translateX(-50%);
  font-size: 36px; font-weight: 600; color: #1D1D1F;
  background: rgba(255,255,255,0.95); padding: 16px 36px; border-radius: 100px;
  letter-spacing: 0.5px; backdrop-filter: blur(16px);
  border: 1px solid rgba(0,0,0,0.04);
  max-width: 1700px; text-align: center; line-height: 1.3;
  box-shadow: 0 12px 32px -10px rgba(0,0,0,0.12);
}

/* bottom signature */
.sig {
  position: absolute; bottom: 36px; left: 80px; right: 80px;
  display: flex; align-items: center; gap: 16px;
  font-size: 12px; color: #86868B; letter-spacing: 4px; font-weight: 500;
}
.sig .dot { width: 5px; height: 5px; border-radius: 50%; background: #D04835; }
.sig b { color: #1D1D1F; font-weight: 700; }
.sig .right { margin-left: auto; font-variant-numeric: tabular-nums; letter-spacing: 6px; }
"""

# ╔════════════════════════════════════════════════════════════════════════╗
# ║  9. COMPOSITION ASSEMBLY                                                ║
# ╚════════════════════════════════════════════════════════════════════════╝

def eyebrow_text(item: NewsItem, visual: dict | None) -> str:
    """优先用 visual_beats.json 的 eyebrow_en，否则用 article category。"""
    if visual:
        for it in visual.get("items", []):
            if it.get("index") == item.n and it.get("eyebrow_en"):
                return it["eyebrow_en"]
    return item.category or "NEWS"

def render_fallback_beats(item: NewsItem, news_start: float, news_dur: float,
                          next_item: NewsItem | None, track_start: int
                          ) -> list[RenderedBeat]:
    """当 visual_beats.json 里没有该条新闻时，退化到 4-beat 模板。"""
    # 比例 HERO 0.2 / TITLE 0.18 / BULLETS 0.5 / CLOSING 0.12
    d = news_dur
    offs = [0.0, d*0.20, d*0.20 + d*0.18, d*0.20 + d*0.18 + d*0.50]
    durs = [d*0.20, d*0.18, d*0.50, d*0.12]
    tr = track_start
    out = []

    # hero = logo or image
    html_hero, anim_hero = render_logo_hero({"company": item.company_name or ""}, item)
    out.append(RenderedBeat(item.n, "logo-hero", news_start + offs[0], durs[0], tr,
                            wrap_broll(html_hero, news_start + offs[0], durs[0], tr), anim_hero))
    tr += 1

    # title
    title = html_escape(item.title_clean)
    if item.company_name:
        title = title.replace(item.company_name, f"<em>{item.company_name}</em>")
    cls_extra = " smaller" if len(item.title_clean) > 28 else ""
    plat = SOURCE_DOT_CLASS.get(item.source_platform, "blog")
    label = SOURCE_LABEL.get(item.source_platform, "web")
    title_html = (f'<div><h2 class="b-title{cls_extra}">{title}</h2>'
                  f'<div class="b-source {plat}"><span class="dot"></span>'
                  f'<span>{label}</span></div></div>')
    out.append(RenderedBeat(item.n, "title", news_start + offs[1], durs[1], tr,
                            wrap_broll(title_html, news_start + offs[1], durs[1], tr), "fade-up"))
    tr += 1

    # bullets
    rows = []
    for b in item.bullets[:5]:
        rows.append(
            f'<div class="b-bullet"><div class="e">{html_escape(b.emoji)}</div>'
            f'<div><div class="t">{html_escape(b.title)}</div>'
            f'<div class="d">{html_escape(b.body)}</div></div></div>'
        )
    bullets_html = f'<div class="b-bullets">{"".join(rows)}</div>'
    out.append(RenderedBeat(item.n, "bullets", news_start + offs[2], durs[2], tr,
                            wrap_broll(bullets_html, news_start + offs[2], durs[2], tr),
                            "stagger-bullets"))
    tr += 1

    # closing
    if next_item:
        label2, text2 = "下一条 →", html_escape(next_item.title_clean)
    else:
        label2, text2 = "本期结束", "感谢收听 · 明日见"
    closing_html = (f'<div class="b-closing">'
                    f'<div class="next-label">{label2}</div>'
                    f'<div class="next-text">{text2}</div>'
                    f'<div class="src">{html_escape(item.source_url)}</div>'
                    f'</div>')
    out.append(RenderedBeat(item.n, "closing", news_start + offs[3], durs[3], tr,
                            wrap_broll(closing_html, news_start + offs[3], durs[3], tr), "fade-up"))
    return out

def render_news_beats(item: NewsItem, visual_item: dict, news_start: float, news_dur: float,
                      track_start: int) -> list[RenderedBeat]:
    beats_spec = visual_item.get("beats", [])
    if not beats_spec:
        return []
    weights = [max(0.02, float(b.get("weight", 1.0 / len(beats_spec)))) for b in beats_spec]
    times = allocate_beat_times(weights, news_dur)

    out: list[RenderedBeat] = []
    tr = track_start
    for (off, dur), spec in zip(times, beats_spec):
        btype = spec.get("type", "")
        data = spec.get("data", {})
        renderer = RENDERERS.get(btype)
        if not renderer:
            # unknown type → 跳过但继续
            continue
        inner_html, anim_kind = renderer(data, item)
        start = news_start + off
        out.append(RenderedBeat(
            news_index=item.n, type_=btype, start=start, duration=dur,
            track_index=tr, html=wrap_broll(inner_html, start, dur, tr),
            animate_kind=anim_kind,
        ))
        tr += 1
    return out

def build_intro_captions(text: str, intro_dur: float, chars: list) -> list[Caption]:
    chunks = split_caption_text(text, 24)
    caps: list[Caption] = []
    cursor = 0.3
    for chunk in chunks:
        t = find_anchor_time(chars, chunk[:6], cursor) or cursor
        caps.append(Caption(start=t, end=t, text=chunk))
        cursor = t + 0.4
    for i, c in enumerate(caps):
        nxt = caps[i+1].start if i+1 < len(caps) else intro_dur
        c.end = nxt
    return caps

def gsap_for_beat(rb: RenderedBeat) -> list[str]:
    """Generate GSAP tweens for a beat's entrance and exit."""
    sel = f'.broll.clip[data-start="{rb.start:.2f}"]'
    anim_in = rb.start + 0.05
    anim_out = max(rb.start, rb.start + rb.duration - 0.5)
    lines = []

    if rb.animate_kind == "fade-scale":
        lines.append(f'tl.from(\'{sel}\', {{ opacity:0, scale:0.86, duration:0.6, ease:"power3.out" }}, {anim_in:.2f});')
        lines.append(f'tl.to(\'{sel}\', {{ opacity:0, scale:1.05, duration:0.4, ease:"power2.in" }}, {anim_out:.2f});')
    elif rb.animate_kind == "fade-up":
        lines.append(f'tl.from(\'{sel}\', {{ opacity:0, y:30, duration:0.55, ease:"power3.out" }}, {anim_in:.2f});')
        lines.append(f'tl.to(\'{sel}\', {{ opacity:0, duration:0.4, ease:"power2.in" }}, {anim_out:.2f});')
    elif rb.animate_kind == "stagger-cards":
        lines.append(f'tl.from(\'{sel}\', {{ opacity:0, duration:0.3, ease:"power2.out" }}, {anim_in:.2f});')
        lines.append(f'tl.from(\'{sel} .b-tier\', {{ opacity:0, y:30, scale:0.96, duration:0.5, ease:"power2.out", stagger:0.12 }}, {anim_in+0.08:.2f});')
        lines.append(f'tl.to(\'{sel}\', {{ opacity:0, duration:0.4, ease:"power2.in" }}, {anim_out:.2f});')
    elif rb.animate_kind == "stagger-tools":
        lines.append(f'tl.from(\'{sel}\', {{ opacity:0, duration:0.3, ease:"power2.out" }}, {anim_in:.2f});')
        lines.append(f'tl.from(\'{sel} .b-tool\', {{ opacity:0, y:24, scale:0.92, duration:0.45, ease:"power2.out", stagger:0.14 }}, {anim_in+0.08:.2f});')
        lines.append(f'tl.to(\'{sel}\', {{ opacity:0, duration:0.4, ease:"power2.in" }}, {anim_out:.2f});')
    elif rb.animate_kind == "stagger-glyphs":
        lines.append(f'tl.from(\'{sel}\', {{ opacity:0, duration:0.3, ease:"power2.out" }}, {anim_in:.2f});')
        lines.append(f'tl.from(\'{sel} .b-glyph\', {{ opacity:0, y:20, duration:0.4, ease:"power2.out", stagger:0.16 }}, {anim_in+0.08:.2f});')
        lines.append(f'tl.to(\'{sel}\', {{ opacity:0, duration:0.4, ease:"power2.in" }}, {anim_out:.2f});')
    elif rb.animate_kind == "stagger-steps":
        lines.append(f'tl.from(\'{sel}\', {{ opacity:0, duration:0.3, ease:"power2.out" }}, {anim_in:.2f});')
        lines.append(f'tl.from(\'{sel} .b-step\', {{ opacity:0, x:40, duration:0.4, ease:"power2.out", stagger:0.15 }}, {anim_in+0.08:.2f});')
        lines.append(f'tl.from(\'{sel} .b-step-sep\', {{ opacity:0, duration:0.3, ease:"power2.out", stagger:0.15 }}, {anim_in+0.12:.2f});')
        lines.append(f'tl.to(\'{sel}\', {{ opacity:0, duration:0.4, ease:"power2.in" }}, {anim_out:.2f});')
    elif rb.animate_kind == "stagger-bullets":
        lines.append(f'tl.from(\'{sel}\', {{ opacity:0, y:20, duration:0.5, ease:"power2.out" }}, {anim_in:.2f});')
        lines.append(f'tl.from(\'{sel} .b-bullet\', {{ opacity:0, x:30, duration:0.4, ease:"power2.out", stagger:0.18 }}, {anim_in+0.1:.2f});')
        lines.append(f'tl.to(\'{sel}\', {{ opacity:0, duration:0.4, ease:"power2.in" }}, {anim_out:.2f});')
    else:  # "fade" / default
        lines.append(f'tl.from(\'{sel}\', {{ opacity:0, duration:0.5, ease:"power2.out" }}, {anim_in:.2f});')
        lines.append(f'tl.to(\'{sel}\', {{ opacity:0, duration:0.4, ease:"power2.in" }}, {anim_out:.2f});')
    return lines

def build_html(episode: Episode, audio_info: dict, audio_chars: list,
               visual: dict | None) -> tuple[str, list[RenderedBeat], list[Caption]]:
    intro_dur = audio_info["intro"]["duration"]
    outro_dur = audio_info["outro"]["duration"]
    news_durs = [audio_info[f"news_{n.n:02d}"]["duration"] for n in episode.news]
    total = intro_dur + sum(news_durs) + outro_dur

    all_beats: list[RenderedBeat] = []
    all_caps: list[Caption] = []

    # intro captions
    all_caps.extend(build_intro_captions(audio_info["intro"]["text"], intro_dur, audio_chars))

    # per news: beats from visual or fallback
    cur_t = intro_dur
    track = 6  # 0-bg, 1-noise, 2-audio, 3-intro, 4-outro, 5-sig
    for i, n in enumerate(episode.news):
        dur = news_durs[i]
        next_n = episode.news[i+1] if i+1 < len(episode.news) else None
        visual_item = None
        if visual:
            for it in visual.get("items", []):
                if it.get("index") == n.n:
                    visual_item = it; break
        if visual_item:
            beats = render_news_beats(n, visual_item, cur_t, dur, track)
        else:
            beats = render_fallback_beats(n, cur_t, dur, next_n, track)
        all_beats.extend(beats)
        track += len(beats) + 2  # reserve slots
        # captions for this news
        all_caps.extend(plan_captions(n, cur_t, dur, audio_chars))
        cur_t += dur

    # ── assemble HTML ─────────────────────────────────────────────
    parts = []
    parts.append(f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                 f'<meta name="viewport" content="width=1920, height=1080">'
                 f'<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>'
                 f'<style>{CSS}</style></head><body>')
    parts.append(f'<div id="root" data-composition-id="root" data-start="0" '
                 f'data-duration="{total:.2f}" data-width="1920" data-height="1080">')

    parts.append(f'<div class="bg clip" data-start="0" data-duration="{total:.2f}" data-track-index="0"></div>')
    parts.append(f'<div class="bg-noise clip" data-start="0" data-duration="{total:.2f}" data-track-index="1"></div>')
    parts.append(f'<audio id="narration" class="clip" data-start="0" data-duration="{total:.2f}" '
                 f'data-track-index="2" src="./assets/audio/full.wav"></audio>')

    # intro
    sections_label = " · ".join(episode.sections[:4]) if episode.sections else "热门资讯"
    parts.append(f'<div class="stage clip" id="i-intro" data-start="0" data-duration="{intro_dur:.2f}" '
                 f'data-track-index="3">'
                 f'<div class="headline"><span class="em">AI 早报</span><br>{episode.date.replace("-","·")}</div>'
                 f'<div class="lede">本期 {len(episode.news)} 条 · {sections_label} · 小兔播报</div>'
                 f'</div>')

    # outro
    outro_start = intro_dur + sum(news_durs)
    parts.append(f'<div class="outro-stage clip" data-start="{outro_start:.2f}" '
                 f'data-duration="{outro_dur:.2f}" data-track-index="4">'
                 f'<div class="outro-msg">感谢收听<span class="dot"> · </span>明日见</div>'
                 f'<div class="outro-tag">作者 <span class="brand">Bunny</span> · 同名 B 站</div>'
                 f'</div>')

    # signature (always visible)
    parts.append(f'<div class="sig clip" data-start="0" data-duration="{total:.2f}" data-track-index="5">'
                 f'<span class="dot"></span><b>Bunny</b><span>·</span><span>AI 早报</span>'
                 f'<span class="right">EP·{episode.episode:04d} · {episode.date.replace("-","·")}</span>'
                 f'</div>')

    # per-news eyebrow (one track per news starting at 100, so they don't collide with beat tracks)
    eyebrow_track = 100
    cur_t2 = intro_dur
    for i, n in enumerate(episode.news):
        cat = html_escape(eyebrow_text(n, visual))
        parts.append(f'<div class="eyebrow clip" data-start="{cur_t2:.2f}" '
                     f'data-duration="{news_durs[i]:.2f}" data-track-index="{eyebrow_track}">'
                     f'<span class="num">№ {n.n:02d}</span>{cat}</div>')
        eyebrow_track += 1
        cur_t2 += news_durs[i]

    # beats
    for b in all_beats:
        parts.append(b.html)

    # captions (no-overlap safety)
    sorted_caps = sorted(all_caps, key=lambda c: c.start)
    for i, c in enumerate(sorted_caps):
        nxt_start = sorted_caps[i+1].start if i+1 < len(sorted_caps) else float("inf")
        safe_end = min(c.end, nxt_start - 0.05)
        dur = safe_end - c.start
        # 太短（< 0.3s）直接跳过，不强行拉长导致下一条被覆盖
        if dur < 0.3:
            continue
        parts.append(f'<div class="cap clip" data-start="{c.start:.2f}" '
                     f'data-duration="{dur:.2f}" data-track-index="200">'
                     f'{html_escape(c.text)}</div>')

    # GSAP timeline
    gsap = [
        'window.__timelines = window.__timelines || {};',
        'const tl = gsap.timeline({ paused: true });',
        'tl.from("#i-intro .headline", { opacity:0, y:30, duration:0.9, ease:"power3.out" }, 0.3)',
        '  .from("#i-intro .lede",     { opacity:0, y:18, duration:0.7, ease:"power2.out" }, 0.9);',
    ]
    for b in all_beats:
        gsap.extend(gsap_for_beat(b))
    gsap.append('window.__timelines["root"] = tl;')
    parts.append(f'<script>{chr(10).join(gsap)}</script>')
    parts.append('</div></body></html>')
    return "\n".join(parts), all_beats, all_caps

# ╔════════════════════════════════════════════════════════════════════════╗
# ║  10. ORCHESTRATOR                                                       ║
# ╚════════════════════════════════════════════════════════════════════════╝

def collect_tool_slugs(visual: dict | None) -> list[str]:
    if not visual: return []
    out = []
    for it in visual.get("items", []):
        for b in it.get("beats", []):
            if b.get("type") == "tools-cascade":
                for t in b.get("data", {}).get("tools", []):
                    slug = t.get("slug")
                    if slug: out.append(slug)
    return list({s for s in out})

def stage_media(episode: Episode):
    media_dir = PROJECT / "assets" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    for f in media_dir.glob("*"):
        f.unlink()
    for n in episode.news:
        if n.media_path:
            shutil.copy(n.media_path, media_dir / n.media_path.name)

def render_video(output: Path, quality: str = "draft"):
    cmd = ["npx", "hyperframes", "render", "-q", quality, "-w", "2",
           "-o", str(output)]
    subprocess.run(cmd, cwd=PROJECT, check=True)

def main():
    if len(sys.argv) < 2:
        print("用法: convert_hyperframes.py <article.md> [--quality draft|standard|high]", file=sys.stderr)
        sys.exit(1)
    article_path = Path(sys.argv[1]).resolve()
    if not article_path.exists():
        print(f"错误: {article_path} 不存在", file=sys.stderr); sys.exit(1)

    quality = "draft"
    if "--quality" in sys.argv:
        quality = sys.argv[sys.argv.index("--quality") + 1]
    skip_render = "--no-render" in sys.argv

    print(f"▶ Parsing: {article_path}")
    episode = parse_article(article_path)
    visual = load_visual_beats(article_path.parent)
    # 若 visual_beats.json 存在但无法解析（schema drift），应硬失败而不是默默降级
    vb_path = article_path.parent / "visual_beats.json"
    if vb_path.exists() and visual is None:
        print(f"  ✗ visual_beats.json 存在但 schema 无法识别", file=sys.stderr)
        print(f"  请检查 {vb_path}，补全 adapter 或修 agent 产出格式", file=sys.stderr)
        sys.exit(3)
    mode = "rich (visual_beats.json)" if visual else "fallback (4-beat)"
    print(f"  episode {episode.episode} · {episode.date} · {len(episode.news)} 条新闻 · 主播={episode.voice} · {mode}")

    # 1) 拉 logos (company + tool)
    print("▶ Fetching logos…")
    for n in episode.news:
        if n.company_slug:
            p = fetch_logo(n.company_slug)
            print(f"  #{n.n} company {n.company_name} → {n.company_slug}: {'✓' if p else '✗'}")
    for slug in collect_tool_slugs(visual):
        p = fetch_logo(slug)
        print(f"  tool {slug}: {'✓' if p else '✗'}")

    # 2) media
    print("▶ Staging media…")
    stage_media(episode)

    # 3) TTS
    print("▶ Generating TTS (云扬)…")
    audio_dir = PROJECT / "assets" / "audio"
    info = asyncio.run(gen_tts(episode, audio_dir))
    print(f"  full.wav  {info['full']['duration']:.1f}s")

    # 4) Whisper
    print("▶ Whisper aligning (medium)…")
    chars = whisper_chars(info["full"]["file"])
    print(f"  {len(chars)} char-level anchors")

    # 5) Build HTML
    print("▶ Building composition…")
    html, beats, caps = build_html(episode, info, chars, visual)
    (PROJECT / "index.html").write_text(html, encoding="utf-8")
    by_type = {}
    for b in beats:
        by_type[b.type_] = by_type.get(b.type_, 0) + 1
    print(f"  {len(beats)} beats (" + ", ".join(f"{k}×{v}" for k, v in sorted(by_type.items())) + f"), {len(caps)} captions")

    # 6) Lint
    print("▶ Linting…")
    r = subprocess.run(["npx", "hyperframes", "lint"], cwd=PROJECT,
                       capture_output=True, text=True)
    out = r.stdout + r.stderr
    if "error" in out.lower() and "0 error" not in out.lower() and "0 errors" not in out.lower():
        print(out, file=sys.stderr)
        print("✗ Lint failed", file=sys.stderr); sys.exit(2)
    print("  lint clean")

    # 7) Render
    out_path = article_path.parent / "video_hyperframes.mp4"
    if skip_render:
        print(f"▶ Skipping render (--no-render). index.html at {PROJECT / 'index.html'}")
        return
    print(f"▶ Rendering ({quality}) → {out_path.name}…")
    render_video(out_path, quality)
    sz = out_path.stat().st_size / (1024*1024)
    print(f"✓ Done: {out_path}  ({sz:.1f}MB)")

if __name__ == "__main__":
    main()
