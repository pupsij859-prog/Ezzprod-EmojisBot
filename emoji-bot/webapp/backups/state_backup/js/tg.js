// Обёртка над Telegram.WebApp. Вне Telegram (обычный браузер) работает в «dev»-режиме:
// сервер примет запрос только если он запущен с DEV_MODE=1 и открыт с этого же компьютера.

const WA = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

export const inTelegram = Boolean(WA && WA.initData);
export const tgUser = (WA && WA.initDataUnsafe && WA.initDataUnsafe.user) || null;

const HEADER_COLOR = '#0a0a0a';
const BG_COLOR = '#0a0a0a';
const BOTTOM_BAR_COLOR = '#141415';

function atLeast(v) {
  try { return WA.isVersionAtLeast(v); } catch { return false; }
}

export function initTelegram() {
  if (!WA || !inTelegram) return; // в обычном браузере (dev) Telegram API не трогаем
  try { WA.ready(); } catch {}
  try { WA.expand(); } catch {}
  try { if (atLeast('7.7')) WA.disableVerticalSwipes(); } catch {}
  try { WA.setHeaderColor(HEADER_COLOR); } catch {}
  try { WA.setBackgroundColor(BG_COLOR); } catch {}
  try { if (atLeast('7.10')) WA.setBottomBarColor(BOTTOM_BAR_COLOR); } catch {}
}

export function devUserId() {
  const q = new URLSearchParams(location.search).get('dev');
  try {
    if (q) localStorage.setItem('ez.dev', q);
    return localStorage.getItem('ez.dev') || '1001';
  } catch {
    return q || '1001';
  }
}

export function authHeaders() {
  if (inTelegram) return { Authorization: `tma ${WA.initData}` };
  return { 'X-Dev-User': devUserId() };
}

// ── BackButton: показываем всегда, когда открыт любой оверлей (в референсе это было непоследовательно)
let backHandler = null;
const devBackListeners = new Set();

export const backButton = {
  show(handler) {
    this.hide();
    backHandler = handler;
    if (inTelegram) {
      try { WA.BackButton.onClick(handler); WA.BackButton.show(); } catch {}
    }
    devBackListeners.forEach((fn) => fn(true));
  },
  hide() {
    if (inTelegram && backHandler) {
      try { WA.BackButton.offClick(backHandler); WA.BackButton.hide(); } catch {}
    }
    backHandler = null;
    devBackListeners.forEach((fn) => fn(false));
  },
  onDevChange(fn) { devBackListeners.add(fn); },
  trigger() { if (backHandler) backHandler(); },
};

export function closeApp() {
  if (inTelegram) { try { WA.close(); } catch {} }
}

// ── haptics (беззвучно игнорируем, если не поддерживается)
export const haptic = {
  tap() { try { WA.HapticFeedback.impactOccurred('light'); } catch {} },
  rigid() { try { WA.HapticFeedback.impactOccurred('rigid'); } catch {} },
  select() { try { WA.HapticFeedback.selectionChanged(); } catch {} },
  success() { try { WA.HapticFeedback.notificationOccurred('success'); } catch {} },
  error() { try { WA.HapticFeedback.notificationOccurred('error'); } catch {} },
};

export function openLink(url) {
  try {
    if (WA && /^https:\/\/t\.me\//.test(url)) return WA.openTelegramLink(url);
    if (WA) return WA.openLink(url);
  } catch {}
  window.open(url, '_blank', 'noopener');
}

export function shareLink(url, text) {
  const share = `https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`;
  openLink(share);
}

// статус: 'paid' | 'cancelled' | 'failed' | 'pending'
export function openInvoice(url) {
  return new Promise((resolve) => {
    if (!WA || !inTelegram || !atLeast('6.1')) return resolve('unsupported');
    try { WA.openInvoice(url, (status) => resolve(status)); } catch { resolve('failed'); }
  });
}

// ── облачное хранилище Telegram (на всех устройствах пользователя); вне Telegram — localStorage
export function cloudGet(key) {
  return new Promise((resolve) => {
    if (WA && inTelegram && atLeast('6.9')) {
      try { WA.CloudStorage.getItem(key, (err, value) => resolve(err ? null : value || null)); return; } catch {}
    }
    try { resolve(localStorage.getItem(`ez.cloud.${key}`)); } catch { resolve(null); }
  });
}

export function cloudSet(key, value) {
  if (WA && inTelegram && atLeast('6.9')) {
    try { WA.CloudStorage.setItem(key, value); return; } catch {}
  }
  try { localStorage.setItem(`ez.cloud.${key}`, value); } catch {}
}

/** Подтверждение действия: нативный диалог Telegram, вне Telegram — confirm браузера. */
export function confirmAction(text) {
  return new Promise((resolve) => {
    if (WA && inTelegram && atLeast('6.2')) {
      try { WA.showConfirm(text, (ok) => resolve(Boolean(ok))); return; } catch {}
    }
    resolve(window.confirm(text));
  });
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement('textarea');
    area.value = text;
    area.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
    document.body.append(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch {}
    area.remove();
    return ok;
  }
}
