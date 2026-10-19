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

export const DEFAULT_COLOR = '#3d6ad6'; // предложение в палитре «Заливки»; по умолчанию у стикера родной цвет
export const DEFAULT_STROKE = '#ffffff'; // предложение в палитре «Обводки»; по умолчанию родной цвет контуров
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
  logo: null, // {id, name, thumb, vector} — vector: логотип в векторе для живого превью
  color: null, // «Заливка» — основной цвет стикера '#rrggbb'; null — родной у каждого шаблона
  stroke: null, // «Обводка» — контуры и надпись '#rrggbb'; null — родные
  size: 100, // «Размер надписи», % (100 — автоподбор по длине ника)
  place: null, // «Положение надписи» {x, y, r}: сдвиг (% ширины места надписи) и поворот, °; null — по центру
  font: null, // шрифт ника {id, name}: готовый (app/data/fonts) или свой загруженный; null — стандартный
  // «Градиент» {a, b, angle, on}: два цвета, угол (как в CSS) и на что он ложится — 'text' ник, 'logo', 'fill'
  // заливка; пустой on — градиент выключен (цвета запоминаются, чтобы при включении не подбирать их заново)
  grad: null,
  // свои настройки отдельных стикеров поверх общих: id шаблона → {text?, logo?, color?, stroke?, size?, edit?}
  // edit — правки продвинутого редактора (Pro), бывают только у отдельных стикеров: части у шаблонов свои
  overrides: {},
  targetPack: null, // готовый пак, в который сейчас добавляются шаблоны с главной
};

export const LOOK_KEYS = ['text', 'logo', 'color', 'stroke', 'size', 'place', 'font', 'grad'];

export const DEFAULT_GRAD = { a: '#2f7bff', b: '#8fd8ff', angle: 135, on: [] };
const GRAD_ON = ['text', 'logo', 'fill'];

/** «Градиент» из сохранённых настроек: {a, b, angle, on} в допустимых пределах или null. */
export function cleanGradPref(g) {
  if (!g || typeof g !== 'object' || !HEX.test(g.a) || !HEX.test(g.b)) return null;
  const angle = Number.isFinite(Number(g.angle)) ? ((Math.round(Number(g.angle)) % 360) + 360) % 360 : DEFAULT_GRAD.angle;
  const on = Array.isArray(g.on) ? GRAD_ON.filter((t) => g.on.includes(t)) : [];
  return { a: g.a.toLowerCase(), b: g.b.toLowerCase(), angle, on };
}

/** Итоговые настройки стикера: свои, если заданы, иначе общие (+ правки редактора, если есть). */
export function lookFor(id) {
  const own = state.overrides[id] || {};
  const out = {};
  LOOK_KEYS.forEach((k) => { out[k] = k in own ? own[k] : state[k]; });
  out.edit = own.edit || null;
  return out;
}

/** Правки продвинутого редактора у стикера (null — нет). */
export const editFor = (id) => (state.overrides[id] && state.overrides[id].edit) || null;

/** Сохранить правки редактора стикера (null — убрать). */
export function setItemEdit(id, edit) {
  const own = { ...(state.overrides[id] || {}) };
  if (edit && Object.keys(edit).length) own.edit = edit; else delete own.edit;
  if (Object.keys(own).length) state.overrides[id] = own; else delete state.overrides[id];
  savePrefs();
  bus.emit('personalization');
}

export const hasOwnLook = (id) => Boolean(state.overrides[id] && Object.keys(state.overrides[id]).length);

/** Изменить только один стикер. */
export function setItemLook(id, patch) {
  state.overrides[id] = { ...(state.overrides[id] || {}), ...patch };
  savePrefs();
  bus.emit('personalization');
}

/** Изменить все стикеры: общее значение, а свои значения этого поля у отдельных стикеров сбрасываются. */
export function setLookForAll(patch) {
  Object.assign(state, patch);
  Object.keys(state.overrides).forEach((id) => {
    const own = state.overrides[id];
    Object.keys(patch).forEach((k) => { delete own[k]; });
    if (!Object.keys(own).length) delete state.overrides[id];
  });
  savePrefs();
  bus.emit('personalization');
}

