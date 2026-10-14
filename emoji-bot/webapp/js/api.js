import { authHeaders } from './tg.js';
import { toast } from './ui.js';

// Сервер пропал (сеть или туннель сменил адрес и старый больше не отвечает): подсказываем, что делать.
// Свой бэкенд отвечает JSON, а 502 без JSON отдаёт уже туннель.
let staleShown = 0;
function serverGone() {
  if (Date.now() - staleShown < 30000) return;
  staleShown = Date.now();
  toast('Нет связи с ботом. Закрой приложение и открой заново из бота', 'warn');
}

export class ApiError extends Error {
  constructor(status, detail) {
    super((detail && detail.code) || `http_${status}`);
    this.status = status;
    this.code = (detail && detail.code) || `http_${status}`;
    this.detail = detail || {};
  }
}

async function request(method, path, body, { form = false, headers: extra = {}, signal } = {}) {
  const headers = { ...authHeaders(), ...extra };
  const init = { method, headers, signal };
  if (body !== undefined) {
    if (form) {
      init.body = body;
    } else {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
  }
  let res;
  try {
    res = await fetch(`/api${path}`, init);
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    serverGone();
    throw new ApiError(0, { code: 'network' });
  }
  // подпись Telegram истекла (initData живёт сутки) — сообщаем приложению, где бы это ни случилось
  if (res.status === 401) window.dispatchEvent(new CustomEvent('ez:session-expired'));
  let data = null;
  try { data = await res.json(); } catch (err) { if (err.name === 'AbortError') throw err; }
  if (!res.ok && data === null && res.status >= 502) serverGone();
  if (!res.ok) {
    const detail = data && data.detail;
    throw new ApiError(res.status, typeof detail === 'object' && detail ? detail : { code: `http_${res.status}` });
  }
  if (data === null) throw new ApiError(res.status, { code: 'invalid_response' });
  return data;
}

export const api = {
  me: () => request('GET', '/me'),
  completeOnboarding: () => request('POST', '/onboarding/complete'),
  categories: () => request('GET', '/categories'),
  templates: (category, page) => request('GET', `/templates?category=${encodeURIComponent(category)}&page=${page}`),
  pricing: () => request('GET', '/pricing'),
  uploadLogo(file) {
    const form = new FormData();
    form.append('file', file);
    return request('POST', '/logos', form, { form: true });
  },
  // освобождает место в квоте логотипов на сервере
  deleteLogo: (id) => request('DELETE', `/logos/${encodeURIComponent(id)}`),
  // векторный логотип для живого превью (после перезапуска приложения)
  logoVector: (id) => request('GET', `/logos/${encodeURIComponent(id)}/vector`),
  // свой шрифт ника (.ttf)
  uploadFont(file) {
    const form = new FormData();
    form.append('file', file);
    return request('POST', '/fonts', form, { form: true });
  },
  deleteFont: (id) => request('DELETE', `/fonts/${encodeURIComponent(id)}`),
  // ник как контуры букв (fontId — свой шрифт, null — стандартный) — для живого превью шаблонов
  glyphs: (text, fontId = null) => request('POST', '/glyphs', { text, font_id: fontId }),
  // продвинутый редактор (Pro): готовая анимация с правками, части эмодзи, цвета и проверка под Telegram
  render: (body, signal) => request('POST', '/render', body, { signal }),
  // idempotencyKey: повтор запроса с тем же ключом не спишет генерации второй раз
  createPack: (body, idempotencyKey) =>
    request('POST', '/packs', body, { headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {} }),
  pack: (id) => request('GET', `/packs/${id}`),
  packs: () => request('GET', '/packs'),
  styles: () => request('GET', '/styles'),
  addStyle: (body) => request('POST', '/styles', body),
  deleteStyle: (id) => request('DELETE', `/styles/${id}`),
  invoice: (qty, method = 'stars') => request('POST', '/billing/invoice', { qty, method }),
  paymentStatus: (id) => request('GET', `/billing/payments/${id}`),
  devPay: (qty, method = 'stars') => request('POST', '/dev/pay', { qty, method }),
  referral: () => request('GET', '/referral'),
  emoji: () => request('GET', '/emoji'),
  admin: {
    stats: () => request('GET', '/admin/stats'),
    payments: (status, offset = 0) => request('GET', `/admin/payments?status=${encodeURIComponent(status)}&offset=${offset}`),
    refund: (chargeId, provider) => request('POST', `/admin/payments/${encodeURIComponent(chargeId)}/refund`, { provider }),
    users: (q, offset = 0) => request('GET', `/admin/users?q=${encodeURIComponent(q)}&offset=${offset}`),
    user: (id) => request('GET', `/admin/users/${id}`),
    grant: (id, delta) => request('POST', `/admin/users/${id}/grant`, { delta }),
    packs: (status, offset = 0) => request('GET', `/admin/packs?status=${encodeURIComponent(status)}&offset=${offset}`),
  },
};

/** Статичная картинка шаблона (assets/thumbs, кадр анимации). Анимацию с ником показывает js/tgs.js. */
// меняй при обновлении шаблонов или их картинок, чтобы сбросить кэш браузера (картинки и файлы .tgs)
export const PREVIEW_VERSION = '2';
export const previewUrl = (templateId) => `assets/thumbs/${encodeURIComponent(templateId)}.webp?v=${PREVIEW_VERSION}`;

export function newIdempotencyKey() {
  try {
    return crypto.randomUUID();
  } catch {
    return `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
  }
}
