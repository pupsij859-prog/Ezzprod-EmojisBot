// Приглашение друзей: баннер на голубом CRT Warp (как на главной), три шага, статистика, ссылка.
// Условие награды — как на сервере (services._maybe_reward_referrer): друг собрал пак И сделал покупку.
import { api } from '../api.js';
import { BigEmoji, Button, CloseButton, ErrorState, Skeleton } from '../components.js';
import { mountCrtWarp } from '../crt.js';
import { openOverlay } from '../nav.js';
import { state } from '../store.js';
import { copyText, haptic, shareLink } from '../tg.js';
import { h, icon, plural, toast } from '../ui.js';

const Stat = (value, label) => h('div', { class: 'stat' }, h('b', { class: 'stat__value' }, String(value)), h('span', { class: 'stat__label' }, label));

function Banner(reward) {
  const fx = h('div', { class: 'hero__fx', 'aria-hidden': 'true' });
  const el = h('section', { class: 'hero rhero' }, fx,
    h('div', { class: 'rhero__text' },
      h('span', { class: 'rhero__num' }, `+${reward}`),
      h('h1', { class: 'hero__title rhero__title' }, `${plural(reward, 'генерация', 'генерации', 'генераций')} за друга`)),
    BigEmoji('friends', 'rhero__emoji'));
  return { el, start: () => mountCrtWarp(fx) };
}

export function openReferral() {
  let stopFx = () => {};
  openOverlay({
    kind: 'screen',
    label: 'пригласить друзей',
    className: 'layer--referral',
    onClose: () => stopFx(),
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
        const copy = async () => {
          const ok = await copyText(r.link);
          haptic.tap();
          toast(ok ? 'Ссылка скопирована' : 'Не получилось скопировать', ok ? 'info' : 'warn');
        };
        const banner = Banner(r.reward);
        body.replaceChildren(...[
          banner.el,
          h('ol', { class: 'rsteps' },
            h('li', null, h('b', null, 'Отправь ссылку'), h('span', null, 'другу или в чат')),
            h('li', null, h('b', null, 'Друг собирает пак'), h('span', null, 'первая генерация у него в подарок')),
            h('li', null, h('b', null, `Тебе +${r.reward}`), h('span', null, 'когда он купит генерации'))),
          h('div', { class: 'stats' },
            Stat(r.invited, 'пришли'), Stat(r.created_pack, 'собрали пак'), Stat(r.earned, 'получено')),
          // ссылки нет, пока бот не подключён к Telegram (не знаем его имя)
          r.link ? h('div', { class: 'linkbox' },
            h('div', { class: 'linkbox__main' },
              h('span', { class: 'linkbox__label' }, 'Твоя ссылка'),
              h('span', { class: 'linkbox__url' }, r.link.replace(/^https:\/\//, ''))),
            h('button', { class: 'linkbox__copy', type: 'button', 'aria-label': 'скопировать ссылку', onclick: copy }, icon('copy')))
            : h('p', { class: 'order__hint' }, 'Ссылка появится, когда бот подключится к Telegram'),
          r.link ? h('div', { class: 'referral__cta' },
            Button({ label: 'Пригласить', icon: 'share', onClick: () => shareLink(r.link, `Свои эмодзи и стикеры с ником — ${state.me.brand}. Первая генерация бесплатно`) })) : null,
        ].filter(Boolean));
        stopFx();
        requestAnimationFrame(() => { if (banner.el.isConnected) stopFx = banner.start(); });
      }
    },
  });
}
