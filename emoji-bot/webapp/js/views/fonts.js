// Шторка «Шрифт ника»: 15 готовых шрифтов и свои загруженные (.ttf). В каждой карточке — ник пользователя,
// нарисованный этим шрифтом: контуры букв считает сервер тем же кодом, что и сборщик пака.
import { api, ApiError } from '../api.js';
import { openOverlay } from '../nav.js';
import { forgetFont, fontInUse } from '../store.js';
import { haptic } from '../tg.js';
import { textDrawing } from '../tgs.js';
import { h, icon, toast } from '../ui.js';

export const FONT_ERRORS = {
  font_otf: 'Этот шрифт в формате OpenType (CFF). Нужен .ttf',
  font_web: 'Это веб-шрифт (woff). Нужен .ttf',
  font_invalid: 'Не получилось прочитать шрифт. Нужен .ttf',
  font_empty: 'В шрифте нет обычных букв',
  font_too_big: 'Файл шрифта слишком большой',
  font_quota: 'Своих шрифтов уже максимум: удали ненужный',
};

const SVG = 'http://www.w3.org/2000/svg';

/** Векторный рисунок надписи ({bbox, layers: [{paths: [{v, i, o}]}]}) → <svg>, залитый currentColor. */
function drawingSvg(drawing) {
  const [x0, y0, x1, y1] = drawing.bbox;
  const pad = Math.max(x1 - x0, y1 - y0) * 0.04;
  const r = (n) => Math.round(n * 10) / 10;
  const pt = (a, b) => `${r(a[0] + b[0])} ${r(a[1] + b[1])}`;
  const d = drawing.layers.flatMap((l) => l.paths).map(({ v, i, o }) => {
    let s = `M${r(v[0][0])} ${r(v[0][1])}`;
    for (let k = 0; k < v.length; k++) {
      const n = (k + 1) % v.length;
      s += `C${pt(v[k], o[k])} ${pt(v[n], i[n])} ${r(v[n][0])} ${r(v[n][1])}`;
    }
    return `${s}Z`;
  }).join('');
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', `${r(x0 - pad)} ${r(y0 - pad)} ${r(x1 - x0 + 2 * pad)} ${r(y1 - y0 + 2 * pad)}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(SVG, 'path');
  path.setAttribute('d', d);
  svg.append(path);
  return svg;
}

/**
 * current — выбранный шрифт {id, name} или null (стандартный); sample — ник для превью.
 * onPick(font | null) — выбран шрифт (шторка закрывается сама).
 */