/** Используется ли свой шрифт ещё где-то (общие настройки или отдельные стикеры). */
export const fontInUse = (fontId) => Boolean(fontId) && (
  (state.font && state.font.id === fontId)
  || Object.values(state.overrides).some((o) => o.font && o.font.id === fontId));

/** Свой шрифт удалён: убираем его отовсюду — там вернётся стандартный. */
export function forgetFont(fontId) {
  if (state.font && state.font.id === fontId) state.font = null;
  Object.values(state.overrides).forEach((o) => { if (o.font && o.font.id === fontId) o.font = null; });
  savePrefs();
  bus.emit('personalization');
}

/** Используется ли логотип ещё где-то (общие настройки или отдельные стикеры). */
export const logoInUse = (logoId) => Boolean(logoId) && (
  (state.logo && state.logo.id === logoId)
  || Object.values(state.overrides).some((o) => o.logo && o.logo.id === logoId));

// вектор логотипа не храним локально (он большой) — подгружаем с сервера один раз на id
const vectorRequests = new Map();
function ensureLogoVector(logo) {
  if (!logo || logo.vector) return;
  if (!vectorRequests.has(logo.id)) {
    vectorRequests.set(logo.id, api.logoVector(logo.id).then((r) => r.vector).catch((err) => {
      if (err && err.status === 404) return 'gone';
      vectorRequests.delete(logo.id);
      return null;
    }));
  }
  vectorRequests.get(logo.id).then((vector) => {
    if (vector === 'gone') {  // логотипа на сервере уже нет — забываем его везде
      if (state.logo && state.logo.id === logo.id) state.logo = null;
      Object.values(state.overrides).forEach((o) => { if (o.logo && o.logo.id === logo.id) o.logo = null; });
      savePrefs();
    } else if (vector) {
      logo.vector = vector;
    } else {
      return;
    }
    bus.emit('personalization');
  });
}

// шрифт ника: свой (32 hex) или готовый (id вида «montserrat»; есть ли такой — проверит сервер)
const cleanFont = (f) => (f && /^(?:[0-9a-f]{32}|[a-z0-9-]{2,24})$/.test(f.id) ? { id: f.id, name: String(f.name || 'шрифт').slice(0, 48) } : null);

const cleanSize = (v) => (Number.isFinite(v) ? Math.max(40, Math.min(160, Math.round(v))) : 100);

/** «Положение надписи» из сохранённых настроек: {x, y, r} в допустимых пределах или null. */
export function cleanPlace(p) {
  if (!p || typeof p !== 'object') return null;
  const lim = { x: 100, y: 100, r: 180 };
  const out = {};
  Object.keys(lim).forEach((k) => {
    const v = Number(p[k]);
    out[k] = Number.isFinite(v) ? Math.max(-lim[k], Math.min(lim[k], Math.round(v))) : 0;
  });
  return out.x || out.y || out.r ? out : null;
}

const cleanLogo = (logo) => (logo && /^[0-9a-f]{32}$/.test(logo.id) && typeof logo.thumb === 'string'
  && logo.thumb.startsWith('data:image/png;base64,')
  ? { id: logo.id, name: String(logo.name || 'logo').slice(0, 48), thumb: logo.thumb, vector: null } : null);
const logoMeta = (logo) => (logo ? { id: logo.id, name: logo.name, thumb: logo.thumb } : null);

