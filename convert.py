#!/usr/bin/env python3
"""
video-pipeline convert v5 — NotebookLM dark style
Updates:
  - Intro TTS: "欢迎收看《文章标题》"
  - No "摘要" prefix in TTS text
  - Subtitles generated from SentenceBoundary events, burned into video
  - Voice fixed: zh-CN-YunxiNeural (明明)
"""
import re, asyncio, subprocess, json, math, urllib.request, shutil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ARTICLE = "/root/video-pipeline/output/2026-04-08/github/article.md"
OUT_DIR = Path(ARTICLE).parent
BUILD   = Path("/tmp/vp_build3")
FONT    = "/root/video-pipeline/assets/fonts/NotoSansSC-Regular.ttf"
EMOJI_FONT_PATH = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"

BUILD.mkdir(parents=True, exist_ok=True)
(BUILD/"audio").mkdir(exist_ok=True)
(BUILD/"slides").mkdir(exist_ok=True)
(BUILD/"subs").mkdir(exist_ok=True)
(BUILD/"media").mkdir(exist_ok=True)

W, H      = 1920, 1080
TAB_H     = 52
TITLE_H   = 108
CONTENT_Y = TAB_H + TITLE_H
CONTENT_H = H - CONTENT_Y
PAD       = 52
CARD_GAP  = 22

