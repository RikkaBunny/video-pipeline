#!/usr/bin/env python3
"""
convert_sketch_v2.py — 手绘 SVG 风格 v2（Pass 1 降密 + Pass 4 视觉一致性）

相对 v1 的变化：
  - 每条新闻拆成 slide_A（读图，大 media 占屏，Ken Burns）+ slide_B（3 张卡片，留白）
  - 每条新闻的 TTS 不拆，但用 ffmpeg -ss/-t 把音频切两段分别喂给 A/B
  - ASS 字幕只用于 slide_B，时间戳整体前移 -t_a
  - slide_A 奇偶交替布局：奇=左图右标题，偶=上图下标题
  - 4 色调色板锁定 + Mascot 兔子 + Long Cang 手写字体
  - 独立 build 目录 /tmp/vp_sketch_v2，输出 video_sketch_v2.mp4
"""
import re, asyncio, subprocess, json, urllib.request, shutil, html as _html
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ── Config ────────────────────────────────────────────────────────────────────
import sys
ARTICLE = sys.argv[1] if len(sys.argv) > 1 else \
          "/root/video-pipeline/output/2026-04-09/ai/article.md"
OUT_DIR  = Path(ARTICLE).parent
BUILD    = Path("/tmp/vp_sketch_v2")
FONT     = "/root/video-pipeline/assets/fonts/NotoSansSC-Regular.ttf"
W, H     = 1920, 1080

for sub in ["audio", "slides", "subs", "media", "html"]:
    (BUILD / sub).mkdir(parents=True, exist_ok=True)

