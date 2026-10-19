// «Продвинутые настройки» (Pro): редактор одного стикера — части эмодзи (цвет, сдвиг, масштаб, поворот, прозрачность,
// анимация детали), все цвета и их варианты, эффекты (пикселизация, блеск металла, глитч, голограмма), эффект при
// показе и проверка под требования Telegram. Превью рисует сервер (POST /api/render) тем же кодом, что собирает пак,
// поэтому в редакторе ровно то, что окажется в Telegram. Правки сохраняются у стикера при выходе из редактора.
import { api, ApiError } from '../api.js';
import { BigEmoji, Button, Quote } from '../components.js';
import { openOverlay } from '../nav.js';
import { editFor, lookFor, setItemEdit, state } from '../store.js';
import { haptic } from '../tg.js';
import { loadLottie, renderParams } from '../tgs.js';
import { h, icon, plural, prefersReducedMotion, replaceChildren, toast } from '../ui.js';
import { openBuy } from './buy.js';

// настройки эффектов по умолчанию — как FX в app/editor.py
const FX = {
  pixel: { on: false, grid: 24, shape: 'square', fps: 12, colors: 48, lines: 80, gap: 14 },
  metal: { on: false, width: 100 },
  glitch: { on: false, count: 6, len: 100, shift: 100, stripes: 8, dashes: 18, ghost: true, c1: '#ff1f4b', c2: '#18e0ff', seed: 1 },
  holo: { on: false, only: false, parts: [], palette: 'red', dir: 'cw', turns: 1, width: 100, bright: 88, gloss: true,
    target: 'contour', dark: true, tint: null, edge: 10 },
  burst: { kind: 'none', count: 10, color: null },
};
const ANIMS = [
  ['none', 'нет'], ['appear', 'появление'], ['jump', 'прыжок'], ['pulse', 'пульс'], ['swing', 'качание'],
  ['shake', 'тряска'], ['float', 'парение'], ['spin', 'вращение'], ['heart', 'сердце'],
];
const PALETTES = [
  ['red', '#ff2d55'], ['blue', '#2fa8ff'], ['purple', '#9b5cff'], ['yellow', '#ffc23d'], ['green', '#2fe0a5'],
  ['pink', '#ff5ccf'], ['rainbow', null],
];
// эффект при показе: id, подпись, цвет подсветки плитки
const BURSTS = [['none', 'нет', '#ffffff'], ['sparks', 'искры', '#ffd23f'], ['hearts', 'сердечки', '#ff3d6e'],
  ['confetti', 'конфетти', '#4dc3ff'], ['stars', 'звёзды', '#ffc23d'], ['flash', 'вспышка', '#ffffff']];
// цвет частиц: null — родные цвета эффекта (как BURST_COLORS / CONFETTI в app/editor.py)
const BURST_COLORS = [null, '#ffffff', '#ffd23f', '#ff3d6e', '#9b5cff', '#2fa8ff', '#2fe0a5'];

// иконки эффектов (viewBox 32×32): рисуют то, что появится вокруг эмодзи
const SVG_NS = 'http://www.w3.org/2000/svg';
function svg(children) {
  const el = document.createElementNS(SVG_NS, 'svg');
  el.setAttribute('viewBox', '0 0 32 32');
  el.setAttribute('aria-hidden', 'true');
  el.classList.add('ed-fxtile__art');
  children.forEach(([tag, attrs]) => {
    const n = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
    el.append(n);
  });
  return el;
}
const star4 = (cx, cy, r) => `M${cx} ${cy - r}L${cx + r * 0.24} ${cy - r * 0.24}L${cx + r} ${cy}L${cx + r * 0.24} ${cy + r * 0.24}`
  + `L${cx} ${cy + r}L${cx - r * 0.24} ${cy + r * 0.24}L${cx - r} ${cy}L${cx - r * 0.24} ${cy - r * 0.24}Z`;
const star5 = (cx, cy, r) => `${Array.from({ length: 10 }, (_, j) => {
  const rr = j % 2 ? r * 0.45 : r;
  const a = (Math.PI * j) / 5;
  return `${j ? 'L' : 'M'}${(cx + rr * Math.sin(a)).toFixed(2)} ${(cy - rr * Math.cos(a)).toFixed(2)}`;
}).join('')}Z`;
const HEART = 'M16 27.5c-.4 0-.8-.1-1.1-.4C8.6 21.8 4 17.6 4 12.4 4 8.8 6.8 6 10.3 6c2.2 0 4.3 1.1 5.7 2.9C17.4 7.1 19.5 6 21.7 6 25.2 6 28 8.8 28 12.4c0 5.2-4.6 9.4-10.9 14.7-.3.3-.7.4-1.1.4z';
function burstArt(kind, color) {
  const c = (own) => color || own;
  switch (kind) {
    case 'sparks': return svg([['path', { d: star4(14, 17, 11), fill: c('#ffd23f') }], ['path', { d: star4(25, 7, 4.5), fill: c('#ffe78a') }]]);
    case 'hearts': return svg([['path', { d: HEART, fill: c('#ff3d6e') }], ['circle', { cx: 11, cy: 11, r: 2.2, fill: '#fff', opacity: 0.55 }]]);
    case 'confetti': return svg([
      ['rect', { x: 5, y: 7, width: 8, height: 4, rx: 1, fill: c('#ff4d6d'), transform: 'rotate(-24 9 9)' }],
      ['rect', { x: 18, y: 4, width: 8, height: 4, rx: 1, fill: c('#ffd23f'), transform: 'rotate(30 22 6)' }],
      ['rect', { x: 12, y: 15, width: 8, height: 4, rx: 1, fill: c('#4dc3ff'), transform: 'rotate(12 16 17)' }],
      ['rect', { x: 4, y: 22, width: 8, height: 4, rx: 1, fill: c('#4dffa6'), transform: 'rotate(40 8 24)' }],
      ['rect', { x: 20, y: 22, width: 8, height: 4, rx: 1, fill: c('#b06bff'), transform: 'rotate(-35 24 24)' }]]);
    case 'stars': return svg([['path', { d: star5(14, 17, 11), fill: c('#ffc23d') }], ['path', { d: star5(26, 7, 4.5), fill: c('#ffe08a') }]]);
    case 'flash': return svg([
      ['circle', { cx: 16, cy: 16, r: 8, fill: 'none', stroke: c('#ffffff'), 'stroke-width': 3 }],
      ['circle', { cx: 16, cy: 16, r: 13.5, fill: 'none', stroke: c('#ffffff'), 'stroke-width': 1.5, opacity: 0.4 }],
      ['path', { d: star4(16, 16, 4), fill: c('#ffffff') }]]);
    default: return svg([
      ['circle', { cx: 16, cy: 16, r: 10, fill: 'none', stroke: 'currentColor', 'stroke-width': 2.2 }],
      ['path', { d: 'M9 23L23 9', stroke: 'currentColor', 'stroke-width': 2.2, 'stroke-linecap': 'round' }]]);
  }
}
const KIND_ICON = { text: 'T', circle: '●', square: '■', rect: '▬', line: '╱', shape: '◆' };

