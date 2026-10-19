// Экран «Оформление»: витрина с живым превью на CRT Warp, выбранные шаблоны, тип пака,
// ник, логотип, заливка и обводка — для всех стикеров сразу или для одного (того, что в витрине) — и создание пака.
import { api, ApiError, newIdempotencyKey } from '../api.js';
import { BigEmoji, Button, dragScroll, Group, Row, SectionTitle, setBusy } from '../components.js';
import { mountCrtWarp } from '../crt.js';
import { openOverlay } from '../nav.js';
import { activePalette, PALETTES, paletteLook } from '../presets.js';
import {
  bus, clearSelection, DEFAULT_COLOR, DEFAULT_GRAD, DEFAULT_STROKE, editFor, hasOwnLook, logoInUse, lookFor, refreshMe, setItemLook,
  setLookForAll, setType, state, toggleSelection,
} from '../store.js';
import { haptic, tgUser } from '../tg.js';
import { cleanGrad, lookOf, TgsPreview, watchVisibility } from '../tgs.js';
import { emojiWord, gens, h, icon, toast } from '../ui.js';
import { openBuy } from './buy.js';
import { openCreating } from './creating.js';
import { openFonts } from './fonts.js';
import { openAdvanced } from './editor.js';
import { openStyles } from './styles.js';

const LOGO_ERRORS = {
  logo_format: 'Поддерживаем png, jpg и webp',
  logo_too_big: 'Файл слишком большой',
  logo_invalid: 'Не получилось прочитать картинку',
  logo_animated: 'Анимированные картинки не подходят, загрузи обычную',
  logo_quota: 'Слишком много загруженных логотипов',
};


function Segmented(options, value, onChange) {
  let current = value;
  const buttons = options.map((o) => h('button', {
    class: `segmented__btn${o.id === value ? ' is-active' : ''}`, type: 'button', role: 'tab',
    'aria-selected': String(o.id === value), dataset: { id: o.id },
    onclick: () => { if (o.id !== current) { haptic.select(); onChange(o.id); } },
  }, o.label));
  const el = h('div', { class: 'segmented', role: 'tablist', dataset: { index: String(options.findIndex((o) => o.id === value)) } }, ...buttons);
  return {
    el,
    set(id) {
      current = id;
      el.dataset.index = String(options.findIndex((o) => o.id === id));
      buttons.forEach((b) => {
        const on = b.dataset.id === id;
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-selected', String(on));
      });
    },
  };
}

// ── цвет: «Заливка» (основной цвет стикера) или «Обводка» (контуры и надпись). Пусто — родной цвет шаблона
function ColorRow({ label, suggest, get, set }) {
  const value = h('span', { class: 'row__value' }, 'как в шаблоне');
  const input = h('input', { class: 'swatch__input', type: 'color', value: get() || suggest, 'aria-label': label });
  const dot = h('span', { class: 'swatch__dot' });
  const reset = h('button', { class: 'color-reset', type: 'button', onclick: () => { haptic.select(); set(null); paint(); } }, 'сбросить');
  const paint = () => {
    const c = get();
    value.hidden = Boolean(c);
    reset.hidden = !c;
    dot.classList.toggle('is-own', !c);
    if (c) dot.style.setProperty('--c', c); else dot.style.removeProperty('--c');
    input.value = c || suggest;
  };
  input.addEventListener('input', () => { set(input.value.toLowerCase()); paint(); });
  paint();
  const el = h('div', { class: 'row' },
    h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, label)), reset, value, h('label', { class: 'swatch' }, input, dot));
  return { el, paint };
}

