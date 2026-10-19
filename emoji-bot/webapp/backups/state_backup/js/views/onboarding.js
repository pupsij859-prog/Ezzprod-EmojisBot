// Онбординг: приветствие (один раз при первом входе) → инструкция. Инструкцию можно открыть снова из профиля.
// Главное в приветствии — сами стикеры: стена шаблонов, а в центре три живых стикера уже с ником пользователя.
// Картинки инструкции — сцены из настоящих элементов приложения, а не рисунки.
import { previewUrl } from '../api.js';
import { BigEmoji, Button } from '../components.js';
import { SLIDES, WELCOME } from '../content.js';
import { TgEmoji } from '../emoji.js';
import { openOverlay } from '../nav.js';
import { state } from '../store.js';
import { haptic } from '../tg.js';
import { TgsPreview, textDrawing } from '../tgs.js';
import { h, icon } from '../ui.js';

// шаблоны с заметным местом под надпись — на них ник читается лучше всего
const LIVE = ['classic-034', 'classic-001', 'classic-025'];
const WALL_TOP = ['characters-019', 'love-003', 'classic-018', 'gift-002', 'one-014', 'classic-005', 'finance-004', 'characters-023', 'flags-001', 'classic-013'];
const WALL_BOTTOM = ['classic-020', 'characters-027', 'gift-009', 'classic-031', 'love-011', 'other-004', 'classic-044', 'characters-040', 'flags-005', 'classic-026'];

const nickOf = (me) => {
  const max = (me && me.limits && me.limits.text_max) || 12;
  return (state.text || (me && (me.username || me.first_name)) || 'EzzProd').trim().slice(0, max);
};

/** Живой стикер с ником. Анимация строится, когда готов рисунок ника (иначе — родной шаблон). */
function LiveSticker(id, nick, cls, extra = {}) {
  const player = TgsPreview(id, { lazy: true, cls });
  textDrawing(nick)
    .then((text) => player.update(text ? { text, ...extra } : null))
    .catch(() => {})
    .finally(() => player.play(true));
  return player;
}

// ───────────────────────── приветствие ─────────────────────────

function Wall(nick) {
  const row = (ids, dir) => h('div', { class: `wall__row wall__row--${dir}` },
    h('div', { class: 'wall__track' }, ...[...ids, ...ids].map((id) => h('img', {
      class: 'wall__tile', src: previewUrl(id), alt: '', draggable: false, decoding: 'async',
    }))));
  const live = LIVE.map((id) => LiveSticker(id, nick, 'tgs--wall'));
  const el = h('div', { class: 'wall', 'aria-hidden': 'true' },
    row(WALL_TOP, 'left'),
    h('div', { class: 'wall__live' }, ...live.map((p, i) => h('div', { class: 'wall__cell', style: { '--i': String(i) } }, p.el))),
    row(WALL_BOTTOM, 'right'));
  return { el, destroy: () => live.forEach((p) => p.destroy()) };
}

function Welcome(me, onStart) {
  const wall = Wall(nickOf(me));
  const el = h('section', { class: 'onb-welcome' },
    wall.el,
    h('div', { class: 'onb-welcome__body' },
      h('h1', { class: 'onb-welcome__title' }, ...WELCOME.title.map((line) => h('span', null, line))),
      h('p', { class: 'onb-welcome__text' }, WELCOME.text),
      h('p', { class: 'onb-welcome__gift' }, TgEmoji('gift'), WELCOME.gift),
      Button({ label: WELCOME.cta, onClick: () => { haptic.tap(); onStart(); } })));
  return { el, destroy: wall.destroy };
}

// ───────────────────────── сцены инструкции ─────────────────────────

const thumb = (id, cls = '') => h('img', { class: cls, src: previewUrl(id), alt: '', draggable: false, decoding: 'async' });

const SCENES = {
  // главная: карточки, три из них отмечаются по очереди
  pick() {
    const ids = ['classic-034', 'characters-019', 'love-003', 'classic-001', 'gift-002', 'finance-004'];
    const order = { 1: 0, 3: 1, 4: 2 };
    return h('div', { class: 'sc-pick' }, ids.map((id, i) => h('div', {
      class: `sc-pick__card${i in order ? ' is-pick' : ''}`, style: { '--d': String(order[i] || 0) },
    }, thumb(id), h('span', { class: 'sc-pick__check' }, icon('check')))));
  },
  // оформление: живой стикер с ником, заливкой и обводкой
  edit(nick, destroyers) {
    const p = LiveSticker('classic-001', nick, 'tgs--scene', { color: [1, 1, 1], stroke: [0.04, 0.04, 0.05] });
    destroyers.push(() => p.destroy());
    return h('div', { class: 'sc-edit' },
      h('div', { class: 'sc-edit__stage' }, p.el),
      h('div', { class: 'sc-edit__rows' },
        h('div', { class: 'sc-row' }, h('span', null, 'Ник'), h('b', null, nick)),
        h('div', { class: 'sc-row' }, h('span', null, 'Заливка'), h('i', { class: 'sc-dot', style: { background: '#ffffff' } })),
        h('div', { class: 'sc-row' }, h('span', null, 'Обводка'), h('i', { class: 'sc-dot', style: { background: '#0b0b0d', boxShadow: '0 0 0 1.5px rgba(255,255,255,0.5)' } }))));
  },
  // сборка: элементы появляются по одному, полоса доходит до конца
  build() {
    const ids = ['classic-034', 'characters-019', 'classic-001', 'gift-002'];
    return h('div', { class: 'sc-build' },
      h('div', { class: 'sc-build__items' }, ids.map((id, i) => h('div', { class: 'sc-build__item', style: { '--d': String(i) } }, thumb(id)))),
      h('div', { class: 'sc-build__bar' }, h('i')),
      h('div', { class: 'sc-build__btn' }, 'Создать пак'));
  },
  // окно Telegram «Добавить эмодзи»
  tg(nick) {
    const ids = ['classic-034', 'characters-019', 'love-003', 'classic-001', 'gift-002', 'finance-004', 'classic-018', 'one-014'];
    return h('div', { class: 'sc-tg' },
      h('div', { class: 'sc-tg__sheet' },
        h('i', { class: 'sc-tg__grab' }),
        h('b', { class: 'sc-tg__title' }, nick),
        h('span', { class: 'sc-tg__sub' }, `${ids.length} эмодзи`),
        h('div', { class: 'sc-tg__grid' }, ids.map((id) => thumb(id))),
        h('div', { class: 'sc-tg__btn' }, `Добавить ${ids.length} эмодзи`)));
  },
  // генерации: подарок и бонус за друга
  balance(nick, destroyers, me) {
    return h('div', { class: 'sc-bal' },
      BigEmoji('gift', 'sc-bal__emoji'),
      h('div', { class: 'sc-bal__chips' },
        h('span', { class: 'sc-chip' }, icon('gens'), 'На балансе ', h('b', null, String(me.balance))),
        h('span', { class: 'sc-chip' }, TgEmoji('friends'), h('b', null, `+${me.referral_reward}`), ' за друга')));
  },
};

