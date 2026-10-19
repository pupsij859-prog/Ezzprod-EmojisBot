// Шаблоны TGS в Mini App: загрузка, живое превью и проигрывание (lottie-web).
// Подстановка ника/логотипа и цвета — порт app/lottie.py: меняй их вместе, тогда превью совпадает с паком.
import { api, PREVIEW_VERSION, previewUrl } from './api.js';
import { lookFor, state } from './store.js';
import { h, prefersReducedMotion } from './ui.js';

const LOTTIE_URL = 'js/vendor/lottie_light.min.js?v=5.12.2';
const SLOT = 'ezzprod color';
const LOGO_HEIGHT = 1.9;
const GAP = 0.18;
// ник вписывается в долю места надписи: короткий упирается в высоту (крупнее), длинный — в ширину (мельче)
const TEXT_FIT_W = 0.86;
const TEXT_FIT_H = 0.78;
const TEXT_WITH_LOGO = 0.72; // рядом с логотипом
const SIZE_RANGE = [0.4, 1.6]; // «размер надписи» пользователя (как в app/lottie.py)
const PLACE_RANGE = { x: 100, y: 100, r: 180 }; // сдвиг надписи, % ширины места надписи; поворот, °
const TOL = 0.06; // допуск, чтобы оттенок считался тем же основным цветом (RGB 0..1), как в app/lottie.py

function shrink([x0, y0, x1, y1], k) {
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, hw = ((x1 - x0) * k) / 2, hh = ((y1 - y0) * k) / 2;
  return [cx - hw, cy - hh, cx + hw, cy + hh];
}

let lottieLib = null;
export function loadLottie() {
  lottieLib ||= new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = LOTTIE_URL;
    s.async = true;
    const failed = () => { clearTimeout(timer); s.remove(); lottieLib = null; reject(new Error('lottie')); };
    const timer = setTimeout(failed, 15000);
    s.onload = () => { clearTimeout(timer); if (window.lottie) resolve(window.lottie); else failed(); };
    s.onerror = failed;
    document.head.append(s);
  });
  return lottieLib;
}


// ── загрузка шаблонов (сервер отдаёт .tgs как JSON с Content-Encoding: gzip)
const templates = new Map();
export function loadTemplate(id) {
  if (!templates.has(id)) {
    // версия в адресе: файлы шаблонов кэшируются на сутки, после перекраски их нужно скачать заново
    const p = fetch(`/api/templates/${encodeURIComponent(id)}.tgs?v=${PREVIEW_VERSION}`).then((r) => {
      if (!r.ok) throw new Error(`template ${r.status}`);
      return r.json();
    });
    p.catch(() => templates.delete(id));
    templates.set(id, p);
    if (templates.size > 400) templates.delete(templates.keys().next().value);
  }
  return templates.get(id);
}

// ── поиск надписи-заглушки
const isLetter = (it) => it && it.ty === 'gr' && (it.it || []).some((i) => i.ty === 'fl' && i.nm === SLOT);

function wordGroups(anim) {
  const found = [];
  const visit = (o) => {
    if (Array.isArray(o)) { o.forEach(visit); return; }
    if (!o || typeof o !== 'object') return;
    if (o.ty === 'gr' && Array.isArray(o.it) && o.it.some(isLetter)) { found.push(o); return; }
    for (const k in o) if (o[k] && typeof o[k] === 'object') visit(o[k]);
  };
  visit(anim);
  return found;
}

function slotColor(groups) {
  for (const g of groups) {
    for (const letter of g.it) {
      if (!isLetter(letter)) continue;
      const fl = letter.it.find((i) => i.ty === 'fl' && i.nm === SLOT && !i.c.a);
      if (fl) return fl.c.k.slice(0, 3);
    }
  }
  return null;
}

function staticValue(prop, def) {
  if (!prop || typeof prop !== 'object') return def;
  let k = prop.k === undefined ? def : prop.k;
  if (prop.a) {
    if (Array.isArray(k)) k = k[0] && typeof k[0] === 'object' && k[0].s !== undefined ? k[0].s : def;
    else if (!k || typeof k !== 'object') k = def; // бывает «a: 1» с одним значением-фигурой вместо кадров
  }
  return k;
}

