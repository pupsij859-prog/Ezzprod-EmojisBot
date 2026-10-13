// Общая часть страниц документов: навигация между ними и премиум-эмодзи (превью сразу, затем анимация,
// если бот отдаёт её через /api/emoji/id/<id>). Текст вставляется только через textContent.
const DOCS = [
  { href: 'terms.html', title: 'Условия покупки', sub: 'Что вы покупаете и как платите', emoji: '5395732581780040886', pack: 'kawaii' },
  { href: 'privacy.html', title: 'Политика конфиденциальности', sub: 'Какие данные мы храним и зачем', emoji: '5397890811436213746', pack: 'kawaii' },
  { href: 'refund.html', title: 'Возврат Stars', sub: 'Когда и как вернуть оплату', emoji: '5287231198098117669', pack: 'finance' },
];
const LOTTIE_URL = 'https://cdnjs.cloudflare.com/ajax/libs/lottie-web/5.12.2/lottie_light.min.js';

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => { if (k === 'class') node.className = v; else node.setAttribute(k, v); });
  children.forEach((c) => node.append(c instanceof Node ? c : document.createTextNode(String(c))));
  return node;
}

function Emoji(id, pack) {
  const box = el('span', { class: 'dk-emoji', 'data-emoji-id': id, 'aria-hidden': 'true' },
    el('img', { src: `../assets/packs/${pack}/${id}.webp`, alt: '' }));
  return box;
}

let lottie;
const loadLottie = () => {
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
};

async function animate(box) {
  try {
    const res = await fetch(`/api/emoji/id/${encodeURIComponent(box.dataset.emojiId)}`);
    if (!res.ok || !(res.headers.get('content-type') || '').includes('json')) return;
    const data = await res.json();
    const lib = await loadLottie();
    const host = el('span');
    box.replaceChildren(host);
    const still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    lib.loadAnimation({ container: host, renderer: 'svg', loop: true, autoplay: !still, animationData: data });
  } catch { /* остаётся превью */ }
}

// навигация
const here = location.pathname.split('/').pop() || 'terms.html';
const nav = document.getElementById('dk-nav');
if (nav) {
  nav.append(...DOCS.map((d) => el('a', { class: `dk-item${d.href === here ? ' is-active' : ''}`, href: d.href },
    Emoji(d.emoji, d.pack),
    el('span', { class: 'dk-item__text' }, el('b', {}, d.title), el('span', {}, d.sub)))));
}

// эмодзи в шапке документа
const head = document.querySelector('[data-doc-emoji]');
if (head) {
  const doc = DOCS.find((d) => d.href === here) || DOCS[0];
  head.replaceWith(Emoji(doc.emoji, doc.pack));
}

document.querySelectorAll('[data-emoji-id]').forEach(animate);
