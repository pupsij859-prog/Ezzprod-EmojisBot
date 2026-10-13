// Нижняя панель вкладок — плавающая стеклянная капсула, как в новом Telegram (перенесена из соседнего
// проекта мини-аппа). Плашка ездит за активной вкладкой с пружинкой; её можно тащить пальцем — она
// меняет ширину между вкладками, иконка под ней оживает, у крайней вкладки плашка вжимается в стенку
// и отскакивает, когда её отпускают.
import { TabIcon } from './tabicons.js';
import { haptic } from './tg.js';
import { h, prefersReducedMotion } from './ui.js';

const SQUASH_RANGE = 70; // на сколько px утянуть за край, чтобы сплющить плашку до упора

/**
 * tabs: [{ id, label, icon, extra? }]; onSelect(id) — пользователь выбрал вкладку (тапом или перетаскиванием).
 * → { el, setActive(id, { animate }) }
 */
export function TabBar({ tabs: list, active, onSelect }) {
  const glass = h('span', { class: 'tab-pill-glass' });
  const pill = h('span', { class: 'tab-pill', 'aria-hidden': 'true' }, glass);
  const tabs = list.map((t) => h('button', {
    class: `tab${t.id === active ? ' is-active' : ''}`, type: 'button', role: 'tab', 'aria-label': t.label,
    dataset: { tab: t.id },
  }, TabIcon(t.icon), h('span', null, t.label), t.extra || null));
  const tabbar = h('nav', { class: `tabbar${tabs.length > 3 ? ' has-4-tabs' : ''}`, role: 'tablist', 'aria-label': 'разделы' },
    pill, ...tabs);

  const reduceMotion = prefersReducedMotion();
  let current = active;
  let pillPos = null;
  let pillAnim = null;
  let drag = null;
  let ignoreClickUntil = 0;
  let skipIcon = false;

  const tabBox = (tab) => ({ center: tab.offsetLeft + tab.offsetWidth / 2, width: tab.offsetWidth });
  const pillTransform = (center, width) => `translateX(${center - width / 2}px)`;
  const glassFrame = (x, sx, sy, offset) => ({ transform: `translateX(${x}px) scale(${sx}, ${sy})`, ...(offset && { offset }) });

  function placePill(center, width) {
    pill.style.top = `${tabs[0].offsetTop}px`;
    pill.style.height = `${tabs[0].offsetHeight}px`;
    pill.style.width = `${width}px`;
    pill.style.transform = pillTransform(center, width);
    pillPos = { center, width };
  }

  function pillToTab(name, animate = true) {
    const index = tabs.findIndex((t) => t.dataset.tab === name);
    if (index < 0 || !tabs[index].offsetWidth) return;
    const { center, width } = tabBox(tabs[index]);
    const from = pillPos;
    if (pillAnim) pillAnim.cancel();
    placePill(center, width);
    if (!animate || !from || reduceMotion) return;
    const dist = center - from.center;
    if (Math.abs(dist) < 1) return;
    const frames = [
      { transform: pillTransform(from.center, from.width), width: `${from.width}px` },
      { transform: pillTransform(center, width), width: `${width}px` },
    ];
    // к крайней вкладке подъезжает мягко и останавливается у стенки, не стукаясь
    // (о стенку плашка бьётся, только когда её дотащили туда пальцем — см. squash); к средней — с пружинкой
    const towardWall = (index === 0 && dist < 0) || (index === tabs.length - 1 && dist > 0);
    const easing = towardWall ? 'cubic-bezier(0.22, 1, 0.36, 1)' : 'cubic-bezier(0.32, 1.32, 0.5, 1)';
    pillAnim = pill.animate(frames, { duration: 520, easing });
  }

  // положение пальца → центр и ширина плашки; за крайними вкладками она упирается в стенку (over — насколько тянем дальше)
  function pillAt(x) {
    const boxes = tabs.map(tabBox);
    const first = boxes[0];
    const last = boxes[boxes.length - 1];
    if (x < first.center) return { center: first.center, width: first.width, index: 0, over: x - first.center };
    if (x > last.center) return { center: last.center, width: last.width, index: boxes.length - 1, over: x - last.center };
    const k = Math.max(0, boxes.findIndex((b, i) => i < boxes.length - 1 && x <= boxes[i + 1].center));
    const a = boxes[k];
    const b = boxes[k + 1] || a;
    const t = b === a ? 0 : (x - a.center) / (b.center - a.center);
    return { center: x, width: a.width + (b.width - a.width) * t, index: t < 0.5 ? k : k + 1, over: 0 };
  }

  // пока тянем за край — плашка вжимается в стенку тем сильнее, чем дальше палец
  function squash(over) {
    if (!over) {
      if (drag.squash) {
        drag.squash = null;
        glass.style.transform = '';
        glass.style.transformOrigin = '';
        tabbar.classList.remove('is-squashing');
      }
      return;
    }
    const side = over < 0 ? 'left' : 'right';
    if (drag.squash !== side) {
      tabbar.classList.add('is-squashing');
      haptic.rigid();
    }
    drag.squash = side;
    const k = Math.min(1, Math.abs(over) / SQUASH_RANGE);
    glass.style.transformOrigin = `${side} center`;
    glass.style.transform = `translateX(${Math.sign(over) * 2 * k}px) scale(${1.07 - 0.35 * k}, ${1.07 + 0.08 * k})`;
  }

  // отпустили сплющенную плашку — она отскакивает от стенки
  function unsquash() {
    const from = glass.style.transform;
    glass.style.transform = '';
    tabbar.classList.remove('is-squashing');
    if (reduceMotion) { glass.style.transformOrigin = ''; return; }
    glass.animate(
      [{ transform: from }, glassFrame(0, 1.09, 0.92, 0.32), glassFrame(0, 0.97, 1.03, 0.62), glassFrame(0, 1, 1)],
      { duration: 520, easing: 'ease-out' },
    ).onfinish = () => { glass.style.transformOrigin = ''; };
  }

  // анимация иконки вкладки (сами анимации — в components.css, класс is-playing)
  function playIcon(name) {
    const tab = tabs.find((t) => t.dataset.tab === name);
    if (!tab || reduceMotion) return;
    tab.classList.remove('is-playing');
    void tab.offsetWidth; // перезапуск CSS-анимации
    tab.classList.add('is-playing');
  }

  tabbar.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    const index = tabs.findIndex((t) => t.dataset.tab === current);
    drag = { id: e.pointerId, startX: e.clientX, active: false, squash: null, index };
  });

  tabbar.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    if (!drag.active) {
      if (Math.abs(e.clientX - drag.startX) < 6) return;
      drag.active = true;
      if (pillAnim) pillAnim.cancel();
      try { tabbar.setPointerCapture(e.pointerId); } catch {}
      tabbar.classList.add('is-dragging');
      tabs[drag.index].classList.add('is-near');
    }
    const { center, width, index, over } = pillAt(e.clientX - tabbar.getBoundingClientRect().left);
    placePill(center, width);
    squash(over);
    if (index !== drag.index) {
      tabs[drag.index].classList.remove('is-near');
      tabs[index].classList.add('is-near');
      drag.index = index;
      playIcon(tabs[index].dataset.tab); // иконка оживает, когда плашка наезжает на неё
      haptic.select();
    }
  });

  function endDrag(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const { active: moved, index, squash: squashed } = drag;
    drag = null;
    if (!moved) return; // обычное нажатие — его обработает click
    ignoreClickUntil = performance.now() + 400;
    tabbar.classList.remove('is-dragging');
    tabs.forEach((t) => t.classList.remove('is-near'));
    if (squashed) unsquash();
    const name = tabs[index].dataset.tab;
    if (name === current) pillToTab(name);
    else { skipIcon = true; onSelect(name); } // иконка уже сыграла, когда плашка на неё наехала
  }
  tabbar.addEventListener('pointerup', endDrag);
  tabbar.addEventListener('pointercancel', endDrag);

  tabbar.addEventListener('click', (e) => {
    if (performance.now() < ignoreClickUntil) return;
    const tab = e.target.closest('.tab');
    if (!tab) return;
    if (tab.dataset.tab === current) playIcon(current);
    else { haptic.select(); onSelect(tab.dataset.tab); }
  });

  function setActive(id, { animate = true } = {}) {
    const icon = !skipIcon;
    skipIcon = false;
    if (!tabs.some((t) => t.dataset.tab === id)) return;
    const changed = id !== current;
    current = id;
    tabs.forEach((t) => {
      const on = t.dataset.tab === id;
      t.classList.toggle('is-active', on);
      t.setAttribute('aria-selected', String(on));
    });
    pillToTab(id, animate && changed);
    if (changed && icon) playIcon(id);
  }

  // первая расстановка без анимации; дальше следим за шрифтами и размером экрана
  new ResizeObserver(() => { if (!drag || !drag.active) pillToTab(current, false); }).observe(tabbar);
  if (document.fonts) document.fonts.ready.then(() => pillToTab(current, false));
  setTimeout(() => playIcon(current), 450);

  return { el: tabbar, setActive };
}
