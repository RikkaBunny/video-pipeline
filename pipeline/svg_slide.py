"""
svg_slide.py — 手绘风格 SVG 幻灯片生成器
NotebookLM 可爱手绘风，支持 intro / news / outro 三种类型
"""
import html as htmllib

W, H       = 1920, 1080
TAB_H      = 52
TITLE_H    = 108
CONTENT_Y  = TAB_H + TITLE_H   # 160
CONTENT_H  = H - CONTENT_Y     # 920
PAD        = 52
CARD_GAP   = 22

ACCENTS    = ['#4ECDC4', '#9B59B6', '#FFB347', '#F48FB1', '#A5D6A7']
BG         = '#0E0E1C'
CARD_BG    = '#1C1C34'
MUTED      = '#B0B0CC'

# ── 布局计算 ──────────────────────────────────────────────────────────────────
def _card_layout(n=5):
    """返回 n 张卡片的 (x, y, w, h) 列表，3+2 布局。"""
    avail_h = CONTENT_H - PAD * 2   # 816
    avail_w = W - PAD * 2           # 1816
    n_top   = min(3, n)
    n_bot   = max(0, n - n_top)
    h_top   = int(avail_h * 0.47) if n_bot else avail_h
    h_bot   = avail_h - h_top - CARD_GAP if n_bot else 0
    w_top   = (avail_w - CARD_GAP * (n_top - 1)) // n_top
    y_top   = CONTENT_Y + PAD

    positions = []
    for i in range(n_top):
        positions.append((PAD + i * (w_top + CARD_GAP), y_top, w_top, h_top))
    if n_bot:
        w_bot = (avail_w - CARD_GAP * (n_bot - 1)) // n_bot
        y_bot = y_top + h_top + CARD_GAP
        for i in range(n_bot):
            positions.append((PAD + i * (w_bot + CARD_GAP), y_bot, w_bot, h_bot))
    return positions

# ── 文字换行（CJK 近似） ───────────────────────────────────────────────────────
def _wrap(text, max_chars, max_lines=3):
    """CJK 感知换行，返回最多 max_lines 行文字列表。"""
    lines, cur, cur_w = [], '', 0.0
    for ch in text:
        cw = 1.05 if ord(ch) > 0x2E7F else 0.58
        if cur_w + cw > max_chars and cur:
            lines.append(cur)
            cur, cur_w = ch, cw
        else:
            cur += ch
            cur_w += cw
    if cur:
        lines.append(cur)
    result = lines[:max_lines]
    if len(lines) > max_lines and result:
        ln = result[-1]
        while sum(1.05 if ord(c) > 0x2E7F else 0.58 for c in ln) > max_chars - 1.5:
            ln = ln[:-1]
        result[-1] = ln + '…'
    return result

