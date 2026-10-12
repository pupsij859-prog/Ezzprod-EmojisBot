// «Мой профиль»: кто я, баланс с быстрой покупкой, мои паки и плитки «Полезное».
import { Avatar, GlowTile, ScreenTitle, Stepper, handle } from '../components.js';
import { bus, refreshMe, state } from '../store.js';
import { openLink } from '../tg.js';
import { displayName, h, icon, plural, toast } from '../ui.js';
import { PayButton, PriceLine } from './buy.js';
import { openDocs, openFreeInfo } from './info.js';
import { openInstruction } from './onboarding.js';
import { PacksSection } from './packs.js';
import { openReferral } from './referral.js';
import { openStyles } from './styles.js';

function planLabel(me) {
  if (me.plan === 'pro') return 'Pro';
  return 'Бесплатный';
}

function planSub(me) {
  if (me.plan === 'pro') return 'сохранённые стили открыты';
  const n = me.trial_exports_left;
  const left = Math.max(1, me.pro_min - me.purchased_total);
  return n > 0
    ? `${n} ${plural(n, 'пробный экспорт', 'пробных экспорта', 'пробных экспортов')} · до pro ${left}`
    : `до pro осталось купить ${left}`;
}

export function mountProfile(container) {
  const me = () => state.me;

  // ── кошелёк: баланс + быстрая покупка
  const plan = h('span', { class: 'wallet__plan' });
  const sub = h('p', { class: 'wallet__sub' });
  const paintPlan = () => {
    plan.textContent = planLabel(me());
    plan.classList.toggle('is-pro', me().plan === 'pro');
    sub.textContent = planSub(me());
  };
  paintPlan();
  const off = bus.on('me', paintPlan);

  let pay;
  const stepper = Stepper({ value: state.pricing.default_qty, min: 1, max: state.pricing.max_qty, onChange: () => pay.update() });
  pay = PayButton(stepper.get);
  const wallet = h('div', { class: 'card wallet' },
    h('div', { class: 'wallet__top' },
      h('div', { class: 'wallet__info' },
        h('span', { class: 'wallet__label' }, 'Баланс генераций'),
        h('b', { class: 'wallet__value' }, h('span', { 'data-balance': '' }, String(me().balance)), icon('sparkle-fill'))),
      plan),
    sub,
    h('hr'),
    PriceLine(), stepper.el, pay.el);

  // ── мои паки
  const packs = PacksSection();

  // ── полезное
  const useful = [
    { label: 'Пригласить', icon: 'users', glow: 'var(--glow-green)', onClick: openReferral },
    { label: 'Бонусы', icon: 'gift', glow: 'var(--glow-gold)', onClick: openFreeInfo },
    { label: 'Инструкция', icon: 'help', glow: 'var(--glow-steel)', onClick: () => openInstruction() },
    { label: 'Поддержка', icon: 'chat', glow: 'var(--glow-rust)', onClick: () => {
      if (me().support_url) openLink(me().support_url);
      else toast('Поддержка скоро появится');
    } },
    { label: 'Мои стили', icon: 'palette', glow: 'var(--glow-violet)', onClick: () => openStyles() },
    { label: 'Документы', icon: 'doc', glow: 'var(--glow-olive)', onClick: openDocs },
  ];

  container.append(h('section', { class: 'screen screen--profile' },
    h('div', { class: 'whoami' },
      Avatar({ size: 'xl' }),
      h('h1', { class: 'whoami__name' }, displayName(me())),
      me().username ? h('span', { class: 'whoami__user' }, handle(me())) : null),
    h('div', { class: 'page' }, wallet),
    ScreenTitle('Мои паки', 'packs'),
    h('div', { class: 'page' }, packs.el),
    ScreenTitle('Полезное'),
    h('div', { class: 'tiles' }, useful.map((u, i) => GlowTile({
      label: u.label, glow: u.glow, art: icon(u.icon), index: i, onClick: u.onClick,
    }))),
    h('p', { class: 'footnote' }, `${me().brand}${me().demo ? ' · демо-версия' : ''}`)));

  refreshMe().catch(() => {});
  return () => { off(); packs.cleanup(); };
}
