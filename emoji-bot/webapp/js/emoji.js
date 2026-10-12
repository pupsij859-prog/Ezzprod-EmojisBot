// Кастомные эмодзи Telegram в текстах. Пока ID не задан (или бот не запущен) — обычный эмодзи.
// Сервер отдаёт файл по ключу: Lottie JSON (анимированные TGS), WebM (видео) или WebP (статичные).
import { state } from './store.js';
import { h, prefersReducedMotion } from './ui.js';

const LOTTIE_URL = 'https://cdnjs.cloudflare.com/ajax/libs/lottie-web/5.12.2/lottie_light.min.js';

const media = new Map(); // ключ → Promise<(() => Node) | null>
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

const Img = (url) => () => h('img', { class: 'tge__media', src: url, alt: '', draggable: false, decoding: 'async' });

async function resolve(key) {
  const url = `/api/emoji/${encodeURIComponent(key)}`;
  const res = await fetch(url);
  if (!res.ok) return null;
  const type = res.headers.get('content-type') || '';

  if (type.includes('json')) {
    const data = await res.json();
    const lib = await loadLottie();
    return () => {
      const box = h('span', { class: 'tge__media' });
      // Lottie меняет переданные данные — каждому экземпляру своя копия
      const anim = lib.loadAnimation({
        container: box, renderer: 'svg', loop: true, autoplay: !prefersReducedMotion(), animationData: structuredClone(data),
      });
      players.add({ box, anim });
      return box;
    };
  }
  if (type.includes('webm')) {
    if (!canPlayWebm()) { // iOS не умеет WebM с прозрачностью — берём статичную превью
      const thumb = await fetch(`${url}?thumb=1`);
      return thumb.ok ? Img(URL.createObjectURL(await thumb.blob())) : null;
    }
    const src = URL.createObjectURL(await res.blob());
    return () => {
      const video = h('video', { class: 'tge__media', src, muted: true, loop: true, playsInline: true, autoplay: !prefersReducedMotion() });
      video.setAttribute('playsinline', '');
      return video;
    };
  }
  return Img(URL.createObjectURL(await res.blob()));
}

/** Эмодзи по ключу из app/emoji.py: сначала обычный, затем (если есть) кастомный из Telegram. */
export function TgEmoji(key) {
  const info = (state.emoji || {})[key];
  const el = h('span', { class: 'tge', 'aria-hidden': 'true' }, info ? info.fallback : '');
  if (info && info.custom) {
    if (!media.has(key)) media.set(key, resolve(key).catch(() => null));
    media.get(key).then((make) => {
      if (!make) return;
      el.replaceChildren(make());
      el.classList.add('is-custom');
    });
  }
  return el;
}
