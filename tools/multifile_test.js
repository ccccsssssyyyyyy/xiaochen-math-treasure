const fs = require('fs');
const path = require('path');
const { JSDOM } = require('/Users/ccsssy/.workbuddy/binaries/node/workspace/node_modules/jsdom');

const ROOT = '/Users/ccsssy/WorkBuddy/2026-08-24-00-26-36/math-question-bank';
const html = fs.readFileSync(path.join(ROOT, 'static/index.html'), 'utf8');
const fileNames = ['api', 'editor', 'ocr', 'import', 'paper'];
const codes = fileNames.map(n => fs.readFileSync(path.join(ROOT, 'static/js', `${n}.js`), 'utf8'));

const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://127.0.0.1:8000/', pretendToBeVisual: true });
const win = dom.window;
const doc = win.document;

// stub external libs BEFORE injecting app scripts
win.renderMathInElement = () => {};
win.marked = { parse: (x) => x || '' };
win.DOMPurify = { sanitize: (x) => x };
win.tailwind = { config: {} };
// jsdom lacks these browser APIs used by scroll-spy / observers
win.IntersectionObserver = class { constructor(cb){ this.cb = cb; } observe(){} unobserve(){} disconnect(){} };
win.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} };
try { win.localStorage.setItem('local_token', 'test-token'); } catch (e) {}
win.local_token = 'test-token';
win.systemPreferParseModel = 'deepseek-chat';
win.systemMetadata = {
  question_types: [
    { value: 'single_choice', label: '单选题' }, { value: 'multi_choice', label: '多选题' },
    { value: 'fill_in_blank', label: '填空题' }, { value: 'detailed_answer', label: '解答题' },
  ],
  difficulties: [ { value: 'easy', label: '易' }, { value: 'medium', label: '中' }, { value: 'hard', label: '难' } ],
};

// fetch mock
const calls = [];
const mkQ = (n, src) => Array.from({ length: n }, (_, i) => ({
  question_type: 'detailed_answer', content: `Q${i + 1} from ${src}`, answer_markdown: `a${i + 1}`, difficulty: 'medium', source: src,
}));
win.fetch = async (url, opts) => {
  const u = String(url);
  calls.push(u.replace('http://127.0.0.1:8000', '') + (opts && opts.method ? ` [${opts.method}]` : ''));
  if (u.includes('/api/upload/tex-source')) return { ok: true, json: async () => ({ status: 'success', source: 'dummy', title: '' }) };
  if (u.includes('/api/ai/parse-paper')) return { ok: true, json: async () => ({ status: 'success', questions: mkQ(3, win.__currentParseSourceFile || 'unknown') }) };
  if (u.includes('/api/tasks/')) return { ok: true, json: async () => ({ status: 'completed', progress: 100, data: mkQ(3, 't') }) };
  return { ok: true, json: async () => ({ status: 'success' }) };
};

win.addEventListener('error', e => console.error('[window.error]', (e.error && (e.error.stack || e.error.message)) || e.message));
win.addEventListener('unhandledrejection', e => console.error('[unhandledrejection]', (e.reason && (e.reason.stack || e.reason.message)) || e.reason));

// inject app scripts as <script> elements (shared global scope, like browser)
for (const code of codes) {
  const s = doc.createElement('script');
  s.textContent = code;
  try { doc.body.appendChild(s); } catch (e) { console.error('[inject error]', e.message); }
}
console.log('[harness] scripts injected. startImportParseClick type:', typeof win.startImportParseClick);

// give DOMContentLoaded a tick (scripts appended after parse; manually fire if needed)
setTimeout(() => {
  console.log('[harness] after tick, startImportParseClick type:', typeof win.startImportParseClick);
  const texInput = doc.getElementById('texFileInput');
  if (!texInput) { console.error('NO texFileInput'); process.exit(1); }
  const f1 = new win.File(['\\documentclass{ctexart}\\begin{document}A\\end{document}'], 'paperA.tex', { type: 'text/plain' });
  const f2 = new win.File(['\\documentclass{ctexart}\\begin{document}B\\end{document}'], 'paperB.tex', { type: 'text/plain' });
  Object.defineProperty(texInput, 'files', { value: [f1, f2], configurable: true });
  texInput.dispatchEvent(new win.Event('change'));
  console.log('[harness] change dispatched');

  setTimeout(() => {
    console.log('[harness] fetch calls after enqueue:', JSON.stringify(calls));
    try { win.startImportParseClick(); console.log('[harness] startImportParseClick() called'); }
    catch (e) { console.error('[harness] start error', e); }

    let waited = 0;
    const iv = setInterval(() => {
      waited += 300;
      const badge = doc.getElementById('parsedCountBadge');
      const cards = doc.getElementById('parsedCardsContainer');
      const qp = doc.getElementById('importQueueProgress');
      if (waited >= 12000) {
        clearInterval(iv);
        console.log('=== FINAL (t=' + waited + 'ms) ===');
        console.log('countBadge:', badge ? badge.textContent : 'MISSING');
        console.log('cards:', cards ? cards.children.length : 'MISSING');
        console.log('queueProgress:', qp ? qp.textContent : 'MISSING');
        console.log('fetch calls:', JSON.stringify(calls));
        console.log('__currentParseStartIndex:', win.__currentParseStartIndex);
        console.log('__importQueueHasPending:', win.__importQueueHasPending);
        process.exit(0);
      }
    }, 300);
  }, 300);
}, 200);
