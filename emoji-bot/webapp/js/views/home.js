// Главная: шапка, приветственный баннер, «Шаблоны» с выбором категории и сетка анимированных шаблонов
// с бесконечной прокруткой. Тап по карточке добавляет шаблон в заказ. Ник, логотип и цвет надписи
// пользователя сразу видны на всех шаблонах (js/tgs.js), проигрываются только карточки на экране.
import { api } from '../api.js';
import { ErrorState, SectionHead, Skeleton, Topbar } from '../components.js';
import { goTab } from '../router.js';
import { bus, clearSelection, isSelected, limitFor, savePrefs, state, toggleSelection } from '../store.js';
import { haptic } from '../tg.js';
import { mountCrtWarp } from '../crt.js';
import { currentLook, isPersonalized, TgsPreview, watchVisibility } from '../tgs.js';
import { cap, h, icon, toast } from '../ui.js';
import { openCategories } from './categories.js';
import { KitsStrip } from './kits.js';

const categoryName = () => (state.categories.find((c) => c.id === state.category) || { name: '' }).name;

// баннер добавления в готовый пак на главной
function TargetPackBanner(onCancel) {
  const box = h('div', { class: 'tp-banner' });
  const render = () => {
    const pack = state.targetPack;
    if (!pack) {
      box.hidden = true;
      box.replaceChildren();
      return;
    }
    box.hidden = false;
    const isSticker = pack.type === 'sticker';
    const title = h('b', { class: 'tp-banner__title' }, pack.title);
    const kind = isSticker ? 'стикеров' : 'эмодзи';
    const sub = h('span', { class: 'tp-banner__sub' }, `Выбор ${kind} для добавления в пак`);
    const cancelBtn = h('button', {
      class: 'tp-banner__cancel',
      type: 'button',
      onclick: (e) => {
        e.stopPropagation();
        haptic.tap();
        onCancel();
      },
    }, 'Отмена ✕');
    box.replaceChildren(
      h('div', { class: 'tp-banner__icon' }, icon('plus')),
      h('div', { class: 'tp-banner__text' }, title, sub),
      cancelBtn);
  };
  render();
  return { el: box, render };
}

// баннер: голубой CRT Warp на фоне и приветствие
function Hero(me) {
  const name = (me.first_name || me.username || 'друг').trim();
  const fx = h('div', { class: 'hero__fx', 'aria-hidden': 'true' });
  const el = h('section', { class: 'hero' }, fx,
    h('h1', { class: 'hero__title' }, h('span', null, 'Добро пожаловать,'), h('span', null, name)));
  return { el, start: () => mountCrtWarp(fx) };
}

function Card(item, index, player) {
  const inPack = Boolean(
    state.targetPack &&
    ((state.targetPack.items && state.targetPack.items.includes(item.id)) ||
     (state.targetPack.preview && state.targetPack.preview.includes(item.id)))
  );
  const selected = !inPack && isSelected(item.id);
  const btn = h('button', {
    class: `tcard${selected ? ' is-selected' : ''}${inPack ? ' is-in-pack' : ''}`, type: 'button', role: 'listitem',
    'aria-pressed': String(selected), 'aria-label': item.name,
    dataset: { id: item.id }, style: { '--i': String(index % 30) },
    onclick: () => {
      if (inPack) {
        haptic.error();
        toast('Этот элемент уже добавлен в пак', 'warn');
        return;
      }
      const result = toggleSelection(item.id);
      if (result === 'limit') {
        haptic.error();
        toast(`Максимум ${limitFor(state.type)} в одном паке`, 'warn');
      } else {
        const on = result === 'added';
        btn.classList.toggle('is-selected', on);
        btn.setAttribute('aria-pressed', String(on));
        haptic.select();
      }
    },
  }, player.el,
  inPack ? h('span', { class: 'tcard__badge' }, 'В паке') : h('span', { class: 'tcard__check' }, icon('check')));
  return btn;
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
  const cancelTargetPack = () => {
    const pack = state.targetPack;
    state.targetPack = null;
    clearSelection();
    bus.emit('targetPack');
    bus.emit('selection');
    goTab('packs');
    if (pack) {
      import('./pack_details.js').then((m) => m.openPackDetails(pack)).catch(() => {});
    }
  };
  const targetBanner = TargetPackBanner(cancelTargetPack);
  // «Наборы»: готовые подборки шаблонов в одном стиле; при добавлении в готовый пак не показываем
  const kits = KitsStrip();
  const kitsHead = SectionHead('Наборы', h('span', { class: 'section-head__note' }, 'стиль в один тап'));
  const paintKits = () => { kitsHead.hidden = Boolean(state.targetPack); kits.el.hidden = Boolean(state.targetPack); };
  paintKits();
  bus.on('targetPack', () => {
    if (!alive) return;
    targetBanner.render();
    paintKits();
    reset();
  });

  container.append(h('section', { class: 'screen screen--home' },
    Topbar(),
    hero.el,
    targetBanner.el,
    kitsHead, kits.el,
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

  // выбор: обновляем точечно (O(1)) при клике или массово только при полном сбросе
  const offSelection = bus.on('selection', (evt) => {
    if (evt && evt.id) {
      const el = grid.querySelector(`.tcard[data-id="${evt.id}"]`);
      if (el) {
        el.classList.toggle('is-selected', evt.selected);
        el.setAttribute('aria-pressed', String(evt.selected));
      }
      return;
    }
    grid.querySelectorAll('.tcard[data-id]').forEach((el) => {
      const on = isSelected(el.dataset.id);
      el.classList.toggle('is-selected', on);
      el.setAttribute('aria-pressed', String(on));
    });
  });
  let timer = 0;
  const offText = bus.on('personalization', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { refreshLook(); kits.refresh(); }, 250);
  });

  reset();
  refreshLook();
  const stopFx = hero.start();
  return () => {
    alive = false;
    clearTimeout(timer);
    stopFx();
    kits.destroy();
    observer.disconnect();
    seen.disconnect();
    players.forEach((p) => p.destroy());
    offSelection();
    offText();
  };
}
