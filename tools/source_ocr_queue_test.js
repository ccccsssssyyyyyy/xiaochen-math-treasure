// 轻量 vm 沙箱测试：验证连续调用 runContentOcrBatch 时不会 abort 前一张的 OCR。
// 第一张用延迟 fetch，第二张紧跟入队；若第二张启动时 abort 了第一张，
// 则第一张的 latex 不会写入 editContent，OCR 调用次数也会少于 2。

const fs = require('fs');
const vm = require('vm');
const path = require('path');

const ROOT = '/Users/ccsssy/WorkBuddy/2026-08-24-00-26-36/math-question-bank';
const code = fs.readFileSync(path.join(ROOT, 'static/js/ocr.js'), 'utf8');

function makeEl(id) {
  return {
    id, value: '', textContent: '', innerHTML: '', checked: false, style: {},
    classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
    addEventListener() {}, dispatchEvent() {}, appendChild() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0 }; },
    offsetParent: null,
    set onclick(v) {}, get onclick() { return null; },
  };
}
const els = {};
function getEl(id) { if (!els[id]) els[id] = makeEl(id); return els[id]; }

let ocrCalls = 0, classifyCalls = 0, latexSeq = 0;

function AbortControllerStub() {
  this.signal = { aborted: false };
  this.abort = () => { this.signal.aborted = true; };
}

function fetchStub(url, opts) {
  const u = String(url);
  if (u.includes('/api/ocr')) {
    const signal = (opts && opts.signal) || {};
    return new Promise((resolve, reject) => {
      setTimeout(() => {
        if (signal.aborted) {
          const err = new Error('AbortError');
          err.name = 'AbortError';
          reject(err);
          return;
        }
        ocrCalls++;
        latexSeq++;
        resolve({ json: () => Promise.resolve({ status: 'success', latex: '题干第' + latexSeq + '部分', confidence: 0.9, image_path: '/x.png' }) });
      }, 20);
    });
  }
  if (u.includes('/api/ai/classify')) {
    classifyCalls++;
    return Promise.resolve({ json: () => Promise.resolve({ status: 'success', source: '' }) });
  }
  return Promise.resolve({ json: () => Promise.resolve({ status: 'ok' }) });
}

const localStorageStub = { _s: {}, getItem(k) { return (k in this._s) ? this._s[k] : null; }, setItem(k, v) { this._s[k] = v; } };

const sandbox = {
  console,
  fetch: fetchStub,
  localStorage: localStorageStub,
  confirm: () => true,
  showToast: () => {},
  setTimeout: global.setTimeout,
  FileReader: function () { this.readAsDataURL = () => { if (this.onload) this.onload({ target: { result: 'data:' } }); }; },
  FormData: class { append() {} },
  AbortController: AbortControllerStub,
  Event: function (t) { this.type = t; },
  document: { getElementById: getEl, addEventListener() {}, createElement: () => makeEl('x'), body: makeEl('body') },
  addEventListener() {},
  innerWidth: 1200,
  innerHeight: 800,
  contentOcrAbortController: null,
  answerOcrAbortController: null,
  aiSolveAbortController: null,
  formatQuestionContent: (s) => s,
  Error,
};
sandbox.window = sandbox;
sandbox.window.getOcrMode = () => 'append';
sandbox.window.setOcrMode = () => {};

vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: 'ocr.js' });

const assert = (c, l) => { console.log((c ? '✅' : '❌') + ' ' + l); if (!c) process.exitCode = 1; };

getEl('editContent').value = '';
const file1 = { type: 'image/png', name: 'q1.png' };
const file2 = { type: 'image/png', name: 'q2.png' };

// 连续两次调用 batch，模拟用户在第一张识别完成前又粘贴第二张
sandbox.window.runContentOcrBatch([file1]);
sandbox.window.runContentOcrBatch([file2]);

setTimeout(() => {
  const val = getEl('editContent').value;
  console.log('DEBUG editContent=[' + val + '] ocrCalls=' + ocrCalls + ' classifyCalls=' + classifyCalls);
  assert(val.includes('题干第1部分') && val.includes('题干第2部分'), '两图内容均进入题干（第一张未被 abort）');
  assert(ocrCalls === 2, 'OCR 接口被调用 2 次 (实际 ' + ocrCalls + ')');
  assert(classifyCalls === 1, 'AI 分类仅调用 1 次 (实际 ' + classifyCalls + ')');
  console.log('\n' + (process.exitCode ? 'FAIL: ocr queue' : 'ALL PASS: ocr queue'));
  process.exit(process.exitCode || 0);
}, 100);
