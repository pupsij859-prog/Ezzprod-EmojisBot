// Плавающая панель вкладок с «линзой».
// Линза — капсула под активной вкладкой. По тапу она перелетает к вкладке на пружине (с лёгким перелётом
// и растяжением по скорости). Её можно схватить и тащить пальцем: линза «приподнимается», становится
// стеклянной, иконки под ней прячутся и оживают, когда она уходит. При отпускании линза с учётом инерции
// прилипает к ближайшей вкладке. Иконка вкладки, на которую приехала линза, проигрывает свою анимацию.
import { haptic } from './tg.js';
import { h, icon, prefersReducedMotion } from './ui.js';

const PAD = 4;                              // отступ линзы от краёв панели
const SNAP = { k: 290, zeta: 0.66 };        // перелёт к вкладке: небольшой перелёт и отскок
const FOLLOW = { k: 1100, zeta: 0.9 };      // следование за пальцем: почти без запаздывания
const LIFT = { k: 520, zeta: 0.62 };        // «подъём» линзы при захвате
const DRAG_START = 6;                       // px: дальше этого палец уже тащит, а не тапает
const FLING = 0.09;                         // с: насколько вперёд учитываем скорость при отпускании

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const rubber = (d) => (d * 0.4) / (1 + d / 90); // сопротивление за краями

function springStep(pos, vel, target, { k, zeta }, dt) {
  const c = 2 * zeta * Math.sqrt(k);
  const acc = -k * (pos - target) - c * vel;
  const v = vel + acc * dt;
  return [pos + v * dt, v];
}

/**
 * tabs: [{ id, label, icon, extra? (Node — например, бейдж) }]
 * onSelect(id) — пользователь выбрал вкладку (тапом или перетаскиванием).
 * → { el, setActive(id, { animate }) }
 */