// ── «Положение ника»: свёрнутая строка-сводка; по нажатию выдвигается панель — площадка, где точку надписи
// тянут пальцем (сдвиг), и ползунки масштаба и поворота. Двойное нажатие по площадке или ползунку — сброс
function PlaceRow({ get, set }) {
  const LIM = 100;
  const value = h('span', { class: 'row__value placer__sum' });
  const chev = icon('chev-down', 'placer__chev');
  const head = h('button', { class: 'row row--action placer__head', type: 'button', 'aria-expanded': 'false', onclick: () => toggle() },
    h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, 'Положение ника'),
      h('span', { class: 'row__sub' }, 'сдвиг, размер и поворот')), value, chev);

  const coords = h('b', { class: 'placer__coords' });
  const dot = h('span', { class: 'placer__dot' });
  const pad = h('div', { class: 'placer__pad', role: 'slider', tabIndex: 0, 'aria-label': 'положение надписи' },
    h('i', { class: 'placer__axis placer__axis--x' }), h('i', { class: 'placer__axis placer__axis--y' }), dot);

  const range = (label, min, max, step, fmt, onInput, reset) => {
    const out = h('b', { class: 'placer__val' });
    const input = h('input', { class: 'range-row__input placer__range', type: 'range', min, max, step, 'aria-label': label });
    const paint = () => {
      const v = Number(input.value);
      out.textContent = fmt(v);
      input.style.setProperty('--p', `${((v - min) / (max - min)) * 100}%`);
    };
    input.addEventListener('input', () => { paint(); onInput(Number(input.value)); });
    input.addEventListener('dblclick', () => { input.value = String(reset); paint(); onInput(reset); });
    const el = h('label', { class: 'placer__line' }, h('span', { class: 'placer__name' }, label), input, out);
    return { el, input, paint };
  };
  const size = range('масштаб', 40, 160, 5, (v) => (v === 100 ? 'авто' : `${v}%`), (v) => set({ size: v }), 100);
  const rot = range('поворот', -180, 180, 1, (v) => `${v}°`, (v) => {
    const snap = Math.abs(v) <= 3 ? 0 : v; // у нуля «прилипает»: легко вернуть ровно
    movePlace({ r: snap });
  }, 0);
  const reset = h('button', { class: 'placer__reset', type: 'button', onclick: () => {
    haptic.select();
    set({ place: null, size: 100 });
    paint();
  } }, icon('refresh'), 'Сбросить');
  const panel = h('div', { class: 'placer__panel', hidden: true },
    h('div', { class: 'placer__top' }, h('span', { class: 'placer__title' }, 'Позиция'), coords), pad, size.el, rot.el,
    h('div', { class: 'placer__foot' }, h('span', { class: 'placer__hint' }, 'Тяни точку · двойное нажатие — по центру'), reset));

  const place = () => get().place || { x: 0, y: 0, r: 0 };
  function movePlace(patch) {
    const next = { ...place(), ...patch };
    set({ place: next.x || next.y || next.r ? next : null });
    paint();
  }

  let open = false;
  function toggle(force) {
    open = force === undefined ? !open : force;
    haptic.select();
    panel.hidden = !open;
    head.setAttribute('aria-expanded', String(open));
    head.classList.toggle('is-open', open);
  }

  // перетаскивание точки: координаты площадки → сдвиг −100…100 (кадр анимации не чаще раза в кадр экрана)
  let frame = 0;
  const fromEvent = (e) => {
    const r = pad.getBoundingClientRect();
    if (!r.width || !r.height) return place();
    const fx = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    const fy = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    let x = Math.round((fx * 2 - 1) * LIM);
    let y = Math.round((fy * 2 - 1) * LIM);
    if (Math.abs(x) <= 4) x = 0; // центр «прилипает»
    if (Math.abs(y) <= 4) y = 0;
    return { x, y };
  };
  const drag = (e) => {
    const p = fromEvent(e);
    paintDot(p);
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => movePlace(p));
  };
  pad.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    pad.setPointerCapture(e.pointerId);
    pad.classList.add('is-drag');
    drag(e);
  });
  pad.addEventListener('pointermove', (e) => { if (pad.hasPointerCapture(e.pointerId)) drag(e); });
  const stop = (e) => { if (pad.hasPointerCapture(e.pointerId)) pad.releasePointerCapture(e.pointerId); pad.classList.remove('is-drag'); };
  pad.addEventListener('pointerup', stop);
  pad.addEventListener('pointercancel', stop);
  pad.addEventListener('dblclick', () => { haptic.select(); movePlace({ x: 0, y: 0 }); });
  pad.addEventListener('keydown', (e) => {
    const step = e.shiftKey ? 10 : 2;
    const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
    if (!d) return;
    e.preventDefault();
    const p = place();
    movePlace({ x: Math.max(-LIM, Math.min(LIM, p.x + d[0])), y: Math.max(-LIM, Math.min(LIM, p.y + d[1])) });
  });

  function paintDot(p) {
    dot.style.left = `${((p.x + LIM) / (2 * LIM)) * 100}%`;
    dot.style.top = `${((p.y + LIM) / (2 * LIM)) * 100}%`;
    coords.textContent = `${p.x}, ${p.y}`;
  }
  function paint() {
    const p = place();
    const s = get().size || 100;
    paintDot(p);
    if (document.activeElement !== size.input) size.input.value = String(s);
    if (document.activeElement !== rot.input) rot.input.value = String(p.r || 0);
    size.paint();
    rot.paint();
    const parts = [];
    if (p.x || p.y) parts.push('сдвиг');
    if (s !== 100) parts.push(`${s}%`);
    if (p.r) parts.push(`${p.r}°`);
    value.textContent = parts.length ? parts.join(' · ') : 'по центру';
    reset.disabled = !parts.length;
  }
  paint();
  return { el: h('div', { class: 'placer' }, head, panel), paint };
}

