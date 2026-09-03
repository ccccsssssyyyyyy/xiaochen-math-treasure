// 轻量 vm 沙箱测试：加载真实 static/js/ocr.js，验证「多图合并 OCR」逻辑。
// 不依赖 jsdom（在受限沙箱会 OOM），直接为 IIFE 注入最小 DOM/fetch 桩。
// 覆盖：
//  1) refreshContentOcrModeUI 根据存储的 content 偏好高亮对应按钮
//  2) runContentOcrBatch([图1,图2])：两张内容都拼接到题干、OCR 调用 2 次、AI 分类仅 1 次

const fs = require('fs');
const vm = require('vm');
const path = require('path');

const ROOT = require('path').resolve(__dirname, '..');
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
const fetchStub = (url) => {
  const u = String(url);
  if (u.includes('/api/ocr')) {
    ocrCalls++;
    latexSeq++;
    return Promise.resolve({ json: () => Promise.resolve({ status: 'success', latex: '题干第' + latexSeq + '部分', confidence: 0.9, image_path: '/x.png' }) });
  }
  if (u.includes('/api/ai/classify')) {
    classifyCalls++;
    return Promise.resolve({ json: () => Promise.resolve({ status: 'success', source: '' }) });
  }
  return Promise.resolve({ json: () => Promise.resolve({ status: 'ok' }) });
};

const localStorageStub = { _s: {}, getItem(k) { return (k in this._s) ? this._s[k] : null; }, setItem(k, v) { this._s[k] = v; } };

const sandbox = {
  console,
  fetch: fetchStub,
  localStorage: localStorageStub,
  confirm: () => true,
  showToast: () => {},
  setTimeout: () => {},
  FileReader: function () { this.readAsDataURL = () => { if (this.onload) this.onload({ target: { result: 'data:' } }); }; },
  FormData: class { append() {} },
  AbortController: function () { this.signal = {}; this.abort = () => {}; },
  Event: function (t) { this.type = t; },
  document: { getElementById: getEl, addEventListener() {}, createElement: () => makeEl('x'), body: makeEl('body') },
  addEventListener() {},
  innerWidth: 1200,
  innerHeight: 800,
  // 预置浏览器中作为隐式全局存在的变量/函数，供 vm 沙箱解析时不报 ReferenceError
  contentOcrAbortController: null,
  answerOcrAbortController: null,
  aiSolveAbortController: null,
  // formatQuestionContent 在 editor.js（浏览器中先于 ocr.js 加载）；测试中用透传桩替代
  formatQuestionContent: (s) => s,
};
sandbox.window = sandbox;
sandbox.window.getOcrMode = () => 'append';
sandbox.window.setOcrMode = () => {};
sandbox.window.applyClassifyResultToEditor = undefined;

vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: 'ocr.js' });

const assert = (c, l) => { console.log((c ? '✅' : '❌') + ' ' + l); if (!c) process.exitCode = 1; };

// Test 1: 开关 UI 跟随存储偏好
sandbox.window.refreshContentOcrModeUI();
assert(getEl('contentOcrModeAppend').className.includes('bg-brand-600'), 'append 模式下「追加」按钮高亮');
assert(getEl('contentOcrModeReplace').className.includes('bg-slate-200'), 'append 模式下「替换」按钮置灰');

// Test 2: 两图批处理 -> 拼接 + 仅一次分类
getEl('editContent').value = '';
const file1 = { type: 'image/png', name: 'q1.png' };
const file2 = { type: 'image/png', name: 'q2.png' };

sandbox.window.runContentOcrBatch([file1, file2]).then(() => {
  const val = getEl('editContent').value;
  console.log('DEBUG editContent=[' + val + '] len=' + val.length);
  assert(val.includes('题干第1部分') && val.includes('题干第2部分'), '两图内容均拼接到题干');
  assert(ocrCalls === 2, 'OCR 接口被调用 2 次 (实际 ' + ocrCalls + ')');
  assert(classifyCalls === 1, 'AI 分类仅调用 1 次 (实际 ' + classifyCalls + ')');
  console.log('\n' + (process.exitCode ? 'FAIL: ocr batch' : 'ALL PASS: ocr batch'));
  process.exit(process.exitCode || 0);
}).catch((e) => { console.error('ERR', e); process.exit(2); });
