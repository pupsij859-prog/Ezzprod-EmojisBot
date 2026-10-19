// Фон карточки профиля: баннеры по кругу (плавная смена) или один выбранный.
// Выбор хранится в облаке Telegram (CloudStorage) — одинаковый на всех устройствах.
import { BANNERS } from '../content.js';
import { openOverlay } from '../nav.js';
import { cloudGet, cloudSet, haptic } from '../tg.js';
import { h, icon, prefersReducedMotion } from '../ui.js';

const KEY = 'profile_banner';
const AUTO = 'auto';
const EVERY_MS = 5000;

const byId = (id) => BANNERS.find((b) => b.id === id);

function preload(banner) {
  const img = new Image();
  img.src = banner.src;
  return (img.decode ? img.decode() : Promise.resolve()).catch(() => {});
}

/** Сцена с баннером. → { el, destroy } */
export function BannerCover() {
  const layers = [h('div', { class: 'pcover__img' }), h('div', { class: 'pcover__img' })];
  const dots = h('div', { class: 'pcover__dots', 'aria-hidden': 'true' }, BANNERS.map(() => h('i')));
  const edit = h('button', { class: 'pcover__edit', type: 'button', 'aria-label': 'сменить баннер', onclick: () => { haptic.tap(); pick(); } },
    icon('image'));
  const el = h('div', { class: 'pcover' }, ...layers, h('div', { class: 'pcover__fade' }), dots, edit);

  let mode = AUTO;
  let index = 0;
  let front = 0;
  let timer = 0;
  let alive = true;

  function show(banner, instant = false) {
    const next = layers[front ^ 1];
    next.style.backgroundImage = `url('${banner.src}')`;
    next.style.backgroundSize = banner.size;
    next.style.backgroundPosition = banner.pos;
    next.classList.toggle('is-instant', instant);
    layers[front ^ 1].classList.add('is-on');
    layers[front].classList.remove('is-on');
    front ^= 1;
    [...dots.children].forEach((d, i) => d.classList.toggle('is-on', BANNERS[i] === banner));
  }

  function stop() { clearInterval(timer); timer = 0; }

  function apply(value, instant = false) {
    stop();
    mode = byId(value) ? value : AUTO;
    dots.hidden = mode !== AUTO;
    if (mode !== AUTO) { show(byId(mode), instant); return; }
    show(BANNERS[index], instant);
    if (prefersReducedMotion()) return;
    timer = setInterval(() => {
      if (document.hidden) return;
      const nextIndex = (index + 1) % BANNERS.length;
      preload(BANNERS[nextIndex]).then(() => {
        if (!alive || mode !== AUTO) return;
        index = nextIndex;
        show(BANNERS[index]);
      });
    }, EVERY_MS);
  }

  function pick() {
    openBannerPicker(mode, (value) => {
      cloudSet(KEY, value);
      if (value === AUTO) index = 0;
      apply(value);
    });
  }

  apply(AUTO, true);
  cloudGet(KEY).then((saved) => { if (alive && saved && saved !== mode) apply(saved, true); });
  BANNERS.forEach(preload);

  return { el, destroy: () => { alive = false; stop(); } };
}

function openBannerPicker(current, onPick) {
  openOverlay({
    kind: 'sheet',
    label: 'баннер профиля',
    className: 'sheet--banners',
    render: ({ close }) => {
      const choose = (value) => () => { haptic.select(); close(); onPick(value); };
      const mark = (on) => h('span', { class: 'bpick__check', hidden: !on }, icon('check'));
      const auto = h('button', { class: `bpick bpick--auto${current === AUTO ? ' is-on' : ''}`, type: 'button', onclick: choose(AUTO) },
        h('span', { class: 'bpick__stack', 'aria-hidden': 'true' },
          BANNERS.slice(0, 3).map((b) => h('i', { style: { backgroundImage: `url('${b.src}')` } }))),
        h('span', { class: 'bpick__label' }, icon('refresh'), 'По кругу'),
        mark(current === AUTO));
      return [
        h('div', { class: 'sheet__grab' }, h('h2', { class: 'sheet__title' }, 'Баннер профиля')),
        h('div', { class: 'sheet__body' },
          h('div', { class: 'bpicks' }, auto,
            BANNERS.map((b) => h('button', {
              class: `bpick${current === b.id ? ' is-on' : ''}`, type: 'button', 'aria-label': 'выбрать баннер',
              style: { backgroundImage: `url('${b.src}')` },
              onclick: choose(b.id),
            }, mark(current === b.id))))),
      ];
    },
  });
}
