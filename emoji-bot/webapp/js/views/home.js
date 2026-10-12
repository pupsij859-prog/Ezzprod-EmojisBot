// Главная: шапка, приветственный баннер, «Шаблоны» с выбором категории и сетка шаблонов
// с бесконечной прокруткой. Тап по карточке добавляет шаблон в заказ.
import { api, previewUrl } from '../api.js';
import { ErrorState, SectionHead, Skeleton, Topbar } from '../components.js';
import { bus, isSelected, limitFor, savePrefs, state, toggleSelection } from '../store.js';
import { haptic } from '../tg.js';
import { cap, h, icon, toast } from '../ui.js';
import { openCategories } from './categories.js';

const categoryName = () => (state.categories.find((c) => c.id === state.category) || { name: '' }).name;

function Hero(me) {
  const name = (me.first_name || me.username || 'друг').trim();
  return h('section', { class: 'hero' },
    h('h1', { class: 'hero__title' }, h('span', null, 'Добро пожаловать,'), h('span', null, name)));
}

function Card(item, index) {
  const selected = isSelected(item.id);
  const img = h('img', { src: previewUrl(item.id), alt: '', loading: index < 12 ? 'eager' : 'lazy', decoding: 'async', draggable: false });
  img.addEventListener('error', () => img.classList.add('is-broken'));
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
  }, img,
  // свой текст пользователя показываем поверх карточки: так превью не зависит от ввода и хорошо кэшируется
  h('span', { class: 'tcard__text', 'aria-hidden': 'true' }, state.text),
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

  container.append(h('section', { class: 'screen screen--home' },
    Topbar(),
    Hero(state.me),
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
      grid.append(...data.items.map(Card));
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
  const offText = bus.on('personalization', () => {
    grid.querySelectorAll('.tcard__text').forEach((el) => { el.textContent = state.text; });
  });

  reset();
  return () => { alive = false; observer.disconnect(); offSelection(); offText(); };
}
