// Вкладка «Заказать»: выбранные эмодзи, тип пака, персонализация, цвета и создание пака.
import { api, ApiError, newIdempotencyKey, previewUrl } from '../api.js';
import { Button, GlowIcon, Group, Quote, Row, SectionHead, SectionTitle, setBusy, Topbar } from '../components.js';
import { goTab } from '../router.js';
import { bus, clearSelection, refreshMe, setPersonalization, setType, state, toggleSelection } from '../store.js';
import { haptic, tgUser } from '../tg.js';
import { emojiWord, gens, h, icon, toast } from '../ui.js';
import { openBuy } from './buy.js';
import { openCreating } from './creating.js';
import { openStyles } from './styles.js';

const LOGO_ERRORS = {
  logo_format: 'Поддерживаем png, jpg и webp',
  logo_too_big: 'Файл слишком большой',
  logo_invalid: 'Не получилось прочитать картинку',
  logo_animated: 'Анимированные картинки не подходят, загрузи обычную',
  logo_quota: 'Слишком много загруженных логотипов',
};

function Segmented(options, value, onChange) {
  const buttons = options.map((o) => h('button', {
    class: `segmented__btn${o.id === value ? ' is-active' : ''}`, type: 'button', role: 'tab',
    'aria-selected': String(o.id === value), dataset: { id: o.id },
    onclick: () => { if (o.id !== state.type) { haptic.select(); onChange(o.id); } },
  }, o.label));
  const el = h('div', { class: 'segmented', role: 'tablist', dataset: { index: String(options.findIndex((o) => o.id === value)) } }, ...buttons);
  return {
    el,
    set(id) {
      el.dataset.index = String(options.findIndex((o) => o.id === id));
      buttons.forEach((b) => {
        const on = b.dataset.id === id;
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-selected', String(on));
      });
    },
  };
}

function ColorRow(label, key, ring) {
  const hex = h('span', { class: 'row__value row__value--mono' }, state[key]);
  const input = h('input', { class: 'swatch__input', type: 'color', value: state[key], 'aria-label': label });
  const dot = h('span', { class: `swatch__dot${ring ? ' swatch__dot--ring' : ''}` });
  const paint = () => {
    hex.textContent = state[key];
    dot.style.setProperty('--c', state[key]);
  };
  input.addEventListener('input', () => { setPersonalization({ [key]: input.value.toLowerCase() }); paint(); });
  paint();
  const swatch = h('label', { class: 'swatch' }, input, dot);
  return h('div', { class: 'row' },
    h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, label)), hex, swatch);
}

// ── лента выбранных: тап по крестику убирает шаблон
function PickedStrip() {
  const strip = h('div', { class: 'strip', role: 'list', 'aria-label': 'выбранные шаблоны' });
  const paint = () => {
    strip.replaceChildren(
      ...state.selection.map((id) => h('div', { class: 'strip__item', role: 'listitem' },
        h('img', { src: previewUrl(id), alt: '', loading: 'lazy', decoding: 'async', draggable: false }),
        h('span', { class: 'tcard__text', 'aria-hidden': 'true' }, state.text),
        h('button', { class: 'strip__del', type: 'button', 'aria-label': 'убрать', onclick: () => { haptic.select(); toggleSelection(id); } },
          icon('close')))),
      h('button', { class: 'strip__add', type: 'button', 'aria-label': 'добавить ещё', onclick: () => { haptic.tap(); goTab('home'); } },
        icon('plus')));
  };
  paint();
  return { el: strip, paint };
}

function EmptyOrder() {
  return h('div', { class: 'empty' },
    GlowIcon('bag'),
    h('h2', { class: 'empty__title' }, 'Пока ничего не выбрано'),
    h('p', { class: 'empty__text' }, 'Открой категорию на главной и отметь эмодзи, которые хочешь в свой пак'),
    Button({ label: 'К эмодзи', full: false, onClick: () => goTab('home') }));
}

