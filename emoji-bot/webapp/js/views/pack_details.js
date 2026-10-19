// Экран просмотра и управления паком (в точности по референсу из скриншотов):
// - Шапка: < пак, справа бейдж баланса (✨ 0)
// - Главная карточка: название, подзаголовок (эмодзи-пак / стикер-пак · @ezzsticker_bot),
//   строки: «открыть в тг», «добавить эмодзи / ещё 198», «симуляция в чате»
// - Секция «содержимое пака»: сетка карточек 2х2 с анимированными стикерами и красной кнопкой удаления у каждого
// - Снизу большая кнопка «удалить пак» (красный текст)
import { api, previewUrl } from '../api.js';
import { BalanceChip, Button } from '../components.js';
import { openOverlay } from '../nav.js';
import { goTab } from '../router.js';
import { bus, clearSelection, refreshMe, state } from '../store.js';
import { confirmAction, haptic, openLink } from '../tg.js';
import { TgsPreview } from '../tgs.js';
import { cap, emojiWord, gens, h, icon, plural, toast } from '../ui.js';
import { openBuy } from './buy.js';
import { openPackChat, userChatPack } from './chat.js';

const BOT_USERNAME = 'ezzsticker_bot';



/**
 * Экран управления и просмотра пака
 */
export function openPackDetails(initialPack, options = {}) {
  let alive = true;
  let pack = { ...initialPack };
  if (!pack.items || !pack.items.length) {
    pack.items = pack.preview ? [...pack.preview] : [];
  }

  openOverlay({
    kind: 'screen',
    label: pack.title,
    className: 'layer--pack-screen',
    onClose: () => {
      alive = false;
    },
    render: ({ close }) => {
      const isSticker = pack.type === 'sticker';
      const kindLabel = isSticker ? 'стикер-пак' : 'эмодзи-пак';
      const maxSlots = isSticker ? (state.me?.limits?.sticker_pack_max || 120) : (state.me?.limits?.emoji_pack_max || 200);

      // ── Шапка: < пак       ✨ 0
      const backBtn = h('button', {
        class: 'pack-topbar__back',
        type: 'button',
        'aria-label': 'назад',
        onclick: () => { haptic.tap(); close(); },
      }, icon('chev-left'));

      const topTitle = h('span', { class: 'pack-topbar__title' }, 'пак');
      const topHead = h('div', { class: 'pack-topbar__left' }, backBtn, topTitle);

      const balBtn = BalanceChip(() => {
        haptic.tap();
        openBuy();
      });

      const topbar = h('header', { class: 'pack-topbar' }, topHead, balBtn);

      // ── Главная карточка (Pack Summary Card)
      const cardTitle = h('h2', { class: 'pack-card__title' }, pack.title);
      const cardSub = h('p', { class: 'pack-card__sub' }, `${kindLabel} · @${BOT_USERNAME}`);
      const cardHead = h('div', { class: 'pack-card__head' }, cardTitle, cardSub);

      // Строка 1: открыть в тг
      const rowTg = h('button', {
        class: 'pack-row',
        type: 'button',
        onclick: () => {
          haptic.tap();
          if (pack.link) {
            openLink(pack.link);
          } else {
            toast('Пак ещё собирается…');
          }
        },
      },
      h('span', { class: 'pack-row__icon pack-row__icon--tg' }, icon('send')),
      h('span', { class: 'pack-row__label' }, 'открыть в тг'),
      icon('chev-right', 'pack-row__chev'));

      // Строка 2: добавить эмодзи / добавить стикеры
      const remainingLabel = h('span', { class: 'pack-row__extra' });
      const rowAdd = h('button', {
        class: 'pack-row',
        type: 'button',
        onclick: () => {
          haptic.tap();
          state.targetPack = pack;
          state.type = pack.type;
          clearSelection();
          if (pack.params) {
            if (pack.params.text) state.text = pack.params.text;
            if (pack.params.color) state.color = pack.params.color;
            if (pack.params.stroke) state.stroke = pack.params.stroke;
          }
          close();
          goTab('home');
          bus.emit('targetPack');
          bus.emit('selection');
        },
      },
      h('span', { class: 'pack-row__icon pack-row__icon--plus' }, icon('plus')),
      h('span', { class: 'pack-row__label' }, `добавить ${isSticker ? 'стикеры' : 'эмодзи'}`),
      h('span', { class: 'pack-row__right' }, remainingLabel, icon('chev-right', 'pack-row__chev')));

      // Строка 3: симуляция в чате
      const rowChat = h('button', {
        class: 'pack-row',
        type: 'button',
        onclick: () => {
          haptic.tap();
          openPackChat(userChatPack(pack));
        },
      },
      h('span', { class: 'pack-row__icon pack-row__icon--chat' }, icon('chat')),
      h('span', { class: 'pack-row__label' }, 'симуляция в чате'),
      icon('chev-right', 'pack-row__chev'));

      const packCard = h('div', { class: 'pack-card' },
        cardHead,
        h('div', { class: 'pack-card__div' }),
        rowTg,
        h('div', { class: 'pack-card__div' }),
        rowAdd,
        h('div', { class: 'pack-card__div' }),
        rowChat);

      // ── Секция «содержимое пака»
      const countNum = h('span', { class: 'pack-sec__count' });
      const secHead = h('div', { class: 'pack-sec__head' },
        h('span', { class: 'pack-sec__title' }, 'содержимое пака'),
        countNum);

      const gridBox = h('div', { class: 'pack-grid' });
      const secBox = h('div', { class: 'pack-sec' }, secHead, gridBox);

      // ── Большая красная кнопка «удалить пак» снизу
      const delPackBtn = h('button', {
        class: 'pack-screen__del-btn',
        type: 'button',
        onclick: async () => {
          haptic.select();
          const ok = await confirmAction(`Удалить «${pack.title}»?`);
          if (!ok) return;
          try {
            await api.deletePack(pack.id);
            haptic.success();
            toast('Пак удалён');
            close();
            if (options.onDelete) options.onDelete();
          } catch {
            haptic.error();
            toast('Не удалось удалить пак', 'warn');
          }
        },
      }, 'удалить пак');

      function renderGrid() {
        const items = pack.items || [];
        const count = items.length;
        const remaining = Math.max(0, maxSlots - count);
        remainingLabel.textContent = `ещё ${remaining}`;
        countNum.textContent = String(count);

        gridBox.replaceChildren(...items.map((tid) => {
          const player = TgsPreview(tid, { autoplay: true, lazy: false, cls: 'pack-item__tgs' });

          const delItemBtn = h('button', {
            class: 'pack-item__del',
            type: 'button',
            'aria-label': 'удалить из пака',
            onclick: async (e) => {
              e.stopPropagation();
              haptic.select();
              if (items.length <= 1) {
                toast('В паке должен остаться хотя бы 1 элемент', 'warn');
                return;
              }
              const ok = await confirmAction(`Удалить этот элемент из пака?`);
              if (!ok) return;
              try {
                const updated = await api.deletePackItem(pack.id, tid);
                haptic.success();
                toast('Элемент удалён');
                updatePackData(updated);
              } catch {
                haptic.error();
                toast('Не удалось удалить элемент', 'warn');
              }
            },
          }, icon('trash'));

          return h('div', {
            class: 'pack-item',
            onclick: () => {
              haptic.tap();
              openPackChat(userChatPack(pack, { initialId: tid }));
            },
          }, player.el, delItemBtn);
        }));
      }

      function updatePackData(updated) {
        pack = { ...pack, ...updated };
        if (!pack.items || !pack.items.length) {
          pack.items = updated.preview ? [...updated.preview] : pack.items;
        }
        cardTitle.textContent = pack.title;
        renderGrid();
        if (pack.status === 'processing') {
          pollPack();
        }
      }

      async function pollPack() {
        while (alive && pack.status === 'processing') {
          try {
            const updated = await api.pack(pack.id);
            if (!alive) return;
            pack = { ...pack, ...updated };
            renderGrid();
            if (pack.status === 'ready') {
              haptic.success();
              toast('Пак обновлён и готов!');
              break;
            }
          } catch {}
          await new Promise((r) => setTimeout(r, 1600));
        }
      }

      async function loadFull() {
        try {
          const full = await api.pack(pack.id);
          if (!alive) return;
          updatePackData(full);
        } catch {}
      }

      renderGrid();
      loadFull();

      const scrollBody = h('div', { class: 'pack-screen__body page' }, packCard, secBox, delPackBtn);
      return [topbar, scrollBody];
    },
  });
}
