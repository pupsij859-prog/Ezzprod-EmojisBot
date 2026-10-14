// «Админка» (только ADMIN_IDS): статистика, платежи с возвратом, пользователи с начислением, лента паков.
import { api, ApiError, previewUrl } from '../api.js';
import {
  Button, Chips, ErrorState, Group, Quote, Row, SectionHead, Skeleton, Stepper, Topbar, setBusy,
} from '../components.js';
import { openOverlay } from '../nav.js';
import { refreshMe, state } from '../store.js';
import { confirmAction, copyText, haptic, openLink } from '../tg.js';
import { debounce, displayName, emojiWord, gens, h, icon, plural, replaceChildren, toast } from '../ui.js';

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const date = (ts) => { const d = new Date(ts * 1000); return `${d.getDate()} ${MONTHS[d.getMonth()]}`; };
const dateTime = (ts) => {
  const d = new Date(ts * 1000);
  return `${date(ts)} ${d.getFullYear()}, ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};
const num = (n) => Number(n || 0).toLocaleString('ru-RU');
const who = (u) => (u.username ? `@${u.username}` : (displayName(u) || `id ${u.id}`));

const PAY_STATUS = {
  credited: ['Оплачен', 'ok'], credited_repeat: ['Оплачен повторно', 'ok'], needs_review: ['Спорный', 'warn'],
  refunding: ['Возврат…', 'muted'], refunded: ['Возвращён', 'bad'], received: ['Получен', 'muted'],
};
const PACK_STATUS = { ready: ['Готов', 'ok'], processing: ['В работе', 'muted'], failed: ['Ошибка', 'bad'] };
const LEDGER = {
  signup: 'Подарок за вход', purchase: 'Покупка', pack: 'Сборка пака', pack_refund: 'Возврат за пак',
  referral: 'За друга', refund: 'Возврат Stars', refund_revert: 'Отмена возврата', admin: 'Вручную',
};

const Status = ([label, tone]) => h('span', { class: `status status--${tone}` }, label);

function Stat({ glow, iconName, value, label, sub }) {
  return h('div', { class: 'astat', style: { '--glow': glow } },
    h('span', { class: 'astat__icon' }, icon(iconName)),
    h('b', { class: 'astat__value' }, value),
    h('span', { class: 'astat__label' }, label),
    sub ? h('span', { class: 'astat__sub' }, sub) : null);
}

function Segmented(options, value, onChange) {
  const buttons = options.map((o) => h('button', {
    class: `segmented__btn${o.id === value ? ' is-active' : ''}`, type: 'button', role: 'tab', 'aria-selected': String(o.id === value), dataset: { id: o.id },
    onclick: () => {
      if (o.id === value) return;
      haptic.select();
      value = o.id;
      el.dataset.index = String(options.findIndex((x) => x.id === value));
      buttons.forEach((b) => { b.classList.toggle('is-active', b.dataset.id === value); b.setAttribute('aria-selected', String(b.dataset.id === value)); });
      onChange(value);
    },
  }, o.label));
  const el = h('div', { class: 'segmented segmented--3', role: 'tablist', dataset: { index: '0' } }, ...buttons);
  return el;
}

// ───────────────────────── списки с «показать ещё» ─────────────────────────

function PagedList({ load, renderItem, empty }) {
  const list = h('div', { class: 'alist' });
  const more = Button({ label: 'Показать ещё', variant: 'secondary', onClick: () => next() });
  const box = h('div', { class: 'alist-box' }, list, more);
  let offset = 0;
  let token = 0;
  let alive = true;
  let loading = false;
  more.hidden = true;

  async function next(reset = false) {
    if (!alive || (loading && !reset)) return null;
    const mine = reset ? ++token : token;
    loading = true;
    if (reset) {
      offset = 0;
      replaceChildren(list, Skeleton('skeleton--line'), Skeleton('skeleton--line'), Skeleton('skeleton--line'));
      more.hidden = true;
    }
    setBusy(more, true);
    try {
      const data = await load(offset);
      if (mine !== token) return null;
      if (reset) replaceChildren(list);
      list.append(...data.items.map(renderItem));
      offset += data.items.length;
      more.hidden = !data.more;
      if (!list.children.length) list.append(h('p', { class: 'alist__empty' }, empty));
      return data;
    } catch {
      if (mine !== token) return null;
      replaceChildren(list, ErrorState({ onRetry: () => next(true) }));
      return null;
    } finally {
      if (mine === token) { loading = false; setBusy(more, false); }
    }
  }
  return { el: box, reload: () => next(true), destroy: () => { alive = false; ++token; } };
}

// ───────────────────────── платежи ─────────────────────────

function PaymentRow(p, onChanged) {
  return h('button', { class: 'arow', type: 'button', onclick: () => { haptic.tap(); openPayment(p, onChanged); } },
    h('span', { class: 'arow__main' },
      h('b', null, `#${p.id} · ${date(p.created_at)} · `, h('span', { class: 'stars' }, num(p.amount), icon('star-fill', 'stars__icon'))),
      h('span', null, `${who(p.user)}${p.qty ? ` · ${gens(p.qty)}` : ''}`)),
    Status(PAY_STATUS[p.status] || [p.status, 'muted']));
}