function matrix(tr) {
  if (!tr) return (x, y) => [x, y];
  const [ax, ay] = [...staticValue(tr.a, [0, 0]), 0, 0];
  const [px, py] = [...staticValue(tr.p, [0, 0]), 0, 0];
  const [sx, sy] = [...staticValue(tr.s, [100, 100]), 100, 100];
  const r = ((staticValue(tr.r, 0) || 0) * Math.PI) / 180;
  const cos = Math.cos(r), sin = Math.sin(r);
  return (x, y) => {
    const X = ((x - ax) * sx) / 100, Y = ((y - ay) * sy) / 100;
    return [X * cos - Y * sin + px, X * sin + Y * cos + py];
  };
}

function lettersBox(group) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const letter of group.it) {
    if (!isLetter(letter)) continue;
    const f = matrix(letter.it.find((i) => i.ty === 'tr'));
    for (const i of letter.it) {
      if (i.ty !== 'sh') continue;
      let shape = staticValue(i.ks, null);
      if (Array.isArray(shape)) shape = shape[0];
      if (!shape || !shape.v) continue;
      for (const v of shape.v) {
        const [x, y] = f(v[0], v[1]);
        x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
      }
    }
  }
  return x0 === Infinity ? null : [x0, y0, x1, y1];
}

// ── векторный рисунок (ник или логотип) → группы Lottie
const r2 = (n) => Math.round(n * 100) / 100;
const fill = (rgb) => ({ ty: 'fl', nm: SLOT, c: { a: 0, k: [rgb[0], rgb[1], rgb[2], 1] }, o: { a: 0, k: 100 }, r: 1, bm: 0 });
const transform = () => ({ ty: 'tr', p: { a: 0, k: [0, 0] }, a: { a: 0, k: [0, 0] }, s: { a: 0, k: [100, 100] },
  r: { a: 0, k: 0 }, o: { a: 0, k: 100 }, sk: { a: 0, k: 0 }, sa: { a: 0, k: 0 } });

// ── градиент на ник, логотип и/или заливку — порт градиента из app/lottie.py (clean_grad, _grad_*)
const GRAD_NAME = 'ezz grad';
const GRAD_TARGETS = ['text', 'logo', 'fill'];
const HEX6 = /^#[0-9a-f]{6}$/i;

/** Градиент из настроек {a, b, angle, on} → чистое значение или null (выключен), как clean_grad. */
export function cleanGrad(g) {
  if (!g || typeof g !== 'object' || !HEX6.test(g.a) || !HEX6.test(g.b) || !Array.isArray(g.on)) return null;
  const on = GRAD_TARGETS.filter((t) => g.on.includes(t));
  if (!on.length) return null;
  const angle = Number.isFinite(Number(g.angle)) ? ((Math.round(Number(g.angle)) % 360) + 360) % 360 : 180;
  return { a: g.a.toLowerCase(), b: g.b.toLowerCase(), angle, on };
}

function gradPoints([x0, y0, x1, y1], angle) {
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, hw = (x1 - x0) / 2, hh = (y1 - y0) / 2;
  const rad = (angle * Math.PI) / 180;
  const dx = Math.sin(rad), dy = -Math.cos(rad);
  const half = Math.abs(hw * dx) + Math.abs(hh * dy) || 1;
  return [[cx - dx * half, cy - dy * half], [cx + dx * half, cy + dy * half]];
}

const r3 = (n) => Math.round(n * 1000) / 1000;
function gradPaint(a, b, s, e) {
  return { ty: 'gf', nm: GRAD_NAME, o: { a: 0, k: 100 }, r: 1, bm: 0, t: 1,
    s: { a: 0, k: [r2(s[0]), r2(s[1])] }, e: { a: 0, k: [r2(e[0]), r2(e[1])] },
    g: { p: 2, k: { a: 0, k: [0, ...a.slice(0, 3).map(r3), 1, ...b.slice(0, 3).map(r3)] } } };
}

function shade(rgb, t) {
  const [k, to] = t >= 0.5 ? [Math.min(0.8, (t - 0.5) * 1.6), 1] : [Math.min(0.6, (0.5 - t) * 1.2), 0];
  return rgb.slice(0, 3).map((c) => c + (to - c) * k);
}

