// Покупка генераций, как витрина магазина: сетка карточек-пакетов (анимированный эмодзи, название, цена,
// выгода и полоска), снизу — плавающая панель «− N +» и «Купить · N ₽», которая открывает шторку
// со способами оплаты списком (Звёзды, xRocket, CryptoBot, СБП).
// Цены — только из state.pricing (сервер, app/pricing.py), клиент ничего не пересчитывает сам.
import { payFor } from '../billing.js';
import { BigEmoji, CloseButton } from '../components.js';
import { openOverlay } from '../nav.js';
import { bus, priceFor, state } from '../store.js';
import { haptic, openLink } from '../tg.js';
import { gens, h, icon, plural, prefersReducedMotion, rub, toast } from '../ui.js';
import { openPromo } from './promo.js';
import { TgsPreview } from '../tgs.js';

// TGS-шаблоны карточек пакетов по порядку (от маленького к большому)
export const PACK_LOOKS = [
  { id: 'love-006', emoji: 'sparkle', glow: '#c084fc' },    // 5: хрустальный куб с сердечком
  { id: 'finance-009', emoji: 'fire', glow: '#f97316' },   // 10: золотая монета с кольцом
  { id: 'gift-033', emoji: 'gift', glow: '#ef4444' },      // 20: синий персонаж в обнимку с сердцем
  { id: 'love-022', emoji: 'party', glow: '#ec4899' },     // 30: розовый мишка с сердцем
  { id: 'gift-030', emoji: 'gem', glow: '#06b6d4' },       // 50: чаша с сияющими алмазами
  { id: 'classic-055', emoji: 'crown', glow: '#eab308' },   // 100: королевская корона
];

const BY_QTY = {
  5: PACK_LOOKS[0],
  10: PACK_LOOKS[1],
  20: PACK_LOOKS[2],
  30: PACK_LOOKS[3],
  50: PACK_LOOKS[4],
  100: PACK_LOOKS[5],
};

/** TGS-шаблон и цвет свечения пакета — одинаковые на экране покупки и в профиле. */
export function packLook(qty) {
  if (qty in BY_QTY) return BY_QTY[qty];
  const p = presets();
  const i = Math.max(0, p.indexOf(qty));
  return PACK_LOOKS[i % PACK_LOOKS.length];
}

/** Пакеты: 5, 10, 20, порог скидки, порог оптовой цены, 100 — в пределах max_qty. */
export function presets() {
  const p = state.pricing;
  return [...new Set([5, 10, 20, p.discount_from, p.bulk_from, 100])]
    .filter((q) => q >= 1 && q <= p.max_qty)
    .sort((a, b) => a - b);
}

/** Бейдж пакета: оптовая цена или скидка. */
export function presetBadge(qty) {
  const userDisc = (state.me && state.me.discount_pct) || 0;
  if (userDisc > 0) return { text: `−${userDisc}% промо`, tone: 'sale' };
  const p = state.pricing;
  if (qty >= p.bulk_from) return { text: 'Выгодно', tone: 'best' };
  if (qty >= p.discount_from) return { text: `−${p.discount_pct}%`, tone: 'sale' };
  return null;
}

/** Сколько Stars спишет Telegram за сумму в ₽ (как на сервере: вверх до целого). */
export function starsFor(total) {
  const rate = (state.pricing && state.pricing.rub_per_star) || 1;
  return Math.max(1, Math.ceil(Math.round((total / rate) * 1e6) / 1e6));
}

const docLink = (href, text) => h('a', { href, onclick: (e) => { e.preventDefault(); openLink(`${location.origin}${href}`); } }, text);
const termsNote = () => h('p', { class: 'buy__terms' }, 'Нажимая на способ оплаты, вы принимаете ', docLink('/docs/terms.html', 'условия покупки'),
  ' и ', docLink('/docs/refund.html', 'правила возврата'));

