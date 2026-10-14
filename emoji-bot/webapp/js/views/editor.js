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
import { debounce, h, icon, plural, prefersReducedMotion, toast } from '../ui.js';
import { openBuy } from './buy.js';

// настройки эффектов по умолчанию — как FX в app/editor.py
const FX = {
  pixel: { on: false, grid: 32, shape: 'square', fps: 15, colors: 48, lines: 50 },
  metal: { on: false, width: 100 },
  glitch: { on: false, count: 6, len: 100, shift: 100, stripes: 8, dashes: 18, ghost: true, c1: '#ff1f4b', c2: '#18e0ff', seed: 1 },
  holo: { on: false, only: false, parts: [], palette: 'red', dir: 'cw', turns: 1, width: 100, bright: 88, gloss: true },
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
const BURSTS = [['none', 'нет'], ['sparks', 'искры'], ['hearts', 'сердечки'], ['confetti', 'конфетти'], ['stars', 'звёзды'], ['flash', 'вспышка']];
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
    playBtn.replaceChildren(icon(on ? 'pause' : 'play'));
    playBtn.setAttribute('aria-label', on ? 'пауза' : 'играть');
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

  async function showAnim(animationData) {
    const lib = await loadLottie();
    if (!alive) return;
    const frame = anim ? anim.currentFrame : 0;
    if (anim) anim.destroy();
    holder.replaceChildren();
    anim = lib.loadAnimation({ container: holder, renderer: 'svg', loop: true, autoplay: false, animationData,
      rendererSettings: { preserveAspectRatio: 'xMidYMid meet' } });
    scrub.max = String(Math.max(1, Math.round(anim.totalFrames) - 1));
    anim.addEventListener('enterFrame', paintFrame);
    anim.goToAndStop(frame || 0, true);
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
    partsList.replaceChildren(...data.parts.map((p) => h('button', {
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
    if (!part) { panel.replaceChildren(); return; }
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
    panel.replaceChildren(
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
    colorsBox.replaceChildren(...rows, more);
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
    variantsBox.replaceChildren(...chips);
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
      on && settings ? h('div', { class: 'ed-fx' }, ...settings()) : null];
  }
  function paintFx() {
    const { pixel, metal, glitch, holo } = edit.fx;
    const holoSettings = () => [
      h('div', { class: 'ed-pal' }, ...PALETTES.map(([pid, c]) => h('button', {
        class: `ed-pal__dot${holo.palette === pid ? ' is-on' : ''}${c ? '' : ' ed-pal__dot--rainbow'}`, type: 'button',
        style: c ? { '--c': c } : {}, 'aria-label': pid, onclick: () => { haptic.select(); setFx('holo', { palette: pid }, true); },
      }))),
      Seg([['cw', 'по часовой'], ['ccw', 'против']], holo.dir, (v) => setFx('holo', { dir: v })),
      Slider({ label: 'оборотов', min: 1, max: 6, value: holo.turns, onInput: (v) => setFx('holo', { turns: v }) }),
      Slider({ label: 'ширина блика', min: 20, max: 300, step: 5, value: holo.width, fmt: pct, onInput: (v) => setFx('holo', { width: v }) }),
      Slider({ label: 'яркость блика', min: 0, max: 100, value: holo.bright, fmt: pct, onInput: (v) => setFx('holo', { bright: v }) }),
      h('div', { class: 'ed-row ed-row--toggle ed-row--in' }, h('span', { class: 'ed-row__label' }, 'глянец на заливках'),
        Toggle(holo.gloss, (v) => setFx('holo', { gloss: v }), 'глянец').el),
    ];
    fxBox.replaceChildren(
      ...fxRow('пикселизация', pixel.on, (v) => setFx('pixel', { on: v }, true), () => [
        Slider({ label: 'сетка', min: 8, max: 64, value: pixel.grid, onInput: (v) => setFx('pixel', { grid: v }) }),
        Seg([['square', 'квадрат'], ['soft', 'мягкий'], ['circle', 'круг']], pixel.shape, (v) => setFx('pixel', { shape: v })),
        Slider({ label: 'кадров/с', min: 4, max: 30, value: pixel.fps, onInput: (v) => setFx('pixel', { fps: v }) }),
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
  const burstBox = h('div', { class: 'ed-group' });
  function paintBurst() {
    const b = edit.fx.burst;
    burstBox.replaceChildren(
      h('div', { class: 'ed-bursts' }, ...BURSTS.map(([kid, label]) => h('button', {
        class: `ed-anim-btn${b.kind === kid ? ' is-on' : ''}`, type: 'button',
        onclick: () => { haptic.select(); setFx('burst', { kind: kid }); paintBurst(); replay(); },
      }, label))),
      b.kind !== 'none' ? Slider({ label: 'частиц', min: 4, max: 20, value: b.count, onInput: (v) => setFx('burst', { count: v }) }) : null,
      h('p', { class: 'ed-note' }, 'Эффект по нажатию в Telegram есть только у премиум-стикеров самого Telegram, ботам он недоступен. Поэтому эффект встроен в анимацию: он играет в начале каждого цикла. Нажми на превью, чтобы посмотреть ещё раз.'),
    );
  }
  const replay = () => { if (anim) { anim.goToAndPlay(0, true); setPlaying(true); } };
  stage.addEventListener('dblclick', replay);

  // ── проверка telegram
  const checksBox = h('div', { class: 'ed-group ed-checks' });
  function paintChecks() {
    if (!data) return;
    checksBox.replaceChildren(...data.checks.map((c) => h('div', { class: `ed-check${c.ok ? '' : ' is-bad'}` },
      h('span', { class: 'ed-check__mark' }, icon(c.ok ? 'check' : 'close')),
      h('span', { class: 'ed-check__label' }, c.label),
      h('span', { class: 'ed-check__value' }, c.value))));
  }

  // ── загрузка превью
  let seq = 0;
  async function rerender() {
    const mine = ++seq;
    spinner.hidden = false;
    try {
      const res = await api.render({ template_id: id, ...renderParams({ ...lookFor(id), edit: compact(edit) }) });
      if (!alive || mine !== seq) return;
      data = res;
      if (selected && !partById(selected)) selected = null;
      await showAnim(res.anim);
      paintParts();
      paintPanel();
      paintColors();
      paintVariants();
      paintChecks();
    } catch (err) {
      if (!alive || mine !== seq) return;
      toast(err instanceof ApiError && err.code === 'pro_required' ? 'Продвинутые настройки доступны в Pro' : 'Не получилось обновить превью', 'warn');
    } finally {
      if (mine === seq) spinner.hidden = true;
    }
  }
  const schedule = debounce(rerender, 220);

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
      ...Section('проверка telegram', checksBox)),
    footer));

  setPlaying(playing);
  paintFx();
  paintBurst();
  rerender();
  raf = requestAnimationFrame(tick);

  return () => {
    alive = false;
    cancelAnimationFrame(raf);
    if (anim) anim.destroy();
    setItemEdit(id, compact(edit));
    if (onDone) onDone();
  };
}