function gradFill(grad, box, tone = null) {
  let a = hexToRgb(grad.a), b = hexToRgb(grad.b);
  if (tone != null) { a = shade(a, tone); b = shade(b, tone); }
  const [s, e] = gradPoints(box, grad.angle);
  return gradPaint(a, b, s, e);
}

const luma = (rgb) => 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2];

// тон цветных слоёв логотипа: яркость, растянутая на 0…1 среди слоёв (один тон — 0.5), как _logo_shades
function logoShades(drawing) {
  const lum = new Map();
  drawing.layers.forEach((layer, n) => { if (layer.color) lum.set(n, luma(layer.color.map((c) => c / 255))); });
  if (!lum.size) return lum;
  const vals = [...lum.values()];
  const lo = Math.min(...vals), hi = Math.max(...vals);
  lum.forEach((v, n) => lum.set(n, hi - lo < 0.08 ? 0.5 : (v - lo) / (hi - lo)));
  return lum;
}

// точка из пространства родителя в локальное пространство группы (обратное matrix)
function inversePoint(tr, x, y) {
  if (!tr) return [x, y];
  const [ax, ay] = [...staticValue(tr.a, [0, 0]), 0, 0];
  const [px, py] = [...staticValue(tr.p, [0, 0]), 0, 0];
  const [sx, sy] = [...staticValue(tr.s, [100, 100]), 100, 100];
  if (!sx || !sy) return null;
  const r = ((staticValue(tr.r, 0) || 0) * Math.PI) / 180;
  const cos = Math.cos(r), sin = Math.sin(r);
  const X = x - px, Y = y - py;
  return [((X * cos + Y * sin) * 100) / sx + ax, ((-X * sin + Y * cos) * 100) / sy + ay];
}

// родные буквы «ezzprod»: один градиент на слово, пересчитанный в пространство каждой буквы
function gradLetters(group, grad) {
  const box = lettersBox(group);
  if (!box) return;
  const [s, e] = gradPoints(box, grad.angle);
  const a = hexToRgb(grad.a), b = hexToRgb(grad.b);
  group.it.filter(isLetter).forEach((letter) => {
    const tr = letter.it.find((i) => i.ty === 'tr');
    const ls = inversePoint(tr, s[0], s[1]), le = inversePoint(tr, e[0], e[1]);
    if (!ls || !le) return;
    const n = letter.it.findIndex((i) => i.ty === 'fl' && i.nm === SLOT);
    hide(letter.it[n]);
    letter.it.splice(n, 0, gradPaint(a, b, ls, le));
  });
}

// сплошная заливка под градиентом — прозрачная, иначе её край светится каймой (как _hide в app/lottie.py)
const hide = (paint) => { paint.o = { a: 0, k: 0 }; };

function points(items, f, out) {
  for (const i of items) {
    if (!i || typeof i !== 'object') continue;
    if (i.ty === 'gr') {
      const sub = i.it || [];
      const m = matrix(sub.find((j) => j && j.ty === 'tr'));
      points(sub.filter((j) => !(j && j.ty === 'tr')), (x, y) => f(...m(x, y)), out);
    } else if (i.ty === 'sh') {
      let shape = staticValue(i.ks, null);
      if (Array.isArray(shape)) shape = shape[0];
      ((shape && shape.v) || []).forEach((v) => out.push(f(v[0], v[1])));
    }
  }
  return out;
}

// основная заливка стикера (цвет src) → градиент по габаритам каждой фигуры этого цвета
function gradBody(anim, src, grad) {
  const visit = (o) => {
    if (Array.isArray(o)) { o.forEach(visit); return; }
    if (!o || typeof o !== 'object') return;
    const items = o.ty === 'gr' ? o.it : (o.ty === 4 ? o.shapes : null);
    if (Array.isArray(items) && !items.some(isLetter)) {
      items.forEach((i, n) => {
        if (!i || i.ty !== 'fl' || i.nm === SLOT) return;
        const rgb = solid(i);
        if (!rgb || !near(rgb, src)) return;
        const pts = points(items, (x, y) => [x, y], []);
        if (!pts.length) return;
        let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
        pts.forEach(([x, y]) => { bx0 = Math.min(bx0, x); by0 = Math.min(by0, y); bx1 = Math.max(bx1, x); by1 = Math.max(by1, y); });
        const paint = gradFill(grad, [bx0, by0, bx1, by1]);
        if (i.o) paint.o = i.o;
        items[n] = paint;
      });
    }
    for (const k in o) if (o[k] && typeof o[k] === 'object') visit(o[k]);
  };
  visit(anim);
}