// id шаблона вида «classic-007»: категория из каталога и номер в её пределах
function isKnownTemplate(id) {
  const m = /^([a-z]+)-(\d{3})$/.exec(id);
  if (!m) return false;
  const cat = state.categories.find((c) => c.id === m[1]);
  const n = Number(m[2]);
  return Boolean(cat) && n >= 1 && n <= cat.count;
}

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
    // сохранённый выбор мог устареть: убираем шаблоны, которых больше нет в каталоге, и лишнее сверх лимита типа
    if (Array.isArray(p.selection)) {
      state.selection = p.selection.filter((x) => typeof x === 'string' && isKnownTemplate(x)).slice(0, limitFor(state.type));
    }
    if (typeof p.text === 'string') state.text = p.text.slice(0, 12);
    if (HEX.test(p.color)) state.color = p.color;
    if (HEX.test(p.stroke)) state.stroke = p.stroke;
    state.size = cleanSize(p.size);
    state.place = cleanPlace(p.place);
    state.font = cleanFont(p.font);
    state.grad = cleanGradPref(p.grad);
    state.logo = cleanLogo(p.logo);
    ensureLogoVector(state.logo);
    // свои настройки — только у стикеров, которые всё ещё выбраны
    if (p.overrides && typeof p.overrides === 'object') {
      state.selection.forEach((id) => {
        const raw = p.overrides[id];
        if (!raw || typeof raw !== 'object') return;
        const own = {};
        if (typeof raw.text === 'string') own.text = raw.text.slice(0, 12);
        if ('color' in raw) own.color = HEX.test(raw.color) ? raw.color : null;
        if ('stroke' in raw) own.stroke = HEX.test(raw.stroke) ? raw.stroke : null;
        if ('size' in raw) own.size = cleanSize(raw.size);
        if ('place' in raw) own.place = cleanPlace(raw.place);
        if ('font' in raw) own.font = cleanFont(raw.font);
        if ('grad' in raw) own.grad = cleanGradPref(raw.grad);
        // правки редактора проверяет сервер; здесь только форма
        if (raw.edit && typeof raw.edit === 'object' && !Array.isArray(raw.edit)) own.edit = raw.edit;
        if ('logo' in raw) { own.logo = cleanLogo(raw.logo); ensureLogoVector(own.logo); }
        if (Object.keys(own).length) state.overrides[id] = own;
      });
    }
  } catch {
    state.selection = state.selection.length ? state.selection : ['classic-001'];
  }
}

let saveTimer = 0;
export function savePrefs() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushPrefs, 150);
}

export function flushPrefs() {
  clearTimeout(saveTimer);
  try {
    const { category, type, selection, text, color, stroke, size, place, font, grad } = state;
    const overrides = {};
    Object.entries(state.overrides).forEach(([id, own]) => {
      overrides[id] = { ...own };
      if ('logo' in own) overrides[id].logo = logoMeta(own.logo);
    });
    localStorage.setItem(prefsKey(), JSON.stringify({
      category, type, selection, text, color, stroke, size, place, font, grad, logo: logoMeta(state.logo), overrides,
    }));
  } catch {}
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', flushPrefs, { passive: true });
  window.addEventListener('pagehide', flushPrefs, { passive: true });
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
    delete state.overrides[id];
    savePrefs();
    bus.emit('selection', { id, action: 'removed', selected: false });
    return 'removed';
  }
  if (state.selection.length >= limitFor(state.type)) return 'limit';
  state.selection.push(id);
  savePrefs();
  bus.emit('selection', { id, action: 'added', selected: true });
  return 'added';
}

export function clearSelection() {
  state.selection = [];
  state.overrides = {};
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
  if (patch.logo) ensureLogoVector(patch.logo);
  savePrefs();
  bus.emit('personalization');
}

// ── цены
export function priceFor(qty) {
  const p = state.pricing;
  if (!p) return 0;
  const q = Math.max(1, Math.min(qty, p.max_qty));
  let total = p.totals[q];
  const disc = (state.me && state.me.discount_pct) || 0;
  if (disc > 0) {
    total = Math.max(1, Math.round(total * (100 - Math.min(99, disc)) / 100));
  }
  return total;
}

/** Сколько Stars спишет Telegram за сумму в ₽ (как на сервере: вверх до целого). */
export function starsFor(total) {
  const rate = (state.pricing && state.pricing.rub_per_star) || 1;
  return Math.max(1, Math.ceil(Math.round((total / rate) * 1e6) / 1e6));
}

export function starsForQty(qty) {
  const p = state.pricing;
  if (!p) return 0;
  const disc = (state.me && state.me.discount_pct) || 0;
  if (disc > 0) {
    return starsFor(priceFor(qty));
  }
  const q = Math.max(1, Math.min(qty, p.max_qty));
  if (p.stars_totals && p.stars_totals[q] !== undefined) {
    return p.stars_totals[q];
  }
  return starsFor(p.totals[q]);
}
