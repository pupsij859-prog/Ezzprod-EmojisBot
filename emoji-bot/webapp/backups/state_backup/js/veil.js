// Фон карточек профиля: Dark Veil (React Bits, https://reactbits.dev/c/backgrounds/dark-veil) —
// шёлковая вуаль света, которая плывёт в темноте и гнётся под пальцем. Порт без React.
// На все карточки один WebGL2-контекст вне экрана: кадр каждой карточки рисуется в нём и сразу
// копируется в её 2D-холст. Так не упираемся в лимит WebGL-контекстов на телефоне.
// Без WebGL2 карточка просто остаётся с CSS-фоном.
import { prefersReducedMotion } from './ui.js';
import { COMPOSITE, FIELD, QUAD_VERTEX } from './veil-shaders.js';

const W = 420;              // размер кадра (как «вид» в оригинале); карточка берёт из него кроп под свой формат
const H = 280;
const QUALITY = 0.5;        // поле считается в половинном разрешении, как в оригинале по умолчанию
const FPS = 30;
const INTRO_SECONDS = 1.8;

let shared;                 // { gl, canvas, field, composite, vao, texture, framebuffer, fw, fh, floatTargets } | null
const veils = new Set();
let raf = 0;
let lastFrame = 0;

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return shader;
  gl.deleteShader(shader);
  return null;
}

function link(gl, fragmentSource) {
  const vertex = compile(gl, gl.VERTEX_SHADER, QUAD_VERTEX);
  const fragment = compile(gl, gl.FRAGMENT_SHADER, fragmentSource);
  if (!vertex || !fragment) return null;
  const program = gl.createProgram();
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.bindAttribLocation(program, 0, 'aPosition');
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) { gl.deleteProgram(program); return null; }
  const uniforms = {};
  const count = gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < count; i += 1) {
    const info = gl.getActiveUniform(program, i);
    if (info) uniforms[info.name] = gl.getUniformLocation(program, info.name);
  }
  return { program, uniforms };
}

function init() {
  if (shared !== undefined) return shared;
  shared = null;
  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const gl = canvas.getContext('webgl2', {
    alpha: true, premultipliedAlpha: true, antialias: false, depth: false, stencil: false, powerPreference: 'high-performance',
  });
  if (!gl) return shared;
  const floatTargets = Boolean(gl.getExtension('EXT_color_buffer_float'));
  const field = link(gl, FIELD);
  const composite = link(gl, COMPOSITE);
  if (!field || !composite) return shared;

  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

  const fw = Math.round(W * QUALITY);
  const fh = Math.round(H * QUALITY);
  const texture = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texStorage2D(gl.TEXTURE_2D, Math.floor(Math.log2(Math.max(fw, fh))) + 1, floatTargets ? gl.RGBA16F : gl.RGBA8, fw, fh);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  const framebuffer = gl.createFramebuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);

  shared = { gl, canvas, field, composite, vao, texture, framebuffer, fw, fh, floatTargets };
  return shared;
}

// как в оригинале: цвет в sRGB → линейный (степень 2.2)
function toLinear(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => (c / 255) ** 2.2);
}

function variationOffsets(variation) {
  const n = Number(variation) || 0;
  if (!n) return [0, 0, 0];
  const wave = (k) => {
    const v = Math.sin(n * 12.9898 + k * 78.233) * 43758.5453;
    return v - Math.floor(v) - 0.5;
  };
  return [wave(1), wave(2), wave(3)];
}