function placeDrawing(drawing, box, textRgb, grad = null) {
  const [x0, y0, x1, y1] = drawing.bbox;
  const bw = Math.max(x1 - x0, 1e-6), bh = Math.max(y1 - y0, 1e-6);
  const [tx0, ty0, tx1, ty1] = box;
  const k = Math.min((tx1 - tx0) / bw, (ty1 - ty0) / bh);
  const ox = (tx0 + tx1) / 2 - (x0 + bw / 2) * k;
  const oy = (ty0 + ty1) / 2 - (y0 + bh / 2) * k;
  const fitted = [x0 * k + ox, y0 * k + oy, x1 * k + ox, y1 * k + oy]; // где рисунок оказался: по нему идёт градиент
  const on = grad ? grad.on : [];
  const tones = on.includes('logo') ? logoShades(drawing) : new Map();
  const groups = drawing.layers.map((layer, n) => {
    const rgb = layer.color ? layer.color.map((c) => c / 255) : textRgb;
    const items = layer.paths.map((p) => ({ ty: 'sh', ks: { a: 0, k: {
      v: p.v.map(([x, y]) => [r2(x * k + ox), r2(y * k + oy)]),
      i: p.i.map(([a, b]) => [r2(a * k), r2(b * k)]),
      o: p.o.map(([a, b]) => [r2(a * k), r2(b * k)]),
      c: true } } }));
    const paints = [fill(rgb)];
    if (layer.color && on.includes('logo')) paints.unshift(gradFill(grad, fitted, tones.has(n) ? tones.get(n) : 0.5));
    else if (!layer.color && on.includes('text')) paints.unshift(gradFill(grad, fitted)); // поверх заливки ника
    if (paints.length > 1) hide(paints[1]);
    // как в app/lottie.py: ник — «ezz nick», цветной слой логотипа — «ezz layer»
    return { ty: 'gr', nm: layer.color ? 'ezz layer' : 'ezz nick', it: [...items, ...paints, transform()] };
  });
  return groups.reverse(); // в Lottie первый элемент группы рисуется поверх остальных
}

/** «Размер надписи» в процентах (100 — автоподбор) → множитель, как text_size в app/lottie.py. */
export function textSize(value) {
  const k = Number.isFinite(Number(value)) && value != null ? Number(value) / 100 : 1;
  return Math.max(SIZE_RANGE[0], Math.min(SIZE_RANGE[1], k));
}

/** «Положение надписи» {x, y, r} → чистое значение или null (как clean_place в app/lottie.py). */
export function cleanPlace(p) {
  if (!p || typeof p !== 'object') return null;
  const out = {};
  for (const [k, lim] of Object.entries(PLACE_RANGE)) {
    const v = Number(p[k]);
    out[k] = Number.isFinite(v) ? Math.max(-lim, Math.min(lim, Math.round(v))) : 0;
  }
  return out.x || out.y || out.r ? out : null;
}

// надпись со сдвигом (доли ширины места надписи), масштабом и поворотом вокруг центра — одной группой, как _moved в app/lottie.py
function moved(items, box, place, size = 1) {
  const hasPlace = place && (place.x || place.y || place.r);
  const hasSize = size != null && Math.abs(size - 1) > 1e-4;
  if ((!hasPlace && !hasSize) || !items.length) return items;
  const [x0, y0, x1, y1] = box;
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, w = x1 - x0;
  const tr = transform();
  tr.a.k = [r2(cx), r2(cy)];
  tr.p.k = [r2(cx + (w * ((place && place.x) || 0)) / 100), r2(cy + (w * ((place && place.y) || 0)) / 100)];
  tr.r.k = (place && place.r) || 0;
  tr.s.k = [r2(100 * (size || 1)), r2(100 * (size || 1))];
  return [{ ty: 'gr', nm: 'ezz text', it: [...items, tr] }];
}

