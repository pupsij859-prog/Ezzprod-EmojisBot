import { authHeaders } from './tg.js';

export class ApiError extends Error {
  constructor(status, detail) {
    super((detail && detail.code) || `http_${status}`);
    this.status = status;
    this.code = (detail && detail.code) || `http_${status}`;
    this.detail = detail || {};
  }
}

async function request(method, path, body, { form = false, headers: extra = {} } = {}) {
  const headers = { ...authHeaders(), ...extra };
  const init = { method, headers };
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
  } catch {
    throw new ApiError(0, { code: 'network' });
  }
  let data = null;
  try { data = await res.json(); } catch {}
  if (!res.ok) {
    const detail = data && data.detail;
    throw new ApiError(res.status, typeof detail === 'object' && detail ? detail : { code: `http_${res.status}` });
  }
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
  // idempotencyKey: повтор запроса с тем же ключом не спишет генерации второй раз
  createPack: (body, idempotencyKey) =>
    request('POST', '/packs', body, { headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {} }),
  pack: (id) => request('GET', `/packs/${id}`),
  packs: () => request('GET', '/packs'),
  styles: () => request('GET', '/styles'),
  addStyle: (body) => request('POST', '/styles', body),
  deleteStyle: (id) => request('DELETE', `/styles/${id}`),
  invoice: (qty) => request('POST', '/billing/invoice', { qty }),
  paymentStatus: (id) => request('GET', `/billing/payments/${id}`),
  devPay: (qty) => request('POST', '/dev/pay', { qty }),
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

/** Превью шаблона: зависит только от шаблона (текст пользователя рисует клиент поверх карточки). */
const PREVIEW_VERSION = '3'; // меняй при обновлении рисунков, чтобы сбросить кэш браузера
export const previewUrl = (templateId) => `/api/templates/${encodeURIComponent(templateId)}/preview.svg?v=${PREVIEW_VERSION}`;

export function newIdempotencyKey() {
  try {
    return crypto.randomUUID();
  } catch {
    return `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
  }
}
