// Общие компоненты интерфейса.
import { TgEmoji } from './emoji.js';
import { goTab } from './router.js';
import { haptic, tgUser } from './tg.js';
import { bus, state } from './store.js';
import { displayName, h, icon, initials } from './ui.js';

// ── аватар: фото из Telegram, а если его нет или не загрузилось — первая буква
export function Avatar({ size = 'md' } = {}) {
  const user = state.me;
  const el = h('div', { class: `avatar avatar--${size}`, 'aria-hidden': 'true' }, initials(user));
  const photo = tgUser && tgUser.photo_url;
  if (photo) {
    const img = h('img', { src: photo, alt: '', referrerpolicy: 'no-referrer', decoding: 'async' });
    img.addEventListener('load', () => { el.textContent = ''; el.append(img); });
  }
  return el;
}

// ── баланс генераций «иконка N (+)»; число обновляется само при событии 'me', «+» — пополнить
export function BalanceChip(onClick) {
  const value = h('span', { class: 'chip__value', 'data-balance': '' }, String(state.me ? state.me.balance : 0));
  return pressable(h('button', {
    class: 'chip chip--balance pill', type: 'button', onclick: onClick,
    'aria-label': 'баланс генераций, пополнить',
  }, icon('gens', 'chip__icon'), value, h('span', { class: 'chip__plus', 'aria-hidden': 'true' }, icon('plus'))));
}

/**
 * Капсула шапки «нажимается»: сжимается под пальцем, кайма вспыхивает, отпускается пружиной.
 * :active на iOS срабатывает ненадёжно, поэтому состояние ставим сами по pointer-событиям.
 */
export function pressable(el) {
  const up = () => el.classList.remove('is-pressed');
  el.addEventListener('pointerdown', () => el.classList.add('is-pressed'));
  ['pointerup', 'pointercancel', 'pointerleave'].forEach((t) => el.addEventListener(t, up));
  return el;
}

bus.on('me', (me) => {
  document.querySelectorAll('[data-balance]').forEach((n) => {
    const changed = n.textContent !== String(me.balance);
    n.textContent = String(me.balance);
    // баланс изменился — капсула коротко «подпрыгивает»
    const chip = changed && n.closest('.chip--balance');
    if (chip) { chip.classList.remove('is-bump'); void chip.offsetWidth; chip.classList.add('is-bump'); }
  });
});

/** «@username» или имя, если юзернейма нет. */
export const handle = (me) => (me && me.username ? `@${me.username}` : displayName(me));

const toProfile = () => { haptic.tap(); goTab('profile'); };
// экран покупки подгружаем при нажатии: buy.js сам импортирует компоненты отсюда
const toBuy = () => { haptic.tap(); import('./views/buy.js').then((m) => m.openBuy()); };

// ── шапка вкладки: аватар и @username слева (тап — профиль), баланс справа (тап — пополнение генераций)
export function Topbar(onBalance = toBuy) {
  return h('header', { class: 'topbar' },
    pressable(h('button', { class: 'topbar__who pill', type: 'button', 'aria-label': 'открыть профиль', onclick: toProfile },
      Avatar({ size: 'hd' }), h('span', { class: 'topbar__handle' }, handle(state.me)))),
    h('span', { class: 'topbar__spacer' }),
    BalanceChip(onBalance));
}

/**
 * Текст в стиле цитаты Telegram: полоска слева, кастомный эмодзи, жирный заголовок.
 * Quote({ emoji: 'gift', title: 'Как получить?', text: '…' }) или с children вместо text.
 */
export function Quote({ emoji, title, text, children, cls = '' }) {
  return h('blockquote', { class: `tgq ${cls}`.trim() },
    emoji ? TgEmoji(emoji) : null,
    title ? h('b', null, `${title}: `) : null,
    text || null,
    children || null);
}