const clone = (o) => JSON.parse(JSON.stringify(o));
const places = (n) => `${n} ${plural(n, 'место', 'места', 'мест')}`;

/** Только то, что действительно меняет стикер (как clean_edit на сервере). */
function compact(edit) {
  const out = {};
  const parts = {};
  Object.entries(edit.parts || {}).forEach(([id, p]) => {
    const q = {};
    if (p.x) q.x = p.x;
    if (p.y) q.y = p.y;
    if (p.s != null && p.s !== 100) q.s = p.s;
    if (p.r) q.r = p.r;
    if (p.o != null && p.o !== 100) q.o = p.o;
    if (p.fill) q.fill = p.fill;
    if (p.anim && p.anim !== 'none') q.anim = p.anim;
    if (Object.keys(q).length) parts[id] = q;
  });
  if (Object.keys(parts).length) out.parts = parts;
  const colors = Object.fromEntries(Object.entries(edit.colors || {}).filter(([k, v]) => v && v !== k));
  if (Object.keys(colors).length) out.colors = colors;
  const fx = {};
  Object.entries(edit.fx || {}).forEach(([name, cfg]) => {
    if (cfg.on || (name === 'burst' && cfg.kind !== 'none')) fx[name] = cfg;
  });
  if (Object.keys(fx).length) out.fx = fx;
  return Object.keys(out).length ? out : null;
}

// ── цвета: оттенок по кругу для «вариантов цветов»
function hexToHsl(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
  if (mx === mn) return [0, 0, l];
  const d = mx - mn;
  const s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
  let hue = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
  hue /= 6;
  return [hue, s, l];
}
function hslToHex([hue, s, l]) {
  const f = (n) => {
    const k = (n + hue * 12) % 12;
    const a = s * Math.min(l, 1 - l);
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)))).toString(16).padStart(2, '0');
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}
const rotateHue = (hex, turn) => {
  const [hu, s, l] = hexToHsl(hex);
  return s < 0.12 ? hex : hslToHex([(hu + turn + 1) % 1, s, l]);
};

// ── мелкие элементы управления
function Toggle(on, onChange, label) {
  const el = h('button', { class: `ed-toggle${on ? ' is-on' : ''}`, type: 'button', role: 'switch', 'aria-checked': String(on), 'aria-label': label,
    onclick: () => { haptic.select(); set(!el.classList.contains('is-on'), true); } }, h('i'));
  function set(v, fire) {
    el.classList.toggle('is-on', v);
    el.setAttribute('aria-checked', String(v));
    if (fire) onChange(v);
  }
  return { el, set };
}

function Slider({ label, min, max, step = 1, value, fmt = (v) => String(v), onInput }) {
  const out = h('span', { class: 'ed-slider__value' }, fmt(value));
  const input = h('input', { class: 'ed-slider__input', type: 'range', min, max, step, value, 'aria-label': label });
  input.addEventListener('input', () => { out.textContent = fmt(Number(input.value)); onInput(Number(input.value)); });
  return h('label', { class: 'ed-slider' }, h('span', { class: 'ed-slider__label' }, label), input, out);
}

function Seg(options, value, onChange) {
  const buttons = options.map(([id, label]) => h('button', {
    class: `ed-seg__btn${id === value ? ' is-active' : ''}`, type: 'button', dataset: { id },
    onclick: () => {
      haptic.select();
      buttons.forEach((b) => b.classList.toggle('is-active', b.dataset.id === id));
      onChange(id);
    },
  }, label));
  return h('div', { class: `ed-seg ed-seg--${options.length}` }, ...buttons);
}

function NumberBox(value, onChange, { min = -9999, max = 9999, label }) {
  const input = h('input', { class: 'ed-num', type: 'text', inputMode: 'decimal', value: fmtNum(value), 'aria-label': label });
  input.spellcheck = false;
  const commit = () => {
    const v = parseFloat(input.value.replace(',', '.'));
    const n = Number.isFinite(v) ? Math.max(min, Math.min(max, Math.round(v * 10) / 10)) : value;
    input.value = fmtNum(n);
    onChange(n);
  };
  input.addEventListener('change', commit);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') input.blur(); });
  return input;
}
function fmtNum(v) { return String(Math.round((v || 0) * 10) / 10).replace('.', ','); }

const Section = (title, ...children) => [h('h3', { class: 'ed-title' }, title), ...children];

/** Вход из «Оформления»: в Pro — редактор стикера id, иначе экран про Pro. */
export function openAdvanced(id, onDone) {
  if (!state.me || state.me.plan !== 'pro') { openProLock(); return; }
  openEditor(id, onDone);
}

