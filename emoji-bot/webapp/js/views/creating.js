// Экран сборки пака: прогресс → «пак готов». Сборка идёт на сервере и не зависит от этого экрана.
import { api } from '../api.js';
import { BigEmoji, Button, CloseButton, Quote } from '../components.js';
import { openOverlay } from '../nav.js';
import { refreshMe, state } from '../store.js';
import { goTab } from '../router.js';
import { haptic, openLink } from '../tg.js';
import { emojiWord, h, prefersReducedMotion, sleep, toast } from '../ui.js';

const SLOW_AFTER_MS = 90_000; // дольше этого — «сборка затянулась» вместо вечного прогресса

const STAGES = [
  [0, 'Готовим файлы…'],
  [0.3, 'Загружаем в Telegram…'],
  [0.85, 'Почти готово…'],
];

const stageFor = (p) => STAGES.reduce((acc, [from, label]) => (p >= from ? label : acc), STAGES[0][1]);

export function openCreating(initial) {
  let alive = true;
  let finished = false;
  let pollRequest = null;

  openOverlay({
    kind: 'screen',
    label: 'создание пака',
    className: 'layer--creating',
    onClose: () => {
      alive = false;
      pollRequest?.abort();
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
        let slowShown = false;
        while (alive) {
          if (!slowShown && Date.now() - started > SLOW_AFTER_MS) {
            slow();
            slowShown = true;
          }
          const controller = new AbortController();
          pollRequest = controller;
          const timeout = setTimeout(() => controller.abort(), 15_000);
          try {
            const pack = await api.pack(initial.id, controller.signal);
            if (!alive) return;
            if (pack.status === 'ready') { paint(1); await sleep(prefersReducedMotion() ? 0 : 350); return done(pack); }
            if (pack.status === 'failed') return failed(pack);
            progress = pack.progress;
          } catch { /* сеть моргнула — пробуем дальше */ }
          finally {
            clearTimeout(timeout);
            if (pollRequest === controller) pollRequest = null;
          }
          paint(progress);
          await sleep(slowShown ? 2000 : 1000);
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

      // сборка затянулась: не крутим проценты бесконечно, а честно говорим, что пришлём сообщение
      function slow() {
        finished = true; // при закрытии не дублируем тост «соберём в фоне»
        root.replaceChildren(
          CloseButton(close),
          h('div', { class: 'creating__box' },
            BigEmoji('clock'),
            h('h2', { class: 'creating__title' }, 'Сборка затянулась'),
            h('p', { class: 'creating__sub' }, 'Пак всё ещё собирается. Пришлём сообщение в чат, когда он будет готов, а статус видно во вкладке «Паки»'),
            Button({ label: 'Понятно', onClick: close })));
      }

      function failed(pack) {
        finished = true;
        haptic.error();
        refreshMe().catch(() => {});
        const reasons = {
          build_timeout: 'Telegram не ответил вовремя. Генерации вернулись на баланс, попробуй ещё раз чуть позже',
          telegram_unavailable: 'Не удалось связаться с Telegram. Генерации вернулись на баланс, попробуй ещё раз чуть позже',
          telegram_busy: 'Telegram временно ограничил загрузку. Генерации вернулись на баланс, попробуй чуть позже',
        };
        root.replaceChildren(
          CloseButton(close),
          h('div', { class: 'creating__box' },
            BigEmoji('sad'),
            h('h2', { class: 'creating__title' }, 'Не получилось'),
            h('p', { class: 'creating__sub' }, reasons[pack.error] || 'Генерации вернулись на баланс. Попробуй ещё раз'),
            Button({ label: 'Понятно', onClick: close })));
      }

      return root;
    },
  });
}
