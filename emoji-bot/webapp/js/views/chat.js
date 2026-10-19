// Пак «как в чате Telegram»: простая и понятная симуляция переписки с другом.
// Сверху сообщение «Покажи свой стикер!» от друга, ниже — твой анимированный стикер,
// и компактная строка стикеров снизу для моментальной отправки в чат.
import { previewUrl } from '../api.js';
import { openOverlay } from '../nav.js';
import { haptic } from '../tg.js';
import { lookFromParams, TgsPreview } from '../tgs.js';
import { emojiWord, h, icon, prefersReducedMotion } from '../ui.js';

const clock = (offsetMin = 0) => {
  const d = new Date(Date.now() + offsetMin * 60000);
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
};

/** Пак пользователя (из /api/packs) → общий вид для чата. */
export function userChatPack(pack, options = {}) {
  const sticker = pack.type === 'sticker';
  const text = pack.params && pack.params.text;
  let lookPromise = null;
  let cachedLook = null;
  if (pack.params && (pack.params.text || pack.params.color || pack.params.stroke)) {
    // сервер хранит шрифт как font_id, превью ждёт {font: {id}} — как в store.lookFor
    const p = { ...pack.params, font: pack.params.font_id ? { id: pack.params.font_id } : null };
    lookPromise = lookFromParams(p, true).then((l) => {
      cachedLook = l;
      return l;
    }).catch(() => null);
  }

  const rawItems = (pack.items && pack.items.length) ? pack.items : (pack.preview || []);
  const initialId = options.initialId || rawItems[0];

  return {
    title: pack.title,
    type: pack.type,
    sub: emojiWord(pack.count || rawItems.length, pack.type),
    initialId,
    items: rawItems.map((id) => ({
      key: id,
      node: (big) => {
        if (big) {
          const wrap = h('span', { class: sticker ? 'tgm__sticker' : 'tgm__emoji' });
          const player = TgsPreview(id, {
            autoplay: true,
            lazy: false,
            look: cachedLook,
            cls: sticker ? 'tgm__tgs-sticker' : 'tgm__tgs-emoji',
          });
          wrap.append(player.el);
          if (!cachedLook && lookPromise) {
            lookPromise.then((look) => {
              if (look) player.update(look);
            });
          }
          return wrap;
        }
        return h('span', { class: 'tgchat__shelf-thumb' },
          h('img', { src: previewUrl(id), alt: '', draggable: false, decoding: 'async', loading: 'lazy' }),
          text ? h('span', { class: 'tgm__nick' }, text) : null);
      },
    })),
  };
}

function Time({ out = true, chip = false, time = clock() } = {}) {
  return h('span', { class: `tgm__time${chip ? ' tgm__time--chip' : ''}` },
    time,
    out ? icon('checks', 'tgm__checks') : null);
}

// одиночный эмодзи или стикер: без пузыря, крупно, время на плашке
function MediaMessage(item, sticker) {
  return h('div', {
    class: `tgm tgm--out tgm--media${sticker ? ' tgm--sticker' : ''}`,
    onclick: () => haptic.tap(),
  },
  item.node(true),
  Time({ chip: true, out: true }));
}

function TextMessage({ out = false, text, time } = {}) {
  return h('div', { class: `tgm ${out ? 'tgm--out' : 'tgm--in'}` },
    h('div', { class: 'tgm__bubble' },
      h('span', { class: 'tgm__text' }, text),
      Time({ out, time: time || clock(out ? 0 : -1) })));
}

export function openPackChat(pack) {
  const sticker = pack.type === 'sticker';
  openOverlay({
    kind: 'screen',
    label: pack.title || 'Чат',
    className: 'layer--chat',
    render: ({ close }) => {
      const list = h('div', { class: 'tgchat__list' });
      const body = h('div', { class: 'tgchat__body' }, list);
      const scrollDown = (smooth) => body.scrollTo({
        top: body.scrollHeight,
        behavior: smooth && !prefersReducedMotion() ? 'smooth' : 'auto',
      });

      // ── шапка как в Telegram: Друг · в сети
      const head = h('header', { class: 'tgchat__head' },
        h('button', { class: 'tgchat__back', type: 'button', 'aria-label': 'назад', onclick: () => { haptic.tap(); close(); } }, icon('chev-left')),
        h('span', { class: 'tgchat__avatar', 'aria-hidden': 'true' },
          h('span', { class: 'tgchat__initial' }, 'Д')),
        h('span', { class: 'tgchat__title' },
          h('b', null, 'Друг'),
          h('span', { class: 'is-online' }, 'в сети')),
        h('span', { class: 'tgchat__more', 'aria-hidden': 'true' }, icon('dots')));

      // ── стартовые сообщения:
      // 1. Дата: "Сегодня"
      // 2. Входящее от друга: "Покажи свой стикер!"
      // 3. Исходящее: твой стикер уже отправлен
      const initialItem = pack.items.find((it) => it.key === pack.initialId) || pack.items[0];

      list.append(
        h('div', { class: 'tgchat__date' }, 'Сегодня'),
        TextMessage({
          out: false,
          text: sticker ? 'Покажи свой стикер!' : 'Покажи свои эмодзи!',
          time: clock(-1),
        })
      );

      if (initialItem) {
        list.append(MediaMessage(initialItem, sticker));
      }

      // ── компактная строка стикеров снизу для моментальной отправки
      const shelfList = h('div', { class: 'tgchat__shelf-list' },
        pack.items.map((it) => h('button', {
          class: 'tgchat__shelf-btn',
          type: 'button',
          'aria-label': 'отправить стикер',
          onclick: () => {
            haptic.tap();
            list.append(MediaMessage(it, sticker));
            requestAnimationFrame(() => scrollDown(true));
          },
        }, it.node(false))));

      const bottomBar = h('div', { class: 'tgchat__shelf-bar' },
        h('span', { class: 'tgchat__shelf-icon', 'aria-hidden': 'true' }, icon('smile')),
        shelfList);

      setTimeout(() => scrollDown(false), 50);
      return [head, body, bottomBar];
    },
  });
}
