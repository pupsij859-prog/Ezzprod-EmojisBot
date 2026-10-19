// Главная: шапка, приветственный баннер, «Шаблоны» с выбором категории и сетка анимированных шаблонов
// с бесконечной прокруткой. Тап по карточке добавляет шаблон в заказ. Ник, логотип и цвет надписи
// пользователя сразу видны на всех шаблонах (js/tgs.js), проигрываются только карточки на экране.
import { api } from '../api.js';
import { ErrorState, SectionHead, Skeleton, Topbar } from '../components.js';
import { bus, isSelected, limitFor, savePrefs, state, toggleSelection } from '../store.js';
import { haptic } from '../tg.js';
import { mountCrtWarp } from '../crt.js';
import { currentLook, isPersonalized, TgsPreview, watchVisibility } from '../tgs.js';
import { cap, h, icon, toast } from '../ui.js';
import { openCategories } from './categories.js';

const categoryName = () => (state.categories.find((c) => c.id === state.category) || { name: '' }).name;

// баннер: голубой CRT Warp на фоне и приветствие
function Hero(me) {
  const name = (me.first_name || me.username || 'друг').trim();
  const fx = h('div', { class: 'hero__fx', 'aria-hidden': 'true' });
  const el = h('section', { class: 'hero' }, fx,
    h('h1', { class: 'hero__title' }, h('span', null, 'Добро пожаловать,'), h('span', null, name)));
  return { el, start: () => mountCrtWarp(fx) };
}

function Card(item, index, player) {
  const selected = isSelected(item.id);
  return h('button', {
    class: `tcard${selected ? ' is-selected' : ''}`, type: 'button', role: 'listitem',
    'aria-pressed': String(selected), 'aria-label': item.name,
    dataset: { id: item.id }, style: { '--i': String(index % 30) },
    onclick: () => {
      const result = toggleSelection(item.id);
      if (result === 'limit') {
        haptic.error();
        toast(`Максимум ${limitFor(state.type)} в одном паке`, 'warn');
      } else {
        haptic.select();
      }
    },
  }, player.el,
  h('span', { class: 'tcard__check' }, icon('check')));
}

const skeletons = (n) => Array.from({ length: n }, () => Skeleton('tcard tcard--skeleton'));

export function mountHome(container) {
  let alive = true;
  let requestId = 0;
  let loading = false;
  let page = 0;
  let pages = 1;

  const grid = h('div', { class: 'tgrid', role: 'list', 'aria-label': 'шаблоны' });
  const sentinel = h('div', { class: 'tgrid__sentinel', 'aria-hidden': 'true' });
  const pillLabel = h('span');
  const pill = h('button', { class: 'catpill', type: 'button', 'aria-label': 'выбрать категорию', onclick: () => {
    haptic.tap();
    openCategories((id) => {
      if (id === state.category) return;
      state.category = id;
      savePrefs();
      reset();
      container.scrollTo({ top: grid.offsetTop - 120, behavior: 'smooth' });
    });
  } }, pillLabel, icon('chev-right', 'catpill__chev'));

  const seen = watchVisibility(container);
  let players = [];
  let look = null;
  let lookVersion = 0;
  const refreshLook = async () => {
    const mine = ++lookVersion;
    const next = isPersonalized() ? await currentLook({ own: true }) : null;
    if (mine !== lookVersion || !alive) return;
    look = next;
    players.forEach((p) => p.update(look));
  };

  const hero = Hero(state.me);
  container.append(h('section', { class: 'screen screen--home' },
    Topbar(),
    hero.el,
    SectionHead('Шаблоны', pill),
    grid, sentinel));

  async function loadMore() {
    if (loading || page >= pages || !alive) return;
    loading = true;
    const mine = requestId;
    const first = page === 0;
    if (!first) grid.append(...skeletons(3));
    try {
      const data = await api.templates(state.category, page + 1);
      if (mine !== requestId || !alive) return;
      grid.querySelectorAll('.tcard--skeleton').forEach((n) => n.remove());
      const cards = data.items.map((item, i) => {
        const player = TgsPreview(item.id, { look, lazy: true, cls: 'tgs--card' });
        players.push(player);
        seen.add(player);
        return Card(item, i, player);
      });
      grid.append(...cards);
      page = data.page;
      pages = data.pages;
    } catch {
      if (mine !== requestId || !alive) return;
      grid.querySelectorAll('.tcard--skeleton').forEach((n) => n.remove());
      if (first) grid.replaceChildren(ErrorState({ onRetry: reset }));
      else toast('Не получилось загрузить ещё, прокрути ниже ещё раз', 'warn');
    } finally {
      if (mine === requestId) loading = false;
    }
    // короткая страница: «маяк» всё ещё на экране, наблюдатель второй раз не сработает
    requestAnimationFrame(() => {
      if (!alive || mine !== requestId) return;
      if (sentinel.getBoundingClientRect().top < container.clientHeight + 600) loadMore();
    });
  }

  function reset() {
    requestId += 1;
    loading = false;
    page = 0;
    pages = 1;
    pillLabel.textContent = cap(categoryName());
    players.forEach((p) => p.destroy());
    players = [];
    grid.replaceChildren(...skeletons(12));
    loadMore();
  }

  const observer = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) loadMore();
  }, { root: container, rootMargin: '0px 0px 600px 0px' });
  observer.observe(sentinel);

  // выбор: обновляем только классы, без перерисовки сетки
  const offSelection = bus.on('selection', () => {
    grid.querySelectorAll('.tcard[data-id]').forEach((el) => {
      const on = isSelected(el.dataset.id);
      el.classList.toggle('is-selected', on);
      el.setAttribute('aria-pressed', String(on));
    });
  });
  let timer = 0;
  const offText = bus.on('personalization', () => { clearTimeout(timer); timer = setTimeout(refreshLook, 250); });

  reset();
  refreshLook();
  const stopFx = hero.start();
  return () => {
    alive = false;
    clearTimeout(timer);
    stopFx();
    observer.disconnect();
    seen.disconnect();
    players.forEach((p) => p.destroy());
    offSelection();
    offText();
  };
}
