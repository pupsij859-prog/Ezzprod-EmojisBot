"""EzzProd: процедурные «3D»-заглушки шаблонов (основа — эталонная реализация из docs/DESIGN.md).
svg_tile(shape, fill, stroke, text, uid) -> самостоятельный SVG (viewBox 256x256).
Палитры приглушённые (без кислотных цветов); ширина текста меряется доступным шрифтом, а при его
отсутствии оценивается приблизительно, поэтому модуль работает и на Linux-хостинге."""
import math
import os
from xml.sax.saxutils import escape

from PIL import ImageFont

_FONT_CANDIDATES = [
    r'C:/Windows/Fonts/ariblk.ttf',
    r'C:/Windows/Fonts/arialbd.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',
]
FONT_STACK = "'Arial Black','Segoe UI Black','Helvetica Neue',Arial,sans-serif"

PALETTES = [  # (name, fill, stroke)
    ('navy', '#1d2a40', '#9db8d6'), ('steel', '#3d6a9e', '#d6e4f3'), ('gold', '#c9a43c', '#f3e6b8'),
    ('violet', '#6a5fb0', '#dcd8f2'), ('coral', '#c45f55', '#f6d9d5'), ('mint', '#3a9c7a', '#d3efe4'),
    ('rose', '#b8527f', '#f1d6e3'), ('graphite', '#3a4559', '#cdd6e5'), ('indigo', '#3f56b0', '#d0d8f5'),
    ('olive', '#5b6d36', '#dde8b8'),
]

# ---- colour helpers --------------------------------------------------------
def _rgb(c):
    c = c.lstrip('#')
    return [int(c[i:i + 2], 16) for i in (0, 2, 4)]

def _hex(v):
    return '#%02x%02x%02x' % tuple(max(0, min(255, round(x))) for x in v)

def mix(a, b, t):
    A, B = _rgb(a), _rgb(b)
    return _hex([A[i] + (B[i] - A[i]) * t for i in range(3)])

def lighten(c, t):
    return mix(c, '#ffffff', t)

def darken(c, t):
    return mix(c, '#000000', t)

def n(v):
    s = '%.1f' % v
    return s[:-2] if s.endswith('.0') else s

_font = None
_font_loaded = False


def text_width(text, size):
    global _font, _font_loaded
    if not _font_loaded:
        _font_loaded = True
        for path in _FONT_CANDIDATES:
            if os.path.exists(path):
                _font = ImageFont.truetype(path, 100)
                break
    if _font is None:
        return 0.68 * size * len(text)  # грубая оценка ширины жирного гротеска
    return _font.getlength(text) * size / 100.0

def _star(cx, cy, R, r):
    pts = []
    for k in range(10):
        rad = R if k % 2 == 0 else r
        a = math.radians(-90 + k * 36)
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    return 'M' + 'L'.join('%s %s' % (n(x), n(y)) for x, y in pts) + 'Z'