# ── SVG 共用 defs + CSS ───────────────────────────────────────────────────────
_DEFS = """\
<defs>
  <filter id="rough" x="-5%" y="-5%" width="110%" height="110%">
    <feTurbulence type="fractalNoise" baseFrequency="0.022" numOctaves="3" seed="7" result="n"/>
    <feDisplacementMap in="SourceGraphic" in2="n" scale="4.5" xChannelSelector="R" yChannelSelector="G"/>
  </filter>
  <filter id="rough2" x="-8%" y="-8%" width="116%" height="116%">
    <feTurbulence type="fractalNoise" baseFrequency="0.03" numOctaves="4" seed="3" result="n"/>
    <feDisplacementMap in="SourceGraphic" in2="n" scale="7" xChannelSelector="R" yChannelSelector="G"/>
  </filter>
  <filter id="glow">
    <feGaussianBlur stdDeviation="18" result="blur"/>
    <feComposite in="SourceGraphic" in2="blur" operator="over"/>
  </filter>
  <pattern id="dots" x="0" y="0" width="48" height="48" patternUnits="userSpaceOnUse">
    <circle cx="24" cy="24" r="1.4" fill="#aaaacc" opacity="0.15"/>
  </pattern>
  <style>
    @keyframes popCard {
      0%   { transform: scale(0.82) rotate(-2.5deg); opacity: 0; }
      60%  { transform: scale(1.04) rotate(0.8deg);  opacity: 1; }
      100% { transform: scale(1)    rotate(0deg);     opacity: 1; }
    }
    @keyframes pop {
      0%   { transform: scale(0.4) rotate(-8deg); opacity: 0; }
      65%  { transform: scale(1.1) rotate(2deg);  opacity: 1; }
      100% { transform: scale(1)   rotate(0deg);  opacity: 1; }
    }
    @keyframes fadeUp {
      from { opacity: 0; transform: translateY(18px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    @keyframes drawLine {
      from { stroke-dashoffset: 2400; }
      to   { stroke-dashoffset: 0; }
    }
    @keyframes float {
      0%,100% { transform: translateY(0px) rotate(0deg); }
      50%     { transform: translateY(-12px) rotate(3deg); }
    }
    @keyframes float2 {
      0%,100% { transform: translateY(0px) rotate(0deg); }
      50%     { transform: translateY(-8px) rotate(-4deg); }
    }
    @keyframes pulse {
      0%,100% { opacity: 0.35; transform: scale(1); }
      50%     { opacity: 0.6;  transform: scale(1.08); }
    }
    @keyframes spinSlow { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
    .card1 { animation: popCard 0.55s cubic-bezier(.34,1.56,.64,1) 0.25s both; }
    .card2 { animation: popCard 0.55s cubic-bezier(.34,1.56,.64,1) 0.58s both; }
    .card3 { animation: popCard 0.55s cubic-bezier(.34,1.56,.64,1) 0.91s both; }
    .card4 { animation: popCard 0.55s cubic-bezier(.34,1.56,.64,1) 1.24s both; }
    .card5 { animation: popCard 0.55s cubic-bezier(.34,1.56,.64,1) 1.57s both; }
    .badge { animation: pop 0.45s cubic-bezier(.34,1.56,.64,1) both; }
    .badge1 { animation-delay: 0.12s; }
    .badge2 { animation-delay: 0.28s; }
    .badge3 { animation-delay: 0.44s; }
    .title-in { animation: fadeUp 0.5s ease-out 0.08s both; }
    .underline { stroke-dasharray: 2400; animation: drawLine 1.3s ease-out 0.1s both; }
    .deco1 { animation: float  4.2s ease-in-out infinite; }
    .deco2 { animation: float2 5.0s ease-in-out 0.9s infinite; }
    .deco3 { animation: float  3.6s ease-in-out 1.8s infinite; }
    .star  { animation: spinSlow 10s linear infinite; }
    .glow1 { animation: pulse 4.5s ease-in-out infinite; transform-origin: 1620px 820px; }
    .glow2 { animation: pulse 5.5s ease-in-out 1.2s infinite; transform-origin: 280px 180px; }
  </style>
</defs>"""

def _bg(glow_color1='#3D1E6B', glow_color2='#0D3B2B'):
    """共用背景：深色底 + 点阵 + 光晕圆。"""
    return f"""\
  <rect width="1920" height="1080" fill="{BG}"/>
  <rect width="1920" height="1080" fill="url(#dots)"/>
  <circle class="glow1" cx="1620" cy="820" r="300" fill="{glow_color1}" opacity="0.0" filter="url(#glow)"/>
  <circle class="glow1" cx="1620" cy="820" r="200" fill="none" stroke="{glow_color1}" stroke-width="1.5" opacity="0.35"/>
  <circle class="glow2" cx="280"  cy="180" r="240" fill="{glow_color2}"  opacity="0.0" filter="url(#glow)"/>
  <circle class="glow2" cx="280"  cy="180" r="160" fill="none" stroke="{glow_color2}"  stroke-width="1.2" opacity="0.30"/>"""

def _tab_bar(tabs, active, channel_color):
    """顶部手绘 Tab 栏。"""
    n    = len(tabs)
    tw   = W // n
    out  = f'<rect x="0" y="0" width="{W}" height="{TAB_H}" fill="#15152A" filter="url(#rough)"/>\n'
    for i, tab in enumerate(tabs):
        x1 = i * tw
        esc = htmllib.escape(tab)
        if tab == active:
            out += f'  <rect x="{x1+4}" y="4" width="{tw-8}" height="44" fill="{channel_color}" rx="8" filter="url(#rough)"/>\n'
            out += f'  <text x="{x1+tw//2}" y="28" text-anchor="middle" dominant-baseline="middle" font-family="Noto Sans CJK SC,Noto Sans SC,sans-serif" font-size="22" fill="white" font-weight="bold">{esc}</text>\n'
        else:
            out += f'  <rect x="{x1+4}" y="4" width="{tw-8}" height="44" fill="#1E1E3A" rx="8" filter="url(#rough)"/>\n'
            out += f'  <text x="{x1+tw//2}" y="28" text-anchor="middle" dominant-baseline="middle" font-family="Noto Sans CJK SC,Noto Sans SC,sans-serif" font-size="20" fill="#777799">{esc}</text>\n'
    return out