# ── ASS subtitle (字幕) ───────────────────────────────────────────────────────
ASS_FONT     = "Noto Sans CJK SC"
ASS_FONTSIZE = 72
ASS_MARGIN_H = 80
ASS_MARGIN_V = 52
ASS_MAX_W    = W - ASS_MARGIN_H * 2

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
""".format(W=W, H=H, font=ASS_FONT, sz=ASS_FONTSIZE,
           mh=ASS_MARGIN_H, mv=ASS_MARGIN_V)

_sub_fnt = None
def _get_sub_fnt():
    global _sub_fnt
    if _sub_fnt is None:
        _sub_fnt = ImageFont.truetype(FONT, ASS_FONTSIZE)
    return _sub_fnt

def _measure(text):
    bbox = _get_sub_fnt().getbbox(text)
    return (bbox[2] - bbox[0]) if bbox else 0

def _ass_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def _split_to_single_lines(text, max_px=ASS_MAX_W):
    lines, cur = [], ''
    for ch in text:
        if _measure(cur + ch) > max_px and cur:
            lines.append(cur.strip()); cur = ch
        else:
            cur += ch
    if cur.strip(): lines.append(cur.strip())
    return lines or [text]

def _char_weight(ch):
    if ch in '。！？…': return 2.2
    if ch in '，、；：': return 1.6
    if ch == ' ':        return 0.5
    return 1.0

def _split_sentence_timed(text, start, end):
    lines = _split_to_single_lines(text)
    if len(lines) == 1:
        return [{'text': text.strip(), 'start': start, 'end': end}]
    weights   = [sum(_char_weight(c) for c in ln) for ln in lines]
    total_w   = sum(weights) or 1
    dur, chunks, t = end - start, [], start
    for ln, w in zip(lines, weights):
        chunk_end = t + dur * w / total_w
        chunks.append({'text': ln.strip(), 'start': t, 'end': chunk_end})
        t = chunk_end
    return chunks

def sentences_to_ass(sentences, ass_path):
    events = []
    for s in sentences:
        text = s['text'].strip()
        if not text: continue
        for chunk in _split_sentence_timed(text, s['start'], s['end']):
            events.append(
                f"Dialogue: 0,{_ass_time(chunk['start'])},{_ass_time(chunk['end'])},"
                f"Default,,0,0,0,,{chunk['text']}")
    Path(ass_path).write_text(ASS_HEADER + '\n'.join(events) + '\n', encoding='utf-8')

# ── TTS ───────────────────────────────────────────────────────────────────────
VOICE = "zh-CN-YunxiNeural"

async def tts_with_subs(text, audio_out, ass_out, rate="+5%"):
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

# ── Parse article.md ──────────────────────────────────────────────────────────
def strip_md(t):
    t = re.sub(r'^\*\*摘要\*\*[^：:]*[：:]\s*', '', t, flags=re.MULTILINE)
    t = re.sub(r'^摘要[^：:]*[：:]\s*',         '', t, flags=re.MULTILINE)
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)
    t = re.sub(r'`(.+?)`',        r'\1', t)
    t = re.sub(r'^>\s*',          '', t, flags=re.MULTILINE)
    return t.strip()

def parse(path):
    txt = Path(path).read_text(encoding='utf-8')
    fm  = {}
    m   = re.match(r'^---\n(.+?)\n---\n', txt, re.DOTALL)
    if m:
        for ln in m.group(1).splitlines():
            if ':' in ln:
                k, v = ln.split(':', 1)
                fm[k.strip()] = v.strip().strip('"')
    date_str = fm.get('date', '2026-04-09')
    title_fm = fm.get('title', 'AI 资讯')
    bm = re.search(r'【([^】]+)】', title_fm)
    show_name = re.sub(r'\d{4}-\d{2}-\d{2}', '', bm.group(1)).strip() if bm else 'AI 资讯'

    cat_order, num2cat, cat_items = [], {}, {}
    ov = re.search(r'## 概览\n(.+?)^---', txt, re.DOTALL | re.MULTILINE)
    if ov:
        cur = None
        for ln in ov.group(1).splitlines():
            h3 = re.match(r'^###\s+(.+)', ln)
            if h3:
                cur = h3.group(1).strip(); cat_order.append(cur); cat_items[cur] = []
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
        for m3 in re.finditer(r'^-\s+(.+?)\s+\*\*(.+?)\*\*[：:]\s*(.+)$', sec, re.MULTILINE):
            cards.append({'emoji': m3.group(1), 'title': m3.group(2), 'body': strip_md(m3.group(3))})
        media_raws = [m.strip() for m in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', sec)][:2]
        news.append({'num': num, 'title': title, 'summary': summary,
                     'cards': cards[:5],
                     'cat': num2cat.get(num, cat_order[0] if cat_order else ''),
                     'media_raw': media_raws[0] if media_raws else '',
                     'media_raws': media_raws})
    news.sort(key=lambda x: x['num'])
    tabs = ['开场'] + cat_order + ['结尾']
    return {'date': date_str, 'title': title_fm, 'show_name': show_name,
            'cat_order': cat_order, 'cat_items': cat_items,
            'news': news, 'total': len(news), 'tabs': tabs}

# ── Media helpers ─────────────────────────────────────────────────────────────
MEDIA_EXTS_IMAGE = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
MEDIA_EXTS_GIF   = {'.gif'}
MEDIA_EXTS_VIDEO = {'.mp4', '.mov', '.webm', '.mkv'}

def resolve_media(raw, num):
    if not raw: return None
    if raw.startswith('http'):
        ext  = Path(raw.split('?')[0]).suffix.lower() or '.jpg'
        dest = BUILD / 'media' / f'{num:02d}_asset{ext}'
        if dest.exists(): return str(dest)
        try:
            req = urllib.request.Request(raw, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as r, open(dest, 'wb') as f:
                shutil.copyfileobj(r, f)
            return str(dest)
        except Exception as e:
            print(f"    ⚠️  下载失败: {e}"); return None
    p = Path(raw)
    if p.exists(): return str(p)
    p2 = OUT_DIR / raw
    return str(p2) if p2.exists() else None

def media_type(path):
    if not path: return None
    ext = Path(path).suffix.lower()
    if ext in MEDIA_EXTS_GIF:   return 'gif'
    if ext in MEDIA_EXTS_VIDEO: return 'video'
    if ext in MEDIA_EXTS_IMAGE: return 'image'
    return None

# ═══════════════════════════════════════════════════════════════════════════════
#  手绘 SVG 风格 Slide 生成器
# ═══════════════════════════════════════════════════════════════════════════════

SKETCH_COLORS = [
    ('#C17F6E', '#F5EBE7'),   # 莫兰迪赭红
    ('#7B9EA8', '#E8F0F2'),   # 莫兰迪雾蓝
    ('#8FA87B', '#EAF0E6'),   # 莫兰迪灰绿
    ('#9B89A8', '#EDE8F0'),   # 莫兰迪灰紫
    ('#C4956A', '#F3ECE4'),   # 莫兰迪焦糖
]

CARTOON_SPARKS = ['star', 'circle', 'diamond', 'heart', 'triangle']

CAT_ICONS = {
    '开发生态': '⚙️', '模型发布': '🤖', '行业动态': '📈',
    '技术与洞察': '💡', '产品应用': '🚀', '开源社区': '🌐',
    '安全与隐私': '🔒', '硬件与芯片': '🖥️', '要闻': '📰',
}

def esc(s): return _html.escape(str(s))

BASE_FONT = "'NotoSansSC', 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif"
FONT_FACE = f"@font-face {{ font-family:'NotoSansSC'; src:url('file://{FONT}'); }}"

# ── v2: 4 色调色板锁定 ────────────────────────────────────────────────────────
PALETTE_V2 = {
    "bg_cream":     "#F5F0EB",   # 奶油背景
    "ink_clay":     "#C75B3E",   # 陶土红（主标题 / 强调）
    "ink_teal":     "#3D7B86",   # 茶蓝（次强调）
    "ink_charcoal": "#4A4440",   # 正文 / 字体主色
    "line_soft":    "#D5CFC6",   # 淡描边
    "accent_bg":    "#F5EBE7",   # 卡片内填色
}

# ── v2: 标题字体（回退到 NotoSansSC 粗体以保证可读性）───────────────────────
# 注：之前尝试 Long Cang 手写字体，实测笔画过于歪扭导致可读性差，改回等线粗体
# 视觉"手绘"感由 rough filter + 配色 + mascot 提供，不依赖手写字体
HAND_FONT_FACE = ""  # 保留常量避免引用报错
HAND_FONT_FAMILY = BASE_FONT

# ── v2: Mascot（手绘小兔子）──────────────────────────────────────────────────
def _mascot_svg(expression='happy', x=0, y=0, size=120):
    """简易手绘兔子头像 SVG。expression: happy|thinking|surprise|point。"""
    s = size / 120.0  # 缩放因子
    cx, cy = x + 60 * s, y + 72 * s
    # 耳朵路径（左右各一只）
    ears = (
        f'<ellipse cx="{x+35*s}" cy="{y+28*s}" rx="{10*s}" ry="{26*s}" '
        f'transform="rotate(-12 {x+35*s} {y+28*s})" '
        f'fill="#FAF4EE" stroke="{PALETTE_V2["ink_charcoal"]}" stroke-width="2.2" filter="url(#rough)"/>'
        f'<ellipse cx="{x+85*s}" cy="{y+28*s}" rx="{10*s}" ry="{26*s}" '
        f'transform="rotate(12 {x+85*s} {y+28*s})" '
        f'fill="#FAF4EE" stroke="{PALETTE_V2["ink_charcoal"]}" stroke-width="2.2" filter="url(#rough)"/>'
        f'<ellipse cx="{x+35*s}" cy="{y+32*s}" rx="{5*s}" ry="{18*s}" '
        f'transform="rotate(-12 {x+35*s} {y+32*s})" fill="{PALETTE_V2["ink_clay"]}" opacity="0.35"/>'
        f'<ellipse cx="{x+85*s}" cy="{y+32*s}" rx="{5*s}" ry="{18*s}" '
        f'transform="rotate(12 {x+85*s} {y+32*s})" fill="{PALETTE_V2["ink_clay"]}" opacity="0.35"/>'
    )
    # 脸部
    face = (
        f'<ellipse cx="{cx}" cy="{cy}" rx="{38*s}" ry="{34*s}" '
        f'fill="#FAF4EE" stroke="{PALETTE_V2["ink_charcoal"]}" stroke-width="2.5" filter="url(#rough)"/>'
    )
    # 腮红
    blush = (
        f'<ellipse cx="{cx-22*s}" cy="{cy+6*s}" rx="{6*s}" ry="{4*s}" fill="{PALETTE_V2["ink_clay"]}" opacity="0.35"/>'
        f'<ellipse cx="{cx+22*s}" cy="{cy+6*s}" rx="{6*s}" ry="{4*s}" fill="{PALETTE_V2["ink_clay"]}" opacity="0.35"/>'
    )
    # 眼睛 + 嘴巴（按表情变化）
    if expression == 'happy':
        eyes = (
            f'<path d="M {cx-14*s} {cy-6*s} q {6*s} -{6*s} {12*s} 0" stroke="{PALETTE_V2["ink_charcoal"]}" '
            f'stroke-width="2.4" fill="none" stroke-linecap="round"/>'
            f'<path d="M {cx+2*s} {cy-6*s} q {6*s} -{6*s} {12*s} 0" stroke="{PALETTE_V2["ink_charcoal"]}" '
            f'stroke-width="2.4" fill="none" stroke-linecap="round"/>'
        )
        mouth = (
            f'<path d="M {cx-5*s} {cy+10*s} q {5*s} {5*s} {10*s} 0" stroke="{PALETTE_V2["ink_charcoal"]}" '
            f'stroke-width="2.2" fill="none" stroke-linecap="round"/>'
        )
    elif expression == 'thinking':
        eyes = (
            f'<circle cx="{cx-8*s}" cy="{cy-4*s}" r="{2.5*s}" fill="{PALETTE_V2["ink_charcoal"]}"/>'
            f'<circle cx="{cx+8*s}" cy="{cy-4*s}" r="{2.5*s}" fill="{PALETTE_V2["ink_charcoal"]}"/>'
        )
        mouth = (
            f'<line x1="{cx-3*s}" y1="{cy+12*s}" x2="{cx+3*s}" y2="{cy+12*s}" '
            f'stroke="{PALETTE_V2["ink_charcoal"]}" stroke-width="2.2" stroke-linecap="round"/>'
        )
    elif expression == 'surprise':
        eyes = (
            f'<circle cx="{cx-8*s}" cy="{cy-4*s}" r="{4*s}" fill="#FFF" '
            f'stroke="{PALETTE_V2["ink_charcoal"]}" stroke-width="2"/>'
            f'<circle cx="{cx-8*s}" cy="{cy-4*s}" r="{1.8*s}" fill="{PALETTE_V2["ink_charcoal"]}"/>'
            f'<circle cx="{cx+8*s}" cy="{cy-4*s}" r="{4*s}" fill="#FFF" '
            f'stroke="{PALETTE_V2["ink_charcoal"]}" stroke-width="2"/>'
            f'<circle cx="{cx+8*s}" cy="{cy-4*s}" r="{1.8*s}" fill="{PALETTE_V2["ink_charcoal"]}"/>'
        )
        mouth = (
            f'<ellipse cx="{cx}" cy="{cy+12*s}" rx="{3*s}" ry="{4*s}" fill="{PALETTE_V2["ink_clay"]}"/>'
        )
    else:  # point
        eyes = (
            f'<circle cx="{cx-8*s}" cy="{cy-4*s}" r="{2.5*s}" fill="{PALETTE_V2["ink_charcoal"]}"/>'
            f'<circle cx="{cx+8*s}" cy="{cy-4*s}" r="{2.5*s}" fill="{PALETTE_V2["ink_charcoal"]}"/>'
        )
        mouth = (
            f'<path d="M {cx-4*s} {cy+12*s} q {4*s} {3*s} {8*s} 0" stroke="{PALETTE_V2["ink_charcoal"]}" '
            f'stroke-width="2.2" fill="none" stroke-linecap="round"/>'
        )
    return (
        f'<g class="mascot" style="animation:fadeUp .8s ease .4s both;opacity:0">'
        f'{ears}{face}{blush}{eyes}{mouth}'
        f'</g>'
    )

# ── v2: 标题 + #N 内嵌渲染 ────────────────────────────────────────────────────
def _v2_title_with_num(title, num_label, x, y_first, font_size, max_px, color,
                        lh, max_lines=3, anchor='start'):
    """渲染 '#N 标题第一行 / 标题第二行...'，#N 内嵌于第一行起始位置。
    返回 (svg_str, used_lines, total_height)
    """
    # #N 用 78% 标题字号，同色同字体
    num_fs = int(font_size * 0.78)
    # #N 视觉宽度估算：每字符 0.55 个 num_fs 单位 + 末尾间隔
    num_text_w = len(num_label) * num_fs * 0.6
    gap = int(font_size * 0.35)
    first_line_offset = num_text_w + gap

    # 第一行可用宽度更窄
    first_max_u = max(1, (max_px - first_line_offset) / font_size)
    other_max_u = max_px / font_size

    # 先按窄宽度折一次拿第一行，再用宽宽度折剩余文字
    initial_lines = _wrap_text_svg(title, first_max_u)
    if len(initial_lines) <= 1:
        lines = initial_lines
    else:
        first_line = initial_lines[0]
        rest = title[len(first_line):].lstrip()
        rest_lines = _wrap_text_svg(rest, other_max_u) if rest else []
        lines = [first_line] + rest_lines
    lines = lines[:max_lines]

    parts = []
    # #N 标签
    parts.append(
        f'<text x="{x}" y="{y_first}" font-size="{num_fs}" font-weight="bold" '
        f'fill="{color}" font-family={BASE_FONT!r} text-anchor="{anchor}" '
        f'class="ttl">{esc(num_label)}</text>'
    )
    # 第一行标题（右移一个 num 宽度）
    parts.append(
        f'<text x="{x + first_line_offset}" y="{y_first}" font-size="{font_size}" '
        f'font-weight="bold" fill="{color}" font-family={BASE_FONT!r} '
        f'text-anchor="{anchor}" class="ttl">{esc(lines[0])}</text>'
    )
    # 后续行回到 x 起始位置
    for li in range(1, len(lines)):
        parts.append(
            f'<text x="{x}" y="{y_first + li*lh}" font-size="{font_size}" '
            f'font-weight="bold" fill="{color}" font-family={BASE_FONT!r} '
            f'text-anchor="{anchor}" class="ttl2">{esc(lines[li])}</text>'
        )
    return '\n'.join(parts), len(lines)

# mascot 表情按分类映射
MASCOT_BY_CAT = {
    "开源动态": "happy",
    "开源生态": "happy",
    "模型发布": "happy",
    "Agent 能力": "thinking",
    "Agent能力": "thinking",
    "开发生态": "thinking",
    "研究前沿": "surprise",
    "技术与洞察": "surprise",
    "要闻": "point",
    "行业动态": "point",
    "产品应用": "point",
}
def _mascot_for_cat(cat):
    return MASCOT_BY_CAT.get(cat, "happy")

# ── v2: ASS 字幕时间戳前移 ────────────────────────────────────────────────────
def shift_ass(src_path, dst_path, shift_sec):
    """把 ASS 字幕所有 Dialogue 行的时间戳向前移 shift_sec 秒（负数则向后）。"""
    if shift_sec == 0:
        shutil.copy(src_path, dst_path)
        return
    def _ts_to_cs(ts):
        h, m, rest = ts.split(':')
        s, cs = rest.split('.')
        return ((int(h) * 3600 + int(m) * 60 + int(s)) * 100) + int(cs)
    def _cs_to_ts(cs):
        if cs < 0: cs = 0
        h = cs // 360000
        m = (cs % 360000) // 6000
        s = (cs % 6000) // 100
        c = cs % 100
        return f"{h}:{m:02d}:{s:02d}.{c:02d}"
    shift_cs = int(shift_sec * 100)
    out_lines = []
    with open(src_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('Dialogue:'):
                parts = line.split(',', 9)
                if len(parts) >= 3:
                    start_cs = _ts_to_cs(parts[1].strip()) - shift_cs
                    end_cs   = _ts_to_cs(parts[2].strip()) - shift_cs
                    if end_cs <= 0:
                        continue   # 整个字幕事件在切点之前，丢弃
                    parts[1] = _cs_to_ts(max(0, start_cs))
                    parts[2] = _cs_to_ts(end_cs)
                    line = ','.join(parts)
            out_lines.append(line)
    with open(dst_path, 'w', encoding='utf-8') as f:
        f.writelines(out_lines)

# ── SVG 工具函数 ──────────────────────────────────────────────────────────────
def _char_width(ch):
    """估算单个字符的相对宽度：CJK=1.0, 拉丁/数字=0.55, 标点=0.5"""
    cp = ord(ch)
    if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
        0xF900 <= cp <= 0xFAFF or 0x2E80 <= cp <= 0x2FDF or
        0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF):
        return 1.0
    elif ch in '，。！？；：、""''（）《》【】':
        return 1.0
    else:
        return 0.55

def _wrap_text_svg(text, max_width_units):
    """按视觉宽度折行，区分中英文宽度。不拆断英文单词。"""
    lines = []
    cur = ''
    cur_w = 0
    i = 0
    while i < len(text):
        ch = text[i]
        cw = _char_width(ch)

        # 如果是拉丁字符，收集整个英文单词
        if ch.isascii() and ch.isalnum():
            word = ''
            word_w = 0
            j = i
            while j < len(text) and text[j].isascii() and (text[j].isalnum() or text[j] in '-_'):
                word += text[j]
                word_w += _char_width(text[j])
                j += 1
            # 单词加上去会超宽，且当前行非空 → 先换行
            if cur_w + word_w > max_width_units and cur.strip():
                lines.append(cur)
                cur = word
                cur_w = word_w
            else:
                cur += word
                cur_w += word_w
            i = j
            continue

        if cur_w + cw > max_width_units and cur.strip():
            lines.append(cur)
            cur = ch
            cur_w = cw
        else:
            cur += ch
            cur_w += cw
        i += 1

    if cur.strip():
        lines.append(cur)
    return lines or ['']

def _svg_multiline(text, x, y, chars, size, fill, lh, anchor='start', attrs=''):
    """将长文本拆成多行 SVG <text> 元素组成的字符串。"""
    lines = _wrap_text_svg(text, chars)
    parts = []
    for i, ln in enumerate(lines):
        parts.append(
            f'<text x="{x}" y="{y + i*lh}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-family={BASE_FONT!r} {attrs}>'
            f'{esc(ln)}</text>')
    return '\n'.join(parts)

def _wavy_underline(x1, y, width):
    """手绘风格波浪下划线 SVG path。"""
    segs, x, amp, period = [], x1, 4, 28
    while x < x1 + width:
        nx = min(x + period, x1 + width)
        cy = y - amp if ((x - x1) // period) % 2 == 0 else y + amp
        segs.append(f'Q{x + period/2:.0f},{cy:.0f} {nx:.0f},{y}')
        x = nx
    d = f'M{x1},{y} ' + ' '.join(segs)
    return f'<path d="{d}" fill="none" stroke="#B8907A" stroke-width="2.5" stroke-linecap="round" opacity="0.9"/>'

def _tab_bar_svg(tabs, active, y=0, height=52):
    """顶部动态 Tab 栏 SVG（与 NotebookLM 版对齐）。"""
    tab_w  = W // max(len(tabs), 1)
    parts  = []
    # 背景条
    parts.append(f'<rect x="0" y="{y}" width="{W}" height="{height}" fill="#EAE4DD"/>')
    for i, tab in enumerate(tabs):
        tx  = i * tab_w
        is_a = (tab == active)
        bg  = '#F0EBE4' if is_a else '#E0DBD4'
        bdr = '#B8907A' if is_a else '#C5BFB6'
        parts.append(
            f'<rect x="{tx}" y="{y}" width="{tab_w}" height="{height}" '
            f'fill="{bg}" stroke="{bdr}" stroke-width="{2 if is_a else 1}"/>')
        fc  = '#1a1a1a' if is_a else '#888'
        fw  = 'bold' if is_a else 'normal'
        fsz = 26 if is_a else 24
        parts.append(
            f'<text x="{tx + tab_w//2}" y="{y + height//2 + 9}" text-anchor="middle" '
            f'font-size="{fsz}" font-weight="{fw}" fill="{fc}" '
            f'font-family={BASE_FONT!r}>{esc(tab)}</text>')
        if is_a:
            parts.append(
                f'<rect x="{tx+4}" y="{y+height-4}" width="{tab_w-8}" height="4" '
                f'fill="#B8907A" rx="2"/>')
        if i:
            parts.append(
                f'<line x1="{tx}" y1="{y+4}" x2="{tx}" y2="{y+height-4}" '
                f'stroke="#C5BFB6" stroke-width="1"/>')
    return '\n'.join(parts)

def _spark_svg(shape, x, y, color, size=14, anim=''):
    """用 SVG 图形绘制右上角装饰，避免特殊字符不渲染的问题。"""
    s = size
    style = f'style="{anim}"' if anim else ''
    if shape == 'star':
        # 五角星
        pts = []
        import math as _m
        for i in range(5):
            a = _m.radians(-90 + i * 72)
            pts.append(f'{x + s*_m.cos(a):.1f},{y + s*_m.sin(a):.1f}')
            a2 = _m.radians(-90 + i * 72 + 36)
            pts.append(f'{x + s*0.45*_m.cos(a2):.1f},{y + s*0.45*_m.sin(a2):.1f}')
        return f'<polygon points="{" ".join(pts)}" fill="{color}" opacity="0.5" {style}/>'
    elif shape == 'circle':
        return f'<circle cx="{x}" cy="{y}" r="{s*0.7}" fill="{color}" opacity="0.4" {style}/>'
    elif shape == 'diamond':
        pts = f'{x},{y-s} {x+s*0.7},{y} {x},{y+s} {x-s*0.7},{y}'
        return f'<polygon points="{pts}" fill="{color}" opacity="0.45" {style}/>'
    elif shape == 'heart':
        # 简化心形
        return (f'<circle cx="{x-s*0.3}" cy="{y-s*0.2}" r="{s*0.45}" fill="{color}" opacity="0.4" {style}/>'
                f'<circle cx="{x+s*0.3}" cy="{y-s*0.2}" r="{s*0.45}" fill="{color}" opacity="0.4" {style}/>'
                f'<polygon points="{x},{y+s*0.6} {x-s*0.65},{y-s*0.05} {x+s*0.65},{y-s*0.05}" fill="{color}" opacity="0.4" {style}/>')
    else:  # triangle
        pts = f'{x},{y-s} {x+s*0.85},{y+s*0.6} {x-s*0.85},{y+s*0.6}'
        return f'<polygon points="{pts}" fill="{color}" opacity="0.45" {style}/>'


def _card_svg(idx, emoji, title, body, x, y, card_w, card_h, delay=0.0):
    """单张要点卡片 SVG（卡通手绘风格：大圆角 + 柔和阴影 + 彩色顶条）。"""
    accent, accent_bg = SKETCH_COLORS[idx % len(SKETCH_COLORS)]
    spark  = CARTOON_SPARKS[idx % len(CARTOON_SPARKS)]
    r      = 26    # 大圆角，更卡通
    shadow_dx = 5
    shadow_dy = 6

    parts = []
    anim = f'animation:popIn 0.45s cubic-bezier(0.34,1.56,0.64,1) {delay:.2f}s both'

    # 柔和阴影（颜色偏向卡片主色调）
    parts.append(
        f'<rect x="{x+shadow_dx}" y="{y+shadow_dy}" width="{card_w}" height="{card_h}" '
        f'rx="{r}" fill="rgba(0,0,0,0.08)" style="{anim}"/>')
    # 卡片主体（大圆角，细边框）
    parts.append(
        f'<rect x="{x}" y="{y}" width="{card_w}" height="{card_h}" rx="{r}" '
        f'fill="white" stroke="#D5CFC6" stroke-width="2" '
        f'filter="url(#rough)" style="{anim}"/>')
    # 彩色顶条（更厚，与大圆角匹配）
    parts.append(
        f'<path d="M{x+r},{y} L{x+card_w-r},{y} Q{x+card_w},{y} {x+card_w},{y+r} '
        f'L{x+card_w},{y+10} L{x},{y+10} L{x},{y+r} Q{x},{y} {x+r},{y} Z" '
        f'fill="{accent}" style="{anim}"/>')
    # emoji — 用彩色圆形背景衬托（更大）
    ec_x, ec_y, ec_r = x + 44, y + 56, 30
    parts.append(
        f'<circle cx="{ec_x}" cy="{ec_y}" r="{ec_r}" fill="{accent_bg}" style="{anim}"/>')
    parts.append(
        f'<text x="{ec_x}" y="{ec_y+10}" text-anchor="middle" font-size="38" style="{anim}">{emoji}</text>')
    # 标题（30px）
    title_fsz = 30
    title_max_w = (card_w - 120) / title_fsz
    title_lines = _wrap_text_svg(title, title_max_w)
    for li, ln in enumerate(title_lines[:2]):
        parts.append(
            f'<text x="{x+86}" y="{y+48+li*36}" font-size="{title_fsz}" font-weight="bold" '
            f'fill="{accent}" font-family={BASE_FONT!r} style="{anim}">{esc(ln)}</text>')
    # 分隔线（圆点风格，更卡通）
    sep_y = y + 88
    parts.append(
        f'<line x1="{x+18}" y1="{sep_y}" x2="{x+card_w-18}" y2="{sep_y}" '
        f'stroke="{accent}" stroke-width="1.5" stroke-dasharray="3,6" '
        f'stroke-linecap="round" opacity="0.35" style="{anim}"/>')
    # 正文（28px，黑色加粗，行高36px）— 严格裁剪防溢出
    body_fsz = 28
    body_lh = 36
    body_pad_x = 22
    body_start_y = sep_y + 28
    body_max_w = (card_w - body_pad_x * 2) / body_fsz
    body_lines = _wrap_text_svg(body, body_max_w)
    # 严格限制：正文底部不能超过卡片底部 - 16px padding
    max_body_lines = max(1, (y + card_h - 16 - body_start_y) // body_lh)
    for li, ln in enumerate(body_lines[:max_body_lines]):
        parts.append(
            f'<text x="{x+body_pad_x}" y="{body_start_y+li*body_lh}" '
            f'font-size="{body_fsz}" fill="#4A4440" font-weight="bold" '
            f'font-family={BASE_FONT!r} style="{anim}">{esc(ln)}</text>')
    # 右上角装饰（SVG图形，确保渲染）
    parts.append(_spark_svg(spark, x + card_w - 26, y + 28, accent, size=11, anim=anim))

    return '\n'.join(parts)

def _build_svg(title, tab_active, cards, extra='', all_tabs=None):
    """
    构建完整 SVG 页面（1920×1080）：
    - 温暖米白纸质背景 + 半色调网点图案
    - 粗边框面板 + draw 动画
    - Tab 栏（显示全部分类）
    - 标题区（带背景条，自动缩放字号）+ 波浪下划线
    - 要点卡片（3+2 布局，与 NotebookLM 版对齐）
    - extra: 额外 SVG 内容
    """
    css = f"""
    {FONT_FACE}
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ width:{W}px; height:{H}px; overflow:hidden; background:#F5F0EB; }}
    @keyframes fadeUp {{
        from {{ opacity:0; transform:translateY(10px); }}
        to   {{ opacity:1; transform:translateY(0); }}
    }}
    @keyframes popIn {{
        from {{ opacity:0; transform-origin:center; transform:scale(0.5) rotate(-3deg); }}
        to   {{ opacity:1; transform-origin:center; transform:scale(1)   rotate(0deg); }}
    }}
    @keyframes drawLine {{
        from {{ stroke-dashoffset:1200; }}
        to   {{ stroke-dashoffset:0; }}
    }}
    @keyframes borderDraw {{
        from {{ stroke-dashoffset:4000; }}
        to   {{ stroke-dashoffset:0; }}
    }}
    """

    # ── 布局参数（与 NotebookLM 版 convert.py 对齐） ──
    tab_h    = 52
    title_h  = 108
    content_y = tab_h + title_h
    content_h = H - content_y
    gap      = 22
    margin   = 52

    # ── 卡片布局：3+2，按比例分配高度 ──
    n        = min(5, len(cards))
    n_top    = min(3, n)
    n_bot    = max(0, n - n_top)
    avail_h  = content_h - margin * 2
    card_h_top = int(avail_h * 0.47) if n_bot else avail_h
    card_h_bot = avail_h - card_h_top - gap if n_bot else 0
    card_w_top = (W - margin * 2 - gap * (n_top - 1)) // n_top if n_top else W - margin * 2

    card_svgs = []
    positions = []
    for ci in range(n):
        if ci < n_top:
            cx = margin + ci * (card_w_top + gap)
            cy = content_y + margin
            positions.append((cx, cy, card_w_top, card_h_top))
        else:
            card_w_bot = (W - margin * 2 - gap * (n_bot - 1)) // n_bot
            cx = margin + (ci - n_top) * (card_w_bot + gap)
            cy = content_y + margin + card_h_top + gap
            positions.append((cx, cy, card_w_bot, card_h_bot))

    for ci, (card, (cx, cy, cw, ch)) in enumerate(zip(cards[:5], positions)):
        d = 0.3 + ci * 0.18
        card_svgs.append(_card_svg(
            ci, card['emoji'], card['title'], card['body'],
            cx, cy, cw, ch, delay=d))

    # ── 标题区：背景条 + 自动缩放字号（58→38px） ──
    # max_width_units = 可用像素宽 / 字号，即一行能容纳多少个CJK字符宽度
    max_px = W - margin * 2 - 20
    best_sz, best_mw = 44, max_px / 44.0
    for sz in [58, 50, 44, 38]:
        mw = max_px / float(sz)  # 以该字号为单位的最大行宽
        lines = _wrap_text_svg(title, mw)
        lh = int(sz * 1.22)
        if len(lines) == 1 or (len(lines) <= 2 and len(lines) * lh <= title_h - 12):
            best_sz, best_mw = sz, mw
            break

    title_lines = _wrap_text_svg(title, best_mw)[:2]
    title_lh = int(best_sz * 1.22)
    total_title_h = len(title_lines) * title_lh
    title_y0 = tab_h + (title_h - total_title_h) // 2 + title_lh // 2 + 4

    title_svg_parts = []
    for li, ln in enumerate(title_lines):
        title_svg_parts.append(
            f'<text x="{margin}" y="{title_y0 + li * title_lh}" '
            f'font-size="{best_sz}" font-weight="bold" dominant-baseline="middle" '
            f'fill="#B0735E" font-family={BASE_FONT!r} '
            f'style="animation:fadeUp .6s ease .12s both;opacity:0">'
            f'{esc(ln)}</text>')
    title_svg = '\n'.join(title_svg_parts)
    underline_y = title_y0 + (len(title_lines) - 1) * title_lh + title_lh // 2 + 4
    wavy = _wavy_underline(margin, underline_y, min(len(title_lines[-1]) * best_sz * 0.55, W - margin * 2))

    # ── 标题背景条 ──
    title_bar_svg = (
        f'<rect x="0" y="{tab_h}" width="{W}" height="{title_h}" fill="#F0EBE4"/>'
        f'<line x1="0" y1="{tab_h + title_h - 1}" x2="{W}" y2="{tab_h + title_h - 1}" '
        f'stroke="#D5CFC6" stroke-width="1"/>')

    # ── 粗边框面板（draw 动画）──
    pad = 18
    bx, by = pad, content_y + 8
    bw, bh = W - pad * 2, H - by - pad
    border_svg = (
        f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="16" '
        f'fill="rgba(245,240,235,0.92)" stroke="#8A8078" stroke-width="3.5" '
        f'stroke-dasharray="4000" '
        f'style="animation:borderDraw 1.2s ease .05s both" '
        f'filter="url(#rough)"/>')

    # ── Tab 栏：显示所有分类 ──
    if all_tabs:
        tabs = all_tabs
    else:
        tabs = ['开场', tab_active, '结尾']

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="0 0 {W} {H}" width="{W}" height="{H}"
     style="background:#F5F0EB;font-family:{BASE_FONT}">
  <defs>
    <style>{css}</style>
    <filter id="rough" x="-5%" y="-5%" width="110%" height="110%">
      <feTurbulence type="turbulence" baseFrequency="0.018" numOctaves="2" result="noise"/>
      <feDisplacementMap in="SourceGraphic" in2="noise" scale="2.2"
          xChannelSelector="R" yChannelSelector="G"/>
    </filter>
    <pattern id="dots" x="0" y="0" width="24" height="24" patternUnits="userSpaceOnUse">
      <circle cx="12" cy="12" r="1.4" fill="rgba(160,148,130,0.15)"/>
    </pattern>
  </defs>

  <!-- 网点底纹 -->
  <rect width="{W}" height="{H}" fill="url(#dots)"/>

  <!-- 面板边框 -->
  {border_svg}

  <!-- Tab 栏 -->
  {_tab_bar_svg(tabs, tab_active)}

  <!-- 标题背景 -->
  {title_bar_svg}

  <!-- 标题 -->
  {title_svg}
  {wavy}

  <!-- 要点卡片 -->
  {''.join(card_svgs)}

  {extra}
</svg>"""
    return svg