function render(v) {
  const { gl, field, composite, vao, texture, framebuffer, fw, fh, floatTargets } = shared;
  const o = v.options;
  const st = v.state;
  const p = st.intro;
  const presence = p * p * p * (p * (p * 6 - 15) + 10);
  gl.bindVertexArray(vao);

  // проход 1: поле CPPN в текстуру
  const f = field.uniforms;
  gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
  gl.viewport(0, 0, fw, fh);
  gl.useProgram(field.program);
  const angle = (o.rotation * Math.PI) / 180;
  const [sx, sy, sz] = variationOffsets(o.variation);
  gl.uniform2f(f.uView, W, H);
  gl.uniform2f(f.uScale, W / fw, H / fh);
  gl.uniform1f(f.uZoom, o.scale);
  gl.uniform2f(f.uTurn, Math.cos(angle), Math.sin(angle));
  gl.uniform1f(f.uTime, st.clock);
  gl.uniform1f(f.uFlow, o.flow);
  gl.uniform1f(f.uWarp, o.warp);
  gl.uniform3f(f.uSeed, 0.3948333106474662 + sx, 0.36 + sy, 0.14 + sz);
  gl.uniform4f(f.uPointer, st.x, st.y, st.touch * o.mouseStrength * 0.9, 0.42);
  gl.uniform2f(f.uDrag, (st.dragX * 2) / H, (st.dragY * 2) / H);
  gl.uniform1f(f.uUnfold, (1 - presence) * 0.45);
  gl.uniform1f(f.uDither, floatTargets ? 0 : 1 / 255);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.generateMipmap(gl.TEXTURE_2D);

  // проход 2: свечение и цвет в кадр
  const c = composite.uniforms;
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  gl.viewport(0, 0, W, H);
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.useProgram(composite.program);
  gl.uniform2f(c.uResolution, W, H);
  gl.uniform2f(c.uTexel, 1 / fw, 1 / fh);
  gl.uniform3fv(c.uColor, v.tint);
  gl.uniform3fv(c.uInk, v.tint);
  gl.uniform1f(c.uGain, 1.15 * o.brightness);
  gl.uniform1f(c.uSheen, 0.5 * o.sheen);
  gl.uniform1f(c.uGlow, o.glow);
  gl.uniform1f(c.uPresence, presence);
  gl.uniform1f(c.uGrain, o.grain);
  gl.uniform1f(c.uFade, 0);
  gl.uniform1f(c.uOpacity, 1);
  gl.uniform1f(c.uTime, st.clock % 1000);
  gl.uniform1f(c.uLightMode, 0);
  gl.uniform4f(c.uLight, (st.lightX * W) / H, 1 - st.lightY, st.touch * o.mouseStrength, 0);
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.uniform1i(c.uField, 0);
  gl.drawArrays(gl.TRIANGLES, 0, 3);

  // копия кадра в холст карточки, кроп под её формат
  const { ctx2d, crop } = v;
  ctx2d.clearRect(0, 0, v.canvas.width, v.canvas.height);
  ctx2d.drawImage(shared.canvas, crop.x, crop.y, crop.w, crop.h, 0, 0, v.canvas.width, v.canvas.height);
}

function step(v, dt) {
  const o = v.options;
  const st = v.state;
  const ptr = v.pointer;
  st.clock += dt * o.speed;
  if (st.intro < 1) st.intro = Math.min(1, st.intro + dt / INTRO_SECONDS);
  const engaged = ptr.inside;
  if (engaged && st.touch < 0.01) { st.x = ptr.x; st.y = ptr.y; }
  const follow = 1 - Math.exp(-dt * 9);
  const px = st.x;
  const py = st.y;
  st.x += (ptr.x - st.x) * follow;
  st.y += (ptr.y - st.y) * follow;
  const vx = (st.x - px) / Math.max(dt, 0.001);
  const vy = (st.y - py) / Math.max(dt, 0.001);
  const blend = 1 - Math.exp(-dt * 5);
  st.dragX += (clamp(vx * 0.12, -120, 120) - st.dragX) * blend;
  st.dragY += (clamp(-vy * 0.12, -120, 120) - st.dragY) * blend;
  st.touch += ((engaged ? 1 : 0) - st.touch) * (1 - Math.exp(-dt * (engaged ? 3 : 1.6)));
  const lx = engaged ? ptr.x / W : 0.5;
  const ly = engaged ? ptr.y / H : 0.15;
  const lf = 1 - Math.exp(-dt * 4);
  st.lightX += (lx - st.lightX) * lf;
  st.lightY += (ly - st.lightY) * lf;
}

