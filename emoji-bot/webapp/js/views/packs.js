// Блок «Мои паки» в профиле: список собранных паков, прогресс сборки, кнопка «открыть».
import { api, previewUrl } from '../api.js';
import { Button, ErrorState, Skeleton } from '../components.js';
import { goTab } from '../router.js';
import { openLink } from '../tg.js';
import { emojiWord, h, sleep } from '../ui.js';

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

function dateLabel(ts) {
  const d = new Date(ts * 1000);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

function PackCard(pack) {
  const thumbs = h('div', { class: 'pack__thumbs', 'aria-hidden': 'true' },
    pack.preview.slice(0, 4).map((id) => h('img', { src: previewUrl(id), alt: '', loading: 'lazy', decoding: 'async' })));
  const kind = pack.type === 'sticker' ? 'стикеры' : 'эмодзи';
  let status;
  if (pack.status === 'processing') {
    status = h('div', { class: 'pack__progress', role: 'progressbar', 'aria-valuenow': Math.round(pack.progress * 100), 'aria-valuemin': 0, 'aria-valuemax': 100 },
      h('i', { style: { width: `${Math.round(pack.progress * 100)}%` } }));
  } else if (pack.status === 'failed') {
    status = h('span', { class: 'badge badge--error' }, 'не собрался');
  } else {
    status = pack.link ? Button({ label: 'Открыть', variant: 'secondary', full: false, icon: 'share', onClick: () => openLink(pack.link) }) : null;
  }
  return h('li', { class: `pack pack--${pack.status}` },
    thumbs,
    h('div', { class: 'pack__info' },
      h('h3', { class: 'pack__title' }, pack.title),
      h('p', { class: 'pack__meta' }, `${kind} · ${emojiWord(pack.count, pack.type)} · ${dateLabel(pack.created_at)}`),
      pack.status === 'processing' ? h('p', { class: 'pack__status' }, 'собирается…') : null),
    status);
}

/** → { el, cleanup } */
export function PacksSection() {
  let alive = true;
  const list = h('ul', { class: 'packs', 'aria-label': 'мои паки' });
  const box = h('div', { class: 'packs-box' }, ...Array.from({ length: 2 }, () => Skeleton('skeleton--pack')));

  async function load() {
    try {
      const { packs } = await api.packs();
      if (!alive) return;
      paint(packs);
      if (packs.some((p) => p.status === 'processing')) {
        await sleep(1200);
        if (alive) load();
      }
    } catch {
      if (alive) box.replaceChildren(ErrorState({ onRetry: load }));
    }
  }

  function paint(packs) {
    if (!packs.length) {
      box.replaceChildren(h('div', { class: 'packs-empty' },
        h('p', null, 'Паков пока нет. Выбери эмодзи и собери первый'),
        Button({ label: 'К эмодзи', full: false, onClick: () => goTab('home') })));
      return;
    }
    list.replaceChildren(...packs.map(PackCard));
    box.replaceChildren(list);
  }

  load();
  return { el: box, cleanup: () => { alive = false; } };
}