# ── 新闻幻灯片 ────────────────────────────────────────────────────────────────
def make_svg_news_slide(item, data):
    tab = item.get('cat', data['cat_order'][0] if data['cat_order'] else '资讯')
    return _build_svg(item['title'], tab, item['cards'], all_tabs=data.get('tabs'))

# ═══════════════════════════════════════════════════════════════════════════════
#  v2 SLIDE BUILDERS (Pass 1 + Pass 4)
# ═══════════════════════════════════════════════════════════════════════════════
def _v2_shared_defs():
    """公用 SVG defs + CSS：rough filter + 点阵图案 + 动画关键帧 + 字体。"""
    return f"""
<defs>
  <style>
    {FONT_FACE}
    {HAND_FONT_FACE}
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ width:{W}px; height:{H}px; overflow:hidden; background:{PALETTE_V2["bg_cream"]}; }}
    @keyframes fadeUp {{
        from {{ opacity:0; transform:translateY(16px); }}
        to   {{ opacity:1; transform:translateY(0); }}
    }}
    @keyframes popIn {{
        from {{ opacity:0; transform:scale(0.5) rotate(-3deg); }}
        to   {{ opacity:1; transform:scale(1) rotate(0deg); }}
    }}
    @keyframes kenBurns {{
        from {{ transform:scale(1.00) translate(0px, 0px); }}
        to   {{ transform:scale(1.08) translate(-14px, -8px); }}
    }}
    @keyframes drift {{
        0%   {{ transform:translate(0, 0) rotate(0deg); }}
        50%  {{ transform:translate(2px, -3px) rotate(0.3deg); }}
        100% {{ transform:translate(0, 0) rotate(0deg); }}
    }}
    /* Ken Burns 已移除，图片静止显示 */
    .ttl {{ animation: fadeUp .7s ease .2s both; opacity:0; }}
    .ttl2 {{ animation: fadeUp .7s ease .45s both; opacity:0; }}
    .num {{ animation: fadeUp .6s ease .1s both; opacity:0; }}
    .mascot {{ animation: fadeUp .9s ease .5s both, drift 4s ease-in-out infinite 1.4s; }}
  </style>
  <filter id="rough" x="-5%" y="-5%" width="110%" height="110%">
    <feTurbulence type="turbulence" baseFrequency="0.018" numOctaves="2" result="noise"/>
    <feDisplacementMap in="SourceGraphic" in2="noise" scale="2.2"
        xChannelSelector="R" yChannelSelector="G"/>
  </filter>
  <pattern id="dots" x="0" y="0" width="24" height="24" patternUnits="userSpaceOnUse">
    <circle cx="12" cy="12" r="1.4" fill="rgba(160,148,130,0.14)"/>
  </pattern>
</defs>
"""

