import { api } from '../api.js';
import { Button, CloseButton, ErrorState, Quote, Skeleton } from '../components.js';
import { TgEmoji } from '../emoji.js';
import { openOverlay } from '../nav.js';
import { state } from '../store.js';
import { copyText, haptic, shareLink } from '../tg.js';
import { h, icon, plural, toast } from '../ui.js';

const Stat = (value, label) => h('div', { class: 'stat' }, h('b', { class: 'stat__value' }, String(value)), h('span', { class: 'stat__label' }, label));

export function openReferral() {
  openOverlay({
    kind: 'screen',
    label: 'реферальная программа',
    className: 'layer--referral',
    render: ({ close }) => {
      const body = h('div', { class: 'referral__body' }, Skeleton('skeleton--hero'), Skeleton('skeleton--line'));
      load();
      return [CloseButton(close), body];

      async function load() {
        try {
          paint(await api.referral());
        } catch {
          body.replaceChildren(ErrorState({ onRetry: load }));
        }
      }

      function paint(r) {
        const word = plural(r.reward, 'генерация', 'генерации', 'генераций');
        const copy = async () => {
          const ok = await copyText(r.link);
          haptic.tap();
          toast(ok ? 'Ссылка скопирована' : 'Не получилось скопировать', ok ? 'info' : 'warn');
        };
        body.replaceChildren(
          h('div', { class: 'hero-card' },
            h('span', { class: 'hero-card__emoji' }, TgEmoji('friends')),
            h('div', { class: 'hero-card__num' }, String(r.reward)),
            h('h1', { class: 'hero-card__title' }, `${word} за друга`)),
          Quote({ emoji: 'friends', title: 'Как получить',
            text: `пригласи друга по своей ссылке. Когда он соберёт свои первые эмодзи, ${r.reward} ${word} придут тебе.` }),
          Quote({ emoji: 'gift', title: 'Другу', text: 'первая генерация в подарок, как и всем новичкам.' }),
          h('div', { class: 'stats' },
            Stat(r.invited, 'приглашено'), Stat(r.created_pack, 'создали пак'), Stat(r.earned, 'получено генераций')),
          h('div', { class: 'linkbox' },
            h('div', { class: 'linkbox__main' },
              h('span', { class: 'linkbox__label' }, 'Твоя ссылка'),
              h('span', { class: 'linkbox__url' }, r.link.replace(/^https:\/\//, ''))),
            h('button', { class: 'linkbox__copy', type: 'button', 'aria-label': 'скопировать ссылку', onclick: copy }, icon('copy'))),
          h('div', { class: 'referral__cta' },
            Button({ label: 'Пригласить друзей', icon: 'share', onClick: () => shareLink(r.link, `Собери свои эмодзи и стикеры в ${state.me.brand}: первая генерация в подарок`) })));
      }
    },
  });
}