def _deco_corners():
    """四角漂浮装饰。"""
    return """\
  <g class="deco1" style="opacity:0.75"><text x="1840" y="290" font-size="42" font-family="Noto Color Emoji,sans-serif">🌿</text></g>
  <g class="deco2" style="opacity:0.80"><text x="24"   y="340" font-size="36" font-family="Noto Color Emoji,sans-serif">💫</text></g>
  <g class="deco3" style="opacity:0.70"><text x="1848" y="700" font-size="38" font-family="Noto Color Emoji,sans-serif">✨</text></g>
  <g class="star"  style="transform-origin:1760px 110px; opacity:0.55">
    <text x="1740" y="130" font-size="52" font-family="Noto Color Emoji,sans-serif">✦</text>
  </g>"""

# ── 新闻卡片 ──────────────────────────────────────────────────────────────────
def _card_svg(cx, cy, cw, ch, emoji, title, body, accent, cls):
    """生成单张要点卡片 SVG。"""
    max_chars = (cw - 100) / 24.0   # 粗估每行可放字数
    lines = _wrap(body, max_chars, max_lines=4)

    # body tspan
    body_svg = ''
    body_y   = cy + 105
    line_h   = 32
    for j, ln in enumerate(lines):
        dy  = 0 if j == 0 else line_h
        body_svg += f'<tspan x="{cx+18}" dy="{dy}">{htmllib.escape(ln)}</tspan>'

    title_esc = htmllib.escape(title[:18])  # 截断超长标题
    return f"""\
  <g class="{cls}">
    <g filter="url(#rough)">
      <rect x="{cx}" y="{cy}" width="{cw}" height="{ch}" fill="{CARD_BG}" rx="14" stroke="{accent}" stroke-width="2.5"/>
      <rect x="{cx}" y="{cy}" width="{cw}" height="9"  fill="{accent}" rx="14"/>
      <rect x="{cx}" y="{cy+4}" width="{cw}" height="5" fill="{accent}"/>
    </g>
    <text x="{cx+16}" y="{cy+60}"
          font-family="Noto Color Emoji,Apple Color Emoji,Segoe UI Emoji,sans-serif"
          font-size="42">{emoji}</text>
    <text x="{cx+70}" y="{cy+46}"
          font-family="Noto Sans CJK SC,Noto Sans SC,sans-serif"
          font-size="24" fill="{accent}" font-weight="bold">{title_esc}</text>
    <text x="{cx+18}" y="{body_y}"
          font-family="Noto Sans CJK SC,Noto Sans SC,sans-serif"
          font-size="22" fill="{MUTED}">{body_svg}</text>
  </g>"""