function openPayment(p, onChanged) {
  openOverlay({
    kind: 'sheet', label: 'платёж', className: 'sheet--admin',
    render: ({ close }) => {
      const canRefund = p.status === 'credited' || p.status === 'credited_repeat';
      const refund = Button({ label: 'Вернуть Stars', variant: 'secondary', onClick: async () => {
        if (!(await confirmAction(`Вернуть ${p.amount} Stars пользователю ${who(p.user)}? С баланса снимется ${gens(p.qty || 0)}.`))) return;
        setBusy(refund, true, 'Возвращаем…');
        try {
          const r = await api.admin.refund(p.charge_id, p.provider);
          if (r.ok) { haptic.success(); toast('Stars возвращены'); } else { haptic.error(); toast('Telegram не вернул Stars, генерации оставлены', 'warn'); }
          close();
          onChanged();
        } catch (err) {
          haptic.error();
          setBusy(refund, false);
          const code = err instanceof ApiError ? err.code : '';
          toast({
            refund_spent: 'Генерации уже потрачены: вернуть автоматически нельзя',
            refund_bad_status: 'Этот платёж нельзя вернуть',
            refund_no_bot: 'Бот не запущен: возврат недоступен',
          }[code] || 'Не получилось вернуть', 'warn');
        }
      } });
      const copy = async () => { const ok = await copyText(p.charge_id); haptic.tap(); toast(ok ? 'charge_id скопирован' : 'Не получилось скопировать'); };
      return [
        h('div', { class: 'sheet__grab' }, h('h2', { class: 'sheet__title' }, `Платёж #${p.id}`)),
        h('div', { class: 'sheet__body' },
          Group(
            Row({ label: 'Статус', value: Status(PAY_STATUS[p.status] || [p.status, 'muted']), chevron: false }),
            Row({ label: 'Сумма', value: h('span', { class: 'row__value stars' }, num(p.amount), icon('star-fill', 'stars__icon')) }),
            Row({ label: 'Генераций', value: p.qty ? String(p.qty) : '—' }),
            Row({ label: 'Пользователь', sub: `id ${p.user.id}`, value: who(p.user) }),
            Row({ label: 'Дата', value: dateTime(p.created_at) }),
            Row({ label: 'charge_id', sub: p.charge_id, onClick: copy, value: icon('copy', 'row__chev') })),
          p.status === 'needs_review'
            ? Quote({ emoji: 'warn', title: 'Спорный платёж', text: 'деньги пришли, но не сошлись с заказом (сумма, пользователь или заказ не найден). Генерации не начислены: начисли вручную в разделе «Пользователи» или верни Stars командой /refund.' })
            : null,
          canRefund
            ? Quote({ emoji: 'info', title: 'Возврат', text: 'Stars вернутся покупателю, генерации снимутся с баланса. Если они уже потрачены, автоматический возврат невозможен.' })
            : null),
        h('div', { class: 'sheet__footer' }, canRefund ? refund : Button({ label: 'Закрыть', variant: 'secondary', onClick: close })),
      ];
    },
  });
}

function PaymentsSection() {
  let status = 'all';
  const chips = Chips([
    { id: 'all', label: 'Все' }, { id: 'paid', label: 'Оплачены' }, { id: 'review', label: 'Спорные' }, { id: 'refunded', label: 'Возвраты' },
  ], status, (id) => { status = id; list.reload(); });
  const list = PagedList({
    load: async (offset) => { const d = await api.admin.payments(status, offset); chips.counts(d.counts); return d; },
    renderItem: (p) => PaymentRow(p, () => list.reload()),
    empty: 'Платежей нет',
  });
  list.reload();
  return { el: h('div', { class: 'asection' }, chips.el, list.el), destroy: list.destroy };
}

// ───────────────────────── пользователи ─────────────────────────