function layout(box, text, logo, size = 1) {
  const [x0, y0, x1, y1] = box;
  const w = x1 - x0, hh = y1 - y0, cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  if (logo && !text) {
    const lh = Math.min(w, hh * LOGO_HEIGHT);
    return [[logo, [x0, cy - lh / 2, x1, cy + lh / 2]]];
  }
  if (text && !logo) {
    const hw = (w * TEXT_FIT_W) / 2, hhh = (hh * TEXT_FIT_H) / 2;
    return [[text, [cx - hw, cy - hhh, cx + hw, cy + hhh]]];
  }
  const side = Math.min(hh * LOGO_HEIGHT * 0.8, w * 0.42);
  const tb = text.bbox;
  const aspect = (tb[2] - tb[0]) / Math.max(tb[3] - tb[1], 1e-6);
  const tw = Math.min(w - side - hh * GAP, hh * aspect);
  const left = cx - (side + hh * GAP + tw) / 2;
  return [
    [logo, [left, cy - side / 2, left + side, cy + side / 2]],
    [text, shrink([left + side + hh * GAP, y0, left + side + hh * GAP + tw, y1], TEXT_WITH_LOGO)],
  ];
}

// ── основные цвета шаблона: «Заливка» — сплошной цвет наибольшей площади, «Обводка» — контуры (по толщине)
function solid(item) {
  const c = item && item.c;
  if (!c || typeof c !== 'object' || c.a) return null;
  const k = c.k;
  if (!Array.isArray(k) || k.length < 3 || !k.slice(0, 3).every((x) => typeof x === 'number')) return null;
  return k.slice(0, 3);
}

function area(items) {
  let total = 0;
  for (const i of items) {
    if (!i || typeof i !== 'object') continue;
    if (i.ty === 'gr') { total += area(i.it || []); continue; }
    if (i.ty !== 'sh') continue;
    let shape = staticValue(i.ks, null);
    if (Array.isArray(shape)) shape = shape[0];
    const pts = shape && shape.v;
    if (!pts || pts.length < 3) continue;
    // площадь многоугольника по вершинам: тонкие штрихи почти ничего не весят
    let sum = 0;
    for (let n = 0; n < pts.length; n += 1) {
      const p = pts[n], q = pts[(n + pts.length - 1) % pts.length];
      sum += p[0] * q[1] - q[0] * p[1];
    }
    total += Math.abs(sum) / 2;
  }
  return total;
}

const colorKey = (rgb) => rgb.map((x) => Math.round(x * 100)).join(',');

export function mainColors(anim) {
  const fills = new Map();
  const strokes = new Map();
  const add = (map, rgb, w) => {
    const key = colorKey(rgb);
    const e = map.get(key) || { rgb, w: 0 };
    e.w += w;
    map.set(key, e);
  };
  const visit = (o) => {
    if (Array.isArray(o)) { o.forEach(visit); return; }
    if (!o || typeof o !== 'object') return;
    const items = o.ty === 'gr' ? o.it : (o.ty === 4 ? o.shapes : null);
    if (Array.isArray(items)) {
      const paints = items.filter((i) => i && (i.ty === 'fl' || i.ty === 'st') && i.nm !== SLOT && solid(i));
      const top = paints.find((i) => i.ty === 'fl'); // видна только верхняя заливка группы
      if (top) add(fills, solid(top), area(items));
      paints.filter((i) => i.ty === 'st').forEach((i) => {
        const w = staticValue(i.w, 1);
        add(strokes, solid(i), Math.max(typeof w === 'number' ? w : 1, 0.5));
      });
    }
    for (const k in o) if (o[k] && typeof o[k] === 'object') visit(o[k]);
  };
  visit(anim);
  const pick = (map) => [...map.values()].reduce((a, b) => (b.w > (a ? a.w : -1) ? b : a), null);
  const f = pick(fills), st = pick(strokes);
  return [f ? f.rgb : null, st ? st.rgb : null];
}

const near = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) <= TOL;

function recolor(anim, kinds, src, dst) {
  const visit = (o) => {
    if (Array.isArray(o)) { o.forEach(visit); return; }
    if (!o || typeof o !== 'object') return;
    if (kinds.includes(o.ty) && o.nm !== SLOT) {
      const rgb = solid(o);
      if (rgb && near(rgb, src)) o.c = { a: 0, k: [dst[0], dst[1], dst[2], o.c.k.length > 3 ? o.c.k[3] : 1] };
    }
    for (const k in o) if (o[k] && typeof o[k] === 'object') visit(o[k]);
  };
  visit(anim);
}