// ── «Подборка цветов»: тап — заливка, обводка и цвета градиента из подборки ставятся сразу
function PaletteRow({ get, set }) {
  const value = h('span', { class: 'row__value' });
  const items = PALETTES.map((p) => h('button', {
    class: 'palette', type: 'button', role: 'listitem', 'aria-label': `подборка «${p.name}»`, dataset: { id: p.id },
    style: { '--f': p.fill, '--s': p.stroke, '--a': p.a, '--b': p.b },
    onclick: () => { haptic.select(); set(paletteLook(p, get().grad)); paint(); },
  }, h('span', { class: 'palette__dot' }), h('span', { class: 'palette__name' }, p.name)));
  const paint = () => {
    const look = get();
    const act = activePalette(look);
    value.textContent = act ? act.name : (look.color || look.stroke ? 'свои цвета' : 'как в шаблоне');
    items.forEach((b) => b.classList.toggle('is-active', Boolean(act) && b.dataset.id === act.id));
  };
  paint();
  const el = h('div', { class: 'palrow' },
    h('div', { class: 'palrow__head' }, h('span', { class: 'row__label' }, 'Подборка цветов'), value),
    dragScroll(h('div', { class: 'palettes', role: 'list', 'aria-label': 'подборки цветов' }, ...items)));
  return { el, paint };
}

// ── «Градиент»: свёрнутая строка-сводка; в панели — на что ложится (ник, логотип, заливка), два цвета и направление
const GRAD_ON = [{ id: 'text', label: 'Ник' }, { id: 'logo', label: 'Логотип' }, { id: 'fill', label: 'Заливка' }];
const GRAD_DIRS = [{ angle: 90, label: '→' }, { angle: 135, label: '↘' }, { angle: 180, label: '↓' }, { angle: 45, label: '↗' }];

function GradRow({ get, set }) {
  const grad = () => ({ ...DEFAULT_GRAD, ...(get().grad || {}) });
  const update = (patch) => { set({ grad: { ...grad(), ...patch } }); paint(); };
  const bar = h('span', { class: 'gradbar' });
  const value = h('span', { class: 'row__value gradrow__sum' });
  const chev = icon('chev-down', 'placer__chev');
  const head = h('button', { class: 'row row--action placer__head', type: 'button', 'aria-expanded': 'false', onclick: () => toggle() },
    h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, 'Градиент'),
      h('span', { class: 'row__sub' }, 'на ник, логотип или заливку')), value, bar, chev);

  const targets = GRAD_ON.map((t) => h('button', { class: 'gtoggle', type: 'button', 'aria-pressed': 'false', dataset: { id: t.id },
    onclick: () => {
      haptic.select();
      const on = grad().on.includes(t.id) ? grad().on.filter((x) => x !== t.id) : [...grad().on, t.id];
      update({ on });
    } }, t.label));

  const swatch = (key, label) => {
    const input = h('input', { class: 'swatch__input', type: 'color', 'aria-label': label });
    const dot = h('span', { class: 'swatch__dot' });
    input.addEventListener('input', () => update({ [key]: input.value.toLowerCase() }));
    return { el: h('label', { class: 'gcolor' }, h('span', { class: 'swatch' }, input, dot), h('span', null, label)), input, dot };
  };
  const from = swatch('a', 'от');
  const to = swatch('b', 'до');
  const swap = h('button', { class: 'gswap', type: 'button', 'aria-label': 'поменять цвета местами', onclick: () => {
    haptic.select();
    const g = grad();
    update({ a: g.b, b: g.a });
  } }, icon('refresh'));
  const dirs = GRAD_DIRS.map((d) => h('button', { class: 'gdir', type: 'button', 'aria-label': `направление ${d.angle}°`,
    dataset: { angle: String(d.angle) }, onclick: () => { haptic.select(); update({ angle: d.angle }); } }, d.label));
  const sample = h('div', { class: 'gsample' });
  const off = h('button', { class: 'placer__reset', type: 'button', onclick: () => { haptic.select(); update({ on: [] }); } },
    icon('close'), 'Выключить');

  const panel = h('div', { class: 'placer__panel', hidden: true },
    h('div', { class: 'placer__top' }, h('span', { class: 'placer__title' }, 'Куда')),
    h('div', { class: 'gtoggles' }, ...targets),
    h('div', { class: 'placer__top' }, h('span', { class: 'placer__title' }, 'Цвета и направление')),
    h('div', { class: 'gline' }, from.el, swap, to.el, h('div', { class: 'gdirs' }, ...dirs)),
    sample,
    h('div', { class: 'placer__foot' }, h('span', { class: 'placer__hint' }, 'Цвета градиента меняет и «Подборка цветов»'), off));

  let open = false;
  function toggle() {
    open = !open;
    haptic.select();
    panel.hidden = !open;
    head.setAttribute('aria-expanded', String(open));
    head.classList.toggle('is-open', open);
  }

  function paint() {
    const g = grad();
    const css = `linear-gradient(${g.angle}deg, ${g.a}, ${g.b})`;
    bar.style.background = css;
    sample.style.background = css;
    bar.classList.toggle('is-off', !g.on.length);
    value.textContent = g.on.length ? GRAD_ON.filter((t) => g.on.includes(t.id)).map((t) => t.label.toLowerCase()).join(' · ') : 'выключен';
    targets.forEach((b) => {
      const on = g.on.includes(b.dataset.id);
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-pressed', String(on));
    });
    [[from, g.a], [to, g.b]].forEach(([s, c]) => {
      if (document.activeElement !== s.input) s.input.value = c;
      s.dot.style.setProperty('--c', c);
    });
    dirs.forEach((b) => b.classList.toggle('is-active', Number(b.dataset.angle) === g.angle));
    off.disabled = !g.on.length;
  }
  paint();
  return { el: h('div', { class: 'placer' }, head, panel), paint };
}

