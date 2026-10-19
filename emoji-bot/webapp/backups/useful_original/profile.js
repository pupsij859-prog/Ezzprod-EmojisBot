// «Профиль»: кто я, статистика на фоне Dark Veil (баланс, куплено, паки, друзья),
// быстрая покупка генераций и «Полезное». Паки — на вкладке «Паки».
import { api } from '../api.js';
import { Avatar, BigEmoji, ProBadge, SectionHead, Topbar, handle } from '../components.js';
import { goTab } from '../router.js';
import { bus, priceFor, refreshMe, starsForQty, state } from '../store.js';
import { haptic, openLink } from '../tg.js';
import { displayName, emojiWord, h, icon, plural, toast } from '../ui.js';
import { mountVeil } from '../veil.js';
import { BannerCover } from './banner.js';
import { openBuy, packLook, presetBadge, presets } from './buy.js';
import { openDocs, openFreeInfo } from './info.js';
import { openInstruction } from './onboarding.js';
import { openReferral } from './referral.js';
import { openStyles } from './styles.js';

// цвета вуали: индиго, пурпур, синий, фиолет, маджента
const VEIL = ['#5b3dff', '#b23cff', '#2d6bff', '#7d2dff', '#d23cff', '#3d4dff'];

/** Карточка-статистика: заголовок, крупное значение, подпись «жирное + серое». */
function StatCard({ title, value, foot, onClick }) {
  const valueEl = h('b', { class: 'vcard__value' }, value);
  const footEl = h('span', { class: 'vcard__foot' }, foot || '');
  const el = h(onClick ? 'button' : 'div', {
    class: 'vcard vstat', type: onClick ? 'button' : undefined,
    onclick: onClick ? () => { haptic.tap(); onClick(); } : undefined,
  }, h('span', { class: 'vcard__title' }, title), valueEl, footEl);
  return {
    el,
    set(v, f) {
      valueEl.replaceChildren(...[].concat(v));
      if (f !== undefined) footEl.replaceChildren(...[].concat(f));
    },
  };
}

const foot = (bold, rest) => [h('b', null, bold), ` ${rest}`];

