// Онбординг: приветствие (один раз при первом входе) → инструкция-карусель с картинками.
// Инструкцию можно открыть снова из профиля (без приветствия).
import { Button, CloseButton, Quote } from '../components.js';
import { SLIDES, WELCOME } from '../content.js';
import { openOverlay } from '../nav.js';
import { haptic } from '../tg.js';
import { h } from '../ui.js';

function Carousel({ onDone, onSkip, doneLabel = 'Понятно' }) {
  const last = SLIDES.length - 1;
  let index = 0;

  const track = h('div', {
    class: 'carousel__track', tabindex: 0, role: 'group',
    'aria-roledescription': 'карусель', 'aria-label': 'инструкция',
  }, SLIDES.map((s, i) => h('article', {
    class: 'slide', role: 'group', 'aria-roledescription': 'слайд', 'aria-label': `${i + 1} из ${SLIDES.length}`,
  },
  h('div', { class: 'slide__art' },
    h('img', { src: s.image, alt: '', draggable: false, loading: i < 2 ? 'eager' : 'lazy', decoding: 'async' })),
  h('h2', { class: 'slide__title' }, s.title),
  Quote({ emoji: s.emoji, text: s.text, cls: 'slide__text' }))));

  const dots = h('div', { class: 'dots', role: 'tablist', 'aria-label': 'слайды' },
    SLIDES.map((_, i) => h('button', { class: 'dot', type: 'button', role: 'tab', 'aria-label': `слайд ${i + 1}`, onclick: () => go(i) })));

  const next = Button({ label: 'Дальше', onClick: () => (index === last ? onDone() : go(index + 1)) });
  const skip = h('button', { class: 'link-btn', type: 'button', onclick: () => { haptic.tap(); onSkip(); } }, 'Пропустить');

  function paint() {
    [...dots.children].forEach((d, i) => {
      d.classList.toggle('is-active', i === index);
      d.setAttribute('aria-selected', String(i === index));
    });
    next.querySelector('.btn__label').textContent = index === last ? doneLabel : 'Дальше';
    skip.classList.toggle('is-hidden', index === last);
    skip.tabIndex = index === last ? -1 : 0;
  }

  function go(i) {
    index = Math.max(0, Math.min(last, i));
    haptic.select();
    track.scrollTo({ left: index * track.clientWidth, behavior: 'smooth' });
    paint();
  }

  let raf = 0;
  track.addEventListener('scroll', () => {
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

  return h('div', { class: 'carousel' }, track, dots, h('div', { class: 'carousel__actions' }, next, skip));
}

function Welcome(onStart) {
  return h('section', { class: 'welcome' },
    h('div', { class: 'welcome__art' },
      h('img', { class: 'welcome__logo', src: 'assets/brand/avatar-clean.jpg', alt: '', width: 128, height: 128 })),
    h('h1', { class: 'welcome__title' }, WELCOME.title),
    h('p', { class: 'welcome__text' }, WELCOME.text),
    h('div', { class: 'quotes welcome__points' }, WELCOME.points.map(([emoji, title, text]) => Quote({ emoji, title, text }))),
    h('div', { class: 'welcome__cta' }, Button({ label: WELCOME.cta, onClick: onStart })));
}

/** Первый вход: приветствие → инструкция. Промис завершается, когда пользователь закончил или пропустил. */
export function runOnboarding(root) {
  return new Promise((resolve) => {
    document.body.classList.add('is-onboarding');
    const finish = () => {
      document.body.classList.remove('is-onboarding');
      resolve();
    };
    const page = h('div', { class: 'onboarding' });
    root.replaceChildren(page);

    const showInstruction = () => {
      const view = h('div', { class: 'onboarding__step onboarding__step--instruction' },
        Carousel({ onDone: finish, onSkip: finish, doneLabel: 'Начать' }));
      page.replaceChildren(view);
    };
    page.append(h('div', { class: 'onboarding__step' }, Welcome(showInstruction)));
  });
}

/** Повторный просмотр из профиля. */
export function openInstruction() {
  openOverlay({
    kind: 'screen',
    label: 'инструкция',
    className: 'layer--instruction',
    render: ({ close }) => [
      CloseButton(close),
      h('div', { class: 'instruction' }, Carousel({ onDone: close, onSkip: close, doneLabel: 'Понятно' })),
    ],
  });
}