export function openFonts({ current, sample, maxBytes, onPick }) {
  const text = (sample || '').trim() || 'Ник';
  openOverlay({
    kind: 'sheet',
    label: 'шрифт ника',
    className: 'sheet--fonts',
    render: ({ close }) => {
      const presetGrid = h('div', { class: 'fontgrid' });
      const ownGrid = h('div', { class: 'fontgrid' });
      const ownCount = h('span', { class: 'fonts__count' });
      const file = h('input', { type: 'file', accept: '.ttf,font/ttf,application/x-font-ttf,.otf', hidden: true });
      let data = null;
      let uploading = false;

      const pick = (font) => {
        haptic.select();
        close();
        onPick(font);
      };

      function Preview(fontId) {
        const box = h('span', { class: 'fontcard__art skeleton' });
        textDrawing(text, fontId).then((drawing) => {
          box.classList.remove('skeleton');
          box.replaceChildren(drawing ? drawingSvg(drawing) : h('span', { class: 'fontcard__miss' }, 'нет букв'));
        }).catch(() => {
          box.classList.remove('skeleton');
          box.replaceChildren(h('span', { class: 'fontcard__miss' }, 'не загрузилось'));
        });
        return box;
      }

      function Card({ font, name, tag, extra = null }) {
        const id = font ? font.id : null;
        const active = (current ? current.id : null) === id;
        return h('div', { class: `fontcard${active ? ' is-active' : ''}${extra ? ' fontcard--own' : ''}` },
          h('button', {
            class: 'fontcard__pick', type: 'button', 'aria-pressed': String(active), 'aria-label': `шрифт ${name}`,
            onclick: () => pick(font),
          },
          Preview(id),
          h('span', { class: 'fontcard__meta' },
            h('span', { class: 'fontcard__name' }, name),
            tag ? h('span', { class: 'fontcard__tag' }, tag) : null)),
          active ? h('span', { class: 'fontcard__mark', 'aria-hidden': 'true' }, icon('check')) : null,
          extra);
      }

      function DeleteButton(font) {
        let armed = 0;
        const btn = h('button', { class: 'fontcard__del', type: 'button', 'aria-label': `удалить шрифт ${font.name}` }, icon('trash'));
        btn.addEventListener('click', async () => {
          // первое нажатие — «точно?», второе в течение пары секунд — удаляем
          if (!armed) {
            haptic.tap();
            btn.classList.add('is-armed');
            armed = setTimeout(() => { armed = 0; btn.classList.remove('is-armed'); }, 2400);
            return;
          }
          clearTimeout(armed);
          btn.disabled = true;
          try {
            await api.deleteFont(font.id);
          } catch (err) {
            if (!(err instanceof ApiError && err.status === 404)) {
              btn.disabled = false;
              toast('Не получилось удалить шрифт', 'warn');
              return;
            }
          }
          haptic.success();
          data.own = data.own.filter((f) => f.id !== font.id);
          if (fontInUse(font.id)) forgetFont(font.id);
          if (current && current.id === font.id) current = null;
          paint();
        });
        return btn;
      }

      const uploadLabel = () => [
        h('span', { class: 'fontcard__plus' }, icon('plus')),
        h('span', { class: 'fontcard__name' }, 'Загрузить .ttf'),
        h('span', { class: 'fontcard__tag' }, `до ${String(Math.round((maxBytes / 1048576) * 10) / 10).replace('.', ',')} МБ`)];
      const uploadCard = h('button', { class: 'fontcard fontcard--add', type: 'button', onclick: () => {
        if (uploading) return;
        if (data && data.own.length >= data.max) { haptic.error(); toast(FONT_ERRORS.font_quota, 'warn'); return; }
        file.click();
      } }, ...uploadLabel());

      file.addEventListener('change', async () => {
        const f = file.files && file.files[0];
        file.value = '';
        if (!f) return;
        if (f.size > maxBytes) { haptic.error(); toast(FONT_ERRORS.font_too_big, 'warn'); return; }
        uploading = true;
        uploadCard.classList.add('is-busy');
        uploadCard.replaceChildren(h('span', { class: 'spinner', 'aria-label': 'загружаем' }), h('span', { class: 'fontcard__tag' }, 'проверяем шрифт…'));
        try {
          const font = await api.uploadFont(f);
          haptic.success();
          toast(`Шрифт «${font.name}» добавлен`);
          pick(font);
        } catch (err) {
          haptic.error();
          const field = err instanceof ApiError ? (err.code === 'too_large' ? 'font_too_big' : err.detail.field) : null;
          toast(FONT_ERRORS[field] || 'Не получилось загрузить шрифт', 'warn');
          uploading = false;
          uploadCard.classList.remove('is-busy');
          paint();
        }
      });

      function paint() {
        if (!data) return;
        presetGrid.replaceChildren(
          Card({ font: null, name: 'Стандартный', tag: null }),
          ...data.presets.map((p) => Card({ font: { id: p.id, name: p.name }, name: p.name, tag: p.tag })));
        ownCount.textContent = `${data.own.length} из ${data.max}`;
        if (!uploading) uploadCard.replaceChildren(...uploadLabel());
        ownGrid.replaceChildren(
          ...data.own.map((f) => Card({ font: { id: f.id, name: f.name }, name: f.name, tag: 'свой', extra: DeleteButton(f) })),
          uploadCard);
      }

      const body = h('div', { class: 'sheet__body fonts__body' },
        h('div', { class: 'fonts__head' }, h('span', { class: 'fonts__label' }, 'Готовые'), h('span', { class: 'fonts__count' }, 'Google Fonts')),
        presetGrid,
        h('div', { class: 'fonts__head' }, h('span', { class: 'fonts__label' }, 'Мои шрифты'), ownCount),
        ownGrid,
        h('p', { class: 'fonts__hint' }, 'Свой шрифт — файл .ttf с кириллицей и латиницей. Подойдут шрифты с Google Fonts, Font Squirrel и похожих сайтов.'),
        file);

      presetGrid.append(...Array.from({ length: 6 }, () => h('span', { class: 'fontcard fontcard--ghost skeleton' })));
      api.fonts().then((res) => { data = res; paint(); }).catch(() => {
        presetGrid.replaceChildren(h('p', { class: 'fonts__hint' }, 'Не получилось загрузить шрифты, попробуй ещё раз'));
      });

      return [
        h('div', { class: 'sheet__grab' },
          h('h2', { class: 'sheet__title' }, 'Шрифт ника'),
          h('p', { class: 'fonts__sub' }, (sample || '').trim()
            ? ['Превью — твой ник «', h('b', null, text), '»'] : 'Шрифт меняет ник. Пока ника нет, на стикере родная надпись')),
        body,
      ];
    },
  });
}