export function mountProfile(container) {
  const me = () => state.me;
  const destroyers = [];
  const veil = (el, i, opts = {}) => destroyers.push(mountVeil(el, { color: VEIL[i % VEIL.length], variation: i + 1, ...opts }));

  // ── кто я: компактная карточка, баннер — её фон
  const plan = h('span', { class: 'vhero__plan' });
  const cover = BannerCover();
  const hero = h('div', { class: 'vcard vcard--plain vhero' },
    cover.el,
    h('div', { class: 'vhero__body' },
      Avatar({ size: 'xl' }),
      h('div', { class: 'vhero__info' },
        h('h1', { class: 'vhero__name' }, displayName(me())),
        me().username ? h('span', { class: 'vhero__user' }, handle(me())) : null,
        plan)));

  // ── статистика
  const balanceValue = () => [h('span', { 'data-balance': '' }, String(me().balance)), icon('gens', 'vcard__spark')];
  const cBalance = StatCard({ title: 'Баланс', value: balanceValue(), foot: foot('1', 'генерация = 1 эмодзи'), onClick: () => openBuy() });
  const cBought = StatCard({ title: 'Куплено', value: '0', onClick: openFreeInfo });
  const cPacks = StatCard({ title: 'Паки', value: '—', foot: 'загружаем…', onClick: () => goTab('packs') });
  const cFriends = StatCard({ title: 'Друзья', value: '—', foot: 'загружаем…', onClick: openReferral });

  const paintMe = () => {
    const m = me();
    // у владельца (админа) вместо тарифа — OWNER
    plan.textContent = m.is_admin ? 'Owner' : m.plan === 'pro' ? 'Pro' : 'Бесплатный';
    plan.classList.toggle('is-owner', Boolean(m.is_admin));
    plan.classList.toggle('is-pro', !m.is_admin && m.plan === 'pro');
    cBalance.set(balanceValue());
    const left = Math.max(0, m.pro_min - m.purchased_total);
    cBought.set(String(m.purchased_total), m.plan === 'pro' ? foot('Pro', 'открыт') : foot(`ещё ${left}`, 'до Pro'));
  };
  paintMe();
  const off = bus.on('me', paintMe);

  // ── покупка: быстрые пакеты открывают экран оплаты с уже выбранным количеством
  const p = state.pricing;
  const quick = presets().filter((q) => q === 10 || q === p.discount_from || q === p.bulk_from).slice(0, 3);
  const buy = h('div', { class: 'vcard vcard--plain vbuy' },
    h('div', { class: 'vbuy__head' },
      h('span', { class: 'vcard__title' }, 'Пополнить'),
      h('button', { class: 'vbuy__all', type: 'button', onclick: () => { haptic.tap(); openBuy(); } }, 'Все пакеты', icon('chev-right'))),
    h('div', { class: 'vquick' }, quick.map((q) => {
      const badge = presetBadge(q);
      const look = packLook(q);
      return h('button', { class: 'vqi', type: 'button', style: { '--glow': look.glow },
        onclick: () => { haptic.tap(); openBuy({ qty: q }); } },
      BigEmoji(look.emoji, 'vqi__emoji'),
      badge ? h('span', { class: `vqi__badge vqi__badge--${badge.tone}` }, badge.text) : null,
      h('b', { class: 'vqi__qty' }, String(q)),
      h('span', { class: 'vqi__price' }, `${starsForQty(q)}\u00a0⭐`));
    })));

  // ── полезное
  // glow — цвет свечения за эмодзи (r, g, b), под цвет самого эмодзи
  const useful = [
    { label: 'Пригласить', sub: `+${me().referral_reward} за друга`, emoji: 'friends', glow: '255, 176, 72', onClick: openReferral },
    { label: 'Бонусы', emoji: 'gift', glow: '255, 72, 104', onClick: openFreeInfo },
    { label: 'Инструкция', emoji: 'question', glow: '255, 206, 64', onClick: () => openInstruction() },
    { label: 'Поддержка', emoji: 'chat', glow: '255, 150, 70', onClick: () => {
      if (me().support_url) openLink(me().support_url);
      else toast('Поддержка скоро появится');
    } },
    { label: 'Мои стили', pro: me().plan !== 'pro', emoji: 'sparkle', glow: '255, 214, 92', onClick: () => openStyles() },
    { label: 'Документы', emoji: 'docs', glow: '140, 176, 255', onClick: openDocs },
  ];
  const tiles = useful.map((u, i) => h('button', { class: 'vcard vcard--plain vtile', type: 'button',
    style: { '--glow': u.glow, '--glow-delay': `${-i * 0.7}s` }, onclick: () => { haptic.tap(); u.onClick(); } },
    BigEmoji(u.emoji, 'vtile__emoji'),
    h('span', { class: 'vtile__text' }, h('b', null, u.label), u.sub ? h('span', null, u.sub) : null, u.pro ? ProBadge('vtile__pro') : null)));

  container.append(h('section', { class: 'screen screen--profile' },
    Topbar(),
    h('div', { class: 'page vprofile' },
      hero,
      h('div', { class: 'vgrid' }, cBalance.el, cBought.el, cPacks.el, cFriends.el),
      buy),
    SectionHead('Полезное'),
    h('div', { class: 'page vgrid vtiles' }, ...tiles),
    h('p', { class: 'footnote' }, `${me().brand}${me().demo ? ' · демо-версия' : ''}`)));

  // вуаль только на карточках статистики, остальные — обычные тёмные
  [cBalance, cBought, cPacks, cFriends].forEach((c, i) => veil(c.el, i + 1));

  // ── данные, которых нет в /me
  let alive = true;
  api.packs().then(({ packs }) => {
    if (!alive) return;
    const ready = packs.filter((p) => p.status === 'ready');
    const items = ready.reduce((n, p) => n + p.count, 0);
    cPacks.set(String(ready.length), ready.length ? foot(emojiWord(items, 'emoji'), 'собрано') : foot('Собери', 'первый пак'));
  }).catch(() => { if (alive) cPacks.set('—', 'не загрузилось'); });
  api.referral().then((r) => {
    if (!alive) return;
    cFriends.set(String(r.invited), r.earned
      ? foot(`+${r.earned}`, `${plural(r.earned, 'генерация', 'генерации', 'генераций')} получено`)
      : foot(`+${r.reward}`, 'за каждого друга'));
  }).catch(() => { if (alive) cFriends.set('—', 'не загрузилось'); });

  refreshMe().catch(() => {});
  return () => { alive = false; off(); cover.destroy(); destroyers.forEach((d) => d()); };
}