/** Шторка «Выберите способ оплаты»: строки с иконкой, названием и подписью; нажатие сразу ведёт к оплате. */
export function openPayMethods({ qty, onPaid }) {
  const methods = state.pricing.methods || [
    { id: 'stars', title: 'Звёзды', sub: '', enabled: true },
    { id: 'xrocket', title: 'xRocket', sub: 'USDT, TON и другие монеты', enabled: true },
    { id: 'cryptobot', title: 'CryptoBot', sub: 'Оплата криптовалютой', enabled: true },
    { id: 'sbp', title: 'СБП', sub: 'Через приложение банка', enabled: false },
  ];
  const total = priceFor(qty);
  openOverlay({
    kind: 'sheet', label: 'способ оплаты', className: 'sheet--pay',
    render: ({ close }) => {
      let busy = false;
      const rows = methods.map((m) => {
        const n = starsFor(total);
        const sub = m.id === 'stars' ? `${n} ${plural(n, 'звезда', 'звезды', 'звёзд')} · ${m.sub}` : m.sub;
        const row = h('button', {
          class: `pmrow pmrow--${m.id}`, type: 'button', disabled: !m.enabled || undefined,
          onclick: async () => {
            if (busy) return;
            busy = true;
            haptic.tap();
            row.classList.add('is-busy');
            const status = await payFor(qty, m.id);
            row.classList.remove('is-busy');
            busy = false;
            if (status === 'paid') {
              haptic.success();
              toast('Готово: генерации на балансе');
              close();
              if (onPaid) onPaid();
            }
          },
        },
        h('img', { class: 'pmrow__icon', src: `/assets/pay/${m.id}.webp?v=7`, alt: '', draggable: 'false' }),
        h('span', { class: 'pmrow__text' }, h('b', null, m.title), h('span', null, sub)),
        m.enabled ? h('span', { class: 'pmrow__go' }, h('i', { class: 'pmrow__spin', 'aria-hidden': 'true' }), icon('chev-right', 'pmrow__chev'))
          : h('span', { class: 'pmrow__soon' }, 'Скоро'));
        return row;
      });
      return [
        h('div', { class: 'sheet__grab pmhead' },
          h('h2', { class: 'pmhead__title' }, 'Выберите способ оплаты'),
          h('p', { class: 'pmhead__sub' }, icon('gens', 'pmhead__spark'), `${gens(qty)} · `, rub(total))),
        h('div', { class: 'sheet__body' }, h('div', { class: 'pmlist' }, ...rows)),
        h('div', { class: 'sheet__footer' }, termsNote()),
      ];
    },
  });
}

/**
 * Число, которое «прокручивается» по цифрам: каждая цифра — лента 0–9, едет к новому значению
 * с небольшой задержкой от разряда к разряду и размытием в движении. → { el, set(n) }
 */
function Odometer(cls) {
  const el = h('span', { class: `odo ${cls}`.trim(), 'aria-live': 'polite' });
  let digits = [];
  const column = () => {
    const strip = h('span', { class: 'odo__strip' }, ...Array.from({ length: 10 }, (_, d) => h('span', null, String(d))));
    return { box: h('span', { class: 'odo__digit' }, strip), strip, value: 0 };
  };
  function set(n) {
    const text = String(n);
    el.setAttribute('aria-label', text);
    // разрядов стало больше или меньше — добавляем/убираем ленты слева
    while (digits.length < text.length) { const c = column(); digits.unshift(c); el.prepend(c.box); }
    while (digits.length > text.length) { digits.shift().box.remove(); }
    const still = prefersReducedMotion();
    [...text].forEach((ch, i) => {
      const c = digits[i];
      const d = Number(ch);
      const moved = c.value !== d;
      c.value = d;
      c.strip.style.transitionDelay = still ? '0ms' : `${(text.length - 1 - i) * 55}ms`;
      c.strip.style.transform = `translateY(${-d * 10}%)`;
      if (moved && !still) {
        c.box.classList.remove('is-rolling');
        void c.box.offsetWidth;
        c.box.classList.add('is-rolling');
      }
    });
  }
  return { el, set };
}

/** Подпись справа от цены: цена за штуку у опта, иначе экономия. */
function perk(qty) {
  const p = state.pricing;
  const sum = priceFor(qty);
  if (qty >= p.bulk_from) return `по ${Math.round((sum / qty) * 10) / 10} ₽`;
  const save = qty * p.unit - sum;
  return save > 0 ? `−${save} ₽` : `${p.unit} ₽/шт`;
}

