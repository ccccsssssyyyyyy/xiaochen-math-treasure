// 测试单题 OCR 录入的「当前卷名（默认来源）」识别与继承逻辑。
// 用轻量 vm 沙箱直接加载真实 static/js/ocr.js（函数自包含、不依赖重型 DOM），
// 避开的 jsdom 在受限沙箱会 OOM（exit 137）的问题。
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const ROOT = require('path').resolve(__dirname, '..');
const code = fs.readFileSync(path.join(ROOT, 'static/js/ocr.js'), 'utf8');

// ---- 最小 DOM / 浏览器 stub ----
function makeEl() {
  return {
    value: '', checked: false, textContent: '',
    classList: { add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, dispatchEvent() {}, onclick: null,
    getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0 }; },
    offsetParent: null,
  };
}
const elements = {};
const document = {
  getElementById(id) { if (!elements[id]) elements[id] = makeEl(); return elements[id]; },
  createElement() { return makeEl(); },
  addEventListener() {},
  body: { appendChild() {} },
};
const store = {};
const localStorage = {
  getItem(k) { return (k in store) ? store[k] : null; },
  setItem(k, v) { store[k] = String(v); },
  removeItem(k) { delete store[k]; },
};

const ctx = {
  document, localStorage, console,
  window: null,
  FormData: function () {}, fetch: function () { return Promise.resolve(); },
  FileReader: function () {}, showToast: function () {},
  setTimeout, clearTimeout,
};
ctx.window = ctx;
ctx.window.addEventListener = function () {};
ctx.window.innerWidth = 1000; ctx.window.innerHeight = 800;

vm.createContext(ctx);
vm.runInContext(code, ctx, { filename: 'ocr.js' });

const detect = ctx.window.detectSourceCandidate;
const applyInheritance = ctx.window.applyInheritanceToSource;

let pass = 0, fail = 0;
function check(label, cond) {
  console.log((cond ? '✅' : '❌') + ' ' + label);
  if (cond) pass++; else fail++;
}

// ---- detectSourceCandidate 用例 ----
const headerText = '第一中学2025届高三入学考试（数学）\n1. 已知函数 f(x)=x^2，求 f(1)。';
check('卷头行识别', detect(headerText, '') === '第一中学2025届高三入学考试（数学）');

const bracketText = '（2019·全国·高考真题）已知 f(x)=x，求 f(1)。';
check('括号出处识别', detect(bracketText, '') === '2019·全国·高考真题');

check('纯题干不误判', detect('已知 f(x)=x^2，求 f(1)。', '') === '');

check('文件名识别', detect('', '第一中学第三次月考.png') === '第一中学第三次月考');

check('带噪声文件名不误判',
  detect('', '微信图片_20250830_123456.png') === '');

check('超长文件名不误判', detect('', 'a'.repeat(50) + '.png') === '');

check('无命中返回空', detect('', '') === '');

// ---- applyInheritanceToSource 用例 ----
store['mathbank_source_use_default'] = '1';
store['mathbank_source_default'] = '默认卷X';
applyInheritance();
check('继承写入 editSource', elements['editSource'].value === '默认卷X');
check('继承勾选 editSourceDefault', elements['editSourceDefault'].checked === true);

store['mathbank_source_use_default'] = '0';
elements['editSource'].value = '';
elements['editSourceDefault'].checked = false;
applyInheritance();
check('未启用继承时不写入', elements['editSource'].value === '');

console.log('\n' + (fail ? `FAIL: ${fail} 项未通过` : `ALL PASS: ${pass} 项`) );
process.exit(fail ? 1 : 0);