// ───────────────────────── инструкция ─────────────────────────

function Instruction({ onDone, doneLabel, skipLabel }) {
  const me = state.me;
  const nick = nickOf(me);
  const last = SLIDES.length - 1;
  const destroyers = [];
  let index = 0;

  const bars = h('div', { class: 'ins__bars', 'aria-hidden': 'true' }, SLIDES.map(() => h('i', null, h('b'))));
  const slides = SLIDES.map((s, i) => h('article', {
    class: 'ins__slide', role: 'group', 'aria-roledescription': 'слайд', 'aria-label': `${i + 1} из ${SLIDES.length}`,
  },
  h('div', { class: 'ins__art' }, SCENES[s.scene](nick, destroyers, me)),
  h('div', { class: 'ins__copy' },
    h('h2', { class: 'ins__title' }, s.title),
    h('p', { class: 'ins__text' }, typeof s.text === 'function' ? s.text(me) : s.text))));
  const track = h('div', {
    class: 'ins__track', tabindex: 0, role: 'group', 'aria-roledescription': 'карусель', 'aria-label': 'инструкция',
  }, ...slides);

  const next = Button({ label: 'Дальше', onClick: () => (index === last ? onDone() : go(index + 1)) });
  const skip = h('button', { class: 'link-btn ins__skip', type: 'button', onclick: () => { haptic.tap(); onDone(); } }, skipLabel);

  function paint() {
    [...bars.children].forEach((b, i) => {
      b.classList.toggle('is-done', i < index);
      b.classList.toggle('is-on', i === index);
    });
    slides.forEach((s, i) => s.classList.toggle('is-active', i === index));
    next.querySelector('.btn__label').textContent = index === last ? doneLabel : 'Дальше';
    skip.classList.toggle('is-hidden', index === last);
    skip.tabIndex = index === last ? -1 : 0;
  }

  // переход кнопкой: пока едет плавная прокрутка, свайп-обработчик не вмешивается;
  // если прокрутка не доехала (бывает при прилипании слайдов), ставим слайд на место сразу
  let target = -1;
  let settle = 0;
  function go(i) {
    index = Math.max(0, Math.min(last, i));
    haptic.select();
    target = index * track.clientWidth;
    track.scrollTo({ left: target, behavior: 'smooth' });
    clearTimeout(settle);
    settle = setTimeout(() => {
      if (Math.abs(track.scrollLeft - target) > 2) track.scrollTo({ left: target, behavior: 'auto' });
      target = -1;
    }, 650);
    paint();
  }

  let raf = 0;
  track.addEventListener('scroll', () => {
    if (target >= 0) return;
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      const i = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
      if (i !== index) { index = i; paint(); haptic.select(); }
    });
  }, { passive: true });
  track.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); go(index + 1); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); go(index - 1); }
  });
  paint();

  return {
    el: h('div', { class: 'ins' }, bars, track, h('div', { class: 'ins__actions' }, next, skip)),
    destroy: () => destroyers.forEach((d) => d()),
  };
}

/** Первый вход: приветствие → инструкция. Промис завершается, когда пользователь закончил или пропустил. */
export function runOnboarding(root) {
  return new Promise((resolve) => {
    document.body.classList.add('is-onboarding');
    const page = h('div', { class: 'onboarding' });
    root.replaceChildren(page);
    let current = null;
    const show = (part) => {
      if (current) current.destroy();
      current = part;
      page.replaceChildren(h('div', { class: 'onboarding__step' }, part.el));
    };
    const finish = () => {
      if (current) current.destroy();
      document.body.classList.remove('is-onboarding');
      resolve();
    };
    show(Welcome(state.me, () => show(Instruction({ onDone: finish, doneLabel: 'Начать', skipLabel: 'Пропустить' }))));
  });
}

/** Повторный просмотр из профиля. */
export function openInstruction() {
  let ins = null;
  openOverlay({
    kind: 'screen',
    label: 'инструкция',
    className: 'layer--instruction',
    onClose: () => { if (ins) ins.destroy(); },
    render: ({ close }) => {
      ins = Instruction({ onDone: close, doneLabel: 'Понятно', skipLabel: 'Закрыть' });
      return ins.el;
    },
  });
}
