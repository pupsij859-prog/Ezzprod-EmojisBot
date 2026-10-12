// Общие компоненты интерфейса.
import { TgEmoji } from './emoji.js';
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

// ── чип баланса «✦ N»; число обновляется само при событии 'me'
export function BalanceChip(onClick) {
  const value = h('span', { class: 'chip__value', 'data-balance': '' }, String(state.me ? state.me.balance : 0));
  return h('button', {
    class: 'chip chip--balance', type: 'button', onclick: onClick,
    'aria-label': 'баланс генераций, нажми, чтобы купить',
  }, icon('sparkle-fill', 'chip__icon'), value);
}
bus.on('me', (me) => {
  document.querySelectorAll('[data-balance]').forEach((n) => { n.textContent = String(me.balance); });
});

/** «@username» или имя, если юзернейма нет. */
export const handle = (me) => (me && me.username ? `@${me.username}` : displayName(me));

// ── шапка вкладки: аватар и @username слева, баланс справа
export function Topbar(onBalance) {
  return h('header', { class: 'topbar' },
    h('span', { class: 'topbar__who' }, Avatar({ size: 'sm' }), h('span', { class: 'topbar__handle' }, handle(state.me))),
    h('span', { class: 'topbar__spacer' }),
    onBalance ? BalanceChip(onBalance) : null);
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

/** Заголовок раздела слева и действие справа («Шаблоны · Классика >»). */
export const SectionHead = (title, action) => h('div', { class: 'section-head' },
  h('h2', { class: 'section-head__title' }, title), action || null);

/**
 * Плитка с мягкой цветной подсветкой (как блок «Полезное»).
 * glow — 'r, g, b' или var(--glow-*); art — картинка/иконка в центре.
 */
export function GlowTile({ label, glow, art, onClick, index = 0, ariaLabel, badge }) {
  const style = { '--i': String(index) };
  if (glow) style['--glow'] = glow;
  return h('button', {
    class: 'gtile', type: 'button', style, 'aria-label': ariaLabel || undefined,
    onclick: () => { haptic.tap(); if (onClick) onClick(); },
  },
  art ? h('span', { class: 'gtile__art', 'aria-hidden': 'true' }, art) : null,
  badge ? h('span', { class: 'gtile__count' }, badge) : null,
  h('span', { class: 'gtile__label' }, label));
}

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
    pro ? h('span', { class: 'badge badge--pro' }, 'pro') : null,
    onClick && chevron ? icon('chev-right', 'row__chev') : null);
  return row;
}

export const Group = (...rows) => h('div', { class: 'group' }, ...rows);

export const SectionTitle = (text) => h('h3', { class: 'section-title' }, text);

// ── заголовок блока по центру («Полезное»)
export const ScreenTitle = (text, id) => h('h2', { class: 'screen-title', id }, text);

export function CloseButton(onClick) {
  return h('button', { class: 'close-btn', type: 'button', 'aria-label': 'закрыть', onclick: onClick }, icon('close'));
}

/** Иконка в квадрате с подсветкой — для пустых состояний и заставок. */
export const GlowIcon = (iconName, cls = '') => h('div', { class: `empty__icon ${cls}`.trim(), 'aria-hidden': 'true' }, icon(iconName));

export function Skeleton(cls) {
  return h('div', { class: `skeleton ${cls || ''}`.trim(), 'aria-hidden': 'true' });
}

export function ErrorState({ title = 'Не получилось загрузить', text = 'Проверь соединение и попробуй ещё раз', onRetry }) {
  return h('div', { class: 'empty empty--error' },
    GlowIcon('info'),
    h('h2', { class: 'empty__title' }, title),
    h('p', { class: 'empty__text' }, text),
    onRetry ? Button({ label: 'Повторить', variant: 'secondary', onClick: onRetry, full: false }) : null);
}
