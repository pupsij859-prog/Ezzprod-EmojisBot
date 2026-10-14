import { api, ApiError, previewUrl } from './api.js';
import { ErrorState } from './components.js';
import { closeAll, mountDevFrame } from './nav.js';
import { setTabHandler } from './router.js';
import { bus, loadPrefs, setMe, state } from './store.js';
import { TabBar } from './tabbar.js';
import { haptic, initTelegram } from './tg.js';
import { emojiWord, h, icon } from './ui.js';
import { mountAdmin } from './views/admin.js';
import { openOrder } from './views/customize.js';
import { mountHome } from './views/home.js';
import { runOnboarding } from './views/onboarding.js';
import { mountPacks } from './views/packs.js';
import { mountProfile } from './views/profile.js';

const app = document.getElementById('app');

// Туннель иногда роняет часть параллельных запросов, и стили не приходят — страница без оформления.
// Проверяем свои CSS и перезагружаем недошедшие (до 3 попыток).
function ensureStyles(attempt = 1) {
  const broken = [...document.querySelectorAll('link[rel="stylesheet"][href*="css/"]')].filter((link) => {
    try { return !link.sheet || !link.sheet.cssRules.length; } catch { return true; }
  });
  broken.forEach((link) => {
    const fresh = link.cloneNode();
    fresh.href = `${link.href.split('?')[0]}?retry=${attempt}-${Date.now()}`;
    link.replaceWith(fresh);
  });
  if (broken.length && attempt < 3) setTimeout(() => ensureStyles(attempt + 1), 1500 * attempt);
}
if (document.readyState === 'complete') ensureStyles();
else window.addEventListener('load', () => ensureStyles(), { once: true });

const TABS = [
  { id: 'home', label: 'Главная', icon: 'home', mount: mountHome },
  { id: 'packs', label: 'Паки', icon: 'packs', mount: mountPacks },
  { id: 'profile', label: 'Профиль', icon: 'user-circle', mount: mountProfile },
  { id: 'admin', label: 'Админка', icon: 'crown', mount: mountAdmin, admin: true },
];

let tabs = TABS;
let tabbar = null;
let cleanup = null;

// плашка над панелью на главной: выбранные шаблоны и кнопка «Оформить»
function CartBar() {
  const thumbs = h('span', { class: 'cartbar__thumbs', 'aria-hidden': 'true' });
  const label = h('b', { class: 'cartbar__label' });
  const el = h('button', { class: 'cartbar', type: 'button', 'aria-label': 'оформить заказ', onclick: () => { haptic.tap(); openOrder(); } },
    thumbs, label, h('span', { class: 'cartbar__go' }, 'Оформить', icon('chev-right')));
  let last = -1;
  const update = () => {
    const n = state.selection.length;
    const show = n > 0 && state.tab === 'home';
    el.classList.toggle('is-shown', show);
    el.tabIndex = show ? 0 : -1;
    document.body.classList.toggle('has-cart', show);
    if (!n) { last = 0; return; }
    label.textContent = emojiWord(n, state.type);
    thumbs.replaceChildren(...state.selection.slice(-3).map((id) => h('img', { src: previewUrl(id), alt: '', decoding: 'async' })));
    if (last > 0 && n !== last) {
      el.classList.remove('bump');
      void el.offsetWidth; // перезапуск анимации
      el.classList.add('bump');
    }
    last = n;
  };
  bus.on('selection', update);
  bus.on('type', update);
  bus.on('tab', update);
  update();
  return el;
}

function renderShell() {
  tabs = TABS.filter((t) => !t.admin || state.me.is_admin);
  if (!tabs.some((t) => t.id === state.tab)) state.tab = tabs[0].id;
  const view = h('div', { class: 'view', id: 'view' });
  tabbar = TabBar({ tabs, active: state.tab, onSelect: switchTab });
  app.replaceChildren(view, h('div', { class: 'dock' }, CartBar(), tabbar.el));
  setTabHandler(switchTab);
}

function switchTab(id) {
  const tab = tabs.find((t) => t.id === id) || tabs[0];
  closeAll();
  state.tab = tab.id;
  tabbar.setActive(tab.id);
  bus.emit('tab', tab.id);
  if (cleanup) { cleanup(); cleanup = null; }
  const view = document.getElementById('view');
  view.replaceChildren();
  view.scrollTop = 0;
  view.dataset.tab = tab.id;
  cleanup = tab.mount(view) || null;
}

const FATAL = {
  unauthorized: ['Сессия устарела', 'Закрой приложение и открой его заново из бота'],
  maintenance: ['Технические работы', 'Скоро вернёмся. Попробуй открыть приложение чуть позже'],
  banned: ['Доступ ограничен', 'Если это ошибка, напиши в поддержку'],
};

function fatal(err) {
  const code = err instanceof ApiError ? (err.status === 401 ? 'unauthorized' : err.code) : '';
  const [title, text] = FATAL[code] || ['Не получилось запуститься', 'Проверь соединение и попробуй ещё раз'];
  app.replaceChildren(ErrorState({ title, text, onRetry: () => location.reload() }));
}

// initData живёт сутки: если сессия истекла посреди работы, любой экран покажет понятное сообщение
let expiredShown = false;
window.addEventListener('ez:session-expired', () => {
  if (expiredShown || !state.me) return; // до загрузки профиля это покажет fatal()
  expiredShown = true;
  closeAll();
  fatal(new ApiError(401, { code: 'unauthorized' }));
});

// шрифт не блокирует первый показ: пока он грузится (или если Google недоступен) работает системный
function loadFont() {
  document.head.append(h('link', { rel: 'stylesheet',
    href: 'https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600;700&family=Unbounded:wght@800&display=swap' }));
}

async function boot() {
  loadFont();
  initTelegram();
  mountDevFrame('EzzProd');
  app.replaceChildren(h('div', { class: 'boot', 'aria-label': 'загрузка' }, h('span', { class: 'spinner spinner--lg' })));

  let me;
  try {
    me = await api.me();
  } catch (err) {
    return fatal(err);
  }
  setMe(me);
  document.title = me.brand;

  // справочники грузим параллельно; без них приложение не нужно
  try {
    const [cats, pricing, emoji] = await Promise.all([
      api.categories(), api.pricing(), api.emoji().catch(() => ({ emoji: {} })), // без эмодзи — обычные заглушки
    ]);
    state.categories = cats.categories;
    state.pricing = pricing;
    state.emoji = emoji.emoji || {};
  } catch (err) {
    return fatal(err);
  }
  loadPrefs();
  if (!state.categories.some((c) => c.id === state.category)) state.category = state.categories[0].id;

  // приветствие и инструкция — один раз. Истина хранится на сервере (не зависит от кэша и устройства);
  // локальная отметка нужна только на случай, если «готово» не дошло до сервера из-за сети
  if (!me.onboarded) {
    const key = `ez.onb.${me.id}`;
    let doneLocally = false;
    try { doneLocally = localStorage.getItem(key) === '1'; } catch {}
    if (!doneLocally) {
      await runOnboarding(app);
      try { localStorage.setItem(key, '1'); } catch {}
    }
    const retry = (n) => api.completeOnboarding().catch(() => { if (n > 0) setTimeout(() => retry(n - 1), 3000); });
    retry(3);
  }

  renderShell();
  switchTab(state.tab);
}

boot();