export function openBuy({ qty } = {}) {
  let off = () => {};
  openOverlay({
    kind: 'screen',
    label: 'покупка генераций',
    className: 'layer--buy',
    onClose: () => {
      off();
      tgsList.forEach((t) => t.destroy());
    },
    render: ({ close }) => {
      const p = state.pricing;
      const packs = presets();
      const biggest = packs[packs.length - 1];
      let current = Math.min(Math.max(qty || p.default_qty, 1), p.max_qty);

      // ── сетка пакетов (в рублях)
      const tgsList = [];
      const cards = packs.map((q, i) => {
        const badge = presetBadge(q);
        const look = packLook(q);
        const tgs = TgsPreview(look.id, { autoplay: true, cls: 'bcard__tgs' });
        tgsList.push(tgs);
        return h('button', { class: 'bcard', type: 'button', dataset: { qty: String(q) }, style: { '--i': String(i), '--glow': look.glow }, onclick: () => {
          if (q === current) return;
          haptic.select();
          current = q;
          paint();
        } },
        tgs.el,
        badge ? h('span', { class: `bcard__badge bcard__badge--${badge.tone}` }, badge.text) : null,
        h('b', { class: 'bcard__title' }, gens(q)),
        h('span', { class: 'bcard__row' },
          h('span', { class: 'bcard__price' }, String(priceFor(q)), h('small', null, '₽')),
          h('span', { class: 'bcard__perk' }, perk(q))),
        h('span', { class: 'bcard__bar', 'aria-hidden': 'true' },
          h('i', { style: { width: `${Math.max(8, Math.round((q / biggest) * 100))}%` } })));
      });

      // ── нижняя панель: − N +  ·  🛒 Купить · N ₽
      const count = h('span', { class: 'bbar__qty' });
      const step = (d) => () => { haptic.select(); current = Math.min(p.max_qty, Math.max(1, current + d)); paint(); };
      const minus = h('button', { class: 'bbar__step', type: 'button', 'aria-label': 'меньше', onclick: step(-1) }, icon('minus'));
      const plus = h('button', { class: 'bbar__step', type: 'button', 'aria-label': 'больше', onclick: step(1) }, icon('plus'));
      const sum = Odometer('odo--bar');
      const buyBtn = h('button', { class: 'bbar__buy', type: 'button', onclick: () => {
        haptic.tap();
        openPayMethods({ qty: current, onPaid: close });
      } }, BigEmoji('cart', 'bbar__emoji'), h('span', null, 'Купить · '), sum.el, h('span', null, ' ₽'));

      const balance = h('span', { 'data-balance': '' }, String(state.me.balance));

      function paint() {
        sum.set(priceFor(current));
        count.textContent = String(current);
        minus.disabled = current <= 1;
        plus.disabled = current >= p.max_qty;
        cards.forEach((c) => c.classList.toggle('is-on', Number(c.dataset.qty) === current));
      }
      paint();
      off = bus.on('me', () => { balance.textContent = String(state.me.balance); });

      const methods = (p.methods || []).filter((m) => m.enabled);
      return [
        CloseButton(close),
        h('div', { class: 'buy' },
          h('header', { class: 'buy__head' },
            h('h1', { class: 'buy__title' }, 'Пополнить баланс'),
            h('p', { class: 'buy__sub' }, '1 генерация = 1 эмодзи или стикер · не сгорают')),
          h('div', { class: 'bwallet' },
            h('span', { class: 'bwallet__label' }, 'На балансе'),
            h('b', { class: 'bwallet__value' }, balance, icon('gens', 'bwallet__spark')),
            h('span', { class: 'bwallet__perk' }, (state.me && state.me.discount_pct) ? `🎟 Скидка по промокоду: −${state.me.discount_pct}%` : `−${p.discount_pct}% от ${p.discount_from} шт · опт от ${p.bulk_from} шт`)),
          h('div', { class: 'bgrid' }, ...cards),
          h('div', { class: 'bextra' },
            h('span', { class: 'vpay' },
              h('span', { class: 'vpay__icons' }, ...methods.map((m) => h('img', { class: `vpay__icon vpay__icon--${m.id}`, src: `/assets/pay/${m.id}.webp?v=7`, alt: m.title, draggable: 'false' }))),
              h('span', { class: 'vpay__text' }, methods.map((m) => m.title).join(' · '))),
            h('button', { class: 'vpromo', type: 'button', onclick: () => { haptic.tap(); openPromo(); } }, icon('gift'), 'Промокод')),
          h('p', { class: 'buy__terms buy__terms--center' }, 'Покупая, вы принимаете ', docLink('/docs/terms.html', 'условия'), ' и ', docLink('/docs/refund.html', 'правила возврата')),
          h('div', { class: 'buy__bottom' },
            h('div', { class: 'bbar' }, h('div', { class: 'bbar__stepper' }, minus, count, plus), buyBtn))),
      ];
    },
  });
}
