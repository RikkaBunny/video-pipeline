#!/usr/bin/env python3
"""
video-pipeline convert_svg — 手绘SVG动画版
用 Playwright 并行录制 SVG 动画幻灯片，替换 Pillow 静态 PNG。
其余 TTS / 字幕 / 素材叠加逻辑与 convert.py 完全一致。
"""
import re, asyncio, subprocess, json, shutil, sys
from pathlib import Path
from PIL import ImageFont

# ── 路径配置 ──────────────────────────────────────────────────────────────────
ARTICLE = "/root/video-pipeline/output/2026-04-09/github/article.md"
OUT_DIR = Path(ARTICLE).parent
BUILD   = Path("/tmp/vp_build_svg")
FONT    = "/root/video-pipeline/assets/fonts/NotoSansSC-Regular.ttf"

BUILD.mkdir(parents=True, exist_ok=True)
for sub in ['audio', 'slides', 'subs', 'media', 'html']:
    (BUILD / sub).mkdir(exist_ok=True)

# ── 常量 ──────────────────────────────────────────────────────────────────────
W, H = 1920, 1080
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
""".format(W=W, H=H, font=ASS_FONT, sz=ASS_FONTSIZE, mh=ASS_MARGIN_H, mv=ASS_MARGIN_V)

MEDIA_EXTS_IMAGE = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
MEDIA_EXTS_GIF   = {'.gif'}
MEDIA_EXTS_VIDEO = {'.mp4', '.mov', '.webm', '.mkv', '.avi'}
MEDIA_OVERLAY_START = 1.2
MEDIA_OVERLAY_DUR   = 3.0
MEDIA_FADE_D        = 0.45
MEDIA_SLIDE_PX      = 80

from PIL import Image, ImageDraw
TEAL = (78, 205, 196)

# ── 解析 ──────────────────────────────────────────────────────────────────────
def strip_md(t):
    t = re.sub(r'^\*\*摘要\*\*[^：:]*[：:]\s*', '', t, flags=re.MULTILINE)
    t = re.sub(r'^摘要[^：:]*[：:]\s*', '', t, flags=re.MULTILINE)
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)
    t = re.sub(r'`(.+?)`', r'\1', t)
    t = re.sub(r'^>\s*', '', t, flags=re.MULTILINE)
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

    date_str  = fm.get('date', '2026-04-09')
    title_fm  = fm.get('title', 'AI 资讯')
    channel_color = fm.get('channel_color', '#2DA44E')

    bm = re.search(r'【([^】]+)】', title_fm)
    show_name = re.sub(r'\d{4}-\d{2}-\d{2}', '', bm.group(1)).strip() if bm else 'AI 周报'

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
    # Also extract stars/language from news body for badges
    star_re   = re.compile(r'\*\*([0-9,，]+)\*\*\s*stars', re.IGNORECASE)
    lang_re   = re.compile(r'以\s+\*?\*?([A-Za-z+#]+)\*?\*?\s+[为编写主]')

    for sec in re.split(r'\n## ', txt)[1:]:
        hdr = re.match(r'^(.+?)\s*#(\d+)\s*\n', sec)
        if not hdr: continue
        num   = int(hdr.group(2))
        title = strip_md(hdr.group(1).strip())
        parts = [ln[2:] for ln in sec.splitlines() if ln.startswith('> ')]
        summary = strip_md(' '.join(parts))
        cards = []
        for m3 in re.finditer(r'^[-•]\s+(\S+)\s+\*\*([^*]+)\*\*[：:]\s+(.+)', sec, re.MULTILINE):
            cards.append({'emoji': m3.group(1), 'title': m3.group(2), 'body': strip_md(m3.group(3))})
        media_raws = [m.strip() for m in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', sec)][:2]

        # Extract extra metadata for badges
        extra = {}
        sm = star_re.search(sec)
        if sm:
            try: extra['new_stars'] = int(sm.group(1).replace(',', '').replace('，', ''))
            except: pass
        lm = lang_re.search(sec)
        if lm: extra['language'] = lm.group(1)

        news.append({
            'num': num, 'title': title, 'summary': summary,
            'cards': cards[:5],
            'cat': num2cat.get(num, cat_order[0] if cat_order else ''),
            'media_raw': media_raws[0] if media_raws else '',
            'media_raws': media_raws,
            'extra': extra,
        })
    news.sort(key=lambda x: x['num'])
    tabs = ['开场'] + cat_order + ['结尾']
    return {
        'date': date_str, 'title': title_fm, 'show_name': show_name,
        'channel_color': channel_color,
        'cat_order': cat_order, 'cat_items': cat_items,
        'news': news, 'total': len(news), 'tabs': tabs
    }

# ── ASS 字幕 ──────────────────────────────────────────────────────────────────
def _ass_time(sec):
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"

_sub_fnt = None
def _get_sub_fnt():
    global _sub_fnt
    if _sub_fnt is None: _sub_fnt = ImageFont.truetype(FONT, ASS_FONTSIZE)
    return _sub_fnt

def _measure(text):
    bbox = _get_sub_fnt().getbbox(text)
    return (bbox[2] - bbox[0]) if bbox else 0

def _char_weight(ch):
    if ch in '。！？…': return 2.2
    if ch in '，、；：': return 1.6
    if ch == ' ':       return 0.5
    return 1.0

def _split_to_single_lines(text):
    lines, cur = [], ''
    for ch in text:
        if _measure(cur + ch) > ASS_MAX_W and cur:
            lines.append(cur.strip()); cur = ch
        else: cur += ch
    if cur.strip(): lines.append(cur.strip())
    return lines or [text]

def _split_sentence_timed(text, start, end):
    lines = _split_to_single_lines(text)
    if len(lines) == 1: return [{'text': text.strip(), 'start': start, 'end': end}]
    weights = [sum(_char_weight(c) for c in ln) for ln in lines]
    total_w = sum(weights) or 1
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
            events.append(f"Dialogue: 0,{_ass_time(chunk['start'])},{_ass_time(chunk['end'])},Default,,0,0,0,,{chunk['text']}")
    Path(ass_path).write_text(ASS_HEADER + '\n'.join(events) + '\n', encoding='utf-8')

# ── TTS ───────────────────────────────────────────────────────────────────────
VOICE = "zh-CN-YunxiNeural"

async def tts_with_subs(text, audio_out, ass_out, rate="+5%"):
    import edge_tts
    comm = edge_tts.Communicate(text, VOICE, rate=rate)
    audio_b, sentences = bytearray(), []
    async for chunk in comm.stream():
        if chunk['type'] == 'audio': audio_b.extend(chunk['data'])
        elif chunk['type'] == 'SentenceBoundary':
            start = chunk['offset'] / 1e7; dur = chunk['duration'] / 1e7
            sentences.append({'text': chunk['text'], 'start': start, 'end': start + dur})
    Path(audio_out).write_bytes(bytes(audio_b))
    sentences_to_ass(sentences, ass_out)
    return sentences[-1]['end'] if sentences else 5.0

def audio_dur(path):
    r = subprocess.run(['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', path],
                       capture_output=True, text=True)
    for s in json.loads(r.stdout).get('streams', []):
        if 'duration' in s: return float(s['duration'])
    return 5.0

# ── Playwright SVG → WebM ─────────────────────────────────────────────────────
async def _render_one(browser, html_content, out_webm, duration):
    """单个幻灯片录制。"""
    html_path = BUILD / 'html' / f'{Path(out_webm).stem}.html'
    html_path.write_text(html_content, encoding='utf-8')

    video_dir = BUILD / 'slides' / 'vtmp'
    video_dir.mkdir(exist_ok=True)

    context = await browser.new_context(
        viewport={'width': W, 'height': H},
        record_video_dir=str(video_dir),
        record_video_size={'width': W, 'height': H},
    )
    page = await context.new_page()
    await page.goto(f"file://{html_path.absolute()}")
    await asyncio.sleep(duration + 0.3)   # 多录 0.3s 保证动画完整
    await page.video.save_as(str(out_webm))
    await context.close()

def _svg_to_html(svg_content):
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ width: {W}px; height: {H}px; overflow: hidden; background: #0E0E1C; }}
</style>
</head>
<body>{svg_content}</body>
</html>"""