def _v2_tabbar(all_tabs, active):
    """窄一点的 tab 栏。"""
    if not all_tabs:
        all_tabs = ['开场', active, '结尾']
    pad_x = 40
    tab_h = 52
    font_size = 22
    y = 0
    n = len(all_tabs)
    slot_w = W / n
    parts = [f'<rect x="0" y="0" width="{W}" height="{tab_h}" fill="{PALETTE_V2["bg_cream"]}"/>',
             f'<line x1="0" y1="{tab_h}" x2="{W}" y2="{tab_h}" stroke="{PALETTE_V2["line_soft"]}" stroke-width="1.5"/>']
    for i, t in enumerate(all_tabs):
        cx = slot_w * (i + 0.5)
        color = PALETTE_V2["ink_clay"] if t == active else PALETTE_V2["ink_charcoal"]
        weight = "bold" if t == active else "normal"
        parts.append(
            f'<text x="{cx}" y="{tab_h*0.62}" text-anchor="middle" '
            f'font-size="{font_size}" font-weight="{weight}" '
            f'fill="{color}" font-family={BASE_FONT!r}>{esc(t)}</text>')
        if t == active:
            # 下划线
            parts.append(
                f'<line x1="{cx-40}" y1="{tab_h-4}" x2="{cx+40}" y2="{tab_h-4}" '
                f'stroke="{PALETTE_V2["ink_clay"]}" stroke-width="3" stroke-linecap="round"/>')
    return '\n'.join(parts)

