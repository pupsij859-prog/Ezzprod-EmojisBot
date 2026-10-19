// Промокод: поле ввода и «Активировать». Код проверяет сервер (POST /api/promo), он же начисляет генерации
// через журнал баланса. Тот же код можно ввести в чате бота: /promo КОД.
import { api, ApiError } from '../api.js';
import { BigEmoji, Button, setBusy } from '../components.js';
import { openOverlay } from '../nav.js';
import { refreshMe } from '../store.js';
import { haptic } from '../tg.js';
import { gens, h, toast } from '../ui.js';

const ERRORS = {
  unknown: 'Такого промокода нет. Проверь, как он написан',
  used: 'Этот промокод ты уже активировал',
  exhausted: 'У этого промокода закончились активации',
  expired: 'Срок действия промокода закончился',
  paused: 'Этот промокод сейчас не действует',
  not_new: 'Промокод только для новых пользователей — тех, кто ещё ничего не покупал',
};

export function openPromo() {
  openOverlay({
    kind: 'sheet',
    label: 'промокод',
    className: 'sheet--promo',
    render: ({ close }) => {
      const input = h('input', {
        class: 'field field--box promo__input', type: 'text', maxLength: 32, placeholder: 'ВВЕДИ КОД',
        autocomplete: 'off', autocapitalize: 'characters', enterKeyHint: 'go', 'aria-label': 'промокод',
      });
      input.spellcheck = false;
      const error = h('p', { class: 'promo__error', role: 'alert' });
      const body = h('div', { class: 'sheet__body promo' },
        BigEmoji('gift', 'promo__emoji'),
        h('h2', { class: 'promo__title' }, 'Промокод'),
        h('p', { class: 'promo__text' }, 'Введи код из канала, розыгрыша или от друга — бонус сразу придет на баланс.'),
        input, error);
      const go = Button({ label: 'Активировать', onClick: () => submit() });
      go.disabled = true;

      input.addEventListener('input', () => {
        const clean = input.value.toUpperCase().replace(/[^A-Z0-9_-]/g, '');
        if (clean !== input.value) input.value = clean;
        go.disabled = clean.length < 3;
        error.textContent = '';
        input.classList.remove('is-bad');
      });
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !go.disabled) submit(); });
      setTimeout(() => { try { input.focus(); } catch {} }, 320);

      let busy = false;
      async function submit() {
        if (busy) return;
        busy = true;
        setBusy(go, true, 'Проверяем…');
        try {
          const res = await api.redeemPromo(input.value);
          const amount = res.gens || 0;
          const disc = res.discount_pct || 0;
          haptic.success();
          await refreshMe().catch(() => {});
          let title = `+${gens(amount)}`;
          let desc = `Промокод активирован. На балансе ${res.balance}.`;
          if (disc > 0 && amount > 0) {
            title = `+${amount} и −${disc}%`;
            desc = `Промокод активирован! +${gens(amount)} и скидка ${disc}% на следующую покупку.`;
          } else if (disc > 0) {
            title = `−${disc}%`;
            desc = `Промокод активирован! Скидка ${disc}% действует на следующую покупку.`;
          }
          body.replaceChildren(
            BigEmoji('party', 'promo__emoji promo__emoji--win'),
            h('h2', { class: 'promo__title' }, title),
            h('p', { class: 'promo__text' }, desc));
          const done = Button({ label: 'Отлично', onClick: close });
          go.replaceWith(done);
        } catch (err) {
          haptic.error();
          setBusy(go, false);
          input.classList.add('is-bad');
          if (err instanceof ApiError && err.code === 'promo') error.textContent = ERRORS[err.detail.reason] || ERRORS.unknown;
          else if (err instanceof ApiError && err.status === 429) error.textContent = 'Слишком много попыток. Подожди минутку';
          else if (err instanceof ApiError && err.code === 'subscribe') error.textContent = 'Сначала подпишись на наш канал';
          else toast('Не получилось проверить код, попробуй ещё раз', 'warn');
        } finally {
          busy = false;
        }
      }
      return [body, h('div', { class: 'sheet__footer' }, go)];
    },
  });
}
