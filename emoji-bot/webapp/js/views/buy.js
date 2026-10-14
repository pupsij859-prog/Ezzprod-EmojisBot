// Покупка генераций, как витрина магазина: сетка карточек-пакетов (анимированный эмодзи, название, цена,
// выгода и полоска), снизу — плавающая панель «− N +» и «Купить · N ₽», которая открывает шторку
// со способами оплаты списком (Звёзды, СБП, xRocket, CryptoBot).
// Цены — только из state.pricing (сервер, app/pricing.py), клиент ничего не пересчитывает сам.
import { payFor } from '../billing.js';
import { BigEmoji, CloseButton } from '../components.js';
import { openOverlay } from '../nav.js';
import { bus, priceFor, state } from '../store.js';
import { haptic, openLink } from '../tg.js';
import { gens, h, icon, plural, prefersReducedMotion, rub, toast } from '../ui.js';

// эмодзи карточек по порядку пакетов: от маленького к большому
const PACK_EMOJI = ['sparkle', 'fire', 'gift', 'party', 'gem', 'crown'];
// цвет свечения за эмодзи карточки
const PACK_GLOW = ['#ffc83d', '#ff6a2b', '#ff4d4d', '#ff5fd2', '#55b8ff', '#ffb92e'];

/** Эмодзи и цвет свечения пакета — одинаковые на экране покупки и в профиле. */
export function packLook(qty) {
  const i = Math.max(0, presets().indexOf(qty));
  return { emoji: PACK_EMOJI[i % PACK_EMOJI.length], glow: PACK_GLOW[i % PACK_GLOW.length] };
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
  const p = state.pricing;
  if (qty >= p.bulk_from) return { text: 'Выгодно', tone: 'best' };
  if (qty >= p.discount_from) return { text: `−${p.discount_pct}%`, tone: 'sale' };
  return null;
}

/** Сколько Stars спишет Telegram за сумму в ₽ (как на сервере: вверх до целого). */
function starsFor(total) {
  const rate = state.pricing.rub_per_star || 1;
  return Math.max(1, Math.ceil(Math.round((total / rate) * 1e6) / 1e6));
}

const docLink = (href, text) => h('a', { href, onclick: (e) => { e.preventDefault(); openLink(`${location.origin}${href}`); } }, text);
const termsNote = () => h('p', { class: 'buy__terms' }, 'Нажимая на способ оплаты, вы принимаете ', docLink('/docs/terms.html', 'условия покупки'),
  ' и ', docLink('/docs/refund.html', 'правила возврата'));

/** Шторка «Выберите способ оплаты»: строки с иконкой, названием и подписью; нажатие сразу ведёт к оплате. */
export function openPayMethods({ qty, onPaid }) {
  const methods = state.pricing.methods || [{ id: 'stars', title: 'Звёзды', sub: '', enabled: true }];
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
        h('img', { class: 'pmrow__icon', src: `/assets/pay/${m.id}.webp`, alt: '', draggable: 'false' }),
        h('span', { class: 'pmrow__text' }, h('b', null, m.title), h('span', null, sub)),
        m.enabled ? h('span', { class: 'pmrow__go' }, h('i', { class: 'pmrow__spin', 'aria-hidden': 'true' }), icon('chev-right', 'pmrow__chev'))
          : h('span', { class: 'pmrow__soon' }, 'Скоро'));
        return row;
      });
      return [
        h('div', { class: 'sheet__grab pmhead' },
          h('h2', { class: 'pmhead__title' }, 'Выберите способ оплаты'),
          h('p', { class: 'pmhead__sub' }, icon('sparkle-fill', 'pmhead__spark'), `${gens(qty)} · `, rub(total))),
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
    onClose: () => off(),
    render: ({ close }) => {
      const p = state.pricing;
      const packs = presets();
      const biggest = packs[packs.length - 1];
      let current = Math.min(Math.max(qty || p.default_qty, 1), p.max_qty);

      // ── сетка пакетов
      const cards = packs.map((q, i) => {
        const badge = presetBadge(q);
        return h('button', { class: 'bcard', type: 'button', dataset: { qty: String(q) }, style: { '--i': String(i), '--glow': PACK_GLOW[i % PACK_GLOW.length] }, onclick: () => {
          if (q === current) return;
          haptic.select();
          current = q;
          paint();
        } },
        BigEmoji(PACK_EMOJI[i % PACK_EMOJI.length], 'bcard__emoji'),
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

      return [
        CloseButton(close),
        h('div', { class: 'buy' },
          h('header', { class: 'buy__head' },
            h('h1', { class: 'buy__title' }, 'Генерации'),
            h('p', { class: 'buy__sub' }, 'На балансе ', balance, ' ✦ · ', h('em', null, `−${p.discount_pct}% от ${p.discount_from} шт`))),
          h('div', { class: 'bgrid' }, ...cards),
          h('p', { class: 'buy__terms buy__terms--center' }, 'Покупая, вы принимаете ', docLink('/docs/terms.html', 'условия'), ' и ', docLink('/docs/refund.html', 'правила возврата')),
          h('div', { class: 'buy__bottom' },
            h('div', { class: 'bbar' }, h('div', { class: 'bbar__stepper' }, minus, count, plus), buyBtn))),
      ];
    },
  });
}
