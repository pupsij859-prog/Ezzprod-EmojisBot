// Экран «Оформление»: витрина с живым превью на CRT Warp, выбранные шаблоны, тип пака,
// ник, логотип, заливка и обводка — для всех стикеров сразу или для одного (того, что в витрине) — и создание пака.
import { api, ApiError, newIdempotencyKey } from '../api.js';
import { BigEmoji, Button, Group, Row, SectionTitle, setBusy } from '../components.js';
import { mountCrtWarp } from '../crt.js';
import { openOverlay } from '../nav.js';
import {
  bus, clearSelection, DEFAULT_COLOR, DEFAULT_STROKE, editFor, fontInUse, hasOwnLook, logoInUse, lookFor, refreshMe, setItemLook, setLookForAll,
  setType, state, toggleSelection,
} from '../store.js';
import { haptic, tgUser } from '../tg.js';
import { lookOf, TgsPreview, watchVisibility } from '../tgs.js';
import { emojiWord, gens, h, icon, toast } from '../ui.js';
import { openBuy } from './buy.js';
import { openCreating } from './creating.js';
import { openAdvanced } from './editor.js';
import { openStyles } from './styles.js';

const LOGO_ERRORS = {
  logo_format: 'Поддерживаем png, jpg и webp',
  logo_too_big: 'Файл слишком большой',
  logo_invalid: 'Не получилось прочитать картинку',
  logo_animated: 'Анимированные картинки не подходят, загрузи обычную',
  logo_quota: 'Слишком много загруженных логотипов',
};

const FONT_ERRORS = {
  font_otf: 'Этот шрифт в формате OpenType (CFF). Нужен .ttf',
  font_web: 'Это веб-шрифт (woff). Нужен .ttf',
  font_invalid: 'Не получилось прочитать шрифт. Нужен .ttf',
  font_empty: 'В шрифте нет обычных букв',
  font_too_big: 'Файл шрифта слишком большой',
  font_quota: 'Слишком много загруженных шрифтов: убери ненужные',
};

function Segmented(options, value, onChange) {
  let current = value;
  const buttons = options.map((o) => h('button', {
    class: `segmented__btn${o.id === value ? ' is-active' : ''}`, type: 'button', role: 'tab',
    'aria-selected': String(o.id === value), dataset: { id: o.id },
    onclick: () => { if (o.id !== current) { haptic.select(); onChange(o.id); } },
  }, o.label));
  const el = h('div', { class: 'segmented', role: 'tablist', dataset: { index: String(options.findIndex((o) => o.id === value)) } }, ...buttons);
  return {
    el,
    set(id) {
      current = id;
      el.dataset.index = String(options.findIndex((o) => o.id === id));
      buttons.forEach((b) => {
        const on = b.dataset.id === id;
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-selected', String(on));
      });
    },
  };
}

// ── цвет: «Заливка» (основной цвет стикера) или «Обводка» (контуры и надпись). Пусто — родной цвет шаблона
function ColorRow({ label, suggest, get, set }) {
  const value = h('span', { class: 'row__value' }, 'как в шаблоне');
  const input = h('input', { class: 'swatch__input', type: 'color', value: get() || suggest, 'aria-label': label });
  const dot = h('span', { class: 'swatch__dot' });
  const reset = h('button', { class: 'color-reset', type: 'button', onclick: () => { haptic.select(); set(null); paint(); } }, 'сбросить');
  const paint = () => {
    const c = get();
    value.hidden = Boolean(c);
    reset.hidden = !c;
    dot.classList.toggle('is-own', !c);
    if (c) dot.style.setProperty('--c', c); else dot.style.removeProperty('--c');
    input.value = c || suggest;
  };
  input.addEventListener('input', () => { set(input.value.toLowerCase()); paint(); });
  paint();
  const el = h('div', { class: 'row' },
    h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, label)), reset, value, h('label', { class: 'swatch' }, input, dot));
  return { el, paint };
}