export function TabBar({ tabs, active, onSelect }) {
  const lens = h('div', { class: 'tabbar__lens', 'aria-hidden': 'true' });
  const buttons = tabs.map((t, i) => h('button', {
    class: 'tab', type: 'button', role: 'tab', 'aria-label': t.label,
    dataset: { tab: t.id, icon: t.icon },
    onclick: () => {
      if (suppressClick) return;
      if (i !== index) { haptic.select(); onSelect(t.id); } else { play(i); }
    },
  },
  h('span', { class: 'tab__iconbox' }, icon(t.icon, 'tab__icon'), t.extra || null),
  h('span', { class: 'tab__label' }, t.label)));
  const bar = h('nav', { class: 'tabbar', role: 'tablist', 'aria-label': 'разделы', style: { '--n': String(tabs.length) } },
    lens, ...buttons);

  // анимация иконки: перезапуск класса
  buttons.forEach((b) => {
    const ic = b.querySelector('.tab__icon');
    ic.addEventListener('animationend', () => ic.classList.remove('is-play'));
  });
  function play(i) {
    if (prefersReducedMotion()) return;
    const ic = buttons[i].querySelector('.tab__icon');
    ic.classList.remove('is-play');
    void ic.getBoundingClientRect();
    ic.classList.add('is-play');
  }

  let index = Math.max(0, tabs.findIndex((t) => t.id === active));
  let x = 0; let v = 0; let target = 0;
  let lift = 0; let liftV = 0; let liftTarget = 0;
  let mode = SNAP;
  let raf = 0; let last = 0;
  let landed = true;            // активная иконка уже «ожила» после прилёта
  let drag = null;              // { id, startX, moved }
  let suppressClick = false;
  let lit = -1;
  const under = new Set();

  const tabW = () => (bar.clientWidth - PAD * 2) / tabs.length;
  const xFor = (i) => PAD + i * tabW();
  const indexAt = (pos) => clamp(Math.round((pos - PAD) / tabW()), 0, tabs.length - 1);

  function render() {
    const speed = Math.abs(v);
    const stretch = Math.min(speed / 3400, 0.18);
    const grow = 1 + lift * 0.12;
    // у краёв линза не вылезает за панель: перелёт превращается в сплющивание
    const min = PAD;
    const max = bar.clientWidth - PAD - tabW();
    const over = x < min ? min - x : x > max ? x - max : 0;
    const vx = x < min ? min - over * 0.2 : x > max ? max + over * 0.2 : x;
    const squash = Math.min(over / 60, 0.12);
    const sx = grow * (1 + stretch - squash);
    const sy = grow * (1 - stretch * 0.45 + squash * 0.5);
    lens.style.transform = `translate3d(${vx.toFixed(2)}px,0,0) scale(${sx.toFixed(4)},${sy.toFixed(4)})`;
    bar.classList.toggle('is-moving', speed > 40 || lift > 0.05);
    bar.classList.toggle('is-lifted', lift > 0.35);

    // подпись под линзой подсвечивается, пока линза в движении
    const i = indexAt(x);
    if (i !== lit) {
      lit = i;
      buttons.forEach((b, j) => b.classList.toggle('is-lit', j === i));
      if (drag && drag.moved) haptic.select();
    }

    // иконки под приподнятой линзой прячутся и «оживают», когда она уходит
    const w = tabW();
    buttons.forEach((b, j) => {
      const covered = lift > 0.3 && Math.abs(xFor(j) - x) < w * 0.42;
      if (covered && !under.has(j)) { under.add(j); b.classList.add('is-under'); }
      else if (!covered && under.has(j)) { under.delete(j); b.classList.remove('is-under'); play(j); }
    });

    // прилёт: активная иконка оживает, как только линза почти на месте
    if (!landed && !drag && Math.abs(x - target) < w * 0.22) {
      landed = true;
      if (!under.has(index)) play(index);
    }
  }

  function frame(now) {
    const dt = Math.min(0.034, (now - last) / 1000 || 0.016);
    last = now;
    for (let s = 0; s < 2; s += 1) { // два полушага: устойчиво даже при просадке fps
      [x, v] = springStep(x, v, target, mode, dt / 2);
      [lift, liftV] = springStep(lift, liftV, liftTarget, LIFT, dt / 2);
    }
    const resting = !drag && Math.abs(x - target) < 0.3 && Math.abs(v) < 6
      && Math.abs(lift - liftTarget) < 0.003 && Math.abs(liftV) < 0.02;
    if (resting) {
      x = target; v = 0; lift = liftTarget; liftV = 0;
      render();
      raf = 0;
      return;
    }
    render();
    raf = requestAnimationFrame(frame);
  }

  function kick() {
    if (prefersReducedMotion()) {
      x = target; v = 0; lift = liftTarget; liftV = 0;
      render();
      return;
    }
    if (!raf) { last = performance.now(); raf = requestAnimationFrame(frame); }
  }

  function setActive(id, { animate = true } = {}) {
    const i = tabs.findIndex((t) => t.id === id);
    if (i < 0) return;
    const changed = i !== index;
    index = i;
    buttons.forEach((b, j) => {
      b.classList.toggle('is-active', j === i);
      b.setAttribute('aria-selected', String(j === i));
    });
    mode = SNAP;
    target = xFor(i);
    if (!animate || !bar.isConnected) {
      x = target; v = 0;
      render();
      return;
    }
    if (changed) landed = false;
    kick();
  }

  // ── перетаскивание линзы
  bar.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    suppressClick = false;
    drag = { id: e.pointerId, startX: e.clientX, moved: false };
  });
  bar.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    if (!drag.moved) {
      if (Math.abs(e.clientX - drag.startX) < DRAG_START) return;
      drag.moved = true;
      try { bar.setPointerCapture(e.pointerId); } catch {}
      mode = FOLLOW;
      liftTarget = 1;
      haptic.tap();
    }
    const rect = bar.getBoundingClientRect();
    const w = tabW();
    const min = PAD;
    const max = rect.width - PAD - w;
    let tx = e.clientX - rect.left - w / 2;
    if (tx < min) tx = min - rubber(min - tx);
    if (tx > max) tx = max + rubber(tx - max);
    target = tx;
    kick();
  });
  const release = (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const moved = drag.moved;
    drag = null;
    if (!moved) return; // обычный тап — его обработает click
    suppressClick = true;
    setTimeout(() => { suppressClick = false; }, 60);
    liftTarget = 0;
    mode = SNAP;
    const i = indexAt(x + v * FLING);
    target = xFor(i);
    landed = false;
    kick();
    if (i !== index) onSelect(tabs[i].id);
    else play(i);
  };
  bar.addEventListener('pointerup', release);
  bar.addEventListener('pointercancel', release);
  bar.addEventListener('lostpointercapture', release);

  // пересчёт позиции при изменении ширины (поворот, ресайз окна)
  if (window.ResizeObserver) {
    new ResizeObserver(() => { if (!raf && !drag) { target = xFor(index); x = target; render(); } }).observe(bar);
  }

  requestAnimationFrame(() => setActive(tabs[index].id, { animate: false }));
  return { el: bar, setActive };
}
