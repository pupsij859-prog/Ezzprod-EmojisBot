// «Наборы»: лента на главной и шторка набора. Набор — шаблоны в одном стиле с готовой подборкой цветов,
// шрифтом и градиентом; после выбора открывается оформление, где остаётся вписать ник.
import { Button, dragScroll } from '../components.js';
import { openOverlay } from '../nav.js';
import { applyKit, KITS, kitLook, kitTemplates, paletteById } from '../presets.js';
import { state } from '../store.js';
import { haptic } from '../tg.js';
import { lookFromParams, TgsPreview, watchVisibility } from '../tgs.js';
import { emojiWord, h, icon } from '../ui.js';
import { openOrder } from './customize.js';

const gradCss = (kit) => {
  const p = paletteById(kit.palette);
  return `linear-gradient(135deg, ${p.a}, ${p.b})`;
};

// карточка: слева название, подпись и сколько эмодзи, справа «веер» из трёх живых шаблонов в оформлении набора;
// фон светится цветами подборки. players — проигрыватели «веера» (их запускает и останавливает лента)
function KitCard(kit, players) {
  const ids = kitTemplates(kit);
  const p = paletteById(kit.palette);
  const fan = ids.slice(0, 3).map((id) => TgsPreview(id, { lazy: true, cls: 'tgs--kit' }));
  players.push(...fan.map((player) => ({ kit, player })));
  return h('button', { class: 'kit', type: 'button', role: 'listitem', 'aria-label': `набор «${kit.name}»`,
    style: { '--a': p.a, '--b': p.b, '--kit': gradCss(kit) }, onclick: () => { haptic.tap(); openKit(kit); } },
  h('span', { class: 'kit__info' },
    h('span', { class: 'kit__tag' }, kit.tag),
    h('span', { class: 'kit__name' }, kit.name),
    h('span', { class: 'kit__meta' },
      h('span', { class: 'kit__count' }, emojiWord(ids.length, 'emoji')),
      h('span', { class: 'kit__font' }, kit.font.name))),
  h('span', { class: 'kit__fan', 'aria-hidden': 'true' },
    ...fan.map((player, n) => h('span', { class: `kit__card kit__card--${n}` }, player.el))),
  h('span', { class: 'kit__go', 'aria-hidden': 'true' }, icon('chev-right')));
}

/** Лента наборов для главной (пусто, пока каталог не загружен). С компьютера листается мышкой. Шаблоны в карточках
 *  анимированы, играют только видимые; ник и логотип пользователя видны сразу. → { el, paint, refresh, destroy } */
export function KitsStrip() {
  const list = dragScroll(h('div', { class: 'kits', role: 'list', 'aria-label': 'наборы' }));
  const seen = watchVisibility(null); // относительно экрана: и вбок по ленте, и вверх-вниз по странице
  let players = [];
  let version = 0;
  const refresh = async () => {
    const mine = ++version;
    const kits = [...new Set(players.map((p) => p.kit))];
    const looks = await Promise.all(kits.map((kit) => lookFromParams({ ...kitLook(kit), text: state.text, logo: state.logo }, true)
      .catch(() => null)));
    if (mine !== version) return;
    const byKit = new Map(kits.map((kit, n) => [kit, looks[n]]));
    players.forEach(({ kit, player }) => player.update(byKit.get(kit)));
  };
  const paint = () => {
    players.forEach(({ player }) => player.destroy());
    players = [];
    list.replaceChildren(...KITS.filter((k) => kitTemplates(k).length).map((kit) => KitCard(kit, players)));
    players.forEach(({ player }) => seen.add(player));
    refresh();
  };
  paint();
  return {
    el: list, paint, refresh,
    destroy() { version += 1; seen.disconnect(); players.forEach(({ player }) => player.destroy()); players = []; },
  };
}

/** Шторка набора: живые превью шаблонов в его оформлении (с ником пользователя, если он есть) и кнопка выбора. */
export function openKit(kit) {
  const ids = kitTemplates(kit);
  const p = paletteById(kit.palette);
  let seen = null;
  let players = [];
  openOverlay({
    kind: 'sheet',
    label: `набор ${kit.name}`,
    className: 'sheet--kit',
    onClose: () => { if (seen) seen.disconnect(); players.forEach((pl) => pl.destroy()); players = []; },
    render: ({ close }) => {
      const grid = h('div', { class: 'kit-grid', role: 'list' });
      const body = h('div', { class: 'sheet__body' },
        h('p', { class: 'kit-sheet__text' }, kit.text),
        h('div', { class: 'kit-sheet__chips' },
          h('span', { class: 'kit-chip' }, h('i', { class: 'kit-chip__dot', style: { background: gradCss(kit) } }),
            kit.native ? 'родные цвета · ник в градиенте' : p.name),
          h('span', { class: 'kit-chip' }, `шрифт ${kit.font.name}`),
          h('span', { class: 'kit-chip' }, emojiWord(ids.length, 'emoji'))),
        grid,
        h('div', { class: 'kit-sheet__cta' },
          Button({ label: state.text ? 'Выбрать набор' : 'Выбрать и вписать ник', onClick: () => {
            applyKit(kit);
            haptic.success();
            close();
            openOrder({ focusText: true });
          } }),
          h('p', { class: 'kit-sheet__hint' }, 'Выбранные сейчас шаблоны заменятся набором. Цвета, шрифт и градиент можно поменять на следующем шаге.')));
      seen = watchVisibility(body);
      players = ids.map((id) => TgsPreview(id, { lazy: true, cls: 'tgs--card' }));
      players.forEach((pl) => seen.add(pl));
      grid.append(...players.map((pl) => h('div', { class: 'kit-grid__item', role: 'listitem' }, pl.el)));
      // оформление набора с ником и логотипом пользователя
      lookFromParams({ ...kitLook(kit), text: state.text, logo: state.logo }, true)
        .then((look) => players.forEach((pl) => pl.update(look))).catch(() => {});
      return [
        h('div', { class: 'sheet__grab' },
          h('h2', { class: 'sheet__title' }, kit.name),
          h('span', { class: 'kit-sheet__tag', style: { background: gradCss(kit) } }, kit.tag)),
        body,
      ];
    },
  });
}
