import { api, ApiError } from './api.js';
import { ErrorState } from './components.js';
import { closeAll, mountDevFrame } from './nav.js';
import { setTabHandler } from './router.js';
import { bus, loadPrefs, setMe, state } from './store.js';
import { TabBar } from './tabbar.js';
import { initTelegram } from './tg.js';
import { h } from './ui.js';
import { mountAdmin } from './views/admin.js';
import { mountOrder } from './views/customize.js';
import { mountHome } from './views/home.js';
import { runOnboarding } from './views/onboarding.js';
import { mountProfile } from './views/profile.js';

const app = document.getElementById('app');

const TABS = [
  { id: 'home', label: 'Главная', icon: 'home', mount: mountHome },
  { id: 'order', label: 'Заказать', icon: 'bag', mount: mountOrder },
  { id: 'profile', label: 'Мой профиль', icon: 'user-circle', mount: mountProfile },
  { id: 'admin', label: 'Админка', icon: 'crown', mount: mountAdmin, admin: true },
];

let tabs = TABS;
let tabbar = null;
let cleanup = null;

// счётчик выбранных эмодзи на вкладке «Заказать»
function OrderBadge() {
  const el = h('span', { class: 'tab__badge', 'aria-hidden': 'true' });
  let last = -1;
  const update = () => {
    const n = state.selection.length;
    el.hidden = n === 0;
    el.textContent = n > 99 ? '99+' : String(n);
    if (last !== -1 && n !== last) {
      el.classList.remove('bump');
      void el.offsetWidth; // перезапуск анимации
      el.classList.add('bump');
    }
    last = n;
  };
  bus.on('selection', update);
  update();
  return el;
}

function renderShell() {
  tabs = TABS.filter((t) => !t.admin || state.me.is_admin);
  if (!tabs.some((t) => t.id === state.tab)) state.tab = tabs[0].id;
  const view = h('div', { class: 'view', id: 'view' });
  tabbar = TabBar({
    tabs: tabs.map((t) => ({ ...t, extra: t.id === 'order' ? OrderBadge() : null })),
    active: state.tab,
    onSelect: switchTab,
  });
  app.replaceChildren(view, h('div', { class: 'dock' }, tabbar.el));
  setTabHandler(switchTab);
}

function switchTab(id) {
  const tab = tabs.find((t) => t.id === id) || tabs[0];
  closeAll();
  state.tab = tab.id;
  tabbar.setActive(tab.id);
  if (cleanup) { cleanup(); cleanup = null; }
  const view = document.getElementById('view');
  view.replaceChildren();
  view.scrollTop = 0;
  view.dataset.tab = tab.id;
  cleanup = tab.mount(view) || null;
}

function fatal(err) {
  const expired = err instanceof ApiError && err.status === 401;
  app.replaceChildren(ErrorState({
    title: expired ? 'Сессия устарела' : 'Не получилось запуститься',
    text: expired ? 'Закрой приложение и открой его заново из бота' : 'Проверь соединение и попробуй ещё раз',
    onRetry: () => location.reload(),
  }));
}

async function boot() {
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