function UserRow(u, onChanged) {
  return h('button', { class: 'arow', type: 'button', onclick: () => { haptic.tap(); openUser(u.id, onChanged); } },
    h('span', { class: 'arow__avatar', 'aria-hidden': 'true' }, (u.first_name || u.username || '?').charAt(0).toUpperCase()),
    h('span', { class: 'arow__main' },
      h('b', null, displayName(u) || `id ${u.id}`),
      h('span', null, `${u.username ? `@${u.username} · ` : ''}id ${u.id} · с ${date(u.created_at)}`)),
    h('span', { class: 'arow__value' }, num(u.balance), icon('gens', 'arow__spark')));
}

function openUser(userId, onChanged) {
  openOverlay({
    kind: 'sheet', label: 'пользователь', className: 'sheet--admin',
    render: ({ close }) => {
      const body = h('div', { class: 'sheet__body' }, Skeleton('skeleton--line'), Skeleton('skeleton--line'));
      const title = h('h2', { class: 'sheet__title' }, 'Пользователь');
      load();
      return [h('div', { class: 'sheet__grab' }, title), body];

      async function load() {
        let u;
        try { u = await api.admin.user(userId); } catch { replaceChildren(body, ErrorState({ onRetry: load })); return; }
        title.textContent = displayName(u) || `id ${u.id}`;
        const stepper = Stepper({ value: 5, min: 1, max: 10000 });
        const act = (sign) => async () => {
          const n = stepper.get() * sign;
          const text = sign > 0 ? `Начислить ${gens(n)} пользователю ${who(u)}?` : `Списать ${gens(-n)} у ${who(u)}?`;
          if (!(await confirmAction(text))) return;
          try {
            const r = await api.admin.grant(u.id, n);
            haptic.success();
            toast(`Готово: на балансе ${r.balance}`);
            if (state.me && u.id === state.me.id) refreshMe().catch(() => {});
            onChanged();
            load();
          } catch (err) {
            haptic.error();
            toast(err instanceof ApiError && err.status === 402 ? 'Столько генераций на балансе нет' : 'Не получилось', 'warn');
          }
        };
        replaceChildren(body, 
          Group(
            Row({ label: 'Баланс', value: gens(u.balance), chevron: false }),
            Row({ label: 'Тариф', value: u.plan === 'pro' ? 'Pro' : 'Бесплатный' }),
            Row({ label: 'Куплено', value: gens(u.purchased_total) }),
            Row({ label: 'Оплачено', value: h('span', { class: 'row__value stars' }, num(u.spent_stars), icon('star-fill', 'stars__icon')) }),
            Row({ label: 'Паков', value: String(u.packs) }),
            Row({ label: 'Пригласил', value: `${u.invited} ${plural(u.invited, 'друга', 'друзей', 'друзей')}` }),
            Row({ label: 'Telegram', sub: `id ${u.id}`, value: u.username ? `@${u.username}` : '—',
              onClick: u.username ? () => openLink(`https://t.me/${u.username}`) : undefined })),
          h('h3', { class: 'section-title' }, 'Генерации вручную'),
          h('div', { class: 'grant' }, stepper.el,
            h('div', { class: 'grant__actions' },
              Button({ label: 'Списать', variant: 'secondary', onClick: act(-1) }),
              Button({ label: 'Начислить', onClick: act(1) }))),
          Quote({ emoji: 'pin', title: 'Журнал', text: 'каждое начисление и списание записывается в историю баланса с пометкой «вручную».' }),
          u.ledger.length ? h('h3', { class: 'section-title' }, 'История баланса') : null,
          u.ledger.length ? Group(...u.ledger.map((e) => Row({
            label: LEDGER[e.reason] || e.reason, sub: dateTime(e.created_at),
            value: h('span', { class: `row__value delta ${e.delta > 0 ? 'is-plus' : 'is-minus'}` }, `${e.delta > 0 ? '+' : ''}${e.delta}`),
          }))) : null,
          h('div', { class: 'gap' }),
          Button({ label: 'Закрыть', variant: 'ghost', onClick: close }));
      }
    },
  });
}

function UsersSection() {
  let q = '';
  const input = h('input', { class: 'field field--box asearch__input', type: 'search', placeholder: 'ID, @username или имя',
    autocomplete: 'off', autocapitalize: 'off', enterKeyHint: 'search', 'aria-label': 'поиск пользователя' });
  input.spellcheck = false;
  const total = h('span', { class: 'asearch__total' });
  const list = PagedList({
    load: async (offset) => { const d = await api.admin.users(q, offset); total.textContent = `найдено ${num(d.total)}`; return d; },
    renderItem: (u) => UserRow(u, () => list.reload()),
    empty: 'Никого не нашли',
  });
  input.addEventListener('input', debounce(() => { q = input.value.trim(); list.reload(); }, 300));
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') input.blur(); });
  list.reload();
  return { el: h('div', { class: 'asection' },
    h('label', { class: 'asearch' }, icon('search', 'asearch__icon'), input), total, list.el), destroy: list.destroy };
}