# ---- shapes: parts = (role, path d); text = (cx, cy, max_width, rotation); gloss = (cx, cy, rx, ry, rot)
SHAPES = {
 'squircle': dict(parts=[('main', 'M84 28H172A56 56 0 0 1 228 84V172A56 56 0 0 1 172 228H84A56 56 0 0 1 28 172V84A56 56 0 0 1 84 28Z')],
                  text=(128, 128, 134, 0), gloss=(92, 66, 58, 24, -24)),
 'circle': dict(parts=[('main', 'M28 128A100 100 0 1 1 228 128A100 100 0 1 1 28 128Z'),
                       ('line', 'M48 128A80 80 0 1 1 208 128A80 80 0 1 1 48 128Z')],
                text=(128, 128, 126, 0), gloss=(96, 62, 62, 24, -30)),
 'shield': dict(parts=[('main', 'M128 26L214 56V124C214 176 176 210 128 232C80 210 42 176 42 124V56Z')],
                text=(128, 118, 108, 0), gloss=(92, 66, 52, 22, -28)),
 'star': dict(parts=[('main', _star(128, 136, 100, 48))],
              text=(128, 140, 70, 0), gloss=(104, 82, 40, 16, -30)),
 'bubble': dict(parts=[('main', 'M84 40H172A56 56 0 0 1 228 96V134A56 56 0 0 1 172 190H118L74 230L80 190H84A56 56 0 0 1 28 134V96A56 56 0 0 1 84 40Z')],
                text=(128, 115, 150, 0), gloss=(92, 68, 56, 22, -18)),
 'flag': dict(parts=[('metal', 'M40 34A7 7 0 0 1 54 34V226A7 7 0 0 1 40 226Z'),
                     ('main', 'M54 44C92 26 118 62 156 44C184 31 204 40 220 46V150C204 144 184 135 156 148C118 166 92 130 54 148Z'),
                     ('metal', 'M47 22A11 11 0 1 1 47 44A11 11 0 1 1 47 22Z')],
              text=(138, 96, 112, -3), gloss=(110, 62, 46, 16, -10)),
 'coin': dict(parts=[('main', 'M28 128A100 100 0 1 1 228 128A100 100 0 1 1 28 128Z'),
                     ('line', 'M46 128A82 82 0 1 1 210 128A82 82 0 1 1 46 128Z'),
                     ('line', 'M58 128A70 70 0 1 1 198 128A70 70 0 1 1 58 128Z')],
              text=(128, 128, 112, 0), gloss=(94, 60, 62, 22, -30), depth=16),
 'heart': dict(parts=[('main', 'M128 226C60 176 28 142 28 98C28 62 54 38 88 38C108 38 122 48 128 62C134 48 148 38 168 38C202 38 228 62 228 98C228 142 196 176 128 226Z')],
               text=(128, 106, 100, 0), gloss=(82, 70, 40, 18, -34)),
 'crown': dict(parts=[('main', 'M38 198L28 86L82 128L128 52L174 128L228 86L218 198Z'),
                      ('accent', 'M38 190H218V222A10 10 0 0 1 208 232H48A10 10 0 0 1 38 222Z'),
                      ('accent', 'M28 70A13 13 0 1 1 28 96A13 13 0 1 1 28 70Z'),
                      ('accent', 'M128 36A13 13 0 1 1 128 62A13 13 0 1 1 128 36Z'),
                      ('accent', 'M228 70A13 13 0 1 1 228 96A13 13 0 1 1 228 70Z')],
               text=(128, 150, 100, 0), gloss=(96, 112, 48, 16, -40)),
 'trophy': dict(parts=[('handle', 'M72 62H46C46 98 56 118 80 124'),
                       ('handle', 'M184 62H210C210 98 200 118 176 124'),
                       ('main', 'M112 160H144V196H112Z'),
                       ('main', 'M70 36H186V104C186 142 160 168 128 168C96 168 70 142 70 104Z'),
                       ('accent', 'M82 196H174A8 8 0 0 1 182 204V222A8 8 0 0 1 174 230H82A8 8 0 0 1 74 222V204A8 8 0 0 1 82 196Z')],
                text=(128, 98, 84, 0), gloss=(98, 66, 40, 20, -20)),
 'gift': dict(parts=[('main', 'M44 112H212V214A18 18 0 0 1 194 232H62A18 18 0 0 1 44 214Z'),
                     ('main', 'M32 84A14 14 0 0 1 46 70H210A14 14 0 0 1 224 84V108A6 6 0 0 1 218 114H38A6 6 0 0 1 32 108Z'),
                     ('accent', 'M112 70H144V232H112Z'),
                     ('accent', 'M128 72C100 72 76 64 76 46C76 30 96 26 108 36C120 46 128 60 128 72Z'),
                     ('accent', 'M128 72C156 72 180 64 180 46C180 30 160 26 148 36C136 46 128 60 128 72Z')],
              text=(128, 174, 86, 0), gloss=(86, 90, 36, 14, -10)),
 'rocket': dict(parts=[('flame', 'M100 176C100 206 114 226 128 242C142 226 156 206 156 176Z'),
                       ('main', 'M82 130L40 176V206L88 182Z'),
                       ('main', 'M174 130L216 176V206L168 182Z'),
                       ('main', 'M128 22C170 54 186 112 178 170H78C70 112 86 54 128 22Z'),
                       ('glass', 'M128 76A22 22 0 1 1 128 120A22 22 0 1 1 128 76Z')],
                text=(128, 148, 62, 0), gloss=(106, 62, 24, 12, -60)),
 'bolt': dict(parts=[('main', 'M150 18L52 144H110L92 238L204 102H142Z')],
              text=(128, 134, 60, -16), gloss=(112, 72, 30, 12, -52)),
 'drop': dict(parts=[('main', 'M128 20C128 20 54 104 54 158C54 198 88 232 128 232C168 232 202 198 202 158C202 104 128 20 128 20Z')],
              text=(128, 168, 92, 0), gloss=(98, 122, 34, 22, -62)),
}
SHAPE_ORDER = ['squircle', 'circle', 'shield', 'star', 'bubble', 'flag', 'coin',
               'heart', 'crown', 'trophy', 'gift', 'rocket', 'bolt', 'drop']