# ── 对外接口 ──────────────────────────────────────────────────────────────────
def make_news_svg(item, data, channel_color='#2DA44E'):
    """生成一条新闻的手绘 SVG 幻灯片。"""
    tabs   = data['tabs']
    active = item['cat']
    title  = htmllib.escape(item['title'])
    cards  = item['cards'][:5]
    positions = _card_layout(len(cards))

    # 标题字号（简单适配长度）
    t_len = len(item['title'])
    fsz   = 46 if t_len < 28 else 40 if t_len < 38 else 34

    # 统计徽章（从 stars / language 字段，如果有的话就用，否则跳过）
    extra = item.get('extra', {})
    badges_svg = ''
    badge_items = []
    if extra.get('new_stars'):
        badge_items.append(('⭐', f"{extra['new_stars']:,} stars", '#FFD166', '#1a1a2e'))
    if extra.get('language'):
        lang_colors = {'Python': ('#3776AB', 'white'), 'TypeScript': ('#3178C6', 'white'),
                       'Kotlin': ('#7F52FF', 'white'), 'C++': ('#F34B7D', 'white')}
        bg, fg = lang_colors.get(extra['language'], ('#52B788', '#0a2a0a'))
        badge_items.append(('', extra['language'], bg, fg))
    bx = 64
    for bi, (bemoji, btext, bbg, bfg) in enumerate(badge_items):
        bw = len(btext) * 16 + (40 if bemoji else 20)
        by = TAB_H + TITLE_H + 8   # just below title, but since title uses full TITLE_H area, badges sit inside
        # Actually put badges inside title bar
        by = TAB_H + TITLE_H // 2 + 14
        badges_svg += f"""\
  <g class="badge badge{bi+1}">
    <g filter="url(#rough2)" transform="rotate({-1.5 + bi * 1.8}, {bx + bw//2}, {by})">
      <rect x="{bx}" y="{by-22}" width="{bw}" height="44" fill="{bbg}" rx="6"/>
    </g>
    <text x="{bx + bw//2}" y="{by+1}"
          text-anchor="middle" dominant-baseline="middle"
          font-family="Noto Color Emoji,Noto Sans CJK SC,sans-serif"
          font-size="22" fill="{bfg}" font-weight="bold">{htmllib.escape(bemoji + ' ' + btext if bemoji else btext)}</text>
  </g>"""
        bx += bw + 14

    # 卡片 SVG
    cards_svg = ''
    cls_names = ['card1', 'card2', 'card3', 'card4', 'card5']
    for i, (card, (cx, cy, cw, ch)) in enumerate(zip(cards, positions)):
        accent = ACCENTS[i % len(ACCENTS)]
        cards_svg += _card_svg(cx, cy, cw, ch,
                                card['emoji'], card['title'], card['body'],
                                accent, cls_names[i])

    # 标题下划线终点估算
    ul_end = min(64 + t_len * (fsz * 0.92), W - 60)

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
{_DEFS}
{_bg()}
  <!-- Tab 栏 -->
{_tab_bar(tabs, active, channel_color)}
  <!-- 标题区 -->
  <rect x="0" y="{TAB_H}" width="{W}" height="{TITLE_H}" fill="#13132A"/>
  <line x1="0" y1="{TAB_H+TITLE_H-1}" x2="{W}" y2="{TAB_H+TITLE_H-1}" stroke="#2A2A48" stroke-width="1"/>
  <text class="title-in" x="64" y="{TAB_H + TITLE_H//2 - 10}"
        dominant-baseline="middle"
        font-family="Noto Sans CJK SC,Noto Sans SC,sans-serif"
        font-size="{fsz}" fill="white" font-weight="bold">{title}</text>
  <path class="underline"
        d="M64,{TAB_H+TITLE_H-10} Q{int(ul_end*0.4)},{TAB_H+TITLE_H-8} {int(ul_end)},{TAB_H+TITLE_H-11}"
        stroke="{channel_color}" stroke-width="3.5" fill="none"
        stroke-linecap="round" filter="url(#rough)"/>
  <!-- 统计徽章 -->
{badges_svg}
  <!-- 要点卡片 -->
{cards_svg}
  <!-- 角落装饰 -->
{_deco_corners()}
  <!-- 底部署名 -->
  <text x="{W//2}" y="1068"
        text-anchor="middle"
        font-family="Noto Sans CJK SC,sans-serif"
        font-size="18" fill="#44446A">
    提示：内容由AI辅助创作，可能存在幻觉和错误  ·  作者Bunny，视频版在同名哔哩哔哩
  </text>