async def render_all_svg(slide_jobs):
    """
    并行录制所有幻灯片。
    slide_jobs: list of (svg_content, out_webm_path, duration)
    """
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=['--disable-dev-shm-usage'])
        tasks = [
            _render_one(browser, _svg_to_html(svg), out, dur)
            for svg, out, dur in slide_jobs
        ]
        await asyncio.gather(*tasks)
        await browser.close()

# ── 媒体工具（与 convert.py 相同） ───────────────────────────────────────────
def calc_overlay_dur(seg_dur, mtype='image'):
    available = seg_dur - MEDIA_OVERLAY_START - 0.6
    min_fade = MEDIA_FADE_D * 2 + 0.5
    if available < min_fade: return MEDIA_OVERLAY_DUR
    if mtype == 'gif': return round(max(4.0, min(available * 0.55, 14.0)), 2)
    return round(max(3.5, min(available * 0.40, 8.0)), 2)

def video_duration(path):
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'default=noprint_wrappers=1:nokey=1', path],
                           capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except: return None

def resolve_media(raw, num):
    if not raw: return None
    if raw.startswith('http'):
        import urllib.request
        ext  = Path(raw.split('?')[0]).suffix.lower() or '.jpg'
        dest = BUILD / 'media' / f'{num:02d}_asset{ext}'
        if dest.exists(): return str(dest)
        try:
            req = urllib.request.Request(raw, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as r, open(dest, 'wb') as f:
                shutil.copyfileobj(r, f)
            return str(dest)
        except: return None
    p = Path(raw)
    if p.exists(): return str(p)
    p2 = OUT_DIR / raw
    return str(p2) if p2.exists() else None

def media_type(path):
    ext = Path(path).suffix.lower()
    if ext in MEDIA_EXTS_GIF: return 'gif'
    if ext in MEDIA_EXTS_VIDEO: return 'video'
    if ext in MEDIA_EXTS_IMAGE: return 'image'
    return None

def make_media_panel_rgba(media_paths):
    if isinstance(media_paths, str): media_paths = [media_paths]
    paths = [p for p in media_paths if p and media_type(p) == 'image']
    if not paths: return None
    imgs = []
    for p in paths:
        try: imgs.append(Image.open(p).convert('RGB'))
        except: pass
    if not imgs: return None
    pad_inner, border = 12, 4
    max_pw = int(W * 0.68); max_ph = int(H * 0.68)
    if len(imgs) == 1:
        mimg = imgs[0]
        ratio = min(max_pw / mimg.width, max_ph / mimg.height)
        iw, ih = int(mimg.width * ratio), int(mimg.height * ratio)
        pw, ph = iw + (pad_inner + border) * 2, ih + (pad_inner + border) * 2
        panel = Image.new('RGBA', (pw, ph), (0,0,0,0))
        d = ImageDraw.Draw(panel)
        d.rounded_rectangle([0,0,pw,ph], radius=20, fill=(12,12,24,220))
        d.rounded_rectangle([0,0,pw,ph], radius=20, outline=(*TEAL,255), width=border)
        panel.paste(mimg.resize((iw, ih), Image.LANCZOS), (pad_inner+border, pad_inner+border))
    else:
        img_gap = 12; slot_w = (max_pw - (pad_inner+border)*2 - img_gap) // 2; slot_h = max_ph - (pad_inner+border)*2
        placed = []
        for mimg in imgs:
            r = min(slot_w/mimg.width, slot_h/mimg.height)
            placed.append((mimg.resize((int(mimg.width*r), int(mimg.height*r)), Image.LANCZOS), int(mimg.width*r), int(mimg.height*r)))
        content_w = placed[0][1]+img_gap+placed[1][1]; content_h = max(placed[0][2], placed[1][2])
        pw = content_w+(pad_inner+border)*2; ph = content_h+(pad_inner+border)*2
        panel = Image.new('RGBA', (pw, ph), (0,0,0,0)); d = ImageDraw.Draw(panel)
        d.rounded_rectangle([0,0,pw,ph], radius=20, fill=(12,12,24,220))
        d.rounded_rectangle([0,0,pw,ph], radius=20, outline=(*TEAL,255), width=border)
        cx2 = pad_inner+border
        for rim, iw, ih in placed:
            panel.paste(rim, (cx2, pad_inner+border+(content_h-ih)//2)); cx2 += iw+img_gap
    stem = '_'.join(Path(p).stem for p in paths[:2])
    out  = BUILD / 'slides' / f'overlay_panel_{stem}.png'
    panel.save(str(out))
    return str(out), pw, ph

# ── FFmpeg 编码（接受 webm 视频幻灯片输入） ────────────────────────────────────
def _ffmpeg_run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{r.stderr[-500:]}")

def make_seg_webm(slide_webm, audio, dur, out, ass=None):
    """基础段编码：webm 幻灯片 + 音频 + 字幕。"""
    vf = f'scale={W}:{H}'
    if ass and Path(ass).exists() and Path(ass).stat().st_size > 50:
        vf += f",ass='{ass.replace(chr(92), '/').replace(':', chr(92)+':')}'"
    audio_args = ['-i', audio] if audio else ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100']
    cmd = ['ffmpeg', '-y', '-i', slide_webm, *audio_args,
           '-t', str(dur), '-vf', vf,
           '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
           '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
           '-shortest', out]
    _ffmpeg_run(cmd)

def make_seg_with_overlay_webm(slide_webm, audio, dur, out, ass=None, media_path=None):
    if not media_path:
        make_seg_webm(slide_webm, audio, dur, out, ass); return
    paths = media_path if isinstance(media_path, list) else [media_path]
    paths = [p for p in paths if p and media_type(p) == 'image']
    if not paths:
        make_seg_webm(slide_webm, audio, dur, out, ass); return
    result = make_media_panel_rgba(paths)
    if not result:
        make_seg_webm(slide_webm, audio, dur, out, ass); return
    panel_path, pw, ph = result
    ox, oy = (W-pw)//2, (H-ph)//2
    ms, md, fd, sd = MEDIA_OVERLAY_START, calc_overlay_dur(dur,'image'), MEDIA_FADE_D, MEDIA_SLIDE_PX
    y_expr = (f"if(lt(t-{ms},{fd}),{oy+sd}-(({sd})*((t-{ms})/{fd})),"
              f"if(gt(t-{ms},{md-fd}),{oy}+(({sd})*((t-{ms}-{md-fd})/{fd})),{oy}))")
    vf = (f"[2:v]scale={pw}:{ph},"
          f"fade=t=in:st={ms}:d={fd}:alpha=1,fade=t=out:st={ms+md-fd}:d={fd}:alpha=1[ov];"
          f"[0:v]scale={W}:{H}[bg];[bg][ov]overlay=x={ox}:y='{y_expr}':"
          f"enable='between(t,{ms},{ms+md})':format=auto[vout]")
    if ass and Path(ass).exists() and Path(ass).stat().st_size > 50:
        ae = ass.replace('\\','/').replace(':','\\:')
        vf = vf.replace('[vout]','[vpre]') + f";[vpre]ass='{ae}'[vout]"
    audio_args = ['-i', audio] if audio else ['-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=44100']
    cmd = ['ffmpeg', '-y', '-i', slide_webm, *audio_args, '-loop', '1', '-i', panel_path,
           '-filter_complex', vf, '-map', '[vout]', '-map', '1:a',
           '-t', str(dur), '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
           '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2', '-shortest', out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    ⚠️  overlay失败，回退: {r.stderr[-200:]}")
        make_seg_webm(slide_webm, audio, dur, out, ass)

def make_gif_overlay_seg_webm(slide_webm, audio, dur, out, media_path, ass=None):
    ms, md, fd = MEDIA_OVERLAY_START, calc_overlay_dur(dur,'gif'), MEDIA_FADE_D
    pw, ph = int(W*0.68), int(H*0.68)
    ox, oy = (W-pw)//2, (H-ph)//2
    vf = (f"[3:v]scale={pw}:{ph}:force_original_aspect_ratio=decrease,"
          f"pad={pw}:{ph}:(ow-iw)/2:(oh-ih)/2:color=0x0C0C18,"
          f"fade=t=in:st={ms}:d={fd}:alpha=1,fade=t=out:st={ms+md-fd}:d={fd}:alpha=1,"
          f"format=yuva420p[gif];[0:v]scale={W}:{H}[bg];"
          f"[bg][gif]overlay=x={ox}:y={oy}:enable='between(t,{ms},{ms+md})':format=auto[vout]")
    if ass and Path(ass).exists() and Path(ass).stat().st_size > 50:
        ae = ass.replace('\\','/').replace(':','\\:')
        vf = vf.replace('[vout]','[vpre]') + f";[vpre]ass='{ae}'[vout]"
    audio_args = ['-i', audio] if audio else ['-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=44100']
    cmd = ['ffmpeg', '-y', '-i', slide_webm, *audio_args,
           '-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=44100',
           '-stream_loop','-1','-i', media_path,
           '-filter_complex', vf, '-map','[vout]','-map','1:a',
           '-t', str(dur), '-c:v','libx264','-preset','fast','-crf','23',
           '-c:a','aac','-b:a','128k','-ar','44100','-ac','2','-shortest', out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    ⚠️  gif overlay失败，回退")
        make_seg_webm(slide_webm, audio, dur, out, ass)

# ── 主流程 ────────────────────────────────────────────────────────────────────
async def main():
    import sys
    sys.path.insert(0, '/root/video-pipeline/pipeline')
    from svg_slide import make_news_svg, make_intro_svg, make_outro_svg

    print("=" * 60)
    print("  video-pipeline SVG v1  (手绘动画风格)")
    print("=" * 60)

    print("\nC1 — 解析文章...")
    data = parse(ARTICLE)
    ch   = data['channel_color']
    print(f"  ✅ {len(data['news'])} 条新闻  |  channel_color: {ch}")
    print(f"  Tabs: {' | '.join(data['tabs'])}")

    print("\nC2 — TTS 配音 + 字幕...")
    asegs = []
    intro_text = f"欢迎收看{data['show_name']}，本期带来 {data['total']} 条热门资讯。"
    intro_ap   = str(BUILD/"audio"/"00_intro.mp3")
    intro_ass  = str(BUILD/"subs"/"00_intro.ass")
    intro_dur  = await tts_with_subs(intro_text, intro_ap, intro_ass)
    print(f"  ✅ intro  {intro_dur:.1f}s")
    for item in data['news']:
        ap  = str(BUILD/"audio"/f"{item['num']:02d}.mp3")
        ass = str(BUILD/"subs"/f"{item['num']:02d}.ass")
        dur = await tts_with_subs(item['summary'], ap, ass)
        asegs.append({'p': ap, 'ass': ass, 'dur': dur, 'num': item['num']})
        print(f"  ✅ 新闻{item['num']}  {dur:.1f}s  {item['title'][:22]}…")
    outro_ap  = str(BUILD/"audio"/"99_outro.mp3")
    outro_ass = str(BUILD/"subs"/"99_outro.ass")
    outro_dur = await tts_with_subs("感谢收听，明天见。", outro_ap, outro_ass)
    print(f"  ✅ outro  {outro_dur:.1f}s")

    print("\nC2.5 — 解析素材...")
    for item in data['news']:
        paths = [resolve_media(r, item['num']) for r in item.get('media_raws', [item['media_raw']])]
        paths = [p for p in paths if p]
        item['media_paths'] = paths; item['media_path'] = paths[0] if paths else None
        if paths:
            types = '+'.join(media_type(p) or '?' for p in paths)
            print(f"  新闻{item['num']}: {types} ✅ ({len(paths)}个)")
        else:
            print(f"  新闻{item['num']}: 无素材")

    print("\nC3 — 生成 SVG 幻灯片...")
    intro_svg = make_intro_svg(data, ch)
    outro_svg = make_outro_svg(data, ch)
    news_svgs = [make_news_svg(item, data, ch) for item in data['news']]
    print(f"  ✅ SVG 生成完毕（intro + {len(news_svgs)} 条 + outro）")

    print("\nC3.5 — Playwright 并行录制动画幻灯片...")
    slide_jobs = []
    intro_webm = str(BUILD/"slides"/"00_intro.webm")
    outro_webm = str(BUILD/"slides"/"99_outro.webm")
    slide_jobs.append((intro_svg, intro_webm, intro_dur + 0.5))
    for i, item in enumerate(data['news']):
        path = str(BUILD/"slides"/f"{item['num']:02d}_news.webm")
        slide_jobs.append((news_svgs[i], path, asegs[i]['dur'] + 0.8))
    slide_jobs.append((outro_svg, outro_webm, outro_dur + 0.5))

    max_dur = max(d for _, _, d in slide_jobs)
    print(f"  录制 {len(slide_jobs)} 个幻灯片（并行，最长 {max_dur:.1f}s）...")
    await render_all_svg(slide_jobs)
    # 验证
    for _, path, _ in slide_jobs:
        size = Path(path).stat().st_size if Path(path).exists() else 0
        status = '✅' if size > 10000 else '❌'
        print(f"  {status} {Path(path).name}  {size//1024}KB")

    print("\nC4 — 编码视频段（字幕 + 素材叠加）...")
    segs = []
    # Intro
    sp = str(BUILD/"seg_00_intro.mp4")
    make_seg_webm(intro_webm, intro_ap, intro_dur+0.5, sp, ass=intro_ass)
    segs.append(sp); print(f"  ✅ intro {intro_dur+0.5:.1f}s")

    # News
    for i, item in enumerate(data['news']):
        ai  = asegs[i]; dur = ai['dur'] + 0.8
        sp  = str(BUILD/f"seg_{item['num']:02d}.mp4")
        wbm = str(BUILD/"slides"/f"{item['num']:02d}_news.webm")
        mps = item.get('media_paths', [])
        mp  = mps[0] if mps else None
        mt  = media_type(mp) if mp else None
        all_imgs = all(media_type(p)=='image' for p in mps) if mps else False

        if mps and all_imgs:
            od = calc_overlay_dur(dur, 'image')
            make_seg_with_overlay_webm(wbm, ai['p'], dur, sp, ass=ai['ass'], media_path=mps)
            print(f"  ✅ 新闻{item['num']} {dur:.1f}s [image×{len(mps)} overlay {od:.1f}s]")
        elif mp and mt == 'gif':
            od = calc_overlay_dur(dur, 'gif')
            make_gif_overlay_seg_webm(wbm, ai['p'], dur, sp, media_path=mp, ass=ai['ass'])
            print(f"  ✅ 新闻{item['num']} {dur:.1f}s [gif overlay {od:.1f}s]")
        elif mp and mt == 'video':
            clip_dur  = video_duration(mp)
            available = dur - MEDIA_OVERLAY_START - 0.6
            od = round(min(clip_dur, available), 2) if clip_dur else calc_overlay_dur(dur,'gif')
            make_gif_overlay_seg_webm(wbm, ai['p'], dur, sp, media_path=mp, ass=ai['ass'])
            print(f"  ✅ 新闻{item['num']} {dur:.1f}s [video overlay {od:.1f}s]")
        else:
            make_seg_webm(wbm, ai['p'], dur, sp, ass=ai['ass'])
            print(f"  ✅ 新闻{item['num']} {dur:.1f}s")
        segs.append(sp)

    # Outro
    sp = str(BUILD/"seg_99_outro.mp4")
    make_seg_webm(outro_webm, outro_ap, outro_dur+0.5, sp, ass=outro_ass)
    segs.append(sp); print(f"  ✅ outro {outro_dur+0.5:.1f}s")

    print("\nC4b — concat...")
    concat_f = BUILD / "concat.txt"
    concat_f.write_text('\n'.join(f"file '{s}'" for s in segs) + '\n')
    out_mp4  = OUT_DIR / "video_sketch.mp4"
    _ffmpeg_run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat_f),
                 '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
                 '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(out_mp4)])
    print(f"  ✅ {out_mp4}")

    print("\nC5 — 质量检验...")
    r = subprocess.run(['ffprobe','-v','quiet','-print_format','json','-show_streams','-show_format', str(out_mp4)],
                       capture_output=True, text=True)
    d = json.loads(r.stdout)
    dur_s  = float(d['format']['duration'])
    size_mb = int(d['format']['size']) / 1024 / 1024
    video_s = next((s for s in d['streams'] if s['codec_type']=='video'), {})
    audio_s = next((s for s in d['streams'] if s['codec_type']=='audio'), {})
    print(f"\n✅ 视频生成完成（手绘SVG版）")
    print(f"路径：{out_mp4}")
    print(f"时长：{int(dur_s//60)}分{int(dur_s%60)}秒 | 大小：{size_mb:.1f}MB | {video_s.get('width')}×{video_s.get('height')} | 字幕：已烧录")

if __name__ == '__main__':
    asyncio.run(main())