function tick(now) {
  raf = 0;
  if (!veils.size || !shared) return;
  const interval = 1000 / FPS;
  if (lastFrame && now - lastFrame < interval - 1) { raf = requestAnimationFrame(tick); return; }
  const dt = lastFrame ? Math.min(0.05, (now - lastFrame) / 1000) : 1 / FPS;
  lastFrame = now;
  let again = false;
  veils.forEach((v) => {
    if (!v.visible || document.hidden || v.done || !v.canvas.width) return;
    if (v.still) {
      render(v);
      v.done = true;
      return;
    }
    step(v, dt);
    render(v);
    again = true;
  });
  if (again) raf = requestAnimationFrame(tick);
  else lastFrame = 0;
}

function wake() {
  if (!raf) raf = requestAnimationFrame(tick);
}
document.addEventListener('visibilitychange', () => { lastFrame = 0; if (!document.hidden) wake(); });

/**
 * Вуаль на фоне container. options: color ('#6b12ff'), variation (число: другой узор), speed, glow, brightness,
 * still (true — один кадр без анимации). → destroy()
 */
export function mountVeil(container, options = {}) {
  if (!init()) return () => {};
  const still = Boolean(options.still) || prefersReducedMotion();
  const o = {
    color: '#6b12ff', speed: 0.5, scale: 1, rotation: 0, flow: 1, warp: 0, variation: 0,
    brightness: 1, sheen: 0, glow: 0.4, grain: 0.03, mouseStrength: 1, ...options,
  };
  const canvas = document.createElement('canvas');
  canvas.className = 'veil__canvas';
  canvas.setAttribute('aria-hidden', 'true');
  container.prepend(canvas);
  const v = {
    options: o,
    tint: toLinear(o.color),
    canvas,
    ctx2d: canvas.getContext('2d'),
    crop: { x: 0, y: 0, w: W, h: H },
    still,
    done: false,
    visible: false,
    pointer: { x: 0, y: 0, inside: false },
    state: {
      clock: (Number(o.variation) || 0) * 3.7, intro: still ? 1 : 0, touch: 0, x: 0, y: 0, dragX: 0, dragY: 0,
      lightX: 0.5, lightY: 0.15,
    },
  };

  const resize = () => {
    const cw = Math.max(1, container.clientWidth);
    const ch = Math.max(1, container.clientHeight);
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(cw * ratio);
    canvas.height = Math.round(ch * ratio);
    // кроп кадра W×H под формат карточки (как background-size: cover)
    const a = cw / ch;
    if (a > W / H) { const h = W / a; v.crop = { x: 0, y: (H - h) / 2, w: W, h }; }
    else { const w = H * a; v.crop = { x: (W - w) / 2, y: 0, w, h: H }; }
    v.done = false;
    wake();
  };
  const ro = new ResizeObserver(resize);
  ro.observe(container);
  const io = new IntersectionObserver((entries) => {
    v.visible = entries.some((e) => e.isIntersecting);
    if (v.visible) wake();
  });
  io.observe(container);

  // палец над карточкой: координаты в пространстве кадра
  const onMove = (e) => {
    const r = container.getBoundingClientRect();
    v.pointer.x = v.crop.x + ((e.clientX - r.left) / Math.max(r.width, 1)) * v.crop.w;
    v.pointer.y = v.crop.y + ((e.clientY - r.top) / Math.max(r.height, 1)) * v.crop.h;
    v.pointer.inside = true;
    wake();
  };
  const onLeave = () => { v.pointer.inside = false; wake(); };
  if (!still) {
    container.addEventListener('pointermove', onMove, { passive: true });
    container.addEventListener('pointerleave', onLeave);
    container.addEventListener('pointercancel', onLeave);
  }

  veils.add(v);
  resize();
  return function destroy() {
    veils.delete(v);
    ro.disconnect();
    io.disconnect();
    container.removeEventListener('pointermove', onMove);
    container.removeEventListener('pointerleave', onLeave);
    container.removeEventListener('pointercancel', onLeave);
    canvas.remove();
  };
}
