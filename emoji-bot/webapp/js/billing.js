// Оплата генераций. Цены в рублях; сейчас работает способ «Звёзды» (WebApp.openInvoice, сумма пересчитана в Stars). Статус 'paid' от Telegram приходит раньше,
// чем бот обработает платёж, поэтому после него опрашиваем сервер: баланс показываем только по его ответу.
import { api, ApiError } from './api.js';
import { refreshMe, state } from './store.js';
import { inTelegram, openInvoice } from './tg.js';
import { sleep, toast } from './ui.js';

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

/** → 'paid' | 'cancelled' | 'pending' | 'failed' */
export async function payFor(qty, method = 'stars') {
  try {
    if (inTelegram) {
      const { url, payment_id: paymentId } = await api.invoice(qty, method);
      const status = await openInvoice(url);
      if (status === 'paid') {
        toast('Зачисляем генерации…');
        if (await waitUntilCredited(paymentId)) return 'paid';
        toast('Платёж обрабатывается: баланс обновится сам, мы напишем в чат', 'info');
        return 'pending';
      }
      if (status === 'cancelled') return 'cancelled';
      return status === 'pending' ? 'pending' : 'failed';
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
    else if (err instanceof ApiError && err.status === 503) toast('Оплата временно недоступна', 'warn');
    else if (err instanceof ApiError && err.status === 429) toast('Слишком часто, подожди минутку', 'warn');
    else toast('Не получилось открыть оплату', 'error');
    return 'failed';
  } finally {
    if (state.me) refreshMe().catch(() => {});
  }
}
