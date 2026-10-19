// Эмодзи в интерфейсе — только картинки, никаких эмодзи-символов системы:
// премиум-эмодзи Telegram (сначала превью WebP, затем анимация, если бот её отдал) или рисованный SVG.
// Сервер отдаёт анимацию по custom_emoji_id: Lottie JSON (TGS), WebM (видео) или WebP (статичные).
import { state } from './store.js';
import { h, prefersReducedMotion } from './ui.js';

const LOTTIE_URL = 'https://cdnjs.cloudflare.com/ajax/libs/lottie-web/5.12.2/lottie_light.min.js';

const media = new Map(); // custom_emoji_id → Promise<(() => Node) | null>
const players = new Set(); // запущенные Lottie: останавливаем, когда узел ушёл из DOM
let lottie = null;

function loadLottie() {
  lottie ||= new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = LOTTIE_URL;
    s.async = true;
    s.crossOrigin = 'anonymous';
    s.onload = () => (window.lottie ? resolve(window.lottie) : reject(new Error('lottie')));
    s.onerror = reject;
    document.head.append(s);
  });
  return lottie;
}

setInterval(() => {
  players.forEach((p) => {
    if (!p.box.isConnected) { p.anim.destroy(); players.delete(p); }
  });
}, 4000);

const canPlayWebm = () => {
  try { return Boolean(document.createElement('video').canPlayType('video/webm; codecs="vp9"')); } catch { return false; }
};

const Img = (src, cls = 'tge__media') => h('img', { class: cls, src, alt: '', draggable: false, decoding: 'async' });

async function resolve(emojiId) {
  const url = `/api/emoji/id/${encodeURIComponent(emojiId)}`;
  const res = await fetch(url);
  if (!res.ok) return null;
  const type = res.headers.get('content-type') || '';

  if (type.includes('json')) {
    const data = await res.json();
    const lib = await loadLottie();
    return ({ loop = true } = {}) => {
      const box = h('span', { class: 'tge__media tge__media--lottie' });
      // Lottie меняет переданные данные — каждому экземпляру своя копия
      const anim = lib.loadAnimation({
        container: box, renderer: 'svg', loop, autoplay: !prefersReducedMotion(), animationData: structuredClone(data),
      });
      players.add({ box, anim });
      return box;
    };
  }
  if (type.includes('webm')) {
    if (!canPlayWebm()) return null; // iOS не играет WebM с прозрачностью — остаётся превью
    const src = URL.createObjectURL(await res.blob());
    return ({ loop = true } = {}) => {
      const video = h('video', { class: 'tge__media', src, muted: true, loop, playsInline: true, autoplay: !prefersReducedMotion() });
      video.setAttribute('playsinline', '');
      return video;
    };
  }
  const src = URL.createObjectURL(await res.blob());
  return () => Img(src);
}

/** Анимированная версия премиум-эмодзи (или null). Один запрос на ID. */
function animatedEmoji(emojiId) {
  if (!media.has(emojiId)) media.set(emojiId, resolve(emojiId).catch(() => null));
  return media.get(emojiId);
}

/** Подменяет превью в el на анимацию, когда она загрузится. */
function upgrade(el, emojiId, opts) {
  animatedEmoji(emojiId).then((make) => {
    if (!make) return;
    el.replaceChildren(make(opts));
    el.classList.add('is-animated');
  });
}

/** Эмодзи по ключу из app/emoji.py (тексты-цитаты, подсказки). */
export function TgEmoji(key) {
  const info = (state.emoji || {})[key];
  const src = info && (info.thumb || info.svg);
  const el = h('span', { class: 'tge', 'aria-hidden': 'true' }, src ? Img(src) : null);
  if (info && info.custom && info.id) upgrade(el, info.id, {});
  return el;
}
