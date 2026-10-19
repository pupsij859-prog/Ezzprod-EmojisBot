// Вкладка «Паки»: собранные пользователем паки (эмодзи или стикеры).
// Тап по паку открывает его «как в чате Telegram» (views/chat.js).
import { api, previewUrl } from '../api.js';
import { Button, Chips, ErrorState, SectionHead, Skeleton, Topbar } from '../components.js';
import { goTab } from '../router.js';
import { confirmAction, haptic } from '../tg.js';
import { emojiWord, h, icon, sleep, toast } from '../ui.js';
import { openPackChat, userChatPack } from './chat.js';
import { openPackDetails } from './pack_details.js';

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

function dateLabel(ts) {
  const d = new Date(ts * 1000);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

function MyPackRow(pack, onDelete) {
  const thumbs = h('div', { class: 'pack__thumbs', 'aria-hidden': 'true' },
    (pack.items || pack.preview).slice(0, 4).map((id) => h('img', { src: previewUrl(id), alt: '', loading: 'lazy', decoding: 'async' })));
  const kind = pack.type === 'sticker' ? 'стикеры' : 'эмодзи';
  let status;
  if (pack.status === 'processing') {
    status = h('div', { class: 'pack__progress', role: 'progressbar', 'aria-valuenow': Math.round(pack.progress * 100), 'aria-valuemin': 0, 'aria-valuemax': 100 },
      h('i', { style: { width: `${Math.round(pack.progress * 100)}%` } }));
  } else if (pack.status === 'failed') {
    status = h('span', { class: 'badge badge--error' }, 'не собрался');
  } else {
    status = icon('chev-right', 'pcard__chev');
  }
  const ready = pack.status === 'ready';

  const delBtn = pack.status !== 'processing' && onDelete
    ? h('button', {
      class: 'pack__del',
      type: 'button',
      'aria-label': `удалить пак ${pack.title}`,
      onclick: async (e) => {
        e.stopPropagation();
        haptic.select();
        const ok = await confirmAction(`Удалить «${pack.title}» из списка?`);
        if (!ok) return;
        onDelete(pack);
      },
    }, icon('trash'))
    : null;

  const main = h('button', {
    class: 'pack__main',
    type: 'button',
    onclick: () => {
      haptic.tap();
      openPackDetails(pack, { onDelete: () => onDelete && onDelete(pack) });
    },
  },
  thumbs,
  h('div', { class: 'pack__info' },
    h('h3', { class: 'pack__title' }, pack.title),
    h('p', { class: 'pack__meta' }, `${kind} · ${emojiWord(pack.count, pack.type)} · ${dateLabel(pack.created_at)}`),
    pack.status === 'processing' ? h('p', { class: 'pack__status' }, 'собирается…') : null),
  status);

  return h('div', { class: `pack pack--${pack.status}`, dataset: { type: pack.type } }, main, delBtn);
}

export function mountPacks(container) {
  let alive = true;
  let filter = 'all';
  const mineBox = h('div', { class: 'packs-box' }, Skeleton('skeleton--pack'), Skeleton('skeleton--pack'));
  const mineTitle = h('h3', { class: 'section-title', id: 'my-packs' }, 'Мои паки');

  const applyFilter = () => {
    container.querySelectorAll('[data-type]').forEach((el) => {
      el.hidden = filter !== 'all' && el.dataset.type !== 'any' && el.dataset.type !== filter;
    });
  };
  const chips = Chips([{ id: 'all', label: 'Все' }, { id: 'emoji', label: 'Эмодзи' }, { id: 'sticker', label: 'Стикеры' }],
    filter, (id) => { filter = id; applyFilter(); });

  container.append(h('section', { class: 'screen screen--packs' },
    Topbar(),
    SectionHead('Паки'),
    h('div', { class: 'page' }, chips.el, mineTitle, mineBox)));

  async function handleDelete(pack) {
    try {
      await api.deletePack(pack.id);
      haptic.success();
      toast('Пак удалён');
      await loadMine();
    } catch {
      haptic.error();
      toast('Не удалось удалить пак', 'warn');
    }
  }

  async function loadMine() {
    try {
      const { packs } = await api.packs();
      if (!alive) return;
      if (!packs.length) {
        mineBox.replaceChildren(h('div', { class: 'packs-empty', dataset: { type: 'any' } },
          h('p', null, 'Своих паков пока нет. Выбери шаблоны на главной и собери первый'),
          Button({ label: 'К шаблонам', full: false, onClick: () => goTab('home') })));
      } else {
        mineBox.replaceChildren(h('div', { class: 'packs' }, ...packs.map((p) => MyPackRow(p, handleDelete))));
      }
      applyFilter();
      if (packs.some((p) => p.status === 'processing')) {
        await sleep(1200);
        if (alive) loadMine();
      }
    } catch {
      if (alive) mineBox.replaceChildren(ErrorState({ onRetry: loadMine }));
    }
  }

  loadMine();
  return () => { alive = false; };
}
