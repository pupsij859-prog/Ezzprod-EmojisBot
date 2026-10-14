import { api, ApiError } from '../api.js';
import { BigEmoji, Button, Quote, Skeleton, setBusy } from '../components.js';
import { openOverlay } from '../nav.js';
import { setPersonalization, state } from '../store.js';
import { haptic } from '../tg.js';
import { h, icon, toast } from '../ui.js';
import { openBuy } from './buy.js';

/** Модалка «сохранённые стили». onApply вызывается после применения стиля. */
export function openStyles(onApply) {
  openOverlay({
    kind: 'modal',
    label: 'сохранённые стили',
    className: 'modal--styles',
    render: ({ close }) => {
      const body = h('div', { class: 'styles__body' }, Skeleton('skeleton--line'), Skeleton('skeleton--line'));
      load();
      return [h('h2', { class: 'modal__title' }, 'Сохранённые стили'), body,
        Button({ label: 'Закрыть', variant: 'ghost', onClick: close })];

      async function load() {
        try {
          paint(await api.styles());
        } catch {
          body.replaceChildren(h('p', { class: 'styles__hint' }, 'Не получилось загрузить, попробуй ещё раз'));
        }
      }

      function paint({ pro, styles, purchased, pro_min: proMin }) {
        body.replaceChildren();
        if (!pro) {
          const left = Math.max(1, proMin - purchased);
          body.append(
            h('div', { class: 'locked' },
              BigEmoji('lock', 'locked__icon'),
              h('p', { class: 'locked__title' }, 'Стили доступны в Pro'),
              Quote({ emoji: 'lock', title: 'Стили', text: 'сохраняй ник, заливку и обводку под своим названием и применяй к любому паку одним тапом.' }),
              h('div', { class: 'meter', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': proMin, 'aria-valuenow': purchased },
                h('i', { style: { width: `${Math.min(100, Math.round((purchased / proMin) * 100))}%` } })),
              h('p', { class: 'locked__text' }, `Pro открывается после покупки ${proMin} генераций: осталось ${left}`)),
            Button({ label: 'Купить генерации', onClick: () => { close(); openBuy({ qty: left }); } }));
          return;
        }
        const name = h('input', { class: 'field field--box', type: 'text', maxLength: 32, placeholder: 'название стиля',
          autocomplete: 'off', 'aria-label': 'название стиля' });
        const save = Button({ label: 'Сохранить', full: false, onClick: async () => {
          const value = name.value.trim();
          if (!value) { name.focus(); return; }
          setBusy(save, true);
          try {
            await api.addStyle({ name: value, text: state.text, color: state.color, stroke: state.stroke });
            haptic.success();
            name.value = '';
            toast('Стиль сохранён');
            await load();
          } catch (err) {
            haptic.error();
            toast(err instanceof ApiError && err.detail.field === 'too_many_styles' ? 'Слишком много стилей: удали ненужные' : 'Не получилось сохранить', 'warn');
          }
          setBusy(save, false);
        } });
        body.append(h('div', { class: 'styles__form' }, name, save));
        if (!styles.length) {
          body.append(h('p', { class: 'styles__hint' }, 'Пока нет сохранённых стилей'));
          return;
        }
        body.append(h('ul', { class: 'styles__list' }, styles.map((s) => h('li', { class: 'style' },
          h('button', { class: 'style__apply', type: 'button', onclick: () => {
            setPersonalization({ text: s.params.text || '', color: s.params.color || null, stroke: s.params.stroke || null });
            haptic.success();
            toast(`Стиль «${s.name}» применён`);
            close();
            if (onApply) onApply();
          } },
          h('span', { class: 'style__dots' },
            h('i', { class: s.params.color ? '' : 'is-own', style: s.params.color ? { background: s.params.color } : {} }),
            s.params.stroke ? h('i', { style: { background: s.params.stroke } }) : null),
          h('span', { class: 'style__name' }, s.name),
          h('span', { class: 'style__text' }, s.params.text || '')),
          h('button', { class: 'style__del', type: 'button', 'aria-label': `удалить стиль ${s.name}`, onclick: async () => {
            try { await api.deleteStyle(s.id); haptic.tap(); await load(); } catch { toast('Не получилось удалить', 'warn'); }
          } }, icon('trash')))))
        );
      }
    },
  });
}
