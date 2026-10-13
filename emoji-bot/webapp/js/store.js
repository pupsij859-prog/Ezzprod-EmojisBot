// Общее состояние приложения + простая шина событий.
import { api } from './api.js';

const listeners = {};
export const bus = {
  on(name, fn) {
    (listeners[name] ||= new Set()).add(fn);
    return () => listeners[name].delete(fn);
  },
  emit(name, payload) {
    if (listeners[name]) listeners[name].forEach((fn) => fn(payload));
  },
};

export const DEFAULTS = { fill: '#131822', stroke: '#a8c7ef' };
const HEX = /^#[0-9a-f]{6}$/i;

export const state = {
  me: null,
  pricing: null,
  categories: [],
  emoji: {}, // ключ → { id, thumb, svg, custom }
  tab: 'home',
  category: 'classic',
  page: 1,
  pages: 1,
  items: [],
  type: 'emoji',
  selection: [],
  text: '',
  logo: null, // {id, name, thumb}
  fill: DEFAULTS.fill,
  stroke: DEFAULTS.stroke,
};

// ── сохранение «удобств» (выбор, цвета, категория) между запусками; без них всё работает
const prefsKey = () => `ez.prefs.${state.me ? state.me.id : 'x'}`;

export function loadPrefs() {
  try {
    const raw = localStorage.getItem(prefsKey());
    if (!raw) {
      state.selection = ['classic-001']; // как в референсе: при первом входе уже выбран один шаблон
      return;
    }
    const p = JSON.parse(raw);
    if (typeof p.category === 'string') state.category = p.category;
    if (p.type === 'emoji' || p.type === 'sticker') state.type = p.type;
    if (Array.isArray(p.selection)) state.selection = p.selection.filter((x) => typeof x === 'string').slice(0, 200);
    if (typeof p.text === 'string') state.text = p.text.slice(0, 12);
    if (HEX.test(p.fill)) state.fill = p.fill;
    if (HEX.test(p.stroke)) state.stroke = p.stroke;
  } catch {
    state.selection = state.selection.length ? state.selection : ['classic-001'];
  }
}

export function savePrefs() {
  try {
    const { category, type, selection, text, fill, stroke } = state;
    localStorage.setItem(prefsKey(), JSON.stringify({ category, type, selection, text, fill, stroke }));
  } catch {}
}

// ── профиль
export function setMe(me) {
  state.me = me;
  bus.emit('me', me);
}

export async function refreshMe() {
  setMe(await api.me());
  return state.me;
}

export const limitFor = (type) =>
  state.me ? (type === 'sticker' ? state.me.limits.sticker_pack_max : state.me.limits.emoji_pack_max) : 200;

// ── выбор шаблонов
export const isSelected = (id) => state.selection.includes(id);

/** Возвращает 'added' | 'removed' | 'limit'. */
export function toggleSelection(id) {
  const i = state.selection.indexOf(id);
  if (i >= 0) {
    state.selection.splice(i, 1);
    savePrefs();
    bus.emit('selection');
    return 'removed';
  }
  if (state.selection.length >= limitFor(state.type)) return 'limit';
  state.selection.push(id);
  savePrefs();
  bus.emit('selection');
  return 'added';
}

export function clearSelection() {
  state.selection = [];
  savePrefs();
  bus.emit('selection');
}

export function setType(type) {
  state.type = type;
  const max = limitFor(type);
  if (state.selection.length > max) state.selection = state.selection.slice(0, max);
  savePrefs();
  bus.emit('type');
  bus.emit('selection');
}

export function setPersonalization(patch) {
  Object.assign(state, patch);
  savePrefs();
  bus.emit('personalization');
}

// ── цены
export function priceFor(qty) {
  const p = state.pricing;
  if (!p) return 0;
  const q = Math.max(1, Math.min(qty, p.max_qty));
  return p.totals[q];
}
