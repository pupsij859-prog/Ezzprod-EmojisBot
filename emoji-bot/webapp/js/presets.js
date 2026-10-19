// Готовые оформления: «Подборки цветов» (заливка + обводка + пара цветов градиента) и «Наборы» — шаблоны
// в одном стиле вместе с подборкой, шрифтом и градиентом. Пользователю остаётся вписать ник (и логотип).
import { cleanGradPref, DEFAULT_GRAD, limitFor, savePrefs, setLookForAll, state, bus } from './store.js';

/** Подборки цветов: fill — «Заливка», stroke — «Обводка», a → b — цвета градиента. */
export const PALETTES = [
  { id: 'ocean', name: 'Океан', fill: '#1e6fff', stroke: '#ffffff', a: '#2f7bff', b: '#8fd8ff' },
  { id: 'neon', name: 'Неон', fill: '#7c3aed', stroke: '#f5d0fe', a: '#ff3cac', b: '#784ba0' },
  { id: 'sunset', name: 'Закат', fill: '#ff6a3d', stroke: '#fff3e0', a: '#ffb347', b: '#ff4e6a' },
  { id: 'gold', name: 'Золото', fill: '#d4a017', stroke: '#fff8dc', a: '#ffe58a', b: '#c8890b' },
  { id: 'mint', name: 'Мята', fill: '#10b981', stroke: '#ecfdf5', a: '#43e97b', b: '#38f9d7' },
  { id: 'love', name: 'Любовь', fill: '#ff2d55', stroke: '#ffffff', a: '#ff5e7e', b: '#ffb3c1' },
  { id: 'candy', name: 'Конфета', fill: '#f472b6', stroke: '#ffffff', a: '#fbc2eb', b: '#a6c1ee' },
  { id: 'ice', name: 'Лёд', fill: '#bfe9ff', stroke: '#0b84d8', a: '#e0f7ff', b: '#5cb8ff' },
  { id: 'night', name: 'Ночь', fill: '#1f2340', stroke: '#c7d2fe', a: '#6366f1', b: '#22d3ee' },
  { id: 'mono', name: 'Монохром', fill: '#151515', stroke: '#ffffff', a: '#ffffff', b: '#9ca3af' },
];

export const paletteById = (id) => PALETTES.find((p) => p.id === id) || null;

/** Подборка, которой совпадают заливка и обводка (null — цвета свои или родные). */
export function activePalette(look) {
  return PALETTES.find((p) => p.fill === look.color && p.stroke === look.stroke) || null;
}

/** Настройки, которые ставит подборка: цвета и, если градиент включён, его цвета. */
export function paletteLook(p, grad) {
  const g = cleanGradPref(grad);
  return { color: p.fill, stroke: p.stroke, grad: { ...(g || DEFAULT_GRAD), a: p.a, b: p.b } };
}

const range = (cat, nums) => nums.map((n) => `${cat}-${String(n).padStart(3, '0')}`);

/** Наборы: шаблоны одного стиля + подборка цветов, шрифт ника и куда ложится градиент. */
export const KITS = [
  {
    id: 'fresh', name: 'Новинки', tag: 'NEW',
    text: 'Свежие шаблоны: ник или логотип встаёт прямо на картинку. Родные цвета, ник — в неоновом градиенте',
    templates: range('new', [10, 5, 14, 8, 19, 15, 7, 12, 16, 17, 18, 13, 1, 4, 20, 9]),
    palette: 'neon', native: true, font: { id: 'unbounded', name: 'Unbounded' }, on: ['text'], angle: 90,
  },
  {
    id: 'blue-brand', name: 'Голубой бренд', tag: 'как у топовых каналов',
    text: 'Клавиши, щиты, значки и таблички в одном синем стиле с градиентом',
    templates: range('classic', [31, 3, 80, 46, 55, 44, 15, 39, 45, 112, 57, 25, 24, 56, 65]),
    palette: 'ocean', font: { id: 'unbounded', name: 'Unbounded' }, on: ['fill'], angle: 160,
  },
  {
    id: 'neon-chars', name: 'Неон-персонажи', tag: 'яркие и живые',
    text: 'Жесты и персонажи с ником в неоновом градиенте',
    templates: range('characters', [19, 23, 27, 30, 40, 41, 44, 6, 16, 26, 37, 42]),
    palette: 'neon', font: { id: 'rubik-mono', name: 'Rubik Mono' }, on: ['text'], angle: 90,
  },
  {
    id: 'love', name: 'Любовь', tag: 'для своих',
    text: 'Сердечки, мишки и признания — нежная розовая подборка',
    templates: range('love', [4, 7, 14, 15, 17, 18, 22, 23, 25, 27, 3, 9]),
    palette: 'love', font: { id: 'pacifico', name: 'Pacifico' }, on: ['text'], angle: 135,
  },
  {
    id: 'finance', name: 'Финансы', tag: 'для бизнеса и крипты',
    text: 'Монеты, графики и кейсы в золотом градиенте',
    templates: range('finance', [2, 3, 6, 8, 9, 10, 11, 13, 15, 17, 1, 12]),
    palette: 'gold', font: { id: 'montserrat', name: 'Montserrat' }, on: ['fill'], angle: 180,
  },
  {
    id: 'gifts', name: 'Подарки', tag: 'праздничный',
    text: 'Подарки, игрушки и сюрпризы в конфетных цветах',
    templates: range('gift', [2, 5, 6, 8, 9, 13, 19, 21, 31, 18, 20, 3]),
    palette: 'candy', font: { id: 'lobster', name: 'Lobster' }, on: ['text'], angle: 135,
  },
];

// шаблон есть в каталоге (категория и номер в её пределах)
function known(id) {
  const m = /^([a-z]+)-(\d{3})$/.exec(id);
  const cat = m && state.categories.find((c) => c.id === m[1]);
  return Boolean(cat) && Number(m[2]) >= 1 && Number(m[2]) <= cat.count;
}

/** Шаблоны набора, которые есть в каталоге (и влезают в пак эмодзи). */
export const kitTemplates = (kit) => kit.templates.filter(known).slice(0, limitFor('emoji'));

/** Оформление набора: подборка цветов, шрифт и градиент (ник и логотип — пользователя).
 *  native — шаблоны остаются в родных цветах, от подборки берётся только градиент. */
export function kitLook(kit) {
  const p = paletteById(kit.palette);
  return { color: kit.native ? null : p.fill, stroke: kit.native ? null : p.stroke, font: kit.font, size: 100, place: null,
    grad: { a: p.a, b: p.b, angle: kit.angle, on: [...kit.on] } };
}

/** Выбрать набор: его шаблоны становятся выбором, а его оформление — общими настройками всех стикеров. */
export function applyKit(kit) {
  state.type = 'emoji';
  state.selection = kitTemplates(kit);
  state.overrides = {};
  savePrefs();
  bus.emit('type');
  bus.emit('selection');
  setLookForAll(kitLook(kit));
}