// ── лента выбранных — она же выбор, что меняем: «Все» — все стикеры, миниатюра — только этот стикер
// (он же крупно в витрине). Крестик убирает шаблон, «+» — к шаблонам
function PickedStrip(close, onFocus) {
  const strip = dragScroll(h('div', { class: 'strip', role: 'list', 'aria-label': 'выбранные шаблоны' }));
  const seen = watchVisibility(strip);
  let players = new Map(); // id → проигрыватель (переиспользуем при перерисовке ленты)
  let looks = new Map(); // id → параметры превью этого стикера
  let focused = null;
  const paint = () => {
    const next = new Map();
    const items = state.selection.map((id) => {
      const player = players.get(id) || TgsPreview(id, { look: looks.get(id) || null, lazy: true, cls: 'tgs--strip' });
      if (!players.has(id)) seen.add(player);
      next.set(id, player);
      return h('div', {
        class: `strip__item${id === focused ? ' is-focus' : ''}${hasOwnLook(id) ? ' is-own' : ''}`,
        role: 'listitem', dataset: { id }, onclick: () => { haptic.select(); onFocus(id); },
      },
        player.el,
        h('button', { class: 'strip__del', type: 'button', 'aria-label': 'убрать', onclick: (e) => {
          e.stopPropagation();
          haptic.select();
          toggleSelection(id);
        } }, icon('close')));
    });
    players.forEach((pl, id) => { if (!next.has(id)) pl.destroy(); });
    players = next;
    const all = state.selection.length > 1
      ? h('button', { class: `strip__all${focused === '*' ? ' is-focus' : ''}`, type: 'button', dataset: { id: '*' },
        'aria-label': 'менять все стикеры', onclick: () => { haptic.select(); onFocus('*'); } },
      icon('grid'), h('span', null, 'Все'))
      : null;
    strip.replaceChildren(...(all ? [all] : []), ...items,
      h('button', { class: 'strip__add', type: 'button', 'aria-label': 'добавить ещё', onclick: () => { haptic.tap(); close(); } },
        icon('plus')));
  };
  paint();
  return {
    el: strip,
    paint,
    apply(map) { looks = map; players.forEach((pl, id) => pl.update(map.get(id) || null)); },
    mark(id) {
      focused = id;
      strip.querySelectorAll('.strip__item, .strip__all').forEach((n) => {
        n.classList.toggle('is-focus', n.dataset.id === id);
        if (n.dataset.id !== '*') n.classList.toggle('is-own', hasOwnLook(n.dataset.id));
      });
    },
    destroy() { seen.disconnect(); players.forEach((pl) => pl.destroy()); },
  };
}

// ── витрина: крупное живое превью выбранного элемента на голубом CRT Warp (как баннер на главной)
function Showcase() {
  const fx = h('div', { class: 'hero__fx', 'aria-hidden': 'true' });
  const stage = h('div', { class: 'show__stage' });
  const balance = h('b', { 'data-balance': '' });
  const pos = h('span', { class: 'show__pos' });
  const el = h('section', { class: 'show' }, fx,
    h('div', { class: 'show__top' },
      h('span', { class: 'show__chip' }, icon('gens', 'show__spark'), balance),
      pos),
    stage);
  let player = null;
  let shownId = null;
  let stopFx = () => {};
  return {
    el,
    show(id, look) {
      const i = state.selection.indexOf(id);
      pos.textContent = state.selection.length > 1 ? `${i + 1} из ${state.selection.length}` : '';
      if (id !== shownId || !player) {
        if (player) player.destroy();
        player = TgsPreview(id, { look, cls: 'tgs--hero' });
        shownId = id;
        stage.replaceChildren(player.el);
      } else {
        player.update(look);
      }
    },
    paint() { balance.textContent = String(state.me.balance); },
    // приглушённее, чем на главной: на этом фоне должен читаться стикер
    start() { stopFx(); stopFx = mountCrtWarp(fx, { brightness: 0.75, speed: 0.35 }); },
    destroy() { stopFx(); stopFx = () => {}; if (player) player.destroy(); player = null; shownId = null; },
  };
}

