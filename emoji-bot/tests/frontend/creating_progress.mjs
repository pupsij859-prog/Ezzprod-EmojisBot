// Выполняем настоящий экран сборки с подменой сети, времени и DOM.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';

let clock = 0;
let requests = 0;
let root;
const renders = [];
const percentages = [];
let stallFirst = false;
const context = vm.createContext({ Date: { now: () => clock }, console, AbortController, clearTimeout,
  setTimeout: (fn, delay) => setTimeout(fn, stallFirst ? 0 : delay),
});
function h(tag, props, ...children) {
  return {
    tag, props: props || {}, children,
    style: { setProperty() {} },
    setAttribute(name, value) { if (name === 'aria-valuenow') percentages.push(value); },
    replaceChildren(...nodes) { this.children = nodes; renders.push(JSON.stringify(nodes)); },
  };
}
const noop = () => {};
const api = { pack: async (id, signal) => {
  assert.ok(signal instanceof AbortSignal, 'status polling must be cancellable');
  requests += 1;
  if (stallFirst && requests === 1) {
    return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }));
  }
  return requests === 1
    ? { id: 1, status: 'processing', progress: 0.1 }
    : { id: 1, status: 'ready', progress: 1, title: 'test pack', count: 2, type: 'emoji' };
} };
const imports = {
  '../api.js': { api },
  '../components.js': { BigEmoji: noop, Button: noop, CloseButton: noop, Quote: noop },
  '../nav.js': { openOverlay: (options) => { root = options.render({ close: noop }); } },
  '../store.js': { refreshMe: async () => {}, state: { me: { demo: false } } },
  '../router.js': { goTab: noop },
  '../tg.js': { haptic: { success: noop, error: noop }, openLink: noop },
  '../ui.js': {
    emojiWord: String, h, prefersReducedMotion: () => true,
    sleep: async () => { clock += 91_000; }, toast: noop,
  },
};
const source = await fs.readFile(new URL('../../webapp/js/views/creating.js', import.meta.url), 'utf8');
const screen = new vm.SourceTextModule(source, { context });
await screen.link((specifier) => {
  const values = imports[specifier];
  assert.ok(values, `unexpected import ${specifier}`);
  return new vm.SyntheticModule(Object.keys(values), function () {
    for (const [key, value] of Object.entries(values)) this.setExport(key, value);
  }, { context });
});
await screen.evaluate();
screen.namespace.openCreating({ id: 1, progress: 0 });
await new Promise((resolve) => setImmediate(resolve));
assert.equal(requests, 2, 'polling must continue after the slow-build screen');
assert.ok(renders.some((render) => render.includes('Сборка затянулась')));
assert.ok(JSON.stringify(root).includes('Пак готов'), 'a late result must replace the slow-build screen');
assert.deepEqual(percentages, ['10', '100'], 'progress must follow actual work instead of elapsed time');

stallFirst = true;
requests = 0;
clock = 0;
percentages.length = 0;
screen.namespace.openCreating({ id: 2, progress: 0 });
await new Promise((resolve) => setTimeout(resolve, 30));
assert.equal(requests, 2, 'a hung status request must be aborted and retried');
assert.ok(JSON.stringify(root).includes('Пак готов'));
console.log('Creating screen: actual progress, late completion and hung request recovery verified');
