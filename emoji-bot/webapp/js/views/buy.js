import { payFor } from '../billing.js';
import { CloseButton, GlowIcon, Quote, Stepper, setBusy } from '../components.js';
import { openOverlay } from '../nav.js';
import { priceFor, state } from '../store.js';
import { haptic, openLink } from '../tg.js';
import { h, icon, stars, toast } from '../ui.js';

/** Строка тарифов: «6 ⭐ за штуку · −10% от 30 · от 50 по 5 ⭐» */
export function PriceLine() {
  const p = state.pricing;
  const star = () => icon('star-fill', 'stars__icon');
  return h('p', { class: 'priceline' },
    h('span', { class: 'stars' }, `${p.unit}`, star()), ' за штуку · ',
    `−${p.discount_pct}% от ${p.discount_from} · от ${p.bulk_from} по `,
    h('span', { class: 'stars' }, `${p.bulk_unit}`, star()));
}

/** Кнопка «оплатить N ⭐», которая сама обновляет сумму. */
export function PayButton(getQty, onPaid) {
  const sum = h('span', { class: 'pay__sum' });
  const btn = h('button', { class: 'btn btn--buy btn--block', type: 'button', onclick: async () => {
    haptic.tap();
    setBusy(btn, true);
    const status = await payFor(getQty());
    setBusy(btn, false);
    if (status === 'paid') {
      haptic.success();
      toast('Готово: генерации на балансе');
      if (onPaid) onPaid();
    }
  } }, h('span', { class: 'btn__label' }, 'Оплатить ', sum));
  const update = () => { sum.replaceChildren(stars(priceFor(getQty()))); };
  update();
  return { el: btn, update };
}

export function openBuy({ qty } = {}) {
  openOverlay({
    kind: 'screen',
    label: 'покупка генераций',
    className: 'layer--buy',
    render: ({ close }) => {
      const p = state.pricing;
      const start = Math.min(Math.max(qty || p.default_qty, 1), p.max_qty);
      let pay;
      const stepper = Stepper({ value: start, min: 1, max: p.max_qty, onChange: () => pay.update() });
      pay = PayButton(stepper.get, close);

      return [
        CloseButton(close),
        h('div', { class: 'buy' },
          GlowIcon('crown', 'buy__crown'),
          h('h1', { class: 'buy__title' }, 'Генерации'),
          h('p', { class: 'buy__sub' }, 'Не сгорают, ник и логотип включены'),
          PriceLine(),
          stepper.el,
          state.me.plan !== 'pro'
            ? Quote({ emoji: 'crown', title: 'Pro', cls: 'buy__note',
              text: `ещё ${Math.max(1, state.me.pro_min - state.me.purchased_total)} купленных генераций, и откроются сохранённые стили.` })
            : null,
          h('div', { class: 'buy__bottom' },
            pay.el,
            h('p', { class: 'buy__terms' }, 'Нажимая кнопку, вы принимаете ',
              h('a', { href: '/docs/terms.html', onclick: (e) => { e.preventDefault(); openLink(`${location.origin}/docs/terms.html`); } }, 'условия покупки')))),
      ];
    },
  });
}