function EmptyOrder(close, onOpenPicker) {
  return h('div', { class: 'empty' },
    BigEmoji('bag'),
    h('h2', { class: 'empty__title' }, 'Пока ничего не выбрано'),
    h('p', { class: 'empty__text' }, onOpenPicker ? 'Выберите шаблоны для добавления в пак' : 'Отметь на главной шаблоны, которые хочешь в свой пак'),
    Button({ label: onOpenPicker ? 'Выбрать шаблоны' : 'К шаблонам', full: false, onClick: onOpenPicker || close }));
}

/** Экран оформления заказа (открывается плашкой «Оформить» над нижней панелью или при добавлении в пак). */
export function openOrder({ targetPack = null, onAdded = null, initialSelection = null, onOpenPicker = null, focusText = false } = {}) {
  let cleanup = () => {};
  if (initialSelection && Array.isArray(initialSelection)) {
    state.selection = [...initialSelection];
  }
  if (targetPack) {
    state.type = targetPack.type;
    if (targetPack.params) {
      if (!state.text && targetPack.params.text) state.text = targetPack.params.text;
      if (!state.color && targetPack.params.color) state.color = targetPack.params.color;
      if (!state.stroke && targetPack.params.stroke) state.stroke = targetPack.params.stroke;
    }
  }
  openOverlay({
    kind: 'screen',
    label: targetPack ? `добавить в «${targetPack.title}»` : 'заказ',
    className: 'layer--order',
    onClose: () => cleanup(),
    render: ({ close }) => {
      const box = h('div', { class: 'order' });
      cleanup = mountOrder(box, close, { targetPack, onAdded, onOpenPicker, focusText });
      return box;
    },
  });
}

