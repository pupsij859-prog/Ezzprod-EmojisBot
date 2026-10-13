// Экран сборки пака: прогресс → «пак готов». Сборка идёт на сервере и не зависит от этого экрана.
import { api } from '../api.js';
import { BigEmoji, Button, CloseButton, Quote } from '../components.js';
import { openOverlay } from '../nav.js';
import { refreshMe, state } from '../store.js';
import { goTab } from '../router.js';
import { haptic, openLink } from '../tg.js';
import { emojiWord, h, prefersReducedMotion, sleep, toast } from '../ui.js';

const STAGES = [
  [0, 'Подбираем шаблоны…'],
  [0.3, 'Красим в твои цвета…'],
  [0.6, 'Добавляем текст и логотип…'],
  [0.85, 'Собираем пак…'],
];

const stageFor = (p) => STAGES.reduce((acc, [from, label]) => (p >= from ? label : acc), STAGES[0][1]);

export function openCreating(initial) {
  let alive = true;
  let finished = false;

  openOverlay({
    kind: 'screen',
    label: 'создание пака',
    className: 'layer--creating',
    onClose: () => {
      alive = false;
      if (!finished) toast('Соберём в фоне: пришлём сообщение, когда пак будет готов');
    },
    render: ({ close }) => {
      const stage = h('p', { class: 'creating__stage' }, stageFor(initial.progress || 0));
      const percent = h('span', { class: 'ring__value' }, '0%');
      const ring = h('div', { class: 'ring', style: { '--p': '0' }, role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': 0 },
        h('div', { class: 'ring__fill' }), percent);
      const stageBox = h('div', { class: 'creating__box' },
        ring,
        h('h2', { class: 'creating__title' }, 'Собираем пак'),
        stage,
        h('p', { class: 'creating__hint' }, 'Можно свернуть: пришлём сообщение, когда всё будет готово'));
      const root = h('div', { class: 'creating' }, CloseButton(close), stageBox);

      let shown = 0; // плавное «догоняние» реального прогресса
      const paint = (p) => {
        shown = Math.max(shown, p);
        const pct = Math.round(shown * 100);
        ring.style.setProperty('--p', String(shown));
        ring.setAttribute('aria-valuenow', String(pct));
        percent.textContent = `${pct}%`;
        stage.textContent = stageFor(shown);
      };

      (async function poll() {
        let progress = initial.progress || 0;
        const started = Date.now();
        while (alive) {
          try {
            const pack = await api.pack(initial.id);
            if (!alive) return;
            if (pack.status === 'ready') { paint(1); await sleep(prefersReducedMotion() ? 0 : 350); return done(pack); }
            if (pack.status === 'failed') return failed();
            progress = pack.progress;
          } catch { /* сеть моргнула — пробуем дальше */ }
          paint(Math.max(progress, Math.min(0.95, (Date.now() - started) / 6000)));
          await sleep(450);
        }
      }());

      function done(pack) {
        finished = true;
        haptic.success();
        refreshMe().catch(() => {});
        const link = pack.link;
        root.replaceChildren(
          CloseButton(close),
          h('div', { class: 'creating__box creating__box--done' },
            BigEmoji('party', 'burst'),
            h('h2', { class: 'creating__title' }, 'Пак готов'),
            h('p', { class: 'creating__sub' }, `${pack.title}`),
            h('p', { class: 'creating__meta' }, emojiWord(pack.count, pack.type)),
            state.me && state.me.demo
              ? Quote({ emoji: 'test', title: 'Демо-сборка', text: 'настоящие наборы в Telegram подключим на следующем этапе.', cls: 'creating__demo' })
              : null,
            h('div', { class: 'creating__actions' },
              link ? Button({ label: 'Открыть в Telegram', icon: 'share', onClick: () => openLink(link) }) : null,
              Button({ label: 'К моим пакам', variant: 'secondary', onClick: () => {
                close();
                goTab('packs');
                document.getElementById('my-packs')?.scrollIntoView();
              } }))));
      }

      function failed() {
        finished = true;
        haptic.error();
        refreshMe().catch(() => {});
        root.replaceChildren(
          CloseButton(close),
          h('div', { class: 'creating__box' },
            BigEmoji('sad'),
            h('h2', { class: 'creating__title' }, 'Не получилось'),
            h('p', { class: 'creating__sub' }, 'Генерации вернулись на баланс. Попробуй ещё раз'),
            Button({ label: 'Понятно', onClick: close })));
      }

      return root;
    },
  });
}