/** Строка фильтров-чипов со счётчиками: «Все 3 · Оплачены 2 · …». */
export function Chips(options, value, onChange) {
  const buttons = options.map((o) => h('button', {
    class: `chip-f${o.id === value ? ' is-active' : ''}`, type: 'button', 'aria-pressed': String(o.id === value),
    dataset: { id: o.id },
    onclick: () => { if (o.id !== value) { haptic.select(); set(o.id); onChange(o.id); } },
  }, h('span', null, o.label), h('b', { class: 'chip-f__count' }, '')));
  const el = h('div', { class: 'chips', role: 'toolbar' }, ...buttons);
  function set(id) {
    value = id;
    buttons.forEach((b) => {
      const on = b.dataset.id === id;
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-pressed', String(on));
    });
  }
  const counts = (map) => buttons.forEach((b) => {
    const n = map ? map[b.dataset.id] : undefined;
    b.querySelector('.chip-f__count').textContent = n == null ? '' : String(n);
  });
  return { el, set, counts };
}

/** Горизонтальная лента, которую с компьютера листают мышкой: перетаскиванием и колесом.
 *  На телефоне ничего не меняется — там лента листается пальцем сама. */
export function dragScroll(el) {
  let start = null; // {x, left, id} — нажатие мышью
  let moved = false;
  let blockClickUntil = 0;
  el.addEventListener('pointerdown', (e) => {
    if (e.pointerType !== 'mouse' || e.button !== 0) return;
    start = { x: e.clientX, left: el.scrollLeft, id: e.pointerId };
    moved = false;
  });
  el.addEventListener('pointermove', (e) => {
    if (!start || e.pointerId !== start.id) return;
    const dx = e.clientX - start.x;
    if (!moved && Math.abs(dx) > 6) {
      moved = true;
      el.classList.add('is-dragging');
      el.setPointerCapture(e.pointerId);
    }
    if (moved) el.scrollLeft = start.left - dx;
  });
  const end = () => {
    if (!start) return;
    if (moved) {
      el.classList.remove('is-dragging');
      blockClickUntil = Date.now() + 250; // отпускание после перетаскивания — не нажатие на карточку
    }
    start = null;
  };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
  el.addEventListener('click', (e) => {
    if (Date.now() < blockClickUntil) { e.preventDefault(); e.stopPropagation(); }
  }, true);
  el.addEventListener('dragstart', (e) => e.preventDefault());
  // колесо мыши крутит ленту вбок, пока ей есть куда ехать; дальше — прокрутка страницы
  el.addEventListener('wheel', (e) => {
    if (e.ctrlKey || Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
    const max = el.scrollWidth - el.clientWidth;
    if (max <= 1) return;
    if ((e.deltaY < 0 && el.scrollLeft <= 0) || (e.deltaY > 0 && el.scrollLeft >= max - 1)) return;
    e.preventDefault();
    el.scrollBy({ left: e.deltaY * (e.deltaMode === 1 ? 32 : 1), behavior: 'auto' });
  }, { passive: false });
  return el;
}

/** Заголовок раздела слева и действие справа («Шаблоны · Классика >»). */
export const SectionHead = (title, action) => h('div', { class: 'section-head' },
  h('h2', { class: 'section-head__title' }, title), action || null);

// ── кнопки
export function Button({ label, variant = 'primary', onClick, disabled = false, icon: iconName, full = true, type = 'button' }) {
  const btn = h('button', {
    class: `btn btn--${variant}${full ? ' btn--block' : ''}`, type, disabled,
    onclick: (e) => { if (!btn.disabled && onClick) { haptic.tap(); onClick(e); } },
  }, iconName ? icon(iconName, 'btn__icon') : null, h('span', { class: 'btn__label' }, label));
  return btn;
}

export function setBusy(btn, busy, busyLabel) {
  btn.classList.toggle('is-busy', busy);
  btn.disabled = busy;
  const label = btn.querySelector('.btn__label');
  if (label) {
    if (busy && busyLabel) { btn.dataset.label = label.textContent; label.textContent = busyLabel; }
    else if (!busy && btn.dataset.label) { label.textContent = btn.dataset.label; delete btn.dataset.label; }
  }
}

// ── степпер количества: − [число] +
export function Stepper({ value, min = 1, max = 500, onChange }) {
  let current = value;
  const input = h('input', {
    class: 'stepper__input', type: 'text', inputMode: 'numeric', pattern: '[0-9]*',
    value: String(current), 'aria-label': 'количество',
  });
  const minus = h('button', { class: 'stepper__btn', type: 'button', 'aria-label': 'меньше' }, icon('minus'));
  const plus = h('button', { class: 'stepper__btn', type: 'button', 'aria-label': 'больше' }, icon('plus'));

  const apply = (n, silent) => {
    current = Math.max(min, Math.min(max, Number.isFinite(n) ? n : min));
    input.value = String(current);
    minus.disabled = current <= min;
    plus.disabled = current >= max;
    if (!silent && onChange) onChange(current);
  };
  const step = (d) => { haptic.select(); apply(current + d); };

  // удержание ускоряет
  const hold = (btn, d) => {
    let timer; let delay = 380;
    const tick = () => { step(d); delay = Math.max(70, delay * 0.82); timer = setTimeout(tick, delay); };
    const stop = () => { clearTimeout(timer); delay = 380; };
    btn.addEventListener('pointerdown', (e) => { e.preventDefault(); step(d); timer = setTimeout(tick, delay); });
    ['pointerup', 'pointerleave', 'pointercancel'].forEach((ev) => btn.addEventListener(ev, stop));
    btn.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); step(d); } });
  };
  hold(minus, -1);
  hold(plus, 1);

  input.addEventListener('input', () => {
    const digits = input.value.replace(/\D/g, '').slice(0, 4);
    input.value = digits;
    if (digits) apply(parseInt(digits, 10));
  });
  input.addEventListener('blur', () => apply(parseInt(input.value, 10)));
  apply(current, true);

  const el = h('div', { class: 'stepper' }, minus, input, plus);
  return { el, get: () => current, set: (n) => apply(n) };
}