function mountOrder(container, close, { targetPack = null, onAdded = null, onOpenPicker = null, focusText = false } = {}) {
  const offs = [];
  const showcase = Showcase();
  offs.push(() => showcase.destroy());
  const root = h('section', { class: 'screen screen--order' });
  container.append(root);

  // ключ идемпотентности: повтор того же запроса (потерялся ответ) не спишет генерации дважды;
  // если выбор или настройки изменились, ключ меняется
  let idemKey = newIdempotencyKey();
  const rekey = () => { idemKey = newIdempotencyKey(); };
  offs.push(bus.on('selection', rekey), bus.on('type', rekey), bus.on('personalization', rekey));

  let parts = null; // элементы формы, пока выбор не пуст
  const render = () => {
    const title = targetPack ? `Добавить в «${targetPack.title}»` : 'Оформление';
    const head = [h('header', { class: 'ohead' },
      h('button', { class: 'back-btn', type: 'button', 'aria-label': 'назад', onclick: () => { haptic.tap(); close(); } }, icon('chev-left')),
      h('h1', { class: 'ohead__title' }, title),
      h('span', { 'aria-hidden': 'true' }))];
    if (parts) { parts.destroy(); parts = null; }
    if (!state.selection.length) {
      showcase.destroy();
      root.replaceChildren(...head, EmptyOrder(close, onOpenPicker));
      return;
    }
    showcase.paint();
    parts = buildForm();
    root.replaceChildren(...head, h('div', { class: 'page' }, showcase.el), parts.el);
    requestAnimationFrame(() => { if (showcase.el.isConnected) showcase.start(); });
  };

  offs.push(bus.on('selection', () => {
    if (!state.selection.length || !parts) { render(); return; }
    parts.paintSelection();
  }));
  offs.push(bus.on('type', () => parts && parts.paintFooter()));
  offs.push(bus.on('me', () => { showcase.paint(); if (parts) parts.paintFooter(); }));
  offs.push(bus.on('personalization', () => parts && parts.paintText()));

  function buildForm() {
    const limits = state.me.limits;

    // ── что меняем: все стикеры или только тот, что в витрине
    let focusId = state.selection[0];  // стикер в витрине
    let scope = 'all';                 // 'all' — все стикеры, 'one' — только стикер в витрине
    const editsOne = () => scope === 'one' && state.selection.length > 1;
    const cur = () => (editsOne() ? lookFor(focusId)
      : { text: state.text, logo: state.logo, color: state.color, stroke: state.stroke, size: state.size, place: state.place, font: state.font,
        grad: state.grad });
    const setLook = (patch) => (editsOne() ? setItemLook(focusId, patch) : setLookForAll(patch));

    // ── живое превью: у каждого стикера свои ник, логотип и цвета
    let looks = new Map();
    let lookVersion = 0;
    const showHero = () => {
      if (!state.selection.includes(focusId)) focusId = state.selection[0];
      if (focusId) showcase.show(focusId, looks.get(focusId) || null);
      strip.mark(editsOne() ? focusId : '*');
    };
    let heroRaf = 0;
    const refreshHeroFast = () => {
      cancelAnimationFrame(heroRaf);
      heroRaf = requestAnimationFrame(async () => {
        const id = focusId || state.selection[0];
        if (!id) return;
        const look = await lookOf(id);
        looks.set(id, look);
        if (showcase) showcase.show(id, look);
      });
    };
    const refreshLive = async () => {
      const mine = ++lookVersion;
      const ids = [...state.selection];
      const list = await Promise.all(ids.map((id) => lookOf(id)));
      if (mine !== lookVersion) return;
      looks = new Map(ids.map((id, i) => [id, list[i]]));
      strip.apply(looks);
      showHero();
    };

    // ── выбранные
    const strip = PickedStrip(onOpenPicker || close, (id) => {
      if (id === '*') scope = 'all';
      else { scope = 'one'; focusId = id; }
      showHero();
      paintFields();
    });
    const pickedTitle = h('b');
    const picked = h('div', { class: 'order__picked' },
      h('div', { class: 'order__picked-head' }, pickedTitle,
        h('button', { class: 'order__clear', type: 'button', onclick: () => { haptic.tap(); clearSelection(); } }, 'Очистить')),
      strip.el);

    // ── тип пака
    const seg = Segmented([{ id: 'emoji', label: 'Эмодзи' }, { id: 'sticker', label: 'Стикеры' }], state.type, setType);

    // ── метка у заголовка: что сейчас меняем; у стикера со своими настройками — «сбросить» к общим
    const scopeTag = h('span', { class: 'scope-tag' });
    const scopeReset = h('button', { class: 'scope-reset', type: 'button', onclick: () => {
      haptic.select();
      delete state.overrides[focusId];
      setLookForAll({});  // сохранить и перерисовать
    } }, 'сбросить');
    const persHead = h('div', { class: 'order__sechead' }, SectionTitle('Персонализация'), scopeTag, scopeReset);

    // ── ник
    const text = h('input', {
      class: 'field', type: 'text', value: cur().text || '', maxLength: limits.text_max,
      placeholder: 'ваш ник', autocomplete: 'off', autocapitalize: 'off', enterKeyHint: 'done',
      'aria-label': 'ник на стикере',
    });
    text.spellcheck = false;
    text.addEventListener('input', () => setLook({ text: text.value }));
    text.addEventListener('keydown', (e) => { if (e.key === 'Enter') text.blur(); });
    const textRow = h('label', { class: 'row row--field' },
      h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, 'Ник')), text);

    // ── положение ника: сдвиг, размер (100% — автоподбор по длине) и поворот — в выдвижной панели
    const placeRow = PlaceRow({ get: cur, set: setLook });

    // ── логотип
    const file = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp', hidden: true });
    const logoValue = h('span', { class: 'row__value' });
    const logoRow = h('button', { class: 'row row--action', type: 'button', onclick: () => {
      if (cur().logo) return;
      file.click();
    } },
    h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, 'Логотип')), logoValue);
    const removeBtn = h('button', { class: 'logo-remove', type: 'button', 'aria-label': 'убрать логотип', onclick: (e) => {
      e.stopPropagation();
      const removed = cur().logo;
      setLook({ logo: null });
      paintLogo();
      // если логотип больше нигде не стоит — удаляем и на сервере, чтобы не занимал квоту загрузок
      if (removed && removed.id && !logoInUse(removed.id)) api.deleteLogo(removed.id).catch(() => {});
    } }, icon('close'));
    function paintLogo(busy) {
      logoValue.replaceChildren();
      logoRow.classList.toggle('is-busy', Boolean(busy));
      if (busy) { logoValue.append(h('span', { class: 'spinner', 'aria-label': 'загружаем' })); return; }
      const logo = cur().logo;
      if (logo) {
        logoValue.append(h('img', { class: 'logo-thumb', src: logo.thumb, alt: '' }),
          h('span', { class: 'logo-name' }, logo.name), removeBtn);
      } else {
        logoValue.append(h('span', null, 'загрузить'), icon('chev-right', 'row__chev'));
      }
    }
    file.addEventListener('change', async () => {
      const f = file.files && file.files[0];
      file.value = '';
      if (!f) return;
      if (f.size > limits.logo_max_bytes) { haptic.error(); toast(`Файл больше ${Math.round(limits.logo_max_bytes / 1048576)} МБ`, 'warn'); return; }
      paintLogo(true);
      try {
        const logo = await api.uploadLogo(f);
        setLook({ logo });
        haptic.success();
      } catch (err) {
        haptic.error();
        const field = err instanceof ApiError ? (err.code === 'too_large' ? 'logo_too_big' : err.detail.field) : null;
        toast(LOGO_ERRORS[field] || 'Не получилось загрузить логотип', 'warn');
      }
      paintLogo(false);
    });

    // ── шрифт ника: шторка с 15 готовыми шрифтами и своими загруженными (.ttf); пусто — стандартный
    const fontValue = h('span', { class: 'row__value' });
    const fontRow = h('button', { class: 'row row--action', type: 'button', onclick: () => {
      haptic.tap();
      openFonts({
        current: cur().font, sample: cur().text, maxBytes: limits.font_max_bytes,
        onPick: (font) => {
          setLook({ font });
          paintFont();
          // шрифт меняет только ник: без ника на стикере остаётся родная надпись шаблона
          if (font && !(cur().text || '').trim()) {
            toast('Шрифт применится к нику — впиши его', 'warn');
            text.focus();
          }
        },
      });
    } },
    h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, 'Шрифт')), fontValue);
    function paintFont() {
      const font = cur().font;
      fontValue.replaceChildren(h('span', { class: 'font-name' }, font ? font.name : 'стандартный'), icon('chev-right', 'row__chev'));
    }

    // ── сохранённые стили (pro) — применяются ко всем стикерам
    const stylesRow = Row({
      label: 'Сохранённые стили', pro: state.me.plan !== 'pro',
      onClick: () => openStyles(() => paintFields()),
    });

    // ── продвинутые настройки (pro): редактор стикера, который сейчас в витрине
    const advancedRow = Row({
      label: 'Продвинутые настройки', sub: ' ', pro: state.me.plan !== 'pro', iconName: 'palette',
      onClick: () => openAdvanced(focusId),
    });
    advancedRow.classList.add('row--advanced');
    const advancedSub = advancedRow.querySelector('.row__sub');

    // ── цвета
    const fillRow = ColorRow({ label: 'Заливка', suggest: DEFAULT_COLOR, get: () => cur().color, set: (c) => setLook({ color: c }) });
    const strokeRow = ColorRow({ label: 'Обводка', suggest: DEFAULT_STROKE, get: () => cur().stroke, set: (c) => setLook({ stroke: c }) });
    const paletteRow = PaletteRow({ get: cur, set: setLook });
    const gradRow = GradRow({ get: cur, set: setLook });

    // поля показывают настройки того, что сейчас меняем
    function paintFields() {
      const many = state.selection.length > 1;
      scopeTag.hidden = !many;
      scopeTag.textContent = editsOne() ? `стикер ${state.selection.indexOf(focusId) + 1}` : 'все стикеры';
      scopeTag.classList.toggle('is-one', editsOne());
      scopeReset.hidden = !(editsOne() && hasOwnLook(focusId));
      if (document.activeElement !== text) text.value = cur().text || '';
      placeRow.paint();
      const edited = Boolean(editFor(focusId));
      advancedSub.textContent = many
        ? `стикер ${state.selection.indexOf(focusId) + 1}${edited ? ' · есть правки' : ''}`
        : (edited ? 'есть правки' : 'части, цвета и эффекты');
      paintLogo(false);
      paintFont();
      fillRow.paint();
      strokeRow.paint();
      paletteRow.paint();
      gradRow.paint();
    }
    paintFields();

    // эмодзи-паки в сообщениях используют подписчики Telegram Premium: предупреждаем до списания генераций
    const premiumNote = h('p', { class: 'order__hint' }, 'Эмодзи в сообщениях доступны с Telegram Premium. Стикеры — всем.');

    // ── низ: итог и кнопка
    const summary = h('p', { class: 'checkout__summary' });
    const cta = Button({ label: '', onClick: () => submit() });
    const paintFooter = () => {
      const n = state.selection.length;
      const balance = state.me.balance;
      const enough = balance >= n;
      summary.classList.toggle('is-warn', !enough);
      summary.replaceChildren(
        h('b', null, emojiWord(n, state.type)),
        h('span', { class: 'checkout__balance' }, enough ? `${gens(n)} из ${balance}` : `не хватает ${gens(n - balance)}`));
      // не хватает генераций → кнопка сразу ведёт к докупке нужного количества
      cta.classList.toggle('btn--buy', !enough);
      cta.classList.toggle('btn--primary', enough);
      cta.querySelector('.btn__label').textContent = enough
        ? (targetPack ? `Добавить в пак (+${n})` : 'Создать пак')
        : `Докупить ${gens(n - balance)}`;
      premiumNote.hidden = state.type !== 'emoji' || Boolean(tgUser && tgUser.is_premium);
      seg.set(state.type);
      pickedTitle.textContent = `Выбрано: ${emojiWord(n, state.type)}`;
    };

    async function submit() {
      const n = state.selection.length;
      if (!n) return;
      if (state.me.balance < n) {
        haptic.error();
        toast(`Не хватает ${gens(n - state.me.balance)}`, 'warn');
        openBuy({ qty: Math.max(1, n - state.me.balance) });
        return;
      }

      if (targetPack) {
        setBusy(cta, true, 'Добавляем…');
        try {
          const updated = await api.addPackItems(targetPack.id, state.selection, {
            overrides: overridesPayload(),
            params: {
              text: state.text,
              color: state.color,
              stroke: state.stroke,
              size: state.size,
              place: state.place,
              grad: cleanGrad(state.grad),
              logo_id: state.logo ? state.logo.id : null,
              font_id: state.font ? state.font.id : null,
            },
          });
          haptic.success();
          close();
          clearSelection();
          refreshMe().catch(() => {});
          toast(`Стикеры добавлены в «${targetPack.title}»! Идёт сборка…`);
          if (onAdded) onAdded(updated);
        } catch (err) {
          haptic.error();
          setBusy(cta, false);
          if (err instanceof ApiError && err.status === 402) {
            toast(`Не хватает ${gens(err.detail.need || 1)}`, 'warn');
            openBuy({ qty: err.detail.need || 1 });
          } else if (err instanceof ApiError && err.status === 429) {
            toast('Слишком часто, подожди минутку', 'warn');
          } else {
            toast('Не удалось добавить в пак, попробуй ещё раз', 'error');
          }
        }
        return;
      }

      setBusy(cta, true, 'Создаём…');
      try {
        const pack = await api.createPack({
          type: state.type,
          template_ids: state.selection,
          text: state.text,
          logo_id: state.logo ? state.logo.id : null,
          font_id: state.font ? state.font.id : null,
          color: state.color,
          stroke: state.stroke,
          size: state.size,
          place: state.place,
          grad: cleanGrad(state.grad),
          overrides: overridesPayload(),
        }, idemKey);
        haptic.success();
        close();
        clearSelection();
        refreshMe().catch(() => {});
        openCreating(pack);
      } catch (err) {
        haptic.error();
        setBusy(cta, false);
        if (err instanceof ApiError && err.status === 402) {
          toast(`Не хватает ${gens(err.detail.need || 1)}`, 'warn');
          openBuy({ qty: err.detail.need || 1 });
        } else if (err instanceof ApiError && err.status === 429) {
          toast('Слишком часто, подожди минутку', 'warn');
        } else {
          toast('Не получилось создать пак, попробуй ещё раз', 'error');
        }
      }
    }

    // свои настройки стикеров — серверу: {id шаблона: {text?, logo_id?, color?, stroke?, size?, edit?}}
    function overridesPayload() {
      const out = {};
      state.selection.forEach((id) => {
        const own = state.overrides[id];
        if (!own || !Object.keys(own).length) return;
        const item = {};
        if ('text' in own) item.text = own.text;
        if ('color' in own) item.color = own.color;
        if ('stroke' in own) item.stroke = own.stroke;
        if ('size' in own) item.size = own.size;
        if ('place' in own) item.place = own.place;
        if ('grad' in own) item.grad = cleanGrad(own.grad);
        if (own.edit && state.me.plan === 'pro') item.edit = own.edit;
        if ('logo' in own) item.logo_id = own.logo ? own.logo.id : null;
        if ('font' in own) item.font_id = own.font ? own.font.id : null;
        out[id] = item;
      });
      return out;
    }

    paintFooter();
    refreshLive();
    // после выбора набора остаётся вписать ник: ставим курсор в поле (если ник ещё пустой)
    if (focusText && !(cur().text || '').trim()) setTimeout(() => {
      if (!text.isConnected) return;
      text.scrollIntoView({ block: 'center', behavior: 'smooth' });
      text.focus({ preventScroll: true });
    }, 420);
    let timer = 0;
    const typeSection = targetPack ? null : [SectionTitle('Тип пака'), seg.el];
    const el = h('div', { class: 'page order-page' },
      picked,
      typeSection, premiumNote,
      persHead,
      Group(textRow, placeRow.el, fontRow, logoRow),
      h('div', { class: 'gap' }),
      Group(paletteRow.el, fillRow.el, strokeRow.el, gradRow.el, stylesRow, file),
      h('div', { class: 'gap' }),
      Group(advancedRow),
      h('div', { class: 'checkout' }, summary, cta));

    return {
      el,
      paintFooter,
      paintSelection: () => { strip.paint(); paintFooter(); showHero(); paintFields(); refreshLive(); },
      // ник, логотип или цвет поменялись — перерисовываем превью (ввод ника — с небольшой паузой)
      paintText: () => {
        paintFields();
        refreshHeroFast();
        clearTimeout(timer);
        timer = setTimeout(refreshLive, 180);
      },
      destroy: () => {
        cancelAnimationFrame(heroRaf);
        clearTimeout(timer);
        strip.destroy();
      },
    };
  }

  render();
  return () => { offs.forEach((off) => off()); if (parts) parts.destroy(); };
}