</svg>"""
    return svg


def make_intro_svg(data, channel_color='#2DA44E'):
    """生成开场介绍幻灯片。"""
    tabs    = data['tabs']
    date_s  = data['date']
    cats    = data['cat_order']
    n       = len(cats)

    # 分类卡片布局（同 Pillow 版 intro）
    avail_h = CONTENT_H - PAD * 2
    avail_w = W - PAD * 2
    if n <= 3:
        rows = [cats]
    else:
        mid  = (n + 1) // 2
        rows = [cats[:mid], cats[mid:]]
    n_rows = len(rows)
    card_h = (avail_h - CARD_GAP * (n_rows - 1)) // n_rows

    CAT_ICONS = {
        '开发生态': '⚙️', '模型发布': '🤖', '开发工具': '🛠️',
        '平台与框架': '🏗️', 'AI 智能体': '🤖', '边缘计算': '📱',
        '技术与洞察': '💡', '产品应用': '🚀', '要闻': '📰',
        '智能体与应用': '🔧', '开源社区': '🌐',
    }

    cards_svg = ''
    cls_names = ['card1', 'card2', 'card3', 'card4', 'card5', 'card6']
    ci = 0
    for ri, row in enumerate(rows):
        n_cols = len(row)
        cw     = (avail_w - CARD_GAP * (n_cols - 1)) // n_cols
        y      = CONTENT_Y + PAD + ri * (card_h + CARD_GAP)
        for ci2, cat in enumerate(row):
            cx     = PAD + ci2 * (cw + CARD_GAP)
            items  = data['cat_items'].get(cat, [])
            body   = '  ·  '.join(items[:4])
            icon   = CAT_ICONS.get(cat, '📌')
            accent = ACCENTS[(ri * 3 + ci2) % len(ACCENTS)]
            cards_svg += _card_svg(cx, y, cw, card_h,
                                   icon, cat, body,
                                   accent, cls_names[ci % len(cls_names)])
            ci += 1

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
{_DEFS}
{_bg('#1E3A5F', '#0D3B2B')}
  <!-- 同心圆背景 -->
  {''.join(f'<circle cx="{W//2}" cy="{H//2-60}" r="{r}" fill="none" stroke="{channel_color}" stroke-width="0.8" opacity="{0.04+r/350*0.12}"/>' for r in range(350, 0, -30))}
{_tab_bar(tabs, '开场', channel_color)}
  <rect x="0" y="{TAB_H}" width="{W}" height="{TITLE_H}" fill="#13132A"/>
  <line x1="0" y1="{TAB_H+TITLE_H-1}" x2="{W}" y2="{TAB_H+TITLE_H-1}" stroke="#2A2A48" stroke-width="1"/>
  <text class="title-in" x="64" y="{TAB_H+TITLE_H//2}"
        dominant-baseline="middle"
        font-family="Noto Sans CJK SC,sans-serif"
        font-size="48" fill="white" font-weight="bold">{htmllib.escape(date_s)}  资讯概览</text>
{cards_svg}
{_deco_corners()}
</svg>"""
    return svg


def make_outro_svg(data, channel_color='#2DA44E'):
    """生成结尾幻灯片。"""
    tabs   = data['tabs']
    date_s = data['date']
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
{_DEFS}
{_bg('#1E0A3C', '#0D3B2B')}
  <!-- 同心圆 -->
  {''.join(f'<circle cx="{W//2}" cy="{H//2-30}" r="{r}" fill="none" stroke="#7B2FBE" stroke-width="1" opacity="{0.05+r/280*0.15}"/>' for r in range(280, 0, -25))}
{_tab_bar(tabs, '结尾', channel_color)}
  <text x="{W//2}" y="{H//2-50}"
        text-anchor="middle" dominant-baseline="middle"
        font-family="Noto Sans CJK SC,sans-serif"
        font-size="76" fill="white" font-weight="bold"
        style="animation: fadeUp 0.8s ease-out 0.2s both; opacity:0">感谢收听 · 明日见</text>
  <text x="{W//2}" y="{H//2+50}"
        text-anchor="middle" dominant-baseline="middle"
        font-family="Noto Sans CJK SC,sans-serif"
        font-size="30" fill="{MUTED}"
        style="animation: fadeUp 0.6s ease-out 0.6s both; opacity:0">
    AI 资讯播客  ·  作者 Bunny  ·  哔哩哔哩同名
  </text>
  <text x="{W//2}" y="{H//2+110}"
        text-anchor="middle" dominant-baseline="middle"
        font-family="Noto Sans CJK SC,sans-serif"
        font-size="24" fill="#555577"
        style="animation: fadeUp 0.5s ease-out 1.0s both; opacity:0">{date_s}</text>
  <g class="deco1" style="opacity:0.8"><text x="900" y="240" font-size="56" font-family="Noto Color Emoji,sans-serif">🎉</text></g>
  <g class="deco2" style="opacity:0.7"><text x="980" y="880" font-size="44" font-family="Noto Color Emoji,sans-serif">✨</text></g>
  <g class="star"  style="transform-origin:{W//2}px {H//2-30}px; opacity:0.3">
    <text x="{W//2-28}" y="{H//2-30+12}" font-size="56" font-family="Noto Color Emoji,sans-serif">✦</text>
  </g>
</svg>"""
    return svg