/** Копия анимации: основной цвет стикера (color — «Заливка», null — родной), контуры и надпись
 *  (stroke — «Обводка», null — родные), ник и/или логотип вместо «ezzprod». */
export function personalize(anim, { color = null, stroke = null, text = null, logo = null, size = 1, place = null, grad = null } = {}) {
  const out = structuredClone(anim);
  const on = grad ? grad.on : [];
  if (color || stroke || on.includes('fill')) {
    const [mainFill, mainStroke] = mainColors(out);
    if (color && mainFill) recolor(out, ['fl'], mainFill, color);
    // «Обводка» — контуры и всё того же цвета, нарисованное заливкой (буквы «TOP», руки, блики)
    if (stroke && mainStroke) recolor(out, ['st', 'fl'], mainStroke, stroke);
    if (on.includes('fill') && mainFill) gradBody(out, color || mainFill, grad);
  }
  const groups = wordGroups(out);
  const textRgb = stroke || slotColor(groups) || [1, 1, 1]; // надпись красится вместе с обводкой
  for (const group of groups) {
    if (!text && !logo) {
      if (stroke) {
        group.it.filter(isLetter).forEach((letter) => letter.it.forEach((i) => {
          if (i.ty === 'fl' && i.nm === SLOT) i.c = { a: 0, k: [...textRgb, 1] };
        }));
      }
      if (on.includes('text')) gradLetters(group, grad);
      const hasTransform = (place && (place.x || place.y || place.r)) || (size != null && Math.abs(size - 1) > 1e-4);
      if (!hasTransform) continue;
      const box = lettersBox(group);
      if (!box) continue;
      const drawn = moved(group.it.filter(isLetter), box, place, size);
      const rest = group.it.filter((i) => !isLetter(i));
      group.it = [...rest.filter((i) => i.ty !== 'tr'), ...drawn, ...rest.filter((i) => i.ty === 'tr')];
      continue;
    }
    const box = lettersBox(group);
    if (!box) continue;
    const drawn = moved(layout(box, text, logo).flatMap(([d, a]) => placeDrawing(d, a, textRgb, grad)), box, place, size);
    const rest = group.it.filter((i) => !isLetter(i));
    group.it = [...rest.filter((i) => i.ty !== 'tr'), ...drawn, ...rest.filter((i) => i.ty === 'tr')];
  }
  return out;
}

export const hexToRgb = (hex) => (hex ? [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) : null);

// ── ник как векторный рисунок (контуры букв считает сервер тем же шрифтом, что и сборщик)
const glyphCache = new Map();
export function textDrawing(text, fontId = null) {
  const t = (text || '').trim();
  if (!t) return Promise.resolve(null);
  const key = `${fontId || ''}|${t}`;
  if (!glyphCache.has(key)) {
    const p = api.glyphs(t, fontId).then((r) => r.drawing || null);
    p.catch(() => glyphCache.delete(key));
    glyphCache.set(key, p);
    if (glyphCache.size > 60) glyphCache.delete(glyphCache.keys().next().value);
  }
  return glyphCache.get(key);
}

/** Текущие настройки пользователя → параметры personalize (own: true — цвет из выбора пользователя). */
export async function currentLook({ own = true } = {}) {
  return lookFromParams(state, own);
}

/** Превью конкретного стикера: его собственные настройки поверх общих (store.lookFor). */
export function lookOf(id) {
  return lookFromParams(lookFor(id), true);
}

export async function lookFromParams(p, own) {
  const fontId = p.font ? p.font.id : null;
  // шрифт мог быть удалён (свой) — тогда ник в превью рисуем стандартным, а не пропускаем
  const text = await textDrawing(p.text, fontId).catch(() => (fontId ? textDrawing(p.text, null) : null)).catch(() => null);
  const logo = p.logo && p.logo.vector ? p.logo.vector : null;
  return {
    text, logo, color: own ? hexToRgb(p.color) : null, stroke: own ? hexToRgb(p.stroke) : null, size: textSize(p.size),
    place: cleanPlace(p.place),
    grad: own ? cleanGrad(p.grad) : null,
    // у стикера с правками редактора превью рисует сервер (тем же кодом, что собирает пак)
    remote: own && p.edit ? renderParams(p) : null,
  };
}

