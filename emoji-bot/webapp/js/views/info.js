import { Button, Group, Quote, Row } from '../components.js';
import { openOverlay } from '../nav.js';
import { state } from '../store.js';
import { openLink } from '../tg.js';
import { gens, h } from '../ui.js';

export function openFreeInfo() {
  const reward = state.me.referral_reward;
  openOverlay({
    kind: 'modal',
    label: 'бонусы',
    className: 'modal--info',
    render: ({ close }) => [
      h('h2', { class: 'modal__title' }, 'Бесплатно и проба'),
      h('div', { class: 'quotes' },
        Quote({ emoji: 'gift', title: '1 генерация в подарок', text: 'хватит на один эмодзи или стикер, чтобы попробовать.' }),
        Quote({ emoji: 'box', title: 'Пробный экспорт', text: 'собери свой первый пак на подарочную генерацию.' }),
        Quote({ emoji: 'friends', title: `+${gens(reward)} за друга`, text: 'когда друг по твоей ссылке соберёт свой первый пак.' }),
        Quote({ emoji: 'crown', title: `Pro от ${state.me.pro_min} купленных`, text: 'открывает сохранённые стили.' })),
      Button({ label: 'Понятно', onClick: close }),
    ],
  });
}

export function openDocs() {
  const open = (path) => openLink(`${location.origin}${path}`);
  openOverlay({
    kind: 'modal',
    label: 'документы',
    className: 'modal--docs',
    render: ({ close }) => [
      h('h2', { class: 'modal__title' }, 'Документы'),
      Group(
        Row({ label: 'Условия покупки', iconName: 'doc', onClick: () => open('/docs/terms.html') }),
        Row({ label: 'Политика конфиденциальности', iconName: 'doc', onClick: () => open('/docs/privacy.html') })),
      Button({ label: 'Закрыть', variant: 'ghost', onClick: close }),
    ],
  });
}