// ── лента выбранных — она же выбор, что меняем: «Все» — все стикеры, миниатюра — только этот стикер
// (он же крупно в витрине). Крестик убирает шаблон, «+» — к шаблонам
function PickedStrip(close, onFocus) {
  const strip = h('div', { class: 'strip', role: 'list', 'aria-label': 'выбранные шаблоны' });
  const seen = watchVisibility(strip);
  let players = new Map(); // id → проигрыватель (переиспользуем при перерисовке ленты)
  let looks = new Map(); // id → параметры превью этого стикера
  let focused = null;
  const paint = () => {
    const next = new Map();
    const items = state.selection.map((id) => {
      const player = players.get(id) || TgsPreview(id, { look: looks.get(id) || null, lazy: true, cls: 'tgs--strip' });
      if (!players.has(id)) seen.add(player);
      next.set(id, player);
      return h('div', {
        class: `strip__item${id === focused ? ' is-focus' : ''}${hasOwnLook(id) ? ' is-own' : ''}`,
        role: 'listitem', dataset: { id }, onclick: () => { haptic.select(); onFocus(id); },
      },
        player.el,
        h('button', { class: 'strip__del', type: 'button', 'aria-label': 'убрать', onclick: (e) => {
          e.stopPropagation();
          haptic.select();
          toggleSelection(id);
        } }, icon('close')));
    });
    players.forEach((pl, id) => { if (!next.has(id)) pl.destroy(); });
    players = next;
    const all = state.selection.length > 1
      ? h('button', { class: `strip__all${focused === '*' ? ' is-focus' : ''}`, type: 'button', dataset: { id: '*' },
        'aria-label': 'менять все стикеры', onclick: () => { haptic.select(); onFocus('*'); } },
      icon('grid'), h('span', null, 'Все'))
      : null;
    strip.replaceChildren(...(all ? [all] : []), ...items,
      h('button', { class: 'strip__add', type: 'button', 'aria-label': 'добавить ещё', onclick: () => { haptic.tap(); close(); } },
        icon('plus')));
  };
  paint();
  return {
    el: strip,
    paint,
    apply(map) { looks = map; players.forEach((pl, id) => pl.update(map.get(id) || null)); },
    mark(id) {
      focused = id;
      strip.querySelectorAll('.strip__item, .strip__all').forEach((n) => {
        n.classList.toggle('is-focus', n.dataset.id === id);
        if (n.dataset.id !== '*') n.classList.toggle('is-own', hasOwnLook(n.dataset.id));
      });
    },
    destroy() { seen.disconnect(); players.forEach((pl) => pl.destroy()); },
  };
}

// ── витрина: крупное живое превью выбранного элемента на голубом CRT Warp (как баннер на главной)
function Showcase() {
  const fx = h('div', { class: 'hero__fx', 'aria-hidden': 'true' });
  const stage = h('div', { class: 'show__stage' });
  const balance = h('b', { 'data-balance': '' });
  const pos = h('span', { class: 'show__pos' });
  const el = h('section', { class: 'show' }, fx,
    h('div', { class: 'show__top' },
      h('span', { class: 'show__chip' }, icon('gens', 'show__spark'), balance),
      pos),
    stage);
  let player = null;
  let shownId = null;
  let stopFx = () => {};
  return {
    el,
    show(id, look) {
      const i = state.selection.indexOf(id);
      pos.textContent = state.selection.length > 1 ? `${i + 1} из ${state.selection.length}` : '';
      if (id !== shownId || !player) {
        if (player) player.destroy();
        player = TgsPreview(id, { look, cls: 'tgs--hero' });
        shownId = id;
        stage.replaceChildren(player.el);
      } else {
        player.update(look);
      }
    },
    paint() { balance.textContent = String(state.me.balance); },
    // приглушённее, чем на главной: на этом фоне должен читаться стикер
    start() { stopFx(); stopFx = mountCrtWarp(fx, { brightness: 0.75, speed: 0.35 }); },
    destroy() { stopFx(); stopFx = () => {}; if (player) player.destroy(); player = null; shownId = null; },
  };
}

function EmptyOrder(close) {
  return h('div', { class: 'empty' },
    BigEmoji('bag'),
    h('h2', { class: 'empty__title' }, 'Пока ничего не выбрано'),
    h('p', { class: 'empty__text' }, 'Отметь на главной шаблоны, которые хочешь в свой пак'),
    Button({ label: 'К шаблонам', full: false, onClick: close }));
}