/** Параметры POST /api/render из настроек стикера (store.lookFor). */
export function renderParams(p) {
  return {
    text: (p.text || '').trim(), logo_id: p.logo ? p.logo.id : null, color: p.color || null, stroke: p.stroke || null,
    size: p.size == null ? 100 : p.size, place: cleanPlace(p.place), grad: cleanGrad(p.grad), font_id: p.font ? p.font.id : null, edit: p.edit || null,
  };
}

// ── превью с сервера: кэш по параметрам, чтобы лента и витрина не просили одно и то же дважды
const rendered = new Map();
export function renderRemote(id, params) {
  const key = `${id}|${JSON.stringify(params)}`;
  if (!rendered.has(key)) {
    const p = api.render({ template_id: id, ...params });
    p.catch(() => rendered.delete(key));
    rendered.set(key, p);
    if (rendered.size > 24) rendered.delete(rendered.keys().next().value);
  }
  return rendered.get(key);
}

export const isPersonalized = () => Boolean((state.text || '').trim() || (state.logo && state.logo.vector) || state.color || state.stroke
  || cleanGrad(state.grad));

// ── проигрыватель: элемент с картинкой-заглушкой, поверх которой появляется анимация
const players = new Set();
setInterval(() => {
  players.forEach((p) => { if (!p.box.isConnected) { p.destroy(); } });
}, 5000);

/**
 * Превью шаблона: сначала статичная картинка, поверх неё — анимация, когда загрузятся lottie и файл.
 * look — параметры personalize или null (родной шаблон). lazy — анимацию строим только когда элемент
 * впервые показан (play(true) от watchVisibility). Возвращает { el, update(look), play(on), destroy }.
 */
export function TgsPreview(id, { look = null, autoplay = true, lazy = false, cls = '' } = {}) {
  const img = h('img', { class: 'tgs__thumb', src: previewUrl(id), alt: '', draggable: false, decoding: 'async', loading: 'lazy' });
  img.addEventListener('error', () => img.classList.add('is-broken'));
  const box = h('span', { class: `tgs ${cls}`.trim(), 'aria-hidden': 'true' }, img);
  let anim = null;
  let version = 0;
  let wanted = look;
  let built = false;
  let playing = autoplay && !lazy;
  const self = {
    el: box,
    box,
    async update(nextLook) {
      wanted = nextLook;
      if (lazy && !playing && !built) return; // ещё не показывали — построим при появлении на экране
      built = true;
      const mine = ++version;
      try {
        const remote = nextLook && nextLook.remote;
        const [lib, data] = await Promise.all([loadLottie(), remote ? renderRemote(id, remote).then((r) => r.anim) : loadTemplate(id)]);
        if (mine !== version) return;
        const animationData = remote ? structuredClone(data) : (nextLook ? personalize(data, nextLook) : structuredClone(data));
        const frame = anim ? anim.currentFrame : 0;
        if (anim) anim.destroy();
        const holder = h('span', { class: 'tgs__anim' });
        box.replaceChildren(holder);
        anim = lib.loadAnimation({ container: holder, renderer: 'svg', loop: true, autoplay: false, animationData,
          rendererSettings: { preserveAspectRatio: 'xMidYMid meet' } });
        anim.goToAndStop(frame || 0, true);
        if (playing && !prefersReducedMotion()) anim.play();
        box.classList.add('is-live');
      } catch {
        built = false; // нет сети или lottie не загрузился — остаётся статичная картинка, попробуем при следующем показе
      }
    },
    play(on) {
      playing = on;
      if (on && !built) { self.update(wanted); return; }
      if (!anim) return;
      if (on && !prefersReducedMotion()) anim.play(); else anim.pause();
    },
    destroy() {
      version += 1;
      if (anim) { anim.destroy(); anim = null; }
      players.delete(self);
    },
  };
  players.add(self);
  if (!lazy) self.update(look);
  return self;
}

// ── видимость: проигрываем только то, что на экране
export function watchVisibility(root) {
  const map = new WeakMap();
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      const p = map.get(e.target);
      if (p) p.play(e.isIntersecting);
    });
  }, { root, rootMargin: '80px 0px' });
  return {
    add(player) { map.set(player.el, player); io.observe(player.el); },
    disconnect() { io.disconnect(); },
  };
}
