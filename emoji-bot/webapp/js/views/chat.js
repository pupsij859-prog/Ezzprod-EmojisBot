// Пак «как в чате Telegram»: шапка чата, обои с узором, сообщения (крупный эмодзи без пузыря,
// стикер, текст с эмодзи) и клавиатура пака снизу — тап по эмодзи «отправляет» его в чат.
// pack: { title, type: 'emoji' | 'sticker', items: [{ key, node(big) }], link?, sub }
import { previewUrl } from '../api.js';
import { TgEmoji } from '../emoji.js';
import { openOverlay } from '../nav.js';
import { haptic, openLink } from '../tg.js';
import { emojiWord, h, icon, prefersReducedMotion } from '../ui.js';

const clock = () => {
  const d = new Date();
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
};

/** Пак пользователя (из /api/packs) → общий вид для чата. */
export function userChatPack(pack) {
  const sticker = pack.type === 'sticker';
  const text = pack.params && pack.params.text;
  return {
    title: pack.title,
    type: pack.type,
    link: pack.link,
    sub: emojiWord(pack.count, pack.type),
    items: (pack.items || pack.preview).map((id) => ({
      key: id,
      node: (big) => h('span', { class: big ? (sticker ? 'tgm__sticker' : 'tgm__emoji') : 'tgk__emoji' },
        h('img', { src: previewUrl(id), alt: '', draggable: false, decoding: 'async' }),
        text ? h('span', { class: 'tgm__nick' }, text) : null),
    })),
  };
}

function Time({ out = true, chip = false } = {}) {
  return h('span', { class: `tgm__time${chip ? ' tgm__time--chip' : ''}` }, clock(),
    out ? icon('checks', 'tgm__checks') : null);
}

// одиночный эмодзи или стикер: без пузыря, крупно, время на плашке
function MediaMessage(item, sticker) {
  return h('div', { class: `tgm tgm--out tgm--media${sticker ? ' tgm--sticker' : ''}` },
    item.node(true), Time({ chip: true }));
}

function TextMessage({ out = false, children, buttons = null }) {
  return h('div', { class: `tgm ${out ? 'tgm--out' : 'tgm--in'}` },
    h('div', { class: 'tgm__bubble' }, ...children, Time({ out })),
    buttons);
}

export function openPackChat(pack) {
  const sticker = pack.type === 'sticker';
  openOverlay({
    kind: 'screen',
    label: pack.title,
    className: 'layer--chat',
    render: ({ close }) => {
      const cover = pack.items[0];
      const list = h('div', { class: 'tgchat__list' });
      const body = h('div', { class: 'tgchat__body' }, list);
      const scrollDown = (smooth) => body.scrollTo({ top: body.scrollHeight, behavior: smooth && !prefersReducedMotion() ? 'smooth' : 'auto' });

      // ── шапка как в Telegram
      const head = h('header', { class: 'tgchat__head' },
        h('button', { class: 'tgchat__back', type: 'button', 'aria-label': 'назад', onclick: () => { haptic.tap(); close(); } }, icon('chev-left')),
        h('span', { class: 'tgchat__avatar', 'aria-hidden': 'true' }, cover ? cover.node(false) : null),
        h('span', { class: 'tgchat__title' }, h('b', null, pack.title), h('span', null, pack.sub)),
        h('span', { class: 'tgchat__more', 'aria-hidden': 'true' }, icon('dots')));

      // ── стартовые сообщения
      const inline = pack.items.slice(0, 3).map((it) => h('span', { class: 'tgm__inline' }, it.node(false)));
      const openBtn = pack.link
        ? h('div', { class: 'tgm__buttons' }, h('button', { class: 'tgm__btn', type: 'button', onclick: () => openLink(pack.link) },
          sticker ? 'Добавить стикеры' : 'Добавить эмодзи'))
        : null;
      list.append(
        h('div', { class: 'tgchat__date' }, 'Сегодня'),
        TextMessage({
          children: [
            h('span', { class: 'tgm__inline' }, TgEmoji('gift')), ' ', h('b', { class: 'tgm__title' }, pack.title), h('br'),
            `${pack.sub}. Нажми на ${sticker ? 'стикер' : 'эмодзи'} внизу — он отправится в чат `, ...inline, h('br'),
            h('span', { class: 'tgm__inline' }, TgEmoji('fire')), ' Так пак будет выглядеть у тебя в Telegram',
          ],
          buttons: openBtn,
        }));
      if (cover) list.append(MediaMessage(cover, sticker));

      // ── клавиатура пака
      const grid = h('div', { class: `tgk__grid${sticker ? ' tgk__grid--stickers' : ''}` },
        pack.items.map((it) => h('button', { class: 'tgk__item', type: 'button', 'aria-label': 'отправить', onclick: () => {
          haptic.tap();
          list.append(MediaMessage(it, sticker));
          requestAnimationFrame(() => scrollDown(true));
        } }, it.node(false))));
      const panel = h('div', { class: 'tgk' },
        h('div', { class: 'tgk__tabs' },
          h('span', { class: 'tgk__tab is-active', 'aria-hidden': 'true' }, cover ? cover.node(false) : null),
          h('span', { class: 'tgk__name' }, pack.title)),
        grid);

      const input = h('div', { class: 'tgchat__input' },
        h('span', { class: 'tgchat__icon is-on', 'aria-hidden': 'true' }, icon('smile')),
        h('span', { class: 'tgchat__field' }, 'Сообщение'),
        h('span', { class: 'tgchat__icon', 'aria-hidden': 'true' }, icon('clip')),
        h('span', { class: 'tgchat__icon', 'aria-hidden': 'true' }, icon('mic')));

      requestAnimationFrame(() => scrollDown(false));
      return [head, body, input, panel];
    },
  });
}
