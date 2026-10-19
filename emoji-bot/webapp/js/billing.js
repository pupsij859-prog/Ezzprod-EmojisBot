// Оплата генераций. Цены в рублях.
// Звёзды — WebApp.openInvoice (сумма пересчитана в Stars). Статус 'paid' от Telegram приходит раньше,
// чем бот обработает платёж, поэтому после него опрашиваем сервер: баланс показываем только по его ответу.
// CryptoBot и xRocket — счёт открывается мини-приложением кошелька поверх нашего (openTelegramLink), а мы
// показываем окно «ждём оплату» и опрашиваем сервер (он сам спрашивает кошелёк, оплачен ли счёт).
import { api, ApiError } from './api.js';
import { BigEmoji, Button } from './components.js';
import { openOverlay } from './nav.js';
import { refreshMe, state } from './store.js';
import { haptic, inTelegram, onAppActive, openInvoice, openWalletLink } from './tg.js';
import { gens, h, rub, sleep, toast } from './ui.js';

const WALLETS = {
  cryptobot: { title: 'CryptoBot', emoji: 'gem' },
  xrocket: { title: 'xRocket', emoji: 'rocket' },
};

async function waitUntilCredited(paymentId) {
  const started = Date.now();
  let delay = 800;
  while (Date.now() - started < 60000) {
    try {
      const { status } = await api.paymentStatus(paymentId);
      if (status === 'paid') {
        await refreshMe();
        return true;
      }
    } catch { /* сеть моргнула — пробуем снова */ }
    await sleep(delay);
    delay = Math.min(delay * 1.5, 5000);
  }
  return false;
}

/**
 * Окно ожидания криптооплаты: счёт уже открыт в кошельке. Сами проверяем каждые несколько секунд
 * и сразу, когда человек возвращается из кошелька. → 'paid' | 'pending' (закрыл окно) | 'expired'
 */
function awaitWalletPayment({ method, url, paymentId, qty, total }) {
  const wallet = WALLETS[method] || { title: 'кошелёк', emoji: 'gem' };
  return new Promise((resolve) => {
    let result = 'pending';
    let stopped = false;
    let offActive = () => {};
    openOverlay({
      kind: 'sheet', label: 'ожидание оплаты', className: 'sheet--await',
      onClose: () => { stopped = true; offActive(); resolve(result); },
      render: ({ close }) => {
        const status = h('p', { class: 'await__status' }, h('i', { class: 'await__pulse', 'aria-hidden': 'true' }), 'Ждём оплату…');
        const check = Button({ label: 'Я оплатил — проверить', variant: 'ghost', onClick: () => poll(true) });
        let busy = false;
        async function poll(manual = false) {
          if (busy || stopped) return;
          busy = true;
          try {
            const { status: s } = await api.paymentStatus(paymentId);
            if (s === 'paid') {
              result = 'paid';
              await refreshMe().catch(() => {});
              close();
              return;
            }
            if (s === 'expired') {
              result = 'expired';
              status.replaceChildren('Счёт истёк. Создай новый — это бесплатно.');
              status.classList.add('is-warn');
              stopped = true;
              return;
            }
            if (manual) toast('Оплата пока не пришла. Если уже оплатил — подожди пару секунд', 'info');
          } catch {
            if (manual) toast('Нет связи, попробуй ещё раз', 'warn');
          } finally {
            busy = false;
          }
        }
        (async () => {
          const started = Date.now();
          while (!stopped && Date.now() - started < 20 * 60 * 1000) {
            await sleep(3500);
            await poll();
          }
        })();
        offActive = onAppActive(() => poll());
        return [
          h('div', { class: 'sheet__body await' },
            BigEmoji(wallet.emoji, 'await__emoji'),
            h('h2', { class: 'await__title' }, `Оплата через ${wallet.title}`),
            h('p', { class: 'await__sum' }, `${gens(qty)} · `, rub(total)),
            status,
            h('p', { class: 'await__hint' }, 'Счёт открылся в кошельке поверх приложения. Оплати его — генерации придут сами, окно закроется.')),
          h('div', { class: 'sheet__footer await__actions' },
            Button({ label: 'Открыть счёт ещё раз', onClick: () => { haptic.tap(); openWalletLink(url); } }),
            check),
        ];
      },
    });
  });
}

/** → 'paid' | 'cancelled' | 'pending' | 'failed' */
export async function payFor(qty, method = 'stars') {
  try {
    if (inTelegram) {
      const res = await api.invoice(qty, method);
      if (method !== 'stars') {
        openWalletLink(res.url);
        const status = await awaitWalletPayment({ method, url: res.url, paymentId: res.payment_id, qty, total: res.total });
        if (status === 'paid') return 'paid';
        if (status === 'expired') return 'failed';
        toast('Если оплатишь позже — генерации придут сами, мы напишем в чат', 'info');
        return 'pending';
      }
      const status = await openInvoice(res.url);
      if (status === 'paid' || status === 'pending') {
        // 'pending' — Telegram ещё подтверждает оплату: ждём так же, как после 'paid'
        toast(status === 'paid' ? 'Зачисляем генерации…' : 'Платёж подтверждается…');
        if (await waitUntilCredited(res.payment_id)) return 'paid';
        toast('Платёж обрабатывается: баланс обновится сам, мы напишем в чат', 'info');
        return 'pending';
      }
      if (status === 'cancelled') return 'cancelled';
      if (status === 'failed') toast('Оплата не прошла, попробуй ещё раз', 'warn');
      return 'failed';
    }
    // вне Telegram оплата возможна только в режиме разработки (маршрут существует лишь при DEV_MODE=1)
    try {
      await api.devPay(qty, method);
    } catch (err) {
      if (err instanceof ApiError && [404, 405].includes(err.status)) {
        toast('Оплата доступна только внутри Telegram', 'warn');
        return 'failed';
      }
      throw err;
    }
    await refreshMe();
    toast('Dev: оплата эмулирована');
    return 'paid';
  } catch (err) {
    if (err instanceof ApiError && err.code === 'method_unavailable') toast('Этот способ оплаты пока не подключён', 'warn');
    else if (err instanceof ApiError && err.code === 'subscription_required') toast('Сначала подпишись на канал', 'warn');
    else if (err instanceof ApiError && err.status === 503) toast('Оплата временно недоступна', 'warn');
    else if (err instanceof ApiError && err.status === 429) toast('Слишком часто, подожди минутку', 'warn');
    else toast('Не получилось открыть оплату', 'error');
    return 'failed';
  } finally {
    if (state.me) refreshMe().catch(() => {});
  }
}