def _v2_wrap_title(text, font_size, max_px):
    """把标题按像素宽度折行，返回 lines。"""
    units = max_px / font_size
    return _wrap_text_svg(text, units)

def _v2_img_as_base64_href(media_path):
    """把本地图片读成 data URI，避免 file:// 跨域/路径问题。SVG <image> 直接引用。"""
    import base64, mimetypes
    if not media_path or not Path(media_path).exists():
        return None
    mime = mimetypes.guess_type(media_path)[0] or 'image/png'
    data = Path(media_path).read_bytes()
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"

def make_svg_news_slide_A(item, data, index_in_episode):
    """读图屏：大 media 面板 + 标题 + mascot + Ken Burns。
    奇数条 → 左图右标题
    偶数条 → 上图下标题
    """
    tab = item.get('cat', data['cat_order'][0] if data['cat_order'] else '资讯')
    all_tabs = data.get('tabs')
    title = item['title']
    media_paths = item.get('media_paths') or []
    first_img = next((p for p in media_paths if media_type(p) == 'image'), None)
    img_href = _v2_img_as_base64_href(first_img) if first_img else None

    is_odd = index_in_episode % 2 == 1
    tab_h = 52
    content_y = tab_h + 8
    content_h = H - content_y - 20

    if is_odd:
        # 左图 + 右标题
        img_w, img_h = 1100, 760
        img_x, img_y = 60, content_y + 60
        title_x = img_x + img_w + 50
        title_max_px = W - title_x - 60
        title_y_center = content_y + content_h * 0.42
        use_anchor_below_img = False
    else:
        # 上图 + 下标题（标题硬卡在图底边下方，避免重叠）
        img_w, img_h = 1100, 560
        img_x = (W - img_w) // 2
        img_y = content_y + 30
        title_x = 100
        title_max_px = W - 200 - 200   # 右侧留 200px 给 mascot
        title_y_center = 0  # 不用
        use_anchor_below_img = True
        img_bottom = img_y + img_h

    # 图片 + 框（无动画，静态显示）
    if img_href:
        img_svg = (
            f'<rect x="{img_x-6}" y="{img_y-6}" width="{img_w+12}" height="{img_h+12}" rx="22" '
            f'fill="#FAF4EE" stroke="{PALETTE_V2["ink_charcoal"]}" stroke-width="2.5" filter="url(#rough)"/>'
            f'<image x="{img_x}" y="{img_y}" width="{img_w}" height="{img_h}" '
            f'href="{img_href}" preserveAspectRatio="xMidYMid slice" clip-path="inset(0 round 18px)"/>'
        )
    else:
        img_svg = (
            f'<rect x="{img_x}" y="{img_y}" width="{img_w}" height="{img_h}" rx="22" '
            f'fill="{PALETTE_V2["accent_bg"]}" stroke="{PALETTE_V2["ink_charcoal"]}" '
            f'stroke-width="2.5" filter="url(#rough)"/>'
            f'<text x="{img_x+img_w/2}" y="{img_y+img_h/2}" text-anchor="middle" '
            f'font-size="40" fill="{PALETTE_V2["ink_charcoal"]}" opacity="0.4">[无素材]</text>'
        )

    # 标题：先用 _v2_title_with_num 内嵌 #N
    t_font_size = 64
    lh = int(t_font_size * 1.25)
    num_label = f"#{item.get('num', index_in_episode)}"
    if use_anchor_below_img:
        # 偶布局：硬卡在图底下方（首行 baseline = 图底 + gap + 上行高一半）
        gap = 56
        start_y = img_bottom + gap + int(t_font_size * 0.85)
    else:
        # 奇布局：右栏垂直居中
        est_total_h = 3 * lh
        start_y = int(title_y_center - est_total_h / 2 + lh * 0.7)
    title_svg, _ = _v2_title_with_num(
        title, num_label, title_x, start_y, t_font_size, title_max_px,
        PALETTE_V2["ink_clay"], lh, max_lines=3
    )
    num_svg = ''   # 已合并到 title_svg

    # Mascot：右下角
    mascot_expr = _mascot_for_cat(tab)
    mascot = _mascot_svg(mascot_expr, W - 180, H - 180, size=140)

    # 签名
    signature = (
        f'<text x="{W - 40}" y="{H - 18}" text-anchor="end" font-size="20" '
        f'fill="{PALETTE_V2["ink_charcoal"]}" opacity="0.5" '
        f'font-family="{HAND_FONT_FAMILY}">— Bunny</text>'
    )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="0 0 {W} {H}" width="{W}" height="{H}"
     style="background:{PALETTE_V2["bg_cream"]};font-family:{BASE_FONT}">
  {_v2_shared_defs()}
  <rect width="{W}" height="{H}" fill="url(#dots)"/>
  {_v2_tabbar(all_tabs, tab)}
  {img_svg}
  {num_svg}
  {title_svg}
  {mascot}
  {signature}
</svg>"""
    return svg

def make_svg_news_slide_B(item, data, index_in_episode):
    """读卡片屏：3 张卡片 + 右侧留白 mascot + 小标题。"""
    tab = item.get('cat', data['cat_order'][0] if data['cat_order'] else '资讯')
    all_tabs = data.get('tabs')
    title = item['title']
    cards3 = item['cards'][:3]   # 只取前 3

    tab_h = 52
    content_y = tab_h + 8
    margin = 60

    # 上方标题区（紧凑）+ #N 内嵌
    t_font_size = 44
    title_y0 = content_y + 80
    title_lh = int(t_font_size * 1.25)
    num_label = f"#{item.get('num', index_in_episode)}"
    title_svg, num_lines_used = _v2_title_with_num(
        title, num_label, margin, title_y0, t_font_size,
        W - margin*2 - 380, PALETTE_V2["ink_clay"], title_lh, max_lines=2
    )
    num_svg = ''   # 已合并

    # 3 张卡片横排
    cards_area_y = title_y0 + num_lines_used * title_lh + 60
    cards_area_h = H - cards_area_y - 80
    cards_area_w = W - margin * 2 - 340   # 留右侧 340 空白给 mascot
    gap = 24
    n = len(cards3)
    if n == 0:
        card_svgs = ''
    else:
        card_w = (cards_area_w - gap * (n - 1)) // n
        card_h = cards_area_h
        card_svgs = []
        for ci, card in enumerate(cards3):
            cx = margin + ci * (card_w + gap)
            cy = cards_area_y
            card_svgs.append(
                _card_svg_v2(ci, card['emoji'], card['title'], card['body'],
                             cx, cy, card_w, card_h, delay=0.25 + ci*0.16))
        card_svgs = '\n'.join(card_svgs)

    # 右侧留白区：大 emoji + mascot
    right_x = W - 280
    right_y_center = cards_area_y + cards_area_h // 2
    mascot_expr = _mascot_for_cat(tab)
    mascot = _mascot_svg(mascot_expr, right_x - 70, right_y_center - 80, size=170)
    big_emoji = (
        f'<text x="{right_x}" y="{cards_area_y + 80}" text-anchor="middle" '
        f'font-size="120" opacity="0.28">{esc(CAT_ICONS.get(tab, "✨"))}</text>'
    )

    signature = (
        f'<text x="{W - 40}" y="{H - 18}" text-anchor="end" font-size="20" '
        f'fill="{PALETTE_V2["ink_charcoal"]}" opacity="0.5" '
        f'font-family="{HAND_FONT_FAMILY}">— Bunny</text>'
    )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="0 0 {W} {H}" width="{W}" height="{H}"
     style="background:{PALETTE_V2["bg_cream"]};font-family:{BASE_FONT}">
  {_v2_shared_defs()}
  <rect width="{W}" height="{H}" fill="url(#dots)"/>
  {_v2_tabbar(all_tabs, tab)}
  {num_svg}
  {title_svg}
  {card_svgs}
  {big_emoji}
  {mascot}
  {signature}
</svg>"""
    return svg