FS_MIN = 18      # below this the text is shortened from the right (never shrunk further)
FS_MAX = 76

def _fit_text(text, W):
    """-> (text, font_size, width). Shortens text from the right until font size >= FS_MIN."""
    t = text
    while True:
        w0 = text_width(t, FS_MAX)
        fs = min(FS_MAX, FS_MAX * W / w0)
        if fs >= FS_MIN or len(t) <= 1:
            return t, fs, w0 * fs / FS_MAX
        t = t[:-1]

def svg_tile(shape, fill, stroke, text='', uid='t', shadow=True):
    sh = SHAPES[shape]
    parts = sh['parts']
    depth = sh.get('depth', 9)
    F, S = fill, stroke
    deepF, lightF, darkF = darken(F, .55), lighten(F, .38), darken(F, .36)
    darkS = darken(S, .30)
    lightS = lighten(S, .35)
    mains = [d for r, d in parts if r == 'main']

    def grad(i, stops, x1=52, y1=24, x2=206, y2=236):
        s = ''.join('<stop offset="%s" stop-color="%s"/>' % st for st in stops)
        return ('<linearGradient id="%s%s" gradientUnits="userSpaceOnUse" x1="%s" y1="%s" x2="%s" y2="%s">%s</linearGradient>'
                % (uid, i, x1, y1, x2, y2, s))

    D = [grad('g', [(0, lightF), (.42, F), (1, darkF)]),                          # body
         grad('a', [(0, lighten(S, .5)), (.5, S), (1, darkS)]),                   # accent parts
         grad('m', [(0, '#eef4fb'), (.5, '#a5b6cb'), (1, '#5d6e86')]),            # metal
         grad('f', [(0, '#fff0a8'), (.55, '#f2b53a'), (1, '#e8683a')], 0, 0, 0, 1),  # flame
         grad('t', [(0, '#ffffff'), (1, lightS)], 0, 0, 0, 1),                    # text face
         ('<linearGradient id="%sh" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".62"/>'
          '<stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>' % uid)]
    for k, sd in (('b5', 5), ('b7', 7), ('b1', 1)):
        D.append('<filter id="%s%s" x="-30%%" y="-30%%" width="160%%" height="160%%"><feGaussianBlur stdDeviation="%s"/></filter>' % (uid, k, sd))
    D.append('<clipPath id="%sc">%s</clipPath>' % (uid, ''.join('<path d="%s"/>' % d for d in mains)))

    B = []
    if shadow:                                                                       # 1. ground shadow
        B.append('<ellipse cx="128" cy="244" rx="78" ry="8" fill="#000" opacity=".45" filter="url(#%sb5)"/>' % uid)
    B.append('<g transform="translate(0 %s)" stroke-linejoin="round" stroke-linecap="round">' % depth)   # 2. extrusion
    for role, d in parts:
        if role == 'line':
            continue
        if role == 'handle':
            B.append('<path d="%s" stroke="%s" stroke-width="22"/>' % (d, deepF))
        else:
            B.append('<path d="%s" fill="%s" stroke="%s" stroke-width="8"/>' % (d, deepF, deepF))
    B.append('</g><g stroke-linejoin="round" stroke-linecap="round">')                                   # 3. bodies
    for role, d in parts:
        if role == 'main':
            B.append('<path d="%s" fill="url(#%sg)" stroke="%s" stroke-width="7"/>' % (d, uid, S))
        elif role == 'accent':
            B.append('<path d="%s" fill="url(#%sa)" stroke="%s" stroke-width="4"/>' % (d, uid, darken(S, .5)))
        elif role == 'metal':
            B.append('<path d="%s" fill="url(#%sm)" stroke="#3a4a60" stroke-width="4"/>' % (d, uid))
        elif role == 'flame':
            B.append('<path d="%s" fill="url(#%sf)" stroke="#fff3c4" stroke-width="3"/>' % (d, uid))
        elif role == 'glass':
            B.append('<path d="%s" fill="%s" stroke="%s" stroke-width="6"/>' % (d, darken(F, .72), S))
        elif role == 'line':
            B.append('<path d="%s" stroke="%s" stroke-opacity=".42" stroke-width="3"/>' % (d, S))
        elif role == 'handle':
            B.append('<path d="%s" stroke="%s" stroke-width="22"/><path d="%s" stroke="url(#%sg)" stroke-width="13"/>' % (d, S, d, uid))
    B.append('</g>')
    gx, gy, grx, gry, grot = sh['gloss']                                                                 # 4. inner shadow + gloss
    B.append('<g clip-path="url(#%sc)">' % uid)
    for d in mains:
        B.append('<path d="%s" stroke="%s" stroke-opacity=".5" stroke-width="26" transform="translate(-5 -8)" filter="url(#%sb7)"/>' % (d, deepF, uid))
    B.append('<ellipse cx="%s" cy="%s" rx="%s" ry="%s" transform="rotate(%s %s %s)" fill="url(#%sh)"/></g>' % (gx, gy, grx, gry, grot, gx, gy, uid))
    B.append('<circle cx="%s" cy="%s" r="5" fill="#fff" opacity=".8" filter="url(#%sb1)"/>' % (n(gx - grx * .45), n(gy - gry * .1), uid))
    if text:                                                                                             # 5. brand text
        cx, cy, W, rot = sh['text']
        text, fs, fw = _fit_text(text, W)
        ty, sw = cy + fs * .36, max(2.5, fs * .11)
        common = ('x="%s" y="%s" text-anchor="middle" font-family="%s" font-weight="900" font-size="%s" textLength="%s" '
                  'lengthAdjust="spacingAndGlyphs" stroke-linejoin="round"' % (n(cx), n(ty), FONT_STACK, n(fs), n(fw)))
        t = escape(text)
        B.append('<g transform="rotate(%s %s %s)">' % (rot, cx, cy))
        B.append('<text %s transform="translate(0 %s)" fill="%s" stroke="%s" stroke-width="%s" opacity=".9">%s</text>' % (common, n(fs * .09), deepF, deepF, n(sw), t))
        B.append('<text %s fill="url(#%st)" stroke="%s" stroke-width="%s" paint-order="stroke">%s</text></g>' % (common, uid, deepF, n(sw * .8), t))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256" fill="none"><defs>%s</defs>%s</svg>'
            % (''.join(D), ''.join(B)))

def pick(template_index):
    """Deterministic shape + palette for the n-th template (0-based, global index)."""
    shape = SHAPE_ORDER[template_index % 14]
    pal = PALETTES[(template_index // 14 + template_index * 3) % 10]
    return shape, pal
