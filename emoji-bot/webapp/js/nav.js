// Стек оверлеев (шторка / модалка / полный экран) и единая логика кнопки «назад».
// Правило: Telegram BackButton показан ВСЕГДА, пока открыт хотя бы один оверлей, и скрыт на корне вкладок.
import { backButton, closeApp, inTelegram } from './tg.js';
import { h, icon, prefersReducedMotion } from './ui.js';

const stack = [];

function host() {
  return document.getElementById('overlays');
}

function syncBack() {
  if (stack.length) backButton.show(closeTop);
  else backButton.hide();
}

function finish(entry) {
  entry.el.remove();
  if (entry.returnFocus && document.contains(entry.returnFocus)) {
    try { entry.returnFocus.focus({ preventScroll: true }); } catch {}
  }
  if (entry.onClose) entry.onClose();
}

function removeEntry(entry) {
  const i = stack.indexOf(entry);
  if (i === -1) return;
  stack.splice(i, 1);
  syncBack();
  entry.el.classList.remove('is-open');
  entry.el.classList.add('is-closing');
  if (prefersReducedMotion()) finish(entry);
  else setTimeout(() => finish(entry), 260);
}

export function closeTop() {
  const top = stack[stack.length - 1];
  if (top && top.dismissible !== false) removeEntry(top);
  return Boolean(top);
}

export function closeAll() {
  [...stack].reverse().forEach(removeEntry);
}

export const depth = () => stack.length;

/**
 * kind: 'sheet' | 'modal' | 'screen'
 * render({close}) → Node | Node[]
 */
export function openOverlay({ kind = 'screen', className = '', render, onClose, dismissible = true, label = '' }) {
  // двойной тап не должен открывать тот же экран второй раз поверх себя
  const key = `${kind}|${className}|${label}`;
  const open = stack.find((e) => e.key === key);
  if (open) return open.handle;
  const returnFocus = document.activeElement;
  const entry = { kind, dismissible, onClose, returnFocus, el: null, key, handle: null };
  const close = () => removeEntry(entry);
  const content = render({ close });
  const nodes = Array.isArray(content) ? content : [content];

  let surface;
  const children = [];
  if (kind !== 'screen') {
    children.push(h('div', { class: 'overlay__scrim', onclick: () => dismissible && close() }));
  }
  if (kind === 'sheet') {
    surface = h('div', { class: `sheet ${className}`.trim(), tabindex: -1 },
      h('div', { class: 'sheet__handle', 'aria-hidden': 'true' }), ...nodes);
  } else if (kind === 'modal') {
    surface = h('div', { class: `modal ${className}`.trim(), tabindex: -1 }, ...nodes);
  } else {
    surface = h('div', { class: `layer ${className}`.trim(), tabindex: -1 }, ...nodes);
  }
  children.push(surface);

  entry.el = h('div', {
    class: `overlay overlay--${kind}`,
    role: 'dialog',
    'aria-modal': 'true',
    'aria-label': label || undefined,
  }, ...children);

  host().append(entry.el);
  stack.push(entry);
  syncBack();
  if (kind === 'sheet') enableSheetDrag(entry, surface, close);

  requestAnimationFrame(() => {
    entry.el.classList.add('is-open');
    try { surface.focus({ preventScroll: true }); } catch {}
  });
  entry.handle = { close, el: entry.el, surface };
  return entry.handle;
}

function enableSheetDrag(entry, surface, close) {
  const handle = surface.querySelector('.sheet__handle');
  let startY = null;
  let dy = 0;
  const grab = (e) => {
    startY = e.clientY;
    dy = 0;
    surface.style.transition = 'none';
    handle.setPointerCapture(e.pointerId);
  };
  const move = (e) => {
    if (startY == null) return;
    dy = Math.max(0, e.clientY - startY);
    surface.style.transform = `translateY(${dy}px)`;
  };
  const release = () => {
    if (startY == null) return;
    startY = null;
    surface.style.transition = '';
    if (dy > 90) close();
    surface.style.transform = '';
  };
  [handle, surface.querySelector('.sheet__grab')].filter(Boolean).forEach((el) => {
    el.style.touchAction = 'none';
    el.addEventListener('pointerdown', grab);
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', release);
    el.addEventListener('pointercancel', release);
  });
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && stack.length) closeTop();
});

// ── Макет «окна Telegram» для запуска в обычном браузере (в Telegram его нет)
export function mountDevFrame(title) {
  if (inTelegram) return;
  const frame = document.getElementById('devframe');
  frame.hidden = false;
  document.body.classList.add('has-devframe');
  const back = h('button', { class: 'devframe__btn', type: 'button', 'aria-label': 'назад', hidden: true,
    onclick: () => backButton.trigger() }, icon('back'));
  frame.append(back, h('span', { class: 'devframe__title' }, title),
    h('button', { class: 'devframe__btn', type: 'button', 'aria-label': 'закрыть', onclick: closeApp }, icon('close')));
  backButton.onDevChange((visible) => { back.hidden = !visible; });
}