/** Экран оформления заказа (открывается плашкой «Оформить» над нижней панелью). */
export function openOrder() {
  let cleanup = () => {};
  openOverlay({
    kind: 'screen',
    label: 'заказ',
    className: 'layer--order',
    onClose: () => cleanup(),
    render: ({ close }) => {
      const box = h('div', { class: 'order' });
      cleanup = mountOrder(box, close);
      return box;
    },
  });
}

function mountOrder(container, close) {
  const offs = [];
  const showcase = Showcase();
  offs.push(() => showcase.destroy());
  const root = h('section', { class: 'screen screen--order' });
  container.append(root);

  // ключ идемпотентности: повтор того же запроса (потерялся ответ) не спишет генерации дважды;
  // если выбор или настройки изменились, ключ меняется
  let idemKey = newIdempotencyKey();
  const rekey = () => { idemKey = newIdempotencyKey(); };
  offs.push(bus.on('selection', rekey), bus.on('type', rekey), bus.on('personalization', rekey));

  let parts = null; // элементы формы, пока выбор не пуст
  const render = () => {
    const head = [h('header', { class: 'ohead' },
      h('button', { class: 'back-btn', type: 'button', 'aria-label': 'назад', onclick: () => { haptic.tap(); close(); } }, icon('chev-left')),
      h('h1', { class: 'ohead__title' }, 'Оформление'),
      h('span', { 'aria-hidden': 'true' }))];
    if (parts) { parts.destroy(); parts = null; }
    if (!state.selection.length) {
      showcase.destroy();
      root.replaceChildren(...head, EmptyOrder(close));
      return;
    }
    showcase.paint();
    parts = buildForm();
    root.replaceChildren(...head, h('div', { class: 'page' }, showcase.el), parts.el);
    requestAnimationFrame(() => { if (showcase.el.isConnected) showcase.start(); });
  };

  offs.push(bus.on('selection', () => {
    if (!state.selection.length || !parts) { render(); return; }
    parts.paintSelection();
  }));
  offs.push(bus.on('type', () => parts && parts.paintFooter()));
  offs.push(bus.on('me', () => { showcase.paint(); if (parts) parts.paintFooter(); }));
  offs.push(bus.on('personalization', () => parts && parts.paintText()));

  function buildForm() {
    const limits = state.me.limits;

    // ── что меняем: все стикеры или только тот, что в витрине
    let focusId = state.selection[0];  // стикер в витрине
    let scope = 'all';                 // 'all' — все стикеры, 'one' — только стикер в витрине
    const editsOne = () => scope === 'one' && state.selection.length > 1;
    const cur = () => (editsOne() ? lookFor(focusId)
      : { text: state.text, logo: state.logo, color: state.color, stroke: state.stroke, size: state.size, font: state.font });
    const setLook = (patch) => (editsOne() ? setItemLook(focusId, patch) : setLookForAll(patch));

    // ── живое превью: у каждого стикера свои ник, логотип и цвета
    let looks = new Map();
    let lookVersion = 0;
    const showHero = () => {
      if (!state.selection.includes(focusId)) focusId = state.selection[0];
      if (focusId) showcase.show(focusId, looks.get(focusId) || null);
      strip.mark(editsOne() ? focusId : '*');
    };
    const refreshLive = async () => {
      const mine = ++lookVersion;
      const ids = [...state.selection];
      const list = await Promise.all(ids.map((id) => lookOf(id)));
      if (mine !== lookVersion) return;
      looks = new Map(ids.map((id, i) => [id, list[i]]));
      strip.apply(looks);
      showHero();
    };

    // ── выбранные
    const strip = PickedStrip(close, (id) => {
      if (id === '*') scope = 'all';
      else { scope = 'one'; focusId = id; }
      showHero();
      paintFields();
    });
    const pickedTitle = h('b');
    const picked = h('div', { class: 'order__picked' },
      h('div', { class: 'order__picked-head' }, pickedTitle,
        h('button', { class: 'order__clear', type: 'button', onclick: () => { haptic.tap(); clearSelection(); } }, 'Очистить')),
      strip.el);

    // ── тип пака
    const seg = Segmented([{ id: 'emoji', label: 'Эмодзи' }, { id: 'sticker', label: 'Стикеры' }], state.type, setType);

    // ── метка у заголовка: что сейчас меняем; у стикера со своими настройками — «сбросить» к общим
    const scopeTag = h('span', { class: 'scope-tag' });
    const scopeReset = h('button', { class: 'scope-reset', type: 'button', onclick: () => {
      haptic.select();
      delete state.overrides[focusId];
      setLookForAll({});  // сохранить и перерисовать
    } }, 'сбросить');
    const persHead = h('div', { class: 'order__sechead' }, SectionTitle('Персонализация'), scopeTag, scopeReset);

    // ── ник
    const text = h('input', {
      class: 'field', type: 'text', value: cur().text || '', maxLength: limits.text_max,
      placeholder: 'ваш ник', autocomplete: 'off', autocapitalize: 'off', enterKeyHint: 'done',
      'aria-label': 'ник на стикере',
    });
    text.spellcheck = false;
    text.addEventListener('input', () => setLook({ text: text.value }));
    text.addEventListener('keydown', (e) => { if (e.key === 'Enter') text.blur(); });
    const textRow = h('label', { class: 'row row--field' },
      h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, 'Ник')), text);

    // ── размер ника: 100% — автоподбор (короткий ник крупнее, длинный мельче), дальше — свой множитель
    const sizeValue = h('span', { class: 'range-row__value' });
    const sizeInput = h('input', { class: 'range-row__input', type: 'range', min: 40, max: 160, step: 5, value: cur().size || 100,
      'aria-label': 'размер ника' });
    const paintSize = () => {
      const v = Number(sizeInput.value);
      sizeValue.textContent = v === 100 ? 'авто' : `${v}%`;
      sizeInput.style.setProperty('--p', `${((v - 40) / 120) * 100}%`);
    };
    sizeInput.addEventListener('input', () => { paintSize(); setLook({ size: Number(sizeInput.value) }); });
    sizeInput.addEventListener('dblclick', () => { sizeInput.value = '100'; paintSize(); setLook({ size: 100 }); });
    const sizeRow = h('label', { class: 'row range-row' },
      h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, 'Размер ника')), sizeInput, sizeValue);

    // ── логотип
    const file = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp', hidden: true });
    const logoValue = h('span', { class: 'row__value' });
    const logoRow = h('button', { class: 'row row--action', type: 'button', onclick: () => {
      if (cur().logo) return;
      file.click();
    } },
    h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, 'Логотип')), logoValue);
    const removeBtn = h('button', { class: 'logo-remove', type: 'button', 'aria-label': 'убрать логотип', onclick: (e) => {
      e.stopPropagation();
      const removed = cur().logo;
      setLook({ logo: null });
      paintLogo();
      // если логотип больше нигде не стоит — удаляем и на сервере, чтобы не занимал квоту загрузок
      if (removed && removed.id && !logoInUse(removed.id)) api.deleteLogo(removed.id).catch(() => {});
    } }, icon('close'));
    function paintLogo(busy) {
      logoValue.replaceChildren();
      logoRow.classList.toggle('is-busy', Boolean(busy));
      if (busy) { logoValue.append(h('span', { class: 'spinner', 'aria-label': 'загружаем' })); return; }
      const logo = cur().logo;
      if (logo) {
        logoValue.append(h('img', { class: 'logo-thumb', src: logo.thumb, alt: '' }),
          h('span', { class: 'logo-name' }, logo.name), removeBtn);
      } else {
        logoValue.append(h('span', null, 'загрузить'), icon('chev-right', 'row__chev'));
      }
    }
    file.addEventListener('change', async () => {
      const f = file.files && file.files[0];
      file.value = '';
      if (!f) return;
      if (f.size > limits.logo_max_bytes) { haptic.error(); toast(`Файл больше ${Math.round(limits.logo_max_bytes / 1048576)} МБ`, 'warn'); return; }
      paintLogo(true);
      try {
        const logo = await api.uploadLogo(f);
        setLook({ logo });
        haptic.success();
      } catch (err) {
        haptic.error();
        const field = err instanceof ApiError ? (err.code === 'too_large' ? 'logo_too_big' : err.detail.field) : null;
        toast(LOGO_ERRORS[field] || 'Не получилось загрузить логотип', 'warn');
      }
      paintLogo(false);
    });

    // ── свой шрифт ника: .ttf с компьютера/телефона; пусто — стандартный
    const fontFile = h('input', { type: 'file', accept: '.ttf,font/ttf,application/x-font-ttf,.otf', hidden: true });
    const fontValue = h('span', { class: 'row__value' });
    const fontRow = h('button', { class: 'row row--action', type: 'button', onclick: () => {
      if (cur().font) return;
      fontFile.click();
    } },
    h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, 'Шрифт')), fontValue);
    const fontRemove = h('button', { class: 'logo-remove', type: 'button', 'aria-label': 'вернуть стандартный шрифт', onclick: (e) => {
      e.stopPropagation();
      const removed = cur().font;
      setLook({ font: null });
      paintFont();
      if (removed && !fontInUse(removed.id)) api.deleteFont(removed.id).catch(() => {});
    } }, icon('close'));
    function paintFont(busy) {
      fontValue.replaceChildren();
      fontRow.classList.toggle('is-busy', Boolean(busy));
      if (busy) { fontValue.append(h('span', { class: 'spinner', 'aria-label': 'загружаем' })); return; }
      const font = cur().font;
      if (font) fontValue.append(h('span', { class: 'logo-name' }, font.name), fontRemove);
      else fontValue.append(h('span', null, 'стандартный'), icon('chev-right', 'row__chev'));
    }
    fontFile.addEventListener('change', async () => {
      const f = fontFile.files && fontFile.files[0];
      fontFile.value = '';
      if (!f) return;
      if (f.size > limits.font_max_bytes) { haptic.error(); toast(FONT_ERRORS.font_too_big, 'warn'); return; }
      paintFont(true);
      try {
        const font = await api.uploadFont(f);
        setLook({ font });
        haptic.success();
      } catch (err) {
        haptic.error();
        const field = err instanceof ApiError ? (err.code === 'too_large' ? 'font_too_big' : err.detail.field) : null;
        toast(FONT_ERRORS[field] || 'Не получилось загрузить шрифт', 'warn');
      }
      paintFont(false);
    });

    // ── сохранённые стили (pro) — применяются ко всем стикерам
    const stylesRow = Row({
      label: 'Сохранённые стили', pro: state.me.plan !== 'pro',
      onClick: () => openStyles(() => paintFields()),
    });

    // ── продвинутые настройки (pro): редактор стикера, который сейчас в витрине
    const advancedRow = Row({
      label: 'Продвинутые настройки', sub: ' ', pro: state.me.plan !== 'pro', iconName: 'palette',
      onClick: () => openAdvanced(focusId),
    });
    advancedRow.classList.add('row--advanced');
    const advancedSub = advancedRow.querySelector('.row__sub');

    // ── цвета
    const fillRow = ColorRow({ label: 'Заливка', suggest: DEFAULT_COLOR, get: () => cur().color, set: (c) => setLook({ color: c }) });
    const strokeRow = ColorRow({ label: 'Обводка', suggest: DEFAULT_STROKE, get: () => cur().stroke, set: (c) => setLook({ stroke: c }) });

    // поля показывают настройки того, что сейчас меняем
    function paintFields() {
      const many = state.selection.length > 1;
      scopeTag.hidden = !many;
      scopeTag.textContent = editsOne() ? `стикер ${state.selection.indexOf(focusId) + 1}` : 'все стикеры';
      scopeTag.classList.toggle('is-one', editsOne());
      scopeReset.hidden = !(editsOne() && hasOwnLook(focusId));
      if (document.activeElement !== text) text.value = cur().text || '';
      if (document.activeElement !== sizeInput) sizeInput.value = String(cur().size || 100);
      paintSize();
      const edited = Boolean(editFor(focusId));
      advancedSub.textContent = many
        ? `стикер ${state.selection.indexOf(focusId) + 1}${edited ? ' · есть правки' : ''}`
        : (edited ? 'есть правки' : 'части, цвета и эффекты');
      paintLogo(false);
      paintFont(false);
      fillRow.paint();
      strokeRow.paint();
    }
    paintFields();

    // эмодзи-паки в сообщениях используют подписчики Telegram Premium: предупреждаем до списания генераций
    const premiumNote = h('p', { class: 'order__hint' }, 'Эмодзи в сообщениях доступны с Telegram Premium. Стикеры — всем.');

    // ── низ: итог и кнопка
    const summary = h('p', { class: 'checkout__summary' });
    const cta = Button({ label: '', onClick: () => submit() });
    const paintFooter = () => {
      const n = state.selection.length;
      const balance = state.me.balance;
      const enough = balance >= n;
      summary.classList.toggle('is-warn', !enough);
      summary.replaceChildren(
        h('b', null, emojiWord(n, state.type)),
        h('span', { class: 'checkout__balance' }, enough ? `${gens(n)} из ${balance}` : `не хватает ${gens(n - balance)}`));
      // не хватает генераций → кнопка сразу ведёт к докупке нужного количества
      cta.classList.toggle('btn--buy', !enough);
      cta.classList.toggle('btn--primary', enough);
      cta.querySelector('.btn__label').textContent = enough
        ? 'Создать пак'
        : `Докупить ${gens(n - balance)}`;
      premiumNote.hidden = state.type !== 'emoji' || Boolean(tgUser && tgUser.is_premium);
      seg.set(state.type);
      pickedTitle.textContent = `Выбрано: ${emojiWord(n, state.type)}`;
    };

    async function submit() {
      const n = state.selection.length;
      if (!n) return;
      if (state.me.balance < n) {
        haptic.error();
        toast(`Не хватает ${gens(n - state.me.balance)}`, 'warn');
        openBuy({ qty: Math.max(1, n - state.me.balance) });
        return;
      }
      setBusy(cta, true, 'Создаём…');
      try {
        const pack = await api.createPack({
          type: state.type,
          template_ids: state.selection,
          text: state.text,
          logo_id: state.logo ? state.logo.id : null,
          font_id: state.font ? state.font.id : null,
          color: state.color,
          stroke: state.stroke,
          size: state.size,
          overrides: overridesPayload(),
        }, idemKey);
        haptic.success();
        close();
        clearSelection();
        refreshMe().catch(() => {});
        openCreating(pack);
      } catch (err) {
        haptic.error();
        setBusy(cta, false);
        if (err instanceof ApiError && err.status === 402) {
          toast(`Не хватает ${gens(err.detail.need || 1)}`, 'warn');
          openBuy({ qty: err.detail.need || 1 });
        } else if (err instanceof ApiError && err.status === 429) {
          toast('Слишком часто, подожди минутку', 'warn');
        } else {
          toast('Не получилось создать пак, попробуй ещё раз', 'error');
        }
      }
    }

    // свои настройки стикеров — серверу: {id шаблона: {text?, logo_id?, color?, stroke?, size?, edit?}}
    function overridesPayload() {
      const out = {};
      state.selection.forEach((id) => {
        const own = state.overrides[id];
        if (!own || !Object.keys(own).length) return;
        const item = {};
        if ('text' in own) item.text = own.text;
        if ('color' in own) item.color = own.color;
        if ('stroke' in own) item.stroke = own.stroke;
        if ('size' in own) item.size = own.size;
        if (own.edit && state.me.plan === 'pro') item.edit = own.edit;
        if ('logo' in own) item.logo_id = own.logo ? own.logo.id : null;
        if ('font' in own) item.font_id = own.font ? own.font.id : null;
        out[id] = item;
      });
      return out;
    }

    paintFooter();
    refreshLive();
    let timer = 0;
    const el = h('div', { class: 'page order-page' },
      picked,
      SectionTitle('Тип пака'), seg.el, premiumNote,
      persHead,
      Group(textRow, sizeRow, fontRow, logoRow, fillRow.el, strokeRow.el, stylesRow, file, fontFile),
      h('div', { class: 'gap' }),
      Group(advancedRow),
      h('div', { class: 'checkout' }, summary, cta));

    return {
      el,
      paintFooter,
      paintSelection: () => { strip.paint(); paintFooter(); showHero(); paintFields(); refreshLive(); },
      // ник, логотип или цвет поменялись — перерисовываем превью (ввод ника — с небольшой паузой)
      paintText: () => { paintFields(); clearTimeout(timer); timer = setTimeout(refreshLive, 180); },
      destroy: () => { clearTimeout(timer); strip.destroy(); },
    };
  }

  render();
  return () => { offs.forEach((off) => off()); if (parts) parts.destroy(); };
}