def _card_svg_v2(idx, emoji, title, body, x, y, card_w, card_h, delay=0.0):
    """v2 卡片：配色锁定到 PALETTE_V2 + 稍大的 body 行高。"""
    # 索引交替用两个主色
    accent = PALETTE_V2["ink_clay"] if idx % 2 == 0 else PALETTE_V2["ink_teal"]
    accent_bg = PALETTE_V2["accent_bg"] if idx % 2 == 0 else "#E8F0F2"
    r = 28
    anim = f'animation:popIn 0.5s cubic-bezier(0.34,1.56,0.64,1) {delay:.2f}s both;opacity:0'
    parts = []
    # 柔和阴影
    parts.append(
        f'<rect x="{x+5}" y="{y+7}" width="{card_w}" height="{card_h}" '
        f'rx="{r}" fill="rgba(74,68,64,0.10)" style="{anim}"/>')
    # 卡片主体
    parts.append(
        f'<rect x="{x}" y="{y}" width="{card_w}" height="{card_h}" rx="{r}" '
        f'fill="white" stroke="{PALETTE_V2["line_soft"]}" stroke-width="2" '
        f'filter="url(#rough)" style="{anim}"/>')
    # 顶部彩色条
    parts.append(
        f'<path d="M{x+r},{y} L{x+card_w-r},{y} Q{x+card_w},{y} {x+card_w},{y+r} '
        f'L{x+card_w},{y+12} L{x},{y+12} L{x},{y+r} Q{x},{y} {x+r},{y} Z" '
        f'fill="{accent}" style="{anim}"/>')
    # emoji 圆背景
    ec_x, ec_y, ec_r = x + 50, y + 72, 38
    parts.append(
        f'<circle cx="{ec_x}" cy="{ec_y}" r="{ec_r}" fill="{accent_bg}" style="{anim}"/>')
    parts.append(
        f'<text x="{ec_x}" y="{ec_y+14}" text-anchor="middle" font-size="46" style="{anim}">{emoji}</text>')
    # 标题
    t_fs = 34
    t_max_u = (card_w - 140) / t_fs
    t_lines = _wrap_text_svg(title, t_max_u)[:2]
    for li, ln in enumerate(t_lines):
        parts.append(
            f'<text x="{x+100}" y="{y+60+li*40}" font-size="{t_fs}" font-weight="bold" '
            f'fill="{accent}" font-family={BASE_FONT!r} style="{anim}">{esc(ln)}</text>')
    # 分隔虚线
    sep_y = y + 125
    parts.append(
        f'<line x1="{x+24}" y1="{sep_y}" x2="{x+card_w-24}" y2="{sep_y}" '
        f'stroke="{accent}" stroke-width="1.6" stroke-dasharray="3,7" '
        f'stroke-linecap="round" opacity="0.4" style="{anim}"/>')
    # 正文
    b_fs = 30
    b_lh = 40
    b_pad = 28
    b_start_y = sep_y + 34
    b_max_u = (card_w - b_pad*2) / b_fs
    b_lines = _wrap_text_svg(body, b_max_u)
    max_body_lines = max(1, (y + card_h - 28 - b_start_y) // b_lh)
    for li, ln in enumerate(b_lines[:max_body_lines]):
        parts.append(
            f'<text x="{x+b_pad}" y="{b_start_y+li*b_lh}" '
            f'font-size="{b_fs}" fill="{PALETTE_V2["ink_charcoal"]}" font-weight="bold" '
            f'font-family={BASE_FONT!r} style="{anim}">{esc(ln)}</text>')
    return '\n'.join(parts)

def make_svg_news_slides(item, data, index_in_episode):
    """返回 (slide_A_svg, slide_B_svg)。"""
    return (
        make_svg_news_slide_A(item, data, index_in_episode),
        make_svg_news_slide_B(item, data, index_in_episode),
    )

# ── 开场幻灯片 ────────────────────────────────────────────────────────────────
def make_svg_intro_slide(data):
    date_str  = data['date']
    show_name = data['show_name']
    total     = data['total']
    tabs      = data.get('tabs', ['开场', '结尾'])

    css_extra = f"""
    @keyframes spinStar {{ to {{ transform:rotate(360deg); transform-origin:center; }} }}
    @keyframes drawCircle {{ from {{ stroke-dashoffset:1200; }} to {{ stroke-dashoffset:0; }} }}
    """

    tab_bar = _tab_bar_svg(tabs, '开场')

    # 标题背景条
    tab_h    = 52
    title_h  = 108
    content_y = tab_h + title_h
    title_bar = (
        f'<rect x="0" y="{tab_h}" width="{W}" height="{title_h}" fill="#F0EBE4"/>'
        f'<line x1="0" y1="{content_y-1}" x2="{W}" y2="{content_y-1}" '
        f'stroke="#D5CFC6" stroke-width="1"/>'
        f'<text x="60" y="{tab_h + title_h//2 + 6}" font-size="50" font-weight="bold" '
        f'fill="#4A4440" font-family={BASE_FONT!r} dominant-baseline="middle" '
        f'style="animation:fadeUp .6s ease .1s both;opacity:0">'
        f'{esc(date_str)}  资讯概览</text>')

    # 内容区中心
    content_h = H - content_y
    cx = W // 2
    cy = content_y + content_h // 2 - 20

    # 同心圆装饰
    circles = '\n'.join([
        f'<circle cx="{cx}" cy="{cy}" r="{150 + i*40}" '
        f'fill="none" stroke="rgba(165,145,120,{0.18-i*0.04:.2f})" stroke-width="{3-i*0.5:.1f}" '
        f'stroke-dasharray="1200" '
        f'style="animation:drawCircle {1.2+i*0.3:.1f}s ease {0.2+i*0.2:.1f}s both"/>'
        for i in range(4)])

    # 中心标题
    main_title = (
        f'<text x="{cx}" y="{cy-20}" text-anchor="middle" font-size="64" font-weight="bold" '
        f'fill="#4A4440" font-family={BASE_FONT!r} filter="url(#rough)" '
        f'style="animation:fadeUp .7s ease .5s both;opacity:0">{esc(show_name)}</text>')
    sub1 = (
        f'<text x="{cx}" y="{cy+40}" text-anchor="middle" font-size="28" fill="#666" '
        f'font-family={BASE_FONT!r} '
        f'style="animation:fadeUp .5s ease .9s both;opacity:0">'
        f'本期 {total} 条资讯</text>')
    underline = _wavy_underline(cx - 160, cy + 2, 320)

    sparks_svg = ''
    for i, (sx, sy) in enumerate([(cx-260, cy-70),(cx+270, cy-80),(cx-220, cy+70),(cx+240, cy+60)]):
        sp = CARTOON_SPARKS[i % len(CARTOON_SPARKS)]
        sparks_svg += _spark_svg(sp, sx, sy, SKETCH_COLORS[i][0], size=16)

    # 分类卡片（居中排列在圆下方）
    cat_cards = ''
    cats = data['cat_order'][:4]
    n_cats = len(cats)
    cw, ch = 240, 72
    total_cw = n_cats * cw + (n_cats - 1) * 20
    start_x = (W - total_cw) // 2
    card_y = cy + 90
    for i, cat in enumerate(cats):
        col, _ = SKETCH_COLORS[i % len(SKETCH_COLORS)]
        icon   = CAT_ICONS.get(cat, '📌')
        cx2 = start_x + i * (cw + 20)
        cat_cards += (
            f'<rect x="{cx2}" y="{card_y}" width="{cw}" height="{ch}" rx="12" '
            f'fill="white" stroke="{col}" stroke-width="2.5" '
            f'style="animation:popIn .45s cubic-bezier(0.34,1.56,0.64,1) {1.1+i*0.15:.2f}s both;opacity:0"/>'
            f'<text x="{cx2+cw//2}" y="{card_y+ch//2+7}" text-anchor="middle" font-size="24" '
            f'fill="{col}" font-family={BASE_FONT!r} font-weight="bold" '
            f'style="animation:fadeUp .4s ease {1.2+i*0.15:.2f}s both;opacity:0">'
            f'{icon} {esc(cat)}</text>')

    return f"""<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="0 0 {W} {H}" width="{W}" height="{H}"
     style="background:#F5F0EB">
  <defs>
    <style>{css_extra}
    {FONT_FACE}
    @keyframes fadeUp {{ from{{opacity:0;transform:translateY(10px)}} to{{opacity:1;transform:translateY(0)}} }}
    @keyframes popIn  {{ from{{opacity:0;transform-origin:center;transform:scale(0.5) rotate(-3deg)}} to{{opacity:1;transform-origin:center;transform:scale(1) rotate(0deg)}} }}
    </style>
    <filter id="rough" x="-5%" y="-5%" width="110%" height="110%">
      <feTurbulence type="turbulence" baseFrequency="0.018" numOctaves="2" result="noise"/>
      <feDisplacementMap in="SourceGraphic" in2="noise" scale="2.2"
          xChannelSelector="R" yChannelSelector="G"/>
    </filter>
    <pattern id="dots" x="0" y="0" width="24" height="24" patternUnits="userSpaceOnUse">
      <circle cx="12" cy="12" r="1.4" fill="rgba(160,148,130,0.15)"/>
    </pattern>
  </defs>
  <rect width="{W}" height="{H}" fill="url(#dots)"/>
  {tab_bar}
  {title_bar}
  {circles}
  {main_title}
  {sub1}
  {underline}
  {sparks_svg}
  {cat_cards}
</svg>"""

# ── 结尾幻灯片 ────────────────────────────────────────────────────────────────
def make_svg_outro_slide(data):
    cx, cy = W // 2, H // 2

    circles = '\n'.join([
        f'<circle cx="{cx}" cy="{cy}" r="{120 + i*40}" '
        f'fill="none" stroke="rgba(165,145,120,{0.2-i*0.05:.2f})" stroke-width="2.5" '
        f'stroke-dasharray="1200" '
        f'style="animation:drawCircle 1.4s ease {0.1+i*0.2:.1f}s both"/>'
        for i in range(4)])

    main_text = (
        f'<text x="{cx}" y="{cy-10}" text-anchor="middle" font-size="72" '
        f'font-weight="bold" fill="#4A4440" font-family={BASE_FONT!r} '
        f'filter="url(#rough)" '
        f'style="animation:fadeUp .7s ease .4s both;opacity:0">感谢收听 · 明日见</text>')
    sub = (
        f'<text x="{cx}" y="{cy+52}" text-anchor="middle" font-size="26" '
        f'fill="#888" font-family={BASE_FONT!r} '
        f'style="animation:fadeUp .5s ease .9s both;opacity:0">'
        f'AI 资讯播客  ·  作者 Bunny  ·  哔哩哔哩同名</text>')
    underline = _wavy_underline(cx - 220, cy + 12, 440)

    sparks_svg = ''
    for i, (sx, sy) in enumerate([(cx-320, cy-90),(cx+310, cy-80),(cx-280, cy+90),(cx+300, cy+80)]):
        sp = CARTOON_SPARKS[i % len(CARTOON_SPARKS)]
        sparks_svg += _spark_svg(sp, sx, sy, SKETCH_COLORS[i][0], size=18)

    return f"""<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="0 0 {W} {H}" width="{W}" height="{H}"
     style="background:#F5F0EB">
  <defs>
    <style>
    {FONT_FACE}
    @keyframes fadeUp    {{ from{{opacity:0;transform:translateY(10px)}} to{{opacity:1;transform:translateY(0)}} }}
    @keyframes drawCircle{{ from{{stroke-dashoffset:1200}} to{{stroke-dashoffset:0}} }}
    </style>
    <filter id="rough" x="-5%" y="-5%" width="110%" height="110%">
      <feTurbulence type="turbulence" baseFrequency="0.018" numOctaves="2" result="noise"/>
      <feDisplacementMap in="SourceGraphic" in2="noise" scale="2.2"
          xChannelSelector="R" yChannelSelector="G"/>
    </filter>
    <pattern id="dots" x="0" y="0" width="24" height="24" patternUnits="userSpaceOnUse">
      <circle cx="12" cy="12" r="1.4" fill="rgba(160,148,130,0.15)"/>
    </pattern>
  </defs>
  <rect width="{W}" height="{H}" fill="url(#dots)"/>
  {circles}
  {main_text}
  {sub}
  {underline}
  {sparks_svg}
</svg>"""

# ═══════════════════════════════════════════════════════════════════════════════
#  SVG → MP4  (Playwright 录屏)
# ═══════════════════════════════════════════════════════════════════════════════
async def svg_to_mp4(slide_content, out_mp4, duration):
    """
    渲染 slide（完整 HTML 或 SVG 片段）为 MP4：
    1. 写入 HTML 文件（完整 HTML 直接写，SVG 片段包装后写）
    2. Playwright Chromium 录屏 duration 秒
    3. WebM → MP4
    """
    from playwright.async_api import async_playwright

    stem      = Path(out_mp4).stem
    html_path = str(BUILD / "html" / f"{stem}.html")
    vid_dir   = str(BUILD / "html")

    # 已是完整 HTML 页面则直接写入，否则包装成最简 HTML
    # 注：必须显式声明 charset，否则 base64 大图打头的 SVG 会让浏览器猜成 Latin-1 → CJK 乱码
    if slide_content.lstrip().startswith('<!DOCTYPE') or slide_content.lstrip().startswith('<html'):
        html = slide_content
    else:
        html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<style>
  *{{margin:0;padding:0;}}
  body{{width:{W}px;height:{H}px;overflow:hidden;background:#F5F0EB;}}
</style></head><body>{slide_content}</body></html>"""
    Path(html_path).write_text(html, encoding='utf-8')

    webm_path = None
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=['--no-sandbox', '--disable-setuid-sandbox'])
        ctx = await browser.new_context(
            viewport={'width': W, 'height': H},
            record_video_dir=vid_dir,
            record_video_size={'width': W, 'height': H},
        )
        page = await ctx.new_page()
        await page.goto(f'file://{html_path}')
        # 等待动画完整播放（多留 0.6s buffer）
        await page.wait_for_timeout(int(duration * 1000) + 600)
        webm_path = await page.video.path()
        await ctx.close()
        await browser.close()

    # webm → mp4
    r = subprocess.run(
        ['ffmpeg', '-y', '-i', webm_path,
         '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
         '-pix_fmt', 'yuv420p', out_mp4],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"webm→mp4 失败:\n{r.stderr[-300:]}")
    Path(webm_path).unlink(missing_ok=True)
    return out_mp4

# ── Media overlay 常量 ────────────────────────────────────────────────────────
MEDIA_OVERLAY_START = 1.2    # 段落开始多久后显示素材
MEDIA_FADE_D        = 0.45   # 淡入/淡出时长
MEDIA_SLIDE_PX      = 80     # 素材滑入像素数
TEAL                = (78, 205, 196)

def calc_overlay_dur(seg_dur, mtype='image'):
    available = seg_dur - MEDIA_OVERLAY_START - 0.6
    min_room  = MEDIA_FADE_D * 2 + 0.5
    if available < min_room:
        return 3.0
    if mtype == 'gif':
        return round(max(4.0, min(available * 0.55, 14.0)), 2)
    return round(max(3.5, min(available * 0.40, 8.0)), 2)

def video_duration(path):
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path],
            capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except Exception:
        return None

def make_media_panel_rgba(media_paths):
    """创建 RGBA 半透明浮层（1 或 2 张图并排）。"""
    if isinstance(media_paths, str):
        media_paths = [media_paths]
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

    pad, border = 12, 4
    img_gap = 12
    max_pw  = int(W * 0.68)
    max_ph  = int(H * 0.68)

    if len(imgs) == 1:
        mimg  = imgs[0]
        ratio = min(max_pw / mimg.width, max_ph / mimg.height)
        iw, ih = int(mimg.width * ratio), int(mimg.height * ratio)
        pw, ph  = iw + (pad + border) * 2, ih + (pad + border) * 2
        panel   = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
        d = ImageDraw.Draw(panel)
        d.rounded_rectangle([0, 0, pw, ph], radius=20, fill=(245, 240, 230, 220))
        d.rounded_rectangle([0, 0, pw, ph], radius=20, outline=(*TEAL, 255), width=border)
        panel.paste(mimg.resize((iw, ih), Image.LANCZOS), (pad + border, pad + border))
    else:
        slot_w = (max_pw - (pad + border) * 2 - img_gap) // 2
        slot_h = max_ph - (pad + border) * 2
        placed = []
        for mimg in imgs:
            ratio = min(slot_w / mimg.width, slot_h / mimg.height)
            iw, ih = int(mimg.width * ratio), int(mimg.height * ratio)
            placed.append((mimg.resize((iw, ih), Image.LANCZOS), iw, ih))
        content_w = placed[0][1] + img_gap + placed[1][1]
        content_h = max(placed[0][2], placed[1][2])
        pw = content_w + (pad + border) * 2
        ph = content_h + (pad + border) * 2
        panel = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
        d = ImageDraw.Draw(panel)
        d.rounded_rectangle([0, 0, pw, ph], radius=20, fill=(245, 240, 230, 220))
        d.rounded_rectangle([0, 0, pw, ph], radius=20, outline=(*TEAL, 255), width=border)
        cx = pad + border
        for rim, iw, ih in placed:
            iy_off = (content_h - ih) // 2
            panel.paste(rim, (cx, pad + border + iy_off))
            cx += iw + img_gap

    stem = '_'.join(Path(p).stem for p in paths[:2])
    out  = BUILD / 'slides' / f'overlay_{stem}.png'
    panel.save(str(out))
    return str(out), pw, ph

# ── 将动画 MP4 slide + 音频 + 字幕 → 最终视频段 ───────────────────────────────
def make_seg_from_svg(slide_mp4, audio, dur, out, ass=None):
    """基础版：SVG 动画 + 音频 + 字幕，无素材浮层。"""
    vf = f'scale={W}:{H}'
    if ass and Path(ass).exists() and Path(ass).stat().st_size > 50:
        ass_e = ass.replace('\\', '/').replace(':', '\\:')
        vf += f",ass='{ass_e}'"

    audio_args = ['-i', audio] if audio else \
                 ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100']

    cmd = ['ffmpeg', '-y',
           '-stream_loop', '-1', '-i', slide_mp4,
           *audio_args,
           '-t', str(dur),
           '-vf', vf,
           '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
           '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
           '-shortest', out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"make_seg_from_svg 失败:\n{r.stderr[-400:]}")

def make_seg_from_svg_with_overlay(slide_mp4, audio, dur, out, ass=None, media_paths=None):
    """图片素材浮层版：居中淡入+上滑，停留，淡出+下滑（翻书感）。"""
    if not media_paths:
        make_seg_from_svg(slide_mp4, audio, dur, out, ass); return
    paths = media_paths if isinstance(media_paths, list) else [media_paths]
    paths = [p for p in paths if p and media_type(p) == 'image']
    if not paths:
        make_seg_from_svg(slide_mp4, audio, dur, out, ass); return

    result = make_media_panel_rgba(paths)
    if not result:
        make_seg_from_svg(slide_mp4, audio, dur, out, ass); return
    panel_path, pw, ph = result

    ox = (W - pw) // 2
    oy = (H - ph) // 2
    ms, md, fd, sd = MEDIA_OVERLAY_START, calc_overlay_dur(dur, 'image'), MEDIA_FADE_D, MEDIA_SLIDE_PX

    y_expr = (
        f"if(lt(t-{ms},{fd}),"
        f"  {oy+sd}-(({sd})*((t-{ms})/{fd})),"
        f"  if(gt(t-{ms},{md-fd}),"
        f"    {oy}+(({sd})*((t-{ms}-{md-fd})/{fd})),"
        f"    {oy}))"
    )
    vf = (
        f"[2:v]scale={pw}:{ph},"
        f"fade=t=in:st={ms}:d={fd}:alpha=1,"
        f"fade=t=out:st={ms+md-fd}:d={fd}:alpha=1[ov];"
        f"[0:v]scale={W}:{H}[bg];"
        f"[bg][ov]overlay=x={ox}:y='{y_expr}':"
        f"enable='between(t,{ms},{ms+md})':format=auto[vout]"
    )
    if ass and Path(ass).exists() and Path(ass).stat().st_size > 50:
        ass_e = ass.replace('\\', '/').replace(':', '\\:')
        vf = vf.replace('[vout]', '[vpre]') + f";[vpre]ass='{ass_e}'[vout]"

    audio_args = ['-i', audio] if audio else \
                 ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100']
    cmd = ['ffmpeg', '-y',
           '-stream_loop', '-1', '-i', slide_mp4,
           *audio_args,
           '-loop', '1', '-i', panel_path,
           '-filter_complex', vf,
           '-map', '[vout]', '-map', '1:a',
           '-t', str(dur),
           '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
           '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
           '-shortest', out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    ⚠️  overlay 失败，回退: {r.stderr[-200:]}")
        make_seg_from_svg(slide_mp4, audio, dur, out, ass)

def make_seg_from_svg_gif_overlay(slide_mp4, audio, dur, out, media_path, ass=None):
    """GIF / 视频素材浮层版：循环播放，居中淡入淡出。"""
    ms = MEDIA_OVERLAY_START
    md = calc_overlay_dur(dur, 'gif')
    fd = MEDIA_FADE_D
    pw = int(W * 0.68)
    ph = int(H * 0.68)
    ox = (W - pw) // 2
    oy = (H - ph) // 2

    vf = (
        f"[3:v]scale={pw}:{ph}:force_original_aspect_ratio=decrease,"
        f"pad={pw}:{ph}:(ow-iw)/2:(oh-ih)/2:color=0xFAF8F3,"
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
           '-stream_loop', '-1', '-i', slide_mp4,
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
        print(f"    ⚠️  gif overlay 失败，回退")
        make_seg_from_svg(slide_mp4, audio, dur, out, ass)

# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════
async def main():
    print("=" * 60)
    print("  video-pipeline  ✏️  手绘 SVG 风格  (sketch mode)")
    print("=" * 60)

    print("\nC1 — 解析文章...")
    data = parse(ARTICLE)
    print(f"  ✅ {len(data['news'])} 条新闻  |  {data['title']}")
    print(f"  Tabs: {' | '.join(data['tabs'])}")

    print("\nC2 — TTS 配音 + 字幕...")
    asegs = []
    intro_text = f"欢迎收看{data['show_name']}，本期带来 {data['total']} 条热门资讯。"
    intro_ap   = str(BUILD / "audio" / "00_intro.mp3")
    intro_ass  = str(BUILD / "subs"  / "00_intro.ass")
    intro_dur  = await tts_with_subs(intro_text, intro_ap, intro_ass)
    print(f"  ✅ intro  {intro_dur:.1f}s")

    for item in data['news']:
        ap  = str(BUILD / "audio" / f"{item['num']:02d}.mp3")
        ass = str(BUILD / "subs"  / f"{item['num']:02d}.ass")
        dur = await tts_with_subs(item['summary'], ap, ass)
        asegs.append({'p': ap, 'ass': ass, 'dur': dur, 'num': item['num']})
        print(f"  ✅ 新闻{item['num']}  {dur:.1f}s  {item['title'][:24]}…")

    outro_text = "感谢收听，明天见。"
    outro_ap   = str(BUILD / "audio" / "99_outro.mp3")
    outro_ass  = str(BUILD / "subs"  / "99_outro.ass")
    outro_dur  = await tts_with_subs(outro_text, outro_ap, outro_ass)
    print(f"  ✅ outro  {outro_dur:.1f}s")

    print("\nC2.5 — 解析素材...")
    for item in data['news']:
        paths = [resolve_media(r, item['num']) for r in item.get('media_raws', [item['media_raw']])]
        item['media_paths'] = [p for p in paths if p]
        if item['media_paths']:
            types = '+'.join(media_type(p) or '?' for p in item['media_paths'])
            print(f"  新闻{item['num']}: {types} ({len(item['media_paths'])}个素材)")
        else:
            print(f"  新闻{item['num']}: 无素材")

    print("\nC3 — 生成 SVG 动画 slides (Playwright 渲染)...")
    slides = {}

    # Intro
    svg = make_svg_intro_slide(data)
    intro_slide = str(BUILD / "slides" / "00_intro.mp4")
    print("  ⏳ 渲染开场 SVG...")
    await svg_to_mp4(svg, intro_slide, intro_dur + 1.0)
    slides['intro'] = intro_slide
    print("  ✅ 开场")

    # News slides — v2 每条新闻出 A+B 两张 slide
    for i, item in enumerate(data['news']):
        audio_dur = asegs[i]['dur']
        t_a = min(6.5, max(4.0, audio_dur * 0.32))   # slide_A 占 ~32% 时长, 4-6.5s
        seg_a_dur = t_a + 0.6
        seg_b_dur = (audio_dur - t_a) + 0.8

        svg_a, svg_b = make_svg_news_slides(item, data, i + 1)

        slide_a_mp4 = str(BUILD / "slides" / f"{item['num']:02d}a_news.mp4")
        slide_b_mp4 = str(BUILD / "slides" / f"{item['num']:02d}b_news.mp4")
        print(f"  ⏳ 渲染新闻 {item['num']} A/B SVG (A:{seg_a_dur:.1f}s B:{seg_b_dur:.1f}s)...")
        await svg_to_mp4(svg_a, slide_a_mp4, seg_a_dur)
        await svg_to_mp4(svg_b, slide_b_mp4, seg_b_dur)
        slides[f"{item['num']}_a"] = slide_a_mp4
        slides[f"{item['num']}_b"] = slide_b_mp4
        slides[f"{item['num']}_ta"] = t_a   # 记录切点
        print(f"  ✅ 新闻{item['num']}: {item['title'][:26]}")

    # Outro
    svg = make_svg_outro_slide(data)
    outro_slide = str(BUILD / "slides" / "99_outro.mp4")
    print("  ⏳ 渲染结尾 SVG...")
    await svg_to_mp4(svg, outro_slide, outro_dur + 1.0)
    slides['outro'] = outro_slide
    print("  ✅ 结尾")

    print("\nC4 — 编码视频段 (字幕烧录)...")
    segs = []
    SILENCE_PAD = 0.6   # 全局：给所有段尾加 0.6s 静音，避免 acrossfade 字头字尾叠加

    # 给 intro 音频加 0.6s 尾部静音
    intro_padded = str(BUILD / "audio" / "00_intro_padded.wav")
    subprocess.run(
        ['ffmpeg', '-y', '-i', intro_ap,
         '-af', f'apad=pad_dur={SILENCE_PAD}',
         '-c:a', 'pcm_s16le', '-ar', '44100', '-ac', '2', intro_padded],
        capture_output=True, text=True)

    # Intro segment
    sp = str(BUILD / "seg_00_intro.mp4")
    make_seg_from_svg(slides['intro'], intro_padded, intro_dur + SILENCE_PAD, sp, ass=intro_ass)
    segs.append(sp)
    print(f"  ✅ intro  {intro_dur+SILENCE_PAD:.1f}s")

    # News segments — 每条新闻出 seg_NNa（读图，无字幕）+ seg_NNb（读卡片，带字幕）
    for i, item in enumerate(data['news']):
        ai   = asegs[i]
        audio_dur = ai['dur']
        t_a = slides[f"{item['num']}_ta"]

        # 拆音频（atrim 滤镜样本级精确，apad 在末尾加 0.6s 静音
        # 这样 acrossfade 的 0.6s 重叠落在静音区，不会把语音字头叠加到字尾上）
        SILENCE_PAD = 0.6
        audio_a_path = str(BUILD / "audio" / f"{item['num']:02d}_a.wav")
        audio_b_path = str(BUILD / "audio" / f"{item['num']:02d}_b.wav")
        subprocess.run(
            ['ffmpeg', '-y', '-i', ai['p'],
             '-af', f'atrim=0:{t_a:.3f},asetpts=PTS-STARTPTS,apad=pad_dur={SILENCE_PAD}',
             '-c:a', 'pcm_s16le', '-ar', '44100', '-ac', '2', audio_a_path],
            capture_output=True, text=True)
        subprocess.run(
            ['ffmpeg', '-y', '-i', ai['p'],
             '-af', f'atrim={t_a:.3f},asetpts=PTS-STARTPTS,apad=pad_dur={SILENCE_PAD}',
             '-c:a', 'pcm_s16le', '-ar', '44100', '-ac', '2', audio_b_path],
            capture_output=True, text=True)

        # 拆字幕（只给 slide_B 用，时间戳前移 t_a）
        ass_b_path = str(BUILD / "subs" / f"{item['num']:02d}_b.ass")
        try:
            shift_ass(ai['ass'], ass_b_path, t_a)
        except Exception as e:
            print(f"    ⚠️  字幕偏移失败: {e}")
            ass_b_path = ai['ass']

        sp_a = str(BUILD / f"seg_{item['num']:02d}a.mp4")
        sp_b = str(BUILD / f"seg_{item['num']:02d}b.mp4")
        # 段时长 = 实际语音时长 + 静音 padding（用于 xfade 重叠区）
        dur_a = t_a + SILENCE_PAD
        dur_b = (audio_dur - t_a) + SILENCE_PAD

        # seg_A: slide_A 视频 + audio_A，不带字幕（读图屏保持简洁）
        make_seg_from_svg(slides[f"{item['num']}_a"], audio_a_path, dur_a, sp_a, ass=None)
        # seg_B: slide_B 视频 + audio_B + 偏移后的字幕
        make_seg_from_svg(slides[f"{item['num']}_b"], audio_b_path, dur_b, sp_b, ass=ass_b_path)

        segs.append(sp_a)
        segs.append(sp_b)
        print(f"  ✅ 新闻{item['num']}  A:{dur_a:.1f}s B:{dur_b:.1f}s")

    # Outro: 给 outro 音频也加 0.6s 头部静音（用于和最后一段 news 的 xfade）
    outro_padded = str(BUILD / "audio" / "99_outro_padded.wav")
    subprocess.run(
        ['ffmpeg', '-y', '-i', outro_ap,
         '-af', f'adelay={int(SILENCE_PAD*1000)}|{int(SILENCE_PAD*1000)},apad=pad_dur={SILENCE_PAD}',
         '-c:a', 'pcm_s16le', '-ar', '44100', '-ac', '2', outro_padded],
        capture_output=True, text=True)

    # Outro segment
    sp = str(BUILD / "seg_99_outro.mp4")
    make_seg_from_svg(slides['outro'], outro_padded, outro_dur + 2*SILENCE_PAD, sp, ass=outro_ass)
    segs.append(sp)
    print(f"  ✅ outro  {outro_dur+2*SILENCE_PAD:.1f}s")

    print("\nC4b — 两两递归 xfade 交叉淡化合并...")
    out = str(OUT_DIR / "video_sketch_v2.mp4")
    FADE_D = 0.6   # 每两段之间 0.6 秒交叉淡化

    def _probe_dur(p):
        try:
            r = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', p],
                capture_output=True, text=True, timeout=10)
            return float(r.stdout.strip())
        except Exception:
            return 2.0

    n = len(segs)
    if n == 1:
        shutil.copy(segs[0], out)
    else:
        # 两两 xfade 递归：每次 ffmpeg 只接 2 个输入，避免 OOM
        intermediate = segs[0]
        ok = True
        for i in range(1, n):
            next_inter = str(BUILD / f"_xf_step{i:02d}.mp4")
            dur_left = _probe_dur(intermediate)
            offset = max(0.05, dur_left - FADE_D)
            cmd = ['ffmpeg', '-y',
                   '-i', intermediate, '-i', segs[i],
                   '-filter_complex',
                   (f'[0:v][1:v]xfade=transition=fade:duration={FADE_D}:offset={offset:.3f}[vout];'
                    f'[0:a][1:a]acrossfade=d={FADE_D}[aout]'),
                   '-map', '[vout]', '-map', '[aout]',
                   '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                   '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
                   '-pix_fmt', 'yuv420p',
                   next_inter]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"  ⚠️  xfade 第 {i}/{n-1} 步失败:\n  {r.stderr[-400:]}")
                ok = False
                break
            intermediate = next_inter
            print(f"  · 已合并 {i}/{n-1} 段")

        if ok:
            shutil.copy(intermediate, out)
            print(f"  ✅ {n} 段两两 xfade 合并成功（{n-1} 个 {FADE_D}s 淡化点）")
        else:
            print("  → 回退到硬切 concat")
            cf = str(BUILD / "concat.txt")
            Path(cf).write_text('\n'.join(f"file '{s}'" for s in segs) + '\n')
            r = subprocess.run(
                ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', cf,
                 '-c:v', 'libx264', '-crf', '23',
                 '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2', out],
                capture_output=True, text=True)
            if r.returncode != 0:
                print(f"  ❌ {r.stderr[-400:]}"); return

    print(f"\n✅ 手绘风格 v2 视频生成完成")
    print(f"   路径: {out}")

    probe = json.loads(subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json',
         '-show_streams', '-show_format', out],
        capture_output=True, text=True).stdout)
    fmt  = probe['format']
    dur  = float(fmt['duration'])
    size = int(fmt['size']) / 1024 / 1024
    vs   = next((s for s in probe['streams'] if s['codec_type'] == 'video'), {})
    mins, secs = int(dur // 60), int(dur % 60)
    print(f"   时长: {mins}分{secs}秒 | 大小: {size:.1f}MB | {vs.get('width')}×{vs.get('height')}")
    print(f"\n如需上传 B站：/video-pipeline upload {out}")

asyncio.run(main())
