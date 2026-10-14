// Мелкие помощники для DOM. Весь пользовательский текст попадает в DOM только через textContent.

const SVG_NS = 'http://www.w3.org/2000/svg';
const ICONS_VERSION = '5'; // меняй при правке assets/icons.svg: спрайт кэшируется браузером на час

function append(el, children) {
  for (const child of children) {
    if (child == null || child === false) continue;
    if (Array.isArray(child)) append(el, child);
    else if (child instanceof Node) el.append(child);
    else el.append(document.createTextNode(String(child)));
  }
}

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'style' && typeof value === 'object') {
      // кастомные свойства (--x) задаются только через setProperty
      for (const [k, v] of Object.entries(value)) {
        if (k.startsWith('--')) el.style.setProperty(k, v);
        else el.style[k] = v;
      }
    }
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key in el && !key.startsWith('aria') && key !== 'list') el[key] = value;
    else el.setAttribute(key, value === true ? '' : value);
  }
  append(el, children);
  return el;
}

export function icon(name, cls = '') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', `icon ${cls}`.trim());
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const use = document.createElementNS(SVG_NS, 'use');
  use.setAttribute('href', `assets/icons.svg?v=${ICONS_VERSION}#i-${name}`);
  svg.append(use);
  return svg;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function plural(n, one, few, many) {
  const a = Math.abs(n);
  if (a % 100 >= 11 && a % 100 <= 14) return many;
  const last = a % 10;
  if (last === 1) return one;
  if (last >= 2 && last <= 4) return few;
  return many;
}

/** «классика» → «Классика» */
export const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : '');

export const gens = (n) => `${n} ${plural(n, 'генерация', 'генерации', 'генераций')}`;
export const emojiWord = (n, type) =>
  type === 'sticker'
    ? `${n} ${plural(n, 'стикер', 'стикера', 'стикеров')}`
    : `${n} ${plural(n, 'эмодзи', 'эмодзи', 'эмодзи')}`;

export function prefersReducedMotion() {
  return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function toast(message, kind = 'info') {
  const host = document.getElementById('toasts');
  const el = h('div', { class: `toast toast--${kind}` }, message);
  host.append(el);
  requestAnimationFrame(() => el.classList.add('is-in'));
  setTimeout(() => {
    el.classList.remove('is-in');
    setTimeout(() => el.remove(), 260);
  }, 2600);
}

export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function initials(user) {
  const name = (user && (user.first_name || user.username)) || '?';
  return name.trim().charAt(0).toLowerCase() || '?';
}

export function displayName(user) {
  if (!user) return '';
  const full = [user.first_name, user.last_name].filter(Boolean).join(' ').trim();
  return full || user.username || 'Гость';
}

// «+» для звёзд Telegram: золотая звезда из спрайта + число
/** Сумма в рублях: «180 ₽» (неразрывный пробел). */
export function rub(n) {
  return h('span', { class: 'rub' }, `${n}\u00a0₽`);
}

export function stars(n) {
  return h('span', { class: 'stars' }, String(n), icon('star-fill', 'stars__icon'));
}