# ASS subtitle settings (PlayResX/Y = actual video resolution)
ASS_FONT     = "Noto Sans CJK SC"
ASS_FONTSIZE = 72          # px at PlayRes 1920×1080
ASS_MARGIN_H = 80          # left/right margin px
ASS_MARGIN_V = 52          # bottom margin px
ASS_MAX_W    = W - ASS_MARGIN_H * 2   # max line pixel width

ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,{font},{sz},&H00FFFFFF,&H000000FF,&H80000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,{mh},{mh},{mv},0

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
""".format(W=W, H=H, font=ASS_FONT, sz=ASS_FONTSIZE, mh=ASS_MARGIN_H, mv=ASS_MARGIN_V)

# ── Palette ───────────────────────────────────────────────────────────────────
BG           = (10,  10,  20)
BG2          = (16,  16,  30)
CARD_BG      = (22,  22,  40)
CARD_BD      = (45,  45,  75)
WHITE        = (255, 255, 255)
MUTED        = (160, 160, 195)
DIM          = (80,  80, 110)
TEAL         = (78,  205, 196)
PURPLE       = (139,  92, 246)
AMBER        = (251, 191,  36)
ROSE         = (251, 113, 133)
GREEN        = ( 74, 222, 128)
INACTIVE_TAB = (35,  35,  55)
CARD_ACCENTS = [TEAL, PURPLE, AMBER, ROSE, GREEN]

# ── Font helpers ───────────────────────────────────────────────────────────────
def f(sz): return ImageFont.truetype(FONT, sz)

_FE = None
def fe():
    global _FE
    if _FE is None:
        _FE = ImageFont.truetype(EMOJI_FONT_PATH, 109)
    return _FE

def emoji_img(char, size):
    tmp = Image.new('RGBA', (130, 130), (0, 0, 0, 0))
    d   = ImageDraw.Draw(tmp)
    try:
        d.text((5, 5), char, font=fe(), embedded_color=True)
        bbox = tmp.getbbox()
        if bbox:
            tmp = tmp.crop(bbox)
    except Exception:
        return None
    return tmp.resize((size, size), Image.LANCZOS)

# ── Text utils ────────────────────────────────────────────────────────────────
def strip_md(t):
    # Strip "摘要...：" prefix BEFORE removing bold markers
    t = re.sub(r'^\*\*摘要\*\*[^：:]*[：:]\s*', '', t, flags=re.MULTILINE)
    t = re.sub(r'^摘要[^：:]*[：:]\s*',         '', t, flags=re.MULTILINE)
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)
    t = re.sub(r'`(.+?)`',        r'\1', t)
    t = re.sub(r'^>\s*',          '', t, flags=re.MULTILINE)
    return t.strip()

def wrap(draw, text, fnt, max_w):
    lines, cur = [], ''
    for ch in text:
        if draw.textlength(cur + ch, font=fnt) > max_w and cur:
            lines.append(cur); cur = ch
        else:
            cur += ch
    if cur: lines.append(cur)
    return lines

def fit_text(draw, text, fnt, max_w):
    if draw.textlength(text, font=fnt) <= max_w: return text
    while text and draw.textlength(text + '…', font=fnt) > max_w:
        text = text[:-1]
    return text + '…'

# ── Parse ─────────────────────────────────────────────────────────────────────
def parse(path):
    txt = Path(path).read_text(encoding='utf-8')
    fm  = {}
    m   = re.match(r'^---\n(.+?)\n---\n', txt, re.DOTALL)
    if m:
        for ln in m.group(1).splitlines():
            if ':' in ln:
                k, v = ln.split(':', 1)
                fm[k.strip()] = v.strip().strip('"')

    date_str  = fm.get('date', '2026-04-08')
    title_fm  = fm.get('title', 'AI 资讯')

    # Extract short show name from 【...】 bracket, e.g. "GitHub 周报 2026-04-08" → "GitHub 周报"
    bm = re.search(r'【([^】]+)】', title_fm)
    if bm:
        show_name = re.sub(r'\d{4}-\d{2}-\d{2}', '', bm.group(1)).strip()
    else:
        show_name = fm.get('type', 'AI 资讯').upper() + ' 周报'

    cat_order, num2cat, cat_items = [], {}, {}
    ov = re.search(r'## 概览\n(.+?)^---', txt, re.DOTALL | re.MULTILINE)
    if ov:
        cur = None
        for ln in ov.group(1).splitlines():
            h3 = re.match(r'^###\s+(.+)', ln)
            if h3:
                cur = h3.group(1).strip()
                cat_order.append(cur)
                cat_items[cur] = []
            elif cur:
                m2 = re.match(r'^-\s+(.+)', ln)
                if m2:
                    raw = re.sub(r'\s*#\d+$', '', m2.group(1))
                    cat_items[cur].append(strip_md(raw))
                    nm = re.search(r'#(\d+)$', m2.group(1))
                    if nm: num2cat[int(nm.group(1))] = cur

    news = []
    for sec in re.split(r'\n## ', txt)[1:]:
        hdr = re.match(r'^(.+?)\s*#(\d+)\s*\n', sec)
        if not hdr: continue
        num   = int(hdr.group(2))
        title = strip_md(hdr.group(1).strip())
        parts = [ln[2:] for ln in sec.splitlines() if ln.startswith('> ')]
        summary = strip_md(' '.join(parts))
        cards = []
        for m3 in re.finditer(r'^[-•]\s+(\S+)\s+\*\*([^*]+)\*\*[：:]\s+(.+)',
                               sec, re.MULTILINE):
            cards.append({
                'emoji': m3.group(1),
                'title': m3.group(2),
                'body':  strip_md(m3.group(3))
            })
        # Extract up to 2 media paths/URLs from ![...](...)
        media_raws = [m.strip() for m in
                      re.findall(r'!\[[^\]]*\]\(([^)]+)\)', sec)][:2]

        news.append({
            'num': num, 'title': title, 'summary': summary,
            'cards': cards[:5],
            'cat': num2cat.get(num, cat_order[0] if cat_order else ''),
            'media_raw': media_raws[0] if media_raws else '',
            'media_raws': media_raws,
        })
    news.sort(key=lambda x: x['num'])
    tabs = ['Intro'] + cat_order + ['Outro']
    return {
        'date': date_str, 'title': title_fm, 'show_name': show_name,
        'cat_order': cat_order, 'cat_items': cat_items,
        'news': news, 'total': len(news), 'tabs': tabs
    }

# ── SRT subtitle generation ───────────────────────────────────────────────────
def _srt_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _ass_time(sec):
    """Format seconds as ASS timestamp H:MM:SS.cs"""
    h  = int(sec // 3600)
    m  = int((sec % 3600) // 60)
    s  = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"

_sub_fnt = None
def _get_sub_fnt():
    global _sub_fnt
    if _sub_fnt is None:
        _sub_fnt = ImageFont.truetype(FONT, ASS_FONTSIZE)
    return _sub_fnt

def _measure(text):
    """Pixel width of text at subtitle font size."""
    bbox = _get_sub_fnt().getbbox(text)
    return (bbox[2] - bbox[0]) if bbox else 0

def _split_to_single_lines(text, max_px=ASS_MAX_W):
    """Split text into chunks that each fit within max_px (one line each)."""
    lines, cur = [], ''
    for ch in text:
        if _measure(cur + ch) > max_px and cur:
            lines.append(cur.strip())
            cur = ch
        else:
            cur += ch
    if cur.strip():
        lines.append(cur.strip())
    return lines or [text]

def _char_weight(ch):
    """Timing weight per character — punctuation = longer pause."""
    if ch in '。！？…':  return 2.2
    if ch in '，、；：':  return 1.6
    if ch == ' ':        return 0.5
    return 1.0

def _split_sentence_timed(text, start, end):
    """
    Split one sentence into single-line chunks.
    Time each chunk proportionally by punctuation-weighted character count.
    """
    lines = _split_to_single_lines(text)
    if len(lines) == 1:
        return [{'text': text.strip(), 'start': start, 'end': end}]

    weights = [sum(_char_weight(c) for c in ln) for ln in lines]
    total_w = sum(weights) or 1
    dur, chunks, t = end - start, [], start
    for ln, w in zip(lines, weights):
        chunk_end = t + dur * w / total_w
        chunks.append({'text': ln.strip(), 'start': t, 'end': chunk_end})
        t = chunk_end
    return chunks

def sentences_to_ass(sentences, ass_path):
    """
    Pre-split sentences into single-line chunks (fixed bottom position).
    Timing weighted by punctuation so pauses feel natural.
    """
    events = []
    for s in sentences:
        text = s['text'].strip()
        if not text:
            continue
        for chunk in _split_sentence_timed(text, s['start'], s['end']):
            events.append(
                f"Dialogue: 0,{_ass_time(chunk['start'])},{_ass_time(chunk['end'])},"
                f"Default,,0,0,0,,{chunk['text']}"
            )
    Path(ass_path).write_text(ASS_HEADER + '\n'.join(events) + '\n', encoding='utf-8')

# ── TTS with subtitle extraction ──────────────────────────────────────────────
VOICE = "zh-CN-YunxiNeural"

async def tts_with_subs(text, audio_out, ass_out, rate="+5%"):
    """TTS via edge-tts; collect SentenceBoundary events → ASS subtitle file."""
    import edge_tts
    comm      = edge_tts.Communicate(text, VOICE, rate=rate)
    audio_b   = bytearray()
    sentences = []
    async for chunk in comm.stream():
        if chunk['type'] == 'audio':
            audio_b.extend(chunk['data'])
        elif chunk['type'] == 'SentenceBoundary':
            start = chunk['offset'] / 1e7
            dur   = chunk['duration'] / 1e7
            sentences.append({'text': chunk['text'], 'start': start, 'end': start + dur})
    Path(audio_out).write_bytes(bytes(audio_b))
    sentences_to_ass(sentences, ass_out)
    return sentences[-1]['end'] if sentences else 5.0

def audio_dur(path):
    r = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', path],
        capture_output=True, text=True)
    for s in json.loads(r.stdout).get('streams', []):
        if 'duration' in s: return float(s['duration'])
    return 5.0

# ── Segment encoding (with subtitle burn) ────────────────────────────────────
def make_seg(slide, audio, dur, out, ass=None):
    cmd = ['ffmpeg', '-y', '-loop', '1', '-i', slide]
    if audio:
        cmd += ['-i', audio]
    else:
        cmd += ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100']

    vf = f'scale={W}:{H}'
    if ass and Path(ass).exists() and Path(ass).stat().st_size > 50:
        ass_escaped = ass.replace('\\', '/').replace(':', '\\:')
        vf += f",ass='{ass_escaped}'"

    cmd += ['-t', str(dur), '-vf', vf,
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
            '-shortest', out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"seg failed:\n{r.stderr[-400:]}")

# ── Draw primitives ───────────────────────────────────────────────────────────
def draw_tab_bar(draw, tabs, active):
    n  = len(tabs)
    tw = W // n
    fa = f(26); fi = f(24)
    for i, tab in enumerate(tabs):
        x1, x2 = i * tw, i * tw + tw
        if tab == active:
            draw.rectangle([x1, 0, x2, TAB_H], fill=TEAL)
            draw.text((x1 + tw // 2, TAB_H // 2), tab,
                      font=fa, fill=(10, 10, 20), anchor='mm')
        else:
            draw.rectangle([x1, 0, x2, TAB_H], fill=INACTIVE_TAB)
            draw.text((x1 + tw // 2, TAB_H // 2), tab,
                      font=fi, fill=MUTED, anchor='mm')
        if i:
            draw.line([x1, 4, x1, TAB_H - 4], fill=DIM, width=1)

def draw_title(draw, title):
    draw.rectangle([0, TAB_H, W, TAB_H + TITLE_H], fill=BG2)
    draw.line([0, TAB_H + TITLE_H - 1, W, TAB_H + TITLE_H - 1],
              fill=(40, 40, 65), width=1)

    max_w  = W - 120
    # Auto font-size: try fitting in 1 line, then 2 lines
    chosen_fnt, chosen_lines = f(44), [title]   # safe fallback
    for sz in [58, 50, 44, 38]:
        fnt   = f(sz)
        lines = wrap(draw, title, fnt, max_w)
        lh    = int(sz * 1.22)          # line height
        if len(lines) == 1:
            chosen_fnt, chosen_lines, chosen_lh = fnt, lines, lh
            break
        if len(lines) <= 2 and len(lines) * lh <= TITLE_H - 12:
            chosen_fnt, chosen_lines, chosen_lh = fnt, lines, lh
            break
    else:
        chosen_lh = int(38 * 1.22)

    n       = len(chosen_lines)
    total_h = n * chosen_lh
    y0      = TAB_H + (TITLE_H - total_h) // 2 + chosen_lh // 2
    for i, ln in enumerate(chosen_lines[:2]):
        draw.text((60, y0 + i * chosen_lh), ln,
                  font=chosen_fnt, fill=TEAL, anchor='lm')

def draw_card(img, draw, x, y, w, h, emoji_char, title, body, accent):
    draw.rounded_rectangle([x, y, x + w, y + h],
                            radius=16, fill=CARD_BG, outline=CARD_BD, width=1)
    draw.rounded_rectangle([x, y, x + w, y + 5], radius=2, fill=accent)
    isz = 52; ix, iy = x + 20, y + 20
    ei = emoji_img(emoji_char, isz)
    if ei:
        img.paste(ei, (ix, iy), ei)
    else:
        draw.rounded_rectangle([ix, iy, ix + isz, iy + isz], radius=12, fill=accent)
        draw.text((ix + isz // 2, iy + isz // 2), emoji_char[:1],
                  font=f(26), fill=(10, 10, 20), anchor='mm')
    title_s = fit_text(draw, title, f(28), w - isz - 70)
    draw.text((ix + isz + 16, iy + isz // 2), title_s,
              font=f(28), fill=accent, anchor='lm')
    bf     = f(26)
    body_x = x + 20
    body_y = iy + isz + 18
    for line in wrap(draw, body, bf, w - 40):
        if body_y + 34 > y + h - 10: break
        draw.text((body_x, body_y), line, font=bf, fill=MUTED)
        body_y += 34

def draw_cards(img, draw, cards):
    n = len(cards)
    if n == 0: return
    n_top   = min(3, n)
    n_bot   = max(0, n - n_top)
    avail_h = CONTENT_H - PAD * 2
    h_top   = int(avail_h * 0.47) if n_bot else avail_h
    h_bot   = avail_h - h_top - CARD_GAP if n_bot else 0
    w_top   = (W - PAD * 2 - CARD_GAP * (n_top - 1)) // n_top
    y_top   = CONTENT_Y + PAD
    for i in range(n_top):
        draw_card(img, draw, PAD + i * (w_top + CARD_GAP), y_top,
                  w_top, h_top, cards[i]['emoji'], cards[i]['title'],
                  cards[i]['body'], CARD_ACCENTS[i % len(CARD_ACCENTS)])
    if n_bot:
        w_bot = (W - PAD * 2 - CARD_GAP * (n_bot - 1)) // n_bot
        y_bot = y_top + h_top + CARD_GAP
        for i in range(n_bot):
            draw_card(img, draw, PAD + i * (w_bot + CARD_GAP), y_bot,
                      w_bot, h_bot, cards[n_top + i]['emoji'],
                      cards[n_top + i]['title'], cards[n_top + i]['body'],
                      CARD_ACCENTS[(n_top + i) % len(CARD_ACCENTS)])

# ── Media helpers ─────────────────────────────────────────────────────────────
MEDIA_EXTS_IMAGE = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
MEDIA_EXTS_GIF   = {'.gif'}
MEDIA_EXTS_VIDEO = {'.mp4', '.mov', '.webm', '.mkv', '.avi'}
MEDIA_DURATION   = 3.0   # seconds to show each media asset (fallback, not used for adaptive)

def calc_overlay_dur(seg_dur, mtype='image'):
    """
    Adaptively compute overlay visible duration based on segment length and media type.

    Strategy per type:
      image — 40 % of available window, clamped [3.5s, 8s]
      gif   — 55 % of available window, clamped [4s, 14s]  (animated, can stay longer)
      video — use the clip's natural duration; handled by caller via ffprobe

    Available window = seg_dur - MEDIA_OVERLAY_START - 0.6s tail buffer.
    Always leaves room for at least 2× fade transitions.
    """
    available = seg_dur - MEDIA_OVERLAY_START - 0.6
    min_fade_room = MEDIA_FADE_D * 2 + 0.5
    if available < min_fade_room:
        return MEDIA_OVERLAY_DUR   # segment too short → keep default

    if mtype == 'gif':
        target = max(4.0, min(available * 0.55, 14.0))
    else:                          # image
        target = max(3.5, min(available * 0.40, 8.0))

    return round(min(target, available), 2)


def video_duration(path):
    """Return duration in seconds of a video file via ffprobe, or None on failure."""
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path],
            capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except Exception:
        return None


def resolve_media(raw, num):
    """Download URL → local path, or verify local path exists. Returns path or None."""
    if not raw:
        return None
    if raw.startswith('http'):
        ext  = Path(raw.split('?')[0]).suffix.lower() or '.jpg'
        dest = BUILD / 'media' / f'{num:02d}_asset{ext}'
        if dest.exists():
            return str(dest)
        try:
            req = urllib.request.Request(raw, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as r, open(dest, 'wb') as f:
                shutil.copyfileobj(r, f)
            return str(dest)
        except Exception as e:
            print(f"    ⚠️  下载失败 {raw[:60]}: {e}")
            return None
    p = Path(raw)
    return str(p) if p.exists() else None

def media_type(path):
    ext = Path(path).suffix.lower()
    if ext in MEDIA_EXTS_GIF:   return 'gif'
    if ext in MEDIA_EXTS_VIDEO: return 'video'
    if ext in MEDIA_EXTS_IMAGE: return 'image'
    return None

MEDIA_OVERLAY_START = 1.2   # seconds into segment before overlay appears
MEDIA_OVERLAY_DUR   = 3.0   # total overlay visible duration
MEDIA_FADE_D        = 0.45  # fade-in / fade-out duration
MEDIA_SLIDE_PX      = 80    # pixels the card travels during slide-up animation

def make_media_panel_rgba(media_paths):
    """
    Create an RGBA PNG overlay panel for 1 or 2 images side-by-side.
    media_paths: str or list[str].  GIF/video paths are skipped.
    Returns (path, panel_w, panel_h) or None.
    """
    if isinstance(media_paths, str):
        media_paths = [media_paths]
    # Filter to static images only
    paths = [p for p in media_paths if p and media_type(p) == 'image']
    if not paths:
        return None

    imgs = []
    for p in paths:
        try:
            imgs.append(Image.open(p).convert('RGB'))
        except Exception:
            pass
    if not imgs:
        return None

    pad_inner = 12
    border    = 4
    img_gap   = 12          # gap between images when 2 shown
    max_pw    = int(W * 0.68)
    max_ph    = int(H * 0.68)

    if len(imgs) == 1:
        mimg  = imgs[0]
        ratio = min(max_pw / mimg.width, max_ph / mimg.height)
        iw    = int(mimg.width  * ratio)
        ih    = int(mimg.height * ratio)
        pw    = iw + (pad_inner + border) * 2
        ph    = ih + (pad_inner + border) * 2

        panel = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
        d     = ImageDraw.Draw(panel)
        d.rounded_rectangle([0, 0, pw, ph], radius=20, fill=(12, 12, 24, 220))
        d.rounded_rectangle([0, 0, pw, ph], radius=20, outline=(*TEAL, 255), width=border)
        panel.paste(mimg.resize((iw, ih), Image.LANCZOS),
                    (pad_inner + border, pad_inner + border))
    else:
        # 2 images side-by-side: each gets half the available content width
        slot_w = (max_pw - (pad_inner + border) * 2 - img_gap) // 2
        slot_h = max_ph - (pad_inner + border) * 2

        placed = []
        for mimg in imgs:
            ratio = min(slot_w / mimg.width, slot_h / mimg.height)
            iw = int(mimg.width  * ratio)
            ih = int(mimg.height * ratio)
            placed.append((mimg.resize((iw, ih), Image.LANCZOS), iw, ih))

        content_w = placed[0][1] + img_gap + placed[1][1]
        content_h = max(placed[0][2], placed[1][2])
        pw = content_w + (pad_inner + border) * 2
        ph = content_h + (pad_inner + border) * 2

        panel = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
        d     = ImageDraw.Draw(panel)
        d.rounded_rectangle([0, 0, pw, ph], radius=20, fill=(12, 12, 24, 220))
        d.rounded_rectangle([0, 0, pw, ph], radius=20, outline=(*TEAL, 255), width=border)

        cx = pad_inner + border
        for ri, (rim, iw, ih) in enumerate(placed):
            iy_off = (content_h - ih) // 2   # vertically center each image
            panel.paste(rim, (cx, pad_inner + border + iy_off))
            cx += iw + img_gap

    stem = '_'.join(Path(p).stem for p in paths[:2])
    p = BUILD / 'slides' / f'overlay_panel_{stem}.png'
    panel.save(str(p))
    return str(p), pw, ph

def make_seg_with_overlay(slide, audio, dur, out, ass=None, media_path=None):
    """
    Encode one video segment. media_path may be a str or list[str] of image paths.
    Composites them as an overlay with 翻书效果: fade-in+slide-up, hold, fade-out+slide-down.
    """
    if not media_path:
        make_seg(slide, audio, dur, out, ass)
        return
    # Normalise to list
    paths = media_path if isinstance(media_path, list) else [media_path]
    paths = [p for p in paths if p and media_type(p) == 'image']
    if not paths:
        make_seg(slide, audio, dur, out, ass)
        return

    result = make_media_panel_rgba(paths)
    if not result:
        make_seg(slide, audio, dur, out, ass)
        return
    panel_path, pw, ph = result

    ox = (W - pw) // 2
    oy = (H - ph) // 2          # resting position (center)
    ms = MEDIA_OVERLAY_START    # overlay start time
    md = calc_overlay_dur(dur, 'image')
    fd = MEDIA_FADE_D
    sd = MEDIA_SLIDE_PX         # slide distance

    # y expression: slides up during fade-in, holds, slides down during fade-out
    y_expr = (
        f"if(lt(t-{ms},{fd}),"
        f"  {oy + sd}-(({sd})*((t-{ms})/{fd})),"    # slide up
        f"  if(gt(t-{ms},{md-fd}),"
        f"    {oy}+(({sd})*((t-{ms}-{md-fd})/{fd})),"  # slide down
        f"    {oy}))"                                   # hold
    )

    # alpha expression via enable + overlay format=auto (FFmpeg handles RGBA fade)
    vf_overlay = (
        f"[2:v]scale={pw}:{ph},"
        f"fade=t=in:st={ms}:d={fd}:alpha=1,"
        f"fade=t=out:st={ms+md-fd}:d={fd}:alpha=1[ov];"
        f"[0:v]scale={W}:{H}[bg];"
        f"[bg][ov]overlay=x={ox}:y='{y_expr}':"
        f"enable='between(t,{ms},{ms+md})':format=auto[vout]"
    )

    # Subtitle filter applied on top of composited output
    if ass and Path(ass).exists() and Path(ass).stat().st_size > 50:
        ass_e = ass.replace('\\', '/').replace(':', '\\:')
        vf_overlay = vf_overlay.replace('[vout]', '[vpre]') + \
                     f";[vpre]ass='{ass_e}'[vout]"

    cmd = ['ffmpeg', '-y',
           '-loop', '1', '-i', slide,
           '-i', audio if audio else '/dev/null',
           '-loop', '1', '-i', panel_path,
           '-filter_complex', vf_overlay,
           '-map', '[vout]', '-map', '1:a',
           '-t', str(dur),
           '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
           '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
           '-shortest', out]
    if not audio:
        cmd[4:6] = ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100']
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    ⚠️  overlay失败，回退: {r.stderr[-200:]}")
        make_seg(slide, audio, dur, out, ass)

def make_gif_overlay_seg(slide, audio, dur, out, media_path, ass=None):
    """For GIF: loop gif, scale/letterbox, overlay on slide with fade."""
    ms = MEDIA_OVERLAY_START
    md = calc_overlay_dur(dur, 'gif')
    fd = MEDIA_FADE_D
    pw = int(W * 0.68)
    ph = int(H * 0.68)
    ox = (W - pw) // 2
    oy = (H - ph) // 2

    vf = (
        f"[3:v]scale={pw}:{ph}:force_original_aspect_ratio=decrease,"
        f"pad={pw}:{ph}:(ow-iw)/2:(oh-ih)/2:color=0x0C0C18,"
        f"fade=t=in:st={ms}:d={fd}:alpha=1,"
        f"fade=t=out:st={ms+md-fd}:d={fd}:alpha=1,"
        f"format=yuva420p[gif];"
        f"[0:v]scale={W}:{H}[bg];"
        f"[bg][gif]overlay=x={ox}:y={oy}:"
        f"enable='between(t,{ms},{ms+md})':format=auto[vout]"
    )
    if ass and Path(ass).exists() and Path(ass).stat().st_size > 50:
        ass_e = ass.replace('\\', '/').replace(':', '\\:')
        vf = vf.replace('[vout]', '[vpre]') + f";[vpre]ass='{ass_e}'[vout]"

    audio_args = ['-i', audio] if audio else \
                 ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100']
    cmd = ['ffmpeg', '-y',
           '-loop', '1', '-i', slide,
           *audio_args,
           '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
           '-stream_loop', '-1', '-i', media_path,
           '-filter_complex', vf,
           '-map', '[vout]', '-map', '1:a',
           '-t', str(dur),
           '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
           '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
           '-shortest', out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    ⚠️  gif overlay失败，回退")
        make_seg(slide, audio, dur, out, ass)

# ── Slide generators ───────────────────────────────────────────────────────────
CAT_ICONS = {
    '开发生态': '</>', '模型发布': '✦', '模型与工具': 'AI',
    '行业动态': '↗',  '前瞻与传闻': '◎', '要闻': '★',
    '技术与洞察': '∞', '产品应用': '▶',
}

def make_intro_slide(data):
    img = Image.new('RGB', (W, H), BG)
    d   = ImageDraw.Draw(img)
    for r in range(350, 0, -25):
        ratio = r / 350
        col   = (int(10 + 68*(1-ratio)), int(10+195*(1-ratio)), int(20+176*(1-ratio)))
        d.ellipse([W//2-r, H//2-r-80, W//2+r, H//2+r-80], fill=col)
    draw_tab_bar(d, data['tabs'], 'Intro')
    d.text((W//2, 160), f"{data['date']}  资讯概览", font=f(60), fill=WHITE, anchor='mm')

    cats = data['cat_order']
    n    = len(cats); bm = 80; bg = 40; by = 250
    bh   = H - by - 60
    cat_c = [TEAL, PURPLE, AMBER, ROSE]

    def _cat_box(x, y, w, h, cat, col):
        d.rounded_rectangle([x,y,x+w,y+h], radius=18, fill=(20,20,38), outline=(*col,80), width=2)
        hdr_h = 58
        d.rounded_rectangle([x,y,x+w,y+hdr_h], radius=18, fill=col)
        d.rectangle([x,y+hdr_h//2,x+w,y+hdr_h], fill=col)
        icon = CAT_ICONS.get(cat, '●')
        d.text((x+w//2, y+hdr_h//2), f"{icon}  {cat}", font=f(28), fill=(10,10,20), anchor='mm')
        items = data['cat_items'].get(cat, [])
        iy = y + hdr_h + 20
        for it in items[:6]:
            if iy + 36 > y + h - 8: break
            d.ellipse([x+20, iy+10, x+33, iy+23], fill=col)
            d.text((x+46, iy+4), fit_text(d, it, f(22), w-60), font=f(22), fill=MUTED)
            iy += 38

    if n == 1:
        _cat_box(bm, by, W-bm*2, bh, cats[0], cat_c[0])
    elif n == 2:
        bw = (W-bm*2-bg)//2
        _cat_box(bm, by, bw, bh, cats[0], cat_c[0])
        _cat_box(bm+bw+bg, by, bw, bh, cats[1], cat_c[1])
    else:
        bw2 = (W-bm*2-bg)//2; bh1 = int(bh*0.50); bh2 = bh-bh1-bg
        _cat_box(bm,        by,        bw2,    bh1, cats[0], cat_c[0])
        _cat_box(bm+bw2+bg, by,        bw2,    bh1, cats[1], cat_c[1])
        _cat_box(bm,        by+bh1+bg, W-bm*2, bh2, cats[2], cat_c[2])

    p = BUILD/"slides"/"00_intro.png"; img.save(str(p)); return str(p)

def make_news_slide(item, data):
    img = Image.new('RGB', (W, H), BG)
    d   = ImageDraw.Draw(img)
    draw_tab_bar(d, data['tabs'], item['cat'])
    draw_title(d, item['title'])
    draw_cards(img, d, item['cards'])
    p = BUILD/"slides"/f"{item['num']:02d}_news.png"
    img.save(str(p)); return str(p)

def make_outro_slide(data):
    img = Image.new('RGB', (W, H), BG)
    d   = ImageDraw.Draw(img)
    draw_tab_bar(d, data['tabs'], 'Outro')
    for r in range(280, 0, -20):
        ratio = r/280
        col   = (int(10+129*(1-ratio)), 10, int(20+226*(1-ratio)))
        d.ellipse([W//2-r, H//2-r-30, W//2+r, H//2+r-30], fill=col)
    d.text((W//2, H//2-40), "感谢收听 · 明日见", font=f(72), fill=WHITE, anchor='mm')
    d.text((W//2, H//2+50), "AI 资讯播客  ·  作者 Bunny  ·  哔哩哔哩同名",
           font=f(30), fill=MUTED, anchor='mm')
    d.text((W//2, H//2+110), data['date'], font=f(24), fill=DIM, anchor='mm')
    p = BUILD/"slides"/"99_outro.png"; img.save(str(p)); return str(p)

# ── Main ─────────────────────────────────────────────────────────────────────
async def main():
    print("="*60)
    print("  video-pipeline v5  (NotebookLM dark + subtitles)")
    print("="*60)

    print("\nC1 — 解析文章...")
    data = parse(ARTICLE)
    print(f"  ✅ {len(data['news'])} 条新闻  |  标题: {data['title']}")
    print(f"  Tabs: {' | '.join(data['tabs'])}")

    print("\nC2 — TTS 配音 + 字幕提取 (明明 · YunxiNeural)...")
    asegs = []

    # Intro: 固定开场句式，使用短节目名
    intro_text = f"欢迎收看{data['show_name']}，本期带来 {data['total']} 条热门资讯。"
    intro_ap  = str(BUILD/"audio"/"00_intro.mp3")
    intro_ass = str(BUILD/"subs"/"00_intro.ass")
    intro_dur = await tts_with_subs(intro_text, intro_ap, intro_ass)
    print(f"  ✅ intro  {intro_dur:.1f}s  「{intro_text[:28]}…」")

    # News items — 读摘要正文（已剥离"摘要："前缀）
    for item in data['news']:
        ap  = str(BUILD/"audio"/f"{item['num']:02d}.mp3")
        ass = str(BUILD/"subs"/f"{item['num']:02d}.ass")
        dur = await tts_with_subs(item['summary'], ap, ass)
        asegs.append({'p': ap, 'ass': ass, 'dur': dur, 'num': item['num']})
        print(f"  ✅ 新闻{item['num']}  {dur:.1f}s  {item['title'][:22]}…")

    # Outro
    outro_text = "感谢收听，明天见。"
    outro_ap  = str(BUILD/"audio"/"99_outro.mp3")
    outro_ass = str(BUILD/"subs"/"99_outro.ass")
    outro_dur = await tts_with_subs(outro_text, outro_ap, outro_ass)
    print(f"  ✅ outro  {outro_dur:.1f}s")

    print("\nC2.5 — 解析素材...")
    for item in data['news']:
        # Resolve all media paths (up to 2)
        paths = [resolve_media(r, item['num']) for r in item.get('media_raws', [item['media_raw']])]
        paths = [p for p in paths if p]   # drop None
        item['media_paths'] = paths
        item['media_path']  = paths[0] if paths else None
        if paths:
            types = '+'.join(media_type(p) or '?' for p in paths)
            print(f"  新闻{item['num']}: {types} ✅ ({len(paths)}张)  {paths[0][:50]}")
        else:
            print(f"  新闻{item['num']}: 无素材")

    print("\nC3 — 生成 Slides...")
    slides = {}
    slides['intro'] = make_intro_slide(data); print("  ✅ 开场")
    for item in data['news']:
        slides[item['num']] = make_news_slide(item, data)
        print(f"  ✅ 新闻{item['num']}: {item['title'][:28]}")
    slides['outro'] = make_outro_slide(data); print("  ✅ 结尾")

    print("\nC4 — 编码视频段 (烧录字幕)...")
    segs = []

    # Intro segment
    sp = str(BUILD/"seg_00_intro.mp4")
    make_seg(slides['intro'], intro_ap, intro_dur + 0.5, sp, ass=intro_ass)
    segs.append(sp)
    print(f"  ✅ intro {intro_dur+0.5:.1f}s")

    # News segments with inline media overlay
    for i, item in enumerate(data['news']):
        ai    = asegs[i]; dur = ai['dur'] + 0.8
        sp    = str(BUILD/f"seg_{item['num']:02d}.mp4")
        mps   = item.get('media_paths', [])   # list of resolved paths
        mp    = mps[0] if mps else None
        mt    = media_type(mp) if mp else None

        # Determine if all paths are static images (eligible for side-by-side panel)
        all_images = all(media_type(p) == 'image' for p in mps) if mps else False

        if mps and all_images:
            od  = calc_overlay_dur(dur, 'image')
            n   = len(mps)
            make_seg_with_overlay(slides[item['num']], ai['p'], dur, sp,
                                  ass=ai['ass'], media_path=mps)
            print(f"  ✅ 新闻{item['num']} {dur:.1f}s [image×{n} overlay {od:.1f}s]")
        elif mp and mt == 'gif':
            od = calc_overlay_dur(dur, 'gif')
            make_gif_overlay_seg(slides[item['num']], ai['p'], dur, sp,
                                 media_path=mp, ass=ai['ass'])
            print(f"  ✅ 新闻{item['num']} {dur:.1f}s [gif overlay {od:.1f}s]")
        elif mp and mt == 'video':
            clip_dur  = video_duration(mp)
            available = dur - MEDIA_OVERLAY_START - 0.6
            od = round(min(clip_dur, available), 2) if clip_dur else calc_overlay_dur(dur, 'gif')
            make_gif_overlay_seg(slides[item['num']], ai['p'], dur, sp,
                                 media_path=mp, ass=ai['ass'])
            print(f"  ✅ 新闻{item['num']} {dur:.1f}s [video overlay {od:.1f}s]")
        else:
            make_seg(slides[item['num']], ai['p'], dur, sp, ass=ai['ass'])
            print(f"  ✅ 新闻{item['num']} {dur:.1f}s")
        segs.append(sp)

    # Outro segment
    sp = str(BUILD/"seg_99_outro.mp4")
    make_seg(slides['outro'], outro_ap, outro_dur + 0.5, sp, ass=outro_ass)
    segs.append(sp); print(f"  ✅ outro {outro_dur+0.5:.1f}s")

    print("\nC4b — concat...")
    cf  = str(BUILD/"concat.txt")
    Path(cf).write_text('\n'.join(f"file '{s}'" for s in segs) + '\n')
    out = str(OUT_DIR/"video_notebooklm.mp4")
    r   = subprocess.run(
        ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', cf, '-c', 'copy', out],
        capture_output=True, text=True)
    if r.returncode != 0:
        print("  ⚠️  copy failed → re-encode")
        r = subprocess.run(
            ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', cf,
             '-c:v', 'libx264', '-crf', '23',
             '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2', out],
            capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  ❌ {r.stderr[-400:]}"); return
    print(f"  ✅ {out}")

    print("\nC5 — 质量检验...")
    probe = json.loads(subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json',
         '-show_streams', '-show_format', out],
        capture_output=True, text=True).stdout)
    fmt    = probe['format']
    dur    = float(fmt['duration'])
    size   = int(fmt['size']) / 1024 / 1024
    vs     = next((s for s in probe['streams'] if s['codec_type'] == 'video'), {})
    wd, ht = vs.get('width', 0), vs.get('height', 0)
    mins, secs = int(dur//60), int(dur%60)
    print(f"""
✅ 视频生成完成
路径：{out}
时长：{mins}分{secs}秒 | 大小：{size:.1f}MB | {wd}×{ht} | 字幕：已烧录
""")

asyncio.run(main())
