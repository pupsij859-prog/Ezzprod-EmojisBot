// Иконки нижней панели — встроенные SVG; части разнесены по классам i-*, их анимации в components.css
// (класс is-playing на вкладке). Перенесено из соседнего проекта мини-аппа, «Паки» дорисован в том же стиле.
const NS = 'http://www.w3.org/2000/svg';

function el([tag, attrs, children = []]) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  children.forEach((c) => node.append(el(c)));
  return node;
}

const ICONS = {
  home: [
    ['g', { class: 'i-home' }, [
      ['path', { d: 'M3.75 10.2 12 3.75l8.25 6.45V18.5a2 2 0 0 1-2 2H5.75a2 2 0 0 1-2-2z' }],
      ['path', { class: 'i-home-door', pathLength: '1', d: 'M12 17.75v-2.5' }],
    ]],
  ],
  // стикер с загнутым уголком: карточка подпрыгивает, уголок отгибается, улыбка дорисовывается
  packs: [
    ['g', { class: 'i-pack' }, [
      ['path', { d: 'M20.25 12.6V7.75a4 4 0 0 0-4-4h-8.5a4 4 0 0 0-4 4v8.5a4 4 0 0 0 4 4h4.85' }],
      ['path', { class: 'i-pack-fold', d: 'M12.6 20.25 20.25 12.6M12.6 20.25V17.1a4.5 4.5 0 0 1 4.5-4.5h3.15' }],
      ['path', { d: 'M9.2 9.4h.01M13.8 9.4h.01' }],
      ['path', { class: 'i-pack-smile', pathLength: '1', d: 'M8.4 13.4c1.25 1.15 3.1 1.2 4.4.15' }],
    ]],
  ],
  'user-circle': [
    ['circle', { class: 'i-user-ring', pathLength: '1', cx: '12', cy: '12', r: '8.75' }],
    ['circle', { class: 'i-user-head', cx: '12', cy: '10', r: '3' }],
    ['path', { class: 'i-user-body', pathLength: '1', d: 'M6.4 18.4a6.75 6.75 0 0 1 11.2 0' }],
  ],
  crown: [
    ['g', { class: 'i-crown' }, [
      ['path', { d: 'M4.5 8.5 8.25 12 12 6.25 15.75 12 19.5 8.5 18 17.25H6z' }],
      ['path', { d: 'M6.5 20.25h11' }],
    ]],
  ],
};

export function TabIcon(name) {
  return el(['svg', {
    viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.7',
    'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false',
  }, ICONS[name] || []]);
}
