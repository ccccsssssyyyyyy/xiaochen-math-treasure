// 轻量 vm 沙箱测试：验证答案识别后，后续图片默认路由到答案栏。
// 第一张图含【答案】标记 -> 题干进题干栏，答案进答案栏；
// 第二张图无答案标记 -> 因 hasAnswerStarted=true，forceAnswer=true，全部追加到答案栏。

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

let ocrCalls = 0, classifyCalls = 0;
function fetchStub(url, opts) {
  const u = String(url);
  if (u.includes('/api/ocr')) {
    ocrCalls++;
    const latex = ocrCalls === 1 ? '题干部分【答案】答案部分' : '后续解析正文';
    return Promise.resolve({ json: () => Promise.resolve({ status: 'success', latex, confidence: 0.9, image_path: '/x.png' }) });
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
  setTimeout: () => {},
  FileReader: function () { this.readAsDataURL = () => { if (this.onload) this.onload({ target: { result: 'data:' } }); }; },
  FormData: class { append() {} },
  AbortController: function () { this.signal = { aborted: false }; this.abort = () => { this.signal.aborted = true; }; },
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
sandbox.window.getOcrMode = () => 'replace'; // 替换模式，确保第一张覆盖旧题干
sandbox.window.setOcrMode = () => {};

vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: 'ocr.js' });

const assert = (c, l) => { console.log((c ? '✅' : '❌') + ' ' + l); if (!c) process.exitCode = 1; };

getEl('editContent').value = '';
getEl('editAnswerMarkdown').value = '';
const file1 = { type: 'image/png', name: 'q1.png' };
const file2 = { type: 'image/png', name: 'q2.png' };

sandbox.window.runContentOcrBatch([file1, file2]).then(() => {
  const content = getEl('editContent').value;
  const answer = getEl('editAnswerMarkdown').value;
  console.log('DEBUG content=[' + content + '] answer=[' + answer + ']');
  assert(content === '题干部分', '第一张图的题干部分只进题干栏');
  assert(answer.includes('【答案】答案部分'), '第一张图的答案部分进答案栏');
  assert(answer.includes('后续解析正文'), '第二张图（forceAnswer）追加到答案栏');
  assert(!content.includes('后续解析正文'), '第二张图内容未污染题干栏');
  assert(ocrCalls === 2, 'OCR 接口被调用 2 次 (实际 ' + ocrCalls + ')');
  assert(classifyCalls === 1, 'AI 分类仅调用 1 次 (实际 ' + classifyCalls + ')');
  console.log('\n' + (process.exitCode ? 'FAIL: ocr route' : 'ALL PASS: ocr route'));
  process.exit(process.exitCode || 0);
}).catch((e) => { console.error('ERR', e); process.exit(2); });