// ───────────────────────── паки ─────────────────────────

function PackRow(p) {
  const thumbs = h('span', { class: 'arow__thumbs', 'aria-hidden': 'true' },
    p.preview.slice(0, 4).map((id) => h('img', { src: previewUrl(id), alt: '', loading: 'lazy', decoding: 'async' })));
  const open = p.link ? () => { haptic.tap(); openLink(p.link); } : undefined;
  return h(open ? 'button' : 'div', { class: 'arow', type: open ? 'button' : undefined, onclick: open },
    thumbs,
    h('span', { class: 'arow__main' },
      h('b', null, `#${p.id} · ${p.title}`),
      h('span', null, `${who(p.user)} · ${emojiWord(p.count, p.type)} · ${date(p.created_at)}`)),
    Status(PACK_STATUS[p.status] || [p.status, 'muted']));
}

function PacksSection() {
  let status = 'all';
  const chips = Chips([
    { id: 'all', label: 'Все' }, { id: 'processing', label: 'В работе' }, { id: 'ready', label: 'Готовы' }, { id: 'failed', label: 'Ошибка' },
  ], status, (id) => { status = id; list.reload(); });
  const list = PagedList({
    load: async (offset) => { const d = await api.admin.packs(status, offset); chips.counts(d.counts); return d; },
    renderItem: PackRow,
    empty: 'Паков нет',
  });
  list.reload();
  return { el: h('div', { class: 'asection' }, chips.el, list.el), destroy: list.destroy };
}

// ───────────────────────── вкладка ─────────────────────────

export function mountAdmin(container) {
  let alive = true;
  const stats = h('div', { class: 'astats' }, ...Array.from({ length: 4 }, () => Skeleton('astat astat--skeleton')));
  const sectionBox = h('div');
  const sections = { payments: PaymentsSection, users: UsersSection, packs: PacksSection };
  let section = null;
  const show = (id) => {
    if (section) section.destroy();
    section = sections[id]();
    replaceChildren(sectionBox, section.el);
  };
  const refresh = h('button', { class: 'catpill', type: 'button', 'aria-label': 'обновить', onclick: () => { haptic.tap(); loadStats(); show(current); } },
    h('span', null, 'Обновить'), icon('refresh', 'catpill__chev'));
  let current = 'payments';
  let statsSeq = 0;

  async function loadStats() {
    const mine = ++statsSeq;
    try {
      const s = await api.admin.stats();
      if (!alive || mine !== statsSeq) return;
      replaceChildren(stats, 
        Stat({ glow: 'var(--glow-gold)', iconName: 'star', value: num(s.revenue.today), label: 'Stars сегодня',
          sub: `7 дней: ${num(s.revenue.week)} · всего: ${num(s.revenue.total)}` }),
        Stat({ glow: 'var(--glow-green)', iconName: 'users', value: num(s.users.total), label: 'Пользователи',
          sub: `+${num(s.users.today)} сегодня · +${num(s.users.week)} за 7 дней` }),
        Stat({ glow: 'var(--glow-steel)', iconName: 'box', value: num(s.packs.total), label: 'Паки',
          sub: `сегодня ${num(s.packs.today)} · в работе ${num(s.packs.processing)} · ошибок ${num(s.packs.failed)}` }),
        Stat({ glow: s.review ? 'var(--glow-rust)' : 'var(--glow-olive)', iconName: 'sparkle', value: num(s.gens.sold), label: 'Генераций продано',
          sub: `на балансах ${num(s.gens.on_balance)}${s.review ? ` · спорных ${s.review}` : ''}` }));
    } catch {
      if (alive && mine === statsSeq) replaceChildren(stats, ErrorState({ onRetry: loadStats }));
    }
  }

  container.append(h('section', { class: 'screen screen--admin' },
    Topbar(),
    SectionHead('Админка', refresh),
    h('div', { class: 'page' },
      stats,
      Segmented([{ id: 'payments', label: 'Платежи' }, { id: 'users', label: 'Пользователи' }, { id: 'packs', label: 'Паки' }],
        current, (id) => { current = id; show(id); }),
      sectionBox)));

  loadStats();
  show(current);
  return () => { alive = false; if (section) section.destroy(); };
}