// ── строка списка: подпись, значение справа, шеврон, pro-бейдж
export function Row({ label, sub, value, chevron = true, pro = false, iconName, onClick, danger = false }) {
  const tag = onClick ? 'button' : 'div';
  const row = h(tag, { class: `row${danger ? ' row--danger' : ''}${onClick ? ' row--action' : ''}`, type: onClick ? 'button' : undefined,
    onclick: onClick ? () => { haptic.tap(); onClick(); } : undefined },
    iconName ? h('span', { class: 'row__icon' }, icon(iconName)) : null,
    h('span', { class: 'row__main' },
      h('span', { class: 'row__label' }, label),
      sub ? h('span', { class: 'row__sub' }, sub) : null),
    value != null ? (value instanceof Node ? value : h('span', { class: 'row__value' }, value)) : null,
    pro ? ProBadge() : null,
    onClick && chevron ? icon('chev-right', 'row__chev') : null);
  return row;
}

/** Метка Pro: белая «таблетка» с надписью PRO. */
export const ProBadge = (cls = '') => h('span', { class: `badge badge--pro ${cls}`.trim(), 'aria-label': 'доступно в Pro' }, 'Pro');

export const Group = (...rows) => h('div', { class: 'group' }, ...rows);

export const SectionTitle = (text) => h('h3', { class: 'section-title' }, text);

export function CloseButton(onClick) {
  return h('button', { class: 'close-btn', type: 'button', 'aria-label': 'закрыть', onclick: onClick }, icon('close'));
}

/** Крупный анимированный эмодзи для заставок и пустых состояний (ключ из app/emoji.py). */
export const BigEmoji = (key, cls = '') => h('div', { class: `bigmoji ${cls}`.trim(), 'aria-hidden': 'true' }, TgEmoji(key));

export function Skeleton(cls) {
  return h('div', { class: `skeleton ${cls || ''}`.trim(), 'aria-hidden': 'true' });
}

export function ErrorState({ title = 'Не получилось загрузить', text = 'Проверь соединение и попробуй ещё раз', onRetry }) {
  return h('div', { class: 'empty empty--error' },
    BigEmoji('oops'),
    h('h2', { class: 'empty__title' }, title),
    h('p', { class: 'empty__text' }, text),
    onRetry ? Button({ label: 'Повторить', variant: 'secondary', onClick: onRetry, full: false }) : null);
}