export function mountOrder(container) {
  const offs = [];
  const root = h('section', { class: 'screen screen--order' });
  container.append(root);

  // ключ идемпотентности: повтор того же запроса (потерялся ответ) не спишет генерации дважды;
  // если выбор или настройки изменились, ключ меняется
  let idemKey = newIdempotencyKey();
  const rekey = () => { idemKey = newIdempotencyKey(); };
  offs.push(bus.on('selection', rekey), bus.on('type', rekey), bus.on('personalization', rekey));

  let parts = null; // элементы формы, пока выбор не пуст
  const render = () => {
    const head = [Topbar(), SectionHead('Заказать')];
    if (!state.selection.length) {
      parts = null;
      root.replaceChildren(...head, EmptyOrder());
      return;
    }
    parts = buildForm();
    root.replaceChildren(...head, parts.el);
  };

  offs.push(bus.on('selection', () => {
    if (!state.selection.length || !parts) { render(); return; }
    parts.paintSelection();
  }));
  offs.push(bus.on('type', () => parts && parts.paintFooter()));
  offs.push(bus.on('me', () => parts && parts.paintFooter()));
  offs.push(bus.on('personalization', () => parts && parts.paintText()));

  function buildForm() {
    const limits = state.me.limits;

    // ── выбранные
    const strip = PickedStrip();
    const pickedTitle = h('b');
    const picked = h('div', { class: 'card order__picked' },
      h('div', { class: 'order__picked-head' }, pickedTitle,
        h('button', { class: 'order__clear', type: 'button', onclick: () => { haptic.tap(); clearSelection(); } }, 'Очистить')),
      strip.el);

    // ── тип пака
    const seg = Segmented([{ id: 'emoji', label: 'Эмодзи' }, { id: 'sticker', label: 'Стикеры' }], state.type, setType);

    // ── текст
    const text = h('input', {
      class: 'field', type: 'text', value: state.text, maxLength: limits.text_max,
      placeholder: 'ваш ник', autocomplete: 'off', autocapitalize: 'off', enterKeyHint: 'done',
      'aria-label': 'текст на эмодзи',
    });
    text.spellcheck = false;
    text.addEventListener('input', () => setPersonalization({ text: text.value }));
    text.addEventListener('keydown', (e) => { if (e.key === 'Enter') text.blur(); });
    const textRow = h('label', { class: 'row row--field' },
      h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, 'Текст')), text);

    // ── логотип
    const file = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp', hidden: true });
    const logoValue = h('span', { class: 'row__value' });
    const logoRow = h('button', { class: 'row row--action', type: 'button', onclick: () => {
      if (state.logo) return;
      file.click();
    } },
    h('span', { class: 'row__main' }, h('span', { class: 'row__label' }, 'Логотип')), logoValue);
    const removeBtn = h('button', { class: 'logo-remove', type: 'button', 'aria-label': 'убрать логотип', onclick: (e) => {
      e.stopPropagation();
      setPersonalization({ logo: null });
      paintLogo();
    } }, icon('close'));
    function paintLogo(busy) {
      logoValue.replaceChildren();
      logoRow.classList.toggle('is-busy', Boolean(busy));
      if (busy) { logoValue.append(h('span', { class: 'spinner', 'aria-label': 'загружаем' })); return; }
      if (state.logo) {
        logoValue.append(h('img', { class: 'logo-thumb', src: state.logo.thumb, alt: '' }),
          h('span', { class: 'logo-name' }, state.logo.name), removeBtn);
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
        setPersonalization({ logo });
        haptic.success();
      } catch (err) {
        haptic.error();
        const field = err instanceof ApiError ? (err.code === 'too_large' ? 'logo_too_big' : err.detail.field) : null;
        toast(LOGO_ERRORS[field] || 'Не получилось загрузить логотип', 'warn');
      }
      paintLogo(false);
    });
    paintLogo(false);

    // ── сохранённые стили (pro)
    const stylesRow = Row({
      label: 'Сохранённые стили', pro: state.me.plan !== 'pro',
      onClick: () => openStyles(() => {
        text.value = state.text;
        paintColors();
      }),
    });

    // ── цвета
    const fillRow = ColorRow('Заливка', 'fill', false);
    const strokeRow = ColorRow('Обводка', 'stroke', true);
    const paintColors = () => {
      [[fillRow, 'fill'], [strokeRow, 'stroke']].forEach(([row, key]) => {
        row.querySelector('.swatch__input').value = state[key];
        row.querySelector('.row__value').textContent = state[key];
        row.querySelector('.swatch__dot').style.setProperty('--c', state[key]);
      });
    };

    // эмодзи-паки в сообщениях используют подписчики Telegram Premium: предупреждаем до списания генераций
    const premiumNote = Quote({ emoji: 'star', title: 'Важно', cls: 'order__note',
      text: 'кастомные эмодзи в сообщениях ставят подписчики Telegram Premium. Стикеры доступны всем.' });

    // ── низ: итог и кнопка
    const summary = h('p', { class: 'checkout__summary' });
    const cta = Button({ label: '', onClick: () => submit() });
    const paintFooter = () => {
      const n = state.selection.length;
      const balance = state.me.balance;
      const enough = balance >= n;
      summary.classList.toggle('is-warn', !enough);
      summary.replaceChildren(
        h('span', null, `${emojiWord(n, state.type)} · спишется ${gens(n)}`),
        h('span', { class: 'checkout__balance' }, enough ? `на балансе ${balance}` : `не хватает ${gens(n - balance)}`));
      // не хватает генераций → кнопка сразу ведёт к докупке нужного количества
      cta.classList.toggle('btn--buy', !enough);
      cta.classList.toggle('btn--primary', enough);
      cta.querySelector('.btn__label').textContent = enough
        ? (state.type === 'sticker' ? 'Создать стикерпак' : 'Создать эмодзи-пак')
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
          fill: state.fill,
          stroke: state.stroke,
        }, idemKey);
        haptic.success();
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

    paintFooter();
    const el = h('div', { class: 'page' },
      picked,
      SectionTitle('Тип пака'), seg.el, premiumNote,
      SectionTitle('Персонализация'),
      Group(textRow, logoRow, stylesRow, file),
      SectionTitle('Цвета'), Group(fillRow, strokeRow),
      h('div', { class: 'checkout' }, summary, cta));

    return {
      el,
      paintFooter,
      paintSelection: () => { strip.paint(); paintFooter(); },
      // текст пользователя поверх миниатюр
      paintText: () => { strip.el.querySelectorAll('.tcard__text').forEach((t) => { t.textContent = state.text; }); },
    };
  }

  render();
  return () => offs.forEach((off) => off());
}
