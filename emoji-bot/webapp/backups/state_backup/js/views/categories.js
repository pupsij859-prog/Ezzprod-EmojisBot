// Шторка выбора категории шаблонов.
import { previewUrl } from '../api.js';
import { openOverlay } from '../nav.js';
import { state } from '../store.js';
import { haptic } from '../tg.js';
import { cap, h, icon } from '../ui.js';

export function openCategories(onPick) {
  openOverlay({
    kind: 'sheet',
    label: 'категории',
    className: 'sheet--cats',
    render: ({ close }) => [
      h('div', { class: 'sheet__grab' }, h('h2', { class: 'sheet__title' }, 'Категории')),
      h('div', { class: 'sheet__body' },
        h('ul', { class: 'catlist' }, state.categories.map((c) => {
          const active = c.id === state.category;
          return h('li', null, h('button', {
            class: `catrow${active ? ' is-active' : ''}`, type: 'button', 'aria-current': active ? 'true' : undefined,
            onclick: () => { haptic.select(); close(); onPick(c.id); },
          },
          h('span', { class: 'catrow__thumb' }, h('img', { src: previewUrl(c.cover), alt: '', decoding: 'async' })),
          h('span', { class: 'catrow__name' }, cap(c.name)),
          h('span', { class: 'catrow__count' }, String(c.count)),
          h('span', { class: 'catrow__mark' }, active ? icon('check') : null)));
        }))),
    ],
  });
}