function openProLock() {
  const me = state.me;
  const left = Math.max(1, me.pro_min - me.purchased_total);
  openOverlay({
    kind: 'modal',
    label: 'продвинутые настройки',
    className: 'modal--styles',
    render: ({ close }) => [
      h('div', { class: 'locked' },
        BigEmoji('lock', 'locked__icon'),
        h('p', { class: 'locked__title' }, 'Продвинутые настройки — в Pro'),
        Quote({ emoji: 'lock', title: 'Редактор', text: 'меняй каждую деталь эмодзи: цвет, место, размер и анимацию; включай пикселизацию, блеск металла, глитч и голограмму.' }),
        h('div', { class: 'meter', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': me.pro_min, 'aria-valuenow': me.purchased_total },
          h('i', { style: { width: `${Math.min(100, Math.round((me.purchased_total / me.pro_min) * 100))}%` } })),
        h('p', { class: 'locked__text' }, `Pro открывается после покупки ${me.pro_min} генераций: осталось ${left}`)),
      Button({ label: 'Купить генерации', onClick: () => { close(); openBuy({ qty: left }); } }),
      Button({ label: 'Закрыть', variant: 'ghost', onClick: close }),
    ],
  });
}

function openEditor(id, onDone) {
  let cleanup = () => {};
  openOverlay({
    kind: 'screen',
    label: 'редактор',
    className: 'layer--editor',
    onClose: () => cleanup(),
    render: ({ close }) => {
      const box = h('div', { class: 'editor' });
      cleanup = mountEditor(box, id, close, onDone);
      return box;
    },
  });
}

function mountEditor(root, id, close, onDone) {
  const saved = editFor(id) || {};
  const edit = { parts: clone(saved.parts || {}), colors: clone(saved.colors || {}), fx: {} };
  Object.keys(FX).forEach((k) => { edit.fx[k] = { ...clone(FX[k]), ...clone((saved.fx || {})[k] || {}) }; });

  let data = null; // ответ сервера: anim, parts, colors, checks
  let selected = null; // id выбранной части
  let anim = null;
  let playing = !prefersReducedMotion();
  let alive = true;
  let raf = 0;

  // ── сцена: превью, рамка выбранной детали, перетаскивание
  const holder = h('div', { class: 'ed-anim' });
  const frameBox = h('div', { class: 'ed-box', hidden: true }, ...['tl', 'tr', 'bl', 'br'].map((c) => h('i', { class: `ed-box__h ed-box__h--${c}` })));
  const spinner = h('span', { class: 'spinner ed-busy', hidden: true });
  const stage = h('div', { class: 'ed-stage' }, holder, frameBox, spinner);
  const playBtn = h('button', { class: 'ed-play', type: 'button', 'aria-label': 'пауза', onclick: () => { haptic.select(); setPlaying(!playing); } });
  const scrub = h('input', { class: 'ed-scrub', type: 'range', min: 0, max: 179, step: 1, value: 0, 'aria-label': 'кадр' });
  const frameLabel = h('span', { class: 'ed-frame' }, 'кадр 0');
  scrub.addEventListener('input', () => { setPlaying(false); if (anim) anim.goToAndStop(Number(scrub.value), true); paintFrame(); });

  function setPlaying(on) {
    playing = on;
    replaceChildren(playBtn, icon(on ? 'pause' : 'play'));
    playBtn.setAttribute('aria-label', on ? 'Пауза' : 'Старт');
    playBtn.setAttribute('title', on ? 'Пауза' : 'Старт');
    playBtn.setAttribute('aria-pressed', String(on));
    playBtn.classList.toggle('is-paused', !on);
    if (anim) { if (on) anim.play(); else anim.pause(); }
  }
  function paintFrame() {
    if (!anim) return;
    const f = Math.round(anim.currentFrame);
    scrub.value = String(f);
    frameLabel.textContent = `кадр ${f}`;
    partFrame.textContent = `кадр ${f}`;
  }
  const domId = (pid) => `ezp-${pid.replace('.', '-')}`;
  const partEl = (pid) => (pid ? holder.querySelector(`#${CSS.escape(domId(pid))}`) : null);
  // габариты детали — по её видимым фигурам: у группы слоя бывают пустые контуры в точке (0, 0)
  function partRect(el) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    el.querySelectorAll('path, ellipse, rect, circle').forEach((n) => {
      if (n.tagName === 'path' && !(n.getAttribute('d') || '').trim()) return;
      const r = n.getBoundingClientRect();
      if (!r.width && !r.height) return;
      x0 = Math.min(x0, r.left); y0 = Math.min(y0, r.top); x1 = Math.max(x1, r.right); y1 = Math.max(y1, r.bottom);
    });
    return x0 === Infinity ? null : { left: x0, top: y0, width: x1 - x0, height: y1 - y0 };
  }
  function placeBox() {
    const el = partEl(selected);
    const r = el && partRect(el);
    if (!r) { frameBox.hidden = true; return; }
    const s = stage.getBoundingClientRect();
    frameBox.hidden = false;
    Object.assign(frameBox.style, { left: `${r.left - s.left - 4}px`, top: `${r.top - s.top - 4}px`, width: `${r.width + 8}px`, height: `${r.height + 8}px` });
  }
  const tick = () => { placeBox(); raf = alive ? requestAnimationFrame(tick) : 0; };

  async function showAnim(animationData, mine) {
    const lib = await loadLottie();
    if (!alive || mine !== seq) return;
    const frame = anim ? anim.currentFrame : 0;
    if (anim) anim.destroy();
    replaceChildren(holder);
    anim = lib.loadAnimation({ container: holder, renderer: 'svg', loop: true, autoplay: false, animationData,
      rendererSettings: { preserveAspectRatio: 'xMidYMid meet' } });
    scrub.max = String(Math.max(1, Math.round(anim.totalFrames) - 1));
    anim.addEventListener('enterFrame', paintFrame);
    anim.goToAndStop(Math.min(frame || 0, Math.max(0, anim.totalFrames - 1)), true);
    if (playing) anim.play();
    paintFrame();
  }

  // выбор детали касанием и перетаскивание выбранной
  let drag = null;
  stage.addEventListener('pointerdown', (e) => {
    const hit = document.elementsFromPoint(e.clientX, e.clientY)
      .map((n) => n.closest && n.closest('[id^="ezp-"]')).find((n) => n && holder.contains(n));
    const pid = hit ? hit.id.slice(4).replace('-', '.') : null;
    if (!pid) { select(null); return; }
    if (pid !== selected) { select(pid); return; }
    const part = partById(pid);
    if (!part) return;
    const p = partEdit(pid);
    drag = { x: e.clientX, y: e.clientY, x0: p.x || 0, y0: p.y || 0, el: hit, part, moved: false };
    stage.setPointerCapture(e.pointerId);
    setPlaying(false);
  });
  stage.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
    // сразу двигаем саму деталь в SVG — в единицах её родителя
    const parent = drag.el.parentNode;
    const m = parent && parent.getScreenCTM ? parent.getScreenCTM() : null;
    if (m) {
      const det = m.a * m.d - m.b * m.c || 1;
      drag.el.style.translate = `${(m.d * dx - m.c * dy) / det}px ${(-m.b * dx + m.a * dy) / det}px`;
    }
  });
  const endDrag = (e) => {
    if (!drag) return;
    const d = drag;
    drag = null;
    if (!d.moved) return;
    const k = 512 / stage.getBoundingClientRect().width / (d.part.scale || 1);
    updatePart(d.part.id, { x: Math.round((d.x0 + (e.clientX - d.x) * k) * 10) / 10, y: Math.round((d.y0 + (e.clientY - d.y) * k) * 10) / 10 });
  };
  stage.addEventListener('pointerup', endDrag);
  stage.addEventListener('pointercancel', endDrag);

  // ── части эмодзи
  const partsList = h('div', { class: 'ed-parts', role: 'listbox', 'aria-label': 'части эмодзи' });
  const partById = (pid) => (data ? data.parts.find((p) => p.id === pid) : null);
  const partEdit = (pid) => edit.parts[pid] || {};
  const colorOf = (part) => partEdit(part.id).fill || edit.colors[part.color] || part.color;

  function paintParts() {
    if (!data) return;
    replaceChildren(partsList, ...data.parts.map((p) => h('button', {
      class: `ed-part${p.id === selected ? ' is-on' : ''}${edit.parts[p.id] && compact({ parts: { [p.id]: edit.parts[p.id] } }) ? ' is-edited' : ''}`,
      type: 'button', role: 'option', 'aria-selected': String(p.id === selected),
      onclick: () => { haptic.select(); select(p.id === selected ? null : p.id); },
    },
    h('span', { class: `ed-part__icon ed-part__icon--${p.kind}`, style: { '--c': colorOf(p) } }, KIND_ICON[p.kind] || '◆'),
    h('span', { class: 'ed-part__name' }, p.name),
    edit.fx.holo.only && edit.fx.holo.parts.includes(p.id) ? h('span', { class: 'ed-part__tag' }, 'голо') : null)));
  }

  // ── панель выбранной детали
  const panel = h('div', { class: 'ed-panel', hidden: true });
  const partFrame = h('span', { class: 'ed-panel__frame' });
  function paintPanel() {
    const part = partById(selected);
    panel.hidden = !part;
    if (!part) { replaceChildren(panel); return; }
    const p = partEdit(part.id);
    const color = colorOf(part);
    const swatch = h('input', { class: 'ed-swatch__input', type: 'color', value: color, 'aria-label': 'цвет детали' });
    swatch.addEventListener('input', () => { dot.style.setProperty('--c', swatch.value); hex.textContent = swatch.value; updatePart(part.id, { fill: swatch.value }); });
    const dot = h('span', { class: 'ed-swatch__dot', style: { '--c': color } });
    const hex = h('span', { class: 'ed-panel__hex' }, color);
    const animBtns = ANIMS.map(([aid, label]) => h('button', {
      class: `ed-anim-btn${(p.anim || 'none') === aid ? ' is-on' : ''}`, type: 'button',
      onclick: () => {
        haptic.select();
        animBtns.forEach((b, i) => b.classList.toggle('is-on', ANIMS[i][0] === aid));
        updatePart(part.id, { anim: aid });
      },
    }, label));
    const row = (label, ...inputs) => h('div', { class: 'ed-row' }, h('span', { class: 'ed-row__label' }, label), h('span', { class: 'ed-row__inputs' }, ...inputs));
    const holoOnly = edit.fx.holo.on && edit.fx.holo.only;
    replaceChildren(panel, 
      h('div', { class: 'ed-panel__head' },
        h('b', { class: 'ed-panel__name' }, part.name),
        h('button', { class: 'ed-icon-btn', type: 'button', 'aria-label': 'сбросить деталь', onclick: () => {
          haptic.select(); delete edit.parts[part.id]; paintPanel(); paintParts(); schedule();
        } }, icon('trash')),
        h('button', { class: 'ed-icon-btn', type: 'button', 'aria-label': 'снять выбор', onclick: () => { haptic.select(); select(null); } }, icon('close'))),
      h('label', { class: 'ed-fill' }, h('span', { class: 'ed-swatch' }, swatch, dot), hex, h('span', { class: 'ed-fill__label' }, 'заливка'),
        p.fill ? h('button', { class: 'ed-icon-btn ed-icon-btn--sm', type: 'button', 'aria-label': 'родной цвет', onclick: (e) => {
          e.preventDefault(); haptic.select(); updatePart(part.id, { fill: null }); paintPanel();
        } }, icon('close')) : null),
      h('div', { class: 'ed-sub' }, h('span', null, 'движение'), partFrame),
      h('div', { class: 'ed-card' },
        row('позиция', NumberBox(p.x || 0, (v) => updatePart(part.id, { x: v }), { min: -1024, max: 1024, label: 'сдвиг по x' }),
          NumberBox(p.y || 0, (v) => updatePart(part.id, { y: v }), { min: -1024, max: 1024, label: 'сдвиг по y' })),
        row('масштаб', NumberBox(p.s == null ? 100 : p.s, (v) => updatePart(part.id, { s: v }), { min: 0, max: 500, label: 'масштаб, %' })),
        row('поворот', NumberBox(p.r || 0, (v) => updatePart(part.id, { r: v }), { min: -720, max: 720, label: 'поворот, градусы' })),
        row('прозрачность', NumberBox(p.o == null ? 100 : p.o, (v) => updatePart(part.id, { o: v }), { min: 0, max: 100, label: 'прозрачность, %' })),
        h('div', { class: 'ed-sub ed-sub--in' }, 'анимация детали'),
        h('div', { class: 'ed-anims' }, ...animBtns)),
      holoOnly ? h('div', { class: 'ed-row ed-row--toggle' }, h('span', { class: 'ed-row__label' }, 'голограмма на этой детали'),
        Toggle(edit.fx.holo.parts.includes(part.id), (on) => {
          const set = new Set(edit.fx.holo.parts);
          if (on) set.add(part.id); else set.delete(part.id);
          edit.fx.holo.parts = [...set];
          paintParts();
          schedule();
        }, 'голограмма на детали').el) : null,
    );
    paintFrame();
  }

  function updatePart(pid, patch) {
    const next = { ...partEdit(pid), ...patch };
    Object.keys(next).forEach((k) => { if (next[k] == null) delete next[k]; });
    edit.parts[pid] = next;
    paintParts();
    schedule();
  }

  function select(pid) {
    selected = pid;
    paintParts();
    paintPanel();
    placeBox();
  }

  // ── все цвета
  const colorsBox = h('div', { class: 'ed-colors' });
  let colorsOpen = false;
  function paintColors() {
    if (!data) return;
    const list = data.colors;
    const shown = colorsOpen ? list : list.slice(0, 5);
    const rows = shown.map(({ hex: src, count }) => {
      const cur = edit.colors[src] || src;
      const input = h('input', { class: 'ed-swatch__input', type: 'color', value: cur, 'aria-label': `цвет ${src}` });
      const dot = h('span', { class: 'ed-swatch__dot', style: { '--c': cur } });
      const label = h('span', { class: 'ed-color__hex' }, cur);
      input.addEventListener('input', () => {
        edit.colors[src] = input.value;
        dot.style.setProperty('--c', input.value);
        label.textContent = input.value;
        schedule();
      });
      return h('label', { class: `ed-color${edit.colors[src] ? ' is-changed' : ''}` },
        h('span', { class: 'ed-swatch' }, input, dot), label,
        edit.colors[src] ? h('button', { class: 'ed-icon-btn ed-icon-btn--sm', type: 'button', 'aria-label': 'вернуть цвет', onclick: (e) => {
          e.preventDefault(); haptic.select(); delete edit.colors[src]; paintColors(); schedule();
        } }, icon('close')) : null,
        h('span', { class: 'ed-color__count' }, places(count)));
    });
    const more = list.length > 5
      ? h('button', { class: 'ed-more', type: 'button', onclick: () => { haptic.select(); colorsOpen = !colorsOpen; paintColors(); } },
        colorsOpen ? 'свернуть' : `ещё ${list.length - 5} ${plural(list.length - 5, 'цвет', 'цвета', 'цветов')}`, icon(colorsOpen ? 'chev-up' : 'chev-down'))
      : null;
    replaceChildren(colorsBox, ...rows, ...(more ? [more] : []));
  }

  // варианты цветов: тот же набор, повёрнутый по цветовому кругу
  const variantsBox = h('div', { class: 'ed-variants', hidden: true });
  const variantsBtn = h('button', { class: 'ed-link-row', type: 'button', onclick: () => {
    haptic.select(); variantsBox.hidden = !variantsBox.hidden; paintVariants();
  } }, 'варианты цветов');
  function paintVariants() {
    if (variantsBox.hidden || !data) return;
    const src = data.colors.map((c) => c.hex);
    const chips = [1, 2, 3, 4, 5].map((n) => {
      const map = Object.fromEntries(src.map((c) => [c, rotateHue(c, n / 6)]));
      return h('button', { class: 'ed-variant', type: 'button', 'aria-label': `вариант ${n}`, onclick: () => {
        haptic.select(); edit.colors = { ...map }; paintColors(); paintParts(); schedule();
      } }, ...src.slice(0, 4).map((c) => h('i', { style: { background: map[c] } })));
    });
    chips.push(h('button', { class: 'ed-variant ed-variant--reset', type: 'button', onclick: () => {
      haptic.select(); edit.colors = {}; paintColors(); paintParts(); schedule();
    } }, 'как было'));
    replaceChildren(variantsBox, ...chips);
  }

  // ── эффекты
  const fxBox = h('div', { class: 'ed-group' });
  const setFx = (name, patch, repaint = false) => {
    Object.assign(edit.fx[name], patch);
    if (repaint) paintFx();
    schedule();
  };
  const pct = (v) => `${v}%`;
  function fxRow(label, on, onChange, settings) {
    const t = Toggle(on, onChange, label);
    return [h('div', { class: 'ed-row ed-row--toggle' }, h('span', { class: 'ed-row__label' }, label), t.el),
      ...(on && settings ? [h('div', { class: 'ed-fx' }, ...settings())] : [])];
  }
  function paintFx() {
    const { pixel, metal, glitch, holo } = edit.fx;
    const holoSettings = () => [
      h('p', { class: 'ed-note ed-note--top' }, 'Хром находит границы эмодзи и бежит по ним: чёрный, белый и выбранный оттенок переливаются по краю и по контурам.'),
      // где переливается: по контуру эмодзи (обводки), по заливкам или везде
      Seg([['contour', 'контур'], ['fill', 'заливки'], ['all', 'всё']], holo.target, (v) => setFx('holo', { target: v }, true)),
      holo.target !== 'fill' ? h('div', { class: 'ed-row ed-row--toggle ed-row--in' }, h('span', { class: 'ed-row__label' }, 'чёрная основа'),
        Toggle(holo.dark, (v) => setFx('holo', { dark: v }), 'чёрная основа').el) : null,
      // хромовая кромка по силуэту стикера; на пиксельной мозаике и на отдельных деталях её нет
      holo.target !== 'fill' && !holo.only && !pixel.on ? Slider({ label: 'кромка', min: 0, max: 32, value: holo.edge,
        fmt: (v) => (v ? `${v} px` : 'нет'), onInput: (v) => setFx('holo', { edge: v }) }) : null,
      h('div', { class: 'ed-pal' }, ...PALETTES.map(([pid, c]) => h('button', {
        class: `ed-pal__dot${!holo.tint && holo.palette === pid ? ' is-on' : ''}${c ? '' : ' ed-pal__dot--rainbow'}`, type: 'button',
        style: c ? { '--c': c } : {}, 'aria-label': pid, onclick: () => { haptic.select(); setFx('holo', { palette: pid, tint: null }, true); },
      }))),
      h('label', { class: 'ed-row ed-row--in' }, h('span', { class: 'ed-row__label' }, 'свой оттенок'),
        h('span', { class: 'ed-swatch' }, h('input', { class: 'ed-swatch__input', type: 'color',
          value: holo.tint || PALETTES.find(([pid]) => pid === holo.palette)?.[1] || '#ff2d55', 'aria-label': 'оттенок хрома',
          oninput: (e) => { e.target.nextElementSibling.style.setProperty('--c', e.target.value); setFx('holo', { tint: e.target.value }); },
        }), h('span', { class: 'ed-swatch__dot', style: { '--c': holo.tint || PALETTES.find(([pid]) => pid === holo.palette)?.[1] || '#ff2d55' } }))),
      Seg([['cw', 'по часовой'], ['ccw', 'против']], holo.dir, (v) => setFx('holo', { dir: v })),
      Slider({ label: 'оборотов', min: 1, max: 6, value: holo.turns, onInput: (v) => setFx('holo', { turns: v }) }),
      Slider({ label: 'ширина блика', min: 20, max: 300, step: 5, value: holo.width, fmt: pct, onInput: (v) => setFx('holo', { width: v }) }),
      Slider({ label: 'яркость блика', min: 0, max: 100, value: holo.bright, fmt: pct, onInput: (v) => setFx('holo', { bright: v }) }),
      h('div', { class: 'ed-row ed-row--toggle ed-row--in' }, h('span', { class: 'ed-row__label' }, 'глянец на заливках'),
        Toggle(holo.gloss, (v) => setFx('holo', { gloss: v }), 'глянец').el),
    ];
    replaceChildren(fxBox, 
      ...fxRow('пикселизация', pixel.on, (v) => setFx('pixel', { on: v }, true), () => [
        Slider({ label: 'сетка', min: 8, max: 40, value: pixel.grid, onInput: (v) => setFx('pixel', { grid: v }) }),
        Slider({ label: 'зазор клеток', min: 0, max: 35, value: pixel.gap, fmt: pct, onInput: (v) => setFx('pixel', { gap: v }) }),
        Seg([['square', 'квадрат'], ['soft', 'мягкий'], ['circle', 'круг']], pixel.shape, (v) => setFx('pixel', { shape: v })),
        Slider({ label: 'кадров/с', min: 4, max: 20, value: pixel.fps, onInput: (v) => setFx('pixel', { fps: v }) }),
        Slider({ label: 'цветов', min: 2, max: 64, value: pixel.colors, fmt: (v) => (v >= 64 ? 'все' : String(v)), onInput: (v) => setFx('pixel', { colors: v }) }),
        Slider({ label: 'тонкие линии', min: 0, max: 100, value: pixel.lines, fmt: pct, onInput: (v) => setFx('pixel', { lines: v }) }),
      ]),
      ...fxRow('блеск металла', metal.on, (v) => setFx('metal', { on: v }, true), () => [
        Slider({ label: 'ширина блика', min: 20, max: 300, step: 5, value: metal.width, fmt: pct, onInput: (v) => setFx('metal', { width: v }) }),
      ]),
      ...fxRow('глитч', glitch.on, (v) => setFx('glitch', { on: v }, true), () => {
        const c1 = h('input', { class: 'ed-swatch__input', type: 'color', value: glitch.c1, 'aria-label': 'цвет силуэта 1' });
        const c2 = h('input', { class: 'ed-swatch__input', type: 'color', value: glitch.c2, 'aria-label': 'цвет силуэта 2' });
        const d1 = h('span', { class: 'ed-swatch__dot', style: { '--c': glitch.c1 } });
        const d2 = h('span', { class: 'ed-swatch__dot', style: { '--c': glitch.c2 } });
        c1.addEventListener('input', () => { d1.style.setProperty('--c', c1.value); setFx('glitch', { c1: c1.value }); });
        c2.addEventListener('input', () => { d2.style.setProperty('--c', c2.value); setFx('glitch', { c2: c2.value }); });
        return [
          Slider({ label: 'сбоев', min: 1, max: 16, value: glitch.count, onInput: (v) => setFx('glitch', { count: v }) }),
          Slider({ label: 'длина сбоя', min: 20, max: 300, step: 5, value: glitch.len, fmt: pct, onInput: (v) => setFx('glitch', { len: v }) }),
          Slider({ label: 'сила сдвига', min: 0, max: 300, step: 5, value: glitch.shift, fmt: pct, onInput: (v) => setFx('glitch', { shift: v }) }),
          Slider({ label: 'полос', min: 0, max: 24, value: glitch.stripes, onInput: (v) => setFx('glitch', { stripes: v }) }),
          Slider({ label: 'чёрточек', min: 0, max: 48, value: glitch.dashes, onInput: (v) => setFx('glitch', { dashes: v }) }),
          h('div', { class: 'ed-row ed-row--toggle ed-row--in' }, h('span', { class: 'ed-row__label' }, 'цветные силуэты'),
            Toggle(glitch.ghost, (v) => setFx('glitch', { ghost: v }), 'цветные силуэты').el),
          h('div', { class: 'ed-row ed-row--in' }, h('span', { class: 'ed-row__label' }, 'цвета силуэтов'),
            h('span', { class: 'ed-row__inputs' }, h('label', { class: 'ed-swatch' }, c1, d1), h('label', { class: 'ed-swatch' }, c2, d2))),
          h('button', { class: 'ed-link-row ed-link-row--in', type: 'button', onclick: () => {
            haptic.select(); setFx('glitch', { seed: Math.floor(Math.random() * 99999) });
          } }, 'перемешать сбои'),
        ];
      }),
      ...fxRow('голограмма', holo.on && !holo.only, (v) => setFx('holo', { on: v, only: false }, true), holoSettings),
      ...fxRow('голограмма только на выделенном', holo.on && holo.only, (v) => {
        if (v && !selected && !holo.parts.length) { toast('Сначала выбери деталь на превью'); paintFx(); return; }
        setFx('holo', { on: v, only: v, parts: v ? (holo.parts.length ? holo.parts : [selected]) : holo.parts }, true);
        paintParts();
        paintPanel();
      }, holoSettings),
    );
  }

  // ── эффект при показе (вместо эффекта по нажатию — его Telegram даёт только своим премиум-стикерам)
  const burstBox = h('div', { class: 'ed-group ed-burst' });
  function paintBurst() {
    const b = edit.fx.burst;
    const on = b.kind !== 'none';
    replaceChildren(burstBox,
      h('div', { class: 'ed-fxpick', role: 'radiogroup', 'aria-label': 'эффект при показе' }, ...BURSTS.map(([kid, label, tint]) => h('button', {
        class: `ed-fxtile${b.kind === kid ? ' is-on' : ''}${kid === 'none' ? ' ed-fxtile--none' : ''}`, type: 'button',
        role: 'radio', 'aria-checked': String(b.kind === kid), style: { '--c': (kid !== 'none' && b.color) || tint },
        onclick: () => {
          if (b.kind === kid) return;
          haptic.select(); setFx('burst', { kind: kid }); paintBurst(); replay();
        },
      }, burstArt(kid, kid === 'none' ? null : b.color), h('span', { class: 'ed-fxtile__label' }, label)))),
      on ? h('div', { class: 'ed-fxopts' },
        Slider({ label: 'частиц', min: 4, max: 20, value: b.count, onInput: (v) => setFx('burst', { count: v }) }),
        h('div', { class: 'ed-fxopts__row' }, h('span', { class: 'ed-fxopts__label' }, 'цвет'),
          h('div', { class: 'ed-pal ed-pal--sm' }, ...BURST_COLORS.map((c) => h('button', {
            class: `ed-pal__dot${(b.color || null) === c ? ' is-on' : ''}${c ? '' : ' ed-pal__dot--rainbow'}`, type: 'button',
            style: c ? { '--c': c } : {}, 'aria-label': c ? `цвет ${c}` : 'родные цвета',
            onclick: () => { haptic.select(); setFx('burst', { color: c }); paintBurst(); },
          })))),
        h('button', { class: 'ed-fxopts__replay', type: 'button', onclick: () => { haptic.select(); replay(); } }, icon('refresh'), 'посмотреть ещё раз')) : null,
      h('div', { class: 'ed-hint' },
        icon('info'),
        h('span', { class: 'ed-hint__text' }, 'Играет в начале каждого цикла. Эффект «по нажатию» Telegram даёт только своим премиум-стикерам.')),
    );
  }
  const replay = () => { if (anim) { anim.goToAndPlay(0, true); setPlaying(true); } };
  stage.addEventListener('dblclick', replay);

  // ── проверка telegram
  const checksBox = h('div', { class: 'ed-group ed-checks' });
  const checksId = `ed-checks-${id}`;
  const checksInner = h('div', { class: 'ed-disclosure__inner' }, checksBox);
  const checksContent = h('div', { class: 'ed-disclosure__content', id: checksId, 'aria-hidden': 'true', inert: true }, checksInner);
  const checksToggle = h('button', { class: 'ed-disclosure__toggle', type: 'button', 'aria-expanded': 'false', 'aria-controls': checksId,
    onclick: () => {
      haptic.select();
      const expanded = checksToggle.getAttribute('aria-expanded') !== 'true';
      checksToggle.setAttribute('aria-expanded', String(expanded));
      checksContent.setAttribute('aria-hidden', String(!expanded));
      checksContent.inert = !expanded;
      checksSection.classList.toggle('is-open', expanded);
    },
  }, h('span', null, 'Проверка Telegram'), icon('chev-down'));
  const checksSection = h('section', { class: 'ed-disclosure' }, checksToggle, checksContent);
  function paintChecks() {
    if (!data) return;
    replaceChildren(checksBox, ...data.checks.map((c) => h('div', { class: `ed-check${c.ok ? '' : ' is-bad'}` },
      h('span', { class: 'ed-check__mark' }, icon(c.ok ? 'check' : 'close')),
      h('span', { class: 'ed-check__label' }, c.label),
      h('span', { class: 'ed-check__value' }, c.value))));
  }

  // ── загрузка превью
  let seq = 0;
  let renderTimer = 0;
  let renderController = null;
  const previewError = h('div', { class: 'ed-preview-error', hidden: true, role: 'status' },
    h('span', { class: 'ed-preview-error__text' }),
    h('button', { type: 'button', onclick: () => rerender(), class: 'ed-preview-error__retry' }, 'Повторить'));
  stage.append(previewError);
  // сбой сети, перегрузка или ответ, обогнанный более новым, — повторяем молча; ошибку показываем, только если
  // и повтор не удался
  const retryable = (err) => !(err instanceof ApiError) || err.status === 0 || err.status === 429 || err.status >= 500;
  async function rerender(attempt = 0) {
    if (!alive) return;
    clearTimeout(renderTimer);
    const mine = ++seq;
    if (renderController) renderController.abort();
    renderController = new AbortController();
    spinner.hidden = false;
    previewError.hidden = true;
    let stage = 'load';
    let retrying = false;
    try {
      const res = await api.render({ template_id: id, ...renderParams({ ...lookFor(id), edit: compact(edit) }) }, renderController.signal);
      if (!alive || mine !== seq) return;
      stage = 'show';
      data = res;
      if (selected && !partById(selected)) selected = null;
      await showAnim(res.anim, mine);
      if (!alive || mine !== seq) return;
      paintParts();
      paintPanel();
      paintColors();
      paintVariants();
      paintChecks();
    } catch (err) {
      if (!alive || mine !== seq || err.name === 'AbortError') return;
      if (err instanceof ApiError && err.status === 204) return; // сервер пропустил устаревший запрос
      if (stage === 'load' && attempt < 2 && retryable(err)) {
        retrying = true;
        renderTimer = setTimeout(() => rerender(attempt + 1), err.status === 429 ? 2500 : 700 * (attempt + 1));
        return;
      }
      if (stage === 'show') console.error('editor preview', err);
      previewError.hidden = false;
      previewError.querySelector('.ed-preview-error__text').textContent = previewErrorText(err, stage);
    } finally {
      if (mine === seq && !retrying) spinner.hidden = true;
    }
  }
  function previewErrorText(err, stage) {
    if (stage === 'show') return 'Превью не открылось на этом устройстве. Файл пака соберётся как обычно.';
    if (!(err instanceof ApiError) || err.status === 0) return 'Нет связи с сервером. Последний кадр сохранён.';
    if (err.code === 'pro_required') return 'Редактор доступен в Pro';
    if (err.status === 429) return 'Слишком много изменений подряд. Подожди пару секунд.';
    if (err.code === 'validation' || err.status === 400 || err.status === 422) return 'Сервер не принял эти настройки. Нажми «сбросить» вверху.';
    return 'Сервер не смог собрать превью с этими эффектами. Попробуй ещё раз.';
  }
  const schedule = () => {
    ++seq; // устаревший ответ не должен перерисовать текущие настройки
    if (renderController) renderController.abort();
    clearTimeout(renderTimer);
    renderTimer = setTimeout(rerender, 300);
  };

  // ── шапка и низ
  const n = state.selection.indexOf(id);
  const head = h('header', { class: 'ohead' },
    h('button', { class: 'back-btn', type: 'button', 'aria-label': 'назад', onclick: () => { haptic.tap(); close(); } }, icon('chev-left')),
    h('div', { class: 'ohead__title ed-head' }, h('b', null, 'Редактор'),
      state.selection.length > 1 ? h('span', null, `стикер ${n + 1} из ${state.selection.length}`) : null),
    h('button', { class: 'ed-reset', type: 'button', onclick: () => {
      haptic.select();
      edit.parts = {};
      edit.colors = {};
      Object.keys(FX).forEach((k) => { edit.fx[k] = clone(FX[k]); });
      select(null);
      paintFx();
      paintBurst();
      rerender();
      toast('Правки сброшены');
    } }, 'сбросить'));

  const footer = h('div', { class: 'ed-footer' },
    state.selection.length > 1 ? Button({ label: 'Эффекты на все стикеры', variant: 'secondary', onClick: () => {
      const fx = compact({ fx: edit.fx });
      state.selection.forEach((other) => {
        if (other === id) return;
        const e = { ...(editFor(other) || {}) };
        if (fx && fx.fx) e.fx = clone(fx.fx); else delete e.fx;
        setItemEdit(other, compact({ parts: e.parts, colors: e.colors, fx: e.fx || {} }));
      });
      haptic.success();
      toast('Эффекты применены ко всем стикерам');
    } }) : null,
    Button({ label: 'Готово', onClick: () => close() }));

  root.append(h('section', { class: 'screen screen--editor' }, head,
    h('div', { class: 'page ed-page' },
      stage,
      h('div', { class: 'ed-transport' }, playBtn, scrub, frameLabel),
      ...Section('части эмодзи', partsList),
      panel,
      ...Section('все цвета', colorsBox, variantsBtn, variantsBox),
      ...Section('эффекты', fxBox),
      ...Section('эффект при показе', burstBox),
      checksSection),
    footer));

  setPlaying(playing);
  paintFx();
  paintBurst();
  rerender();
  raf = requestAnimationFrame(tick);

  return () => {
    alive = false;
    clearTimeout(renderTimer);
    if (renderController) renderController.abort();
    cancelAnimationFrame(raf);
    if (anim) anim.destroy();
    setItemEdit(id, compact(edit));
    if (onDone) onDone();
  };
}
